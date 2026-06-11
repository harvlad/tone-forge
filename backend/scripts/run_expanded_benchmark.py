#!/usr/bin/env python3
"""Run expanded benchmark across all discovered samples.

Usage:
    python scripts/run_expanded_benchmark.py [--parallel] [--workers N] [--output-dir DIR]
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from tone_forge.evaluation.benchmark_expansion import (
    ParallelConfig,
    ParallelBenchmarkRunner,
    BenchmarkHistory,
    load_or_create_manifest,
    get_default_samples_dir,
)
from tone_forge.evaluation.benchmark_expansion.history_tracker import BenchmarkRun

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def progress_callback(current: int, total: int, message: str):
    """Print progress updates."""
    pct = current / total * 100 if total > 0 else 0
    print(f"\r[{pct:5.1f}%] {message}", end='', flush=True)
    if current == total:
        print()


def main():
    parser = argparse.ArgumentParser(description='Run expanded benchmark')
    parser.add_argument('--parallel', action='store_true', help='Use parallel processing')
    parser.add_argument('--workers', type=int, default=4, help='Number of parallel workers')
    parser.add_argument('--output-dir', type=str, default='benchmark_results', help='Output directory')
    parser.add_argument('--samples-dir', type=str, help='Override samples directory')
    parser.add_argument('--check-regression', action='store_true', help='Check for regressions')
    parser.add_argument('--set-baseline', action='store_true', help='Set this run as baseline')
    args = parser.parse_args()

    # Load or create manifest
    samples_dir = Path(args.samples_dir) if args.samples_dir else get_default_samples_dir()
    logger.info(f"Loading samples from: {samples_dir}")

    manifest = load_or_create_manifest(samples_dir=samples_dir)
    logger.info(f"Manifest: {manifest.name} v{manifest.version}")
    logger.info(f"Total samples: {len(manifest.samples)}")

    # Print sample breakdown
    stem_counts = {}
    for sample in manifest.samples:
        stem_counts[sample.stem_type] = stem_counts.get(sample.stem_type, 0) + 1

    logger.info("Samples by stem type:")
    for stem, count in sorted(stem_counts.items()):
        logger.info(f"  {stem}: {count}")

    # Configure runner
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    config = ParallelConfig(
        max_workers=args.workers if args.parallel else 1,
        per_sample_timeout_sec=180.0,  # 3 minutes per sample
        output_dir=output_dir,
        save_csv=True,
        save_json=True,
        progress_interval=1,
    )

    runner = ParallelBenchmarkRunner(config)

    # Run benchmark
    logger.info(f"Running benchmark ({'parallel' if args.parallel else 'sequential'})...")
    start_time = datetime.now()

    if args.parallel:
        result = runner.run(manifest, progress_callback=progress_callback)
    else:
        result = runner.run_sequential(manifest, progress_callback=progress_callback)

    duration = (datetime.now() - start_time).total_seconds()

    # Print results
    print("\n" + "=" * 60)
    print(result.aggregate_metrics.summary())
    print("=" * 60)

    # Track in history
    history = BenchmarkHistory()
    history_run = BenchmarkRun.from_benchmark_result(result)
    history.add_run(history_run)

    # Check for regression
    if args.check_regression:
        report = history.detect_regression(history_run)
        print("\n" + report.summary())

        if report.has_regressions:
            sys.exit(1)  # Exit with error code for CI

    # Set as baseline
    if args.set_baseline:
        history.set_baseline(history_run.run_id)
        logger.info(f"Set run {history_run.run_id} as baseline")

    logger.info(f"Benchmark completed in {duration:.1f}s")
    logger.info(f"Results saved to: {output_dir}")


if __name__ == '__main__':
    main()
