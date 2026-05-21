"""Production archetypes for genre-specific recommendations.

Defines sonic templates for different genres and production styles.
Used to:
- Guide block selection toward genre-appropriate choices
- Set default parameter ranges
- Provide context for tweak hints

Archetypes are templates, not rules - they inform recommendations
but can be overridden by actual audio analysis.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any

# Re-export from ontology for convenience
from tone_forge.ontology.production import (
    PRODUCTION_STYLES,
    ProductionStyleDefaults,
    get_style_defaults,
    get_styles_for_genre,
)


@dataclass
class EffectChainTemplate:
    """Template for effect chain order and settings."""

    # Effect presence (True = include, False = exclude, None = optional)
    includes_drive: Optional[bool] = None
    includes_delay: Optional[bool] = None
    includes_reverb: Optional[bool] = None
    includes_modulation: Optional[bool] = None
    includes_compression: Optional[bool] = None

    # Preferred effect types
    drive_types: List[str] = field(default_factory=list)
    delay_types: List[str] = field(default_factory=list)
    reverb_types: List[str] = field(default_factory=list)
    modulation_types: List[str] = field(default_factory=list)

    # Effect intensity ranges (0-1)
    drive_range: Tuple[float, float] = (0.0, 1.0)
    delay_mix_range: Tuple[float, float] = (0.0, 0.5)
    reverb_mix_range: Tuple[float, float] = (0.0, 0.5)
    modulation_depth_range: Tuple[float, float] = (0.0, 1.0)


@dataclass
class ToneArchetype:
    """Complete archetype for a production style/genre.

    Combines amp, cab, and effect preferences into a cohesive
    template for genre-appropriate recommendations.
    """

    name: str
    display_name: str
    description: str

    # Amp preferences
    amp_families: List[str]
    gain_range: Tuple[float, float]
    gain_character: str  # "clean", "crunch", "high_gain"

    # Cab preferences
    cab_configs: List[str]
    speaker_chars: List[str]

    # Voicing tendencies
    bass_range: Tuple[float, float] = (0.4, 0.6)
    mid_range: Tuple[float, float] = (0.4, 0.6)
    treble_range: Tuple[float, float] = (0.4, 0.6)
    presence_range: Tuple[float, float] = (0.4, 0.6)

    # Effect chain
    effect_chain: EffectChainTemplate = field(default_factory=EffectChainTemplate)

    # Production characteristics
    production_era: str = "modern"  # "vintage", "80s", "90s", "modern"
    dynamic_range: str = "normal"   # "compressed", "normal", "dynamic"

    # Reference tones (for similarity matching)
    reference_artists: List[str] = field(default_factory=list)
    reference_songs: List[str] = field(default_factory=list)


# Core archetypes library
ARCHETYPES: Dict[str, ToneArchetype] = {
    # Rock archetypes
    "classic_rock": ToneArchetype(
        name="classic_rock",
        display_name="Classic Rock",
        description="70s-inspired rock tones with tube warmth",
        amp_families=["marshall_plexi", "fender_clean", "tweed"],
        gain_range=(0.30, 0.55),
        gain_character="crunch",
        cab_configs=["4x12", "2x12"],
        speaker_chars=["g12m_like", "g12h_like"],
        bass_range=(0.45, 0.55),
        mid_range=(0.55, 0.70),
        treble_range=(0.45, 0.60),
        presence_range=(0.45, 0.55),
        effect_chain=EffectChainTemplate(
            includes_drive=True,
            includes_reverb=True,
            includes_delay=True,
            drive_types=["tube_screamer", "fuzz"],
            delay_types=["tape", "analog_bbd"],
            reverb_types=["spring", "room"],
            drive_range=(0.2, 0.5),
            delay_mix_range=(0.1, 0.25),
            reverb_mix_range=(0.1, 0.25),
        ),
        production_era="vintage",
        reference_artists=["Led Zeppelin", "AC/DC", "Cream"],
    ),

    "80s_rock": ToneArchetype(
        name="80s_rock",
        display_name="80s Rock",
        description="Big, polished 80s rock tones with chorus",
        amp_families=["marshall_jcm", "soldano", "bogner"],
        gain_range=(0.45, 0.70),
        gain_character="crunch",
        cab_configs=["4x12"],
        speaker_chars=["v30_like", "g12m_like"],
        bass_range=(0.40, 0.55),
        mid_range=(0.50, 0.65),
        treble_range=(0.50, 0.65),
        presence_range=(0.50, 0.60),
        effect_chain=EffectChainTemplate(
            includes_drive=True,
            includes_delay=True,
            includes_reverb=True,
            includes_modulation=True,
            drive_types=["tube_screamer", "overdrive"],
            delay_types=["digital", "stereo"],
            reverb_types=["plate", "hall"],
            modulation_types=["chorus"],
            drive_range=(0.3, 0.6),
            delay_mix_range=(0.15, 0.30),
            reverb_mix_range=(0.15, 0.30),
            modulation_depth_range=(0.2, 0.5),
        ),
        production_era="80s",
        reference_artists=["Van Halen", "Def Leppard", "Bon Jovi"],
    ),

    # Metal archetypes
    "80s_metal": ToneArchetype(
        name="80s_metal",
        display_name="80s Metal",
        description="Classic heavy metal crunch",
        amp_families=["marshall_jcm", "5150_peavey"],
        gain_range=(0.60, 0.80),
        gain_character="high_gain",
        cab_configs=["4x12"],
        speaker_chars=["v30_like", "g12m_like"],
        bass_range=(0.40, 0.55),
        mid_range=(0.35, 0.50),  # Slightly scooped
        treble_range=(0.55, 0.70),
        presence_range=(0.50, 0.65),
        effect_chain=EffectChainTemplate(
            includes_drive=True,
            includes_compression=True,
            drive_types=["tube_screamer", "overdrive"],
            reverb_types=["plate_short"],
            drive_range=(0.2, 0.4),
        ),
        production_era="80s",
        dynamic_range="compressed",
        reference_artists=["Metallica", "Iron Maiden", "Judas Priest"],
    ),

    "modern_metal": ToneArchetype(
        name="modern_metal",
        display_name="Modern Metal",
        description="Tight, aggressive modern metal tones",
        amp_families=["5150_peavey", "mesa_rectifier", "bogner"],
        gain_range=(0.75, 0.95),
        gain_character="high_gain",
        cab_configs=["4x12"],
        speaker_chars=["v30_like"],
        bass_range=(0.45, 0.55),
        mid_range=(0.30, 0.45),  # Scooped
        treble_range=(0.55, 0.70),
        presence_range=(0.55, 0.70),
        effect_chain=EffectChainTemplate(
            includes_drive=True,
            includes_compression=True,
            drive_types=["tube_screamer", "precision_drive"],
            drive_range=(0.1, 0.3),  # Boost, not main gain
        ),
        production_era="modern",
        dynamic_range="compressed",
        reference_artists=["Periphery", "Meshuggah", "Trivium"],
    ),

    "djent": ToneArchetype(
        name="djent",
        display_name="Djent / Progressive Metal",
        description="Ultra-tight modern progressive tones",
        amp_families=["5150_peavey", "bogner", "mesa_rectifier"],
        gain_range=(0.80, 0.98),
        gain_character="high_gain",
        cab_configs=["4x12"],
        speaker_chars=["v30_like"],
        bass_range=(0.35, 0.50),
        mid_range=(0.25, 0.40),
        treble_range=(0.55, 0.70),
        presence_range=(0.60, 0.75),
        effect_chain=EffectChainTemplate(
            includes_drive=True,
            includes_compression=True,
            drive_types=["precision_drive", "tube_screamer"],
            drive_range=(0.05, 0.25),
        ),
        production_era="modern",
        dynamic_range="compressed",
        reference_artists=["Animals as Leaders", "TesseracT", "Periphery"],
    ),

    # Blues archetypes
    "blues": ToneArchetype(
        name="blues",
        display_name="Blues",
        description="Warm, responsive blues tones",
        amp_families=["fender_clean", "tweed", "dumble"],
        gain_range=(0.15, 0.40),
        gain_character="clean",
        cab_configs=["1x12", "2x10"],
        speaker_chars=["jensen_like", "alnico_blue_like"],
        bass_range=(0.45, 0.60),
        mid_range=(0.50, 0.65),
        treble_range=(0.40, 0.55),
        presence_range=(0.40, 0.50),
        effect_chain=EffectChainTemplate(
            includes_drive=True,
            includes_reverb=True,
            drive_types=["tube_screamer", "blues_driver"],
            delay_types=["tape"],
            reverb_types=["spring"],
            drive_range=(0.2, 0.5),
            reverb_mix_range=(0.1, 0.25),
        ),
        production_era="vintage",
        dynamic_range="dynamic",
        reference_artists=["Stevie Ray Vaughan", "B.B. King", "Eric Clapton"],
    ),

    # Clean archetypes
    "jazz": ToneArchetype(
        name="jazz",
        display_name="Jazz",
        description="Warm, clean jazz tones",
        amp_families=["fender_clean", "tweed"],
        gain_range=(0.05, 0.20),
        gain_character="clean",
        cab_configs=["1x12", "1x15"],
        speaker_chars=["jensen_like", "alnico_blue_like"],
        bass_range=(0.50, 0.65),
        mid_range=(0.50, 0.60),
        treble_range=(0.30, 0.45),
        presence_range=(0.30, 0.45),
        effect_chain=EffectChainTemplate(
            includes_reverb=True,
            reverb_types=["room_small"],
            reverb_mix_range=(0.05, 0.15),
        ),
        production_era="vintage",
        dynamic_range="dynamic",
        reference_artists=["Wes Montgomery", "Joe Pass", "Pat Metheny"],
    ),

    "country": ToneArchetype(
        name="country",
        display_name="Country",
        description="Bright, twangy country tones",
        amp_families=["fender_clean", "tweed"],
        gain_range=(0.10, 0.30),
        gain_character="clean",
        cab_configs=["1x12", "2x12"],
        speaker_chars=["jensen_like"],
        bass_range=(0.40, 0.50),
        mid_range=(0.45, 0.55),
        treble_range=(0.55, 0.70),
        presence_range=(0.50, 0.60),
        effect_chain=EffectChainTemplate(
            includes_compression=True,
            includes_reverb=True,
            includes_delay=True,
            delay_types=["slapback"],
            reverb_types=["spring"],
            delay_mix_range=(0.1, 0.2),
            reverb_mix_range=(0.1, 0.2),
        ),
        production_era="vintage",
        reference_artists=["Brad Paisley", "Keith Urban", "Vince Gill"],
    ),

    # Alternative/indie archetypes
    "indie_rock": ToneArchetype(
        name="indie_rock",
        display_name="Indie Rock",
        description="Jangly, textured indie tones",
        amp_families=["vox_chime", "fender_clean", "ac30"],
        gain_range=(0.20, 0.45),
        gain_character="crunch",
        cab_configs=["2x12", "1x12"],
        speaker_chars=["alnico_blue_like", "g12h_like"],
        bass_range=(0.40, 0.55),
        mid_range=(0.50, 0.65),
        treble_range=(0.50, 0.65),
        presence_range=(0.50, 0.60),
        effect_chain=EffectChainTemplate(
            includes_delay=True,
            includes_reverb=True,
            includes_modulation=True,
            delay_types=["analog_bbd", "tape"],
            reverb_types=["room", "plate"],
            modulation_types=["chorus", "tremolo"],
            delay_mix_range=(0.15, 0.35),
            reverb_mix_range=(0.15, 0.35),
            modulation_depth_range=(0.2, 0.5),
        ),
        production_era="90s",
        reference_artists=["Radiohead", "The Strokes", "Arctic Monkeys"],
    ),

    "shoegaze": ToneArchetype(
        name="shoegaze",
        display_name="Shoegaze",
        description="Heavily effected, atmospheric tones",
        amp_families=["vox_chime", "fender_clean", "marshall_plexi"],
        gain_range=(0.35, 0.60),
        gain_character="crunch",
        cab_configs=["2x12"],
        speaker_chars=["alnico_blue_like", "g12m_like"],
        bass_range=(0.45, 0.60),
        mid_range=(0.40, 0.55),
        treble_range=(0.45, 0.60),
        presence_range=(0.40, 0.55),
        effect_chain=EffectChainTemplate(
            includes_drive=True,
            includes_delay=True,
            includes_reverb=True,
            includes_modulation=True,
            drive_types=["fuzz", "distortion"],
            delay_types=["analog_bbd", "modulated"],
            reverb_types=["hall_large", "shimmer"],
            modulation_types=["chorus", "flanger"],
            drive_range=(0.4, 0.7),
            delay_mix_range=(0.30, 0.50),
            reverb_mix_range=(0.35, 0.60),
            modulation_depth_range=(0.3, 0.6),
        ),
        production_era="90s",
        reference_artists=["My Bloody Valentine", "Slowdive", "Ride"],
    ),

    "ambient": ToneArchetype(
        name="ambient",
        display_name="Ambient",
        description="Spacious, ethereal ambient tones",
        amp_families=["fender_clean", "vox_chime"],
        gain_range=(0.05, 0.25),
        gain_character="clean",
        cab_configs=["1x12"],
        speaker_chars=["alnico_blue_like", "jensen_like"],
        bass_range=(0.40, 0.55),
        mid_range=(0.45, 0.55),
        treble_range=(0.50, 0.65),
        presence_range=(0.45, 0.55),
        effect_chain=EffectChainTemplate(
            includes_delay=True,
            includes_reverb=True,
            includes_modulation=True,
            delay_types=["dotted_eighth", "modulated"],
            reverb_types=["shimmer", "hall_large"],
            modulation_types=["chorus", "tremolo"],
            delay_mix_range=(0.35, 0.60),
            reverb_mix_range=(0.40, 0.70),
            modulation_depth_range=(0.2, 0.5),
        ),
        production_era="modern",
        dynamic_range="dynamic",
        reference_artists=["The Edge", "Andy Summers", "Brian Eno"],
    ),

    # Electronic/synth
    "synthwave": ToneArchetype(
        name="synthwave",
        display_name="Synthwave",
        description="80s-inspired synth and guitar tones",
        amp_families=["marshall_jcm", "fender_clean"],
        gain_range=(0.30, 0.60),
        gain_character="crunch",
        cab_configs=["4x12", "2x12"],
        speaker_chars=["v30_like", "g12m_like"],
        bass_range=(0.50, 0.65),
        mid_range=(0.45, 0.60),
        treble_range=(0.50, 0.65),
        presence_range=(0.50, 0.60),
        effect_chain=EffectChainTemplate(
            includes_delay=True,
            includes_reverb=True,
            includes_modulation=True,
            delay_types=["digital", "dotted_eighth"],
            reverb_types=["plate", "hall"],
            modulation_types=["chorus"],
            delay_mix_range=(0.2, 0.4),
            reverb_mix_range=(0.2, 0.4),
            modulation_depth_range=(0.3, 0.6),
        ),
        production_era="80s",
        reference_artists=["Gunship", "The Midnight", "FM-84"],
    ),
}


def get_archetype(name: str) -> Optional[ToneArchetype]:
    """Get an archetype by name."""
    return ARCHETYPES.get(name.lower().replace(" ", "_").replace("-", "_"))


def get_archetype_for_genre(
    genre: str,
    subgenre: Optional[str] = None,
) -> Optional[ToneArchetype]:
    """Get the best archetype for a genre.

    Args:
        genre: Primary genre
        subgenre: Optional subgenre for more specific matching

    Returns:
        Matching archetype or None
    """
    # Try subgenre first
    if subgenre:
        archetype = get_archetype(subgenre)
        if archetype:
            return archetype

    # Try genre directly
    archetype = get_archetype(genre)
    if archetype:
        return archetype

    # Map genres to archetypes
    genre_map = {
        "rock": "classic_rock",
        "metal": "modern_metal",
        "blues": "blues",
        "jazz": "jazz",
        "country": "country",
        "funk": "blues",  # Similar clean tones
        "pop": "indie_rock",
        "indie": "indie_rock",
        "ambient": "ambient",
        "electronic": "synthwave",
        "punk": "classic_rock",
        "progressive": "modern_metal",
    }

    mapped = genre_map.get(genre.lower())
    if mapped:
        return get_archetype(mapped)

    return None


def list_archetypes() -> List[str]:
    """List all available archetype names."""
    return list(ARCHETYPES.keys())


def get_archetype_categories() -> Dict[str, List[str]]:
    """Get archetypes organized by category."""
    categories = {
        "rock": ["classic_rock", "80s_rock"],
        "metal": ["80s_metal", "modern_metal", "djent"],
        "blues_jazz": ["blues", "jazz"],
        "clean": ["country", "jazz"],
        "indie_alt": ["indie_rock", "shoegaze", "ambient"],
        "electronic": ["synthwave"],
    }
    return categories
