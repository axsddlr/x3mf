"""``Metadata/project_settings.config`` — the slicer's resolved settings dump.

Two facts drive everything here, both observed in a real Orca 2.4.2 project:

* The file is a flat JSON object of ~650 keys.  Most values are **strings**
  or **lists of strings**, even when they represent numbers or booleans.
* The values are already *resolved*: preset inheritance has been flattened
  into this dump.  ``inherits`` pointed at a base profile that may not exist
  on the machine we are translating to, so resolution has to happen on our
  side of the wire.

Everything in this module is about classification: which key belongs to the
printer, which to the process, which to a filament slot.  Classification is
heuristic (prefix based) and is therefore *auditable* — see
:func:`classify` and the ``GEOMETRY_KEYS`` safety set, which the translator
asserts against the destination preset instead of trusting the heuristic.
"""

from __future__ import annotations

import json
from typing import Any, Iterable

PATH = "Metadata/project_settings.config"

#: Keys that describe the physical machine.  These must come from the
#: destination, never from the source: carrying them across is how a project
#: "opens fine" and then prints off the bed.
GEOMETRY_KEYS = frozenset({
    "printer_model",
    "printer_variant",
    "printer_settings_id",
    "printer_technology",
    "nozzle_diameter",
    "nozzle_type",
    "nozzle_volume",
    "printer_extruder_id",
    "printer_extruder_variant",
    "printer_extruder_offset_to_nozzle",
    "bed_shape",
    "printable_area",
    "printable_height",
    "bed_exclude_area",
    "bed_custom_model",
    "bed_custom_texture",
    "extruder_count",
    "extruder_colour",
    "extruder_offset",
    "gcode_flavor",
    "use_relative_e_distances",
    "single_extruder_multi_material",
    "machine_max_acceleration_x",
    "machine_max_acceleration_y",
    "machine_max_acceleration_z",
    "machine_max_acceleration_e",
    "machine_max_feedrate_x",
    "machine_max_feedrate_y",
    "machine_max_feedrate_z",
    "machine_max_feedrate_e",
    "machine_max_jerk_x",
    "machine_max_jerk_y",
    "machine_max_jerk_z",
    "machine_max_jerk_e",
    "machine_max_junction_deviation",
})

#: Prefixes that unambiguously belong to the machine profile.
MACHINE_PREFIXES = (
    "machine_", "printer_", "printable_", "bed_", "extruder_",
    "physical_extruder_", "host_", "printhost_", "print_host",
    "bbl_use_printhost", "silent_mode", "thumbnails",
)

#: Prefixes that unambiguously belong to a filament slot.
FILAMENT_PREFIXES = (
    "filament_", "flush_volumes_", "default_filament_",
    "auxiliary_filament", "nozzle_temperature", "hot_plate_temp",
    "cool_plate_temp", "eng_plate_temp", "textured_plate_temp",
    "idle_temperature", "temperature_vitrification", "chamber_temperature",
    "overhang_fan_speed", "fan_max_speed", "fan_min_speed",
    "slow_down_for_layer_cooling", "slow_down_layer_time",
    "required_nozzle_HRC", "filament_density", "filament_cost",
)

#: Machine keys that are *not* geometry and are safe to carry from the source
#: when the destination says nothing about them (calibration, host settings).
CARRYABLE_MACHINE_KEYS = frozenset({
    "print_host", "printhost_apikey", "printhost_cafile", "printhost_port",
    "printhost_ssl_ignore_revoke", "print_host_webui", "bbl_use_printhost",
    "bbl_calib_mark_logo", "silent_mode",
})

DOMAINS = ("machine", "process", "filament")

#: G-code templates belong to the machine profile, not the process: they are
#: written against one firmware and one motion system.  Carrying them to another
#: printer makes it execute sequences it was never configured for.
GCODE_KEYS = frozenset({
    "start_gcode", "end_gcode", "before_layer_gcode", "between_objects_gcode",
    "layer_change_gcode", "before_layer_change_gcode", "change_filament_gcode",
    "printing_by_object_gcode", "time_lapse_gcode", "template_custom_gcode",
    "change_extrusion_role_gcode", "process_change_extrusion_role_gcode",
    "wrapping_detection_gcode", "pause_gcode", "toolchange_gcode",
})


def is_gcode_key(key: str) -> bool:
    """True for machine G-code templates; filament-scoped ones stay filament."""
    if key.startswith("filament_"):
        return False
    return key in GCODE_KEYS or key.endswith("_gcode")


def classify(key: str) -> str:
    """Return ``machine``/``process``/``filament`` for a settings key."""
    if key in GEOMETRY_KEYS:
        return "machine"
    if key in CARRYABLE_MACHINE_KEYS:
        return "machine"
    if key.startswith(FILAMENT_PREFIXES):
        return "filament"
    if is_gcode_key(key):
        return "machine"
    if key.startswith(MACHINE_PREFIXES):
        return "machine"
    return "process"


def is_geometry(key: str) -> bool:
    return key in GEOMETRY_KEYS


#: Keys that hold exactly one entry per filament slot.
PER_FILAMENT_PREFIXES = (
    "filament_",
    "default_filament_",
    "auxiliary_filament_",
    "nozzle_temperature",
    "hot_plate_temp",
    "cool_plate_temp",
    "eng_plate_temp",
    "textured_plate_temp",
    "idle_temperature",
    "temperature_vitrification",
    "chamber_temperature",
    "overhang_fan_speed",
    "fan_max_speed",
    "fan_min_speed",
    "slow_down_for_layer_cooling",
    "slow_down_layer_time",
    "required_nozzle_HRC",
)

#: Keys that look per-filament by name but are sized by something else.
#: ``flush_volumes_vector`` is per *extruder*, ``machine_max_*`` is [max, min],
#: and ``wiping_volumes_extruders`` is a fixed 2N table.  Reshaping any of these
#: by filament count corrupts them.
NOT_PER_FILAMENT = frozenset({
    "filament_map",
    "filament_settings_id",
    "filament_type",
    "filament_colour",
    "filament_ids",
    "filament_map_mode",
    "filament_volume_map",
    "flush_volumes_vector",
    "flush_volumes_matrix",
    "flush_multiplier",
    "wiping_volumes_extruders",
    "extruder_ams_count",
    "extruder_printable_area",
    "filament_wipe",
})


def is_per_filament(key: str) -> bool:
    """True when a key is expected to carry exactly one value per filament slot."""
    if key in NOT_PER_FILAMENT or key.startswith("machine_"):
        return False
    return key.startswith(PER_FILAMENT_PREFIXES)


class ProjectSettings:
    """A typed view over the flat settings dictionary."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data

    # -- io -----------------------------------------------------------------
    @classmethod
    def loads(cls, raw: bytes | str) -> "ProjectSettings":
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8-sig")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"project settings are not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("project settings must be a JSON object")
        return cls(data)

    @classmethod
    def from_package(cls, package) -> "ProjectSettings":
        raw = package.get(PATH)
        if raw is None:
            return cls({})
        return cls.loads(raw)

    def dumps(self) -> bytes:
        return (json.dumps(self.data, indent=4, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")

    def copy(self) -> "ProjectSettings":
        return ProjectSettings(json.loads(json.dumps(self.data)))

    # -- scalar access ------------------------------------------------------
    def __contains__(self, key: str) -> bool:
        return key in self.data

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def get_str(self, key: str, default: str = "") -> str:
        value = self.data.get(key, default)
        if isinstance(value, list):
            return str(value[0]) if value else default
        return str(value)

    def get_list(self, key: str) -> list[str]:
        """Read a key that *should* be a list; accept a scalar, split on ';'."""
        value = self.data.get(key)
        if value is None:
            return []
        if isinstance(value, list):
            return [str(v) for v in value]
        if isinstance(value, str):
            if not value:
                return []
            return [v.strip() for v in value.split(";") if v.strip() != ""] if ";" in value else [value]
        return [str(value)]

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    def set_list(self, key: str, values: Iterable[Any]) -> None:
        self.data[key] = [str(v) for v in values]

    # -- slot counts --------------------------------------------------------
    def extruder_count(self) -> int:
        diameters = self.get_list("nozzle_diameter")
        if diameters:
            return len(diameters)
        return max(1, len(self.get_list("printer_extruder_id")) or 1)

    def filament_count(self) -> int:
        for key in ("filament_settings_id", "filament_type", "filament_colour"):
            values = self.get_list(key)
            if values:
                return len(values)
        return 1

    def filaments(self) -> list["FilamentSlot"]:
        n = self.filament_count()
        ids = self.get_list("filament_settings_id")
        types = self.get_list("filament_type")
        colours = self.get_list("filament_colour")
        out = []
        for i in range(n):
            out.append(FilamentSlot(
                index=i,
                preset_id=ids[i] if i < len(ids) else "",
                material=types[i] if i < len(types) else "",
                colour=colours[i] if i < len(colours) else "",
            ))
        return out

    # -- aggregate accessors ------------------------------------------------
    def keys(self) -> list[str]:
        return list(self.data)

    def domain_of(self, key: str) -> str:
        return classify(key)

    def values_in_domain(self, domain: str) -> dict[str, Any]:
        return {k: v for k, v in self.data.items() if classify(k) == domain}

    def unsigned_keys(self) -> list[str]:
        """Keys whose classification is a guess (machine prefix but not geometry)."""
        return [k for k in self.data if is_geometry(k) or classify(k) == "machine"]

    PRESET_KEYS = {
        "machine": "printer_settings_id",
        "process": "print_settings_id",
        "filament": "filament_settings_id",
    }

    def preset_id(self, kind: str) -> str:
        return self.get_str(self.PRESET_KEYS[kind])


class FilamentSlot:
    __slots__ = ("index", "preset_id", "material", "colour")

    def __init__(self, index: int, preset_id: str, material: str, colour: str) -> None:
        self.index = index
        self.preset_id = preset_id
        self.material = material
        self.colour = colour

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"FilamentSlot({self.index}, {self.preset_id!r}, {self.material!r}, {self.colour!r})"


def config_diff(a: ProjectSettings, b: ProjectSettings, keys: Iterable[str] | None = None) -> list[str]:
    """Keys whose values differ between two settings dumps."""
    keys = list(keys) if keys is not None else sorted(set(a.keys()) | set(b.keys()))
    diff = []
    for key in keys:
        if a.get(key) != b.get(key):
            diff.append(key)
    return diff


def semicolon(values: Iterable[str]) -> str:
    return ";".join(values)


def split_semicolon(value: str) -> list[str]:
    if not value:
        return []
    return [v for v in value.split(";") if v]


#: Order of the ``different_settings_to_system`` entries, inferred from a real
#: Orca 2.4.2 project: entry 0 held process keys, entry 1 filament keys, entry 2
#: printer keys (empty because that project used a stock printer preset).
DIFF_ORDER = ("process", "filament", "machine")

#: Keys that describe the project rather than a preset value.  They must never
#: appear in ``different_settings_to_system``: the field is read back as "these
#: parameters were customised against the named preset", and listing the field
#: itself (or ``from``/``version``) makes a reader try to reset metadata to an
#: empty value — Orca rejects such a project with "Invalid value provided for
#: parameter layer_height".
NON_DIFFABLE = frozenset({
    "different_settings_to_system",
    "from",
    "version",
    "is_custom_defined",
    "name",
    "inherits",
    "printer_settings_id",
    "print_settings_id",
    "filament_settings_id",
})


def build_different_settings(
    emitted: ProjectSettings,
    baseline: ProjectSettings,
    order: tuple[str, ...] = DIFF_ORDER,
) -> list[str]:
    """Rebuild ``different_settings_to_system`` as ';'-joined keys per domain.

    This field only tells the UI which presets are "modified"; values are
    loaded from this same file regardless.  It must nevertheless be *consistent*
    with the emitted values, because a stale copy is one of the ways a
    foreign project confuses a strict reader.
    """
    out = []
    for domain in order:
        keys = [
            k for k in emitted.keys()
            if classify(k) == domain and k not in NON_DIFFABLE
        ]
        out.append(semicolon(config_diff(emitted, baseline, keys)))
    return out
