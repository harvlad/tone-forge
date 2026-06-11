"""Beat grid filter pass.

Validates note timing against the detected beat grid and removes
or adjusts notes that fall on musically improbable positions.

Uses probabilistic scoring rather than hard rejection to preserve
intentional off-grid notes (syncopation, swing, rubato) while
filtering extraction artifacts that happen to land off-grid.
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


class BeatGridFilterPass(ExtractionPass):
    """Filter notes based on beat grid alignment.

    This pass scores notes based on how well they align with
    musically meaningful grid positions. Notes that fall on
    improbable positions with low confidence are filtered.

    Grid positions (at 16th note resolution):
    - Downbeats (1, 1): highest priority
    - Backbeats (2, 4 in 4/4): high priority
    - 8th notes: medium priority
    - 16th notes: lower priority
    - Off-grid: lowest priority

    Notes with high confidence or clear melodic context are
    preserved regardless of grid position.
    """

    def __init__(
        self,
        pass_number: int = 0,
        grid_strength: float = 0.7,
        grid_divisions: int = 16,
        downbeat_weight: float = 1.0,
        backbeat_weight: float = 0.9,
        eighth_weight: float = 0.7,
        sixteenth_weight: float = 0.5,
        off_grid_weight: float = 0.3,
        timing_tolerance_ms: float = 30.0,
        confidence_override_threshold: float = 0.8,
        swing_amount: float = 0.0,
    ):
        """Initialize beat grid filter.

        Args:
            pass_number: Pass number in pipeline
            grid_strength: How strongly to enforce grid (0-1)
            grid_divisions: Grid resolution (4=quarter, 8=eighth, 16=sixteenth)
            downbeat_weight: Weight for downbeat positions
            backbeat_weight: Weight for backbeat positions
            eighth_weight: Weight for eighth note positions
            sixteenth_weight: Weight for sixteenth note positions
            off_grid_weight: Weight for off-grid positions
            timing_tolerance_ms: Tolerance for grid alignment
            confidence_override_threshold: Confidence above which grid is ignored
            swing_amount: Swing ratio (0=straight, 0.67=triplet swing)
        """
        super().__init__(pass_number)
        self.grid_strength = grid_strength
        self.grid_divisions = grid_divisions
        self.downbeat_weight = downbeat_weight
        self.backbeat_weight = backbeat_weight
        self.eighth_weight = eighth_weight
        self.sixteenth_weight = sixteenth_weight
        self.off_grid_weight = off_grid_weight
        self.timing_tolerance_ms = timing_tolerance_ms
        self.confidence_override_threshold = confidence_override_threshold
        self.swing_amount = swing_amount

    @property
    def name(self) -> str:
        return "beat_grid_filter"

    def process(
        self,
        notes: List[ExtractedNote],
        context: ExtractionContext,
    ) -> PassResult:
        """Filter notes based on beat grid alignment.

        Args:
            notes: Input notes
            context: Extraction context

        Returns:
            PassResult with filtered notes
        """
        start_time = time.time()
        input_notes = notes.copy()

        if len(notes) == 0:
            return PassResult(
                notes=[],
                statistics=self._create_statistics(input_notes, [], 0.0),
            )

        # Get tempo and time signature
        tempo = context.tempo or self._estimate_tempo(notes)
        beats_per_measure, beat_unit = context.time_signature

        # Calculate grid timing
        beat_duration = 60.0 / tempo  # seconds per beat
        grid_duration = beat_duration * 4 / self.grid_divisions  # seconds per grid unit
        tolerance_sec = self.timing_tolerance_ms / 1000.0

        # Build grid position weights for one measure
        grid_weights = self._build_grid_weights(beats_per_measure)

        # Score each note
        grid_scores: Dict[int, Tuple[float, int, float]] = {}  # idx -> (weight, grid_pos, offset)

        for i, note in enumerate(notes):
            # High confidence notes bypass grid filtering
            if note.confidence >= self.confidence_override_threshold:
                continue

            # Find nearest grid position
            grid_pos, offset = self._find_nearest_grid(
                note.start, beat_duration, grid_duration, tempo
            )

            # Get weight for this grid position
            measure_pos = grid_pos % len(grid_weights)
            weight = grid_weights[measure_pos]

            # Apply swing adjustment if enabled
            if self.swing_amount > 0:
                weight = self._apply_swing_adjustment(weight, measure_pos, offset, tolerance_sec)

            # Store score
            grid_scores[i] = (weight, grid_pos, offset)

        # Apply filtering based on scores and grid strength
        output_notes = []
        filtered_count = 0
        confidence_adjusted_count = 0

        for i, note in enumerate(notes):
            if i not in grid_scores:
                # High confidence - keep as-is
                output_notes.append(note)
                continue

            weight, grid_pos, offset = grid_scores[i]

            # Combine weight with confidence
            combined_score = note.confidence * (1 - self.grid_strength) + weight * self.grid_strength

            if combined_score < 0.3:
                # Too low - filter out
                filtered_count += 1
                continue
            elif combined_score < 0.5:
                # Borderline - reduce confidence
                provenance = note.provenance or NoteProvenance()
                provenance = replace(
                    provenance,
                    cleanup_passes=provenance.cleanup_passes + [self.name],
                    suppression_reasons=provenance.suppression_reasons + [
                        f"weak_grid_alignment_pos{grid_pos}"
                    ],
                )

                new_confidence = note.confidence * 0.7
                provenance = replace(provenance, final_confidence=new_confidence)

                modified_note = replace(
                    note,
                    confidence=new_confidence,
                    flags=note.flags | {NoteFlag.LOW_CONFIDENCE},
                    provenance=provenance,
                )
                output_notes.append(modified_note)
                confidence_adjusted_count += 1
            else:
                output_notes.append(note)

        execution_time = (time.time() - start_time) * 1000

        stats = self._create_statistics(
            input_notes,
            output_notes,
            execution_time,
            notes_filtered=filtered_count,
            confidence_adjusted=confidence_adjusted_count,
            tempo=tempo,
            grid_divisions=self.grid_divisions,
        )

        warnings = []
        if filtered_count > len(input_notes) * 0.2:
            warnings.append(
                f"Filtered {filtered_count}/{len(input_notes)} off-grid notes - "
                "consider reducing grid_strength for this content"
            )

        return PassResult(
            notes=output_notes,
            statistics=stats,
            warnings=warnings,
            metadata={
                "tempo": tempo,
                "grid_strength": self.grid_strength,
                "grid_divisions": self.grid_divisions,
            },
        )

    def _estimate_tempo(self, notes: List[ExtractedNote]) -> float:
        """Estimate tempo from note onsets."""
        if len(notes) < 2:
            return 120.0

        onsets = sorted([n.start for n in notes])
        iois = np.diff(onsets)
        iois = iois[(iois > 0.1) & (iois < 2.0)]

        if len(iois) == 0:
            return 120.0

        # Find most common IOI
        hist, bin_edges = np.histogram(iois, bins=50)
        peak_idx = np.argmax(hist)
        common_ioi = (bin_edges[peak_idx] + bin_edges[peak_idx + 1]) / 2

        # Assume common IOI is an eighth note
        tempo = 60.0 / (common_ioi * 2)
        return float(np.clip(tempo, 60, 200))

    def _build_grid_weights(self, beats_per_measure: int) -> List[float]:
        """Build weight array for grid positions in one measure."""
        # Grid at 16th note resolution (4 per beat)
        positions_per_beat = self.grid_divisions // 4
        total_positions = beats_per_measure * positions_per_beat

        weights = []
        for i in range(total_positions):
            beat_num = i // positions_per_beat
            position_in_beat = i % positions_per_beat

            if position_in_beat == 0:
                # On the beat
                if beat_num == 0:
                    # Downbeat
                    weights.append(self.downbeat_weight)
                elif beats_per_measure == 4 and beat_num in [1, 3]:
                    # Backbeats in 4/4
                    weights.append(self.backbeat_weight)
                else:
                    # Other beats
                    weights.append(self.eighth_weight)
            elif position_in_beat == positions_per_beat // 2:
                # Eighth note position
                weights.append(self.eighth_weight)
            elif position_in_beat in [positions_per_beat // 4, 3 * positions_per_beat // 4]:
                # Sixteenth note position
                weights.append(self.sixteenth_weight)
            else:
                # Off-grid
                weights.append(self.off_grid_weight)

        return weights

    def _find_nearest_grid(
        self,
        time_sec: float,
        beat_duration: float,
        grid_duration: float,
        tempo: float,
    ) -> Tuple[int, float]:
        """Find nearest grid position and offset."""
        # Convert time to grid units
        grid_time = time_sec / grid_duration
        nearest_grid = round(grid_time)
        offset = (grid_time - nearest_grid) * grid_duration  # in seconds

        return int(nearest_grid), offset

    def _apply_swing_adjustment(
        self,
        weight: float,
        measure_pos: int,
        offset: float,
        tolerance: float,
    ) -> float:
        """Adjust weight for swing timing."""
        # Swing affects off-beat 8th notes
        positions_per_beat = self.grid_divisions // 4

        if measure_pos % positions_per_beat == positions_per_beat // 2:
            # This is an 8th note position - check for swing offset
            swing_offset = tolerance * self.swing_amount

            if abs(offset - swing_offset) < tolerance:
                # Matches swing timing - boost weight
                return min(1.0, weight * 1.3)

        return weight
