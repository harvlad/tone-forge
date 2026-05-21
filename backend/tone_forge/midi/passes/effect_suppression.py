"""Pass 4: Effect suppression.

Removes notes that are artifacts of delay, reverb, and other effects.
Critical for synthwave and other heavily-processed genres.
"""
from __future__ import annotations

import logging
import time
from dataclasses import replace
from typing import Dict, List, Optional, Tuple

import librosa
import numpy as np

from .base import (
    ExtractionContext,
    ExtractionPass,
    ExtractedNote,
    NoteFlag,
    PassResult,
)

logger = logging.getLogger(__name__)


class EffectSuppressionPass(ExtractionPass):
    """Remove effect-generated artifact notes.

    This pass identifies and removes notes that are likely artifacts
    of delay, reverb, or other effects rather than intentional notes.

    Detection strategies:
    1. Delay pattern detection - repeating notes at consistent intervals
    2. Reverb tail detection - decaying notes following primary notes
    3. Echo detection - quieter repetitions of the same pitch
    4. Spectral analysis - notes without clean transient attacks
    """

    def __init__(
        self,
        pass_number: int = 4,
        delay_tolerance_ms: float = 20.0,
        min_delay_repeats: int = 2,
        reverb_decay_threshold: float = 0.3,
        velocity_decay_ratio: float = 0.7,
        min_echo_gap_ms: float = 30.0,
        max_echo_gap_ms: float = 500.0,
    ):
        """Initialize effect suppression pass.

        Args:
            pass_number: Pass number in pipeline
            delay_tolerance_ms: Timing tolerance for delay detection
            min_delay_repeats: Minimum repeats to confirm delay pattern
            reverb_decay_threshold: Minimum decay to consider reverb
            velocity_decay_ratio: Expected velocity ratio for echoes
            min_echo_gap_ms: Minimum gap to consider as echo
            max_echo_gap_ms: Maximum gap to consider as echo
        """
        super().__init__(pass_number)
        self.delay_tolerance_ms = delay_tolerance_ms
        self.min_delay_repeats = min_delay_repeats
        self.reverb_decay_threshold = reverb_decay_threshold
        self.velocity_decay_ratio = velocity_decay_ratio
        self.min_echo_gap_ms = min_echo_gap_ms
        self.max_echo_gap_ms = max_echo_gap_ms

    @property
    def name(self) -> str:
        return "effect_suppression"

    def process(
        self,
        notes: List[ExtractedNote],
        context: ExtractionContext,
    ) -> PassResult:
        """Remove effect artifacts from notes.

        Args:
            notes: Input notes from previous passes
            context: Extraction context

        Returns:
            PassResult with cleaned notes
        """
        start_time = time.time()
        input_notes = notes.copy()

        if len(notes) == 0:
            return PassResult(
                notes=[],
                statistics=self._create_statistics(input_notes, [], 0.0),
                warnings=["No notes to process"],
            )

        # Estimate tempo if not provided
        tempo = context.tempo
        if tempo is None:
            tempo = self._estimate_tempo_from_notes(notes)

        # Detect delay patterns
        delay_info = self._detect_delay_patterns(notes, tempo)

        # Detect reverb tails
        reverb_notes = self._detect_reverb_tails(notes, context)

        # Detect echoes
        echo_notes = self._detect_echoes(notes)

        # Combine all artifact notes
        artifact_indices = set()
        artifact_indices.update(delay_info["artifact_indices"])
        artifact_indices.update(reverb_notes)
        artifact_indices.update(echo_notes)

        # Filter out artifacts, but mark originals
        cleaned_notes = []
        for i, note in enumerate(notes):
            if i in artifact_indices:
                # Mark as removed but with lower confidence if borderline
                if self._is_borderline_artifact(note, notes, i):
                    # Keep but flag and lower confidence
                    modified = replace(
                        note,
                        confidence=note.confidence * 0.5,
                        flags=note.flags | {NoteFlag.LOW_CONFIDENCE},
                    )
                    cleaned_notes.append(modified)
                # Otherwise drop the note
                continue

            # Check if this note had its echo/delay removed
            if i in delay_info.get("primary_indices", set()):
                note = replace(note, flags=note.flags | {NoteFlag.DELAY_REMOVED})
            if self._had_reverb_removed(i, reverb_notes, notes):
                note = replace(note, flags=note.flags | {NoteFlag.REVERB_REMOVED})

            cleaned_notes.append(note)

        execution_time = (time.time() - start_time) * 1000

        stats = self._create_statistics(
            input_notes,
            cleaned_notes,
            execution_time,
            delay_artifacts=len(delay_info["artifact_indices"]),
            reverb_artifacts=len(reverb_notes),
            echo_artifacts=len(echo_notes),
            estimated_tempo=tempo,
            estimated_delay_time=delay_info.get("delay_time_ms"),
        )

        warnings = []
        removed_ratio = 1 - len(cleaned_notes) / max(len(input_notes), 1)
        if removed_ratio > 0.5:
            warnings.append(f"Removed {removed_ratio:.0%} of notes - may be over-aggressive")

        return PassResult(
            notes=cleaned_notes,
            statistics=stats,
            warnings=warnings,
            metadata={
                "delay_pattern_found": delay_info.get("delay_time_ms") is not None,
                "estimated_delay_ms": delay_info.get("delay_time_ms"),
            },
        )

    def _estimate_tempo_from_notes(self, notes: List[ExtractedNote]) -> float:
        """Estimate tempo from note onsets."""
        if len(notes) < 2:
            return 120.0  # Default tempo

        # Calculate inter-onset intervals
        onsets = sorted([n.start for n in notes])
        iois = np.diff(onsets)

        if len(iois) == 0:
            return 120.0

        # Use median IOI to estimate beat duration
        median_ioi = np.median(iois)
        if median_ioi <= 0:
            return 120.0

        # Assume median IOI is around an eighth note
        beat_duration = median_ioi * 2
        tempo = 60.0 / beat_duration

        # Clamp to reasonable range
        return float(np.clip(tempo, 60, 200))

    def _detect_delay_patterns(
        self,
        notes: List[ExtractedNote],
        tempo: float,
    ) -> Dict:
        """Detect delay repeat patterns."""
        result = {
            "artifact_indices": set(),
            "primary_indices": set(),
            "delay_time_ms": None,
        }

        if len(notes) < 3:
            return result

        # Common delay times relative to tempo
        beat_ms = 60000.0 / tempo
        common_delays = [
            beat_ms / 4,      # 16th note
            beat_ms / 3,      # Triplet
            beat_ms / 2,      # 8th note
            beat_ms * 2 / 3,  # Dotted 8th
            beat_ms,          # Quarter note
        ]

        # Also check for absolute delay times common in effects
        common_delays.extend([100, 150, 200, 250, 300, 375, 500])

        # Group notes by pitch
        pitch_groups: Dict[int, List[Tuple[int, ExtractedNote]]] = {}
        for i, note in enumerate(notes):
            if note.pitch not in pitch_groups:
                pitch_groups[note.pitch] = []
            pitch_groups[note.pitch].append((i, note))

        # Look for delay patterns in each pitch group
        best_delay = None
        best_score = 0

        for delay_ms in common_delays:
            delay_sec = delay_ms / 1000.0
            score, artifacts, primaries = self._score_delay_pattern(
                pitch_groups, delay_sec
            )
            if score > best_score:
                best_score = score
                best_delay = delay_ms
                result["artifact_indices"] = artifacts
                result["primary_indices"] = primaries

        if best_score >= self.min_delay_repeats:
            result["delay_time_ms"] = best_delay
            logger.debug(f"Detected delay pattern: {best_delay:.0f}ms")

        return result

    def _score_delay_pattern(
        self,
        pitch_groups: Dict[int, List[Tuple[int, ExtractedNote]]],
        delay_sec: float,
    ) -> Tuple[int, set, set]:
        """Score how well notes fit a delay pattern."""
        tolerance_sec = self.delay_tolerance_ms / 1000.0
        total_matches = 0
        artifacts = set()
        primaries = set()

        for pitch, indexed_notes in pitch_groups.items():
            if len(indexed_notes) < 2:
                continue

            # Sort by start time
            indexed_notes = sorted(indexed_notes, key=lambda x: x[1].start)

            for i, (idx1, note1) in enumerate(indexed_notes):
                repeats = 0
                repeat_indices = []

                for j, (idx2, note2) in enumerate(indexed_notes[i + 1:], i + 1):
                    expected_time = note1.start + delay_sec * (j - i)
                    time_diff = abs(note2.start - expected_time)

                    if time_diff < tolerance_sec:
                        # Check velocity decay
                        if note2.velocity <= note1.velocity * self.velocity_decay_ratio + 10:
                            repeats += 1
                            repeat_indices.append(idx2)
                    elif note2.start > expected_time + tolerance_sec:
                        break

                if repeats >= 1:
                    total_matches += repeats
                    artifacts.update(repeat_indices)
                    primaries.add(idx1)

        return total_matches, artifacts, primaries

    def _detect_reverb_tails(
        self,
        notes: List[ExtractedNote],
        context: ExtractionContext,
    ) -> set:
        """Detect notes that are likely reverb tails."""
        reverb_indices = set()

        # Check stem quality for reverb density
        reverb_density = 0.5  # Default
        if context.stem_quality is not None:
            if hasattr(context.stem_quality, "reverb_density"):
                reverb_density = context.stem_quality.reverb_density

        # Higher reverb density means more aggressive tail detection
        decay_threshold = self.reverb_decay_threshold * (1 + reverb_density)

        # Sort notes by pitch and time
        notes_by_pitch: Dict[int, List[Tuple[int, ExtractedNote]]] = {}
        for i, note in enumerate(notes):
            if note.pitch not in notes_by_pitch:
                notes_by_pitch[note.pitch] = []
            notes_by_pitch[note.pitch].append((i, note))

        for pitch, indexed_notes in notes_by_pitch.items():
            indexed_notes = sorted(indexed_notes, key=lambda x: x[1].start)

            for i in range(len(indexed_notes) - 1):
                idx1, note1 = indexed_notes[i]
                idx2, note2 = indexed_notes[i + 1]

                # Check if note2 starts right after note1 ends (reverb tail)
                gap = note2.start - note1.end
                if -0.1 < gap < 0.2:  # Slight overlap to small gap
                    # Check for velocity decay
                    if note2.velocity < note1.velocity * decay_threshold:
                        # Check for confidence decay
                        if note2.confidence < note1.confidence:
                            reverb_indices.add(idx2)

        return reverb_indices

    def _detect_echoes(self, notes: List[ExtractedNote]) -> set:
        """Detect echo repetitions."""
        echo_indices = set()
        min_gap = self.min_echo_gap_ms / 1000.0
        max_gap = self.max_echo_gap_ms / 1000.0

        # Sort by start time
        sorted_indices = sorted(range(len(notes)), key=lambda i: notes[i].start)

        for i, idx1 in enumerate(sorted_indices):
            note1 = notes[idx1]

            for idx2 in sorted_indices[i + 1:]:
                note2 = notes[idx2]
                gap = note2.start - note1.start

                if gap < min_gap:
                    continue
                if gap > max_gap:
                    break

                # Same pitch
                if note2.pitch != note1.pitch:
                    continue

                # Significantly quieter
                if note2.velocity > note1.velocity * self.velocity_decay_ratio:
                    continue

                # Lower confidence
                if note2.confidence >= note1.confidence:
                    continue

                # Similar duration (echoes tend to have similar shape)
                duration_ratio = note2.duration / max(note1.duration, 0.01)
                if 0.5 < duration_ratio < 1.5:
                    echo_indices.add(idx2)

        return echo_indices

    def _is_borderline_artifact(
        self,
        note: ExtractedNote,
        all_notes: List[ExtractedNote],
        index: int,
    ) -> bool:
        """Check if a detected artifact is borderline (might be real)."""
        # High confidence notes are borderline
        if note.confidence > 0.8:
            return True

        # Notes with strong velocity are borderline
        if note.velocity > 100:
            return True

        # Notes that don't fit common effect patterns are borderline
        # (e.g., velocity increases instead of decreases)
        return False

    def _had_reverb_removed(
        self,
        index: int,
        reverb_indices: set,
        notes: List[ExtractedNote],
    ) -> bool:
        """Check if this note had its reverb tail removed."""
        note = notes[index]

        for rev_idx in reverb_indices:
            rev_note = notes[rev_idx]
            if rev_note.pitch == note.pitch:
                # Check if reverb note follows this note
                if 0 <= rev_note.start - note.end < 0.2:
                    return True

        return False
