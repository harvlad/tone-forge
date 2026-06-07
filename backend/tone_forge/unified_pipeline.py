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
            include_waveform=False,
            include_profiling=True,  # Enable for Technical tab
        )

    @classmethod
    def deep(cls) -> "PipelineConfig":
        """Deep analysis - full features with stem separation."""
        return cls(
            mode=AnalysisMode.DEEP,
            separate_stems=True,
            extract_midi=True,
            use_ensemble=True,
            analyze_quality=True,
            include_provenance=True,
            detect_synth_behavior=True,
            include_waveform=True,
            include_profiling=True,
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

    # Quality
    quality: Optional[Dict[str, Any]] = None

    # Aggregated provenance
    provenance: Optional[Dict[str, Any]] = None

    # Visualization
    waveform: Optional[List[float]] = None

    # Profiling
    profiling: Optional[Dict[str, Any]] = None

    # Arrangement / sections
    sections: Optional[List[Dict[str, Any]]] = None
    energy_curve: Optional[List[float]] = None

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
                          "midi_stems", "stems", "stems_paths", "quality",
                          "provenance", "waveform", "profiling",
                          "sections", "energy_curve",
                          "type", "descriptor", "chain", "tweak_hints"]:
            value = getattr(self, field_name)
            if value is not None:
                result[field_name] = value

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
            if config.separate_stems and detection.is_full_mix:
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

            # 7. Detect arrangement sections
            sections = None
            energy_curve = None
            yield ProgressEvent("sections", "Analyzing arrangement...", 87)
            stage_start = time.time()
            try:
                section_result = await self._detect_sections(audio_data, midi_stems)
                if section_result:
                    sections = section_result.get("sections")
                    energy_curve = section_result.get("energy_curve")
            except Exception as e:
                logger.warning(f"Section detection failed: {e}")
            if stage_timings is not None:
                stage_timings["section_detection"] = {
                    "duration_ms": (time.time() - stage_start) * 1000,
                    "sections_found": len(sections) if sections else 0,
                }

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
                midi_stems, waveform, sections, energy_curve, stage_timings, config
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

    def _generate_waveform(self, audio_data: AudioData, num_points: int = 200) -> List[float]:
        """Generate waveform data for visualization."""
        audio = audio_data.audio

        # Downsample to target number of points
        if len(audio) > num_points:
            step = len(audio) // num_points
            peaks = []
            for i in range(0, len(audio), step):
                chunk = audio[i:i + step]
                peaks.append(float(np.max(np.abs(chunk))))
            return peaks[:num_points]
        else:
            return [float(abs(x)) for x in audio]

    async def _detect_sections(
        self,
        audio_data: AudioData,
        midi_stems: Dict[str, Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """Detect arrangement sections using section detector."""
        def detect():
            from tone_forge.reconstruction.section_detector import SectionDetector

            # Get tempo from MIDI if available
            tempo = None
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

    def _build_result(
        self,
        audio_data: AudioData,
        detection: DetectionResult,
        stems: Dict[str, Path],
        stem_results: Dict[str, StemResult],
        instrument_results: Dict[str, Dict[str, Any]],
        midi_stems: Dict[str, Dict[str, Any]],
        waveform: Optional[List[float]],
        sections: Optional[List[Dict[str, Any]]],
        energy_curve: Optional[List[float]],
        stage_timings: Optional[Dict],
        config: PipelineConfig,
    ) -> AnalysisResult:
        """Build the final analysis result."""
        # Build stems paths dict
        stems_paths = {name: str(path) for name, path in stems.items()} if stems else None

        # Aggregate provenance
        provenance = self._aggregate_provenance(stem_results, midi_stems) if config.include_provenance else None

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
            # Quality
            quality=quality,
            # Visualization
            waveform=waveform,
            # Profiling
            profiling={"stages": stage_timings, **stage_timings} if stage_timings else None,
            # Arrangement sections
            sections=sections,
            energy_curve=energy_curve,
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
