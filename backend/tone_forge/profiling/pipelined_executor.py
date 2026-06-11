"""Pipelined execution for overlapping stem separation and MIDI extraction.

Instead of:
  separate(all stems) -> extract_midi(stem1) -> extract_midi(stem2) -> ...

We do:
  separate(stem1) ─┬─> extract_midi(stem1) ────────────────────────>
                   │
  separate(stem2) ─┼─────────────────────> extract_midi(stem2) ────>
                   │
  separate(stem3) ─┴───────────────────────────────> extract_midi(stem3)

This overlaps I/O (stem output) with CPU (MIDI extraction).
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, Future
from dataclasses import dataclass, field
from pathlib import Path
from queue import Queue
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class StemResult:
    """Result of stem separation for one stem."""
    stem_name: str
    path: Path
    audio: Optional[Any] = None  # numpy array if in-memory
    separation_time_ms: float = 0.0


@dataclass
class MidiResult:
    """Result of MIDI extraction for one stem."""
    stem_name: str
    note_count: int = 0
    tempo: float = 120.0
    extraction_time_ms: float = 0.0
    error: Optional[str] = None


@dataclass
class PipelineResult:
    """Complete pipeline result."""
    stems: Dict[str, StemResult] = field(default_factory=dict)
    midi: Dict[str, MidiResult] = field(default_factory=dict)
    total_time_ms: float = 0.0
    stem_separation_time_ms: float = 0.0
    midi_extraction_time_ms: float = 0.0
    overlap_saved_ms: float = 0.0


class PipelinedExecutor:
    """Executes stem separation and MIDI extraction in a pipelined fashion.

    Usage:
        executor = PipelinedExecutor(
            stem_separator=my_separator_fn,
            midi_extractor=my_extractor_fn,
        )
        result = executor.run(audio_path, genre="synthwave")

    The stem_separator should yield (stem_name, stem_path) tuples as stems
    become available. The midi_extractor should be a function that takes
    (stem_path, stem_name, genre) and returns a MidiResult.
    """

    def __init__(
        self,
        stem_separator: Callable,
        midi_extractor: Callable,
        melodic_stems: List[str] = None,
        max_extraction_workers: int = 1,
    ):
        """Initialize the pipelined executor.

        Args:
            stem_separator: Function that separates audio into stems
            midi_extractor: Function that extracts MIDI from a stem
            melodic_stems: Stems to extract MIDI from (default: bass, vocals, other)
            max_extraction_workers: Number of parallel MIDI extractors
        """
        self.stem_separator = stem_separator
        self.midi_extractor = midi_extractor
        self.melodic_stems = melodic_stems or ["bass", "vocals", "other"]
        self.max_extraction_workers = max_extraction_workers

    def run(
        self,
        audio_path: Path,
        genre: str = "default",
        output_dir: Optional[Path] = None,
    ) -> PipelineResult:
        """Run the pipelined extraction.

        Args:
            audio_path: Path to input audio
            genre: Pre-detected genre
            output_dir: Output directory for stems

        Returns:
            PipelineResult with all stem and MIDI results
        """
        result = PipelineResult()
        start_time = time.perf_counter()

        # Queue for stems ready for MIDI extraction
        stem_queue: Queue[Optional[StemResult]] = Queue()

        # Storage for results
        stem_results: Dict[str, StemResult] = {}
        midi_results: Dict[str, MidiResult] = {}
        midi_lock = threading.Lock()

        # Track timing
        stem_start = time.perf_counter()
        stem_end = 0.0
        midi_times: List[float] = []

        def extraction_worker():
            """Worker that extracts MIDI from stems as they become available."""
            while True:
                stem = stem_queue.get()
                if stem is None:  # Poison pill
                    break

                if stem.stem_name not in self.melodic_stems:
                    continue

                logger.info(f"Starting MIDI extraction for {stem.stem_name}")
                extract_start = time.perf_counter()

                try:
                    midi_result = self.midi_extractor(
                        stem.path,
                        stem.stem_name,
                        genre,
                    )
                    midi_result.extraction_time_ms = (
                        time.perf_counter() - extract_start
                    ) * 1000
                except Exception as e:
                    logger.error(f"MIDI extraction failed for {stem.stem_name}: {e}")
                    midi_result = MidiResult(
                        stem_name=stem.stem_name,
                        error=str(e),
                        extraction_time_ms=(time.perf_counter() - extract_start) * 1000,
                    )

                with midi_lock:
                    midi_results[stem.stem_name] = midi_result
                    midi_times.append(midi_result.extraction_time_ms)

                logger.info(
                    f"Completed MIDI extraction for {stem.stem_name} "
                    f"in {midi_result.extraction_time_ms:.0f}ms"
                )

        # Start extraction worker(s)
        extraction_threads = []
        for _ in range(self.max_extraction_workers):
            t = threading.Thread(target=extraction_worker, daemon=True)
            t.start()
            extraction_threads.append(t)

        # Run stem separation and feed stems to queue
        try:
            for stem_name, stem_path in self.stem_separator(audio_path, output_dir):
                sep_time = (time.perf_counter() - stem_start) * 1000
                stem_result = StemResult(
                    stem_name=stem_name,
                    path=Path(stem_path),
                    separation_time_ms=sep_time,
                )
                stem_results[stem_name] = stem_result

                # Queue for MIDI extraction
                stem_queue.put(stem_result)
                logger.info(f"Stem {stem_name} ready, queued for extraction")

            stem_end = time.perf_counter()

        finally:
            # Send poison pills to stop workers
            for _ in extraction_threads:
                stem_queue.put(None)

            # Wait for extraction to complete
            for t in extraction_threads:
                t.join()

        end_time = time.perf_counter()

        # Calculate timing
        result.stems = stem_results
        result.midi = midi_results
        result.total_time_ms = (end_time - start_time) * 1000
        result.stem_separation_time_ms = (stem_end - stem_start) * 1000
        result.midi_extraction_time_ms = sum(midi_times)

        # Calculate overlap savings
        # Sequential would be: stem_separation + midi_extraction
        # Pipelined is: total_time
        sequential_estimate = result.stem_separation_time_ms + result.midi_extraction_time_ms
        result.overlap_saved_ms = sequential_estimate - result.total_time_ms

        logger.info(
            f"Pipeline complete: {result.total_time_ms:.0f}ms total, "
            f"{result.overlap_saved_ms:.0f}ms saved from overlap"
        )

        return result


def create_simple_separator(use_demucs: bool = True):
    """Create a simple stem separator generator.

    Yields (stem_name, stem_path) tuples as stems become available.
    """
    def separator(audio_path: Path, output_dir: Optional[Path] = None):
        if use_demucs:
            # Use HTDemucs
            from demucs.api import Separator
            import torchaudio

            separator = Separator(model="htdemucs_ft")
            origin, separated = separator.separate_audio_file(str(audio_path))

            stems_dir = output_dir or Path("stems_output")
            stems_dir.mkdir(parents=True, exist_ok=True)

            for stem_name, audio_tensor in separated.items():
                stem_path = stems_dir / f"{stem_name}.wav"
                torchaudio.save(str(stem_path), audio_tensor, separator.samplerate)
                yield stem_name, stem_path
        else:
            # Dummy separator for testing
            yield "other", audio_path

    return separator


def create_simple_extractor(genre: str = "default"):
    """Create a simple MIDI extractor function."""
    def extractor(stem_path: Path, stem_name: str, genre: str) -> MidiResult:
        from tone_forge.midi_extractor import extract_midi_polyphonic

        try:
            result = extract_midi_polyphonic(
                str(stem_path),
                preset_name=f"{stem_name.capitalize()} MIDI",
                stem_type=stem_name,
                genre=genre,
            )
            return MidiResult(
                stem_name=stem_name,
                note_count=result.note_count,
                tempo=result.tempo_bpm,
            )
        except Exception as e:
            return MidiResult(stem_name=stem_name, error=str(e))

    return extractor
