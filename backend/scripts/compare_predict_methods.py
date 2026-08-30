#!/usr/bin/env python3
"""
Compare official Basic Pitch predict() vs custom inference.

Test if timing differences explain the harness discrepancy.
"""
from pathlib import Path
import numpy as np
import mido
import yaml

# Official Basic Pitch
from basic_pitch.inference import predict

SLAKH_ROOT = Path("/Users/mattharvey/Sites/tone-forge/datasets/slakh2100_flac_redux")


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


def match_notes(gt_notes, pred_notes, onset_tol=0.35):
    """Match using ExpA style (350ms, pitch-class first)."""
    pred_converted = [(n["pitch"], n["onset"], n["offset"]) for n in pred_notes]
    gt_converted = [(n["pitch"], n["onset"], n["offset"]) for n in gt_notes]

    gt_matched = set()
    n_correct = 0
    n_octave = 0

    for i, (ep, eo, ee) in enumerate(pred_converted):
        best_match = None
        best_diff = float('inf')

        for j, (gp, go, ge) in enumerate(gt_converted):
            if j in gt_matched:
                continue
            onset_diff = abs(eo - go)
            if onset_diff > onset_tol:
                continue
            if (ep % 12) == (gp % 12):
                if onset_diff < best_diff:
                    best_diff = onset_diff
                    best_match = j

        if best_match is not None:
            gt_matched.add(best_match)
            gp = gt_converted[best_match][0]
            if ep == gp:
                n_correct += 1
            else:
                n_octave += 1

    return n_correct, n_octave, len(gt_notes) - len(gt_matched)


def find_test_stem():
    """Find one test stem."""
    split_dir = SLAKH_ROOT / "validation"
    for track_dir in sorted(split_dir.iterdir()):
        if not track_dir.is_dir():
            continue
        metadata_path = track_dir / "metadata.yaml"
        if not metadata_path.exists():
            continue
        with open(metadata_path) as f:
            meta = yaml.safe_load(f)
        for stem_name, stem_info in meta.get("stems", {}).items():
            audio_path = track_dir / "stems" / f"{stem_name}.flac"
            midi_path = track_dir / "MIDI" / f"{stem_name}.mid"
            if audio_path.exists() and midi_path.exists():
                inst_class = stem_info.get("inst_class", "Unknown")
                if inst_class in ["Piano", "Guitar", "Bass"]:
                    return {
                        "audio_path": audio_path,
                        "midi_path": midi_path,
                        "inst_class": inst_class,
                        "track_id": track_dir.name,
                        "stem_id": stem_name,
                    }
    return None


def main():
    print("="*70)
    print("COMPARING OFFICIAL VS CUSTOM PREDICT")
    print("="*70)

    stem = find_test_stem()
    if not stem:
        print("No test stem found!")
        return

    print(f"\nTest stem: {stem['track_id']}/{stem['stem_id']} ({stem['inst_class']})")

    # Load GT
    gt_notes = load_gt_notes(stem["midi_path"])
    print(f"GT notes: {len(gt_notes)}")

    # Get GT time range
    gt_onsets = sorted([n["onset"] for n in gt_notes])
    print(f"GT time range: {gt_onsets[0]:.2f}s - {gt_onsets[-1]:.2f}s")
    print(f"First 5 GT onsets: {[f'{t:.2f}' for t in gt_onsets[:5]]}")

    # Run official predict
    print("\nRunning official Basic Pitch predict()...")
    model_output, midi_data, note_events = predict(str(stem["audio_path"]))
    print(f"Official pred notes: {len(note_events)}")

    # note_events format: (onset, offset, pitch, amplitude, pitch_bends)
    official_notes = [
        {"pitch": n[2], "onset": n[0], "offset": n[1]}
        for n in note_events
    ]
    official_onsets = sorted([n["onset"] for n in official_notes])
    print(f"Official time range: {official_onsets[0]:.2f}s - {official_onsets[-1]:.2f}s")
    print(f"First 5 official onsets: {[f'{t:.2f}' for t in official_onsets[:5]]}")

    # Match with 350ms tolerance
    n_c, n_o, n_m = match_notes(gt_notes, official_notes, onset_tol=0.35)
    recall = (n_c + n_o) / len(gt_notes) if gt_notes else 0
    print(f"\nOfficial @350ms: correct={n_c}, octave={n_o}, recall={recall:.1%}")

    # Match with 50ms tolerance
    n_c, n_o, n_m = match_notes(gt_notes, official_notes, onset_tol=0.05)
    recall = (n_c + n_o) / len(gt_notes) if gt_notes else 0
    print(f"Official @50ms: correct={n_c}, octave={n_o}, recall={recall:.1%}")

    # Show detailed alignment
    print("\n" + "-"*50)
    print("First 10 GT notes and nearby official predictions:")
    gt_sorted = sorted(gt_notes, key=lambda x: x["onset"])
    for i, gt in enumerate(gt_sorted[:10]):
        gt_pitch = gt["pitch"]
        gt_onset = gt["onset"]
        nearby = [(p, abs(p["onset"] - gt_onset)) for p in official_notes
                  if abs(p["onset"] - gt_onset) < 1.0]
        nearby.sort(key=lambda x: x[1])

        print(f"\nGT[{i}]: pitch={gt_pitch} onset={gt_onset:.3f}s")
        if not nearby:
            print("  -> No predictions within 1s")
        else:
            for pred, diff in nearby[:3]:
                diff_pitch = pred["pitch"] - gt_pitch
                match = "EXACT" if diff_pitch == 0 else f"OCT{diff_pitch:+d}" if diff_pitch % 12 == 0 else f"+{diff_pitch}"
                print(f"  pred: pitch={pred['pitch']} onset={pred['onset']:.3f}s Δt={diff*1000:.0f}ms {match}")


if __name__ == "__main__":
    main()
