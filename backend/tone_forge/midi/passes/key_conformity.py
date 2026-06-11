"""Key conformity pass.

Validates notes against the detected key and adjusts confidence
for out-of-key notes. Uses probabilistic scoring to preserve
intentional chromaticism while filtering extraction artifacts
that happen to land on wrong pitches.

Factors considered:
- Diatonic vs chromatic pitch classes
- Common chromatic alterations (leading tones, borrowed chords)
- Melodic context (passing tones, neighbor tones)
- Genre-specific chromaticism expectations
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


# Common chromatic alterations that are musically valid
COMMON_CHROMATIC = {
    "major": {
        # Leading tone to vi (raised 5)
        7: 0.8,
        # Flat 7 (mixolydian borrowing)
        10: 0.7,
        # Flat 3 (minor borrowing)
        3: 0.6,
        # Flat 6 (minor borrowing)
        8: 0.6,
        # Sharp 4 (lydian)
        6: 0.5,
    },
    "minor": {
        # Raised 7 (harmonic minor)
        11: 0.9,
        # Raised 6 (melodic minor ascending)
        9: 0.7,
        # Natural 7 (natural minor / dorian)
        10: 0.8,
        # Flat 2 (phrygian)
        1: 0.5,
    },
}


class KeyConformityPass(ExtractionPass):
    """Validate notes against detected key.

    This pass scores notes based on how well they conform to the
    detected key. Out-of-key notes with low confidence are filtered
    or have their confidence reduced.

    The pass is context-aware:
    - Common chromatic alterations are tolerated
    - Passing tones and neighbor tones are preserved
    - Genre affects chromaticism expectations
    - High-confidence notes are preserved regardless
    """

    def __init__(
        self,
        pass_number: int = 0,
        strictness: float = 0.5,
        min_confidence_threshold: float = 0.3,
        allow_common_chromatic: bool = True,
        allow_passing_tones: bool = True,
        allow_neighbor_tones: bool = True,
        confidence_override_threshold: float = 0.8,
    ):
        """Initialize key conformity pass.

        Args:
            pass_number: Pass number in pipeline
            strictness: How strictly to enforce key (0-1)
            min_confidence_threshold: Notes below this are filtered if out-of-key
            allow_common_chromatic: Allow common chromatic alterations
            allow_passing_tones: Allow chromatic passing tones
            allow_neighbor_tones: Allow chromatic neighbor tones
            confidence_override_threshold: Confidence above which key is ignored
        """
        super().__init__(pass_number)
        self.strictness = strictness
        self.min_confidence_threshold = min_confidence_threshold
        self.allow_common_chromatic = allow_common_chromatic
        self.allow_passing_tones = allow_passing_tones
        self.allow_neighbor_tones = allow_neighbor_tones
        self.confidence_override_threshold = confidence_override_threshold

    @property
    def name(self) -> str:
        return "key_conformity"

    def process(
        self,
        notes: List[ExtractedNote],
        context: ExtractionContext,
    ) -> PassResult:
        """Validate notes against detected key.

        Args:
            notes: Input notes
            context: Extraction context

        Returns:
            PassResult with key-validated notes
        """
        start_time = time.time()
        input_notes = notes.copy()

        if len(notes) == 0:
            return PassResult(
                notes=[],
                statistics=self._create_statistics(input_notes, [], 0.0),
            )

        # Get key information
        if context.key is None:
            # No key detected - skip filtering
            return PassResult(
                notes=notes,
                statistics=self._create_statistics(input_notes, notes, 0.0),
                warnings=["No key detected - skipping key conformity check"],
            )

        key_root, key_mode = context.key
        diatonic_pitches = self._get_diatonic_pitches(key_root, key_mode)
        chromatic_weights = self._get_chromatic_weights(key_mode)

        # Sort notes by time for melodic context analysis
        sorted_indices = sorted(range(len(notes)), key=lambda i: notes[i].start)

        # Score each note for key conformity
        conformity_scores: Dict[int, Tuple[float, str]] = {}

        for i, note in enumerate(notes):
            # High confidence notes bypass key filtering
            if note.confidence >= self.confidence_override_threshold:
                continue

            pitch_class = note.pitch % 12
            relative_pitch = (pitch_class - key_root) % 12

            if relative_pitch in diatonic_pitches:
                # Diatonic - full conformity
                continue

            # Chromatic note - compute conformity score
            score, reason = self._compute_conformity_score(
                note, notes, i, sorted_indices,
                key_root, diatonic_pitches, chromatic_weights
            )

            if score < 1.0:
                conformity_scores[i] = (score, reason)

        # Apply filtering based on scores and strictness
        output_notes = []
        filtered_count = 0
        confidence_adjusted_count = 0

        for i, note in enumerate(notes):
            if i not in conformity_scores:
                # Diatonic or high confidence - keep
                output_notes.append(note)
                continue

            score, reason = conformity_scores[i]

            # Combine score with strictness
            effective_score = score * (1 - self.strictness) + (1 - self.strictness)

            if effective_score < 0.3 and note.confidence < self.min_confidence_threshold:
                # Low conformity + low confidence - filter
                filtered_count += 1
                continue
            elif effective_score < 0.6:
                # Moderate conformity - reduce confidence
                provenance = note.provenance or NoteProvenance()
                provenance = replace(
                    provenance,
                    cleanup_passes=provenance.cleanup_passes + [self.name],
                    suppression_reasons=provenance.suppression_reasons + [
                        f"out_of_key_{reason}"
                    ],
                )

                penalty = (0.6 - effective_score) * self.strictness
                new_confidence = note.confidence * (1 - penalty)
                provenance = replace(provenance, final_confidence=new_confidence)

                modified_note = replace(
                    note,
                    confidence=new_confidence,
                    flags=note.flags | {NoteFlag.LOW_CONFIDENCE},
                    provenance=provenance,
                )
                output_notes.append(modified_note)
                confidence_adjusted_count += 1
            else:
                output_notes.append(note)

        execution_time = (time.time() - start_time) * 1000

        stats = self._create_statistics(
            input_notes,
            output_notes,
            execution_time,
            notes_filtered=filtered_count,
            confidence_adjusted=confidence_adjusted_count,
            key=f"{self._pitch_name(key_root)} {key_mode}",
        )

        warnings = []
        if filtered_count > len(input_notes) * 0.15:
            warnings.append(
                f"Filtered {filtered_count}/{len(input_notes)} out-of-key notes - "
                "verify key detection is correct"
            )

        return PassResult(
            notes=output_notes,
            statistics=stats,
            warnings=warnings,
            metadata={
                "key_root": key_root,
                "key_mode": key_mode,
                "strictness": self.strictness,
            },
        )

    def _get_diatonic_pitches(self, root: int, mode: str) -> Set[int]:
        """Get diatonic pitch classes relative to root."""
        if mode == "major":
            intervals = {0, 2, 4, 5, 7, 9, 11}
        elif mode == "minor":
            intervals = {0, 2, 3, 5, 7, 8, 10}
        else:
            # Unknown mode - allow all
            return set(range(12))
        return intervals

    def _get_chromatic_weights(self, mode: str) -> Dict[int, float]:
        """Get weights for common chromatic alterations."""
        if self.allow_common_chromatic:
            return COMMON_CHROMATIC.get(mode, {})
        return {}

    def _compute_conformity_score(
        self,
        note: ExtractedNote,
        all_notes: List[ExtractedNote],
        note_idx: int,
        sorted_indices: List[int],
        key_root: int,
        diatonic_pitches: Set[int],
        chromatic_weights: Dict[int, float],
    ) -> Tuple[float, str]:
        """Compute conformity score for a chromatic note.

        Returns (score, reason) where score is 0-1 (1 = fully conforms).
        """
        pitch_class = note.pitch % 12
        relative_pitch = (pitch_class - key_root) % 12

        # Check common chromatic alterations
        if relative_pitch in chromatic_weights:
            return chromatic_weights[relative_pitch], "common_chromatic"

        # Check for passing tone
        if self.allow_passing_tones:
            is_passing, passing_score = self._check_passing_tone(
                note, all_notes, note_idx, sorted_indices, diatonic_pitches, key_root
            )
            if is_passing:
                return passing_score, "passing_tone"

        # Check for neighbor tone
        if self.allow_neighbor_tones:
            is_neighbor, neighbor_score = self._check_neighbor_tone(
                note, all_notes, note_idx, sorted_indices, diatonic_pitches, key_root
            )
            if is_neighbor:
                return neighbor_score, "neighbor_tone"

        # Pure chromatic - score based on context
        return 0.3, "chromatic"

    def _check_passing_tone(
        self,
        note: ExtractedNote,
        all_notes: List[ExtractedNote],
        note_idx: int,
        sorted_indices: List[int],
        diatonic_pitches: Set[int],
        key_root: int,
    ) -> Tuple[bool, float]:
        """Check if note is a passing tone between diatonic notes."""
        # Find position in sorted order
        try:
            pos = sorted_indices.index(note_idx)
        except ValueError:
            return False, 0.0

        # Need previous and next notes
        if pos == 0 or pos == len(sorted_indices) - 1:
            return False, 0.0

        prev_idx = sorted_indices[pos - 1]
        next_idx = sorted_indices[pos + 1]

        prev_note = all_notes[prev_idx]
        next_note = all_notes[next_idx]

        # Check for stepwise motion through this note
        prev_pc = (prev_note.pitch % 12 - key_root) % 12
        next_pc = (next_note.pitch % 12 - key_root) % 12

        if prev_pc in diatonic_pitches and next_pc in diatonic_pitches:
            # Previous and next are diatonic
            # Check stepwise
            if abs(note.pitch - prev_note.pitch) <= 2 and abs(note.pitch - next_note.pitch) <= 2:
                # Check direction is consistent
                if (prev_note.pitch < note.pitch < next_note.pitch or
                    prev_note.pitch > note.pitch > next_note.pitch):
                    return True, 0.75

        return False, 0.0

    def _check_neighbor_tone(
        self,
        note: ExtractedNote,
        all_notes: List[ExtractedNote],
        note_idx: int,
        sorted_indices: List[int],
        diatonic_pitches: Set[int],
        key_root: int,
    ) -> Tuple[bool, float]:
        """Check if note is a neighbor tone to a diatonic note."""
        try:
            pos = sorted_indices.index(note_idx)
        except ValueError:
            return False, 0.0

        # Need at least previous or next note
        has_prev = pos > 0
        has_next = pos < len(sorted_indices) - 1

        if not (has_prev or has_next):
            return False, 0.0

        # Check for neighbor pattern (note returns to same pitch)
        if has_prev and has_next:
            prev_idx = sorted_indices[pos - 1]
            next_idx = sorted_indices[pos + 1]

            prev_note = all_notes[prev_idx]
            next_note = all_notes[next_idx]

            # Same pitch before and after
            if prev_note.pitch == next_note.pitch:
                prev_pc = (prev_note.pitch % 12 - key_root) % 12
                if prev_pc in diatonic_pitches:
                    # Chromatic neighbor to diatonic note
                    interval = abs(note.pitch - prev_note.pitch)
                    if interval <= 2:
                        return True, 0.7

        return False, 0.0

    def _pitch_name(self, pitch_class: int) -> str:
        """Convert pitch class to name."""
        names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
        return names[pitch_class % 12]
