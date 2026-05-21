"""Hardware definitions and user profile management.

This module provides:
- Generic hardware catalog schema (works for any platform)
- User hardware profile (what gear they own)
- Platform definitions (Helix, Boss, Pedals, Kemper, etc.)
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Optional, Literal

# Platform identifiers
Platform = Literal[
    "helix",       # Line 6 Helix / HX Stomp / POD Go
    "boss",        # Boss GT-1000 / GT-100 / ME series
    "kemper",      # Kemper Profiler
    "fractal",     # Fractal Axe-FX / FM series
    "neural_dsp",  # Quad Cortex / plugins
    "strymon",     # Strymon Iridium / Sunset etc.
    "pedals",      # Real pedal recommendations
    "synth",       # Software/hardware synths
]

# Hardware categories
HardwareCategory = Literal[
    "amp", "cab", "drive", "delay", "reverb", "modulation",
    "compressor", "eq", "gate", "wah", "pitch",
    "synth_osc", "synth_filter", "synth_env", "synth_effect",
]


@dataclass
class HardwareBlock:
    """A single hardware block (amp, pedal, synth module, etc.)."""
    id: str
    display: str
    category: HardwareCategory
    platform: Platform
    # What this models (e.g., "Fender Twin Reverb")
    models: Optional[str] = None
    # For matching against ToneDescriptor
    families: list[str] = field(default_factory=list)
    styles: list[str] = field(default_factory=list)
    # Parameters with their ranges
    params: dict[str, tuple[float, float]] = field(default_factory=dict)
    # Extra metadata (price range, availability, etc.)
    meta: dict = field(default_factory=dict)


@dataclass
class UserGear:
    """A piece of gear the user owns."""
    id: str
    display: str
    category: HardwareCategory
    platform: Platform
    # If this is a multi-fx, list its available blocks
    available_blocks: list[str] = field(default_factory=list)


@dataclass
class UserProfile:
    """User's hardware profile - what gear they own."""
    name: str = "default"
    gear: list[UserGear] = field(default_factory=list)
    # Preferred platforms (in order of preference)
    preferred_platforms: list[Platform] = field(default_factory=list)
    # Budget constraints (optional)
    budget_max: Optional[float] = None

    def has_platform(self, platform: Platform) -> bool:
        """Check if user has any gear for a given platform."""
        return any(g.platform == platform for g in self.gear)

    def get_available_blocks(self, category: HardwareCategory) -> list[str]:
        """Get all block IDs available to user for a category."""
        blocks = []
        for g in self.gear:
            if g.category == category:
                blocks.append(g.id)
            elif g.available_blocks:
                # Multi-fx unit - check its blocks
                blocks.extend(g.available_blocks)
        return blocks

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "UserProfile":
        gear = [UserGear(**g) for g in d.get("gear", [])]
        return cls(
            name=d.get("name", "default"),
            gear=gear,
            preferred_platforms=d.get("preferred_platforms", []),
            budget_max=d.get("budget_max"),
        )


# Common multi-fx units with their available block categories
MULTI_FX_UNITS = {
    "helix_floor": {
        "display": "Line 6 Helix Floor",
        "platform": "helix",
        "categories": ["amp", "cab", "drive", "delay", "reverb", "modulation", "compressor", "eq", "gate", "wah", "pitch"],
    },
    "helix_lt": {
        "display": "Line 6 Helix LT",
        "platform": "helix",
        "categories": ["amp", "cab", "drive", "delay", "reverb", "modulation", "compressor", "eq", "gate", "wah", "pitch"],
    },
    "hx_stomp": {
        "display": "Line 6 HX Stomp",
        "platform": "helix",
        "categories": ["amp", "cab", "drive", "delay", "reverb", "modulation", "compressor", "eq", "gate", "wah", "pitch"],
    },
    "pod_go": {
        "display": "Line 6 POD Go",
        "platform": "helix",
        "categories": ["amp", "cab", "drive", "delay", "reverb", "modulation", "compressor"],
    },
    "boss_gt1000": {
        "display": "Boss GT-1000",
        "platform": "boss",
        "categories": ["amp", "cab", "drive", "delay", "reverb", "modulation", "compressor", "eq", "gate", "wah", "pitch"],
    },
    "boss_gt1000_core": {
        "display": "Boss GT-1000 CORE",
        "platform": "boss",
        "categories": ["amp", "cab", "drive", "delay", "reverb", "modulation", "compressor", "eq"],
    },
    "kemper_profiler": {
        "display": "Kemper Profiler",
        "platform": "kemper",
        "categories": ["amp", "cab", "drive", "delay", "reverb", "modulation", "compressor", "eq", "gate", "wah", "pitch"],
    },
    "kemper_player": {
        "display": "Kemper Player",
        "platform": "kemper",
        "categories": ["amp", "cab", "drive", "delay", "reverb", "modulation"],
    },
    "quad_cortex": {
        "display": "Neural DSP Quad Cortex",
        "platform": "neural_dsp",
        "categories": ["amp", "cab", "drive", "delay", "reverb", "modulation", "compressor", "eq", "gate", "wah", "pitch"],
    },
    "fractal_axe3": {
        "display": "Fractal Axe-FX III",
        "platform": "fractal",
        "categories": ["amp", "cab", "drive", "delay", "reverb", "modulation", "compressor", "eq", "gate", "wah", "pitch"],
    },
    "fractal_fm9": {
        "display": "Fractal FM9",
        "platform": "fractal",
        "categories": ["amp", "cab", "drive", "delay", "reverb", "modulation", "compressor", "eq", "gate", "wah", "pitch"],
    },
    "fractal_fm3": {
        "display": "Fractal FM3",
        "platform": "fractal",
        "categories": ["amp", "cab", "drive", "delay", "reverb", "modulation", "compressor", "eq"],
    },
    "strymon_iridium": {
        "display": "Strymon Iridium",
        "platform": "strymon",
        "categories": ["amp", "cab"],
    },
}


def create_user_gear_from_unit(unit_id: str) -> Optional[UserGear]:
    """Create a UserGear from a known multi-fx unit ID."""
    if unit_id not in MULTI_FX_UNITS:
        return None
    unit = MULTI_FX_UNITS[unit_id]
    return UserGear(
        id=unit_id,
        display=unit["display"],
        category="amp",  # Multi-fx are categorized by their primary use
        platform=unit["platform"],
        available_blocks=[],  # Would be populated from the platform's catalog
    )


def save_user_profile(profile: UserProfile, path: Path) -> None:
    """Save user profile to a JSON file."""
    with open(path, "w") as f:
        json.dump(profile.to_dict(), f, indent=2)


def load_user_profile(path: Path) -> Optional[UserProfile]:
    """Load user profile from a JSON file."""
    if not path.exists():
        return None
    with open(path) as f:
        return UserProfile.from_dict(json.load(f))
