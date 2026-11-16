"""Test support: keep temporary files inside the workspace.

The harness sandbox allows writes under the session workspace but refuses to
delete directories, including under the system temp tree.  ``TemporaryDirectory``
therefore raises during cleanup *after* the assertions have passed, which would
turn a green suite red for the wrong reason.  ``scratch()`` hands out a fresh
directory under ``.tmp/`` and deliberately does not remove it; the suites are
small and ``.tmp/`` is ignored.
"""

from __future__ import annotations

import pathlib
import tempfile
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[1]
TMP_ROOT = ROOT / ".tmp"
TMP_ROOT.mkdir(exist_ok=True)
tempfile.tempdir = str(TMP_ROOT)


class Scratch:
    """Minimal stand-in for ``TemporaryDirectory`` that does not delete."""

    def __init__(self, prefix: str = "t") -> None:
        self.name = str(TMP_ROOT / f"{prefix}-{uuid.uuid4().hex[:10]}")
        pathlib.Path(self.name).mkdir(parents=True, exist_ok=True)

    def __enter__(self) -> "Scratch":
        return self

    def __fspath__(self) -> str:
        return self.name

    def __exit__(self, *exc_info) -> bool:
        return False

    def cleanup(self) -> None:
        return None


def scratch(prefix: str = "t") -> Scratch:
    return Scratch(prefix)
