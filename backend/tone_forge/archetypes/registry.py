"""Archetype registry for looking up production archetypes.

Provides centralized access to all defined archetypes with
fuzzy matching for genre names.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

from .base import ProductionArchetype
from .synthwave import SYNTHWAVE, DARKWAVE, DREAMWAVE
from .shoegaze import SHOEGAZE, DREAM_POP
from .ambient import AMBIENT, DRONE, DARK_AMBIENT

logger = logging.getLogger(__name__)


class ArchetypeRegistry:
    """Registry for production archetypes.

    Provides lookup by genre name with fuzzy matching and
    fallback to related archetypes.
    """

    def __init__(self):
        """Initialize the registry with built-in archetypes."""
        self._archetypes: Dict[str, ProductionArchetype] = {}
        self._genre_aliases: Dict[str, str] = {}

        # Register built-in archetypes
        self._register_builtins()

    def _register_builtins(self):
        """Register all built-in archetypes."""
        # Synthwave family
        self.register(SYNTHWAVE)
        self.register(DARKWAVE)
        self.register(DREAMWAVE)

        # Shoegaze family
        self.register(SHOEGAZE)
        self.register(DREAM_POP)

        # Ambient family
        self.register(AMBIENT)
        self.register(DRONE)
        self.register(DARK_AMBIENT)

        # Set up common aliases
        self._genre_aliases.update({
            "retro": "synthwave",
            "outrun": "synthwave",
            "80s": "synthwave",
            "cyberpunk": "darkwave",
            "vapor": "dreamwave",
            "vaporwave": "dreamwave",
            "chillwave": "dreamwave",
            "gazey": "shoegaze",
            "noise_pop": "shoegaze",
            "ethereal": "dream_pop",
            "dreampop": "dream_pop",
            "atmospheric": "ambient",
            "soundscape": "ambient",
            "meditative": "ambient",
            "space_music": "ambient",
            "new_age": "ambient",
        })

    def register(self, archetype: ProductionArchetype) -> None:
        """Register an archetype.

        Args:
            archetype: Archetype to register
        """
        name = archetype.name.lower()
        self._archetypes[name] = archetype

        # Also register related genres as aliases
        for related in archetype.related_genres:
            related_lower = related.lower()
            if related_lower not in self._archetypes:
                self._genre_aliases[related_lower] = name

        logger.debug(f"Registered archetype: {name}")

    def get(self, genre: str) -> Optional[ProductionArchetype]:
        """Get archetype for a genre.

        Args:
            genre: Genre name to look up

        Returns:
            ProductionArchetype if found, None otherwise
        """
        genre_lower = genre.lower().replace("-", "_").replace(" ", "_")

        # Direct lookup
        if genre_lower in self._archetypes:
            return self._archetypes[genre_lower]

        # Alias lookup
        if genre_lower in self._genre_aliases:
            return self._archetypes[self._genre_aliases[genre_lower]]

        # Fuzzy match
        matched = self._fuzzy_match(genre_lower)
        if matched:
            return self._archetypes[matched]

        return None

    def get_or_default(
        self,
        genre: str,
        default: Optional[ProductionArchetype] = None,
    ) -> ProductionArchetype:
        """Get archetype with fallback to default.

        Args:
            genre: Genre to look up
            default: Default archetype (uses synthwave if None)

        Returns:
            ProductionArchetype
        """
        archetype = self.get(genre)
        if archetype is not None:
            return archetype

        if default is not None:
            return default

        # Use synthwave as ultimate fallback
        return SYNTHWAVE

    def get_best_match(
        self,
        genre: str,
    ) -> Tuple[Optional[ProductionArchetype], float]:
        """Get best matching archetype with confidence score.

        Args:
            genre: Genre to match

        Returns:
            Tuple of (archetype, confidence) where confidence is 0-1
        """
        genre_lower = genre.lower().replace("-", "_").replace(" ", "_")

        # Exact match
        if genre_lower in self._archetypes:
            return self._archetypes[genre_lower], 1.0

        # Alias match
        if genre_lower in self._genre_aliases:
            return self._archetypes[self._genre_aliases[genre_lower]], 0.9

        # Fuzzy match
        matched = self._fuzzy_match(genre_lower)
        if matched:
            # Calculate similarity
            similarity = self._calculate_similarity(genre_lower, matched)
            return self._archetypes[matched], similarity * 0.7

        return None, 0.0

    def list_archetypes(self) -> List[str]:
        """List all registered archetype names.

        Returns:
            List of archetype names
        """
        return list(self._archetypes.keys())

    def list_all_genres(self) -> List[str]:
        """List all recognized genre names (archetypes + aliases).

        Returns:
            List of all genre names
        """
        genres = set(self._archetypes.keys())
        genres.update(self._genre_aliases.keys())

        # Add related genres from all archetypes
        for archetype in self._archetypes.values():
            for related in archetype.related_genres:
                genres.add(related.lower())

        return sorted(genres)

    def get_archetype_info(self) -> List[Dict]:
        """Get info about all archetypes.

        Returns:
            List of archetype info dicts
        """
        return [
            {
                "name": a.name,
                "description": a.description,
                "related_genres": a.related_genres,
                "applicable_stems": a.applicable_stems,
            }
            for a in self._archetypes.values()
        ]

    def _fuzzy_match(self, genre: str) -> Optional[str]:
        """Fuzzy match a genre to an archetype name.

        Args:
            genre: Genre to match

        Returns:
            Matched archetype name or None
        """
        # Check if genre contains any archetype name
        for name in self._archetypes:
            if name in genre or genre in name:
                return name

        # Check related genres of all archetypes
        for archetype in self._archetypes.values():
            for related in archetype.related_genres:
                related_lower = related.lower()
                if related_lower in genre or genre in related_lower:
                    return archetype.name.lower()

        # Word-based matching
        genre_words = set(genre.split("_"))
        for name in self._archetypes:
            name_words = set(name.split("_"))
            if genre_words & name_words:  # Intersection
                return name

        return None

    def _calculate_similarity(self, s1: str, s2: str) -> float:
        """Calculate string similarity.

        Args:
            s1: First string
            s2: Second string

        Returns:
            Similarity score 0-1
        """
        # Simple Jaccard similarity on characters
        set1 = set(s1)
        set2 = set(s2)

        intersection = len(set1 & set2)
        union = len(set1 | set2)

        if union == 0:
            return 0.0

        return intersection / union


# Global registry instance
_registry: Optional[ArchetypeRegistry] = None


def get_registry() -> ArchetypeRegistry:
    """Get the global archetype registry.

    Returns:
        ArchetypeRegistry instance
    """
    global _registry
    if _registry is None:
        _registry = ArchetypeRegistry()
    return _registry


def get_archetype(genre: str) -> Optional[ProductionArchetype]:
    """Get archetype for a genre.

    Args:
        genre: Genre name

    Returns:
        ProductionArchetype if found
    """
    return get_registry().get(genre)


def get_archetype_or_default(
    genre: str,
    default: Optional[ProductionArchetype] = None,
) -> ProductionArchetype:
    """Get archetype with fallback.

    Args:
        genre: Genre name
        default: Default archetype

    Returns:
        ProductionArchetype
    """
    return get_registry().get_or_default(genre, default)
