"""Retrieval-augmented tone reconstruction.

Provides context from historical analyses to improve new reconstructions:

- reference_library: Storage and search for historical tones
- augmented_analysis: Augmentation logic

Usage:
    from tone_forge.ml.retrieval import augment_analysis

    # Augment a new analysis with historical context
    augmented_descriptor, context = augment_analysis(
        descriptor,
        embedding,
        detected_genre="rock",
    )
"""
from __future__ import annotations

from .reference_library import (
    ReferenceLibrary,
    ToneReference,
    RetrievalResult,
    get_library,
)
from .augmented_analysis import (
    RetrievalAugmenter,
    AugmentedContext,
    get_augmenter,
    augment_analysis,
    augment_descriptor,
)

__all__ = [
    # Library
    "ReferenceLibrary",
    "ToneReference",
    "RetrievalResult",
    "get_library",
    # Augmentation
    "RetrievalAugmenter",
    "AugmentedContext",
    "get_augmenter",
    "augment_analysis",
    "augment_descriptor",
]
