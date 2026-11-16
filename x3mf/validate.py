"""Output invariants for an Orca-lineage 3MF project.

The translator must not write a file it cannot defend, so this module states
what "opens clean" means in terms that are checkable without the slicer:

* the OPC container is complete (content types, relationships, parts);
* every object/instance/component reference resolves;
* every per-slot array has exactly one entry per filament;
* every machine field the destination cares about came from the destination;
* nothing from the source slicer's last slice session is left behind.

Rules are numbered so a report stays quotable.  ``error`` means a reader is
likely to reject or mis-render the project; ``warning`` means the project will
load but something is worth a human look.
"""

from __future__ import annotations

import posixpath
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from .container import CONTENT_TYPES_PATH, Package
from .model import (
    CUSTOM_GCODE_PATH,
    FILAMENT_SEQUENCE_PATH,
    LAYER_RANGES_PATH,
    SLICE_INFO_PATH,
    AuxFile,
    FilamentSequence,
    Model3D,
    ModelSettings,
    localname,
    q,
    NS,
)
from .settings import GEOMETRY_KEYS, ProjectSettings


@dataclass(frozen=True)
class Issue:
    level: str      # "error" | "warning" | "info"
    code: str
    message: str

    def __str__(self) -> str:
        return f"[{self.level}] {self.code}: {self.message}"


class _Collector:
    def __init__(self) -> None:
        self.issues: list[Issue] = []

    def error(self, code: str, message: str) -> None:
        self.issues.append(Issue("error", code, message))

    def warn(self, code: str, message: str) -> None:
        self.issues.append(Issue("warning", code, message))

    def info(self, code: str, message: str) -> None:
        self.issues.append(Issue("info", code, message))

    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "error"]


#: Project keys a reader needs to rebuild a print setup.
REQUIRED_SETTINGS = (
    "printer_model",
    "printer_variant",
    "nozzle_diameter",
    "filament_type",
    "filament_colour",
    "filament_settings_id",
    "printer_settings_id",
    "print_settings_id",
    "version",
    "from",
)

_COLOUR_RE = re.compile(r"^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$")


def validate(package: Package, kit=None) -> list[Issue]:
    """Return every invariant violation found in ``package``."""
    c = _Collector()
    _check_container(package, c)

    try:
        model = Model3D.from_package(package)
    except Exception as exc:
        c.error("V002", f"cannot read the primary model part: {exc}")
        return c.issues
    _check_model(package, model, c)

    settings = ProjectSettings.from_package(package)
    _check_settings(settings, kit, c)

    try:
        ms = ModelSettings.from_package(package)
    except Exception as exc:
        c.error("V004", f"model_settings.config is not parseable XML: {exc}")
        ms = None
    if ms is not None:
        _check_model_settings(ms, model, settings, package, c)

    _check_aux(package, model, settings, c)
    _check_staleness(package, model, c)
    return c.issues


# ---------------------------------------------------------------------------
# container
# ---------------------------------------------------------------------------

def _check_container(package: Package, c: _Collector) -> None:
    if not package.has(CONTENT_TYPES_PATH):
        c.error("V001", "[Content_Types].xml is missing")
        return
    try:
        _declared(package)
    except ET.ParseError as exc:
        c.error("V001", f"[Content_Types].xml is not parseable: {exc}")
    try:
        rels = package.relationships()
    except Exception as exc:
        c.error("V003", f"_rels/.rels is not parseable: {exc}")
        return
    if not rels:
        c.error("V003", "_rels/.rels declares no relationships")
    model_path = None
    for rel in rels:
        if rel["type"].rstrip("/").endswith("3dmodel"):
            model_path = rel["target"].lstrip("/")
    if model_path is None:
        c.error("V003", "no 3dmodel relationship in _rels/.rels")
    elif not package.has(model_path):
        c.error("V003", f"primary model relationship points at missing part {model_path}")

    for name in package.names():
        if "\\" in name or name.startswith("/"):
            c.error("V005", f"entry name is not package-relative: {name!r}")

    # Only the extensions a reader must be able to dispatch are checked here.
    # Slicers routinely ship .config/.json/.xml metadata without declaring a
    # content type and read it back fine, so those are not defects.
    declared = _declared(package)
    for ext in ("rels", "model"):
        if ext not in declared:
            c.error("V006", f"no default content type declared for .{ext}")
    if any(n.endswith(".png") for n in package.names()) and "png" not in declared:
        c.warn("V006", "no default content type declared for .png")


def _declared(package: Package) -> set[str]:
    raw = package.require(CONTENT_TYPES_PATH).decode("utf-8-sig")
    root = ET.fromstring(raw)
    out = set()
    for el in root:
        if localname(el.tag) == "Default":
            out.add((el.get("Extension") or "").lower())
    return out


# ---------------------------------------------------------------------------
# model
# ---------------------------------------------------------------------------

def _check_model(package: Package, model: Model3D, c: _Collector) -> None:
    raw = package.get(model.path) or b""
    occurrences = len(re.findall(rb"<object\b", raw))
    unique = len(model.objects())
    if occurrences != unique:
        c.error(
            "V010",
            f"model declares {occurrences} <object> elements but {unique} unique ids "
            "(duplicate object ids)",
        )
    if not model.objects():
        c.error("V011", "model has no objects")
    if model.build is None or not model.build_items():
        c.error("V011", "model has no <build> items; nothing is placed on a plate")

    ids = set(model.objects())
    for object_id, obj in model.objects().items():
        for comp in model.components(obj):
            path = comp["path"]
            if not path:
                continue
            if not package.has(path):
                c.error("V012", f"object {object_id} references missing part {path}")
                continue
            # The component must be resolvable *through a relationship*.
            rels = _part_relationships(package, model.path)
            if path not in rels:
                c.error(
                    "V013",
                    f"component path {path} has no relationship in the model's rels part",
                )
            part = model.parts.get(path)
            if part is not None and comp["objectid"] not in part.object_ids:
                c.error(
                    "V014",
                    f"object {object_id} component references object "
                    f"{comp['objectid']!r} absent from {path}",
                )

    for item in model.build_items():
        object_id = item.get("objectid", "")
        if object_id not in ids:
            c.error("V015", f"build item references unknown object {object_id!r}")


def _part_relationships(package: Package, model_path: str) -> set[str]:
    rels_path = posixpath.join(
        posixpath.dirname(model_path), "_rels", posixpath.basename(model_path) + ".rels"
    )
    raw = package.get(rels_path)
    if raw is None:
        return set()
    try:
        root = ET.fromstring(raw.decode("utf-8-sig"))
    except ET.ParseError:
        return set()
    return {
        (el.get("Target") or "").lstrip("/")
        for el in root
        if localname(el.tag) == "Relationship"
    }


# ---------------------------------------------------------------------------
# project settings
# ---------------------------------------------------------------------------

def _check_settings(settings: ProjectSettings, kit, c: _Collector) -> None:
    if not settings.keys():
        c.error("V040", "project_settings.config is missing or empty")
        return

    for key in REQUIRED_SETTINGS:
        if key not in settings:
            c.error("V040", f"required setting {key!r} is absent")

    for key, value in settings.data.items():
        if isinstance(value, list):
            if any(not isinstance(v, (str, int, float)) for v in value):
                c.error("V041", f"{key} is a list containing non-scalar values")
        elif not isinstance(value, (str, int, float, bool)):
            c.error("V041", f"{key} has an unsupported value type {type(value).__name__}")

    n_ext = settings.extruder_count()
    n_fil = settings.filament_count()

    diameters = settings.get_list("nozzle_diameter")
    if not diameters:
        c.error("V042", "nozzle_diameter is absent")
    elif len(diameters) != n_ext:
        c.error(
            "V042",
            f"nozzle_diameter has {len(diameters)} entries but the extruder count is {n_ext}",
        )

    for key in ("filament_type", "filament_colour", "filament_settings_id"):
        values = settings.get_list(key)
        if values and len(values) != n_fil:
            c.error(
                "V043",
                f"{key} has {len(values)} entries but there are {n_fil} filament slot(s)",
            )

    for index, colour in enumerate(settings.get_list("filament_colour"), start=1):
        if colour and not _COLOUR_RE.match(colour):
            c.warn("V044", f"filament {index} colour {colour!r} is not #RRGGBB(AA)")

    for index, preset in enumerate(settings.get_list("filament_settings_id"), start=1):
        if not preset:
            c.warn("V045", f"filament {index} has no preset id")

    extruders = max(1, n_ext)
    for index, value in enumerate(settings.get_list("filament_map"), start=1):
        try:
            slot = int(value)
        except ValueError:
            c.error("V046", f"filament_map[{index}] is not an integer: {value!r}")
            continue
        if not 1 <= slot <= extruders:
            c.error(
                "V046",
                f"filament_map[{index}]={slot} is outside 1..{extruders}",
            )

    matrix = settings.get_list("flush_volumes_matrix")
    if matrix and len(matrix) not in (1, n_fil, n_fil * n_fil):
        c.warn(
            "V047",
            f"flush_volumes_matrix has {len(matrix)} entries; expected {n_fil} or "
            f"{n_fil * n_fil}",
        )

    diff = settings.get("different_settings_to_system")
    slots = settings.filament_count()
    if diff is None:
        c.warn("V048", "different_settings_to_system is absent; the UI will show no overrides")
    elif not isinstance(diff, list) or not all(isinstance(entry, str) for entry in diff):
        c.warn(
            "V048",
            "different_settings_to_system is not a list of strings; the slicer's "
            "override display will be wrong",
        )
    elif len(diff) not in (3, slots + 2):
        # Orca writes one entry per domain (process, filament, printer); Bambu
        # writes one per domain *plus one per filament slot*, so a four-filament
        # Bambu project legitimately has six. Anything else is malformed.
        c.warn(
            "V048",
            f"different_settings_to_system has {len(diff)} entries for {slots} filament "
            f"slot(s); expected 3 (process/filament/printer) or {slots + 2} "
            "(the same, one entry per filament)",
        )

    if kit is not None and kit.has_machine_geometry():
        for key in GEOMETRY_KEYS:
            if key not in kit.settings:
                continue
            if settings.get(key) != kit.settings.get(key):
                c.error(
                    "V049",
                    f"machine key {key!r} does not match the destination kit "
                    f"({settings.get(key)!r} != {kit.settings.get(key)!r})",
                )


# ---------------------------------------------------------------------------
# model settings
# ---------------------------------------------------------------------------

def _check_model_settings(
    ms: ModelSettings, model: Model3D, settings: ProjectSettings, package: Package, c: _Collector
) -> None:
    model_ids = set(model.objects())
    n_fil = max(1, settings.filament_count())

    for object_id in ms.object_ids():
        if object_id not in model_ids:
            c.error("V020", f"model_settings object {object_id} is not in the model")

    for obj in ms.object_elements():
        seen: set[str] = set()
        for part in ms.parts(obj):
            part_id = part.get("id", "")
            if part_id in seen:
                c.error("V021", f"object {obj.get('id')} has duplicate part id {part_id}")
            seen.add(part_id)
            matrix = ms.part_meta(part, "matrix")
            if matrix is not None:
                numbers = matrix.split()
                # Writers differ here: Orca/Bambu write a 4x4 for parts
                # (16 values) and a 3x4 for build/assemble transforms (12).
                if len(numbers) not in (12, 16) or not all(_is_float(x) for x in numbers):
                    c.error("V022", f"part {part_id} has a malformed matrix {matrix!r}")

    plate_ids: list[str] = []
    for plate in ms.plate_elements():
        plater_id = ms.object_meta(plate, "plater_id") or ""
        plate_ids.append(plater_id)
        if not plater_id.isdigit():
            c.error("V023", f"plate has non-numeric plater_id {plater_id!r}")
        for key in ("filament_maps", "filament_volume_maps"):
            raw = ms.object_meta(plate, key)
            if raw is None:
                continue
            values = raw.split()
            if len(values) != n_fil:
                c.error(
                    "V028",
                    f"plate {plater_id} {key} has {len(values)} entries for {n_fil} filament(s)",
                )
        if not ms.plate_instances(plate):
            c.warn("V029", f"plate {plater_id} has no instances")
        for inst in ms.plate_instances(plate):
            object_id = ms.instance_object_id(inst)
            instance_id = ms.instance_index(inst)
            if object_id not in model_ids:
                c.error("V024", f"plate {plater_id} references unknown object {object_id!r}")
                continue
            placed = model.instances_of(object_id)
            try:
                index = int(instance_id)
            except ValueError:
                index = -1
            if not 0 <= index < placed:
                c.error(
                    "V025",
                    f"plate {plater_id} uses instance {instance_id} of object {object_id} "
                    f"but only {placed} instance(s) exist",
                )
            for key in ("thumbnail_file", "top_file", "pick_file", "thumbnail_no_light_file"):
                target = ms.object_meta(plate, key)
                if target and not package.has(target.lstrip("/")):
                    c.error("V030", f"plate {plater_id} {key} points at missing {target}")

    if len(set(plate_ids)) != len(plate_ids):
        c.error("V023", f"duplicate plater_id values: {plate_ids}")

    # per-object / per-part filament indices are 1-based
    for obj in ms.object_elements():
        for el in [obj] + ms.parts(obj):
            raw = (ms.part_meta(el, "extruder") if el.tag == "part" else ms.object_meta(el, "extruder"))
            if raw is None:
                continue
            try:
                index = int(raw)
            except ValueError:
                c.error("V027", f"{el.tag} {el.get('id')} has non-numeric extruder {raw!r}")
                continue
            if not 1 <= index <= n_fil:
                c.error(
                    "V027",
                    f"{el.tag} {el.get('id')} assigns filament {index} but only "
                    f"{n_fil} exist",
                )

    assemble = ms.assemble()
    if assemble is not None:
        for item in assemble:
            object_id = item.get("object_id", "")
            if object_id not in model_ids:
                c.error("V031", f"assemble_item references unknown object {object_id!r}")
            transform = item.get("transform", "")
            if transform and (len(transform.split()) != 12 or not all(_is_float(v) for v in transform.split())):
                c.error("V031", f"assemble_item for {object_id} has a malformed transform")


def _is_float(text: str) -> bool:
    try:
        float(text)
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# auxiliary metadata
# ---------------------------------------------------------------------------

def _check_aux(
    package: Package, model: Model3D, settings: ProjectSettings, c: _Collector
) -> None:
    model_ids = set(model.objects())

    ranges = AuxFile.from_package(package, LAYER_RANGES_PATH)
    if ranges is not None:
        for object_id in ranges.object_ids():
            if object_id not in model_ids:
                c.error("V050", f"layer_config_ranges references unknown object {object_id!r}")
        # Validation may parse the document; the translator may not rewrite it.
        try:
            root = ET.fromstring(ranges.raw.decode("utf-8-sig"))
        except ET.ParseError:
            c.error("V050", "layer_config_ranges.xml is not parseable")
            root = None
        if root is not None:
            for el in root.iter():
                if localname(el.tag) != "range":
                    continue
                for attr in ("min_z", "max_z"):
                    if attr in el.attrib and not _is_float(el.get(attr, "")):
                        c.error("V050", f"range has non-numeric {attr}={el.get(attr)!r}")
                if "min_z" in el.attrib and "max_z" in el.attrib:
                    if float(el.get("min_z")) > float(el.get("max_z")):
                        c.error(
                            "V050",
                            f"range min_z {el.get('min_z')} > max_z {el.get('max_z')}",
                        )

    gcode = AuxFile.from_package(package, CUSTOM_GCODE_PATH)
    if gcode is not None:
        n_fil = max(1, settings.filament_count())
        for ref in gcode.extruder_refs():
            if ref and not 1 <= ref <= n_fil:
                c.error(
                    "V051",
                    f"custom_gcode_per_layer references filament {ref}, only {n_fil} exist",
                )

    sequence = FilamentSequence.from_package(package)
    if sequence is not None:
        n_fil = max(1, settings.filament_count())
        for key in sequence.plate_keys():
            for field in ("nozzle_sequence", "optimal_assignment", "sequence"):
                values = sequence.data.get(key, {}).get(field) or []
                for value in values:
                    if isinstance(value, int) and not 1 <= value <= n_fil:
                        c.warn(
                            "V052",
                            f"{FILAMENT_SEQUENCE_PATH}: {key}.{field} contains {value}, "
                            f"outside 1..{n_fil}",
                        )


# ---------------------------------------------------------------------------
# staleness
# ---------------------------------------------------------------------------

def _check_staleness(package: Package, model: Model3D, c: _Collector) -> None:
    if package.has(SLICE_INFO_PATH):
        c.warn(
            "V060",
            "slice_info.config is present: the project was saved after slicing, and that "
            "slice data is stale for any other printer (dropped on translation)",
        )
    if model.metadata("BambuStudio:3mfVersion") and "bambu" not in model.metadata("Application").lower():
        c.warn(
            "V061",
            "the Bambu 3MF version marker is present on a project whose Application is "
            f"{model.metadata('Application')!r}",
        )


# ---------------------------------------------------------------------------
# convenience
# ---------------------------------------------------------------------------

def summarize(issues: list[Issue]) -> str:
    if not issues:
        return "no issues"
    errors = sum(1 for i in issues if i.level == "error")
    warnings = sum(1 for i in issues if i.level == "warning")
    infos = sum(1 for i in issues if i.level == "info")
    return f"{errors} error(s), {warnings} warning(s), {infos} info"
