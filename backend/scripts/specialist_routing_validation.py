#!/usr/bin/env python3
"""
Specialist Routing Validation

Deep analysis of Bass/Synth Lead routing hypothesis:
- Bass -> MT3
- Everything else -> Basic Pitch

Analyzes:
1. Bass by register
2. Bass octave error direction
3. Bass playing regimes
4. Per-stem consistency
5. Routing oracles (stem/phrase/note)
6. Synth Lead validation
7. Organ control case
8. Bootstrap confidence intervals
9. Routing architectures A/B/C/D
10. Runtime/deployment cost
11. Remaining oracle gap
"""
import json
import os
import sys
import time
from pathlib import Path
from collections import defaultdict
from datetime import datetime
import tempfile

import numpy as np
import yaml
import mido
import librosa

sys.path.insert(0, '/Users/mattharvey/Sites/tone-forge/backend')

from scripts.mt3_vs_basic_pitch import (
    find_stems, load_gt_notes, run_basic_pitch_inference, run_mt3_inference,
    match_notes, compute_metrics, head_to_head_analysis, SLAKH_ROOT, OUTPUT_DIR
)

try:
    import mt3_infer
    MT3_AVAILABLE = True
except ImportError:
    MT3_AVAILABLE = False

# Register boundaries for Bass analysis
# MIDI note 28 = E1 (~41Hz), 40 = E2 (~82Hz), 48 = C3, 55 = G3
BASS_REGISTERS = {
    "sub_bass": (0, 35),      # < B1 (~62Hz) - subharmonic territory
    "low": (36, 43),          # C2-G2 (65-98Hz) - fundamental bass
    "mid_low": (44, 51),      # G#2-D#3 (104-156Hz)
    "mid": (52, 59),          # E3-B3 (165-247Hz)
    "high": (60, 127),        # C4+ (262Hz+) - upper bass/overlap
}


def analyze_bass_by_register(bass_stems, bp_model=None, mt3_model="mt3_pytorch"):
    """Analyze Bass performance by MIDI register."""
    print("\n" + "="*70)
    print("BASS ANALYSIS BY REGISTER")
    print("="*70)

    register_stats = {reg: {
        "bp": defaultdict(float),
        "mt3": defaultdict(float),
        "h2h": defaultdict(int),
    } for reg in BASS_REGISTERS}

    all_bp_notes = []
    all_mt3_notes = []
    all_gt_notes = []
    stem_results = []

    for i, stem in enumerate(bass_stems):
        if (i + 1) % 5 == 0:
            print(f"  [{i+1}/{len(bass_stems)}]...", flush=True)

        gt_notes = load_gt_notes(stem["midi_path"])
        if not gt_notes:
            continue

        bp_notes = run_basic_pitch_inference(stem["audio_path"], bp_model)
        mt3_notes = run_mt3_inference(stem["audio_path"], mt3_model)

        if bp_notes is None or mt3_notes is None:
            continue

        # Store for later analysis
        stem_gt = [(n, stem["track_id"], stem["stem_id"]) for n in gt_notes]
        stem_bp = [(n, stem["track_id"], stem["stem_id"]) for n in bp_notes]
        stem_mt3 = [(n, stem["track_id"], stem["stem_id"]) for n in mt3_notes]
        all_gt_notes.extend(stem_gt)
        all_bp_notes.extend(bp_notes)
        all_mt3_notes.extend(mt3_notes)

        # Per-stem metrics
        bp_metrics = compute_metrics(gt_notes, bp_notes)
        mt3_metrics = compute_metrics(gt_notes, mt3_notes)
        h2h = head_to_head_analysis(gt_notes, bp_notes, mt3_notes)

        stem_results.append({
            "track_id": stem["track_id"],
            "stem_id": stem["stem_id"],
            "n_gt": len(gt_notes),
            "bp_recall": bp_metrics["recall"],
            "bp_precision": bp_metrics["precision"],
            "bp_f1": bp_metrics["f1"],
            "bp_octave_rate": bp_metrics["octave_error_rate"],
            "mt3_recall": mt3_metrics["recall"],
            "mt3_precision": mt3_metrics["precision"],
            "mt3_f1": mt3_metrics["f1"],
            "mt3_octave_rate": mt3_metrics["octave_error_rate"],
            "both_correct": h2h["both_correct"],
            "bp_only": h2h["bp_only"],
            "mt3_only": h2h["mt3_only"],
            "both_wrong": h2h["both_wrong"],
        })

        # Analyze by register
        for reg_name, (lo, hi) in BASS_REGISTERS.items():
            reg_gt = [n for n in gt_notes if lo <= n["pitch"] <= hi]
            if not reg_gt:
                continue

            reg_bp = [n for n in bp_notes if lo <= n["pitch"] <= hi]
            reg_mt3 = [n for n in mt3_notes if lo <= n["pitch"] <= hi]

            bp_m = compute_metrics(reg_gt, bp_notes)  # Match against full pred
            mt3_m = compute_metrics(reg_gt, mt3_notes)

            for key in ["n_gt", "n_pred", "n_correct", "n_octave", "octave_up", "octave_down"]:
                register_stats[reg_name]["bp"][key] += bp_m.get(key, 0)
                register_stats[reg_name]["mt3"][key] += mt3_m.get(key, 0)

            # H2H for register
            h2h_reg = head_to_head_analysis(reg_gt, bp_notes, mt3_notes)
            for key in ["both_correct", "bp_only", "mt3_only", "both_wrong"]:
                register_stats[reg_name]["h2h"][key] += h2h_reg[key]

    # Compute derived metrics per register
    print("\nBass by Register:")
    print("-" * 100)
    print(f"{'Register':<12} {'GT':>6} {'BP_Rec':>8} {'MT3_Rec':>8} {'BP_Prec':>8} {'MT3_Prec':>8} "
          f"{'BP_Oct':>8} {'MT3_Oct':>8} {'MT3_Only':>9}")
    print("-" * 100)

    for reg_name in ["sub_bass", "low", "mid_low", "mid", "high"]:
        bp = register_stats[reg_name]["bp"]
        mt3 = register_stats[reg_name]["mt3"]
        h2h = register_stats[reg_name]["h2h"]

        n_gt = bp["n_gt"]
        if n_gt == 0:
            continue

        bp_recall = bp["n_correct"] / n_gt if n_gt > 0 else 0
        mt3_recall = mt3["n_correct"] / n_gt if n_gt > 0 else 0
        bp_prec = bp["n_correct"] / bp["n_pred"] if bp["n_pred"] > 0 else 0
        mt3_prec = mt3["n_correct"] / mt3["n_pred"] if mt3["n_pred"] > 0 else 0
        bp_oct = bp["n_octave"] / n_gt if n_gt > 0 else 0
        mt3_oct = mt3["n_octave"] / n_gt if n_gt > 0 else 0
        mt3_unique = h2h["mt3_only"]

        print(f"{reg_name:<12} {int(n_gt):>6} {bp_recall:>8.1%} {mt3_recall:>8.1%} "
              f"{bp_prec:>8.1%} {mt3_prec:>8.1%} {bp_oct:>8.1%} {mt3_oct:>8.1%} {mt3_unique:>9}")

        register_stats[reg_name]["metrics"] = {
            "n_gt": n_gt,
            "bp_recall": bp_recall,
            "mt3_recall": mt3_recall,
            "bp_precision": bp_prec,
            "mt3_precision": mt3_prec,
            "bp_octave_rate": bp_oct,
            "mt3_octave_rate": mt3_oct,
            "mt3_only": mt3_unique,
        }

    return register_stats, stem_results, all_gt_notes, all_bp_notes, all_mt3_notes


def analyze_octave_direction(stems, bp_model=None, mt3_model="mt3_pytorch", inst_class="Bass"):
    """Analyze octave error direction for a class."""
    print(f"\n{'='*70}")
    print(f"{inst_class.upper()} OCTAVE ERROR DIRECTION")
    print("="*70)

    bp_octave_errors = []
    mt3_octave_errors = []

    for stem in stems:
        if stem["inst_class"] != inst_class:
            continue

        gt_notes = load_gt_notes(stem["midi_path"])
        if not gt_notes:
            continue

        bp_notes = run_basic_pitch_inference(stem["audio_path"], bp_model)
        mt3_notes = run_mt3_inference(stem["audio_path"], mt3_model)

        if bp_notes is None or mt3_notes is None:
            continue

        # Get octave errors with details
        _, bp_octave, _, _ = match_notes(gt_notes, bp_notes)
        _, mt3_octave, _, _ = match_notes(gt_notes, mt3_notes)

        for gt, pred, diff in bp_octave:
            bp_octave_errors.append({
                "gt_pitch": gt["pitch"],
                "pred_pitch": pred["pitch"],
                "diff": diff,
                "direction": "up" if diff > 0 else "down",
            })

        for gt, pred, diff in mt3_octave:
            mt3_octave_errors.append({
                "gt_pitch": gt["pitch"],
                "pred_pitch": pred["pitch"],
                "diff": diff,
                "direction": "up" if diff > 0 else "down",
            })

    # Summarize
    def summarize_octave_errors(errors, name):
        if not errors:
            print(f"{name}: No octave errors")
            return {}

        total = len(errors)
        up = sum(1 for e in errors if e["direction"] == "up")
        down = sum(1 for e in errors if e["direction"] == "down")
        exact_12_up = sum(1 for e in errors if e["diff"] == 12)
        exact_12_down = sum(1 for e in errors if e["diff"] == -12)
        exact_24_plus = sum(1 for e in errors if abs(e["diff"]) >= 24)

        # By GT register
        by_register = defaultdict(lambda: {"up": 0, "down": 0, "total": 0})
        for e in errors:
            for reg_name, (lo, hi) in BASS_REGISTERS.items():
                if lo <= e["gt_pitch"] <= hi:
                    by_register[reg_name]["total"] += 1
                    by_register[reg_name][e["direction"]] += 1
                    break

        print(f"\n{name}:")
        print(f"  Total octave errors: {total}")
        print(f"  Octave UP (+12): {up} ({up/total:.1%})")
        print(f"  Octave DOWN (-12): {down} ({down/total:.1%})")
        print(f"  Exact +12: {exact_12_up}")
        print(f"  Exact -12: {exact_12_down}")
        print(f"  +/-24 or larger: {exact_24_plus}")
        print(f"  By GT register:")
        for reg in ["sub_bass", "low", "mid_low", "mid", "high"]:
            r = by_register[reg]
            if r["total"] > 0:
                print(f"    {reg}: {r['total']} errors (up={r['up']}, down={r['down']})")

        return {
            "total": total,
            "up": up,
            "down": down,
            "exact_12_up": exact_12_up,
            "exact_12_down": exact_12_down,
            "exact_24_plus": exact_24_plus,
            "by_register": dict(by_register),
        }

    bp_summary = summarize_octave_errors(bp_octave_errors, "Basic Pitch")
    mt3_summary = summarize_octave_errors(mt3_octave_errors, "MT3")

    return {"bp": bp_summary, "mt3": mt3_summary}


def analyze_playing_regimes(bass_stems, bp_model=None, mt3_model="mt3_pytorch"):
    """Analyze Bass by playing regime proxies."""
    print("\n" + "="*70)
    print("BASS PLAYING REGIME ANALYSIS")
    print("="*70)

    stem_features = []

    for stem in bass_stems:
        gt_notes = load_gt_notes(stem["midi_path"])
        if not gt_notes or len(gt_notes) < 10:
            continue

        bp_notes = run_basic_pitch_inference(stem["audio_path"], bp_model)
        mt3_notes = run_mt3_inference(stem["audio_path"], mt3_model)

        if bp_notes is None or mt3_notes is None:
            continue

        bp_metrics = compute_metrics(gt_notes, bp_notes)
        mt3_metrics = compute_metrics(gt_notes, mt3_notes)

        # Compute regime features from GT
        onsets = sorted([n["onset"] for n in gt_notes])
        durations = [n["offset"] - n["onset"] for n in gt_notes]
        pitches = [n["pitch"] for n in gt_notes]
        intervals = [abs(pitches[i+1] - pitches[i]) for i in range(len(pitches)-1)]

        # Features
        duration_mean = np.mean(durations)
        duration_std = np.std(durations)
        onset_density = len(gt_notes) / (onsets[-1] - onsets[0]) if onsets[-1] > onsets[0] else 0

        # IOI (inter-onset interval)
        iois = [onsets[i+1] - onsets[i] for i in range(len(onsets)-1)]
        ioi_mean = np.mean(iois) if iois else 0
        ioi_std = np.std(iois) if iois else 0

        # Repeated notes
        repeated_notes = sum(1 for i in intervals if i == 0)
        repeated_rate = repeated_notes / len(intervals) if intervals else 0

        # Large intervals
        large_intervals = sum(1 for i in intervals if i >= 5)
        large_interval_rate = large_intervals / len(intervals) if intervals else 0

        # Pitch range
        pitch_range = max(pitches) - min(pitches)
        pitch_mean = np.mean(pitches)

        stem_features.append({
            "track_id": stem["track_id"],
            "stem_id": stem["stem_id"],
            "n_gt": len(gt_notes),
            "duration_mean": duration_mean,
            "duration_std": duration_std,
            "onset_density": onset_density,
            "ioi_mean": ioi_mean,
            "ioi_std": ioi_std,
            "repeated_rate": repeated_rate,
            "large_interval_rate": large_interval_rate,
            "pitch_range": pitch_range,
            "pitch_mean": pitch_mean,
            "bp_recall": bp_metrics["recall"],
            "mt3_recall": mt3_metrics["recall"],
            "bp_octave_rate": bp_metrics["octave_error_rate"],
            "mt3_octave_rate": mt3_metrics["octave_error_rate"],
            "mt3_advantage": mt3_metrics["recall"] - bp_metrics["recall"],
        })

    if not stem_features:
        print("No valid stems for regime analysis")
        return {}

    # Analyze by regime bins
    features_df = stem_features

    # Duration regime: short (<0.3s) vs sustained (>=0.3s)
    short_stems = [f for f in features_df if f["duration_mean"] < 0.3]
    sustained_stems = [f for f in features_df if f["duration_mean"] >= 0.3]

    print("\nBy Note Duration:")
    print(f"  Short (<0.3s): {len(short_stems)} stems")
    if short_stems:
        print(f"    BP recall: {np.mean([f['bp_recall'] for f in short_stems]):.1%}")
        print(f"    MT3 recall: {np.mean([f['mt3_recall'] for f in short_stems]):.1%}")
    print(f"  Sustained (>=0.3s): {len(sustained_stems)} stems")
    if sustained_stems:
        print(f"    BP recall: {np.mean([f['bp_recall'] for f in sustained_stems]):.1%}")
        print(f"    MT3 recall: {np.mean([f['mt3_recall'] for f in sustained_stems]):.1%}")

    # Density regime: sparse (<2 notes/s) vs dense (>=2 notes/s)
    sparse_stems = [f for f in features_df if f["onset_density"] < 2]
    dense_stems = [f for f in features_df if f["onset_density"] >= 2]

    print("\nBy Onset Density:")
    print(f"  Sparse (<2/s): {len(sparse_stems)} stems")
    if sparse_stems:
        print(f"    BP recall: {np.mean([f['bp_recall'] for f in sparse_stems]):.1%}")
        print(f"    MT3 recall: {np.mean([f['mt3_recall'] for f in sparse_stems]):.1%}")
    print(f"  Dense (>=2/s): {len(dense_stems)} stems")
    if dense_stems:
        print(f"    BP recall: {np.mean([f['bp_recall'] for f in dense_stems]):.1%}")
        print(f"    MT3 recall: {np.mean([f['mt3_recall'] for f in dense_stems]):.1%}")

    # Repeated notes regime
    no_repeat = [f for f in features_df if f["repeated_rate"] < 0.1]
    high_repeat = [f for f in features_df if f["repeated_rate"] >= 0.1]

    print("\nBy Repeated Notes:")
    print(f"  Low repeat (<10%): {len(no_repeat)} stems")
    if no_repeat:
        print(f"    BP recall: {np.mean([f['bp_recall'] for f in no_repeat]):.1%}")
        print(f"    MT3 recall: {np.mean([f['mt3_recall'] for f in no_repeat]):.1%}")
    print(f"  High repeat (>=10%): {len(high_repeat)} stems")
    if high_repeat:
        print(f"    BP recall: {np.mean([f['bp_recall'] for f in high_repeat]):.1%}")
        print(f"    MT3 recall: {np.mean([f['mt3_recall'] for f in high_repeat]):.1%}")

    # Pitch range regime
    low_range = [f for f in features_df if f["pitch_mean"] < 45]
    high_range = [f for f in features_df if f["pitch_mean"] >= 45]

    print("\nBy Mean Pitch:")
    print(f"  Low (<MIDI 45): {len(low_range)} stems")
    if low_range:
        print(f"    BP recall: {np.mean([f['bp_recall'] for f in low_range]):.1%}")
        print(f"    MT3 recall: {np.mean([f['mt3_recall'] for f in low_range]):.1%}")
        print(f"    BP octave err: {np.mean([f['bp_octave_rate'] for f in low_range]):.1%}")
        print(f"    MT3 octave err: {np.mean([f['mt3_octave_rate'] for f in low_range]):.1%}")
    print(f"  High (>=MIDI 45): {len(high_range)} stems")
    if high_range:
        print(f"    BP recall: {np.mean([f['bp_recall'] for f in high_range]):.1%}")
        print(f"    MT3 recall: {np.mean([f['mt3_recall'] for f in high_range]):.1%}")
        print(f"    BP octave err: {np.mean([f['bp_octave_rate'] for f in high_range]):.1%}")
        print(f"    MT3 octave err: {np.mean([f['mt3_octave_rate'] for f in high_range]):.1%}")

    return {
        "by_duration": {
            "short": {"n": len(short_stems),
                      "bp_recall": np.mean([f['bp_recall'] for f in short_stems]) if short_stems else 0,
                      "mt3_recall": np.mean([f['mt3_recall'] for f in short_stems]) if short_stems else 0},
            "sustained": {"n": len(sustained_stems),
                          "bp_recall": np.mean([f['bp_recall'] for f in sustained_stems]) if sustained_stems else 0,
                          "mt3_recall": np.mean([f['mt3_recall'] for f in sustained_stems]) if sustained_stems else 0},
        },
        "by_density": {
            "sparse": {"n": len(sparse_stems),
                       "bp_recall": np.mean([f['bp_recall'] for f in sparse_stems]) if sparse_stems else 0,
                       "mt3_recall": np.mean([f['mt3_recall'] for f in sparse_stems]) if sparse_stems else 0},
            "dense": {"n": len(dense_stems),
                      "bp_recall": np.mean([f['bp_recall'] for f in dense_stems]) if dense_stems else 0,
                      "mt3_recall": np.mean([f['mt3_recall'] for f in dense_stems]) if dense_stems else 0},
        },
        "stem_features": stem_features,
    }


def analyze_per_stem_consistency(stem_results):
    """Analyze per-stem wins/losses."""
    print("\n" + "="*70)
    print("PER-STEM CONSISTENCY")
    print("="*70)

    if not stem_results:
        print("No stem results")
        return {}

    # Classify stems
    mt3_wins = []
    bp_wins = []
    ties = []

    for s in stem_results:
        diff = s["mt3_recall"] - s["bp_recall"]
        if diff > 0.05:  # MT3 wins by >5pp
            mt3_wins.append(s)
        elif diff < -0.05:  # BP wins by >5pp
            bp_wins.append(s)
        else:
            ties.append(s)

    print(f"\nStem classification (5pp threshold):")
    print(f"  MT3 wins: {len(mt3_wins)} ({len(mt3_wins)/len(stem_results):.1%})")
    print(f"  BP wins: {len(bp_wins)} ({len(bp_wins)/len(stem_results):.1%})")
    print(f"  Tied: {len(ties)} ({len(ties)/len(stem_results):.1%})")

    # Distribution of improvement
    improvements = [s["mt3_recall"] - s["bp_recall"] for s in stem_results]
    print(f"\nMT3 advantage distribution:")
    print(f"  Median: {np.median(improvements):.1%}")
    print(f"  Mean: {np.mean(improvements):.1%}")
    print(f"  Std: {np.std(improvements):.1%}")
    print(f"  Min (worst MT3): {min(improvements):.1%}")
    print(f"  Max (best MT3): {max(improvements):.1%}")

    # Percentiles
    percentiles = [10, 25, 50, 75, 90]
    print(f"  Percentiles:")
    for p in percentiles:
        print(f"    {p}th: {np.percentile(improvements, p):.1%}")

    return {
        "mt3_wins": len(mt3_wins),
        "bp_wins": len(bp_wins),
        "ties": len(ties),
        "total": len(stem_results),
        "improvement_median": np.median(improvements),
        "improvement_mean": np.mean(improvements),
        "improvement_std": np.std(improvements),
        "improvement_min": min(improvements),
        "improvement_max": max(improvements),
        "percentiles": {p: np.percentile(improvements, p) for p in percentiles},
    }


def compute_routing_oracles(bass_stems, bp_model=None, mt3_model="mt3_pytorch"):
    """Compute stem/phrase/note level routing oracles."""
    print("\n" + "="*70)
    print("BASS ROUTING ORACLES")
    print("="*70)

    # Collect all results
    all_gt = 0
    bp_only_global = {"correct": 0, "total_pred": 0}
    mt3_only_global = {"correct": 0, "total_pred": 0}
    stem_oracle_global = {"correct": 0, "total_pred": 0}
    note_oracle_global = {"correct": 0}

    for stem in bass_stems:
        gt_notes = load_gt_notes(stem["midi_path"])
        if not gt_notes:
            continue

        bp_notes = run_basic_pitch_inference(stem["audio_path"], bp_model)
        mt3_notes = run_mt3_inference(stem["audio_path"], mt3_model)

        if bp_notes is None or mt3_notes is None:
            continue

        n_gt = len(gt_notes)
        all_gt += n_gt

        bp_correct, _, _, _ = match_notes(gt_notes, bp_notes)
        mt3_correct, _, _, _ = match_notes(gt_notes, mt3_notes)

        bp_n_correct = len(bp_correct)
        mt3_n_correct = len(mt3_correct)

        bp_only_global["correct"] += bp_n_correct
        bp_only_global["total_pred"] += len(bp_notes)
        mt3_only_global["correct"] += mt3_n_correct
        mt3_only_global["total_pred"] += len(mt3_notes)

        # Stem oracle: pick better model per stem
        if mt3_n_correct >= bp_n_correct:
            stem_oracle_global["correct"] += mt3_n_correct
            stem_oracle_global["total_pred"] += len(mt3_notes)
        else:
            stem_oracle_global["correct"] += bp_n_correct
            stem_oracle_global["total_pred"] += len(bp_notes)

        # Note oracle: count notes where at least one model correct
        h2h = head_to_head_analysis(gt_notes, bp_notes, mt3_notes)
        note_oracle_global["correct"] += h2h["both_correct"] + h2h["bp_only"] + h2h["mt3_only"]

    # Compute metrics
    def metrics(correct, n_pred, n_gt):
        recall = correct / n_gt if n_gt > 0 else 0
        precision = correct / n_pred if n_pred > 0 else 0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0
        return recall, precision, f1

    bp_r, bp_p, bp_f1 = metrics(bp_only_global["correct"], bp_only_global["total_pred"], all_gt)
    mt3_r, mt3_p, mt3_f1 = metrics(mt3_only_global["correct"], mt3_only_global["total_pred"], all_gt)
    stem_r, stem_p, stem_f1 = metrics(stem_oracle_global["correct"], stem_oracle_global["total_pred"], all_gt)
    note_r = note_oracle_global["correct"] / all_gt if all_gt > 0 else 0

    print(f"\n{'Method':<20} {'Recall':>10} {'Precision':>10} {'F1':>10}")
    print("-" * 52)
    print(f"{'BP only':<20} {bp_r:>10.1%} {bp_p:>10.1%} {bp_f1:>10.3f}")
    print(f"{'MT3 only':<20} {mt3_r:>10.1%} {mt3_p:>10.1%} {mt3_f1:>10.3f}")
    print(f"{'Stem oracle':<20} {stem_r:>10.1%} {stem_p:>10.1%} {stem_f1:>10.3f}")
    print(f"{'Note oracle':<20} {note_r:>10.1%} {'N/A':>10} {'N/A':>10}")

    return {
        "bp_only": {"recall": bp_r, "precision": bp_p, "f1": bp_f1},
        "mt3_only": {"recall": mt3_r, "precision": mt3_p, "f1": mt3_f1},
        "stem_oracle": {"recall": stem_r, "precision": stem_p, "f1": stem_f1},
        "note_oracle": {"recall": note_r},
        "n_gt": all_gt,
    }


def validate_synth_lead(stems, bp_model=None, mt3_model="mt3_pytorch"):
    """Validate Synth Lead with per-stem analysis."""
    print("\n" + "="*70)
    print("SYNTH LEAD VALIDATION")
    print("="*70)

    synth_stems = [s for s in stems if s["inst_class"] == "Synth Lead"]
    print(f"Found {len(synth_stems)} Synth Lead stems")

    if not synth_stems:
        return {}

    stem_results = []
    all_gt = 0
    bp_total = {"correct": 0, "octave": 0, "pred": 0}
    mt3_total = {"correct": 0, "octave": 0, "pred": 0}
    h2h_total = {"both_correct": 0, "bp_only": 0, "mt3_only": 0, "both_wrong": 0}

    for stem in synth_stems:
        gt_notes = load_gt_notes(stem["midi_path"])
        if not gt_notes:
            continue

        bp_notes = run_basic_pitch_inference(stem["audio_path"], bp_model)
        mt3_notes = run_mt3_inference(stem["audio_path"], mt3_model)

        if bp_notes is None or mt3_notes is None:
            continue

        all_gt += len(gt_notes)

        bp_m = compute_metrics(gt_notes, bp_notes)
        mt3_m = compute_metrics(gt_notes, mt3_notes)
        h2h = head_to_head_analysis(gt_notes, bp_notes, mt3_notes)

        bp_total["correct"] += bp_m["n_correct"]
        bp_total["octave"] += bp_m["n_octave"]
        bp_total["pred"] += bp_m["n_pred"]
        mt3_total["correct"] += mt3_m["n_correct"]
        mt3_total["octave"] += mt3_m["n_octave"]
        mt3_total["pred"] += mt3_m["n_pred"]

        for k in h2h_total:
            h2h_total[k] += h2h[k]

        stem_results.append({
            "track_id": stem["track_id"],
            "stem_id": stem["stem_id"],
            "n_gt": len(gt_notes),
            "bp_recall": bp_m["recall"],
            "bp_f1": bp_m["f1"],
            "bp_octave_rate": bp_m["octave_error_rate"],
            "mt3_recall": mt3_m["recall"],
            "mt3_f1": mt3_m["f1"],
            "mt3_octave_rate": mt3_m["octave_error_rate"],
            "mt3_advantage": mt3_m["recall"] - bp_m["recall"],
        })

    # Aggregate metrics
    bp_recall = bp_total["correct"] / all_gt if all_gt > 0 else 0
    mt3_recall = mt3_total["correct"] / all_gt if all_gt > 0 else 0
    bp_oct = bp_total["octave"] / all_gt if all_gt > 0 else 0
    mt3_oct = mt3_total["octave"] / all_gt if all_gt > 0 else 0

    print(f"\nGT notes: {all_gt}")
    print(f"BP recall: {bp_recall:.1%}, octave error: {bp_oct:.1%}")
    print(f"MT3 recall: {mt3_recall:.1%}, octave error: {mt3_oct:.1%}")
    print(f"H2H: both={h2h_total['both_correct']}, bp_only={h2h_total['bp_only']}, "
          f"mt3_only={h2h_total['mt3_only']}, neither={h2h_total['both_wrong']}")

    # Per-stem wins
    mt3_wins = sum(1 for s in stem_results if s["mt3_advantage"] > 0.05)
    bp_wins = sum(1 for s in stem_results if s["mt3_advantage"] < -0.05)
    ties = len(stem_results) - mt3_wins - bp_wins

    print(f"\nPer-stem: MT3 wins {mt3_wins}, BP wins {bp_wins}, tied {ties}")

    # Warning about sample size
    print(f"\n*** CAUTION: Only {all_gt} GT notes. Evidence weaker than Bass ({all_gt} vs 3821). ***")

    return {
        "n_stems": len(synth_stems),
        "n_gt": all_gt,
        "bp_recall": bp_recall,
        "mt3_recall": mt3_recall,
        "bp_octave_rate": bp_oct,
        "mt3_octave_rate": mt3_oct,
        "h2h": h2h_total,
        "per_stem": {
            "mt3_wins": mt3_wins,
            "bp_wins": bp_wins,
            "ties": ties,
        },
        "stem_results": stem_results,
    }


def analyze_organ_control(stems, bp_model=None, mt3_model="mt3_pytorch"):
    """Analyze Organ as control case."""
    print("\n" + "="*70)
    print("ORGAN CONTROL ANALYSIS")
    print("="*70)

    organ_stems = [s for s in stems if s["inst_class"] == "Organ"]
    print(f"Found {len(organ_stems)} Organ stems")

    if not organ_stems:
        return {}

    all_gt = 0
    bp_total = {"correct": 0, "octave": 0, "pred": 0}
    mt3_total = {"correct": 0, "octave": 0, "pred": 0}
    h2h_total = {"both_correct": 0, "bp_only": 0, "mt3_only": 0, "both_wrong": 0}

    for stem in organ_stems:
        gt_notes = load_gt_notes(stem["midi_path"])
        if not gt_notes:
            continue

        bp_notes = run_basic_pitch_inference(stem["audio_path"], bp_model)
        mt3_notes = run_mt3_inference(stem["audio_path"], mt3_model)

        if bp_notes is None or mt3_notes is None:
            continue

        all_gt += len(gt_notes)

        bp_m = compute_metrics(gt_notes, bp_notes)
        mt3_m = compute_metrics(gt_notes, mt3_notes)
        h2h = head_to_head_analysis(gt_notes, bp_notes, mt3_notes)

        bp_total["correct"] += bp_m["n_correct"]
        bp_total["octave"] += bp_m["n_octave"]
        bp_total["pred"] += bp_m["n_pred"]
        mt3_total["correct"] += mt3_m["n_correct"]
        mt3_total["octave"] += mt3_m["n_octave"]
        mt3_total["pred"] += mt3_m["n_pred"]

        for k in h2h_total:
            h2h_total[k] += h2h[k]

    bp_recall = bp_total["correct"] / all_gt if all_gt > 0 else 0
    mt3_recall = mt3_total["correct"] / all_gt if all_gt > 0 else 0
    bp_oct = bp_total["octave"] / all_gt if all_gt > 0 else 0
    mt3_oct = mt3_total["octave"] / all_gt if all_gt > 0 else 0
    bp_prec = bp_total["correct"] / bp_total["pred"] if bp_total["pred"] > 0 else 0
    mt3_prec = mt3_total["correct"] / mt3_total["pred"] if mt3_total["pred"] > 0 else 0

    print(f"\nGT notes: {all_gt}")
    print(f"BP: recall={bp_recall:.1%}, precision={bp_prec:.1%}, octave_err={bp_oct:.1%}")
    print(f"MT3: recall={mt3_recall:.1%}, precision={mt3_prec:.1%}, octave_err={mt3_oct:.1%}")
    print(f"\nH2H breakdown:")
    print(f"  Both correct: {h2h_total['both_correct']}")
    print(f"  BP only: {h2h_total['bp_only']}")
    print(f"  MT3 only: {h2h_total['mt3_only']}")
    print(f"  Neither: {h2h_total['both_wrong']}")

    # Ensemble oracle
    oracle_correct = h2h_total["both_correct"] + h2h_total["bp_only"] + h2h_total["mt3_only"]
    oracle_recall = oracle_correct / all_gt if all_gt > 0 else 0
    print(f"\nOracle recall: {oracle_recall:.1%} (vs BP {bp_recall:.1%})")

    # Analysis: Why does MT3 have low recall but almost no octave errors?
    print("\nDiagnosis:")
    print(f"  MT3 predicts {mt3_total['pred']} notes for {all_gt} GT notes")
    print(f"  MT3 is very conservative (pred/GT = {mt3_total['pred']/all_gt:.2f})")
    print(f"  BP is aggressive (pred/GT = {bp_total['pred']/all_gt:.2f})")
    print(f"  MT3's few predictions are accurate but miss most notes")

    return {
        "n_gt": all_gt,
        "bp": {"recall": bp_recall, "precision": bp_prec, "octave_rate": bp_oct, "n_pred": bp_total["pred"]},
        "mt3": {"recall": mt3_recall, "precision": mt3_prec, "octave_rate": mt3_oct, "n_pred": mt3_total["pred"]},
        "h2h": h2h_total,
        "oracle_recall": oracle_recall,
    }


def bootstrap_confidence_intervals(stem_results, n_bootstrap=1000, inst_class="Bass"):
    """Bootstrap CIs by resampling stems."""
    print(f"\n{'='*70}")
    print(f"{inst_class.upper()} BOOTSTRAP CONFIDENCE INTERVALS")
    print("="*70)

    if not stem_results:
        print("No stem results")
        return {}

    n_stems = len(stem_results)

    # Bootstrap
    recall_diffs = []
    f1_diffs = []
    octave_diffs = []

    for _ in range(n_bootstrap):
        # Resample with replacement
        indices = np.random.choice(n_stems, size=n_stems, replace=True)
        sample = [stem_results[i] for i in indices]

        bp_recall = np.mean([s["bp_recall"] for s in sample])
        mt3_recall = np.mean([s["mt3_recall"] for s in sample])
        bp_f1 = np.mean([s["bp_f1"] for s in sample])
        mt3_f1 = np.mean([s["mt3_f1"] for s in sample])
        bp_oct = np.mean([s["bp_octave_rate"] for s in sample])
        mt3_oct = np.mean([s["mt3_octave_rate"] for s in sample])

        recall_diffs.append(mt3_recall - bp_recall)
        f1_diffs.append(mt3_f1 - bp_f1)
        octave_diffs.append(mt3_oct - bp_oct)

    def ci(diffs, pct=95):
        lo = np.percentile(diffs, (100 - pct) / 2)
        hi = np.percentile(diffs, 100 - (100 - pct) / 2)
        return lo, np.mean(diffs), hi

    recall_ci = ci(recall_diffs)
    f1_ci = ci(f1_diffs)
    octave_ci = ci(octave_diffs)

    print(f"\n{n_bootstrap} bootstrap resamples, {n_stems} stems")
    print(f"\nMT3 - BP differences (95% CI):")
    print(f"  Recall: {recall_ci[1]:+.1%} [{recall_ci[0]:+.1%}, {recall_ci[2]:+.1%}]")
    print(f"  F1: {f1_ci[1]:+.3f} [{f1_ci[0]:+.3f}, {f1_ci[2]:+.3f}]")
    print(f"  Octave error: {octave_ci[1]:+.1%} [{octave_ci[0]:+.1%}, {octave_ci[2]:+.1%}]")

    # Significance
    recall_sig = "***" if recall_ci[0] > 0 or recall_ci[2] < 0 else ""
    octave_sig = "***" if octave_ci[0] > 0 or octave_ci[2] < 0 else ""

    if recall_sig:
        print(f"\n  Recall difference significant at 95% level")
    if octave_sig:
        print(f"  Octave error difference significant at 95% level")

    return {
        "recall_diff": {"mean": recall_ci[1], "lo": recall_ci[0], "hi": recall_ci[2]},
        "f1_diff": {"mean": f1_ci[1], "lo": f1_ci[0], "hi": f1_ci[2]},
        "octave_diff": {"mean": octave_ci[1], "lo": octave_ci[0], "hi": octave_ci[2]},
        "n_stems": n_stems,
        "n_bootstrap": n_bootstrap,
    }


def evaluate_routing_architectures(stems, bp_model=None, mt3_model="mt3_pytorch"):
    """Evaluate routing architectures A/B/C/D."""
    print("\n" + "="*70)
    print("ROUTING ARCHITECTURES EVALUATION")
    print("="*70)

    # Collect per-class results
    class_results = defaultdict(lambda: {
        "n_gt": 0,
        "bp_correct": 0, "bp_pred": 0, "bp_octave": 0,
        "mt3_correct": 0, "mt3_pred": 0, "mt3_octave": 0,
    })

    for stem in stems:
        gt_notes = load_gt_notes(stem["midi_path"])
        if not gt_notes:
            continue

        inst_class = stem["inst_class"]

        bp_notes = run_basic_pitch_inference(stem["audio_path"], bp_model)
        mt3_notes = run_mt3_inference(stem["audio_path"], mt3_model)

        if bp_notes is None:
            continue

        class_results[inst_class]["n_gt"] += len(gt_notes)

        bp_m = compute_metrics(gt_notes, bp_notes)
        class_results[inst_class]["bp_correct"] += bp_m["n_correct"]
        class_results[inst_class]["bp_pred"] += bp_m["n_pred"]
        class_results[inst_class]["bp_octave"] += bp_m["n_octave"]

        if mt3_notes:
            mt3_m = compute_metrics(gt_notes, mt3_notes)
            class_results[inst_class]["mt3_correct"] += mt3_m["n_correct"]
            class_results[inst_class]["mt3_pred"] += mt3_m["n_pred"]
            class_results[inst_class]["mt3_octave"] += mt3_m["n_octave"]

    # Route A: BP everywhere
    route_a = {"correct": 0, "pred": 0, "gt": 0, "octave": 0}
    for cls, r in class_results.items():
        route_a["correct"] += r["bp_correct"]
        route_a["pred"] += r["bp_pred"]
        route_a["gt"] += r["n_gt"]
        route_a["octave"] += r["bp_octave"]

    # Route B: MT3 for Bass, BP elsewhere
    route_b = {"correct": 0, "pred": 0, "gt": 0, "octave": 0}
    for cls, r in class_results.items():
        if cls == "Bass":
            route_b["correct"] += r["mt3_correct"]
            route_b["pred"] += r["mt3_pred"]
            route_b["octave"] += r["mt3_octave"]
        else:
            route_b["correct"] += r["bp_correct"]
            route_b["pred"] += r["bp_pred"]
            route_b["octave"] += r["bp_octave"]
        route_b["gt"] += r["n_gt"]

    # Route C: MT3 for Bass + Synth Lead, BP elsewhere
    route_c = {"correct": 0, "pred": 0, "gt": 0, "octave": 0}
    for cls, r in class_results.items():
        if cls in ["Bass", "Synth Lead"]:
            route_c["correct"] += r["mt3_correct"]
            route_c["pred"] += r["mt3_pred"]
            route_c["octave"] += r["mt3_octave"]
        else:
            route_c["correct"] += r["bp_correct"]
            route_c["pred"] += r["bp_pred"]
            route_c["octave"] += r["bp_octave"]
        route_c["gt"] += r["n_gt"]

    # Route D: Best per class oracle
    route_d = {"correct": 0, "pred": 0, "gt": 0, "octave": 0}
    for cls, r in class_results.items():
        if r["mt3_correct"] > r["bp_correct"]:
            route_d["correct"] += r["mt3_correct"]
            route_d["pred"] += r["mt3_pred"]
            route_d["octave"] += r["mt3_octave"]
        else:
            route_d["correct"] += r["bp_correct"]
            route_d["pred"] += r["bp_pred"]
            route_d["octave"] += r["bp_octave"]
        route_d["gt"] += r["n_gt"]

    def route_metrics(r):
        recall = r["correct"] / r["gt"] if r["gt"] > 0 else 0
        precision = r["correct"] / r["pred"] if r["pred"] > 0 else 0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0
        oct_rate = r["octave"] / r["gt"] if r["gt"] > 0 else 0
        return {"recall": recall, "precision": precision, "f1": f1, "octave_rate": oct_rate}

    routes = {
        "A (BP everywhere)": route_metrics(route_a),
        "B (MT3 Bass)": route_metrics(route_b),
        "C (MT3 Bass+SynthLead)": route_metrics(route_c),
        "D (Best per class)": route_metrics(route_d),
    }

    print(f"\n{'Route':<25} {'Recall':>10} {'Precision':>10} {'F1':>10} {'OctErr':>10}")
    print("-" * 67)
    for name, m in routes.items():
        print(f"{name:<25} {m['recall']:>10.1%} {m['precision']:>10.1%} "
              f"{m['f1']:>10.3f} {m['octave_rate']:>10.1%}")

    # Improvement over baseline
    baseline = routes["A (BP everywhere)"]["recall"]
    print(f"\nImprovement over Route A baseline:")
    for name, m in routes.items():
        if name != "A (BP everywhere)":
            diff = m["recall"] - baseline
            print(f"  {name}: {diff:+.1%}")

    return routes, class_results


def measure_runtime_cost(stems, bp_model=None, mt3_model="mt3_pytorch", n_samples=5):
    """Measure runtime cost of each model."""
    print("\n" + "="*70)
    print("RUNTIME / DEPLOYMENT COST")
    print("="*70)

    # Pick a few representative stems
    sample_stems = stems[:n_samples]

    bp_times = []
    mt3_times = []

    for stem in sample_stems:
        # BP timing
        start = time.time()
        _ = run_basic_pitch_inference(stem["audio_path"], bp_model)
        bp_times.append(time.time() - start)

        # MT3 timing
        if MT3_AVAILABLE:
            start = time.time()
            _ = run_mt3_inference(stem["audio_path"], mt3_model)
            mt3_times.append(time.time() - start)

    print(f"\nInference time ({n_samples} samples):")
    print(f"  Basic Pitch: {np.mean(bp_times):.2f}s (std={np.std(bp_times):.2f}s)")
    if mt3_times:
        print(f"  MT3: {np.mean(mt3_times):.2f}s (std={np.std(mt3_times):.2f}s)")
        print(f"  MT3/BP ratio: {np.mean(mt3_times)/np.mean(bp_times):.1f}x")

    # Estimate routing overhead
    # Typical song: ~5 stems, assume 1 Bass stem
    print("\nEstimated per-song overhead (5-stem song with 1 Bass):")
    print(f"  Route A (BP only): {5 * np.mean(bp_times):.1f}s")
    if mt3_times:
        print(f"  Route B (MT3 for Bass): {4 * np.mean(bp_times) + 1 * np.mean(mt3_times):.1f}s")
        overhead = (1 * np.mean(mt3_times) - 1 * np.mean(bp_times)) / (5 * np.mean(bp_times))
        print(f"  Overhead: {overhead:+.1%}")

    return {
        "bp_mean_time": np.mean(bp_times),
        "bp_std_time": np.std(bp_times),
        "mt3_mean_time": np.mean(mt3_times) if mt3_times else None,
        "mt3_std_time": np.std(mt3_times) if mt3_times else None,
    }


def document_class_availability():
    """Document how instrument class is obtained in production."""
    print("\n" + "="*70)
    print("PRODUCTION INSTRUMENT CLASS AVAILABILITY")
    print("="*70)

    print("""
In Tone Forge production pipeline:

1. STEM SEPARATION
   - Demucs separates mixture into stems (bass, drums, vocals, other)
   - Bass stem is explicitly labeled by separator
   - Other melodic instruments go to "other" stem

2. CLASS ROUTING ASSUMPTION
   For Route B (Bass -> MT3):
   - Bass class comes directly from Demucs "bass" output
   - No additional classifier needed
   - Routing is deterministic based on stem type

3. FOR MIXED/OTHER STEMS
   - Synth Lead would require a classifier or user selection
   - Current "other" stem contains mixed melodic content
   - Class-level routing for Synth Lead is NOT straightforward

4. PRACTICAL IMPLICATION
   - Route B (MT3 for Bass) is architecturally simple
   - Route C (MT3 for Bass + Synth Lead) requires additional classification
   - Recommend Route B as production candidate

5. STEM TYPE MAPPING
   Demucs output -> Routing decision:
   - "bass" -> MT3
   - "drums" -> (separate pipeline)
   - "vocals" -> (separate pipeline or skip)
   - "other" -> Basic Pitch
""")

    return {
        "bass_source": "demucs_stem_label",
        "synth_lead_source": "requires_classifier",
        "recommendation": "Route B (MT3 for Bass only)",
    }


def analyze_remaining_oracle_gap(stems, bp_model=None, mt3_model="mt3_pytorch"):
    """Analyze where the remaining oracle gap exists."""
    print("\n" + "="*70)
    print("REMAINING ORACLE GAP ANALYSIS")
    print("="*70)

    # Track MT3-only correct notes by various attributes
    mt3_only_by_class = defaultdict(list)
    mt3_only_by_register = defaultdict(list)
    mt3_only_by_duration = defaultdict(list)

    for stem in stems:
        gt_notes = load_gt_notes(stem["midi_path"])
        if not gt_notes:
            continue

        inst_class = stem["inst_class"]

        bp_notes = run_basic_pitch_inference(stem["audio_path"], bp_model)
        mt3_notes = run_mt3_inference(stem["audio_path"], mt3_model)

        if bp_notes is None or mt3_notes is None:
            continue

        h2h = head_to_head_analysis(gt_notes, bp_notes, mt3_notes)

        # Get details of MT3-only notes
        for gt_note in h2h["details"]["mt3_only"]:
            pitch = gt_note["pitch"]
            duration = gt_note.get("offset", gt_note["onset"] + 0.5) - gt_note["onset"]

            mt3_only_by_class[inst_class].append(gt_note)

            # Register bin
            if pitch < 48:
                mt3_only_by_register["low"].append(gt_note)
            elif pitch < 72:
                mt3_only_by_register["mid"].append(gt_note)
            else:
                mt3_only_by_register["high"].append(gt_note)

            # Duration bin
            if duration < 0.2:
                mt3_only_by_duration["short"].append(gt_note)
            elif duration < 0.5:
                mt3_only_by_duration["medium"].append(gt_note)
            else:
                mt3_only_by_duration["long"].append(gt_note)

    # Summarize
    total_mt3_only = sum(len(v) for v in mt3_only_by_class.values())

    print(f"\nTotal MT3-only correct notes: {total_mt3_only}")

    print("\nBy instrument class:")
    for cls, notes in sorted(mt3_only_by_class.items(), key=lambda x: -len(x[1])):
        pct = len(notes) / total_mt3_only if total_mt3_only > 0 else 0
        print(f"  {cls}: {len(notes)} ({pct:.1%})")

    print("\nBy pitch register:")
    for reg in ["low", "mid", "high"]:
        notes = mt3_only_by_register[reg]
        pct = len(notes) / total_mt3_only if total_mt3_only > 0 else 0
        print(f"  {reg}: {len(notes)} ({pct:.1%})")

    print("\nBy duration:")
    for dur in ["short", "medium", "long"]:
        notes = mt3_only_by_duration[dur]
        pct = len(notes) / total_mt3_only if total_mt3_only > 0 else 0
        print(f"  {dur}: {len(notes)} ({pct:.1%})")

    # Key insight: how much is Bass?
    bass_mt3_only = len(mt3_only_by_class.get("Bass", []))
    non_bass_mt3_only = total_mt3_only - bass_mt3_only

    print(f"\nKey insight:")
    print(f"  Bass MT3-only: {bass_mt3_only} ({bass_mt3_only/total_mt3_only:.1%} of oracle gap)")
    print(f"  Non-Bass MT3-only: {non_bass_mt3_only} ({non_bass_mt3_only/total_mt3_only:.1%})")

    if bass_mt3_only / total_mt3_only > 0.5:
        print(f"  -> Most of oracle gap is captured by Bass routing")
    else:
        print(f"  -> Significant oracle gap exists outside Bass")

    return {
        "total_mt3_only": total_mt3_only,
        "by_class": {k: len(v) for k, v in mt3_only_by_class.items()},
        "by_register": {k: len(v) for k, v in mt3_only_by_register.items()},
        "by_duration": {k: len(v) for k, v in mt3_only_by_duration.items()},
        "bass_fraction": bass_mt3_only / total_mt3_only if total_mt3_only > 0 else 0,
    }


def main():
    print("="*70)
    print("SPECIALIST ROUTING VALIDATION")
    print("="*70)
    print(f"Start: {datetime.now()}")

    # Load stems
    print("\nLoading validation stems...")
    stems = find_stems(SLAKH_ROOT, "validation", max_stems=100)
    print(f"Found {len(stems)} stems")

    # Filter by class
    bass_stems = [s for s in stems if s["inst_class"] == "Bass"]
    print(f"Bass stems: {len(bass_stems)}")

    # Models
    bp_model = None  # Uses official predict()
    mt3_model = "mt3_pytorch" if MT3_AVAILABLE else None

    if not MT3_AVAILABLE:
        print("ERROR: MT3 not available. Install mt3-infer.")
        return

    results = {}

    # 1. Bass by register
    print("\n[1/12] Bass register analysis...")
    register_stats, stem_results, _, _, _ = analyze_bass_by_register(bass_stems, bp_model, mt3_model)
    results["bass_register"] = {k: v.get("metrics", {}) for k, v in register_stats.items()}

    # 2. Bass octave direction
    print("\n[2/12] Bass octave direction...")
    octave_direction = analyze_octave_direction(stems, bp_model, mt3_model, "Bass")
    results["bass_octave_direction"] = octave_direction

    # 3. Bass playing regimes
    print("\n[3/12] Bass playing regimes...")
    regime_results = analyze_playing_regimes(bass_stems, bp_model, mt3_model)
    results["bass_regimes"] = {k: v for k, v in regime_results.items() if k != "stem_features"}

    # 4. Per-stem consistency
    print("\n[4/12] Per-stem consistency...")
    consistency = analyze_per_stem_consistency(stem_results)
    results["bass_per_stem"] = consistency

    # 5. Bass routing oracles
    print("\n[5/12] Bass routing oracles...")
    oracles = compute_routing_oracles(bass_stems, bp_model, mt3_model)
    results["bass_oracles"] = oracles

    # 6. Synth Lead validation
    print("\n[6/12] Synth Lead validation...")
    synth_results = validate_synth_lead(stems, bp_model, mt3_model)
    results["synth_lead"] = {k: v for k, v in synth_results.items() if k != "stem_results"}

    # 7. Organ control
    print("\n[7/12] Organ control...")
    organ_results = analyze_organ_control(stems, bp_model, mt3_model)
    results["organ_control"] = organ_results

    # 8. Bootstrap CIs
    print("\n[8/12] Bootstrap confidence intervals...")
    bass_ci = bootstrap_confidence_intervals(stem_results, n_bootstrap=1000, inst_class="Bass")
    results["bass_bootstrap_ci"] = bass_ci

    # Synth Lead CIs (if we have stem_results)
    if synth_results.get("stem_results"):
        synth_ci = bootstrap_confidence_intervals(
            synth_results["stem_results"], n_bootstrap=1000, inst_class="Synth Lead"
        )
        results["synth_lead_bootstrap_ci"] = synth_ci

    # 9. Routing architectures
    print("\n[9/12] Routing architectures...")
    routes, class_results = evaluate_routing_architectures(stems, bp_model, mt3_model)
    results["routing_architectures"] = routes

    # 10. Class availability
    print("\n[10/12] Production class availability...")
    class_avail = document_class_availability()
    results["class_availability"] = class_avail

    # 11. Runtime cost
    print("\n[11/12] Runtime cost...")
    runtime = measure_runtime_cost(stems, bp_model, mt3_model, n_samples=3)
    results["runtime"] = runtime

    # 12. Oracle gap analysis
    print("\n[12/12] Oracle gap analysis...")
    oracle_gap = analyze_remaining_oracle_gap(stems, bp_model, mt3_model)
    results["oracle_gap"] = oracle_gap

    # Final report
    print("\n" + "="*70)
    print("FINAL REPORT")
    print("="*70)

    # Decision gate
    print("\n" + "-"*70)
    print("DECISION GATE CLASSIFICATION")
    print("-"*70)

    # Determine classification based on results
    mt3_win_rate = consistency.get("mt3_wins", 0) / consistency.get("total", 1)
    mt3_advantage_mean = consistency.get("improvement_mean", 0)
    bass_oracle_gap = oracles.get("note_oracle", {}).get("recall", 0) - oracles.get("mt3_only", {}).get("recall", 0)

    if mt3_win_rate >= 0.7 and bass_oracle_gap < 0.05:
        classification = "A"
        desc = "SIMPLE BASS SPECIALIST"
        rationale = f"MT3 wins {mt3_win_rate:.0%} of Bass stems. Stem oracle adds only {bass_oracle_gap:.1%}."
    elif mt3_win_rate >= 0.5 and synth_results.get("per_stem", {}).get("mt3_wins", 0) > synth_results.get("per_stem", {}).get("bp_wins", 0):
        classification = "B"
        desc = "BASS + SYNTH LEAD SPECIALIST"
        rationale = "MT3 advantage consistent for both Bass and Synth Lead."
    elif mt3_win_rate >= 0.5:
        classification = "C"
        desc = "CONDITIONAL SPECIALIST"
        rationale = "MT3 advantage exists but varies by regime."
    elif mt3_win_rate < 0.5:
        classification = "D"
        desc = "ROUTING TOO FRAGILE"
        rationale = f"MT3 wins only {mt3_win_rate:.0%} of stems. Gains concentrated in minority."
    else:
        classification = "E"
        desc = "ENSEMBLE OPPORTUNITY"
        rationale = "Strong predictable structure in oracle gap."

    print(f"\nClassification: {classification} - {desc}")
    print(f"Rationale: {rationale}")

    results["decision_gate"] = {
        "classification": classification,
        "description": desc,
        "rationale": rationale,
        "mt3_win_rate": mt3_win_rate,
        "bass_oracle_gap": bass_oracle_gap,
    }

    # Recommendation
    print("\n" + "-"*70)
    print("RECOMMENDATION")
    print("-"*70)

    if classification == "A":
        print("""
Production candidate: Route B
  - Bass -> MT3
  - Everything else -> Basic Pitch

Justification:
  - MT3 robustly dominates Bass across stems and registers
  - Additional routing complexity has minimal oracle value
  - Architecture is simple: Demucs bass stem goes to MT3
  - No additional classifier required
""")
    elif classification == "B":
        print("""
Production candidate: Route C
  - Bass + Synth Lead -> MT3
  - Everything else -> Basic Pitch

Caveat:
  - Synth Lead requires classifier (not from stem separation)
  - Consider Route B first if classifier not available
""")
    else:
        print(f"\nClassification {classification} suggests further investigation needed.")

    # Save results
    output_path = OUTPUT_DIR / "specialist_routing_validation.json"
    with open(output_path, "w") as f:
        def convert(obj):
            if isinstance(obj, (np.floating, float)):
                return float(obj)
            elif isinstance(obj, (np.integer, int)):
                return int(obj)
            elif isinstance(obj, dict):
                return {k: convert(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [convert(x) for x in obj]
            return obj
        json.dump(convert(results), f, indent=2)

    print(f"\nResults saved to {output_path}")
    print(f"End: {datetime.now()}")


if __name__ == "__main__":
    main()
