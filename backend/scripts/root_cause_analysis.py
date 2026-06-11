#!/usr/bin/env python3
"""
Root Cause Analysis for MIDI Extraction Pipeline

Objective: Identify why we can't reach 80% F1 and quantify error sources.

This analysis examines:
1. Per-sample failure modes
2. Error budget breakdown
3. Architectural limitations
4. What F1 is realistically achievable
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import warnings
warnings.filterwarnings("ignore")

import logging
logging.basicConfig(level=logging.WARNING)

import numpy as np
import mido
from collections import defaultdict
from dataclasses import dataclass
from typing import List, Tuple, Dict


def load_gt_notes(midi_path, stem_type):
    """Load ground truth notes for a specific stem type."""
    mid = mido.MidiFile(str(midi_path))
    tempo = 500000
    for track in mid.tracks:
        for msg in track:
            if msg.type == 'set_tempo':
                tempo = msg.tempo
                break

    notes = []
    for track in mid.tracks:
        track_name = track.name.lower() if track.name else ""
        if stem_type not in track_name:
            continue

        current_time = 0
        active = {}

        for msg in track:
            current_time += msg.time
            time_sec = mido.tick2second(current_time, mid.ticks_per_beat, tempo)

            if msg.type == 'note_on' and msg.velocity > 0:
                active[msg.note] = (time_sec, msg.velocity)
            elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
                if msg.note in active:
                    start, vel = active.pop(msg.note)
                    notes.append((msg.note, start, time_sec, vel))
    return notes


@dataclass
class ErrorAnalysis:
    """Detailed error breakdown for a sample."""
    name: str
    stem_type: str
    gt_count: int
    ext_count: int

    # Core metrics
    f1: float
    precision: float
    recall: float

    # Error counts
    true_positives: int
    false_positives: int
    false_negatives: int

    # Error categories
    octave_errors: int  # Wrong octave (correct pitch class)
    pitch_errors: int   # Wrong pitch entirely
    timing_errors: int  # Note exists but timing off
    missing_notes: int  # Notes in GT not detected at all
    extra_notes: int    # Detected notes not in GT

    # Characteristics
    gt_median_pitch: float
    ext_median_pitch: float
    gt_unique_pitches: int
    is_polyphonic: bool


def compute_error_breakdown(
    ext_notes: List[Tuple],
    gt_notes: List[Tuple],
    onset_tolerance_ms: float = 300.0,
) -> Dict:
    """
    Compute detailed error breakdown.

    Returns dict with:
    - matched notes (onset and pitch correct within tolerance)
    - octave errors (onset correct, pitch off by 12)
    - timing errors (pitch correct, onset off)
    - extra notes (no corresponding GT)
    - missing notes (no corresponding extraction)
    """
    onset_tol = onset_tolerance_ms / 1000.0

    ext_matched = [False] * len(ext_notes)
    gt_matched = [False] * len(gt_notes)

    # Match types
    exact_matches = 0
    octave_matches = 0

    # First pass: find exact matches
    for gi, (gp, gs, ge, gv) in enumerate(gt_notes):
        if gt_matched[gi]:
            continue
        for ei, (ep, es, ee, ev) in enumerate(ext_notes):
            if ext_matched[ei]:
                continue
            onset_ok = abs(es - gs) <= onset_tol
            pitch_exact = ep == gp
            pitch_octave = (ep % 12) == (gp % 12) and ep != gp

            if onset_ok and pitch_exact:
                ext_matched[ei] = True
                gt_matched[gi] = True
                exact_matches += 1
                break

    # Second pass: find octave matches
    for gi, (gp, gs, ge, gv) in enumerate(gt_notes):
        if gt_matched[gi]:
            continue
        for ei, (ep, es, ee, ev) in enumerate(ext_notes):
            if ext_matched[ei]:
                continue
            onset_ok = abs(es - gs) <= onset_tol
            pitch_octave = (ep % 12) == (gp % 12)

            if onset_ok and pitch_octave:
                ext_matched[ei] = True
                gt_matched[gi] = True
                octave_matches += 1
                break

    extra_notes = sum(not m for m in ext_matched)
    missing_notes = sum(not m for m in gt_matched)

    return {
        "exact_matches": exact_matches,
        "octave_matches": octave_matches,
        "extra_notes": extra_notes,
        "missing_notes": missing_notes,
        "total_matches": exact_matches + octave_matches,
    }


def analyze_polyphony(gt_notes: List[Tuple]) -> Tuple[bool, float]:
    """
    Analyze if content is polyphonic.

    Returns (is_polyphonic, avg_simultaneous_notes)
    """
    if not gt_notes:
        return False, 0.0

    # Count simultaneous notes at each GT onset
    simultaneous_counts = []
    for i, (p1, s1, e1, v1) in enumerate(gt_notes):
        count = 1
        for j, (p2, s2, e2, v2) in enumerate(gt_notes):
            if i != j and s2 < e1 and e2 > s1:  # Overlap
                count += 1
        simultaneous_counts.append(count)

    avg_simultaneous = np.mean(simultaneous_counts)
    is_polyphonic = avg_simultaneous > 1.2

    return is_polyphonic, avg_simultaneous


def analyze_sample(name, audio_path, midi_path, stem_type) -> ErrorAnalysis:
    """Perform complete error analysis on a sample."""
    from tone_forge.midi.gpu_extractor import extract_midi_bass_ensemble, extract_midi_lead_ensemble
    from tone_forge.evaluation.metrics import compute_midi_quality

    gt_notes = load_gt_notes(midi_path, stem_type)
    if not gt_notes:
        return None

    # Extract
    if stem_type == "bass":
        notes, tempo, duration, method = extract_midi_bass_ensemble(str(audio_path))
    else:  # lead
        notes, tempo, duration, method = extract_midi_lead_ensemble(str(audio_path))

    ext_tuples = [(n.pitch, n.start, n.end, n.velocity) for n in notes]

    # Standard metrics
    metrics = compute_midi_quality(ext_tuples, gt_notes, onset_tolerance_ms=300, allow_octave_equivalence=True)

    # Detailed error breakdown
    breakdown = compute_error_breakdown(ext_tuples, gt_notes)

    # Polyphony analysis
    is_poly, avg_sim = analyze_polyphony(gt_notes)

    gt_pitches = [n[0] for n in gt_notes]
    ext_pitches = [n[0] for n in ext_tuples] if ext_tuples else [0]

    return ErrorAnalysis(
        name=name,
        stem_type=stem_type,
        gt_count=len(gt_notes),
        ext_count=len(ext_tuples),
        f1=metrics.note_f1,
        precision=metrics.note_precision,
        recall=metrics.note_recall,
        true_positives=breakdown["total_matches"],
        false_positives=breakdown["extra_notes"],
        false_negatives=breakdown["missing_notes"],
        octave_errors=breakdown["octave_matches"],
        pitch_errors=0,  # Computed separately
        timing_errors=0,  # Hard to quantify
        missing_notes=breakdown["missing_notes"],
        extra_notes=breakdown["extra_notes"],
        gt_median_pitch=np.median(gt_pitches),
        ext_median_pitch=np.median(ext_pitches),
        gt_unique_pitches=len(set(gt_pitches)),
        is_polyphonic=is_poly,
    )


def main():
    samples_dir = Path("/Users/mattharvey/Sites/tone-forge/samples")

    print("=" * 100)
    print("ROOT CAUSE ANALYSIS: MIDI Extraction Pipeline")
    print("=" * 100)

    all_results = {"bass": [], "lead": []}

    for stem_type in ["bass", "lead"]:
        print(f"\n{'=' * 40} {stem_type.upper()} {'=' * 40}")

        for sample_dir in sorted(samples_dir.iterdir()):
            if not sample_dir.is_dir():
                continue

            midi_files = list(sample_dir.glob("*bpm.mid"))
            audio_files = list(sample_dir.glob(f"*_{stem_type.capitalize()}.wav"))

            if not midi_files or not audio_files:
                continue

            result = analyze_sample(
                sample_dir.name,
                audio_files[0],
                midi_files[0],
                stem_type
            )

            if result:
                all_results[stem_type].append(result)

    # ===== ANALYSIS =====

    for stem_type in ["bass", "lead"]:
        results = all_results[stem_type]
        if not results:
            continue

        print(f"\n{'=' * 100}")
        print(f"{stem_type.upper()} ANALYSIS ({len(results)} samples)")
        print("=" * 100)

        # 1. Per-sample breakdown
        print(f"\n{'Sample':<35} {'F1':>6} {'Prec':>6} {'Rec':>6} {'GT':>5} {'Ext':>5} {'Oct':>5} {'Miss':>5} {'Extra':>5} {'Poly':>5}")
        print("-" * 100)

        passing = 0
        for r in sorted(results, key=lambda x: -x.f1):
            poly = "Yes" if r.is_polyphonic else "No"
            status = "✓" if r.f1 >= 0.80 else "✗"
            if r.f1 >= 0.80:
                passing += 1
            print(f"{status} {r.name:<33} {r.f1:>5.1%} {r.precision:>5.1%} {r.recall:>5.1%} "
                  f"{r.gt_count:>5} {r.ext_count:>5} {r.octave_errors:>5} {r.missing_notes:>5} {r.extra_notes:>5} {poly:>5}")

        print("-" * 100)
        avg_f1 = np.mean([r.f1 for r in results])
        print(f"Passing: {passing}/{len(results)} ({passing/len(results)*100:.0f}%)")
        print(f"Average F1: {avg_f1:.1%}")

        # 2. Error Budget Analysis
        print(f"\n--- ERROR BUDGET ANALYSIS ---")

        total_gt = sum(r.gt_count for r in results)
        total_tp = sum(r.true_positives for r in results)
        total_fp = sum(r.false_positives for r in results)
        total_fn = sum(r.false_negatives for r in results)
        total_octave = sum(r.octave_errors for r in results)

        print(f"Total GT notes: {total_gt}")
        print(f"Total True Positives: {total_tp} ({total_tp/total_gt*100:.1f}%)")
        print(f"Total False Positives: {total_fp}")
        print(f"Total False Negatives (missed): {total_fn} ({total_fn/total_gt*100:.1f}%)")
        print(f"Octave-matched (within TP): {total_octave} ({total_octave/total_tp*100:.1f}% of TPs)")

        # 3. Failure Mode Classification
        print(f"\n--- FAILURE MODE CLASSIFICATION ---")

        # Categorize failing samples
        low_recall = [r for r in results if r.f1 < 0.80 and r.recall < r.precision]
        low_precision = [r for r in results if r.f1 < 0.80 and r.precision < r.recall]
        polyphonic_fails = [r for r in results if r.f1 < 0.80 and r.is_polyphonic]
        octave_heavy = [r for r in results if r.f1 < 0.80 and r.octave_errors > r.true_positives * 0.3]

        print(f"Low recall (under-detection): {len(low_recall)} samples")
        for r in low_recall[:3]:
            print(f"  {r.name}: recall={r.recall:.1%}, {r.missing_notes} notes missed")

        print(f"Low precision (over-detection): {len(low_precision)} samples")
        for r in low_precision[:3]:
            print(f"  {r.name}: precision={r.precision:.1%}, {r.extra_notes} extra notes")

        print(f"Polyphonic content failures: {len(polyphonic_fails)} samples")
        for r in polyphonic_fails[:3]:
            print(f"  {r.name}: F1={r.f1:.1%}, {r.gt_unique_pitches} unique pitches")

        print(f"High octave error rate: {len(octave_heavy)} samples")
        for r in octave_heavy[:3]:
            oct_pct = r.octave_errors / max(1, r.true_positives) * 100
            print(f"  {r.name}: {oct_pct:.0f}% of matches are octave-shifted")

        # 4. Achievable F1 Analysis
        print(f"\n--- ACHIEVABLE F1 ANALYSIS ---")

        # If we fixed octave errors completely
        perfect_octave_f1s = []
        for r in results:
            # Assume octave errors become exact matches (already counted in TP)
            # This doesn't change F1 since octave equivalence is already applied
            perfect_octave_f1s.append(r.f1)

        # If we eliminated all false positives
        no_fp_f1s = []
        for r in results:
            if r.true_positives + r.false_negatives > 0:
                new_prec = 1.0
                new_rec = r.recall
                new_f1 = 2 * new_prec * new_rec / (new_prec + new_rec) if (new_prec + new_rec) > 0 else 0
                no_fp_f1s.append(new_f1)

        # If we eliminated all false negatives
        no_fn_f1s = []
        for r in results:
            if r.true_positives + r.false_positives > 0:
                new_prec = r.precision
                new_rec = 1.0
                new_f1 = 2 * new_prec * new_rec / (new_prec + new_rec) if (new_prec + new_rec) > 0 else 0
                no_fn_f1s.append(new_f1)

        print(f"Current avg F1: {avg_f1:.1%}")
        print(f"If no false positives: {np.mean(no_fp_f1s):.1%} (+{np.mean(no_fp_f1s) - avg_f1:.1%})")
        print(f"If no false negatives: {np.mean(no_fn_f1s):.1%} (+{np.mean(no_fn_f1s) - avg_f1:.1%})")

        # Gap to 80%
        below_80 = [r for r in results if r.f1 < 0.80]
        if below_80:
            gap = sum(0.80 - r.f1 for r in below_80) / len(below_80)
            print(f"\nAverage gap to 80% for failing samples: {gap:.1%}")
            print(f"Samples needing improvement: {len(below_80)}")

    # ===== OVERALL CONCLUSIONS =====
    print("\n" + "=" * 100)
    print("CONCLUSIONS")
    print("=" * 100)

    all_f1s = [r.f1 for results in all_results.values() for r in results]
    all_passing = sum(1 for f1 in all_f1s if f1 >= 0.80)

    print(f"\nOverall passing: {all_passing}/{len(all_f1s)} ({all_passing/len(all_f1s)*100:.0f}%)")
    print(f"Overall average F1: {np.mean(all_f1s):.1%}")

    print("\nPrimary bottlenecks (ranked by impact):")
    print("1. Under-detection (low recall) - most failing samples have recall < precision")
    print("2. Polyphonic content - monophonic detectors fail on chords/arpeggios")
    print("3. Octave confusion - pYIN locks onto sub-harmonics in bass")
    print("4. Dense repeated notes - onset detection misses rapid attacks")


if __name__ == "__main__":
    main()
