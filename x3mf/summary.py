"""Structured description of a project, shared by the CLI and the web UI.

``describe`` returns data, never prose: the CLI renders it as aligned lines,
the browser renders it as cards.  Keeping one extractor means both surfaces
report the same facts about the same file.
"""

from __future__ import annotations

from .container import Package
from .flavors import detect_source
from .model import (
    CUSTOM_GCODE_PATH,
    FILAMENT_SEQUENCE_PATH,
    LAYER_RANGES_PATH,
    PRINT_PROFILE_PATH,
    SLICE_INFO_PATH,
    AuxFile,
    FilamentSequence,
    Model3D,
    ModelSettings,
    slice_info_header,
)
from .settings import ProjectSettings


def describe(package: Package, name: str = "") -> dict:
    settings = ProjectSettings.from_package(package)
    profile = detect_source(package, settings)
    out: dict = {
        "name": name,
        "entries": len(package.names()),
        "dialect": {
            "key": profile.key,
            "application": profile.application,
            "from": profile.from_value,
            "version": profile.version,
            "conflict": "; ".join(profile.contradictions),
        },
        "warnings": [],
    }

    try:
        model = Model3D.from_package(package)
    except Exception as exc:  # a broken file must still produce a description
        out["model_error"] = str(exc)
        model = None

    if model is not None:
        parts = len(model.parts)
        out["model"] = {
            "objects": len(model.objects()),
            "build_items": len(model.build_items()),
            "components": sum(len(model.components(o)) for o in model.objects().values()),
            "part_files": parts,
        }

    if settings.keys():
        out["machine"] = {
            "model": settings.get_str("printer_model"),
            "variant": settings.get_str("printer_variant"),
            "extruders": settings.extruder_count(),
            "settings_keys": len(settings.keys()),
            "process": settings.get_str("print_settings_id"),
        }
        out["filaments"] = [
            {
                "index": slot.index + 1,
                "material": slot.material,
                "colour": slot.colour,
                "preset": slot.preset_id,
            }
            for slot in settings.filaments()
        ]
    else:
        out["warnings"].append("no project_settings.config: this is a model, not a print project")

    ms = ModelSettings.from_package(package)
    if ms is not None:
        roles: dict[str, int] = {}
        for obj in ms.object_elements():
            for part in ms.parts(obj):
                role = ms.part_type(part)
                roles[role] = roles.get(role, 0) + 1
        out["layout"] = {
            "plates": len(ms.plate_elements()),
            "objects": len(ms.object_elements()),
            "parts": sum(roles.values()),
            "roles": {k: v for k, v in sorted(roles.items()) if k != "normal_part"},
        }
    else:
        out["layout"] = None

    ranges = AuxFile.from_package(package, LAYER_RANGES_PATH)
    gcode = AuxFile.from_package(package, CUSTOM_GCODE_PATH)
    sequence = FilamentSequence.from_package(package)
    out["aux"] = {
        "height_ranges": ranges.count(b"<range") if ranges is not None else 0,
        "colour_changes": gcode.count(b'type="color"') if gcode is not None else 0,
        "pauses": gcode.count(b'type="pause"') if gcode is not None else 0,
        "sequenced_plates": len(sequence.plate_keys()) if sequence is not None else 0,
    }
    if package.has(PRINT_PROFILE_PATH):
        out["print_profile"] = True
    if package.has(SLICE_INFO_PATH):
        out["slice_info"] = slice_info_header(package)
    return out


def text_lines(d: dict) -> list[str]:
    """Render ``describe`` output as the CLI's aligned report."""
    lines: list[str] = []
    lines.append(f"container     {d['entries']} entries")
    model = d.get("model")
    if model:
        lines.append(
            f"model         {model['objects']} object(s), {model['build_items']} build item(s), "
            f"{model['part_files']} part file(s)"
        )
    elif d.get("model_error"):
        lines.append(f"model         unreadable: {d['model_error']}")

    dialect = d["dialect"]
    lines.append(
        f"dialect       [{dialect['key']}] Application={dialect['application'] or '(none)'} "
        f"from={dialect['from'] or '(none)'} version={dialect['version'] or '(none)'}"
    )
    if dialect["conflict"]:
        lines.append(f"              ! {dialect['conflict']}")

    machine = d.get("machine")
    if machine:
        lines.append(
            f"machine       {machine['model'] or '?'} / {machine['variant'] or '?'} / "
            f"{machine['extruders']} extruder(s)"
        )
        lines.append(
            f"process       {machine['process'] or '(none)'} "
            f"({machine['settings_keys']} keys resolved)"
        )
        for filament in d.get("filaments", []):
            lines.append(
                f"filament      {filament['index']}: {filament['material'] or '?'} "
                f"{filament['colour'] or '?'} {filament['preset'] or '(no preset)'}"
            )
    else:
        lines.append("machine       (no project settings part)")

    layout = d.get("layout")
    if layout:
        extra = ", ".join(f"{v} {k}" for k, v in layout["roles"].items())
        lines.append(
            f"plates        {layout['plates']} plate(s), {layout['objects']} object(s), "
            f"{layout['parts']} part(s)" + (f" [{extra}]" if extra else "")
        )
    else:
        lines.append("plates        (no model_settings.config)")

    aux = d["aux"]
    parts = []
    if aux["height_ranges"]:
        parts.append(f"{aux['height_ranges']} height range(s)")
    if aux["colour_changes"]:
        parts.append(f"{aux['colour_changes']} colour change(s)")
    if aux["pauses"]:
        parts.append(f"{aux['pauses']} pause(s)")
    if aux["sequenced_plates"]:
        parts.append(f"{aux['sequenced_plates']} sequenced plate(s)")
    lines.append("aux           " + (", ".join(parts) if parts else "none"))

    if d.get("slice_info"):
        detail = ", ".join(f"{k}={v}" for k, v in sorted(d["slice_info"].items()))
        lines.append(f"slice_info    present ({detail or 'no header'})")
    return lines
