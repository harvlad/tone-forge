#!/usr/bin/env python3
"""
Benchmark Harness Diagnostic

Investigates why MT3 benchmark shows F1=0.001 while Experiment A shows F1=0.37.

Tests:
1. Same predictions, different onset tolerances (50ms vs 350ms)
2. Same predictions, both matchers (experiment_a style vs mt3_benchmark style)
3. Tolerance sweep from 50ms to 500ms
4. End-to-end trace of individual notes
"""
import json
import sys
from pathlib import Path
from collections import defaultdict

import numpy as np
import librosa
import mido
import yaml

# Basic Pitch
from basic_pitch import ICASSP_2022_MODEL_PATH
from basic_pitch.inference import AUDIO_SAMPLE_RATE
from basic_pitch import note_creation as infer
import tensorflow as tf

SLAKH_ROOT = Path("/Users/mattharvey/Sites/tone-forge/datasets/slakh2100_flac_redux")
AUDIO_N_SAMPLES = 43844
ANNOTATIONS_FPS = AUDIO_SAMPLE_RATE / 256

GM_CLASSES = {
    (1, 8): "Piano", (9, 16): "Chromatic Percussion",
    (17, 24): "Organ", (25, 32): "Guitar", (33, 40): "Bass",
    (41, 48): "Strings", (49, 56): "Ensemble",
    (57, 64): "Brass", (65, 72): "Reed", (73, 80): "Pipe",
    (81, 88): "Synth Lead", (89, 96): "Synth Pad",
}


def get_inst_class(program: int) -> str:
    for (lo, hi), name in GM_CLASSES.items():
        if lo <= program + 1 <= hi:
            return name
    return "Unknown"


def find_stems(dataset_dir: Path, split: str, max_stems: int = 10, target_classes=None):
    """Find stems in Slakh dataset."""
    split_dir = dataset_dir / split
    stems = []
    for track_dir in sorted(split_dir.iterdir()):
        if not track_dir.is_dir():
            continue
        metadata_path = track_dir / "metadata.yaml"
        if not metadata_path.exists():
            continue
        try:
            with open(metadata_path) as f:
                meta = yaml.safe_load(f)
        except:
            continue
        stems_dir = track_dir / "stems"
        midi_dir = track_dir / "MIDI"
        for stem_name, stem_info in meta.get("stems", {}).items():
            audio_path = stems_dir / f"{stem_name}.flac"
            midi_path = midi_dir / f"{stem_name}.mid"
            if not audio_path.exists() or not midi_path.exists():
                continue
            inst_class = get_inst_class(stem_info.get("program_num", 0))
            if inst_class == "Unknown":
                continue
            if target_classes and inst_class not in target_classes:
                continue
            stems.append({
                "audio_path": audio_path,
                "midi_path": midi_path,
                "inst_class": inst_class,
                "track_id": track_dir.name,
                "stem_id": stem_name,
            })
            if max_stems and len(stems) >= max_stems:
                return stems
    return stems


def load_gt_notes(midi_path):
    """Load ground truth notes from MIDI."""
    try:
        mid = mido.MidiFile(str(midi_path))
    except:
        return []
    tempo = 500000
    for track in mid.tracks:
        for msg in track:
            if msg.type == 'set_tempo':
                tempo = msg.tempo
                break
    notes = []
    for track in mid.tracks:
        current_time = 0
        active = {}
        for msg in track:
            current_time += mido.tick2second(msg.time, mid.ticks_per_beat, tempo)
            if msg.type == 'note_on' and msg.velocity > 0:
                active[msg.note] = current_time
            elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
                if msg.note in active:
                    start = active.pop(msg.note)
                    notes.append({
                        "pitch": msg.note,
                        "onset": start,
                        "offset": current_time,
                    })
    return notes


def run_basic_pitch_inference(audio_path, model):
    """Run Basic Pitch inference - identical to MT3 benchmark."""
    try:
        audio, _ = librosa.load(str(audio_path), sr=AUDIO_SAMPLE_RATE, mono=True)
    except:
        return None

    n_overlapping_frames = 30
    overlap_len = n_overlapping_frames * 256
    hop_size = AUDIO_N_SAMPLES - overlap_len

    if len(audio) < AUDIO_N_SAMPLES:
        audio = np.pad(audio, (0, AUDIO_N_SAMPLES - len(audio)))

    outputs = {"onset": [], "note": [], "contour": []}

    for start in range(0, len(audio) - AUDIO_N_SAMPLES + 1, hop_size):
        window = audio[start:start + AUDIO_N_SAMPLES]
        window = window.reshape(1, -1, 1).astype(np.float32)
        pred = model(window, training=False)
        for key in outputs:
            outputs[key].append(pred[key].numpy()[0])

    if not outputs["onset"]:
        window = audio[:AUDIO_N_SAMPLES]
        if len(window) < AUDIO_N_SAMPLES:
            window = np.pad(window, (0, AUDIO_N_SAMPLES - len(window)))
        window = window.reshape(1, -1, 1).astype(np.float32)
        pred = model(window, training=False)
        for key in outputs:
            outputs[key].append(pred[key].numpy()[0])

    result = {}
    for key in outputs:
        if len(outputs[key]) == 1:
            result[key] = outputs[key][0]
        else:
            result[key] = np.concatenate(outputs[key], axis=0)

    # Extract notes - same as MT3 benchmark
    pred_notes = infer.output_to_notes_polyphonic(
        frames=result["note"],
        onsets=result["onset"],
        onset_thresh=0.5,
        frame_thresh=0.3,
        min_note_len=int(np.round(127.7 / 1000 * ANNOTATIONS_FPS)),
        infer_onsets=True,
        min_freq=None,
        max_freq=None,
        melodia_trick=True,
        energy_tol=11,
    )

    # Convert to standard format
    notes = []
    for f in pred_notes:
        onset = f[0] / ANNOTATIONS_FPS
        offset = f[1] / ANNOTATIONS_FPS
        pitch = f[2] + 21
        velocity = f[3]
        notes.append({
            "pitch": pitch,
            "onset": onset,
            "offset": offset,
            "velocity": velocity,
        })

    return notes


def match_mt3_style(gt_notes, pred_notes, onset_tolerance=0.05):
    """MT3 benchmark matcher: exact pitch first, then octave."""
    correct = []
    octave = []
    missed = []
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

    return len(correct), len(octave), len(missed)


def match_expa_style(gt_notes, pred_notes, onset_tolerance=0.35):
    """Experiment A matcher: pitch-class first, finds best onset match."""
    # Convert pred to (pitch, onset, offset) tuples
    pred_converted = [(n["pitch"], n["onset"], n["offset"]) for n in pred_notes]
    gt_converted = [(n["pitch"], n["onset"], n["offset"]) for n in gt_notes]

    gt_matched = set()
    pred_matched = set()

    n_correct = 0
    n_octave_error = 0

    # Match pred to GT
    for i, (ep, eo, ee) in enumerate(pred_converted):
        best_match = None
        best_diff = float('inf')

        for j, (gp, go, ge) in enumerate(gt_converted):
            if j in gt_matched:
                continue

            onset_diff = abs(eo - go)
            if onset_diff > onset_tolerance:
                continue

            # Pitch class match
            if (ep % 12) == (gp % 12):
                if onset_diff < best_diff:
                    best_diff = onset_diff
                    best_match = j

        if best_match is not None:
            gt_matched.add(best_match)
            pred_matched.add(i)

            gp = gt_converted[best_match][0]
            if ep == gp:
                n_correct += 1
            else:
                n_octave_error += 1

    n_missed = len(gt_notes) - len(gt_matched)

    return n_correct, n_octave_error, n_missed


def compute_prf(n_correct, n_gt, n_pred):
    """Compute precision, recall, F1."""
    recall = n_correct / n_gt if n_gt > 0 else 0
    precision = n_correct / n_pred if n_pred > 0 else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0
    return precision, recall, f1


def trace_stem(stem, gt_notes, pred_notes, n_examples=20):
    """Detailed trace of GT vs predictions for one stem."""
    print(f"\n{'='*70}")
    print(f"STEM TRACE: {stem['track_id']}/{stem['stem_id']} ({stem['inst_class']})")
    print(f"{'='*70}")
    print(f"GT notes: {len(gt_notes)}, Pred notes: {len(pred_notes)}")

    # Sort both by onset
    gt_sorted = sorted(gt_notes, key=lambda x: x["onset"])
    pred_sorted = sorted(pred_notes, key=lambda x: x["onset"])

    print(f"\nFirst {n_examples} GT notes and nearby predictions:")
    print("-" * 70)

    for i, gt in enumerate(gt_sorted[:n_examples]):
        gt_pitch = gt["pitch"]
        gt_onset = gt["onset"]

        # Find nearby predictions (within 500ms)
        nearby = [(p, abs(p["onset"] - gt_onset)) for p in pred_sorted
                  if abs(p["onset"] - gt_onset) < 0.5]
        nearby.sort(key=lambda x: x[1])

        print(f"\nGT[{i}]: pitch={gt_pitch} ({pitch_name(gt_pitch)}) onset={gt_onset:.3f}s")

        if not nearby:
            print("  -> No predictions within 500ms")
        else:
            for pred, diff in nearby[:3]:
                pitch_diff = pred["pitch"] - gt_pitch
                match_type = "EXACT" if pitch_diff == 0 else \
                             f"OCT{pitch_diff:+d}" if pitch_diff % 12 == 0 else \
                             f"PITCH{pitch_diff:+d}"
                within_50 = "✓ 50ms" if diff <= 0.05 else ""
                within_350 = "✓ 350ms" if diff <= 0.35 else ""
                print(f"  pred: pitch={pred['pitch']} ({pitch_name(pred['pitch'])}) "
                      f"onset={pred['onset']:.3f}s Δt={diff*1000:.0f}ms {match_type} {within_50} {within_350}")


def pitch_name(midi_pitch):
    """Convert MIDI pitch to note name."""
    notes = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
    octave = (midi_pitch // 12) - 1
    note = notes[midi_pitch % 12]
    return f"{note}{octave}"


def tolerance_sweep(gt_notes, pred_notes):
    """Test various onset tolerances."""
    tolerances = [0.025, 0.05, 0.075, 0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.5]

    print("\nTolerance sweep (MT3-style matcher):")
    print("-" * 60)
    print(f"{'Tol (ms)':<10} {'Correct':<10} {'Octave':<10} {'Recall':<10} {'F1':<10}")
    print("-" * 60)

    for tol in tolerances:
        n_correct, n_octave, n_missed = match_mt3_style(gt_notes, pred_notes, tol)
        precision, recall, f1 = compute_prf(n_correct, len(gt_notes), len(pred_notes))
        print(f"{tol*1000:<10.0f} {n_correct:<10} {n_octave:<10} {recall:<10.3f} {f1:<10.3f}")

    print("\nTolerance sweep (ExpA-style matcher):")
    print("-" * 60)
    print(f"{'Tol (ms)':<10} {'Correct':<10} {'Octave':<10} {'Recall':<10} {'F1':<10}")
    print("-" * 60)

    for tol in tolerances:
        n_correct, n_octave, n_missed = match_expa_style(gt_notes, pred_notes, tol)
        precision, recall, f1 = compute_prf(n_correct, len(gt_notes), len(pred_notes))
        print(f"{tol*1000:<10.0f} {n_correct:<10} {n_octave:<10} {recall:<10.3f} {f1:<10.3f}")


def main():
    print("="*70)
    print("BENCHMARK HARNESS DIAGNOSTIC")
    print("="*70)

    # Load model
    print("\nLoading Basic Pitch model...")
    bp_model = tf.keras.models.load_model(str(ICASSP_2022_MODEL_PATH), compile=False)

    # Find diagnostic stems - one from each important class
    target_classes = ["Piano", "Guitar", "Bass", "Reed"]
    print(f"\nFinding stems for classes: {target_classes}")
    stems = find_stems(SLAKH_ROOT, "validation", max_stems=20, target_classes=target_classes)

    # Select one stem per class
    selected = {}
    for stem in stems:
        cls = stem["inst_class"]
        if cls not in selected:
            selected[cls] = stem

    print(f"Selected {len(selected)} stems: {list(selected.keys())}")

    # Aggregate results
    all_gt = []
    all_pred = []

    for cls, stem in selected.items():
        print(f"\n{'='*70}")
        print(f"Processing: {cls}")
        print(f"{'='*70}")

        gt_notes = load_gt_notes(stem["midi_path"])
        pred_notes = run_basic_pitch_inference(stem["audio_path"], bp_model)

        if not gt_notes or pred_notes is None:
            print("  Skipped (no notes or inference failed)")
            continue

        print(f"  GT: {len(gt_notes)} notes, Pred: {len(pred_notes)} notes")

        # Trace this stem
        trace_stem(stem, gt_notes, pred_notes, n_examples=10)

        # Tolerance sweep
        tolerance_sweep(gt_notes, pred_notes)

        # Aggregate
        all_gt.extend(gt_notes)
        all_pred.extend(pred_notes)

        # Compare matchers at both tolerances
        print(f"\n{cls} - Matcher comparison:")
        print("-" * 40)

        for tol in [0.05, 0.35]:
            mt3_c, mt3_o, mt3_m = match_mt3_style(gt_notes, pred_notes, tol)
            exp_c, exp_o, exp_m = match_expa_style(gt_notes, pred_notes, tol)

            _, mt3_r, mt3_f = compute_prf(mt3_c, len(gt_notes), len(pred_notes))
            _, exp_r, exp_f = compute_prf(exp_c, len(gt_notes), len(pred_notes))

            print(f"  @{int(tol*1000)}ms: MT3={mt3_c}/{len(gt_notes)} ({mt3_r:.1%}) "
                  f"ExpA={exp_c}/{len(gt_notes)} ({exp_r:.1%})")

    # Global summary
    print(f"\n{'='*70}")
    print("GLOBAL SUMMARY (all stems)")
    print(f"{'='*70}")
    print(f"Total GT: {len(all_gt)}, Total Pred: {len(all_pred)}")

    print("\nMT3-style matcher:")
    for tol in [0.05, 0.1, 0.2, 0.35, 0.5]:
        c, o, m = match_mt3_style(all_gt, all_pred, tol)
        p, r, f = compute_prf(c, len(all_gt), len(all_pred))
        print(f"  @{int(tol*1000):>3}ms: correct={c:>4} octave={o:>4} "
              f"recall={r:.1%} F1={f:.3f}")

    print("\nExpA-style matcher:")
    for tol in [0.05, 0.1, 0.2, 0.35, 0.5]:
        c, o, m = match_expa_style(all_gt, all_pred, tol)
        p, r, f = compute_prf(c, len(all_gt), len(all_pred))
        print(f"  @{int(tol*1000):>3}ms: correct={c:>4} octave={o:>4} "
              f"recall={r:.1%} F1={f:.3f}")


if __name__ == "__main__":
    main()
