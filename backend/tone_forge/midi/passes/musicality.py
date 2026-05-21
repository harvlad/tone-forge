"""Pass 7: Musicality check.

Final validation pass that checks musical coherence and flags
or removes notes that don't make musical sense.
"""
from __future__ import annotations

import logging
import time
from collections import Counter
from dataclasses import replace
from typing import Dict, List, Optional, Set, Tuple

import numpy as np

from .base import (
    ExtractionContext,
    ExtractionPass,
    ExtractedNote,
    NoteFlag,
    PassResult,
)

logger = logging.getLogger(__name__)


# Musical intervals in semitones
CONSONANT_INTERVALS = {0, 3, 4, 5, 7, 8, 9, 12}  # Unison, 3rds, 4th, 5th, 6ths, octave
DISSONANT_INTERVALS = {1, 2, 6, 10, 11}  # Semitone, whole tone, tritone, 7ths


class MusicalityCheckPass(ExtractionPass):
    """Check and validate musical coherence.

    This final pass ensures the extracted MIDI makes musical sense:
    1. Validates note relationships (intervals, harmonics)
    2. Checks for impossible note combinations
    3. Validates key/scale consistency
    4. Removes obviously wrong notes
    5. Assigns final confidence scores
    """

    def __init__(
        self,
        pass_number: int = 7,
        check_intervals: bool = True,
        check_key_consistency: bool = True,
        check_temporal_patterns: bool = True,
        remove_outliers: bool = True,
        min_final_confidence: float = 0.3,
        dissonance_tolerance: float = 0.3,
    ):
        """Initialize musicality check pass.

        Args:
            pass_number: Pass number in pipeline
            check_intervals: Validate interval relationships
            check_key_consistency: Check key/scale consistency
            check_temporal_patterns: Validate temporal patterns
            remove_outliers: Remove notes that are clear outliers
            min_final_confidence: Minimum confidence for final output
            dissonance_tolerance: How much dissonance to allow (0-1)
        """
        super().__init__(pass_number)
        self.check_intervals = check_intervals
        self.check_key_consistency = check_key_consistency
        self.check_temporal_patterns = check_temporal_patterns
        self.remove_outliers = remove_outliers
        self.min_final_confidence = min_final_confidence
        self.dissonance_tolerance = dissonance_tolerance

    @property
    def name(self) -> str:
        return "musicality_check"

    def process(
        self,
        notes: List[ExtractedNote],
        context: ExtractionContext,
    ) -> PassResult:
        """Validate musical coherence of notes.

        Args:
            notes: Input notes
            context: Extraction context

        Returns:
            PassResult with validated notes
        """
        start_time = time.time()

        if len(notes) < 2:
            return PassResult(
                notes=notes,
                statistics=self._create_statistics(notes, notes, 0.0),
                warnings=["Too few notes for musicality check"],
            )

        warnings = []
        validated_notes = notes.copy()

        # Step 1: Detect key/scale
        detected_key, key_confidence = self._detect_key(notes, context)

        # Step 2: Check interval relationships
        if self.check_intervals:
            validated_notes, interval_warnings = self._check_intervals(
                validated_notes, context
            )
            warnings.extend(interval_warnings)

        # Step 3: Check key consistency
        if self.check_key_consistency and detected_key:
            validated_notes, key_warnings = self._check_key_consistency(
                validated_notes, detected_key, key_confidence
            )
            warnings.extend(key_warnings)

        # Step 4: Check temporal patterns
        if self.check_temporal_patterns:
            validated_notes, temporal_warnings = self._check_temporal_patterns(
                validated_notes, context
            )
            warnings.extend(temporal_warnings)

        # Step 5: Remove outliers
        if self.remove_outliers:
            validated_notes, outlier_warnings = self._remove_outliers(
                validated_notes, context
            )
            warnings.extend(outlier_warnings)

        # Step 6: Calculate final confidence scores
        validated_notes = self._calculate_final_confidence(
            validated_notes, detected_key, context
        )

        # Step 7: Filter by minimum confidence
        final_notes = [
            n for n in validated_notes
            if n.confidence >= self.min_final_confidence
        ]

        if len(final_notes) < len(validated_notes):
            warnings.append(
                f"Removed {len(validated_notes) - len(final_notes)} notes "
                f"below confidence threshold {self.min_final_confidence}"
            )

        execution_time = (time.time() - start_time) * 1000

        stats = self._create_statistics(
            notes,
            final_notes,
            execution_time,
            detected_key=detected_key,
            key_confidence=key_confidence,
        )

        return PassResult(
            notes=final_notes,
            statistics=stats,
            warnings=warnings,
            metadata={
                "detected_key": detected_key,
                "key_confidence": key_confidence,
            },
        )

    def _detect_key(
        self,
        notes: List[ExtractedNote],
        context: ExtractionContext,
    ) -> Tuple[Optional[Tuple[int, str]], float]:
        """Detect the most likely key from notes.

        Returns (root, mode) tuple and confidence.
        """
        if context.key:
            return context.key, 1.0

        if len(notes) < 4:
            return None, 0.0

        # Count pitch classes weighted by duration and confidence
        pitch_class_weights = [0.0] * 12

        for note in notes:
            pc = note.pitch % 12
            weight = note.duration * note.confidence
            pitch_class_weights[pc] += weight

        # Normalize
        total = sum(pitch_class_weights)
        if total == 0:
            return None, 0.0

        pitch_class_weights = [w / total for w in pitch_class_weights]

        # Major and minor scale templates
        major_template = [1, 0, 1, 0, 1, 1, 0, 1, 0, 1, 0, 1]  # C major
        minor_template = [1, 0, 1, 1, 0, 1, 0, 1, 1, 0, 1, 0]  # C minor

        best_key = None
        best_score = 0.0

        for root in range(12):
            # Rotate templates
            major_rotated = major_template[-root:] + major_template[:-root]
            minor_rotated = minor_template[-root:] + minor_template[:-root]

            # Calculate correlation
            major_score = sum(
                w * t for w, t in zip(pitch_class_weights, major_rotated)
            )
            minor_score = sum(
                w * t for w, t in zip(pitch_class_weights, minor_rotated)
            )

            if major_score > best_score:
                best_score = major_score
                best_key = (root, "major")

            if minor_score > best_score:
                best_score = minor_score
                best_key = (root, "minor")

        # Confidence based on how well notes fit the detected key
        confidence = min(1.0, best_score * 1.5)

        return best_key, confidence

    def _check_intervals(
        self,
        notes: List[ExtractedNote],
        context: ExtractionContext,
    ) -> Tuple[List[ExtractedNote], List[str]]:
        """Check interval relationships between simultaneous notes."""
        warnings = []
        validated = []

        # Group notes by time (simultaneous notes)
        time_groups = self._group_by_time(notes, tolerance=0.05)

        dissonance_count = 0

        for group in time_groups:
            if len(group) < 2:
                validated.extend(group)
                continue

            # Check intervals between notes in group
            pitches = sorted(n.pitch for n in group)
            intervals = [pitches[i + 1] - pitches[i] for i in range(len(pitches) - 1)]

            # Count dissonant intervals
            dissonant = sum(1 for i in intervals if (i % 12) in DISSONANT_INTERVALS)
            dissonance_ratio = dissonant / len(intervals) if intervals else 0

            if dissonance_ratio > self.dissonance_tolerance:
                dissonance_count += 1
                # Flag lowest-confidence note in group
                lowest_conf = min(group, key=lambda n: n.confidence)
                lowest_conf.flags.add(NoteFlag.LOW_CONFIDENCE)

            validated.extend(group)

        if dissonance_count > 0:
            warnings.append(
                f"Found {dissonance_count} groups with high dissonance"
            )

        return validated, warnings

    def _check_key_consistency(
        self,
        notes: List[ExtractedNote],
        detected_key: Tuple[int, str],
        key_confidence: float,
    ) -> Tuple[List[ExtractedNote], List[str]]:
        """Check if notes are consistent with detected key."""
        warnings = []
        validated = []

        root, mode = detected_key

        # Build scale
        if mode == "major":
            scale_intervals = [0, 2, 4, 5, 7, 9, 11]
        else:  # minor
            scale_intervals = [0, 2, 3, 5, 7, 8, 10]

        scale_pcs = set((root + i) % 12 for i in scale_intervals)

        out_of_key_count = 0

        for note in notes:
            pc = note.pitch % 12

            if pc not in scale_pcs:
                out_of_key_count += 1

                # Reduce confidence for out-of-key notes
                confidence_penalty = 0.2 * key_confidence
                new_confidence = max(0.1, note.confidence - confidence_penalty)
                note = replace(note, confidence=new_confidence)

            validated.append(note)

        if out_of_key_count > 0:
            key_name = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"][root]
            warnings.append(
                f"{out_of_key_count} notes outside detected key ({key_name} {mode})"
            )

        return validated, warnings

    def _check_temporal_patterns(
        self,
        notes: List[ExtractedNote],
        context: ExtractionContext,
    ) -> Tuple[List[ExtractedNote], List[str]]:
        """Check for temporal pattern anomalies."""
        warnings = []
        validated = []

        if len(notes) < 3:
            return notes, warnings

        # Sort by start time
        sorted_notes = sorted(notes, key=lambda n: n.start)

        # Calculate inter-onset intervals
        iois = []
        for i in range(len(sorted_notes) - 1):
            ioi = sorted_notes[i + 1].start - sorted_notes[i].start
            if ioi > 0:
                iois.append(ioi)

        if not iois:
            return notes, warnings

        # Detect anomalous IOIs (way outside normal distribution)
        ioi_mean = np.mean(iois)
        ioi_std = np.std(iois)

        if ioi_std > 0:
            anomaly_count = 0
            for i, note in enumerate(sorted_notes):
                if i > 0:
                    prev_note = sorted_notes[i - 1]
                    ioi = note.start - prev_note.start

                    # Check if IOI is anomalous (> 3 std from mean)
                    if abs(ioi - ioi_mean) > 3 * ioi_std:
                        anomaly_count += 1
                        note.flags.add(NoteFlag.LOW_CONFIDENCE)

                validated.append(note)

            if anomaly_count > 0:
                warnings.append(
                    f"{anomaly_count} notes with anomalous timing"
                )
        else:
            validated = list(sorted_notes)

        return validated, warnings

    def _remove_outliers(
        self,
        notes: List[ExtractedNote],
        context: ExtractionContext,
    ) -> Tuple[List[ExtractedNote], List[str]]:
        """Remove notes that are clear outliers."""
        warnings = []

        if len(notes) < 5:
            return notes, warnings

        # Calculate pitch statistics
        pitches = [n.pitch for n in notes]
        pitch_mean = np.mean(pitches)
        pitch_std = np.std(pitches)

        # Calculate velocity statistics
        velocities = [n.velocity for n in notes]
        vel_mean = np.mean(velocities)
        vel_std = np.std(velocities)

        filtered = []
        removed_count = 0

        for note in notes:
            is_outlier = False

            # Pitch outlier (> 3 std from mean)
            if pitch_std > 0 and abs(note.pitch - pitch_mean) > 3 * pitch_std:
                is_outlier = True

            # Velocity outlier with low confidence
            if vel_std > 0 and abs(note.velocity - vel_mean) > 3 * vel_std:
                if note.confidence < 0.5:
                    is_outlier = True

            # Very short notes with low confidence
            if note.duration_ms < 30 and note.confidence < 0.5:
                is_outlier = True

            if is_outlier:
                removed_count += 1
            else:
                filtered.append(note)

        if removed_count > 0:
            warnings.append(f"Removed {removed_count} outlier notes")

        return filtered, warnings

    def _calculate_final_confidence(
        self,
        notes: List[ExtractedNote],
        detected_key: Optional[Tuple[int, str]],
        context: ExtractionContext,
    ) -> List[ExtractedNote]:
        """Calculate final confidence scores for all notes."""
        final_notes = []

        for note in notes:
            confidence = note.confidence

            # Penalty for low-confidence flags
            if NoteFlag.LOW_CONFIDENCE in note.flags:
                confidence *= 0.7

            # Bonus for high-pass notes (harmonic recovery, etc.)
            if note.source_pass <= 2 and NoteFlag.ORIGINAL in note.flags:
                confidence = min(1.0, confidence * 1.1)

            # Bonus for notes fitting the key
            if detected_key:
                root, mode = detected_key
                if mode == "major":
                    scale_intervals = [0, 2, 4, 5, 7, 9, 11]
                else:
                    scale_intervals = [0, 2, 3, 5, 7, 8, 10]

                scale_pcs = set((root + i) % 12 for i in scale_intervals)

                if (note.pitch % 12) in scale_pcs:
                    confidence = min(1.0, confidence * 1.05)

            # Update note with final confidence
            final_notes.append(replace(note, confidence=confidence))

        return final_notes

    def _group_by_time(
        self,
        notes: List[ExtractedNote],
        tolerance: float = 0.05,
    ) -> List[List[ExtractedNote]]:
        """Group notes that occur at roughly the same time."""
        if not notes:
            return []

        sorted_notes = sorted(notes, key=lambda n: n.start)
        groups = []
        current_group = [sorted_notes[0]]

        for i in range(1, len(sorted_notes)):
            if sorted_notes[i].start - current_group[0].start < tolerance:
                current_group.append(sorted_notes[i])
            else:
                groups.append(current_group)
                current_group = [sorted_notes[i]]

        groups.append(current_group)

        return groups
