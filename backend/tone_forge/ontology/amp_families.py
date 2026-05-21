"""Canonical amp family definitions and relationships.

This module defines the authoritative vocabulary for amp families,
including aliases, traits, and relationships. Use these definitions
consistently across:
- Analysis (classifier outputs)
- Translation (block matching)
- Embeddings (semantic clustering)
- Export (preset generation)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Set


@dataclass
class AmpFamilyInfo:
    """Comprehensive information about an amp family."""
    id: str
    display_name: str
    description: str

    # Gain characteristics
    gain_range: tuple  # (min_typical, max_typical) in 0-1
    gain_character: str  # "clean", "edge_of_breakup", "crunch", "high_gain"

    # Tonal traits
    bass_response: str  # "tight", "full", "loose", "scooped"
    mid_character: str  # "scooped", "flat", "honky", "present"
    treble_character: str  # "dark", "neutral", "bright", "fizzy"
    presence_emphasis: str  # "smooth", "neutral", "aggressive", "harsh"

    # Historical context
    era: str  # "vintage", "classic", "modern"
    origin: str  # "american", "british", "german"
    reference_amps: List[str]  # Real amps this family models

    # Related families
    related: List[str]  # Sonically similar families
    contrasts: List[str]  # Sonically different families

    # Typical use cases
    genres: List[str]
    playing_styles: List[str]


# Canonical amp families with full metadata
AMP_FAMILIES: Dict[str, AmpFamilyInfo] = {
    "fender_clean": AmpFamilyInfo(
        id="fender_clean",
        display_name="Fender Clean",
        description="Classic American clean tones with bell-like clarity",
        gain_range=(0.0, 0.35),
        gain_character="clean",
        bass_response="full",
        mid_character="flat",
        treble_character="bright",
        presence_emphasis="smooth",
        era="vintage",
        origin="american",
        reference_amps=["Fender Twin Reverb", "Fender Deluxe Reverb", "Fender Princeton"],
        related=["tweed", "dumble"],
        contrasts=["mesa_rectifier", "5150_peavey"],
        genres=["blues", "country", "jazz", "r&b", "pop"],
        playing_styles=["clean_strum", "fingerpicking", "jazz_comping"],
    ),
    "tweed": AmpFamilyInfo(
        id="tweed",
        display_name="Tweed",
        description="Vintage American warmth with natural compression",
        gain_range=(0.1, 0.5),
        gain_character="edge_of_breakup",
        bass_response="loose",
        mid_character="present",
        treble_character="dark",
        presence_emphasis="smooth",
        era="vintage",
        origin="american",
        reference_amps=["Fender Bassman", "Fender Deluxe (tweed)", "Fender Champ"],
        related=["fender_clean", "dumble"],
        contrasts=["marshall_jcm", "5150_peavey"],
        genres=["blues", "country", "rockabilly", "classic_rock"],
        playing_styles=["chord_riff", "lead", "clean_strum"],
    ),
    "vox_chime": AmpFamilyInfo(
        id="vox_chime",
        display_name="Vox Chime",
        description="British chime with jangly top-end sparkle",
        gain_range=(0.15, 0.55),
        gain_character="edge_of_breakup",
        bass_response="tight",
        mid_character="present",
        treble_character="bright",
        presence_emphasis="aggressive",
        era="vintage",
        origin="british",
        reference_amps=["Vox AC15", "Vox AC30"],
        related=["ac30"],
        contrasts=["mesa_rectifier", "fender_clean"],
        genres=["british_invasion", "indie", "jangle_pop", "classic_rock"],
        playing_styles=["chord_riff", "arpeggios", "jangle"],
    ),
    "ac30": AmpFamilyInfo(
        id="ac30",
        display_name="AC30",
        description="Pushed Vox with more saturation and compression",
        gain_range=(0.3, 0.65),
        gain_character="crunch",
        bass_response="tight",
        mid_character="present",
        treble_character="bright",
        presence_emphasis="aggressive",
        era="vintage",
        origin="british",
        reference_amps=["Vox AC30 Top Boost"],
        related=["vox_chime", "marshall_plexi"],
        contrasts=["fender_clean", "mesa_rectifier"],
        genres=["classic_rock", "indie", "alternative", "british_rock"],
        playing_styles=["chord_riff", "lead", "power_chords"],
    ),
    "marshall_plexi": AmpFamilyInfo(
        id="marshall_plexi",
        display_name="Marshall Plexi",
        description="Classic British crunch with midrange growl",
        gain_range=(0.25, 0.65),
        gain_character="crunch",
        bass_response="tight",
        mid_character="present",
        treble_character="neutral",
        presence_emphasis="aggressive",
        era="vintage",
        origin="british",
        reference_amps=["Marshall 1959 Super Lead", "Marshall JTM45"],
        related=["marshall_jcm", "ac30"],
        contrasts=["fender_clean", "mesa_rectifier"],
        genres=["classic_rock", "hard_rock", "blues_rock"],
        playing_styles=["power_chords", "lead", "chord_riff"],
    ),
    "marshall_jcm": AmpFamilyInfo(
        id="marshall_jcm",
        display_name="Marshall JCM",
        description="Hot-rodded British tone with more gain and bite",
        gain_range=(0.4, 0.8),
        gain_character="crunch",
        bass_response="tight",
        mid_character="honky",
        treble_character="neutral",
        presence_emphasis="aggressive",
        era="classic",
        origin="british",
        reference_amps=["Marshall JCM800", "Marshall JCM900", "Marshall JCM2000"],
        related=["marshall_plexi", "bogner"],
        contrasts=["fender_clean", "tweed"],
        genres=["hard_rock", "metal", "punk", "80s_rock"],
        playing_styles=["power_chords", "palm_mute", "lead", "shred"],
    ),
    "mesa_rectifier": AmpFamilyInfo(
        id="mesa_rectifier",
        display_name="Mesa Rectifier",
        description="Modern high-gain with scooped mids and massive low end",
        gain_range=(0.6, 0.98),
        gain_character="high_gain",
        bass_response="full",
        mid_character="scooped",
        treble_character="neutral",
        presence_emphasis="smooth",
        era="modern",
        origin="american",
        reference_amps=["Mesa Dual Rectifier", "Mesa Triple Rectifier"],
        related=["5150_peavey", "bogner"],
        contrasts=["fender_clean", "tweed", "vox_chime"],
        genres=["metal", "nu_metal", "hard_rock", "djent"],
        playing_styles=["palm_mute", "chug", "power_chords", "lead"],
    ),
    "5150_peavey": AmpFamilyInfo(
        id="5150_peavey",
        display_name="5150/6505",
        description="Aggressive high-gain with cutting upper-mids",
        gain_range=(0.65, 0.98),
        gain_character="high_gain",
        bass_response="tight",
        mid_character="present",
        treble_character="bright",
        presence_emphasis="aggressive",
        era="modern",
        origin="american",
        reference_amps=["Peavey 5150", "Peavey 6505", "EVH 5150 III"],
        related=["mesa_rectifier", "bogner"],
        contrasts=["fender_clean", "tweed"],
        genres=["metal", "hardcore", "thrash", "metalcore"],
        playing_styles=["palm_mute", "shred", "power_chords", "chug"],
    ),
    "bogner": AmpFamilyInfo(
        id="bogner",
        display_name="Bogner",
        description="Modern high-gain with articulate note definition",
        gain_range=(0.5, 0.9),
        gain_character="high_gain",
        bass_response="tight",
        mid_character="flat",
        treble_character="neutral",
        presence_emphasis="smooth",
        era="modern",
        origin="german",
        reference_amps=["Bogner Ecstasy", "Bogner Uberschall", "Bogner Shiva"],
        related=["marshall_jcm", "soldano"],
        contrasts=["fender_clean", "tweed"],
        genres=["rock", "hard_rock", "progressive", "metal"],
        playing_styles=["lead", "chord_riff", "power_chords"],
    ),
    "soldano": AmpFamilyInfo(
        id="soldano",
        display_name="Soldano",
        description="Smooth high-gain with singing sustain",
        gain_range=(0.5, 0.88),
        gain_character="high_gain",
        bass_response="tight",
        mid_character="present",
        treble_character="neutral",
        presence_emphasis="smooth",
        era="modern",
        origin="american",
        reference_amps=["Soldano SLO-100", "Soldano Decatone"],
        related=["bogner", "marshall_jcm"],
        contrasts=["fender_clean", "mesa_rectifier"],
        genres=["hard_rock", "80s_rock", "shred", "progressive"],
        playing_styles=["lead", "shred", "power_chords"],
    ),
    "dumble": AmpFamilyInfo(
        id="dumble",
        display_name="Dumble",
        description="Boutique overdrive with touch sensitivity and clarity",
        gain_range=(0.2, 0.6),
        gain_character="edge_of_breakup",
        bass_response="full",
        mid_character="present",
        treble_character="neutral",
        presence_emphasis="smooth",
        era="modern",
        origin="american",
        reference_amps=["Dumble Overdrive Special", "Two-Rock Custom"],
        related=["fender_clean", "tweed"],
        contrasts=["mesa_rectifier", "5150_peavey"],
        genres=["blues", "jazz_fusion", "country", "rnb"],
        playing_styles=["lead", "clean_strum", "fingerpicking"],
    ),
    "unknown": AmpFamilyInfo(
        id="unknown",
        display_name="Unknown",
        description="Unclassified amp type",
        gain_range=(0.0, 1.0),
        gain_character="unknown",
        bass_response="neutral",
        mid_character="flat",
        treble_character="neutral",
        presence_emphasis="neutral",
        era="unknown",
        origin="unknown",
        reference_amps=[],
        related=[],
        contrasts=[],
        genres=[],
        playing_styles=[],
    ),
}


# Aliases for fuzzy matching and user input normalization
AMP_FAMILY_ALIASES: Dict[str, str] = {
    # Fender Clean
    "fender": "fender_clean",
    "twin": "fender_clean",
    "twin_reverb": "fender_clean",
    "deluxe_reverb": "fender_clean",
    "princeton": "fender_clean",
    "clean": "fender_clean",
    "american_clean": "fender_clean",
    "blackface": "fender_clean",
    "silverface": "fender_clean",

    # Tweed
    "bassman": "tweed",
    "tweed_deluxe": "tweed",
    "tweed_champ": "tweed",
    "vintage_fender": "tweed",

    # Vox
    "vox": "vox_chime",
    "ac15": "vox_chime",
    "chime": "vox_chime",
    "jangle": "vox_chime",
    "british_clean": "vox_chime",
    "ac30_clean": "vox_chime",
    "top_boost": "ac30",

    # Marshall
    "marshall": "marshall_plexi",
    "plexi": "marshall_plexi",
    "super_lead": "marshall_plexi",
    "jtm": "marshall_plexi",
    "jtm45": "marshall_plexi",
    "1959": "marshall_plexi",
    "jcm800": "marshall_jcm",
    "jcm900": "marshall_jcm",
    "jcm2000": "marshall_jcm",
    "hot_rod_marshall": "marshall_jcm",

    # High Gain
    "rectifier": "mesa_rectifier",
    "mesa": "mesa_rectifier",
    "dual_rec": "mesa_rectifier",
    "triple_rec": "mesa_rectifier",
    "recto": "mesa_rectifier",
    "5150": "5150_peavey",
    "6505": "5150_peavey",
    "peavey": "5150_peavey",
    "evh": "5150_peavey",

    # Boutique
    "ecstasy": "bogner",
    "uberschall": "bogner",
    "shiva": "bogner",
    "slo": "soldano",
    "slo100": "soldano",
    "dumble_style": "dumble",
    "ods": "dumble",
    "overdrive_special": "dumble",
    "two_rock": "dumble",
}


# Traits for semantic embedding
AMP_FAMILY_TRAITS: Dict[str, List[str]] = {
    "fender_clean": ["clean", "bright", "headroom", "american", "vintage", "bell_like"],
    "tweed": ["warm", "compressed", "vintage", "american", "creamy", "loose_bass"],
    "vox_chime": ["chimey", "jangly", "british", "bright", "vintage", "class_a"],
    "ac30": ["chimey", "crunchy", "british", "compressed", "singing"],
    "marshall_plexi": ["british", "crunchy", "midrange", "rock", "vintage", "responsive"],
    "marshall_jcm": ["british", "aggressive", "tight", "80s", "hot_rodded"],
    "mesa_rectifier": ["modern", "scooped", "heavy", "american", "saturated"],
    "5150_peavey": ["aggressive", "tight", "cutting", "modern", "metal"],
    "bogner": ["articulate", "modern", "german", "versatile", "defined"],
    "soldano": ["smooth", "singing", "sustain", "80s", "boutique"],
    "dumble": ["boutique", "dynamic", "touch_sensitive", "expensive", "rare"],
    "unknown": [],
}


def get_amp_family_info(family: str) -> Optional[AmpFamilyInfo]:
    """Get full information about an amp family."""
    family = normalize_amp_family(family)
    return AMP_FAMILIES.get(family)


def normalize_amp_family(family: str) -> str:
    """Normalize an amp family name to canonical form.

    Handles aliases and common variations.
    """
    family = family.lower().strip().replace(" ", "_").replace("-", "_")

    # Check aliases first
    if family in AMP_FAMILY_ALIASES:
        return AMP_FAMILY_ALIASES[family]

    # Check canonical names
    if family in AMP_FAMILIES:
        return family

    return "unknown"


def get_related_families(family: str, include_self: bool = False) -> List[str]:
    """Get families related to the given family.

    Useful for suggesting alternatives or expanding search.
    """
    family = normalize_amp_family(family)
    info = AMP_FAMILIES.get(family)

    if info is None:
        return []

    related = list(info.related)
    if include_self:
        related.insert(0, family)

    return related
