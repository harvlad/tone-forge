"""Subharmonic cleanup filter.

Detects and suppresses notes that are subharmonic artifacts -
notes detected at octaves BELOW the real fundamental due to
pitch detection artifacts or resonance.

Key insight: if a note at pitch P exists and a note at P-12 or P-24
has much weaker spectral support at its fundamental, it's likely
a subharmonic artifact.
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


class SubharmonicCleanupFilter(PrecisionFilter):
    """Filter that removes subharmonic artifacts.

    Subharmonic artifacts occur when:
    1. Pitch detection latches onto an octave below
    2. Room resonance adds low frequency content
    3. Non-linear processing creates subharmonics

    This filter is especially important for bass extraction where
    subharmonic detection is common.

    Key rule: a low note is suspect if a higher note exists with
    much stronger spectral support at its fundamental.
    """

    def __init__(
        self,
        min_suppression_confidence: float = 0.75,
        protection_weight: float = 2.0,  # Higher protection for low notes
        time_tolerance_ms: float = 50.0,
        spectral_ratio_threshold: float = 2.5,
        min_pitch: int = 28,  # E1 - below this is very suspicious
    ):
        """Initialize filter.

        Args:
            min_suppression_confidence: Minimum confidence to suppress
            protection_weight: Weight for protection vs suppression
            time_tolerance_ms: Time tolerance for simultaneous notes
            spectral_ratio_threshold: Ratio to consider subharmonic
            min_pitch: MIDI pitch below which notes are suspicious
        """
        super().__init__(min_suppression_confidence, protection_weight)
        self.time_tolerance_ms = time_tolerance_ms
        self.spectral_ratio_threshold = spectral_ratio_threshold
        self.min_pitch = min_pitch

    @property
    def name(self) -> str:
        return "subharmonic_cleanup"

    def score_notes(
        self,
        notes: List[ExtractedNote],
        context: FilterContext,
    ) -> List[NoteScore]:
        """Score notes for subharmonic artifacts."""
        scores = {id(n): NoteScore(note=n) for n in notes}

        # Find subharmonic relationships
        subharmonic_pairs = self._find_subharmonic_pairs(notes, context)

        for real_note, subharmonic, spectral_ratio in subharmonic_pairs:
            score = scores[id(subharmonic)]

            suppression = min(1.0, spectral_ratio / self.spectral_ratio_threshold)

            if suppression > score.suppression_score:
                score.suppression_score = suppression
                score.suppression_reasons.append(SuppressionReason.SUBHARMONIC_ARTIFACT)

        # Flag very low notes as suspicious even without pairs
        for note in notes:
            if note.pitch < self.min_pitch:
                score = scores[id(note)]
                # Add mild suppression for very low notes
                base_suppression = (self.min_pitch - note.pitch) / 12.0 * 0.3
                score.suppression_score = max(score.suppression_score, base_suppression)
                if SuppressionReason.SUBHARMONIC_ARTIFACT not in score.suppression_reasons:
                    score.suppression_reasons.append(SuppressionReason.SUBHARMONIC_ARTIFACT)

        # Apply protection rules
        for note in notes:
            score = scores[id(note)]

            # Strong rhythmic alignment protects
            if context.tempo:
                rhythmic = self._compute_rhythmic_alignment(
                    note, context.tempo, context.time_signature
                )
                if rhythmic > 0.8:
                    score.protection_score = max(score.protection_score, rhythmic * 0.6)
                    score.protection_reasons.append(ProtectionReason.RHYTHMIC_ALIGNMENT)

            # Key conformity protects
            if context.key:
                key_fit = self._compute_key_conformity(note, context.key)
                if key_fit > 0.8:
                    score.protection_score = max(score.protection_score, key_fit * 0.4)
                    score.protection_reasons.append(ProtectionReason.KEY_CONFORMITY)

            # Check for bass pattern (repeated root notes)
            if context.tempo and note.pitch < 48:  # Below C3
                pattern = self._find_repeated_patterns(notes, note, context.tempo)
                if pattern > 0.5:
                    # Bass patterns get strong protection
                    score.protection_score = max(score.protection_score, pattern * 0.7)
                    score.protection_reasons.append(ProtectionReason.REPEATED_PATTERN)

            # High confidence with spectral support
            if note.confidence > 0.8:
                score.protection_score = max(score.protection_score, note.confidence * 0.4)
                score.protection_reasons.append(ProtectionReason.HIGH_CONFIDENCE)

        return list(scores.values())

    def _find_subharmonic_pairs(
        self,
        notes: List[ExtractedNote],
        context: FilterContext,
    ) -> List[Tuple[ExtractedNote, ExtractedNote, float]]:
        """Find pairs where lower note is likely a subharmonic of higher.

        Returns list of (real_note, subharmonic_note, spectral_ratio).
        """
        pairs = []
        time_tolerance = self.time_tolerance_ms / 1000.0

        # Group by approximate start time
        time_groups: Dict[int, List[ExtractedNote]] = {}
        time_resolution = 0.05

        for note in notes:
            bucket = int(note.start / time_resolution)
            if bucket not in time_groups:
                time_groups[bucket] = []
            time_groups[bucket].append(note)

        for bucket, group in time_groups.items():
            # Include adjacent buckets
            candidates = list(group)
            for adj in [bucket - 1, bucket + 1]:
                if adj in time_groups:
                    candidates.extend(time_groups[adj])

            # Sort by pitch
            candidates.sort(key=lambda n: n.pitch)

            # Check for subharmonic relationships
            for i, lower in enumerate(candidates):
                for higher in candidates[i + 1:]:
                    # Must be octave related
                    pitch_diff = higher.pitch - lower.pitch
                    if pitch_diff not in [12, 24]:
                        continue

                    # Must be time-aligned
                    if abs(higher.start - lower.start) > time_tolerance:
                        continue

                    # Compare spectral energy at fundamentals
                    lower_freq = 440.0 * (2 ** ((lower.pitch - 69) / 12))
                    higher_freq = 440.0 * (2 ** ((higher.pitch - 69) / 12))

                    lower_energy = context.get_spectral_energy_at_freq(
                        lower_freq, lower.start, lower.end
                    )
                    higher_energy = context.get_spectral_energy_at_freq(
                        higher_freq, higher.start, higher.end
                    )

                    # If higher note has much more spectral support, lower is subharmonic
                    if lower_energy > 0 and higher_energy > 0:
                        ratio = higher_energy / lower_energy
                        if ratio > self.spectral_ratio_threshold:
                            pairs.append((higher, lower, ratio))
                    elif higher_energy > 0 and lower_energy == 0:
                        # Lower note has no spectral support at all
                        pairs.append((higher, lower, 10.0))

        return pairs
