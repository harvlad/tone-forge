"""Profile analysis module for MIDI extraction.

Provides tools for validating and optimizing extraction profiles:
- ProfileValidator: Ensure profiles meet expectations
- ProfileOptimizer: Grid search for optimal parameters
- ProfileComparator: Compare profiles side-by-side
"""

from .profile_validator import (
    ProfileExpectation,
    ValidationResult,
    ProfileValidator,
    validate_profile,
)
from .profile_optimizer import (
    OptimizationConfig,
    OptimizationResult,
    ProfileOptimizer,
    ParameterSensitivity,
    analyze_parameter_sensitivity,
)

__all__ = [
    # Validation
    "ProfileExpectation",
    "ValidationResult",
    "ProfileValidator",
    "validate_profile",
    # Optimization
    "OptimizationConfig",
    "OptimizationResult",
    "ProfileOptimizer",
    "ParameterSensitivity",
    "analyze_parameter_sensitivity",
]
