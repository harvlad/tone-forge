"""Benchmark validation module for rigorous metric verification.

This module provides tools to validate that benchmark F1 scores are
trustworthy and not artifacts of:
- Bad matching logic
- Timing tolerance abuse
- Duplicate counting
- Track filtering issues
- MIDI alignment bugs
- Overfitting to specific datasets

PHASE 1: Benchmark validity auditing
PHASE 2: Matching strategy comparison (Strict/Musical/Reconstruction)
PHASE 3: Cross-dataset validation
PHASE 4: False positive analysis
PHASE 5: False negative analysis
PHASE 6: Confidence calibration validation
PHASE 7: Human usability scoring
"""

from .matching_auditor import (
    MatchingAuditor,
    MatchingAuditReport,
    MatchIssue,
    SuspiciousMatch,
    DuplicateInflation,
)
from .matching_strategies import (
    MatchMode,
    MatchingStrategy,
    StrictMatcher,
    MusicalMatcher,
    ReconstructionMatcher,
    NoteMatch,
    MatchResult,
    StrategyComparison,
    compare_strategies,
)
from .false_positive_analyzer import (
    FalsePositiveAnalyzer,
    FPCategory,
    FalsePositiveNote,
    FalsePositiveReport,
)
from .false_negative_analyzer import (
    FalseNegativeAnalyzer,
    FNCategory,
    FalseNegativeNote,
    FalseNegativeReport,
)
from .validation_runner import (
    BenchmarkValidationRunner,
    ValidationSampleResult,
    BenchmarkValidationReport,
)
from .usability_scorer import (
    UsabilityScorer,
    UsabilityRating,
    UsabilityReport,
    EditabilityScore,
    MusicalIntentScore,
    TimingNaturalnessScore,
    HallucinationTolerability,
)

__all__ = [
    # Auditing
    "MatchingAuditor",
    "MatchingAuditReport",
    "MatchIssue",
    "SuspiciousMatch",
    "DuplicateInflation",
    # Strategies
    "MatchMode",
    "MatchingStrategy",
    "StrictMatcher",
    "MusicalMatcher",
    "ReconstructionMatcher",
    "NoteMatch",
    "MatchResult",
    "StrategyComparison",
    "compare_strategies",
    # FP analysis
    "FalsePositiveAnalyzer",
    "FPCategory",
    "FalsePositiveNote",
    "FalsePositiveReport",
    # FN analysis
    "FalseNegativeAnalyzer",
    "FNCategory",
    "FalseNegativeNote",
    "FalseNegativeReport",
    # Validation runner
    "BenchmarkValidationRunner",
    "ValidationSampleResult",
    "BenchmarkValidationReport",
    # Usability
    "UsabilityScorer",
    "UsabilityRating",
    "UsabilityReport",
    "EditabilityScore",
    "MusicalIntentScore",
    "TimingNaturalnessScore",
    "HallucinationTolerability",
]
