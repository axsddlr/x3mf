"""x3mf — translate slicer 3MF *projects*, not just their meshes."""

from .container import Package, PackageError
from .flavors import FLAVORS, TargetKit, detect_source
from .settings import ProjectSettings
from .translate import Options, Report, Translation, TranslationError, Translator
from .validate import Issue, summarize, validate

__all__ = [
    "FLAVORS",
    "Issue",
    "Options",
    "Package",
    "PackageError",
    "ProjectSettings",
    "Report",
    "TargetKit",
    "Translation",
    "TranslationError",
    "Translator",
    "detect_source",
    "summarize",
    "validate",
]

__version__ = "0.1.0"
