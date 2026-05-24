"""Harmonic duplicate filter.

Detects and suppresses notes that are harmonic artifacts -
notes detected at harmonic intervals (5th, 3rd, etc.) of
real notes due to strong overtone content.

Key insight: a note at the 5th or 3rd above another note,
with weaker spectral support, is likely a harmonic artifact.
"""
from __future__ import annotations

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


# Harmonic intervals in semitones with their relative strength
HARMONIC_INTERVALS = {
    12: 1.0,   # Octave (2nd harmonic)
    7: 0.8,   # Perfect 5th (3rd harmonic)
    19: 0.7,  # Octave + 5th (3rd harmonic)
    24: 0.6,  # 2 octaves (4th harmonic)
    4: 0.5,   # Major 3rd (5th harmonic)
    16: 0.5,  # Octave + major 3rd (5th harmonic)
}


class HarmonicDuplicateFilter(PrecisionFilter):
    """Filter that removes harmonic duplicate artifacts.

    Harmonic duplicates occur when overtones in the audio are
    strong enough to trigger note detection. Common cases:
    - Perfect 5th above fundamental (3rd harmonic)
    - Major 3rd above fundamental (5th harmonic)
    - Compound intervals (octave + interval)

    This filter:
    1. For each note, checks if there's a lower note at a harmonic interval
    2. Compares confidence/spectral support
    3. Suppresses the higher note if it appears to be a harmonic
    """

    def __init__(
        self,
        min_suppression_confidence: float = 0.7,
        protection_weight: float = 1.5,
        time_tolerance_ms: float = 30.0,
        confidence_ratio_threshold: float = 1.5,
    ):
        """Initialize filter.

        Args:
            min_suppression_confidence: Minimum confidence to suppress
            protection_weight: Weight for protection vs suppression
            time_tolerance_ms: Time tolerance for simultaneous notes
            confidence_ratio_threshold: Ratio to consider harmonic artifact
        """
        super().__init__(min_suppression_confidence, protection_weight)
        self.time_tolerance_ms = time_tolerance_ms
        self.confidence_ratio_threshold = confidence_ratio_threshold

    @property
    def name(self) -> str:
        return "harmonic_duplicate"

    def score_notes(
        self,
        notes: List[ExtractedNote],
        context: FilterContext,
    ) -> List[NoteScore]:
        """Score notes for harmonic duplicate artifacts."""
        scores = {id(n): NoteScore(note=n) for n in notes}

        # Find harmonic relationships
        harmonic_pairs = self._find_harmonic_pairs(notes)

        for fundamental, harmonic, interval, conf_ratio in harmonic_pairs:
            score = scores[id(harmonic)]

            # Score based on interval strength and confidence ratio
            interval_weight = HARMONIC_INTERVALS.get(interval, 0.3)
            suppression = min(1.0, (conf_ratio / self.confidence_ratio_threshold) * interval_weight)

            if suppression > score.suppression_score:
                score.suppression_score = suppression
                score.suppression_reasons.append(SuppressionReason.HARMONIC_DUPLICATE)
                score.harmonic_support = 1.0 / conf_ratio if conf_ratio > 0 else 0

        # Apply protection rules
        for note in notes:
            score = scores[id(note)]

            # Protect notes with strong rhythmic alignment
            if context.tempo:
                rhythmic = self._compute_rhythmic_alignment(
                    note, context.tempo, context.time_signature
                )
                if rhythmic > 0.7:
                    score.protection_score = max(score.protection_score, rhythmic * 0.6)
                    score.protection_reasons.append(ProtectionReason.RHYTHMIC_ALIGNMENT)

            # Protect notes in key
            if context.key:
                key_fit = self._compute_key_conformity(note, context.key)
                if key_fit > 0.8:
                    score.protection_score = max(score.protection_score, key_fit * 0.4)
                    score.protection_reasons.append(ProtectionReason.KEY_CONFORMITY)

            # Check for repeated patterns
            if context.tempo:
                pattern = self._find_repeated_patterns(notes, note, context.tempo)
                if pattern > 0.6:
                    score.protection_score = max(score.protection_score, pattern * 0.5)
                    score.protection_reasons.append(ProtectionReason.REPEATED_PATTERN)

            # High confidence notes get protection
            if note.confidence > 0.75:
                score.protection_score = max(score.protection_score, note.confidence * 0.3)
                score.protection_reasons.append(ProtectionReason.HIGH_CONFIDENCE)

        return list(scores.values())

    def _find_harmonic_pairs(
        self,
        notes: List[ExtractedNote],
    ) -> List[Tuple[ExtractedNote, ExtractedNote, int, float]]:
        """Find pairs of notes with harmonic relationships.

        Returns list of (fundamental, harmonic, interval_semitones, confidence_ratio).
        """
        pairs = []
        time_tolerance = self.time_tolerance_ms / 1000.0

        # Sort by pitch (ascending)
        sorted_notes = sorted(notes, key=lambda n: n.pitch)

        for i, lower in enumerate(sorted_notes):
            for higher in sorted_notes[i + 1:]:
                # Check timing overlap
                time_overlap = min(lower.end, higher.end) - max(lower.start, higher.start)
                if time_overlap < -time_tolerance:
                    continue

                # Check if at harmonic interval
                interval = higher.pitch - lower.pitch
                if interval not in HARMONIC_INTERVALS:
                    continue

                # Check if they start together (harmonic artifacts do)
                if abs(higher.start - lower.start) > time_tolerance:
                    continue

                # Compute confidence ratio
                if higher.confidence > 0:
                    conf_ratio = lower.confidence / higher.confidence
                    if conf_ratio > 1.0:
                        pairs.append((lower, higher, interval, conf_ratio))

        return pairs

    def _check_independent_melodic_line(
        self,
        note: ExtractedNote,
        potential_fundamental: ExtractedNote,
        all_notes: List[ExtractedNote],
    ) -> bool:
        """Check if a note appears to be part of an independent melodic line.

        If the 'harmonic' note has melodic continuity that the fundamental
        doesn't share, it's likely a real note, not a harmonic.
        """
        # Find notes immediately before and after
        tolerance = 0.2  # 200ms

        preceding = [
            n for n in all_notes
            if n.end <= note.start + 0.01 and note.start - n.end < tolerance
            and abs(n.pitch - note.pitch) <= 4  # Within a major third
        ]

        following = [
            n for n in all_notes
            if n.start >= note.end - 0.01 and n.start - note.end < tolerance
            and abs(n.pitch - note.pitch) <= 4
        ]

        # If there's melodic context, protect the note
        return bool(preceding or following)
