"""P1 smoke eval — SCOUT METRICS ONLY, never a promotion signal.

For each arm's best checkpoint, run inference on the valid pairs and report
SI-SDR of the predicted synth against the paired-difference target, next to
the two degenerate baselines every arm must beat to survive:

  identity  — predict rest = input, synth = 0 (what shipping nothing does)
  half      — predict synth = input/2 (energy-split strawman)

Doctrine: SI-SDR is a false-positive machine — these numbers ONLY feed the
P1 kill decision (an arm that can't beat `identity` on its own training
distribution has learned nothing) and arm RANKING. Promotion is ears-only.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import soundfile as sf


def si_sdr(ref: np.ndarray, est: np.ndarray) -> float:
    ref = ref.flatten().astype(np.float64)
    est = est.flatten().astype(np.float64)
    n = min(len(ref), len(est))
    ref, est = ref[:n], est[:n]
    if float((ref**2).sum()) < 1e-10:
        return float("nan")  # silent target — SI-SDR undefined, skip
    s = (np.dot(est, ref) / (np.dot(ref, ref))) * ref
    e = est - s
    return float(10 * np.log10(((s**2).sum() + 1e-12) / ((e**2).sum() + 1e-12)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", required=True)
    ap.add_argument("--results", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    pair_dirs = sorted(p for p in Path(args.pairs).rglob("*") if (p / "mixture.flac").exists())
    report: dict = {"n_pairs": len(pair_dirs), "arms": {}, "baselines": {}}

    # Baselines from the raw pairs (no model needed).
    ident, half = [], []
    for p in pair_dirs:
        mix, _ = sf.read(p / "mixture.flac", dtype="float32")
        syn, _ = sf.read(p / "synth.flac", dtype="float32")
        v = si_sdr(syn, np.zeros_like(syn))
        if not np.isnan(v):
            ident.append(v)
        v = si_sdr(syn, mix / 2)
        if not np.isnan(v):
            half.append(v)
    report["baselines"] = {
        "identity_zero_synth": {"median": float(np.median(ident)) if ident else None},
        "half_energy": {"median": float(np.median(half)) if half else None},
    }

    # Arms: inference via MSST's inference script output layout (results/<arm>
    # holds checkpoints; the pod entry runs msst/inference.py per arm into
    # results/<arm>/pred before calling this). If predictions are absent the
    # arm is reported as not-evaluated rather than silently skipped.
    for arm_dir in sorted(Path(args.results).iterdir()):
        pred_root = arm_dir / "pred"
        if not pred_root.exists():
            report["arms"][arm_dir.name] = {"status": "no predictions"}
            continue
        scores = []
        for p in pair_dirs:
            # inference input was infer_in/<track>__<variant>.flac; MSST's
            # default template writes store_dir/<file_name>/<instr>.<codec>
            name = f"{p.parent.name}__{p.name}"
            pp = pred_root / name / "synth.flac"
            if not pp.exists():
                pp = pred_root / name / "synth.wav"
            if not pp.exists():
                continue
            ref, _ = sf.read(p / "synth.flac", dtype="float32")
            est, _ = sf.read(pp, dtype="float32")
            v = si_sdr(ref, est)
            if not np.isnan(v):
                scores.append(v)
        report["arms"][arm_dir.name] = {
            "n": len(scores),
            "median": float(np.median(scores)) if scores else None,
            "mean": float(np.mean(scores)) if scores else None,
        }

    Path(args.out).write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
