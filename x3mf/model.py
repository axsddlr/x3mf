"""Model parts and slicer metadata parts.

Layers handled here (names confirmed against an OrcaSlicer 2.4.2 install's
own string table):

===========================  ==========================================
``3D/3dmodel.model``         core 3MF model: wrapper objects, components, build
``3D/Objects/*.model``       per-object part files referenced by ``p:path``
``Metadata/model_settings.config``   per-object/per-part settings and plates
``Metadata/layer_config_ranges.xml`` height ("layer range") modifiers
``Metadata/custom_gcode_per_layer.xml`` colour changes and per-layer gcode
``Metadata/filament_sequence.json``  per-plate filament sequencing
``Metadata/slice_info.config``       result of the *source* slicer; stale
===========================  ==========================================

Part files are treated as opaque bytes: the translator never rewrites geometry,
so they are only *scanned* for the object ids that components may reference.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET

CORE_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
PROD_NS = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"
BAMBU_NS = "http://schemas.bambulab.com/package/2021"

MODEL_PATH = "3D/3dmodel.model"
MODEL_SETTINGS_PATH = "Metadata/model_settings.config"
LAYER_RANGES_PATH = "Metadata/layer_config_ranges.xml"
CUSTOM_GCODE_PATH = "Metadata/custom_gcode_per_layer.xml"
FILAMENT_SEQUENCE_PATH = "Metadata/filament_sequence.json"
SLICE_INFO_PATH = "Metadata/slice_info.config"
PRINT_PROFILE_PATH = "Metadata/print_profile.config"

NS = {"c": CORE_NS, "p": PROD_NS}
XML_DECL = '<?xml version="1.0" encoding="UTF-8"?>\n'


def register_namespaces() -> None:
    ET.register_namespace("", CORE_NS)
    ET.register_namespace("p", PROD_NS)
    ET.register_namespace("BambuStudio", BAMBU_NS)


# ElementTree drops prefixes it has not been told about, which would turn
# ``<object>`` into ``<ns0:object>`` and silently break every reader that keys
# off the core namespace spelling.  Register once, at import.
register_namespaces()


def localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def q(ns: str, name: str) -> str:
    return f"{{{ns}}}{name}"


def to_xml(root: ET.Element, indent: str = "  ") -> bytes:
    ET.indent(root, space=indent)
    return (XML_DECL + ET.tostring(root, encoding="unicode") + "\n").encode("utf-8")


def parse_xml(raw: bytes) -> ET.Element:
    # Strip a UTF-8 BOM if present; ET dislikes it in some versions.
    return ET.fromstring(raw.decode("utf-8-sig"))


# ---------------------------------------------------------------------------
# core model
# ---------------------------------------------------------------------------

_COMPONENT_ID_RE = re.compile(rb'<object\b[^>]*\bid="([0-9]+)"')


class PartFile:
    """A ``3D/Objects/*.model`` part: raw bytes plus the ids it defines."""

    def __init__(self, path: str, raw: bytes) -> None:
        self.path = path
        self.raw = raw
        self.object_ids = sorted({m.decode() for m in _COMPONENT_ID_RE.findall(raw)})

    @property
    def has_paint(self) -> bool:
        """True when faces carry a painted colour, seam or support mask.

        Per-triangle paint is a bitfield of filament slots written into the mesh
        itself, so slot remapping has to reach into these bytes.
        """
        return b"paint_color=" in self.raw or b"mmu_segmentation=" in self.raw

    def __repr__(self) -> str:  # pragma: no cover
        return f"PartFile({self.path!r}, ids={self.object_ids})"


class Model3D:
    """The top-level ``3dmodel.model`` plus every part it references."""

    def __init__(self, root: ET.Element, path: str = MODEL_PATH) -> None:
        self.root = root
        self.path = path
        self.parts: dict[str, PartFile] = {}

    # -- io -----------------------------------------------------------------
    @classmethod
    def from_package(cls, package) -> "Model3D":
        path = package.primary_model_path()
        model = cls(parse_xml(package.require(path)), path)
        # Every other *.model part is reachable through the model's own rels
        # part (``3D/_rels/3dmodel.model.rels``), not the package rels, so the
        # container is scanned rather than trusted.
        for name in package.names():
            if name == path or not name.endswith(".model"):
                continue
            raw = package.get(name)
            if raw is not None:
                model.parts[name] = PartFile(name, raw)
        return model

    def to_bytes(self) -> bytes:
        return to_xml(self.root)

    # -- metadata -----------------------------------------------------------
    def metadata(self, name: str) -> str:
        for el in self.root.findall("c:metadata", NS):
            if el.get("name") == name:
                return (el.text or "").strip()
        return ""

    def set_metadata(self, name: str, value: str) -> None:
        for el in self.root.findall("c:metadata", NS):
            if el.get("name") == name:
                el.text = value
                return
        el = ET.Element(q(CORE_NS, "metadata"), {"name": name})
        el.text = value
        resources = self.resources
        if resources is not None:
            self.root.insert(list(self.root).index(resources), el)
        else:
            self.root.append(el)

    def remove_metadata(self, name: str) -> None:
        for el in list(self.root.findall("c:metadata", NS)):
            if el.get("name") == name:
                self.root.remove(el)

    def metadata_keys(self) -> list[str]:
        return [el.get("name", "") for el in self.root.findall("c:metadata", NS)]

    # -- structure ----------------------------------------------------------
    @property
    def resources(self) -> ET.Element | None:
        return self.root.find("c:resources", NS)

    @property
    def build(self) -> ET.Element | None:
        return self.root.find("c:build", NS)

    def objects(self) -> dict[str, ET.Element]:
        out: dict[str, ET.Element] = {}
        resources = self.resources
        if resources is None:
            return out
        for el in resources.findall("c:object", NS):
            out[el.get("id", "")] = el
        return out

    def object_ids(self) -> list[str]:
        return list(self.objects())

    def components(self, obj: ET.Element) -> list[dict[str, str]]:
        found = []
        for comp in obj.findall("c:components/c:component", NS):
            found.append({
                "path": (comp.get(q(PROD_NS, "path")) or "").lstrip("/"),
                "objectid": comp.get("objectid", ""),
                "transform": comp.get("transform", ""),
            })
        return found

    def build_items(self) -> list[ET.Element]:
        build = self.build
        return [] if build is None else build.findall("c:item", NS)

    def instances_of(self, object_id: str) -> int:
        return sum(1 for item in self.build_items() if item.get("objectid") == object_id)

    def remove_object(self, object_id: str) -> None:
        resources = self.resources
        if resources is not None:
            for el in list(resources.findall("c:object", NS)):
                if el.get("id") == object_id:
                    resources.remove(el)
        build = self.build
        if build is not None:
            for item in list(build.findall("c:item", NS)):
                if item.get("objectid") == object_id:
                    build.remove(item)

    def object_name(self, object_id: str) -> str:
        obj = self.objects().get(object_id)
        if obj is None:
            return ""
        for el in obj.findall("c:metadata", NS):
            if el.get("name") == "name":
                return (el.text or "").strip()
        return ""


# ---------------------------------------------------------------------------
# model_settings.config
# ---------------------------------------------------------------------------

def _get_meta(el: ET.Element, key: str) -> str | None:
    for child in el.findall("metadata"):
        if child.get("key") == key:
            return child.get("value")
    return None


def _set_meta(el: ET.Element, key: str, value: str) -> None:
    for child in el.findall("metadata"):
        if child.get("key") == key:
            child.set("value", value)
            return
    ET.SubElement(el, "metadata", {"key": key, "value": value})


def _drop_meta(el: ET.Element, key: str) -> None:
    for child in list(el.findall("metadata")):
        if child.get("key") == key:
            el.remove(child)


class ModelSettings:
    """``Metadata/model_settings.config``: objects, parts, plates, assembly."""

    def __init__(self, root: ET.Element) -> None:
        self.root = root

    @classmethod
    def from_package(cls, package) -> "ModelSettings | None":
        raw = package.get(MODEL_SETTINGS_PATH)
        if raw is None:
            return None
        return cls(parse_xml(raw))

    def to_bytes(self) -> bytes:
        return to_xml(self.root)

    # -- objects ------------------------------------------------------------
    def object_elements(self) -> list[ET.Element]:
        return self.root.findall("object")

    def object_ids(self) -> list[str]:
        return [el.get("id", "") for el in self.object_elements()]

    def find_object(self, object_id: str) -> ET.Element | None:
        for el in self.object_elements():
            if el.get("id") == object_id:
                return el
        return None

    def object_meta(self, el: ET.Element, key: str) -> str | None:
        return _get_meta(el, key)

    def set_object_meta(self, el: ET.Element, key: str, value: str) -> None:
        _set_meta(el, key, value)

    def drop_object_meta(self, el: ET.Element, key: str) -> None:
        _drop_meta(el, key)

    def parts(self, obj_el: ET.Element) -> list[ET.Element]:
        return obj_el.findall("part")

    def part_meta(self, part_el: ET.Element, key: str) -> str | None:
        return _get_meta(part_el, key)

    def set_part_meta(self, part_el: ET.Element, key: str, value: str) -> None:
        _set_meta(part_el, key, value)

    def part_type(self, part_el: ET.Element) -> str:
        """Volume role.  Newer writers use ``part_type``; older ones ``subtype``."""
        meta = _get_meta(part_el, "part_type")
        if meta:
            return meta
        return part_el.get("subtype") or "normal_part"

    def remove_object(self, object_id: str) -> bool:
        for el in list(self.object_elements()):
            if el.get("id") == object_id:
                self.root.remove(el)
                return True
        return False

    # -- plates -------------------------------------------------------------
    def plate_elements(self) -> list[ET.Element]:
        return self.root.findall("plate")

    def plate_ids(self) -> list[str]:
        return [(_get_meta(p, "plater_id") or "") for p in self.plate_elements()]

    def plate_instances(self, plate: ET.Element) -> list[ET.Element]:
        return plate.findall("model_instance")

    def instance_object_id(self, el: ET.Element) -> str:
        return _get_meta(el, "object_id") or ""

    def instance_index(self, el: ET.Element) -> str:
        return _get_meta(el, "instance_id") or "0"

    def remove_plate_instance(self, plate: ET.Element, el: ET.Element) -> None:
        plate.remove(el)

    def drop_empty_plates(self, min_plate: int = 1) -> None:
        for plate in list(self.plate_elements()):
            if not self.plate_instances(plate):
                self.root.remove(plate)

    # -- assembly -----------------------------------------------------------
    def assemble(self) -> ET.Element | None:
        return self.root.find("assemble")

    def rebuild_assemble(self, model: Model3D) -> None:
        """Re-derive ``<assemble>`` from the plates.

        ``assemble`` is the "assembled view" transform table.  Writers keep it
        in sync with the plates; a stale table is a real source of confusion
        when a foreign file is opened, so it is regenerated rather than copied.
        """
        old = self.assemble()
        if old is not None:
            self.root.remove(old)
        new = ET.Element("assemble")
        seen: set[tuple[str, str]] = set()
        for plate in self.plate_elements():
            for inst in self.plate_instances(plate):
                object_id = self.instance_object_id(inst)
                instance_id = self.instance_index(inst)
                if (object_id, instance_id) in seen:
                    continue
                seen.add((object_id, instance_id))
                item = ET.SubElement(new, "assemble_item", {
                    "object_id": object_id,
                    "instance_id": instance_id,
                    "transform": _instance_transform(model, object_id, instance_id),
                    "offset": "0 0 0",
                })
        if len(new):
            self.root.append(new)


def _instance_transform(model: Model3D, object_id: str, instance_id: str) -> str:
    items = [i for i in model.build_items() if i.get("objectid") == object_id]
    try:
        index = int(instance_id)
    except ValueError:
        index = 0
    if 0 <= index < len(items):
        return items[index].get("transform") or ""
    return ""


# ---------------------------------------------------------------------------
# auxiliary metadata
# ---------------------------------------------------------------------------

class AuxFile:
    """An XML metadata part we treat as *bytes*, not as a parsed document.

    ``layer_config_ranges.xml`` and ``custom_gcode_per_layer.xml`` have no
    published schema, and each slicer version can add attributes.  Re-serialising
    them through an XML library would rewrite structure we do not fully
    understand, so this class edits the original bytes surgically: only the
    identifier values we positively recognise (an object id, a filament index)
    are substituted, and everything else — element order, unknown attributes,
    formatting — survives untouched.
    """

    _OBJECT_ID = re.compile(rb'(<object\b[^>]*?\bid=")([0-9]+)(")')
    _FILAMENT_ATTR = re.compile(rb'(\b(?:extruder|extruder_id|filament)=")([0-9]+)(")')

    def __init__(self, path: str, raw: bytes) -> None:
        self.path = path
        self.raw = raw

    @classmethod
    def from_package(cls, package, path: str) -> "AuxFile | None":
        raw = package.get(path)
        if raw is None:
            return None
        # Cheap validity gate: we never rewrite a file we cannot at least parse.
        try:
            parse_xml(raw)
        except ET.ParseError:
            return None
        return cls(path, raw)

    def to_bytes(self) -> bytes:
        return self.raw

    # -- recognised facts ---------------------------------------------------
    def object_ids(self) -> list[str]:
        return [m.group(2).decode() for m in self._OBJECT_ID.finditer(self.raw)]

    def extruder_refs(self) -> set[int]:
        return {int(m.group(2)) for m in self._FILAMENT_ATTR.finditer(self.raw)}

    def count(self, needle: bytes) -> int:
        return self.raw.count(needle)

    def has_objects(self) -> bool:
        return bool(self._OBJECT_ID.search(self.raw))

    # -- edits --------------------------------------------------------------
    def remap_object_ids(self, mapping: dict[str, str]) -> int:
        changed = 0

        def replace(match: "re.Match[bytes]") -> bytes:
            nonlocal changed
            old = match.group(2).decode()
            if old in mapping and mapping[old] != old:
                changed += 1
                return match.group(1) + mapping[old].encode() + match.group(3)
            return match.group(0)

        self.raw = self._OBJECT_ID.sub(replace, self.raw)
        return changed

    def remap_filament_refs(self, mapping: dict[int, int]) -> int:
        changed = 0

        def replace(match: "re.Match[bytes]") -> bytes:
            nonlocal changed
            old = int(match.group(2))
            if old in mapping and mapping[old] != old:
                changed += 1
                return match.group(1) + str(mapping[old]).encode() + match.group(3)
            return match.group(0)

        self.raw = self._FILAMENT_ATTR.sub(replace, self.raw)
        return changed

    def drop_objects(self, dead: set[str]) -> bool:
        """Return True when a dead object is still referenced (caller drops us)."""
        return any(oid in dead for oid in self.object_ids())


class FilamentSequence:
    """``Metadata/filament_sequence.json``: per-plate filament ordering."""

    def __init__(self, data: dict) -> None:
        self.data = data

    @classmethod
    def from_package(cls, package) -> "FilamentSequence | None":
        raw = package.get(FILAMENT_SEQUENCE_PATH)
        if raw is None:
            return None
        try:
            data = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        return cls(data) if isinstance(data, dict) else None

    def to_bytes(self) -> bytes:
        return (json.dumps(self.data, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")

    def remap_filaments(self, mapping: dict[int, int]) -> int:
        changed = 0

        def walk(node):
            nonlocal changed
            if isinstance(node, list):
                for i, value in enumerate(node):
                    if isinstance(value, int) and not isinstance(value, bool):
                        if value in mapping and mapping[value] != value:
                            node[i] = mapping[value]
                            changed += 1
                    else:
                        walk(value)
            elif isinstance(node, dict):
                for value in node.values():
                    walk(value)

        walk(self.data)
        return changed

    def plate_keys(self) -> list[str]:
        return [k for k in self.data if k.startswith("plate_")]


# ---------------------------------------------------------------------------
# slice_info.config (source-only; a translated project must not keep it)
# ---------------------------------------------------------------------------

def slice_info_header(package) -> dict[str, str]:
    raw = package.get(SLICE_INFO_PATH)
    if raw is None:
        return {}
    try:
        root = parse_xml(raw)
    except ET.ParseError:
        return {}
    out = {}
    for el in root.iter():
        if localname(el.tag) == "header_item":
            out[el.get("key", "")] = el.get("value", "")
    return out
