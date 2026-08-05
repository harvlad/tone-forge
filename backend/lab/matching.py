"""Note matching + metrics + match cache.

The matcher is a VERBATIM port of the validated benchmark's greedy
matcher (mt3_vs_basic_pitch.py `match_notes`): 100 ms default onset
tolerance, exact pitch = correct, same-pitch-class = separate octave
bucket (octave matches are NOT counted in precision/recall).

Match results are cached keyed on (prediction identity, GT identity,
matcher version, eval params).  Changing tolerance recomputes matches
locally — it can never trigger inference.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

import pandas as pd

from . import config
from .hashing import config_hash, short

# v2: predictions are matched in adapter source order (source_index), the
# order the validated benchmark used.  The greedy matcher is order-sensitive.
MATCHER_VERSION = "greedy_exact_srcorder_v2"

MATCH_COLUMNS = [
    "kind",  # correct | octave | missed | extra
    "gt_pitch", "gt_onset", "gt_offset",
    "pred_pitch", "pred_onset", "pred_offset", "pred_velocity", "pred_confidence",
    "octave_delta",
]


def match_notes(gt_notes: List[dict], pred_notes: List[dict],
                onset_tolerance: float = 0.1) -> Tuple[list, list, list, list]:
    """Verbatim port of the validated matcher."""
    correct, octave, missed, extra = [], [], [], []
    pred_used = set()
    for gt in gt_notes:
        gt_pitch = gt["pitch"]
        gt_onset = gt["onset"]
        matched = False
        for pi, pred in enumerate(pred_notes):
            if pi in pred_used:
                continue
            if abs(pred["onset"] - gt_onset) > onset_tolerance:
                continue
            if pred["pitch"] == gt_pitch:
                correct.append((gt, pred))
                pred_used.add(pi)
                matched = True
                break
            elif (pred["pitch"] - gt_pitch) % 12 == 0:
                octave.append((gt, pred, pred["pitch"] - gt_pitch))
                pred_used.add(pi)
                matched = True
                break
        if not matched:
            missed.append(gt)
    for pi, pred in enumerate(pred_notes):
        if pi not in pred_used:
            extra.append(pred)
    return correct, octave, missed, extra


def match_table(gt_df: pd.DataFrame, pred_df: pd.DataFrame,
                onset_tolerance: float = 0.1) -> pd.DataFrame:
    """Run the matcher and return a flat per-note match table."""
    gt_notes = gt_df.to_dict("records")
    if "source_index" in pred_df.columns and len(pred_df):
        pred_df = pred_df.sort_values("source_index", kind="mergesort")
    pred_notes = pred_df.to_dict("records")
    correct, octave, missed, extra = match_notes(gt_notes, pred_notes, onset_tolerance)
    rows = []
    for gt, pred in correct:
        rows.append(_row("correct", gt, pred, 0))
    for gt, pred, delta in octave:
        rows.append(_row("octave", gt, pred, delta))
    for gt in missed:
        rows.append(_row("missed", gt, None, None))
    for pred in extra:
        rows.append(_row("extra", None, pred, None))
    return pd.DataFrame(rows, columns=MATCH_COLUMNS)


def _row(kind, gt, pred, delta):
    return {
        "kind": kind,
        "gt_pitch": gt["pitch"] if gt else None,
        "gt_onset": gt["onset"] if gt else None,
        "gt_offset": gt["offset"] if gt else None,
        "pred_pitch": pred["pitch"] if pred else None,
        "pred_onset": pred["onset"] if pred else None,
        "pred_offset": pred["offset"] if pred else None,
        "pred_velocity": pred.get("velocity") if pred else None,
        "pred_confidence": pred.get("confidence") if pred else None,
        "octave_delta": delta,
    }


def metrics_from_counts(n_gt: int, n_pred: int, n_correct: int, n_octave: int,
                        octave_up: int = 0, octave_down: int = 0) -> dict:
    """Validated metric formulas (octave bucket excluded from P/R/F1)."""
    precision = n_correct / n_pred if n_pred > 0 else 0.0
    recall = n_correct / n_gt if n_gt > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    return {
        "n_gt": n_gt, "n_pred": n_pred, "n_correct": n_correct,
        "n_octave": n_octave, "octave_up": octave_up, "octave_down": octave_down,
        "precision": precision, "recall": recall, "f1": f1,
        "octave_error_rate": n_octave / n_gt if n_gt > 0 else 0.0,
    }


def metrics_from_match_table(mt: pd.DataFrame) -> dict:
    n_correct = int((mt["kind"] == "correct").sum())
    n_octave = int((mt["kind"] == "octave").sum())
    n_missed = int((mt["kind"] == "missed").sum())
    n_extra = int((mt["kind"] == "extra").sum())
    n_gt = n_correct + n_octave + n_missed
    n_pred = n_correct + n_octave + n_extra
    oct_rows = mt[mt["kind"] == "octave"]["octave_delta"]
    return metrics_from_counts(
        n_gt, n_pred, n_correct, n_octave,
        octave_up=int((oct_rows > 0).sum()), octave_down=int((oct_rows < 0).sum()))


# ---------------------------------------------------------------------------
# Match cache
# ---------------------------------------------------------------------------

def match_key(prediction_key: str, gt_identity: str,
              onset_tolerance: float, matcher_version: str = MATCHER_VERSION) -> str:
    return short(config_hash({
        "prediction_key": prediction_key,
        "gt_identity": gt_identity,
        "matcher_version": matcher_version,
        "onset_tolerance": onset_tolerance,
    }), 24)


def _match_path(key: str):
    return config.MATCHES_DIR / f"{key}.parquet"


def get_match_table(prediction_key: str, gt_identity: str,
                    gt_df: pd.DataFrame, pred_df: pd.DataFrame,
                    onset_tolerance: float = 0.1) -> pd.DataFrame:
    """Cached match table for (predictions, GT, eval params)."""
    key = match_key(prediction_key, gt_identity, onset_tolerance)
    path = _match_path(key)
    if path.exists():
        try:
            return pd.read_parquet(path)
        except Exception:
            path.unlink()
    mt = match_table(gt_df, pred_df, onset_tolerance)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".parquet.tmp")
    mt.to_parquet(tmp, index=False)
    tmp.rename(path)
    return mt
