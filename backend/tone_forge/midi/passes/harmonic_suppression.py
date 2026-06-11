"""Harmonic suppression pass.

Removes notes that are harmonic overtones of other notes rather than
intentional musical content. Common artifacts:
- Octave harmonics (2x frequency)
- Fifth harmonics (3x frequency / 1.5x)
- Third harmonics (5x frequency / 1.25x)

Uses probabilistic scoring to avoid false positives.
"""
from __future__ import annotations

import logging
import time
from dataclasses import replace
from typing import Dict, List, Optional, Set, Tuple

import numpy as np

from .base import (
    ExtractionContext,
    ExtractionPass,
    ExtractedNote,
    NoteFlag,
    NoteProvenance,
    PassResult,
)

logger = logging.getLogger(__name__)

# MIDI semitone intervals for common harmonics
OCTAVE_INTERVAL = 12  # Perfect octave
FIFTH_INTERVAL = 7    # Perfect fifth (approximates 3rd harmonic)
MAJOR_THIRD_INTERVAL = 4  # Major third (approximates 5th harmonic)
MINOR_THIRD_INTERVAL = 3  # Minor third


class HarmonicSuppressionPass(ExtractionPass):
    """Remove harmonic overtone artifacts.

    This pass identifies notes that are likely harmonics of other notes
    rather than intentional musical content. Uses probabilistic scoring
    based on:
    - Timing alignment with potential fundamental
    - Relative velocity (harmonics are typically quieter)
    - Relative confidence (harmonics have lower extraction confidence)
    - Harmonic relationship strength

    Notes are not removed outright - their confidence is reduced based
    on harmonic probability, and provenance is updated.
    """

    def __init__(
        self,
        pass_number: int = 0,
        octave_enabled: bool = True,
        fifth_enabled: bool = True,
        third_enabled: bool = False,
        min_harmonic_probability: float = 0.7,
        timing_tolerance_ms: float = 30.0,
        velocity_ratio_threshold: float = 0.8,
        confidence_ratio_threshold: float = 0.9,
    ):
        """Initialize harmonic suppression pass.

        Args:
            pass_number: Pass number in pipeline
            octave_enabled: Suppress octave harmonics
            fifth_enabled: Suppress fifth harmonics
            third_enabled: Suppress third harmonics (more aggressive)
            min_harmonic_probability: Minimum probability to suppress
            timing_tolerance_ms: Max timing difference to consider aligned
            velocity_ratio_threshold: Max velocity ratio (harmonic/fundamental)
            confidence_ratio_threshold: Max confidence ratio
        """
        super().__init__(pass_number)
        self.octave_enabled = octave_enabled
        self.fifth_enabled = fifth_enabled
        self.third_enabled = third_enabled
        self.min_harmonic_probability = min_harmonic_probability
        self.timing_tolerance_ms = timing_tolerance_ms
        self.velocity_ratio_threshold = velocity_ratio_threshold
        self.confidence_ratio_threshold = confidence_ratio_threshold

    @property
    def name(self) -> str:
        return "harmonic_suppression"

    def process(
        self,
        notes: List[ExtractedNote],
        context: ExtractionContext,
    ) -> PassResult:
        """Suppress harmonic overtones.

        Args:
            notes: Input notes
            context: Extraction context

        Returns:
            PassResult with harmonics suppressed
        """
        start_time = time.time()
        input_notes = notes.copy()

        if len(notes) < 2:
            return PassResult(
                notes=notes,
                statistics=self._create_statistics(input_notes, notes, 0.0),
            )

        # Build intervals to check
        intervals_to_check = []
        if self.octave_enabled:
            intervals_to_check.append((OCTAVE_INTERVAL, "octave", 1.0))
            intervals_to_check.append((OCTAVE_INTERVAL * 2, "double_octave", 0.9))
        if self.fifth_enabled:
            intervals_to_check.append((OCTAVE_INTERVAL + FIFTH_INTERVAL, "octave_fifth", 0.85))
        if self.third_enabled:
            intervals_to_check.append((OCTAVE_INTERVAL + MAJOR_THIRD_INTERVAL, "octave_third", 0.7))

        # Index notes by pitch for fast lookup
        notes_by_pitch: Dict[int, List[Tuple[int, ExtractedNote]]] = {}
        for i, note in enumerate(notes):
            if note.pitch not in notes_by_pitch:
                notes_by_pitch[note.pitch] = []
            notes_by_pitch[note.pitch].append((i, note))

        # Score each note for harmonic probability
        harmonic_scores: Dict[int, Tuple[float, str, int]] = {}  # idx -> (prob, type, fundamental_idx)

        for pitch, indexed_notes in notes_by_pitch.items():
            for idx, note in indexed_notes:
                best_prob = 0.0
                best_type = ""
                best_fundamental_idx = -1

                # Check if this note could be a harmonic of a lower pitch
                for interval, harmonic_type, base_weight in intervals_to_check:
                    fundamental_pitch = pitch - interval
                    if fundamental_pitch < 0:
                        continue

                    if fundamental_pitch not in notes_by_pitch:
                        continue

                    # Check for aligned fundamentals
                    for fund_idx, fund_note in notes_by_pitch[fundamental_pitch]:
                        prob = self._compute_harmonic_probability(
                            note, fund_note, base_weight
                        )
                        if prob > best_prob:
                            best_prob = prob
                            best_type = harmonic_type
                            best_fundamental_idx = fund_idx

                if best_prob >= self.min_harmonic_probability:
                    harmonic_scores[idx] = (best_prob, best_type, best_fundamental_idx)

        # Apply suppression
        output_notes = []
        suppressed_count = 0
        confidence_reduced_count = 0

        for i, note in enumerate(notes):
            if i in harmonic_scores:
                prob, harmonic_type, fund_idx = harmonic_scores[i]

                # Update provenance
                provenance = note.provenance or NoteProvenance()
                provenance = replace(
                    provenance,
                    cleanup_passes=provenance.cleanup_passes + [self.name],
                    suppression_reasons=provenance.suppression_reasons + [
                        f"harmonic_{harmonic_type}_p{prob:.2f}"
                    ],
                )

                if prob >= 0.9:
                    # High confidence harmonic - suppress entirely
                    suppressed_count += 1
                    continue
                else:
                    # Reduce confidence proportionally
                    new_confidence = note.confidence * (1 - prob * 0.5)
                    provenance = replace(provenance, final_confidence=new_confidence)

                    modified_note = replace(
                        note,
                        confidence=new_confidence,
                        flags=note.flags | {NoteFlag.LOW_CONFIDENCE},
                        provenance=provenance,
                    )
                    output_notes.append(modified_note)
                    confidence_reduced_count += 1
            else:
                output_notes.append(note)

        execution_time = (time.time() - start_time) * 1000

        stats = self._create_statistics(
            input_notes,
            output_notes,
            execution_time,
            harmonics_suppressed=suppressed_count,
            confidence_reduced=confidence_reduced_count,
            intervals_checked=len(intervals_to_check),
        )

        warnings = []
        if suppressed_count > len(input_notes) * 0.3:
            warnings.append(
                f"Suppressed {suppressed_count}/{len(input_notes)} notes as harmonics - "
                "may be over-aggressive"
            )

        return PassResult(
            notes=output_notes,
            statistics=stats,
            warnings=warnings,
            metadata={
                "octave_enabled": self.octave_enabled,
                "fifth_enabled": self.fifth_enabled,
                "third_enabled": self.third_enabled,
            },
        )

    def _compute_harmonic_probability(
        self,
        candidate: ExtractedNote,
        fundamental: ExtractedNote,
        base_weight: float,
    ) -> float:
        """Compute probability that candidate is a harmonic of fundamental.

        Factors:
        - Timing alignment (must overlap or be very close)
        - Velocity ratio (harmonic should be quieter)
        - Confidence ratio (harmonic should have lower confidence)
        - Duration similarity (harmonics have similar duration)
        """
        prob = base_weight

        # Timing alignment
        timing_diff_ms = abs(candidate.start - fundamental.start) * 1000
        if timing_diff_ms > self.timing_tolerance_ms:
            # Not aligned - unlikely to be harmonic
            return 0.0

        # Must overlap in time
        if candidate.start > fundamental.end or fundamental.start > candidate.end:
            # No overlap
            prob *= 0.3

        # Velocity ratio
        if fundamental.velocity > 0:
            velocity_ratio = candidate.velocity / fundamental.velocity
            if velocity_ratio > self.velocity_ratio_threshold:
                # Candidate is too loud to be a harmonic
                prob *= 0.5
            elif velocity_ratio < 0.5:
                # Much quieter - more likely harmonic
                prob *= 1.2

        # Confidence ratio
        if fundamental.confidence > 0:
            confidence_ratio = candidate.confidence / fundamental.confidence
            if confidence_ratio > self.confidence_ratio_threshold:
                # Candidate is too confident to be artifact
                prob *= 0.6
            elif confidence_ratio < 0.7:
                # Much lower confidence - more likely harmonic
                prob *= 1.1

        # Duration similarity
        if fundamental.duration > 0:
            duration_ratio = candidate.duration / fundamental.duration
            if 0.7 < duration_ratio < 1.3:
                # Similar duration - harmonics track fundamental
                prob *= 1.1
            elif duration_ratio > 2.0 or duration_ratio < 0.5:
                # Very different duration - less likely harmonic
                prob *= 0.7

        return min(1.0, max(0.0, prob))
