"""Probabilistic delay artifact removal.

Unlike binary delay detection, this pass scores each note on multiple
factors to determine delay probability:
- Timing consistency with delay interval
- Velocity falloff pattern
- Spectral similarity to source
- Phrase context (part of melody vs isolated)

Only suppresses notes with high combined probability scores.
This preserves intentional repeated notes (staccato, trills) while
removing actual delay artifacts.
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


class DelayCleanupPass(ExtractionPass):
    """Probabilistic delay artifact removal.

    This pass uses multi-factor scoring to identify delay artifacts
    while preserving intentional repeated notes. Each potential
    delay note is scored on:

    1. timing_consistency: Does it match a delay interval?
    2. velocity_falloff: Does velocity decrease appropriately?
    3. confidence_decay: Does extraction confidence decrease?
    4. phrase_context: Is it part of a melodic phrase or isolated?
    5. repetition_pattern: Does the pattern look like delay vs music?

    Only notes with combined_score > threshold are suppressed.
    """

    def __init__(
        self,
        pass_number: int = 0,
        min_suppression_probability: float = 0.85,
        timing_tolerance_ms: float = 25.0,
        min_delay_ms: float = 50.0,
        max_delay_ms: float = 600.0,
        expected_velocity_decay: float = 0.75,
        expected_confidence_decay: float = 0.85,
        phrase_isolation_penalty: float = 0.15,
    ):
        """Initialize delay cleanup pass.

        Args:
            pass_number: Pass number in pipeline
            min_suppression_probability: Minimum combined probability to suppress
            timing_tolerance_ms: Timing tolerance for delay pattern matching
            min_delay_ms: Minimum delay time to consider
            max_delay_ms: Maximum delay time to consider
            expected_velocity_decay: Expected velocity ratio for delay repeats
            expected_confidence_decay: Expected confidence ratio for delay repeats
            phrase_isolation_penalty: Penalty for notes that appear melodic
        """
        super().__init__(pass_number)
        self.min_suppression_probability = min_suppression_probability
        self.timing_tolerance_ms = timing_tolerance_ms
        self.min_delay_ms = min_delay_ms
        self.max_delay_ms = max_delay_ms
        self.expected_velocity_decay = expected_velocity_decay
        self.expected_confidence_decay = expected_confidence_decay
        self.phrase_isolation_penalty = phrase_isolation_penalty

    @property
    def name(self) -> str:
        return "delay_cleanup"

    def process(
        self,
        notes: List[ExtractedNote],
        context: ExtractionContext,
    ) -> PassResult:
        """Remove delay artifacts using probabilistic scoring.

        Args:
            notes: Input notes
            context: Extraction context

        Returns:
            PassResult with delay artifacts handled
        """
        start_time = time.time()
        input_notes = notes.copy()

        if len(notes) < 3:
            return PassResult(
                notes=notes,
                statistics=self._create_statistics(input_notes, notes, 0.0),
            )

        # Estimate tempo and common delay times
        tempo = context.tempo or self._estimate_tempo(notes)
        delay_candidates = self._get_delay_candidates(tempo)

        # Detect the most likely delay time
        detected_delay_ms = self._detect_delay_time(notes, delay_candidates)

        # Score each note for delay probability
        delay_scores: Dict[int, Tuple[float, Dict[str, float]]] = {}

        if detected_delay_ms is not None:
            delay_sec = detected_delay_ms / 1000.0

            # Group notes by pitch
            pitch_groups = self._group_by_pitch(notes)

            for pitch, indexed_notes in pitch_groups.items():
                if len(indexed_notes) < 2:
                    continue

                # Sort by time
                indexed_notes = sorted(indexed_notes, key=lambda x: x[1].start)

                # Score each note (except first in chain)
                for i in range(1, len(indexed_notes)):
                    idx, note = indexed_notes[i]

                    # Find potential source notes
                    for j in range(i):
                        src_idx, src_note = indexed_notes[j]
                        gap = note.start - src_note.start

                        # Check if gap matches delay interval (or multiples)
                        for multiplier in [1, 2, 3]:
                            expected_gap = delay_sec * multiplier
                            if abs(gap - expected_gap) < self.timing_tolerance_ms / 1000.0:
                                # Score this as potential delay
                                score, factors = self._compute_delay_probability(
                                    note, src_note, notes, idx, multiplier
                                )
                                if idx not in delay_scores or score > delay_scores[idx][0]:
                                    delay_scores[idx] = (score, factors)
                                break

        # Apply suppression based on scores
        output_notes = []
        suppressed_count = 0
        confidence_adjusted_count = 0

        for i, note in enumerate(notes):
            if i in delay_scores:
                score, factors = delay_scores[i]

                # Update provenance
                provenance = note.provenance or NoteProvenance()
                provenance = replace(
                    provenance,
                    cleanup_passes=provenance.cleanup_passes + [self.name],
                )

                if score >= self.min_suppression_probability:
                    # High confidence delay artifact - suppress
                    provenance = replace(
                        provenance,
                        suppression_reasons=provenance.suppression_reasons + [
                            f"delay_artifact_p{score:.2f}"
                        ],
                        final_confidence=0.0,
                    )
                    suppressed_count += 1
                    logger.debug(
                        f"Suppressed delay: pitch={note.pitch}, "
                        f"score={score:.2f}, factors={factors}"
                    )
                    continue
                elif score >= 0.5:
                    # Medium confidence - reduce confidence proportionally
                    penalty = (score - 0.5) * 0.6  # 0-30% reduction
                    new_confidence = note.confidence * (1 - penalty)
                    provenance = replace(
                        provenance,
                        suppression_reasons=provenance.suppression_reasons + [
                            f"possible_delay_p{score:.2f}"
                        ],
                        final_confidence=new_confidence,
                    )

                    modified_note = replace(
                        note,
                        confidence=new_confidence,
                        flags=note.flags | {NoteFlag.LOW_CONFIDENCE},
                        provenance=provenance,
                    )
                    output_notes.append(modified_note)
                    confidence_adjusted_count += 1
                    continue

            output_notes.append(note)

        execution_time = (time.time() - start_time) * 1000

        stats = self._create_statistics(
            input_notes,
            output_notes,
            execution_time,
            delays_suppressed=suppressed_count,
            confidence_adjusted=confidence_adjusted_count,
            detected_delay_ms=detected_delay_ms,
            tempo_used=tempo,
        )

        warnings = []
        if suppressed_count > len(input_notes) * 0.25:
            warnings.append(
                f"Suppressed {suppressed_count}/{len(input_notes)} notes as delays - "
                "consider using lead_staccato profile if these are intentional"
            )

        return PassResult(
            notes=output_notes,
            statistics=stats,
            warnings=warnings,
            metadata={
                "detected_delay_ms": detected_delay_ms,
                "delay_candidates_checked": len(delay_candidates),
            },
        )

    def _estimate_tempo(self, notes: List[ExtractedNote]) -> float:
        """Estimate tempo from note onsets."""
        if len(notes) < 2:
            return 120.0

        onsets = sorted([n.start for n in notes])
        iois = np.diff(onsets)
        iois = iois[iois > 0.05]  # Filter very short

        if len(iois) == 0:
            return 120.0

        median_ioi = np.median(iois)
        tempo = 60.0 / (median_ioi * 2)  # Assume 8th notes
        return float(np.clip(tempo, 60, 200))

    def _get_delay_candidates(self, tempo: float) -> List[float]:
        """Get candidate delay times in milliseconds."""
        beat_ms = 60000.0 / tempo
        candidates = []

        # Musically-related delay times
        musical_fractions = [1/4, 1/3, 1/2, 2/3, 3/4, 1.0]
        for frac in musical_fractions:
            delay = beat_ms * frac
            if self.min_delay_ms <= delay <= self.max_delay_ms:
                candidates.append(delay)

        # Common absolute delay times
        absolute_delays = [100, 125, 150, 175, 200, 250, 300, 375, 400, 500]
        for delay in absolute_delays:
            if self.min_delay_ms <= delay <= self.max_delay_ms:
                if delay not in candidates:
                    candidates.append(delay)

        return sorted(candidates)

    def _detect_delay_time(
        self,
        notes: List[ExtractedNote],
        candidates: List[float],
    ) -> Optional[float]:
        """Detect the most likely delay time."""
        if len(notes) < 3:
            return None

        pitch_groups = self._group_by_pitch(notes)
        tolerance_sec = self.timing_tolerance_ms / 1000.0

        best_delay = None
        best_score = 0

        for delay_ms in candidates:
            delay_sec = delay_ms / 1000.0
            score = 0

            for pitch, indexed_notes in pitch_groups.items():
                if len(indexed_notes) < 2:
                    continue

                indexed_notes = sorted(indexed_notes, key=lambda x: x[1].start)

                for i in range(len(indexed_notes) - 1):
                    _, note1 = indexed_notes[i]
                    for j in range(i + 1, min(i + 4, len(indexed_notes))):
                        _, note2 = indexed_notes[j]
                        gap = note2.start - note1.start

                        # Check multiples of delay
                        for mult in [1, 2, 3]:
                            expected = delay_sec * mult
                            if abs(gap - expected) < tolerance_sec:
                                # Weight by velocity decay (more likely if decaying)
                                if note2.velocity < note1.velocity:
                                    score += 1.5
                                else:
                                    score += 0.5
                                break

            if score > best_score:
                best_score = score
                best_delay = delay_ms

        # Only return if we found a clear pattern
        if best_score >= 3:
            logger.debug(f"Detected delay time: {best_delay:.0f}ms (score={best_score})")
            return best_delay

        return None

    def _group_by_pitch(
        self,
        notes: List[ExtractedNote],
    ) -> Dict[int, List[Tuple[int, ExtractedNote]]]:
        """Group notes by pitch."""
        groups: Dict[int, List[Tuple[int, ExtractedNote]]] = {}
        for i, note in enumerate(notes):
            if note.pitch not in groups:
                groups[note.pitch] = []
            groups[note.pitch].append((i, note))
        return groups

    def _compute_delay_probability(
        self,
        note: ExtractedNote,
        source: ExtractedNote,
        all_notes: List[ExtractedNote],
        note_idx: int,
        delay_multiplier: int,
    ) -> Tuple[float, Dict[str, float]]:
        """Compute probability that note is a delay artifact of source.

        Returns (probability, factor_breakdown).
        """
        factors = {}

        # 1. Velocity falloff
        if source.velocity > 0:
            velocity_ratio = note.velocity / source.velocity
            expected_ratio = self.expected_velocity_decay ** delay_multiplier

            if velocity_ratio <= expected_ratio + 0.1:
                factors["velocity"] = 0.9 if velocity_ratio <= expected_ratio else 0.7
            elif velocity_ratio <= 1.0:
                factors["velocity"] = 0.4
            else:
                # Louder than source - unlikely delay
                factors["velocity"] = 0.1
        else:
            factors["velocity"] = 0.5

        # 2. Confidence decay
        if source.confidence > 0:
            confidence_ratio = note.confidence / source.confidence
            expected_conf = self.expected_confidence_decay ** delay_multiplier

            if confidence_ratio <= expected_conf + 0.1:
                factors["confidence"] = 0.85
            elif confidence_ratio <= 1.0:
                factors["confidence"] = 0.5
            else:
                factors["confidence"] = 0.2
        else:
            factors["confidence"] = 0.5

        # 3. Duration similarity (delays have similar duration to source)
        if source.duration > 0:
            duration_ratio = note.duration / source.duration
            if 0.6 < duration_ratio < 1.4:
                factors["duration"] = 0.8
            elif 0.4 < duration_ratio < 2.0:
                factors["duration"] = 0.5
            else:
                factors["duration"] = 0.2
        else:
            factors["duration"] = 0.5

        # 4. Phrase context - is this note part of a melodic phrase?
        phrase_score = self._compute_phrase_context(note, all_notes, note_idx)
        factors["phrase"] = 1.0 - phrase_score  # High phrase score = low delay prob

        # 5. Repetition pattern analysis
        # True delays often have consistent velocity decay across repetitions
        factors["pattern"] = self._compute_pattern_consistency(
            note, source, all_notes, note_idx
        )

        # Combine factors (weighted average)
        weights = {
            "velocity": 0.25,
            "confidence": 0.2,
            "duration": 0.15,
            "phrase": 0.25,
            "pattern": 0.15,
        }

        combined = sum(factors[k] * weights[k] for k in factors)

        return combined, factors

    def _compute_phrase_context(
        self,
        note: ExtractedNote,
        all_notes: List[ExtractedNote],
        note_idx: int,
    ) -> float:
        """Compute how much this note appears to be part of a melodic phrase.

        Returns 0-1 where 1 = clearly part of melody, 0 = isolated.
        """
        score = 0.0
        window = 0.5  # 500ms window

        # Count nearby notes with different pitches (melodic movement)
        neighbors = [
            n for n in all_notes
            if abs(n.start - note.start) < window and n.pitch != note.pitch
        ]

        if len(neighbors) >= 2:
            score += 0.4  # Multiple notes nearby suggests melodic content

        # Check for stepwise motion (adjacent scale notes)
        for n in neighbors:
            interval = abs(n.pitch - note.pitch)
            if interval in [1, 2]:  # Semitone or whole tone
                score += 0.2
                break

        # Notes with high velocity are more likely melodic
        if note.velocity > 80:
            score += 0.2

        # Notes with high confidence are more likely intentional
        if note.confidence > 0.7:
            score += 0.2

        return min(1.0, score)

    def _compute_pattern_consistency(
        self,
        note: ExtractedNote,
        source: ExtractedNote,
        all_notes: List[ExtractedNote],
        note_idx: int,
    ) -> float:
        """Check if velocity decay pattern is consistent with delay effect."""
        # Find all notes at this pitch
        same_pitch = [
            n for n in all_notes
            if n.pitch == note.pitch and n.start <= note.start
        ]

        if len(same_pitch) < 3:
            return 0.5  # Not enough data

        # Sort by time
        same_pitch = sorted(same_pitch, key=lambda n: n.start)

        # Check for consistent decay
        velocities = [n.velocity for n in same_pitch]
        if len(velocities) >= 2:
            diffs = np.diff(velocities)
            if np.all(diffs <= 0):
                # Monotonically decreasing - consistent with delay
                return 0.9
            elif np.mean(diffs) < 0:
                # Generally decreasing
                return 0.7
            else:
                # Increasing or erratic - probably not delay
                return 0.3

        return 0.5
