"""Sustain overlap cleanup filter.

Cleans up notes that appear during sustained note tails, which are
often artifacts from resonance or model confusion.

Key insight: notes detected during the decay of a sustained note
at similar pitch are likely artifacts of the sustain, not new notes.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

import numpy as np

from tone_forge.midi.passes.base import ExtractedNote
from .base import (
    FilterContext,
    NoteScore,
    PrecisionFilter,
    ProtectionReason,
    SuppressionReason,
)


class SustainOverlapCleanup(PrecisionFilter):
    """Filter that removes sustain overlap artifacts.

    Sustain overlap artifacts occur when:
    1. A note is held while model detects "new" note at same/similar pitch
    2. Resonance from sustained note triggers detection
    3. Release tails create phantom notes

    This filter:
    1. Identifies sustained notes (long duration)
    2. Finds notes that start during sustain
    3. Checks if they're likely artifacts or real notes
    """

    def __init__(
        self,
        min_suppression_confidence: float = 0.7,
        protection_weight: float = 1.5,
        sustain_threshold_ms: float = 500.0,
        overlap_pitch_tolerance: int = 2,
        min_overlap_ratio: float = 0.3,
    ):
        """Initialize filter.

        Args:
            min_suppression_confidence: Minimum confidence to suppress
            protection_weight: Weight for protection vs suppression
            sustain_threshold_ms: Duration to consider a note "sustained"
            overlap_pitch_tolerance: Pitch range for overlap detection
            min_overlap_ratio: Minimum overlap ratio to consider artifact
        """
        super().__init__(min_suppression_confidence, protection_weight)
        self.sustain_threshold_ms = sustain_threshold_ms
        self.overlap_pitch_tolerance = overlap_pitch_tolerance
        self.min_overlap_ratio = min_overlap_ratio

    @property
    def name(self) -> str:
        return "sustain_overlap"

    def score_notes(
        self,
        notes: List[ExtractedNote],
        context: FilterContext,
    ) -> List[NoteScore]:
        """Score notes for sustain overlap artifacts."""
        scores = {id(n): NoteScore(note=n) for n in notes}

        # Find sustained notes
        sustain_threshold = self.sustain_threshold_ms / 1000.0
        sustained_notes = [n for n in notes if n.duration >= sustain_threshold]

        # Find overlap relationships
        overlap_pairs = self._find_overlap_artifacts(notes, sustained_notes)

        for sustained, overlapping, overlap_ratio, confidence_ratio in overlap_pairs:
            score = scores[id(overlapping)]

            # Score based on overlap and confidence
            suppression = min(1.0, overlap_ratio * confidence_ratio)

            if suppression > score.suppression_score:
                score.suppression_score = suppression
                score.suppression_reasons.append(SuppressionReason.SUSTAIN_OVERLAP)

        # Apply protection rules
        for note in notes:
            score = scores[id(note)]

            # Rhythmic alignment protects
            if context.tempo:
                rhythmic = self._compute_rhythmic_alignment(
                    note, context.tempo, context.time_signature
                )
                if rhythmic > 0.7:
                    score.protection_score = max(score.protection_score, rhythmic * 0.5)
                    score.protection_reasons.append(ProtectionReason.RHYTHMIC_ALIGNMENT)

            # Key conformity protects
            if context.key:
                key_fit = self._compute_key_conformity(note, context.key)
                if key_fit > 0.8:
                    score.protection_score = max(score.protection_score, key_fit * 0.3)
                    score.protection_reasons.append(ProtectionReason.KEY_CONFORMITY)

            # Strong onset protects (indicates real note attack)
            # Use confidence as proxy for onset strength
            if note.confidence > 0.8:
                score.protection_score = max(score.protection_score, note.confidence * 0.5)
                score.protection_reasons.append(ProtectionReason.HIGH_CONFIDENCE)

            # Notes that form melodic motion are likely real
            if self._has_melodic_continuation(note, notes):
                score.protection_score = max(score.protection_score, 0.4)
                score.protection_reasons.append(ProtectionReason.MELODIC_CONTINUITY)

        return list(scores.values())

    def _find_overlap_artifacts(
        self,
        all_notes: List[ExtractedNote],
        sustained_notes: List[ExtractedNote],
    ) -> List[Tuple[ExtractedNote, ExtractedNote, float, float]]:
        """Find notes that appear during sustains.

        Returns list of (sustained_note, overlapping_note, overlap_ratio, confidence_ratio).
        """
        artifacts = []

        for sustained in sustained_notes:
            # Find notes that start during this sustain
            for other in all_notes:
                if other == sustained:
                    continue

                # Check pitch proximity
                pitch_diff = abs(other.pitch - sustained.pitch)
                if pitch_diff > self.overlap_pitch_tolerance:
                    continue

                # Check if other starts during sustained note
                if other.start < sustained.start or other.start >= sustained.end:
                    continue

                # Compute overlap ratio
                overlap_duration = min(other.end, sustained.end) - other.start
                other_duration = other.duration
                overlap_ratio = overlap_duration / other_duration if other_duration > 0 else 0

                if overlap_ratio < self.min_overlap_ratio:
                    continue

                # Compute confidence ratio
                # Lower confidence + high overlap = likely artifact
                if other.confidence > 0:
                    confidence_ratio = sustained.confidence / other.confidence
                else:
                    confidence_ratio = 2.0

                # Only flag if sustained note is more confident
                if confidence_ratio > 1.0:
                    artifacts.append((sustained, other, overlap_ratio, confidence_ratio))

        return artifacts

    def _has_melodic_continuation(
        self,
        note: ExtractedNote,
        all_notes: List[ExtractedNote],
    ) -> bool:
        """Check if note leads to or from another note melodically.

        Melodic continuation suggests the note is intentional, not an artifact.
        """
        tolerance = 0.1  # 100ms gap
        max_pitch_jump = 5  # P4 or less

        # Check for note that starts when this one ends
        for other in all_notes:
            if other == note:
                continue

            # Following note
            if abs(other.start - note.end) < tolerance:
                if abs(other.pitch - note.pitch) <= max_pitch_jump and other.pitch != note.pitch:
                    return True

            # Preceding note
            if abs(note.start - other.end) < tolerance:
                if abs(note.pitch - other.pitch) <= max_pitch_jump and note.pitch != other.pitch:
                    return True

        return False
