import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import support  # noqa: E402,F401  (redirects tempfile into the workspace)

from x3mf.container import Package  # noqa: E402
from x3mf.settings import classify, is_per_filament  # noqa: E402
from x3mf.validate import summarize, validate  # noqa: E402

from fixtures import build_foreign_project, build_kit_project  # noqa: E402


def _codes(path):
    return {issue.code for issue in validate(Package.read(path))}


class DiffListTests(unittest.TestCase):
    """different_settings_to_system has two legitimate shapes, not one."""

    def _issues(self, **kwargs):
        with support.scratch() as tmp:
            path = build_foreign_project(pathlib.Path(tmp) / "diff.3mf", broken=False, **kwargs)
            return validate(Package.read(path))

    def test_bambu_per_filament_shape_is_accepted(self):
        # Bambu writes process + one entry per filament slot + printer, so a
        # four-filament project has six entries. That is not malformed.
        issues = self._issues(
            filaments=4,
            extra={"different_settings_to_system": ["", "", "", "", "", ""]},
        )
        self.assertNotIn("V048", {issue.code for issue in issues})

    def test_orca_three_entry_shape_is_accepted(self):
        issues = self._issues(filaments=4, extra={"different_settings_to_system": ["", "", ""]})
        self.assertNotIn("V048", {issue.code for issue in issues})

    def test_other_lengths_are_reported_with_the_numbers(self):
        issues = [
            issue for issue in self._issues(
                filaments=4,
                extra={"different_settings_to_system": ["", "", "", ""]},
            ) if issue.code == "V048"
        ]
        self.assertTrue(issues)
        self.assertIn("4 entr", issues[0].message)
        self.assertIn("6", issues[0].message)


class SliceEvidenceTests(unittest.TestCase):
    """V060 warns about sliced g-code, not about a leftover slice summary."""

    def test_leftover_summary_is_information_not_a_warning(self):
        with support.scratch() as tmp:
            path = build_foreign_project(pathlib.Path(tmp) / "sliced.3mf", broken=False)
            issues = [i for i in validate(Package.read(path)) if i.code == "V060"]
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].level, "info")
        self.assertIn("no g-code", issues[0].message)

    def test_carried_gcode_is_a_warning(self):
        import zipfile

        with support.scratch() as tmp:
            path = build_foreign_project(pathlib.Path(tmp) / "sliced.3mf", broken=False)
            with zipfile.ZipFile(path) as archive:
                entries = {name: archive.read(name) for name in archive.namelist()}
            entries["Metadata/plate_1.gcode"] = b"; gcode for another printer\n"
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
                for name, payload in entries.items():
                    archive.writestr(name, payload)
            issues = [i for i in validate(Package.read(path)) if i.code == "V060"]
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].level, "warning")
        self.assertIn("g-code", issues[0].message)


class ValidateTests(unittest.TestCase):
    def test_broken_fixture_is_diagnosed(self):
        with support.scratch() as tmp:
            path = build_foreign_project(pathlib.Path(tmp) / "broken.3mf", broken=True)
            codes = _codes(path)
        self.assertIn("V013", codes, "missing part relationship not detected")
        self.assertIn("V024", codes, "plate instance to unknown object not detected")
        self.assertIn("V027", codes, "out-of-range filament assignment not detected")
        self.assertIn("V028", codes, "plate filament_maps length not checked")
        self.assertIn("V060", codes, "stale slice_info not reported")

    def test_clean_fixture_has_no_errors(self):
        with support.scratch() as tmp:
            path = build_foreign_project(pathlib.Path(tmp) / "clean.3mf", broken=False)
            issues = validate(Package.read(path))
        errors = [i for i in issues if i.level == "error"]
        self.assertEqual(errors, [], f"unexpected errors: {summarize(issues)}")

    def test_malformed_part_matrix_detected(self):
        with support.scratch() as tmp:
            path = build_foreign_project(
                pathlib.Path(tmp) / "matrix.3mf", broken=False, bad_part_matrix=True
            )
            codes = _codes(path)
        self.assertIn("V022", codes)

    def test_bad_plate_thumbnail_detected(self):
        from fixtures import build_foreign_project as build

        with support.scratch() as tmp:
            path = build(pathlib.Path(tmp) / "thumb.3mf", broken=False)
            package = Package.read(path)
            package.remove("Metadata/plate_1.png")
            # keep the plate metadata pointing at the removed file
            issues = validate(package)
            codes = {i.code for i in issues}
            # the metadata is only written when a plate references a missing file
            package2 = Package.read(path)
            ms = package2.require("Metadata/model_settings.config").decode()
            self.assertIn("thumbnail_file", ms)
        self.assertTrue(codes & {"V030"}, f"expected a thumbnail defect, saw {codes}")

    def test_kit_project_validates(self):
        with support.scratch() as tmp:
            path = build_kit_project(pathlib.Path(tmp) / "kit.3mf")
            issues = validate(Package.read(path))
        errors = [i for i in issues if i.level == "error"]
        self.assertEqual(errors, [])


class ClassificationTests(unittest.TestCase):
    def test_geometry_is_machine(self):
        for key in ("bed_shape", "nozzle_diameter", "printer_model", "machine_max_jerk_x"):
            self.assertEqual(classify(key), "machine", key)

    def test_process_keys(self):
        for key in ("layer_height", "wall_loops", "outer_wall_speed", "sparse_infill_density"):
            self.assertEqual(classify(key), "process", key)

    def test_gcode_templates_are_machine_settings(self):
        # Carrying these across printers makes the target run foreign motion code.
        for key in ("layer_change_gcode", "before_layer_change_gcode",
                    "change_filament_gcode", "printing_by_object_gcode", "start_gcode"):
            self.assertEqual(classify(key), "machine", key)
        # Filament-scoped gcode belongs to the filament profile.
        self.assertEqual(classify("filament_start_gcode"), "filament")
        self.assertEqual(classify("filament_end_gcode"), "filament")

    def test_per_filament_detection(self):
        self.assertTrue(is_per_filament("filament_max_volumetric_speed"))
        self.assertTrue(is_per_filament("nozzle_temperature"))
        # sized by extruder or by [max, min], never by filament count
        self.assertFalse(is_per_filament("flush_volumes_vector"))
        self.assertFalse(is_per_filament("machine_max_acceleration_x"))
        self.assertFalse(is_per_filament("filament_map"))


if __name__ == "__main__":
    unittest.main()
