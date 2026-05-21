"""Pass 5: Genre-aware refinement.

Applies archetype priors to refine notes based on genre-specific
expectations for velocity, density, sustain, and timing.
"""
from __future__ import annotations

import logging
import time
from dataclasses import replace
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .base import (
    ExtractionContext,
    ExtractionPass,
    ExtractedNote,
    NoteFlag,
    PassResult,
)

logger = logging.getLogger(__name__)


# Try to import archetypes
try:
    from ...archetypes import (
        get_archetype,
        get_extraction_priors,
        ProductionArchetype,
        ExtractionPriors,
    )
    ARCHETYPES_AVAILABLE = True
except ImportError:
    ARCHETYPES_AVAILABLE = False
    ProductionArchetype = Any
    ExtractionPriors = Any


class GenreRefinementPass(ExtractionPass):
    """Refine notes using genre-specific priors.

    This pass uses production archetype information to:
    1. Adjust velocity ranges to match genre expectations
    2. Filter notes outside expected density ranges
    3. Adjust sustain characteristics
    4. Apply genre-specific quantization hints
    5. Flag notes that deviate from genre norms
    """

    def __init__(
        self,
        pass_number: int = 5,
        apply_velocity_adjustment: bool = True,
        apply_density_filtering: bool = True,
        apply_sustain_adjustment: bool = True,
        apply_pitch_validation: bool = True,
        strict_mode: bool = False,
    ):
        """Initialize genre refinement pass.

        Args:
            pass_number: Pass number in pipeline
            apply_velocity_adjustment: Adjust velocities to match genre
            apply_density_filtering: Filter based on density expectations
            apply_sustain_adjustment: Adjust note durations
            apply_pitch_validation: Validate pitch ranges
            strict_mode: Remove notes that don't fit genre (vs just flagging)
        """
        super().__init__(pass_number)
        self.apply_velocity_adjustment = apply_velocity_adjustment
        self.apply_density_filtering = apply_density_filtering
        self.apply_sustain_adjustment = apply_sustain_adjustment
        self.apply_pitch_validation = apply_pitch_validation
        self.strict_mode = strict_mode

    @property
    def name(self) -> str:
        return "genre_refinement"

    def process(
        self,
        notes: List[ExtractedNote],
        context: ExtractionContext,
    ) -> PassResult:
        """Refine notes using genre priors.

        Args:
            notes: Input notes
            context: Extraction context

        Returns:
            PassResult with genre-refined notes
        """
        start_time = time.time()

        if not notes:
            return PassResult(
                notes=[],
                statistics=self._create_statistics(notes, [], 0.0),
                warnings=["No notes to refine"],
            )

        # Get archetype and priors
        archetype = None
        priors = None

        if ARCHETYPES_AVAILABLE and context.genre:
            archetype = get_archetype(context.genre)
            priors = get_extraction_priors(
                genre=context.genre,
                stem_type=context.stem_type,
            )

        if not archetype and not priors:
            # No genre info - return notes unchanged
            return PassResult(
                notes=notes,
                statistics=self._create_statistics(notes, notes, 0.0),
                warnings=["No genre archetype available - skipping refinement"],
                metadata={"genre_available": False},
            )

        refined_notes = notes.copy()
        warnings = []

        # Step 1: Velocity adjustment
        if self.apply_velocity_adjustment and priors:
            refined_notes, vel_warnings = self._adjust_velocities(
                refined_notes, priors, context
            )
            warnings.extend(vel_warnings)

        # Step 2: Density filtering
        if self.apply_density_filtering and priors:
            refined_notes, density_warnings = self._filter_by_density(
                refined_notes, priors, context
            )
            warnings.extend(density_warnings)

        # Step 3: Sustain adjustment
        if self.apply_sustain_adjustment and priors:
            refined_notes, sustain_warnings = self._adjust_sustain(
                refined_notes, priors, context
            )
            warnings.extend(sustain_warnings)

        # Step 4: Pitch validation
        if self.apply_pitch_validation and priors:
            refined_notes, pitch_warnings = self._validate_pitch_range(
                refined_notes, priors, context
            )
            warnings.extend(pitch_warnings)

        # Step 5: Apply archetype-specific adjustments
        if archetype:
            refined_notes = self._apply_archetype_adjustments(
                refined_notes, archetype, context
            )

        execution_time = (time.time() - start_time) * 1000

        stats = self._create_statistics(
            notes,
            refined_notes,
            execution_time,
            archetype_name=archetype.name if archetype else None,
            genre=context.genre,
        )

        return PassResult(
            notes=refined_notes,
            statistics=stats,
            warnings=warnings,
            metadata={
                "genre_available": True,
                "archetype_name": archetype.name if archetype else None,
                "priors_source": priors.source_archetype if priors else None,
            },
        )

    def _adjust_velocities(
        self,
        notes: List[ExtractedNote],
        priors: ExtractionPriors,
        context: ExtractionContext,
    ) -> Tuple[List[ExtractedNote], List[str]]:
        """Adjust note velocities to match genre expectations."""
        warnings = []
        adjusted = []

        min_vel, max_vel = priors.expected_velocity_range
        expected_mean = priors.expected_velocity_mean

        # Calculate current distribution
        if notes:
            current_velocities = [n.velocity for n in notes]
            current_mean = np.mean(current_velocities)
            current_std = np.std(current_velocities)
        else:
            return notes, warnings

        # Check if adjustment is needed
        if abs(current_mean - expected_mean) > 20:
            # Significant deviation from expected
            shift = expected_mean - current_mean

            for note in notes:
                new_velocity = int(np.clip(note.velocity + shift * 0.5, min_vel, max_vel))

                if new_velocity != note.velocity:
                    note = replace(note, velocity=new_velocity)
                    note.flags.add(NoteFlag.VELOCITY_ADJUSTED)

                adjusted.append(note)

            warnings.append(
                f"Adjusted velocity distribution (mean {current_mean:.0f} -> ~{expected_mean:.0f})"
            )
        else:
            adjusted = notes

        return adjusted, warnings

    def _filter_by_density(
        self,
        notes: List[ExtractedNote],
        priors: ExtractionPriors,
        context: ExtractionContext,
    ) -> Tuple[List[ExtractedNote], List[str]]:
        """Filter notes based on density expectations."""
        warnings = []

        if len(notes) < 2:
            return notes, warnings

        # Calculate current density
        time_range = max(n.end for n in notes) - min(n.start for n in notes)
        if time_range <= 0:
            return notes, warnings

        current_density = len(notes) / time_range
        min_density, max_density = priors.expected_note_density

        # Check if density is way too high
        if current_density > max_density * 2:
            # Too many notes - likely false positives
            # Keep only higher confidence notes
            sorted_by_conf = sorted(notes, key=lambda n: -n.confidence)

            # Target density in the middle of expected range
            target_density = (min_density + max_density) / 2
            target_count = int(target_density * time_range)
            target_count = max(target_count, len(notes) // 2)  # Keep at least half

            if self.strict_mode:
                filtered = sorted_by_conf[:target_count]
                warnings.append(
                    f"Density {current_density:.1f}/s exceeds expected max {max_density:.1f}/s - "
                    f"removed {len(notes) - len(filtered)} low-confidence notes"
                )
                return filtered, warnings
            else:
                # Just flag low-confidence notes
                for i, note in enumerate(sorted_by_conf):
                    if i >= target_count:
                        note.flags.add(NoteFlag.LOW_CONFIDENCE)
                warnings.append(
                    f"Density {current_density:.1f}/s exceeds expected max {max_density:.1f}/s"
                )

        # Check if density is too low
        elif current_density < min_density * 0.5:
            warnings.append(
                f"Density {current_density:.1f}/s below expected min {min_density:.1f}/s - "
                "may be missing notes"
            )

        return notes, warnings

    def _adjust_sustain(
        self,
        notes: List[ExtractedNote],
        priors: ExtractionPriors,
        context: ExtractionContext,
    ) -> Tuple[List[ExtractedNote], List[str]]:
        """Adjust note durations based on sustain expectations."""
        warnings = []
        adjusted = []

        expected_sustain = priors.expected_sustain_ratio
        min_note_ms = priors.min_note_ms
        max_note_ms = priors.max_note_ms

        for note in notes:
            duration_ms = note.duration_ms

            # Check minimum duration
            if duration_ms < min_note_ms:
                if self.strict_mode:
                    # Skip very short notes
                    continue
                else:
                    note.flags.add(NoteFlag.LOW_CONFIDENCE)

            # Check maximum duration
            if duration_ms > max_note_ms:
                # Truncate overly long notes
                new_end = note.start + (max_note_ms / 1000)
                note = replace(note, end=new_end, original_end=note.end)
                note.flags.add(NoteFlag.MERGED)  # Using merged flag for modified

            adjusted.append(note)

        if len(adjusted) < len(notes):
            warnings.append(
                f"Removed {len(notes) - len(adjusted)} notes shorter than {min_note_ms:.0f}ms"
            )

        return adjusted, warnings

    def _validate_pitch_range(
        self,
        notes: List[ExtractedNote],
        priors: ExtractionPriors,
        context: ExtractionContext,
    ) -> Tuple[List[ExtractedNote], List[str]]:
        """Validate notes are within expected pitch range."""
        warnings = []
        validated = []

        min_pitch, max_pitch = priors.expected_pitch_range
        pitch_confidence = priors.pitch_confidence

        out_of_range_count = 0

        for note in notes:
            if note.pitch < min_pitch or note.pitch > max_pitch:
                out_of_range_count += 1

                if self.strict_mode and pitch_confidence > 0.7:
                    # High confidence in pitch expectations - skip note
                    continue
                else:
                    # Just flag it
                    note.flags.add(NoteFlag.LOW_CONFIDENCE)

            validated.append(note)

        if out_of_range_count > 0:
            warnings.append(
                f"{out_of_range_count} notes outside expected pitch range "
                f"(MIDI {min_pitch}-{max_pitch})"
            )

        return validated, warnings

    def _apply_archetype_adjustments(
        self,
        notes: List[ExtractedNote],
        archetype: ProductionArchetype,
        context: ExtractionContext,
    ) -> List[ExtractedNote]:
        """Apply archetype-specific adjustments."""
        adjusted = []

        # Get extraction parameters for this stem type
        params = archetype.get_extraction_params(context.stem_type)

        for note in notes:
            # Apply ghost note sensitivity
            if params.ghost_note_sensitivity < 0.5:
                # Less sensitive to ghost notes - remove low velocity notes
                if note.velocity < 40 and note.confidence < 0.5:
                    continue

            # Store archetype info in context
            ctx = note.harmonic_context or {}
            ctx["archetype"] = archetype.name
            ctx["archetype_params"] = {
                "quantization_strength": params.quantization_strength,
                "swing_amount": params.swing_amount,
            }
            note = replace(note, harmonic_context=ctx)

            adjusted.append(note)

        return adjusted
