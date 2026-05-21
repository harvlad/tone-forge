"""Canonical effect type definitions and taxonomy.

Defines authoritative vocabulary for effect types, subtypes,
and their characteristics for consistent classification.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class EffectTypeInfo:
    """Information about an effect type."""
    id: str
    display_name: str
    category: str  # "time", "modulation", "dynamics", "filter", "drive"
    subtypes: List[str]
    typical_params: List[str]
    description: str


# Canonical effect types
EFFECT_TYPES: Dict[str, EffectTypeInfo] = {
    "delay": EffectTypeInfo(
        id="delay",
        display_name="Delay",
        category="time",
        subtypes=["digital", "analog_bbd", "tape", "lo_fi", "dual", "ping_pong"],
        typical_params=["time_ms", "feedback", "mix", "modulation"],
        description="Time-based echo effect",
    ),
    "reverb": EffectTypeInfo(
        id="reverb",
        display_name="Reverb",
        category="time",
        subtypes=["room", "plate", "hall", "spring", "chamber", "shimmer", "ambient"],
        typical_params=["size", "decay", "mix", "pre_delay", "damping"],
        description="Spatial ambience and reflection",
    ),
    "modulation": EffectTypeInfo(
        id="modulation",
        display_name="Modulation",
        category="modulation",
        subtypes=["chorus", "flanger", "phaser", "tremolo", "vibrato", "rotary", "univibe"],
        typical_params=["rate", "depth", "mix", "feedback"],
        description="Pitch/time modulation effects",
    ),
    "compressor": EffectTypeInfo(
        id="compressor",
        display_name="Compressor",
        category="dynamics",
        subtypes=["studio", "optical", "vca", "fet", "multiband", "limiter"],
        typical_params=["threshold", "ratio", "attack", "release", "makeup"],
        description="Dynamic range compression",
    ),
    "overdrive": EffectTypeInfo(
        id="overdrive",
        display_name="Overdrive",
        category="drive",
        subtypes=["tube_screamer", "klon", "blues_driver", "timmy", "transparent"],
        typical_params=["drive", "tone", "level"],
        description="Soft clipping/tube-like saturation",
    ),
    "distortion": EffectTypeInfo(
        id="distortion",
        display_name="Distortion",
        category="drive",
        subtypes=["rat", "ds1", "metal_zone", "fuzz_face", "big_muff", "octave_fuzz"],
        typical_params=["gain", "tone", "level", "filter"],
        description="Hard clipping saturation",
    ),
    "eq": EffectTypeInfo(
        id="eq",
        display_name="EQ",
        category="filter",
        subtypes=["parametric", "graphic", "shelf", "notch"],
        typical_params=["low", "mid", "high", "freq", "q"],
        description="Frequency shaping",
    ),
    "wah": EffectTypeInfo(
        id="wah",
        display_name="Wah",
        category="filter",
        subtypes=["crybaby", "vox", "auto", "envelope"],
        typical_params=["position", "q", "range"],
        description="Resonant filter sweep",
    ),
    "pitch": EffectTypeInfo(
        id="pitch",
        display_name="Pitch",
        category="pitch",
        subtypes=["octave", "harmonizer", "whammy", "detune"],
        typical_params=["pitch", "mix", "tracking"],
        description="Pitch shifting effects",
    ),
    "noise_gate": EffectTypeInfo(
        id="noise_gate",
        display_name="Noise Gate",
        category="dynamics",
        subtypes=["gate", "expander", "ducking"],
        typical_params=["threshold", "attack", "release", "range"],
        description="Noise reduction and gating",
    ),
}


# Effect subtypes with detailed info
EFFECT_SUBTYPES: Dict[str, Dict] = {
    # Delay subtypes
    "digital": {"warmth": 0.2, "modulation": 0.0, "degradation": 0.0},
    "analog_bbd": {"warmth": 0.6, "modulation": 0.3, "degradation": 0.2},
    "tape": {"warmth": 0.8, "modulation": 0.5, "degradation": 0.4},
    "lo_fi": {"warmth": 0.5, "modulation": 0.2, "degradation": 0.7},

    # Reverb subtypes
    "room": {"size": 0.3, "brightness": 0.5, "density": 0.6},
    "plate": {"size": 0.5, "brightness": 0.7, "density": 0.8},
    "hall": {"size": 0.8, "brightness": 0.5, "density": 0.5},
    "spring": {"size": 0.4, "brightness": 0.6, "density": 0.3, "character": "drip"},
    "chamber": {"size": 0.6, "brightness": 0.5, "density": 0.7},
    "shimmer": {"size": 0.9, "brightness": 0.9, "density": 0.4, "pitch_shift": True},

    # Modulation subtypes
    "chorus": {"voices": 2, "character": "lush"},
    "flanger": {"character": "jet", "feedback": 0.7},
    "phaser": {"stages": 4, "character": "swoosh"},
    "tremolo": {"character": "amplitude"},
    "vibrato": {"character": "pitch"},
    "rotary": {"character": "doppler"},

    # Drive subtypes
    "tube_screamer": {"mid_hump": True, "character": "smooth"},
    "klon": {"character": "transparent", "mid_hump": False},
    "blues_driver": {"character": "crunchy"},
    "rat": {"character": "gritty"},
    "big_muff": {"character": "sustain", "fuzz": True},
}


# Traits for semantic matching
EFFECT_TRAITS: Dict[str, List[str]] = {
    "delay": ["rhythmic", "ambient", "echo", "repeat", "time"],
    "reverb": ["space", "ambient", "tail", "room", "atmosphere"],
    "modulation": ["movement", "sweep", "wobble", "animated"],
    "compressor": ["sustain", "punch", "dynamics", "control"],
    "overdrive": ["warm", "tube", "breakup", "touch_sensitive"],
    "distortion": ["aggressive", "saturated", "gain", "heavy"],
    "eq": ["shape", "cut", "boost", "sculpt"],
    "wah": ["vocal", "expressive", "funk", "sweep"],
    "pitch": ["harmony", "octave", "shift", "detune"],
    "noise_gate": ["tight", "silent", "noise_free"],
}


def normalize_effect_type(effect: str) -> str:
    """Normalize effect type to canonical form."""
    effect = effect.lower().strip().replace(" ", "_").replace("-", "_")

    # Direct match
    if effect in EFFECT_TYPES:
        return effect

    # Check aliases
    aliases = {
        "echo": "delay",
        "verb": "reverb",
        "chorus": "modulation",
        "flanger": "modulation",
        "phaser": "modulation",
        "tremolo": "modulation",
        "comp": "compressor",
        "od": "overdrive",
        "drive": "overdrive",
        "ts": "overdrive",
        "tube_screamer": "overdrive",
        "dist": "distortion",
        "fuzz": "distortion",
        "gate": "noise_gate",
    }

    return aliases.get(effect, effect)
