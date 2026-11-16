import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import support  # noqa: E402,F401  (redirects tempfile into the workspace)

from x3mf.container import CONTENT_TYPES_PATH, Package, parse_relationships  # noqa: E402


class ContainerTests(unittest.TestCase):
    def test_read_write_preserves_entries(self):
        from fixtures import build_foreign_project
        import tempfile

        with support.scratch() as tmp:
            src = build_foreign_project(pathlib.Path(tmp) / "a.3mf", broken=False)
            package = Package.read(src)
            names = package.names()
            out = package.write(pathlib.Path(tmp) / "b.3mf")
            again = Package.read(out)
        self.assertEqual(names, again.names())
        for name in names:
            self.assertEqual(package.require(name), again.require(name), name)

    def test_ensure_content_types_declares_json(self):
        from fixtures import build_foreign_project
        import tempfile

        with support.scratch() as tmp:
            src = build_foreign_project(pathlib.Path(tmp) / "a.3mf", broken=False)
            package = Package.read(src)
            self.assertNotIn("json", package.require(CONTENT_TYPES_PATH).decode().lower())
            added = package.ensure_content_types()
            self.assertIn("json", added)
            self.assertIn('Extension="json"', package.require(CONTENT_TYPES_PATH).decode())

    def test_ensure_part_relationships_repairs_dangling_component(self):
        from fixtures import build_foreign_project
        import tempfile

        with support.scratch() as tmp:
            src = build_foreign_project(pathlib.Path(tmp) / "a.3mf", broken=True)
            package = Package.read(src)
            self.assertNotIn("3D/Objects/part_8.model", package.relationship_targets())
            missing = package.ensure_part_relationships()
            rels = parse_relationships(
                package.require("3D/_rels/3dmodel.model.rels").decode()
            )
            targets = {r["target"].lstrip("/") for r in rels}
        self.assertIn("3D/Objects/part_8.model", missing)
        self.assertIn("3D/Objects/part_8.model", targets)

    def test_primary_model_path_from_relationships(self):
        from fixtures import build_foreign_project
        import tempfile

        with support.scratch() as tmp:
            src = build_foreign_project(pathlib.Path(tmp) / "a.3mf", broken=False)
            package = Package.read(src)
        self.assertEqual(package.primary_model_path(), "3D/3dmodel.model")


if __name__ == "__main__":
    unittest.main()
