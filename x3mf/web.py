"""A local web interface for the translator.

One process, standard library only, no build step:

    python -m x3mf.web            # http://127.0.0.1:8756

Design constraints that shaped this file:

* The browser never learns a filesystem path. Uploads are written to a scratch
  directory, results are held in memory under an opaque id, and downloads are
  served from that id alone.
* A refused translation is a *successful* response with ``status: "refused"``
  and the validator's findings — the UI needs to explain why a project was not
  produced, not just paint an error box.
* The server is single-purpose and local by default. Binding a public interface
  is an explicit flag, and the upload cap, result TTL and concurrency limit are
  all bounded so a stray request cannot exhaust the machine.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import traceback
import uuid
import webbrowser
from dataclasses import dataclass, field
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from .container import Package, PackageError
from .flavors import FLAVORS, TargetKit
from .settings import ProjectSettings
from .summary import describe
from .translate import Options, TranslationError, Translator
from .validate import summarize, validate

UI_DIR = Path(__file__).resolve().parent / "webui"
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
}

MAX_UPLOAD_DEFAULT = 256 * 1024 * 1024

#: Labels for the bundled samples exposed by ``--demo``.
SAMPLES = {
    "source": "a Bambu-dialect project: 2 plates, 4 filaments, 2 height ranges, 2 colour changes",
    "kit": "an OrcaSlicer project for a Creality Ender-3 V3 SE",
}
MAX_ENTRIES = 4000
MAX_UNPACKED = 512 * 1024 * 1024


# ---------------------------------------------------------------------------
# result store
# ---------------------------------------------------------------------------

@dataclass
class Result:
    payload: bytes
    filename: str
    created: float


class ResultStore:
    """Short-lived downloads, keyed by an opaque id."""

    def __init__(self, ttl_seconds: int = 900, max_items: int = 32) -> None:
        self._items: dict[str, Result] = {}
        self._lock = threading.Lock()
        self._ttl = ttl_seconds
        self._max = max_items

    def put(self, payload: bytes, filename: str) -> str:
        token = uuid.uuid4().hex
        now = time.time()
        with self._lock:
            self._prune(now)
            if len(self._items) >= self._max:
                oldest = min(self._items, key=lambda k: self._items[k].created)
                self._items.pop(oldest, None)
            self._items[token] = Result(payload, filename, now)
        return token

    def get(self, token: str) -> Result | None:
        with self._lock:
            self._prune(time.time())
            return self._items.get(token)

    def _prune(self, now: float) -> None:
        dead = [k for k, v in self._items.items() if now - v.created > self._ttl]
        for key in dead:
            self._items.pop(key, None)


# ---------------------------------------------------------------------------
# multipart
# ---------------------------------------------------------------------------

def parse_multipart(content_type: str, body: bytes) -> tuple[dict[str, str], dict[str, tuple[str, bytes]]]:
    """Return (text fields, file fields) from a ``multipart/form-data`` body."""
    if "boundary=" not in content_type:
        raise ValueError("expected multipart/form-data")
    message = BytesParser(policy=policy.default).parsebytes(
        b"Content-Type: " + content_type.encode("latin-1") + b"\r\n"
        b"MIME-Version: 1.0\r\n\r\n" + body
    )
    if not message.is_multipart():
        raise ValueError("body is not multipart")
    fields: dict[str, str] = {}
    files: dict[str, tuple[str, bytes]] = {}
    for part in message.iter_parts():
        if part.get_content_disposition() != "form-data":
            continue
        name = part.get_param("name", header="content-disposition") or ""
        filename = part.get_filename()
        payload = part.get_payload(decode=True) or b""
        if filename:
            files[str(name)] = (Path(filename).name, payload)
        else:
            fields[str(name)] = payload.decode("utf-8", "replace")
    return fields, files


# ---------------------------------------------------------------------------
# application
# ---------------------------------------------------------------------------

@dataclass
class App:
    work_dir: Path
    max_upload: int = MAX_UPLOAD_DEFAULT
    ttl_seconds: int = 900
    results: ResultStore = field(default_factory=ResultStore)
    quiet: bool = False
    examples: dict[str, Path] = field(default_factory=dict)
    _slots: threading.BoundedSemaphore = field(default_factory=lambda: threading.BoundedSemaphore(2))

    def __post_init__(self) -> None:
        self.work_dir.mkdir(parents=True, exist_ok=True)

    # -- helpers ------------------------------------------------------------
    def stage(self, name: str, payload: bytes) -> Path:
        """Write an upload to scratch space under a name we control."""
        suffix = Path(name).suffix if Path(name).suffix.lower() in (".3mf",) else ".3mf"
        path = self.work_dir / f"{uuid.uuid4().hex}{suffix}"
        path.write_bytes(payload)
        return path

    def read_package(self, name: str, payload: bytes) -> tuple[Package, str]:
        clean_name = Path(name).name or "upload.3mf"
        if not payload:
            raise ValueError(f"“{clean_name}” is empty. Choose the .3mf your slicer saved.")
        path = self.stage(name, payload)
        try:
            package = Package.read(path)
        except PackageError:
            # The scratch path is an implementation detail and the zipfile
            # exception means nothing to the user; name their file instead.
            raise ValueError(
                f"“{clean_name}” is not a 3MF project. Check that it is the file "
                "your slicer exported (File ▸ Save Project), not a model or a screenshot."
            ) from None
        if len(package.names()) > MAX_ENTRIES:
            raise ValueError(
                f"“{clean_name}” contains {len(package.names())} parts, more than this tool accepts."
            )
        unpacked = sum(len(package.require(n)) for n in package.names())
        if unpacked > MAX_UNPACKED:
            raise ValueError(f"“{clean_name}” expands beyond the allowed size.")
        return package, clean_name

    # -- bundled samples ----------------------------------------------------
    def example(self, role: str) -> tuple[str, bytes]:
        """Return a bundled sample by role, never by path."""
        path = self.examples.get(role)
        if path is None or not path.is_file():
            raise ValueError(f"no bundled sample named {role!r}")
        return path.name, path.read_bytes()

    def sample_list(self) -> list[dict]:
        out = []
        for role, path in self.examples.items():
            if not path.is_file():
                continue
            out.append({
                "role": role,
                "name": path.name,
                "bytes": path.stat().st_size,
                "label": SAMPLES.get(role, path.name),
            })
        return out

    # -- operations ---------------------------------------------------------
    def inspect(self, name: str, payload: bytes) -> dict:
        package, clean_name = self.read_package(name, payload)
        issues = validate(package)
        return {
            "ok": True,
            "source": describe(package, clean_name),
            "issues": [i.__dict__ for i in issues],
            "summary": summarize(issues),
        }

    def translate(
        self,
        source_name: str,
        source_bytes: bytes,
        kit_name: str | None,
        kit_bytes: bytes | None,
        options: dict,
    ) -> tuple[int, dict]:
        package, clean_name = self.read_package(source_name, source_bytes)
        kit = None
        if kit_bytes:
            kit = TargetKit.from_project(self.stage(kit_name or "kit.3mf", kit_bytes))

        opts = Options(
            target_flavor=options.get("to") or None,
            filaments=options.get("filaments") or None,
            extruders=options.get("extruders") or None,
            keep_unknown=bool(options.get("keep_unknown")),
            drop_aux=bool(options.get("drop_aux")),
            force=bool(options.get("force")),
        )

        translator = Translator(package, kit, opts)
        with self._slots:
            translation = translator.run()

        issues = validate(translation.package, kit=kit)
        errors = [i for i in issues if i.level == "error"]
        payload = {
            "ok": not errors,
            "status": "refused" if errors else "ok",
            "source": describe(package, clean_name),
            "report": {
                "counts": translation.report.counts,
                "carried": [_plural(item) for item in translation.report.carried],
                "adjusted": [_plural(item) for item in translation.report.adjusted],
                "dropped": [_plural(item) for item in translation.report.dropped],
                "warnings": [_plural(item) for item in translation.report.warnings],
                "paint_parts": translation.report.paint_parts,
            },
            "target": _target_summary(translation.package, translator.target_flavor.key),
            "issues": [i.__dict__ for i in issues],
            "summary": summarize(issues),
        }
        payload["verdict"] = build_verdict(payload["source"], payload["target"], payload["report"])
        if errors and not opts.force:
            payload["download"] = None
            return 422, payload

        filename = download_name(clean_name, payload["target"])
        data = translation.package.to_bytes()
        token = self.results.put(data, filename)
        payload["download"] = {"id": token, "filename": filename, "bytes": len(data)}
        return 200, payload


def _target_summary(package: Package, flavor: str) -> dict:
    """What the output is *for*.

    ``flavor`` is the target the translator chose, not the dialect detected from
    the written stamps: with no kit the source's Application is preserved on
    purpose, so re-detecting would report the source.
    """
    settings = ProjectSettings.from_package(package)
    described = describe(package)
    return {
        "flavor": flavor,
        "application": described["dialect"]["application"],
        "machine": settings.get_str("printer_model"),
        "variant": settings.get_str("printer_variant"),
        "process": settings.get_str("print_settings_id"),
        "printer_preset": settings.get_str("printer_settings_id"),
        "extruders": settings.extruder_count(),
        "filaments": [
            {"index": s.index + 1, "material": s.material, "colour": s.colour, "preset": s.preset_id}
            for s in settings.filaments()
        ],
        "plates": (described.get("layout") or {}).get("plates", 0),
    }


#: Short app names used in download filenames.  The long labels in
#: ``flavors.FLAVORS`` describe the lineage; these name the file the user opens.
PLATFORM_LABEL = {
    "bambu": "Bambu Studio",
    "orca": "OrcaSlicer",
    "flashforge": "Flash Studio",
    "snapmaker": "Snapmaker Orca",
    "generic": "Orca",
}

#: Characters that are illegal on Windows and awkward everywhere else.
ILLEGAL_FILENAME = set('<>:"/\\|?*')


def _clean_part(text: str, limit: int) -> str:
    cleaned = "".join(c for c in text if c >= " " and c not in ILLEGAL_FILENAME)
    cleaned = " ".join(cleaned.split())
    return cleaned[:limit].strip(" .")


def download_name(source_name: str, target: dict) -> str:
    """Name the result after where it is going.

    ``model - Snapmaker U1 (Snapmaker Orca).3mf`` tells the user which machine and
    which app the file belongs to; the source name only says what it came from.
    """
    stem = _clean_part(Path(source_name).stem, 80) or "project"
    printer = _clean_part(target.get("machine") or "", 48)
    platform = PLATFORM_LABEL.get(target.get("flavor") or "", "")

    if printer and platform:
        name = f"{stem} - {printer} ({platform}).3mf"
    elif printer:
        name = f"{stem} - {printer}.3mf"
    elif platform:
        name = f"{stem} ({platform}).3mf"
    else:
        name = f"{stem}.3mf"
    return name[:180]


def content_disposition(filename: str) -> str:
    """RFC 5987 disposition: an ASCII fallback plus the exact UTF-8 name."""
    from urllib.parse import quote

    fallback = filename.encode("ascii", "ignore").decode().strip() or "project.3mf"
    fallback = "".join(c for c in fallback if c not in '";\\')
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename)}"


def _plural(text: str) -> str:
    """Turn the engine's ``2 object(s)`` into ``2 objects`` for display.

    The number and the noun are not always adjacent (``26 process/appearance
    key(s)``), so the count is taken from the nearest integer *preceding* the
    noun rather than from the token immediately before it.
    """
    import re as _re

    def swap(match: "_re.Match[str]") -> str:
        noun = match.group(1)
        numbers = _re.findall(r"\b(\d+)\b", text[: match.start()])
        count = int(numbers[-1]) if numbers else 2
        return noun if count == 1 else f"{noun}s"

    return _re.sub(r"([A-Za-z][A-Za-z-]*)\(s\)", swap, text)


def build_verdict(source: dict, target: dict, report: dict) -> dict:
    """One sentence that answers "can I print this?" before the audit trail.

    The translator never touches geometry, so "geometry untouched" is a fact, not
    a guess.  Colours are compared slot by slot; drops and warnings are the two
    things that can actually cost someone a print.
    """
    dropped = report.get("dropped") or []
    warnings = report.get("warnings") or []
    layout = source.get("layout") or {}
    plates = layout.get("plates", 0)
    objects = layout.get("objects", 0)
    parts = layout.get("parts", 0)

    source_colours = [(f.get("colour") or "").lower() for f in source.get("filaments") or []]
    target_colours = [(f.get("colour") or "").lower() for f in target.get("filaments") or []]
    colours_preserved = bool(source_colours) and source_colours == target_colours
    slots = len(target_colours)

    def count(n: int, noun: str) -> str:
        return f"{n} {noun}" if n == 1 else f"{n} {noun}s"

    if dropped:
        tone = "warn"
        headline = f"Carried, with {count(len(dropped), 'item')} dropped."
        detail = "Check what was dropped before you print; everything else arrived."
    elif warnings:
        tone = "warn"
        headline = f"Carried, with {count(len(warnings), 'item')} worth a look."
        detail = "Geometry and colours are intact. Review the warnings, then slice."
    else:
        tone = "ok"
        headline = "Carried cleanly: nothing dropped, nothing flagged."
        detail = "Geometry, colours and settings all arrived. Safe to slice and print."

    facts = []
    if layout:
        facts.append(
            f"Geometry untouched: {count(plates, 'plate')}, "
            f"{count(objects, 'object')}, {count(parts, 'part')}"
        )
    else:
        facts.append("Geometry untouched: the source had no plate layout")
    facts.append(
        f"{'Colours preserved' if colours_preserved else 'Colours remapped'}"
        f" across {count(slots, 'filament slot')}"
    )
    facts.append(
        "Nothing dropped" if not dropped else f"{count(len(dropped), 'item')} dropped"
    )
    adjusted = report.get("adjusted") or []
    if adjusted:
        facts.append(f"{count(len(adjusted), 'adjustment')} listed below")
    paint = report.get("paint_parts") or 0
    if paint:
        same_slots = len(source_colours) == slots
        facts.append(
            f"{count(paint, 'part file')} with painted faces"
            + ("" if same_slots else " (not remapped)")
        )
        if not same_slots:
            tone = "warn"
            headline = "Carried, but painted faces need a look."
            detail = (
                "The slot count changed and per-face paint still points at the source "
                "slots. Check painted regions in the slicer preview."
            )
    if warnings:
        facts.append(count(len(warnings), "warning"))
    return {"tone": tone, "headline": headline, "detail": detail, "facts": facts}


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "x3mf/0.1"
    protocol_version = "HTTP/1.1"
    app: App  # injected by make_server

    # -- plumbing -----------------------------------------------------------
    def log_message(self, fmt: str, *args) -> None:
        if not self.app.quiet:
            sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def _send(self, status: int, body: bytes, content_type: str, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload: dict) -> None:
        self._send(status, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")

    def _fail(self, status: int, message: str, **extra) -> None:
        self._json(status, {"ok": False, "error": message, **extra})

    def _body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            raise ValueError("empty request body")
        if length > self.app.max_upload:
            self.close_connection = True
            raise OverSize(length)
        return self.rfile.read(length)

    # -- routes -------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in STATIC:
            name, content_type = STATIC[path]
            file = UI_DIR / name
            if not file.is_file():
                self._fail(500, f"UI asset missing: {name}")
                return
            self._send(200, file.read_bytes(), content_type)
            return
        if path == "/api/examples":
            self._json(200, {"ok": True, "samples": self.app.sample_list()})
            return
        if path.startswith("/api/examples/"):
            role = unquote(path.rsplit("/", 1)[-1])
            try:
                name, payload = self.app.example(role)
            except ValueError as exc:
                self._fail(404, str(exc))
                return
            self._send(200, payload, "model/3mf",
                       {"Content-Disposition": f'inline; filename="{name}"'})
            return
        if path == "/api/health":
            from . import __version__

            self._json(200, {"ok": True, "service": "x3mf", "version": __version__})
            return
        if path.startswith("/api/download/"):
            token = unquote(path.rsplit("/", 1)[-1])
            result = self.app.results.get(token)
            if result is None:
                self._fail(404, "this download has expired; translate the project again")
                return
            self._send(
                200,
                result.payload,
                "model/3mf",
                {"Content-Disposition": content_disposition(result.filename)},
            )
            return
        self._fail(404, "not found")

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            body = self._body()
        except OverSize as exc:
            self._fail(413, f"upload is larger than the {exc.limit // (1024 * 1024)} MB limit")
            return
        except ValueError as exc:
            self._fail(400, str(exc))
            return

        try:
            fields, files = parse_multipart(self.headers.get("Content-Type", ""), body)
        except ValueError as exc:
            self._fail(400, str(exc))
            return

        try:
            if path == "/api/inspect":
                if "source" in files:
                    name, payload = files["source"]
                elif fields.get("source_example"):
                    name, payload = self.app.example(fields["source_example"])
                else:
                    self._fail(400, "attach a .3mf project as 'source'")
                    return
                self._json(200, self.app.inspect(name, payload))
                return
            if path == "/api/translate":
                if "source" in files:
                    source_name, source_bytes = files["source"]
                elif fields.get("source_example"):
                    source_name, source_bytes = self.app.example(fields["source_example"])
                else:
                    self._fail(400, "attach a .3mf project as 'source'")
                    return
                if "kit" in files:
                    kit_name, kit_bytes = files["kit"]
                elif fields.get("kit_example"):
                    kit_name, kit_bytes = self.app.example(fields["kit_example"])
                else:
                    kit_name, kit_bytes = None, None
                options = json.loads(fields.get("options") or "{}")
                status, payload = self.app.translate(
                    source_name,
                    source_bytes,
                    kit_name,
                    kit_bytes,
                    options if isinstance(options, dict) else {},
                )
                self._json(status, payload)
                return
            self._fail(404, "not found")
        except (PackageError, ValueError) as exc:
            self._fail(400, str(exc))
        except TranslationError as exc:
            self._json(422, {"ok": False, "status": "error", "error": str(exc), "issues": []})
        except Exception:  # pragma: no cover - unexpected
            traceback.print_exc()
            self._fail(500, "the translator hit an unexpected error; see the server log")


class OverSize(Exception):
    def __init__(self, limit: int) -> None:
        super().__init__("upload too large")
        self.limit = limit


def make_server(app: App, host: str, port: int) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"app": app})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="x3mf.web",
        description="Serve the x3mf project translator as a local web app.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="interface to bind (default: localhost only)")
    parser.add_argument("--port", type=int, default=8756, help="port to listen on")
    parser.add_argument("--work-dir", default=".x3mf-web", help="scratch directory for uploads")
    parser.add_argument("--max-upload-mb", type=int, default=MAX_UPLOAD_DEFAULT // (1024 * 1024))
    parser.add_argument("--demo", action="store_true",
                        help="expose the bundled examples as one-click samples")
    parser.add_argument("--examples", default="examples", help="directory used by --demo")
    parser.add_argument("--open", action="store_true", help="open the browser once listening")
    args = parser.parse_args(argv)

    examples: dict[str, Path] = {}
    if args.demo:
        root = Path(args.examples).resolve()
        examples = {"source": root / "foreign_broken.3mf", "kit": root / "kit_ender.3mf"}

    app = App(
        work_dir=Path(args.work_dir).resolve(),
        max_upload=args.max_upload_mb * 1024 * 1024,
        examples=examples,
    )
    try:
        server = make_server(app, args.host, args.port)
    except OSError as exc:
        print(f"x3mf.web: cannot listen on {args.host}:{args.port}: {exc}", file=sys.stderr)
        return 1

    host, port = server.server_address[:2]
    shown = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    url = f"http://{shown}:{port}/"
    print(f"x3mf web interface listening on {url}")
    print(f"  uploads  {app.work_dir}")
    print(f"  limit    {args.max_upload_mb} MB per file, 2 concurrent translations")
    if examples:
        print(f"  samples  {len(app.sample_list())} bundled example(s) from {Path(args.examples).resolve()}")
    if args.open:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
