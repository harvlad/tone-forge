"""Threaded MIDI extraction using a shared model.

Unlike ProcessPoolExecutor which spawns new processes (each loading the model),
ThreadPoolExecutor shares the model across threads. This avoids the ~4s model
loading overhead per stem.

Note: basic-pitch releases the GIL during inference, so threading can provide
real parallelism for I/O-bound portions while sharing the model.
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _warm_up_model():
    """Pre-load the basic-pitch model to avoid first-call overhead."""
    from basic_pitch.inference import predict
    from basic_pitch import ICASSP_2022_MODEL_PATH
    import tempfile
    import numpy as np
    import soundfile as sf

    # Create a tiny audio file for warmup
    with tempfile.NamedTemporaryFile(suffix='.wav', delete=True) as f:
        sr = 22050
        duration = 0.1  # 100ms
        audio = np.zeros(int(sr * duration), dtype=np.float32)
        sf.write(f.name, audio, sr)

        # Warm up the model
        try:
            predict(
                f.name,
                model_or_model_path=ICASSP_2022_MODEL_PATH,
            )
            logger.info("basic-pitch model warmed up")
        except Exception as e:
            logger.debug(f"Warmup failed (expected for empty audio): {e}")


def extract_midi_threaded(
    stem_paths: Dict[str, Path],
    genre: str,
    melodic_stems: List[str] = None,
    max_workers: int = 2,
    warm_up: bool = True,
) -> Tuple[Dict[str, Dict[str, Any]], float]:
    """Extract MIDI from multiple stems using threading.

    Unlike multiprocessing, threads share the loaded model, avoiding
    the ~4s model loading overhead per stem.

    Args:
        stem_paths: Dict mapping stem name to path
        genre: Pre-detected genre
        melodic_stems: Stems to process (default: bass, vocals, other)
        max_workers: Number of threads
        warm_up: Pre-load model before starting

    Returns:
        Tuple of (results dict, total_time_ms)
    """
    if melodic_stems is None:
        melodic_stems = ["bass", "vocals", "other"]

    stems_to_process = [
        (name, path) for name, path in stem_paths.items()
        if name in melodic_stems
    ]

    if not stems_to_process:
        return {}, 0.0

    # Warm up model in main thread (loads it once)
    if warm_up:
        logger.info("Warming up basic-pitch model...")
        _warm_up_model()

    start = time.perf_counter()
    results = {}

    def extract_one(stem_name: str, stem_path: Path) -> Tuple[str, Dict[str, Any]]:
        """Extract MIDI from a single stem."""
        from tone_forge.midi_extractor import extract_midi_polyphonic

        extract_start = time.perf_counter()
        try:
            result = extract_midi_polyphonic(
                str(stem_path),
                preset_name=f"{stem_name.capitalize()} MIDI",
                stem_type=stem_name,
                genre=genre,
            )
            return stem_name, {
                "note_count": int(result.note_count),
                "tempo": float(result.tempo_bpm),
                "pitch_range": tuple(int(p) for p in result.pitch_range),
                "time_ms": (time.perf_counter() - extract_start) * 1000,
            }
        except Exception as e:
            logger.error(f"MIDI extraction failed for {stem_name}: {e}")
            return stem_name, {
                "error": str(e),
                "time_ms": (time.perf_counter() - extract_start) * 1000,
            }

    # Use ThreadPoolExecutor for true parallelism on I/O-bound parts
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(extract_one, name, path): name
            for name, path in stems_to_process
        }

        for future in as_completed(futures):
            stem_name = futures[future]
            try:
                name, result = future.result()
                results[name] = result
                logger.info(f"Completed {name}: {result.get('note_count', 0)} notes in {result['time_ms']:.0f}ms")
            except Exception as e:
                logger.error(f"Error in threaded extraction for {stem_name}: {e}")
                results[stem_name] = {"error": str(e)}

    elapsed_ms = (time.perf_counter() - start) * 1000
    return results, elapsed_ms


def benchmark_threaded_vs_sequential(
    stem_paths: Dict[str, Path],
    genre: str,
) -> Dict[str, Any]:
    """Benchmark threaded vs sequential MIDI extraction.

    Returns timing comparison and recommendations.
    """
    melodic_stems = ["bass", "vocals", "other"]
    stems_to_process = [
        (name, path) for name, path in stem_paths.items()
        if name in melodic_stems
    ]

    if len(stems_to_process) < 2:
        return {"error": "Need at least 2 stems to benchmark"}

    # Warm up model first
    logger.info("Warming up model...")
    _warm_up_model()

    # Sequential extraction
    logger.info("Running sequential extraction...")
    seq_start = time.perf_counter()
    seq_results = {}

    from tone_forge.midi_extractor import extract_midi_polyphonic

    for name, path in stems_to_process:
        result = extract_midi_polyphonic(
            str(path),
            preset_name=f"{name.capitalize()} MIDI",
            stem_type=name,
            genre=genre,
        )
        seq_results[name] = {"note_count": result.note_count}

    seq_time = (time.perf_counter() - seq_start) * 1000

    # Threaded extraction (2 threads)
    logger.info("Running threaded extraction (2 workers)...")
    thread_results, thread_time = extract_midi_threaded(
        stem_paths, genre, melodic_stems,
        max_workers=2, warm_up=False  # Already warmed up
    )

    return {
        "sequential_ms": seq_time,
        "threaded_2_workers_ms": thread_time,
        "speedup": seq_time / thread_time if thread_time > 0 else 0,
        "stem_count": len(stems_to_process),
        "recommendation": (
            "threaded" if thread_time < seq_time else "sequential"
        ),
    }
