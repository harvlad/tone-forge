"""Pass 1: High-confidence note extraction.

Conservative initial detection that only keeps notes with strong evidence.
This forms the foundation for subsequent passes to build upon.
"""
from __future__ import annotations

import logging
import time
from dataclasses import replace
from typing import List, Optional, Tuple

import librosa
import numpy as np

from .base import (
    ExtractionContext,
    ExtractionPass,
    ExtractedNote,
    NoteFlag,
    PassResult,
)

logger = logging.getLogger(__name__)


class HighConfidencePass(ExtractionPass):
    """Extract only high-confidence notes from audio.

    This pass performs initial note detection with conservative thresholds,
    ensuring high precision at the potential cost of recall. Subsequent
    passes can recover missed notes using harmonic context.
    """

    def __init__(
        self,
        pass_number: int = 1,
        min_confidence: float = 0.6,
        onset_threshold: float = 0.5,
        frame_threshold: float = 0.4,
        min_note_ms: float = 50.0,
        use_basic_pitch: bool = True,
    ):
        """Initialize high-confidence pass.

        Args:
            pass_number: Pass number in pipeline
            min_confidence: Minimum confidence to keep a note
            onset_threshold: Threshold for onset detection
            frame_threshold: Threshold for frame activation
            min_note_ms: Minimum note duration in milliseconds
            use_basic_pitch: Whether to use basic_pitch for extraction
        """
        super().__init__(pass_number)
        self.min_confidence = min_confidence
        self.onset_threshold = onset_threshold
        self.frame_threshold = frame_threshold
        self.min_note_ms = min_note_ms
        self.use_basic_pitch = use_basic_pitch

    @property
    def name(self) -> str:
        return "high_confidence"

    def process(
        self,
        notes: List[ExtractedNote],
        context: ExtractionContext,
    ) -> PassResult:
        """Extract high-confidence notes from audio.

        For the first pass, input notes are typically empty. This pass
        performs the initial extraction from audio.

        Args:
            notes: Input notes (usually empty for first pass)
            context: Extraction context with audio and parameters

        Returns:
            PassResult with high-confidence notes
        """
        start_time = time.time()
        input_count = len(notes)

        # Adapt thresholds based on context
        onset_thresh = self._adapt_onset_threshold(context)
        frame_thresh = self._adapt_frame_threshold(context)

        # Extract notes from audio
        if self.use_basic_pitch:
            raw_notes = self._extract_with_basic_pitch(
                context.audio,
                context.sr,
                onset_thresh,
                frame_thresh,
            )
        else:
            raw_notes = self._extract_with_librosa(
                context.audio,
                context.sr,
                onset_thresh,
            )

        # Filter to high-confidence notes
        high_conf_notes = self._filter_high_confidence(raw_notes, context)

        # Add original flag
        for note in high_conf_notes:
            note.flags.add(NoteFlag.ORIGINAL)

        execution_time = (time.time() - start_time) * 1000

        stats = self._create_statistics(
            notes,
            high_conf_notes,
            execution_time,
            onset_threshold=onset_thresh,
            frame_threshold=frame_thresh,
            raw_notes_count=len(raw_notes),
            filtered_count=len(raw_notes) - len(high_conf_notes),
        )

        warnings = []
        if len(high_conf_notes) == 0:
            warnings.append("No high-confidence notes detected")
        elif len(high_conf_notes) < 5:
            warnings.append(f"Only {len(high_conf_notes)} notes detected - may need recovery passes")

        return PassResult(
            notes=high_conf_notes,
            statistics=stats,
            warnings=warnings,
            metadata={
                "extraction_method": "basic_pitch" if self.use_basic_pitch else "librosa",
            },
        )

    def _adapt_onset_threshold(self, context: ExtractionContext) -> float:
        """Adapt onset threshold based on context."""
        base_threshold = context.onset_threshold

        # Adjust based on stem quality if available
        if context.stem_quality is not None:
            quality = context.stem_quality
            # Lower threshold for cleaner stems
            if hasattr(quality, "transient_integrity"):
                if quality.transient_integrity > 0.7:
                    base_threshold *= 0.9
                elif quality.transient_integrity < 0.4:
                    base_threshold *= 1.2

        # Adjust based on role if available
        if context.role_classification is not None:
            role = context.role_classification
            if hasattr(role, "primary_role"):
                if role.primary_role in ["pad_atmosphere", "texture_layer"]:
                    # Pads have soft attacks - lower threshold
                    base_threshold *= 0.7
                elif role.primary_role in ["transient_fx", "rhythmic_element"]:
                    # Transient-heavy - can use higher threshold
                    base_threshold *= 1.1

        return min(max(base_threshold, 0.2), 0.9)

    def _adapt_frame_threshold(self, context: ExtractionContext) -> float:
        """Adapt frame threshold based on context."""
        base_threshold = context.frame_threshold

        # Similar adaptations as onset
        if context.stem_quality is not None:
            quality = context.stem_quality
            if hasattr(quality, "harmonic_purity"):
                if quality.harmonic_purity > 0.7:
                    base_threshold *= 0.9
                elif quality.harmonic_purity < 0.4:
                    base_threshold *= 1.2

        return min(max(base_threshold, 0.2), 0.9)

    def _extract_with_basic_pitch(
        self,
        audio: np.ndarray,
        sr: int,
        onset_threshold: float,
        frame_threshold: float,
    ) -> List[ExtractedNote]:
        """Extract notes using basic_pitch library."""
        try:
            from basic_pitch.inference import predict
            from basic_pitch import ICASSP_2022_MODEL_PATH

            # Ensure mono
            if audio.ndim > 1:
                audio = np.mean(audio, axis=0)

            # basic_pitch expects float32
            audio = audio.astype(np.float32)

            # Run prediction
            model_output, midi_data, note_events = predict(
                audio,
                sr,
                onset_threshold=onset_threshold,
                frame_threshold=frame_threshold,
                minimum_note_length=self.min_note_ms,
            )

            # Convert to ExtractedNote
            notes = []
            for note in note_events:
                # note format: (start_time, end_time, pitch, velocity, confidence)
                start, end, pitch, velocity, confidence = note

                # Skip short notes
                if (end - start) * 1000 < self.min_note_ms:
                    continue

                notes.append(ExtractedNote(
                    pitch=int(pitch),
                    start=float(start),
                    end=float(end),
                    velocity=int(velocity * 127) if velocity <= 1 else int(velocity),
                    confidence=float(confidence),
                    source_pass=self.pass_number,
                ))

            return notes

        except ImportError:
            logger.warning("basic_pitch not available, falling back to librosa")
            return self._extract_with_librosa(audio, sr, onset_threshold)
        except Exception as e:
            logger.error(f"basic_pitch extraction failed: {e}")
            return self._extract_with_librosa(audio, sr, onset_threshold)

    def _extract_with_librosa(
        self,
        audio: np.ndarray,
        sr: int,
        onset_threshold: float,
    ) -> List[ExtractedNote]:
        """Extract notes using librosa (fallback method)."""
        # Ensure mono
        if audio.ndim > 1:
            audio = np.mean(audio, axis=0)

        # Detect onsets
        onset_env = librosa.onset.onset_strength(y=audio, sr=sr)
        onsets = librosa.onset.onset_detect(
            onset_envelope=onset_env,
            sr=sr,
            delta=onset_threshold,
            units="time",
        )

        if len(onsets) == 0:
            return []

        # Estimate pitches using piptrack
        pitches, magnitudes = librosa.piptrack(y=audio, sr=sr)

        notes = []
        hop_length = 512
        min_duration = self.min_note_ms / 1000.0

        for i, onset_time in enumerate(onsets):
            # Find end time (next onset or end of audio)
            if i < len(onsets) - 1:
                end_time = onsets[i + 1]
            else:
                end_time = len(audio) / sr

            # Skip short notes
            if end_time - onset_time < min_duration:
                continue

            # Get frame index for onset
            onset_frame = int(onset_time * sr / hop_length)
            if onset_frame >= pitches.shape[1]:
                continue

            # Find dominant pitch around onset
            pitch, confidence = self._get_dominant_pitch(
                pitches[:, onset_frame],
                magnitudes[:, onset_frame],
            )

            if pitch is None:
                continue

            # Estimate velocity from magnitude
            velocity = int(np.clip(confidence * 127, 20, 127))

            notes.append(ExtractedNote(
                pitch=pitch,
                start=onset_time,
                end=end_time,
                velocity=velocity,
                confidence=confidence,
                source_pass=self.pass_number,
            ))

        return notes

    def _get_dominant_pitch(
        self,
        pitches: np.ndarray,
        magnitudes: np.ndarray,
    ) -> Tuple[Optional[int], float]:
        """Get dominant pitch from piptrack frame."""
        # Find strongest pitch
        idx = magnitudes.argmax()
        pitch_hz = pitches[idx]
        magnitude = magnitudes[idx]

        if pitch_hz <= 0 or magnitude <= 0:
            return None, 0.0

        # Convert Hz to MIDI
        midi_pitch = int(round(librosa.hz_to_midi(pitch_hz)))

        # Clamp to valid MIDI range
        if midi_pitch < 0 or midi_pitch > 127:
            return None, 0.0

        # Normalize magnitude to confidence
        confidence = float(np.clip(magnitude / magnitudes.max(), 0, 1))

        return midi_pitch, confidence

    def _filter_high_confidence(
        self,
        notes: List[ExtractedNote],
        context: ExtractionContext,
    ) -> List[ExtractedNote]:
        """Filter notes to keep only high-confidence ones."""
        filtered = []

        for note in notes:
            # Basic confidence filter
            if note.confidence < self.min_confidence:
                continue

            # Velocity filter
            if note.velocity < context.min_velocity:
                continue

            # Duration filter
            if note.duration_ms < context.min_note_ms:
                continue

            # Additional quality-based filtering
            if context.contamination is not None:
                # Skip notes in heavily contaminated regions
                if self._is_in_contaminated_region(note, context.contamination):
                    # Lower confidence for notes in contaminated regions
                    note = replace(note, confidence=note.confidence * 0.7)
                    if note.confidence < self.min_confidence:
                        continue

            filtered.append(note)

        return filtered

    def _is_in_contaminated_region(
        self,
        note: ExtractedNote,
        contamination,
    ) -> bool:
        """Check if note falls within a contaminated region."""
        if not hasattr(contamination, "events"):
            return False

        for event in contamination.events:
            if hasattr(event, "start_time") and hasattr(event, "end_time"):
                if event.start_time <= note.start <= event.end_time:
                    if hasattr(event, "severity") and event.severity > 0.5:
                        return True

        return False
