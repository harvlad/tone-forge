"""Arpeggiator stem extraction pipeline.

Optimized for:
- Fast rhythmic patterns
- Precise timing
- Pattern preservation
- Short repeated notes
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


class ArpPipeline(StemPipeline):
    """Pipeline optimized for arpeggiator/sequence extraction.

    Key behaviors:
    - Strong pattern preservation
    - Precise quantization
    - Short note support
    - Strict timing grid
    - High note density handling
    """

    @property
    def name(self) -> str:
        return "arp"

    def _default_config(self) -> PipelineConfig:
        return PipelineConfig(
            # Higher onset threshold for clean triggers
            onset_threshold=0.55,
            frame_threshold=0.4,
            min_note_ms=20.0,  # Allow very short notes

            # Strong quantization for arps
            quantize_strength=0.85,
            quantize_grid=32,  # 32nd note grid for fast arps

            # No merging - preserve individual notes
            merge_max_gap_ms=0.0,
            merge_enabled=False,

            # Light key filtering (arps are usually in key)
            key_filter_strictness=0.5,

            # Don't filter isolated notes (could be arp start)
            isolated_filter_enabled=False,

            # Harmonic handling
            harmonic_suppression_enabled=True,
            octave_correction_enabled=False,
            subharmonic_cleanup_enabled=False,

            # Velocity - arps often have consistent velocity
            velocity_normalize=True,
            velocity_min=80,
            velocity_max=100,

            # Precision filters
            precision_filters=[
                "octave_false_positive",
                "harmonic_duplicate",
                "transient_validator",
                "repeated_pattern",
            ],
        )

    def _setup_filters(self) -> None:
        """Set up precision recovery filters for arps."""
        # Octave filter
        self._precision_filters.append(
            OctaveFalsePositiveFilter(
                min_suppression_confidence=0.75,
                protection_weight=1.5,
            )
        )

        # Harmonic filter
        self._precision_filters.append(
            HarmonicDuplicateFilter(
                min_suppression_confidence=0.7,
                protection_weight=1.5,
            )
        )

        # Transient validator - critical for arps
        self._precision_filters.append(
            TransientNoteValidator(
                min_suppression_confidence=0.65,
                protection_weight=1.6,
            )
        )

        # Pattern validator - VERY important for arps
        self._precision_filters.append(
            RepeatedPatternValidator(
                min_suppression_confidence=0.5,
                protection_weight=2.5,  # Very strong pattern protection
            )
        )

    def _preprocess_audio(
        self,
        audio: np.ndarray,
        sr: int,
    ) -> np.ndarray:
        """Preprocess audio for arp extraction.

        - Full mid-range focus
        - Enhance transients slightly
        """
        from scipy import signal

        # Band-pass (100 Hz to 10 kHz)
        nyquist = sr / 2
        low = min(100 / nyquist, 0.99)
        high = min(10000 / nyquist, 0.99)
        b, a = signal.butter(2, [low, high], btype='band')
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
        """Post-process notes for arp extraction.

        - Enforce arp pitch range
        - Detect and preserve arp patterns
        - Clean up non-pattern notes
        """
        if not notes:
            return notes

        # Filter to typical arp pitch range (C3-C6)
        filtered = [n for n in notes if 48 <= n.pitch <= 84]

        # Detect arp pattern and enforce
        if tempo and len(filtered) > 4:
            filtered = self._enforce_arp_pattern(filtered, tempo)

        return filtered

    def _enforce_arp_pattern(
        self,
        notes: List[ExtractedNote],
        tempo: float,
    ) -> List[ExtractedNote]:
        """Enforce arp-like pattern structure.

        Arps typically have:
        - Regular timing intervals
        - Repeating pitch sequences
        - Consistent note durations
        """
        if not notes:
            return notes

        beat_duration = 60.0 / tempo

        # Common arp intervals (in beats)
        arp_intervals = [
            beat_duration / 4,   # 16th
            beat_duration / 2,   # 8th
            beat_duration / 3,   # Triplet 8th
            beat_duration / 6,   # 16th triplet
        ]

        sorted_notes = sorted(notes, key=lambda n: n.start)

        # Calculate actual intervals
        intervals = []
        for i in range(1, len(sorted_notes)):
            interval = sorted_notes[i].start - sorted_notes[i - 1].start
            intervals.append(interval)

        if not intervals:
            return notes

        # Find the most consistent interval
        best_interval = None
        best_match_count = 0

        for arp_interval in arp_intervals:
            match_count = sum(
                1 for i in intervals
                if abs(i - arp_interval) < arp_interval * 0.15  # 15% tolerance
            )
            if match_count > best_match_count:
                best_match_count = match_count
                best_interval = arp_interval

        # If we found a strong pattern, clean up notes that don't fit
        if best_interval and best_match_count > len(intervals) * 0.5:
            # Re-quantize to detected pattern
            quantized = []
            for note in sorted_notes:
                grid_pos = round(note.start / best_interval)
                quantized_start = grid_pos * best_interval

                # Only keep if close to grid
                if abs(note.start - quantized_start) < best_interval * 0.2:
                    quantized.append(ExtractedNote(
                        pitch=note.pitch,
                        start=quantized_start,
                        end=quantized_start + best_interval * 0.8,  # Standard gate
                        velocity=note.velocity,
                        confidence=note.confidence,
                        source_pass=note.source_pass,
                    ))

            return quantized

        return notes

    def _detect_arp_sequence(
        self,
        notes: List[ExtractedNote],
    ) -> Optional[List[int]]:
        """Detect repeating pitch sequence in arp.

        Returns the pitch sequence if found, None otherwise.
        """
        if len(notes) < 4:
            return None

        pitches = [n.pitch for n in sorted(notes, key=lambda n: n.start)]

        # Try to find repeating sequence of length 2-8
        for seq_len in range(2, min(9, len(pitches) // 2)):
            sequence = pitches[:seq_len]
            is_match = True

            for i in range(seq_len, len(pitches)):
                if pitches[i] != sequence[i % seq_len]:
                    is_match = False
                    break

            if is_match:
                return sequence

        return None
