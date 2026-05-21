"""Generic translator: ToneDescriptor -> Signal Chain recommendations.

This is the platform-agnostic translation layer. It takes a ToneDescriptor
and produces recommendations that can be rendered for any supported platform.

Supported platforms:
- helix: Line 6 Helix / HX Stomp / POD Go
- boss: Boss GT-1000 / GT-100
- kemper: Kemper Profiler
- fractal: Fractal Axe-FX / FM series
- neural_dsp: Quad Cortex
- pedals: Real pedal recommendations
- synth: Synth parameter recommendations

ML Integration:
- When `use_ml_ranking=True`, uses LightGBM-based ranking for block selection
- Falls back to rule-based selection when ML models aren't available
- Feedback collection enables personalization over time
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, Literal, Dict, List, Any

from .descriptor import ToneDescriptor
from .hardware import Platform, UserProfile, HardwareCategory
from . import rules_engine as rules

logger = logging.getLogger(__name__)


_DATA_DIR = Path(__file__).parent.parent / "data"


def _descriptor_to_dict(descriptor: ToneDescriptor) -> Dict[str, Any]:
    """Convert ToneDescriptor to dict for ML feature extraction."""
    return {
        "source": {
            "kind": descriptor.source.kind,
            "duration_sec": descriptor.source.duration_sec,
        },
        "amp": {
            "family": descriptor.amp.family,
            "gain": descriptor.amp.gain,
            "voicing": {
                "bass": descriptor.amp.voicing.bass,
                "mid": descriptor.amp.voicing.mid,
                "treble": descriptor.amp.voicing.treble,
                "presence": descriptor.amp.voicing.presence,
                "mid_scoop": descriptor.amp.voicing.mid_scoop,
            },
            "alternates": descriptor.amp.alternates,
        },
        "cab": {
            "configuration": descriptor.cab.configuration,
            "speaker_character": descriptor.cab.speaker_character,
        },
        "effects": {
            "overdrive_pedal": {
                "style": descriptor.effects.overdrive_pedal.style if descriptor.effects.overdrive_pedal else None,
                "drive": descriptor.effects.overdrive_pedal.drive if descriptor.effects.overdrive_pedal else 0.0,
                "level": descriptor.effects.overdrive_pedal.level if descriptor.effects.overdrive_pedal else 0.0,
            } if descriptor.effects.overdrive_pedal else None,
            "delay": {
                "type": descriptor.effects.delay.type if descriptor.effects.delay else "none",
                "time_ms": descriptor.effects.delay.time_ms if descriptor.effects.delay else 0.0,
                "feedback": descriptor.effects.delay.feedback if descriptor.effects.delay else 0.0,
                "mix": descriptor.effects.delay.mix if descriptor.effects.delay else 0.0,
            } if descriptor.effects.delay else None,
            "reverb": {
                "type": descriptor.effects.reverb.type if descriptor.effects.reverb else "none",
                "size": descriptor.effects.reverb.size if descriptor.effects.reverb else 0.0,
                "mix": descriptor.effects.reverb.mix if descriptor.effects.reverb else 0.0,
            } if descriptor.effects.reverb else None,
            "modulation": {
                "type": descriptor.effects.modulation.type if descriptor.effects.modulation else "none",
                "rate": descriptor.effects.modulation.rate if descriptor.effects.modulation else 0.0,
                "depth": descriptor.effects.modulation.depth if descriptor.effects.modulation else 0.0,
            } if descriptor.effects.modulation else None,
        },
        "guitar": {
            "pickup_brightness": descriptor.guitar.pickup_brightness,
            "playing_style": descriptor.guitar.playing_style,
        },
        "confidence": {
            "amp_family": descriptor.confidence.amp_family,
            "gain": descriptor.confidence.gain,
            "cab": descriptor.confidence.cab,
            "effects": descriptor.confidence.effects,
        },
    }


def _descriptor_hash(descriptor: ToneDescriptor) -> str:
    """Generate a hash for the descriptor (for feedback tracking)."""
    d = _descriptor_to_dict(descriptor)
    # Exclude confidence from hash since it doesn't affect target tone
    d.pop("confidence", None)
    content = json.dumps(d, sort_keys=True)
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def _try_ml_pick(
    descriptor_dict: Dict,
    catalog_list: List[Dict],
    slot: str,
    user_prefs: Optional[Dict] = None,
    block_stats: Optional[Dict[str, Dict]] = None,
) -> Optional[Dict]:
    """Try to pick a block using ML ranking.

    Returns the top-ranked block dict if ML is available, None otherwise.
    """
    try:
        from .ml.translator import get_ranker, is_ranker_ready

        ranker = get_ranker()
        top = ranker.get_top_block(
            descriptor=descriptor_dict,
            blocks=catalog_list,
            slot=slot,
            user_prefs=user_prefs,
            block_stats=block_stats,
        )

        if top:
            logger.debug(
                "ML ranked %s: %s (score=%.3f, %s)",
                slot, top.block_id, top.score,
                "ML" if ranker.is_ml_ready() else "heuristic"
            )
            return {
                "block": top.block,
                "score": top.score,
                "explanation": top.explanation,
                "is_ml": ranker.is_ml_ready(),
            }
    except ImportError:
        logger.debug("ML translator module not available")
    except Exception as e:
        logger.warning("ML ranking failed for %s: %s", slot, e)

    return None


@dataclass
class BlockRecommendation:
    """A recommended hardware block for a signal chain slot."""
    slot: str  # e.g., "amp", "drive", "delay"
    block_id: str
    display: str
    platform: Platform
    params: dict = field(default_factory=dict)
    rationale: str = ""
    # Alternatives from other platforms
    alternatives: list["BlockRecommendation"] = field(default_factory=list)
    # Price estimate for pedals
    price_estimate: Optional[str] = None
    # ML ranking metadata
    ml_score: Optional[float] = None
    ml_explanation: Optional[dict] = None
    used_ml: bool = False


@dataclass
class SignalChainCard:
    """Complete signal chain recommendation."""
    picks: list[BlockRecommendation]
    tweak_hints: list[str] = field(default_factory=list)
    platform: Platform = "helix"
    # Cross-platform alternatives
    alternative_chains: dict[Platform, list[BlockRecommendation]] = field(default_factory=dict)


def load_catalog(platform: Platform) -> dict:
    """Load the hardware catalog for a platform."""
    catalog_path = _DATA_DIR / f"{platform}_blocks.json"
    if not catalog_path.exists():
        # Fall back to helix_blocks.json for backward compatibility
        if platform == "helix":
            catalog_path = _DATA_DIR / "helix_blocks.json"
        else:
            return {}
    with open(catalog_path) as f:
        return json.load(f)


def _get_price(catalog_list: list[dict], block_id: str) -> Optional[str]:
    """Look up price from catalog by block ID."""
    for item in catalog_list:
        if item.get("id") == block_id:
            return item.get("price")
    return None


def translate(
    descriptor: ToneDescriptor,
    platform: Platform = "helix",
    user_profile: Optional[UserProfile] = None,
    include_alternatives: bool = True,
    use_ml_ranking: bool = True,
    user_prefs: Optional[Dict] = None,
    block_stats: Optional[Dict[str, Dict]] = None,
    collect_feedback: bool = False,
) -> SignalChainCard:
    """Translate a ToneDescriptor to a signal chain for the specified platform.

    Args:
        descriptor: The analyzed tone descriptor.
        platform: Target platform for recommendations.
        user_profile: Optional user hardware profile to filter/rank results.
        include_alternatives: Whether to include cross-platform alternatives.
        use_ml_ranking: Whether to use ML ranking (falls back to rules if unavailable).
        user_prefs: Optional user preferences for ML ranking.
        block_stats: Optional block usage statistics for ML ranking.
        collect_feedback: Whether to record feedback for model retraining.

    Returns:
        A SignalChainCard with recommendations.
    """
    catalog = load_catalog(platform)
    if not catalog:
        raise ValueError(f"No catalog found for platform: {platform}")

    picks: list[BlockRecommendation] = []

    # Convert descriptor to dict for ML
    descriptor_dict = _descriptor_to_dict(descriptor) if use_ml_ranking else None
    desc_hash = _descriptor_hash(descriptor) if collect_feedback else None

    # Helper to create BlockRecommendation with optional ML metadata
    def make_recommendation(
        slot: str,
        rules_pick: rules.BlockPick,
        catalog_list: list[dict],
        ml_slot: str,
    ) -> BlockRecommendation:
        ml_result = None
        if use_ml_ranking and descriptor_dict:
            ml_result = _try_ml_pick(
                descriptor_dict, catalog_list, ml_slot,
                user_prefs=user_prefs, block_stats=block_stats
            )

        if ml_result and ml_result["block"].get("id") != rules_pick.block_id:
            # ML chose a different block - use it but keep rules pick info
            ml_block = ml_result["block"]
            return BlockRecommendation(
                slot=slot,
                block_id=ml_block.get("id", rules_pick.block_id),
                display=ml_block.get("display", rules_pick.display),
                platform=platform,
                params=rules_pick.params,  # Keep parameter mapping from rules
                rationale=f"ML-ranked: {ml_result.get('explanation', {})}",
                price_estimate=_get_price(catalog_list, ml_block.get("id", "")),
                ml_score=ml_result.get("score"),
                ml_explanation=ml_result.get("explanation"),
                used_ml=ml_result.get("is_ml", False),
            )
        else:
            # Use rules pick (ML unavailable, agreed, or no improvement)
            return BlockRecommendation(
                slot=slot,
                block_id=rules_pick.block_id,
                display=rules_pick.display,
                platform=platform,
                params=rules_pick.params,
                rationale=rules_pick.rationale,
                price_estimate=_get_price(catalog_list, rules_pick.block_id),
                ml_score=ml_result.get("score") if ml_result else None,
                ml_explanation=ml_result.get("explanation") if ml_result else None,
                used_ml=False,
            )

    # Drive
    if "drives" in catalog:
        drive = rules.pick_drive(descriptor, catalog["drives"])
        if drive:
            picks.append(make_recommendation("drive", drive, catalog["drives"], "drive"))

    # Amp
    if "amps" in catalog:
        amp = rules.pick_amp(descriptor, catalog["amps"])
        picks.append(make_recommendation("amp", amp, catalog["amps"], "amp"))

        # Alternates (always use rules for these)
        for alt in rules.pick_amp_alternates(descriptor, catalog["amps"]):
            picks.append(BlockRecommendation(
                slot="amp_alt",
                block_id=alt.block_id,
                display=alt.display,
                platform=platform,
                params=alt.params,
                rationale=alt.rationale,
                price_estimate=_get_price(catalog["amps"], alt.block_id),
            ))

    # Cab
    if "cabs" in catalog:
        cab = rules.pick_cab(descriptor, catalog["cabs"])
        picks.append(make_recommendation("cab", cab, catalog["cabs"], "cab"))

    # Modulation
    if "modulation" in catalog:
        mod = rules.pick_modulation(descriptor, catalog["modulation"])
        if mod:
            picks.append(make_recommendation("modulation", mod, catalog["modulation"], "modulation"))

    # Delay
    if "delays" in catalog:
        delay = rules.pick_delay(descriptor, catalog["delays"])
        if delay:
            picks.append(make_recommendation("delay", delay, catalog["delays"], "delay"))

    # Reverb
    if "reverbs" in catalog:
        reverb = rules.pick_reverb(descriptor, catalog["reverbs"])
        if reverb:
            picks.append(make_recommendation("reverb", reverb, catalog["reverbs"], "reverb"))

    # Filter by user profile if provided
    if user_profile:
        picks = _filter_by_profile(picks, user_profile)

    card = SignalChainCard(
        picks=picks,
        tweak_hints=rules.tweak_hints(descriptor),
        platform=platform,
    )

    # Add cross-platform alternatives
    if include_alternatives:
        card.alternative_chains = _get_alternative_chains(descriptor, platform, user_profile)

    return card


def _filter_by_profile(
    picks: list[BlockRecommendation],
    profile: UserProfile,
) -> list[BlockRecommendation]:
    """Filter/reorder picks based on user's available gear."""
    # For now, just return all picks
    # TODO: Implement filtering based on user's available blocks
    return picks


def _get_alternative_chains(
    descriptor: ToneDescriptor,
    primary_platform: Platform,
    user_profile: Optional[UserProfile],
) -> dict[Platform, list[BlockRecommendation]]:
    """Get alternative signal chains for other platforms."""
    alternatives = {}

    # List of platforms to suggest alternatives for
    alt_platforms: list[Platform] = ["pedals"]

    # If user has specific platforms, prioritize those
    if user_profile and user_profile.preferred_platforms:
        alt_platforms = [p for p in user_profile.preferred_platforms if p != primary_platform]

    for platform in alt_platforms:
        if platform == primary_platform:
            continue
        try:
            catalog = load_catalog(platform)
            if catalog:
                # Generate a minimal chain for this platform
                chain = _translate_minimal(descriptor, platform, catalog)
                if chain:
                    alternatives[platform] = chain
        except Exception:
            continue

    return alternatives


def _translate_minimal(
    descriptor: ToneDescriptor,
    platform: Platform,
    catalog: dict,
) -> list[BlockRecommendation]:
    """Generate a minimal signal chain for a platform (amp + effects only)."""
    picks = []

    if "amps" in catalog:
        amp = rules.pick_amp(descriptor, catalog["amps"])
        picks.append(BlockRecommendation(
            slot="amp",
            block_id=amp.block_id,
            display=amp.display,
            platform=platform,
            params=amp.params,
            rationale=amp.rationale,
        ))

    return picks


# Convenience functions for specific platforms
def translate_for_helix(descriptor: ToneDescriptor) -> SignalChainCard:
    """Translate for Line 6 Helix."""
    return translate(descriptor, platform="helix")


def translate_for_pedals(descriptor: ToneDescriptor) -> SignalChainCard:
    """Translate to real pedal recommendations."""
    return translate(descriptor, platform="pedals")


def translate_for_synth(descriptor: ToneDescriptor) -> SignalChainCard:
    """Translate for synth recreation (when implemented)."""
    return translate(descriptor, platform="synth")
