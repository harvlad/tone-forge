#!/usr/bin/env python3
"""Profile the complete ToneForge analysis pipeline.

Runs comprehensive profiling on a sample audio file and generates
detailed reports with bottleneck analysis.

Usage:
    python scripts/profile_pipeline.py /path/to/audio.mp3 [--output-dir profile_results]
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from tone_forge.profiling import (
    Profiler,
    GPUMonitor,
    MemoryTracker,
    get_profiler,
    profile_stage,
)
from tone_forge.profiling.pipeline_instrumentation import (
    InstrumentedPipeline,
    PipelineTimings,
    generate_bottleneck_report,
    generate_flamegraph_data,
)
from tone_forge.profiling.instrumented_stem_separator import (
    separate_all_stems_profiled,
    get_stem_separation_summary,
)
from tone_forge.profiling.instrumented_midi_extractor import (
    extract_midi_polyphonic_profiled,
    get_midi_extraction_summary,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def profile_full_pipeline(
    audio_path: Path,
    output_dir: Path,
    skip_stem_separation: bool = False,
    parallel_midi: bool = False,
) -> dict:
    """Run full pipeline profiling.

    Args:
        audio_path: Path to audio file
        output_dir: Directory for outputs
        skip_stem_separation: Skip stem separation (use audio as-is)

    Returns:
        Complete profiling results dictionary
    """
    import librosa
    import soundfile as sf

    output_dir.mkdir(parents=True, exist_ok=True)

    # Initialize instrumented pipeline
    pipeline = InstrumentedPipeline(
        run_id=datetime.now().strftime("%Y%m%d_%H%M%S"),
        enable_gpu_monitoring=True,
        enable_memory_tracking=True,
        output_dir=output_dir,
    )

    pipeline.start()
    profiler = get_profiler()

    results = {
        "audio_file": str(audio_path.name),
        "run_id": pipeline.run_id,
        "stages": {},
    }

    # Stage 1: Audio Loading
    with profiler.profile("audio_loading") as stage:
        y, sr = librosa.load(str(audio_path), sr=22050, mono=True)
        duration = len(y) / sr

        stage.metadata["sample_rate"] = sr
        stage.metadata["samples"] = len(y)
        stage.metadata["duration_sec"] = duration

        pipeline.add_metadata("audio_duration_sec", duration)

    logger.info(f"Audio loaded: {duration:.1f}s duration")
    results["audio_duration_sec"] = duration

    # Pre-detect genre once (saves ~2s by avoiding per-stem detection)
    with profiler.profile("genre_detection_cached") as genre_stage:
        from tone_forge.midi_extractor import detect_genre_from_audio
        cached_genre = detect_genre_from_audio(y, sr)
        genre_stage.metadata["detected_genre"] = cached_genre

    logger.info(f"Cached genre detection: {cached_genre}")
    results["cached_genre"] = cached_genre

    # Stage 2: Stem Separation (optional)
    stem_paths = {}
    if not skip_stem_separation:
        logger.info("Running stem separation...")
        try:
            stems_dir = output_dir / "stems"
            stem_paths = separate_all_stems_profiled(
                audio_path,
                output_dir=stems_dir,
            )
            pipeline.add_metadata("stem_count", len(stem_paths))
            results["stems"] = list(stem_paths.keys())
        except Exception as e:
            logger.warning(f"Stem separation failed: {e}")
            results["stem_separation_error"] = str(e)
    else:
        # Use the original audio as "other" stem
        stem_paths = {"other": audio_path}
        results["stems"] = ["other (original audio)"]

    # Stage 3: MIDI Extraction for each melodic stem
    melodic_stems = ["bass", "other", "vocals"]  # Skip drums
    midi_results = {}

    if parallel_midi:
        # Parallel MIDI extraction (experimental)
        logger.info("Running parallel MIDI extraction...")
        from tone_forge.profiling.parallel_midi_extraction import extract_midi_parallel_with_profiling

        with profiler.profile("midi_extraction_parallel"):
            midi_results, parallel_time_ms = extract_midi_parallel_with_profiling(
                stem_paths=stem_paths,
                genre=cached_genre,
                melodic_stems=melodic_stems,
                max_workers=3,
            )

        logger.info(f"Parallel MIDI extraction completed in {parallel_time_ms/1000:.2f}s")
    else:
        # Sequential MIDI extraction (default)
        for stem_name, stem_path in stem_paths.items():
            if stem_name in melodic_stems:
                logger.info(f"Extracting MIDI from {stem_name} stem...")

                try:
                    with profiler.profile(f"midi_extraction_{stem_name}"):
                        midi_result = extract_midi_polyphonic_profiled(
                            str(stem_path),
                            preset_name=f"{stem_name.capitalize()} MIDI",
                            stem_type=stem_name,
                            genre=cached_genre,  # Use cached genre
                        )
                        midi_results[stem_name] = {
                            "note_count": midi_result.note_count,
                            "tempo": midi_result.tempo_bpm,
                        }
                except Exception as e:
                    logger.warning(f"MIDI extraction failed for {stem_name}: {e}")
                    midi_results[stem_name] = {"error": str(e)}

    results["midi_extraction"] = midi_results

    # Stage 4: Tone Analysis
    with profiler.profile("tone_analysis"):
        try:
            from tone_forge.analyzer import analyze

            # Analyze the original audio (or "other" stem)
            descriptor = analyze(str(audio_path))
            results["tone_analysis"] = {
                "amp_family": descriptor.amp.family if descriptor.amp else None,
                "gain": descriptor.amp.gain if descriptor.amp else None,
            }
        except Exception as e:
            logger.warning(f"Tone analysis failed: {e}")
            results["tone_analysis"] = {"error": str(e)}

    # Generate report
    report = pipeline.finish()
    timings = pipeline.get_timings()

    # Save report
    report_path = pipeline.save_report(report)

    # Generate bottleneck analysis
    bottleneck_report = generate_bottleneck_report(report)

    # Generate flamegraph data
    flamegraph_data = generate_flamegraph_data(report)

    # Save all results
    results["report_path"] = str(report_path)
    results["timings"] = timings.to_dict()
    results["bottleneck_analysis"] = bottleneck_report
    results["flamegraph_data"] = flamegraph_data

    # Save complete results
    results_path = output_dir / f"profile_results_{pipeline.run_id}.json"
    with open(results_path, "w") as f:
        json.dump(results, f, indent=2)

    logger.info(f"Results saved to {results_path}")

    return results


def print_summary(results: dict) -> None:
    """Print human-readable summary."""
    print("\n" + "=" * 70)
    print("PIPELINE PROFILE SUMMARY")
    print("=" * 70)

    print(f"\nAudio: {results['audio_file']}")
    print(f"Duration: {results['audio_duration_sec']:.1f}s")

    timings = results.get("timings", {})
    summary = timings.get("summary", {})

    print(f"\nTotal processing time: {summary.get('total_sec', 0):.2f}s")
    print(f"Realtime factor: {summary.get('realtime_factor', 0):.1f}x")

    # Stage breakdown
    stages = timings.get("stages", {})
    print("\nSTAGE BREAKDOWN:")
    print("-" * 50)

    for stage, time_ms in sorted(stages.items(), key=lambda x: x[1], reverse=True):
        time_s = time_ms / 1000
        total_ms = summary.get("total_ms", 1)
        pct = (time_ms / total_ms) * 100 if total_ms > 0 else 0
        print(f"  {stage:<25} {time_s:>8.2f}s  ({pct:>5.1f}%)")

    # Bottleneck analysis
    bottleneck = results.get("bottleneck_analysis", {})
    print("\nBOTTLENECK ANALYSIS:")
    print("-" * 50)

    categories = bottleneck.get("categories", {})
    for category, items in categories.items():
        if items:
            print(f"\n{category.replace('_', ' ').title()}:")
            for item in items[:3]:
                print(f"  - {item['name']}: {item['time_ms']/1000:.2f}s ({item['time_pct']:.1f}%)")

    # Recommendations
    recommendations = bottleneck.get("recommendations", [])
    if recommendations:
        print("\nRECOMMENDATIONS:")
        print("-" * 50)
        for rec in recommendations:
            print(f"  • {rec}")

    # Memory
    memory = timings.get("memory", {})
    print("\nMEMORY USAGE:")
    print(f"  Peak CPU memory: {memory.get('peak_mb', 0):.1f} MB")
    print(f"  Peak GPU memory: {memory.get('peak_gpu_mb', 0):.1f} MB")


def main():
    parser = argparse.ArgumentParser(
        description='Profile ToneForge analysis pipeline'
    )
    parser.add_argument('audio_path', type=str, help='Path to audio file')
    parser.add_argument(
        '--output-dir', type=str, default='profile_results',
        help='Output directory for results'
    )
    parser.add_argument(
        '--skip-stems', action='store_true',
        help='Skip stem separation (analyze audio directly)'
    )
    parser.add_argument(
        '--parallel-midi', action='store_true',
        help='Enable parallel MIDI extraction (experimental)'
    )
    parser.add_argument(
        '--verbose', '-v', action='store_true',
        help='Verbose logging'
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    audio_path = Path(args.audio_path)
    if not audio_path.exists():
        print(f"Error: Audio file not found: {audio_path}")
        sys.exit(1)

    output_dir = Path(args.output_dir)

    print(f"\nProfiling: {audio_path.name}")
    print(f"Output: {output_dir}")
    print("-" * 50)

    results = profile_full_pipeline(
        audio_path,
        output_dir,
        skip_stem_separation=args.skip_stems,
        parallel_midi=args.parallel_midi,
    )

    print_summary(results)

    print(f"\nDetailed results saved to: {output_dir}")


if __name__ == '__main__':
    main()
