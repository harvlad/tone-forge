#!/usr/bin/env python3
"""Run MIDI extraction benchmark against Slakh2100 stems.

Slakh2100 provides perfect ground truth MIDI aligned with synthesized audio.
"""
import sys
from pathlib import Path
from typing import List, Tuple, Dict, Optional, Set
import yaml
import mido
import numpy as np
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tone_forge.midi.gpu_extractor import extract_midi_lead_ensemble

# Instrument classes to exclude (non-melodic)
EXCLUDED_CLASSES = {
    'Drums',
    'Sound effects',
    'Sound Effects',
    'Percussive',
}

# All melodic instrument classes
MELODIC_CLASSES = {
    'Guitar', 'Piano', 'Bass', 'Brass', 'Synth Pad', 'Reed', 'Pipe',
    'Organ', 'Synth Lead', 'Strings', 'Strings (continued)',
    'Chromatic Percussion', 'Ethnic',
}


def extract_with_mt3(audio_path: str):
    """Extract using MT3 model."""
    import subprocess
    import tempfile
    import os

    with tempfile.NamedTemporaryFile(suffix=".mid", delete=False) as tmp:
        tmp_midi = tmp.name

    try:
        result = subprocess.run(
            [
                "/Library/Frameworks/Python.framework/Versions/3.10/bin/python3",
                "-c",
                f"""
import sys
sys.path.insert(0, '/Users/mattharvey/Sites/tone-forge/backend')
from tone_forge.midi.mt3_extractor import extract_midi_mt3
notes, tempo, duration = extract_midi_mt3('{audio_path}', lead_mode=True)
print(f"NOTES={{len(notes)}}")
import mido
mid = mido.MidiFile()
track = mido.MidiTrack()
mid.tracks.append(track)
track.append(mido.MetaMessage('set_tempo', tempo=int(60000000/tempo)))
ticks_per_beat = 480
notes_sorted = sorted(notes, key=lambda n: n.start)
events = []
for n in notes_sorted:
    on_tick = int(n.start * tempo / 60 * ticks_per_beat)
    off_tick = int(n.end * tempo / 60 * ticks_per_beat)
    events.append((on_tick, 'on', n.pitch, n.velocity))
    events.append((off_tick, 'off', n.pitch, 0))
events.sort(key=lambda e: (e[0], 0 if e[1] == 'off' else 1))
last_tick = 0
for tick, typ, pitch, vel in events:
    delta = tick - last_tick
    if typ == 'on':
        track.append(mido.Message('note_on', note=pitch, velocity=vel, time=delta))
    else:
        track.append(mido.Message('note_off', note=pitch, velocity=0, time=delta))
    last_tick = tick
mid.save('{tmp_midi}')
"""
            ],
            capture_output=True,
            text=True,
            timeout=120
        )
        if result.returncode != 0:
            raise RuntimeError(f"MT3 extraction failed: {result.stderr}")

        # Load the MIDI file
        mid = mido.MidiFile(tmp_midi)
        notes = load_midi_notes_from_mido(mid)
        return notes

    finally:
        if os.path.exists(tmp_midi):
            os.unlink(tmp_midi)


def load_midi_notes_from_mido(mid) -> List[Tuple[int, float, float]]:
    """Load notes from mido MidiFile object."""
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


def load_midi_notes(midi_path: Path) -> List[Tuple[int, float, float]]:
    """Load notes from MIDI file as (pitch, onset, offset) tuples."""
    mid = mido.MidiFile(str(midi_path))

    tempo = 500000  # Default
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

    return {'f1': f1, 'precision': precision, 'recall': recall, 'tp': tp, 'fp': fp, 'fn': fn}


def find_stems(dataset_dir: Path, inst_classes: Optional[Set[str]] = None) -> List[Dict]:
    """Find stems in the dataset, optionally filtered by instrument class.

    Args:
        dataset_dir: Path to Slakh dataset
        inst_classes: Set of instrument classes to include. None = all melodic.
    """
    if inst_classes is None:
        inst_classes = MELODIC_CLASSES

    stems = []

    # Handle both flat structure (BabySlakh) and split structure (Slakh2100)
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
                    # Support both WAV (BabySlakh) and FLAC (Slakh2100)
                    audio_path_wav = track_dir / "stems" / f"{stem_id}.wav"
                    audio_path_flac = track_dir / "stems" / f"{stem_id}.flac"
                    midi_path = track_dir / "MIDI" / f"{stem_id}.mid"

                    audio_path = None
                    if audio_path_wav.exists():
                        audio_path = audio_path_wav
                    elif audio_path_flac.exists():
                        audio_path = audio_path_flac

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


def run_benchmark(
    dataset_dir: Path,
    inst_classes: Optional[Set[str]] = None,
    limit: int = 0,
    checkpoint_file: str = "/tmp/slakh_bench_checkpoint.json",
    skip_count: int = 0,
    prev_results: List[Dict] = None,
):
    """Run benchmark on stems.

    Args:
        dataset_dir: Path to Slakh dataset
        inst_classes: Set of instrument classes. None = all melodic.
        limit: Max stems to process (0 = no limit)
        checkpoint_file: Path to save checkpoints for resume
        skip_count: Number of stems to skip (for resume)
        prev_results: Previous results to merge (for resume)
    """
    import json

    stems = find_stems(dataset_dir, inst_classes)

    if not stems:
        print("No stems found!")
        return

    if limit > 0:
        stems = stems[:limit]

    class_desc = "all melodic" if inst_classes is None else ", ".join(sorted(inst_classes))
    print(f"Found {len(stems)} stems ({class_desc})")
    print(f"Using extractor: basic_pitch ensemble")
    if skip_count > 0:
        print(f"Skipping first {skip_count} (already processed)")
    print()

    results = list(prev_results) if prev_results else []
    results_by_class = defaultdict(list)

    # Rebuild results_by_class from prev_results
    for r in results:
        results_by_class[r['inst_class']].append(r)

    for i, stem in enumerate(stems):
        # Skip already processed
        if i < skip_count:
            continue
        print(f"[{i+1}/{len(stems)}] {stem['track']}/{stem['stem_id']} ({stem['inst_class']}: {stem['program_name']})...")

        # Load GT
        try:
            gt_notes = load_midi_notes(stem['midi_path'])
        except Exception as e:
            print(f"  SKIP: bad MIDI ({type(e).__name__})")
            continue
        if not gt_notes:
            print(f"  Skipping - no GT notes")
            continue

        # Extract
        try:
            ext_notes_raw, _, _, method = extract_midi_lead_ensemble(str(stem['audio_path']))
            ext_notes = [(n.pitch, n.start, n.end) for n in ext_notes_raw]
        except Exception as e:
            print(f"  SKIP: {type(e).__name__}")
            continue

        # Compute F1
        result = compute_f1(gt_notes, ext_notes)
        result['track'] = stem['track']
        result['stem_id'] = stem['stem_id']
        result['inst_class'] = stem['inst_class']
        result['program'] = stem['program_name']
        result['gt_count'] = len(gt_notes)
        result['ext_count'] = len(ext_notes)
        results.append(result)
        results_by_class[stem['inst_class']].append(result)

        symbol = "✓" if result['f1'] >= 0.8 else "✗"
        print(f"  {symbol} F1={result['f1']*100:.1f}% (P={result['precision']*100:.0f}%, R={result['recall']*100:.0f}%)")

        # Save checkpoint every 10 stems
        if (i + 1) % 10 == 0:
            with open(checkpoint_file, 'w') as f:
                json.dump({"processed": i + 1, "results": results}, f)
            print(f"  [checkpoint saved: {i+1} processed]")

    # Final checkpoint
    with open(checkpoint_file, 'w') as f:
        json.dump({"processed": len(stems), "results": results, "complete": True}, f)
    print(f"\n[Final checkpoint saved]")

    # Summary
    if results:
        avg_f1 = np.mean([r['f1'] for r in results])
        passing = sum(1 for r in results if r['f1'] >= 0.8)

        print(f"\n{'='*70}")
        print(f"SLAKH BENCHMARK SUMMARY")
        print(f"{'='*70}")
        print(f"Total: {passing}/{len(results)} passing (≥80%), Avg F1: {avg_f1*100:.1f}%")

        print(f"\n{'='*70}")
        print(f"RESULTS BY INSTRUMENT CLASS")
        print(f"{'='*70}")
        for inst_class in sorted(results_by_class.keys()):
            class_results = results_by_class[inst_class]
            class_avg = np.mean([r['f1'] for r in class_results])
            class_pass = sum(1 for r in class_results if r['f1'] >= 0.8)
            print(f"  {inst_class:25s}: {class_pass:3d}/{len(class_results):3d} pass, F1={class_avg*100:5.1f}%")

        print(f"\n{'='*70}")
        print(f"TOP 20 / BOTTOM 20")
        print(f"{'='*70}")
        sorted_results = sorted(results, key=lambda x: -x['f1'])
        for r in sorted_results[:20]:
            symbol = "✓" if r['f1'] >= 0.8 else "✗"
            print(f"  {symbol} {r['f1']*100:5.1f}% - {r['inst_class']:20s} {r['track']}/{r['stem_id']}")
        print("  ...")
        for r in sorted_results[-20:]:
            symbol = "✓" if r['f1'] >= 0.8 else "✗"
            print(f"  {symbol} {r['f1']*100:5.1f}% - {r['inst_class']:20s} {r['track']}/{r['stem_id']}")


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser()
    parser.add_argument("--class", dest="inst_class", type=str, default=None,
                        help="Filter to specific instrument class (e.g., 'Piano', 'Guitar')")
    parser.add_argument("--limit", type=int, default=0,
                        help="Limit number of stems to process (0 = no limit)")
    parser.add_argument("--list-classes", action="store_true",
                        help="List available instrument classes and exit")
    parser.add_argument("--checkpoint", type=str, default="/tmp/slakh_bench_checkpoint.json",
                        help="Checkpoint file for resume support")
    parser.add_argument("--resume", action="store_true",
                        help="Resume from checkpoint")
    args = parser.parse_args()

    dataset_dir = Path("/Users/mattharvey/Sites/tone-forge/datasets/slakh2100_flac_redux")

    if not dataset_dir.exists():
        print(f"Dataset not found: {dataset_dir}")
        sys.exit(1)

    if args.list_classes:
        print("Available melodic instrument classes:")
        for c in sorted(MELODIC_CLASSES):
            print(f"  {c}")
        sys.exit(0)

    inst_classes = None
    if args.inst_class:
        inst_classes = {args.inst_class}

    # Resume support
    skip_count = 0
    prev_results = []
    if args.resume and Path(args.checkpoint).exists():
        with open(args.checkpoint) as f:
            checkpoint = json.load(f)
            skip_count = checkpoint.get("processed", 0)
            prev_results = checkpoint.get("results", [])
            print(f"Resuming from checkpoint: {skip_count} already processed")

    run_benchmark(
        dataset_dir,
        inst_classes=inst_classes,
        limit=args.limit,
        checkpoint_file=args.checkpoint,
        skip_count=skip_count,
        prev_results=prev_results,
    )
