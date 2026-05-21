"""Pass 6: Confidence-aware quantization.

Snaps notes to a musical grid with strength proportional to confidence.
High-confidence notes get stronger quantization; uncertain notes preserve
their original timing.
"""
from __future__ import annotations

import logging
import time
from dataclasses import replace
from typing import List, Optional, Tuple

import numpy as np

from .base import (
    ExtractionContext,
    ExtractionPass,
    ExtractedNote,
    NoteFlag,
    PassResult,
)

logger = logging.getLogger(__name__)


class ConfidenceQuantizationPass(ExtractionPass):
    """Quantize notes to grid based on confidence.

    This pass performs musical quantization with strength inversely
    proportional to uncertainty. High-confidence notes are strongly
    quantized to clean up timing; low-confidence notes retain more
    of their original timing to preserve potentially intentional
    timing nuances.
    """

    def __init__(
        self,
        pass_number: int = 6,
        base_strength: float = 0.7,
        min_strength: float = 0.2,
        max_strength: float = 1.0,
        grid_divisions: int = 16,
        swing_amount: float = 0.0,
        humanize_amount: float = 0.0,
    ):
        """Initialize confidence quantization pass.

        Args:
            pass_number: Pass number in pipeline
            base_strength: Base quantization strength (0-1)
            min_strength: Minimum strength for low-confidence notes
            max_strength: Maximum strength for high-confidence notes
            grid_divisions: Grid divisions per beat (4=quarter, 8=eighth, 16=16th)
            swing_amount: Swing amount (0-1, 0=straight)
            humanize_amount: Humanization variance (0-1)
        """
        super().__init__(pass_number)
        self.base_strength = base_strength
        self.min_strength = min_strength
        self.max_strength = max_strength
        self.grid_divisions = grid_divisions
        self.swing_amount = swing_amount
        self.humanize_amount = humanize_amount

    @property
    def name(self) -> str:
        return "confidence_quantization"

    def process(
        self,
        notes: List[ExtractedNote],
        context: ExtractionContext,
    ) -> PassResult:
        """Quantize notes based on confidence.

        Args:
            notes: Input notes from previous passes
            context: Extraction context

        Returns:
            PassResult with quantized notes
        """
        start_time = time.time()
        input_notes = notes.copy()

        if len(notes) == 0:
            return PassResult(
                notes=[],
                statistics=self._create_statistics(input_notes, [], 0.0),
                warnings=["No notes to quantize"],
            )

        # Get or estimate tempo
        tempo = context.tempo
        if tempo is None:
            tempo = self._estimate_tempo(notes)

        # Calculate grid parameters
        beat_duration = 60.0 / tempo
        grid_duration = beat_duration / (self.grid_divisions / 4)

        # Adapt strength based on context
        adapted_strength = self._adapt_strength(context)

        # Quantize each note
        quantized_notes = []
        timing_changes = []

        for note in notes:
            # Calculate note-specific strength based on confidence
            note_strength = self._calculate_note_strength(
                note.confidence, adapted_strength
            )

            # Store original timing
            original_start = note.start
            original_end = note.end

            # Quantize start time
            new_start = self._quantize_time(
                note.start, grid_duration, note_strength, tempo
            )

            # Quantize end time (or preserve duration)
            if note_strength > 0.5:
                # Strong quantization - quantize both
                new_end = self._quantize_time(
                    note.end, grid_duration, note_strength, tempo
                )
                # Ensure minimum duration
                min_duration = grid_duration * 0.5
                if new_end - new_start < min_duration:
                    new_end = new_start + note.duration
            else:
                # Weak quantization - preserve duration
                new_end = new_start + note.duration

            # Apply humanization if enabled
            if self.humanize_amount > 0:
                new_start, new_end = self._apply_humanization(
                    new_start, new_end, grid_duration, note.confidence
                )

            # Track timing changes
            start_delta = abs(new_start - original_start) * 1000
            end_delta = abs(new_end - original_end) * 1000
            timing_changes.append((start_delta, end_delta))

            # Create quantized note
            flags = note.flags.copy()
            if start_delta > 5 or end_delta > 5:  # More than 5ms change
                flags.add(NoteFlag.QUANTIZED)

            quantized = replace(
                note,
                start=new_start,
                end=new_end,
                original_start=original_start if NoteFlag.QUANTIZED in flags else note.original_start,
                original_end=original_end if NoteFlag.QUANTIZED in flags else note.original_end,
                flags=flags,
            )
            quantized_notes.append(quantized)

        # Resolve overlaps after quantization
        quantized_notes = self._resolve_overlaps(quantized_notes)

        execution_time = (time.time() - start_time) * 1000

        # Calculate statistics
        avg_start_shift = np.mean([tc[0] for tc in timing_changes]) if timing_changes else 0
        avg_end_shift = np.mean([tc[1] for tc in timing_changes]) if timing_changes else 0
        notes_quantized = sum(1 for n in quantized_notes if NoteFlag.QUANTIZED in n.flags)

        stats = self._create_statistics(
            input_notes,
            quantized_notes,
            execution_time,
            tempo=tempo,
            grid_divisions=self.grid_divisions,
            adapted_strength=adapted_strength,
            avg_start_shift_ms=avg_start_shift,
            avg_end_shift_ms=avg_end_shift,
            notes_quantized=notes_quantized,
        )

        warnings = []
        if avg_start_shift > 50:
            warnings.append(f"Large average timing shift: {avg_start_shift:.1f}ms")

        return PassResult(
            notes=quantized_notes,
            statistics=stats,
            warnings=warnings,
            metadata={
                "tempo": tempo,
                "grid_ms": grid_duration * 1000,
            },
        )

    def _estimate_tempo(self, notes: List[ExtractedNote]) -> float:
        """Estimate tempo from note timings."""
        if len(notes) < 2:
            return 120.0

        # Calculate inter-onset intervals
        onsets = sorted([n.start for n in notes])
        iois = np.diff(onsets)

        if len(iois) == 0:
            return 120.0

        # Filter very short IOIs (likely grace notes)
        iois = iois[iois > 0.1]
        if len(iois) == 0:
            return 120.0

        # Find most common IOI using histogram
        hist, bin_edges = np.histogram(iois, bins=50, range=(0.1, 2.0))
        peak_idx = np.argmax(hist)
        peak_ioi = (bin_edges[peak_idx] + bin_edges[peak_idx + 1]) / 2

        # Estimate beat from peak IOI (assume it's an 8th note)
        beat_duration = peak_ioi * 2
        tempo = 60.0 / beat_duration

        # Snap to common tempos
        common_tempos = [60, 70, 80, 90, 100, 110, 120, 130, 140, 150, 160, 170, 180]
        closest = min(common_tempos, key=lambda t: abs(t - tempo))
        if abs(closest - tempo) < 10:
            tempo = closest

        return float(np.clip(tempo, 60, 200))

    def _adapt_strength(self, context: ExtractionContext) -> float:
        """Adapt quantization strength based on context."""
        strength = self.base_strength

        # Adjust based on genre
        if context.genre:
            genre_lower = context.genre.lower()
            if any(g in genre_lower for g in ["ambient", "shoegaze", "experimental"]):
                # Less quantization for ambient/loose genres
                strength *= 0.7
            elif any(g in genre_lower for g in ["techno", "house", "edm", "electronic"]):
                # More quantization for electronic genres
                strength *= 1.2

        # Adjust based on stem type
        if context.stem_type:
            stem_lower = context.stem_type.lower()
            if "drum" in stem_lower:
                # Drums need tight quantization
                strength *= 1.2
            elif "pad" in stem_lower or "ambient" in stem_lower:
                # Pads can be looser
                strength *= 0.8

        # Adjust based on role
        if context.role_classification is not None:
            role = context.role_classification
            if hasattr(role, "primary_role"):
                if role.primary_role in ["pad_atmosphere", "texture_layer"]:
                    strength *= 0.6
                elif role.primary_role in ["rhythmic_element", "bass_foundation"]:
                    strength *= 1.1

        return float(np.clip(strength, self.min_strength, self.max_strength))

    def _calculate_note_strength(
        self,
        confidence: float,
        base_strength: float,
    ) -> float:
        """Calculate per-note quantization strength."""
        # Scale strength by confidence
        # High confidence -> closer to base_strength
        # Low confidence -> closer to min_strength
        confidence_factor = confidence ** 0.5  # Sqrt to be less aggressive

        strength = (
            self.min_strength +
            (base_strength - self.min_strength) * confidence_factor
        )

        return float(np.clip(strength, self.min_strength, self.max_strength))

    def _quantize_time(
        self,
        time_sec: float,
        grid_duration: float,
        strength: float,
        tempo: float,
    ) -> float:
        """Quantize a time value to the grid."""
        # Find nearest grid position
        grid_position = round(time_sec / grid_duration)
        quantized_time = grid_position * grid_duration

        # Apply swing if enabled
        if self.swing_amount > 0:
            quantized_time = self._apply_swing(
                quantized_time, grid_position, grid_duration, tempo
            )

        # Blend between original and quantized based on strength
        return time_sec + (quantized_time - time_sec) * strength

    def _apply_swing(
        self,
        time_sec: float,
        grid_position: int,
        grid_duration: float,
        tempo: float,
    ) -> float:
        """Apply swing timing."""
        # Swing affects off-beat positions (odd grid positions at 8th note level)
        beat_divisions = self.grid_divisions // 4  # Divisions per beat

        # Check if this is an off-beat position
        position_in_beat = grid_position % beat_divisions
        if position_in_beat % 2 == 1:  # Off-beat
            # Push forward by swing amount
            swing_offset = grid_duration * self.swing_amount * 0.5
            return time_sec + swing_offset

        return time_sec

    def _apply_humanization(
        self,
        start: float,
        end: float,
        grid_duration: float,
        confidence: float,
    ) -> Tuple[float, float]:
        """Apply humanization to timing."""
        # Lower confidence = more humanization (preserve original character)
        humanize_factor = self.humanize_amount * (1 - confidence * 0.5)

        # Random offset scaled by grid duration
        max_offset = grid_duration * 0.1 * humanize_factor

        # Use deterministic "randomness" based on timing
        np.random.seed(int(start * 1000) % (2**31))
        start_offset = np.random.uniform(-max_offset, max_offset)
        end_offset = np.random.uniform(-max_offset, max_offset)

        return start + start_offset, end + end_offset

    def _resolve_overlaps(
        self,
        notes: List[ExtractedNote],
    ) -> List[ExtractedNote]:
        """Resolve note overlaps created by quantization."""
        if len(notes) < 2:
            return notes

        # Group by pitch
        pitch_groups: dict = {}
        for i, note in enumerate(notes):
            if note.pitch not in pitch_groups:
                pitch_groups[note.pitch] = []
            pitch_groups[note.pitch].append((i, note))

        resolved = list(notes)

        for pitch, indexed_notes in pitch_groups.items():
            # Sort by start time
            indexed_notes = sorted(indexed_notes, key=lambda x: x[1].start)

            for i in range(len(indexed_notes) - 1):
                idx1, note1 = indexed_notes[i]
                idx2, note2 = indexed_notes[i + 1]

                # Check for overlap
                if note1.end > note2.start:
                    # Shorten note1 to end just before note2
                    gap = 0.01  # 10ms gap
                    new_end = note2.start - gap

                    # Don't make note too short
                    min_duration = 0.05  # 50ms
                    if new_end - note1.start < min_duration:
                        new_end = note1.start + min_duration

                    resolved[idx1] = replace(note1, end=new_end)

        return resolved
