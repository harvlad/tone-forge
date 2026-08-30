#!/usr/bin/env python3
"""Verify MT3 timing is correct."""
import sys
sys.path.insert(0, '/Users/mattharvey/Sites/tone-forge/backend')

from pathlib import Path
from scripts.mt3_vs_basic_pitch import run_mt3_inference, load_gt_notes, match_notes

SLAKH_ROOT = Path("/Users/mattharvey/Sites/tone-forge/datasets/slakh2100_flac_redux")

# Test stem
audio_path = SLAKH_ROOT / "validation/Track01512/stems/S07.flac"
midi_path = SLAKH_ROOT / "validation/Track01512/MIDI/S07.mid"

print("Testing MT3 timing...")
print(f"Stem: Track01512/S07 (Piano)")

gt_notes = load_gt_notes(midi_path)
print(f"GT notes: {len(gt_notes)}")

print("\nRunning MT3 inference...")
pred_notes = run_mt3_inference(audio_path, "mt3_pytorch")

if pred_notes is None:
    print("MT3 inference failed!")
    sys.exit(1)

print(f"MT3 pred notes: {len(pred_notes)}")

# Show time ranges
gt_onsets = sorted([n["onset"] for n in gt_notes])
pred_onsets = sorted([n["onset"] for n in pred_notes])
print(f"\nGT time range: {gt_onsets[0]:.2f}s - {gt_onsets[-1]:.2f}s")
print(f"MT3 time range: {pred_onsets[0]:.2f}s - {pred_onsets[-1]:.2f}s")

# Match at various tolerances
for tol in [0.05, 0.1, 0.2, 0.35, 0.5]:
    correct, octave, missed, extra = match_notes(gt_notes, pred_notes, onset_tolerance=tol)
    recall = (len(correct) + len(octave)) / len(gt_notes) if gt_notes else 0
    print(f"@{int(tol*1000):>3}ms: correct={len(correct):>3}, octave={len(octave):>3}, recall={recall:.1%}")

# Show first few notes
print("\n" + "-"*50)
print("First 5 GT notes and nearby MT3 predictions:")
gt_sorted = sorted(gt_notes, key=lambda x: x["onset"])
for i, gt in enumerate(gt_sorted[:5]):
    gt_pitch = gt["pitch"]
    gt_onset = gt["onset"]
    nearby = [(p, abs(p["onset"] - gt_onset)) for p in pred_notes
              if abs(p["onset"] - gt_onset) < 0.5]
    nearby.sort(key=lambda x: x[1])

    print(f"\nGT[{i}]: pitch={gt_pitch} onset={gt_onset:.3f}s")
    if not nearby:
        print("  -> No MT3 predictions within 500ms")
    else:
        for pred, diff in nearby[:3]:
            diff_pitch = pred["pitch"] - gt_pitch
            match = "EXACT" if diff_pitch == 0 else f"OCT{diff_pitch:+d}" if diff_pitch % 12 == 0 else f"+{diff_pitch}"
            print(f"  mt3: pitch={pred['pitch']} onset={pred['onset']:.3f}s Δt={diff*1000:.0f}ms {match}")
