"""Canonical prediction schema + normalization/validation.

Every adapter returns notes that are normalized through
`normalize_predictions` before hitting the cache, so downstream analysis
never sees model-specific quirks.
"""
from __future__ import annotations

import math
from typing import List, Optional

import pandas as pd

PREDICTION_COLUMNS = [
    "pitch", "onset", "offset", "velocity", "confidence", "instrument",
    "source_index",
]

# Sanity bounds — outside these, predictions are considered invalid timing.
MAX_ONSET_S = 60 * 60          # 1 hour
MIN_PITCH, MAX_PITCH = 0, 127


class InvalidPredictionError(ValueError):
    pass


def normalize_predictions(notes: List[dict]) -> pd.DataFrame:
    """Normalize adapter output (list of dicts) into the canonical table.

    Sorted by (onset, pitch) for storage/analysis, but `source_index`
    preserves the adapter's original output order.  The validated greedy
    matcher is order-sensitive: matching MUST iterate predictions in
    source order to reproduce historical numbers (verified empirically —
    sorted-order matching shifts Bass recall 0.188 -> 0.146).
    """
    rows = []
    for i, n in enumerate(notes):
        rows.append({
            "source_index": i,
            "pitch": int(n["pitch"]),
            "onset": float(n["onset"]),
            "offset": float(n["offset"]),
            "velocity": int(n.get("velocity", 0) or 0),
            "confidence": float(n["confidence"]) if n.get("confidence") is not None else float("nan"),
            "instrument": str(n.get("instrument", "") or ""),
        })
    df = pd.DataFrame(rows, columns=PREDICTION_COLUMNS)
    if len(df):
        df = df.sort_values(["onset", "pitch"], kind="mergesort").reset_index(drop=True)
    return df


def validate_predictions(df: pd.DataFrame, audio_duration: Optional[float] = None) -> None:
    """Raise InvalidPredictionError on insane output (broken adapter/timing)."""
    if len(df) == 0:
        return  # empty is legal (silent stem / conservative model)
    if df["pitch"].min() < MIN_PITCH or df["pitch"].max() > MAX_PITCH:
        raise InvalidPredictionError("pitch outside MIDI range 0..127")
    if df["onset"].min() < 0:
        raise InvalidPredictionError("negative onset")
    if (df["offset"] < df["onset"]).any():
        raise InvalidPredictionError("offset before onset")
    limit = MAX_ONSET_S
    if audio_duration and not math.isnan(audio_duration):
        # 1.5x guard catches the historical chunked-timing stretch bug
        limit = min(limit, audio_duration * 1.5 + 10.0)
    if df["onset"].max() > limit:
        raise InvalidPredictionError(
            f"onset {df['onset'].max():.1f}s exceeds plausible limit {limit:.1f}s "
            "(timing-stretch bug?)")


def find_duplicates(df: pd.DataFrame) -> pd.DataFrame:
    """Exact duplicate (pitch, onset, offset) rows — smell of a broken adapter."""
    if len(df) == 0:
        return df
    return df[df.duplicated(subset=["pitch", "onset", "offset"], keep=False)]
