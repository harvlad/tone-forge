#!/usr/bin/env python3
"""
Phase 3: Conservative audio-driven router test.

Tests heuristics using RUNTIME-AVAILABLE features only.
Cannot use gt_duration - must use model_duration.
"""

import json
import sys
from pathlib import Path
from collections import defaultdict

def load_data(json_path: Path) -> list:
    """Load per-note data from oracle experiment."""
    with open(json_path) as f:
        data = json.load(f)
    return data['notes']


def evaluate_heuristic(notes: list, condition_fn, name: str) -> dict:
    """
    Evaluate a heuristic that decides when to apply spectral correction.

    Returns metrics showing how well the heuristic predicts spectral_helped.
    """
    tp = fp = tn = fn = 0

    for note in notes:
        predicted_apply = condition_fn(note)
        actually_helped = note['spectral_helped']
        actually_hurt = note['spectral_hurt']

        if predicted_apply:
            if actually_helped:
                tp += 1
            elif actually_hurt:
                fp += 1
            else:
                # Neutral - count as neither
                pass
        else:
            if actually_helped:
                fn += 1
            elif actually_hurt:
                tn += 1

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0

    # Net improvement: helped - hurt when we apply correction
    applied_helped = sum(1 for n in notes if condition_fn(n) and n['spectral_helped'])
    applied_hurt = sum(1 for n in notes if condition_fn(n) and n['spectral_hurt'])
    net = applied_helped - applied_hurt

    # Coverage
    applied = sum(1 for n in notes if condition_fn(n))

    return {
        'name': name,
        'applied': applied,
        'coverage': applied / len(notes),
        'tp': tp,
        'fp': fp,
        'tn': tn,
        'fn': fn,
        'precision': precision,
        'recall': recall,
        'net_improvement': net,
    }


def compute_duration_metrics(notes: list, condition_fn) -> dict:
    """
    Compute actual duration metrics when applying correction based on heuristic.
    """
    good_count = 0
    total = len(notes)

    for note in notes:
        apply_spectral = condition_fn(note)

        if apply_spectral:
            is_good = note['spectral_is_good']
        else:
            is_good = note['model_is_good']

        if is_good:
            good_count += 1

    return {
        'good_count': good_count,
        'good_rate': good_count / total,
    }


def main():
    json_path = Path('/tmp/offset_oracle_data.json')
    if not json_path.exists():
        print(f"Error: {json_path} not found. Run offset_oracle_experiment.py first.")
        sys.exit(1)

    notes = load_data(json_path)
    print(f"Loaded {len(notes)} notes")
    print()

    # Baseline metrics
    baseline_good = sum(1 for n in notes if n['model_is_good'])
    spectral_all_good = sum(1 for n in notes if n['spectral_is_good'])
    oracle_good = sum(1 for n in notes if n['spectral_is_good'] or n['model_is_good'])

    print("=" * 70)
    print("BASELINE COMPARISON")
    print("=" * 70)
    print(f"Model baseline:     {baseline_good:5d} / {len(notes)} = {100*baseline_good/len(notes):5.1f}%")
    print(f"Spectral all:       {spectral_all_good:5d} / {len(notes)} = {100*spectral_all_good/len(notes):5.1f}%")
    print(f"Per-note oracle:    {oracle_good:5d} / {len(notes)} = {100*oracle_good/len(notes):5.1f}%")
    print()

    # Define heuristics using RUNTIME-AVAILABLE features
    heuristics = [
        # Original best from Phase 2 (but with model_duration)
        (
            "spectral_decay<-50 AND model_dur<0.5",
            lambda n: n['spectral_decay_slope'] < -50 and n['model_duration'] < 0.5
        ),
        # Variations on threshold
        (
            "spectral_decay<-40 AND model_dur<0.5",
            lambda n: n['spectral_decay_slope'] < -40 and n['model_duration'] < 0.5
        ),
        (
            "spectral_decay<-60 AND model_dur<0.5",
            lambda n: n['spectral_decay_slope'] < -60 and n['model_duration'] < 0.5
        ),
        # Different duration thresholds
        (
            "spectral_decay<-50 AND model_dur<0.3",
            lambda n: n['spectral_decay_slope'] < -50 and n['model_duration'] < 0.3
        ),
        (
            "spectral_decay<-50 AND model_dur<1.0",
            lambda n: n['spectral_decay_slope'] < -50 and n['model_duration'] < 1.0
        ),
        # Add energy decay
        (
            "spectral_decay<-50 AND energy_decay<-30",
            lambda n: n['spectral_decay_slope'] < -50 and n['energy_decay_slope'] < -30
        ),
        # Transient-based
        (
            "transient>0.5 AND model_dur<0.5",
            lambda n: n['transient_strength'] > 0.5 and n['model_duration'] < 0.5
        ),
        # Combined
        (
            "spectral_decay<-50 AND transient>0.3",
            lambda n: n['spectral_decay_slope'] < -50 and n['transient_strength'] > 0.3
        ),
        # Very conservative
        (
            "spectral_decay<-70 AND model_dur<0.3",
            lambda n: n['spectral_decay_slope'] < -70 and n['model_duration'] < 0.3
        ),
        # Attack-based
        (
            "attack_slope>100 AND model_dur<0.5",
            lambda n: n['attack_slope'] > 100 and n['model_duration'] < 0.5
        ),
    ]

    print("=" * 70)
    print("HEURISTIC EVALUATION (prediction quality)")
    print("=" * 70)
    print(f"{'Heuristic':<45} {'Applied':>7} {'Prec':>6} {'Net':>6}")
    print("-" * 70)

    results = []
    for name, condition in heuristics:
        metrics = evaluate_heuristic(notes, condition, name)
        results.append((name, condition, metrics))
        print(f"{name:<45} {metrics['applied']:>7} {100*metrics['precision']:>5.1f}% {metrics['net_improvement']:>+6d}")

    print()
    print("=" * 70)
    print("ACTUAL GOOD-DURATION RATE WITH EACH HEURISTIC")
    print("=" * 70)
    print(f"{'Heuristic':<45} {'Good':>7} {'Rate':>7} {'vs Base':>8}")
    print("-" * 70)

    baseline_rate = baseline_good / len(notes)

    best_heuristic = None
    best_improvement = 0

    for name, condition, pred_metrics in results:
        dur_metrics = compute_duration_metrics(notes, condition)
        improvement = dur_metrics['good_rate'] - baseline_rate

        print(f"{name:<45} {dur_metrics['good_count']:>7} {100*dur_metrics['good_rate']:>6.1f}% {100*improvement:>+7.1f}%")

        if improvement > best_improvement:
            best_improvement = improvement
            best_heuristic = (name, condition)

    print()
    print("=" * 70)
    print("BEST HEURISTIC ANALYSIS")
    print("=" * 70)

    if best_heuristic:
        name, condition = best_heuristic
        print(f"Best: {name}")
        print(f"Improvement: {100*best_improvement:+.1f}% good-duration rate")
        print()

        # Per-class breakdown
        print("Per-class breakdown:")
        print(f"{'Class':<30} {'Base':>6} {'Heur':>6} {'Delta':>7}")
        print("-" * 55)

        class_notes = defaultdict(list)
        for n in notes:
            class_notes[n['inst_class']].append(n)

        for cls in sorted(class_notes.keys()):
            cls_notes = class_notes[cls]
            cls_base = sum(1 for n in cls_notes if n['model_is_good']) / len(cls_notes)

            cls_heur = 0
            for n in cls_notes:
                if condition(n):
                    if n['spectral_is_good']:
                        cls_heur += 1
                else:
                    if n['model_is_good']:
                        cls_heur += 1
            cls_heur /= len(cls_notes)

            delta = cls_heur - cls_base
            print(f"{cls:<30} {100*cls_base:>5.1f}% {100*cls_heur:>5.1f}% {100*delta:>+6.1f}%")

    print()
    print("=" * 70)
    print("CONSERVATIVE ROUTER RECOMMENDATION")
    print("=" * 70)

    # Check if ANY heuristic actually helps
    any_helps = best_improvement > 0.005  # 0.5% threshold

    if any_helps:
        print(f"Recommended heuristic: {best_heuristic[0]}")
        print(f"Expected improvement: {100*best_improvement:+.1f}% good-duration rate")
        print()
        print("Implementation:")
        print("  if spectral_decay_slope < THRESHOLD and model_duration < DUR_THRESHOLD:")
        print("      use spectral offset")
        print("  else:")
        print("      use model offset")
    else:
        print("NO heuristic provides meaningful improvement (>0.5%).")
        print("Audio-driven routing for offsets is NOT recommended.")
        print("Consider focusing on other error sources (octave errors, onset timing).")


if __name__ == '__main__':
    main()
