"""Local analysis over cached predictions.  ZERO neural inference here.

Everything in this module consumes the prediction cache, GT cache and
match cache.  If predictions are missing it reports them missing — it
never runs a model.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Optional

import pandas as pd

from . import cache, corpus, gt, matching
from .adapters import get_adapter
from .runner import stem_prediction_key


def evaluate_model(model_id: str, stems: pd.DataFrame,
                   onset_tolerance: float = 0.1) -> dict:
    """Aggregate metrics (global + per class) for cached predictions.

    Returns {global, per_class, n_stems_evaluated, missing: [stem_id...]}.
    """
    adapter = get_adapter(model_id)
    ck = adapter.checkpoint_hash()
    cfg = adapter.inference_config()

    agg_g = defaultdict(int)
    agg_c: Dict[str, dict] = defaultdict(lambda: defaultdict(int))
    missing: List[str] = []
    evaluated = 0

    for _, row in stems.iterrows():
        key = cache.prediction_key(row["audio_hash"], adapter.model_id,
                                   adapter.model_version, ck,
                                   adapter.adapter_version, cfg)
        pred_df = cache.load(model_id, key)
        if pred_df is None:
            missing.append(row["stem_id"])
            continue
        gt_df = gt.get_gt_notes(corpus.resolve_midi(row), row["midi_hash"])
        if len(gt_df) == 0:
            continue  # validated benchmark skips empty-GT stems
        mt = matching.get_match_table(key, gt.gt_identity(row["midi_hash"]),
                                      gt_df, pred_df, onset_tolerance)
        m = matching.metrics_from_match_table(mt)
        evaluated += 1
        for k in ("n_gt", "n_pred", "n_correct", "n_octave", "octave_up", "octave_down"):
            agg_g[k] += m[k]
            agg_c[row["inst_class_gm"]][k] += m[k]

    result = {
        "global": matching.metrics_from_counts(
            agg_g["n_gt"], agg_g["n_pred"], agg_g["n_correct"], agg_g["n_octave"],
            agg_g["octave_up"], agg_g["octave_down"]),
        "per_class": {
            cls: matching.metrics_from_counts(
                c["n_gt"], c["n_pred"], c["n_correct"], c["n_octave"],
                c["octave_up"], c["octave_down"])
            for cls, c in sorted(agg_c.items())
        },
        "n_stems_evaluated": evaluated,
        "missing": missing,
        "onset_tolerance": onset_tolerance,
    }
    return result


def head_to_head(model_a: str, model_b: str, stems: pd.DataFrame,
                 onset_tolerance: float = 0.1) -> dict:
    """Per-note oracle between two models over cached predictions.
    Verbatim port of the validated head_to_head_analysis keying:
    a GT note counts for a model iff (pitch, round(onset, 2)) appears in
    that model's correct matches."""
    adapters = {m: get_adapter(m) for m in (model_a, model_b)}
    cks = {m: a.checkpoint_hash() for m, a in adapters.items()}
    cfgs = {m: a.inference_config() for m, a in adapters.items()}

    agg = defaultdict(int)
    per_class = defaultdict(lambda: defaultdict(int))
    missing: List[str] = []

    for _, row in stems.iterrows():
        keys = {}
        preds = {}
        for m in (model_a, model_b):
            a = adapters[m]
            keys[m] = cache.prediction_key(row["audio_hash"], a.model_id,
                                           a.model_version, cks[m],
                                           a.adapter_version, cfgs[m])
            preds[m] = cache.load(m, keys[m])
        if preds[model_a] is None or preds[model_b] is None:
            missing.append(row["stem_id"])
            continue
        gt_df = gt.get_gt_notes(corpus.resolve_midi(row), row["midi_hash"])
        if len(gt_df) == 0:
            continue
        hit_sets = {}
        for m in (model_a, model_b):
            mt = matching.get_match_table(keys[m], gt.gt_identity(row["midi_hash"]),
                                          gt_df, preds[m], onset_tolerance)
            c = mt[mt["kind"] == "correct"]
            hit_sets[m] = {(int(p), round(float(o), 2))
                           for p, o in zip(c["gt_pitch"], c["gt_onset"])}
        for n in gt_df.itertuples():
            key = (int(n.pitch), round(float(n.onset), 2))
            a_hit = key in hit_sets[model_a]
            b_hit = key in hit_sets[model_b]
            bucket = ("both_correct" if a_hit and b_hit else
                      "a_only" if a_hit else
                      "b_only" if b_hit else "both_wrong")
            agg[bucket] += 1
            per_class[row["inst_class_gm"]][bucket] += 1

    def finish(d):
        total = sum(d.get(k, 0) for k in ("both_correct", "a_only", "b_only", "both_wrong"))
        out = dict(d)
        if total:
            out["oracle_recall"] = (d["both_correct"] + d["a_only"] + d["b_only"]) / total
            out["a_unique_contribution"] = d["a_only"] / total
            out["b_unique_contribution"] = d["b_only"] / total
            out["total"] = total
        return out

    return {
        "model_a": model_a, "model_b": model_b,
        "global": finish(agg),
        "per_class": {cls: finish(d) for cls, d in sorted(per_class.items())},
        "missing": missing,
        "onset_tolerance": onset_tolerance,
    }


def bootstrap_ci(per_stem_counts: List[dict], metric: str = "recall",
                 n_boot: int = 1000, seed: int = 42) -> Optional[dict]:
    """Stem-level bootstrap CI over count dicts with n_gt/n_pred/n_correct."""
    import random
    if not per_stem_counts:
        return None
    rng = random.Random(seed)
    vals = []
    n = len(per_stem_counts)
    for _ in range(n_boot):
        sample = [per_stem_counts[rng.randrange(n)] for _ in range(n)]
        n_gt = sum(s["n_gt"] for s in sample)
        n_pred = sum(s["n_pred"] for s in sample)
        n_correct = sum(s["n_correct"] for s in sample)
        m = matching.metrics_from_counts(n_gt, n_pred, n_correct, 0)
        vals.append(m[metric])
    vals.sort()
    return {"metric": metric, "mean": sum(vals) / len(vals),
            "ci_low": vals[int(0.025 * n_boot)], "ci_high": vals[int(0.975 * n_boot)]}


def per_stem_metrics(model_id: str, stems: pd.DataFrame,
                     onset_tolerance: float = 0.1) -> pd.DataFrame:
    """Per-stem metric table (for bootstrap, failure analysis, DuckDB)."""
    adapter = get_adapter(model_id)
    ck = adapter.checkpoint_hash()
    cfg = adapter.inference_config()
    rows = []
    for _, row in stems.iterrows():
        key = cache.prediction_key(row["audio_hash"], adapter.model_id,
                                   adapter.model_version, ck,
                                   adapter.adapter_version, cfg)
        pred_df = cache.load(model_id, key)
        if pred_df is None:
            continue
        gt_df = gt.get_gt_notes(corpus.resolve_midi(row), row["midi_hash"])
        if len(gt_df) == 0:
            continue
        mt = matching.get_match_table(key, gt.gt_identity(row["midi_hash"]),
                                      gt_df, pred_df, onset_tolerance)
        m = matching.metrics_from_match_table(mt)
        m["stem_id"] = row["stem_id"]
        m["inst_class"] = row["inst_class_gm"]
        rows.append(m)
    return pd.DataFrame(rows)
