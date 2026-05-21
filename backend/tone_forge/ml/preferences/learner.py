"""Preference learning from behavior data.

Analyzes tracked behavior to learn user preferences:
- Amp family preferences from analysis and selection patterns
- Effect chain patterns from translation and export data
- Genre affinities from detected genres
- Parameter tendencies from edit patterns

Uses statistical aggregation with optional ML enhancement.
"""
from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

from .models import (
    UserPreferences,
    AmpPreference,
    CabPreference,
    EffectPreference,
    GenreAffinity,
    EquipmentBias,
    PreferenceConfidence,
    get_confidence_from_count,
)
from .tracker import BehaviorTracker, EventType, get_tracker

logger = logging.getLogger(__name__)


@dataclass
class LearningConfig:
    """Configuration for preference learning."""

    # Minimum events needed for confidence
    min_events_low: int = 3
    min_events_medium: int = 10
    min_events_high: int = 25

    # Weighting for recency
    recency_weight: float = 0.7  # Recent events weighted more

    # Decay factor for old data
    decay_days: int = 90  # Data older than this is weighted less

    # Learning rates
    amp_learning_rate: float = 0.3
    effect_learning_rate: float = 0.25
    genre_learning_rate: float = 0.2


class PreferenceLearner:
    """Learns user preferences from behavior data."""

    def __init__(
        self,
        tracker: Optional[BehaviorTracker] = None,
        config: Optional[LearningConfig] = None,
    ):
        """Initialize the learner.

        Args:
            tracker: BehaviorTracker instance
            config: Learning configuration
        """
        self.tracker = tracker or get_tracker()
        self.config = config or LearningConfig()

    def learn_preferences(
        self,
        existing: Optional[UserPreferences] = None,
    ) -> UserPreferences:
        """Learn preferences from all tracked behavior.

        Args:
            existing: Existing preferences to update

        Returns:
            Updated UserPreferences
        """
        prefs = existing or UserPreferences()

        # Learn each category
        prefs.amp = self._learn_amp_preferences(prefs.amp)
        prefs.cab = self._learn_cab_preferences(prefs.cab)
        prefs.effects = self._learn_effect_preferences(prefs.effects)
        prefs.genre = self._learn_genre_affinities(prefs.genre)
        prefs.equipment = self._learn_equipment_bias(prefs.equipment)

        # Update metadata
        prefs.updated_at = datetime.now().isoformat()

        stats = self.tracker.get_aggregated_stats()
        prefs.total_sessions = stats.get('total_sessions', 0)
        prefs.total_analyses = stats.get('event_counts', {}).get('analysis', 0)

        return prefs

    def _learn_amp_preferences(
        self,
        existing: AmpPreference,
    ) -> AmpPreference:
        """Learn amp preferences from behavior.

        Args:
            existing: Existing preferences

        Returns:
            Updated AmpPreference
        """
        # Get relevant events
        analysis_events = self.tracker.get_events(EventType.ANALYSIS, limit=500)
        selection_events = self.tracker.get_events(EventType.BLOCK_SELECTION, limit=500)
        edit_events = self.tracker.get_events(EventType.PARAMETER_EDIT, limit=500)

        # Count amp families from analyses
        family_counts = Counter()
        gain_values = []

        for event in analysis_events:
            data = event.data
            if data.get('amp_family'):
                family_counts[data['amp_family']] += 1
            if data.get('gain') is not None:
                gain_values.append(data['gain'])

        # Count selected amp blocks
        for event in selection_events:
            data = event.data
            if data.get('slot') == 'amp' and data.get('block_family'):
                family_counts[data['block_family']] += 2  # Weight selections higher

        # Learn preferred families
        total_family_count = sum(family_counts.values())
        if total_family_count > 0:
            preferred = [
                family for family, _ in family_counts.most_common(5)
            ]
            existing.preferred_families = preferred

        # Learn gain tendencies
        if gain_values:
            avg_gain = sum(gain_values) / len(gain_values)
            min_gain = min(gain_values)
            max_gain = max(gain_values)

            existing.typical_gain_low = max(0.1, min_gain)
            existing.typical_gain_high = min(0.95, max_gain)
            existing.prefers_high_gain = avg_gain > 0.6

        # Learn EQ tendencies from edits
        eq_edits = defaultdict(list)
        for event in edit_events:
            data = event.data
            if data.get('slot') == 'amp':
                param = data.get('parameter', '').lower()
                new_val = data.get('new_value')
                if isinstance(new_val, (int, float)):
                    if 'bass' in param:
                        eq_edits['bass'].append(new_val)
                    elif 'mid' in param:
                        eq_edits['mid'].append(new_val)
                    elif 'treble' in param:
                        eq_edits['treble'].append(new_val)
                    elif 'presence' in param:
                        eq_edits['presence'].append(new_val)

        if eq_edits.get('bass'):
            existing.bass_tendency = sum(eq_edits['bass']) / len(eq_edits['bass'])
        if eq_edits.get('mid'):
            existing.mid_tendency = sum(eq_edits['mid']) / len(eq_edits['mid'])
        if eq_edits.get('treble'):
            existing.treble_tendency = sum(eq_edits['treble']) / len(eq_edits['treble'])
        if eq_edits.get('presence'):
            existing.presence_tendency = sum(eq_edits['presence']) / len(eq_edits['presence'])

        # Update confidence
        existing.data_points = len(analysis_events) + len(selection_events)
        existing.confidence = get_confidence_from_count(existing.data_points)

        return existing

    def _learn_cab_preferences(
        self,
        existing: CabPreference,
    ) -> CabPreference:
        """Learn cabinet preferences from behavior.

        Args:
            existing: Existing preferences

        Returns:
            Updated CabPreference
        """
        analysis_events = self.tracker.get_events(EventType.ANALYSIS, limit=500)
        selection_events = self.tracker.get_events(EventType.BLOCK_SELECTION, limit=500)

        # Count cab configurations
        config_counts = Counter()
        speaker_counts = Counter()

        for event in analysis_events:
            data = event.data
            if data.get('cab_config'):
                config_counts[data['cab_config']] += 1

        for event in selection_events:
            data = event.data
            if data.get('slot') == 'cab':
                family = data.get('block_family', '')
                # Parse config from family name
                if '4x12' in family:
                    config_counts['4x12'] += 2
                elif '2x12' in family:
                    config_counts['2x12'] += 2
                elif '1x12' in family:
                    config_counts['1x12'] += 2

                # Parse speaker type
                if 'v30' in family.lower():
                    speaker_counts['v30'] += 2
                elif 'greenback' in family.lower():
                    speaker_counts['greenback'] += 2

        # Update preferences
        if config_counts:
            existing.preferred_configs = [
                config for config, _ in config_counts.most_common(3)
            ]

        if speaker_counts:
            existing.preferred_speakers = [
                speaker for speaker, _ in speaker_counts.most_common(3)
            ]

        # Update confidence
        existing.data_points = len(analysis_events) + len(selection_events)
        existing.confidence = get_confidence_from_count(existing.data_points)

        return existing

    def _learn_effect_preferences(
        self,
        existing: EffectPreference,
    ) -> EffectPreference:
        """Learn effect preferences from behavior.

        Args:
            existing: Existing preferences

        Returns:
            Updated EffectPreference
        """
        analysis_events = self.tracker.get_events(EventType.ANALYSIS, limit=500)
        selection_events = self.tracker.get_events(EventType.BLOCK_SELECTION, limit=500)
        edit_events = self.tracker.get_events(EventType.PARAMETER_EDIT, limit=500)

        # Count effect types
        effect_counts = Counter()
        total_effects = 0

        for event in analysis_events:
            data = event.data
            effect_types = data.get('effect_types', [])
            for eff_type in effect_types:
                effect_counts[eff_type] += 1
                total_effects += 1

        for event in selection_events:
            data = event.data
            slot = data.get('slot', '')
            if slot.startswith('effect') or slot in ('overdrive', 'delay', 'reverb'):
                family = data.get('block_family', '')
                if family:
                    effect_counts[family] += 2
                    total_effects += 2

        # Calculate frequencies
        if total_effects > 0:
            for eff_type, count in effect_counts.items():
                existing.effect_frequencies[eff_type] = count / total_effects

        # Learn specific pedal preferences
        overdrive_counts = Counter()
        delay_counts = Counter()
        reverb_counts = Counter()

        for event in selection_events:
            data = event.data
            family = data.get('block_family', '')
            if 'overdrive' in family or 'ts' in family.lower() or 'klon' in family.lower():
                overdrive_counts[family] += 1
            elif 'delay' in family:
                delay_counts[family] += 1
            elif 'reverb' in family:
                reverb_counts[family] += 1

        if overdrive_counts:
            existing.preferred_overdrives = [
                od for od, _ in overdrive_counts.most_common(3)
            ]
        if delay_counts:
            existing.preferred_delays = [
                d for d, _ in delay_counts.most_common(3)
            ]
        if reverb_counts:
            existing.preferred_reverbs = [
                r for r, _ in reverb_counts.most_common(3)
            ]

        # Learn typical effect settings from edits
        delay_times = []
        reverb_mixes = []
        od_drives = []

        for event in edit_events:
            data = event.data
            param = data.get('parameter', '').lower()
            new_val = data.get('new_value')

            if isinstance(new_val, (int, float)):
                if 'delay' in data.get('slot', '').lower() and 'time' in param:
                    delay_times.append(new_val)
                elif 'reverb' in data.get('slot', '').lower() and 'mix' in param:
                    reverb_mixes.append(new_val)
                elif 'drive' in param or 'gain' in param:
                    slot = data.get('slot', '')
                    if 'overdrive' in slot.lower() or 'effect' in slot.lower():
                        od_drives.append(new_val)

        if delay_times:
            existing.typical_delay_time_ms = sum(delay_times) / len(delay_times)
        if reverb_mixes:
            existing.typical_reverb_mix = sum(reverb_mixes) / len(reverb_mixes)
        if od_drives:
            existing.typical_od_drive = sum(od_drives) / len(od_drives)

        # Update confidence
        existing.data_points = len(analysis_events) + len(selection_events)
        existing.confidence = get_confidence_from_count(existing.data_points)

        return existing

    def _learn_genre_affinities(
        self,
        existing: GenreAffinity,
    ) -> GenreAffinity:
        """Learn genre affinities from behavior.

        Args:
            existing: Existing preferences

        Returns:
            Updated GenreAffinity
        """
        genre_events = self.tracker.get_events(EventType.GENRE_DETECTED, limit=500)
        archetype_events = self.tracker.get_events(EventType.ARCHETYPE_USED, limit=500)

        # Also check all events for genre field
        all_events = self.tracker.get_events(limit=1000)

        genre_counts = Counter()

        for event in genre_events:
            genre = event.genre
            if genre:
                genre_counts[genre] += 1

        for event in archetype_events:
            genre = event.genre
            if genre:
                genre_counts[genre] += 1

        for event in all_events:
            genre = event.genre
            if genre:
                genre_counts[genre] += 0.5  # Lower weight for incidental mentions

        # Calculate frequencies
        total_genre_events = sum(genre_counts.values())
        if total_genre_events > 0:
            for genre, count in genre_counts.items():
                existing.genre_frequencies[genre] = count / total_genre_events

            # Set primary genres
            existing.primary_genres = [
                genre for genre, _ in genre_counts.most_common(3)
            ]

        # Determine era preference
        vintage_genres = {'blues', 'classic_rock', 'jazz', 'country'}
        modern_genres = {'metal', 'djent', 'modern_rock', 'progressive'}

        vintage_count = sum(genre_counts.get(g, 0) for g in vintage_genres)
        modern_count = sum(genre_counts.get(g, 0) for g in modern_genres)

        if vintage_count > modern_count * 1.5:
            existing.prefers_vintage = True
            existing.prefers_modern = False
        elif modern_count > vintage_count * 1.5:
            existing.prefers_modern = True
            existing.prefers_vintage = False

        # Update confidence
        existing.data_points = len(genre_events) + len(archetype_events)
        existing.confidence = get_confidence_from_count(existing.data_points)

        return existing

    def _learn_equipment_bias(
        self,
        existing: EquipmentBias,
    ) -> EquipmentBias:
        """Learn equipment preferences from behavior.

        Args:
            existing: Existing preferences

        Returns:
            Updated EquipmentBias
        """
        translation_events = self.tracker.get_events(EventType.TRANSLATION, limit=500)
        export_events = self.tracker.get_events(EventType.EXPORT, limit=500)
        plugin_events = self.tracker.get_events(EventType.PLUGIN_USED, limit=500)

        # Count platforms
        platform_counts = Counter()

        for event in translation_events:
            platform = event.platform
            if platform:
                platform_counts[platform] += 1

        for event in export_events:
            platform = event.platform
            if platform:
                platform_counts[platform] += 2  # Weight exports higher

        if platform_counts:
            existing.preferred_platforms = [
                p for p, _ in platform_counts.most_common(3)
            ]

        # Learn brand affinities from plugin usage
        brand_counts = Counter()
        for event in plugin_events:
            data = event.data
            plugin_name = data.get('plugin_name', '')
            # Extract brand from common naming patterns
            if 'neural' in plugin_name.lower():
                brand_counts['Neural DSP'] += 1
            elif 'line 6' in plugin_name.lower() or 'helix' in plugin_name.lower():
                brand_counts['Line 6'] += 1
            elif 'fractal' in plugin_name.lower() or 'axe' in plugin_name.lower():
                brand_counts['Fractal Audio'] += 1

        total_brand = sum(brand_counts.values())
        if total_brand > 0:
            for brand, count in brand_counts.items():
                existing.brand_affinities[brand] = count / total_brand

        # Learn complexity preference from block counts
        block_counts = []
        for event in translation_events:
            data = event.data
            count = data.get('block_count', 0)
            if count > 0:
                block_counts.append(count)

        if block_counts:
            avg_blocks = sum(block_counts) / len(block_counts)
            # Normalize: 3 blocks = 0.3, 10 blocks = 1.0
            existing.complexity_preference = min(1.0, avg_blocks / 10.0)

        # Update confidence
        existing.data_points = len(translation_events) + len(export_events)
        existing.confidence = get_confidence_from_count(existing.data_points)

        return existing

    def get_preference_insights(
        self,
        prefs: UserPreferences,
    ) -> List[str]:
        """Generate human-readable insights from preferences.

        Args:
            prefs: User preferences

        Returns:
            List of insight strings
        """
        insights = []

        # Amp insights
        if prefs.amp.preferred_families:
            top_amp = prefs.amp.preferred_families[0]
            gain_desc = "high-gain" if prefs.amp.prefers_high_gain else "moderate"
            insights.append(
                f"You tend to prefer {top_amp} style amps with {gain_desc} tones"
            )

        # Genre insights
        if prefs.genre.primary_genres:
            genres = ", ".join(prefs.genre.primary_genres[:2])
            insights.append(f"Your music often falls into {genres} categories")

        # Effect insights
        if prefs.effects.effect_frequencies:
            top_effect = max(
                prefs.effects.effect_frequencies.items(),
                key=lambda x: x[1],
            )[0]
            insights.append(f"You frequently use {top_effect} in your signal chains")

        # EQ insights
        if prefs.amp.mid_tendency != 0.5:
            if prefs.amp.mid_tendency > 0.55:
                insights.append("You tend to boost mids for more presence")
            elif prefs.amp.mid_tendency < 0.45:
                insights.append("You tend to scoop mids for a more modern sound")

        # Platform insights
        if prefs.equipment.preferred_platforms:
            platform = prefs.equipment.preferred_platforms[0]
            insights.append(f"Your primary platform is {platform}")

        return insights


# Singleton instance
_learner: Optional[PreferenceLearner] = None


def get_learner() -> PreferenceLearner:
    """Get the singleton learner instance.

    Returns:
        PreferenceLearner instance
    """
    global _learner
    if _learner is None:
        _learner = PreferenceLearner()
    return _learner


def learn_preferences(
    existing: Optional[UserPreferences] = None,
) -> UserPreferences:
    """Convenience function to learn preferences.

    Args:
        existing: Existing preferences to update

    Returns:
        Updated UserPreferences
    """
    return get_learner().learn_preferences(existing)
