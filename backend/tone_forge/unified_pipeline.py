"""Unified analysis pipeline for all ToneForge endpoints.

This module provides a single pipeline that handles:
- File uploads (sync and streaming)
- YouTube URL analysis (sync and streaming)
- Regional analysis
- Quality analysis
- Deep analysis with profiling

All endpoints call into this pipeline with appropriate configuration,
ensuring consistent features and response structure across the app.
"""
from __future__ import annotations

import asyncio
import base64
import concurrent.futures
import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import (
    Any, AsyncGenerator, Callable, Dict, List, Literal,
    Optional, Tuple, Union
)

import numpy as np
import librosa
from dataclasses import is_dataclass

logger = logging.getLogger(__name__)

# =============================================================================
# Shared Process Pool for CPU-bound operations (avoids GIL)
# =============================================================================
# ProcessPoolExecutor runs work in separate processes, completely bypassing
# Python's GIL. This keeps the main event loop responsive during heavy CPU work.
_CPU_WORKERS = int(os.environ.get("TONEFORGE_CPU_WORKERS", "2"))
_cpu_executor: Optional[concurrent.futures.ProcessPoolExecutor] = None
_thread_executor: Optional[concurrent.futures.ThreadPoolExecutor] = None


def get_cpu_executor() -> concurrent.futures.ProcessPoolExecutor:
    """Get the shared CPU process pool executor."""
    global _cpu_executor
    if _cpu_executor is None:
        _cpu_executor = concurrent.futures.ProcessPoolExecutor(
            max_workers=_CPU_WORKERS,
        )
        logger.info(f"Created process pool with {_CPU_WORKERS} workers (GIL-free)")
    return _cpu_executor


def get_thread_executor() -> concurrent.futures.ThreadPoolExecutor:
    """Get a thread pool for I/O-bound or unpicklable operations."""
    global _thread_executor
    if _thread_executor is None:
        _thread_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=4,
            thread_name_prefix="toneforge_io"
        )
    return _thread_executor


async def run_in_thread(func, *args, **kwargs):
    """Run a function in thread pool (for I/O or unpicklable closures)."""
    loop = asyncio.get_event_loop()
    if kwargs:
        def wrapper():
            return func(*args, **kwargs)
        return await loop.run_in_executor(get_thread_executor(), wrapper)
    return await loop.run_in_executor(get_thread_executor(), func, *args)


def _serialize_obj(obj: Any) -> Any:
    """Convert any object to a JSON-serializable format.

    Handles dataclasses, objects with to_dict methods, numpy types, etc.
    """
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        val = float(obj)
        if np.isnan(val) or np.isinf(val):
            return None
        return val
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, dict):
        return {k: _serialize_obj(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_serialize_obj(v) for v in obj]
    if hasattr(obj, "to_dict"):
        return _serialize_obj(obj.to_dict())
    if is_dataclass(obj) and not isinstance(obj, type):
        return _serialize_obj(asdict(obj))
    # Fallback - try to convert to dict or string
    if hasattr(obj, "__dict__"):
        return _serialize_obj(vars(obj))
    return str(obj)


# =============================================================================
# Configuration
# =============================================================================

class AnalysisMode(str, Enum):
    """Analysis mode determines the depth of processing."""
    FAST = "fast"           # Quick preview, no stem separation
    STANDARD = "standard"   # Balanced quality/speed
    DEEP = "deep"           # Full analysis with all features


@dataclass
class PipelineConfig:
    """Configuration for the unified analysis pipeline.

    Use factory methods for common configurations:
    - PipelineConfig.fast() - Quick preview mode
    - PipelineConfig.standard() - Default balanced mode
    - PipelineConfig.deep() - Full analysis with all features
    """
    # Mode
    mode: AnalysisMode = AnalysisMode.STANDARD

    # Feature flags
    separate_stems: bool = False      # Demucs stem separation
    force_stem_separation: bool = False  # Separate stems even for non-full-mix content
    extract_midi: bool = True         # MIDI extraction
    use_ensemble: bool = True         # Multi-detector ensemble (vs basic-pitch only)
    analyze_quality: bool = True      # Reconstruction quality analysis
    include_provenance: bool = True   # Track extraction decisions
    detect_synth_behavior: bool = True  # Synth-specific detection

    # Audio handling
    trim_start: Optional[float] = None
    trim_end: Optional[float] = None
    max_duration: float = 300.0       # 5 minutes default
    target_sr: int = 22050

    # Output options
    include_waveform: bool = False    # Return waveform data for visualization
    include_profiling: bool = False   # Return timing data
    stem_serve_url_base: Optional[str] = None  # Base URL for serving stem files (e.g., "/api/admin/serve-file")

    # Source metadata
    source_url: Optional[str] = None
    source_name: Optional[str] = None

    @classmethod
    def fast(cls) -> "PipelineConfig":
        """Quick preview mode - no stem separation, minimal features."""
        return cls(
            mode=AnalysisMode.FAST,
            separate_stems=False,
            extract_midi=True,
            use_ensemble=False,  # Use basic-pitch only for speed
            analyze_quality=False,
            include_provenance=False,
            detect_synth_behavior=False,
            include_waveform=False,
            include_profiling=False,
        )

    @classmethod
    def standard(cls) -> "PipelineConfig":
        """Standard mode - balanced quality and speed."""
        return cls(
            mode=AnalysisMode.STANDARD,
            separate_stems=False,
            extract_midi=True,
            use_ensemble=True,
            analyze_quality=True,
            include_provenance=True,
            detect_synth_behavior=True,
            include_waveform=True,  # Enable for studio visualization
            include_profiling=True,  # Enable for Technical tab
        )

    @classmethod
    def deep(cls) -> "PipelineConfig":
        """Deep analysis - full features with stem separation."""
        return cls(
            mode=AnalysisMode.DEEP,
            separate_stems=True,
            force_stem_separation=True,  # Always separate, even for isolated content
            extract_midi=True,
            use_ensemble=True,
            analyze_quality=True,
            include_provenance=True,
            detect_synth_behavior=True,
            include_waveform=True,
            include_profiling=True,
            stem_serve_url_base="/api/admin/serve-file",  # Default for web playback
        )


# =============================================================================
# Result Types
# =============================================================================

@dataclass
class AudioData:
    """Loaded audio data with metadata."""
    audio: np.ndarray
    sr: int
    duration: float
    path: Path
    source_type: Literal["file", "url", "upload"]
    source_name: str
    source_url: Optional[str] = None


@dataclass
class DetectionResult:
    """Audio content detection result."""
    is_full_mix: bool
    is_guitar: bool
    is_bass: bool
    is_drums: bool
    is_synth: bool
    is_vocals: bool
    detected_type: str  # Primary detected type
    summary: str
    confidence: Dict[str, float]
    genre: Optional[str] = None


@dataclass
class StemResult:
    """Analysis result for a single stem."""
    stem_type: str
    audio_path: Optional[Path]

    # MIDI extraction
    midi_data: Optional[Dict[str, Any]] = None
    midi_path: Optional[Path] = None

    # Quality analysis
    quality: Optional[Dict[str, Any]] = None
    confidence_map: Optional[Dict[str, Any]] = None

    # Provenance
    provenance: Optional[Dict[str, Any]] = None

    # Synth behavior (if detected as synth)
    synth_behavior: Optional[Dict[str, Any]] = None


@dataclass
class AnalysisResult:
    """Complete analysis result returned by the pipeline."""
    # Source info
    source_name: str
    source_url: Optional[str]
    duration_sec: float
    sample_rate: int

    # Detection
    detection: DetectionResult
    detected_type: str  # Backward compatibility

    # Per-instrument results
    guitar: Optional[Dict[str, Any]] = None
    bass: Optional[Dict[str, Any]] = None
    drums: Optional[Dict[str, Any]] = None
    synth: Optional[Dict[str, Any]] = None

    # MIDI
    midi: Optional[Dict[str, Any]] = None
    midi_stems: Optional[Dict[str, Dict[str, Any]]] = None

    # Stems (paths for playback)
    stems: Optional[Dict[str, str]] = None
    stems_paths: Optional[Dict[str, str]] = None  # Alias for compatibility

    # V2 preset matches per stem (for reconstruction export)
    preset_matches: Optional[Dict[str, Dict[str, Any]]] = None

    # Quality
    quality: Optional[Dict[str, Any]] = None

    # Aggregated provenance
    provenance: Optional[Dict[str, Any]] = None

    # Visualization (peaks_positive, peaks_negative, rms, duration_sec, sample_rate)
    waveform: Optional[Dict[str, Any]] = None

    # Profiling
    profiling: Optional[Dict[str, Any]] = None

    # Arrangement / sections
    sections: Optional[List[Dict[str, Any]]] = None
    energy_curve: Optional[List[float]] = None

    # Beat grid (hoisted Phase 7 — single source of truth feeding both
    # section detection and chord-lane snap). Persisting these here is
    # what closes the historical "tempo=0.0, beats_s=()" UI bug: the
    # values were being computed inside _detect_sections /
    # _detect_chord_lane and silently dropped on the floor before this
    # commit. ``downbeats_s`` is derived from ``beats_s`` at the
    # detected time signature (every 4th beat at 4/4); a smarter
    # downbeat tracker can replace the derivation without changing the
    # field contract.
    tempo_bpm: float = 0.0
    beats_s: Optional[List[float]] = None
    downbeats_s: Optional[List[float]] = None

    # Detected musical key (Phase-7+ hoist — same defensibility
    # pattern as tempo/beats). chord_detector internally runs
    # Krumhansl-Schmuckler + optional bass-tiebreak to anchor the
    # diatonic-bias scoring; previously the result was logged then
    # dropped on the floor, leaving downstream re-spelling and
    # key-aware UI blind. Now lifted to the top of the result so
    # ``bundle._resolve_key`` and the chord ribbon can consume it
    # directly.
    #
    # ``detected_key`` is the human label ("F minor", "E major") —
    # the source of truth for display + enharmonic re-spelling.
    # ``detected_key_root`` is the 0-11 pitch-class index
    # (5 = F, 4 = E, etc.) for code that needs numeric arithmetic.
    # ``detected_key_strength`` is the Krumhansl top-1 vs top-2
    # margin, normalised to [0, 1]; values near 0 mean the key
    # picker is essentially guessing.
    detected_key: Optional[str] = None
    detected_key_root: Optional[int] = None
    detected_key_strength: float = 0.0

    # Chord lane (P4a wire-up).
    # Persisted shape matches ``analysis.chords.detect_chords`` -> contracts.Chord:
    # ``[{"start_s": float, "end_s": float, "symbol": str, "confidence": float}, ...]``.
    # Bundle assembler reads this directly into ``SongUnderstanding.chords``.
    chords: Optional[List[Dict[str, Any]]] = None
    # Phase 6 (hybrid + UI toggle): beat-snapped chord regions for the
    # Jam ribbon's "snap to beats" toggle. Same shape as ``chords``;
    # ``None`` when beat tracking failed or fewer than 2 regions
    # surfaced (frontend disables the toggle in that case).
    chords_beat_snapped: Optional[List[Dict[str, Any]]] = None

    # Backward compatibility
    type: Optional[str] = None
    descriptor: Optional[Dict[str, Any]] = None
    chain: Optional[List[Dict[str, Any]]] = None
    tweak_hints: Optional[List[str]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        result = {
            "source_name": self.source_name,
            "source_url": self.source_url,
            "duration_sec": self.duration_sec,
            "sample_rate": self.sample_rate,
            "detected_type": self.detected_type,
            "detection": {
                "is_full_mix": self.detection.is_full_mix,
                "is_guitar": self.detection.is_guitar,
                "is_bass": self.detection.is_bass,
                "is_drums": self.detection.is_drums,
                "is_synth": self.detection.is_synth,
                "summary": self.detection.summary,
                "confidence": self.detection.confidence,
            },
            # Role object for frontend compatibility
            "role": {
                "primary_role": self.detected_type,
                "role": self.detected_type,
                "confidence": self.detection.confidence.get(self.detected_type, 0.8),
            },
        }

        # Add optional fields
        for field_name in ["guitar", "bass", "drums", "synth", "midi",
                          "midi_stems", "stems", "stems_paths", "preset_matches",
                          "quality",
                          "provenance", "waveform", "profiling",
                          "sections", "energy_curve",
                          "beats_s", "downbeats_s",
                          "detected_key", "detected_key_root",
                          "chords",
                          "chords_beat_snapped",
                          "type", "descriptor", "chain", "tweak_hints"]:
            value = getattr(self, field_name)
            if value is not None:
                result[field_name] = value

        # tempo_bpm is a non-Optional float (default 0.0). Persist it
        # unconditionally — bundle/jam UI key off `> 0` to decide
        # whether to render the BPM readout, so emitting 0.0 is the
        # honest "no tempo detected" signal.
        result["tempo_bpm"] = float(self.tempo_bpm or 0.0)
        # detected_key_strength is a non-Optional float (default 0.0).
        # Persist unconditionally for the same reason as tempo_bpm:
        # downstream callers branch on `> 0` to gate key-aware
        # re-spelling, and "0.0" is the honest "no key inferred"
        # signal.
        result["detected_key_strength"] = float(self.detected_key_strength or 0.0)

        # Add midi_stats for frontend
        if self.midi:
            result["midi_stats"] = {
                "note_count": self.midi.get("note_count", 0),
                "tempo": self.midi.get("tempo"),
                "polyphony": self.midi.get("polyphony"),
                "confidence": self.midi.get("confidence", 0),
            }

        # Add flattened timbral characteristics for visualization
        # Extract 0-1 numeric values from nested descriptor structures
        timbral = {}
        if self.descriptor:
            # Guitar descriptor
            if "guitar" in self.descriptor:
                guitar = self.descriptor["guitar"]
                if isinstance(guitar, dict) and "pickup_brightness" in guitar:
                    timbral["brightness"] = guitar.get("pickup_brightness", 0.5)
            if "amp" in self.descriptor:
                amp = self.descriptor["amp"]
                if isinstance(amp, dict):
                    timbral["gain"] = amp.get("gain", 0.5)
                    voicing = amp.get("voicing", {})
                    if isinstance(voicing, dict):
                        timbral["bass"] = voicing.get("bass", 0.5)
                        timbral["mid"] = voicing.get("mid", 0.5)
                        timbral["treble"] = voicing.get("treble", 0.5)
                        timbral["presence"] = voicing.get("presence", 0.5)
            if "effects" in self.descriptor:
                effects = self.descriptor["effects"]
                if isinstance(effects, dict):
                    if "reverb" in effects and isinstance(effects["reverb"], dict):
                        timbral["reverb"] = effects["reverb"].get("mix", 0.0)
                    if "delay" in effects and isinstance(effects["delay"], dict):
                        timbral["delay"] = effects["delay"].get("mix", 0.0)
            # Synth descriptor values at top level
            for key in ["brightness", "movement", "stereo_width"]:
                if key in self.descriptor and isinstance(self.descriptor[key], (int, float)):
                    val = self.descriptor[key]
                    if 0 <= val <= 1:
                        timbral[key] = val

        if timbral:
            result["timbral"] = timbral

        return result


# =============================================================================
# Progress Events (for SSE streaming)
# =============================================================================

@dataclass
class ProgressEvent:
    """Progress event for streaming responses."""
    stage: str
    message: str
    percent: int
    data: Optional[Dict[str, Any]] = None


# =============================================================================
# Unified Pipeline
# =============================================================================

class UnifiedPipeline:
    """Single pipeline for all analysis endpoints.

    This class orchestrates all analysis components:
    - Audio loading (files, URLs, uploads)
    - Content detection
    - Stem separation (Demucs)
    - MIDI extraction (ensemble or basic-pitch)
    - Quality analysis (reconstruction pipeline)
    - Synth behavior detection
    - Provenance tracking

    Usage:
        pipeline = UnifiedPipeline()

        # Sync analysis
        result = await pipeline.analyze(file_path, PipelineConfig.fast())

        # Streaming analysis with progress
        async for event in pipeline.analyze_streaming(url, PipelineConfig.deep()):
            print(event)
    """

    def __init__(self):
        self._stem_separator = None
        self._ensemble_extractor = None
        self._reconstruction_pipeline = None
        self._synth_analyzer = None

    # -------------------------------------------------------------------------
    # Main Entry Points
    # -------------------------------------------------------------------------

    async def analyze(
        self,
        source: Union[Path, str],
        config: PipelineConfig = None,
    ) -> AnalysisResult:
        """Analyze audio and return complete result.

        Args:
            source: File path or YouTube URL
            config: Pipeline configuration (defaults to standard mode)

        Returns:
            Complete analysis result
        """
        if config is None:
            config = PipelineConfig.standard()

        # Collect all events and return final result
        result = None
        async for event in self.analyze_streaming(source, config):
            if isinstance(event, AnalysisResult):
                result = event

        return result

    async def analyze_streaming(
        self,
        source: Union[Path, str],
        config: PipelineConfig = None,
    ) -> AsyncGenerator[Union[ProgressEvent, AnalysisResult], None]:
        """Analyze audio with streaming progress events.

        Yields ProgressEvent objects during analysis, then final AnalysisResult.

        Args:
            source: File path or YouTube URL
            config: Pipeline configuration

        Yields:
            ProgressEvent during processing, AnalysisResult at end
        """
        if config is None:
            config = PipelineConfig.standard()

        stage_timings = {} if config.include_profiling else None
        total_start = time.time()

        try:
            # 1. Load audio
            yield ProgressEvent("loading", "Loading audio...", 5)
            stage_start = time.time()
            audio_data = await self._load_audio(source, config)
            if stage_timings is not None:
                stage_timings["audio_loading"] = {
                    "duration_ms": (time.time() - stage_start) * 1000
                }

            # 2. Detect content type
            yield ProgressEvent("detection", "Detecting content type...", 10)
            stage_start = time.time()
            detection = await self._detect_content(audio_data, config)
            if stage_timings is not None:
                stage_timings["detection"] = {
                    "duration_ms": (time.time() - stage_start) * 1000
                }

            yield ProgressEvent(
                "detection",
                f"Detected: {detection.summary}",
                15,
                {"detected_type": detection.detected_type}
            )

            # 3. Separate stems (if configured)
            stems = {}
            should_separate = config.separate_stems and (detection.is_full_mix or config.force_stem_separation)
            if should_separate:
                yield ProgressEvent("stems", "Separating stems (GPU)...", 20)
                stage_start = time.time()
                stems = await self._separate_stems(audio_data, config)
                if stage_timings is not None:
                    stage_timings["stem_separation"] = {
                        "duration_ms": (time.time() - stage_start) * 1000,
                        "gpu_used": True,
                        "stems": list(stems.keys()),
                    }
                yield ProgressEvent(
                    "stems",
                    f"Separated {len(stems)} stems",
                    40,
                    {"stems": list(stems.keys())}
                )

            # 4. Analyze each stem quality (if configured)
            stem_results = {}
            if config.analyze_quality:
                yield ProgressEvent("quality", "Analyzing audio quality...", 45)
                stage_start = time.time()
                stem_results = await self._analyze_stems(
                    audio_data, stems, detection, config, stage_timings
                )
                if stage_timings is not None:
                    stage_timings["quality_analysis"] = {
                        "duration_ms": (time.time() - stage_start) * 1000
                    }

            # 5. Analyze detected instruments
            yield ProgressEvent("analysis", "Analyzing instruments...", 50)
            stage_start = time.time()
            instrument_results = await self._analyze_instruments(
                audio_data, stems, detection, config
            )
            if stage_timings is not None:
                stage_timings["instrument_analysis"] = {
                    "duration_ms": (time.time() - stage_start) * 1000,
                    "instruments": list(instrument_results.keys()),
                }

            # 6. Extract MIDI
            midi_stems = {}
            if config.extract_midi:
                yield ProgressEvent("midi", "Extracting MIDI...", 70)
                stage_start = time.time()
                midi_stems = await self._extract_midi(
                    audio_data, stems, detection, config
                )
                if stage_timings is not None:
                    stage_timings["midi_extraction"] = {
                        "duration_ms": (time.time() - stage_start) * 1000,
                        "stems_extracted": list(midi_stems.keys()),
                    }
                yield ProgressEvent(
                    "midi",
                    f"Extracted MIDI from {len(midi_stems)} stems",
                    85,
                    {"midi_stems": list(midi_stems.keys())}
                )

            # 6.5 Track beats (Phase 7: hoisted single-source-of-truth).
            #
            # Previously the beat grid was computed twice — once inside
            # ``_detect_sections`` (only persisted as ``tempo_bpm`` and
            # *then dropped on the floor* by the caller at this site),
            # and once inside ``_detect_chord_lane`` (used only for the
            # local snap step, never returned). The result was a UI
            # that read ``tempo_bpm = 0.0`` and ``beats_s = []`` on
            # every session, even when librosa had cleanly tracked the
            # beats. Hoisting fixes the root cause: a single call here,
            # consumed by both downstream stages and persisted in the
            # AnalysisResult.
            yield ProgressEvent("beats", "Tracking beats...", 86)
            stage_start = time.time()
            beat_grid = await self._track_beats(audio_data, stems)
            tempo_bpm = beat_grid["tempo_bpm"]
            beats_s = beat_grid["beats_s"]
            downbeats_s = beat_grid["downbeats_s"]
            if stage_timings is not None:
                stage_timings["beat_tracking"] = {
                    "duration_ms": (time.time() - stage_start) * 1000,
                    "tempo_bpm": tempo_bpm,
                    "beats_detected": len(beats_s),
                }

            # 7. Detect arrangement sections
            sections = None
            energy_curve = None
            yield ProgressEvent("sections", "Analyzing arrangement...", 87)
            stage_start = time.time()
            try:
                section_result = await self._detect_sections(
                    audio_data, midi_stems, tempo_hint=tempo_bpm,
                )
                if section_result:
                    sections = section_result.get("sections")
                    energy_curve = section_result.get("energy_curve")
                    # Belt-and-braces: if the hoisted beat tracker
                    # failed (tempo_bpm==0) but the section detector
                    # successfully recovered one, accept it.
                    if (not tempo_bpm or tempo_bpm <= 0) and section_result.get("tempo_bpm"):
                        tempo_bpm = float(section_result["tempo_bpm"])
            except Exception as e:
                logger.warning(f"Section detection failed: {e}")
            if stage_timings is not None:
                stage_timings["section_detection"] = {
                    "duration_ms": (time.time() - stage_start) * 1000,
                    "sections_found": len(sections) if sections else 0,
                }

            # 7.5 Detect chord lane (P4a wire-up — analysis subsystem).
            # Cheap librosa chroma+template pass. Skipped when the audio is
            # not a full mix / not guitar-y (e.g. drum-only stems) because
            # the result would be noise and Jam wouldn't display it.
            chords = None
            chords_beat_snapped = None
            # Phase-7+: detected_key surfaces out of the chord-lane
            # stage (chord_detector internally runs Krumhansl + the
            # bass-tiebreak; ``detect_chords_with_key`` returns it
            # alongside the chord records).
            detected_key: Optional[str] = None
            detected_key_root: Optional[int] = None
            detected_key_strength: float = 0.0
            if detection.is_full_mix or detection.is_guitar:
                yield ProgressEvent("chords", "Detecting chord lane...", 89)
                stage_start = time.time()
                try:
                    chord_lane = await self._detect_chord_lane(
                        audio_data, stems, beats_s=beats_s,
                    )
                    if chord_lane is not None:
                        chords = chord_lane.get("fixed")
                        chords_beat_snapped = chord_lane.get("snapped")
                        key_dict = chord_lane.get("key") or {}
                        if key_dict.get("label"):
                            detected_key = str(key_dict["label"])
                            detected_key_root = (
                                int(key_dict["root"])
                                if key_dict.get("root") is not None else None
                            )
                            detected_key_strength = float(
                                key_dict.get("strength", 0.0) or 0.0
                            )
                except Exception as e:  # pragma: no cover - defensive
                    logger.warning(f"Chord detection failed: {e}")
                if stage_timings is not None:
                    stage_timings["chord_detection"] = {
                        "duration_ms": (time.time() - stage_start) * 1000,
                        "chords_found": len(chords) if chords else 0,
                    }

            # 7.6 Per-section guidance-mode classification (chord/riff/lead).
            # Runs after chord detection so chord_density can feed the
            # classifier, and before _build_result so the persisted
            # AnalysisResult carries one (mode, confidence, reason) triple
            # per section. The chord detector is unchanged — we just gate
            # the display.
            if sections:
                try:
                    from tone_forge.analysis.guidance_mode import classify_section
                    from tone_forge.analysis.section_features import (
                        compute_section_features,
                    )

                    chord_regions = chords or ()
                    stem_notes_by_name = {
                        name: (data.get("notes") or [])
                        for name, data in (midi_stems or {}).items()
                    }
                    classified = []
                    for section in sections:
                        per_stem = [
                            compute_section_features(
                                stem_name=name,
                                stem_midi=notes,
                                chord_regions=chord_regions,
                                section_start_s=float(section.start_time),
                                section_end_s=float(section.end_time),
                                beats_s=beats_s,
                            )
                            for name, notes in stem_notes_by_name.items()
                        ]
                        decision = classify_section(per_stem)
                        section.guidance_mode = decision.mode
                        section.guidance_confidence = float(decision.confidence)
                        section.guidance_reason = decision.reason
                        classified.append(section)
                    sections = classified
                except Exception as e:  # pragma: no cover - defensive
                    logger.warning(f"Guidance-mode classification failed: {e}")

            # 8. Generate waveform visualization
            waveform = None
            if config.include_waveform:
                yield ProgressEvent("waveform", "Generating waveform...", 92)
                waveform = self._generate_waveform(audio_data)

            # 9. Build final result
            yield ProgressEvent("finalizing", "Finalizing results...", 95)

            if stage_timings is not None:
                stage_timings["total_ms"] = (time.time() - total_start) * 1000
                stage_timings["audio_duration_sec"] = audio_data.duration
                stage_timings["processing_ratio"] = (
                    stage_timings["total_ms"] / 1000 / audio_data.duration
                    if audio_data.duration > 0 else 0
                )

            result = self._build_result(
                audio_data, detection, stems, stem_results, instrument_results,
                midi_stems, waveform, sections, energy_curve, chords,
                chords_beat_snapped,
                stage_timings, config,
                tempo_bpm=tempo_bpm,
                beats_s=beats_s,
                downbeats_s=downbeats_s,
                detected_key=detected_key,
                detected_key_root=detected_key_root,
                detected_key_strength=detected_key_strength,
            )

            yield ProgressEvent("complete", "Analysis complete", 100)
            yield result

        except Exception as e:
            logger.exception(f"Pipeline error: {e}")
            raise

    # -------------------------------------------------------------------------
    # Pipeline Stages
    # -------------------------------------------------------------------------

    async def _load_audio(
        self,
        source: Union[Path, str],
        config: PipelineConfig,
    ) -> AudioData:
        """Load audio from file or URL."""
        source_str = str(source)

        # Determine source type
        if source_str.startswith(("http://", "https://", "youtu.be", "youtube.com")):
            return await self._load_from_url(source_str, config)
        else:
            return await self._load_from_file(Path(source_str), config)

    async def _load_from_file(
        self,
        path: Path,
        config: PipelineConfig,
    ) -> AudioData:
        """Load audio from local file."""
        def load():
            y, sr = librosa.load(str(path), sr=config.target_sr, mono=True)

            # Apply trim if configured
            if config.trim_start is not None or config.trim_end is not None:
                start_sample = int((config.trim_start or 0) * sr)
                end_sample = int((config.trim_end or len(y) / sr) * sr)
                y = y[start_sample:end_sample]

            # Apply max duration limit
            max_samples = int(config.max_duration * sr)
            if len(y) > max_samples:
                y = y[:max_samples]

            return y, sr

        audio, sr = await run_in_thread(load)

        return AudioData(
            audio=audio,
            sr=sr,
            duration=len(audio) / sr,
            path=path,
            source_type="file",
            source_name=config.source_name or path.stem,
            source_url=config.source_url,
        )

    async def _load_from_url(
        self,
        url: str,
        config: PipelineConfig,
    ) -> AudioData:
        """Load audio from a URL via the acquisition subsystem."""
        from tone_forge.acquisition.youtube import download_audio

        def download_and_load():
            return download_audio(
                url,
                target_sr=config.target_sr,
                max_duration_s=config.max_duration,
                trim_start_s=config.trim_start,
                trim_end_s=config.trim_end,
            )

        audio, sr, path, title = await run_in_thread(download_and_load)

        return AudioData(
            audio=audio,
            sr=sr,
            duration=len(audio) / sr,
            path=path,
            source_type="url",
            source_name=config.source_name or title,
            source_url=url,
        )

    async def _detect_content(
        self,
        audio_data: AudioData,
        config: PipelineConfig,
    ) -> DetectionResult:
        """Detect audio content type."""
        from tone_forge import auto_detect


        def detect():
            detection = auto_detect.detect_audio_type(str(audio_data.path))

            # Determine primary type - prioritize melodic instruments for tone analysis
            if detection.is_guitar:
                detected_type = "guitar"
            elif detection.is_bass:
                detected_type = "bass"
            elif detection.is_synth:
                detected_type = "synth"
            elif detection.is_drums:
                detected_type = "drums"
            else:
                detected_type = "guitar"  # Default

            # Build summary
            detected = []
            if detection.is_drums:
                detected.append("drums")
            if detection.is_synth:
                detected.append("synth")
            if detection.is_bass:
                detected.append("bass")
            if detection.is_guitar:
                detected.append("guitar")
            if hasattr(detection, "is_vocals") and detection.is_vocals:
                detected.append("vocals")

            summary = f"Detected: {', '.join(detected) if detected else 'unknown'}"

            return DetectionResult(
                is_full_mix=detection.is_full_mix,
                is_guitar=detection.is_guitar,
                is_bass=detection.is_bass,
                is_drums=detection.is_drums,
                is_synth=detection.is_synth,
                is_vocals=getattr(detection, "is_vocals", False),
                detected_type=detected_type,
                summary=summary,
                confidence=getattr(detection, "confidence", {}),
            )

        return await run_in_thread(detect)

    async def _separate_stems(
        self,
        audio_data: AudioData,
        config: PipelineConfig,
    ) -> Dict[str, Path]:
        """Separate audio into stems using Demucs."""
        try:
            from tone_forge import stem_separator
        except ImportError:
            logger.warning("Stem separator not available")
            return {}


        def separate():
            try:
                # Try 6-stem model first
                return stem_separator.separate_all_stems(
                    str(audio_data.path),
                    model_name="htdemucs_6s"
                )
            except Exception as e:
                logger.warning(f"6-stem model failed, falling back to 4-stem: {e}")
                return stem_separator.separate_all_stems(str(audio_data.path))

        stems = await run_in_thread(separate)

        return {name: Path(path) for name, path in stems.items()}

    async def _analyze_stems(
        self,
        audio_data: AudioData,
        stems: Dict[str, Path],
        detection: DetectionResult,
        config: PipelineConfig,
        stage_timings: Optional[Dict] = None,
    ) -> Dict[str, StemResult]:
        """Analyze each stem or the whole audio."""
        results = {}

        # Determine what to analyze
        if stems:
            # Analyze separated stems
            for stem_type, stem_path in stems.items():
                result = await self._analyze_single_stem(
                    stem_path, stem_type, detection, config
                )
                results[stem_type] = result
        else:
            # Analyze whole audio
            result = await self._analyze_single_stem(
                audio_data.path, detection.detected_type, detection, config
            )
            results[detection.detected_type] = result

        return results

    async def _analyze_single_stem(
        self,
        audio_path: Path,
        stem_type: str,
        detection: DetectionResult,
        config: PipelineConfig,
    ) -> StemResult:
        """Analyze a single stem or audio file."""
        result = StemResult(stem_type=stem_type, audio_path=audio_path)


        # Quality analysis (if configured)
        if config.analyze_quality:
            try:
                from tone_forge.reconstruction import ReconstructionPipeline

                def analyze_quality():
                    y, sr = librosa.load(str(audio_path), sr=22050, mono=True)
                    pipeline = ReconstructionPipeline()
                    analysis, quality_report = pipeline.analyze_only(y, sr, stem_type)
                    # Use _serialize_obj to ensure all nested objects are serializable
                    return _serialize_obj({
                        "stem_quality": getattr(analysis, "stem_quality", None),
                        "contamination": getattr(analysis, "contamination", None),
                        "artifacts": getattr(analysis, "artifacts", None),
                        "quality_report": quality_report,
                    })

                result.quality = await run_in_thread(analyze_quality)
            except Exception as e:
                logger.warning(f"Quality analysis failed for {stem_type}: {e}")

        # Synth behavior detection (if synth)
        if config.detect_synth_behavior and detection.is_synth:
            try:
                from tone_forge.analysis.synth_behavior import SynthBehaviorAnalyzer

                def analyze_synth():
                    y, sr = librosa.load(str(audio_path), sr=22050, mono=True)
                    analyzer = SynthBehaviorAnalyzer()
                    behavior = analyzer.analyze(y, sr)
                    return _serialize_obj(behavior)

                result.synth_behavior = await run_in_thread(analyze_synth)
            except Exception as e:
                logger.warning(f"Synth behavior analysis failed: {e}")

        return result

    async def _analyze_instruments(
        self,
        audio_data: AudioData,
        stems: Dict[str, Path],
        detection: DetectionResult,
        config: PipelineConfig,
    ) -> Dict[str, Dict[str, Any]]:
        """Analyze each detected instrument type."""
        results = {}

        # Determine which audio to use for each instrument
        audio_path = audio_data.path

        # Synth analysis (always run for synth content or as fallback)
        if detection.is_synth or not detection.is_guitar:
            try:
                from tone_forge import synth_analyzer

                def analyze_synth():
                    synth_desc = synth_analyzer.analyze_synth(str(audio_path))
                    return {
                        "descriptor": synth_desc.to_dict(),
                        "chain": [],
                        "tweak_hints": self._generate_synth_hints(synth_desc),
                    }

                results["synth"] = await run_in_thread(analyze_synth)
            except Exception as e:
                logger.warning(f"Synth analysis failed: {e}")

        # Guitar analysis
        if detection.is_guitar:
            try:
                from tone_forge import analyzer, helix_translator, translator

                # Use guitar stem if available
                guitar_audio_path = stems.get("guitar", stems.get("other", audio_path))

                def analyze_guitar():
                    source_kind = "isolated_guitar" if stems else "full_mix"
                    descriptor = analyzer.analyze(str(guitar_audio_path), source_kind=source_kind)

                    helix_card = helix_translator.translate(descriptor)
                    helix_chain = [asdict(p) for p in helix_card.picks]

                    pedal_card = translator.translate(descriptor, platform="pedals")
                    pedal_chain = [asdict(p) for p in pedal_card.picks]

                    return {
                        "descriptor": descriptor.to_dict(),
                        "platforms": {
                            "helix": helix_chain,
                            "pedals": pedal_chain,
                        },
                        "tweak_hints": helix_card.tweak_hints,
                    }

                results["guitar"] = await run_in_thread(analyze_guitar)
            except Exception as e:
                logger.warning(f"Guitar analysis failed: {e}")

        # Bass analysis
        if detection.is_bass:
            try:
                from tone_forge import bass_analyzer

                # Use bass stem if available
                bass_audio_path = stems.get("bass", audio_path)

                def analyze_bass():
                    bass_desc = bass_analyzer.analyze_bass(str(bass_audio_path))
                    return {
                        "descriptor": self._bass_descriptor_to_dict(bass_desc),
                        "recommendations": self._get_bass_recommendations(bass_desc),
                        "tweak_hints": self._generate_bass_hints(bass_desc),
                    }

                results["bass"] = await run_in_thread(analyze_bass)
            except Exception as e:
                logger.warning(f"Bass analysis failed: {e}")

        # Drums analysis
        if detection.is_drums:
            try:
                from tone_forge import drum_analyzer

                def analyze_drums():
                    drum_desc = drum_analyzer.analyze_drums(str(audio_path))
                    return {
                        "descriptor": self._drum_descriptor_to_dict(drum_desc),
                        "machine_match": drum_analyzer.match_drum_machine(drum_desc),
                        "tweak_hints": self._generate_drum_hints(drum_desc),
                    }

                results["drums"] = await run_in_thread(analyze_drums)
            except Exception as e:
                logger.warning(f"Drums analysis failed: {e}")

        return results

    def _generate_synth_hints(self, desc) -> List[str]:
        """Generate tweak hints for synth sounds."""
        hints = []

        if desc.oscillator.type == "saw":
            hints.append("Start with a sawtooth oscillator for this buzzy, harmonically-rich tone.")
        elif desc.oscillator.type == "square":
            hints.append("Use a square/pulse wave oscillator for this hollow, woody character.")
        elif desc.oscillator.type == "sine":
            hints.append("A pure sine wave will get you close to this smooth, fundamental-heavy tone.")

        if desc.oscillator.num_voices > 1:
            hints.append(f"Add unison with {desc.oscillator.num_voices} voices and ~{desc.oscillator.detune:.0f} cents detune for width.")

        if desc.filter.cutoff_normalized < 0.7:
            hints.append(f"Low-pass filter around {desc.filter.cutoff_hz:.0f}Hz gives this muffled character.")

        if desc.filter.resonance > 0.3:
            hints.append("Add some filter resonance for that characteristic 'quack'.")

        if desc.amp_envelope.attack_ms > 50:
            hints.append(f"Slow attack (~{desc.amp_envelope.attack_ms:.0f}ms) creates the pad-like swell.")
        elif desc.amp_envelope.attack_ms < 10:
            hints.append("Keep attack very short for punchy, percussive response.")

        if desc.lfo and desc.lfo.rate_hz > 0:
            hints.append(f"LFO at ~{desc.lfo.rate_hz:.1f}Hz modulating {desc.lfo.target} creates the movement.")

        if desc.has_chorus:
            hints.append("Chorus effect adds the stereo width and shimmer.")

        if desc.has_reverb:
            hints.append("Add reverb for the ambient, spacious quality.")

        return hints

    def _generate_bass_hints(self, desc) -> List[str]:
        """Generate tweak hints for bass sounds."""
        hints = []

        amp_family = desc.amp.family
        if amp_family == "ampeg_svt":
            hints.append("Classic Ampeg SVT tone - try driving the preamp for that signature growl.")
        elif amp_family == "darkglass":
            hints.append("Modern Darkglass tone - blend clean and dirty for clarity with grit.")
        elif amp_family == "fender_bassman":
            hints.append("Fender Bassman provides clean headroom with vintage sparkle.")

        if desc.amp.gain > 0.5:
            hints.append(f"Significant overdrive detected ({desc.amp.gain:.0%}) - consider a dedicated bass drive pedal.")

        if desc.technique == "slap":
            hints.append("Slap technique detected - boost high mids (~2-3kHz) for pop and cut lows for tightness.")
        elif desc.technique == "pick":
            hints.append("Pick attack detected - a slight mid boost brings out the percussive quality.")
        elif desc.technique == "fretless":
            hints.append("Fretless character - emphasize mids for that 'mwah' and consider subtle chorus.")

        if desc.effects.compressor > 0.4:
            hints.append("Heavy compression detected - try a bass compressor with slow attack to preserve transients.")

        if desc.effects.octaver > 0.3:
            hints.append("Sub-octave detected - an octave pedal like the Boss OC-3 or EHX POG will recreate this.")

        return hints

    def _generate_drum_hints(self, desc) -> List[str]:
        """Generate tweak hints for drum sounds."""
        hints = []

        if hasattr(desc, "kick"):
            if desc.kick.sub_presence > 0.7:
                hints.append("Strong sub bass on the kick - use a low shelf boost around 60Hz.")
            if desc.kick.attack_ms < 5:
                hints.append("Very punchy kick attack - try a transient shaper or fast compressor.")

        if hasattr(desc, "snare"):
            if desc.snare.ring_ms > 200:
                hints.append("Snare has a long ring - try adding a gated reverb.")
            if desc.snare.brightness > 0.7:
                hints.append("Bright snare tone - boost around 5kHz for that crisp crack.")

        if hasattr(desc, "machine_style"):
            if desc.machine_style == "808":
                hints.append("TR-808 character detected - long kick decay and snappy snare.")
            elif desc.machine_style == "909":
                hints.append("TR-909 character - punchy kick and crisp hi-hats.")

        return hints

    def _bass_descriptor_to_dict(self, desc) -> Dict[str, Any]:
        """Convert BassDescriptor to dictionary."""
        return {
            "source": {
                "duration_sec": getattr(desc, "duration_sec", 0),
            },
            "amp": {
                "family": desc.amp.family,
                "gain": desc.amp.gain,
                "eq": desc.amp.eq if hasattr(desc.amp, "eq") else None,
            },
            "technique": desc.technique,
            "effects": {
                "compressor": desc.effects.compressor,
                "octaver": desc.effects.octaver,
            } if hasattr(desc, "effects") else {},
        }

    def _get_bass_recommendations(self, desc) -> List[Dict[str, Any]]:
        """Get bass equipment recommendations."""
        recs = []
        if desc.amp.family == "ampeg_svt":
            recs.append({"type": "amp", "name": "Ampeg SVT", "confidence": 0.9})
        elif desc.amp.family == "darkglass":
            recs.append({"type": "pedal", "name": "Darkglass B7K", "confidence": 0.85})
        return recs

    def _drum_descriptor_to_dict(self, desc) -> Dict[str, Any]:
        """Convert DrumDescriptor to dictionary."""
        result = {
            "source": {
                "duration_sec": getattr(desc, "duration_sec", 0),
            },
        }
        if hasattr(desc, "kick"):
            result["kick"] = asdict(desc.kick) if hasattr(desc.kick, "__dataclass_fields__") else vars(desc.kick)
        if hasattr(desc, "snare"):
            result["snare"] = asdict(desc.snare) if hasattr(desc.snare, "__dataclass_fields__") else vars(desc.snare)
        if hasattr(desc, "machine_style"):
            result["machine_style"] = desc.machine_style
        return result

    async def _extract_midi(
        self,
        audio_data: AudioData,
        stems: Dict[str, Path],
        detection: DetectionResult,
        config: PipelineConfig,
    ) -> Dict[str, Dict[str, Any]]:
        """Extract MIDI from audio or stems."""
        midi_stems = {}

        # Determine what to extract from
        sources = stems if stems else {detection.detected_type: audio_data.path}

        for stem_type, audio_path in sources.items():
            midi_data = None
            try:
                if config.use_ensemble:
                    midi_data = await self._extract_midi_ensemble(
                        audio_path, stem_type, detection.genre, config
                    )
            except Exception as e:
                logger.warning(f"Ensemble MIDI extraction failed for {stem_type}: {e}, falling back to basic")

            # Fall back to basic extraction if ensemble failed or returned no notes
            if not midi_data or midi_data.get("note_count", 0) == 0:
                try:
                    midi_data = await self._extract_midi_basic(
                        audio_path, stem_type, detection.genre
                    )
                except Exception as e:
                    logger.warning(f"Basic MIDI extraction also failed for {stem_type}: {e}")

            if midi_data and midi_data.get("note_count", 0) > 0:
                midi_stems[stem_type] = midi_data

        return midi_stems

    async def _extract_midi_ensemble(
        self,
        audio_path: Path,
        stem_type: str,
        genre: Optional[str],
        config: PipelineConfig,
    ) -> Dict[str, Any]:
        """Extract MIDI using ensemble detector (multiple detectors)."""
        # Import the patch first to enable GPU
        from tone_forge.midi import basic_pitch_patch  # noqa: F401
        from tone_forge.midi.ensemble_extractor import PitchEnsembleExtractor


        def extract():
            y, sr = librosa.load(str(audio_path), sr=22050, mono=True)

            extractor = PitchEnsembleExtractor()
            result = extractor.extract(
                y, sr,
                stem_type=stem_type,
                genre=genre,
            )

            # Convert to MIDI file using pretty_midi
            import pretty_midi
            import tempfile
            import io

            pm = pretty_midi.PrettyMIDI(initial_tempo=result.tempo or 120)
            instrument = pretty_midi.Instrument(program=0)

            for note_obj in result.notes:
                # EnsembleNote is a dataclass with pitch, start, end, velocity attributes
                pitch = int(note_obj.pitch)
                start = float(note_obj.start)
                end = float(note_obj.end)
                velocity = int(note_obj.velocity)
                note = pretty_midi.Note(velocity=velocity, pitch=pitch, start=start, end=end)
                instrument.notes.append(note)

            pm.instruments.append(instrument)

            # Write to bytes and encode
            midi_buffer = io.BytesIO()
            pm.write(midi_buffer)
            midi_content = base64.b64encode(midi_buffer.getvalue()).decode()

            # Convert notes to JSON-serializable format for arrangement view
            notes_list = [
                {"pitch": int(n.pitch), "start": float(n.start), "end": float(n.end), "velocity": int(n.velocity)}
                for n in result.notes
            ]

            return {
                "content": midi_content,
                "filename": f"{stem_type}.mid",
                "note_count": len(result.notes),
                "notes": notes_list,  # Include notes array for arrangement view
                "confidence": result.overall_confidence,
                "tempo": result.tempo,
                "detector_stats": result.detector_stats,
                "provenance": self._build_midi_provenance(result) if config.include_provenance else None,
            }

        return await run_in_thread(extract)

    async def _extract_midi_basic(
        self,
        audio_path: Path,
        stem_type: str,
        genre: Optional[str],
    ) -> Dict[str, Any]:
        """Extract MIDI using basic-pitch only (faster)."""
        from tone_forge import midi_extractor
        import pretty_midi
        import io


        def extract():
            result = midi_extractor.extract_midi(
                str(audio_path),
                stem_type=stem_type,
                genre=genre,
            )

            if result is None:
                return None

            # Parse notes from MIDI content for arrangement view
            notes_list = []
            try:
                midi_bytes = base64.b64decode(result.content)
                pm = pretty_midi.PrettyMIDI(io.BytesIO(midi_bytes))
                for instrument in pm.instruments:
                    for note in instrument.notes:
                        notes_list.append({
                            "pitch": note.pitch,
                            "start": note.start,
                            "end": note.end,
                            "velocity": note.velocity
                        })
            except Exception as e:
                logger.warning(f"Failed to parse MIDI notes: {e}")

            return {
                "content": result.content,
                "filename": result.filename,
                "note_count": result.note_count,
                "notes": notes_list,  # Include notes for arrangement view
                "tempo": getattr(result, "tempo_bpm", 120),
                "confidence": getattr(result, "confidence", 0.8),
                "provenance": result.provenance if result.provenance else None,
            }

        return await run_in_thread(extract)

    def _build_midi_provenance(self, ensemble_result) -> Dict[str, Any]:
        """Build provenance data from ensemble extraction result."""
        return {
            "domain": "midi_extraction",
            "method": "ensemble",
            "detectors_used": list(ensemble_result.detector_stats.keys()),
            "agreement_scores": {
                note.pitch: note.agreement_score
                for note in ensemble_result.notes[:100]  # Limit for size
            },
        }

    def _generate_waveform(self, audio_data: AudioData, num_points: int = 200) -> Dict[str, Any]:
        """Generate waveform data for visualization.

        Returns format expected by studio.html displayWaveform:
        {
            peaks_positive: [...],
            peaks_negative: [...],
            rms: [...],
            duration_sec: float,
            sample_rate: int
        }
        """
        audio = audio_data.audio

        peaks_positive = []
        peaks_negative = []
        rms_values = []

        # Downsample to target number of points
        if len(audio) > num_points:
            step = len(audio) // num_points
            for i in range(0, len(audio), step):
                chunk = audio[i:i + step]
                peaks_positive.append(float(np.max(chunk)))
                peaks_negative.append(float(np.min(chunk)))
                rms_values.append(float(np.sqrt(np.mean(chunk ** 2))))
            # Trim to exact count
            peaks_positive = peaks_positive[:num_points]
            peaks_negative = peaks_negative[:num_points]
            rms_values = rms_values[:num_points]
        else:
            peaks_positive = [float(x) if x > 0 else 0.0 for x in audio]
            peaks_negative = [float(x) if x < 0 else 0.0 for x in audio]
            rms_values = [float(abs(x)) for x in audio]

        return {
            "peaks_positive": peaks_positive,
            "peaks_negative": peaks_negative,
            "rms": rms_values,
            "duration_sec": audio_data.duration,
            "sample_rate": audio_data.sr,
        }

    async def _track_beats(
        self,
        audio_data: AudioData,
        stems: Optional[Dict[str, Path]] = None,
    ) -> Dict[str, Any]:
        """Pipeline-level beat-tracking stage (Phase 7 hoist).

        Runs ``librosa.beat.beat_track`` once on the most informative
        source available (the demucs 'other' stem when present —
        harmonic + percussive but free of vocals — else the full mix).
        Returns ``{tempo_bpm, beats_s, downbeats_s}`` for both the
        section detector and the chord-lane snap step to consume.

        Failure is silent and observable: every output is degraded
        rather than raised. ``tempo_bpm == 0.0`` signals "no tempo
        detected" without breaking the pipeline; the new pipeline-
        output invariant test (``test_pipeline_output_invariants``)
        catches the silent-zero regression for non-silent fixtures.

        ``downbeats_s`` is currently derived from ``beats_s`` at 4/4
        (every 4th beat starting at beat 0). When/if a real downbeat
        tracker lands (madmom DBN, librosa.beat.plp, or a learned
        head), it can replace the derivation without changing the
        AnalysisResult / SongUnderstanding contract.
        """
        def track() -> Dict[str, Any]:
            import librosa as _lr
            # Prefer the 'other' stem (harmonic + percussive without
            # vocals/bass smear); the chord lane uses the same source,
            # which keeps the beat grid musically aligned to the
            # chord-region edges. Fall back to the full mix on any
            # load failure.
            y, sr = audio_data.audio, audio_data.sr
            if stems is not None:
                other_path = stems.get("other")
                if other_path is not None:
                    try:
                        y, sr = _lr.load(str(other_path), sr=sr, mono=True)
                    except Exception as e:  # pragma: no cover - defensive
                        logger.warning(
                            f"Beat tracking: 'other' stem load failed "
                            f"({e}); using full mix"
                        )
                        y, sr = audio_data.audio, audio_data.sr

            tempo_bpm = 0.0
            beats_s: List[float] = []
            downbeats_s: List[float] = []
            try:
                tempo_raw, beat_frames = _lr.beat.beat_track(y=y, sr=sr)
                tempo_val = (
                    float(np.asarray(tempo_raw).item())
                    if tempo_raw is not None else 0.0
                )
                # Same 40–240 BPM sanity window the legacy in-stage
                # code used. Out-of-range outputs are almost always
                # phantom-pulse artefacts and would mislead the UI.
                if (
                    40.0 <= tempo_val <= 240.0
                    and beat_frames is not None
                    and len(beat_frames) >= 2
                ):
                    tempo_bpm = tempo_val
                    beats_s = _lr.frames_to_time(
                        beat_frames, sr=sr
                    ).tolist()
                    # Derive downbeats at 4/4 (every 4th beat starting
                    # from beat 0). The first beat in the librosa
                    # tracking output is treated as the anchor; this
                    # is an estimate, not a measured downbeat — UI
                    # honesty: render as a thinner tick than a
                    # measured one would warrant.
                    downbeats_s = beats_s[::4]
                    logger.info(
                        f"Beat tracking: tempo={tempo_bpm:.1f} BPM, "
                        f"{len(beats_s)} beats, "
                        f"{len(downbeats_s)} downbeats (derived 4/4)"
                    )
                else:
                    logger.warning(
                        f"Beat tracking: tempo {tempo_val:.1f} BPM "
                        f"outside 40–240 range or <2 beats; degrading"
                    )
            except Exception as e:  # pragma: no cover - defensive
                logger.warning(f"Beat tracking failed: {e}")

            return {
                "tempo_bpm": tempo_bpm,
                "beats_s": beats_s,
                "downbeats_s": downbeats_s,
            }

        return await run_in_thread(track)

    async def _detect_sections(
        self,
        audio_data: AudioData,
        midi_stems: Dict[str, Dict[str, Any]],
        tempo_hint: float = 0.0,
    ) -> Optional[Dict[str, Any]]:
        """Detect arrangement sections using section detector.

        ``tempo_hint`` is the pipeline-level tempo from ``_track_beats``.
        When > 0 it short-circuits the section detector's internal
        beat-track call, avoiding the duplicate computation that
        previously fed two stages independently and produced
        inconsistent tempo values between them.
        """
        def detect():
            from tone_forge.analysis.sections import SectionDetector

            # Prefer the hoisted pipeline tempo; fall back to MIDI
            # tempo when the hoisted beat tracker degraded.
            tempo = tempo_hint if tempo_hint and tempo_hint > 0 else None
            if tempo is None:
                for stem_type, midi_data in midi_stems.items():
                    if midi_data.get("tempo"):
                        tempo = midi_data["tempo"]
                        break

            detector = SectionDetector(sr=audio_data.sr)
            result = detector.detect_sections(
                audio_data.audio,
                sr=audio_data.sr,
                tempo=tempo,
            )

            return {
                "sections": [s.to_dict() for s in result.sections],
                "energy_curve": result.energy_curve.tolist() if len(result.energy_curve) > 0 else [],
                "tempo_bpm": result.tempo_bpm,
            }

        return await run_in_thread(detect)

    async def _detect_chord_lane(
        self,
        audio_data: AudioData,
        stems: Optional[Dict[str, Path]] = None,
        beats_s: Optional[List[float]] = None,
    ) -> List[Dict[str, Any]]:
        """Detect the chord lane via the analysis subsystem (P4a).

        Delegates to ``tone_forge.analysis.detect_chords`` (the public
        contracts-shaped entry point) and serializes each ``Chord`` to
        the dict shape persisted in history results and consumed by
        ``session.bundle._iter_chords``.

        Chroma source: prefer the demucs "other" stem (harmonic content
        — guitar + keys, no drums, no bass, no vocals) over the full
        mix. The full mix is dominated by bass-string fundamentals;
        CQT chroma reads the bass root and the cosine matcher locks
        onto the bass note's relative-minor template (e.g. Pub Feed:
        bass on F# → intro labelled F#m even though guitar plays E).
        Falls back to the full mix audio when the stem is missing.

        Returns an empty list on failure rather than raising — the
        caller logs and skips the field. The chord lane is not on the
        critical path for analysis; absence is a soft degradation.
        """
        def detect() -> List[Dict[str, Any]]:
            import librosa as _lr
            from tone_forge.analysis import detect_chords

            y, sr = audio_data.audio, audio_data.sr
            other_path = stems.get("other") if stems else None
            if other_path is not None:
                try:
                    y, sr = _lr.load(str(other_path), sr=22050, mono=True)
                    logger.info(
                        f"Chord detection: using 'other' stem "
                        f"({len(y)/sr:.1f}s) instead of full mix"
                    )
                except Exception as e:
                    logger.warning(
                        f"Chord detection: 'other' stem load failed ({e}); "
                        f"falling back to full mix"
                    )
                    y, sr = audio_data.audio, audio_data.sr

            # Phase 5: bass-routed disambiguation. Load the bass stem
            # at the same sample rate as the 'other' stem so the pyin
            # bass-root frames inside detect_chords align with chroma
            # frames. The bias resolves the relative-major/minor
            # ambiguity (A vs F#m, C vs Am, ...) that chroma alone
            # cannot separate. Missing bass stem degrades to no-bias.
            y_bass = None
            bass_path = stems.get("bass") if stems else None
            if bass_path is not None:
                try:
                    y_bass, _ = _lr.load(str(bass_path), sr=sr, mono=True)
                    logger.info(
                        f"Chord detection: routing 'bass' stem "
                        f"({len(y_bass)/sr:.1f}s) for root bias"
                    )
                except Exception as e:
                    logger.warning(
                        f"Chord detection: 'bass' stem load failed ({e}); "
                        f"falling back to no bass-root bias"
                    )
                    y_bass = None

            # Phase 7 hoist: beats come from the pipeline-level
            # ``_track_beats`` stage rather than being re-derived here.
            # The previous in-stage ``librosa.beat.beat_track`` call
            # was duplicative AND its result was dropped (used only
            # for the snap below, never propagated to AnalysisResult).
            # The caller passes ``beats_s=None`` when the hoisted
            # tracker degraded, which gracefully disables the snap
            # variant and matches the legacy fixed-window output.
            beats_array = (
                np.asarray(beats_s, dtype=np.float64)
                if beats_s is not None and len(beats_s) >= 2 else None
            )

            # Phase 6 (hybrid grid + UI toggle): detector runs on the
            # fixed 0.5s grid (Phase 5 WCSR floor). The beat-snapped
            # variant is produced separately via the cheap post-process
            # so both views ship to the client.
            #
            # Phase-7+ key hoist: use detect_chords_with_key so the
            # post-tie-break key decision surfaces alongside the chord
            # records, instead of being computed and dropped on the
            # floor inside chord_detector. Backward-compatible: empty
            # dict on degenerate input.
            from tone_forge.analysis.chords import (
                detect_chords_with_key,
                snap_chord_boundaries_to_beats,
            )
            chord_records, key_dict = detect_chords_with_key(
                y, sr, bass_audio=y_bass, beats_s=None,
            )
            fixed = [
                {
                    "start_s": c.start_s,
                    "end_s": c.end_s,
                    "symbol": c.symbol,
                    "confidence": c.confidence,
                }
                for c in chord_records
            ]
            snapped = None
            if beats_array is not None and len(chord_records) >= 2:
                song_dur_s = float(len(y) / sr) if sr else 0.0
                snapped_records = snap_chord_boundaries_to_beats(
                    chord_records, beats_array, song_dur_s,
                )
                snapped = [
                    {
                        "start_s": c.start_s,
                        "end_s": c.end_s,
                        "symbol": c.symbol,
                        "confidence": c.confidence,
                    }
                    for c in snapped_records
                ]
            return {"fixed": fixed, "snapped": snapped, "key": key_dict}

        return await run_in_thread(detect)

    def _build_result(
        self,
        audio_data: AudioData,
        detection: DetectionResult,
        stems: Dict[str, Path],
        stem_results: Dict[str, StemResult],
        instrument_results: Dict[str, Dict[str, Any]],
        midi_stems: Dict[str, Dict[str, Any]],
        waveform: Optional[Dict[str, Any]],
        sections: Optional[List[Dict[str, Any]]],
        energy_curve: Optional[List[float]],
        chords: Optional[List[Dict[str, Any]]],
        chords_beat_snapped: Optional[List[Dict[str, Any]]],
        stage_timings: Optional[Dict],
        config: PipelineConfig,
        tempo_bpm: float = 0.0,
        beats_s: Optional[List[float]] = None,
        downbeats_s: Optional[List[float]] = None,
        detected_key: Optional[str] = None,
        detected_key_root: Optional[int] = None,
        detected_key_strength: float = 0.0,
    ) -> AnalysisResult:
        """Build the final analysis result."""
        # Build stems paths dict - optionally as URLs for web playback
        stems_paths = None
        if stems:
            if config.stem_serve_url_base:
                # Generate URLs for web playback
                from urllib.parse import quote
                stems_paths = {
                    name: f"{config.stem_serve_url_base}?path={quote(str(path))}"
                    for name, path in stems.items()
                }
            else:
                # Use raw file paths
                stems_paths = {name: str(path) for name, path in stems.items()}

        # Aggregate provenance
        provenance = self._aggregate_provenance(stem_results, midi_stems) if config.include_provenance else None

        # Per-stem V2 preset retrieval (for reconstruction export)
        preset_matches = self._match_presets_per_stem(stems) if stems else None

        # Get primary MIDI - prefer melodic instruments, then drums, then any
        primary_midi = None
        for stem_key in ["guitar", "piano", "other", "bass", "vocals", "drums", "synth"]:
            if stem_key in midi_stems:
                primary_midi = midi_stems[stem_key]
                break
        # Fallback: use any available MIDI data
        if primary_midi is None and midi_stems:
            primary_midi = next(iter(midi_stems.values()))

        # Get quality data from stem results
        quality = None
        for stem_type, stem_result in stem_results.items():
            if stem_result.quality:
                quality = stem_result.quality
                break

        # Determine primary descriptor/chain for backward compatibility
        primary_descriptor = None
        primary_chain = None
        primary_tweak_hints = []

        detected_type = detection.detected_type
        if detected_type == "drums" and "drums" in instrument_results:
            primary_descriptor = instrument_results["drums"].get("descriptor")
            primary_chain = instrument_results["drums"].get("machine_match", {})
            primary_tweak_hints = instrument_results["drums"].get("tweak_hints", [])
        elif detected_type == "bass" and "bass" in instrument_results:
            primary_descriptor = instrument_results["bass"].get("descriptor")
            primary_chain = instrument_results["bass"].get("recommendations", [])
            primary_tweak_hints = instrument_results["bass"].get("tweak_hints", [])
        elif detected_type == "synth" and "synth" in instrument_results:
            primary_descriptor = instrument_results["synth"].get("descriptor")
            primary_chain = []
            primary_tweak_hints = instrument_results["synth"].get("tweak_hints", [])
        elif "guitar" in instrument_results:
            primary_descriptor = instrument_results["guitar"].get("descriptor")
            primary_chain = instrument_results["guitar"].get("platforms", {}).get("helix", [])
            primary_tweak_hints = instrument_results["guitar"].get("tweak_hints", [])

        result = AnalysisResult(
            source_name=audio_data.source_name,
            source_url=audio_data.source_url,
            duration_sec=audio_data.duration,
            sample_rate=audio_data.sr,
            detection=detection,
            detected_type=detection.detected_type,
            # Instrument results
            guitar=instrument_results.get("guitar"),
            bass=instrument_results.get("bass"),
            drums=instrument_results.get("drums"),
            synth=instrument_results.get("synth"),
            # MIDI
            midi=primary_midi,
            midi_stems=midi_stems if midi_stems else None,
            # Stems
            stems=stems_paths,
            stems_paths=stems_paths,  # Alias
            # V2 preset matches per stem
            preset_matches=preset_matches,
            # Quality
            quality=quality,
            # Visualization
            waveform=waveform,
            # Profiling
            profiling={"stages": stage_timings, **stage_timings} if stage_timings else None,
            # Arrangement sections
            sections=sections,
            energy_curve=energy_curve,
            # Beat grid (Phase 7 hoist)
            tempo_bpm=tempo_bpm or 0.0,
            beats_s=beats_s if beats_s else None,
            downbeats_s=downbeats_s if downbeats_s else None,
            # Detected key (Phase-7+ hoist)
            detected_key=detected_key,
            detected_key_root=detected_key_root,
            detected_key_strength=detected_key_strength or 0.0,
            # Chord lane (P4a)
            chords=chords,
            chords_beat_snapped=chords_beat_snapped,
            # Provenance
            provenance=provenance,
            # Backward compatibility
            type=detection.detected_type,
            descriptor=primary_descriptor,
            chain=primary_chain,
            tweak_hints=primary_tweak_hints,
        )

        return result

    def _aggregate_provenance(
        self,
        stem_results: Dict[str, StemResult],
        midi_stems: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Aggregate provenance from all analysis stages."""
        decisions = []

        # Collect from stem analysis
        for stem_type, result in stem_results.items():
            if result.provenance:
                decisions.extend(result.provenance.get("decisions", []))

        # Collect from MIDI extraction
        for stem_type, midi_data in midi_stems.items():
            if midi_data.get("provenance"):
                prov = midi_data["provenance"]
                decisions.append({
                    "domain": "midi_extraction",
                    "stem": stem_type,
                    "method": prov.get("method", "unknown"),
                    "note_count": midi_data.get("note_count", 0),
                })

        return {
            "total_decisions": len(decisions),
            "domains": list(set(d.get("domain", "unknown") for d in decisions)),
            "decisions": decisions[:100],  # Limit for response size
        }

    # Stem → V2 catalog sound_type filter for retrieval.
    # None means "no filter — pick best Analog match".
    _STEM_SOUND_TYPE_FILTER: Dict[str, Optional[str]] = {
        "bass": "bass",
        "vocals": "lead",
        "guitar": None,
        "other": None,
        "piano": "keys",
    }

    def _match_presets_per_stem(
        self,
        stems: Dict[str, Path],
    ) -> Optional[Dict[str, Dict[str, Any]]]:
        """Run V2 preset retrieval per melodic stem (used by reconstruction export).

        Returns a dict keyed by stem name, each entry containing the top-1
        match metadata. Drums are skipped (no drum kits in the V2 catalog).
        Failures on a single stem are logged and that stem omitted — the
        export path falls back to the Phase 1 default for missing stems.
        """
        from .preset_catalog import preset_retrieval

        matches: Dict[str, Dict[str, Any]] = {}
        for stem_name, stem_path in stems.items():
            if stem_name == "drums":
                continue
            sound_type = self._STEM_SOUND_TYPE_FILTER.get(stem_name)
            t0 = time.perf_counter()
            try:
                results = preset_retrieval.match_audio_file(
                    Path(stem_path),
                    k=1,
                    instrument="Analog",
                    sound_type_filter=sound_type,
                )
            except Exception as e:
                logger.warning(
                    "[preset_match] %s failed: %s", stem_name, e
                )
                continue
            elapsed_ms = (time.perf_counter() - t0) * 1000
            if not results:
                logger.info(
                    "[preset_match] %s: no match (%.0f ms)", stem_name, elapsed_ms
                )
                continue
            top = results[0]
            matches[stem_name] = {
                "preset_id": top["preset_id"],
                "preset_name": top["preset_name"],
                "preset_path": top["preset_path"],
                "instrument": top["instrument"],
                "category": top["category"],
                "sound_type": top["sound_type"],
                "distance": top["distance"],
            }
            logger.info(
                "[preset_match] %s -> %s (distance=%.3f, %.0f ms)",
                stem_name,
                top["preset_name"],
                top["distance"],
                elapsed_ms,
            )
        return matches or None


# =============================================================================
# Module-level singleton
# =============================================================================

_pipeline_instance: Optional[UnifiedPipeline] = None


def get_pipeline() -> UnifiedPipeline:
    """Get the singleton pipeline instance."""
    global _pipeline_instance
    if _pipeline_instance is None:
        _pipeline_instance = UnifiedPipeline()
    return _pipeline_instance
