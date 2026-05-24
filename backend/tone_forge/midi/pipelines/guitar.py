"""Guitar stem extraction pipeline.

Optimized for:
- Strumming patterns
- Polyphonic chords
- String sustain and decay
- Hammer-ons and pull-offs
"""
from __future__ import annotations

from typing import List, Optional, Tuple

import numpy as np

from tone_forge.midi.passes.base import ExtractedNote
from tone_forge.midi.filters.octave_false_positive import OctaveFalsePositiveFilter
from tone_forge.midi.filters.harmonic_duplicate import HarmonicDuplicateFilter
from tone_forge.midi.filters.transient_validator import TransientNoteValidator
from tone_forge.midi.filters.repeated_pattern import RepeatedPatternValidator
from .base import StemPipeline, PipelineConfig


class GuitarPipeline(StemPipeline):
    """Pipeline optimized for guitar extraction.

    Key behaviors:
    - Handle polyphonic chords (6 strings max)
    - Preserve strumming patterns with slight time offsets
    - Strong transient detection for picking
    - Handle sustain and natural decay
    - Support for various guitar techniques
    """

    @property
    def name(self) -> str:
        return "guitar"

    def _default_config(self) -> PipelineConfig:
        return PipelineConfig(
            # Moderate thresholds
            onset_threshold=0.45,
            frame_threshold=0.35,
            min_note_ms=40.0,

            # Light quantization (strumming has natural timing)
            quantize_strength=0.3,
            quantize_grid=16,

            # Some merging for sustained notes
            merge_max_gap_ms=30.0,
            merge_enabled=True,

            # Moderate key filtering
            key_filter_strictness=0.4,

            # Keep isolated notes (could be single string hits)
            isolated_filter_enabled=False,

            # Harmonic handling
            harmonic_suppression_enabled=True,
            octave_correction_enabled=False,
            subharmonic_cleanup_enabled=False,

            # Velocity - guitars have dynamic range
            velocity_normalize=True,
            velocity_min=60,
            velocity_max=120,

            # Precision filters
            precision_filters=[
                "octave_false_positive",
                "harmonic_duplicate",
                "transient_validator",
                "repeated_pattern",
            ],
        )

    def _setup_filters(self) -> None:
        """Set up precision recovery filters for guitar."""
        # Octave filter
        self._precision_filters.append(
            OctaveFalsePositiveFilter(
                min_suppression_confidence=0.7,
                protection_weight=1.4,
            )
        )

        # Harmonic filter - guitars have strong harmonics
        self._precision_filters.append(
            HarmonicDuplicateFilter(
                min_suppression_confidence=0.65,
                protection_weight=1.6,
            )
        )

        # Transient validator - picking transients
        self._precision_filters.append(
            TransientNoteValidator(
                min_suppression_confidence=0.7,
                protection_weight=1.4,
            )
        )

        # Pattern validator - strumming patterns
        self._precision_filters.append(
            RepeatedPatternValidator(
                min_suppression_confidence=0.6,
                protection_weight=1.8,
            )
        )

    def _preprocess_audio(
        self,
        audio: np.ndarray,
        sr: int,
    ) -> np.ndarray:
        """Preprocess audio for guitar extraction.

        - Full frequency range for acoustic and electric
        - Light high-pass to remove rumble
        """
        from scipy import signal

        # High-pass at 80 Hz (lowest guitar note is E2 = 82 Hz)
        nyquist = sr / 2
        high_cutoff = min(80 / nyquist, 0.99)
        b, a = signal.butter(2, high_cutoff, btype='high')
        filtered = signal.filtfilt(b, a, audio)

        return filtered.astype(np.float32)

    def _postprocess_notes(
        self,
        notes: List[ExtractedNote],
        audio: np.ndarray,
        sr: int,
        tempo: Optional[float],
        key: Optional[Tuple[int, str]],
    ) -> List[ExtractedNote]:
        """Post-process notes for guitar extraction.

        - Filter to guitar pitch range
        - Handle chord voicings
        - Limit simultaneous notes to 6 (guitar has 6 strings)
        """
        if not notes:
            return notes

        # Filter to guitar pitch range (E2=40 to E6=88)
        filtered = [n for n in notes if 40 <= n.pitch <= 88]

        # Limit polyphony to 6 notes at any given time
        filtered = self._limit_polyphony(filtered, max_voices=6)

        # Clean up strumming artifacts
        filtered = self._clean_strumming_artifacts(filtered)

        return filtered

    def _limit_polyphony(
        self,
        notes: List[ExtractedNote],
        max_voices: int = 6,
    ) -> List[ExtractedNote]:
        """Limit simultaneous notes to max_voices."""
        if not notes:
            return notes

        sorted_notes = sorted(notes, key=lambda n: n.start)
        result = []

        for note in sorted_notes:
            # Count currently active notes
            active = sum(
                1 for n in result
                if n.start <= note.start < n.end
            )

            if active < max_voices:
                result.append(note)
            else:
                # Too many notes - only add if higher confidence than existing
                active_notes = [
                    n for n in result
                    if n.start <= note.start < n.end
                ]
                min_confidence_note = min(active_notes, key=lambda n: n.confidence)

                if note.confidence > min_confidence_note.confidence:
                    # Remove lowest confidence active note
                    result.remove(min_confidence_note)
                    result.append(note)

        return sorted(result, key=lambda n: n.start)

    def _clean_strumming_artifacts(
        self,
        notes: List[ExtractedNote],
    ) -> List[ExtractedNote]:
        """Clean up artifacts from strumming patterns.

        Strumming creates slight time offsets between strings.
        Notes within ~30ms of each other with chord-like intervals
        should be treated as simultaneous.
        """
        if len(notes) < 2:
            return notes

        strum_tolerance = 0.03  # 30ms

        # Group notes that could be strummed together
        sorted_notes = sorted(notes, key=lambda n: n.start)
        groups = []
        current_group = [sorted_notes[0]]

        for note in sorted_notes[1:]:
            if note.start - current_group[-1].start <= strum_tolerance:
                current_group.append(note)
            else:
                groups.append(current_group)
                current_group = [note]
        groups.append(current_group)

        # Process each group
        result = []
        for group in groups:
            if len(group) <= 6:
                result.extend(group)
            else:
                # Too many notes for one strum - likely artifacts
                # Keep highest confidence notes
                sorted_by_conf = sorted(group, key=lambda n: -n.confidence)
                result.extend(sorted_by_conf[:6])

        return sorted(result, key=lambda n: n.start)
