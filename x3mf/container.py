"""OPC (3MF) package container: read, mutate, write.

A 3MF file is a ZIP with OPC conventions: ``[Content_Types].xml``,
``_rels/.rels`` and relationship parts.  Slicer projects add ``Metadata/``.

This module deliberately owns *only* packaging concerns: entries, content
types and relationship parts.  It never interprets model or setting XML.
"""

from __future__ import annotations

import io
import posixpath
import zipfile
from pathlib import Path

# --- namespaces / relationship types ---------------------------------------
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
MODEL_REL = "http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"
THUMB_REL = "http://schemas.openxmlformats.org/package/2006/relationships/metadata/thumbnail"

CONTENT_TYPES_PATH = "[Content_Types].xml"
ROOT_RELS_PATH = "_rels/.rels"

#: Default content type per extension, for extensions we may need to declare.
#: Only *missing* extensions are added; existing declarations are left alone.
DEFAULT_CONTENT_TYPES = {
    "rels": "application/vnd.openxmlformats-package.relationships+xml",
    "model": "application/vnd.ms-package.3dmanufacturing-3dmodel+xml",
    "xml": "text/xml",
    "config": "text/xml",
    "json": "application/json",
    "txt": "text/plain",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "svg": "image/svg+xml",
    "gcode": "text/x.gcode",
    "bin": "application/octet-stream",
    "stl": "model/stl",
}

#: Fixed timestamp so translated files are byte-reproducible.
_ZIP_DATE = (1980, 1, 1, 0, 0, 0)


def _clean(name: str) -> str:
    name = name.replace("\\", "/").lstrip("/")
    return posixpath.normpath(name) if name else name


class PackageError(Exception):
    pass


class Package:
    """An ordered set of ZIP entries, i.e. one 3MF container."""

    def __init__(self, entries: dict[str, bytes] | None = None) -> None:
        self._entries: dict[str, bytes] = {}
        for name, data in (entries or {}).items():
            self._entries[_clean(name)] = data

    # -- construction -------------------------------------------------------
    @classmethod
    def read(cls, path: str | Path) -> "Package":
        path = Path(path)
        if not path.is_file():
            raise PackageError(f"no such file: {path}")
        out: dict[str, bytes] = {}
        try:
            with zipfile.ZipFile(path) as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    out[_clean(info.filename)] = zf.read(info)
        except zipfile.BadZipFile as exc:
            raise PackageError(f"{path} is not a 3MF/OPC package: {exc}") from exc
        if CONTENT_TYPES_PATH not in out:
            raise PackageError(f"{path} has no {CONTENT_TYPES_PATH}; not an OPC package")
        return cls(out)

    def copy(self) -> "Package":
        return Package(dict(self._entries))

    # -- entry access -------------------------------------------------------
    def names(self) -> list[str]:
        return list(self._entries)

    def __contains__(self, name: str) -> bool:
        return _clean(name) in self._entries

    def has(self, name: str) -> bool:
        return _clean(name) in self._entries

    def get(self, name: str) -> bytes | None:
        return self._entries.get(_clean(name))

    def require(self, name: str) -> bytes:
        data = self.get(name)
        if data is None:
            raise PackageError(f"missing required part: {_clean(name)}")
        return data

    def put(self, name: str, data: bytes) -> None:
        self._entries[_clean(name)] = data

    def put_text(self, name: str, text: str) -> None:
        self.put(name, text.encode("utf-8"))

    def remove(self, name: str) -> None:
        self._entries.pop(_clean(name), None)

    def remove_prefix(self, prefix: str) -> list[str]:
        removed = [n for n in self._entries if n.startswith(prefix)]
        for n in removed:
            del self._entries[n]
        return removed

    def entries_under(self, prefix: str) -> list[str]:
        return [n for n in self._entries if n.startswith(prefix)]

    # -- OPC structure ------------------------------------------------------
    def relationships(self) -> list[dict[str, str]]:
        """Parse ``_rels/.rels`` into dicts with Target/Id/Type (+ '' for text)."""
        raw = self.get(ROOT_RELS_PATH)
        if raw is None:
            return []
        return parse_relationships(raw.decode("utf-8"))

    def relationship_targets(self) -> set[str]:
        return {_clean(r["target"]) for r in self.relationships()}

    def primary_model_path(self) -> str:
        for rel in self.relationships():
            if rel["type"].rstrip("/") == MODEL_REL.rstrip("/"):
                return _clean(rel["target"])
        # Fall back to the conventional location, then any top-level .model.
        if self.has("3D/3dmodel.model"):
            return "3D/3dmodel.model"
        models = [n for n in self._entries if n.endswith(".model")]
        if len(models) == 1:
            return models[0]
        raise PackageError("no 3dmodel relationship and no unambiguous model part")

    def ensure_model_relationship(self, model_path: str | None = None) -> None:
        model_path = _clean(model_path or self.primary_model_path())
        rels = self.relationships()
        if any(r["type"].rstrip("/") == MODEL_REL.rstrip("/") for r in rels):
            return
        rels.insert(0, {"target": "/" + model_path, "id": "rel-1", "type": MODEL_REL})
        self.put_text(ROOT_RELS_PATH, render_relationships(rels))

    def ensure_part_relationships(self) -> list[str]:
        """Make ``3D/_rels/3dmodel.model.rels`` cover every ``3D/Objects/*.model``.

        A ``<component p:path="...">`` without a matching relationship is a
        documented hard failure in some readers, so this is a repair, not a
        cosmetic tidy-up.  Returns the part paths that were missing.
        """
        model_path = self.primary_model_path()
        rels_path = posixpath.join(posixpath.dirname(model_path), "_rels",
                                   posixpath.basename(model_path) + ".rels")
        parts = self.entries_under(posixpath.join(posixpath.dirname(model_path), "Objects"))
        parts = [p for p in parts if p.endswith(".model")]
        rels = parse_relationships(self.get(rels_path).decode("utf-8")) if self.has(rels_path) else []
        have = {_clean(r["target"]) for r in rels}
        missing = []
        next_id = len(rels) + 1
        for part in sorted(parts):
            if part in have:
                continue
            missing.append(part)
            rels.append({"target": "/" + part, "id": f"rel-{next_id}", "type": MODEL_REL})
            next_id += 1
        if missing:
            self.put_text(rels_path, render_relationships(rels))
        return missing

    def ensure_content_types(self) -> list[str]:
        """Declare a default content type for every extension used but undeclared."""
        raw = self.require(CONTENT_TYPES_PATH).decode("utf-8")
        present = _declared_extensions(raw)
        used = {posixpath.splitext(n)[1].lstrip(".").lower() for n in self._entries}
        missing = sorted(e for e in used - present - {""} if e in DEFAULT_CONTENT_TYPES)
        if missing:
            self.put_text(CONTENT_TYPES_PATH, _add_content_type_defaults(raw, missing))
        return missing

    # -- output -------------------------------------------------------------
    def to_bytes(self) -> bytes:
        """Serialise the package deterministically (fixed timestamps)."""
        order = [n for n in (CONTENT_TYPES_PATH, ROOT_RELS_PATH) if n in self._entries]
        order += [n for n in self._entries if n not in order]
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for name in order:
                info = zipfile.ZipInfo(name, date_time=_ZIP_DATE)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o600 << 16
                zf.writestr(info, self._entries[name])
        return buffer.getvalue()

    def write(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.to_bytes())
        return path


# --- relationship parsing / rendering --------------------------------------

def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_relationships(text: str) -> list[dict[str, str]]:
    """Tolerant rels parser (namespace-agnostic, order preserving)."""
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise PackageError(f"malformed relationships part: {exc}") from exc
    rels = []
    for el in root:
        if _localname(el.tag) != "Relationship":
            continue
        rels.append({
            "target": el.get("Target", ""),
            "id": el.get("Id", ""),
            "type": el.get("Type", ""),
        })
    return rels


def render_relationships(rels: list[dict[str, str]]) -> str:
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<Relationships xmlns="{REL_NS}">',
    ]
    for rel in rels:
        lines.append(
            f' <Relationship Target="{rel["target"]}" Id="{rel["id"]}"'
            f' Type="{rel["type"]}"/>'
        )
    lines.append("</Relationships>")
    return "\n".join(lines) + "\n"


def _declared_extensions(content_types: str) -> set[str]:
    import xml.etree.ElementTree as ET

    root = ET.fromstring(content_types)
    found = set()
    for el in root:
        if _localname(el.tag) == "Default":
            ext = (el.get("Extension") or "").lower()
            if ext:
                found.add(ext)
    return found


def _add_content_type_defaults(content_types: str, extensions: list[str]) -> str:
    lines = content_types.rstrip("\n").split("\n")
    for ext in extensions:
        lines.insert(
            len(lines) - 1,
            f' <Default Extension="{ext}" ContentType="{DEFAULT_CONTENT_TYPES[ext]}"/>',
        )
    return "\n".join(lines) + "\n"
