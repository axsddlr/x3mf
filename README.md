# x3mf

Translate a slicer **3MF project** — not just its meshes — into another slicer's
native project.

A `.3mf` saved by a slicer is not geometry. It is geometry plus a whole print
setup: plates, per-part roles and transforms, which filament each object uses,
the filament colours, layer colour changes, height-range ("layer range")
modifiers, and several hundred process settings. Copy the meshes into another
slicer and all of that is gone.

`x3mf` rewrites the project itself, and it does so with a rule:

> **The destination's own presets decide the machine. The source project
> decides the print.**

Printer geometry, nozzle, machine limits and the preset names come from a *kit*
— a small project saved from the destination slicer, or its preset JSONs.
Process tuning, plates, parts, filaments and colours come from the source.
Carrying the source's bed size into a different printer is how a file "opens
fine" and then prints off the bed, so it never happens: machine keys are taken
from the kit, and the validator re-asserts that before anything is written.

## What it carries

| Carried | Where it lives |
|---|---|
| Plates, plate names, locked state, filament maps | `Metadata/model_settings.config` |
| Objects, per-part roles (`modifier_part`, `support_blocker`, `negative_part`), matrices, part ids | `model_settings.config` + `3D/Objects/*.model` |
| Per-object / per-part filament assignment (remapped, clamped if out of range) | `model_settings.config` |
| Filament count, material, colour, per-slot preset | `project_settings.config` |
| Layer colour changes and pauses at their original Z heights | `Metadata/custom_gcode_per_layer.xml` |
| Height-range modifiers and their per-range overrides | `Metadata/layer_config_ranges.xml` |
| Process settings (speeds, widths, infill, temperatures…) | `project_settings.config` |
| Per-plate filament sequencing | `Metadata/filament_sequence.json` |

Byte-for-byte, when no identifier needs to change. The two layer-metadata files
have no published schema and differ between versions, so they are edited
*surgically* — only a recognised object id or filament index is substituted;
formatting, unknown attributes and element order survive untouched.

## What it does not carry

* **Geometry is never retouched.** Part files are copied verbatim.
* **Presets it cannot resolve.** If the destination has no preset for a
  material (say PETG), the source preset id is kept and a warning is printed;
  it is not silently replaced with a PLA profile.
* **Flush volumes** are dropped when the slot count changes: the row encoding
  differs between writers and a wrong guess is worse than the destination's own
  default. Unchanged counts keep the original matrix.
* **Filament sequencing** is dropped when the slot count changes, for the same
  reason (0- vs 1-based indices are not consistent across writers).
* **Stale slice artefacts.** `slice_info.config` and `print_profile.config` from
  the source's last slice session are removed.

## Web interface

```console
python -m x3mf web --demo          # http://127.0.0.1:8756
```

Drop a source project, say where it will print, press translate, download the result.
The same engine runs underneath, so the report in the browser is the report the CLI
prints.

Step 2 is a choice, not jargon:

* **Another printer** — you need one file: a `.3mf` saved from the slicer you are
  moving *to*, with your printer, nozzle and filament selected (a cube is enough).
  Nothing is printed from it; it carries the machine. The page has a "How do I get
  that file?" panel with the four steps and the reason a dropdown cannot replace it:
  the destination keeps its printer list inside its own installation, which this
  process cannot read — the project it saves *is* that list.
* **Same printer** — only the dialect changes, so the machine settings already in
  the project are kept. The page says plainly that this is only correct for the same
  physical printer, and the translate button stays disabled otherwise.

In CLI terms the printer file is the `--kit`.

The result panel answers the only question that matters first: one verdict sentence
("Carried, with 2 items dropped"), then what it is for, then the audit trail. Dropped
items and warnings are shown open; the carried lists stay folded until asked for.
Structural defects in the source are surfaced where they are found — a red
"5 errors · show" chip expands to the validator findings — and each file can be
removed from its dropzone without reloading the page.

The download is named after where it is going, not where it came from:
`model - Snapmaker U1 (Snapmaker Orca).3mf`. The source stem stays first so files
sort together; the printer model and the destination app follow, and illegal
characters are stripped. With no printer file (same-printer mode) the project's own
printer is used, so the name still says which machine the file targets.

`--demo` adds a one-click sample (the Bambu-dialect fixture and an Ender-3 printer
file); drop it when deploying. Options in the panel map to the CLI flags: target dialect, filament
slots, extruders, dropping layer metadata, keeping unknown keys, and forcing a write.

The HTTP surface is small enough to script against:

| Route | Method | Body |
|---|---|---|
| `/api/inspect` | POST | multipart: `source`, or `source_example` |
| `/api/translate` | POST | multipart: `source`/`source_example`, `kit`/`kit_example`, `options` (JSON) |
| `/api/download/<id>` | GET | — |
| `/api/examples` | GET | — (demo mode only) |

A refused translation answers `422` with `status: "refused"`, the validator's findings
and no download id; a bad input answers `400`; the file is never written on either.

Bounds, because this is a local tool that may be put behind a proxy: 256 MB per upload
(`--max-upload-mb`), two concurrent translations, results expire after 15 minutes, and
uploads are staged in a scratch directory under names the process generates. It binds
`127.0.0.1`; `--host 0.0.0.0` exposes it, and there is no authentication — put it behind
something that has some. On Windows a port inside a reserved range fails with
`WinError 10013`; pick another.

## Docker

Pure standard-library Python, so there is no dependency install and no build
stage: the image is an interpreter plus the source, 75 MB, running as uid 10001.

```bash
docker compose up --build        # http://127.0.0.1:8791/
```

The compose file is the hardened default: read-only root filesystem, all Linux
capabilities dropped, `no-new-privileges`, a 1 GB memory cap, and exactly two
writable paths — the `x3mf-data` volume and a 64 MB `tmpfs` at `/tmp`.

Standalone, with the port and volume of your choosing:

```bash
docker run --rm -p 8791:8791 -v x3mf-data:/data x3mf:0.1.0
```

The image also carries the CLI, because the entry point is the package:

```bash
# translate a project that lives on the host
docker run --rm -v "$PWD:/work" x3mf:0.1.0 \
    translate /work/source.3mf --kit /work/destination.3mf --out /work/out.3mf
```

On Linux, add `--user "$(id -u):$(id -g)"` so the written file belongs to you
rather than to uid 10001.

`--demo` is on by default in the container, so the page can load a bundled
project and an Ender-3 kit without any upload. Turn it off for a shared
deployment:

```bash
docker run --rm -p 8791:8791 -v x3mf-data:/data x3mf:0.1.0 \
    web --host 0.0.0.0 --work-dir /data
```

## What crosses, and what cannot

Two interoperability facts worth stating, because they decide whether a translated
project prints correctly:

- **G-code templates are machine settings.** `layer_change_gcode`,
  `change_filament_gcode`, `printing_by_object_gcode` and friends are written
  against one firmware and one motion system, so they come from the destination kit
  and are never carried from the source. Only `filament_*_gcode` stays with the
  filament profile.

Settings that are the *same thing under a different name or spelling* are handled
by [`x3mf/mapping.py`](x3mf/mapping.py): key aliases (including one key fanning
out to several), the organic-support and shell-thickness grammars, and a
**transfer gate** that keeps the destination's own value when the source value is
not one the destination accepts.

- **Per-face paint is a slot bitfield inside the mesh.** Models with painted
  colours or supports store filament indices in the `.model` bytes
  (`paint_color`, `mmu_segmentation`). When the slot count changes, those indices
  are *not* remapped: the translator says so in the report and the verdict, rather
  than claiming the colours are intact.

## Use

```console
python -m x3mf inspect  project.3mf
python -m x3mf translate project.3mf --kit destination.3mf --out project_for_dest.3mf
python -m x3mf validate project_for_dest.3mf --kit destination.3mf
```

`inspect` is the diagnostic: it prints the dialect markers, the machine, plates,
parts, filaments, auxiliary metadata and every invariant violation.

### Making a kit

Save any small project from the destination slicer with *your* printer, process
and filaments selected — a cube is enough. That project is a complete, resolved
dump of the destination's schema (an Orca 2.5 project carries ~780 keys), which
is exactly what the translator needs:

```console
python -m x3mf translate makerworld.3mf --kit my_flashforge.3mf --out fixed.3mf
```

Preset JSONs work too, when a project is not available:

```console
python -m x3mf translate src.3mf --preset "%APPDATA%\OrcaSlicer\user\default" \
                               --base my_kit.3mf --to orca --out out.3mf
```

Preset files are *sparse* (they hold only what you changed plus an `inherits`
pointer), so `x3mf` resolves inheritance across the supplied files and reports
anything still dangling instead of inventing values.

### Options that matter

| Flag | Effect |
|---|---|
| `--kit PATH` | destination project; its key set is the target schema and its machine block is the geometry |
| `--to FLAVOR` | `bambu`, `orca`, `flashforge`, `snapmaker`, `generic` (affects the stamp fallback and validation, not geometry) |
| `--filaments N` | force the slot count; assignments and colour changes are remapped to the surviving slots |
| `--keep-unknown` | carry source keys the kit does not declare |
| `--drop-aux` | drop layer-range, colour-change and sequencing metadata entirely |
| `--force` | write even when validation fails (never the default) |

## Refusing to write

`translate` validates its own output against the invariants below and exits `2`
**without writing a file** if any error remains. A translated project that would
not open is not a deliverable; a report explaining why is.

Rules are quoted in reports as `V0xx`:

| Code | Invariant |
|---|---|
| `V001`–`V006` | OPC package complete: content types, relationships, part-relative names |
| `V010`–`V015` | object ids unique; every component resolves through a relationship; build items exist |
| `V020`–`V031` | `model_settings` consistent: object/part ids, matrices, plate ids, instance indices, `filament_maps` length, thumbnails, `assemble` |
| `V040`–`V049` | settings complete: required keys, one nozzle per extruder, one value per filament slot, colour syntax, `filament_map` range, and **machine keys equal to the kit** |
| `V050`–`V052` | auxiliary metadata references live objects and valid filament indices |
| `V060`–`V061` | no stale slice session or contradictory vendor marker |

## Verified

Against a real project on this machine (`xteink_x4_retrocase_ender_PLAplus.3mf`,
11 objects across 12 part files, 655 resolved settings) and OrcaSlicer 2.5.0:

```
$ python -m x3mf inspect <project>          → 0 errors, 1 warning (stale slice_info)
$ python -m x3mf translate <project> --kit <same> --out roundtrip.3mf
                                             → output validation: no issues
$ orca-slicer.exe --info roundtrip.3mf      → 11 objects reported, manifold = yes
$ orca-slicer.exe --export-3mf out.3mf roundtrip.3mf
                                             → 745,995-byte project re-exported
```

That is the real reader loading the translated project — not just our own
validator — and accepting it well enough to re-save it through its own writer.

45 unit tests cover the carry-overs (`test_translate.py`), the defect classes
(`test_validate.py`), the packaging layer (`test_container.py`) and the HTTP surface
(`test_web.py`), on generated fixtures that contain plates, multi-part objects,
modifier and support-blocker volumes, four filaments, colour changes, height ranges
and deliberately broken references.

```console
python -m unittest discover -s tests
```

## Not verified, and known

* **The multi-plate/multi-filament fixture does not load in OrcaSlicer.** A
  generated fixture with two plates and four filaments is rejected by
  `--info` with `Invalid value provided for parameter layer_height`, while the
  same fixture's model alone loads fine and the real project path (above) loads
  and re-exports. The defect is in the generated `model_settings`/auxiliary
  fixture, not localised yet. Treat multi-plate translation as *structurally*
  tested but not app-verified, and the validator as weaker than a real reader.
* **Forks are unverified.** FlashForge Flash Studio and Snapmaker's slicer are
  Orca-derived and read the same layout, but no build of either was available
  here, so their acceptance is not measured.
* **Machine keys are classified by prefix.** The classifier is explicit and
  auditable (`settings.classify`), and geometry keys are additionally forced
  from the kit and re-checked by `V049`; a printer key that is neither in
  `GEOMETRY_KEYS` nor prefix-matched would be carried from the source. Run with
  `--to` and a kit whose machine differs, then compare `inspect` output, if you
  are pointing at an unfamiliar machine.
* **`different_settings_to_system` order** (`process`, `filament`, `printer`) is
  inferred from one real project. The field only drives the UI's "modified"
  markers; values load from the same file either way.
* **`Application` / `BambuStudio:3mfVersion`** are adopted from the kit, because
  the kit is the one artefact the destination itself wrote. With no kit, the
  source's stamps are preserved rather than guessed.

## Layout

```
x3mf/
  container.py   OPC package: entries, content types, relationships
  model.py       3dmodel.model, part scan, model_settings, layer metadata
  settings.py    project_settings, key-domain classification, per-slot rules
  flavors.py     source detection, target kits (project or presets)
  translate.py   the pipeline and the report
  validate.py    output invariants (V0xx)
  summary.py     structured project description (CLI + web share it)
  cli.py         inspect / translate / validate / flavors
  web.py         local HTTP server + JSON API
  webui/         index.html, app.css, app.js, favicon.svg
tests/           fixtures + unit tests (engine and HTTP)
examples/        a foreign project, a kit, and their translation
```

## Licence

MIT — see [LICENSE](LICENSE).

