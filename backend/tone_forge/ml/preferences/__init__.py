"""User preference learning and personalization.

Provides personalized tone reconstruction based on learned preferences:

- models: Data structures for preferences
- tracker: Behavior event tracking
- learner: Preference learning from behavior
- privacy: Data export, deletion, and control

Privacy-first design:
- All data stored locally in SQLite
- Audio never stored, only hashes and metadata
- User can view, export, and delete all data
- Tracking can be disabled
- Cloud sync is opt-in only
"""
from __future__ import annotations

from .models import (
    UserPreferences,
    AmpPreference,
    CabPreference,
    EffectPreference,
    GenreAffinity,
    EquipmentBias,
    PreferenceConfidence,
    PreferenceSummary,
    get_confidence_from_count,
)
from .tracker import (
    BehaviorTracker,
    BehaviorEvent,
    EventType,
    get_tracker,
    track_event,
)
from .learner import (
    PreferenceLearner,
    LearningConfig,
    get_learner,
    learn_preferences,
)
from .privacy import (
    PrivacyManager,
    get_privacy_manager,
    get_data_summary,
    export_all_data,
    delete_all_data,
    set_tracking_enabled,
    load_preferences,
    save_preferences,
)

__all__ = [
    # Models
    "UserPreferences",
    "AmpPreference",
    "CabPreference",
    "EffectPreference",
    "GenreAffinity",
    "EquipmentBias",
    "PreferenceConfidence",
    "PreferenceSummary",
    "get_confidence_from_count",
    # Tracker
    "BehaviorTracker",
    "BehaviorEvent",
    "EventType",
    "get_tracker",
    "track_event",
    # Learner
    "PreferenceLearner",
    "LearningConfig",
    "get_learner",
    "learn_preferences",
    # Privacy
    "PrivacyManager",
    "get_privacy_manager",
    "get_data_summary",
    "export_all_data",
    "delete_all_data",
    "set_tracking_enabled",
    "load_preferences",
    "save_preferences",
]


def get_personalized_context(
    user_id: str = "default",
) -> dict:
    """Get personalized context for translation.

    This is the main integration point for using preferences
    in the translation pipeline.

    Args:
        user_id: User identifier

    Returns:
        Dictionary with personalization context
    """
    # Load preferences
    prefs = load_preferences()

    # Learn any new preferences from recent behavior
    learner = get_learner()
    prefs = learner.learn_preferences(prefs)

    # Save updated preferences
    save_preferences(prefs)

    # Build context for translator
    context = {
        "user_prefs": {
            # Amp preferences
            "preferred_amp_families": prefs.amp.preferred_families,
            "prefers_high_gain": prefs.amp.prefers_high_gain,
            "typical_gain_range": (
                prefs.amp.typical_gain_low,
                prefs.amp.typical_gain_high,
            ),

            # EQ tendencies
            "eq_tendencies": {
                "bass": prefs.amp.bass_tendency,
                "mid": prefs.amp.mid_tendency,
                "treble": prefs.amp.treble_tendency,
                "presence": prefs.amp.presence_tendency,
            },

            # Cab preferences
            "preferred_cab_configs": prefs.cab.preferred_configs,
            "preferred_speakers": prefs.cab.preferred_speakers,

            # Effect preferences
            "effect_frequencies": prefs.effects.effect_frequencies,
            "typical_delay_time_ms": prefs.effects.typical_delay_time_ms,
            "typical_reverb_mix": prefs.effects.typical_reverb_mix,

            # Genre context
            "primary_genres": prefs.genre.primary_genres,
            "prefers_vintage": prefs.genre.prefers_vintage,
            "prefers_modern": prefs.genre.prefers_modern,

            # Equipment
            "preferred_platforms": prefs.equipment.preferred_platforms,
            "brand_affinities": prefs.equipment.brand_affinities,
        },
        "confidence": prefs.get_overall_confidence().value,
        "data_points": prefs.get_total_data_points(),
    }

    return context


def apply_preferences_to_ranking(
    blocks: list,
    prefs: UserPreferences,
    slot: str,
) -> list:
    """Apply preference-based boosting to block rankings.

    Args:
        blocks: List of ranked blocks
        prefs: User preferences
        slot: Block slot (amp, cab, effects)

    Returns:
        Re-ranked blocks with preference boost applied
    """
    if not blocks:
        return blocks

    # Calculate preference boost for each block
    boosted = []
    for block in blocks:
        boost = 0.0
        block_id = block.get('block_id', '')
        family = block.get('family', '')

        if slot == 'amp':
            # Boost preferred amp families
            if family in prefs.amp.preferred_families:
                rank = prefs.amp.preferred_families.index(family)
                boost += 0.2 * (1 - rank * 0.2)  # Top family gets +0.2, decreasing

        elif slot == 'cab':
            # Boost preferred cab configs
            for config in prefs.cab.preferred_configs:
                if config in family:
                    boost += 0.15

        elif slot.startswith('effect'):
            # Boost frequently used effects
            for effect_type, freq in prefs.effects.effect_frequencies.items():
                if effect_type in family:
                    boost += 0.1 * freq

        # Apply boost to existing score
        current_score = block.get('score', 0.5)
        block['score'] = min(1.0, current_score + boost)
        block['preference_boost'] = boost
        boosted.append(block)

    # Re-sort by boosted score
    boosted.sort(key=lambda x: x['score'], reverse=True)

    return boosted
