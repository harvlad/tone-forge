#!/usr/bin/env python3
"""Quick test of fixed Basic Pitch inference."""
import sys
sys.path.insert(0, '/Users/mattharvey/Sites/tone-forge/backend')

from pathlib import Path
from scripts.mt3_vs_basic_pitch import run_basic_pitch_inference, load_gt_notes, match_notes

SLAKH_ROOT = Path("/Users/mattharvey/Sites/tone-forge/datasets/slakh2100_flac_redux")

# Test stem
audio_path = SLAKH_ROOT / "validation/Track01512/stems/S07.flac"
midi_path = SLAKH_ROOT / "validation/Track01512/MIDI/S07.mid"

print("Testing fixed run_basic_pitch_inference...")

gt_notes = load_gt_notes(midi_path)
print(f"GT notes: {len(gt_notes)}")

pred_notes = run_basic_pitch_inference(audio_path)
print(f"Pred notes: {len(pred_notes)}")

# Match at 50ms
correct, octave, missed, extra = match_notes(gt_notes, pred_notes, onset_tolerance=0.05)
recall = (len(correct) + len(octave)) / len(gt_notes) if gt_notes else 0
print(f"@50ms: correct={len(correct)}, octave={len(octave)}, recall={recall:.1%}")

# Match at 350ms
correct, octave, missed, extra = match_notes(gt_notes, pred_notes, onset_tolerance=0.35)
recall = (len(correct) + len(octave)) / len(gt_notes) if gt_notes else 0
print(f"@350ms: correct={len(correct)}, octave={len(octave)}, recall={recall:.1%}")
