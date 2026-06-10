"""Tone-card instrumentation log.

Append-only JSONL on disk at ``backend/data/tone_log.jsonl`` capturing
the three signals the calibration refit needs:

* ``recommendation`` — what we suggested, what tier, the calibrated
  confidence, the raw distance ranking.
* ``applied`` — the user clicked Apply on the suggested chain. The
  positive label for the calibrator.
* ``ignored`` — the user dismissed the recommendation (closed the
  card, switched songs, applied a different chain). The negative
  label.

Why a flat JSONL file
---------------------

Calibration refits run quarterly per Plan §7. The data volume is small
(one recommendation per analyze call), so a JSONL file is operationally
simpler than a database table: ``jq`` / ``pandas.read_json(lines=True)``
both consume it directly. Each line is a self-contained event with
its own ``event_type`` so we can pivot freely at refit time.

Schema
------

Every event carries:

* ``ts`` — UTC ISO timestamp (second precision).
* ``event_type`` — ``"recommendation" | "applied" | "ignored"``.
* ``session_id`` — analysis history id when available, else
  ``"unattached"``. Joins the three event types per song.
* ``chain_id`` — the chain id this event is about.
* ``tier`` — only on ``recommendation``.
* ``distance`` — top-1 z-norm distance, only on ``recommendation``.
* ``confidence`` — calibrated confidence, only on ``recommendation``.
* ``rationale`` — human-readable, only on ``recommendation``. Useful
  when grepping the file by hand.
* ``source_url`` — when known. Helps de-dupe and join with stems.

Failure mode
------------

Every public function swallows exceptions and logs a warning. A broken
log must never block a recommendation from reaching the UI.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

from tone_forge.contracts import ToneRecommendation

logger = logging.getLogger(__name__)

# ``backend/data/tone_log.jsonl`` — sibling of the existing
# ``history.json``, on the same persistence root.
_BACKEND_ROOT: Path = Path(__file__).resolve().parent.parent.parent
DEFAULT_LOG_PATH: Path = _BACKEND_ROOT / "data" / "tone_log.jsonl"

EVENT_RECOMMENDATION: str = "recommendation"
EVENT_APPLIED: str = "applied"
EVENT_IGNORED: str = "ignored"


def _utc_now_iso() -> str:
    return _dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _resolve_log_path() -> Path:
    """Resolve the on-disk path, honouring a ``TONE_LOG_PATH`` env var.

    The env var lets tests redirect to a tmp file without monkey-patching
    the module.
    """
    override = os.environ.get("TONE_LOG_PATH")
    if override:
        return Path(override)
    return DEFAULT_LOG_PATH


def _append(event: Dict[str, Any]) -> None:
    """Append one JSON object as a single line. Never raises."""
    path = _resolve_log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # JSON object on one line. ``ensure_ascii`` is fine — chain
        # ids and rationale are ASCII; if a future rationale contains
        # unicode we'd want the encoded form anyway for grep.
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=True) + "\n")
    except Exception as exc:  # pragma: no cover — telemetry must not raise
        logger.warning("tone_log: failed to write to %s: %s", path, exc)


# ---------------------------------------------------------------------------
# Public emitters
# ---------------------------------------------------------------------------


def log_recommendation(
    rec: ToneRecommendation,
    *,
    session_id: Optional[str] = None,
    source_url: Optional[str] = None,
) -> None:
    """Persist a recommendation event for calibration refit.

    The chain id logged is the resolved Apply target (``rec.apply.chain_id``)
    — that's the id the user is actually being offered, regardless of
    whether the tier picked a match or fallback. Distance/confidence
    come from ``rec.match`` when present; on fallback paths both are
    ``None``.
    """
    try:
        event: Dict[str, Any] = {
            "ts": _utc_now_iso(),
            "event_type": EVENT_RECOMMENDATION,
            "session_id": session_id or "unattached",
            "chain_id": rec.apply.chain_id,
            "tier": rec.tier.value,
            "rationale": rec.rationale,
            "source_url": source_url,
        }
        if rec.match is not None:
            event["distance"] = rec.match.distance
            event["confidence"] = rec.match.confidence
            event["match_chain_id"] = rec.match.chain_id
        else:
            event["distance"] = None
            event["confidence"] = None
            event["match_chain_id"] = None
        if rec.fallback is not None:
            event["fallback_reason"] = rec.fallback.reason
        else:
            event["fallback_reason"] = None
        _append(event)
    except Exception as exc:  # pragma: no cover — telemetry must not raise
        logger.warning("tone_log: failed to format recommendation: %s", exc)


def log_applied(
    chain_id: str,
    *,
    session_id: Optional[str] = None,
    source_url: Optional[str] = None,
) -> None:
    """Persist a user-applied event (positive label).

    Emitted from the Connect WS bridge when an ``apply_chain`` frame
    arrives — see ``local_engine.connect_bridge``.
    """
    _append({
        "ts": _utc_now_iso(),
        "event_type": EVENT_APPLIED,
        "session_id": session_id or "unattached",
        "chain_id": chain_id,
        "source_url": source_url,
    })


def log_ignored(
    chain_id: str,
    *,
    session_id: Optional[str] = None,
    source_url: Optional[str] = None,
    reason: Optional[str] = None,
) -> None:
    """Persist a user-ignored event (negative label).

    ``reason`` is a short string identifying the UX path that produced
    the dismissal (``"card_closed"``, ``"song_switched"``,
    ``"other_chain_applied"``).
    """
    _append({
        "ts": _utc_now_iso(),
        "event_type": EVENT_IGNORED,
        "session_id": session_id or "unattached",
        "chain_id": chain_id,
        "source_url": source_url,
        "reason": reason,
    })


__all__ = [
    "DEFAULT_LOG_PATH",
    "EVENT_APPLIED",
    "EVENT_IGNORED",
    "EVENT_RECOMMENDATION",
    "log_applied",
    "log_ignored",
    "log_recommendation",
]
