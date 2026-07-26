"""Internal musician-feedback capture for real-song specialist testing.

Pairwise-preference records (BETTER/SAME/WORSE vs the current pipeline)
per docs/MUSICAL_PLAYABILITY_EVALUATION.md — no 1-10 ratings, no
composite scores. Research data, appended to a JSONL under data/.
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import List, Optional

VERDICTS = {"BETTER", "SAME", "WORSE"}
PROBLEM_TAGS = {
    "wrong_notes", "missing_notes", "wrong_octave", "timing", "bad_stem",
    "bleed", "wrong_chords", "wrong_section", "other",
}

FEEDBACK_PATH = Path(__file__).resolve().parents[2] / "data" / "feedback" / "specialist_feedback.jsonl"


def record(*, verdict: str, song_hash: str, target_family: str,
           engine: str, registry_version: str,
           part: Optional[str] = None, section: Optional[str] = None,
           time_range: Optional[str] = None, tags: Optional[List[str]] = None,
           note: str = "", history_id: str = "", config_hash: str = "") -> dict:
    verdict = verdict.upper()
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {sorted(VERDICTS)}")
    bad = set(tags or []) - PROBLEM_TAGS
    if bad:
        raise ValueError(f"unknown tags {sorted(bad)}; valid: {sorted(PROBLEM_TAGS)}")
    rec = {
        "verdict": verdict,
        "song_hash": song_hash,
        "target_family": target_family,
        "part": part,
        "engine": engine,
        "registry_version": registry_version,
        "config_hash": config_hash,
        "history_id": history_id,
        "section": section,
        "time_range": time_range,
        "tags": sorted(tags or []),
        "note": note[:500],
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    FEEDBACK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(FEEDBACK_PATH, "a") as f:
        f.write(json.dumps(rec) + "\n")
    return rec


def load_all() -> List[dict]:
    if not FEEDBACK_PATH.exists():
        return []
    out = []
    for line in FEEDBACK_PATH.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out
