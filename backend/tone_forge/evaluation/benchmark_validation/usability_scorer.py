"""Human usability scoring for MIDI extraction.

Creates a "producer usefulness" evaluation that separates
"benchmark good" from "actually usable in a DAW."

Questions evaluated:
- Is the MIDI editable?
- Does it preserve musical intent?
- Does cleanup take <2 minutes?
- Does timing feel natural?
- Are hallucinations tolerable?
- Would a producer keep or discard the output?
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


class UsabilityRating(Enum):
    """Usability rating levels."""
    EXCELLENT = "excellent"      # Ready to use with minimal edits
    GOOD = "good"                # Usable with some cleanup
    ACCEPTABLE = "acceptable"    # Needs significant cleanup but worth it
    MARGINAL = "marginal"        # Borderline usable, might be faster to re-record
    UNUSABLE = "unusable"        # Would discard and re-record


@dataclass
class EditabilityScore:
    """Score for MIDI editability."""
    score: float = 0.0  # 0-1
    note_density_penalty: float = 0.0  # Penalty for too many notes
    note_sparsity_penalty: float = 0.0  # Penalty for too few notes
    ghost_note_penalty: float = 0.0  # Penalty for spurious notes
    missing_note_penalty: float = 0.0  # Penalty for missing notes
    cleanup_time_estimate_min: float = 0.0  # Estimated cleanup time
    comments: List[str] = field(default_factory=list)


@dataclass
class MusicalIntentScore:
    """Score for musical intent preservation."""
    score: float = 0.0
    melody_preserved: float = 0.0  # Are melodic contours correct?
    rhythm_preserved: float = 0.0  # Is rhythmic feel correct?
    dynamics_preserved: float = 0.0  # Are velocity dynamics correct?
    articulation_preserved: float = 0.0  # Are articulations captured?
    harmonic_accuracy: float = 0.0  # Are chord tones correct?
    comments: List[str] = field(default_factory=list)


@dataclass
class TimingNaturalnessScore:
    """Score for timing naturalness."""
    score: float = 0.0
    quantization_feel: float = 0.0  # Does it feel human or robotic?
    swing_preservation: float = 0.0  # Is swing feel maintained?
    groove_coherence: float = 0.0  # Does the groove make sense?
    beat_alignment: float = 0.0  # Are notes aligned to beats?
    comments: List[str] = field(default_factory=list)


@dataclass
class HallucinationTolerability:
    """Score for hallucination tolerability."""
    score: float = 0.0
    total_hallucinations: int = 0
    hallucinations_per_minute: float = 0.0
    disruptive_hallucinations: int = 0  # Notes that would be obviously wrong
    subtle_hallucinations: int = 0  # Notes that blend in but are wrong
    comments: List[str] = field(default_factory=list)


@dataclass
class UsabilityReport:
    """Complete usability assessment."""
    sample_id: str = ""
    audio_duration_sec: float = 0.0

    # Overall rating
    overall_rating: UsabilityRating = UsabilityRating.UNUSABLE
    overall_score: float = 0.0  # 0-1

    # Component scores
    editability: EditabilityScore = field(default_factory=EditabilityScore)
    musical_intent: MusicalIntentScore = field(default_factory=MusicalIntentScore)
    timing_naturalness: TimingNaturalnessScore = field(default_factory=TimingNaturalnessScore)
    hallucination_tolerance: HallucinationTolerability = field(default_factory=HallucinationTolerability)

    # Producer decision
    would_keep: bool = False
    cleanup_effort: str = "high"  # "minimal", "moderate", "high", "prohibitive"
    recommendation: str = ""

    # Notes
    strengths: List[str] = field(default_factory=list)
    weaknesses: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sample_id": self.sample_id,
            "audio_duration_sec": self.audio_duration_sec,
            "overall": {
                "rating": self.overall_rating.value,
                "score": self.overall_score,
            },
            "editability": {
                "score": self.editability.score,
                "cleanup_time_min": self.editability.cleanup_time_estimate_min,
                "comments": self.editability.comments,
            },
            "musical_intent": {
                "score": self.musical_intent.score,
                "melody": self.musical_intent.melody_preserved,
                "rhythm": self.musical_intent.rhythm_preserved,
                "dynamics": self.musical_intent.dynamics_preserved,
                "comments": self.musical_intent.comments,
            },
            "timing": {
                "score": self.timing_naturalness.score,
                "quantization_feel": self.timing_naturalness.quantization_feel,
                "groove": self.timing_naturalness.groove_coherence,
                "comments": self.timing_naturalness.comments,
            },
            "hallucinations": {
                "score": self.hallucination_tolerance.score,
                "total": self.hallucination_tolerance.total_hallucinations,
                "per_minute": self.hallucination_tolerance.hallucinations_per_minute,
                "comments": self.hallucination_tolerance.comments,
            },
            "producer_decision": {
                "would_keep": self.would_keep,
                "cleanup_effort": self.cleanup_effort,
                "recommendation": self.recommendation,
            },
            "strengths": self.strengths,
            "weaknesses": self.weaknesses,
        }

    def summary(self) -> str:
        lines = [
            f"Usability Report: {self.sample_id}",
            "=" * 60,
            "",
            f"Overall Rating: {self.overall_rating.value.upper()} ({self.overall_score:.0%})",
            f"Producer Decision: {'KEEP' if self.would_keep else 'DISCARD'}",
            f"Cleanup Effort: {self.cleanup_effort}",
            "",
            "Component Scores:",
            f"  Editability:      {self.editability.score:.0%}",
            f"  Musical Intent:   {self.musical_intent.score:.0%}",
            f"  Timing:           {self.timing_naturalness.score:.0%}",
            f"  Hallucinations:   {self.hallucination_tolerance.score:.0%}",
            "",
        ]

        if self.strengths:
            lines.append("Strengths:")
            for s in self.strengths:
                lines.append(f"  + {s}")
            lines.append("")

        if self.weaknesses:
            lines.append("Weaknesses:")
            for w in self.weaknesses:
                lines.append(f"  - {w}")
            lines.append("")

        lines.append(f"Recommendation: {self.recommendation}")

        return "\n".join(lines)


class UsabilityScorer:
    """Scores MIDI extraction for human usability.

    Evaluates whether extracted MIDI is actually useful for
    production work, not just good on benchmark metrics.
    """

    def __init__(
        self,
        ideal_note_density_per_sec: float = 4.0,  # For typical melodic content
        max_acceptable_hallucinations_per_min: float = 5.0,
        quick_cleanup_threshold_min: float = 2.0,
    ):
        """Initialize the scorer.

        Args:
            ideal_note_density_per_sec: Expected notes per second
            max_acceptable_hallucinations_per_min: Max FPs per minute
            quick_cleanup_threshold_min: Cleanup time threshold for "good"
        """
        self.ideal_note_density = ideal_note_density_per_sec
        self.max_hallucinations_per_min = max_acceptable_hallucinations_per_min
        self.quick_cleanup_threshold = quick_cleanup_threshold_min

    def score(
        self,
        extracted_notes: List[Tuple[int, float, float, int]],
        ground_truth_notes: List[Tuple[int, float, float, int]],
        matched_ext_indices: set,
        matched_gt_indices: set,
        sample_id: str = "",
        audio_duration_sec: float = 0.0,
        tempo_bpm: float = 120.0,
    ) -> UsabilityReport:
        """Score extracted MIDI for usability.

        Args:
            extracted_notes: Extracted notes (pitch, onset, offset, velocity)
            ground_truth_notes: Ground truth notes
            matched_ext_indices: Indices of matched extracted notes
            matched_gt_indices: Indices of matched GT notes
            sample_id: Sample identifier
            audio_duration_sec: Audio duration in seconds
            tempo_bpm: Tempo in BPM

        Returns:
            UsabilityReport with complete assessment
        """
        report = UsabilityReport(sample_id=sample_id)

        if audio_duration_sec <= 0 and extracted_notes:
            audio_duration_sec = max(n[2] for n in extracted_notes)
        report.audio_duration_sec = audio_duration_sec

        # Score editability
        report.editability = self._score_editability(
            extracted_notes, ground_truth_notes,
            matched_ext_indices, matched_gt_indices,
            audio_duration_sec
        )

        # Score musical intent
        report.musical_intent = self._score_musical_intent(
            extracted_notes, ground_truth_notes,
            matched_ext_indices, matched_gt_indices
        )

        # Score timing naturalness
        report.timing_naturalness = self._score_timing(
            extracted_notes, ground_truth_notes,
            matched_ext_indices, matched_gt_indices,
            tempo_bpm
        )

        # Score hallucination tolerability
        report.hallucination_tolerance = self._score_hallucinations(
            extracted_notes, matched_ext_indices, audio_duration_sec
        )

        # Compute overall score
        report.overall_score = (
            0.25 * report.editability.score +
            0.30 * report.musical_intent.score +
            0.20 * report.timing_naturalness.score +
            0.25 * report.hallucination_tolerance.score
        )

        # Determine rating
        report.overall_rating = self._compute_rating(report.overall_score)

        # Producer decision
        report.would_keep = report.overall_score >= 0.5
        report.cleanup_effort = self._estimate_effort(report)
        report.recommendation = self._generate_recommendation(report)

        # Identify strengths and weaknesses
        report.strengths, report.weaknesses = self._identify_strengths_weaknesses(report)

        return report

    def _score_editability(
        self,
        extracted: List[Tuple[int, float, float, int]],
        ground_truth: List[Tuple[int, float, float, int]],
        matched_ext: set,
        matched_gt: set,
        duration_sec: float,
    ) -> EditabilityScore:
        """Score how editable the MIDI is."""
        score = EditabilityScore()

        if not extracted or duration_sec <= 0:
            return score

        # Note density analysis
        ext_density = len(extracted) / duration_sec
        gt_density = len(ground_truth) / duration_sec if ground_truth else self.ideal_note_density

        # Penalty for over-extraction
        if ext_density > gt_density * 1.5:
            score.note_density_penalty = min(0.3, (ext_density / gt_density - 1) * 0.2)
            score.comments.append(f"Too many notes: {ext_density:.1f}/s vs expected {gt_density:.1f}/s")

        # Penalty for under-extraction
        if ext_density < gt_density * 0.5:
            score.note_sparsity_penalty = min(0.3, (1 - ext_density / gt_density) * 0.3)
            score.comments.append(f"Too few notes: {ext_density:.1f}/s vs expected {gt_density:.1f}/s")

        # Ghost note penalty
        ghost_count = len(extracted) - len(matched_ext)
        ghost_ratio = ghost_count / len(extracted) if extracted else 0
        score.ghost_note_penalty = min(0.3, ghost_ratio * 0.4)
        if ghost_ratio > 0.2:
            score.comments.append(f"High ghost note ratio: {ghost_ratio:.1%}")

        # Missing note penalty
        missed_count = len(ground_truth) - len(matched_gt)
        missed_ratio = missed_count / len(ground_truth) if ground_truth else 0
        score.missing_note_penalty = min(0.3, missed_ratio * 0.4)
        if missed_ratio > 0.3:
            score.comments.append(f"Many notes missed: {missed_ratio:.1%}")

        # Cleanup time estimate (rough heuristic)
        # ~2 sec per ghost note, ~1 sec per missing note
        score.cleanup_time_estimate_min = (ghost_count * 2 + missed_count * 1) / 60

        # Final score
        score.score = max(0, 1.0 - (
            score.note_density_penalty +
            score.note_sparsity_penalty +
            score.ghost_note_penalty +
            score.missing_note_penalty
        ))

        return score

    def _score_musical_intent(
        self,
        extracted: List[Tuple[int, float, float, int]],
        ground_truth: List[Tuple[int, float, float, int]],
        matched_ext: set,
        matched_gt: set,
    ) -> MusicalIntentScore:
        """Score how well musical intent is preserved."""
        score = MusicalIntentScore()

        if not extracted or not ground_truth or not matched_ext:
            return score

        # Get matched pairs
        # (This is simplified - in practice we'd need the actual matching)
        matched_notes = [extracted[i] for i in matched_ext]
        matched_gt_notes = [ground_truth[i] for i in matched_gt]

        # Melody preservation: check pitch contour
        if len(matched_notes) >= 3:
            ext_pitches = [n[0] for n in sorted(matched_notes, key=lambda x: x[1])]
            gt_pitches = [n[0] for n in sorted(matched_gt_notes, key=lambda x: x[1])]

            # Direction changes
            ext_directions = [1 if ext_pitches[i+1] > ext_pitches[i] else -1
                            for i in range(len(ext_pitches)-1)]
            gt_directions = [1 if gt_pitches[i+1] > gt_pitches[i] else -1
                           for i in range(min(len(gt_pitches)-1, len(ext_directions)))]

            if gt_directions:
                matching_dirs = sum(1 for i, d in enumerate(ext_directions[:len(gt_directions)])
                                   if d == gt_directions[i])
                score.melody_preserved = matching_dirs / len(gt_directions)

        # Rhythm preservation: check onset patterns
        if len(matched_notes) >= 2:
            ext_iois = np.diff([n[1] for n in sorted(matched_notes, key=lambda x: x[1])])
            gt_iois = np.diff([n[1] for n in sorted(matched_gt_notes, key=lambda x: x[1])])

            if len(ext_iois) > 0 and len(gt_iois) > 0:
                # Compare rhythm by checking if IOI ratios are preserved
                min_len = min(len(ext_iois), len(gt_iois))
                ioi_errors = [abs(ext_iois[i] - gt_iois[i]) for i in range(min_len)]
                avg_error = np.mean(ioi_errors)
                score.rhythm_preserved = max(0, 1 - avg_error / 0.2)  # 200ms = bad

        # Dynamics preservation: velocity correlation
        ext_vels = [n[3] for n in matched_notes]
        gt_vels = [n[3] for n in matched_gt_notes]
        if len(ext_vels) > 1 and len(gt_vels) > 1:
            min_len = min(len(ext_vels), len(gt_vels))
            vel_corr = np.corrcoef(ext_vels[:min_len], gt_vels[:min_len])[0, 1]
            score.dynamics_preserved = max(0, vel_corr) if not np.isnan(vel_corr) else 0.5

        # Overall musical intent
        score.score = (
            0.35 * score.melody_preserved +
            0.35 * score.rhythm_preserved +
            0.30 * score.dynamics_preserved
        )

        if score.melody_preserved < 0.6:
            score.comments.append("Melodic contour not well preserved")
        if score.rhythm_preserved < 0.6:
            score.comments.append("Rhythmic feel compromised")
        if score.dynamics_preserved < 0.5:
            score.comments.append("Dynamics/velocity not well captured")

        return score

    def _score_timing(
        self,
        extracted: List[Tuple[int, float, float, int]],
        ground_truth: List[Tuple[int, float, float, int]],
        matched_ext: set,
        matched_gt: set,
        tempo_bpm: float,
    ) -> TimingNaturalnessScore:
        """Score timing naturalness."""
        score = TimingNaturalnessScore()

        if not extracted:
            return score

        beat_interval = 60.0 / tempo_bpm

        # Quantization feel: are notes too perfectly quantized?
        onsets = [n[1] for n in extracted]
        beat_offsets = [onset % beat_interval for onset in onsets]

        # Variance in beat offset (human playing has variance)
        offset_variance = np.var(beat_offsets) if len(beat_offsets) > 1 else 0

        # Too low variance = too quantized, too high = sloppy
        if offset_variance < 0.001:  # Overly quantized
            score.quantization_feel = 0.5
            score.comments.append("Feels overly quantized/robotic")
        elif offset_variance > 0.01:  # Sloppy
            score.quantization_feel = 0.6
            score.comments.append("Timing feels loose/sloppy")
        else:
            score.quantization_feel = 0.9

        # Beat alignment
        close_to_beat = sum(1 for offset in beat_offsets
                          if offset < 0.05 or offset > beat_interval - 0.05)
        score.beat_alignment = close_to_beat / len(beat_offsets) if beat_offsets else 0

        # Groove coherence (simplified)
        score.groove_coherence = 0.7  # Default moderate score

        # Overall
        score.score = (
            0.40 * score.quantization_feel +
            0.30 * score.beat_alignment +
            0.30 * score.groove_coherence
        )

        return score

    def _score_hallucinations(
        self,
        extracted: List[Tuple[int, float, float, int]],
        matched_ext: set,
        duration_sec: float,
    ) -> HallucinationTolerability:
        """Score hallucination tolerability."""
        score = HallucinationTolerability()

        if not extracted or duration_sec <= 0:
            score.score = 1.0
            return score

        # Count hallucinations (unmatched extracted notes)
        hallucinations = [extracted[i] for i in range(len(extracted)) if i not in matched_ext]
        score.total_hallucinations = len(hallucinations)
        score.hallucinations_per_minute = (score.total_hallucinations / duration_sec) * 60

        # Classify disruptive vs subtle
        for h in hallucinations:
            pitch, onset, offset, velocity = h
            duration = offset - onset

            # Disruptive: loud, long, or at phrase boundaries
            if velocity > 80 or duration > 0.5:
                score.disruptive_hallucinations += 1
            else:
                score.subtle_hallucinations += 1

        # Score based on hallucination rate
        if score.hallucinations_per_minute <= 2:
            score.score = 0.95
        elif score.hallucinations_per_minute <= 5:
            score.score = 0.8
        elif score.hallucinations_per_minute <= 10:
            score.score = 0.5
            score.comments.append(f"High hallucination rate: {score.hallucinations_per_minute:.1f}/min")
        else:
            score.score = 0.2
            score.comments.append(f"Very high hallucinations: {score.hallucinations_per_minute:.1f}/min")

        # Extra penalty for disruptive hallucinations
        if score.disruptive_hallucinations > 3:
            score.score *= 0.8
            score.comments.append(f"{score.disruptive_hallucinations} disruptive ghost notes")

        return score

    def _compute_rating(self, overall_score: float) -> UsabilityRating:
        """Compute rating from overall score."""
        if overall_score >= 0.85:
            return UsabilityRating.EXCELLENT
        elif overall_score >= 0.70:
            return UsabilityRating.GOOD
        elif overall_score >= 0.50:
            return UsabilityRating.ACCEPTABLE
        elif overall_score >= 0.30:
            return UsabilityRating.MARGINAL
        else:
            return UsabilityRating.UNUSABLE

    def _estimate_effort(self, report: UsabilityReport) -> str:
        """Estimate cleanup effort level."""
        cleanup_time = report.editability.cleanup_time_estimate_min

        if cleanup_time < 1:
            return "minimal"
        elif cleanup_time < 3:
            return "moderate"
        elif cleanup_time < 10:
            return "high"
        else:
            return "prohibitive"

    def _generate_recommendation(self, report: UsabilityReport) -> str:
        """Generate producer recommendation."""
        if report.overall_rating == UsabilityRating.EXCELLENT:
            return "Use directly or with minimal tweaks. Ready for production."
        elif report.overall_rating == UsabilityRating.GOOD:
            return "Good starting point. Plan for some cleanup but worth using."
        elif report.overall_rating == UsabilityRating.ACCEPTABLE:
            return "Usable with effort. Consider if cleanup time is justified."
        elif report.overall_rating == UsabilityRating.MARGINAL:
            return "Borderline. May be faster to re-record or use as reference only."
        else:
            return "Not recommended for use. Re-record or try different extraction settings."

    def _identify_strengths_weaknesses(
        self,
        report: UsabilityReport,
    ) -> Tuple[List[str], List[str]]:
        """Identify strengths and weaknesses."""
        strengths = []
        weaknesses = []

        # Editability
        if report.editability.score >= 0.7:
            strengths.append("Clean extraction with minimal ghost notes")
        elif report.editability.score < 0.4:
            weaknesses.append("Many notes need manual correction")

        # Musical intent
        if report.musical_intent.melody_preserved >= 0.8:
            strengths.append("Melodic contours well preserved")
        if report.musical_intent.rhythm_preserved >= 0.8:
            strengths.append("Rhythmic feel accurately captured")
        if report.musical_intent.dynamics_preserved >= 0.7:
            strengths.append("Good velocity dynamics")

        if report.musical_intent.score < 0.5:
            weaknesses.append("Musical intent not well captured")

        # Timing
        if report.timing_naturalness.score >= 0.7:
            strengths.append("Natural-feeling timing")
        elif report.timing_naturalness.score < 0.5:
            weaknesses.append("Timing feels unnatural")

        # Hallucinations
        if report.hallucination_tolerance.total_hallucinations < 5:
            strengths.append("Very few ghost notes")
        if report.hallucination_tolerance.disruptive_hallucinations > 5:
            weaknesses.append("Many disruptive ghost notes")

        return strengths, weaknesses
