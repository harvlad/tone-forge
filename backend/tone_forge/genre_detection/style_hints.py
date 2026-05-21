"""Genre-specific style hints for tone refinement.

Generates contextual hints based on genre, archetype, and
detected tone characteristics. Used to:
- Suggest parameter adjustments
- Highlight genre-specific techniques
- Guide users toward better tones

Hints are actionable suggestions, not rules.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any

from .classifier import GenrePrediction
from .archetypes import ToneArchetype, get_archetype_for_genre

logger = logging.getLogger(__name__)


@dataclass
class StyleHint:
    """A single style hint/suggestion."""

    category: str       # "amp", "cab", "effects", "technique", "mixing"
    priority: int       # 1=high, 2=medium, 3=low
    message: str        # The hint text
    action: str         # Suggested action ("increase", "decrease", "try", etc.)
    parameter: Optional[str] = None  # Specific parameter if applicable
    value_suggestion: Optional[str] = None  # Suggested value/range


def generate_genre_hints(
    genre_prediction: GenrePrediction,
    descriptor: Optional[Dict] = None,
) -> List[StyleHint]:
    """Generate style hints based on genre classification.

    Args:
        genre_prediction: Genre classification result
        descriptor: ToneDescriptor as dict (optional, for context)

    Returns:
        List of StyleHint suggestions
    """
    hints = []

    genre = genre_prediction.primary_genre
    subgenre = genre_prediction.primary_subgenre
    archetype = get_archetype_for_genre(genre, subgenre)

    if archetype:
        hints.extend(_hints_from_archetype(archetype, descriptor))

    # Genre-specific hints
    if genre == "metal":
        hints.extend(_metal_hints(genre_prediction, descriptor))
    elif genre == "blues":
        hints.extend(_blues_hints(genre_prediction, descriptor))
    elif genre == "jazz":
        hints.extend(_jazz_hints(genre_prediction, descriptor))
    elif genre in ("rock", "indie"):
        hints.extend(_rock_hints(genre_prediction, descriptor))
    elif genre == "ambient":
        hints.extend(_ambient_hints(genre_prediction, descriptor))
    elif genre == "electronic":
        hints.extend(_electronic_hints(genre_prediction, descriptor))

    # Era-specific hints
    hints.extend(_era_hints(genre_prediction.production_era, descriptor))

    # Sort by priority
    hints.sort(key=lambda h: h.priority)

    return hints


def _hints_from_archetype(
    archetype: ToneArchetype,
    descriptor: Optional[Dict],
) -> List[StyleHint]:
    """Generate hints based on archetype comparison."""
    hints = []

    if not descriptor:
        return hints

    amp = descriptor.get("amp", {})
    gain = amp.get("gain", 0.5)
    voicing = amp.get("voicing", {})

    # Check gain against archetype range
    min_gain, max_gain = archetype.gain_range
    if gain < min_gain - 0.1:
        hints.append(StyleHint(
            category="amp",
            priority=2,
            message=f"Gain seems low for {archetype.display_name}. "
                   f"Typical range is {int(min_gain*10)}-{int(max_gain*10)}.",
            action="increase",
            parameter="gain",
            value_suggestion=f"{int(min_gain*10)}-{int(max_gain*10)}",
        ))
    elif gain > max_gain + 0.1:
        hints.append(StyleHint(
            category="amp",
            priority=2,
            message=f"Gain seems high for {archetype.display_name}. "
                   f"Consider backing off for more clarity.",
            action="decrease",
            parameter="gain",
            value_suggestion=f"{int(min_gain*10)}-{int(max_gain*10)}",
        ))

    # Check mid range for styles that need it
    mid = voicing.get("mid", 0.5)
    min_mid, max_mid = archetype.mid_range
    if mid < min_mid - 0.1:
        hints.append(StyleHint(
            category="amp",
            priority=2,
            message=f"Mids are scooped - {archetype.display_name} typically "
                   f"uses more mid presence for cut.",
            action="increase",
            parameter="mid",
            value_suggestion=f"{int(min_mid*10)}-{int(max_mid*10)}",
        ))

    return hints


def _metal_hints(
    prediction: GenrePrediction,
    descriptor: Optional[Dict],
) -> List[StyleHint]:
    """Metal-specific hints."""
    hints = []

    subgenre = prediction.primary_subgenre

    if subgenre == "djent":
        hints.append(StyleHint(
            category="technique",
            priority=2,
            message="For djent tones, tighten the low end with a high-pass "
                   "filter around 80-100Hz before the amp.",
            action="try",
            parameter="eq",
        ))
        hints.append(StyleHint(
            category="effects",
            priority=2,
            message="A precision boost (low-gain overdrive) before the amp "
                   "tightens attack and adds definition.",
            action="try",
            parameter="drive",
        ))

    elif subgenre == "modern_metal":
        hints.append(StyleHint(
            category="amp",
            priority=2,
            message="Modern metal tones benefit from scooped mids but don't "
                   "go too far - you need cut in the mix.",
            action="balance",
            parameter="mid",
            value_suggestion="3-5",
        ))

    # General metal hints
    if prediction.is_aggressive:
        hints.append(StyleHint(
            category="mixing",
            priority=3,
            message="Aggressive tones can get muddy - consider a slight cut "
                   "around 200-400Hz for clarity.",
            action="decrease",
            parameter="eq_low_mid",
        ))

    return hints


def _blues_hints(
    prediction: GenrePrediction,
    descriptor: Optional[Dict],
) -> List[StyleHint]:
    """Blues-specific hints."""
    hints = []

    hints.append(StyleHint(
        category="amp",
        priority=2,
        message="Blues tones shine with the amp just at the edge of breakup. "
               "Let your pick attack control the dynamics.",
        action="set",
        parameter="gain",
        value_suggestion="3-5",
    ))

    hints.append(StyleHint(
        category="technique",
        priority=3,
        message="Spring reverb is classic for blues - a touch adds "
               "that vintage room sound without washing out the tone.",
        action="try",
        parameter="reverb",
    ))

    return hints


def _jazz_hints(
    prediction: GenrePrediction,
    descriptor: Optional[Dict],
) -> List[StyleHint]:
    """Jazz-specific hints."""
    hints = []

    hints.append(StyleHint(
        category="amp",
        priority=1,
        message="Jazz tones are typically very clean with rolled-off treble "
               "for warmth. Neck pickup recommended.",
        action="decrease",
        parameter="treble",
        value_suggestion="3-5",
    ))

    hints.append(StyleHint(
        category="effects",
        priority=2,
        message="Most jazz players avoid effects entirely - if using any, "
               "keep reverb subtle and avoid delay.",
        action="minimize",
        parameter="effects",
    ))

    return hints


def _rock_hints(
    prediction: GenrePrediction,
    descriptor: Optional[Dict],
) -> List[StyleHint]:
    """Rock/indie-specific hints."""
    hints = []

    subgenre = prediction.primary_subgenre

    if subgenre == "shoegaze":
        hints.append(StyleHint(
            category="effects",
            priority=1,
            message="Shoegaze lives in the effects - layer reverb and delay "
                   "heavily, and don't be afraid of modulation.",
            action="increase",
            parameter="reverb",
            value_suggestion="40-60% mix",
        ))
        hints.append(StyleHint(
            category="effects",
            priority=2,
            message="Fuzz before modulation creates that classic wall of "
                   "sound texture.",
            action="try",
            parameter="drive",
        ))

    elif subgenre == "indie_rock":
        hints.append(StyleHint(
            category="amp",
            priority=2,
            message="Indie rock often uses British-voiced amps (Vox, Marshall) "
                   "for chime and jangle.",
            action="try",
            parameter="amp_family",
        ))

    # Era hints for rock
    if prediction.production_era == "80s":
        hints.append(StyleHint(
            category="effects",
            priority=2,
            message="80s rock tones often feature chorus on clean and crunch "
                   "sections for width and shimmer.",
            action="try",
            parameter="chorus",
        ))

    return hints


def _ambient_hints(
    prediction: GenrePrediction,
    descriptor: Optional[Dict],
) -> List[StyleHint]:
    """Ambient-specific hints."""
    hints = []

    hints.append(StyleHint(
        category="effects",
        priority=1,
        message="Ambient guitar relies heavily on reverb and delay - "
               "experiment with shimmer reverbs and long decay times.",
        action="try",
        parameter="reverb",
        value_suggestion="hall or shimmer, 50-70% mix",
    ))

    hints.append(StyleHint(
        category="technique",
        priority=2,
        message="Volume swells (using volume knob or pedal) are essential "
               "for ambient textures - remove the attack for pad-like sounds.",
        action="try",
        parameter="volume_swell",
    ))

    hints.append(StyleHint(
        category="effects",
        priority=2,
        message="Dotted-eighth delays create rhythmic motion and are "
               "classic for ambient and post-rock.",
        action="try",
        parameter="delay",
        value_suggestion="dotted-eighth timing",
    ))

    return hints


def _electronic_hints(
    prediction: GenrePrediction,
    descriptor: Optional[Dict],
) -> List[StyleHint]:
    """Electronic/synthwave-specific hints."""
    hints = []

    hints.append(StyleHint(
        category="effects",
        priority=1,
        message="Synthwave guitar tones often blend 80s-style chorus "
               "and gated reverb for that retro feel.",
        action="try",
        parameter="chorus",
    ))

    hints.append(StyleHint(
        category="mixing",
        priority=2,
        message="In electronic contexts, guitar often sits behind synths - "
               "cut some low-mids to avoid frequency competition.",
        action="decrease",
        parameter="eq_low_mid",
    ))

    return hints


def _era_hints(
    era: str,
    descriptor: Optional[Dict],
) -> List[StyleHint]:
    """Production era-specific hints."""
    hints = []

    if era == "vintage":
        hints.append(StyleHint(
            category="mixing",
            priority=3,
            message="Vintage tones often benefit from slightly rolling off "
                   "the very high frequencies for warmth.",
            action="decrease",
            parameter="presence",
        ))

    elif era == "80s":
        hints.append(StyleHint(
            category="effects",
            priority=3,
            message="80s production often featured tight gated reverbs "
                   "on guitars - try a short plate or room.",
            action="try",
            parameter="reverb",
        ))

    elif era == "modern":
        hints.append(StyleHint(
            category="mixing",
            priority=3,
            message="Modern production tends to be brighter and tighter - "
                   "don't over-process the low end.",
            action="check",
            parameter="low_end",
        ))

    return hints


def format_hints_for_display(hints: List[StyleHint]) -> List[str]:
    """Format hints as human-readable strings.

    Args:
        hints: List of StyleHint objects

    Returns:
        List of formatted hint strings
    """
    formatted = []

    for hint in hints:
        if hint.value_suggestion:
            formatted.append(
                f"[{hint.category.upper()}] {hint.message} "
                f"(Suggested: {hint.value_suggestion})"
            )
        else:
            formatted.append(f"[{hint.category.upper()}] {hint.message}")

    return formatted


def get_quick_tips(genre: str, limit: int = 3) -> List[str]:
    """Get quick tips for a genre without full analysis.

    Args:
        genre: Genre name
        limit: Maximum number of tips

    Returns:
        List of tip strings
    """
    tips = {
        "metal": [
            "Tight low-end is key - use a high-pass filter before the amp",
            "A tube screamer or precision boost tightens the attack",
            "V30-style speakers add the mid-range cut metal needs",
        ],
        "blues": [
            "Keep the amp just at the edge of breakup for dynamics",
            "Spring reverb adds authentic vintage character",
            "Less is more - let your playing provide the expression",
        ],
        "jazz": [
            "Roll off the treble for a warmer, rounder tone",
            "Keep effects minimal or absent entirely",
            "Neck pickup with the tone rolled back is classic",
        ],
        "rock": [
            "Mid-range is where rock guitar cuts through",
            "A touch of room reverb adds dimension without washing out",
            "British amp voicing (Marshall, Vox) is classic for rock",
        ],
        "ambient": [
            "Layer reverb and delay heavily - don't be subtle",
            "Volume swells remove attack for pad-like textures",
            "Shimmer reverb and dotted-eighth delays are genre staples",
        ],
        "country": [
            "Compression helps achieve that snappy, consistent attack",
            "Slapback delay is essential for classic country twang",
            "Keep it clean and bright - bridge pickup position",
        ],
    }

    genre_tips = tips.get(genre.lower(), [
        "Match your gain to the genre - cleaner for jazz/country, more for rock/metal",
        "Effects should enhance, not mask, your core tone",
        "Consider the production era for authentic sound",
    ])

    return genre_tips[:limit]
