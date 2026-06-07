"""Backward-compat shim.

The canonical home for section detection moved to
``tone_forge.analysis.sections`` as part of the subsystem boundary
freeze (see ``/EXECUTION_PLAN.md`` §11). This module re-exports the
public surface so existing callers keep working without edits.

New code should import from ``tone_forge.analysis.sections``.
"""
from tone_forge.analysis.sections import (  # noqa: F401
    ArrangementAnalysis,
    ArrangementSection,
    SectionDetector,
    SectionTransition,
    SectionType,
    detect_sections,
)
