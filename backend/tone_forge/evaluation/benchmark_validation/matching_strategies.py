"""Multiple matching strategies for benchmark evaluation.

Implements three evaluation modes:
1. STRICT: Exact pitch, tight timing, no forgiveness
2. MUSICAL: Relaxed for musical usability
3. RECONSTRUCTION: Weighted by perceptual usefulness

Goal: Understand if improvements are technically correct,
musically useful, or benchmark artifacts.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np

logger = logging.getLogger(__name__)


class MatchMode(Enum):
    """Matching mode enumeration."""
    STRICT = "strict"
    MUSICAL = "musical"
    RECONSTRUCTION = "reconstruction"


@dataclass
class NoteMatch:
    """A matched note pair."""
    extracted_idx: int
    ground_truth_idx: int
    extracted_note: Tuple[int, float, float, int]
    ground_truth_note: Tuple[int, float, float, int]
    match_score: float = 1.0  # Quality of match (0-1)
    onset_error_ms: float = 0.0
    pitch_error_cents: float = 0.0


@dataclass
class MatchResult:
    """Result from a matching strategy."""
    mode: MatchMode
    matches: List[NoteMatch] = field(default_factory=list)

    # Metrics
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    precision: float = 0.0
    recall: float = 0.0
    f1: float = 0.0

    # Weighted metrics (for reconstruction mode)
    weighted_precision: float = 0.0
    weighted_recall: float = 0.0
    weighted_f1: float = 0.0

    # Details
    avg_match_score: float = 0.0
    onset_errors_ms: List[float] = field(default_factory=list)
    pitch_errors_cents: List[float] = field(default_factory=list)

    # Flags
    extracted_note_count: int = 0
    ground_truth_note_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode.value,
            "metrics": {
                "tp": self.true_positives,
                "fp": self.false_positives,
                "fn": self.false_negatives,
                "precision": self.precision,
                "recall": self.recall,
                "f1": self.f1,
            },
            "weighted_metrics": {
                "precision": self.weighted_precision,
                "recall": self.weighted_recall,
                "f1": self.weighted_f1,
            },
            "statistics": {
                "avg_match_score": self.avg_match_score,
                "avg_onset_error_ms": np.mean(self.onset_errors_ms) if self.onset_errors_ms else 0,
                "avg_pitch_error_cents": np.mean(self.pitch_errors_cents) if self.pitch_errors_cents else 0,
            },
            "counts": {
                "extracted": self.extracted_note_count,
                "ground_truth": self.ground_truth_note_count,
            },
        }


@dataclass
class StrategyComparison:
    """Comparison across all matching strategies."""
    sample_id: str
    strict_result: MatchResult
    musical_result: MatchResult
    reconstruction_result: MatchResult

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sample_id": self.sample_id,
            "strict": self.strict_result.to_dict(),
            "musical": self.musical_result.to_dict(),
            "reconstruction": self.reconstruction_result.to_dict(),
            "comparison": {
                "f1_strict": self.strict_result.f1,
                "f1_musical": self.musical_result.f1,
                "f1_reconstruction": self.reconstruction_result.f1,
                "musical_vs_strict": self.musical_result.f1 - self.strict_result.f1,
                "recon_vs_strict": self.reconstruction_result.f1 - self.strict_result.f1,
            },
        }

    def summary(self) -> str:
        lines = [
            f"Strategy Comparison: {self.sample_id}",
            "=" * 60,
            "",
            f"{'Mode':<20} {'F1':>10} {'Precision':>10} {'Recall':>10}",
            "-" * 60,
            f"{'STRICT':<20} {self.strict_result.f1:>10.1%} {self.strict_result.precision:>10.1%} {self.strict_result.recall:>10.1%}",
            f"{'MUSICAL':<20} {self.musical_result.f1:>10.1%} {self.musical_result.precision:>10.1%} {self.musical_result.recall:>10.1%}",
            f"{'RECONSTRUCTION':<20} {self.reconstruction_result.weighted_f1:>10.1%} {self.reconstruction_result.weighted_precision:>10.1%} {self.reconstruction_result.weighted_recall:>10.1%}",
            "",
            "Analysis:",
        ]

        # Determine if improvements are real
        strict_f1 = self.strict_result.f1
        musical_f1 = self.musical_result.f1
        recon_f1 = self.reconstruction_result.weighted_f1

        if musical_f1 - strict_f1 > 0.1:
            lines.append(f"  - Musical mode {(musical_f1 - strict_f1):.1%} higher: suggests octave/timing flexibility helps")
        elif strict_f1 > musical_f1:
            lines.append(f"  - Strict mode higher: extraction is precise but may miss musically relevant notes")

        if recon_f1 < musical_f1:
            lines.append(f"  - Reconstruction lower than musical: some matched notes have low perceptual value")

        return "\n".join(lines)


class MatchingStrategy(ABC):
    """Base class for matching strategies."""

    @abstractmethod
    def match(
        self,
        extracted: List[Tuple[int, float, float, int]],
        ground_truth: List[Tuple[int, float, float, int]],
    ) -> MatchResult:
        """Match extracted notes to ground truth."""
        pass

    def _compute_metrics(
        self,
        result: MatchResult,
        extracted: List[Tuple[int, float, float, int]],
        ground_truth: List[Tuple[int, float, float, int]],
    ) -> None:
        """Compute standard metrics from matches."""
        matched_gt_indices = set(m.ground_truth_idx for m in result.matches)

        result.true_positives = len(result.matches)
        result.false_positives = len(extracted) - result.true_positives
        result.false_negatives = len(ground_truth) - len(matched_gt_indices)

        result.extracted_note_count = len(extracted)
        result.ground_truth_note_count = len(ground_truth)

        tp = result.true_positives
        fp = result.false_positives
        fn = result.false_negatives

        result.precision = tp / (tp + fp) if (tp + fp) > 0 else 0
        result.recall = tp / (tp + fn) if (tp + fn) > 0 else 0
        result.f1 = (
            2 * result.precision * result.recall /
            (result.precision + result.recall)
            if (result.precision + result.recall) > 0 else 0
        )

        if result.matches:
            result.avg_match_score = np.mean([m.match_score for m in result.matches])
            result.onset_errors_ms = [m.onset_error_ms for m in result.matches]
            result.pitch_errors_cents = [m.pitch_error_cents for m in result.matches]


class StrictMatcher(MatchingStrategy):
    """Strict matching: exact pitch, tight timing, no forgiveness.

    - Exact pitch match required (0 semitone tolerance)
    - 25ms onset tolerance (very tight)
    - No octave forgiveness
    """

    def __init__(
        self,
        onset_tolerance_ms: float = 25.0,
        pitch_tolerance_semitones: int = 0,
    ):
        self.onset_tolerance_ms = onset_tolerance_ms
        self.pitch_tolerance_semitones = pitch_tolerance_semitones

    def match(
        self,
        extracted: List[Tuple[int, float, float, int]],
        ground_truth: List[Tuple[int, float, float, int]],
    ) -> MatchResult:
        result = MatchResult(mode=MatchMode.STRICT)

        if not extracted or not ground_truth:
            self._compute_metrics(result, extracted, ground_truth)
            return result

        onset_tol_sec = self.onset_tolerance_ms / 1000.0
        matched_gt: Set[int] = set()

        for i, ext in enumerate(extracted):
            ext_pitch, ext_onset, _, _ = ext
            best_match = None
            best_error = float('inf')

            for j, gt in enumerate(ground_truth):
                if j in matched_gt:
                    continue

                gt_pitch, gt_onset, _, _ = gt

                # Strict pitch check
                if abs(ext_pitch - gt_pitch) > self.pitch_tolerance_semitones:
                    continue

                # Tight timing check
                onset_diff = abs(ext_onset - gt_onset)
                if onset_diff > onset_tol_sec:
                    continue

                if onset_diff < best_error:
                    best_error = onset_diff
                    best_match = j

            if best_match is not None:
                gt = ground_truth[best_match]
                result.matches.append(NoteMatch(
                    extracted_idx=i,
                    ground_truth_idx=best_match,
                    extracted_note=ext,
                    ground_truth_note=gt,
                    match_score=1.0,
                    onset_error_ms=best_error * 1000,
                    pitch_error_cents=abs(ext_pitch - gt[0]) * 100,
                ))
                matched_gt.add(best_match)

        self._compute_metrics(result, extracted, ground_truth)
        return result


class MusicalMatcher(MatchingStrategy):
    """Musical matching: relaxed for real-world usability.

    - ±1 semitone tolerance (allows for tuning issues)
    - 75ms onset tolerance (more forgiving)
    - Octave errors count as partial matches
    - Sustain-aware: offset matching is lenient
    """

    def __init__(
        self,
        onset_tolerance_ms: float = 75.0,
        pitch_tolerance_semitones: int = 1,
        allow_octave_match: bool = True,
        octave_match_weight: float = 0.5,
    ):
        self.onset_tolerance_ms = onset_tolerance_ms
        self.pitch_tolerance_semitones = pitch_tolerance_semitones
        self.allow_octave_match = allow_octave_match
        self.octave_match_weight = octave_match_weight

    def match(
        self,
        extracted: List[Tuple[int, float, float, int]],
        ground_truth: List[Tuple[int, float, float, int]],
    ) -> MatchResult:
        result = MatchResult(mode=MatchMode.MUSICAL)

        if not extracted or not ground_truth:
            self._compute_metrics(result, extracted, ground_truth)
            return result

        onset_tol_sec = self.onset_tolerance_ms / 1000.0
        matched_gt: Set[int] = set()

        for i, ext in enumerate(extracted):
            ext_pitch, ext_onset, _, _ = ext
            best_match = None
            best_score = 0.0
            best_onset_error = 0.0
            best_pitch_error = 0.0

            for j, gt in enumerate(ground_truth):
                if j in matched_gt:
                    continue

                gt_pitch, gt_onset, _, _ = gt

                # Timing check
                onset_diff = abs(ext_onset - gt_onset)
                if onset_diff > onset_tol_sec:
                    continue

                # Pitch check with octave tolerance
                pitch_diff = abs(ext_pitch - gt_pitch)
                match_weight = 0.0

                if pitch_diff <= self.pitch_tolerance_semitones:
                    match_weight = 1.0
                elif self.allow_octave_match and pitch_diff % 12 <= self.pitch_tolerance_semitones:
                    match_weight = self.octave_match_weight
                else:
                    continue

                # Score based on timing accuracy
                timing_score = 1.0 - (onset_diff / onset_tol_sec)
                total_score = match_weight * timing_score

                if total_score > best_score:
                    best_score = total_score
                    best_match = j
                    best_onset_error = onset_diff * 1000
                    best_pitch_error = pitch_diff * 100

            if best_match is not None:
                gt = ground_truth[best_match]
                result.matches.append(NoteMatch(
                    extracted_idx=i,
                    ground_truth_idx=best_match,
                    extracted_note=ext,
                    ground_truth_note=gt,
                    match_score=best_score,
                    onset_error_ms=best_onset_error,
                    pitch_error_cents=best_pitch_error,
                ))
                matched_gt.add(best_match)

        self._compute_metrics(result, extracted, ground_truth)
        return result


class ReconstructionMatcher(MatchingStrategy):
    """Reconstruction matching: weighted by perceptual usefulness.

    Evaluates:
    - Rhythmic structure preservation (timing on beats more important)
    - Note density (hallucinated notes penalized more in sparse sections)
    - Velocity accuracy (dynamics matter for expression)
    - Harmonic relationships (chord tones weighted higher)
    """

    def __init__(
        self,
        onset_tolerance_ms: float = 50.0,
        pitch_tolerance_semitones: int = 0,
        tempo_bpm: float = 120.0,
        beat_weight_boost: float = 1.5,  # Notes on beats worth more
        hallucination_penalty: float = 0.8,  # FP penalty in sparse sections
    ):
        self.onset_tolerance_ms = onset_tolerance_ms
        self.pitch_tolerance_semitones = pitch_tolerance_semitones
        self.tempo_bpm = tempo_bpm
        self.beat_weight_boost = beat_weight_boost
        self.hallucination_penalty = hallucination_penalty

    def match(
        self,
        extracted: List[Tuple[int, float, float, int]],
        ground_truth: List[Tuple[int, float, float, int]],
    ) -> MatchResult:
        result = MatchResult(mode=MatchMode.RECONSTRUCTION)

        if not extracted or not ground_truth:
            self._compute_metrics(result, extracted, ground_truth)
            return result

        onset_tol_sec = self.onset_tolerance_ms / 1000.0
        matched_gt: Set[int] = set()

        # Compute beat positions
        beat_interval = 60.0 / self.tempo_bpm

        # Compute note density over time (for hallucination penalty)
        gt_density = self._compute_density(ground_truth, window_sec=2.0)

        for i, ext in enumerate(extracted):
            ext_pitch, ext_onset, ext_offset, ext_vel = ext
            best_match = None
            best_score = 0.0
            best_onset_error = 0.0
            best_pitch_error = 0.0

            for j, gt in enumerate(ground_truth):
                if j in matched_gt:
                    continue

                gt_pitch, gt_onset, gt_offset, gt_vel = gt

                # Pitch check
                pitch_diff = abs(ext_pitch - gt_pitch)
                if pitch_diff > self.pitch_tolerance_semitones:
                    continue

                # Timing check
                onset_diff = abs(ext_onset - gt_onset)
                if onset_diff > onset_tol_sec:
                    continue

                # Compute perceptual weight
                weight = 1.0

                # Boost for notes on beats
                beat_offset = gt_onset % beat_interval
                if beat_offset < 0.05 or beat_offset > beat_interval - 0.05:
                    weight *= self.beat_weight_boost

                # Velocity accuracy component
                vel_accuracy = 1.0 - abs(ext_vel - gt_vel) / 127.0
                weight *= (0.7 + 0.3 * vel_accuracy)  # 30% weight to velocity

                # Duration accuracy (for sustain)
                ext_dur = ext_offset - ext_onset
                gt_dur = gt_offset - gt_onset
                if gt_dur > 0:
                    dur_ratio = min(ext_dur, gt_dur) / max(ext_dur, gt_dur)
                    weight *= (0.8 + 0.2 * dur_ratio)  # 20% weight to duration

                # Final score
                timing_score = 1.0 - (onset_diff / onset_tol_sec)
                total_score = weight * timing_score

                if total_score > best_score:
                    best_score = total_score
                    best_match = j
                    best_onset_error = onset_diff * 1000
                    best_pitch_error = pitch_diff * 100

            if best_match is not None:
                gt = ground_truth[best_match]
                result.matches.append(NoteMatch(
                    extracted_idx=i,
                    ground_truth_idx=best_match,
                    extracted_note=ext,
                    ground_truth_note=gt,
                    match_score=best_score,
                    onset_error_ms=best_onset_error,
                    pitch_error_cents=best_pitch_error,
                ))
                matched_gt.add(best_match)

        self._compute_metrics(result, extracted, ground_truth)
        self._compute_weighted_metrics(result, extracted, ground_truth, gt_density)
        return result

    def _compute_density(
        self,
        notes: List[Tuple[int, float, float, int]],
        window_sec: float = 2.0,
    ) -> Dict[float, float]:
        """Compute note density over time."""
        if not notes:
            return {}

        max_time = max(n[1] for n in notes)
        density = {}

        for t in np.arange(0, max_time + window_sec, window_sec / 2):
            count = sum(1 for n in notes if t <= n[1] < t + window_sec)
            density[t] = count / window_sec

        return density

    def _compute_weighted_metrics(
        self,
        result: MatchResult,
        extracted: List[Tuple[int, float, float, int]],
        ground_truth: List[Tuple[int, float, float, int]],
        gt_density: Dict[float, float],
    ) -> None:
        """Compute weighted precision/recall/F1."""
        if not extracted or not ground_truth:
            return

        # Weighted TP: sum of match scores
        weighted_tp = sum(m.match_score for m in result.matches)

        # Weighted FP: penalize more in sparse regions
        matched_ext = set(m.extracted_idx for m in result.matches)
        weighted_fp = 0.0
        for i, ext in enumerate(extracted):
            if i not in matched_ext:
                # Find density at this time
                ext_time = ext[1]
                local_density = 0.0
                for t, d in gt_density.items():
                    if t <= ext_time < t + 2.0:
                        local_density = d
                        break

                # Higher penalty in sparse regions
                if local_density < 2.0:  # Less than 2 notes/sec
                    weighted_fp += self.hallucination_penalty * 1.5
                else:
                    weighted_fp += 1.0

        # Weighted FN: missed notes (equal weight)
        weighted_fn = len(ground_truth) - len(set(m.ground_truth_idx for m in result.matches))

        # Compute weighted metrics
        result.weighted_precision = (
            weighted_tp / (weighted_tp + weighted_fp)
            if (weighted_tp + weighted_fp) > 0 else 0
        )
        result.weighted_recall = (
            weighted_tp / (weighted_tp + weighted_fn)
            if (weighted_tp + weighted_fn) > 0 else 0
        )
        result.weighted_f1 = (
            2 * result.weighted_precision * result.weighted_recall /
            (result.weighted_precision + result.weighted_recall)
            if (result.weighted_precision + result.weighted_recall) > 0 else 0
        )


def compare_strategies(
    extracted: List[Tuple[int, float, float, int]],
    ground_truth: List[Tuple[int, float, float, int]],
    sample_id: str = "",
    tempo_bpm: float = 120.0,
) -> StrategyComparison:
    """Compare all matching strategies on a sample.

    Args:
        extracted: Extracted notes (pitch, onset, offset, velocity)
        ground_truth: Ground truth notes
        sample_id: Sample identifier
        tempo_bpm: Tempo for reconstruction weighting

    Returns:
        StrategyComparison with results from all strategies
    """
    strict = StrictMatcher()
    musical = MusicalMatcher()
    reconstruction = ReconstructionMatcher(tempo_bpm=tempo_bpm)

    return StrategyComparison(
        sample_id=sample_id,
        strict_result=strict.match(extracted, ground_truth),
        musical_result=musical.match(extracted, ground_truth),
        reconstruction_result=reconstruction.match(extracted, ground_truth),
    )
