"""Retrieval-augmented analysis for improved tone reconstruction.

Augments new analyses with context from similar historical tones:
- Retrieves k most similar references
- Extracts consensus patterns and confidence boosts
- Provides additional context for translation
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple

import numpy as np

from .reference_library import (
    ReferenceLibrary,
    ToneReference,
    RetrievalResult,
    get_library,
)

logger = logging.getLogger(__name__)


@dataclass
class AugmentedContext:
    """Context from retrieval augmentation."""

    # Retrieved references
    similar_references: List[RetrievalResult]

    # Consensus from references
    consensus_amp_family: Optional[str] = None
    consensus_genre: Optional[str] = None
    consensus_archetype: Optional[str] = None

    # Confidence adjustments
    amp_confidence_boost: float = 0.0
    genre_confidence_boost: float = 0.0

    # Suggested adjustments based on successful exports
    suggested_gain_adjustment: float = 0.0
    suggested_effects: List[str] = field(default_factory=list)

    # Quality indicators
    retrieval_quality: float = 0.0  # How good the matches were
    consensus_strength: float = 0.0  # How much references agree

    def to_dict(self) -> Dict[str, Any]:
        return {
            "similar_count": len(self.similar_references),
            "consensus_amp_family": self.consensus_amp_family,
            "consensus_genre": self.consensus_genre,
            "consensus_archetype": self.consensus_archetype,
            "amp_confidence_boost": self.amp_confidence_boost,
            "genre_confidence_boost": self.genre_confidence_boost,
            "suggested_gain_adjustment": self.suggested_gain_adjustment,
            "suggested_effects": self.suggested_effects,
            "retrieval_quality": self.retrieval_quality,
            "consensus_strength": self.consensus_strength,
        }


class RetrievalAugmenter:
    """Augments analyses with retrieved context."""

    def __init__(
        self,
        library: Optional[ReferenceLibrary] = None,
        k: int = 5,
        min_similarity: float = 0.4,
    ):
        """Initialize the augmenter.

        Args:
            library: Reference library instance
            k: Number of references to retrieve
            min_similarity: Minimum similarity threshold
        """
        self.library = library or get_library()
        self.k = k
        self.min_similarity = min_similarity

    def augment_analysis(
        self,
        descriptor: Dict[str, Any],
        embedding: Optional[np.ndarray] = None,
        detected_genre: Optional[str] = None,
    ) -> AugmentedContext:
        """Augment an analysis with retrieved context.

        Args:
            descriptor: ToneDescriptor as dict
            embedding: Optional pre-computed embedding
            detected_genre: Detected genre for filtering

        Returns:
            AugmentedContext with retrieved information
        """
        # If no embedding, try to compute one or use attribute search
        if embedding is None:
            return self._augment_by_attributes(descriptor, detected_genre)

        # Search by embedding
        similar = self.library.search_similar(
            query_embedding=embedding,
            k=self.k,
            min_similarity=self.min_similarity,
            genre_filter=detected_genre,
        )

        if not similar:
            # Fall back to attribute search
            return self._augment_by_attributes(descriptor, detected_genre)

        return self._build_context(similar, descriptor)

    def _augment_by_attributes(
        self,
        descriptor: Dict[str, Any],
        detected_genre: Optional[str],
    ) -> AugmentedContext:
        """Augment using attribute-based search.

        Args:
            descriptor: ToneDescriptor as dict
            detected_genre: Genre filter

        Returns:
            AugmentedContext
        """
        amp_data = descriptor.get("amp", {})
        amp_family = amp_data.get("family")
        gain = amp_data.get("gain", 0.5)

        # Search by attributes
        references = self.library.search_by_attributes(
            amp_family=amp_family,
            genre=detected_genre,
            gain_range=(max(0, gain - 0.2), min(1, gain + 0.2)),
            exported_only=False,
            limit=self.k,
        )

        if not references:
            return AugmentedContext(similar_references=[])

        # Convert to RetrievalResult with estimated similarity
        similar = []
        for ref in references:
            # Estimate similarity from attribute match
            sim = 0.5  # Base similarity

            if ref.amp_family == amp_family:
                sim += 0.2
            if ref.genre == detected_genre:
                sim += 0.15
            if abs(ref.gain_level - gain) < 0.15:
                sim += 0.15

            similar.append(RetrievalResult(
                reference=ref,
                similarity=min(1.0, sim),
                match_reasons=["Attribute-based match"],
            ))

        return self._build_context(similar, descriptor)

    def _build_context(
        self,
        similar: List[RetrievalResult],
        descriptor: Dict[str, Any],
    ) -> AugmentedContext:
        """Build augmented context from similar references.

        Args:
            similar: List of similar references
            descriptor: Original descriptor

        Returns:
            AugmentedContext
        """
        context = AugmentedContext(similar_references=similar)

        if not similar:
            return context

        # Calculate retrieval quality
        similarities = [r.similarity for r in similar]
        context.retrieval_quality = sum(similarities) / len(similarities)

        # Find consensus amp family
        amp_families = [r.reference.amp_family for r in similar if r.reference.amp_family]
        if amp_families:
            context.consensus_amp_family = max(set(amp_families), key=amp_families.count)
            consensus_count = amp_families.count(context.consensus_amp_family)
            context.consensus_strength = consensus_count / len(amp_families)

            # Boost confidence if consensus matches
            current_family = descriptor.get("amp", {}).get("family")
            if current_family == context.consensus_amp_family:
                context.amp_confidence_boost = 0.1 * context.consensus_strength

        # Find consensus genre
        genres = [r.reference.genre for r in similar if r.reference.genre]
        if genres:
            context.consensus_genre = max(set(genres), key=genres.count)
            genre_consensus = genres.count(context.consensus_genre) / len(genres)
            context.genre_confidence_boost = 0.1 * genre_consensus

        # Find consensus archetype
        archetypes = [r.reference.archetype for r in similar if r.reference.archetype]
        if archetypes:
            context.consensus_archetype = max(set(archetypes), key=archetypes.count)

        # Analyze successful exports for suggestions
        exported_refs = [r for r in similar if r.reference.was_exported]
        if exported_refs:
            # Calculate average gain adjustment
            current_gain = descriptor.get("amp", {}).get("gain", 0.5)
            exported_gains = [r.reference.gain_level for r in exported_refs]
            avg_exported_gain = sum(exported_gains) / len(exported_gains)
            context.suggested_gain_adjustment = avg_exported_gain - current_gain

            # Collect common effects from successful exports
            effect_counts = {}
            for r in exported_refs:
                for eff in r.reference.effect_types:
                    effect_counts[eff] = effect_counts.get(eff, 0) + 1

            # Suggest effects that appear in >50% of exports
            threshold = len(exported_refs) * 0.5
            current_effects = set(descriptor.get("effects", {}).keys())
            for eff, count in effect_counts.items():
                if count >= threshold and eff not in current_effects:
                    context.suggested_effects.append(eff)

        return context

    def store_analysis(
        self,
        descriptor: Dict[str, Any],
        embedding: Optional[np.ndarray] = None,
        genre: Optional[str] = None,
        subgenre: Optional[str] = None,
        archetype: Optional[str] = None,
        confidence: float = 0.0,
    ) -> str:
        """Store an analysis in the reference library.

        Args:
            descriptor: ToneDescriptor as dict
            embedding: Pre-computed embedding
            genre: Detected genre
            subgenre: Detected subgenre
            archetype: Assigned archetype
            confidence: Analysis confidence

        Returns:
            Reference ID
        """
        return self.library.add_reference(
            descriptor=descriptor,
            embedding=embedding,
            genre=genre,
            subgenre=subgenre,
            archetype=archetype,
            confidence=confidence,
            source="analysis",
        )

    def mark_successful(self, reference_id: str, rating: Optional[float] = None):
        """Mark a reference as successfully exported.

        Args:
            reference_id: Reference ID
            rating: Optional user rating
        """
        self.library.mark_exported(reference_id)
        if rating is not None:
            self.library.update_rating(reference_id, rating)


def augment_descriptor(
    descriptor: Dict[str, Any],
    context: AugmentedContext,
) -> Dict[str, Any]:
    """Apply augmentation context to a descriptor.

    Args:
        descriptor: Original ToneDescriptor as dict
        context: Augmentation context

    Returns:
        Augmented descriptor
    """
    augmented = descriptor.copy()

    # Add retrieval metadata
    augmented["_retrieval"] = {
        "augmented": True,
        "similar_count": len(context.similar_references),
        "retrieval_quality": context.retrieval_quality,
        "consensus_strength": context.consensus_strength,
    }

    # Apply confidence boosts
    if "amp" in augmented:
        augmented["amp"] = augmented["amp"].copy()
        current_confidence = augmented["amp"].get("confidence", 0.7)
        augmented["amp"]["confidence"] = min(1.0, current_confidence + context.amp_confidence_boost)

        # Add consensus info
        if context.consensus_amp_family:
            augmented["amp"]["consensus_family"] = context.consensus_amp_family

    # Add genre consensus
    if context.consensus_genre:
        augmented["_retrieval"]["consensus_genre"] = context.consensus_genre
        augmented["_retrieval"]["genre_confidence_boost"] = context.genre_confidence_boost

    # Add archetype suggestion
    if context.consensus_archetype:
        augmented["_retrieval"]["suggested_archetype"] = context.consensus_archetype

    # Add effect suggestions
    if context.suggested_effects:
        augmented["_retrieval"]["suggested_effects"] = context.suggested_effects

    # Add gain suggestion
    if abs(context.suggested_gain_adjustment) > 0.05:
        augmented["_retrieval"]["suggested_gain_adjustment"] = context.suggested_gain_adjustment

    return augmented


# Singleton instance
_augmenter: Optional[RetrievalAugmenter] = None


def get_augmenter() -> RetrievalAugmenter:
    """Get the singleton augmenter instance.

    Returns:
        RetrievalAugmenter instance
    """
    global _augmenter
    if _augmenter is None:
        _augmenter = RetrievalAugmenter()
    return _augmenter


def augment_analysis(
    descriptor: Dict[str, Any],
    embedding: Optional[np.ndarray] = None,
    detected_genre: Optional[str] = None,
) -> Tuple[Dict[str, Any], AugmentedContext]:
    """Convenience function to augment an analysis.

    Args:
        descriptor: ToneDescriptor as dict
        embedding: Optional embedding
        detected_genre: Optional genre filter

    Returns:
        Tuple of (augmented_descriptor, context)
    """
    augmenter = get_augmenter()
    context = augmenter.augment_analysis(descriptor, embedding, detected_genre)
    augmented = augment_descriptor(descriptor, context)
    return augmented, context
