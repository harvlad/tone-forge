"""Parallel MIDI extraction for multiple stems.

Uses ProcessPoolExecutor to extract MIDI from stems concurrently,
significantly reducing total pipeline time.
"""
from __future__ import annotations

import logging
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _extract_midi_single(
    stem_path: str,
    stem_name: str,
    preset_name: str,
    genre: str,
) -> Tuple[str, Dict[str, Any]]:
    """Extract MIDI from a single stem (worker function).

    This function runs in a separate process.

    Args:
        stem_path: Path to stem audio file
        stem_name: Name of the stem (bass, vocals, etc.)
        preset_name: Name for the MIDI preset
        genre: Pre-detected genre

    Returns:
        Tuple of (stem_name, result_dict)
    """
    try:
        # Import here to avoid pickling issues
        from tone_forge.midi_extractor import extract_midi_polyphonic

        midi_result = extract_midi_polyphonic(
            stem_path,
            preset_name=preset_name,
            stem_type=stem_name,
            genre=genre,
        )

        return stem_name, {
            "note_count": int(midi_result.note_count),
            "tempo": float(midi_result.tempo_bpm),
            "pitch_range": tuple(int(p) for p in midi_result.pitch_range),
            "filename": str(midi_result.filename),
        }
    except Exception as e:
        logger.warning(f"MIDI extraction failed for {stem_name}: {e}")
        return stem_name, {"error": str(e)}


def extract_midi_parallel(
    stem_paths: Dict[str, Path],
    genre: str,
    melodic_stems: List[str] = None,
    max_workers: int = 3,
) -> Dict[str, Dict[str, Any]]:
    """Extract MIDI from multiple stems in parallel.

    Args:
        stem_paths: Dict mapping stem name to path
        genre: Pre-detected genre (to avoid redundant detection)
        melodic_stems: List of stem names to process (default: bass, other, vocals)
        max_workers: Maximum number of parallel workers

    Returns:
        Dict mapping stem name to extraction results
    """
    if melodic_stems is None:
        melodic_stems = ["bass", "other", "vocals"]

    # Filter to only melodic stems that exist
    stems_to_process = [
        (name, path) for name, path in stem_paths.items()
        if name in melodic_stems
    ]

    if not stems_to_process:
        return {}

    results = {}

    # Use process pool for true parallelism (avoid GIL)
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = {}

        for stem_name, stem_path in stems_to_process:
            future = executor.submit(
                _extract_midi_single,
                str(stem_path),
                stem_name,
                f"{stem_name.capitalize()} MIDI",
                genre,
            )
            futures[future] = stem_name

        # Collect results as they complete
        for future in as_completed(futures):
            stem_name = futures[future]
            try:
                name, result = future.result()
                results[name] = result
                logger.info(f"Completed MIDI extraction for {name}")
            except Exception as e:
                logger.error(f"Error in parallel extraction for {stem_name}: {e}")
                results[stem_name] = {"error": str(e)}

    return results


def extract_midi_parallel_with_profiling(
    stem_paths: Dict[str, Path],
    genre: str,
    melodic_stems: List[str] = None,
    max_workers: int = 3,
) -> Tuple[Dict[str, Dict[str, Any]], float]:
    """Extract MIDI from multiple stems in parallel with timing.

    Args:
        stem_paths: Dict mapping stem name to path
        genre: Pre-detected genre
        melodic_stems: List of stem names to process
        max_workers: Maximum number of parallel workers

    Returns:
        Tuple of (results dict, total_time_ms)
    """
    import time

    start = time.perf_counter()
    results = extract_midi_parallel(
        stem_paths=stem_paths,
        genre=genre,
        melodic_stems=melodic_stems,
        max_workers=max_workers,
    )
    elapsed_ms = (time.perf_counter() - start) * 1000

    return results, elapsed_ms
