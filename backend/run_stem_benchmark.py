#!/usr/bin/env python3
"""Run MIDI benchmark for a specific stem type.

Usage:
    python run_stem_benchmark.py drums   # Run drums benchmark
    python run_stem_benchmark.py lead    # Run lead benchmark
    python run_stem_benchmark.py bass    # Run bass benchmark
"""
from __future__ import annotations

import io
import base64
import logging
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import mido
import numpy as np

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent))

from tone_forge.evaluation.metrics import compute_midi_quality

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s"
)
logger = logging.getLogger(__name__)

# Silence noisy loggers
logging.getLogger("numba").setLevel(logging.WARNING)
logging.getLogger("torch").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("basic_pitch").setLevel(logging.WARNING)
logging.getLogger("tone_forge").setLevel(logging.WARNING)


# Track name patterns for each stem type
STEM_TRACK_PATTERNS = {
    "bass": ["bass"],
    "lead": ["lead"],
    "pads": ["pad"],
    "drums": ["drum", "kick", "snare", "hat", "cymbal", "tom", "perc"],
}


def load_filtered_midi_notes(
    midi_path: Path,
    stem_type: str,
) -> List[Tuple[int, float, float, int]]:
    """Load notes from MIDI file, filtered by stem type."""
    mid = mido.MidiFile(str(midi_path))
    patterns = STEM_TRACK_PATTERNS.get(stem_type.lower(), [stem_type.lower()])

    # Get tempo from all tracks first
    tempo = 500000
    for track in mid.tracks:
        for msg in track:
            if msg.type == 'set_tempo':
                tempo = msg.tempo
                break

    notes = []
    for track in mid.tracks:
        track_name = track.name.lower() if track.name else ""

        # Check if track name matches any pattern for this stem type
        matches = any(pattern in track_name for pattern in patterns)
        if not matches:
            continue

        # Extract notes from this track
        current_time = 0
        active_notes: Dict[int, Tuple[float, int]] = {}

        for msg in track:
            current_time += msg.time
            time_sec = mido.tick2second(current_time, mid.ticks_per_beat, tempo)

            if msg.type == 'note_on' and msg.velocity > 0:
                active_notes[msg.note] = (time_sec, msg.velocity)
            elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
                if msg.note in active_notes:
                    start, velocity = active_notes.pop(msg.note)
                    notes.append((msg.note, start, time_sec, velocity))

    return notes


def parse_extracted_midi(result: dict) -> List[Tuple[int, float, float, int]]:
    """Parse extracted MIDI from API result."""
    midi_bytes = base64.b64decode(result['content'])
    mid = mido.MidiFile(file=io.BytesIO(midi_bytes))

    # Get tempo from all tracks first
    tempo = 500000
    for track in mid.tracks:
        for msg in track:
            if msg.type == 'set_tempo':
                tempo = msg.tempo
                break

    notes = []
    for track in mid.tracks:
        current_time = 0
        note_starts = {}

        for msg in track:
            current_time += msg.time

            if msg.type == 'note_on' and msg.velocity > 0:
                time_sec = mido.tick2second(current_time, mid.ticks_per_beat, tempo)
                note_starts[msg.note] = (time_sec, msg.velocity)
            elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
                if msg.note in note_starts:
                    time_sec = mido.tick2second(current_time, mid.ticks_per_beat, tempo)
                    start, vel = note_starts[msg.note]
                    notes.append((msg.note, start, time_sec, vel))
                    del note_starts[msg.note]

    return notes


def discover_samples(samples_dir: Path, stem_type: str) -> List[dict]:
    """Discover samples for a specific stem type."""
    samples = []
    stem_types = [stem_type.capitalize()]

    for track_dir in sorted(samples_dir.iterdir()):
        if not track_dir.is_dir() or track_dir.name.startswith("."):
            continue

        # Find MIDI file
        midi_files = list(track_dir.glob("*bpm.mid")) + list(track_dir.glob("*bpm.midi"))
        if not midi_files:
            continue

        midi_file = midi_files[0]
        track_prefix = midi_file.stem.rsplit("_", 1)[0]

        for st in stem_types:
            stem_file = track_dir / f"{track_prefix}_{st}.wav"
            if not stem_file.exists():
                alt_stems = list(track_dir.glob(f"*_{st}.wav"))
                if alt_stems:
                    stem_file = alt_stems[0]
                else:
                    continue

            # Load ground truth notes
            gt_notes = load_filtered_midi_notes(midi_file, st)
            if not gt_notes:
                continue

            samples.append({
                "name": track_dir.name,
                "audio_path": stem_file,
                "midi_path": midi_file,
                "gt_notes": gt_notes,
                "stem_type": st.lower(),
            })

    return samples


def extract_midi(audio_path: Path, stem_type: str) -> List[Tuple[int, float, float, int]]:
    """Extract MIDI using the GPU extractor."""
    from tone_forge.midi.gpu_extractor import (
        extract_midi_bass_ensemble,
        extract_midi_lead_ensemble,
    )

    if stem_type == "bass":
        notes, tempo, duration, method = extract_midi_bass_ensemble(str(audio_path))
    elif stem_type == "lead":
        notes, tempo, duration, method = extract_midi_lead_ensemble(str(audio_path))
    elif stem_type == "drums":
        # For drums, use basic pitch detection with onset detection
        from tone_forge.midi.passes.high_confidence import HighConfidencePass
        from tone_forge.midi.passes.base import ExtractionContext
        import librosa

        audio, sr = librosa.load(str(audio_path), sr=22050, mono=True)

        context = ExtractionContext(
            audio=audio,
            sr=sr,
            stem_type="drums",
            onset_threshold=0.3,
            frame_threshold=0.3,
            min_velocity=20,
            min_note_ms=20.0,
        )

        pass_obj = HighConfidencePass(
            pass_number=1,
            min_confidence=0.2,
            onset_threshold=0.3,
            frame_threshold=0.3,
            min_note_ms=20.0,
        )

        result = pass_obj.process([], context)
        notes = [
            (n.pitch, n.start, n.end, n.velocity)
            for n in result.notes
        ]
        return notes
    else:
        notes, tempo, duration, method = extract_midi_lead_ensemble(str(audio_path))

    return [(n.pitch, n.start, n.end, n.velocity) for n in notes]


def run_benchmark(samples_dir: Path, stem_type: str):
    """Run benchmark for a specific stem type."""
    logger.info(f"Discovering {stem_type} samples...")
    samples = discover_samples(samples_dir, stem_type)

    if not samples:
        logger.error(f"No {stem_type} samples found!")
        return

    logger.info(f"Found {len(samples)} {stem_type} samples\n")

    results = []
    passing = 0
    total_f1 = 0

    for sample in samples:
        name = sample["name"]
        audio_path = sample["audio_path"]
        gt_notes = sample["gt_notes"]

        print(f"Testing: {name}... ", end="", flush=True)

        try:
            extracted_notes = extract_midi(audio_path, stem_type)

            if not gt_notes:
                print(f"No GT {stem_type} notes")
                continue

            # Compute metrics with octave equivalence
            metrics = compute_midi_quality(
                extracted_notes,
                gt_notes,
                onset_tolerance_ms=300.0,
                allow_octave_equivalence=True,
            )

            f1 = metrics.note_f1 * 100
            total_f1 += f1

            if f1 >= 80:
                passing += 1
                print(f"F1={f1:.1f}% \u2713")
            else:
                print(f"F1={f1:.1f}% \u2717")

            results.append({
                "name": name,
                "f1": f1,
                "precision": metrics.note_precision * 100,
                "recall": metrics.note_recall * 100,
                "extracted": len(extracted_notes),
                "gt": len(gt_notes),
            })

        except Exception as e:
            print(f"ERROR: {e}")
            results.append({
                "name": name,
                "f1": 0,
                "error": str(e),
            })

    # Summary
    valid_results = [r for r in results if "error" not in r]
    if valid_results:
        avg_f1 = sum(r["f1"] for r in valid_results) / len(valid_results)

        print(f"\n{'=' * 60}")
        print(f"{stem_type.upper()} BENCHMARK SUMMARY")
        print(f"{'=' * 60}")
        print(f"Passing (\u226580%): {passing}/{len(valid_results)}")
        print(f"Average F1: {avg_f1:.1f}%")

        # Results by sample
        print(f"\nResults by sample:")
        for r in sorted(valid_results, key=lambda x: -x["f1"]):
            symbol = "\u2713" if r["f1"] >= 80 else "\u2717"
            ext_gt = f"({r['extracted']}/{r['gt']})"
            print(f"  {symbol} {r['f1']:5.1f}% - {r['name']} {ext_gt}")


if __name__ == "__main__":
    samples_dir = Path("/Users/mattharvey/Sites/tone-forge/samples")

    stem_type = "drums"
    if len(sys.argv) > 1:
        stem_type = sys.argv[1].lower()

    if not samples_dir.exists():
        logger.error(f"Samples directory not found: {samples_dir}")
        sys.exit(1)

    run_benchmark(samples_dir, stem_type)
