"""Base class for stem-specific MIDI extraction pipelines."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Type

import numpy as np

from tone_forge.midi.passes.base import ExtractedNote
from tone_forge.midi.filters.base import PrecisionFilter, FilterContext, FilterResult
from tone_forge.midi.polyphony_estimator import (
    PolyphonyEstimator,
    PolyphonyEstimate,
    PolyphonyClass,
)


@dataclass
class PipelineConfig:
    """Configuration for a stem pipeline."""
    # Extraction thresholds
    onset_threshold: float = 0.5
    frame_threshold: float = 0.4
    min_note_ms: float = 50.0
    min_velocity: int = 20

    # Note processing
    quantize_strength: float = 0.5
    quantize_grid: int = 16  # 16th notes
    merge_max_gap_ms: float = 30.0
    merge_enabled: bool = True

    # Filtering
    key_filter_strictness: float = 0.5
    isolated_filter_enabled: bool = True
    isolated_min_neighbors: int = 1
    isolated_time_window: float = 2.0

    # Harmonic handling
    harmonic_suppression_enabled: bool = True
    octave_correction_enabled: bool = False
    subharmonic_cleanup_enabled: bool = False

    # Post-processing
    velocity_normalize: bool = True
    velocity_min: int = 60
    velocity_max: int = 110

    # Precision recovery
    precision_filters: List[str] = field(default_factory=list)


@dataclass
class PipelineResult:
    """Result from a stem pipeline."""
    notes: List[ExtractedNote]
    pipeline_name: str
    config: PipelineConfig
    stats: Dict[str, Any] = field(default_factory=dict)
    filter_results: List[FilterResult] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    polyphony_estimate: Optional[PolyphonyEstimate] = None

    @property
    def note_count(self) -> int:
        return len(self.notes)

    @property
    def total_suppressed(self) -> int:
        return sum(len(fr.suppressed_notes) for fr in self.filter_results)

    @property
    def polyphony_class_name(self) -> str:
        if self.polyphony_estimate:
            return self.polyphony_estimate.polyphony_class.value
        return "unknown"


class StemPipeline(ABC):
    """Base class for stem-specific extraction pipelines.

    Each stem type has fundamentally different extraction needs:
    - Lead: Fast transients, staccato preservation, delay handling
    - Bass: Monophonic focus, octave accuracy, subharmonic cleanup
    - Pad: Long notes, chord detection, harmonic suppression
    - Guitar: Strumming patterns, sustain handling, polyphony
    - Arp: Fast sequences, pattern preservation, quantization

    Global heuristics do not work. Each pipeline must own its behavior.
    """

    def __init__(self, config: Optional[PipelineConfig] = None):
        """Initialize pipeline with configuration."""
        self.config = config or self._default_config()
        self._precision_filters: List[PrecisionFilter] = []
        self._polyphony_estimator = PolyphonyEstimator()
        self._setup_filters()

    @property
    @abstractmethod
    def name(self) -> str:
        """Pipeline name for logging/tracking."""
        pass

    @abstractmethod
    def _default_config(self) -> PipelineConfig:
        """Return default configuration for this stem type."""
        pass

    @abstractmethod
    def _setup_filters(self) -> None:
        """Set up precision recovery filters for this pipeline."""
        pass

    @abstractmethod
    def _preprocess_audio(
        self,
        audio: np.ndarray,
        sr: int,
    ) -> np.ndarray:
        """Apply stem-specific audio preprocessing.

        Examples:
        - Bass: Low-pass filter to focus on fundamentals
        - Lead: High-pass to remove rumble
        - Pad: Smoothing to reduce transient artifacts
        """
        pass

    @abstractmethod
    def _postprocess_notes(
        self,
        notes: List[ExtractedNote],
        audio: np.ndarray,
        sr: int,
        tempo: Optional[float],
        key: Optional[Tuple[int, str]],
    ) -> List[ExtractedNote]:
        """Apply stem-specific note post-processing.

        Examples:
        - Bass: Monophonic enforcement
        - Lead: Merge legato notes
        - Arp: Strict quantization
        """
        pass

    def extract(
        self,
        audio: np.ndarray,
        sr: int,
        tempo: Optional[float] = None,
        key: Optional[Tuple[int, str]] = None,
        time_signature: Tuple[int, int] = (4, 4),
    ) -> PipelineResult:
        """Run full extraction pipeline.

        Args:
            audio: Audio signal (mono or stereo)
            sr: Sample rate
            tempo: Tempo in BPM (estimated if not provided)
            key: Key as (root, mode) tuple
            time_signature: Time signature

        Returns:
            PipelineResult with extracted notes
        """
        warnings = []

        # Ensure mono
        if audio.ndim > 1:
            audio = np.mean(audio, axis=0)

        # Estimate polyphony first
        polyphony_estimate = self._polyphony_estimator.estimate(
            audio, sr, stem_type=self.name
        )

        # Adapt configuration based on polyphony
        self._adapt_to_polyphony(polyphony_estimate)

        # Preprocess audio
        processed_audio = self._preprocess_audio(audio, sr)

        # Extract notes using basic-pitch or librosa
        raw_notes = self._extract_raw_notes(processed_audio, sr)
        initial_count = len(raw_notes)

        # Apply precision recovery filters
        filter_context = FilterContext(
            audio=processed_audio,
            sr=sr,
            tempo=tempo,
            key=key,
            time_signature=time_signature,
            stem_type=self.name,
        )

        filter_results = []
        current_notes = raw_notes

        for precision_filter in self._precision_filters:
            result = precision_filter.filter(current_notes, filter_context)
            filter_results.append(result)
            current_notes = result.kept_notes

            if result.suppression_rate > 0.3:
                warnings.append(
                    f"{precision_filter.name} suppressed {result.suppression_rate:.1%} of notes"
                )

        # Apply stem-specific post-processing
        final_notes = self._postprocess_notes(
            current_notes, processed_audio, sr, tempo, key
        )

        # Quantize if enabled
        if self.config.quantize_strength > 0 and tempo:
            final_notes = self._quantize_notes(final_notes, tempo)

        # Normalize velocities if enabled
        if self.config.velocity_normalize:
            final_notes = self._normalize_velocities(final_notes)

        # Apply monophonic enforcement if recommended
        if polyphony_estimate.monophonic_enforcement:
            final_notes = self._enforce_monophonic(final_notes)

        return PipelineResult(
            notes=final_notes,
            pipeline_name=self.name,
            config=self.config,
            stats={
                "initial_notes": initial_count,
                "after_filters": len(current_notes),
                "final_notes": len(final_notes),
                "suppression_rate": 1 - len(final_notes) / initial_count if initial_count > 0 else 0,
                "polyphony_class": polyphony_estimate.polyphony_class.value,
                "avg_voices": polyphony_estimate.avg_voices,
            },
            filter_results=filter_results,
            warnings=warnings,
            polyphony_estimate=polyphony_estimate,
        )

    def _adapt_to_polyphony(self, estimate: PolyphonyEstimate) -> None:
        """Adapt pipeline configuration based on polyphony estimate.

        Subclasses can override for custom adaptation.
        """
        # Adapt merge gap
        if estimate.polyphony_class == PolyphonyClass.MONOPHONIC:
            self.config.merge_max_gap_ms = estimate.recommended_merge_gap * 1000
        elif estimate.polyphony_class == PolyphonyClass.DENSE_POLY:
            # Reduce merging for dense polyphony to preserve chord voicing
            self.config.merge_max_gap_ms = min(
                self.config.merge_max_gap_ms,
                estimate.recommended_merge_gap * 1000
            )

        # Adapt harmonic cleanup based on recommendation
        cleanup_level = estimate.recommended_cleanup_level
        if cleanup_level == "aggressive":
            self.config.harmonic_suppression_enabled = True
            self.config.subharmonic_cleanup_enabled = True
        elif cleanup_level == "conservative":
            self.config.harmonic_suppression_enabled = False
            self.config.subharmonic_cleanup_enabled = False

    def _enforce_monophonic(
        self,
        notes: List[ExtractedNote],
    ) -> List[ExtractedNote]:
        """Enforce single-voice output by keeping highest-confidence overlapping notes."""
        if not notes:
            return notes

        # Sort by start time, then confidence (descending)
        sorted_notes = sorted(notes, key=lambda n: (n.start, -n.confidence))

        result = []
        last_end = 0.0

        for note in sorted_notes:
            if note.start >= last_end:
                # No overlap, keep this note
                result.append(note)
                last_end = note.end
            else:
                # Overlapping - only keep if we have none, or truncate previous
                if not result:
                    result.append(note)
                    last_end = note.end
                else:
                    # Truncate previous note to avoid overlap
                    prev = result[-1]
                    if prev.confidence < note.confidence:
                        # Replace with higher confidence note
                        result[-1] = ExtractedNote(
                            pitch=prev.pitch,
                            start=prev.start,
                            end=note.start,  # Truncate
                            velocity=prev.velocity,
                            confidence=prev.confidence,
                            source_pass=prev.source_pass,
                            flags=prev.flags,
                            provenance=prev.provenance,
                        )
                        result.append(note)
                        last_end = note.end
                    # Otherwise, skip this note (keep previous)

        return result

    def _extract_raw_notes(
        self,
        audio: np.ndarray,
        sr: int,
    ) -> List[ExtractedNote]:
        """Extract raw notes using basic-pitch."""
        try:
            import tempfile
            import os
            import soundfile as sf
            from basic_pitch.inference import predict

            # Write to temp file
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
                tmp_path = tmp.name
                sf.write(tmp_path, audio.astype(np.float32), sr)

            try:
                model_output, midi_data, note_events = predict(
                    tmp_path,
                    onset_threshold=self.config.onset_threshold,
                    frame_threshold=self.config.frame_threshold,
                    minimum_note_length=self.config.min_note_ms,
                )
            finally:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)

            # Convert to ExtractedNote
            notes = []
            for note in note_events:
                start, end, pitch, amplitude, pitch_bends = note

                if (end - start) * 1000 < self.config.min_note_ms:
                    continue

                notes.append(ExtractedNote(
                    pitch=int(pitch),
                    start=float(start),
                    end=float(end),
                    velocity=int(amplitude * 127) if amplitude <= 1 else int(amplitude),
                    confidence=float(amplitude),
                    source_pass=1,
                ))

            return notes

        except Exception as e:
            # Fallback to librosa if basic-pitch fails
            return self._extract_with_librosa(audio, sr)

    def _extract_with_librosa(
        self,
        audio: np.ndarray,
        sr: int,
    ) -> List[ExtractedNote]:
        """Fallback extraction using librosa."""
        import librosa

        # Detect onsets
        onset_frames = librosa.onset.onset_detect(
            y=audio, sr=sr,
            backtrack=True,
            units='frames',
        )
        onset_times = librosa.frames_to_time(onset_frames, sr=sr)

        # Estimate pitches
        f0, voiced_flag, voiced_probs = librosa.pyin(
            audio, fmin=librosa.note_to_hz('C2'),
            fmax=librosa.note_to_hz('C7'),
            sr=sr,
        )
        times = librosa.times_like(f0, sr=sr)

        notes = []
        for i, onset_time in enumerate(onset_times):
            # Find pitch at onset
            onset_idx = np.argmin(np.abs(times - onset_time))

            if voiced_flag[onset_idx]:
                pitch = int(round(librosa.hz_to_midi(f0[onset_idx])))

                # Find note end (next onset or end of voiced region)
                if i + 1 < len(onset_times):
                    end_time = onset_times[i + 1]
                else:
                    # Find where voicing ends
                    for j in range(onset_idx + 1, len(voiced_flag)):
                        if not voiced_flag[j]:
                            end_time = times[j]
                            break
                    else:
                        end_time = times[-1]

                notes.append(ExtractedNote(
                    pitch=pitch,
                    start=float(onset_time),
                    end=float(end_time),
                    velocity=80,
                    confidence=float(voiced_probs[onset_idx]) if onset_idx < len(voiced_probs) else 0.5,
                    source_pass=1,
                ))

        return notes

    def _quantize_notes(
        self,
        notes: List[ExtractedNote],
        tempo: float,
    ) -> List[ExtractedNote]:
        """Quantize notes to grid."""
        if not tempo or tempo <= 0:
            return notes

        beat_duration = 60.0 / tempo
        grid_duration = beat_duration * 4 / self.config.quantize_grid
        strength = self.config.quantize_strength

        quantized = []
        for note in notes:
            # Quantize start time
            grid_pos = round(note.start / grid_duration)
            quantized_start = grid_pos * grid_duration
            offset = quantized_start - note.start
            new_start = note.start + offset * strength

            # Preserve duration
            new_end = new_start + note.duration

            quantized.append(ExtractedNote(
                pitch=note.pitch,
                start=new_start,
                end=new_end,
                velocity=note.velocity,
                confidence=note.confidence,
                source_pass=note.source_pass,
                flags=note.flags,
                provenance=note.provenance,
            ))

        return quantized

    def _normalize_velocities(
        self,
        notes: List[ExtractedNote],
    ) -> List[ExtractedNote]:
        """Normalize velocities to configured range."""
        if not notes:
            return notes

        velocities = [n.velocity for n in notes]
        min_vel = min(velocities)
        max_vel = max(velocities)

        if max_vel == min_vel:
            # All same velocity, use middle of range
            target_vel = (self.config.velocity_min + self.config.velocity_max) // 2
            return [
                ExtractedNote(
                    pitch=n.pitch,
                    start=n.start,
                    end=n.end,
                    velocity=target_vel,
                    confidence=n.confidence,
                    source_pass=n.source_pass,
                    flags=n.flags,
                    provenance=n.provenance,
                )
                for n in notes
            ]

        # Scale to target range
        normalized = []
        for note in notes:
            scaled = (note.velocity - min_vel) / (max_vel - min_vel)
            new_vel = int(
                self.config.velocity_min +
                scaled * (self.config.velocity_max - self.config.velocity_min)
            )
            normalized.append(ExtractedNote(
                pitch=note.pitch,
                start=note.start,
                end=note.end,
                velocity=new_vel,
                confidence=note.confidence,
                source_pass=note.source_pass,
                flags=note.flags,
                provenance=note.provenance,
            ))

        return normalized
