#!/usr/bin/env python3
"""Expanded error taxonomy benchmark for transcription analysis.

Captures detailed failure modes beyond simple F1:
- Onset/offset timing errors
- Octave errors vs pitch-class errors vs exact matches
- Fragmented notes (GT split into multiple extracted)
- Merged notes (multiple GT merged into one extracted)
- Duplicate detections
- Note density comparisons
"""
import sys
from pathlib import Path
from typing import List, Tuple, Dict, Optional, Set, NamedTuple
import yaml
import mido
import numpy as np
from collections import defaultdict
from dataclasses import dataclass, field
import json

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tone_forge.midi.gpu_extractor import extract_midi_lead_ensemble

# Instrument classes
EXCLUDED_CLASSES = {'Drums', 'Sound effects', 'Sound Effects', 'Percussive'}
MELODIC_CLASSES = {
    'Guitar', 'Piano', 'Bass', 'Brass', 'Synth Pad', 'Reed', 'Pipe',
    'Organ', 'Synth Lead', 'Strings', 'Strings (continued)',
    'Chromatic Percussion', 'Ethnic',
}


@dataclass
class NoteMatch:
    """Detailed match between GT and extracted note."""
    gt_idx: int
    ext_idx: int
    gt_pitch: int
    ext_pitch: int
    gt_onset: float
    ext_onset: float
    gt_offset: float
    ext_offset: float
    onset_error: float  # seconds
    offset_error: float  # seconds
    duration_error: float  # ratio
    pitch_error: int  # semitones
    is_exact_pitch: bool
    is_octave_match: bool
    is_pitch_class_match: bool


@dataclass
class ErrorTaxonomy:
    """Comprehensive error analysis for a single stem."""
    # Basic counts
    gt_count: int = 0
    ext_count: int = 0

    # Match types
    exact_matches: int = 0  # Same pitch, within onset tolerance
    octave_matches: int = 0  # Same pitch class, different octave
    pitch_class_only: int = 0  # Would match if octave ignored

    # Errors
    false_positives: int = 0  # Extracted notes with no GT match
    false_negatives: int = 0  # GT notes with no extracted match
    octave_errors: int = 0  # Matched but wrong octave
    octave_error_direction: List[int] = field(default_factory=list)  # +12 or -12

    # Timing errors (for matched notes)
    onset_errors: List[float] = field(default_factory=list)  # seconds
    offset_errors: List[float] = field(default_factory=list)  # seconds
    duration_ratios: List[float] = field(default_factory=list)  # ext/gt

    # Structural errors
    fragmented_gt_notes: int = 0  # GT notes matched by multiple ext
    merged_gt_notes: int = 0  # Multiple GT matched by single ext
    duplicate_extractions: int = 0  # Ext notes too close together

    # Density
    gt_note_density: float = 0.0  # notes per second
    ext_note_density: float = 0.0
    duration: float = 0.0

    # Pitch range
    gt_pitch_min: int = 127
    gt_pitch_max: int = 0
    ext_pitch_min: int = 127
    ext_pitch_max: int = 0

    # Duration stats
    gt_mean_duration: float = 0.0
    ext_mean_duration: float = 0.0
    gt_short_notes: int = 0  # < 100ms
    gt_long_notes: int = 0  # > 1s

    def compute_metrics(self) -> Dict:
        """Compute derived metrics."""
        tp = self.exact_matches + self.octave_matches

        precision = tp / self.ext_count if self.ext_count > 0 else 0
        recall = tp / self.gt_count if self.gt_count > 0 else 0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0

        # Strict F1 (exact pitch only)
        strict_precision = self.exact_matches / self.ext_count if self.ext_count > 0 else 0
        strict_recall = self.exact_matches / self.gt_count if self.gt_count > 0 else 0
        strict_f1 = 2 * strict_precision * strict_recall / (strict_precision + strict_recall) if (strict_precision + strict_recall) > 0 else 0

        # Timing error stats
        mean_onset_error = float(np.mean(np.abs(self.onset_errors))) if self.onset_errors else 0.0
        std_onset_error = float(np.std(self.onset_errors)) if self.onset_errors else 0.0
        mean_offset_error = float(np.mean(np.abs(self.offset_errors))) if self.offset_errors else 0.0
        mean_duration_ratio = float(np.mean(self.duration_ratios)) if self.duration_ratios else 1.0

        # Octave error rate
        octave_error_rate = float(self.octave_errors / tp) if tp > 0 else 0.0

        # Density ratio
        density_ratio = float(self.ext_note_density / self.gt_note_density) if self.gt_note_density > 0 else 0.0

        return {
            # Standard metrics
            'f1': float(f1),
            'precision': float(precision),
            'recall': float(recall),
            'strict_f1': float(strict_f1),
            'strict_precision': float(strict_precision),
            'strict_recall': float(strict_recall),

            # Counts
            'gt_count': int(self.gt_count),
            'ext_count': int(self.ext_count),
            'exact_matches': int(self.exact_matches),
            'octave_matches': int(self.octave_matches),
            'false_positives': int(self.false_positives),
            'false_negatives': int(self.false_negatives),

            # Octave errors
            'octave_errors': int(self.octave_errors),
            'octave_error_rate': float(octave_error_rate),
            'octave_up_errors': int(sum(1 for e in self.octave_error_direction if e > 0)),
            'octave_down_errors': int(sum(1 for e in self.octave_error_direction if e < 0)),

            # Timing
            'mean_onset_error_ms': float(mean_onset_error * 1000),
            'std_onset_error_ms': float(std_onset_error * 1000),
            'mean_offset_error_ms': float(mean_offset_error * 1000),
            'mean_duration_ratio': float(mean_duration_ratio),

            # Structural
            'fragmented_notes': int(self.fragmented_gt_notes),
            'merged_notes': int(self.merged_gt_notes),
            'duplicates': int(self.duplicate_extractions),

            # Density
            'gt_density': float(self.gt_note_density),
            'ext_density': float(self.ext_note_density),
            'density_ratio': float(density_ratio),

            # Duration profile
            'gt_mean_duration_ms': float(self.gt_mean_duration * 1000),
            'ext_mean_duration_ms': float(self.ext_mean_duration * 1000),
            'gt_short_note_ratio': float(self.gt_short_notes / self.gt_count) if self.gt_count > 0 else 0.0,
            'gt_long_note_ratio': float(self.gt_long_notes / self.gt_count) if self.gt_count > 0 else 0.0,

            # Pitch range
            'gt_pitch_range': int(self.gt_pitch_max - self.gt_pitch_min) if self.gt_count > 0 else 0,
            'ext_pitch_range': int(self.ext_pitch_max - self.ext_pitch_min) if self.ext_count > 0 else 0,
            'gt_pitch_min': int(self.gt_pitch_min) if self.gt_count > 0 else 0,
            'gt_pitch_max': int(self.gt_pitch_max) if self.gt_count > 0 else 0,
        }


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


def analyze_errors(
    gt_notes: List[Tuple[int, float, float]],
    ext_notes: List[Tuple[int, float, float]],
    onset_tol: float = 0.35,
) -> ErrorTaxonomy:
    """Perform comprehensive error analysis between GT and extracted notes."""
    taxonomy = ErrorTaxonomy()
    taxonomy.gt_count = len(gt_notes)
    taxonomy.ext_count = len(ext_notes)

    if not gt_notes:
        taxonomy.false_positives = len(ext_notes)
        return taxonomy

    if not ext_notes:
        taxonomy.false_negatives = len(gt_notes)
        return taxonomy

    # Calculate duration from notes
    all_times = [n[1] for n in gt_notes] + [n[2] for n in gt_notes]
    all_times += [n[1] for n in ext_notes] + [n[2] for n in ext_notes]
    taxonomy.duration = max(all_times) - min(all_times) if all_times else 0

    # Note densities
    if taxonomy.duration > 0:
        taxonomy.gt_note_density = len(gt_notes) / taxonomy.duration
        taxonomy.ext_note_density = len(ext_notes) / taxonomy.duration

    # GT note statistics
    gt_durations = [n[2] - n[1] for n in gt_notes]
    taxonomy.gt_mean_duration = np.mean(gt_durations) if gt_durations else 0
    taxonomy.gt_short_notes = sum(1 for d in gt_durations if d < 0.1)
    taxonomy.gt_long_notes = sum(1 for d in gt_durations if d > 1.0)

    for p, o, e in gt_notes:
        taxonomy.gt_pitch_min = min(taxonomy.gt_pitch_min, p)
        taxonomy.gt_pitch_max = max(taxonomy.gt_pitch_max, p)

    # Ext note statistics
    ext_durations = [n[2] - n[1] for n in ext_notes]
    taxonomy.ext_mean_duration = np.mean(ext_durations) if ext_durations else 0

    for p, o, e in ext_notes:
        taxonomy.ext_pitch_min = min(taxonomy.ext_pitch_min, p)
        taxonomy.ext_pitch_max = max(taxonomy.ext_pitch_max, p)

    # Match notes with detailed analysis
    # Priority: exact match > octave match
    # Each GT note can only be matched once (to best ext note)
    # Each ext note can only be matched once (to best GT note)

    gt_matched_to = {}  # gt_idx -> ext_idx (best match)
    ext_matched_to = {}  # ext_idx -> gt_idx (best match)

    # Build candidate matches with scores
    candidates = []  # (gt_idx, ext_idx, is_exact, pitch_diff, onset_diff)

    for i, (ep, eo, ee) in enumerate(ext_notes):
        for j, (gp, go, ge) in enumerate(gt_notes):
            onset_diff = abs(eo - go)
            if onset_diff > onset_tol:
                continue

            pitch_diff = ep - gp
            same_pitch_class = (ep % 12) == (gp % 12)

            if pitch_diff == 0:
                candidates.append((j, i, True, 0, onset_diff, go, ge, eo, ee))
            elif same_pitch_class:
                candidates.append((j, i, False, pitch_diff, onset_diff, go, ge, eo, ee))

    # Sort: exact matches first, then by onset closeness
    candidates.sort(key=lambda x: (not x[2], x[4]))

    # Greedy matching
    for gt_idx, ext_idx, is_exact, pitch_diff, onset_diff, go, ge, eo, ee in candidates:
        if gt_idx in gt_matched_to or ext_idx in ext_matched_to:
            continue  # Already matched

        gt_matched_to[gt_idx] = ext_idx
        ext_matched_to[ext_idx] = gt_idx

        if is_exact:
            taxonomy.exact_matches += 1
        else:
            taxonomy.octave_matches += 1
            taxonomy.octave_errors += 1
            taxonomy.octave_error_direction.append(pitch_diff)

        # Record timing errors
        taxonomy.onset_errors.append(eo - go)
        taxonomy.offset_errors.append(ee - ge)
        gt_dur = ge - go
        ext_dur = ee - eo
        if gt_dur > 0:
            taxonomy.duration_ratios.append(ext_dur / gt_dur)

    # Count unmatched
    taxonomy.false_negatives = taxonomy.gt_count - len(gt_matched_to)
    taxonomy.false_positives = taxonomy.ext_count - len(ext_matched_to)

    # Track multi-match candidates for fragmentation/merge analysis
    # A GT note is "fragmented" if multiple ext notes could have matched it
    # An ext note is "merged" if it could have matched multiple GT notes
    gt_candidate_counts = defaultdict(int)
    ext_candidate_counts = defaultdict(int)
    for gt_idx, ext_idx, *_ in candidates:
        gt_candidate_counts[gt_idx] += 1
        ext_candidate_counts[ext_idx] += 1

    # Fragmented: GT notes that had multiple candidate ext notes
    for gt_idx in gt_matched_to:
        if gt_candidate_counts[gt_idx] > 1:
            taxonomy.fragmented_gt_notes += 1

    # Merged: ext notes that had multiple candidate GT notes
    for ext_idx in ext_matched_to:
        if ext_candidate_counts[ext_idx] > 1:
            taxonomy.merged_gt_notes += 1

    # Detect duplicate extractions (ext notes very close in time with same pitch)
    ext_sorted = sorted(enumerate(ext_notes), key=lambda x: (x[1][0], x[1][1]))  # sort by pitch, then onset
    for i in range(len(ext_sorted) - 1):
        idx1, (p1, o1, e1) = ext_sorted[i]
        idx2, (p2, o2, e2) = ext_sorted[i + 1]
        if p1 == p2 and abs(o1 - o2) < 0.05:  # Same pitch within 50ms
            taxonomy.duplicate_extractions += 1

    return taxonomy


def find_stems(dataset_dir: Path, inst_classes: Optional[Set[str]] = None) -> List[Dict]:
    """Find melodic stems in dataset."""
    if inst_classes is None:
        inst_classes = MELODIC_CLASSES

    stems = []
    search_dirs = []

    for subdir in ["train", "test", "validation"]:
        split_dir = dataset_dir / subdir
        if split_dir.exists():
            search_dirs.append(split_dir)
    if not search_dirs:
        search_dirs = [dataset_dir]

    for search_dir in search_dirs:
        for track_dir in sorted(search_dir.iterdir()):
            if not track_dir.is_dir() or not track_dir.name.startswith("Track"):
                continue

            metadata_path = track_dir / "metadata.yaml"
            if not metadata_path.exists():
                continue

            try:
                with open(metadata_path) as f:
                    metadata = yaml.safe_load(f)
                if not metadata or 'stems' not in metadata:
                    continue
            except Exception:
                continue

            for stem_id, stem_info in metadata.get('stems', {}).items():
                inst_class = stem_info.get('inst_class', '')
                if inst_class in inst_classes and inst_class not in EXCLUDED_CLASSES:
                    audio_path_flac = track_dir / "stems" / f"{stem_id}.flac"
                    audio_path_wav = track_dir / "stems" / f"{stem_id}.wav"
                    midi_path = track_dir / "MIDI" / f"{stem_id}.mid"

                    audio_path = None
                    if audio_path_flac.exists():
                        audio_path = audio_path_flac
                    elif audio_path_wav.exists():
                        audio_path = audio_path_wav

                    if audio_path and midi_path.exists():
                        stems.append({
                            'track': track_dir.name,
                            'stem_id': stem_id,
                            'audio_path': audio_path,
                            'midi_path': midi_path,
                            'inst_class': inst_class,
                            'program_name': stem_info.get('midi_program_name', 'Unknown'),
                        })

    return stems


def run_taxonomy_benchmark(
    dataset_dir: Path,
    output_file: Path,
    limit: int = 0,
    checkpoint_file: str = "/tmp/taxonomy_checkpoint.json",
    resume: bool = False,
):
    """Run expanded error taxonomy benchmark."""
    stems = find_stems(dataset_dir)

    if not stems:
        print("No stems found!")
        return

    if limit > 0:
        stems = stems[:limit]

    print(f"Found {len(stems)} stems")
    print(f"Output: {output_file}")

    # Resume support
    skip_count = 0
    results = []
    if resume and Path(checkpoint_file).exists():
        with open(checkpoint_file) as f:
            checkpoint = json.load(f)
            skip_count = checkpoint.get("processed", 0)
            results = checkpoint.get("results", [])
            print(f"Resuming from {skip_count}")

    for i, stem in enumerate(stems):
        if i < skip_count:
            continue

        print(f"[{i+1}/{len(stems)}] {stem['track']}/{stem['stem_id']} ({stem['inst_class']})...")

        # Load GT
        try:
            gt_notes = load_midi_notes(stem['midi_path'])
        except Exception as e:
            print(f"  SKIP: bad MIDI")
            continue

        if not gt_notes:
            print(f"  SKIP: no GT notes")
            continue

        # Extract
        try:
            ext_notes_raw, _, _, method = extract_midi_lead_ensemble(str(stem['audio_path']))
            ext_notes = [(n.pitch, n.start, n.end) for n in ext_notes_raw]
        except Exception as e:
            print(f"  SKIP: {type(e).__name__}")
            continue

        # Analyze errors
        taxonomy = analyze_errors(gt_notes, ext_notes)
        metrics = taxonomy.compute_metrics()

        # Add metadata
        metrics['track'] = stem['track']
        metrics['stem_id'] = stem['stem_id']
        metrics['inst_class'] = stem['inst_class']
        metrics['program_name'] = stem['program_name']

        results.append(metrics)

        symbol = "✓" if metrics['f1'] >= 0.8 else "✗"
        print(f"  {symbol} F1={metrics['f1']*100:.1f}% oct_err={metrics['octave_error_rate']*100:.0f}% onset={metrics['mean_onset_error_ms']:.0f}ms")

        # Checkpoint every 25
        if (i + 1) % 25 == 0:
            with open(checkpoint_file, 'w') as f:
                json.dump({"processed": i + 1, "results": results}, f)
            print(f"  [checkpoint: {i+1}]")

    # Save results
    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2)

    print(f"\nResults saved to {output_file}")

    # Print summary
    print_taxonomy_summary(results)


def print_taxonomy_summary(results: List[Dict]):
    """Print aggregated error taxonomy summary."""
    if not results:
        return

    print(f"\n{'='*80}")
    print("ERROR TAXONOMY SUMMARY")
    print(f"{'='*80}")

    # Overall stats
    total = len(results)
    passing = sum(1 for r in results if r['f1'] >= 0.8)
    avg_f1 = np.mean([r['f1'] for r in results])
    avg_strict_f1 = np.mean([r['strict_f1'] for r in results])

    print(f"\nOverall: {passing}/{total} passing ({passing/total*100:.1f}%)")
    print(f"Mean F1: {avg_f1*100:.1f}% (strict: {avg_strict_f1*100:.1f}%)")

    # Aggregate error rates
    total_octave_errors = sum(r['octave_errors'] for r in results)
    total_matches = sum(r['exact_matches'] + r['octave_matches'] for r in results)
    total_octave_up = sum(r['octave_up_errors'] for r in results)
    total_octave_down = sum(r['octave_down_errors'] for r in results)

    print(f"\nOctave errors: {total_octave_errors}/{total_matches} ({total_octave_errors/total_matches*100:.1f}% of matches)")
    print(f"  Up (+12): {total_octave_up}  Down (-12): {total_octave_down}")

    # Timing errors
    all_onset_errors = [r['mean_onset_error_ms'] for r in results if r['mean_onset_error_ms'] > 0]
    if all_onset_errors:
        print(f"\nOnset error: {np.mean(all_onset_errors):.1f}ms mean, {np.median(all_onset_errors):.1f}ms median")

    # Duration ratios
    all_dur_ratios = [r['mean_duration_ratio'] for r in results if r['mean_duration_ratio'] > 0]
    if all_dur_ratios:
        print(f"Duration ratio (ext/gt): {np.mean(all_dur_ratios):.2f} mean")

    # Structural errors
    total_fragmented = sum(r['fragmented_notes'] for r in results)
    total_merged = sum(r['merged_notes'] for r in results)
    total_duplicates = sum(r['duplicates'] for r in results)
    print(f"\nStructural: {total_fragmented} fragmented, {total_merged} merged, {total_duplicates} duplicates")

    # By instrument class
    print(f"\n{'='*80}")
    print("BY INSTRUMENT CLASS")
    print(f"{'='*80}")

    by_class = defaultdict(list)
    for r in results:
        by_class[r['inst_class']].append(r)

    # Sort by F1
    class_summaries = []
    for inst_class, class_results in by_class.items():
        n = len(class_results)
        passing = sum(1 for r in class_results if r['f1'] >= 0.8)
        avg_f1 = np.mean([r['f1'] for r in class_results])
        avg_strict = np.mean([r['strict_f1'] for r in class_results])

        # Octave errors for this class
        class_octave = sum(r['octave_errors'] for r in class_results)
        class_matches = sum(r['exact_matches'] + r['octave_matches'] for r in class_results)
        octave_rate = class_octave / class_matches if class_matches > 0 else 0

        # Octave direction
        class_oct_up = sum(r['octave_up_errors'] for r in class_results)
        class_oct_down = sum(r['octave_down_errors'] for r in class_results)

        # Timing
        class_onset = np.mean([r['mean_onset_error_ms'] for r in class_results])

        # Duration
        class_dur_ratio = np.mean([r['mean_duration_ratio'] for r in class_results if r['mean_duration_ratio'] > 0])

        # Density
        class_density_ratio = np.mean([r['density_ratio'] for r in class_results if r['density_ratio'] > 0])

        # Short/long note profile
        class_short = np.mean([r['gt_short_note_ratio'] for r in class_results])
        class_long = np.mean([r['gt_long_note_ratio'] for r in class_results])

        class_summaries.append({
            'class': inst_class,
            'n': n,
            'passing': passing,
            'pass_rate': passing / n,
            'f1': avg_f1,
            'strict_f1': avg_strict,
            'octave_rate': octave_rate,
            'oct_up': class_oct_up,
            'oct_down': class_oct_down,
            'onset_ms': class_onset,
            'dur_ratio': class_dur_ratio,
            'density_ratio': class_density_ratio,
            'short_ratio': class_short,
            'long_ratio': class_long,
        })

    # Sort by F1
    class_summaries.sort(key=lambda x: -x['f1'])

    print(f"\n{'Class':25s} {'Pass':>8s} {'F1':>6s} {'Strict':>6s} {'Oct%':>5s} {'Oct↑':>4s} {'Oct↓':>4s} {'Onset':>6s} {'DurR':>5s} {'DenR':>5s}")
    print("-" * 95)

    for s in class_summaries:
        print(f"{s['class']:25s} {s['passing']:3d}/{s['n']:<4d} {s['f1']*100:5.1f}% {s['strict_f1']*100:5.1f}% {s['octave_rate']*100:4.0f}% {s['oct_up']:4d} {s['oct_down']:4d} {s['onset_ms']:5.0f}ms {s['dur_ratio']:5.2f} {s['density_ratio']:5.2f}")

    # Failure mode hypotheses
    print(f"\n{'='*80}")
    print("FAILURE MODE ANALYSIS")
    print(f"{'='*80}")

    for s in class_summaries:
        if s['f1'] < 0.6:  # Focus on struggling classes
            print(f"\n{s['class']}:")
            issues = []

            if s['octave_rate'] > 0.2:
                direction = "up" if s['oct_up'] > s['oct_down'] else "down"
                issues.append(f"HIGH OCTAVE ERROR ({s['octave_rate']*100:.0f}%, mostly {direction})")

            if s['density_ratio'] < 0.5:
                issues.append(f"UNDER-DETECTION (density ratio {s['density_ratio']:.2f})")
            elif s['density_ratio'] > 1.5:
                issues.append(f"OVER-DETECTION (density ratio {s['density_ratio']:.2f})")

            if s['dur_ratio'] < 0.7:
                issues.append(f"TRUNCATED NOTES (dur ratio {s['dur_ratio']:.2f})")
            elif s['dur_ratio'] > 1.3:
                issues.append(f"EXTENDED NOTES (dur ratio {s['dur_ratio']:.2f})")

            if s['long_ratio'] > 0.3:
                issues.append(f"LONG NOTE HEAVY ({s['long_ratio']*100:.0f}% > 1s)")

            if s['onset_ms'] > 100:
                issues.append(f"TIMING DRIFT (onset error {s['onset_ms']:.0f}ms)")

            if not issues:
                issues.append("No dominant single failure mode identified")

            for issue in issues:
                print(f"  - {issue}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--output", type=str, default="/tmp/error_taxonomy_results.json")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    dataset_dir = Path("/Users/mattharvey/Sites/tone-forge/datasets/slakh2100_flac_redux")

    run_taxonomy_benchmark(
        dataset_dir,
        Path(args.output),
        limit=args.limit,
        resume=args.resume,
    )
