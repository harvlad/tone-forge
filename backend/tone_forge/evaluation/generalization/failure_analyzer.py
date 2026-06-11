"""Failure pattern analysis for MIDI extraction.

Identifies common failure patterns by genre and characteristics,
helping to understand why extraction fails in specific cases.
"""
from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class FailurePattern:
    """A detected failure pattern."""

    pattern_type: str  # "octave_error", "timing_drift", "missing_notes", etc.
    description: str
    frequency: float  # How often this pattern occurs (0-1)
    affected_samples: int
    severity: str  # "low", "medium", "high"

    # Pattern-specific details
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pattern_type": self.pattern_type,
            "description": self.description,
            "frequency": self.frequency,
            "affected_samples": self.affected_samples,
            "severity": self.severity,
            "details": self.details,
        }


@dataclass
class GenreFailureProfile:
    """Failure profile for a specific genre."""

    genre: str
    total_samples: int
    failed_samples: int  # F1 < threshold

    # Failure patterns
    patterns: List[FailurePattern] = field(default_factory=list)

    # Characteristics of failing samples
    common_characteristics: List[str] = field(default_factory=list)

    # Suggested fixes
    suggested_fixes: List[str] = field(default_factory=list)

    @property
    def failure_rate(self) -> float:
        """Percentage of samples that failed."""
        return self.failed_samples / self.total_samples if self.total_samples > 0 else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "genre": self.genre,
            "total_samples": self.total_samples,
            "failed_samples": self.failed_samples,
            "failure_rate": self.failure_rate,
            "patterns": [p.to_dict() for p in self.patterns],
            "common_characteristics": self.common_characteristics,
            "suggested_fixes": self.suggested_fixes,
        }

    def summary(self) -> str:
        """Generate summary."""
        lines = [
            f"Genre: {self.genre}",
            f"Failure rate: {self.failure_rate:.1%} ({self.failed_samples}/{self.total_samples})",
        ]

        if self.patterns:
            lines.append("Patterns:")
            for p in sorted(self.patterns, key=lambda x: -x.frequency)[:5]:
                lines.append(f"  - {p.pattern_type}: {p.frequency:.1%} ({p.description})")

        if self.common_characteristics:
            lines.append("Common characteristics:")
            for c in self.common_characteristics[:3]:
                lines.append(f"  - {c}")

        if self.suggested_fixes:
            lines.append("Suggested fixes:")
            for f in self.suggested_fixes[:3]:
                lines.append(f"  - {f}")

        return "\n".join(lines)


@dataclass
class FailureAnalysisResult:
    """Complete failure analysis across all genres."""

    per_genre_profiles: Dict[str, GenreFailureProfile] = field(default_factory=dict)

    # Cross-genre patterns
    common_patterns_across_genres: List[FailurePattern] = field(default_factory=list)

    # Summary
    total_samples: int = 0
    total_failures: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "per_genre_profiles": {
                k: v.to_dict() for k, v in self.per_genre_profiles.items()
            },
            "common_patterns_across_genres": [p.to_dict() for p in self.common_patterns_across_genres],
            "summary": {
                "total_samples": self.total_samples,
                "total_failures": self.total_failures,
                "overall_failure_rate": self.total_failures / self.total_samples if self.total_samples > 0 else 0,
            },
        }

    def summary(self) -> str:
        """Generate overall summary."""
        failure_rate = self.total_failures / self.total_samples if self.total_samples > 0 else 0
        lines = [
            "Failure Analysis Summary",
            "=" * 50,
            f"Total samples: {self.total_samples}",
            f"Total failures: {self.total_failures} ({failure_rate:.1%})",
            "",
        ]

        # Sort genres by failure rate
        sorted_genres = sorted(
            self.per_genre_profiles.items(),
            key=lambda x: x[1].failure_rate,
            reverse=True,
        )

        lines.append("Per-Genre Breakdown:")
        for genre, profile in sorted_genres:
            status = "HIGH FAILURE" if profile.failure_rate > 0.3 else ""
            lines.append(
                f"  {genre:15s}: {profile.failure_rate:.1%} "
                f"({profile.failed_samples}/{profile.total_samples}) {status}"
            )

        if self.common_patterns_across_genres:
            lines.extend(["", "Common Patterns Across Genres:"])
            for p in self.common_patterns_across_genres[:5]:
                lines.append(f"  - {p.pattern_type}: {p.description}")

        return "\n".join(lines)


@dataclass
class SampleFailureInfo:
    """Failure information for a single sample."""

    sample_id: str
    genre: str
    f1: float
    precision: float
    recall: float

    # Error details
    false_positive_count: int = 0
    false_negative_count: int = 0
    octave_error_count: int = 0
    timing_error_count: int = 0

    # Sample characteristics
    characteristics: List[str] = field(default_factory=list)  # ["heavy_reverb", "fast_tempo"]


class FailureAnalyzer:
    """Analyzes failure patterns in MIDI extraction."""

    def __init__(
        self,
        failure_threshold: float = 0.5,  # F1 below this is "failed"
    ):
        """Initialize analyzer.

        Args:
            failure_threshold: F1 threshold below which sample is considered failed
        """
        self.failure_threshold = failure_threshold

    def analyze(
        self,
        samples: List[SampleFailureInfo],
    ) -> FailureAnalysisResult:
        """Analyze failure patterns across samples.

        Args:
            samples: List of sample failure information

        Returns:
            FailureAnalysisResult
        """
        # Group by genre
        by_genre: Dict[str, List[SampleFailureInfo]] = {}
        for sample in samples:
            if sample.genre not in by_genre:
                by_genre[sample.genre] = []
            by_genre[sample.genre].append(sample)

        # Analyze each genre
        per_genre_profiles = {}
        for genre, genre_samples in by_genre.items():
            profile = self._analyze_genre(genre, genre_samples)
            per_genre_profiles[genre] = profile

        # Find patterns common across genres
        common_patterns = self._find_common_patterns(per_genre_profiles)

        # Totals
        total_samples = len(samples)
        total_failures = sum(1 for s in samples if s.f1 < self.failure_threshold)

        return FailureAnalysisResult(
            per_genre_profiles=per_genre_profiles,
            common_patterns_across_genres=common_patterns,
            total_samples=total_samples,
            total_failures=total_failures,
        )

    def _analyze_genre(
        self,
        genre: str,
        samples: List[SampleFailureInfo],
    ) -> GenreFailureProfile:
        """Analyze failures for a single genre."""
        failed = [s for s in samples if s.f1 < self.failure_threshold]

        # Detect patterns
        patterns = []

        # Check for octave errors
        octave_error_samples = [s for s in failed if s.octave_error_count > 0]
        if octave_error_samples:
            freq = len(octave_error_samples) / len(failed) if failed else 0
            patterns.append(FailurePattern(
                pattern_type="octave_errors",
                description="Notes detected in wrong octave",
                frequency=freq,
                affected_samples=len(octave_error_samples),
                severity="high" if freq > 0.5 else "medium",
                details={
                    "avg_octave_errors": np.mean([s.octave_error_count for s in octave_error_samples]),
                },
            ))

        # Check for timing errors
        timing_error_samples = [s for s in failed if s.timing_error_count > 0]
        if timing_error_samples:
            freq = len(timing_error_samples) / len(failed) if failed else 0
            patterns.append(FailurePattern(
                pattern_type="timing_errors",
                description="Notes have incorrect onset/offset timing",
                frequency=freq,
                affected_samples=len(timing_error_samples),
                severity="medium",
                details={
                    "avg_timing_errors": np.mean([s.timing_error_count for s in timing_error_samples]),
                },
            ))

        # Check for high false positive rate
        high_fp_samples = [s for s in failed if s.precision < 0.5 and s.false_positive_count > 5]
        if high_fp_samples:
            freq = len(high_fp_samples) / len(failed) if failed else 0
            patterns.append(FailurePattern(
                pattern_type="excessive_false_positives",
                description="Too many notes detected that aren't in ground truth",
                frequency=freq,
                affected_samples=len(high_fp_samples),
                severity="high" if freq > 0.3 else "medium",
                details={
                    "avg_fp_count": np.mean([s.false_positive_count for s in high_fp_samples]),
                },
            ))

        # Check for high false negative rate
        high_fn_samples = [s for s in failed if s.recall < 0.5 and s.false_negative_count > 5]
        if high_fn_samples:
            freq = len(high_fn_samples) / len(failed) if failed else 0
            patterns.append(FailurePattern(
                pattern_type="missing_notes",
                description="Too many ground truth notes not detected",
                frequency=freq,
                affected_samples=len(high_fn_samples),
                severity="high" if freq > 0.3 else "medium",
                details={
                    "avg_fn_count": np.mean([s.false_negative_count for s in high_fn_samples]),
                },
            ))

        # Find common characteristics
        all_characteristics = []
        for s in failed:
            all_characteristics.extend(s.characteristics)

        char_counts = Counter(all_characteristics)
        common_chars = [
            char for char, count in char_counts.most_common(5)
            if count > len(failed) * 0.3  # Present in >30% of failures
        ]

        # Generate suggestions
        suggestions = self._generate_suggestions(patterns, common_chars, genre)

        return GenreFailureProfile(
            genre=genre,
            total_samples=len(samples),
            failed_samples=len(failed),
            patterns=patterns,
            common_characteristics=common_chars,
            suggested_fixes=suggestions,
        )

    def _find_common_patterns(
        self,
        profiles: Dict[str, GenreFailureProfile],
    ) -> List[FailurePattern]:
        """Find patterns that appear across multiple genres."""
        pattern_counts: Dict[str, int] = {}
        pattern_details: Dict[str, List[FailurePattern]] = {}

        for profile in profiles.values():
            for pattern in profile.patterns:
                if pattern.pattern_type not in pattern_counts:
                    pattern_counts[pattern.pattern_type] = 0
                    pattern_details[pattern.pattern_type] = []
                pattern_counts[pattern.pattern_type] += 1
                pattern_details[pattern.pattern_type].append(pattern)

        # Patterns appearing in >50% of genres are "common"
        threshold = len(profiles) * 0.5
        common = []

        for pattern_type, count in pattern_counts.items():
            if count >= threshold:
                patterns = pattern_details[pattern_type]
                avg_freq = np.mean([p.frequency for p in patterns])
                total_affected = sum(p.affected_samples for p in patterns)

                common.append(FailurePattern(
                    pattern_type=pattern_type,
                    description=patterns[0].description,
                    frequency=avg_freq,
                    affected_samples=total_affected,
                    severity="high" if avg_freq > 0.5 else "medium",
                    details={"genres_affected": count},
                ))

        return sorted(common, key=lambda p: -p.frequency)

    def _generate_suggestions(
        self,
        patterns: List[FailurePattern],
        characteristics: List[str],
        genre: str,
    ) -> List[str]:
        """Generate fix suggestions based on patterns and characteristics."""
        suggestions = []

        for pattern in patterns:
            if pattern.pattern_type == "octave_errors":
                suggestions.append("Enable octave correction or adjust sub-harmonic cleanup")
            elif pattern.pattern_type == "timing_errors":
                suggestions.append("Reduce quantization strength to preserve original timing")
            elif pattern.pattern_type == "excessive_false_positives":
                suggestions.append("Increase onset threshold or enable harmonic suppression")
            elif pattern.pattern_type == "missing_notes":
                suggestions.append("Decrease onset threshold or reduce minimum note duration")

        for char in characteristics:
            if "reverb" in char.lower():
                suggestions.append("This genre has heavy reverb - consider delay cleanup pass")
            elif "fast" in char.lower() or "staccato" in char.lower():
                suggestions.append("Fast notes present - reduce min_note_ms and merge_max_gap")
            elif "polyphonic" in char.lower() or "chord" in char.lower():
                suggestions.append("Polyphonic content - adjust harmonic suppression settings")

        return list(set(suggestions))[:5]  # Deduplicate and limit


def analyze_failures(
    samples: List[SampleFailureInfo],
    failure_threshold: float = 0.5,
) -> FailureAnalysisResult:
    """Convenience function for failure analysis.

    Args:
        samples: Sample failure information
        failure_threshold: F1 threshold for failure

    Returns:
        FailureAnalysisResult
    """
    analyzer = FailureAnalyzer(failure_threshold=failure_threshold)
    return analyzer.analyze(samples)


def create_failure_info_from_results(
    sample_id: str,
    genre: str,
    f1: float,
    precision: float,
    recall: float,
    extracted_count: int,
    ground_truth_count: int,
    true_positives: int,
    characteristics: Optional[List[str]] = None,
) -> SampleFailureInfo:
    """Helper to create SampleFailureInfo from benchmark results.

    Args:
        sample_id: Sample identifier
        genre: Genre name
        f1: F1 score
        precision: Precision score
        recall: Recall score
        extracted_count: Number of extracted notes
        ground_truth_count: Number of ground truth notes
        true_positives: Number of matched notes
        characteristics: Optional list of characteristics

    Returns:
        SampleFailureInfo
    """
    false_positives = extracted_count - true_positives
    false_negatives = ground_truth_count - true_positives

    return SampleFailureInfo(
        sample_id=sample_id,
        genre=genre,
        f1=f1,
        precision=precision,
        recall=recall,
        false_positive_count=false_positives,
        false_negative_count=false_negatives,
        characteristics=characteristics or [],
    )
