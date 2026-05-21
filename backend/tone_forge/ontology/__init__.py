"""Descriptor Ontology for ToneForge.

Defines canonical vocabularies, taxonomies, and semantic relationships
for audio descriptors. This ontology serves as:
- Interoperability contracts
- Training targets
- Retrieval anchors
- Export interfaces
- Semantic graph nodes

Treat these schemas like API contracts:
- Version carefully
- Maintain backward compatibility
- Avoid uncontrolled schema drift

This becomes extremely important as embeddings and retrieval
depend on consistent semantic vocabularies.
"""
from __future__ import annotations

from .amp_families import (
    AMP_FAMILIES,
    AMP_FAMILY_ALIASES,
    AMP_FAMILY_TRAITS,
    get_amp_family_info,
    normalize_amp_family,
    get_related_families,
)
from .effects import (
    EFFECT_TYPES,
    EFFECT_SUBTYPES,
    EFFECT_TRAITS,
    normalize_effect_type,
)
from .speakers import (
    SPEAKER_CHARACTERS,
    SPEAKER_ALIASES,
    CAB_CONFIGURATIONS,
    normalize_speaker_character,
)
from .production import (
    PRODUCTION_STYLES,
    GENRE_MAPPINGS,
    get_style_defaults,
)
from .schema import (
    ONTOLOGY_VERSION,
    validate_descriptor,
    migrate_descriptor,
)

__all__ = [
    # Amp families
    "AMP_FAMILIES",
    "AMP_FAMILY_ALIASES",
    "AMP_FAMILY_TRAITS",
    "get_amp_family_info",
    "normalize_amp_family",
    "get_related_families",
    # Effects
    "EFFECT_TYPES",
    "EFFECT_SUBTYPES",
    "EFFECT_TRAITS",
    "normalize_effect_type",
    # Speakers
    "SPEAKER_CHARACTERS",
    "SPEAKER_ALIASES",
    "CAB_CONFIGURATIONS",
    "normalize_speaker_character",
    # Production
    "PRODUCTION_STYLES",
    "GENRE_MAPPINGS",
    "get_style_defaults",
    # Schema
    "ONTOLOGY_VERSION",
    "validate_descriptor",
    "migrate_descriptor",
]
