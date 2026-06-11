#!/usr/bin/env python3
"""Comprehensive benchmark validation script.

Runs all 7 validation phases to verify benchmark metric integrity:
1. Benchmark validity audit
2. Matching strategy comparison
3. Cross-dataset validation
4. False positive analysis
5. False negative analysis
6. Confidence calibration
7. Human usability scoring

Usage:
    python scripts/validate_benchmark.py [--samples-dir DIR] [--output-dir DIR] [--limit N]
"""

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

import numpy as np

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from tone_forge.evaluation.benchmark_validation import (
    BenchmarkValidationRunner,
    BenchmarkValidationReport,
    UsabilityScorer,
    compare_strategies,
)
from tone_forge.evaluation.benchmark_expansion import (
    discover_samples,
    load_or_create_manifest,
    ParallelBenchmarkRunner,
    ParallelConfig,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def load_midi_notes(midi_path: Path) -> List[Tuple[int, float, float, int]]:
    """Load notes from a MIDI file."""
    try:
        import mido
    except ImportError:
        logger.error("mido not installed: pip install mido")
        return []

    try:
        mid = mido.MidiFile(str(midi_path))
        notes = []
        active_notes = {}

        for track in mid.tracks:
            current_time = 0.0
            for msg in track:
                current_time += mido.tick2second(msg.time, mid.ticks_per_beat, 500000)

                if msg.type == 'note_on' and msg.velocity > 0:
                    active_notes[msg.note] = (current_time, msg.velocity)
                elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
                    if msg.note in active_notes:
                        start, velocity = active_notes.pop(msg.note)
                        notes.append((msg.note, start, current_time, velocity))

        return notes
    except Exception as e:
        logger.error(f"Failed to load MIDI {midi_path}: {e}")
        return []


def extract_midi_for_sample(audio_path: Path, stem_type: str = "other") -> List[Tuple[int, float, float, int]]:
    """Extract MIDI from audio."""
    try:
        import librosa
        from tone_forge.midi import MultiPassExtractor
    except ImportError as e:
        logger.error(f"Missing dependency: {e}")
        return []

    try:
        audio, sr = librosa.load(str(audio_path), sr=22050, mono=True)
        extractor = MultiPassExtractor()
        result = extractor.extract(audio, sr, stem_type=stem_type)
        return [(n.pitch, n.start, n.end, n.velocity) for n in result.notes]
    except Exception as e:
        logger.error(f"Extraction failed for {audio_path}: {e}")
        return []


def main():
    parser = argparse.ArgumentParser(description="Validate benchmark metrics")
    parser.add_argument(
        "--samples-dir",
        type=Path,
        default=Path("benchmark_samples"),
        help="Directory with benchmark samples",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("benchmark_results/validation"),
        help="Output directory for reports",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of samples to validate",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Verbose output",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Create output directory
    args.output_dir.mkdir(parents=True, exist_ok=True)

    # Discover samples
    logger.info(f"Discovering samples in {args.samples_dir}")

    if not args.samples_dir.exists():
        logger.error(f"Samples directory not found: {args.samples_dir}")
        logger.info("Creating synthetic test data for demonstration...")

        # Create synthetic data for demonstration
        samples = create_synthetic_samples(3)
    else:
        # Load manifest
        manifest = load_or_create_manifest(args.samples_dir)
        logger.info(f"Found {len(manifest.samples)} samples")

        if args.limit:
            manifest.samples = manifest.samples[:args.limit]
            logger.info(f"Limited to {len(manifest.samples)} samples")

        # Convert to validation format
        samples = []
        for sample in manifest.samples:
            if not sample.ground_truth_midi_path:
                logger.warning(f"No GT MIDI for {sample.id}, skipping")
                continue

            gt_notes = load_midi_notes(sample.ground_truth_midi_path)
            if not gt_notes:
                continue

            ext_notes = extract_midi_for_sample(sample.audio_path, sample.stem_type)
            samples.append((sample.id, ext_notes, gt_notes))

    if not samples:
        logger.error("No valid samples found")
        sys.exit(1)

    logger.info(f"Validating {len(samples)} samples...")

    # Run validation
    runner = BenchmarkValidationRunner()
    report = runner.validate_batch(samples, manifest_name="benchmark_validation")

    # Save reports
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # JSON report
    json_path = args.output_dir / f"validation_report_{timestamp}.json"
    runner.save_report(report, json_path)

    # Print summary
    print("\n" + report.summary())

    # Save summary
    summary_path = args.output_dir / f"validation_summary_{timestamp}.txt"
    with open(summary_path, "w") as f:
        f.write(report.summary())
    logger.info(f"Saved summary to {summary_path}")

    # Print key findings
    print("\n" + "=" * 70)
    print("KEY FINDINGS")
    print("=" * 70)

    # F1 comparison
    print(f"\nF1 Score Analysis:")
    print(f"  Original F1:        {report.aggregate_original_f1:.1%}")
    print(f"  Corrected F1:       {report.aggregate_corrected_f1:.1%}")
    print(f"  Optimal F1:         {report.aggregate_optimal_f1:.1%}")
    print(f"  Strict F1:          {report.aggregate_strict_f1:.1%}")
    print(f"  Musical F1:         {report.aggregate_musical_f1:.1%}")
    print(f"  Reconstruction F1:  {report.aggregate_reconstruction_f1:.1%}")

    # Validity assessment
    print(f"\nBenchmark Validity: {report.benchmark_validity.upper()}")
    print(f"Metric Confidence:  {report.aggregate_metric_confidence:.1%}")

    if report.validity_reasons:
        print("\nReasons:")
        for reason in report.validity_reasons:
            print(f"  - {reason}")

    # Dominant failure modes
    if report.dominant_fp_category:
        print(f"\nDominant FP Category: {report.dominant_fp_category}")
    if report.dominant_fn_category:
        print(f"Dominant FN Category: {report.dominant_fn_category}")

    print("\n" + "=" * 70)


def create_synthetic_samples(n: int) -> List[Tuple[str, List, List]]:
    """Create synthetic samples for testing."""
    samples = []

    for i in range(n):
        # Create synthetic GT
        gt_notes = []
        for j in range(20):
            pitch = 60 + np.random.randint(-12, 12)
            onset = j * 0.5 + np.random.uniform(-0.02, 0.02)
            offset = onset + np.random.uniform(0.2, 0.4)
            velocity = np.random.randint(60, 100)
            gt_notes.append((pitch, onset, offset, velocity))

        # Create synthetic extracted (with some errors)
        ext_notes = []
        for pitch, onset, offset, velocity in gt_notes:
            if np.random.random() > 0.15:  # 85% recall
                # Add some timing noise
                ext_onset = onset + np.random.uniform(-0.03, 0.03)
                ext_offset = offset + np.random.uniform(-0.05, 0.05)
                ext_velocity = velocity + np.random.randint(-10, 10)
                ext_notes.append((pitch, ext_onset, ext_offset, max(1, min(127, ext_velocity))))

        # Add some false positives
        for _ in range(3):
            fp_pitch = 60 + np.random.randint(-12, 12)
            fp_onset = np.random.uniform(0, 10)
            fp_offset = fp_onset + np.random.uniform(0.1, 0.3)
            fp_velocity = np.random.randint(40, 80)
            ext_notes.append((fp_pitch, fp_onset, fp_offset, fp_velocity))

        samples.append((f"synthetic_{i}", ext_notes, gt_notes))

    return samples


if __name__ == "__main__":
    main()
