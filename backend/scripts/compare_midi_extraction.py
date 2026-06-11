#!/usr/bin/env python3
"""Compare MIDI extraction results against ground truth MIDI files.

This script runs the new profile-aware MIDI extraction on audio stems
and compares the results against ground truth MIDI files.

Usage:
    python scripts/compare_midi_extraction.py /path/to/dataset
    python scripts/compare_midi_extraction.py "/Users/mattharvey/Downloads/08 - Demolition Warning"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tone_forge.evaluation.metrics import compute_midi_quality, MIDIQualityMetrics


@dataclass
class StemComparison:
    """Result of comparing extracted MIDI to ground truth for one stem."""
    stem_name: str
    stem_type: str
    audio_path: Path
    ground_truth_path: Path
    profile_used: str
    note_count_extracted: int
    note_count_ground_truth: int
    precision: float
    recall: float
    f1: float
    pitch_accuracy: float
    onset_deviation_ms: float
    error: Optional[str] = None


@dataclass
class DatasetComparison:
    """Result of comparing all stems in a dataset."""
    dataset_name: str
    tempo_bpm: float
    stem_results: List[StemComparison]

    @property
    def overall_f1(self) -> float:
        valid = [s for s in self.stem_results if s.error is None]
        if not valid:
            return 0.0
        return np.mean([s.f1 for s in valid])

    @property
    def overall_precision(self) -> float:
        valid = [s for s in self.stem_results if s.error is None]
        if not valid:
            return 0.0
        return np.mean([s.precision for s in valid])

    @property
    def overall_recall(self) -> float:
        valid = [s for s in self.stem_results if s.error is None]
        if not valid:
            return 0.0
        return np.mean([s.recall for s in valid])


def infer_stem_type(filename: str) -> str:
    """Infer stem type from filename."""
    lower = filename.lower()
    if 'bass' in lower:
        return 'bass'
    elif 'lead' in lower:
        return 'lead'
    elif 'pad' in lower:
        return 'pad'
    elif 'drum' in lower:
        return 'drums'
    elif 'synth' in lower:
        return 'synth'
    elif 'guitar' in lower:
        return 'other'
    elif 'vocal' in lower:
        return 'vocals'
    else:
        return 'other'


def extract_tempo_from_midi_filename(midi_path: Path) -> float:
    """Extract tempo from MIDI filename like 'Song_120bpm.mid'."""
    import re
    match = re.search(r'(\d+)bpm', midi_path.name, re.IGNORECASE)
    if match:
        return float(match.group(1))
    return 120.0  # Default


def load_ground_truth_notes(
    midi_path: Path,
    stem_type: Optional[str] = None,
) -> List[Tuple[int, float, float, int]]:
    """Load notes from ground truth MIDI file, filtered by stem type.

    Returns list of (pitch, start_time, end_time, velocity) tuples.
    """
    import mido

    mid = mido.MidiFile(str(midi_path))
    notes = []

    # Get tempo from MIDI file
    tempo = 500000  # Default 120 BPM
    for track in mid.tracks:
        for msg in track:
            if msg.type == 'set_tempo':
                tempo = msg.tempo
                break

    ticks_per_beat = mid.ticks_per_beat

    # Map stem types to track name patterns
    stem_track_patterns = {
        'bass': ['bass'],
        'lead': ['lead'],
        'pad': ['pad'],
        'drums': ['drum'],
        'synth': ['synth', 'sprinkle'],
        'other': [],  # All tracks
    }

    patterns = stem_track_patterns.get(stem_type, [])

    for track in mid.tracks:
        track_name = track.name.lower() if track.name else ''

        # Filter by stem type if specified
        if patterns:
            if not any(p in track_name for p in patterns):
                continue

        current_time = 0.0
        active_notes: Dict[int, Tuple[float, int]] = {}  # pitch -> (start_time, velocity)

        for msg in track:
            # Convert delta time to seconds
            delta_seconds = mido.tick2second(msg.time, ticks_per_beat, tempo)
            current_time += delta_seconds

            if msg.type == 'note_on' and msg.velocity > 0:
                active_notes[msg.note] = (current_time, msg.velocity)
            elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
                if msg.note in active_notes:
                    start_time, velocity = active_notes.pop(msg.note)
                    notes.append((msg.note, start_time, current_time, velocity))

    return sorted(notes, key=lambda n: n[1])


def extract_midi_with_profile(
    audio_path: Path,
    stem_type: str,
    tempo_bpm: float,
    use_pipeline: bool = True,
) -> Tuple[List[Tuple[int, float, float, int]], str]:
    """Extract MIDI using profile-aware system or new pipelines.

    Returns (notes, profile_name_used).
    """
    import librosa

    # Load audio
    audio, sr = librosa.load(str(audio_path), sr=22050, mono=True)

    if use_pipeline:
        # Use new stem-specific pipelines
        try:
            from tone_forge.midi.pipelines import get_pipeline_for_stem

            pipeline = get_pipeline_for_stem(stem_type)
            result = pipeline.extract(audio, sr, tempo=tempo_bpm)

            notes = [
                (n.pitch, n.start, n.end, n.velocity)
                for n in result.notes
            ]
            return notes, f"pipeline:{pipeline.name}"
        except Exception as e:
            print(f"  Pipeline error: {e}, falling back to profile system")

    # Fall back to profile-aware system
    from tone_forge.midi import (
        MultiPassExtractor,
        create_extractor_for_profile,
        get_profile,
        classify_profile,
    )
    from tone_forge.midi.profiles import get_default_profile_for_stem

    # Try auto-classification first
    try:
        classification = classify_profile(audio, sr, stem_type)
        profile = get_profile(classification.profile_name)
        profile_name = classification.profile_name
    except Exception:
        # Fall back to stem default
        profile = get_default_profile_for_stem(stem_type)
        profile_name = profile.name if profile else "default"

    # Create extractor for profile
    if profile:
        extractor = create_extractor_for_profile(profile)
    else:
        extractor = MultiPassExtractor()

    # Extract
    result = extractor.extract(
        audio,
        sr,
        stem_type=stem_type,
        tempo=tempo_bpm,
        profile=profile,
    )

    # Convert to note tuples
    notes = [
        (n.pitch, n.start, n.end, n.velocity)
        for n in result.notes
    ]

    return notes, profile_name


def compare_notes(
    extracted: List[Tuple[int, float, float, int]],
    ground_truth: List[Tuple[int, float, float, int]],
    onset_tolerance_ms: float = 50.0,
    pitch_tolerance: int = 0,
) -> Tuple[float, float, float, float, float]:
    """Compare extracted notes to ground truth.

    Returns (precision, recall, f1, pitch_accuracy, onset_deviation_ms).
    """
    if not ground_truth:
        return (1.0, 1.0, 1.0, 1.0, 0.0) if not extracted else (0.0, 0.0, 0.0, 0.0, 0.0)
    if not extracted:
        return (0.0, 0.0, 0.0, 0.0, 0.0)

    onset_tolerance_sec = onset_tolerance_ms / 1000.0

    # Match extracted notes to ground truth
    matched_gt = set()
    matched_ext = set()
    onset_deviations = []
    pitch_matches = 0

    for i, ext_note in enumerate(extracted):
        ext_pitch, ext_start, ext_end, ext_vel = ext_note

        best_match = None
        best_distance = float('inf')

        for j, gt_note in enumerate(ground_truth):
            if j in matched_gt:
                continue

            gt_pitch, gt_start, gt_end, gt_vel = gt_note

            # Check pitch match (with tolerance)
            if abs(ext_pitch - gt_pitch) > pitch_tolerance:
                continue

            # Check onset match
            onset_diff = abs(ext_start - gt_start)
            if onset_diff <= onset_tolerance_sec and onset_diff < best_distance:
                best_match = j
                best_distance = onset_diff

        if best_match is not None:
            matched_gt.add(best_match)
            matched_ext.add(i)
            onset_deviations.append(best_distance * 1000)  # Convert to ms

            gt_pitch = ground_truth[best_match][0]
            if ext_pitch == gt_pitch:
                pitch_matches += 1

    true_positives = len(matched_gt)
    false_positives = len(extracted) - len(matched_ext)
    false_negatives = len(ground_truth) - len(matched_gt)

    precision = true_positives / (true_positives + false_positives) if (true_positives + false_positives) > 0 else 0
    recall = true_positives / (true_positives + false_negatives) if (true_positives + false_negatives) > 0 else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0

    pitch_accuracy = pitch_matches / true_positives if true_positives > 0 else 0
    avg_onset_deviation = np.mean(onset_deviations) if onset_deviations else 0

    return precision, recall, f1, pitch_accuracy, avg_onset_deviation


def compare_stem(
    audio_path: Path,
    ground_truth_midi: Path,
    tempo_bpm: float,
) -> StemComparison:
    """Compare a single stem's extracted MIDI to ground truth."""
    stem_name = audio_path.stem
    stem_type = infer_stem_type(stem_name)

    try:
        # Load ground truth filtered by stem type
        gt_notes = load_ground_truth_notes(ground_truth_midi, stem_type=stem_type)

        # Extract MIDI
        extracted_notes, profile_used = extract_midi_with_profile(
            audio_path, stem_type, tempo_bpm
        )

        # Compare
        precision, recall, f1, pitch_acc, onset_dev = compare_notes(
            extracted_notes, gt_notes
        )

        return StemComparison(
            stem_name=stem_name,
            stem_type=stem_type,
            audio_path=audio_path,
            ground_truth_path=ground_truth_midi,
            profile_used=profile_used,
            note_count_extracted=len(extracted_notes),
            note_count_ground_truth=len(gt_notes),
            precision=precision,
            recall=recall,
            f1=f1,
            pitch_accuracy=pitch_acc,
            onset_deviation_ms=onset_dev,
        )

    except Exception as e:
        return StemComparison(
            stem_name=stem_name,
            stem_type=stem_type,
            audio_path=audio_path,
            ground_truth_path=ground_truth_midi,
            profile_used="error",
            note_count_extracted=0,
            note_count_ground_truth=0,
            precision=0,
            recall=0,
            f1=0,
            pitch_accuracy=0,
            onset_deviation_ms=0,
            error=str(e),
        )


def compare_dataset(dataset_path: Path) -> DatasetComparison:
    """Compare all stems in a dataset folder."""
    # Find ground truth MIDI
    midi_files = list(dataset_path.glob("*.mid"))
    if not midi_files:
        raise ValueError(f"No MIDI files found in {dataset_path}")

    ground_truth_midi = midi_files[0]
    tempo_bpm = extract_tempo_from_midi_filename(ground_truth_midi)

    # Find audio stems (exclude click tracks)
    audio_files = [
        f for f in dataset_path.glob("*.wav")
        if not f.name.startswith("_") and "click" not in f.name.lower()
    ]

    if not audio_files:
        raise ValueError(f"No audio files found in {dataset_path}")

    print(f"\n{'='*60}")
    print(f"Dataset: {dataset_path.name}")
    print(f"Ground Truth: {ground_truth_midi.name}")
    print(f"Tempo: {tempo_bpm} BPM")
    print(f"Stems: {len(audio_files)}")
    print(f"{'='*60}")

    results = []
    for audio_path in sorted(audio_files):
        print(f"\nProcessing: {audio_path.name}")
        result = compare_stem(audio_path, ground_truth_midi, tempo_bpm)
        results.append(result)

        if result.error:
            print(f"  ERROR: {result.error}")
        else:
            print(f"  Profile: {result.profile_used}")
            print(f"  Notes: {result.note_count_extracted} extracted / {result.note_count_ground_truth} ground truth")
            print(f"  Precision: {result.precision:.1%}")
            print(f"  Recall: {result.recall:.1%}")
            print(f"  F1: {result.f1:.1%}")
            print(f"  Pitch Accuracy: {result.pitch_accuracy:.1%}")
            print(f"  Onset Deviation: {result.onset_deviation_ms:.1f}ms")

    return DatasetComparison(
        dataset_name=dataset_path.name,
        tempo_bpm=tempo_bpm,
        stem_results=results,
    )


def print_summary(comparisons: List[DatasetComparison]):
    """Print overall summary of all comparisons."""
    print("\n" + "="*70)
    print("SUMMARY")
    print("="*70)

    all_stems = []
    for comp in comparisons:
        all_stems.extend(comp.stem_results)

    valid_stems = [s for s in all_stems if s.error is None]

    if not valid_stems:
        print("No valid results!")
        return

    # Overall metrics
    print(f"\nTotal stems processed: {len(all_stems)}")
    print(f"Successful: {len(valid_stems)}")
    print(f"Errors: {len(all_stems) - len(valid_stems)}")

    overall_f1 = np.mean([s.f1 for s in valid_stems])
    overall_precision = np.mean([s.precision for s in valid_stems])
    overall_recall = np.mean([s.recall for s in valid_stems])

    print(f"\nOverall Precision: {overall_precision:.1%}")
    print(f"Overall Recall: {overall_recall:.1%}")
    print(f"Overall F1: {overall_f1:.1%}")

    # Per-stem-type breakdown
    stem_types = set(s.stem_type for s in valid_stems)
    print("\n--- Per Stem Type ---")
    for st in sorted(stem_types):
        st_stems = [s for s in valid_stems if s.stem_type == st]
        if st_stems:
            st_f1 = np.mean([s.f1 for s in st_stems])
            st_prec = np.mean([s.precision for s in st_stems])
            st_rec = np.mean([s.recall for s in st_stems])
            print(f"  {st:10s}: P={st_prec:.1%} R={st_rec:.1%} F1={st_f1:.1%} (n={len(st_stems)})")

    # Per-profile breakdown
    profiles = set(s.profile_used for s in valid_stems)
    print("\n--- Per Profile ---")
    for prof in sorted(profiles):
        prof_stems = [s for s in valid_stems if s.profile_used == prof]
        if prof_stems:
            prof_f1 = np.mean([s.f1 for s in prof_stems])
            print(f"  {prof:20s}: F1={prof_f1:.1%} (n={len(prof_stems)})")

    # Worst performers
    print("\n--- Worst Performers (by F1) ---")
    sorted_stems = sorted(valid_stems, key=lambda s: s.f1)
    for stem in sorted_stems[:5]:
        print(f"  {stem.stem_name}: F1={stem.f1:.1%} (profile={stem.profile_used})")

    # Best performers
    print("\n--- Best Performers (by F1) ---")
    for stem in sorted_stems[-5:]:
        print(f"  {stem.stem_name}: F1={stem.f1:.1%} (profile={stem.profile_used})")


def main():
    parser = argparse.ArgumentParser(description="Compare MIDI extraction to ground truth")
    parser.add_argument("paths", nargs="+", help="Dataset folder(s) to process")
    parser.add_argument("--onset-tolerance", type=float, default=50.0, help="Onset tolerance in ms")
    args = parser.parse_args()

    comparisons = []

    for path_str in args.paths:
        path = Path(path_str)
        if not path.exists():
            print(f"Warning: {path} does not exist, skipping")
            continue

        if path.is_dir():
            try:
                comp = compare_dataset(path)
                comparisons.append(comp)
            except Exception as e:
                print(f"Error processing {path}: {e}")
        else:
            print(f"Skipping non-directory: {path}")

    if comparisons:
        print_summary(comparisons)


if __name__ == "__main__":
    main()
