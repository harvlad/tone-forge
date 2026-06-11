"""Octave correction pass.

Fixes sub-harmonic detection where notes (especially bass) are
detected an octave too low. This is common when the fundamental
is weak compared to harmonics.

Uses spectral analysis and musical context to determine if
notes should be shifted up an octave.
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

# Bass range boundaries (MIDI note numbers)
BASS_LOW_LIMIT = 24   # C1 - below this is unusual
BASS_HIGH_LIMIT = 48  # C3 - above this is mid-range
SUB_BASS_THRESHOLD = 36  # C2 - notes below this are suspicious

# Typical instrument ranges (MIDI note numbers)
# These are tuned based on analysis of real-world extraction results
# where basic-pitch tends to detect notes 1-2 octaves too low
DEFAULT_STEM_RANGES = {
    "bass": (36, 60),      # C2 to C4 - raised from E1, bass rarely below C2
    "lead": (48, 96),      # C3 to C7
    "synth": (36, 96),     # C2 to C7
    "pad": (36, 84),       # C2 to C6
    "vocals": (48, 84),    # C3 to C6
}


class OctaveCorrectionPass(ExtractionPass):
    """Correct sub-harmonic octave detection errors.

    This pass identifies notes that are likely detected an octave
    too low (sub-harmonic detection) and shifts them up. Particularly
    useful for bass stems where the fundamental can be weak.

    Detection criteria:
    - Notes below typical instrument range
    - Notes that would better fit the detected key when shifted
    - Notes with low confidence that have stronger harmonics above
    - Patterns suggesting sub-harmonic tracking
    """

    def __init__(
        self,
        pass_number: int = 0,
        min_correction_probability: float = 0.6,
        low_note_threshold: int = SUB_BASS_THRESHOLD,
        stem_type_ranges: Optional[Dict[str, Tuple[int, int]]] = None,
        prefer_key_conformity: bool = True,
        analyze_harmonics: bool = True,
        check_double_octave: bool = True,
        aggressive_bass_correction: bool = True,
    ):
        """Initialize octave correction pass.

        Args:
            pass_number: Pass number in pipeline
            min_correction_probability: Minimum probability to apply correction
            low_note_threshold: Notes below this are candidates for correction
            stem_type_ranges: Expected ranges per stem type {stem: (low, high)}
            prefer_key_conformity: Prefer shifts that improve key conformity
            analyze_harmonics: Check for harmonic evidence of higher octave
            check_double_octave: Also check for 2-octave correction (24 semitones)
            aggressive_bass_correction: Use more aggressive correction for bass stems
        """
        super().__init__(pass_number)
        self.min_correction_probability = min_correction_probability
        self.low_note_threshold = low_note_threshold
        self.prefer_key_conformity = prefer_key_conformity
        self.analyze_harmonics = analyze_harmonics
        self.check_double_octave = check_double_octave
        self.aggressive_bass_correction = aggressive_bass_correction

        # Default ranges if not specified - tuned based on extraction analysis
        self.stem_type_ranges = stem_type_ranges or DEFAULT_STEM_RANGES.copy()

    @property
    def name(self) -> str:
        return "octave_correction"

    def process(
        self,
        notes: List[ExtractedNote],
        context: ExtractionContext,
    ) -> PassResult:
        """Correct octave detection errors.

        Args:
            notes: Input notes
            context: Extraction context

        Returns:
            PassResult with corrected notes
        """
        start_time = time.time()
        input_notes = notes.copy()

        if len(notes) == 0:
            return PassResult(
                notes=[],
                statistics=self._create_statistics(input_notes, [], 0.0),
            )

        # Get expected range for this stem type
        stem_type = context.stem_type or "bass"
        expected_low, expected_high = self.stem_type_ranges.get(
            stem_type, (24, 96)
        )

        # For aggressive bass correction, use tighter threshold
        is_bass = stem_type.lower() in ("bass", "sub_bass", "mono_bass", "poly_bass")
        if is_bass and self.aggressive_bass_correction:
            # Bass typically shouldn't go below C2 (36) in most genres
            expected_low = max(expected_low, 36)
            correction_threshold = self.min_correction_probability - 0.1
        else:
            correction_threshold = self.min_correction_probability

        # Get key information for conformity checking
        key_root, key_mode = context.key or (0, "major")
        key_pitches = self._get_key_pitches(key_root, key_mode)

        # Score each note for octave correction
        # Tuple: (score, factors, octave_shift) where octave_shift is 12 or 24
        correction_scores: Dict[int, Tuple[float, Dict[str, float], int]] = {}

        for i, note in enumerate(notes):
            if note.pitch >= expected_low:
                # Not suspiciously low
                continue

            # Check single octave correction first
            score, factors = self._compute_correction_probability(
                note, notes, i, expected_low, expected_high, key_pitches, context,
                octave_shift=12
            )

            best_score = score
            best_factors = factors
            best_shift = 12

            # Check double octave correction if enabled and note is very low
            if self.check_double_octave and note.pitch < expected_low - 12:
                score_double, factors_double = self._compute_correction_probability(
                    note, notes, i, expected_low, expected_high, key_pitches, context,
                    octave_shift=24
                )
                if score_double > best_score:
                    best_score = score_double
                    best_factors = factors_double
                    best_shift = 24

            if best_score >= correction_threshold:
                correction_scores[i] = (best_score, best_factors, best_shift)

        # Apply corrections
        output_notes = []
        corrected_count = 0
        double_corrected_count = 0

        for i, note in enumerate(notes):
            if i in correction_scores:
                score, factors, octave_shift = correction_scores[i]

                # Shift up by determined amount (12 or 24 semitones)
                new_pitch = note.pitch + octave_shift

                # Update provenance
                provenance = note.provenance or NoteProvenance()
                provenance = replace(
                    provenance,
                    cleanup_passes=provenance.cleanup_passes + [self.name],
                    pitch_corrected=True,
                    original_pitch=note.pitch,
                )

                corrected_note = replace(
                    note,
                    pitch=new_pitch,
                    provenance=provenance,
                )
                output_notes.append(corrected_note)
                corrected_count += 1
                if octave_shift == 24:
                    double_corrected_count += 1

                logger.debug(
                    f"Octave corrected: {note.pitch} -> {new_pitch} "
                    f"(shift={octave_shift}, score={score:.2f}, factors={factors})"
                )
            else:
                output_notes.append(note)

        execution_time = (time.time() - start_time) * 1000

        stats = self._create_statistics(
            input_notes,
            output_notes,
            execution_time,
            notes_corrected=corrected_count,
            double_octave_corrected=double_corrected_count,
            stem_type=stem_type,
            expected_range=(expected_low, expected_high),
            aggressive_mode=is_bass and self.aggressive_bass_correction,
        )

        warnings = []
        if corrected_count > len(input_notes) * 0.5:
            warnings.append(
                f"Corrected {corrected_count}/{len(input_notes)} notes - "
                "may indicate systematic detection issue"
            )

        return PassResult(
            notes=output_notes,
            statistics=stats,
            warnings=warnings,
            metadata={
                "corrections_applied": corrected_count,
                "double_octave_corrections": double_corrected_count,
                "stem_type": stem_type,
                "expected_low": expected_low,
                "aggressive_mode": is_bass and self.aggressive_bass_correction,
            },
        )

    def _compute_correction_probability(
        self,
        note: ExtractedNote,
        all_notes: List[ExtractedNote],
        note_idx: int,
        expected_low: int,
        expected_high: int,
        key_pitches: Set[int],
        context: ExtractionContext,
        octave_shift: int = 12,
    ) -> Tuple[float, Dict[str, float]]:
        """Compute probability that note should be shifted up.

        Args:
            octave_shift: Amount to shift (12 for single octave, 24 for double)
        """
        factors = {}
        shifted_pitch = note.pitch + octave_shift

        # 1. Range factor - how far below expected range, and where would shift land?
        range_distance = expected_low - note.pitch
        shifted_in_range = expected_low <= shifted_pitch <= expected_high

        if range_distance > 24:
            # More than 2 octaves below - definitely wrong
            factors["range"] = 0.95 if shifted_in_range else 0.7
        elif range_distance > 12:
            # More than 1 octave below
            factors["range"] = 0.9 if shifted_in_range else 0.6
        elif range_distance > 6:
            factors["range"] = 0.8 if shifted_in_range else 0.5
        elif range_distance > 0:
            factors["range"] = 0.65 if shifted_in_range else 0.4
        else:
            factors["range"] = 0.3

        # 2. Key conformity - does shifting improve key fit?
        if self.prefer_key_conformity and key_pitches:
            current_in_key = (note.pitch % 12) in key_pitches
            shifted_in_key = (shifted_pitch % 12) in key_pitches

            if not current_in_key and shifted_in_key:
                factors["key"] = 0.9
            elif current_in_key and shifted_in_key:
                factors["key"] = 0.5
            elif current_in_key and not shifted_in_key:
                factors["key"] = 0.2
            else:
                factors["key"] = 0.5
        else:
            factors["key"] = 0.5

        # 3. Harmonic evidence - are there notes at the target octave?
        has_harmonic = any(
            n.pitch == shifted_pitch and
            abs(n.start - note.start) < 0.1  # Simultaneous
            for n in all_notes
        )
        if has_harmonic:
            # Already have a note there - we're likely detecting sub-harmonic
            factors["harmonic"] = 0.85
        else:
            # No note there - still might be sub-harmonic detection
            factors["harmonic"] = 0.5

        # 4. Confidence factor - low confidence suggests detection issue
        if note.confidence < 0.4:
            factors["confidence"] = 0.8
        elif note.confidence < 0.6:
            factors["confidence"] = 0.65
        else:
            factors["confidence"] = 0.4

        # 5. Context factor - do other notes suggest higher octave?
        other_pitches = [n.pitch for n in all_notes if n != note]
        if other_pitches:
            avg_pitch = np.mean(other_pitches)
            median_pitch = np.median(other_pitches)

            # How far below the median is this note?
            distance_from_median = median_pitch - note.pitch

            if distance_from_median > 18:  # More than octave + fifth below
                factors["context"] = 0.9
            elif distance_from_median > 12:  # More than octave below
                factors["context"] = 0.8
            elif distance_from_median > 6:  # More than fifth below
                factors["context"] = 0.65
            else:
                factors["context"] = 0.5
        else:
            factors["context"] = 0.5

        # 6. Pattern factor - consistent low detection suggests issue
        same_pitch_count = sum(1 for n in all_notes if n.pitch == note.pitch)
        total_notes = len(all_notes)
        if total_notes > 0 and same_pitch_count / total_notes > 0.3:
            # Many notes at this pitch - systematic issue likely
            factors["pattern"] = 0.8
        elif total_notes > 0 and same_pitch_count / total_notes > 0.15:
            factors["pattern"] = 0.65
        else:
            factors["pattern"] = 0.5

        # 7. Double-octave penalty/bonus
        if octave_shift == 24:
            # Double-octave should only be used when really necessary
            if range_distance > 18:
                # Very far below - double shift more appropriate
                factors["shift_type"] = 0.75
            else:
                # Prefer single octave when possible
                factors["shift_type"] = 0.4
        else:
            factors["shift_type"] = 0.6

        # Combine factors
        weights = {
            "range": 0.25,
            "key": 0.15,
            "harmonic": 0.15,
            "confidence": 0.1,
            "context": 0.2,
            "pattern": 0.1,
            "shift_type": 0.05,
        }

        combined = sum(factors[k] * weights[k] for k in factors)
        return combined, factors

    def _get_key_pitches(self, root: int, mode: str) -> Set[int]:
        """Get pitch classes in the given key."""
        if mode == "major":
            intervals = [0, 2, 4, 5, 7, 9, 11]
        elif mode == "minor":
            intervals = [0, 2, 3, 5, 7, 8, 10]
        else:
            # Default to chromatic (all pitches OK)
            return set(range(12))

        return {(root + i) % 12 for i in intervals}
