#!/usr/bin/env python3
"""Analyze basic_pitch failures on BabySlakh Synth Lead stems.

Compares GT MIDI with extracted notes to understand failure modes.
"""
import sys
from pathlib import Path
from typing import List, Tuple, Dict
import yaml
import mido
import numpy as np
from collections import Counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def load_midi_notes(midi_path: Path) -> List[Tuple[int, float, float]]:
    """Load notes from MIDI file as (pitch, onset, offset) tuples."""
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


def find_synth_lead_stems(dataset_dir: Path) -> List[Dict]:
    """Find all Synth Lead stems in the dataset."""
    stems = []

    for track_dir in sorted(dataset_dir.iterdir()):
        if not track_dir.is_dir() or not track_dir.name.startswith("Track"):
            continue

        metadata_path = track_dir / "metadata.yaml"
        if not metadata_path.exists():
            continue

        with open(metadata_path) as f:
            metadata = yaml.safe_load(f)

        for stem_id, stem_info in metadata.get('stems', {}).items():
            if stem_info.get('inst_class') == 'Synth Lead':
                audio_path = track_dir / "stems" / f"{stem_id}.wav"
                midi_path = track_dir / "MIDI" / f"{stem_id}.mid"

                if audio_path.exists() and midi_path.exists():
                    stems.append({
                        'track': track_dir.name,
                        'stem_id': stem_id,
                        'audio_path': audio_path,
                        'midi_path': midi_path,
                        'program_name': stem_info.get('midi_program_name', 'Unknown'),
                    })

    return stems


def analyze_pitch_errors(gt_notes, ext_notes, onset_tol=0.35):
    """Analyze pitch errors between GT and extracted notes."""
    errors = {
        'octave_up': 0,  # Extracted +12 semitones
        'octave_down': 0,  # Extracted -12 semitones
        'harmonic_2x': 0,  # Extracted at 2nd harmonic (+12)
        'harmonic_3x': 0,  # Extracted at 3rd harmonic (+19)
        'subharmonic': 0,  # Extracted at subharmonic
        'other': 0,
        'matched': 0,
    }

    gt_by_onset = {}
    for pitch, onset, offset in gt_notes:
        if onset not in gt_by_onset:
            gt_by_onset[onset] = []
        gt_by_onset[onset].append(pitch)

    for ext_pitch, ext_onset, _ in ext_notes:
        matched = False
        for gt_onset, gt_pitches in gt_by_onset.items():
            if abs(ext_onset - gt_onset) <= onset_tol:
                for gt_pitch in gt_pitches:
                    diff = ext_pitch - gt_pitch

                    if diff == 0:
                        errors['matched'] += 1
                        matched = True
                        break
                    elif diff == 12:
                        errors['octave_up'] += 1
                        matched = True
                        break
                    elif diff == -12:
                        errors['octave_down'] += 1
                        matched = True
                        break
                    elif diff == 19:  # Perfect 5th + octave (3rd harmonic)
                        errors['harmonic_3x'] += 1
                        matched = True
                        break
                    elif ext_pitch % 12 == gt_pitch % 12:  # Same pitch class
                        if diff > 0:
                            errors['harmonic_2x'] += 1
                        else:
                            errors['subharmonic'] += 1
                        matched = True
                        break

                if matched:
                    break

        if not matched:
            errors['other'] += 1

    return errors


def analyze_stem(stem: Dict):
    """Analyze a single stem."""
    print(f"\n{'='*60}")
    print(f"{stem['track']}/{stem['stem_id']} ({stem['program_name']})")
    print(f"{'='*60}")

    # Load GT
    gt_notes = load_midi_notes(stem['midi_path'])
    print(f"GT notes: {len(gt_notes)}")

    # Analyze GT pitch distribution
    gt_pitches = [n[0] for n in gt_notes]
    gt_pitch_classes = [p % 12 for p in gt_pitches]
    pitch_class_names = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']

    print(f"GT pitch range: {min(gt_pitches)}-{max(gt_pitches)} (MIDI)")
    print(f"GT pitch classes: {Counter(gt_pitch_classes).most_common(5)}")

    # Calculate GT note density
    if gt_notes:
        duration = max(n[2] for n in gt_notes) - min(n[1] for n in gt_notes)
        density = len(gt_notes) / duration if duration > 0 else 0
        avg_duration = np.mean([n[2] - n[1] for n in gt_notes])
        print(f"GT duration: {duration:.1f}s, density: {density:.1f} notes/sec")
        print(f"GT avg note duration: {avg_duration:.3f}s")

    # Extract with basic_pitch
    try:
        from tone_forge.midi.gpu_extractor import extract_midi_lead_ensemble
        ext_notes_raw, _, _, method = extract_midi_lead_ensemble(str(stem['audio_path']))
        ext_notes = [(n.pitch, n.start, n.end) for n in ext_notes_raw]
        print(f"\nExtracted notes: {len(ext_notes)} (method: {method})")

        if ext_notes:
            ext_pitches = [n[0] for n in ext_notes]
            ext_pitch_classes = [p % 12 for p in ext_pitches]
            print(f"Extracted pitch range: {min(ext_pitches)}-{max(ext_pitches)} (MIDI)")
            print(f"Extracted pitch classes: {Counter(ext_pitch_classes).most_common(5)}")

            ext_avg_duration = np.mean([n[2] - n[1] for n in ext_notes])
            print(f"Extracted avg note duration: {ext_avg_duration:.3f}s")

        # Analyze errors
        errors = analyze_pitch_errors(gt_notes, ext_notes)
        total_ext = len(ext_notes)
        print(f"\nError analysis:")
        print(f"  Matched exact: {errors['matched']} ({100*errors['matched']/total_ext:.0f}%)")
        print(f"  Octave up: {errors['octave_up']} ({100*errors['octave_up']/total_ext:.0f}%)")
        print(f"  Octave down: {errors['octave_down']} ({100*errors['octave_down']/total_ext:.0f}%)")
        print(f"  Harmonic 2x: {errors['harmonic_2x']} ({100*errors['harmonic_2x']/total_ext:.0f}%)")
        print(f"  Harmonic 3x: {errors['harmonic_3x']} ({100*errors['harmonic_3x']/total_ext:.0f}%)")
        print(f"  Subharmonic: {errors['subharmonic']} ({100*errors['subharmonic']/total_ext:.0f}%)")
        print(f"  Other: {errors['other']} ({100*errors['other']/total_ext:.0f}%)")

    except Exception as e:
        print(f"Extraction failed: {e}")


def main():
    dataset_dir = Path("/Users/mattharvey/Sites/tone-forge/datasets/babyslakh_16k")

    if not dataset_dir.exists():
        print(f"Dataset not found: {dataset_dir}")
        sys.exit(1)

    stems = find_synth_lead_stems(dataset_dir)
    print(f"Found {len(stems)} Synth Lead stems")

    for stem in stems:
        analyze_stem(stem)


if __name__ == "__main__":
    main()
