"""Instrumented MIDI extraction with detailed profiling.

Wraps the MIDI extractor with comprehensive timing instrumentation
for identifying performance bottlenecks.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .profiler import profile_stage, get_profiler
from .gpu_monitor import get_gpu_monitor

logger = logging.getLogger(__name__)


def extract_midi_polyphonic_profiled(
    audio_path: str,
    preset_name: str = "Extracted MIDI",
    onset_threshold: float = None,
    frame_threshold: float = None,
    min_note_length_ms: float = None,
    stem_type: str = 'other',
    genre: str = None,
) -> Any:
    """Extract MIDI using basic-pitch with detailed profiling.

    This is a drop-in replacement for midi_extractor.extract_midi_polyphonic
    with comprehensive timing instrumentation.

    Args:
        audio_path: Path to audio file
        preset_name: Name for MIDI file
        onset_threshold: Onset detection threshold
        frame_threshold: Frame detection threshold
        min_note_length_ms: Minimum note length in ms
        stem_type: Type of stem
        genre: Genre hint

    Returns:
        MIDIExtractionResult
    """
    from basic_pitch.inference import predict
    from basic_pitch import ICASSP_2022_MODEL_PATH
    import librosa
    import pretty_midi
    import soundfile as sf
    import tempfile

    # Import the original module's helpers
    from tone_forge.midi_extractor import (
        detect_genre_from_audio,
        get_extraction_profile,
        MIDIExtractionResult,
    )

    profiler = get_profiler()
    gpu_monitor = get_gpu_monitor()

    with profiler.profile("midi_extraction") as stage:
        stage.metadata["audio_path"] = str(Path(audio_path).name)
        stage.metadata["stem_type"] = stem_type
        stage.metadata["preset_name"] = preset_name

        # Audio loading
        with profiler.profile("midi_extraction/audio_load") as load_stage:
            y, sr = librosa.load(audio_path, sr=22050, mono=True)
            duration = len(y) / sr

            load_stage.metadata["sample_rate"] = sr
            load_stage.metadata["samples"] = len(y)
            load_stage.metadata["duration_sec"] = duration

        stage.metadata["duration_sec"] = duration

        # Genre detection
        with profiler.profile("midi_extraction/genre_detection") as genre_stage:
            if genre is None:
                genre = detect_genre_from_audio(y, sr)
            genre_stage.metadata["detected_genre"] = genre

        # Profile selection
        with profiler.profile("midi_extraction/profile_selection") as profile_stage:
            profile = get_extraction_profile(stem_type, genre)

            # Apply profile defaults
            if onset_threshold is None:
                onset_threshold = profile.get('onset_threshold', 0.5)
            if frame_threshold is None:
                frame_threshold = profile.get('frame_threshold', 0.4)
            if min_note_length_ms is None:
                min_note_length_ms = profile.get('min_note_ms', 50)

            profile_stage.metadata["profile"] = {
                "stem_type": stem_type,
                "genre": genre,
                "onset_threshold": onset_threshold,
                "frame_threshold": frame_threshold,
                "min_note_ms": min_note_length_ms,
            }

        # Audio preprocessing
        audio_path_for_prediction = audio_path
        clean_path = None

        with profiler.profile("midi_extraction/preprocessing") as preproc_stage:
            if y.size > 0 and not np.all(np.isfinite(y)):
                logger.warning("Audio contains non-finite values, cleaning...")
                y = np.nan_to_num(y, nan=0.0, posinf=0.0, neginf=0.0)
                y = y.astype(np.float32)

                clean_path = Path(tempfile.mktemp(suffix='.wav'))
                sf.write(str(clean_path), y, sr)
                audio_path_for_prediction = str(clean_path)
                preproc_stage.metadata["cleaned"] = True
            else:
                preproc_stage.metadata["cleaned"] = False

        # Basic-pitch inference (GPU)
        with profiler.profile("midi_extraction/basic_pitch") as bp_stage:
            gpu_mem_before = gpu_monitor.get_stats().memory_allocated_mb

            inference_start = time.perf_counter()

            try:
                model_output, midi_data, note_events = predict(
                    audio_path_for_prediction,
                    model_or_model_path=ICASSP_2022_MODEL_PATH,
                    onset_threshold=onset_threshold,
                    frame_threshold=frame_threshold,
                    minimum_note_length=min_note_length_ms / 1000.0,
                    midi_tempo=120.0,
                )
            finally:
                if clean_path and clean_path.exists():
                    clean_path.unlink()

            inference_time = (time.perf_counter() - inference_start) * 1000

            # Synchronize GPU for accurate timing
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.synchronize()
                elif torch.backends.mps.is_available():
                    torch.mps.synchronize()
            except Exception:
                pass

            gpu_mem_after = gpu_monitor.get_stats().memory_allocated_mb

            profiler.record_gpu_time("midi_extraction/basic_pitch", inference_time)

            bp_stage.metadata["inference_time_ms"] = round(inference_time, 2)
            bp_stage.metadata["gpu_memory_delta_mb"] = round(gpu_mem_after - gpu_mem_before, 2)
            bp_stage.metadata["raw_note_count"] = len(note_events) if note_events else 0

        # Tempo detection
        with profiler.profile("midi_extraction/tempo_detection") as tempo_stage:
            tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
            if hasattr(tempo, '__iter__'):
                tempo = float(tempo[0]) if len(tempo) > 0 else 120.0
            tempo = float(tempo) if tempo > 0 else 120.0

            tempo_stage.metadata["detected_tempo"] = tempo

        # MIDI creation
        with profiler.profile("midi_extraction/midi_creation") as midi_stage:
            new_midi = pretty_midi.PrettyMIDI(initial_tempo=tempo)
            total_notes = 0

            for old_inst in midi_data.instruments:
                new_inst = pretty_midi.Instrument(
                    program=old_inst.program,
                    is_drum=old_inst.is_drum,
                    name=old_inst.name,
                )

                for note in old_inst.notes:
                    # Keep notes in original time base (seconds)
                    new_inst.notes.append(pretty_midi.Note(
                        velocity=note.velocity,
                        pitch=note.pitch,
                        start=note.start,
                        end=note.end,
                    ))
                    total_notes += 1

                new_midi.instruments.append(new_inst)

            midi_stage.metadata["note_count"] = total_notes

        stage.metadata["note_count"] = total_notes

        # Check if we need to run the multi-pass pipeline
        # Import the pipeline if available
        try:
            from tone_forge.midi.extraction_pipeline import MultiPassExtractor
            from tone_forge.midi.profiles import get_profile_for_stem

            with profiler.profile("midi_extraction/pass_pipeline") as pipeline_stage:
                # Get extraction profile
                extraction_profile = get_profile_for_stem(stem_type)

                # Create context for passes
                from tone_forge.midi.passes.base import ExtractionContext, ExtractedNote

                context = ExtractionContext(
                    audio=y,
                    sr=sr,
                    stem_type=stem_type,
                    genre=genre,
                    tempo=tempo,
                )

                # Convert notes to ExtractedNote format
                extracted_notes = []
                for inst in new_midi.instruments:
                    for note in inst.notes:
                        extracted_notes.append(ExtractedNote(
                            pitch=note.pitch,
                            start=note.start,
                            end=note.end,
                            velocity=note.velocity,
                            confidence=0.8,  # Default confidence from basic-pitch
                            source_pass=0,
                        ))

                pipeline_stage.metadata["input_notes"] = len(extracted_notes)

                # Run multi-pass extraction
                extractor = MultiPassExtractor(profile=extraction_profile)

                # Profile each pass individually
                passes = extractor._create_passes(extraction_profile, context)
                current_notes = extracted_notes

                for pass_obj in passes:
                    pass_name = pass_obj.name
                    with profiler.profile(f"midi_extraction/pass_pipeline/{pass_name}"):
                        result = pass_obj.process(current_notes, context)
                        current_notes = result.notes

                pipeline_stage.metadata["output_notes"] = len(current_notes)
                pipeline_stage.metadata["pass_count"] = len(passes)

                # Update MIDI with processed notes
                new_midi = pretty_midi.PrettyMIDI(initial_tempo=tempo)
                new_inst = pretty_midi.Instrument(program=0, name=preset_name)

                for note in current_notes:
                    new_inst.notes.append(pretty_midi.Note(
                        velocity=note.velocity,
                        pitch=note.pitch,
                        start=note.start,
                        end=note.end,
                    ))

                new_midi.instruments.append(new_inst)

        except ImportError:
            logger.debug("Multi-pass pipeline not available")

        # MIDI serialization
        with profiler.profile("midi_extraction/serialization") as serial_stage:
            import base64
            import io

            midi_buffer = io.BytesIO()
            new_midi.write(midi_buffer)
            midi_buffer.seek(0)
            midi_base64 = base64.b64encode(midi_buffer.read()).decode('utf-8')

            serial_stage.metadata["midi_size_bytes"] = len(midi_base64) * 3 // 4

        # Create result
        # Get note count and pitch range
        all_notes = []
        for inst in new_midi.instruments:
            all_notes.extend([n.pitch for n in inst.notes])

        note_count = len(all_notes)
        if all_notes:
            pitch_range = (min(all_notes), max(all_notes))
        else:
            pitch_range = (0, 0)

        # Sanitize filename
        safe_name = "".join(c if c.isalnum() or c in " -_" else "" for c in preset_name).strip()
        if not safe_name:
            safe_name = "Extracted MIDI"

        return MIDIExtractionResult(
            filename=f"{safe_name}.mid",
            content=midi_base64,
            note_count=note_count,
            duration_seconds=duration,
            tempo_bpm=tempo,
            pitch_range=pitch_range,
        )


def get_midi_extraction_summary(report) -> Dict:
    """Extract MIDI extraction summary from profile report.

    Args:
        report: ProfileReport from profiler

    Returns:
        Summary dictionary
    """
    summary = {
        "total_time_ms": 0,
        "audio_load_ms": 0,
        "genre_detection_ms": 0,
        "basic_pitch_ms": 0,
        "pass_pipeline_ms": 0,
        "per_pass_ms": {},
        "gpu_time_ms": 0,
        "note_count": 0,
    }

    for name, stage in report.stages.items():
        if name == "midi_extraction":
            summary["total_time_ms"] = stage.wall_time_ms
            summary["note_count"] = stage.metadata.get("note_count", 0)
        elif name == "midi_extraction/audio_load":
            summary["audio_load_ms"] = stage.wall_time_ms
        elif name == "midi_extraction/genre_detection":
            summary["genre_detection_ms"] = stage.wall_time_ms
        elif name == "midi_extraction/basic_pitch":
            summary["basic_pitch_ms"] = stage.wall_time_ms
            summary["gpu_time_ms"] = stage.gpu_time_ms
        elif name == "midi_extraction/pass_pipeline":
            summary["pass_pipeline_ms"] = stage.wall_time_ms
        elif name.startswith("midi_extraction/pass_pipeline/"):
            pass_name = name.split("/")[-1]
            summary["per_pass_ms"][pass_name] = stage.wall_time_ms

    return summary
