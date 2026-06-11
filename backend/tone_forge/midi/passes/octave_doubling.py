"""Octave doubling pass for bass.

Adds upper octave notes when bass patterns suggest octave doubling
but the neural model only detected the fundamental. This compensates
for basic-pitch's tendency to miss upper octave notes in bass stems.

The pass analyzes the pitch distribution and adds synthetic notes
at +12 semitones for notes that appear to be missing their octave pair.
"""
from __future__ import annotations

import logging
import time
from dataclasses import replace
from typing import Dict, List, Optional, Set, Tuple
from collections import Counter

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


class OctaveDoublingPass(ExtractionPass):
    """Add missing upper octave notes for bass.

    Bass synths commonly play octaves (e.g., D2+D3 together), but
    basic-pitch often only detects the stronger fundamental (D2).
    This pass adds the missing upper octave notes.

    Detection heuristics:
    - Note is in typical bass range (MIDI 36-48)
    - Note has high confidence/velocity (strong fundamental)
    - No corresponding note exists at +12 semitones
    - Pitch pattern suggests octave doubling is expected
    """

    def __init__(
        self,
        pass_number: int = 0,
        min_confidence_for_doubling: float = 0.5,
        doubling_confidence_factor: float = 0.7,
        bass_range: Tuple[int, int] = (36, 48),
        enable_pattern_detection: bool = True,
        min_doubling_ratio: float = 0.3,
    ):
        """Initialize octave doubling pass.

        Args:
            pass_number: Pass number in pipeline
            min_confidence_for_doubling: Minimum confidence to create doubled note
            doubling_confidence_factor: Confidence multiplier for synthetic notes
            bass_range: MIDI range to consider for doubling (low, high)
            enable_pattern_detection: Use pattern analysis to decide doubling
            min_doubling_ratio: Minimum ratio of notes to double (0-1)
        """
        super().__init__(pass_number)
        self.min_confidence_for_doubling = min_confidence_for_doubling
        self.doubling_confidence_factor = doubling_confidence_factor
        self.bass_range = bass_range
        self.enable_pattern_detection = enable_pattern_detection
        self.min_doubling_ratio = min_doubling_ratio

    @property
    def name(self) -> str:
        return "octave_doubling"

    def process(
        self,
        notes: List[ExtractedNote],
        context: ExtractionContext,
    ) -> PassResult:
        """Add missing upper octave notes.

        Args:
            notes: Input notes
            context: Extraction context

        Returns:
            PassResult with doubled notes added
        """
        start_time = time.time()
        input_notes = notes.copy()

        if len(notes) == 0:
            return PassResult(
                notes=[],
                statistics=self._create_statistics(input_notes, [], 0.0),
            )

        # Only apply to bass stems
        stem_type = context.stem_type or ""
        if "bass" not in stem_type.lower():
            return PassResult(
                notes=notes,
                statistics=self._create_statistics(input_notes, notes, 0.0),
                metadata={"skipped": "not_bass_stem"},
            )

        # Analyze pitch distribution
        pitches = [n.pitch for n in notes]
        pitch_counts = Counter(pitches)

        # Check if doubling is warranted
        should_double, doubling_reason = self._should_apply_doubling(
            notes, pitch_counts, context
        )

        if not should_double:
            return PassResult(
                notes=notes,
                statistics=self._create_statistics(input_notes, notes, 0.0),
                metadata={"skipped": doubling_reason},
            )

        # Find notes to double
        doubled_notes = []
        notes_doubled = 0

        # Determine which pitches should be doubled (only root notes)
        pitches_to_double = self._get_pitches_to_double(pitch_counts)
        logger.debug(f"Pitches to double: {pitches_to_double}")

        # Group notes by time to avoid doubling when upper octave exists
        notes_by_time: Dict[float, List[ExtractedNote]] = {}
        for note in notes:
            time_key = round(note.start * 20) / 20  # 50ms buckets
            if time_key not in notes_by_time:
                notes_by_time[time_key] = []
            notes_by_time[time_key].append(note)

        for note in notes:
            # Check if note is in bass doubling range
            if not (self.bass_range[0] <= note.pitch <= self.bass_range[1]):
                continue

            # Only double root notes (most frequent pitch)
            if note.pitch not in pitches_to_double:
                continue

            # Check confidence threshold
            if note.confidence < self.min_confidence_for_doubling:
                continue

            # Check if upper octave already exists at this time
            time_key = round(note.start * 20) / 20
            concurrent_pitches = {n.pitch for n in notes_by_time.get(time_key, [])}
            upper_octave_pitch = note.pitch + 12

            if upper_octave_pitch in concurrent_pitches:
                # Already have the upper octave
                continue

            # Create doubled note
            doubled_confidence = note.confidence * self.doubling_confidence_factor
            provenance = NoteProvenance(
                source=self.name,
                cleanup_passes=[self.name],
                original_pitch=note.pitch,
                pitch_corrected=True,  # Mark as pitch-modified
            )

            doubled_note = ExtractedNote(
                pitch=upper_octave_pitch,
                start=note.start,
                end=note.end,
                velocity=int(note.velocity * 0.85),  # Slightly lower velocity
                confidence=doubled_confidence,
                source_pass=self.pass_number,  # Use pass number, not name
                provenance=provenance,
                flags={NoteFlag.SYNTHETIC, NoteFlag.OCTAVE_DOUBLED},
            )
            doubled_notes.append(doubled_note)
            notes_doubled += 1

        # Combine original and doubled notes
        output_notes = notes + doubled_notes
        output_notes = sorted(output_notes, key=lambda n: (n.start, n.pitch))

        execution_time = (time.time() - start_time) * 1000

        stats = self._create_statistics(
            input_notes,
            output_notes,
            execution_time,
            notes_doubled=notes_doubled,
            doubling_reason=doubling_reason,
        )

        return PassResult(
            notes=output_notes,
            statistics=stats,
            metadata={
                "notes_doubled": notes_doubled,
                "doubling_reason": doubling_reason,
            },
        )

    def _should_apply_doubling(
        self,
        notes: List[ExtractedNote],
        pitch_counts: Counter,
        context: ExtractionContext,
    ) -> Tuple[bool, str]:
        """Determine if octave doubling should be applied.

        Returns:
            (should_double, reason) tuple
        """
        if not self.enable_pattern_detection:
            return True, "pattern_detection_disabled"

        # Check if notes are predominantly in lower bass range
        bass_notes = [n for n in notes if self.bass_range[0] <= n.pitch <= self.bass_range[1]]
        upper_notes = [n for n in notes if n.pitch > self.bass_range[1]]

        if len(bass_notes) == 0:
            return False, "no_bass_range_notes"

        # Calculate ratio of lower to upper notes
        lower_ratio = len(bass_notes) / len(notes) if notes else 0
        upper_ratio = len(upper_notes) / len(notes) if notes else 0

        # If most notes are in lower range and few in upper, doubling is warranted
        if lower_ratio > 0.7 and upper_ratio < 0.2:
            return True, f"imbalanced_octaves_lower={lower_ratio:.2f}_upper={upper_ratio:.2f}"

        # Check for specific pitch class imbalance (e.g., lots of D2 but no D3)
        for pitch in range(self.bass_range[0], self.bass_range[1] + 1):
            lower_count = pitch_counts.get(pitch, 0)
            upper_count = pitch_counts.get(pitch + 12, 0)

            if lower_count > 50 and upper_count < lower_count * 0.2:
                return True, f"pitch_imbalance_midi{pitch}={lower_count}_midi{pitch+12}={upper_count}"

        return False, "octave_balance_ok"

    def _get_pitches_to_double(
        self,
        pitch_counts: Counter,
    ) -> Set[int]:
        """Determine which pitches should be doubled.

        Only the most frequent pitch (typically the root) gets doubled,
        as bass octave doubling usually only applies to the root.

        Returns:
            Set of MIDI pitches to double
        """
        # Get bass range pitches sorted by frequency
        bass_pitches = [
            (pitch, count)
            for pitch, count in pitch_counts.items()
            if self.bass_range[0] <= pitch <= self.bass_range[1]
        ]
        bass_pitches.sort(key=lambda x: x[1], reverse=True)

        if not bass_pitches:
            return set()

        # Only double the most frequent pitch (the root)
        # This is because bass typically only doubles the root, not chord tones
        most_frequent_pitch, most_frequent_count = bass_pitches[0]

        # Check if this pitch is significantly more common than others
        # (indicating it's likely the root)
        pitches_to_double = {most_frequent_pitch}

        # Also double any pitch that has similar frequency (within 30%)
        # to handle cases where root is played in different positions
        for pitch, count in bass_pitches[1:3]:  # Check next 2 most frequent
            if count > most_frequent_count * 0.7:
                # Close in frequency - might also be a root position
                pitches_to_double.add(pitch)

        return pitches_to_double


def create_octave_doubling_pass(
    pass_number: int = 0,
    aggressive: bool = False,
) -> OctaveDoublingPass:
    """Factory function for octave doubling pass.

    Args:
        pass_number: Pass number in pipeline
        aggressive: Use more aggressive doubling settings

    Returns:
        Configured OctaveDoublingPass
    """
    if aggressive:
        return OctaveDoublingPass(
            pass_number=pass_number,
            min_confidence_for_doubling=0.4,
            doubling_confidence_factor=0.8,
            min_doubling_ratio=0.5,
        )
    else:
        return OctaveDoublingPass(
            pass_number=pass_number,
            min_confidence_for_doubling=0.5,
            doubling_confidence_factor=0.7,
            min_doubling_ratio=0.3,
        )
