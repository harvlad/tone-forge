"""Repeated pattern validator.

Validates notes by checking if they form part of a repeated musical pattern.
Notes that are part of consistent patterns are protected; isolated notes
without pattern support are more suspect.

Key insight: musical notes often form patterns (arpeggios, ostinatos,
melodic motifs). Artifacts tend to be isolated or inconsistent.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Set, Tuple

import numpy as np

from tone_forge.midi.passes.base import ExtractedNote
from .base import (
    FilterContext,
    NoteScore,
    PrecisionFilter,
    ProtectionReason,
    SuppressionReason,
)


class RepeatedPatternValidator(PrecisionFilter):
    """Filter that validates notes based on pattern membership.

    Musical patterns include:
    1. Repeated notes at regular intervals (ostinato, bass)
    2. Arpeggiated chord tones
    3. Melodic motif repetition
    4. Rhythmic patterns

    This filter:
    1. Detects repeated pitch patterns
    2. Detects rhythmic consistency
    3. Protects notes that fit patterns
    4. Flags isolated notes as suspect
    """

    def __init__(
        self,
        min_suppression_confidence: float = 0.6,
        protection_weight: float = 2.0,  # Strong protection for patterns
        min_pattern_occurrences: int = 2,
        timing_tolerance_ms: float = 50.0,
    ):
        """Initialize filter.

        Args:
            min_suppression_confidence: Minimum confidence to suppress
            protection_weight: Weight for protection vs suppression
            min_pattern_occurrences: Minimum times a pattern must occur
            timing_tolerance_ms: Tolerance for pattern timing
        """
        super().__init__(min_suppression_confidence, protection_weight)
        self.min_pattern_occurrences = min_pattern_occurrences
        self.timing_tolerance_ms = timing_tolerance_ms

    @property
    def name(self) -> str:
        return "repeated_pattern"

    def score_notes(
        self,
        notes: List[ExtractedNote],
        context: FilterContext,
    ) -> List[NoteScore]:
        """Score notes based on pattern membership."""
        scores = {id(n): NoteScore(note=n) for n in notes}

        # Detect various pattern types
        pitch_patterns = self._detect_pitch_patterns(notes, context)
        interval_patterns = self._detect_interval_patterns(notes, context)
        rhythmic_patterns = self._detect_rhythmic_patterns(notes, context)

        # Score notes based on pattern membership
        for note in notes:
            note_id = id(note)
            score = scores[note_id]

            # Check pitch pattern membership (use note id as key)
            if note_id in pitch_patterns:
                pattern_strength = pitch_patterns[note_id]
                score.protection_score = max(score.protection_score, pattern_strength * 0.7)
                score.protection_reasons.append(ProtectionReason.REPEATED_PATTERN)

            # Check interval pattern membership
            if note_id in interval_patterns:
                pattern_strength = interval_patterns[note_id]
                score.protection_score = max(score.protection_score, pattern_strength * 0.5)
                if ProtectionReason.REPEATED_PATTERN not in score.protection_reasons:
                    score.protection_reasons.append(ProtectionReason.REPEATED_PATTERN)

            # Check rhythmic consistency
            if note_id in rhythmic_patterns:
                rhythm_strength = rhythmic_patterns[note_id]
                score.protection_score = max(score.protection_score, rhythm_strength * 0.4)
                score.protection_reasons.append(ProtectionReason.RHYTHMIC_ALIGNMENT)

            # Notes not in any pattern get mild suppression
            if (note_id not in pitch_patterns and
                note_id not in interval_patterns and
                note_id not in rhythmic_patterns):
                # Only if isolated (not in melodic context)
                if not self._has_melodic_context(note, notes):
                    score.suppression_score = max(score.suppression_score, 0.3)
                    score.suppression_reasons.append(SuppressionReason.PATTERN_INCONSISTENT)

            # Apply standard protection rules
            if context.key:
                key_fit = self._compute_key_conformity(note, context.key)
                if key_fit > 0.8:
                    score.protection_score = max(score.protection_score, key_fit * 0.3)
                    score.protection_reasons.append(ProtectionReason.KEY_CONFORMITY)

            # High confidence
            if note.confidence > 0.75:
                score.protection_score = max(score.protection_score, note.confidence * 0.3)
                score.protection_reasons.append(ProtectionReason.HIGH_CONFIDENCE)

        return list(scores.values())

    def _detect_pitch_patterns(
        self,
        notes: List[ExtractedNote],
        context: FilterContext,
    ) -> Dict[int, float]:
        """Detect repeated pitch patterns.

        Returns dict of note id -> pattern strength (0-1).
        Uses id(note) as key since ExtractedNote is unhashable.
        """
        pattern_notes: Dict[int, float] = {}

        if not context.tempo or context.tempo <= 0:
            return pattern_notes

        beat_duration = 60.0 / context.tempo
        tolerance = self.timing_tolerance_ms / 1000.0

        # Group notes by pitch
        pitch_groups: Dict[int, List[ExtractedNote]] = defaultdict(list)
        for note in notes:
            pitch_groups[note.pitch].append(note)

        # For each pitch group, find repeated timing patterns
        for pitch, pitch_notes in pitch_groups.items():
            if len(pitch_notes) < self.min_pattern_occurrences:
                continue

            # Sort by start time
            sorted_notes = sorted(pitch_notes, key=lambda n: n.start)

            # Compute intervals between consecutive notes
            intervals = []
            for i in range(1, len(sorted_notes)):
                interval = sorted_notes[i].start - sorted_notes[i - 1].start
                intervals.append(interval)

            if not intervals:
                continue

            # Find the most common interval (quantized to beat divisions)
            beat_divisions = [0.25, 0.5, 1.0, 2.0, 4.0]
            best_match = None
            best_count = 0

            for div in beat_divisions:
                expected = beat_duration * div
                count = sum(1 for i in intervals if abs(i - expected) < tolerance)
                if count > best_count:
                    best_count = count
                    best_match = expected

            # If consistent pattern found, mark all notes
            if best_count >= self.min_pattern_occurrences - 1:
                pattern_strength = best_count / len(intervals)
                for note in sorted_notes:
                    note_id = id(note)
                    pattern_notes[note_id] = max(
                        pattern_notes.get(note_id, 0),
                        pattern_strength
                    )

        return pattern_notes

    def _detect_interval_patterns(
        self,
        notes: List[ExtractedNote],
        context: FilterContext,
    ) -> Dict[int, float]:
        """Detect repeated melodic interval patterns.

        Returns dict of note id -> pattern strength.
        """
        pattern_notes: Dict[int, float] = {}

        if len(notes) < 4:
            return pattern_notes

        # Sort by time
        sorted_notes = sorted(notes, key=lambda n: n.start)

        # Compute pitch intervals
        intervals = []
        for i in range(1, len(sorted_notes)):
            if sorted_notes[i].start - sorted_notes[i - 1].end < 0.5:  # Adjacent notes
                interval = sorted_notes[i].pitch - sorted_notes[i - 1].pitch
                intervals.append((i - 1, i, interval))

        # Find repeated interval sequences (motifs)
        if len(intervals) >= 4:
            # Look for 2-3 note motifs that repeat
            for motif_len in [2, 3]:
                for start_idx in range(len(intervals) - motif_len + 1):
                    motif = tuple(intervals[start_idx + j][2] for j in range(motif_len))

                    # Count occurrences
                    occurrences = []
                    for check_idx in range(len(intervals) - motif_len + 1):
                        check_motif = tuple(intervals[check_idx + j][2] for j in range(motif_len))
                        if motif == check_motif:
                            occurrences.append(check_idx)

                    if len(occurrences) >= self.min_pattern_occurrences:
                        pattern_strength = min(1.0, len(occurrences) / 3)
                        for occ_idx in occurrences:
                            for j in range(motif_len + 1):
                                if occ_idx + j < len(sorted_notes):
                                    note = sorted_notes[intervals[occ_idx][0] + j] if j == 0 else sorted_notes[intervals[occ_idx + j - 1][1]]
                                    note_id = id(note)
                                    pattern_notes[note_id] = max(
                                        pattern_notes.get(note_id, 0),
                                        pattern_strength
                                    )

        return pattern_notes

    def _detect_rhythmic_patterns(
        self,
        notes: List[ExtractedNote],
        context: FilterContext,
    ) -> Dict[int, float]:
        """Detect notes on consistent rhythmic grid.

        Returns dict of note id -> rhythmic consistency (0-1).
        """
        pattern_notes: Dict[int, float] = {}

        if not context.tempo or context.tempo <= 0:
            return pattern_notes

        beat_duration = 60.0 / context.tempo

        for note in notes:
            alignment = self._compute_rhythmic_alignment(
                note, context.tempo, context.time_signature
            )
            if alignment > 0.7:
                pattern_notes[id(note)] = alignment

        return pattern_notes

    def _has_melodic_context(
        self,
        note: ExtractedNote,
        all_notes: List[ExtractedNote],
    ) -> bool:
        """Check if note has melodic neighbors."""
        tolerance = 0.3  # 300ms
        pitch_range = 7  # 5th

        for other in all_notes:
            if other == note:
                continue

            time_gap = min(abs(note.start - other.end), abs(other.start - note.end))
            pitch_gap = abs(note.pitch - other.pitch)

            if time_gap < tolerance and pitch_gap <= pitch_range:
                return True

        return False
