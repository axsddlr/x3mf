"""Fixture factory: realistic foreign projects, including broken ones.

The fixtures are generated rather than checked in so that every defect the
translator claims to repair is visible in code and can be toggled.  Geometry is
a 10 mm cube per object — enough for the readers, small enough to keep tests
fast.  Everything else (plates, parts, roles, height ranges, colour changes,
filament sequencing, presets, stamps) mirrors a Bambu-style project.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8AAAwAB/AGN2f0"
    "AAAAASUVORK5CYII="
)

CORE = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
PROD = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"
BAMBU = "http://schemas.bambulab.com/package/2021"


def _cube(object_id: int, size: float = 10.0) -> str:
    v = [
        (0, 0, 0), (size, 0, 0), (size, size, 0), (0, size, 0),
        (0, 0, size), (size, 0, size), (size, size, size), (0, size, size),
    ]
    tris = [
        (0, 1, 2), (0, 2, 3), (4, 6, 5), (4, 7, 6),
        (0, 4, 5), (0, 5, 1), (1, 5, 6), (1, 6, 2),
        (2, 6, 7), (2, 7, 3), (3, 7, 4), (3, 4, 0),
    ]
    verts = "\n".join(f'      <vertex x="{x}" y="{y}" z="{z}"/>' for x, y, z in v)
    faces = "\n".join(f'      <triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in tris)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<model unit="millimeter" xml:lang="en-US" xmlns="{CORE}" xmlns:p="{PROD}"'
        ' requiredextensions="p">\n'
        f' <resources>\n  <object id="{object_id}" type="model"'
        f' p:UUID="{object_id:08x}-0001-4000-8000-000000000001">\n'
        '   <mesh>\n    <vertices>\n'
        f"{verts}\n"
        "    </vertices>\n    <triangles>\n"
        f"{faces}\n"
        "    </triangles>\n   </mesh>\n  </object>\n </resources>\n"
        " <build/>\n</model>\n"
    )


def _matrix(ox: float = 0.0, oy: float = 0.0, oz: float = 0.0) -> str:
    """4x4 part matrix, as slicers write it (16 values)."""
    return f"1 0 0 0 0 1 0 0 0 0 1 0 {ox} {oy} {oz} 1"


def _transform(ox: float, oy: float, oz: float = 0.0) -> str:
    return f"1 0 0 0 1 0 0 0 1 {ox} {oy} {oz}"


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------

FOREIGN_SETTINGS = {
    "from": "BambuStudio-01.09.05.51",
    "version": "01.09.05.51",
    "is_custom_defined": "0",
    "printer_model": "Bambu Lab X1 Carbon",
    "printer_variant": "0.4",
    "printer_settings_id": "Bambu Lab X1 Carbon 0.4 nozzle",
    "print_settings_id": "0.20mm Standard @BBL X1C",
    "printer_technology": "FFF",
    "nozzle_diameter": ["0.4"],
    "nozzle_type": ["hardened_steel"],
    "min_layer_height": ["0.08"],
    "max_layer_height": ["0.28"],
    "extruder_count": "1",
    "bed_shape": ["0x0", "256x0", "256x256", "0x256"],
    "printable_area": ["0x0", "256x0", "256x256", "0x256"],
    "printable_height": "256",
    "gcode_flavor": "marlin2",
    "filament_settings_id": [
        "Bambu PLA Basic @BBL X1C",
        "Bambu PLA Matte @BBL X1C",
        "Generic PETG @BBL X1C",
        "Bambu PLA Basic @BBL X1C",
    ],
    "filament_type": ["PLA", "PLA", "PETG", "PLA"],
    "filament_colour": ["#FF0000", "#00FF00", "#0000FF", "#FFFFFF"],
    "filament_ids": ["GFA00", "GFA01", "GFG99", "GFA00"],
    "filament_map": ["1", "1", "1", "1"],
    "filament_max_volumetric_speed": ["21", "21", "12", "21"],
    "nozzle_temperature": ["220", "220", "250", "220"],
    "default_filament_profile": ["Bambu PLA Basic @BBL X1C"],
    "flush_volumes_matrix": ["0", "140", "140", "0"],
    "flush_volumes_vector": ["140", "140"],
    "layer_height": "0.2",
    "initial_layer_height": "0.2",
    "wall_loops": "2",
    "sparse_infill_density": "15%",
    "sparse_infill_pattern": "grid",
    "outer_wall_speed": "200",
    "inner_wall_speed": "300",
    "travel_speed": "500",
    "enable_prime_tower": "1",
    "different_settings_to_system": [
        "layer_height;wall_loops;outer_wall_speed",
        "filament_max_volumetric_speed",
        "",
    ],
    # Keys only this vendor writes; a correct translation drops them.
    "firmware_retraction": "0",
    "bbl_use_printhost": "1",
    "vendor_specific_flag": "1",
}

FOREIGN_FILAMENTS = [
    ("#FF0000", "PLA"),
    ("#00FF00", "PLA"),
    ("#0000FF", "PETG"),
    ("#FFFFFF", "PLA"),
]

KIT_SETTINGS = {
    "from": "project",
    "version": "2.3.2.88",
    "is_custom_defined": "0",
    "printer_model": "Creality Ender-3 V3 SE",
    "printer_variant": "0.4",
    "printer_settings_id": "Creality Ender-3 V3 SE 0.4 nozzle",
    "print_settings_id": "0.12mm Fine @Creality Ender3V3SE 0.4",
    "printer_technology": "FFF",
    "nozzle_diameter": ["0.4"],
    "nozzle_type": ["brass"],
    "min_layer_height": ["0.08"],
    "max_layer_height": ["0.28"],
    "extruder_count": "1",
    "bed_shape": ["0x0", "220x0", "220x220", "0x220"],
    "printable_area": ["0x0", "220x0", "220x220", "0x220"],
    "printable_height": "250",
    "gcode_flavor": "marlin2",
    "filament_settings_id": ["Sunlu PLA+ Best v1"],
    "filament_type": ["PLA"],
    "filament_colour": ["#26A69A"],
    "filament_ids": ["GFG99"],
    "filament_map": ["1"],
    "filament_max_volumetric_speed": ["12"],
    "nozzle_temperature": ["215"],
    "default_filament_profile": ["Creality Generic PLA @Ender-3V3-all"],
    "flush_volumes_matrix": ["0"],
    "flush_volumes_vector": ["140", "140"],
    "layer_height": "0.12",
    "initial_layer_height": "0.2",
    "wall_loops": "3",
    "sparse_infill_density": "15%",
    "sparse_infill_pattern": "gyroid",
    "outer_wall_speed": "50",
    "inner_wall_speed": "100",
    "travel_speed": "150",
    "enable_prime_tower": "0",
    "different_settings_to_system": ["", "", ""],
}


# ---------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------

def build_foreign_project(
    path: str | Path,
    *,
    broken: bool = True,
    filaments: int = 4,
    plates: int = 2,
    bad_part_matrix: bool = False,
    application: str = "BambuStudio-01.09.05.51",
    extra: dict | None = None,
) -> Path:
    """Write a Bambu-dialect project with optional defects."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    settings = json.loads(json.dumps(FOREIGN_SETTINGS))
    settings["filament_settings_id"] = settings["filament_settings_id"][:filaments]
    settings["filament_type"] = settings["filament_type"][:filaments]
    settings["filament_colour"] = settings["filament_colour"][:filaments]
    settings["filament_ids"] = settings["filament_ids"][:filaments]
    settings["filament_map"] = ["1"] * filaments
    settings["filament_max_volumetric_speed"] = ["21"] * filaments
    settings["nozzle_temperature"] = ["220"] * filaments
    settings.update(extra or {})

    entries: dict[str, bytes] = {}

    def put(name: str, text: str) -> None:
        entries[name] = text.encode("utf-8")

    object_ids = [2, 4, 6, 8][: max(2, min(4, plates * 2))]
    parts = {oid: f"3D/Objects/part_{oid}.model" for oid in object_ids}

    put("[Content_Types].xml", "\n".join([
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
        ' <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
        ' <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>',
        ' <Default Extension="png" ContentType="image/png"/>',
        "</Types>",
    ]) + "\n")
    put("_rels/.rels", f"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Target="/3D/3dmodel.model" Id="rel-1" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>
 <Relationship Target="/Metadata/plate_1.png" Id="rel-2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/thumbnail"/>
</Relationships>
""")

    components = []
    for oid in object_ids:
        components.append(
            f'  <object id="{oid}" type="model">\n   <components>\n'
            f'    <component p:path="/{parts[oid]}" objectid="1"'
            f' p:UUID="{oid:08x}-0000-4000-8000-000000000000" transform="{_transform(0, 0)}"/>\n'
            f"   </components>\n  </object>"
        )
    items = []
    positions = {2: (40, 40), 4: (120, 40), 6: (40, 120), 8: (120, 120)}
    for oid in object_ids:
        x, y = positions[oid]
        items.append(f'  <item objectid="{oid}" transform="{_transform(x, y, 0.2)}"/>')
    put("3D/3dmodel.model", "\n".join([
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<model unit="millimeter" xml:lang="en-US" xmlns="{CORE}" xmlns:p="{PROD}"'
        f' xmlns:BambuStudio="{BAMBU}" requiredextensions="p">',
        f' <metadata name="Application">{application}</metadata>',
        ' <metadata name="BambuStudio:3mfVersion">1</metadata>',
        f' <metadata name="Title">x3mf fixture</metadata>',
        " <resources>",
        "\n".join(components),
        " </resources>",
        " <build>",
        "\n".join(items),
        " </build>",
        "</model>",
    ]) + "\n")

    part_rels = []
    for index, oid in enumerate(object_ids, start=1):
        put(parts[oid], _cube(1))
        part_rels.append(
            f' <Relationship Target="/{parts[oid]}" Id="rel-{index}"'
            ' Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>'
        )
    put("3D/_rels/3dmodel.model.rels", "\n".join([
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">',
        *part_rels,
        "</Relationships>",
    ]) + "\n")

    # --- model_settings ---------------------------------------------------
    ms_objects = []
    for index, oid in enumerate(object_ids):
        extruder = index + 1
        if broken and oid == object_ids[-1]:
            extruder = 9  # out of range: more filaments assigned than exist
        matrix = _matrix(positions[oid][0], positions[oid][1], 0.2)
        if bad_part_matrix and index == 0:
            matrix = "1 0 0 0 0 1 0 0 0 0 1 0 0 0 0"  # 15 values
        role = "normal_part"
        extra_parts = ""
        if index == 1:
            role = "modifier_part"
        if index == 2:
            role = "support_blocker"
        ms_objects.append(f"""  <object id="{oid}">
   <metadata key="name" value="object_{oid}"/>
   <metadata key="extruder" value="{extruder}"/>
   <part id="{oid + 1}" subtype="{role}">
    <metadata key="name" value="object_{oid}"/>
    <metadata key="extruder" value="{extruder}"/>
    <metadata key="matrix" value="{matrix}"/>
    <metadata key="source_file" value="fixture.3mf"/>
    <metadata key="source_object_id" value="{index}"/>
    <metadata key="source_volume_id" value="0"/>
   </part>{extra_parts}
  </object>""")

    plate_blocks = []
    for plate_index in range(plates):
        members = object_ids[plate_index::plates]
        instances = []
        for oid in members:
            instances.append(f"""   <model_instance>
    <metadata key="object_id" value="{oid}"/>
    <metadata key="instance_id" value="0"/>
    <metadata key="identify_id" value="{1000 + oid}"/>
   </model_instance>""")
        if broken and plate_index == plates - 1:
            instances.append("""   <model_instance>
    <metadata key="object_id" value="99"/>
    <metadata key="instance_id" value="0"/>
    <metadata key="identify_id" value="9999"/>
   </model_instance>""")
        maps = " ".join(["1"] * filaments)
        plate_blocks.append(f"""  <plate>
   <metadata key="plater_id" value="{plate_index + 1}"/>
   <metadata key="plater_name" value="plate {plate_index + 1}"/>
   <metadata key="locked" value="false"/>
   <metadata key="filament_map_mode" value="Auto For Flush"/>
   <metadata key="filament_maps" value="{maps if not (broken and plate_index == 0) else '1 1'}"/>
   <metadata key="thumbnail_file" value="Metadata/plate_{plate_index + 1}.png"/>
{chr(10).join(instances)}
  </plate>""")

    assemble_items = "\n".join(
        f'   <assemble_item object_id="{oid}" instance_id="0"'
        f' transform="{_transform(positions[oid][0], positions[oid][1], 0.2)}" offset="0 0 0"/>'
        for oid in object_ids
    )
    put("Metadata/model_settings.config", "\n".join([
        '<?xml version="1.0" encoding="UTF-8"?>',
        "<config>",
        *ms_objects,
        *plate_blocks,
        "  <assemble>",
        assemble_items,
        "  </assemble>",
        "</config>",
    ]) + "\n")

    put("Metadata/project_settings.config", json.dumps(settings, indent=4, sort_keys=True) + "\n")

    put("Metadata/layer_config_ranges.xml", f"""<?xml version="1.0" encoding="UTF-8"?>
<objects>
 <object id="{object_ids[0]}">
  <range min_z="1.2" max_z="3.6">
   <option opt_key="layer_height" value="0.12"/>
   <option opt_key="wall_loops" value="4"/>
  </range>
 </object>
 <object id="{object_ids[1]}">
  <range min_z="0.2" max_z="1.0">
   <option opt_key="sparse_infill_density" value="60%"/>
  </range>
 </object>
</objects>
""")

    put("Metadata/custom_gcode_per_layer.xml", """<?xml version="1.0" encoding="UTF-8"?>
<custom_gcodes_per_layer>
 <mode value="SingleExtruder"/>
 <layer top_z="0.6" type="color" extruder="2" color="#00FF00" extra=""/>
 <layer top_z="1.8" type="color" extruder="3" color="#0000FF" extra=""/>
 <layer top_z="2.4" type="pause" extra="insert magnets"/>
</custom_gcodes_per_layer>
""")

    put("Metadata/filament_sequence.json", json.dumps({
        f"plate_{i + 1}": {
            "nozzle_sequence": list(range(1, filaments + 1)),
            "optimal_assignment": list(range(1, filaments + 1)),
            "sequence": list(range(1, filaments + 1)),
        }
        for i in range(plates)
    }, separators=(",", ":")) + "\n")

    for i in range(plates):
        entries[f"Metadata/plate_{i + 1}.png"] = PNG_1PX

    put("Metadata/slice_info.config", f"""<?xml version="1.0" encoding="UTF-8"?>
<config>
  <header>
    <header_item key="X-BBL-Client-Type" value="slicer"/>
    <header_item key="X-BBL-Client-Version" value="01.09.05.51"/>
  </header>
</config>
""")

    if broken:
        # Drop one part relationship on purpose: a component must be reachable
        # through the model's rels part.
        entries["3D/_rels/3dmodel.model.rels"] = "\n".join([
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">',
            *part_rels[:-1],
            "</Relationships>",
        ]) + "\n"

    _write_zip(path, entries)
    return path


def build_kit_project(path: str | Path, *, name: str = "kit", extra: dict | None = None) -> Path:
    """A minimal but complete destination project (kit)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    entries: dict[str, bytes] = {}
    entries["[Content_Types].xml"] = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
        ' <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
        ' <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>\n'
        "</Types>\n"
    ).encode()
    entries["_rels/.rels"] = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
        ' <Relationship Target="/3D/3dmodel.model" Id="rel-1" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>\n'
        "</Relationships>\n"
    ).encode()
    entries["3D/3dmodel.model"] = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<model unit="millimeter" xmlns="{CORE}">\n'
        f' <metadata name="Application">OrcaSlicer-{KIT_SETTINGS["version"]}</metadata>\n'
        " <resources>\n"
        f'  <object id="1" type="model">\n   <mesh>\n    <vertices>\n'
        '      <vertex x="0" y="0" z="0"/>\n      <vertex x="10" y="0" z="0"/>\n'
        '      <vertex x="10" y="10" z="0"/>\n      <vertex x="0" y="10" z="0"/>\n'
        "    </vertices>\n    <triangles>\n"
        '      <triangle v1="0" v2="1" v3="2"/>\n      <triangle v1="0" v2="2" v3="3"/>\n'
        "    </triangles>\n   </mesh>\n  </object>\n"
        " </resources>\n"
        ' <build>\n  <item objectid="1" transform="1 0 0 0 1 0 0 0 1 0 0 0"/>\n </build>\n'
        "</model>\n"
    ).encode()
    kit_settings = dict(KIT_SETTINGS)
    kit_settings.update(extra or {})
    entries["Metadata/project_settings.config"] = (
        json.dumps(kit_settings, indent=4, sort_keys=True) + "\n"
    ).encode()
    _write_zip(path, entries)
    return path


def _write_zip(path: Path, entries: dict[str, bytes]) -> None:
    import zipfile

    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        order = ["[Content_Types].xml", "_rels/.rels"]
        order += [n for n in entries if n not in order]
        for name in order:
            zf.writestr(name, entries[name])
