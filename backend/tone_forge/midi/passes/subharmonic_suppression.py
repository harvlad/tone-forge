"""Sub-harmonic suppression pass.

Removes notes that are sub-harmonics (lower octaves) of other detected notes.
This is common in bass extraction where the neural model detects both the
fundamental and phantom sub-harmonics one or two octaves below.

Unlike octave_correction which SHIFTS notes up, this pass REMOVES
sub-harmonics when a corresponding higher-octave note already exists.
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

# Default pitch floors by stem type (MIDI note numbers)
DEFAULT_PITCH_FLOORS = {
    "bass": 36,      # C2 - prevent sub-bass artifacts
    "lead": 48,      # C3 - leads rarely go below this
    "synth": 36,     # C2 - synths can go low but not sub-bass
    "pad": 36,       # C2 - pads typically in mid range
    "vocals": 48,    # C3 - vocal range
}


class SubHarmonicSuppressionPass(ExtractionPass):
    """Remove sub-harmonic artifacts from extracted notes.

    This pass identifies notes that are likely sub-harmonics (1 or 2 octaves
    below) of other detected notes and removes them. This is particularly
    useful for bass extraction where neural models often detect phantom
    fundamentals at lower octaves.

    Detection criteria:
    - Note is 12 or 24 semitones below another note at the same time
    - Note has lower or similar confidence to the higher-octave note
    - Note timing aligns with higher-octave note (within tolerance)

    The pass can also apply a hard pitch floor to remove all notes
    below a stem-specific threshold.
    """

    def __init__(
        self,
        pass_number: int = 0,
        timing_tolerance_ms: float = 50.0,
        min_suppression_probability: float = 0.6,
        check_single_octave: bool = True,
        check_double_octave: bool = True,
        prefer_higher_confidence: bool = True,
        apply_pitch_floor: bool = True,
        pitch_floors: Optional[Dict[str, int]] = None,
    ):
        """Initialize sub-harmonic suppression pass.

        Args:
            pass_number: Pass number in pipeline
            timing_tolerance_ms: Max timing difference to consider notes aligned
            min_suppression_probability: Minimum probability to suppress a note
            check_single_octave: Check for notes 12 semitones above
            check_double_octave: Check for notes 24 semitones above
            prefer_higher_confidence: Prefer keeping higher confidence notes
            apply_pitch_floor: Apply stem-specific pitch floor filtering
            pitch_floors: Custom pitch floors by stem type
        """
        super().__init__(pass_number)
        self.timing_tolerance_ms = timing_tolerance_ms
        self.min_suppression_probability = min_suppression_probability
        self.check_single_octave = check_single_octave
        self.check_double_octave = check_double_octave
        self.prefer_higher_confidence = prefer_higher_confidence
        self.apply_pitch_floor = apply_pitch_floor
        self.pitch_floors = pitch_floors or DEFAULT_PITCH_FLOORS

    @property
    def name(self) -> str:
        return "subharmonic_suppression"

    def process(
        self,
        notes: List[ExtractedNote],
        context: ExtractionContext,
    ) -> PassResult:
        """Remove sub-harmonic artifacts.

        Args:
            notes: Input notes
            context: Extraction context

        Returns:
            PassResult with sub-harmonics removed
        """
        start_time = time.time()
        input_notes = notes.copy()

        if len(notes) == 0:
            return PassResult(
                notes=[],
                statistics=self._create_statistics(input_notes, [], 0.0),
            )

        stem_type = context.stem_type or "bass"
        timing_tolerance_sec = self.timing_tolerance_ms / 1000.0

        # Phase 1: Apply pitch floor if enabled
        floor_removed = 0
        if self.apply_pitch_floor:
            pitch_floor = self.pitch_floors.get(stem_type, 24)  # Default to C1
            notes_after_floor = []
            for note in notes:
                if note.pitch >= pitch_floor:
                    notes_after_floor.append(note)
                else:
                    floor_removed += 1
                    logger.debug(
                        f"Pitch floor removed: MIDI {note.pitch} < {pitch_floor}"
                    )
            notes = notes_after_floor

        # Phase 2: Build time-indexed lookup for efficient matching
        # Group notes by quantized start time
        notes_by_time: Dict[float, List[Tuple[int, ExtractedNote]]] = {}
        for i, note in enumerate(notes):
            # Quantize time to tolerance buckets
            time_key = round(note.start / timing_tolerance_sec) * timing_tolerance_sec
            if time_key not in notes_by_time:
                notes_by_time[time_key] = []
            notes_by_time[time_key].append((i, note))

        # Phase 3: Identify sub-harmonics to suppress
        suppress_indices: Set[int] = set()
        suppression_reasons: Dict[int, str] = {}

        for time_key, time_notes in notes_by_time.items():
            # Get all pitches at this time
            pitches_at_time = {n.pitch: (i, n) for i, n in time_notes}

            for idx, note in time_notes:
                if idx in suppress_indices:
                    continue

                # Check if there's a note one octave higher
                octave_up = note.pitch + 12
                double_octave_up = note.pitch + 24

                higher_note = None
                interval = 0

                if self.check_single_octave and octave_up in pitches_at_time:
                    higher_note = pitches_at_time[octave_up][1]
                    interval = 12
                elif self.check_double_octave and double_octave_up in pitches_at_time:
                    higher_note = pitches_at_time[double_octave_up][1]
                    interval = 24

                if higher_note is not None:
                    # Compute suppression probability
                    prob, reason = self._compute_suppression_probability(
                        note, higher_note, interval
                    )

                    if prob >= self.min_suppression_probability:
                        suppress_indices.add(idx)
                        suppression_reasons[idx] = reason
                        logger.debug(
                            f"Sub-harmonic suppressed: MIDI {note.pitch} "
                            f"(octave of {higher_note.pitch}, prob={prob:.2f})"
                        )

        # Phase 4: Build output, excluding suppressed notes
        output_notes = []
        subharmonic_removed = 0

        for i, note in enumerate(notes):
            if i in suppress_indices:
                subharmonic_removed += 1
            else:
                output_notes.append(note)

        execution_time = (time.time() - start_time) * 1000

        stats = self._create_statistics(
            input_notes,
            output_notes,
            execution_time,
            floor_removed=floor_removed,
            subharmonic_removed=subharmonic_removed,
            stem_type=stem_type,
            pitch_floor=self.pitch_floors.get(stem_type, 24),
        )

        warnings = []
        total_removed = floor_removed + subharmonic_removed
        if total_removed > len(input_notes) * 0.5:
            warnings.append(
                f"Removed {total_removed}/{len(input_notes)} notes - "
                "may indicate systematic sub-harmonic detection issue"
            )

        return PassResult(
            notes=output_notes,
            statistics=stats,
            warnings=warnings,
            metadata={
                "floor_removed": floor_removed,
                "subharmonic_removed": subharmonic_removed,
                "total_removed": total_removed,
            },
        )

    def _compute_suppression_probability(
        self,
        lower_note: ExtractedNote,
        higher_note: ExtractedNote,
        interval: int,
    ) -> Tuple[float, str]:
        """Compute probability that lower_note is a sub-harmonic of higher_note.

        Args:
            lower_note: The potential sub-harmonic (lower pitch)
            higher_note: The potential fundamental (higher pitch)
            interval: Semitone interval (12 or 24)

        Returns:
            (probability, reason) tuple
        """
        factors = []
        reasons = []

        # Factor 1: Confidence comparison
        # Sub-harmonics typically have similar or lower confidence
        if self.prefer_higher_confidence:
            if lower_note.confidence <= higher_note.confidence:
                factors.append(0.8)
                reasons.append("lower_conf")
            elif lower_note.confidence > higher_note.confidence * 1.2:
                # Lower note has significantly higher confidence - less likely sub-harmonic
                factors.append(0.3)
                reasons.append("higher_conf")
            else:
                factors.append(0.5)
                reasons.append("similar_conf")
        else:
            factors.append(0.6)

        # Factor 2: Interval - single octave more likely than double
        if interval == 12:
            factors.append(0.85)
            reasons.append("single_octave")
        elif interval == 24:
            factors.append(0.7)
            reasons.append("double_octave")
        else:
            factors.append(0.5)

        # Factor 3: Velocity comparison
        # Sub-harmonics often have similar or lower velocity
        if lower_note.velocity <= higher_note.velocity:
            factors.append(0.75)
            reasons.append("lower_vel")
        elif lower_note.velocity > higher_note.velocity * 1.3:
            factors.append(0.4)
            reasons.append("higher_vel")
        else:
            factors.append(0.6)
            reasons.append("similar_vel")

        # Factor 4: Duration alignment
        # Sub-harmonics typically have similar duration
        lower_dur = lower_note.end - lower_note.start
        higher_dur = higher_note.end - higher_note.start
        if higher_dur > 0:
            dur_ratio = lower_dur / higher_dur
            if 0.7 <= dur_ratio <= 1.3:
                factors.append(0.8)
                reasons.append("aligned_dur")
            else:
                factors.append(0.5)
                reasons.append("misaligned_dur")
        else:
            factors.append(0.5)

        # Combine factors (weighted average)
        weights = [0.3, 0.3, 0.2, 0.2]  # confidence, interval, velocity, duration
        probability = sum(f * w for f, w in zip(factors, weights))

        reason = "+".join(reasons)
        return probability, reason


def create_subharmonic_suppression_pass(
    pass_number: int = 0,
    stem_type: Optional[str] = None,
    aggressive: bool = False,
) -> SubHarmonicSuppressionPass:
    """Factory function to create configured sub-harmonic suppression pass.

    Args:
        pass_number: Pass number in pipeline
        stem_type: Stem type for pitch floor selection
        aggressive: Use more aggressive suppression settings

    Returns:
        Configured SubHarmonicSuppressionPass
    """
    if aggressive:
        return SubHarmonicSuppressionPass(
            pass_number=pass_number,
            timing_tolerance_ms=75.0,
            min_suppression_probability=0.5,
            check_single_octave=True,
            check_double_octave=True,
            apply_pitch_floor=True,
        )
    else:
        return SubHarmonicSuppressionPass(
            pass_number=pass_number,
            timing_tolerance_ms=50.0,
            min_suppression_probability=0.6,
            check_single_octave=True,
            check_double_octave=True,
            apply_pitch_floor=True,
        )
