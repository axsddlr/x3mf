import io
import json
import os
import pathlib
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from xml.etree import ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import support  # noqa: E402,F401  (redirects tempfile into the workspace)

from x3mf.cli import main as cli_main  # noqa: E402
from x3mf.container import Package  # noqa: E402
from x3mf.flavors import TargetKit  # noqa: E402
from x3mf.model import (  # noqa: E402
    CUSTOM_GCODE_PATH,
    FILAMENT_SEQUENCE_PATH,
    LAYER_RANGES_PATH,
    MODEL_SETTINGS_PATH,
    SLICE_INFO_PATH,
    Model3D,
    ModelSettings,
    localname,
)
from x3mf.settings import ProjectSettings  # noqa: E402
from x3mf.translate import Options, Translator  # noqa: E402
from x3mf.validate import validate  # noqa: E402

from fixtures import build_foreign_project, build_kit_project  # noqa: E402

#: A real project is optional: point X3MF_REAL_PROJECT at one to run the
#: end-to-end check against genuine slicer output.  Never a hard-coded path --
#: this file ships publicly, and somebody else's home directory is not ours to
#: publish.
_REAL_PROJECT = os.environ.get("X3MF_REAL_PROJECT", "").strip()
REAL_PROJECT = pathlib.Path(_REAL_PROJECT) if _REAL_PROJECT else None


def translate(source, kit, **option_kwargs):
    package = Package.read(source)
    target = TargetKit.from_project(kit) if isinstance(kit, (str, pathlib.Path)) else kit
    return Translator(package, target, Options(**option_kwargs)).run()


def errors_of(package, kit=None):
    return [i for i in validate(package, kit=kit) if i.level == "error"]


class TranslateCarryOverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = support.scratch()
        tmp = pathlib.Path(cls._tmp.name)
        cls.source = build_foreign_project(tmp / "foreign.3mf", broken=True)
        cls.kit = build_kit_project(tmp / "kit.3mf")
        cls.translation = translate(cls.source, cls.kit)
        cls.out = cls.translation.package
        cls.settings = ProjectSettings.from_package(cls.out)
        cls.ms = ModelSettings.from_package(cls.out)
        cls.model = Model3D.from_package(cls.out)

    @classmethod
    def tearDownClass(cls):
        pass

    def test_output_satisfies_its_own_invariants(self):
        self.assertEqual(errors_of(self.out, TargetKit.from_project(self.kit)), [])

    def test_plates_and_instances_survive(self):
        plates = self.ms.plate_elements()
        self.assertEqual(len(plates), 2)
        instances = [i for p in plates for i in self.ms.plate_instances(p)]
        self.assertEqual(len(instances), 4, "the dangling instance to object 99 must be gone")
        for inst in instances:
            object_id = self.ms.instance_object_id(inst)
            self.assertIn(object_id, self.model.objects())

    def test_part_roles_are_preserved(self):
        roles = {
            self.ms.part_type(part)
            for obj in self.ms.object_elements()
            for part in self.ms.parts(obj)
        }
        self.assertIn("modifier_part", roles)
        self.assertIn("support_blocker", roles)

    def test_filament_slots_and_colours_are_carried(self):
        self.assertEqual(
            self.settings.get_list("filament_colour"),
            ["#FF0000", "#00FF00", "#0000FF", "#FFFFFF"],
        )
        self.assertEqual(
            self.settings.get_list("filament_type"), ["PLA", "PLA", "PETG", "PLA"]
        )

    def test_height_ranges_are_carried_and_resolve(self):
        ranges = self.out.require(LAYER_RANGES_PATH).decode()
        self.assertEqual(ranges.count("<range"), 2)
        root = ET.fromstring(ranges)
        ids = [el.get("id") for el in root if localname(el.tag) == "object"]
        self.assertEqual(sorted(ids), ["2", "4"])
        for object_id in ids:
            self.assertIn(object_id, self.model.objects())

    def test_colour_changes_keep_their_z_and_slot(self):
        gcode = ET.fromstring(self.out.require(CUSTOM_GCODE_PATH).decode())
        layers = [el for el in gcode if localname(el.tag) == "layer"]
        colours = [el for el in layers if el.get("type") == "color"]
        self.assertEqual(len(colours), 2)
        self.assertEqual([c.get("extruder") for c in colours], ["2", "3"])
        self.assertEqual([c.get("top_z") for c in colours], ["0.6", "1.8"])
        self.assertTrue(any(el.get("type") == "pause" for el in layers))

    def test_filament_sequence_kept_when_slot_count_is_unchanged(self):
        self.assertTrue(self.out.has(FILAMENT_SEQUENCE_PATH))

    def test_stale_slice_info_is_removed(self):
        self.assertFalse(self.out.has(SLICE_INFO_PATH))

    def test_geometry_comes_from_the_kit(self):
        kit_settings = TargetKit.from_project(self.kit).settings
        for key in ("printer_model", "printer_variant", "nozzle_diameter", "bed_shape",
                    "printable_area", "printable_height"):
            self.assertEqual(
                self.settings.get(key), kit_settings.get(key), f"{key} must come from the kit"
            )

    def test_process_settings_come_from_the_source(self):
        self.assertEqual(self.settings.get_str("layer_height"), "0.2")
        self.assertEqual(self.settings.get_str("wall_loops"), "2")
        self.assertEqual(self.settings.get_str("outer_wall_speed"), "200")

    def test_vendor_only_keys_are_dropped(self):
        for key in ("vendor_specific_flag", "firmware_retraction", "bbl_use_printhost"):
            self.assertNotIn(key, self.settings, key)

    def test_dialect_is_restamped(self):
        self.assertEqual(self.model.metadata("Application"), "OrcaSlicer-2.3.2.88")
        self.assertEqual(self.model.metadata("BambuStudio:3mfVersion"), "")
        self.assertEqual(self.settings.get_str("from"), "project")

    def test_preset_names_point_at_the_destination(self):
        self.assertEqual(self.settings.get_str("printer_settings_id"),
                         "Creality Ender-3 V3 SE 0.4 nozzle")
        self.assertEqual(self.settings.get_str("print_settings_id"),
                         "0.12mm Fine @Creality Ender3V3SE 0.4")

    def test_report_explains_the_retarget(self):
        report = self.translation.report
        blob = report.text()
        self.assertIn("retargeted", blob)
        self.assertIn("dropped", blob)
        self.assertIn("height-range", blob)
        self.assertIn("colour change", blob)


class ValueMappingTests(unittest.TestCase):
    """The re-spelling layer: same setting, different grammar in the destination."""

    def _run(self, source_extra, kit_extra, **options):
        with support.scratch() as tmp:
            tmp = pathlib.Path(tmp)
            source = build_foreign_project(tmp / "src.3mf", broken=False, extra=source_extra)
            kit = build_kit_project(tmp / "kit.3mf", extra=kit_extra)
            translation = translate(source, kit, **options)
            return translation, ProjectSettings.from_package(translation.package)

    def test_organic_support_follows_the_destination_family(self):
        # Orca-lineage destinations spell it "organic"; Bambu spells it "tree_organic".
        translation, settings = self._run(
            {"support_style": "tree_organic"},
            {"support_style": "tree_slim"},
        )
        self.assertEqual(settings.get_str("support_style"), "organic")
        self.assertTrue(any("re-spelled" in item for item in translation.report.adjusted))

    def test_organic_support_reverse_direction(self):
        _, settings = self._run(
            {"support_style": "organic"},
            {"support_style": "grid"},
            target_flavor="bambu",
        )
        self.assertEqual(settings.get_str("support_style"), "tree_organic")

    def test_support_type_gains_its_manual_suffix(self):
        _, settings = self._run(
            {"support_type": "tree"},
            {"support_type": "normal(auto)"},
        )
        self.assertEqual(settings.get_str("support_type"), "tree(manual)")

    def test_shell_thickness_moves_between_three_grammars(self):
        _, boolean_target = self._run(
            {"ensure_vertical_shell_thickness": "ensure_all"},
            {"ensure_vertical_shell_thickness": "0"},
        )
        self.assertEqual(boolean_target.get_str("ensure_vertical_shell_thickness"), "1")

        _, orca_target = self._run(
            {"ensure_vertical_shell_thickness": "1"},
            {"ensure_vertical_shell_thickness": "none"},
        )
        self.assertEqual(orca_target.get_str("ensure_vertical_shell_thickness"), "ensure_all")

    def test_key_alias_is_carried_under_the_destination_name(self):
        translation, settings = self._run(
            {"enable_support_ironing": "1"},
            {"support_ironing": "0"},
        )
        self.assertEqual(settings.get_str("support_ironing"), "1")
        self.assertNotIn("enable_support_ironing", settings)
        self.assertTrue(any("destination's name" in item for item in translation.report.adjusted))

    def test_value_outside_the_destination_choices_is_not_carried(self):
        translation, settings = self._run(
            {"sparse_infill_pattern": "cross_hatch"},
            {"sparse_infill_pattern": "grid"},
        )
        self.assertEqual(settings.get_str("sparse_infill_pattern"), "grid")
        self.assertTrue(
            any("would reject them" in item for item in translation.report.dropped),
            translation.report.dropped,
        )

    def test_switch_with_a_non_boolean_value_is_refused(self):
        _, settings = self._run({"spiral_mode": "2"}, {"spiral_mode": "0"})
        self.assertEqual(settings.get_str("spiral_mode"), "0")

    def test_slot_index_beyond_the_slot_count_is_refused(self):
        _, settings = self._run({"support_filament": "9"}, {"support_filament": "1"})
        self.assertEqual(settings.get_str("support_filament"), "1")

    def test_alias_fans_out_to_every_destination_key(self):
        translation, settings = self._run(
            {"wall_filament": "2"},
            {"outer_wall_filament_id": "1", "inner_wall_filament_id": "1"},
        )
        self.assertEqual(settings.get_str("outer_wall_filament_id"), "2")
        self.assertEqual(settings.get_str("inner_wall_filament_id"), "2")
        self.assertTrue(any("destination's name" in item for item in translation.report.adjusted))

    def test_per_extruder_array_that_differs_is_refused(self):
        translation, settings = self._run(
            {"initial_layer_speed": ["30", "40"]},
            {"initial_layer_speed": "30"},
        )
        self.assertEqual(settings.get_str("initial_layer_speed"), "30")
        self.assertTrue(any("would reject them" in item for item in translation.report.dropped))

    def test_vendor_blockers_are_named(self):
        from x3mf.mapping import Destination, refuses

        creality = Destination("creality")
        self.assertIn("negative", refuses("raft_first_layer_expansion", "-1", creality))
        self.assertIn("0 and 2", refuses("tree_support_wall_count", "3", creality))
        self.assertIn("rectilinear", refuses("ironing_pattern", "rectilinear", creality))
        self.assertIsNone(refuses("raft_first_layer_expansion", "-1", Destination("orca")))

    def test_value_is_left_alone_without_a_witness(self):
        _, settings = self._run(
            {"support_style": "grid"},
            {"support_style": "grid"},
        )
        self.assertEqual(settings.get_str("support_style"), "grid")


class TranslateEdgeCaseTests(unittest.TestCase):
    def test_collapse_to_one_slot_remaps_every_assignment(self):
        with support.scratch() as tmp:
            tmp = pathlib.Path(tmp)
            source = build_foreign_project(tmp / "foreign.3mf", broken=False)
            kit = build_kit_project(tmp / "kit.3mf")
            translation = translate(source, kit, filaments=1)
            package = translation.package
            settings = ProjectSettings.from_package(package)
            ms = ModelSettings.from_package(package)

            self.assertEqual(settings.get_list("filament_colour"), ["#FF0000"])
            extruders = {
                ms.object_meta(part, "extruder")
                for obj in ms.object_elements()
                for part in ms.parts(obj)
            } | {ms.object_meta(obj, "extruder") for obj in ms.object_elements()}
            self.assertEqual(extruders, {"1"})
            gcode = ET.fromstring(package.require(CUSTOM_GCODE_PATH).decode())
            refs = {
                el.get("extruder")
                for el in gcode
                if localname(el.tag) == "layer" and el.get("extruder")
            }
            self.assertEqual(refs, {"1"})
            self.assertEqual(errors_of(package), [])

    def test_painted_faces_are_flagged_when_slots_collapse(self):
        """Per-face paint is a slot bitfield inside the mesh; we do not rewrite it."""
        import zipfile

        with support.scratch() as tmp:
            tmp = pathlib.Path(tmp)
            source = build_foreign_project(tmp / "paint.3mf", broken=False)
            # Mark one part's faces as painted, the way a multi-colour model is.
            with zipfile.ZipFile(source) as archive:
                entries = {name: archive.read(name) for name in archive.namelist()}
            part = "3D/Objects/part_2.model"
            entries[part] = entries[part].replace(
                b"<triangle ", b'<triangle paint_color="4" ', 1
            )
            with zipfile.ZipFile(source, "w", zipfile.ZIP_DEFLATED) as archive:
                for name, payload in entries.items():
                    archive.writestr(name, payload)

            kit = build_kit_project(tmp / "kit.3mf")
            translation = translate(source, kit, filaments=1)
            self.assertEqual(translation.report.paint_parts, 1)
            self.assertTrue(
                any("painted" in warning.lower() for warning in translation.report.warnings),
                translation.report.warnings,
            )

    def test_unchanged_slot_count_keeps_paint_silent(self):
        import zipfile

        with support.scratch() as tmp:
            tmp = pathlib.Path(tmp)
            source = build_foreign_project(tmp / "paint2.3mf", broken=False)
            with zipfile.ZipFile(source) as archive:
                entries = {name: archive.read(name) for name in archive.namelist()}
            entries["3D/Objects/part_2.model"] = entries["3D/Objects/part_2.model"].replace(
                b"<triangle ", b'<triangle paint_color="2" ', 1
            )
            with zipfile.ZipFile(source, "w", zipfile.ZIP_DEFLATED) as archive:
                for name, payload in entries.items():
                    archive.writestr(name, payload)

            kit = build_kit_project(tmp / "kit.3mf")
            translation = translate(source, kit)
            self.assertEqual(translation.report.paint_parts, 1)
            self.assertFalse(
                any("painted" in warning.lower() for warning in translation.report.warnings),
                "slot indices are unchanged, so paint is still valid",
            )

    def test_malformed_matrix_is_reported_and_not_written(self):
        with support.scratch() as tmp:
            tmp = pathlib.Path(tmp)
            source = build_foreign_project(
                tmp / "matrix.3mf", broken=False, bad_part_matrix=True
            )
            kit = build_kit_project(tmp / "kit.3mf")
            out = tmp / "out.3mf"
            stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = cli_main([
                    "translate", str(source), "--kit", str(kit), "--out", str(out),
                ])
            self.assertEqual(code, 2)
            self.assertFalse(out.exists(), "a project that fails its invariants must not be written")
            self.assertIn("V022", stdout.getvalue() + stderr.getvalue())

    def test_force_writes_a_project_that_fails_validation(self):
        with support.scratch() as tmp:
            tmp = pathlib.Path(tmp)
            source = build_foreign_project(
                tmp / "matrix.3mf", broken=False, bad_part_matrix=True
            )
            kit = build_kit_project(tmp / "kit.3mf")
            out = tmp / "out.3mf"
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                code = cli_main([
                    "translate", str(source), "--kit", str(kit), "--out", str(out), "--force",
                ])
            self.assertEqual(code, 0)
            self.assertTrue(out.exists())

    def test_preset_kit_reports_unresolved_inheritance(self):
        with support.scratch() as tmp:
            tmp = pathlib.Path(tmp)
            preset = tmp / "My Ender.json"
            preset.write_text(json.dumps({
                "from": "User",
                "inherits": "Creality Ender-3 V3 SE 0.4 nozzle",
                "name": "My Ender",
                "printer_settings_id": "My Ender",
                "printer_model": "Creality Ender-3 V3 SE",
                "printer_variant": "0.4",
                "nozzle_diameter": ["0.4"],
                "bed_shape": ["0x0", "220x0", "220x220", "0x220"],
                "printable_area": ["0x0", "220x0", "220x220", "0x220"],
                "printable_height": "250",
                "layer_height": "0.16",
            }), encoding="utf-8")
            kit = TargetKit.from_presets([preset], flavor="orca")
            self.assertTrue(kit.has_machine_geometry())
            self.assertIn("Creality Ender-3 V3 SE 0.4 nozzle", kit.unresolved_inherits)
            self.assertTrue(any("inherit" in w for w in kit.warnings))

            source = build_foreign_project(tmp / "foreign.3mf", broken=False)
            translation = translate(source, kit)
            settings = ProjectSettings.from_package(translation.package)
            self.assertEqual(settings.get_str("printer_settings_id"), "My Ender")
            self.assertEqual(settings.get_str("layer_height"), "0.2")
            self.assertEqual(errors_of(translation.package, kit), [])

    def test_missing_kit_keeps_source_machine_and_says_so(self):
        with support.scratch() as tmp:
            source = build_foreign_project(pathlib.Path(tmp) / "foreign.3mf", broken=False)
            translation = translate(source, None, target_flavor="orca")
            settings = ProjectSettings.from_package(translation.package)
            self.assertEqual(settings.get_str("printer_model"), "Bambu Lab X1 Carbon")
            self.assertTrue(
                any("no destination kit" in w for w in translation.report.warnings)
            )


@unittest.skipUnless(REAL_PROJECT and REAL_PROJECT.is_file(), "set X3MF_REAL_PROJECT to a real project to run this")
class RealProjectTests(unittest.TestCase):
    def test_roundtrip_real_project(self):
        with support.scratch() as tmp:
            out = pathlib.Path(tmp) / "roundtrip.3mf"
            translation = translate(REAL_PROJECT, REAL_PROJECT, target_flavor="orca")
            self.assertEqual(errors_of(translation.package), [])
            translation.save(out)
            reloaded = Package.read(out)
            self.assertFalse(reloaded.has(SLICE_INFO_PATH))
            settings = ProjectSettings.from_package(reloaded)
            self.assertEqual(settings.get_str("printer_model"), "Creality Ender-3 V3 SE")
            self.assertEqual(len(reloaded.names()), len(Package.read(REAL_PROJECT).names()) - 1)


if __name__ == "__main__":
    unittest.main()
