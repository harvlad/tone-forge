#!/usr/bin/env python3
"""
Timing Offset Search

Search for global offset between GT MIDI and Basic Pitch predictions.
Test offsets from -5s to +5s.
"""
import json
from pathlib import Path
from collections import defaultdict

import numpy as np
import librosa
import mido
import yaml

from basic_pitch import ICASSP_2022_MODEL_PATH
from basic_pitch.inference import AUDIO_SAMPLE_RATE
from basic_pitch import note_creation as infer
import tensorflow as tf

SLAKH_ROOT = Path("/Users/mattharvey/Sites/tone-forge/datasets/slakh2100_flac_redux")
AUDIO_N_SAMPLES = 43844
ANNOTATIONS_FPS = AUDIO_SAMPLE_RATE / 256


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
    """Run Basic Pitch inference."""
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

    notes = []
    for f in pred_notes:
        onset = f[0] / ANNOTATIONS_FPS
        offset = f[1] / ANNOTATIONS_FPS
        pitch = f[2] + 21
        notes.append({
            "pitch": pitch,
            "onset": onset,
            "offset": offset,
        })

    return notes


def match_with_offset(gt_notes, pred_notes, offset, onset_tol=0.1):
    """Count matches with GT offset applied."""
    n_correct = 0
    n_octave = 0
    pred_used = set()

    for gt in gt_notes:
        gt_pitch = gt["pitch"]
        gt_onset = gt["onset"] + offset  # Apply offset to GT

        for pi, pred in enumerate(pred_notes):
            if pi in pred_used:
                continue
            if abs(pred["onset"] - gt_onset) > onset_tol:
                continue

            if pred["pitch"] == gt_pitch:
                n_correct += 1
                pred_used.add(pi)
                break
            elif (pred["pitch"] - gt_pitch) % 12 == 0:
                n_octave += 1
                pred_used.add(pi)
                break

    return n_correct, n_octave


def find_best_offset(gt_notes, pred_notes, search_range=(-5, 5), step=0.1):
    """Search for best offset."""
    best_offset = 0
    best_matches = 0
    results = []

    for offset in np.arange(search_range[0], search_range[1] + step, step):
        n_correct, n_octave = match_with_offset(gt_notes, pred_notes, offset)
        total = n_correct + n_octave
        results.append((offset, n_correct, n_octave, total))
        if total > best_matches:
            best_matches = total
            best_offset = offset

    return best_offset, best_matches, results


def find_stems(dataset_dir, split, max_stems=5):
    """Find stems."""
    split_dir = Path(dataset_dir) / split
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
            stems.append({
                "audio_path": audio_path,
                "midi_path": midi_path,
                "track_id": track_dir.name,
                "stem_id": stem_name,
            })
            if max_stems and len(stems) >= max_stems:
                return stems
    return stems


def main():
    print("="*70)
    print("TIMING OFFSET SEARCH")
    print("="*70)

    print("\nLoading Basic Pitch model...")
    bp_model = tf.keras.models.load_model(str(ICASSP_2022_MODEL_PATH), compile=False)

    stems = find_stems(SLAKH_ROOT, "validation", max_stems=10)
    print(f"Found {len(stems)} stems")

    all_offsets = []

    for stem in stems:
        print(f"\n{'-'*50}")
        print(f"Stem: {stem['track_id']}/{stem['stem_id']}")

        gt_notes = load_gt_notes(stem["midi_path"])
        pred_notes = run_basic_pitch_inference(stem["audio_path"], bp_model)

        if not gt_notes or not pred_notes:
            print("  Skipped")
            continue

        print(f"  GT: {len(gt_notes)} notes, Pred: {len(pred_notes)} notes")

        # Show GT and pred time ranges
        gt_start = min(n["onset"] for n in gt_notes)
        gt_end = max(n["onset"] for n in gt_notes)
        pred_start = min(n["onset"] for n in pred_notes)
        pred_end = max(n["onset"] for n in pred_notes)

        print(f"  GT time range: {gt_start:.2f}s - {gt_end:.2f}s")
        print(f"  Pred time range: {pred_start:.2f}s - {pred_end:.2f}s")
        print(f"  Apparent offset: GT starts {gt_start - pred_start:.2f}s after pred")

        # Search for best offset
        best_offset, best_matches, results = find_best_offset(
            gt_notes, pred_notes, search_range=(-30, 30), step=0.5
        )

        print(f"  Best offset: {best_offset:.1f}s ({best_matches} matches)")
        all_offsets.append(best_offset)

        # Fine search around best
        fine_offset, fine_matches, _ = find_best_offset(
            gt_notes, pred_notes,
            search_range=(best_offset - 1, best_offset + 1),
            step=0.05
        )
        print(f"  Fine offset: {fine_offset:.2f}s ({fine_matches} matches)")

        # Show some matches at best offset
        n_c, n_o = match_with_offset(gt_notes, pred_notes, fine_offset, onset_tol=0.1)
        recall = (n_c + n_o) / len(gt_notes) if gt_notes else 0
        print(f"  At offset {fine_offset:.2f}s: {n_c} exact, {n_o} octave = {recall:.1%} recall")

    print(f"\n{'='*70}")
    print("SUMMARY")
    print(f"{'='*70}")
    print(f"Offsets found: {[f'{o:.1f}' for o in all_offsets]}")
    if all_offsets:
        print(f"Mean offset: {np.mean(all_offsets):.2f}s")
        print(f"Median offset: {np.median(all_offsets):.2f}s")
        print(f"Std: {np.std(all_offsets):.2f}s")


if __name__ == "__main__":
    main()
