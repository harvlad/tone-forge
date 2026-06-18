"""Record one user correction as an evidence row.

The HTTP endpoint validates a JSON payload and hands a structured
``CorrectionPayload`` here. We:

    1. Sanity-check the correction type against a small allowlist
       (engine fields the consensus pipeline already reasons about).
    2. Build a ``Correction`` sub-record carrying the previous /
       corrected values + the user's free-form note.
    3. Append an ``EvidenceRecord`` whose ``corrections`` tuple has
       just this one correction, ``jam_output`` empty, no
       ``consensus_output``.

Why a fresh record per correction (rather than mutating the latest
one for the section)? The evidence store is append-only. Mutating
records would break crash safety, replay determinism, and the
Phase 9 ML pipeline's assumption that the JSONL stream is
monotonically time-ordered.

Latest-wins reads (``EvidenceStore.latest_for_section``) already
handle the "give me the current state" case; longitudinal queries
(did the user correct a section multiple times?) need every row
preserved.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from bench.evidence.schema import Correction, EvidenceRecord
from bench.evidence.store import EvidenceStore


__all__ = [
    "CorrectionPayload",
    "CorrectionRecordingError",
    "SUPPORTED_CORRECTION_TYPES",
    "record_correction",
]


# Fields the engine emits that a user can correct. Adding here
# automatically widens the endpoint's allowlist; the consensus
# builder (Phase 3) keys off ``scored_keys`` independently, so a
# correction type can land in evidence without forcing consensus
# changes.
SUPPORTED_CORRECTION_TYPES: tuple[str, ...] = (
    "guidance_mode",
    "chord",
    "chord_sequence",
    "key",
    "tempo_bpm",
    "section_boundary",
)


class CorrectionRecordingError(ValueError):
    """Raised when a correction payload fails validation."""


@dataclass(frozen=True)
class CorrectionPayload:
    """Structured input for ``record_correction``.

    The HTTP endpoint constructs this from its Pydantic request
    model so the domain layer never touches FastAPI types.
    """

    song_id: str
    section_id: str
    correction_type: str
    previous_value: Any
    corrected_value: Any
    user_id: Optional[str] = None
    note: Optional[str] = None
    # If the client knows the exact ISO-8601 timestamp it should
    # pass it through; otherwise the server stamps wallclock UTC.
    timestamp_utc: Optional[str] = None


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _validate(payload: CorrectionPayload) -> None:
    if not payload.song_id:
        raise CorrectionRecordingError("song_id is required")
    if not payload.section_id:
        raise CorrectionRecordingError("section_id is required")
    if payload.correction_type not in SUPPORTED_CORRECTION_TYPES:
        raise CorrectionRecordingError(
            f"correction_type {payload.correction_type!r} is not in the "
            f"allowlist {SUPPORTED_CORRECTION_TYPES}"
        )
    # corrected_value must be present in some meaningful sense.
    # ``None`` is allowed as a "this field has no value here" assertion
    # but only when previous_value was non-None (otherwise the
    # correction conveys no information).
    if payload.corrected_value is None and payload.previous_value is None:
        raise CorrectionRecordingError(
            "either previous_value or corrected_value must be non-null"
        )


def record_correction(
    payload: CorrectionPayload,
    *,
    store: Optional[EvidenceStore] = None,
    store_root: Optional[Path] = None,
) -> EvidenceRecord:
    """Append one correction row to the evidence store.

    If ``store`` is provided it wins. Otherwise an ``EvidenceStore``
    is constructed with ``store_root`` (which defaults to ``None``,
    causing the store to use its own default root under
    ``backend/data/evidence/``). Returns the record that was
    appended so the HTTP endpoint can echo the timestamp / record
    id back to the client.
    """
    _validate(payload)
    if store is None:
        store = EvidenceStore(root=store_root)

    timestamp_utc = payload.timestamp_utc or _now_utc_iso()
    correction = Correction(
        correction_type=payload.correction_type,
        previous_value=payload.previous_value,
        corrected_value=payload.corrected_value,
        user_id=payload.user_id,
        note=payload.note,
    )
    record = EvidenceRecord(
        song_id=payload.song_id,
        section_id=payload.section_id,
        timestamp_utc=timestamp_utc,
        corrections=(correction,),
    )
    store.append(record)
    return record
