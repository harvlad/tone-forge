"""Canonical speaker/cab definitions.

Defines vocabulary for speaker characters and cab configurations.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List


@dataclass
class SpeakerInfo:
    """Information about a speaker character."""
    id: str
    display_name: str
    description: str
    reference_speakers: List[str]
    frequency_character: str  # "warm", "balanced", "bright", "scooped"
    breakup_character: str  # "smooth", "crunchy", "aggressive"
    typical_cabs: List[str]


SPEAKER_CHARACTERS: Dict[str, SpeakerInfo] = {
    "v30_like": SpeakerInfo(
        id="v30_like",
        display_name="V30 Type",
        description="Modern high-gain character with upper-mid emphasis",
        reference_speakers=["Celestion Vintage 30", "Eminence Governor"],
        frequency_character="scooped",
        breakup_character="aggressive",
        typical_cabs=["4x12", "2x12"],
    ),
    "g12m_like": SpeakerInfo(
        id="g12m_like",
        display_name="Greenback Type",
        description="Classic British rock character with midrange focus",
        reference_speakers=["Celestion G12M Greenback", "G12M-65"],
        frequency_character="warm",
        breakup_character="crunchy",
        typical_cabs=["4x12", "2x12", "1x12"],
    ),
    "g12h_like": SpeakerInfo(
        id="g12h_like",
        display_name="G12H Type",
        description="Bright British character with extended highs",
        reference_speakers=["Celestion G12H", "G12H-75", "Heritage G12H"],
        frequency_character="bright",
        breakup_character="smooth",
        typical_cabs=["4x12", "2x12"],
    ),
    "alnico_blue_like": SpeakerInfo(
        id="alnico_blue_like",
        display_name="Alnico Blue Type",
        description="Vintage British chime with bell-like clarity",
        reference_speakers=["Celestion Blue", "Celestion Alnico Gold"],
        frequency_character="balanced",
        breakup_character="smooth",
        typical_cabs=["2x12", "1x12"],
    ),
    "jensen_like": SpeakerInfo(
        id="jensen_like",
        display_name="Jensen Type",
        description="Classic American clean with sparkling highs",
        reference_speakers=["Jensen P12R", "Jensen C12N", "Eminence Legend"],
        frequency_character="bright",
        breakup_character="smooth",
        typical_cabs=["1x12", "2x10", "1x10"],
    ),
    "unknown": SpeakerInfo(
        id="unknown",
        display_name="Unknown",
        description="Unclassified speaker type",
        reference_speakers=[],
        frequency_character="balanced",
        breakup_character="smooth",
        typical_cabs=["4x12"],
    ),
}


SPEAKER_ALIASES: Dict[str, str] = {
    "v30": "v30_like",
    "vintage30": "v30_like",
    "vintage_30": "v30_like",
    "greenback": "g12m_like",
    "g12m": "g12m_like",
    "g12h": "g12h_like",
    "blue": "alnico_blue_like",
    "alnico": "alnico_blue_like",
    "celestion_blue": "alnico_blue_like",
    "jensen": "jensen_like",
    "p12r": "jensen_like",
    "c12n": "jensen_like",
}


CAB_CONFIGURATIONS: Dict[str, Dict] = {
    "1x8": {"speakers": 1, "size": 8, "character": "small", "low_end": 0.3},
    "1x10": {"speakers": 1, "size": 10, "character": "small", "low_end": 0.4},
    "1x12": {"speakers": 1, "size": 12, "character": "medium", "low_end": 0.5},
    "2x10": {"speakers": 2, "size": 10, "character": "medium", "low_end": 0.5},
    "2x12": {"speakers": 2, "size": 12, "character": "medium", "low_end": 0.7},
    "4x10": {"speakers": 4, "size": 10, "character": "large", "low_end": 0.7},
    "4x12": {"speakers": 4, "size": 12, "character": "large", "low_end": 0.9},
}


def normalize_speaker_character(char: str) -> str:
    """Normalize speaker character to canonical form."""
    char = char.lower().strip().replace(" ", "_").replace("-", "_")

    if char in SPEAKER_ALIASES:
        return SPEAKER_ALIASES[char]

    if char in SPEAKER_CHARACTERS:
        return char

    return "unknown"
