"""``python -m x3mf`` — inspect, translate and validate slicer project files."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .container import Package, PackageError
from .flavors import FLAVORS, TARGET_KEYS, TargetKit
from .settings import ProjectSettings
from .summary import describe, text_lines
from .translate import Options, TranslationError, Translator
from .validate import summarize, validate


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _load(path: str) -> Package:
    return Package.read(path)


def dossier(package: Package) -> list[str]:
    """A compact, quotable description of a project (shared with the web UI)."""
    return text_lines(describe(package))


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def cmd_inspect(args: argparse.Namespace) -> int:
    package = _load(args.file)
    print(f"{args.file}\n")
    lines = dossier(package)
    for line in lines:
        print(line)
    issues = validate(package)
    print()
    print(f"issues        {summarize(issues)}")
    for issue in issues:
        print(f"  {issue}")
    if args.json:
        print()
        print(json.dumps({"dossier": [ln.strip() for ln in lines],
                          "issues": [i.__dict__ for i in issues]}, indent=2))
    return 0


def _build_kit(args: argparse.Namespace) -> TargetKit | None:
    if args.kit:
        return TargetKit.from_project(args.kit)
    if args.preset:
        paths: list[Path] = []
        for raw in args.preset:
            path = Path(raw)
            if path.is_dir():
                paths.extend(sorted(p for p in path.rglob("*.json")))
            else:
                paths.append(path)
        if not paths:
            raise SystemExit(f"no preset JSON found in {args.preset}")
        base = TargetKit.from_project(args.base) if args.base else None
        return TargetKit.from_presets(paths, base=base, flavor=args.to or "orca")
    if args.base:
        return TargetKit.from_project(args.base)
    return None


def cmd_translate(args: argparse.Namespace) -> int:
    package = _load(args.source)
    kit = _build_kit(args)
    options = Options(
        target_flavor=args.to,
        app_string=args.app_string,
        version=args.version,
        filaments=args.filaments,
        extruders=args.extruders,
        keep_unknown=args.keep_unknown,
        keep_source_presets=args.keep_source_presets,
        drop_provenance=args.drop_provenance,
        drop_aux=args.drop_aux,
        force=args.force,
    )
    try:
        translator = Translator(package, kit, options)
        translation = translator.run()
    except TranslationError as exc:
        print(f"x3mf: {exc}", file=sys.stderr)
        if exc.report is not None:
            print(exc.report.text(), file=sys.stderr)
        return 1

    report = translation.report
    print(report.text())

    issues = validate(translation.package, kit=kit)
    errors = [i for i in issues if i.level == "error"]
    warnings = [i for i in issues if i.level == "warning"]
    print(f"output validation: {summarize(issues)}")
    for issue in issues:
        print(f"  {issue}")
    if errors and not args.force:
        print(
            "\nx3mf: refusing to write a project that fails its own invariants "
            "(pass --force to write it anyway)",
            file=sys.stderr,
        )
        return 2
    if args.dry_run:
        print("\n(dry run: nothing written)")
        return 0

    out = translation.save(args.out)
    print(f"\nwrote {out}  ({out.stat().st_size} bytes)")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    package = _load(args.file)
    kit = _build_kit(argparse.Namespace(kit=args.kit, preset=None, base=None, to=None))
    issues = validate(package, kit=kit)
    print(f"{args.file}: {summarize(issues)}")
    for issue in issues:
        print(f"  {issue}")
    return 1 if any(i.level == "error" for i in issues) else 0


def cmd_flavors(_: argparse.Namespace) -> int:
    print("target flavors (all write the same Orca-lineage project layout):")
    for key in TARGET_KEYS:
        flavor = FLAVORS[key]
        print(
            f"  {key:<11} {flavor.label:<38} "
            f"fallback Application={flavor.app_string('<version>')!r}"
        )
    print(
        "\nThe Application stamp and the Bambu 3MF marker are adopted from the kit when one\n"
        "is supplied, because the kit is the artefact the destination itself wrote."
    )
    return 0


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="x3mf",
        description=(
            "Translate a slicer 3MF *project* (not just its meshes) into another "
            "slicer's native project, carrying plates, parts, filament assignment, "
            "colours, colour changes, height modifiers and process settings."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # The web interface keeps its own flag set; hand it the remainder rather than
    # duplicating every option in this parser.
    p_web = sub.add_parser("web", help="run the local web interface", add_help=False)
    p_web.add_argument("web_args", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
    p_web.set_defaults(func=cmd_web)

    p_inspect = sub.add_parser("inspect", help="describe a project and diagnose it")
    p_inspect.add_argument("file")
    p_inspect.add_argument("--json", action="store_true", help="also dump machine-readable output")
    p_inspect.set_defaults(func=cmd_inspect)

    p_tr = sub.add_parser("translate", help="translate a project to a destination kit")
    p_tr.add_argument("source")
    p_tr.add_argument("--out", required=True, help="output .3mf")
    p_tr.add_argument("--kit", help="a .3mf saved from the destination slicer (best input)")
    p_tr.add_argument("--preset", action="append", metavar="PATH",
                      help="preset JSON or directory of presets (repeatable)")
    p_tr.add_argument("--base", help="kit project used as the resolution base for sparse presets")
    p_tr.add_argument("--to", choices=TARGET_KEYS, help="target dialect (default: kit's, else source's)")
    p_tr.add_argument("--app-string", help="override the Application stamp written to the model")
    p_tr.add_argument("--version", help="override the settings version stamp")
    p_tr.add_argument("--filaments", type=int, help="force the number of filament slots")
    p_tr.add_argument("--extruders", type=int, help="force the extruder count")
    p_tr.add_argument("--keep-unknown", action="store_true",
                      help="carry source keys the kit does not declare")
    p_tr.add_argument("--keep-source-presets", action="store_true",
                      help="do not substitute the kit's filament presets")
    p_tr.add_argument("--drop-provenance", action="store_true",
                      help="strip source_file/source_offset_* part metadata")
    p_tr.add_argument("--drop-aux", action="store_true",
                      help="drop layer-range, colour-change and sequencing metadata")
    p_tr.add_argument("--force", action="store_true", help="write even if validation fails")
    p_tr.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    p_tr.set_defaults(func=cmd_translate)

    p_val = sub.add_parser("validate", help="check a project against the output invariants")
    p_val.add_argument("file")
    p_val.add_argument("--kit", help="kit project to compare machine keys against")
    p_val.set_defaults(func=cmd_validate)

    p_fl = sub.add_parser("flavors", help="list target dialects")
    p_fl.set_defaults(func=cmd_flavors)
    return parser


def cmd_web(args: argparse.Namespace) -> int:
    """Delegate to the web server's own argument parser."""
    from .web import main as web_main

    return web_main(list(args.web_args))


def main(argv: list[str] | None = None) -> int:
    # Reports quote file names, printer names and setting values; the default
    # Windows console code page cannot encode all of them, and a crash while
    # printing a report is worse than a replacement character.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):  # redirected or detached stream
            pass

    raw = list(sys.argv[1:] if argv is None else argv)
    if raw[:1] == ["web"]:
        # argparse cannot forward "-h" through a subparser, and the web parser
        # has its own flags; delegate before parsing instead of duplicating them.
        from .web import main as web_main

        return web_main(raw[1:])

    parser = build_parser()
    args = parser.parse_args(raw)
    try:
        return args.func(args)
    except FileNotFoundError as exc:
        print(f"x3mf: {exc}", file=sys.stderr)
        return 1
    except (PackageError, ValueError) as exc:
        print(f"x3mf: {exc}", file=sys.stderr)
        return 1
