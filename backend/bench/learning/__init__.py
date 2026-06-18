"""User correction capture (JAM Learning System V1 — Phase 7).

User corrections from the JAM UI land here as *evidence*: each
correction becomes an ``EvidenceRecord`` with a ``Correction`` row
in the ``corrections`` field. Per the directive: corrections are
never immediately applied to the detector. Engine changes follow
evidence accumulation through the consensus + sweep gate
pipeline (Phases 3 / 6), not individual user reports.

This module is the seam between the FastAPI endpoint
(``POST /api/learning/correct``) and the evidence store. Living
under ``bench.*`` honours the one-way ``bench → tone_forge``
import boundary (this module depends on ``bench.evidence``,
which is bench-only). Keeping it FastAPI-free means the domain
logic is unit-testable without spinning up the HTTP layer, and a
future CLI-driven correction import can reuse the same entry
points.
"""
from __future__ import annotations

from .corrections import (
    CorrectionPayload,
    CorrectionRecordingError,
    SUPPORTED_CORRECTION_TYPES,
    record_correction,
)


__all__ = [
    "CorrectionPayload",
    "CorrectionRecordingError",
    "SUPPORTED_CORRECTION_TYPES",
    "record_correction",
]
