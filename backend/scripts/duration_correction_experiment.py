#!/usr/bin/env python3
"""Duration Correction Control Experiment.

Tests whether simple duration scaling can improve F1:
A) Global correction factor (1 / median_ratio for all instruments)
B) Per-instrument-class correction factor

This is a CONTROL experiment to establish upper bounds before attempting
more sophisticated audio-driven offset reconstruction.

Does NOT modify the model - applies corrections post-hoc to benchmark data.
"""
import sys
from pathlib import Path
from typing import List, Tuple, Dict, Optional
import json
import numpy as np
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def compute_f1_with_correction(
    gt_notes: List[Tuple[int, float, float]],
    ext_notes: List[Tuple[int, float, float]],
    duration_factor: float = 1.0,
    onset_tol: float = 0.35,
) -> Dict:
    """Compute F1 with duration correction applied to extracted notes.

    Args:
        gt_notes: Ground truth (pitch, onset, offset)
        ext_notes: Extracted (pitch, onset, offset)
        duration_factor: Multiply extracted duration by this factor
        onset_tol: Onset tolerance for matching
    """
    if not gt_notes or not ext_notes:
        return {'f1': 0, 'precision': 0, 'recall': 0, 'tp': 0, 'fp': len(ext_notes), 'fn': len(gt_notes)}

    # Apply correction: new_offset = onset + (offset - onset) * factor
    corrected_ext = []
    for p, onset, offset in ext_notes:
        duration = offset - onset
        new_duration = duration * duration_factor
        new_offset = onset + new_duration
        corrected_ext.append((p, onset, new_offset))

    # Match with octave tolerance
    candidates = []
    for i, (ep, eo, ee) in enumerate(corrected_ext):
        for j, (gp, go, ge) in enumerate(gt_notes):
            onset_diff = abs(eo - go)
            if onset_diff > onset_tol:
                continue

            pitch_diff = ep - gp
            same_pitch_class = (ep % 12) == (gp % 12)

            if pitch_diff == 0:
                candidates.append((j, i, True, onset_diff))
            elif same_pitch_class:
                candidates.append((j, i, False, onset_diff))

    # Sort: exact first, then by onset closeness
    candidates.sort(key=lambda x: (not x[2], x[3]))

    gt_matched = set()
    ext_matched = set()

    for gt_idx, ext_idx, is_exact, _ in candidates:
        if gt_idx in gt_matched or ext_idx in ext_matched:
            continue
        gt_matched.add(gt_idx)
        ext_matched.add(ext_idx)

    tp = len(gt_matched)
    fp = len(ext_notes) - len(ext_matched)
    fn = len(gt_notes) - len(gt_matched)

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0

    return {
        'f1': float(f1),
        'precision': float(precision),
        'recall': float(recall),
        'tp': int(tp),
        'fp': int(fp),
        'fn': int(fn),
    }


def compute_duration_stats(
    gt_notes: List[Tuple[int, float, float]],
    ext_notes: List[Tuple[int, float, float]],
    onset_tol: float = 0.35,
) -> Dict:
    """Compute duration ratios for matched notes."""
    if not gt_notes or not ext_notes:
        return {'ratios': [], 'mean': 0, 'median': 0}

    # Match notes
    candidates = []
    for i, (ep, eo, ee) in enumerate(ext_notes):
        for j, (gp, go, ge) in enumerate(gt_notes):
            onset_diff = abs(eo - go)
            if onset_diff > onset_tol:
                continue

            same_pitch_class = (ep % 12) == (gp % 12)
            if ep == gp or same_pitch_class:
                candidates.append((j, i, ep == gp, onset_diff, go, ge, eo, ee))

    candidates.sort(key=lambda x: (not x[2], x[3]))

    gt_matched = set()
    ext_matched = set()
    ratios = []

    for gt_idx, ext_idx, _, _, go, ge, eo, ee in candidates:
        if gt_idx in gt_matched or ext_idx in ext_matched:
            continue
        gt_matched.add(gt_idx)
        ext_matched.add(ext_idx)

        gt_dur = ge - go
        ext_dur = ee - eo
        if gt_dur > 0.01:  # Avoid division by near-zero
            ratios.append(ext_dur / gt_dur)

    if not ratios:
        return {'ratios': [], 'mean': 1.0, 'median': 1.0}

    return {
        'ratios': ratios,
        'mean': float(np.mean(ratios)),
        'median': float(np.median(ratios)),
    }


def run_correction_experiment(
    results_file: Path,
    output_file: Path,
):
    """Run duration correction experiment on existing benchmark results.

    Uses the per-note data from duration_analysis.json.
    """
    with open(results_file) as f:
        data = json.load(f)

    stems = data['stems']
    summary = data['summary']

    print(f"Loaded {len(stems)} stems with {summary['total_notes']} matched notes")
    print(f"Current median duration ratio: {summary['median_ratio']:.2f}")

    # Compute global correction factor
    global_factor = 1.0 / summary['median_ratio']
    print(f"\nGlobal correction factor: {global_factor:.2f}")

    # Per-class correction factors
    class_factors = {}
    for cls, stats in summary['by_class'].items():
        class_factors[cls] = 1.0 / stats['median']
        print(f"  {cls:25s}: {class_factors[cls]:.2f} (1 / {stats['median']:.2f})")

    # Test correction factors
    # For proper testing, we need the original GT and extracted notes
    # The duration_analysis.json has per-note matched data but not full note lists

    # We'll simulate by analyzing the effect on duration ratios
    print(f"\n{'='*70}")
    print("SIMULATED CORRECTION EFFECTS")
    print(f"{'='*70}")

    all_notes = []
    for stem in stems:
        for note in stem['notes']:
            note['inst_class'] = stem['inst_class']
            all_notes.append(note)

    # Baseline: what percentage of notes are "good" (0.8-1.2)?
    baseline_good = sum(1 for n in all_notes if 0.8 <= n['duration_ratio'] <= 1.2)
    print(f"\nBaseline good (0.8-1.2): {baseline_good}/{len(all_notes)} ({baseline_good/len(all_notes)*100:.1f}%)")

    # With global correction
    global_good = 0
    for n in all_notes:
        corrected_ratio = n['duration_ratio'] * global_factor
        if 0.8 <= corrected_ratio <= 1.2:
            global_good += 1
    print(f"Global correction good: {global_good}/{len(all_notes)} ({global_good/len(all_notes)*100:.1f}%)")

    # With per-class correction
    class_good = 0
    for n in all_notes:
        factor = class_factors.get(n['inst_class'], 1.0)
        corrected_ratio = n['duration_ratio'] * factor
        if 0.8 <= corrected_ratio <= 1.2:
            class_good += 1
    print(f"Per-class correction good: {class_good}/{len(all_notes)} ({class_good/len(all_notes)*100:.1f}%)")

    # Breakdown by class
    print(f"\n{'='*70}")
    print("BY INSTRUMENT CLASS")
    print(f"{'='*70}")

    by_class = defaultdict(list)
    for n in all_notes:
        by_class[n['inst_class']].append(n)

    print(f"\n{'Class':25s} {'Base%':>7s} {'Global%':>8s} {'PerCls%':>8s} {'Delta':>7s}")
    print("-" * 60)

    class_results = []
    for cls in sorted(by_class.keys()):
        notes = by_class[cls]
        n_total = len(notes)

        base = sum(1 for n in notes if 0.8 <= n['duration_ratio'] <= 1.2) / n_total * 100
        glob = sum(1 for n in notes if 0.8 <= n['duration_ratio'] * global_factor <= 1.2) / n_total * 100
        pcls = sum(1 for n in notes if 0.8 <= n['duration_ratio'] * class_factors.get(cls, 1.0) <= 1.2) / n_total * 100

        delta = pcls - base
        class_results.append({
            'class': cls,
            'n': n_total,
            'baseline': base,
            'global': glob,
            'per_class': pcls,
            'delta': delta,
        })

        print(f"{cls:25s} {base:6.1f}% {glob:7.1f}% {pcls:7.1f}% {delta:+6.1f}%")

    # Save results
    results = {
        'global_factor': global_factor,
        'class_factors': class_factors,
        'baseline_good_pct': baseline_good / len(all_notes) * 100,
        'global_good_pct': global_good / len(all_notes) * 100,
        'per_class_good_pct': class_good / len(all_notes) * 100,
        'by_class': class_results,
    }

    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2)

    print(f"\nResults saved to {output_file}")

    # Insights
    print(f"\n{'='*70}")
    print("INSIGHTS")
    print(f"{'='*70}")

    print(f"\n1. Global correction moves {global_good - baseline_good} more notes to 'good' range")
    print(f"   ({baseline_good/len(all_notes)*100:.1f}% -> {global_good/len(all_notes)*100:.1f}%)")

    print(f"\n2. Per-class correction moves {class_good - baseline_good} more notes to 'good' range")
    print(f"   ({baseline_good/len(all_notes)*100:.1f}% -> {class_good/len(all_notes)*100:.1f}%)")

    improvement = class_good - baseline_good
    max_possible = len(all_notes) - baseline_good
    print(f"\n3. Per-class recovers {improvement/max_possible*100:.1f}% of possible improvement")

    # Classes that get WORSE with global correction
    print(f"\n4. Classes harmed by global correction:")
    for cr in class_results:
        if cr['global'] < cr['baseline']:
            print(f"   {cr['class']}: {cr['baseline']:.1f}% -> {cr['global']:.1f}%")

    print(f"\n5. Classes with biggest per-class improvement:")
    for cr in sorted(class_results, key=lambda x: -x['delta'])[:5]:
        print(f"   {cr['class']}: +{cr['delta']:.1f}% ({cr['baseline']:.1f}% -> {cr['per_class']:.1f}%)")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=str, default="/tmp/duration_analysis.json")
    parser.add_argument("--output", type=str, default="/tmp/duration_correction_results.json")
    args = parser.parse_args()

    run_correction_experiment(Path(args.input), Path(args.output))
