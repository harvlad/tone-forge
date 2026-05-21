"""Production style definitions and genre mappings.

Defines vocabulary for production styles and their associated
sonic characteristics for style-aware recommendations.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class ProductionStyleDefaults:
    """Default settings for a production style."""
    amp_families: List[str]
    gain_range: tuple  # (min, max)
    reverb_style: Optional[str] = None
    delay_style: Optional[str] = None
    cab_preference: Optional[str] = None
    effects_emphasis: List[str] = field(default_factory=list)
    eq_character: str = "neutral"


PRODUCTION_STYLES: Dict[str, ProductionStyleDefaults] = {
    "80s_rock": ProductionStyleDefaults(
        amp_families=["marshall_jcm", "soldano", "marshall_plexi"],
        gain_range=(0.45, 0.75),
        reverb_style="plate",
        delay_style="digital",
        cab_preference="4x12",
        effects_emphasis=["chorus"],
        eq_character="mid_forward",
    ),
    "80s_metal": ProductionStyleDefaults(
        amp_families=["marshall_jcm", "5150_peavey", "mesa_rectifier"],
        gain_range=(0.65, 0.90),
        reverb_style="plate_short",
        delay_style="none",
        cab_preference="4x12",
        effects_emphasis=["noise_gate"],
        eq_character="scooped",
    ),
    "modern_metal": ProductionStyleDefaults(
        amp_families=["5150_peavey", "mesa_rectifier", "bogner"],
        gain_range=(0.75, 0.98),
        reverb_style="room_small",
        delay_style="none",
        cab_preference="4x12",
        effects_emphasis=["noise_gate", "compressor"],
        eq_character="tight_low",
    ),
    "djent": ProductionStyleDefaults(
        amp_families=["5150_peavey", "bogner", "mesa_rectifier"],
        gain_range=(0.80, 0.98),
        reverb_style="none",
        delay_style="none",
        cab_preference="4x12",
        effects_emphasis=["noise_gate", "compressor", "eq"],
        eq_character="very_tight",
    ),
    "classic_rock": ProductionStyleDefaults(
        amp_families=["marshall_plexi", "fender_clean", "ac30"],
        gain_range=(0.30, 0.60),
        reverb_style="room",
        delay_style="tape",
        cab_preference="4x12",
        effects_emphasis=[],
        eq_character="mid_forward",
    ),
    "blues": ProductionStyleDefaults(
        amp_families=["fender_clean", "tweed", "dumble"],
        gain_range=(0.15, 0.45),
        reverb_style="spring",
        delay_style="tape",
        cab_preference="1x12",
        effects_emphasis=["overdrive"],
        eq_character="warm",
    ),
    "country": ProductionStyleDefaults(
        amp_families=["fender_clean", "tweed"],
        gain_range=(0.10, 0.30),
        reverb_style="spring",
        delay_style="slapback",
        cab_preference="1x12",
        effects_emphasis=["compressor"],
        eq_character="bright",
    ),
    "indie_rock": ProductionStyleDefaults(
        amp_families=["vox_chime", "fender_clean", "marshall_plexi"],
        gain_range=(0.25, 0.50),
        reverb_style="room",
        delay_style="analog_bbd",
        cab_preference="2x12",
        effects_emphasis=["chorus", "tremolo"],
        eq_character="jangly",
    ),
    "shoegaze": ProductionStyleDefaults(
        amp_families=["vox_chime", "fender_clean", "marshall_plexi"],
        gain_range=(0.35, 0.65),
        reverb_style="hall_large",
        delay_style="analog_bbd",
        cab_preference="2x12",
        effects_emphasis=["reverb", "delay", "modulation", "fuzz"],
        eq_character="washed",
    ),
    "ambient": ProductionStyleDefaults(
        amp_families=["fender_clean", "vox_chime"],
        gain_range=(0.05, 0.25),
        reverb_style="shimmer",
        delay_style="dotted_eighth",
        cab_preference="1x12",
        effects_emphasis=["reverb", "delay", "modulation"],
        eq_character="clean_wide",
    ),
    "jazz": ProductionStyleDefaults(
        amp_families=["fender_clean", "tweed"],
        gain_range=(0.05, 0.20),
        reverb_style="room_small",
        delay_style="none",
        cab_preference="1x12",
        effects_emphasis=[],
        eq_character="warm_round",
    ),
    "fusion": ProductionStyleDefaults(
        amp_families=["dumble", "fender_clean", "mesa_rectifier"],
        gain_range=(0.20, 0.50),
        reverb_style="plate",
        delay_style="digital",
        cab_preference="1x12",
        effects_emphasis=["compressor", "chorus"],
        eq_character="defined",
    ),
    "punk": ProductionStyleDefaults(
        amp_families=["marshall_jcm", "marshall_plexi"],
        gain_range=(0.50, 0.75),
        reverb_style="none",
        delay_style="none",
        cab_preference="4x12",
        effects_emphasis=[],
        eq_character="raw",
    ),
    "grunge": ProductionStyleDefaults(
        amp_families=["fender_clean", "mesa_rectifier", "marshall_jcm"],
        gain_range=(0.40, 0.70),
        reverb_style="room",
        delay_style="none",
        cab_preference="4x12",
        effects_emphasis=["distortion", "chorus"],
        eq_character="thick",
    ),
}


GENRE_MAPPINGS: Dict[str, List[str]] = {
    # Maps genres to production styles
    "rock": ["classic_rock", "80s_rock", "indie_rock"],
    "metal": ["80s_metal", "modern_metal", "djent"],
    "blues": ["blues"],
    "country": ["country"],
    "jazz": ["jazz", "fusion"],
    "indie": ["indie_rock", "shoegaze"],
    "ambient": ["ambient", "shoegaze"],
    "punk": ["punk", "grunge"],
    "alternative": ["indie_rock", "grunge", "shoegaze"],
    "progressive": ["fusion", "80s_rock", "modern_metal"],
    "pop": ["80s_rock", "country", "indie_rock"],
}


def get_style_defaults(style: str) -> Optional[ProductionStyleDefaults]:
    """Get default settings for a production style."""
    style = style.lower().strip().replace(" ", "_").replace("-", "_")
    return PRODUCTION_STYLES.get(style)


def get_styles_for_genre(genre: str) -> List[str]:
    """Get production styles associated with a genre."""
    genre = genre.lower().strip().replace(" ", "_").replace("-", "_")
    return GENRE_MAPPINGS.get(genre, [])
