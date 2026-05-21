"""Production archetypes for genre-specific reconstruction.

Archetypes encode assumptions about audio characteristics, expected
patterns, and extraction parameters for specific genres/styles.

Usage:
    from tone_forge.archetypes import get_archetype, SYNTHWAVE

    # Get archetype by genre name
    archetype = get_archetype("synthwave")

    # Get extraction parameters
    params = archetype.get_extraction_params(stem_type="synth")

    # Get quality thresholds
    thresholds = archetype.get_quality_thresholds()
"""
from __future__ import annotations

from .base import (
    AudioCharacteristics,
    ExtractionParameters,
    ExpectedPatterns,
    ProductionArchetype,
    TransientClarity,
    HarmonicComplexity,
    RhythmicProfile,
    DynamicRange,
)
from .synthwave import (
    SYNTHWAVE,
    DARKWAVE,
    DREAMWAVE,
    create_synthwave_archetype,
    create_darkwave_archetype,
    create_dreamwave_archetype,
)
from .shoegaze import (
    SHOEGAZE,
    DREAM_POP,
    create_shoegaze_archetype,
    create_dream_pop_archetype,
)
from .ambient import (
    AMBIENT,
    DRONE,
    DARK_AMBIENT,
    create_ambient_archetype,
    create_drone_archetype,
    create_dark_ambient_archetype,
)
from .registry import (
    ArchetypeRegistry,
    get_registry,
    get_archetype,
    get_archetype_or_default,
)
from .priors import (
    ExtractionPriors,
    ValidationBounds,
    ReconstructionPriors,
    get_priors_generator,
    get_extraction_priors,
)

__all__ = [
    # Base types
    "AudioCharacteristics",
    "ExtractionParameters",
    "ExpectedPatterns",
    "ProductionArchetype",
    "TransientClarity",
    "HarmonicComplexity",
    "RhythmicProfile",
    "DynamicRange",
    # Synthwave
    "SYNTHWAVE",
    "DARKWAVE",
    "DREAMWAVE",
    "create_synthwave_archetype",
    "create_darkwave_archetype",
    "create_dreamwave_archetype",
    # Shoegaze
    "SHOEGAZE",
    "DREAM_POP",
    "create_shoegaze_archetype",
    "create_dream_pop_archetype",
    # Ambient
    "AMBIENT",
    "DRONE",
    "DARK_AMBIENT",
    "create_ambient_archetype",
    "create_drone_archetype",
    "create_dark_ambient_archetype",
    # Registry
    "ArchetypeRegistry",
    "get_registry",
    "get_archetype",
    "get_archetype_or_default",
    # Priors
    "ExtractionPriors",
    "ValidationBounds",
    "ReconstructionPriors",
    "get_priors_generator",
    "get_extraction_priors",
]
