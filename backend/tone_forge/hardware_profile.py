"""
User hardware profile system.

Allows users to specify their available gear, and adapts
analysis outputs to match what they actually own.

Example:
- User owns Helix LT (not full Helix)
- User has a Stratocaster and Les Paul
- User has studio monitors

Outputs are then filtered/adapted to:
- Only show Helix LT-compatible blocks
- Suggest pickup configurations for their guitars
- Note monitoring limitations
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Set
from dataclasses import dataclass, field, asdict

logger = logging.getLogger(__name__)


@dataclass
class GuitarProfile:
    """User's guitar configuration."""
    name: str
    type: str  # stratocaster, les_paul, tele, sg, prs, etc.
    pickups: str  # sss, hh, hsh, p90, etc.
    bridge: str = "fixed"  # fixed, trem, floyd
    active: bool = False


@dataclass
class AmpProfile:
    """User's amp/modeler configuration."""
    name: str
    type: str  # helix, helix_lt, hx_stomp, kemper, axe_fx, quad_cortex, real_amp
    model: Optional[str] = None  # Specific model name

    # Limitations
    max_blocks: Optional[int] = None  # e.g., HX Stomp = 6
    has_dual_path: bool = True
    has_scribble_strips: bool = True


@dataclass
class MonitoringProfile:
    """User's monitoring setup."""
    type: str  # studio_monitors, headphones, frfr, guitar_cab
    model: Optional[str] = None
    notes: Optional[str] = None  # e.g., "Yamaha HS8"


@dataclass
class EffectsProfile:
    """User's available effects pedals."""
    owned_pedals: List[str] = field(default_factory=list)
    owned_categories: List[str] = field(default_factory=list)  # overdrive, delay, reverb, etc.


@dataclass
class HardwareProfile:
    """Complete user hardware profile."""
    name: str = "Default"

    # Primary modeler/amp
    primary_amp: Optional[AmpProfile] = None

    # Guitars
    guitars: List[GuitarProfile] = field(default_factory=list)

    # Monitoring
    monitoring: Optional[MonitoringProfile] = None

    # Additional effects
    effects: Optional[EffectsProfile] = None

    # Preferences
    preferences: Dict = field(default_factory=dict)

    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict) -> "HardwareProfile":
        """Create from dictionary."""
        profile = cls(name=data.get("name", "Default"))

        if data.get("primary_amp"):
            profile.primary_amp = AmpProfile(**data["primary_amp"])

        if data.get("guitars"):
            profile.guitars = [GuitarProfile(**g) for g in data["guitars"]]

        if data.get("monitoring"):
            profile.monitoring = MonitoringProfile(**data["monitoring"])

        if data.get("effects"):
            profile.effects = EffectsProfile(**data["effects"])

        profile.preferences = data.get("preferences", {})

        return profile


# =============================================================================
# PRESET HARDWARE PROFILES
# =============================================================================

PRESET_PROFILES = {
    "helix_floor": HardwareProfile(
        name="Helix Floor",
        primary_amp=AmpProfile(
            name="Line 6 Helix Floor",
            type="helix",
            max_blocks=32,
            has_dual_path=True,
            has_scribble_strips=True,
        ),
    ),
    "helix_lt": HardwareProfile(
        name="Helix LT",
        primary_amp=AmpProfile(
            name="Line 6 Helix LT",
            type="helix_lt",
            max_blocks=32,
            has_dual_path=True,
            has_scribble_strips=False,
        ),
    ),
    "hx_stomp": HardwareProfile(
        name="HX Stomp",
        primary_amp=AmpProfile(
            name="Line 6 HX Stomp",
            type="hx_stomp",
            max_blocks=6,
            has_dual_path=False,
            has_scribble_strips=False,
        ),
    ),
    "hx_stomp_xl": HardwareProfile(
        name="HX Stomp XL",
        primary_amp=AmpProfile(
            name="Line 6 HX Stomp XL",
            type="hx_stomp_xl",
            max_blocks=8,
            has_dual_path=False,
            has_scribble_strips=False,
        ),
    ),
    "quad_cortex": HardwareProfile(
        name="Quad Cortex",
        primary_amp=AmpProfile(
            name="Neural DSP Quad Cortex",
            type="quad_cortex",
            max_blocks=32,
            has_dual_path=True,
            has_scribble_strips=True,
        ),
    ),
    "kemper": HardwareProfile(
        name="Kemper Profiler",
        primary_amp=AmpProfile(
            name="Kemper Profiler",
            type="kemper",
            max_blocks=8,  # Stomp slots
            has_dual_path=False,
            has_scribble_strips=False,
        ),
    ),
    "axe_fx_3": HardwareProfile(
        name="Axe-Fx III",
        primary_amp=AmpProfile(
            name="Fractal Axe-Fx III",
            type="axe_fx",
            max_blocks=None,  # Flexible
            has_dual_path=True,
            has_scribble_strips=True,
        ),
    ),
    "pedalboard": HardwareProfile(
        name="Traditional Pedalboard",
        primary_amp=AmpProfile(
            name="Real Amp + Pedals",
            type="real_amp",
        ),
    ),
}


# =============================================================================
# ADAPTATION FUNCTIONS
# =============================================================================

def get_block_limit(profile: HardwareProfile) -> Optional[int]:
    """Get the block limit for the user's hardware."""
    if profile.primary_amp:
        return profile.primary_amp.max_blocks
    return None


def can_use_dual_path(profile: HardwareProfile) -> bool:
    """Check if user's hardware supports dual signal paths."""
    if profile.primary_amp:
        return profile.primary_amp.has_dual_path
    return True


def get_export_format_for_profile(profile: HardwareProfile) -> str:
    """Get the appropriate export format for the user's hardware."""
    if not profile.primary_amp:
        return "json"

    amp_type = profile.primary_amp.type

    format_map = {
        "helix": "helix",
        "helix_lt": "helix",
        "hx_stomp": "hx_stomp",
        "hx_stomp_xl": "hx_stomp",
        "quad_cortex": "neural_dsp",
        "kemper": "json",  # No direct export yet
        "axe_fx": "json",  # No direct export yet
        "real_amp": "pedal_recommendations",
    }

    return format_map.get(amp_type, "json")


def adapt_chain_to_profile(
    chain: List[Dict],
    profile: HardwareProfile,
) -> Dict:
    """
    Adapt a signal chain to fit the user's hardware limitations.

    Returns:
        Dict with:
        - adapted_chain: The modified chain
        - removed_blocks: Blocks that had to be removed
        - warnings: Any warnings about compromises made
        - suggestions: Suggestions for the user
    """
    result = {
        "adapted_chain": [],
        "removed_blocks": [],
        "warnings": [],
        "suggestions": [],
    }

    block_limit = get_block_limit(profile)

    if block_limit is None:
        # No limit, return as-is
        result["adapted_chain"] = chain
        return result

    # Priority order for keeping blocks (higher = more important)
    block_priority = {
        "amp": 10,
        "cab": 9,
        "preamp": 8,
        "drive": 7,
        "eq": 6,
        "compressor": 5,
        "delay": 4,
        "reverb": 4,
        "modulation": 3,
        "gate": 2,
        "volume": 1,
    }

    # Sort by priority
    sorted_chain = sorted(
        chain,
        key=lambda b: block_priority.get(b.get("type", ""), 0),
        reverse=True,
    )

    # Take top N blocks
    if len(sorted_chain) > block_limit:
        result["adapted_chain"] = sorted_chain[:block_limit]
        result["removed_blocks"] = sorted_chain[block_limit:]

        removed_types = [b.get("type", "unknown") for b in result["removed_blocks"]]
        result["warnings"].append(
            f"Chain reduced from {len(chain)} to {block_limit} blocks "
            f"(removed: {', '.join(removed_types)})"
        )

        # Suggestions based on what was removed
        if any(b.get("type") in ["delay", "reverb"] for b in result["removed_blocks"]):
            result["suggestions"].append(
                "Consider using external pedals for time-based effects"
            )

        if any(b.get("type") == "modulation" for b in result["removed_blocks"]):
            result["suggestions"].append(
                "Modulation was removed - try adding a dedicated mod pedal"
            )
    else:
        result["adapted_chain"] = sorted_chain

    return result


def get_pickup_suggestions(
    descriptor: Dict,
    profile: HardwareProfile,
) -> List[str]:
    """
    Get pickup position suggestions based on detected tone and available guitars.
    """
    suggestions = []

    if not profile.guitars:
        return ["Unable to suggest - no guitars in profile"]

    # Analyze detected tone characteristics
    spectral_centroid = descriptor.get("spectral", {}).get("centroid_mean", 2000)
    gain = descriptor.get("amp", {}).get("gain_normalized", 0.5)

    is_bright = spectral_centroid > 2500
    is_dark = spectral_centroid < 1500
    is_high_gain = gain > 0.6

    for guitar in profile.guitars:
        guitar_suggestions = []

        if guitar.pickups == "sss":  # Stratocaster
            if is_bright:
                guitar_suggestions.append(f"{guitar.name}: Bridge pickup")
            elif is_dark:
                guitar_suggestions.append(f"{guitar.name}: Neck pickup, roll tone to 5-7")
            else:
                guitar_suggestions.append(f"{guitar.name}: Position 4 (bridge+middle)")

        elif guitar.pickups == "hh":  # Les Paul style
            if is_high_gain:
                guitar_suggestions.append(f"{guitar.name}: Bridge humbucker")
            elif is_dark:
                guitar_suggestions.append(f"{guitar.name}: Neck humbucker")
            else:
                guitar_suggestions.append(f"{guitar.name}: Both pickups, or bridge with tone rolled back")

        elif guitar.pickups == "hsh":
            if is_bright and not is_high_gain:
                guitar_suggestions.append(f"{guitar.name}: Position 2 or 4 (coil split)")
            elif is_high_gain:
                guitar_suggestions.append(f"{guitar.name}: Bridge humbucker")
            else:
                guitar_suggestions.append(f"{guitar.name}: Middle single coil or position 2")

        suggestions.extend(guitar_suggestions)

    return suggestions if suggestions else ["Use bridge pickup for articulation, neck for warmth"]


def get_monitoring_notes(
    descriptor: Dict,
    profile: HardwareProfile,
) -> List[str]:
    """
    Get notes about how the tone might translate to the user's monitoring.
    """
    notes = []

    if not profile.monitoring:
        return []

    mon_type = profile.monitoring.type

    # Low frequency considerations
    has_heavy_low_end = descriptor.get("spectral", {}).get("low_energy_ratio", 0) > 0.4

    if mon_type == "headphones" and has_heavy_low_end:
        notes.append(
            "Tone has prominent low end - may feel different on speakers vs headphones"
        )

    if mon_type == "guitar_cab":
        notes.append(
            "Using guitar cab - disable cab simulation in your preset"
        )

    if mon_type == "frfr":
        notes.append(
            "FRFR monitoring - cab simulation should be enabled"
        )

    # Stereo considerations
    has_stereo_fx = any(
        fx in descriptor.get("effects", {})
        for fx in ["chorus", "stereo_delay", "ping_pong_delay"]
    )

    if has_stereo_fx and mon_type in ["guitar_cab", "headphones"]:
        notes.append(
            "Stereo effects detected - best experienced on stereo monitors/FRFR"
        )

    return notes


def generate_profile_adapted_output(
    descriptor: Dict,
    chain: List[Dict],
    profile: HardwareProfile,
) -> Dict:
    """
    Generate a complete profile-adapted output with suggestions.
    """
    # Adapt chain to hardware
    adapted = adapt_chain_to_profile(chain, profile)

    # Get pickup suggestions
    pickup_suggestions = get_pickup_suggestions(descriptor, profile)

    # Get monitoring notes
    monitoring_notes = get_monitoring_notes(descriptor, profile)

    # Get recommended export format
    export_format = get_export_format_for_profile(profile)

    return {
        "profile_name": profile.name,
        "chain": adapted["adapted_chain"],
        "removed_blocks": adapted["removed_blocks"],
        "warnings": adapted["warnings"],
        "suggestions": adapted["suggestions"],
        "pickup_suggestions": pickup_suggestions,
        "monitoring_notes": monitoring_notes,
        "recommended_export_format": export_format,
        "hardware_summary": {
            "amp": profile.primary_amp.name if profile.primary_amp else "Not specified",
            "guitars": [g.name for g in profile.guitars],
            "monitoring": profile.monitoring.type if profile.monitoring else "Not specified",
        },
    }


# =============================================================================
# PROFILE STORAGE
# =============================================================================

def save_profile(profile: HardwareProfile, path: Path) -> None:
    """Save profile to JSON file."""
    with open(path, 'w') as f:
        json.dump(profile.to_dict(), f, indent=2)


def load_profile(path: Path) -> HardwareProfile:
    """Load profile from JSON file."""
    with open(path, 'r') as f:
        data = json.load(f)
    return HardwareProfile.from_dict(data)


def get_profile_template() -> Dict:
    """Get a template for users to fill in their hardware profile."""
    return {
        "name": "My Setup",
        "primary_amp": {
            "name": "Line 6 Helix",
            "type": "helix",  # helix, helix_lt, hx_stomp, quad_cortex, kemper, axe_fx, real_amp
            "max_blocks": 32,
            "has_dual_path": True,
        },
        "guitars": [
            {
                "name": "Fender Stratocaster",
                "type": "stratocaster",
                "pickups": "sss",  # sss, hh, hsh, p90
                "bridge": "trem",
                "active": False,
            },
        ],
        "monitoring": {
            "type": "studio_monitors",  # studio_monitors, headphones, frfr, guitar_cab
            "model": "Yamaha HS8",
        },
        "effects": {
            "owned_pedals": ["Tube Screamer", "Klon Centaur", "Strymon Timeline"],
            "owned_categories": ["overdrive", "delay", "reverb"],
        },
        "preferences": {
            "prefer_amp_in_room": False,
            "use_headphones_often": True,
        },
    }
