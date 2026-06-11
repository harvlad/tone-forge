"""False negative analyzer for MIDI extraction.

Classifies missed ground truth notes into categories:
- Low-energy notes
- Masked notes (covered by louder notes)
- Fast articulations (too short to detect)
- Polyphonic collapse (multiple notes merged)
- Transient misses (failed onset detection)
- Low-frequency failures (bass too deep)
- Chord detection failures
- Extreme velocity (too quiet/loud)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np

logger = logging.getLogger(__name__)


class FNCategory(Enum):
    """False negative categories."""
    LOW_ENERGY = "low_energy"
    MASKED_NOTE = "masked_note"
    FAST_ARTICULATION = "fast_articulation"
    POLYPHONIC_COLLAPSE = "polyphonic_collapse"
    TRANSIENT_MISS = "transient_miss"
    LOW_FREQUENCY = "low_frequency"
    HIGH_FREQUENCY = "high_frequency"
    CHORD_FAILURE = "chord_failure"
    EXTREME_VELOCITY = "extreme_velocity"
    SUSTAIN_MISS = "sustain_miss"
    TIMING_GAP = "timing_gap"
    UNKNOWN = "unknown"


@dataclass
class FalseNegativeNote:
    """A false negative (missed) note with classification."""
    index: int
    note: Tuple[int, float, float, int]  # pitch, onset, offset, velocity
    category: FNCategory
    confidence: float = 0.0
    related_ext_idx: Optional[int] = None  # Potentially related extracted note
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
            "related_ext_idx": self.related_ext_idx,
            "details": self.details,
        }


@dataclass
class FalseNegativeReport:
    """Complete false negative analysis report."""
    sample_id: str = ""

    # Counts
    total_false_negatives: int = 0
    extracted_note_count: int = 0
    ground_truth_note_count: int = 0

    # By category
    by_category: Dict[FNCategory, List[FalseNegativeNote]] = field(default_factory=dict)
    category_counts: Dict[str, int] = field(default_factory=dict)
    category_percentages: Dict[str, float] = field(default_factory=dict)

    # Classified FNs
    classified_fns: List[FalseNegativeNote] = field(default_factory=list)

    # Distributions
    fn_time_distribution: List[float] = field(default_factory=list)
    fn_pitch_distribution: List[int] = field(default_factory=list)
    fn_velocity_distribution: List[int] = field(default_factory=list)
    fn_duration_distribution: List[float] = field(default_factory=list)

    # Statistics
    avg_missed_velocity: float = 0.0
    avg_missed_duration_ms: float = 0.0
    pitch_range_missed: Tuple[int, int] = (0, 0)

    # Top categories
    top_categories: List[Tuple[str, int, float]] = field(default_factory=list)

    # Extraction blind spots
    blind_spots: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sample_id": self.sample_id,
            "counts": {
                "total_fns": self.total_false_negatives,
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
            "statistics": {
                "avg_missed_velocity": self.avg_missed_velocity,
                "avg_missed_duration_ms": self.avg_missed_duration_ms,
                "pitch_range_missed": self.pitch_range_missed,
            },
            "blind_spots": self.blind_spots,
        }

    def summary(self) -> str:
        lines = [
            f"False Negative Analysis: {self.sample_id}",
            "=" * 60,
            "",
            f"Total FNs: {self.total_false_negatives} / {self.ground_truth_note_count} GT ({self.total_false_negatives / max(self.ground_truth_note_count, 1):.1%} missed)",
            "",
            "By Category:",
        ]

        for cat, count, pct in self.top_categories:
            lines.append(f"  {cat:25s} {count:4d} ({pct:5.1%})")

        lines.extend([
            "",
            "Missed Note Characteristics:",
            f"  Avg velocity: {self.avg_missed_velocity:.0f}/127",
            f"  Avg duration: {self.avg_missed_duration_ms:.0f}ms",
            f"  Pitch range: {self.pitch_range_missed[0]}-{self.pitch_range_missed[1]}",
        ])

        if self.blind_spots:
            lines.extend(["", "Extraction Blind Spots:"])
            for spot, value in self.blind_spots.items():
                lines.append(f"  {spot}: {value}")

        return "\n".join(lines)


class FalseNegativeAnalyzer:
    """Analyzes false negatives to identify extraction blind spots.

    This helps identify systematic recall failures and
    guides improvements to note detection sensitivity.
    """

    def __init__(
        self,
        onset_tolerance_ms: float = 50.0,
        short_note_threshold_ms: float = 80.0,
        quiet_velocity_threshold: int = 40,
        loud_velocity_threshold: int = 120,
        low_pitch_threshold: int = 40,  # E2
        high_pitch_threshold: int = 96,  # C7
    ):
        """Initialize the analyzer.

        Args:
            onset_tolerance_ms: Tolerance for matching
            short_note_threshold_ms: Notes shorter than this are "fast"
            quiet_velocity_threshold: Velocities below this are "quiet"
            loud_velocity_threshold: Velocities above this are "loud"
            low_pitch_threshold: Pitches below this are "low frequency"
            high_pitch_threshold: Pitches above this are "high frequency"
        """
        self.onset_tolerance_ms = onset_tolerance_ms
        self.short_note_threshold_ms = short_note_threshold_ms
        self.quiet_velocity_threshold = quiet_velocity_threshold
        self.loud_velocity_threshold = loud_velocity_threshold
        self.low_pitch_threshold = low_pitch_threshold
        self.high_pitch_threshold = high_pitch_threshold

    def analyze(
        self,
        extracted_notes: List[Tuple[int, float, float, int]],
        ground_truth_notes: List[Tuple[int, float, float, int]],
        matched_gt_indices: Set[int],
        sample_id: str = "",
    ) -> FalseNegativeReport:
        """Analyze false negatives (missed GT notes).

        Args:
            extracted_notes: All extracted notes
            ground_truth_notes: All ground truth notes
            matched_gt_indices: Indices of GT notes that were matched
            sample_id: Sample identifier

        Returns:
            FalseNegativeReport with classified FNs
        """
        report = FalseNegativeReport(sample_id=sample_id)
        report.extracted_note_count = len(extracted_notes)
        report.ground_truth_note_count = len(ground_truth_notes)

        # Find false negatives (unmatched GT notes)
        fn_indices = [i for i in range(len(ground_truth_notes)) if i not in matched_gt_indices]
        report.total_false_negatives = len(fn_indices)

        # Initialize category dict
        for cat in FNCategory:
            report.by_category[cat] = []

        velocities = []
        durations = []

        # Classify each FN
        for idx in fn_indices:
            fn_note = ground_truth_notes[idx]
            classification = self._classify_fn(
                fn_note, idx, extracted_notes, ground_truth_notes, matched_gt_indices
            )
            report.classified_fns.append(classification)
            report.by_category[classification.category].append(classification)

            # Collect distributions
            report.fn_time_distribution.append(fn_note[1])
            report.fn_pitch_distribution.append(fn_note[0])
            report.fn_velocity_distribution.append(fn_note[3])

            duration_ms = (fn_note[2] - fn_note[1]) * 1000
            report.fn_duration_distribution.append(duration_ms)
            velocities.append(fn_note[3])
            durations.append(duration_ms)

        # Compute statistics
        if velocities:
            report.avg_missed_velocity = np.mean(velocities)
        if durations:
            report.avg_missed_duration_ms = np.mean(durations)
        if report.fn_pitch_distribution:
            report.pitch_range_missed = (
                min(report.fn_pitch_distribution),
                max(report.fn_pitch_distribution)
            )

        # Compute category statistics
        for cat, notes in report.by_category.items():
            count = len(notes)
            report.category_counts[cat.value] = count
            report.category_percentages[cat.value] = (
                count / report.total_false_negatives
                if report.total_false_negatives > 0 else 0
            )

        # Sort top categories
        sorted_cats = sorted(
            report.category_counts.items(),
            key=lambda x: x[1],
            reverse=True
        )
        report.top_categories = [
            (cat, count, count / max(report.total_false_negatives, 1))
            for cat, count in sorted_cats
            if count > 0
        ]

        # Identify blind spots
        report.blind_spots = self._identify_blind_spots(report)

        return report

    def _classify_fn(
        self,
        fn_note: Tuple[int, float, float, int],
        fn_idx: int,
        extracted: List[Tuple[int, float, float, int]],
        ground_truth: List[Tuple[int, float, float, int]],
        matched_gt_indices: Set[int],
    ) -> FalseNegativeNote:
        """Classify a single false negative note."""
        fn_pitch, fn_onset, fn_offset, fn_vel = fn_note
        fn_duration_ms = (fn_offset - fn_onset) * 1000

        # Check for fast articulation
        if fn_duration_ms < self.short_note_threshold_ms:
            return FalseNegativeNote(
                index=fn_idx,
                note=fn_note,
                category=FNCategory.FAST_ARTICULATION,
                confidence=0.85,
                details=f"Duration only {fn_duration_ms:.0f}ms",
            )

        # Check for extreme velocity
        if fn_vel < self.quiet_velocity_threshold:
            return FalseNegativeNote(
                index=fn_idx,
                note=fn_note,
                category=FNCategory.EXTREME_VELOCITY,
                confidence=0.8,
                details=f"Quiet velocity: {fn_vel}/127",
            )
        if fn_vel > self.loud_velocity_threshold:
            return FalseNegativeNote(
                index=fn_idx,
                note=fn_note,
                category=FNCategory.EXTREME_VELOCITY,
                confidence=0.7,
                details=f"Loud velocity: {fn_vel}/127",
            )

        # Check for low/high frequency
        if fn_pitch < self.low_pitch_threshold:
            return FalseNegativeNote(
                index=fn_idx,
                note=fn_note,
                category=FNCategory.LOW_FREQUENCY,
                confidence=0.8,
                details=f"Low pitch: MIDI {fn_pitch}",
            )
        if fn_pitch > self.high_pitch_threshold:
            return FalseNegativeNote(
                index=fn_idx,
                note=fn_note,
                category=FNCategory.HIGH_FREQUENCY,
                confidence=0.8,
                details=f"High pitch: MIDI {fn_pitch}",
            )

        # Check for masked note
        mask_result = self._check_masked(fn_note, ground_truth, matched_gt_indices)
        if mask_result:
            return FalseNegativeNote(
                index=fn_idx,
                note=fn_note,
                category=FNCategory.MASKED_NOTE,
                confidence=mask_result[0],
                details=mask_result[1],
            )

        # Check for chord failure
        chord_result = self._check_chord_failure(fn_note, fn_idx, ground_truth, matched_gt_indices)
        if chord_result:
            return FalseNegativeNote(
                index=fn_idx,
                note=fn_note,
                category=FNCategory.CHORD_FAILURE,
                confidence=chord_result[0],
                details=chord_result[1],
            )

        # Check for polyphonic collapse
        poly_result = self._check_polyphonic_collapse(fn_note, extracted)
        if poly_result:
            return FalseNegativeNote(
                index=fn_idx,
                note=fn_note,
                category=FNCategory.POLYPHONIC_COLLAPSE,
                confidence=poly_result[0],
                related_ext_idx=poly_result[1],
                details="Likely merged into nearby detected note",
            )

        # Check for timing gap (transient miss)
        timing_result = self._check_timing_gap(fn_note, extracted)
        if timing_result:
            return FalseNegativeNote(
                index=fn_idx,
                note=fn_note,
                category=FNCategory.TIMING_GAP,
                confidence=timing_result[0],
                details=f"Nearest extracted note {timing_result[1]:.0f}ms away",
            )

        # Unknown
        return FalseNegativeNote(
            index=fn_idx,
            note=fn_note,
            category=FNCategory.UNKNOWN,
            confidence=0.0,
            details="No clear pattern",
        )

    def _check_masked(
        self,
        fn_note: Tuple[int, float, float, int],
        ground_truth: List[Tuple[int, float, float, int]],
        matched_gt_indices: Set[int],
    ) -> Optional[Tuple[float, str]]:
        """Check if note was masked by a louder simultaneous note."""
        fn_pitch, fn_onset, fn_offset, fn_vel = fn_note
        onset_tol_sec = self.onset_tolerance_ms / 1000.0

        # Find simultaneous GT notes that WERE matched
        for j, gt in enumerate(ground_truth):
            if j not in matched_gt_indices:
                continue

            gt_pitch, gt_onset, gt_offset, gt_vel = gt

            # Simultaneous
            if abs(gt_onset - fn_onset) < onset_tol_sec:
                # Louder
                if gt_vel > fn_vel + 20:
                    # Similar or lower pitch (more likely to mask)
                    if gt_pitch <= fn_pitch + 5:
                        return (0.75, f"Masked by louder note (vel {gt_vel} vs {fn_vel})")

        return None

    def _check_chord_failure(
        self,
        fn_note: Tuple[int, float, float, int],
        fn_idx: int,
        ground_truth: List[Tuple[int, float, float, int]],
        matched_gt_indices: Set[int],
    ) -> Optional[Tuple[float, str]]:
        """Check if this is part of a chord where some notes were missed."""
        fn_pitch, fn_onset, _, _ = fn_note
        onset_tol_sec = self.onset_tolerance_ms / 1000.0

        # Find all GT notes at same time
        simultaneous_gt = [
            (i, gt) for i, gt in enumerate(ground_truth)
            if abs(gt[1] - fn_onset) < onset_tol_sec
        ]

        if len(simultaneous_gt) < 3:  # Not really a chord
            return None

        # Count how many were matched vs missed
        matched = sum(1 for i, _ in simultaneous_gt if i in matched_gt_indices)
        missed = sum(1 for i, _ in simultaneous_gt if i not in matched_gt_indices)

        if missed >= 2 and missed >= matched:
            return (0.8, f"Part of {len(simultaneous_gt)}-note chord, {missed} missed")

        return None

    def _check_polyphonic_collapse(
        self,
        fn_note: Tuple[int, float, float, int],
        extracted: List[Tuple[int, float, float, int]],
    ) -> Optional[Tuple[float, int]]:
        """Check if note was collapsed into another detection."""
        fn_pitch, fn_onset, _, _ = fn_note
        onset_tol_sec = self.onset_tolerance_ms / 1000.0

        for i, ext in enumerate(extracted):
            ext_pitch, ext_onset, _, _ = ext

            # Similar timing
            if abs(ext_onset - fn_onset) < onset_tol_sec:
                # Adjacent pitch (within 3 semitones)
                if 0 < abs(ext_pitch - fn_pitch) <= 3:
                    return (0.7, i)

        return None

    def _check_timing_gap(
        self,
        fn_note: Tuple[int, float, float, int],
        extracted: List[Tuple[int, float, float, int]],
    ) -> Optional[Tuple[float, float]]:
        """Check if there's no extracted note nearby (timing gap)."""
        fn_pitch, fn_onset, _, _ = fn_note

        # Find nearest extracted note with same pitch
        min_gap_ms = float('inf')
        for ext in extracted:
            ext_pitch, ext_onset, _, _ = ext

            if ext_pitch == fn_pitch:
                gap_ms = abs(ext_onset - fn_onset) * 1000
                min_gap_ms = min(min_gap_ms, gap_ms)

        # If nearest same-pitch note is far away
        if min_gap_ms > 200:  # >200ms away
            return (0.65, min_gap_ms)

        return None

    def _identify_blind_spots(self, report: FalseNegativeReport) -> Dict[str, Any]:
        """Identify systematic extraction blind spots."""
        blind_spots = {}

        if not report.classified_fns:
            return blind_spots

        # Pitch blind spots
        if report.fn_pitch_distribution:
            pitches = report.fn_pitch_distribution
            low_count = sum(1 for p in pitches if p < 48)  # Below C3
            high_count = sum(1 for p in pitches if p > 84)  # Above C6

            total = len(pitches)
            if low_count / total > 0.3:
                blind_spots["low_bass_sensitivity"] = f"{low_count}/{total} missed notes below C3"
            if high_count / total > 0.3:
                blind_spots["high_treble_sensitivity"] = f"{high_count}/{total} missed notes above C6"

        # Velocity blind spots
        if report.fn_velocity_distribution:
            vels = report.fn_velocity_distribution
            quiet_count = sum(1 for v in vels if v < 50)
            total = len(vels)
            if quiet_count / total > 0.3:
                blind_spots["quiet_note_sensitivity"] = f"{quiet_count}/{total} missed notes with velocity < 50"

        # Duration blind spots
        if report.fn_duration_distribution:
            durs = report.fn_duration_distribution
            short_count = sum(1 for d in durs if d < 100)  # <100ms
            total = len(durs)
            if short_count / total > 0.3:
                blind_spots["short_note_sensitivity"] = f"{short_count}/{total} missed notes shorter than 100ms"

        # Polyphony blind spots
        poly_count = report.category_counts.get(FNCategory.POLYPHONIC_COLLAPSE.value, 0)
        chord_count = report.category_counts.get(FNCategory.CHORD_FAILURE.value, 0)
        if poly_count + chord_count > report.total_false_negatives * 0.25:
            blind_spots["polyphony_handling"] = f"{poly_count + chord_count} notes lost to polyphonic collapse"

        return blind_spots
