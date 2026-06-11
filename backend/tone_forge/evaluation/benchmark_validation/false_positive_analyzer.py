"""False positive analyzer for MIDI extraction.

Classifies unmatched extracted notes into categories:
- Delay echoes
- Octave hallucinations
- Harmonic artifacts (2nd, 3rd, 4th, 5th harmonics)
- Transient duplicates
- Sustain overlaps
- Quantization artifacts
- Pitch drift
- Unknown/noise
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np

logger = logging.getLogger(__name__)


class FPCategory(Enum):
    """False positive categories."""
    DELAY_ECHO = "delay_echo"
    OCTAVE_HALLUCINATION = "octave_hallucination"
    HARMONIC_ARTIFACT = "harmonic_artifact"
    TRANSIENT_DUPLICATE = "transient_duplicate"
    SUSTAIN_OVERLAP = "sustain_overlap"
    QUANTIZATION_ARTIFACT = "quantization_artifact"
    PITCH_DRIFT = "pitch_drift"
    REVERB_TAIL = "reverb_tail"
    CHORD_BLEED = "chord_bleed"
    UNKNOWN = "unknown"


@dataclass
class FalsePositiveNote:
    """A false positive note with classification."""
    index: int
    note: Tuple[int, float, float, int]  # pitch, onset, offset, velocity
    category: FPCategory
    confidence: float = 0.0  # Confidence in classification
    related_gt_idx: Optional[int] = None  # Related GT note if found
    details: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "note": {
                "pitch": self.note[0],
                "onset": self.note[1],
                "offset": self.note[2],
                "velocity": self.note[3],
            },
            "category": self.category.value,
            "confidence": self.confidence,
            "related_gt_idx": self.related_gt_idx,
            "details": self.details,
        }


@dataclass
class FalsePositiveReport:
    """Complete false positive analysis report."""
    sample_id: str = ""

    # Counts
    total_false_positives: int = 0
    extracted_note_count: int = 0
    ground_truth_note_count: int = 0

    # By category
    by_category: Dict[FPCategory, List[FalsePositiveNote]] = field(default_factory=dict)
    category_counts: Dict[str, int] = field(default_factory=dict)
    category_percentages: Dict[str, float] = field(default_factory=dict)

    # Classified FPs
    classified_fps: List[FalsePositiveNote] = field(default_factory=list)

    # Time distribution
    fp_time_distribution: List[float] = field(default_factory=list)  # Onset times
    fp_pitch_distribution: List[int] = field(default_factory=list)  # Pitches

    # Dominant categories
    top_categories: List[Tuple[str, int, float]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sample_id": self.sample_id,
            "counts": {
                "total_fps": self.total_false_positives,
                "extracted": self.extracted_note_count,
                "ground_truth": self.ground_truth_note_count,
            },
            "by_category": {
                cat.value: len(notes) for cat, notes in self.by_category.items()
            },
            "category_percentages": self.category_percentages,
            "top_categories": [
                {"category": c, "count": n, "percentage": p}
                for c, n, p in self.top_categories
            ],
            "time_distribution": {
                "min": min(self.fp_time_distribution) if self.fp_time_distribution else 0,
                "max": max(self.fp_time_distribution) if self.fp_time_distribution else 0,
                "mean": np.mean(self.fp_time_distribution) if self.fp_time_distribution else 0,
            },
            "pitch_distribution": {
                "min": min(self.fp_pitch_distribution) if self.fp_pitch_distribution else 0,
                "max": max(self.fp_pitch_distribution) if self.fp_pitch_distribution else 0,
            },
        }

    def summary(self) -> str:
        lines = [
            f"False Positive Analysis: {self.sample_id}",
            "=" * 60,
            "",
            f"Total FPs: {self.total_false_positives} / {self.extracted_note_count} extracted ({self.total_false_positives / max(self.extracted_note_count, 1):.1%})",
            "",
            "By Category:",
        ]

        for cat, count, pct in self.top_categories:
            lines.append(f"  {cat:25s} {count:4d} ({pct:5.1%})")

        if self.fp_time_distribution:
            lines.extend([
                "",
                "Time Distribution:",
                f"  Range: {min(self.fp_time_distribution):.1f}s - {max(self.fp_time_distribution):.1f}s",
                f"  Mean: {np.mean(self.fp_time_distribution):.1f}s",
            ])

        return "\n".join(lines)


class FalsePositiveAnalyzer:
    """Analyzes false positives to classify them into categories.

    This helps identify systematic extraction failures and
    guides improvements to the extraction pipeline.
    """

    def __init__(
        self,
        onset_tolerance_ms: float = 50.0,
        delay_tolerance_ms: float = 30.0,
        common_delays_ms: List[float] = None,
    ):
        """Initialize the analyzer.

        Args:
            onset_tolerance_ms: Base tolerance for matching
            delay_tolerance_ms: Tolerance for delay pattern detection
            common_delays_ms: Common delay times to check
        """
        self.onset_tolerance_ms = onset_tolerance_ms
        self.delay_tolerance_ms = delay_tolerance_ms
        self.common_delays_ms = common_delays_ms or [
            125, 250, 333, 375, 500, 750, 1000,  # Common delay times
        ]

    def analyze(
        self,
        extracted_notes: List[Tuple[int, float, float, int]],
        ground_truth_notes: List[Tuple[int, float, float, int]],
        matched_extracted_indices: Set[int],
        sample_id: str = "",
    ) -> FalsePositiveReport:
        """Analyze false positives in extracted notes.

        Args:
            extracted_notes: All extracted notes (pitch, onset, offset, velocity)
            ground_truth_notes: Ground truth notes
            matched_extracted_indices: Indices of extracted notes that matched GT
            sample_id: Sample identifier

        Returns:
            FalsePositiveReport with classified FPs
        """
        report = FalsePositiveReport(sample_id=sample_id)
        report.extracted_note_count = len(extracted_notes)
        report.ground_truth_note_count = len(ground_truth_notes)

        # Find false positives (unmatched extracted notes)
        fp_indices = [i for i in range(len(extracted_notes)) if i not in matched_extracted_indices]
        report.total_false_positives = len(fp_indices)

        # Initialize category dict
        for cat in FPCategory:
            report.by_category[cat] = []

        # Classify each FP
        for idx in fp_indices:
            fp_note = extracted_notes[idx]
            classification = self._classify_fp(
                fp_note, idx, extracted_notes, ground_truth_notes, matched_extracted_indices
            )
            report.classified_fps.append(classification)
            report.by_category[classification.category].append(classification)
            report.fp_time_distribution.append(fp_note[1])
            report.fp_pitch_distribution.append(fp_note[0])

        # Compute category statistics
        for cat, notes in report.by_category.items():
            count = len(notes)
            report.category_counts[cat.value] = count
            report.category_percentages[cat.value] = (
                count / report.total_false_positives
                if report.total_false_positives > 0 else 0
            )

        # Sort top categories
        sorted_cats = sorted(
            report.category_counts.items(),
            key=lambda x: x[1],
            reverse=True
        )
        report.top_categories = [
            (cat, count, count / max(report.total_false_positives, 1))
            for cat, count in sorted_cats
            if count > 0
        ]

        return report

    def _classify_fp(
        self,
        fp_note: Tuple[int, float, float, int],
        fp_idx: int,
        all_extracted: List[Tuple[int, float, float, int]],
        ground_truth: List[Tuple[int, float, float, int]],
        matched_indices: Set[int],
    ) -> FalsePositiveNote:
        """Classify a single false positive note."""
        fp_pitch, fp_onset, fp_offset, fp_vel = fp_note

        # Check for delay echo
        delay_result = self._check_delay_echo(fp_note, ground_truth)
        if delay_result:
            return FalsePositiveNote(
                index=fp_idx,
                note=fp_note,
                category=FPCategory.DELAY_ECHO,
                confidence=delay_result[0],
                related_gt_idx=delay_result[1],
                details=f"Delay of ~{delay_result[2]:.0f}ms from GT note",
            )

        # Check for octave hallucination
        octave_result = self._check_octave_hallucination(fp_note, ground_truth)
        if octave_result:
            return FalsePositiveNote(
                index=fp_idx,
                note=fp_note,
                category=FPCategory.OCTAVE_HALLUCINATION,
                confidence=octave_result[0],
                related_gt_idx=octave_result[1],
                details=f"Octave {'up' if octave_result[2] > 0 else 'down'} from GT note",
            )

        # Check for harmonic artifact
        harmonic_result = self._check_harmonic_artifact(fp_note, ground_truth)
        if harmonic_result:
            return FalsePositiveNote(
                index=fp_idx,
                note=fp_note,
                category=FPCategory.HARMONIC_ARTIFACT,
                confidence=harmonic_result[0],
                related_gt_idx=harmonic_result[1],
                details=f"{harmonic_result[2]} harmonic of GT note",
            )

        # Check for transient duplicate
        dup_result = self._check_transient_duplicate(fp_note, fp_idx, all_extracted, matched_indices)
        if dup_result:
            return FalsePositiveNote(
                index=fp_idx,
                note=fp_note,
                category=FPCategory.TRANSIENT_DUPLICATE,
                confidence=dup_result[0],
                details=f"Duplicate of extracted note at {dup_result[1]:.3f}s",
            )

        # Check for sustain overlap
        sustain_result = self._check_sustain_overlap(fp_note, ground_truth)
        if sustain_result:
            return FalsePositiveNote(
                index=fp_idx,
                note=fp_note,
                category=FPCategory.SUSTAIN_OVERLAP,
                confidence=sustain_result[0],
                related_gt_idx=sustain_result[1],
                details="Triggered during sustain of GT note",
            )

        # Check for reverb tail
        reverb_result = self._check_reverb_tail(fp_note, ground_truth)
        if reverb_result:
            return FalsePositiveNote(
                index=fp_idx,
                note=fp_note,
                category=FPCategory.REVERB_TAIL,
                confidence=reverb_result[0],
                related_gt_idx=reverb_result[1],
                details=f"Reverb decay from GT note ({reverb_result[2]:.0f}ms after)",
            )

        # Check for chord bleed
        chord_result = self._check_chord_bleed(fp_note, ground_truth)
        if chord_result:
            return FalsePositiveNote(
                index=fp_idx,
                note=fp_note,
                category=FPCategory.CHORD_BLEED,
                confidence=chord_result[0],
                details="Adjacent pitch from simultaneous chord notes",
            )

        # Unknown
        return FalsePositiveNote(
            index=fp_idx,
            note=fp_note,
            category=FPCategory.UNKNOWN,
            confidence=0.0,
            details="No pattern detected",
        )

    def _check_delay_echo(
        self,
        fp_note: Tuple[int, float, float, int],
        ground_truth: List[Tuple[int, float, float, int]],
    ) -> Optional[Tuple[float, int, float]]:
        """Check if FP is a delay echo of a GT note.

        Returns (confidence, gt_idx, delay_ms) or None.
        """
        fp_pitch, fp_onset, _, fp_vel = fp_note

        for delay_ms in self.common_delays_ms:
            delay_sec = delay_ms / 1000.0

            for j, gt in enumerate(ground_truth):
                gt_pitch, gt_onset, _, gt_vel = gt

                # Same pitch
                if fp_pitch != gt_pitch:
                    continue

                # Check if onset matches delay pattern
                expected_onset = gt_onset + delay_sec
                onset_diff_ms = abs(fp_onset - expected_onset) * 1000

                if onset_diff_ms < self.delay_tolerance_ms:
                    # Velocity should be lower for delay
                    vel_ratio = fp_vel / max(gt_vel, 1)
                    if vel_ratio < 0.9:  # Delay echo typically quieter
                        confidence = 0.9 * (1 - onset_diff_ms / self.delay_tolerance_ms)
                        return (confidence, j, delay_ms)

        return None

    def _check_octave_hallucination(
        self,
        fp_note: Tuple[int, float, float, int],
        ground_truth: List[Tuple[int, float, float, int]],
    ) -> Optional[Tuple[float, int, int]]:
        """Check if FP is an octave error from a GT note.

        Returns (confidence, gt_idx, octave_diff) or None.
        """
        fp_pitch, fp_onset, _, _ = fp_note
        onset_tol_sec = self.onset_tolerance_ms / 1000.0

        for j, gt in enumerate(ground_truth):
            gt_pitch, gt_onset, _, _ = gt

            # Check timing
            if abs(fp_onset - gt_onset) > onset_tol_sec:
                continue

            # Check for octave relationship
            pitch_diff = fp_pitch - gt_pitch
            if pitch_diff != 0 and pitch_diff % 12 == 0:
                octaves = pitch_diff // 12
                confidence = 0.85
                return (confidence, j, octaves)

        return None

    def _check_harmonic_artifact(
        self,
        fp_note: Tuple[int, float, float, int],
        ground_truth: List[Tuple[int, float, float, int]],
    ) -> Optional[Tuple[float, int, str]]:
        """Check if FP is a harmonic of a GT note.

        Harmonics: 2nd (+12 semitones), 3rd (+19), 4th (+24), 5th (+28)
        """
        fp_pitch, fp_onset, _, _ = fp_note
        onset_tol_sec = self.onset_tolerance_ms / 1000.0

        harmonic_intervals = {
            12: "2nd",
            19: "3rd",
            24: "4th",
            28: "5th",
            31: "6th",
        }

        for j, gt in enumerate(ground_truth):
            gt_pitch, gt_onset, _, _ = gt

            if abs(fp_onset - gt_onset) > onset_tol_sec:
                continue

            pitch_diff = fp_pitch - gt_pitch
            if pitch_diff in harmonic_intervals:
                return (0.8, j, harmonic_intervals[pitch_diff])

        return None

    def _check_transient_duplicate(
        self,
        fp_note: Tuple[int, float, float, int],
        fp_idx: int,
        all_extracted: List[Tuple[int, float, float, int]],
        matched_indices: Set[int],
    ) -> Optional[Tuple[float, float]]:
        """Check if FP is a duplicate of another extracted note.

        Returns (confidence, other_onset) or None.
        """
        fp_pitch, fp_onset, _, _ = fp_note
        dup_tolerance_sec = 0.03  # 30ms

        for i, other in enumerate(all_extracted):
            if i == fp_idx or i not in matched_indices:
                continue

            other_pitch, other_onset, _, _ = other

            if other_pitch == fp_pitch:
                onset_diff = abs(fp_onset - other_onset)
                if onset_diff < dup_tolerance_sec:
                    confidence = 0.9 * (1 - onset_diff / dup_tolerance_sec)
                    return (confidence, other_onset)

        return None

    def _check_sustain_overlap(
        self,
        fp_note: Tuple[int, float, float, int],
        ground_truth: List[Tuple[int, float, float, int]],
    ) -> Optional[Tuple[float, int]]:
        """Check if FP occurs during sustain of a GT note."""
        fp_pitch, fp_onset, _, _ = fp_note

        for j, gt in enumerate(ground_truth):
            gt_pitch, gt_onset, gt_offset, _ = gt

            # Same pitch, during GT sustain
            if fp_pitch == gt_pitch:
                if gt_onset < fp_onset < gt_offset:
                    # How far into the sustain?
                    sustain_pos = (fp_onset - gt_onset) / (gt_offset - gt_onset)
                    if sustain_pos > 0.3:  # Not too close to onset
                        return (0.75, j)

        return None

    def _check_reverb_tail(
        self,
        fp_note: Tuple[int, float, float, int],
        ground_truth: List[Tuple[int, float, float, int]],
    ) -> Optional[Tuple[float, int, float]]:
        """Check if FP is in the reverb tail of a GT note."""
        fp_pitch, fp_onset, _, fp_vel = fp_note
        reverb_window_sec = 1.5  # Look back up to 1.5s

        for j, gt in enumerate(ground_truth):
            gt_pitch, gt_onset, gt_offset, gt_vel = gt

            # Same pitch
            if fp_pitch != gt_pitch:
                continue

            # After GT offset, within reverb window
            time_after_offset = fp_onset - gt_offset
            if 0 < time_after_offset < reverb_window_sec:
                # Quieter than original
                if fp_vel < gt_vel * 0.7:
                    confidence = 0.7 * (1 - time_after_offset / reverb_window_sec)
                    return (confidence, j, time_after_offset * 1000)

        return None

    def _check_chord_bleed(
        self,
        fp_note: Tuple[int, float, float, int],
        ground_truth: List[Tuple[int, float, float, int]],
    ) -> Optional[Tuple[float,]]:
        """Check if FP is bleeding from simultaneous chord notes."""
        fp_pitch, fp_onset, _, _ = fp_note
        onset_tol_sec = self.onset_tolerance_ms / 1000.0

        # Find GT notes at same time
        simultaneous = [
            gt for gt in ground_truth
            if abs(gt[1] - fp_onset) < onset_tol_sec
        ]

        if len(simultaneous) >= 2:  # Chord present
            # Check if FP is adjacent to any chord note
            for gt in simultaneous:
                pitch_diff = abs(fp_pitch - gt[0])
                if 1 <= pitch_diff <= 2:  # 1-2 semitones away
                    return (0.65,)

        return None
