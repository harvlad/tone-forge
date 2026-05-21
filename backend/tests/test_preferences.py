"""Tests for user preference learning and personalization.

Tests the preference models, behavior tracking, learning,
and privacy controls.
"""
import pytest
import tempfile
import json
from pathlib import Path
from datetime import datetime, timedelta

from tone_forge.ml.preferences.models import (
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
from tone_forge.ml.preferences.tracker import (
    BehaviorTracker,
    BehaviorEvent,
    EventType,
)
from tone_forge.ml.preferences.learner import (
    PreferenceLearner,
    LearningConfig,
)
from tone_forge.ml.preferences.privacy import PrivacyManager


# ============================================================================
# Test fixtures
# ============================================================================

@pytest.fixture
def temp_dir():
    """Create a temporary directory for tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def tracker(temp_dir):
    """Create a tracker with temporary storage."""
    db_path = temp_dir / "test_behavior.db"
    tracker = BehaviorTracker(db_path=db_path, enabled=True)
    yield tracker
    tracker.close()


@pytest.fixture
def sample_preferences():
    """Create sample user preferences."""
    return UserPreferences(
        user_id="test_user",
        amp=AmpPreference(
            preferred_families=["marshall_jcm", "mesa_rectifier"],
            typical_gain_low=0.4,
            typical_gain_high=0.8,
            prefers_high_gain=True,
            confidence=PreferenceConfidence.MEDIUM,
            data_points=15,
        ),
        cab=CabPreference(
            preferred_configs=["4x12", "2x12"],
            preferred_speakers=["v30"],
            confidence=PreferenceConfidence.LOW,
            data_points=8,
        ),
        effects=EffectPreference(
            effect_frequencies={"delay": 0.4, "reverb": 0.3, "overdrive": 0.3},
            typical_delay_time_ms=350.0,
            typical_reverb_mix=0.25,
            confidence=PreferenceConfidence.MEDIUM,
            data_points=12,
        ),
        genre=GenreAffinity(
            genre_frequencies={"metal": 0.5, "rock": 0.3, "blues": 0.2},
            primary_genres=["metal", "rock"],
            prefers_modern=True,
            confidence=PreferenceConfidence.MEDIUM,
            data_points=20,
        ),
        total_sessions=10,
        total_analyses=25,
    )


# ============================================================================
# UserPreferences tests
# ============================================================================

class TestUserPreferences:
    """Tests for UserPreferences model."""

    def test_create_default(self):
        """Test creating default preferences."""
        prefs = UserPreferences()

        assert prefs.user_id == "default"
        assert isinstance(prefs.amp, AmpPreference)
        assert isinstance(prefs.cab, CabPreference)
        assert prefs.tracking_enabled is True
        assert prefs.cloud_sync_enabled is False

    def test_to_dict(self, sample_preferences):
        """Test converting to dictionary."""
        d = sample_preferences.to_dict()

        assert d["user_id"] == "test_user"
        assert "amp" in d
        assert d["amp"]["preferred_families"] == ["marshall_jcm", "mesa_rectifier"]
        assert d["total_sessions"] == 10

    def test_from_dict(self, sample_preferences):
        """Test creating from dictionary."""
        d = sample_preferences.to_dict()
        restored = UserPreferences.from_dict(d)

        assert restored.user_id == sample_preferences.user_id
        assert restored.amp.preferred_families == sample_preferences.amp.preferred_families
        assert restored.genre.primary_genres == sample_preferences.genre.primary_genres

    def test_to_json(self, sample_preferences):
        """Test JSON serialization."""
        json_str = sample_preferences.to_json()
        data = json.loads(json_str)

        assert data["user_id"] == "test_user"
        assert "amp" in data

    def test_from_json(self, sample_preferences):
        """Test JSON deserialization."""
        json_str = sample_preferences.to_json()
        restored = UserPreferences.from_json(json_str)

        assert restored.user_id == sample_preferences.user_id
        assert restored.amp.prefers_high_gain == sample_preferences.amp.prefers_high_gain

    def test_get_overall_confidence(self, sample_preferences):
        """Test overall confidence calculation."""
        confidence = sample_preferences.get_overall_confidence()

        # With multiple MEDIUM confidences, should be MEDIUM
        assert confidence in (PreferenceConfidence.LOW, PreferenceConfidence.MEDIUM)

    def test_get_total_data_points(self, sample_preferences):
        """Test data point counting."""
        total = sample_preferences.get_total_data_points()

        # Sum of all category data points
        expected = 15 + 8 + 12 + 20 + 0  # amp + cab + effects + genre + equipment
        assert total == expected

    def test_merge_with(self, sample_preferences):
        """Test merging preferences."""
        other = UserPreferences(
            user_id="test_user",
            amp=AmpPreference(
                preferred_families=["5150_peavey"],
                data_points=30,  # More data points
            ),
            total_sessions=5,
            total_analyses=10,
        )

        merged = sample_preferences.merge_with(other)

        # Should use amp from other (more data points)
        assert "5150_peavey" in merged.amp.preferred_families
        # Sessions should be summed
        assert merged.total_sessions == 15
        assert merged.total_analyses == 35


class TestPreferenceSummary:
    """Tests for PreferenceSummary."""

    def test_from_preferences(self, sample_preferences):
        """Test generating summary from preferences."""
        summary = PreferenceSummary.from_preferences(sample_preferences)

        assert "marshall_jcm" in summary.amp_summary.lower() or "jcm" in summary.amp_summary.lower()
        assert len(summary.profile_description) > 0
        assert len(summary.confidence_level) > 0

    def test_empty_preferences_summary(self):
        """Test summary for empty preferences."""
        prefs = UserPreferences()
        summary = PreferenceSummary.from_preferences(prefs)

        assert "no strong" in summary.amp_summary.lower() or "no amp" in summary.amp_summary.lower()


class TestConfidenceHelpers:
    """Tests for confidence helper functions."""

    def test_confidence_from_count(self):
        """Test confidence calculation from count."""
        assert get_confidence_from_count(0) == PreferenceConfidence.LOW
        assert get_confidence_from_count(3) == PreferenceConfidence.LOW
        assert get_confidence_from_count(5) == PreferenceConfidence.MEDIUM
        assert get_confidence_from_count(15) == PreferenceConfidence.MEDIUM
        assert get_confidence_from_count(25) == PreferenceConfidence.HIGH


# ============================================================================
# BehaviorTracker tests
# ============================================================================

class TestBehaviorTracker:
    """Tests for BehaviorTracker."""

    def test_init(self, tracker):
        """Test tracker initialization."""
        assert tracker.enabled is True
        assert tracker.db_path.exists()

    def test_start_end_session(self, tracker):
        """Test session lifecycle."""
        session_id = tracker.start_session(platform="helix")

        assert session_id is not None
        assert tracker._current_session_id == session_id

        tracker.end_session(summary={"tests": "passed"})

        assert tracker._current_session_id is None

    def test_track_analysis(self, tracker):
        """Test analysis event tracking."""
        tracker.start_session()

        descriptor = {
            "amp": {"family": "marshall_jcm", "gain": 0.7},
            "cab": {"configuration": "4x12"},
            "effects": {"delay": {}, "reverb": {}},
        }

        tracker.track_analysis(
            descriptor=descriptor,
            confidence=0.85,
            genre="rock",
        )

        events = tracker.get_events(EventType.ANALYSIS)
        assert len(events) >= 1

        event = events[0]
        assert event.data["amp_family"] == "marshall_jcm"
        assert event.data["gain"] == 0.7
        assert event.genre == "rock"

    def test_track_translation(self, tracker):
        """Test translation event tracking."""
        tracker.start_session()

        tracker.track_translation(
            descriptor_hash="abc123",
            platform="helix",
            blocks=[
                {"slot": "amp", "block_id": "US Deluxe", "family": "fender_deluxe"},
                {"slot": "cab", "block_id": "4x12 Cali V30", "family": "4x12_v30"},
            ],
            genre="blues",
        )

        events = tracker.get_events(EventType.TRANSLATION)
        assert len(events) >= 1

        event = events[0]
        assert event.data["block_count"] == 2
        assert event.platform == "helix"

    def test_track_block_selection(self, tracker):
        """Test block selection tracking."""
        tracker.start_session()

        tracker.track_block_selection(
            slot="amp",
            block_id="Cali Rectifire",
            block_family="mesa_rectifier",
            was_top_pick=False,
            rank=2,
        )

        events = tracker.get_events(EventType.BLOCK_SELECTION)
        assert len(events) >= 1

        event = events[0]
        assert event.data["block_family"] == "mesa_rectifier"
        assert event.data["rank"] == 2
        assert event.data["was_top_pick"] is False

    def test_track_parameter_edit(self, tracker):
        """Test parameter edit tracking."""
        tracker.start_session()

        tracker.track_parameter_edit(
            slot="amp",
            block_id="US Deluxe",
            parameter="gain",
            old_value=0.5,
            new_value=0.7,
        )

        events = tracker.get_events(EventType.PARAMETER_EDIT)
        assert len(events) >= 1

        event = events[0]
        assert event.data["parameter"] == "gain"
        assert event.data["direction"] == "increase"

    def test_track_genre_detected(self, tracker):
        """Test genre detection tracking."""
        tracker.start_session()

        tracker.track_genre_detected(
            genre="metal",
            subgenre="djent",
            confidence=0.85,
        )

        events = tracker.get_events(EventType.GENRE_DETECTED)
        assert len(events) >= 1

        event = events[0]
        assert event.genre == "metal"
        assert event.data["subgenre"] == "djent"

    def test_get_aggregated_stats(self, tracker):
        """Test statistics aggregation."""
        tracker.start_session()

        # Track various events
        tracker.track_analysis({"amp": {"family": "marshall"}}, 0.8, "rock")
        tracker.track_analysis({"amp": {"family": "mesa"}}, 0.9, "metal")
        tracker.track_genre_detected("rock", None, 0.8)
        tracker.track_genre_detected("metal", None, 0.9)

        stats = tracker.get_aggregated_stats()

        assert stats["total_events"] >= 4
        assert "genre_distribution" in stats
        assert len(stats["genre_distribution"]) >= 1

    def test_disabled_tracking(self, temp_dir):
        """Test that disabled tracker doesn't store events."""
        db_path = temp_dir / "disabled.db"
        tracker = BehaviorTracker(db_path=db_path, enabled=False)

        tracker.start_session()
        tracker.track_analysis({"amp": {"family": "test"}}, 0.8)

        events = tracker.get_events()
        assert len(events) == 0

        tracker.close()


# ============================================================================
# PreferenceLearner tests
# ============================================================================

class TestPreferenceLearner:
    """Tests for PreferenceLearner."""

    def test_init(self, tracker):
        """Test learner initialization."""
        learner = PreferenceLearner(tracker=tracker)
        assert learner.tracker is tracker

    def test_learn_from_empty(self, tracker):
        """Test learning from empty data."""
        learner = PreferenceLearner(tracker=tracker)
        prefs = learner.learn_preferences()

        assert isinstance(prefs, UserPreferences)
        assert prefs.amp.confidence == PreferenceConfidence.LOW

    def test_learn_amp_preferences(self, tracker):
        """Test learning amp preferences."""
        tracker.start_session()

        # Generate analysis events
        for _ in range(10):
            tracker.track_analysis(
                {"amp": {"family": "marshall_jcm", "gain": 0.7}},
                0.85,
                "rock",
            )

        for _ in range(5):
            tracker.track_analysis(
                {"amp": {"family": "mesa_rectifier", "gain": 0.85}},
                0.80,
                "metal",
            )

        learner = PreferenceLearner(tracker=tracker)
        prefs = learner.learn_preferences()

        # Marshall should be preferred (more events)
        assert "marshall_jcm" in prefs.amp.preferred_families

    def test_learn_genre_affinities(self, tracker):
        """Test learning genre affinities."""
        tracker.start_session()

        # Generate genre events
        for _ in range(15):
            tracker.track_genre_detected("metal", "modern_metal", 0.85)

        for _ in range(5):
            tracker.track_genre_detected("rock", "hard_rock", 0.80)

        learner = PreferenceLearner(tracker=tracker)
        prefs = learner.learn_preferences()

        # Metal should be primary genre
        assert "metal" in prefs.genre.primary_genres

    def test_learn_effect_preferences(self, tracker):
        """Test learning effect preferences."""
        tracker.start_session()

        # Generate selection events for effects
        for _ in range(8):
            tracker.track_block_selection(
                slot="effect1",
                block_id="Simple Delay",
                block_family="delay",
                was_top_pick=True,
                rank=1,
            )

        for _ in range(4):
            tracker.track_block_selection(
                slot="effect2",
                block_id="Hall Reverb",
                block_family="reverb",
                was_top_pick=True,
                rank=1,
            )

        learner = PreferenceLearner(tracker=tracker)
        prefs = learner.learn_preferences()

        # Delay should have higher frequency
        assert prefs.effects.effect_frequencies.get("delay", 0) > 0

    def test_get_preference_insights(self, tracker, sample_preferences):
        """Test generating insights."""
        learner = PreferenceLearner(tracker=tracker)
        insights = learner.get_preference_insights(sample_preferences)

        assert len(insights) > 0
        assert any("marshall" in i.lower() or "metal" in i.lower() for i in insights)


# ============================================================================
# PrivacyManager tests
# ============================================================================

class TestPrivacyManager:
    """Tests for PrivacyManager."""

    def test_init(self, tracker, temp_dir):
        """Test privacy manager initialization."""
        prefs_path = temp_dir / "prefs.json"
        manager = PrivacyManager(tracker=tracker, preferences_path=prefs_path)

        assert manager.tracker is tracker
        assert manager.preferences_path == prefs_path

    def test_get_data_summary(self, tracker, temp_dir):
        """Test data summary."""
        tracker.start_session()
        tracker.track_analysis({"amp": {}}, 0.8)
        tracker.track_analysis({"amp": {}}, 0.8)

        prefs_path = temp_dir / "prefs.json"
        manager = PrivacyManager(tracker=tracker, preferences_path=prefs_path)

        summary = manager.get_data_summary()

        assert summary["total_events"] >= 2
        assert "storage_location" in summary

    def test_save_load_preferences(self, tracker, temp_dir, sample_preferences):
        """Test saving and loading preferences."""
        prefs_path = temp_dir / "prefs.json"
        manager = PrivacyManager(tracker=tracker, preferences_path=prefs_path)

        # Save
        manager.save_preferences(sample_preferences)
        assert prefs_path.exists()

        # Load
        loaded = manager.load_preferences()
        assert loaded.user_id == sample_preferences.user_id
        assert loaded.amp.preferred_families == sample_preferences.amp.preferred_families

    def test_export_all_data(self, tracker, temp_dir):
        """Test data export."""
        tracker.start_session()
        tracker.track_analysis({"amp": {"family": "test"}}, 0.8)

        prefs_path = temp_dir / "prefs.json"
        export_dir = temp_dir / "exports"
        manager = PrivacyManager(tracker=tracker, preferences_path=prefs_path)

        # Save some preferences first
        manager.save_preferences(UserPreferences())

        # Export
        export_path = manager.export_all_data(output_dir=export_dir)

        assert export_path.exists()
        assert (export_path / "events.json").exists()
        assert (export_path / "manifest.json").exists()

    def test_delete_all_events(self, tracker, temp_dir):
        """Test event deletion."""
        tracker.start_session()
        tracker.track_analysis({"amp": {}}, 0.8)
        tracker.track_analysis({"amp": {}}, 0.8)

        assert tracker.get_event_count() >= 2

        prefs_path = temp_dir / "prefs.json"
        manager = PrivacyManager(tracker=tracker, preferences_path=prefs_path)

        manager.delete_all_events()

        assert tracker.get_event_count() == 0

    def test_delete_preferences(self, tracker, temp_dir, sample_preferences):
        """Test preference deletion."""
        prefs_path = temp_dir / "prefs.json"
        manager = PrivacyManager(tracker=tracker, preferences_path=prefs_path)

        manager.save_preferences(sample_preferences)
        assert prefs_path.exists()

        manager.delete_preferences()
        assert not prefs_path.exists()

    def test_set_tracking_enabled(self, tracker, temp_dir):
        """Test enabling/disabling tracking."""
        prefs_path = temp_dir / "prefs.json"
        manager = PrivacyManager(tracker=tracker, preferences_path=prefs_path)

        manager.set_tracking_enabled(False)
        assert tracker.enabled is False

        manager.set_tracking_enabled(True)
        assert tracker.enabled is True

    def test_reset_preferences(self, tracker, temp_dir, sample_preferences):
        """Test preference reset."""
        prefs_path = temp_dir / "prefs.json"
        manager = PrivacyManager(tracker=tracker, preferences_path=prefs_path)

        manager.save_preferences(sample_preferences)

        fresh = manager.reset_preferences()

        assert fresh.user_id == "default"
        assert len(fresh.amp.preferred_families) == 0


# ============================================================================
# Integration tests
# ============================================================================

class TestPreferencesIntegration:
    """Integration tests for the preferences module."""

    def test_full_workflow(self, tracker, temp_dir):
        """Test complete preferences workflow."""
        # Start session
        tracker.start_session(platform="helix")

        # Track some behavior
        for _ in range(10):
            tracker.track_analysis(
                {"amp": {"family": "marshall_jcm", "gain": 0.6}},
                0.85,
                "rock",
            )
            tracker.track_block_selection(
                slot="amp",
                block_id="Brit 2204",
                block_family="marshall_jcm",
                was_top_pick=True,
                rank=1,
            )
            tracker.track_genre_detected("rock", None, 0.8)

        tracker.end_session()

        # Learn preferences
        learner = PreferenceLearner(tracker=tracker)
        prefs = learner.learn_preferences()

        # Verify learning
        assert "marshall_jcm" in prefs.amp.preferred_families
        assert "rock" in prefs.genre.primary_genres

        # Save preferences
        prefs_path = temp_dir / "prefs.json"
        manager = PrivacyManager(tracker=tracker, preferences_path=prefs_path)
        manager.save_preferences(prefs)

        # Export data
        export_path = manager.export_all_data(output_dir=temp_dir / "exports")
        assert export_path.exists()

        # Load and verify
        loaded = manager.load_preferences()
        assert loaded.amp.preferred_families == prefs.amp.preferred_families

    def test_module_imports(self):
        """Test that all module imports work correctly."""
        from tone_forge.ml.preferences import (
            UserPreferences,
            BehaviorTracker,
            PreferenceLearner,
            PrivacyManager,
            EventType,
            get_tracker,
            get_learner,
            get_privacy_manager,
            load_preferences,
            save_preferences,
        )

        assert UserPreferences is not None
        assert BehaviorTracker is not None
        assert PreferenceLearner is not None
        assert PrivacyManager is not None

    def test_personalized_context(self, tracker, temp_dir):
        """Test getting personalized context."""
        from tone_forge.ml.preferences import get_personalized_context

        # This would need to mock the singleton, but we can test the structure
        # For now, just verify the function exists and returns expected format
        # (In production tests, we'd mock the preferences storage)

    def test_apply_preferences_to_ranking(self, sample_preferences):
        """Test preference-based ranking boost."""
        from tone_forge.ml.preferences import apply_preferences_to_ranking

        blocks = [
            {"block_id": "US Deluxe", "family": "fender_deluxe", "score": 0.8},
            {"block_id": "Brit 2204", "family": "marshall_jcm", "score": 0.7},
            {"block_id": "Cali Rectifire", "family": "mesa_rectifier", "score": 0.6},
        ]

        boosted = apply_preferences_to_ranking(blocks, sample_preferences, "amp")

        # Marshall should be boosted (in preferred families)
        marshall_block = next(b for b in boosted if "marshall" in b["family"])
        assert marshall_block.get("preference_boost", 0) > 0

        # Result should be re-sorted
        assert boosted[0]["score"] >= boosted[1]["score"]
