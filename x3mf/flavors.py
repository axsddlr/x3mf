"""Slicer dialects: what a project says it is, and what a target expects.

There is no published cross-slicer project spec.  What exists is a family of
formats that share a lineage: Bambu Studio and OrcaSlicer (and the Orca-derived
forks such as FlashForge Flash Studio and Snapmaker's slicer) all write the same
``model_settings.config`` / ``project_settings.config`` pair, and they
*disagree only at the edges*.  Two consequences shape this module:

1. Detection must be evidence-based, not name-based: a project carries several
   independent markers (``Application`` metadata, ``from``/``version`` in the
   settings, ``X-BBL-Client-*`` slice headers, the Bambu 3MF version metadata)
   that can contradict each other, because a file can be opened by one slicer
   and saved by another.  We report every marker and never claim certainty we
   do not have.
2. A target is defined by a **kit** the user supplies — a small project saved
   from the destination slicer, or its preset JSONs — not by a table of values
   we invented.  The kit's key set is the target's schema, and its machine
   block is the only authority for printer geometry.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .model import Model3D
from .settings import ProjectSettings, classify, is_geometry


@dataclass(frozen=True)
class Flavor:
    key: str
    label: str
    application: str
    keeps_bambu_marker: bool
    orca_family: bool = True

    def app_string(self, version: str) -> str:
        return f"{self.application}-{version}" if version else self.application


FLAVORS: dict[str, Flavor] = {
    "bambu": Flavor("bambu", "Bambu Studio", "BambuStudio", True),
    "orca": Flavor("orca", "OrcaSlicer", "OrcaSlicer", False),
    "flashforge": Flavor(
        "flashforge",
        "FlashForge Flash Studio (Orca-based)",
        "OrcaSlicer",
        False,
    ),
    "snapmaker": Flavor(
        "snapmaker",
        "Snapmaker slicer (Orca-based)",
        "OrcaSlicer",
        False,
    ),
    "generic": Flavor("generic", "generic Orca-lineage project", "OrcaSlicer", False),
}

TARGET_KEYS = tuple(FLAVORS)


@dataclass
class SourceProfile:
    """What the source project claims about itself, with the evidence."""

    flavor: Flavor
    application: str
    version: str
    from_value: str
    printer_model: str
    evidence: list[str] = field(default_factory=list)
    contradictions: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return self.flavor.key


def _match_flavor(*texts: str) -> Flavor | None:
    joined = " ".join(t for t in texts if t).lower()
    if "flash" in joined:
        return FLAVORS["flashforge"]
    if "snapmaker" in joined or "snaporca" in joined or "snap orca" in joined:
        return FLAVORS["snapmaker"]
    if "bambu" in joined:
        return FLAVORS["bambu"]
    if "orca" in joined:
        return FLAVORS["orca"]
    return None


def detect_source(package, settings: ProjectSettings) -> SourceProfile:
    """Collect every dialect marker in the package and resolve it.

    A single file can carry markers from several writers, because slicers
    rewrite each other's projects.  The *last* writer wins for retargeting
    purposes, but every disagreement is reported: a project that says
    ``Application=BambuStudio`` and also carries ``OrcaSlicer-Version`` is not
    a Bambu project, it is a Bambu project that Orca re-saved.
    """
    from .model import slice_info_header

    try:
        model = Model3D.from_package(package)
        application = model.metadata("Application")
        bambu_marker = bool(model.metadata("BambuStudio:3mfVersion"))
    except Exception:  # a broken model must not stop diagnosis
        application = ""
        bambu_marker = False
    header = slice_info_header(package)

    from_value = settings.get_str("from")
    version = settings.get_str("version")
    printer_model = settings.get_str("printer_model")

    evidence: list[str] = []
    if application:
        evidence.append(f"Application={application}")
    if from_value:
        evidence.append(f"settings.from={from_value}")
    if bambu_marker:
        evidence.append("model metadata BambuStudio:3mfVersion present")
    if header:
        evidence.append(
            "slice_info header "
            + ", ".join(f"{k}={v}" for k, v in sorted(header.items()))
        )
    if printer_model:
        evidence.append(f"printer_model={printer_model}")

    candidates: list[tuple[str, Flavor]] = []
    for label, text in (("Application", application), ("settings.from", from_value)):
        flavor = _match_flavor(text)
        if flavor is not None:
            candidates.append((label, flavor))
    if bambu_marker:
        candidates.append(("BambuStudio:3mfVersion", FLAVORS["bambu"]))
    if header.get("OrcaSlicer-Version"):
        candidates.append(("slice_info OrcaSlicer-Version", FLAVORS["orca"]))
    elif header.get("X-BBL-Client-Type"):
        candidates.append(("slice_info X-BBL-Client-Type", FLAVORS["bambu"]))

    contradictions = []
    keys = {c[1].key for c in candidates}
    if len(keys) > 1:
        contradictions.append(
            "writers disagree (" + ", ".join(sorted(keys)) + "); the project was re-saved "
            "by a second slicer, so only format-level facts are safe to assume"
        )
    if candidates:
        flavor = candidates[-1][1]
    else:
        flavor = FLAVORS["generic"]
        evidence.append("no vendor marker recognised")

    return SourceProfile(
        flavor=flavor,
        application=application,
        version=version,
        from_value=from_value,
        printer_model=printer_model,
        evidence=evidence,
        contradictions=contradictions,
    )


# ---------------------------------------------------------------------------
# target kit
# ---------------------------------------------------------------------------

PRESET_KIND_KEYS = {
    "machine": ("printer_settings_id", "printer_model", "nozzle_diameter", "bed_shape"),
    "process": ("print_settings_id", "layer_height", "wall_loops"),
    "filament": ("filament_settings_id", "filament_type"),
}


@dataclass
class TargetKit:
    """The destination's own presets, already resolved as far as possible."""

    settings: ProjectSettings
    flavor: Flavor
    source_label: str
    machine_id: str = ""
    process_id: str = ""
    application: str = ""
    has_bambu_marker: bool | None = None
    filament_ids: list[str] = field(default_factory=list)
    unresolved_inherits: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def extruders(self) -> int:
        return self.settings.extruder_count()

    @property
    def schema_keys(self) -> set[str]:
        return set(self.settings.keys())

    def has_machine_geometry(self) -> bool:
        return bool(
            self.settings.get("bed_shape") or self.settings.get("printable_area")
        ) and bool(self.settings.get("nozzle_diameter"))

    def describe(self) -> str:
        return (
            f"{self.source_label}: printer={self.machine_id or '?'} "
            f"process={self.process_id or '?'} "
            f"filaments={len(self.filament_ids) or '?'} "
            f"extruders={self.extruders}"
        )

    # -- constructors -------------------------------------------------------
    @classmethod
    def from_project(cls, path: str | Path) -> "TargetKit":
        from .container import Package

        package = Package.read(path)
        settings = ProjectSettings.from_package(package)
        if not settings.keys():
            raise ValueError(f"{path} has no project settings part")
        profile = detect_source(package, settings)
        try:
            model = Model3D.from_package(package)
            application = model.metadata("Application")
            has_marker = bool(model.metadata("BambuStudio:3mfVersion"))
        except Exception:
            application, has_marker = "", None
        kit = cls(
            settings=settings,
            flavor=profile.flavor,
            source_label=f"project {Path(path).name}",
            machine_id=settings.get_str("printer_settings_id"),
            process_id=settings.get_str("print_settings_id"),
            filament_ids=settings.get_list("filament_settings_id"),
            application=application,
            has_bambu_marker=has_marker,
        )
        if not kit.has_machine_geometry():
            kit.warnings.append(
                "kit project does not resolve machine geometry "
                "(bed_shape/nozzle_diameter missing)"
            )
        return kit

    @classmethod
    def from_presets(
        cls,
        paths: list[str | Path],
        base: "TargetKit | None" = None,
        flavor: str = "orca",
    ) -> "TargetKit":
        """Merge preset JSONs into one settings dump.

        Preset files written by the slicer are *sparse*: they hold only what the
        user changed plus an ``inherits`` pointer.  Inheritance is resolved
        across the supplied files, then over ``base``; anything still dangling is
        reported rather than guessed.
        """
        documents: dict[str, dict] = {}
        for path in paths:
            path = Path(path)
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict):
                raise ValueError(f"{path} is not a preset object")
            name = data.get("name") or path.stem
            documents[str(name)] = data

        merged: dict = dict(base.settings.data) if base else {}
        order = _inheritance_order(documents)
        unresolved: list[str] = []
        for name in order:
            doc = documents[name]
            parent = doc.get("inherits")
            if parent and parent not in documents and not base:
                unresolved.append(str(parent))
            for key, value in doc.items():
                if key in ("inherits", "name", "from", "version", "sync_info"):
                    continue
                merged[key] = value

        settings = ProjectSettings(merged)
        kit = cls(
            settings=settings,
            flavor=FLAVORS.get(flavor, FLAVORS["generic"]),
            source_label=f"{len(documents)} preset file(s)",
            machine_id=settings.get_str("printer_settings_id") or _first_name(documents, "machine"),
            process_id=settings.get_str("print_settings_id") or _first_name(documents, "process"),
            filament_ids=settings.get_list("filament_settings_id"),
            unresolved_inherits=sorted(set(unresolved)),
        )
        if unresolved:
            kit.warnings.append(
                "preset(s) inherit from profiles not supplied: "
                + ", ".join(sorted(set(unresolved)))
                + " — the target's own defaults will apply to those keys"
            )
        if not kit.has_machine_geometry():
            kit.warnings.append(
                "preset bundle does not resolve machine geometry; "
                "supply a kit project or the full machine preset"
            )
        return kit


def _inheritance_order(documents: dict[str, dict]) -> list[str]:
    """Order presets so that a parent is applied before its children."""
    done: list[str] = []
    visiting: set[str] = set()

    def visit(name: str) -> None:
        if name in done:
            return
        if name in visiting:
            return  # cycle: keep insertion order and let validation complain
        visiting.add(name)
        parent = documents[name].get("inherits")
        if parent in documents:
            visit(str(parent))
        visiting.discard(name)
        done.append(name)

    for name in documents:
        visit(name)
    return done


def _first_name(documents: dict[str, dict], kind: str) -> str:
    for name, doc in documents.items():
        if any(k in doc for k in PRESET_KIND_KEYS[kind]):
            return name
    return ""


def describe_domain_split(settings: ProjectSettings) -> dict[str, int]:
    counts = {"machine": 0, "process": 0, "filament": 0}
    for key in settings.keys():
        counts[classify(key)] += 1
    return counts


def geometry_snapshot(settings: ProjectSettings) -> dict[str, object]:
    return {k: settings.get(k) for k in sorted(settings.keys()) if is_geometry(k)}
