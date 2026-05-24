"""Octave false positive filter.

Detects and suppresses notes that are octave hallucinations -
notes detected at octave multiples of real notes due to
strong harmonic content or model confusion.

Key insight: if a note at pitch P exists and a note at P+12 or P-12
has much weaker spectral support, it's likely an octave hallucination.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Set

import numpy as np

from tone_forge.midi.passes.base import ExtractedNote
from .base import (
    FilterContext,
    NoteScore,
    PrecisionFilter,
    ProtectionReason,
    SuppressionReason,
)


class OctaveFalsePositiveFilter(PrecisionFilter):
    """Filter that removes octave hallucination artifacts.

    Octave hallucinations occur when:
    1. Strong harmonic content causes detection at octave multiples
    2. Model confusion between fundamental and harmonics
    3. Subharmonic detection artifacts

    This filter:
    1. Groups notes by timing (simultaneous notes)
    2. For octave pairs, compares spectral support
    3. Suppresses the weaker note if spectral ratio is strong
    """

    def __init__(
        self,
        min_suppression_confidence: float = 0.7,
        protection_weight: float = 1.5,
        octave_tolerance_cents: float = 50.0,
        time_tolerance_ms: float = 50.0,
        spectral_ratio_threshold: float = 3.0,
    ):
        """Initialize filter.

        Args:
            min_suppression_confidence: Minimum confidence to suppress
            protection_weight: Weight for protection vs suppression
            octave_tolerance_cents: Pitch tolerance for octave matching
            time_tolerance_ms: Time tolerance for simultaneous notes
            spectral_ratio_threshold: Ratio of spectral energy to consider hallucination
        """
        super().__init__(min_suppression_confidence, protection_weight)
        self.octave_tolerance_cents = octave_tolerance_cents
        self.time_tolerance_ms = time_tolerance_ms
        self.spectral_ratio_threshold = spectral_ratio_threshold

    @property
    def name(self) -> str:
        return "octave_false_positive"

    def score_notes(
        self,
        notes: List[ExtractedNote],
        context: FilterContext,
    ) -> List[NoteScore]:
        """Score notes for octave hallucination."""
        scores = []

        # Build lookup for quick access
        note_map: Dict[int, List[ExtractedNote]] = {}
        for note in notes:
            idx = id(note)
            note_map[idx] = note

        # Find octave relationships
        octave_pairs = self._find_octave_pairs(notes)

        # Score each note
        for note in notes:
            score = NoteScore(note=note)

            # Check if this note is in an octave pair
            for primary, secondary, spectral_ratio in octave_pairs:
                if secondary == note:
                    # This note is the weaker partner in an octave pair
                    score.suppression_score = min(1.0, spectral_ratio / self.spectral_ratio_threshold)
                    score.suppression_reasons.append(SuppressionReason.OCTAVE_HALLUCINATION)
                    score.spectral_support = 1.0 / spectral_ratio if spectral_ratio > 0 else 0

            # Apply protection rules
            if context.tempo:
                rhythmic = self._compute_rhythmic_alignment(note, context.tempo, context.time_signature)
                if rhythmic > 0.8:
                    score.protection_score = max(score.protection_score, rhythmic * 0.5)
                    score.protection_reasons.append(ProtectionReason.RHYTHMIC_ALIGNMENT)

            if context.key:
                key_fit = self._compute_key_conformity(note, context.key)
                if key_fit > 0.8:
                    score.protection_score = max(score.protection_score, key_fit * 0.3)
                    score.protection_reasons.append(ProtectionReason.KEY_CONFORMITY)

            # High original confidence provides protection
            if note.confidence > 0.7:
                score.protection_score = max(score.protection_score, note.confidence * 0.4)
                score.protection_reasons.append(ProtectionReason.HIGH_CONFIDENCE)

            scores.append(score)

        return scores

    def _find_octave_pairs(
        self,
        notes: List[ExtractedNote],
    ) -> List[tuple]:
        """Find pairs of notes that are octave-related.

        Returns list of (primary_note, secondary_note, spectral_ratio).
        """
        pairs = []
        time_tolerance = self.time_tolerance_ms / 1000.0

        # Group notes by approximate start time
        time_groups: Dict[int, List[ExtractedNote]] = {}
        time_resolution = 0.05  # 50ms buckets

        for note in notes:
            bucket = int(note.start / time_resolution)
            if bucket not in time_groups:
                time_groups[bucket] = []
            time_groups[bucket].append(note)

        # Check adjacent buckets for octave relationships
        for bucket, group in time_groups.items():
            # Include notes from adjacent buckets
            candidates = list(group)
            for adj in [bucket - 1, bucket + 1]:
                if adj in time_groups:
                    candidates.extend(time_groups[adj])

            # Check all pairs
            for i, note1 in enumerate(candidates):
                for note2 in candidates[i + 1:]:
                    # Check if octave related
                    pitch_diff = abs(note1.pitch - note2.pitch)
                    if pitch_diff not in [12, 24]:  # One or two octaves
                        continue

                    # Check timing overlap
                    if abs(note1.start - note2.start) > time_tolerance:
                        continue

                    # Determine which is likely the hallucination
                    # Generally, lower octave with higher confidence is real
                    if note1.pitch < note2.pitch:
                        lower, higher = note1, note2
                    else:
                        lower, higher = note2, note1

                    # Use confidence as proxy for spectral support
                    # (Real spectral analysis would be better but slower)
                    if lower.confidence > 0 and higher.confidence > 0:
                        ratio = lower.confidence / higher.confidence
                        if ratio > 1.0:
                            pairs.append((lower, higher, ratio))
                        else:
                            pairs.append((higher, lower, 1.0 / ratio))

        return pairs

    def _get_spectral_energy(
        self,
        context: FilterContext,
        note: ExtractedNote,
    ) -> float:
        """Get spectral energy at note's fundamental frequency."""
        # Convert MIDI pitch to frequency
        freq = 440.0 * (2 ** ((note.pitch - 69) / 12))

        return context.get_spectral_energy_at_freq(
            freq,
            note.start,
            note.end,
            bandwidth_hz=freq * 0.03,  # 3% bandwidth
        )
