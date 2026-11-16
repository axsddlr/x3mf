"""HTTP-level tests for the web interface.

The server is started on an ephemeral port inside the test process and driven
with ``urllib``, so these tests exercise the real request path: multipart
parsing, translation, validation refusal and the download store.
"""

import json
import pathlib
import sys
import threading
import unittest
import urllib.error
import urllib.request
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import support  # noqa: E402,F401  (redirects tempfile into the workspace)

from x3mf.container import Package  # noqa: E402
from x3mf.web import App, make_server  # noqa: E402
from x3mf.validate import validate  # noqa: E402

from fixtures import build_foreign_project, build_kit_project  # noqa: E402


def multipart(fields=None, files=None):
    boundary = "----x3mf" + uuid.uuid4().hex
    chunks = []
    for name, value in (fields or {}).items():
        chunks.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n".encode("utf-8")
        )
    for name, (filename, payload) in (files or {}).items():
        chunks.append(
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; '
                f'filename="{filename}"\r\nContent-Type: application/octet-stream\r\n\r\n'
            ).encode("utf-8")
            + payload
            + b"\r\n"
        )
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


class ServerCase(unittest.TestCase):
    max_upload = 8 * 1024 * 1024

    @classmethod
    def setUpClass(cls):
        cls.scratch = support.scratch("web")
        cls.app = App(
            work_dir=pathlib.Path(cls.scratch) / "work",
            max_upload=cls.max_upload,
            quiet=True,
        )
        cls.server = make_server(cls.app, "127.0.0.1", 0)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    # -- helpers ------------------------------------------------------------
    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def get(self, path):
        request = urllib.request.Request(self.url(path), method="GET")
        return self._open(request)

    def post(self, path, fields=None, files=None, body=None, content_type=None):
        if body is None:
            body, content_type = multipart(fields, files)
        request = urllib.request.Request(
            self.url(path), data=body, method="POST",
            headers={"Content-Type": content_type, "Content-Length": str(len(body))},
        )
        return self._open(request)

    def _open(self, request):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, response.read(), dict(response.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(), dict(exc.headers)

    # -- fixtures -----------------------------------------------------------
    @classmethod
    def fixture(cls, name="foreign.3mf", **kwargs):
        path = pathlib.Path(cls.scratch) / name
        if not path.exists():
            build_foreign_project(path, **kwargs)
        return path

    @classmethod
    def kit(cls):
        path = pathlib.Path(cls.scratch) / "kit.3mf"
        if not path.exists():
            build_kit_project(path)
        return path


class CliEntryPointTests(unittest.TestCase):
    def test_web_is_a_discoverable_subcommand(self):
        from x3mf.cli import build_parser

        args = build_parser().parse_args(["web"])
        self.assertEqual(args.command, "web")
        # `x3mf web --flag` is delegated before argparse runs, so the web parser
        # keeps its own flags (and its own -h).
        self.assertEqual(list(args.web_args), [])


class StaticTests(ServerCase):
    def test_index_is_served(self):
        status, body, headers = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertIn(b"x3mf", body)

    def test_assets_are_served(self):
        for path, kind in (("/app.css", "text/css"), ("/app.js", "text/javascript")):
            status, body, headers = self.get(path)
            self.assertEqual(status, 200, path)
            self.assertIn(kind, headers["Content-Type"])
            self.assertGreater(len(body), 500)

    def test_kofi_link_is_present_and_out_of_the_way(self):
        status, body, _ = self.get("/")
        self.assertEqual(status, 200)
        html = body.decode()
        self.assertIn('href="https://ko-fi.com/J3J1IR40C"', html)
        self.assertIn('target="_blank"', html)
        self.assertIn('rel="noopener noreferrer"', html)
        self.assertIn('aria-label="Support this project on Ko-fi', html)
        # It belongs to the footer, not to a floating overlay.
        self.assertGreater(html.index('class="kofi"'), html.index("<footer"))
        _, css, _ = self.get("/app.css")
        self.assertNotRegex(
            css.decode(), r"\.kofi[^{]*\{[^}]*position:\s*(fixed|sticky)"
        )

    def test_page_has_one_h1_and_keyboard_reachable_file_inputs(self):
        _, body, _ = self.get("/")
        html = body.decode()
        self.assertEqual(html.count("<h1"), 1, "the page needs exactly one h1")
        for control in ("source-input", "kit-input"):
            self.assertIn(f'id="{control}"', html)
            self.assertNotRegex(
                html, rf'id="{control}"[^>]*\bhidden\b',
                f"{control} must stay focusable for keyboard users",
            )

    def test_health(self):
        status, body, _ = self.get("/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["service"], "x3mf")

    def test_unknown_download_is_gone(self):
        status, body, _ = self.get("/api/download/deadbeef")
        self.assertEqual(status, 404)
        self.assertFalse(json.loads(body)["ok"])


class InspectTests(ServerCase):
    def test_inspect_reports_structure_and_defects(self):
        status, body, _ = self.post(
            "/api/inspect", files={"source": ("foreign.3mf", self.fixture().read_bytes())}
        )
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["source"]["layout"]["plates"], 2)
        self.assertEqual(len(payload["source"]["filaments"]), 4)
        codes = {issue["code"] for issue in payload["issues"]}
        self.assertIn("V024", codes)
        self.assertIn("V027", codes)

    def test_inspect_rejects_a_non_3mf(self):
        status, body, _ = self.post(
            "/api/inspect", files={"source": ("notes.3mf", b"not a zip at all")}
        )
        self.assertEqual(status, 400)
        message = json.loads(body)["error"]
        self.assertIn("notes.3mf", message)          # the user's filename, not a scratch path
        self.assertIn("not a 3MF project", message)
        self.assertNotIn(".x3mf-web", message)       # no server internals
        self.assertNotIn("zip file", message)        # no exception text

    def test_inspect_requires_a_file(self):
        status, body, _ = self.post("/api/inspect", fields={"source": "x"})
        self.assertEqual(status, 400)


class TranslateTests(ServerCase):
    def test_translates_and_serves_a_download(self):
        status, body, _ = self.post(
            "/api/translate",
            fields={"options": json.dumps({"to": "orca"})},
            files={
                "source": ("foreign.3mf", self.fixture().read_bytes()),
                "kit": ("kit.3mf", self.kit().read_bytes()),
            },
        )
        self.assertEqual(status, 200, body[:400])
        payload = json.loads(body)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "ok")
        # Named after the destination, not the dialect key.
        self.assertEqual(
            payload["download"]["filename"],
            "foreign - Creality Ender-3 V3 SE (OrcaSlicer).3mf",
        )
        self.assertGreater(payload["download"]["bytes"], 1000)

        # The verdict answers "can I print this" before the audit trail.
        verdict = payload["verdict"]
        self.assertEqual(verdict["tone"], "warn")  # this fixture drops two things
        self.assertTrue(verdict["headline"])
        self.assertTrue(any("Geometry untouched" in fact for fact in verdict["facts"]))
        self.assertTrue(any("Colours" in fact for fact in verdict["facts"]))

        # Report copy is humanised: no "(s)" pluralisation leaks to the UI.
        for bucket in ("carried", "adjusted", "dropped", "warnings"):
            for item in payload["report"][bucket]:
                self.assertNotIn("(s)", item, item)

        status, data, headers = self.get("/api/download/" + payload["download"]["id"])
        self.assertEqual(status, 200)
        self.assertIn("attachment", headers["Content-Disposition"])
        self.assertGreater(len(data), 1000)

        joined = pathlib.Path(self.scratch) / "web-download.3mf"
        joined.write_bytes(data)
        package = Package.read(joined)
        errors = [i for i in validate(package) if i.level == "error"]
        self.assertEqual(errors, [])
        self.assertFalse(package.has("Metadata/slice_info.config"))

    def test_refuses_a_project_that_fails_validation(self):
        bad = pathlib.Path(self.scratch) / "matrix.3mf"
        if not bad.exists():
            build_foreign_project(bad, broken=False, bad_part_matrix=True)
        status, body, _ = self.post(
            "/api/translate",
            fields={"options": "{}"},
            files={
                "source": ("matrix.3mf", bad.read_bytes()),
                "kit": ("kit.3mf", self.kit().read_bytes()),
            },
        )
        self.assertEqual(status, 422)
        payload = json.loads(body)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "refused")
        self.assertIsNone(payload["download"])
        self.assertIn("V022", {issue["code"] for issue in payload["issues"]})

    def test_force_writes_the_refused_project(self):
        bad = pathlib.Path(self.scratch) / "matrix.3mf"
        if not bad.exists():
            build_foreign_project(bad, broken=False, bad_part_matrix=True)
        status, body, _ = self.post(
            "/api/translate",
            fields={"options": json.dumps({"force": True})},
            files={
                "source": ("matrix.3mf", bad.read_bytes()),
                "kit": ("kit.3mf", self.kit().read_bytes()),
            },
        )
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertIsNotNone(payload["download"])

    def test_download_name_follows_the_destination_printer(self):
        status, body, _ = self.post(
            "/api/translate",
            fields={"options": "{}"},
            files={
                "source": ("my part.v2.3mf", self.fixture().read_bytes()),
                "kit": ("kit.3mf", self.kit().read_bytes()),
            },
        )
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(
            payload["download"]["filename"],
            "my part.v2 - Creality Ender-3 V3 SE (OrcaSlicer).3mf",
        )

    def test_download_name_without_a_kit_uses_the_source_printer(self):
        status, body, _ = self.post(
            "/api/translate",
            fields={"options": json.dumps({"to": "flashforge"})},
            files={"source": ("bambu_model.3mf", self.fixture().read_bytes())},
        )
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(
            payload["download"]["filename"],
            "bambu_model - Bambu Lab X1 Carbon (Flash Studio).3mf",
        )

    def test_download_name_strips_illegal_characters(self):
        status, body, _ = self.post(
            "/api/translate",
            fields={"options": "{}"},
            files={
                "source": ('bad:name*?"<>|.3mf', self.fixture().read_bytes()),
                "kit": ("kit.3mf", self.kit().read_bytes()),
            },
        )
        self.assertEqual(status, 200)
        name = json.loads(body)["download"]["filename"]
        self.assertFalse(set(name) & set('<>:"/\\|?*'), name)
        self.assertTrue(name.endswith(".3mf"))

    def test_translates_without_a_kit(self):
        status, body, _ = self.post(
            "/api/translate",
            fields={"options": json.dumps({"to": "orca"})},
            files={"source": ("foreign.3mf", self.fixture().read_bytes())},
        )
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["ok"])
        self.assertTrue(
            any("no destination kit" in warning for warning in payload["report"]["warnings"])
        )

    def test_collapsing_slots_is_reflected_in_the_report(self):
        status, body, _ = self.post(
            "/api/translate",
            fields={"options": json.dumps({"filaments": 1})},
            files={
                "source": ("foreign.3mf", self.fixture().read_bytes()),
                "kit": ("kit.3mf", self.kit().read_bytes()),
            },
        )
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(len(payload["target"]["filaments"]), 1)
        self.assertTrue(
            any("collapsed" in item for item in payload["report"]["adjusted"]),
            payload["report"]["adjusted"],
        )


class LimitTests(ServerCase):
    max_upload = 200

    def test_oversize_upload_is_rejected(self):
        request = urllib.request.Request(
            self.url("/api/inspect"),
            data=b"x" * 5000,
            method="POST",
            headers={"Content-Type": "multipart/form-data; boundary=x", "Content-Length": "5000"},
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                status = response.status
                body = response.read()
        except urllib.error.HTTPError as exc:
            status, body = exc.code, exc.read()
        except OSError:
            self.skipTest("server closed the connection before responding")
            return
        self.assertEqual(status, 413)
        self.assertIn("limit", json.loads(body)["error"])


if __name__ == "__main__":
    unittest.main()
