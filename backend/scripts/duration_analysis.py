#!/usr/bin/env python3
"""Deep analysis of duration errors at per-note level.

Determines whether 3.03x mean duration ratio is caused by:
1. Systematic proportional inflation
2. Catastrophically long outlier notes
3. Notes held until next onset (failure to detect decay)
4. Retriggering issues (same pitch repeated)

Captures per-note data for distribution analysis.
"""
import sys
from pathlib import Path
from typing import List, Tuple, Dict, Optional
import mido
import numpy as np
from collections import defaultdict
import json

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tone_forge.midi.gpu_extractor import extract_midi_lead_ensemble


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


def analyze_duration_per_note(
    gt_notes: List[Tuple[int, float, float]],
    ext_notes: List[Tuple[int, float, float]],
    onset_tol: float = 0.35,
) -> Dict:
    """Analyze duration errors at per-note granularity.

    Returns detailed per-note duration data plus categorization.
    """
    if not gt_notes or not ext_notes:
        return None

    # Match notes (same logic as taxonomy benchmark)
    candidates = []
    for i, (ep, eo, ee) in enumerate(ext_notes):
        for j, (gp, go, ge) in enumerate(gt_notes):
            onset_diff = abs(eo - go)
            if onset_diff > onset_tol:
                continue

            pitch_diff = ep - gp
            same_pitch_class = (ep % 12) == (gp % 12)

            if pitch_diff == 0:
                candidates.append((j, i, True, 0, onset_diff, go, ge, eo, ee, gp))
            elif same_pitch_class:
                candidates.append((j, i, False, pitch_diff, onset_diff, go, ge, eo, ee, gp))

    # Sort: exact first, then by onset closeness
    candidates.sort(key=lambda x: (not x[2], x[4]))

    gt_matched = {}
    ext_matched = {}

    note_data = []  # Per-matched-note data

    for gt_idx, ext_idx, is_exact, pitch_diff, onset_diff, go, ge, eo, ee, pitch in candidates:
        if gt_idx in gt_matched or ext_idx in ext_matched:
            continue

        gt_matched[gt_idx] = ext_idx
        ext_matched[ext_idx] = gt_idx

        gt_dur = ge - go
        ext_dur = ee - eo

        if gt_dur > 0:
            ratio = ext_dur / gt_dur
        else:
            ratio = float('inf')

        # Categorize the error
        category = categorize_duration_error(gt_dur, ext_dur, ratio, go, ge, eo, ee, gt_notes, ext_notes, ext_idx)

        note_data.append({
            'pitch': int(pitch),
            'gt_onset': float(go),
            'gt_offset': float(ge),
            'gt_duration': float(gt_dur),
            'ext_onset': float(eo),
            'ext_offset': float(ee),
            'ext_duration': float(ext_dur),
            'duration_ratio': float(ratio) if ratio != float('inf') else 9999.0,
            'onset_error': float(eo - go),
            'offset_error': float(ee - ge),
            'is_exact_pitch': bool(is_exact),
            'pitch_diff': int(pitch_diff),
            'category': category,
        })

    if not note_data:
        return None

    # Aggregate stats
    ratios = [n['duration_ratio'] for n in note_data if n['duration_ratio'] < float('inf')]
    categories = defaultdict(int)
    for n in note_data:
        categories[n['category']] += 1

    return {
        'matched_notes': len(note_data),
        'gt_total': len(gt_notes),
        'ext_total': len(ext_notes),
        'mean_ratio': float(np.mean(ratios)) if ratios else 0,
        'median_ratio': float(np.median(ratios)) if ratios else 0,
        'std_ratio': float(np.std(ratios)) if ratios else 0,
        'min_ratio': float(np.min(ratios)) if ratios else 0,
        'max_ratio': float(np.max(ratios)) if ratios else 0,
        'categories': dict(categories),
        'notes': note_data,  # Per-note data
    }


def categorize_duration_error(
    gt_dur: float,
    ext_dur: float,
    ratio: float,
    go: float,
    ge: float,
    eo: float,
    ee: float,
    gt_notes: List[Tuple[int, float, float]],
    ext_notes: List[Tuple[int, float, float]],
    ext_idx: int,
) -> str:
    """Categorize the duration error for a single note.

    Categories:
    - 'good': ratio 0.8-1.2
    - 'slight_long': ratio 1.2-2.0
    - 'slight_short': ratio 0.5-0.8
    - 'held_to_next': ext offset near next ext onset (held until retrigger)
    - 'catastrophic_long': ratio > 5.0
    - 'truncated': ratio < 0.5
    - 'other': doesn't fit above
    """
    # Good range
    if 0.8 <= ratio <= 1.2:
        return 'good'

    # Check if held to next onset
    ext_pitch = ext_notes[ext_idx][0]
    ext_onset = ext_notes[ext_idx][1]
    ext_offset = ext_notes[ext_idx][2]

    # Find next ext note with same pitch
    next_same_pitch_onset = None
    for i, (p, o, e) in enumerate(ext_notes):
        if i != ext_idx and p == ext_pitch and o > ext_onset:
            if next_same_pitch_onset is None or o < next_same_pitch_onset:
                next_same_pitch_onset = o

    if next_same_pitch_onset is not None:
        # Check if ext offset is close to next onset (within 50ms)
        if abs(ext_offset - next_same_pitch_onset) < 0.05:
            return 'held_to_next'

    # Slight variations
    if 1.2 < ratio <= 2.0:
        return 'slight_long'
    if 0.5 <= ratio < 0.8:
        return 'slight_short'

    # Catastrophic
    if ratio > 5.0:
        return 'catastrophic_long'
    if ratio < 0.5:
        return 'truncated'

    # Moderate long
    if ratio > 2.0:
        return 'long'

    return 'other'


def find_stems(dataset_dir: Path, limit: int = 0) -> List[Dict]:
    """Find melodic stems in dataset."""
    import yaml

    MELODIC_CLASSES = {
        'Guitar', 'Piano', 'Bass', 'Brass', 'Synth Pad', 'Reed', 'Pipe',
        'Organ', 'Synth Lead', 'Strings', 'Strings (continued)',
        'Chromatic Percussion', 'Ethnic',
    }
    EXCLUDED_CLASSES = {'Drums', 'Sound effects', 'Sound Effects', 'Percussive'}

    stems = []
    search_dirs = []

    for subdir in ["train", "validation", "test"]:
        split_dir = dataset_dir / subdir
        if split_dir.exists():
            search_dirs.append(split_dir)

    for search_dir in search_dirs:
        for track_dir in sorted(search_dir.iterdir()):
            if limit > 0 and len(stems) >= limit:
                break
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
                if limit > 0 and len(stems) >= limit:
                    break
                inst_class = stem_info.get('inst_class', '')
                if inst_class in MELODIC_CLASSES and inst_class not in EXCLUDED_CLASSES:
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


def run_duration_analysis(
    dataset_dir: Path,
    output_file: Path,
    limit: int = 100,
):
    """Run per-note duration analysis on subset of stems."""
    stems = find_stems(dataset_dir, limit=limit)

    if not stems:
        print("No stems found!")
        return

    print(f"Analyzing {len(stems)} stems...")

    all_results = []
    all_notes = []  # Flat list of all matched notes

    for i, stem in enumerate(stems):
        if (i + 1) % 10 == 0:
            print(f"  [{i+1}/{len(stems)}]...")

        try:
            gt_notes = load_midi_notes(stem['midi_path'])
        except Exception:
            continue

        if not gt_notes:
            continue

        try:
            ext_notes_raw, _, _, _ = extract_midi_lead_ensemble(str(stem['audio_path']))
            ext_notes = [(n.pitch, n.start, n.end) for n in ext_notes_raw]
        except Exception:
            continue

        result = analyze_duration_per_note(gt_notes, ext_notes)
        if result is None:
            continue

        result['stem'] = f"{stem['track']}/{stem['stem_id']}"
        result['inst_class'] = stem['inst_class']
        result['program'] = stem['program_name']

        all_results.append(result)

        # Add notes with stem info
        for n in result['notes']:
            n['stem'] = result['stem']
            n['inst_class'] = stem['inst_class']
            all_notes.append(n)

    # Save results
    with open(output_file, 'w') as f:
        json.dump({
            'stems': all_results,
            'summary': compute_summary(all_results, all_notes),
        }, f, indent=2)

    print(f"\nSaved to {output_file}")
    print_summary(all_results, all_notes)


def compute_summary(results: List[Dict], notes: List[Dict]) -> Dict:
    """Compute aggregate summary statistics."""
    if not notes:
        return {}

    ratios = [n['duration_ratio'] for n in notes if n['duration_ratio'] < float('inf')]

    # Category counts
    categories = defaultdict(int)
    for n in notes:
        categories[n['category']] += 1

    # By instrument class
    by_class = defaultdict(list)
    for n in notes:
        if n['duration_ratio'] < float('inf'):
            by_class[n['inst_class']].append(n['duration_ratio'])

    class_stats = {}
    for cls, cls_ratios in by_class.items():
        class_stats[cls] = {
            'n': len(cls_ratios),
            'mean': float(np.mean(cls_ratios)),
            'median': float(np.median(cls_ratios)),
            'std': float(np.std(cls_ratios)),
            'p10': float(np.percentile(cls_ratios, 10)),
            'p90': float(np.percentile(cls_ratios, 90)),
        }

    return {
        'total_notes': len(notes),
        'mean_ratio': float(np.mean(ratios)),
        'median_ratio': float(np.median(ratios)),
        'std_ratio': float(np.std(ratios)),
        'percentiles': {
            '10': float(np.percentile(ratios, 10)),
            '25': float(np.percentile(ratios, 25)),
            '50': float(np.percentile(ratios, 50)),
            '75': float(np.percentile(ratios, 75)),
            '90': float(np.percentile(ratios, 90)),
            '95': float(np.percentile(ratios, 95)),
            '99': float(np.percentile(ratios, 99)),
        },
        'categories': dict(categories),
        'by_class': class_stats,
    }


def print_summary(results: List[Dict], notes: List[Dict]):
    """Print analysis summary."""
    if not notes:
        print("No data to summarize")
        return

    print(f"\n{'='*70}")
    print("PER-NOTE DURATION ANALYSIS")
    print(f"{'='*70}")

    ratios = [n['duration_ratio'] for n in notes if n['duration_ratio'] < float('inf')]

    print(f"\nTotal matched notes: {len(notes)}")
    print(f"Mean ratio: {np.mean(ratios):.2f}")
    print(f"Median ratio: {np.median(ratios):.2f}")
    print(f"Std: {np.std(ratios):.2f}")

    print(f"\nPercentiles:")
    for p in [10, 25, 50, 75, 90, 95, 99]:
        print(f"  {p:2d}th: {np.percentile(ratios, p):.2f}")

    # Category breakdown
    print(f"\n{'='*70}")
    print("DURATION ERROR CATEGORIES")
    print(f"{'='*70}")

    categories = defaultdict(int)
    for n in notes:
        categories[n['category']] += 1

    total = len(notes)
    for cat in ['good', 'slight_long', 'long', 'catastrophic_long', 'held_to_next',
                'slight_short', 'truncated', 'other']:
        count = categories[cat]
        pct = count / total * 100
        print(f"  {cat:20s}: {count:5d} ({pct:5.1f}%)")

    # By instrument class
    print(f"\n{'='*70}")
    print("BY INSTRUMENT CLASS")
    print(f"{'='*70}")

    by_class = defaultdict(list)
    for n in notes:
        if n['duration_ratio'] < float('inf'):
            by_class[n['inst_class']].append(n)

    class_stats = []
    for cls, cls_notes in by_class.items():
        ratios = [n['duration_ratio'] for n in cls_notes]
        cats = defaultdict(int)
        for n in cls_notes:
            cats[n['category']] += 1

        class_stats.append({
            'class': cls,
            'n': len(cls_notes),
            'mean': np.mean(ratios),
            'median': np.median(ratios),
            'good_pct': cats['good'] / len(cls_notes) * 100,
            'held_pct': cats['held_to_next'] / len(cls_notes) * 100,
            'catastrophic_pct': cats['catastrophic_long'] / len(cls_notes) * 100,
        })

    class_stats.sort(key=lambda x: -x['mean'])

    print(f"\n{'Class':25s} {'N':>6s} {'Mean':>6s} {'Med':>6s} {'Good%':>6s} {'Held%':>6s} {'Cat%':>6s}")
    print("-" * 75)
    for s in class_stats:
        print(f"{s['class']:25s} {s['n']:6d} {s['mean']:6.2f} {s['median']:6.2f} {s['good_pct']:5.1f}% {s['held_pct']:5.1f}% {s['catastrophic_pct']:5.1f}%")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=100,
                        help="Number of stems to analyze")
    parser.add_argument("--output", type=str, default="/tmp/duration_analysis.json")
    args = parser.parse_args()

    dataset_dir = Path("/Users/mattharvey/Sites/tone-forge/datasets/slakh2100_flac_redux")

    run_duration_analysis(
        dataset_dir,
        Path(args.output),
        limit=args.limit,
    )
