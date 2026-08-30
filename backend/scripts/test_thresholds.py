#!/usr/bin/env python3
"""Test different basic_pitch thresholds on Slakh stems."""
import sys
from pathlib import Path
from typing import List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yaml
import mido
import numpy as np


def load_midi_notes(midi_path: Path) -> List[Tuple[int, float, float]]:
    """Load notes from MIDI file."""
    mid = mido.MidiFile(str(midi_path))
    tempo = 500000
    for track in mid.tracks:
        for msg in track:
            if msg.type == 'set_tempo':
                tempo = msg.tempo
                break

    notes = []
    for track in mid.tracks:
        current_time = 0
        active_notes = {}
        for msg in track:
            current_time += mido.tick2second(msg.time, mid.ticks_per_beat, tempo)
            if msg.type == 'note_on' and msg.velocity > 0:
                active_notes[msg.note] = current_time
            elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
                if msg.note in active_notes:
                    start = active_notes.pop(msg.note)
                    notes.append((msg.note, start, current_time))
    return notes


def compute_f1(gt_notes, ext_notes, onset_tol=0.35, allow_octave=True):
    """Compute F1 with onset tolerance and optional octave equivalence."""
    matched_gt = set()
    matched_ext = set()

    for i, (ep, eo, _) in enumerate(ext_notes):
        for j, (gp, go, _) in enumerate(gt_notes):
            if j in matched_gt:
                continue
            if abs(eo - go) > onset_tol:
                continue
            if allow_octave:
                if ep % 12 != gp % 12:
                    continue
            else:
                if ep != gp:
                    continue
            matched_gt.add(j)
            matched_ext.add(i)
            break

    tp = len(matched_gt)
    fp = len(ext_notes) - len(matched_ext)
    fn = len(gt_notes) - len(matched_gt)

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0

    return f1, precision, recall, len(ext_notes)


def test_thresholds(audio_path: str, gt_notes: List[Tuple[int, float, float]]):
    """Test different threshold combinations."""
    from basic_pitch.inference import predict
    from basic_pitch import ICASSP_2022_MODEL_PATH

    results = []

    # Test different onset/frame threshold combinations
    threshold_combos = [
        (0.3, 0.2),  # Low thresholds (more notes)
        (0.5, 0.3),  # Default
        (0.6, 0.4),  # Higher (fewer notes)
        (0.7, 0.5),  # Even higher
        (0.8, 0.6),  # Very high
    ]

    for onset_thresh, frame_thresh in threshold_combos:
        try:
            _, _, note_events = predict(
                audio_path,
                ICASSP_2022_MODEL_PATH,
                onset_threshold=onset_thresh,
                frame_threshold=frame_thresh,
                minimum_note_length=50.0,
            )
            ext_notes = [(int(p), float(s), float(e)) for s, e, p, a, b in note_events]
            f1, prec, rec, count = compute_f1(gt_notes, ext_notes)
            results.append({
                'onset': onset_thresh,
                'frame': frame_thresh,
                'f1': f1,
                'precision': prec,
                'recall': rec,
                'count': count,
            })
        except Exception as e:
            print(f"  Error with onset={onset_thresh}, frame={frame_thresh}: {e}")

    return results


def main():
    dataset_dir = Path("/Users/mattharvey/Sites/tone-forge/datasets/babyslakh_16k")

    # Focus on the worst case: Track00002/S02 (over-extraction)
    audio_path = dataset_dir / "Track00002" / "stems" / "S02.wav"
    midi_path = dataset_dir / "Track00002" / "MIDI" / "S02.mid"

    gt_notes = load_midi_notes(midi_path)
    print(f"GT notes: {len(gt_notes)}")
    print(f"\nTesting thresholds on Track00002/S02...")

    results = test_thresholds(str(audio_path), gt_notes)

    print("\nResults:")
    print(f"{'Onset':>6} {'Frame':>6} {'F1':>6} {'Prec':>6} {'Rec':>6} {'Count':>6}")
    print("-" * 42)
    for r in results:
        print(f"{r['onset']:>6.1f} {r['frame']:>6.1f} {r['f1']*100:>5.1f}% {r['precision']*100:>5.0f}% {r['recall']*100:>5.0f}% {r['count']:>6}")

    # Test on under-extraction case: Track00019/S13
    print("\n\nTesting thresholds on Track00019/S13 (under-extraction case)...")
    audio_path2 = dataset_dir / "Track00019" / "stems" / "S13.wav"
    midi_path2 = dataset_dir / "Track00019" / "MIDI" / "S13.mid"

    gt_notes2 = load_midi_notes(midi_path2)
    print(f"GT notes: {len(gt_notes2)}")

    results2 = test_thresholds(str(audio_path2), gt_notes2)

    print("\nResults:")
    print(f"{'Onset':>6} {'Frame':>6} {'F1':>6} {'Prec':>6} {'Rec':>6} {'Count':>6}")
    print("-" * 42)
    for r in results2:
        print(f"{r['onset']:>6.1f} {r['frame']:>6.1f} {r['f1']*100:>5.1f}% {r['precision']*100:>5.0f}% {r['recall']*100:>5.0f}% {r['count']:>6}")


if __name__ == "__main__":
    main()
