"""The translation pipeline.

Input: a source project package, an optional destination kit.
Output: a new package that the destination slicer can open, plus a report of
everything that was carried, adjusted, dropped or could not be resolved.

The pipeline is deliberately ordered from cheapest-to-verify to hardest:

1. **Repair the object graph** — ids, plate instances, part references.
   Everything downstream addresses objects by id, so this must be consistent
   before any settings are emitted.
2. **Decide the filament slots** — count, colours and materials belong to the
   project; the *preset* behind each slot belongs to the destination.
3. **Project the settings** — the destination kit's key set is the schema.
   Printer geometry always comes from the kit; process tuning comes from the
   source; filament arrays are rebuilt from the slot decisions.
4. **Rewrite the slicer metadata** — model_settings, layer ranges, colour
   changes, assembly table.
5. **Restamp the dialect and drop stale slice artefacts.**
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field
from pathlib import Path

from .container import Package
from .flavors import FLAVORS, Flavor, TargetKit, detect_source
from .model import (
    CUSTOM_GCODE_PATH,
    FILAMENT_SEQUENCE_PATH,
    LAYER_RANGES_PATH,
    MODEL_SETTINGS_PATH,
    PRINT_PROFILE_PATH,
    SLICE_INFO_PATH,
    AuxFile,
    FilamentSequence,
    Model3D,
    ModelSettings,
)
from .mapping import VARIANT_KEYS, Destination, alias_targets, clamp_to_limits, scalar_of
from .mapping import translate as translate_value
from .settings import (
    GEOMETRY_KEYS,
    ProjectSettings,
    build_different_settings,
    classify,
    is_per_filament,
)


class TranslationError(Exception):
    """Raised when the translator refuses to produce an output."""

    def __init__(self, message: str, report: "Report | None" = None) -> None:
        super().__init__(message)
        self.report = report


@dataclass
class Options:
    target_flavor: str | None = None
    app_string: str | None = None
    version: str | None = None
    filaments: int | None = None
    extruders: int | None = None
    keep_unknown: bool = False
    keep_source_presets: bool = False
    drop_provenance: bool = False
    drop_aux: bool = False
    force: bool = False


@dataclass
class Report:
    source_label: str = ""
    source_flavor: str = ""
    source_evidence: list[str] = field(default_factory=list)
    target_label: str = ""
    target_flavor: str = ""
    carried: list[str] = field(default_factory=list)
    adjusted: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    counts: dict[str, object] = field(default_factory=dict)
    paint_parts: int = 0

    def add(self, bucket: str, message: str) -> None:
        getattr(self, bucket).append(message)

    def text(self) -> str:
        lines = [
            "x3mf translation report",
            "=======================",
            f"source : {self.source_label}  [{self.source_flavor}]",
            f"target : {self.target_label}  [{self.target_flavor}]",
        ]
        if self.source_evidence:
            lines.append("source markers: " + "; ".join(self.source_evidence))
        if self.counts:
            lines.append("")
            lines.append("counts:")
            for key, value in self.counts.items():
                lines.append(f"  {key:<22} {value}")
        for title, bucket in (
            ("carried", self.carried),
            ("carried with adjustment", self.adjusted),
            ("dropped", self.dropped),
            ("warnings", self.warnings),
            ("errors", self.errors),
        ):
            if not bucket:
                continue
            lines.append("")
            lines.append(f"{title}:")
            for item in bucket:
                lines.append(f"  - {item}")
        return "\n".join(lines) + "\n"


@dataclass
class Translation:
    package: Package
    report: Report

    def save(self, path: str | Path) -> Path:
        return self.package.write(path)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_PART_FILE_RE = re.compile(r"<object\b", re.IGNORECASE)


def _is_list(value) -> bool:
    return isinstance(value, list)


def _cycle(values: list[str], n: int) -> list[str]:
    if n <= 0:
        return []
    if not values:
        return [""] * n
    if len(values) == n:
        return values
    return [values[i % len(values)] for i in range(n)]


def _parse_int_list(value: str) -> list[int] | None:
    if not value:
        return None
    parts = re.split(r"[\s,]+", value.strip())
    try:
        return [int(p) for p in parts if p != ""]
    except ValueError:
        return None


def _int_list_text(values: list[int]) -> str:
    return " ".join(str(v) for v in values)


# ---------------------------------------------------------------------------
# translator
# ---------------------------------------------------------------------------

class Translator:
    def __init__(
        self,
        package: Package,
        kit: TargetKit | None = None,
        options: Options | None = None,
    ) -> None:
        self.pkg = package.copy()
        self.kit = kit
        self.opts = options or Options()
        self.model = Model3D.from_package(self.pkg)
        self.ms = ModelSettings.from_package(self.pkg)
        self.settings = ProjectSettings.from_package(self.pkg)
        self.source = detect_source(self.pkg, self.settings)
        self.report = Report(
            source_label="<memory>",
            source_flavor=self.source.flavor.key,
            source_evidence=list(self.source.evidence),
        )
        for contradiction in self.source.contradictions:
            self.report.warnings.append(contradiction)

        self.target_flavor: Flavor = FLAVORS["generic"]
        self.out: ProjectSettings = ProjectSettings({})
        self.filament_map: dict[int, int] = {}      # 1-based source slot -> out slot
        self.out_filaments: list[dict[str, str]] = []
        self.object_ids: list[str] = []
        self.dropped_objects: set[str] = set()
        self.plates_before = len(self.ms.plate_elements()) if self.ms else 0

    # -- entry point --------------------------------------------------------
    def run(self) -> Translation:
        if not self.settings.keys() and self.kit is None:
            raise TranslationError(
                "source has no project settings and no destination kit was supplied: "
                "there is nothing to say what the output should print on"
            )
        self._choose_target()
        self._repair_objects()
        self._choose_filaments()
        self._emit_settings()
        if self.ms is not None:
            self._emit_model_settings()
        self._emit_aux()
        self._stamp_dialect()
        self._cleanup()

        self.report.paint_parts = sum(1 for part in self.model.parts.values() if part.has_paint)
        if self.report.paint_parts and len(self.out_filaments) != len(self.settings.filaments()):
            self.report.warnings.append(
                f"{self.report.paint_parts} part file(s) carry per-face colour paint: those faces "
                "still reference the source slot numbers and were not remapped, so check painted "
                "regions in the slicer preview"
            )
        self.report.counts.setdefault("objects", len(self.object_ids))
        self.report.counts.setdefault("plates", len(self.ms.plate_elements()) if self.ms else 0)
        self.report.counts.setdefault("filament slots", len(self.out_filaments))
        return Translation(self.pkg, self.report)

    # -- 1. target ----------------------------------------------------------
    def _choose_target(self) -> None:
        if self.opts.target_flavor:
            if self.opts.target_flavor not in FLAVORS:
                raise TranslationError(f"unknown target flavor {self.opts.target_flavor!r}")
            self.target_flavor = FLAVORS[self.opts.target_flavor]
        elif self.kit is not None:
            self.target_flavor = self.kit.flavor
        else:
            self.target_flavor = self.source.flavor
        self.report.target_flavor = self.target_flavor.key
        self.report.target_label = self.kit.describe() if self.kit else "in-place repair (no kit)"
        if self.kit is None:
            self.report.warnings.append(
                "no destination kit supplied: printer geometry is inherited from the "
                "source project, so the output targets the same physical machine"
            )
        else:
            for warning in self.kit.warnings:
                self.report.warnings.append(warning)

    # -- 2. object graph ----------------------------------------------------
    def _repair_objects(self) -> None:
        model_ids = self.model.objects()
        if not model_ids:
            raise TranslationError("source model has no objects")
        self.object_ids = list(model_ids)

        # Components pointing at part files that are not in the package.
        for object_id, obj in model_ids.items():
            for comp in self.model.components(obj):
                path = comp["path"]
                if not path:
                    continue
                part = self.model.parts.get(path)
                if part is None:
                    self.report.warnings.append(
                        f"object {object_id} references missing part file {path}"
                    )
                elif comp["objectid"] and comp["objectid"] not in part.object_ids:
                    self.report.warnings.append(
                        f"object {object_id} references object {comp['objectid']} "
                        f"absent from {path}"
                    )

        if self.ms is None:
            self.report.warnings.append(
                "source has no model_settings.config: plates and per-part settings "
                "cannot be carried"
            )
            return

        # Objects in model_settings that no longer exist in the model.
        for obj_el in list(self.ms.object_elements()):
            object_id = obj_el.get("id", "")
            if object_id not in model_ids:
                self.ms.root.remove(obj_el)
                self.dropped_objects.add(object_id)
                self.report.adjusted.append(
                    f"dropped model_settings object {object_id} (not present in the model)"
                )

        # Plate instances that cannot resolve to a placed object instance.
        removed_instances = 0
        for plate in self.ms.plate_elements():
            for inst in list(self.ms.plate_instances(plate)):
                object_id = self.ms.instance_object_id(inst)
                instance_id = self.ms.instance_index(inst)
                if object_id in self.dropped_objects or object_id not in model_ids:
                    self.ms.remove_plate_instance(plate, inst)
                    removed_instances += 1
                    continue
                placed = self.model.instances_of(object_id)
                try:
                    index = int(instance_id)
                except ValueError:
                    index = -1
                if index < 0 or index >= placed:
                    self.ms.remove_plate_instance(plate, inst)
                    removed_instances += 1
                    self.report.adjusted.append(
                        f"plate {self.ms.plate_ids()[0] if self.ms.plate_ids() else '?'}: "
                        f"dropped instance #{instance_id} of object {object_id} "
                        f"({placed} instance(s) in build)"
                    )
        if removed_instances and not any(self.ms.plate_instances(p) for p in self.ms.plate_elements()):
            self.report.warnings.append("no plate instances survived object-graph repair")

        parts = sum(len(self.ms.parts(o)) for o in self.ms.object_elements())
        self.report.counts["parts"] = parts
        self.report.carried.append(
            f"{len(self.ms.plate_elements())} plate(s), {len(self.ms.object_elements())} "
            f"object(s), {parts} part(s) with their transforms and roles"
        )

    # -- 3. filaments -------------------------------------------------------
    def _choose_filaments(self) -> None:
        src_slots = self.settings.filaments()
        if not self.settings.keys():
            src_slots = []
        n_src = len(src_slots)
        kit_slots = self.kit.settings.filaments() if self.kit else []

        wanted = self.opts.filaments
        if wanted is None:
            wanted = max(n_src, 1)
        if wanted < 1:
            raise TranslationError("a project needs at least one filament slot")

        out: list[dict[str, str]] = []
        for index in range(wanted):
            source = src_slots[index] if index < n_src else (src_slots[index % n_src] if n_src else None)
            material = source.material if source else ""
            colour = source.colour if source else ""
            preset_id = source.preset_id if source else ""
            if kit_slots:
                match, how = _match_preset(kit_slots, material, colour, index)
                if match is not None:
                    if how != "exact" and not self.opts.keep_source_presets:
                        self.report.adjusted.append(
                            f"filament {index + 1}: preset {match.preset_id!r} chosen by "
                            f"{how} (source preset {preset_id!r} is not in the kit)"
                        )
                    elif how == "exact":
                        self.report.carried.append(
                            f"filament {index + 1}: {material or '?'} {colour} -> {match.preset_id!r}"
                        )
                    preset_id = match.preset_id
                else:
                    self.report.warnings.append(
                        f"filament {index + 1}: the kit has no preset for material "
                        f"{material!r}; kept the source preset id {preset_id!r}, which the "
                        "destination may not have installed"
                    )
            out.append({"preset_id": preset_id, "material": material, "colour": colour})

        # 1-based source slot -> output slot.  When the target has fewer slots,
        # collapse each surplus slot onto the closest surviving colour/material.
        mapping: dict[int, int] = {}
        for index in range(1, max(n_src, wanted) + 1):
            if index <= wanted:
                mapping[index] = index
                continue
            source = src_slots[index - 1]
            best, best_score = 1, 99
            for candidate in range(1, wanted + 1):
                target = out[candidate - 1]
                score = (source.material != target["material"]) * 4 + (
                    source.colour.lower() != target["colour"].lower()
                )
                if score < best_score:
                    best, best_score = candidate, score
            mapping[index] = best

        if n_src and wanted < n_src:
            self.report.adjusted.append(
                f"filament slots collapsed from {n_src} to {wanted}; "
                f"assignments and layer colour changes were remapped to the nearest slot"
            )
        if n_src and wanted > n_src:
            self.report.adjusted.append(
                f"filament slots extended from {n_src} to {wanted} by repeating the source slots"
            )

        self.filament_map = mapping
        self.out_filaments = out
        self.report.counts["filament slots (source)"] = n_src

    # -- 4. settings --------------------------------------------------------
    def _destination(self) -> Destination:
        return Destination("creality" if self.target_flavor.key == "creality" else self.target_flavor.key)

    def _carry(self, key: str, value, base_value, destination: Destination, slots: int):
        """Carry one value into the destination's spelling, or refuse it.

        Returns ``(value, reason)``.  A refusal keeps the destination's own
        setting: shipping a value the slicer will silently reinterpret is worse
        than shipping the default it shipped with.
        """
        mapped, reason = translate_value(key, value, base_value, slots, destination)
        if mapped is None:
            return None, reason
        if isinstance(base_value, (list, tuple)) and base_value:
            return [mapped] * len(base_value), None
        return mapped, None

    def _emit_settings(self) -> None:
        if self.kit is not None:
            base = self.kit.settings
            emitted = dict(base.data)
            destination = self._destination()
            slots = max(1, len(self.out_filaments) or self.settings.filament_count())
            dropped: list[str] = []
            kept_unknown: list[str] = []
            respelled: list[str] = []
            renamed: list[str] = []
            refused: list[str] = []
            for key, value in self.settings.data.items():
                # Per-filament arrays are rebuilt by _emit_filament_block, and
                # the diff list by _emit_diff; copying them here would race those.
                if classify(key) == "filament" or key in _SELF_COMPUTED:
                    continue
                if key in emitted:
                    if classify(key) == "machine":
                        continue  # the destination's machine is the authority, always
                    if isinstance(value, (list, tuple)) and key not in VARIANT_KEYS:
                        emitted[key] = value  # an array is copied whole, never mapped
                        continue
                    carried, reason = self._carry(key, value, base.get(key), destination, slots)
                    if carried is None:
                        refused.append(f"{key} ({reason})")
                        continue
                    if scalar_of(carried) != scalar_of(value):
                        respelled.append(f"{key} {scalar_of(value)} -> {scalar_of(carried)}")
                    emitted[key] = carried
                elif targets := alias_targets(key, set(emitted)):
                    # Same setting, different name(s) in the destination.
                    for target in targets:
                        carried, reason = self._carry(target, value, base.get(target), destination, slots)
                        if carried is None:
                            refused.append(f"{target} ({reason})")
                            continue
                        emitted[target] = carried
                        renamed.append(f"{key} -> {target}")
                elif self.opts.keep_unknown or key in _UNIVERSAL_EXTRAS:
                    emitted[key] = value
                    kept_unknown.append(key)
                else:
                    dropped.append(key)
            if renamed:
                self.report.adjusted.append(
                    f"{len(renamed)} setting(s) carried under the destination's name: "
                    + ", ".join(sorted(renamed)[:6])
                )
            if respelled:
                self.report.adjusted.append(
                    f"{len(respelled)} value(s) re-spelled for the destination: "
                    + ", ".join(sorted(respelled)[:6])
                )
            if refused:
                self.report.dropped.append(
                    f"{len(refused)} value(s) kept at the destination's setting because the "
                    "destination would reject them: " + ", ".join(sorted(refused)[:6])
                )
            if dropped:
                self.report.dropped.append(
                    f"{len(dropped)} setting key(s) the destination does not understand: "
                    + ", ".join(sorted(dropped)[:12])
                    + (" …" if len(dropped) > 12 else "")
                )
            if kept_unknown:
                self.report.adjusted.append(
                    f"{len(kept_unknown)} key(s) kept although the kit does not declare them "
                    "(--keep-unknown): " + ", ".join(sorted(kept_unknown)[:8])
                    + (" …" if len(kept_unknown) > 8 else "")
                )
            self.out = ProjectSettings(emitted)
            # Re-assert geometry from the kit even if a key was absent from it.
            for key in GEOMETRY_KEYS:
                if key in base.data:
                    self.out.set(key, base.get(key))
            self.report.carried.append(
                f"{sum(1 for k in self.settings.keys() if k in base.data and classify(k) != 'machine')} "
                "process/appearance key(s) carried from the source project"
            )
            machine_keys = sum(1 for k in base.keys() if classify(k) == "machine")
            self.report.carried.append(
                f"{machine_keys} machine key(s) taken from the destination kit, not the source"
            )
        else:
            self.out = self.settings.copy()
            self.report.carried.append("all source settings carried unchanged (no kit)")

        self._clamp_machine_limits()

        if self.opts.extruders is not None:
            self._force_extruder_count(self.opts.extruders)

        self._emit_filament_block()
        self._emit_identity()
        self._emit_flush_matrix()
        self._emit_diff()

    def _clamp_machine_limits(self) -> None:
        """Hold speeds and accelerations inside the destination's declared maxima.

        Orca and Bambu both cap these on load and tell the user the setting
        "exceeds the printer's maximum".  Capping here means the translated
        project opens quietly, and the report still says what changed.
        """
        clamped: list[str] = []
        for key in list(self.out.keys()):
            value = self.out.get(key)
            replacement, ceiling = clamp_to_limits(key, value, self.out.data)
            if ceiling is None:
                continue
            if isinstance(replacement, list):
                self.out.set_list(key, replacement)
            else:
                self.out.set(key, replacement)
            shown = scalar_of(value)
            clamped.append(f"{key} {shown} -> {ceiling:g}")
        if clamped:
            self.report.adjusted.append(
                f"{len(clamped)} value(s) clamped to the destination's machine limits: "
                + ", ".join(sorted(clamped)[:8])
                + (" …" if len(clamped) > 8 else "")
            )

    def _force_extruder_count(self, count: int) -> None:
        current = self.out.extruder_count()
        if count == current:
            return
        for key in ("nozzle_diameter", "extruder_colour", "extruder_offset",
                    "printer_extruder_id", "printer_extruder_variant"):
            values = self.out.get_list(key)
            if values:
                self.out.set_list(key, _cycle(values, count))
        self.report.adjusted.append(f"extruder count forced from {current} to {count} (--extruders)")

    def _emit_filament_block(self) -> None:
        slots = self.out_filaments
        n = len(slots)
        extruders = max(1, self.out.extruder_count())

        self.out.set_list("filament_settings_id", [s["preset_id"] for s in slots])
        self.out.set_list("filament_type", [s["material"] for s in slots])
        self.out.set_list("filament_colour", [s["colour"] for s in slots])

        previous = self.settings.filaments()
        ids = self.settings.get_list("filament_ids")
        if ids:
            out_ids = []
            for index in range(n):
                source = previous[index] if index < len(previous) else None
                if source is not None and index < len(ids):
                    out_ids.append(ids[index])
                else:
                    match = [i for i, s in enumerate(previous) if s.material == slots[index]["material"]]
                    out_ids.append(ids[match[0]] if match and match[0] < len(ids) else "")
            self.out.set_list("filament_ids", out_ids)

        # Per-slot arrays: keep the source value where the slot count is
        # unchanged, otherwise cycle to the new length.
        # Per-slot arrays.  Only keys that are *known* to be per-filament are
        # reshaped, and only when their current length matches the source slot
        # count — anything else is left alone and reported, because a wrong
        # reshape (``flush_volumes_vector`` is per extruder, ``machine_max_*``
        # is [max, min]) silently corrupts a working project.
        n_src = len(self.settings.filaments())
        resized: list[str] = []
        untouched: list[str] = []
        for key in list(self.out.keys()):
            if not is_per_filament(key):
                continue
            value = self.out.get(key)
            if not isinstance(value, list) or len(value) == n:
                continue
            if n_src and len(value) != n_src:
                untouched.append(key)
                continue
            self.out.set_list(key, _cycle(value, n))
            resized.append(key)
        if resized:
            self.report.adjusted.append(
                f"{len(resized)} per-filament array(s) resized to {n} slot(s): "
                + ", ".join(sorted(resized)[:8]) + (" …" if len(resized) > 8 else "")
            )
        if untouched:
            self.report.warnings.append(
                f"{len(untouched)} per-slot key(s) did not have one entry per source "
                "filament and were left untouched: " + ", ".join(sorted(untouched)[:8])
            )

        # filament -> extruder assignment
        if n:
            if extruders == 1:
                self.out.set_list("filament_map", ["1"] * n)
            else:
                self.out.set_list("filament_map", [str(min(i + 1, extruders)) for i in range(n)])

        if n == 1:
            self.report.carried.append("single filament slot: colour and material carried")

    def _emit_identity(self) -> None:
        if self.kit is not None:
            if self.kit.machine_id:
                self.out.set("printer_settings_id", self.kit.machine_id)
            if self.kit.process_id:
                self.out.set("print_settings_id", self.kit.process_id)
            self.report.adjusted.append(
                "project points at the destination's own preset names "
                f"({self.kit.machine_id!r} / {self.kit.process_id!r}) instead of the source's"
            )
        if "nozzle_diameter" in self.out:
            diameters = self.out.get_list("nozzle_diameter")
            extruders = self.out.extruder_count()
            if len(diameters) != extruders:
                self.report.warnings.append(
                    f"nozzle_diameter has {len(diameters)} entries for {extruders} extruder(s)"
                )

    def _emit_flush_matrix(self) -> None:
        matrix = self.out.get("flush_volumes_matrix")
        if matrix is None:
            return
        source_n = len(self.settings.filaments())
        if len(self.out_filaments) == source_n:
            self.report.carried.append("flush volumes matrix carried unchanged")
            return
        self.out.data.pop("flush_volumes_matrix", None)
        self.report.dropped.append(
            "flush_volumes_matrix dropped: its row encoding differs between writers and a "
            "wrong guess is worse than the destination's own default"
        )

    def _emit_diff(self) -> None:
        if self.kit is None:
            existing = self.settings.get("different_settings_to_system")
            if existing is not None:
                # Without a destination baseline there is nothing to diff
                # against, and recomputing would claim the presets are stock.
                self.out.set("different_settings_to_system", existing)
                return
        baseline = self.kit.settings if self.kit is not None else self.settings
        entries = build_different_settings(self.out, baseline)
        self.out.set("different_settings_to_system", entries)

    def _write_settings(self) -> None:
        self.pkg.put_text("Metadata/project_settings.config", self.out.dumps().decode("utf-8"))

    # -- 5. model settings --------------------------------------------------
    def _emit_model_settings(self) -> None:
        ms = self.ms
        assert ms is not None
        remapped_objects = 0
        remapped_parts = 0
        clamped: list[str] = []
        colour_roles: dict[str, int] = {}
        n_out = max(1, len(self.out_filaments))

        def note_clamp(element: str, old: int, new: int) -> None:
            message = f"{element} assigned filament {old} but only {n_out} slot(s) exist: mapped to {new}"
            if message not in clamped:
                clamped.append(message)

        for obj_el in ms.object_elements():
            changed = _remap_extruder_meta(
                obj_el, self.filament_map, ms, n_out, note_clamp
            )
            remapped_objects += 1 if changed else 0
            for part_el in ms.parts(obj_el):
                if _remap_extruder_meta(part_el, self.filament_map, ms, n_out, note_clamp):
                    remapped_parts += 1
                role = ms.part_type(part_el)
                colour_roles[role] = colour_roles.get(role, 0) + 1
                if self.opts.drop_provenance:
                    for key in ("source_file", "source_object_id", "source_volume_id",
                                "source_offset_x", "source_offset_y", "source_offset_z"):
                        ms.drop_object_meta(part_el, key)

        if remapped_objects or remapped_parts:
            self.report.adjusted.append(
                f"filament assignment remapped on {remapped_objects} object(s) and "
                f"{remapped_parts} part(s)"
            )
        for message in clamped:
            self.report.adjusted.append(message)
        special = {k: v for k, v in colour_roles.items() if k != "normal_part" and v}
        if special:
            self.report.carried.append(
                "part roles carried: " + ", ".join(f"{v} {k}" for k, v in sorted(special.items()))
            )

        self._emit_plates()
        self._drop_dangling_thumbnails()
        ms.rebuild_assemble(self.model)
        if len(ms.plate_elements()) != self.plates_before:
            self.report.adjusted.append(
                f"plate count changed from {self.plates_before} to {len(ms.plate_elements())}"
            )
        self.pkg.put_text(MODEL_SETTINGS_PATH, ms.to_bytes().decode("utf-8"))

    def _emit_plates(self) -> None:
        ms = self.ms
        assert ms is not None
        n = len(self.out_filaments)
        for plate in ms.plate_elements():
            for key in ("filament_maps", "filament_volume_maps"):
                raw = ms.object_meta(plate, key)
                if raw is None:
                    continue
                values = _parse_int_list(raw)
                if values is None:
                    continue
                remapped = [self.filament_map.get(v, v) for v in values]
                if len(remapped) != n:
                    remapped = [self.filament_map.get(v, v) for v in _cycle_ints(remapped, n)]
                ms.set_object_meta(plate, key, _int_list_text(remapped))

    def _drop_dangling_thumbnails(self) -> None:
        ms = self.ms
        assert ms is not None
        for plate in ms.plate_elements():
            for key in ("thumbnail_file", "thumbnail_no_light_file", "top_file", "pick_file"):
                target = ms.object_meta(plate, key)
                if target and not self.pkg.has(target.lstrip("/")):
                    ms.drop_object_meta(plate, key)
                    self.report.adjusted.append(f"dropped plate metadata {key} -> missing {target}")

    # -- 6. auxiliary metadata ---------------------------------------------
    def _emit_aux(self) -> None:
        if self.opts.drop_aux:
            for path in (LAYER_RANGES_PATH, CUSTOM_GCODE_PATH, FILAMENT_SEQUENCE_PATH):
                if self.pkg.has(path):
                    self.pkg.remove(path)
            self.report.dropped.append(
                "layer ranges, per-layer colour changes and filament sequencing were "
                "removed on request (--drop-aux); the destination will recompute defaults"
            )
            return

        ranges = AuxFile.from_package(self.pkg, LAYER_RANGES_PATH)
        if ranges is not None:
            if ranges.drop_objects(self.dropped_objects):
                self.pkg.remove(LAYER_RANGES_PATH)
                self.report.dropped.append(
                    "layer_config_ranges.xml removed: it referenced objects that no longer exist"
                )
            else:
                # Byte-identical: object ids are not renumbered, so there is
                # nothing to rewrite and the original schema is preserved.
                self.pkg.put(LAYER_RANGES_PATH, ranges.raw)
                count = ranges.count(b"<range")
                self.report.counts["height ranges"] = count
                if count:
                    self.report.carried.append(
                        f"{count} height-range modifier(s) with their per-range overrides"
                    )

        gcode = AuxFile.from_package(self.pkg, CUSTOM_GCODE_PATH)
        if gcode is not None:
            colours = gcode.count(b'type="color"')
            pauses = gcode.count(b'type="pause"')
            if len(self.out_filaments) != len(self.settings.filaments()):
                changed = gcode.remap_filament_refs(self.filament_map)
                if changed:
                    self.report.adjusted.append(
                        f"{changed} colour-change filament reference(s) remapped to surviving slots"
                    )
            n_out = max(1, len(self.out_filaments))
            refs = {ref for ref in gcode.extruder_refs() if ref}
            if refs and max(refs) > n_out:
                self.pkg.remove(CUSTOM_GCODE_PATH)
                self.report.dropped.append(
                    "custom_gcode_per_layer.xml removed: it referenced filament "
                    f"{max(refs)} but only {n_out} slot(s) exist"
                )
            else:
                self.pkg.put(CUSTOM_GCODE_PATH, gcode.raw)
                parts = []
                if colours:
                    parts.append(f"{colours} colour change(s)")
                if pauses:
                    parts.append(f"{pauses} pause(s)")
                if parts:
                    self.report.carried.append(
                        ", ".join(parts) + " at their original Z heights"
                    )

        sequence = FilamentSequence.from_package(self.pkg)
        if sequence is not None:
            if len(self.out_filaments) == len(self.settings.filaments()):
                self.pkg.put(FILAMENT_SEQUENCE_PATH, sequence.to_bytes())
            else:
                self.pkg.remove(FILAMENT_SEQUENCE_PATH)
                self.report.dropped.append(
                    "filament_sequence.json dropped: its indices are not consistently 0- or "
                    "1-based across writers, and the destination recomputes it"
                )

    # -- 7. dialect ---------------------------------------------------------
    def _stamp_dialect(self) -> None:
        version = (
            self.opts.version
            or (self.kit.settings.get_str("version") if self.kit else "")
            or self.source.version
            or ""
        )
        # The destination's own stamp is the only non-invented one: a kit
        # project was written by the destination, so its Application string and
        # its use (or not) of the Bambu 3MF marker are exactly what that writer
        # emits.  Without a kit, keep the source's stamp rather than guessing.
        app = (
            self.opts.app_string
            or (self.kit.application if self.kit else "")
            or self.source.application
            or self.target_flavor.app_string(version)
        )
        if app:
            self.model.set_metadata("Application", app)
        marker = self.kit.has_bambu_marker if self.kit else None
        if marker is None:
            marker = bool(self.model.metadata("BambuStudio:3mfVersion"))
        if marker:
            self.model.set_metadata("BambuStudio:3mfVersion", "1")
        else:
            self.model.remove_metadata("BambuStudio:3mfVersion")
        self.pkg.put_text(self.model.path, self.model.to_bytes().decode("utf-8"))

        self.out.set("from", "project")
        if version:
            self.out.set("version", version)
        # Settings are written last so the stamp above is part of the file.
        self._write_settings()
        self.report.adjusted.append(
            f"dialect restamped for {self.target_flavor.label}: Application={app!r}, from='project'"
        )
        if self.source.key != self.target_flavor.key:
            self.report.adjusted.append(
                f"project was written by [{self.source.key}] and is being retargeted to "
                f"[{self.target_flavor.key}]"
            )

        if self.pkg.has(SLICE_INFO_PATH):
            header = {}
            try:
                from .model import slice_info_header
                header = slice_info_header(self.pkg)
            except Exception:
                pass
            self.pkg.remove(SLICE_INFO_PATH)
            detail = ", ".join(f"{k}={v}" for k, v in sorted(header.items())) or "no header"
            self.report.dropped.append(
                f"stale slice_info.config from the source slicer removed ({detail})"
            )
        if self.pkg.has(PRINT_PROFILE_PATH):
            self.pkg.remove(PRINT_PROFILE_PATH)
            self.report.dropped.append("source print_profile.config removed (regenerated on save)")

    # -- 8. packaging -------------------------------------------------------
    def _cleanup(self) -> None:
        self._drop_previews_if_plates_changed()
        self.pkg.ensure_model_relationship(self.model.path)
        missing = self.pkg.ensure_part_relationships()
        if missing:
            self.report.adjusted.append(
                f"{len(missing)} part relationship(s) added for {missing[0]}"
                + (" …" if len(missing) > 1 else "")
            )
        added = self.pkg.ensure_content_types()
        if added:
            self.report.adjusted.append(
                "content-type defaults declared for: " + ", ".join(added)
            )

    def _drop_previews_if_plates_changed(self) -> None:
        if self.ms is None:
            return
        if len(self.ms.plate_elements()) == self.plates_before:
            return
        removed = 0
        for name in list(self.pkg.names()):
            base = posixpath.basename(name)
            if base.startswith(("plate_", "top_", "pick_")) and base.endswith(".png"):
                self.pkg.remove(name)
                removed += 1
        if removed:
            self.report.adjusted.append(
                f"{removed} plate preview image(s) removed because the plate set changed"
            )


# ---------------------------------------------------------------------------
# small helpers used above
# ---------------------------------------------------------------------------

#: Settings the translator writes itself; never copied from the source loop.
_SELF_COMPUTED = frozenset({"different_settings_to_system"})

_UNIVERSAL_EXTRAS = frozenset({
    # Keys every Orca-lineage reader accepts even when the kit omits them.
    "thumbnails", "version", "from", "is_custom_defined", "printer_model",
    "printer_variant", "nozzle_diameter", "printer_technology", "gcode_flavor",
})


def _remap_extruder_meta(
    el,
    mapping: dict[int, int],
    ms: ModelSettings,
    n_out: int,
    on_clamp=None,
) -> bool:
    raw = ms.part_meta(el, "extruder") if el.tag == "part" else ms.object_meta(el, "extruder")
    if raw is None:
        return False
    try:
        index = int(raw)
    except ValueError:
        return False
    if index in mapping:
        target = mapping[index]
    else:
        target = ((index - 1) % max(1, n_out)) + 1
        if on_clamp is not None:
            on_clamp(f"{el.tag} {el.get('id')}", index, target)
    if target == index:
        return False
    if el.tag == "part":
        ms.set_part_meta(el, "extruder", str(target))
    else:
        ms.set_object_meta(el, "extruder", str(target))
    return True


def _cycle_ints(values: list[int], n: int) -> list[int]:
    if n <= 0:
        return []
    if not values:
        return [1] * n
    return [values[i % len(values)] for i in range(n)]


def _match_preset(slots, material: str, colour: str, index: int):
    """Pick a destination filament preset for one project slot."""
    for slot in slots:
        if slot.material and slot.material == material and slot.colour.lower() == colour.lower():
            return slot, "exact"
    for slot in slots:
        if slot.material and slot.material == material:
            return slot, "material"
    for slot in slots:
        if slot.colour and slot.colour.lower() == colour.lower():
            return slot, "colour"
    if material:
        return None, "none"
    slot = slots[index % len(slots)]
    return slot, "position"
