"""Pass 3: Phrase grouping and detection.

Groups notes into musical phrases based on timing, pitch contour,
and rhythmic patterns. Phrase information helps subsequent passes
make better decisions about note validity.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
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


@dataclass
class Phrase:
    """A musical phrase - a group of related notes."""

    notes: List[ExtractedNote]
    start: float
    end: float
    phrase_id: int
    confidence: float = 1.0
    phrase_type: str = "melodic"  # melodic, harmonic, rhythmic, sustained

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def note_count(self) -> int:
        return len(self.notes)

    @property
    def average_pitch(self) -> float:
        if not self.notes:
            return 0
        return sum(n.pitch for n in self.notes) / len(self.notes)

    @property
    def pitch_range(self) -> int:
        if not self.notes:
            return 0
        pitches = [n.pitch for n in self.notes]
        return max(pitches) - min(pitches)

    def to_dict(self) -> dict:
        return {
            "phrase_id": self.phrase_id,
            "start": self.start,
            "end": self.end,
            "duration": self.duration,
            "note_count": self.note_count,
            "phrase_type": self.phrase_type,
            "confidence": self.confidence,
            "average_pitch": self.average_pitch,
            "pitch_range": self.pitch_range,
        }


class PhraseGroupingPass(ExtractionPass):
    """Group notes into musical phrases.

    This pass identifies phrase boundaries and groups notes accordingly.
    Phrase information is used by later passes for:
    - Validating note timing against phrase structure
    - Detecting anomalous notes
    - Informing quantization decisions
    - Genre-specific phrase pattern matching
    """

    def __init__(
        self,
        pass_number: int = 3,
        gap_threshold_ms: float = 300.0,
        min_phrase_notes: int = 2,
        max_phrase_duration: float = 16.0,
        merge_overlapping: bool = True,
        detect_repeated_phrases: bool = True,
    ):
        """Initialize phrase grouping pass.

        Args:
            pass_number: Pass number in pipeline
            gap_threshold_ms: Gap size that indicates phrase boundary
            min_phrase_notes: Minimum notes to form a phrase
            max_phrase_duration: Maximum phrase duration in seconds
            merge_overlapping: Merge notes into harmonic phrases
            detect_repeated_phrases: Detect repeating phrase patterns
        """
        super().__init__(pass_number)
        self.gap_threshold_ms = gap_threshold_ms
        self.min_phrase_notes = min_phrase_notes
        self.max_phrase_duration = max_phrase_duration
        self.merge_overlapping = merge_overlapping
        self.detect_repeated_phrases = detect_repeated_phrases

    @property
    def name(self) -> str:
        return "phrase_grouping"

    def process(
        self,
        notes: List[ExtractedNote],
        context: ExtractionContext,
    ) -> PassResult:
        """Group notes into phrases.

        Args:
            notes: Input notes
            context: Extraction context

        Returns:
            PassResult with notes annotated with phrase info
        """
        start_time = time.time()

        if len(notes) < self.min_phrase_notes:
            return PassResult(
                notes=notes,
                statistics=self._create_statistics(notes, notes, 0.0),
                warnings=["Too few notes for phrase detection"],
            )

        # Adapt thresholds for genre/stem type
        gap_threshold = self._adapt_gap_threshold(context)

        # Step 1: Initial phrase detection based on timing
        phrases = self._detect_phrases_by_timing(notes, gap_threshold)

        # Step 2: Classify phrase types
        phrases = self._classify_phrases(phrases, context)

        # Step 3: Detect harmonic phrases (overlapping notes)
        if self.merge_overlapping:
            phrases = self._detect_harmonic_phrases(notes, phrases)

        # Step 4: Detect repeated phrase patterns
        if self.detect_repeated_phrases:
            repeated = self._detect_repeated_patterns(phrases)

        # Step 5: Annotate notes with phrase information
        annotated_notes = self._annotate_notes_with_phrases(notes, phrases)

        # Step 6: Infer missing phrase notes
        inferred_notes = self._infer_phrase_notes(phrases, annotated_notes, context)

        # Combine all notes
        all_notes = annotated_notes + inferred_notes

        execution_time = (time.time() - start_time) * 1000

        stats = self._create_statistics(
            notes,
            all_notes,
            execution_time,
            phrase_count=len(phrases),
            melodic_phrases=len([p for p in phrases if p.phrase_type == "melodic"]),
            harmonic_phrases=len([p for p in phrases if p.phrase_type == "harmonic"]),
            sustained_phrases=len([p for p in phrases if p.phrase_type == "sustained"]),
        )

        return PassResult(
            notes=all_notes,
            statistics=stats,
            metadata={
                "phrases": [p.to_dict() for p in phrases],
                "gap_threshold_ms": gap_threshold,
            },
        )

    def _adapt_gap_threshold(self, context: ExtractionContext) -> float:
        """Adapt gap threshold based on context."""
        base_threshold = self.gap_threshold_ms

        # Adjust for tempo
        if context.tempo:
            # Faster tempo = shorter gaps between phrases
            tempo_factor = 120 / context.tempo
            base_threshold *= tempo_factor

        # Adjust for stem type
        if context.stem_type:
            stem = context.stem_type.lower()
            if stem in ["pad", "synth"]:
                # Pads have longer gaps
                base_threshold *= 1.5
            elif stem == "bass":
                # Bass lines are more continuous
                base_threshold *= 0.8
            elif stem in ["lead", "melody"]:
                # Melodies have clear phrase breaks
                base_threshold *= 1.2

        # Adjust for role
        if context.role_classification:
            role = getattr(context.role_classification, "primary_role", "")
            if role == "pad_atmosphere":
                base_threshold *= 2.0
            elif role == "arp_rhythm":
                base_threshold *= 0.6

        return max(100.0, min(base_threshold, 2000.0))

    def _detect_phrases_by_timing(
        self,
        notes: List[ExtractedNote],
        gap_threshold_ms: float,
    ) -> List[Phrase]:
        """Detect phrases based on timing gaps."""
        if not notes:
            return []

        # Sort by start time
        sorted_notes = sorted(notes, key=lambda n: n.start)
        gap_threshold_sec = gap_threshold_ms / 1000.0

        phrases = []
        current_phrase_notes = [sorted_notes[0]]
        phrase_id = 0

        for i in range(1, len(sorted_notes)):
            prev_note = sorted_notes[i - 1]
            curr_note = sorted_notes[i]

            gap = curr_note.start - prev_note.end

            if gap > gap_threshold_sec or curr_note.start - current_phrase_notes[0].start > self.max_phrase_duration:
                # End current phrase and start new one
                if len(current_phrase_notes) >= self.min_phrase_notes:
                    phrase = Phrase(
                        notes=current_phrase_notes.copy(),
                        start=current_phrase_notes[0].start,
                        end=current_phrase_notes[-1].end,
                        phrase_id=phrase_id,
                    )
                    phrases.append(phrase)
                    phrase_id += 1

                current_phrase_notes = [curr_note]
            else:
                current_phrase_notes.append(curr_note)

        # Add final phrase
        if len(current_phrase_notes) >= self.min_phrase_notes:
            phrase = Phrase(
                notes=current_phrase_notes,
                start=current_phrase_notes[0].start,
                end=current_phrase_notes[-1].end,
                phrase_id=phrase_id,
            )
            phrases.append(phrase)

        return phrases

    def _classify_phrases(
        self,
        phrases: List[Phrase],
        context: ExtractionContext,
    ) -> List[Phrase]:
        """Classify each phrase by type."""
        for phrase in phrases:
            phrase.phrase_type = self._determine_phrase_type(phrase, context)

        return phrases

    def _determine_phrase_type(
        self,
        phrase: Phrase,
        context: ExtractionContext,
    ) -> str:
        """Determine the type of a phrase."""
        notes = phrase.notes

        if len(notes) < 2:
            return "melodic"

        # Check for sustained (long notes, small pitch range)
        avg_duration = sum(n.duration for n in notes) / len(notes)
        pitch_range = phrase.pitch_range

        if avg_duration > 2.0 and pitch_range <= 12:
            return "sustained"

        # Check for harmonic (many overlapping notes)
        overlap_count = 0
        for i, n1 in enumerate(notes):
            for n2 in notes[i + 1:]:
                if n1.start < n2.end and n1.end > n2.start:
                    overlap_count += 1

        if overlap_count > len(notes) // 2:
            return "harmonic"

        # Check for rhythmic (regular timing, percussive)
        if len(notes) >= 4:
            intervals = []
            sorted_notes = sorted(notes, key=lambda n: n.start)
            for i in range(len(sorted_notes) - 1):
                intervals.append(sorted_notes[i + 1].start - sorted_notes[i].start)

            if intervals:
                interval_std = np.std(intervals)
                interval_mean = np.mean(intervals)

                # Low variance = rhythmic
                if interval_mean > 0 and interval_std / interval_mean < 0.3:
                    return "rhythmic"

        return "melodic"

    def _detect_harmonic_phrases(
        self,
        notes: List[ExtractedNote],
        existing_phrases: List[Phrase],
    ) -> List[Phrase]:
        """Detect harmonic phrases (chords/stacks)."""
        # Find simultaneous note groups
        sorted_notes = sorted(notes, key=lambda n: n.start)

        harmonic_groups = []
        current_group = [sorted_notes[0]] if sorted_notes else []

        for i in range(1, len(sorted_notes)):
            curr = sorted_notes[i]
            prev = sorted_notes[i - 1]

            # Notes starting within 50ms of each other are simultaneous
            if abs(curr.start - prev.start) < 0.05:
                current_group.append(curr)
            else:
                if len(current_group) >= 2:
                    harmonic_groups.append(current_group)
                current_group = [curr]

        if len(current_group) >= 2:
            harmonic_groups.append(current_group)

        # Create harmonic phrases that don't overlap with existing
        next_id = max((p.phrase_id for p in existing_phrases), default=-1) + 1

        for group in harmonic_groups:
            # Check if this overlaps significantly with existing phrase
            group_start = min(n.start for n in group)
            group_end = max(n.end for n in group)

            overlaps = False
            for phrase in existing_phrases:
                if phrase.start <= group_start <= phrase.end:
                    overlaps = True
                    break

            if not overlaps and len(group) >= 2:
                harmonic_phrase = Phrase(
                    notes=group,
                    start=group_start,
                    end=group_end,
                    phrase_id=next_id,
                    phrase_type="harmonic",
                )
                existing_phrases.append(harmonic_phrase)
                next_id += 1

        return existing_phrases

    def _detect_repeated_patterns(
        self,
        phrases: List[Phrase],
    ) -> List[Tuple[int, int, float]]:
        """Detect repeated phrase patterns.

        Returns list of (phrase_id_1, phrase_id_2, similarity) tuples.
        """
        repeated = []

        for i, p1 in enumerate(phrases):
            for p2 in phrases[i + 1:]:
                similarity = self._phrase_similarity(p1, p2)
                if similarity > 0.7:  # Threshold for "repeated"
                    repeated.append((p1.phrase_id, p2.phrase_id, similarity))

        return repeated

    def _phrase_similarity(self, p1: Phrase, p2: Phrase) -> float:
        """Calculate similarity between two phrases."""
        # Compare duration
        dur_ratio = min(p1.duration, p2.duration) / max(p1.duration, p2.duration)

        # Compare note count
        count_ratio = min(p1.note_count, p2.note_count) / max(p1.note_count, p2.note_count)

        # Compare pitch range
        range_diff = abs(p1.pitch_range - p2.pitch_range)
        range_score = 1.0 / (1.0 + range_diff / 12)

        # Compare average pitch (allowing transposition)
        pitch_diff = abs(p1.average_pitch - p2.average_pitch) % 12
        pitch_score = 1.0 - (pitch_diff / 12)

        # Weight and combine
        similarity = (
            dur_ratio * 0.3 +
            count_ratio * 0.3 +
            range_score * 0.2 +
            pitch_score * 0.2
        )

        return similarity

    def _annotate_notes_with_phrases(
        self,
        notes: List[ExtractedNote],
        phrases: List[Phrase],
    ) -> List[ExtractedNote]:
        """Annotate notes with their phrase information."""
        annotated = []

        for note in notes:
            # Find phrase containing this note
            containing_phrase = None
            for phrase in phrases:
                if note in phrase.notes:
                    containing_phrase = phrase
                    break

            # Add phrase info to note's harmonic context
            if containing_phrase:
                context = note.harmonic_context or {}
                context["phrase_id"] = containing_phrase.phrase_id
                context["phrase_type"] = containing_phrase.phrase_type

                from dataclasses import replace
                note = replace(note, harmonic_context=context)

            annotated.append(note)

        return annotated

    def _infer_phrase_notes(
        self,
        phrases: List[Phrase],
        existing_notes: List[ExtractedNote],
        context: ExtractionContext,
    ) -> List[ExtractedNote]:
        """Infer missing notes based on phrase patterns.

        If a phrase has a clear rhythmic pattern with a gap,
        we can infer that a note might be missing.
        """
        inferred = []

        for phrase in phrases:
            if phrase.phrase_type != "rhythmic":
                continue

            if len(phrase.notes) < 4:
                continue

            # Analyze timing pattern
            sorted_notes = sorted(phrase.notes, key=lambda n: n.start)
            intervals = [
                sorted_notes[i + 1].start - sorted_notes[i].start
                for i in range(len(sorted_notes) - 1)
            ]

            if not intervals:
                continue

            # Find expected interval (mode)
            median_interval = np.median(intervals)

            # Look for gaps that are approximately 2x the expected interval
            for i in range(len(intervals)):
                if intervals[i] > median_interval * 1.8 and intervals[i] < median_interval * 2.2:
                    # There might be a missing note
                    prev_note = sorted_notes[i]
                    next_note = sorted_notes[i + 1]

                    # Estimate missing note properties
                    missing_start = prev_note.start + median_interval
                    missing_end = missing_start + (next_note.end - next_note.start)

                    # Check if this position already has a note
                    has_note = any(
                        abs(n.start - missing_start) < 0.05
                        for n in existing_notes
                    )

                    if not has_note:
                        # Interpolate pitch
                        pitch = int((prev_note.pitch + next_note.pitch) / 2)

                        inferred_note = ExtractedNote(
                            pitch=pitch,
                            start=missing_start,
                            end=min(missing_end, next_note.start - 0.01),
                            velocity=int((prev_note.velocity + next_note.velocity) / 2),
                            confidence=0.4,  # Low confidence for inferred
                            source_pass=self.pass_number,
                            flags={NoteFlag.PHRASE_INFERRED},
                            harmonic_context={
                                "phrase_id": phrase.phrase_id,
                                "phrase_type": phrase.phrase_type,
                                "inferred_from_pattern": True,
                            },
                        )
                        inferred.append(inferred_note)

        return inferred
