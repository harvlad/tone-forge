"""Feature builder for block ranking.

Builds feature vectors from descriptor + block pairs for use in
the ML ranking model. Features capture:
- Descriptor-block compatibility (family match, gain range)
- User preference signals (history affinity)
- Block characteristics (popularity, category)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple

import numpy as np


@dataclass
class RankingFeatures:
    """Feature vector for ranking a descriptor-block pair.

    Contains 25+ features that capture the compatibility between
    an audio descriptor and a candidate block.
    """

    # Descriptor-block compatibility (10 features)
    family_exact_match: float = 0.0      # 1.0 if block family matches descriptor
    family_related_match: float = 0.0    # 1.0 if block in related families
    gain_in_range: float = 0.0           # 1.0 if descriptor gain in block's range
    gain_distance: float = 0.0           # Distance from block's ideal gain
    voicing_match_bass: float = 0.0      # How well bass matches
    voicing_match_mid: float = 0.0       # How well mid matches
    voicing_match_treble: float = 0.0    # How well treble matches
    style_match: float = 0.0             # Effect style compatibility
    configuration_match: float = 0.0     # Cab config match
    speaker_char_match: float = 0.0      # Speaker character match

    # Block characteristics (8 features)
    block_popularity: float = 0.0        # Usage frequency in exports
    block_avg_rating: float = 0.0        # Average user rating
    block_edit_rate: float = 0.0         # How often users edit this block
    block_category_affinity: float = 0.0 # User's affinity for this category
    block_platform_native: float = 0.0   # 1.0 if native to user's platform
    block_price_tier: float = 0.0        # 0-1 price tier (for pedals)
    block_versatility: float = 0.0       # How many families it covers
    block_is_fallback: float = 0.0       # 1.0 if this is a fallback choice

    # User preference signals (5 features)
    user_used_before: float = 0.0        # 1.0 if user selected before
    user_family_preference: float = 0.0  # User's preference for this family
    user_gain_bias: float = 0.0          # User's gain preference (-1 to 1)
    user_effects_affinity: float = 0.0   # User's affinity for effects
    user_session_context: float = 0.0    # Recent session context

    # Confidence signals (2 features)
    descriptor_confidence: float = 0.0   # Confidence in descriptor
    analysis_quality: float = 0.0        # Overall analysis quality

    def to_array(self) -> np.ndarray:
        """Convert to numpy array for ML model input."""
        return np.array([
            # Compatibility
            self.family_exact_match,
            self.family_related_match,
            self.gain_in_range,
            self.gain_distance,
            self.voicing_match_bass,
            self.voicing_match_mid,
            self.voicing_match_treble,
            self.style_match,
            self.configuration_match,
            self.speaker_char_match,
            # Block characteristics
            self.block_popularity,
            self.block_avg_rating,
            self.block_edit_rate,
            self.block_category_affinity,
            self.block_platform_native,
            self.block_price_tier,
            self.block_versatility,
            self.block_is_fallback,
            # User preferences
            self.user_used_before,
            self.user_family_preference,
            self.user_gain_bias,
            self.user_effects_affinity,
            self.user_session_context,
            # Confidence
            self.descriptor_confidence,
            self.analysis_quality,
        ], dtype=np.float32)

    @classmethod
    def feature_names(cls) -> List[str]:
        """Return list of feature names in same order as to_array()."""
        return [
            "family_exact_match", "family_related_match",
            "gain_in_range", "gain_distance",
            "voicing_match_bass", "voicing_match_mid", "voicing_match_treble",
            "style_match", "configuration_match", "speaker_char_match",
            "block_popularity", "block_avg_rating", "block_edit_rate",
            "block_category_affinity", "block_platform_native",
            "block_price_tier", "block_versatility", "block_is_fallback",
            "user_used_before", "user_family_preference", "user_gain_bias",
            "user_effects_affinity", "user_session_context",
            "descriptor_confidence", "analysis_quality",
        ]

    @classmethod
    def num_features(cls) -> int:
        """Return total number of features."""
        return 25


def build_ranking_features(
    descriptor: Dict,
    block: Dict,
    slot: str,
    user_prefs: Optional[Dict] = None,
    block_stats: Optional[Dict] = None,
) -> RankingFeatures:
    """Build ranking features for a descriptor-block pair.

    Args:
        descriptor: ToneDescriptor as dict
        block: Block from catalog
        slot: Slot type ("amp", "cab", "drive", etc.)
        user_prefs: Optional user preferences
        block_stats: Optional block usage statistics

    Returns:
        RankingFeatures for this pair
    """
    features = RankingFeatures()

    # Get descriptor components
    amp = descriptor.get("amp", {})
    cab = descriptor.get("cab", {})
    effects = descriptor.get("effects", {})
    confidence = descriptor.get("confidence", {})

    desc_family = amp.get("family", "unknown")
    desc_gain = amp.get("gain", 0.5)
    voicing = amp.get("voicing", {})

    # Get block attributes
    block_families = block.get("families", [])
    block_id = block.get("id", "")

    # Compatibility features
    if slot == "amp":
        features = _build_amp_features(features, desc_family, desc_gain, voicing, block)
    elif slot == "cab":
        features = _build_cab_features(features, cab, block)
    elif slot in ("drive", "delay", "reverb", "modulation"):
        features = _build_effect_features(features, effects, slot, block)

    # Block characteristics
    if block_stats:
        features.block_popularity = block_stats.get("popularity", 0.5)
        features.block_avg_rating = block_stats.get("avg_rating", 0.5)
        features.block_edit_rate = block_stats.get("edit_rate", 0.5)

    features.block_versatility = len(block_families) / 5.0 if block_families else 0.2
    features.block_is_fallback = 1.0 if "fallback" in block.get("tags", []) else 0.0

    # User preferences
    if user_prefs:
        features.user_used_before = 1.0 if block_id in user_prefs.get("used_blocks", []) else 0.0
        features.user_family_preference = user_prefs.get("family_prefs", {}).get(desc_family, 0.5)
        features.user_gain_bias = user_prefs.get("gain_bias", 0.0)
        features.user_effects_affinity = user_prefs.get("effects_affinity", 0.5)

    # Confidence signals
    features.descriptor_confidence = confidence.get("amp_family", 0.5)
    features.analysis_quality = (
        confidence.get("amp_family", 0.5) +
        confidence.get("gain", 0.5) +
        confidence.get("cab", 0.5)
    ) / 3.0

    return features


def _build_amp_features(
    features: RankingFeatures,
    desc_family: str,
    desc_gain: float,
    voicing: Dict,
    block: Dict,
) -> RankingFeatures:
    """Build features for amp blocks."""
    block_families = block.get("families", [])

    # Family matching
    features.family_exact_match = 1.0 if desc_family in block_families else 0.0

    # Check related families
    from tone_forge.ontology.amp_families import get_related_families
    related = get_related_families(desc_family)
    features.family_related_match = 1.0 if any(f in block_families for f in related) else 0.0

    # Gain compatibility
    gain_range = block.get("gain_range", (0.0, 1.0))
    if isinstance(gain_range, (list, tuple)) and len(gain_range) == 2:
        min_gain, max_gain = gain_range
        features.gain_in_range = 1.0 if min_gain <= desc_gain <= max_gain else 0.0
        center = (min_gain + max_gain) / 2
        features.gain_distance = abs(desc_gain - center)
    else:
        features.gain_in_range = 0.5
        features.gain_distance = 0.25

    # Voicing compatibility
    block_voicing = block.get("default_voicing", {})
    features.voicing_match_bass = 1.0 - abs(
        voicing.get("bass", 0.5) - block_voicing.get("bass", 0.5)
    )
    features.voicing_match_mid = 1.0 - abs(
        voicing.get("mid", 0.5) - block_voicing.get("mid", 0.5)
    )
    features.voicing_match_treble = 1.0 - abs(
        voicing.get("treble", 0.5) - block_voicing.get("treble", 0.5)
    )

    return features


def _build_cab_features(
    features: RankingFeatures,
    cab: Dict,
    block: Dict,
) -> RankingFeatures:
    """Build features for cab blocks."""
    desc_char = cab.get("speaker_character", "unknown")
    desc_config = cab.get("configuration", "4x12")

    block_char = block.get("speaker_character", "unknown")
    block_config = block.get("configuration", "4x12")

    features.speaker_char_match = 1.0 if desc_char == block_char else 0.0
    features.configuration_match = 1.0 if desc_config == block_config else 0.0

    # Partial match for related characters
    from tone_forge.ontology.speakers import SPEAKER_CHARACTERS
    if desc_char in SPEAKER_CHARACTERS and block_char in SPEAKER_CHARACTERS:
        desc_info = SPEAKER_CHARACTERS[desc_char]
        block_info = SPEAKER_CHARACTERS[block_char]
        if desc_info.frequency_character == block_info.frequency_character:
            features.speaker_char_match = max(features.speaker_char_match, 0.7)

    return features


def _build_effect_features(
    features: RankingFeatures,
    effects: Dict,
    slot: str,
    block: Dict,
) -> RankingFeatures:
    """Build features for effect blocks."""
    effect_map = {
        "drive": "overdrive_pedal",
        "delay": "delay",
        "reverb": "reverb",
        "modulation": "modulation",
    }

    effect_key = effect_map.get(slot)
    effect = effects.get(effect_key, {})

    if effect:
        desc_type = effect.get("type", "unknown")
        block_type = block.get("type", "unknown")

        features.style_match = 1.0 if desc_type == block_type else 0.0

        # Partial match for related types
        from tone_forge.ontology.effects import EFFECT_SUBTYPES
        if desc_type in EFFECT_SUBTYPES and block_type in EFFECT_SUBTYPES:
            # Same category gets partial credit
            features.style_match = max(features.style_match, 0.5)

    return features


def build_features_batch(
    descriptor: Dict,
    blocks: List[Dict],
    slot: str,
    user_prefs: Optional[Dict] = None,
    block_stats: Optional[Dict[str, Dict]] = None,
) -> Tuple[np.ndarray, List[str]]:
    """Build features for all blocks in a batch.

    Args:
        descriptor: ToneDescriptor as dict
        blocks: List of candidate blocks
        slot: Slot type
        user_prefs: Optional user preferences
        block_stats: Optional dict mapping block_id -> stats

    Returns:
        (feature_matrix, block_ids) where feature_matrix is (n_blocks, n_features)
    """
    features_list = []
    block_ids = []

    for block in blocks:
        stats = None
        if block_stats:
            stats = block_stats.get(block.get("id", ""))

        features = build_ranking_features(
            descriptor=descriptor,
            block=block,
            slot=slot,
            user_prefs=user_prefs,
            block_stats=stats,
        )
        features_list.append(features.to_array())
        block_ids.append(block.get("id", ""))

    if features_list:
        return np.stack(features_list), block_ids
    else:
        return np.zeros((0, RankingFeatures.num_features())), []
