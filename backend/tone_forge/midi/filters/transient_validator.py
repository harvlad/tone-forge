"""Transient note validator.

Validates that detected notes have genuine transient/onset characteristics.
Suppresses notes that appear to be resonance or spectral artifacts rather
than actual note attacks.

Key insight: real notes have clear onset transients; artifacts often
appear as gradual energy buildup or resonance tails.
"""
from __future__ import annotations

from typing import List

import numpy as np

from tone_forge.midi.passes.base import ExtractedNote
from .base import (
    FilterContext,
    NoteScore,
    PrecisionFilter,
    ProtectionReason,
    SuppressionReason,
)


class TransientNoteValidator(PrecisionFilter):
    """Filter that validates note transients.

    Real musical notes typically have:
    1. Clear onset (energy increase)
    2. Spectral change at note start
    3. Defined attack envelope

    Artifacts often lack these characteristics:
    - Gradual energy buildup (resonance)
    - No spectral change (sustained harmonics)
    - No clear attack

    This filter uses lightweight DSP to validate transients.
    """

    def __init__(
        self,
        min_suppression_confidence: float = 0.8,  # Higher threshold - harder to suppress
        protection_weight: float = 2.0,  # Stronger protection
        onset_window_ms: float = 50.0,  # Wider window
        min_onset_strength: float = 0.1,  # Lower threshold - easier to pass
        spectral_flux_threshold: float = 0.1,
    ):
        """Initialize filter.

        Args:
            min_suppression_confidence: Minimum confidence to suppress
            protection_weight: Weight for protection vs suppression
            onset_window_ms: Window around note start to analyze
            min_onset_strength: Minimum onset strength to consider valid
            spectral_flux_threshold: Minimum spectral change at onset
        """
        super().__init__(min_suppression_confidence, protection_weight)
        self.onset_window_ms = onset_window_ms
        self.min_onset_strength = min_onset_strength
        self.spectral_flux_threshold = spectral_flux_threshold

    @property
    def name(self) -> str:
        return "transient_validator"

    def score_notes(
        self,
        notes: List[ExtractedNote],
        context: FilterContext,
    ) -> List[NoteScore]:
        """Score notes based on transient validity."""
        scores = []

        # Compute onset strength for all note positions
        onset_strengths = self._compute_onset_strengths(notes, context)

        for note, onset_strength in zip(notes, onset_strengths):
            score = NoteScore(note=note)

            # Weak onset MAY suggest resonance artifact - but be conservative
            if onset_strength < self.min_onset_strength:
                # Only set modest suppression score - don't be too aggressive
                score.suppression_score = (self.min_onset_strength - onset_strength) / self.min_onset_strength
                score.suppression_score = min(0.5, score.suppression_score)  # Cap at 0.5 - conservative
                score.suppression_reasons.append(SuppressionReason.RESONANCE_ARTIFACT)
            else:
                # Strong onset provides protection
                score.spectral_support = onset_strength
                score.protection_score = max(score.protection_score, onset_strength * 0.3)
                score.protection_reasons.append(ProtectionReason.STRONG_SPECTRAL_SUPPORT)

            # Apply protection rules - more generous thresholds
            if context.tempo:
                rhythmic = self._compute_rhythmic_alignment(
                    note, context.tempo, context.time_signature
                )
                if rhythmic > 0.5:  # Lower threshold
                    score.protection_score = max(score.protection_score, rhythmic * 0.6)
                    score.protection_reasons.append(ProtectionReason.RHYTHMIC_ALIGNMENT)

            if context.key:
                key_fit = self._compute_key_conformity(note, context.key)
                if key_fit > 0.6:  # Lower threshold
                    score.protection_score = max(score.protection_score, key_fit * 0.4)
                    score.protection_reasons.append(ProtectionReason.KEY_CONFORMITY)

            # High confidence protects - lower threshold, stronger protection
            if note.confidence > 0.5:  # Lower threshold (was 0.75)
                score.protection_score = max(score.protection_score, note.confidence * 0.5)
                score.protection_reasons.append(ProtectionReason.HIGH_CONFIDENCE)

            # Notes that are part of melodic lines get strong protection
            melodic_context = self._check_melodic_context(note, notes)
            if melodic_context > 0.3:  # Lower threshold (was 0.5)
                score.protection_score = max(score.protection_score, melodic_context * 0.6)
                score.protection_reasons.append(ProtectionReason.MELODIC_CONTINUITY)

            scores.append(score)

        return scores

    def _compute_onset_strengths(
        self,
        notes: List[ExtractedNote],
        context: FilterContext,
    ) -> List[float]:
        """Compute onset strength for each note.

        Uses spectral flux in a window around the note start.
        """
        import librosa

        # Compute onset strength envelope
        onset_env = librosa.onset.onset_strength(
            y=context.audio,
            sr=context.sr,
            hop_length=512,
        )

        # Convert to times
        times = librosa.times_like(onset_env, sr=context.sr, hop_length=512)

        # Normalize
        if onset_env.max() > 0:
            onset_env = onset_env / onset_env.max()

        strengths = []
        window_sec = self.onset_window_ms / 1000.0

        for note in notes:
            # Find onset strength at note start
            start_idx = np.searchsorted(times, note.start)
            window_samples = int(window_sec * context.sr / 512)

            # Look for peak in window around start
            start_window = max(0, start_idx - window_samples // 2)
            end_window = min(len(onset_env), start_idx + window_samples // 2)

            if start_window < end_window:
                strength = float(np.max(onset_env[start_window:end_window]))
            else:
                strength = 0.0

            strengths.append(strength)

        return strengths

    def _check_melodic_context(
        self,
        note: ExtractedNote,
        all_notes: List[ExtractedNote],
    ) -> float:
        """Check if note has melodic context (preceding/following notes).

        Returns 0-1 score where 1 = strong melodic context.
        """
        tolerance = 0.3  # 300ms gap tolerance
        pitch_range = 7  # Within a 5th

        # Find preceding notes
        preceding = [
            n for n in all_notes
            if n.end <= note.start + 0.01
            and note.start - n.end < tolerance
            and abs(n.pitch - note.pitch) <= pitch_range
            and n != note
        ]

        # Find following notes
        following = [
            n for n in all_notes
            if n.start >= note.end - 0.01
            and n.start - note.end < tolerance
            and abs(n.pitch - note.pitch) <= pitch_range
            and n != note
        ]

        # Score based on melodic continuity
        score = 0.0
        if preceding:
            score += 0.5
        if following:
            score += 0.5

        return score
