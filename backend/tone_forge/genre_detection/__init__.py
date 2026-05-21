"""Genre detection and production archetypes.

Provides genre classification and genre-specific recommendations:

- Classifier: Multi-label genre classification from audio
- Archetypes: Production templates for different styles
- Style hints: Genre-specific tweaking suggestions

All modules fall back to heuristics when ML models aren't available.
"""
from __future__ import annotations

from .classifier import (
    GenreClassifier,
    GenreFeatures,
    GenrePrediction,
    GENRES,
    SUBGENRES,
    get_classifier,
    classify_genre,
    extract_genre_features,
)
from .archetypes import (
    ToneArchetype,
    EffectChainTemplate,
    ARCHETYPES,
    get_archetype,
    get_archetype_for_genre,
    list_archetypes,
    get_archetype_categories,
)
from .style_hints import (
    StyleHint,
    generate_genre_hints,
    format_hints_for_display,
    get_quick_tips,
)

__all__ = [
    # Classification
    "GenreClassifier",
    "GenreFeatures",
    "GenrePrediction",
    "GENRES",
    "SUBGENRES",
    "get_classifier",
    "classify_genre",
    "extract_genre_features",
    # Archetypes
    "ToneArchetype",
    "EffectChainTemplate",
    "ARCHETYPES",
    "get_archetype",
    "get_archetype_for_genre",
    "list_archetypes",
    "get_archetype_categories",
    # Style hints
    "StyleHint",
    "generate_genre_hints",
    "format_hints_for_display",
    "get_quick_tips",
]


def analyze_and_recommend(
    audio,
    sr: int = 22050,
    descriptor: dict = None,
) -> dict:
    """Full genre analysis with archetype and hints.

    Convenience function that runs full analysis pipeline.

    Args:
        audio: Audio array
        sr: Sample rate
        descriptor: Optional ToneDescriptor as dict

    Returns:
        Dictionary with genre, archetype, and hints
    """
    # Classify genre
    prediction = classify_genre(audio, sr)

    # Get matching archetype
    archetype = get_archetype_for_genre(
        prediction.primary_genre,
        prediction.primary_subgenre,
    )

    # Generate hints
    hints = generate_genre_hints(prediction, descriptor)

    return {
        "genre": prediction.primary_genre,
        "genre_confidence": prediction.primary_confidence,
        "subgenre": prediction.primary_subgenre,
        "secondary_genres": prediction.secondary_genres,
        "archetype": archetype.name if archetype else None,
        "archetype_display": archetype.display_name if archetype else None,
        "hints": format_hints_for_display(hints),
        "production_era": prediction.production_era,
        "is_distorted": prediction.is_distorted,
        "is_clean": prediction.is_clean,
        "quick_tips": get_quick_tips(prediction.primary_genre),
    }
