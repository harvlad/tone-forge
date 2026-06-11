#!/usr/bin/env python3
"""Run MIDI benchmark on the samples directory.

Discovers all stem+MIDI pairs and runs extraction benchmark.
Filters ground truth MIDI to only include tracks matching the stem type.
"""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import mido

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent))

from tone_forge.evaluation.midi_benchmark import (
    MIDIBenchmarkDataset,
    MIDIBenchmarkSample,
    MIDIBenchmarkRunner,
    ProfiledMIDIMetrics,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


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
    """Load notes from MIDI file, filtered by stem type.

    Only includes notes from tracks whose names match the stem type patterns.

    Args:
        midi_path: Path to MIDI file
        stem_type: Stem type to filter for ("bass", "lead", "pads", "drums")

    Returns:
        List of (pitch, start, end, velocity) tuples
    """
    mid = mido.MidiFile(str(midi_path))
    patterns = STEM_TRACK_PATTERNS.get(stem_type.lower(), [stem_type.lower()])

    notes = []

    for track in mid.tracks:
        track_name = track.name.lower() if track.name else ""

        # Check if track name matches any pattern for this stem type
        matches = any(pattern in track_name for pattern in patterns)
        if not matches:
            continue

        logger.debug(f"Including track '{track.name}' for stem type '{stem_type}'")

        # Extract notes from this track
        current_time = 0.0
        active_notes: Dict[int, Tuple[float, int]] = {}

        for msg in track:
            current_time += mido.tick2second(msg.time, mid.ticks_per_beat, 500000)

            if msg.type == 'note_on' and msg.velocity > 0:
                active_notes[msg.note] = (current_time, msg.velocity)
            elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
                if msg.note in active_notes:
                    start, velocity = active_notes.pop(msg.note)
                    notes.append((msg.note, start, current_time, velocity))

    return notes


def discover_samples(samples_dir: Path) -> list[MIDIBenchmarkSample]:
    """Discover all stem+MIDI pairs in samples directory."""
    samples = []

    # Valid stem types we're looking for
    stem_types = ["Bass", "Lead", "Pads", "Drums"]

    # Iterate through track directories
    for track_dir in sorted(samples_dir.iterdir()):
        if not track_dir.is_dir() or track_dir.name.startswith("."):
            continue

        logger.info(f"Scanning: {track_dir.name}")

        # Find MIDI file (contains bpm in name)
        midi_files = list(track_dir.glob("*bpm.mid")) + list(track_dir.glob("*bpm.midi"))
        if not midi_files:
            logger.warning(f"  No MIDI file found in {track_dir.name}")
            continue

        midi_file = midi_files[0]

        # Extract track name prefix from MIDI filename
        # e.g., "DemolitionWarning_140bpm.mid" -> "DemolitionWarning"
        track_prefix = midi_file.stem.rsplit("_", 1)[0]

        # Find stem files
        for stem_type in stem_types:
            stem_file = track_dir / f"{track_prefix}_{stem_type}.wav"
            if not stem_file.exists():
                # Try alternate naming
                alt_stems = list(track_dir.glob(f"*_{stem_type}.wav"))
                if alt_stems:
                    stem_file = alt_stems[0]
                else:
                    logger.debug(f"  No {stem_type} stem found")
                    continue

            sample_id = f"{track_dir.name}_{stem_type}"

            # Load ground truth notes filtered by stem type
            filtered_notes = load_filtered_midi_notes(midi_file, stem_type)

            if not filtered_notes:
                logger.warning(f"  No matching tracks for {stem_type} in MIDI file")
                continue

            samples.append(MIDIBenchmarkSample(
                id=sample_id,
                audio_path=stem_file,
                ground_truth_midi_path=midi_file,
                ground_truth_notes=filtered_notes,  # Pre-filtered notes
                stem_type=stem_type.lower(),
                genre="synthwave",  # Default genre for this dataset
                tags=["album_track"],
            ))
            logger.info(f"  Found: {stem_type} -> {stem_file.name} ({len(filtered_notes)} GT notes)")

    return samples


def run_benchmark(samples_dir: Path) -> ProfiledMIDIMetrics:
    """Run benchmark on samples directory."""
    logger.info(f"Discovering samples in: {samples_dir}")

    samples = discover_samples(samples_dir)

    if not samples:
        logger.error("No samples found!")
        return ProfiledMIDIMetrics()

    logger.info(f"Found {len(samples)} samples across stems")

    # Create dataset
    dataset = MIDIBenchmarkDataset(
        name="ToneForge Album Samples",
        version="1.0",
        samples=samples,
        metadata={
            "source": str(samples_dir),
            "discovered_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
    )

    # Show breakdown
    stems = dataset.get_stems()
    logger.info(f"Stems: {stems}")
    for stem in stems:
        count = len([s for s in samples if s.stem_type == stem])
        logger.info(f"  {stem}: {count} samples")

    # Create runner
    runner = MIDIBenchmarkRunner(
        use_auto_classify=True,
    )

    # Run benchmark
    logger.info("Starting benchmark...")
    start_time = time.time()

    def progress_callback(current: int, total: int):
        if current % 5 == 0 or current == total - 1:
            logger.info(f"Progress: {current + 1}/{total}")

    metrics, sample_results = runner.run(dataset, progress_callback=progress_callback)

    elapsed = time.time() - start_time
    logger.info(f"Benchmark completed in {elapsed:.1f}s")

    # Print results
    print("\n" + "=" * 60)
    print(metrics.summary())
    print("=" * 60)

    # Save results
    results_dir = samples_dir.parent / "backend" / "benchmark_results"
    results_dir.mkdir(exist_ok=True)

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    results_file = results_dir / f"benchmark_{timestamp}.json"

    import json
    with open(results_file, "w") as f:
        json.dump({
            "metrics": metrics.to_dict(),
            "sample_results": [r.to_dict() for r in sample_results],
        }, f, indent=2, default=str)

    logger.info(f"Results saved to: {results_file}")

    # Print per-sample breakdown for worst performers
    print("\n--- Per-Sample Results (sorted by F1) ---")
    sorted_results = sorted(
        [r for r in sample_results if r.success and r.metrics],
        key=lambda r: r.metrics.note_f1
    )

    for result in sorted_results[:20]:  # Show worst 20
        m = result.metrics
        print(f"{result.sample_id:45s}: F1={m.note_f1:.1%} P={m.note_precision:.1%} R={m.note_recall:.1%} ({result.extracted_note_count}/{result.ground_truth_note_count} notes)")

    # Show failures
    failures = [r for r in sample_results if not r.success]
    if failures:
        print(f"\n--- Failures ({len(failures)}) ---")
        for result in failures:
            print(f"{result.sample_id}: {result.error}")

    return metrics


if __name__ == "__main__":
    # Default samples directory
    samples_dir = Path("/Users/mattharvey/Sites/tone-forge/samples")

    if len(sys.argv) > 1:
        samples_dir = Path(sys.argv[1])

    if not samples_dir.exists():
        logger.error(f"Samples directory not found: {samples_dir}")
        sys.exit(1)

    metrics = run_benchmark(samples_dir)
