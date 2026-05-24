"""Bass stem extraction pipeline.

Optimized for:
- Monophonic extraction
- Octave accuracy (avoid sub-harmonic errors)
- Low frequency focus
- Root note stability
"""
from __future__ import annotations

from typing import List, Optional, Tuple

import numpy as np

from tone_forge.midi.passes.base import ExtractedNote
from tone_forge.midi.filters.octave_false_positive import OctaveFalsePositiveFilter
from tone_forge.midi.filters.subharmonic_cleanup import SubharmonicCleanupFilter
from tone_forge.midi.filters.repeated_pattern import RepeatedPatternValidator
from tone_forge.midi.polyphony_estimator import PolyphonyEstimate, PolyphonyClass
from .base import StemPipeline, PipelineConfig


class BassPipeline(StemPipeline):
    """Pipeline optimized for bass extraction.

    Key behaviors:
    - Enforces monophonic output (one note at a time)
    - Strong octave correction to avoid sub-harmonic errors
    - Focus on low frequency range
    - Pattern protection for bass ostinatos
    - Heavier quantization for rhythmic precision
    """

    @property
    def name(self) -> str:
        return "bass"

    def _default_config(self) -> PipelineConfig:
        return PipelineConfig(
            # Lower thresholds for bass (often less transient)
            onset_threshold=0.35,
            frame_threshold=0.25,
            min_note_ms=50.0,

            # Strong quantization for bass
            quantize_strength=0.7,
            quantize_grid=8,  # 8th note grid

            # Merge close notes (bass tends to sustain)
            merge_max_gap_ms=50.0,
            merge_enabled=True,

            # Moderate key filtering
            key_filter_strictness=0.4,

            # Don't filter isolated bass notes
            isolated_filter_enabled=False,

            # Harmonic handling - critical for bass
            harmonic_suppression_enabled=False,  # Bass IS the fundamental
            octave_correction_enabled=True,  # Very important
            subharmonic_cleanup_enabled=True,

            # Velocity - bass usually more consistent
            velocity_normalize=True,
            velocity_min=80,
            velocity_max=100,

            # Precision filters
            precision_filters=[
                "octave_false_positive",
                "subharmonic_cleanup",
                "repeated_pattern",
            ],
        )

    def _setup_filters(self) -> None:
        """Set up precision recovery filters for bass."""
        # Octave filter - very important for bass
        self._precision_filters.append(
            OctaveFalsePositiveFilter(
                min_suppression_confidence=0.65,  # Lower threshold
                protection_weight=1.2,
            )
        )

        # Subharmonic cleanup - critical
        self._precision_filters.append(
            SubharmonicCleanupFilter(
                min_suppression_confidence=0.7,
                protection_weight=2.0,  # But protect real low notes
                min_pitch=28,  # E1
            )
        )

        # Pattern validator - protect bass patterns strongly
        self._precision_filters.append(
            RepeatedPatternValidator(
                min_suppression_confidence=0.5,
                protection_weight=2.5,  # Very strong pattern protection
            )
        )

    def _adapt_to_polyphony(self, estimate: PolyphonyEstimate) -> None:
        """Adapt bass pipeline based on polyphony estimation.

        Bass is almost always monophonic, but some synth bass or
        layered bass may have light polyphony.
        """
        if estimate.polyphony_class == PolyphonyClass.LIGHT_POLY:
            # Possible layered bass or bass + sub
            # Be more careful about merging
            self.config.merge_max_gap_ms = 30.0  # Shorter merge gap

            # May have intentional doubled notes
            for f in self._precision_filters:
                if hasattr(f, 'protection_weight'):
                    f.protection_weight *= 1.2

        elif estimate.polyphony_class == PolyphonyClass.DENSE_POLY:
            # This is unusual for bass - might be incorrectly classified
            # or this is actually a synth/pad stem
            # Log warning and use conservative settings
            self.config.harmonic_suppression_enabled = True
            self.config.subharmonic_cleanup_enabled = True

        # Always enforce monophonic for bass regardless of estimate
        # The estimate helps with cleanup aggressiveness, not voice count
        # (bass should always output monophonic)

    def _preprocess_audio(
        self,
        audio: np.ndarray,
        sr: int,
    ) -> np.ndarray:
        """Preprocess audio for bass extraction.

        - Low-pass filter to focus on bass frequencies
        - Remove high frequency content that confuses detection
        """
        from scipy import signal

        # Low-pass filter at 500 Hz
        nyquist = sr / 2
        low_cutoff = min(500 / nyquist, 0.99)
        b, a = signal.butter(4, low_cutoff, btype='low')
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
        """Post-process notes for bass extraction.

        - Correct octave errors FIRST (basic-pitch often detects bass an octave low)
        - Filter to bass pitch range (E1-E4)
        - Enforce monophonic (one note at a time)
        """
        if not notes:
            return notes

        # First, apply aggressive octave correction
        # Basic-pitch often detects bass one octave too low
        corrected = self._correct_bass_octave(notes)

        # Filter to bass pitch range (E1=28 to E4=64)
        filtered = [n for n in corrected if 28 <= n.pitch <= 64]

        # Sort by time
        filtered = sorted(filtered, key=lambda n: n.start)

        # Enforce monophonic - when notes overlap, keep the one with higher confidence
        monophonic = self._enforce_monophonic(filtered)

        # Apply additional octave correction based on key if available
        if key:
            monophonic = self._apply_octave_correction(monophonic, key)

        return monophonic

    def _correct_bass_octave(
        self,
        notes: List[ExtractedNote],
    ) -> List[ExtractedNote]:
        """Correct octave errors in bass detection.

        Basic-pitch often detects bass notes one octave too low due to
        strong sub-harmonics in synth bass. This function shifts notes
        that are likely in the wrong octave.

        Bass typically sits in E1-E3 range (28-52). Notes below E2 (40)
        that have a corresponding note one octave up should be shifted.
        """
        if not notes:
            return notes

        # Typical bass range is E2-E3 (40-52) for synth bass
        # Notes in E1-D#2 (28-39) may be octave errors

        # Count notes by pitch class and octave
        from collections import Counter
        pitch_classes = Counter(n.pitch % 12 for n in notes)
        octaves = Counter(n.pitch // 12 for n in notes)

        # Find the most common octave (should be 2 or 3 for bass)
        # If octave 1 or 2 dominates and there are also notes in octave 3,
        # likely the lower notes are errors
        common_octave = octaves.most_common(1)[0][0] if octaves else 3

        # If most notes are in octave 2 (36-47) but we expect bass to be
        # in a typical synth bass range, shift up
        corrected = []
        for note in notes:
            pitch = note.pitch
            octave = pitch // 12

            # Notes below D2 (38) are likely sub-harmonic artifacts
            # Shift them up an octave
            if pitch < 38:
                pitch += 12

            # If the most common octave is 2 (E1-E2 range) but we have
            # pitch classes that commonly appear in bass (D, A, E, G),
            # consider shifting up notes that are too low
            elif octave == 2 and pitch < 40:
                # Check if same pitch class exists an octave higher
                pitch_class = pitch % 12
                higher_exists = any(
                    n.pitch == pitch + 12 or n.pitch == pitch + 24
                    for n in notes
                )
                if not higher_exists:
                    # Shift up if no higher version exists
                    pitch += 12

            if pitch != note.pitch:
                corrected.append(ExtractedNote(
                    pitch=pitch,
                    start=note.start,
                    end=note.end,
                    velocity=note.velocity,
                    confidence=note.confidence * 0.95,  # Slightly reduce confidence
                    source_pass=note.source_pass,
                    flags=note.flags,
                    provenance=note.provenance,
                ))
            else:
                corrected.append(note)

        return corrected

    def _enforce_monophonic(
        self,
        notes: List[ExtractedNote],
    ) -> List[ExtractedNote]:
        """Ensure only one note plays at a time."""
        if not notes:
            return notes

        result = []
        sorted_notes = sorted(notes, key=lambda n: (n.start, -n.confidence))

        for note in sorted_notes:
            # Check for overlap with existing notes
            overlaps = False
            for existing in result:
                if note.start < existing.end and note.end > existing.start:
                    # Overlap detected
                    if note.confidence > existing.confidence:
                        # New note wins - truncate existing
                        existing_idx = result.index(existing)
                        result[existing_idx] = ExtractedNote(
                            pitch=existing.pitch,
                            start=existing.start,
                            end=min(existing.end, note.start),
                            velocity=existing.velocity,
                            confidence=existing.confidence,
                            source_pass=existing.source_pass,
                        )
                    else:
                        overlaps = True
                        break

            if not overlaps:
                result.append(note)

        return sorted(result, key=lambda n: n.start)

    def _apply_octave_correction(
        self,
        notes: List[ExtractedNote],
        key: Tuple[int, str],
    ) -> List[ExtractedNote]:
        """Correct obvious octave errors based on key.

        If most bass notes are in one octave range, outliers are likely errors.
        """
        if len(notes) < 3:
            return notes

        root, _ = key
        pitches = [n.pitch for n in notes]

        # Find the most common octave
        octaves = [p // 12 for p in pitches]
        from collections import Counter
        octave_counts = Counter(octaves)
        most_common_octave = octave_counts.most_common(1)[0][0]

        # Correct notes that are an octave off
        corrected = []
        for note in notes:
            note_octave = note.pitch // 12
            pitch_class = note.pitch % 12

            if abs(note_octave - most_common_octave) == 1:
                # One octave off - consider correction
                if octave_counts[note_octave] < octave_counts[most_common_octave] * 0.3:
                    # This octave is rare, likely an error
                    new_pitch = most_common_octave * 12 + pitch_class
                    corrected.append(ExtractedNote(
                        pitch=new_pitch,
                        start=note.start,
                        end=note.end,
                        velocity=note.velocity,
                        confidence=note.confidence * 0.9,  # Slightly reduce confidence
                        source_pass=note.source_pass,
                    ))
                    continue

            corrected.append(note)

        return corrected
