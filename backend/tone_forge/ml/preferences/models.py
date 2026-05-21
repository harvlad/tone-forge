"""User preference models for personalized tone reconstruction.

Stores and represents user preferences learned from behavior:
- Preferred amp families and gain levels
- Effect chain patterns
- Genre/style affinities
- Equipment biases
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Any, Tuple


class PreferenceConfidence(Enum):
    """Confidence level in a learned preference."""
    LOW = "low"           # Few data points (< 5)
    MEDIUM = "medium"     # Moderate data (5-20)
    HIGH = "high"         # Strong evidence (> 20)
    USER_SET = "user_set" # Explicitly set by user


@dataclass
class AmpPreference:
    """Learned amp preferences."""

    # Preferred families ranked by usage
    preferred_families: List[str] = field(default_factory=list)

    # Gain level preferences (0-1)
    typical_gain_low: float = 0.3
    typical_gain_high: float = 0.7
    prefers_high_gain: bool = False

    # EQ tendencies (0-1, 0.5 = neutral)
    bass_tendency: float = 0.5
    mid_tendency: float = 0.5
    treble_tendency: float = 0.5
    presence_tendency: float = 0.5

    # Confidence
    confidence: PreferenceConfidence = PreferenceConfidence.LOW
    data_points: int = 0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d['confidence'] = self.confidence.value
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "AmpPreference":
        d = d.copy()
        d['confidence'] = PreferenceConfidence(d.get('confidence', 'low'))
        return cls(**d)


@dataclass
class CabPreference:
    """Learned cabinet/IR preferences."""

    # Preferred configurations
    preferred_configs: List[str] = field(default_factory=list)  # ["4x12", "2x12"]
    preferred_speakers: List[str] = field(default_factory=list)  # ["v30", "greenback"]

    # Mic position tendencies
    prefers_close_mic: bool = True
    prefers_room_blend: bool = False

    # Confidence
    confidence: PreferenceConfidence = PreferenceConfidence.LOW
    data_points: int = 0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d['confidence'] = self.confidence.value
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CabPreference":
        d = d.copy()
        d['confidence'] = PreferenceConfidence(d.get('confidence', 'low'))
        return cls(**d)


@dataclass
class EffectPreference:
    """Learned effect preferences."""

    # Effect type preferences (type -> usage frequency 0-1)
    effect_frequencies: Dict[str, float] = field(default_factory=dict)

    # Chain position preferences
    prefers_pre_effects: bool = True
    prefers_post_effects: bool = True

    # Common effect settings
    typical_delay_time_ms: float = 350.0
    typical_reverb_mix: float = 0.25
    typical_od_drive: float = 0.4

    # Specific pedal preferences
    preferred_overdrives: List[str] = field(default_factory=list)
    preferred_delays: List[str] = field(default_factory=list)
    preferred_reverbs: List[str] = field(default_factory=list)

    # Confidence
    confidence: PreferenceConfidence = PreferenceConfidence.LOW
    data_points: int = 0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d['confidence'] = self.confidence.value
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "EffectPreference":
        d = d.copy()
        d['confidence'] = PreferenceConfidence(d.get('confidence', 'low'))
        return cls(**d)


@dataclass
class GenreAffinity:
    """User's genre/style affinities."""

    # Genre usage frequency (genre -> frequency 0-1)
    genre_frequencies: Dict[str, float] = field(default_factory=dict)

    # Primary genres (top 3)
    primary_genres: List[str] = field(default_factory=list)

    # Production era preference
    prefers_vintage: bool = False
    prefers_modern: bool = False

    # Confidence
    confidence: PreferenceConfidence = PreferenceConfidence.LOW
    data_points: int = 0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d['confidence'] = self.confidence.value
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "GenreAffinity":
        d = d.copy()
        d['confidence'] = PreferenceConfidence(d.get('confidence', 'low'))
        return cls(**d)


@dataclass
class EquipmentBias:
    """User's equipment and platform preferences."""

    # Preferred platforms
    preferred_platforms: List[str] = field(default_factory=list)  # ["helix", "axe_fx"]

    # Price sensitivity (0 = no concern, 1 = very sensitive)
    price_sensitivity: float = 0.5

    # Complexity preference (0 = simple, 1 = complex)
    complexity_preference: float = 0.5

    # Brand affinities (brand -> affinity 0-1)
    brand_affinities: Dict[str, float] = field(default_factory=dict)

    # Confidence
    confidence: PreferenceConfidence = PreferenceConfidence.LOW
    data_points: int = 0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d['confidence'] = self.confidence.value
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "EquipmentBias":
        d = d.copy()
        d['confidence'] = PreferenceConfidence(d.get('confidence', 'low'))
        return cls(**d)


@dataclass
class UserPreferences:
    """Complete user preference profile."""

    # User identifier (local only)
    user_id: str = "default"

    # Individual preference categories
    amp: AmpPreference = field(default_factory=AmpPreference)
    cab: CabPreference = field(default_factory=CabPreference)
    effects: EffectPreference = field(default_factory=EffectPreference)
    genre: GenreAffinity = field(default_factory=GenreAffinity)
    equipment: EquipmentBias = field(default_factory=EquipmentBias)

    # Metadata
    created_at: str = ""
    updated_at: str = ""
    total_sessions: int = 0
    total_analyses: int = 0

    # Privacy settings
    tracking_enabled: bool = True
    cloud_sync_enabled: bool = False

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        if not self.updated_at:
            self.updated_at = self.created_at

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "user_id": self.user_id,
            "amp": self.amp.to_dict(),
            "cab": self.cab.to_dict(),
            "effects": self.effects.to_dict(),
            "genre": self.genre.to_dict(),
            "equipment": self.equipment.to_dict(),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "total_sessions": self.total_sessions,
            "total_analyses": self.total_analyses,
            "tracking_enabled": self.tracking_enabled,
            "cloud_sync_enabled": self.cloud_sync_enabled,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "UserPreferences":
        """Create from dictionary."""
        return cls(
            user_id=d.get("user_id", "default"),
            amp=AmpPreference.from_dict(d.get("amp", {})),
            cab=CabPreference.from_dict(d.get("cab", {})),
            effects=EffectPreference.from_dict(d.get("effects", {})),
            genre=GenreAffinity.from_dict(d.get("genre", {})),
            equipment=EquipmentBias.from_dict(d.get("equipment", {})),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
            total_sessions=d.get("total_sessions", 0),
            total_analyses=d.get("total_analyses", 0),
            tracking_enabled=d.get("tracking_enabled", True),
            cloud_sync_enabled=d.get("cloud_sync_enabled", False),
        )

    def to_json(self) -> str:
        """Serialize to JSON."""
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_json(cls, json_str: str) -> "UserPreferences":
        """Deserialize from JSON."""
        return cls.from_dict(json.loads(json_str))

    def get_overall_confidence(self) -> PreferenceConfidence:
        """Get overall confidence across all preferences."""
        confidences = [
            self.amp.confidence,
            self.cab.confidence,
            self.effects.confidence,
            self.genre.confidence,
            self.equipment.confidence,
        ]

        # Count confidence levels
        high_count = sum(1 for c in confidences if c in (PreferenceConfidence.HIGH, PreferenceConfidence.USER_SET))
        medium_count = sum(1 for c in confidences if c == PreferenceConfidence.MEDIUM)

        if high_count >= 3:
            return PreferenceConfidence.HIGH
        elif high_count >= 1 or medium_count >= 3:
            return PreferenceConfidence.MEDIUM
        else:
            return PreferenceConfidence.LOW

    def get_total_data_points(self) -> int:
        """Get total data points across all preferences."""
        return (
            self.amp.data_points +
            self.cab.data_points +
            self.effects.data_points +
            self.genre.data_points +
            self.equipment.data_points
        )

    def merge_with(self, other: "UserPreferences") -> "UserPreferences":
        """Merge with another preferences object (for combining profiles)."""
        merged = UserPreferences(user_id=self.user_id)

        # Merge amp preferences - keep higher confidence
        if other.amp.data_points > self.amp.data_points:
            merged.amp = other.amp
        else:
            merged.amp = self.amp

        # Similar for other categories
        if other.cab.data_points > self.cab.data_points:
            merged.cab = other.cab
        else:
            merged.cab = self.cab

        if other.effects.data_points > self.effects.data_points:
            merged.effects = other.effects
        else:
            merged.effects = self.effects

        if other.genre.data_points > self.genre.data_points:
            merged.genre = other.genre
        else:
            merged.genre = self.genre

        if other.equipment.data_points > self.equipment.data_points:
            merged.equipment = other.equipment
        else:
            merged.equipment = self.equipment

        # Update metadata
        merged.total_sessions = self.total_sessions + other.total_sessions
        merged.total_analyses = self.total_analyses + other.total_analyses
        merged.updated_at = datetime.now().isoformat()

        return merged


@dataclass
class PreferenceSummary:
    """Human-readable summary of user preferences."""

    # One-line descriptions
    amp_summary: str = ""
    cab_summary: str = ""
    effects_summary: str = ""
    genre_summary: str = ""

    # Overall profile description
    profile_description: str = ""

    # Confidence indicator
    confidence_level: str = ""

    @classmethod
    def from_preferences(cls, prefs: UserPreferences) -> "PreferenceSummary":
        """Generate summary from preferences."""
        summary = cls()

        # Amp summary
        if prefs.amp.preferred_families:
            families = ", ".join(prefs.amp.preferred_families[:3])
            gain_desc = "high-gain" if prefs.amp.prefers_high_gain else "moderate gain"
            summary.amp_summary = f"Prefers {families} with {gain_desc}"
        else:
            summary.amp_summary = "No strong amp preference yet"

        # Cab summary
        if prefs.cab.preferred_configs:
            configs = ", ".join(prefs.cab.preferred_configs[:2])
            summary.cab_summary = f"Prefers {configs} cabinets"
        else:
            summary.cab_summary = "No strong cabinet preference yet"

        # Effects summary
        if prefs.effects.effect_frequencies:
            top_effects = sorted(
                prefs.effects.effect_frequencies.items(),
                key=lambda x: x[1],
                reverse=True,
            )[:3]
            effects_str = ", ".join(e[0] for e in top_effects)
            summary.effects_summary = f"Commonly uses {effects_str}"
        else:
            summary.effects_summary = "No strong effect preferences yet"

        # Genre summary
        if prefs.genre.primary_genres:
            genres = ", ".join(prefs.genre.primary_genres[:2])
            summary.genre_summary = f"Primarily plays {genres}"
        else:
            summary.genre_summary = "No genre preference detected yet"

        # Overall profile
        confidence = prefs.get_overall_confidence()
        if confidence == PreferenceConfidence.HIGH:
            summary.profile_description = "Well-established preference profile"
            summary.confidence_level = "High confidence"
        elif confidence == PreferenceConfidence.MEDIUM:
            summary.profile_description = "Developing preference profile"
            summary.confidence_level = "Medium confidence"
        else:
            summary.profile_description = "Limited preference data"
            summary.confidence_level = "Low confidence"

        return summary


def get_confidence_from_count(count: int) -> PreferenceConfidence:
    """Determine confidence level from data point count."""
    if count >= 20:
        return PreferenceConfidence.HIGH
    elif count >= 5:
        return PreferenceConfidence.MEDIUM
    else:
        return PreferenceConfidence.LOW
