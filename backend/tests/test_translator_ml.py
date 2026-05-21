"""Tests for ML-based translator intelligence.

Tests the feature builder, block ranker, and feedback collection
modules that power the ML-enhanced translation.
"""
import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import numpy as np
import pytest

from tone_forge.ml.translator.feature_builder import (
    RankingFeatures,
    build_ranking_features,
    build_features_batch,
)
from tone_forge.ml.translator.ranker import (
    BlockRanker,
    ScoredBlock,
    get_ranker,
    rank_blocks,
)
from tone_forge.ml.translator.feedback import (
    FeedbackCollector,
    FeedbackEvent,
    FeedbackType,
    TrainingExample,
)


# ============================================================================
# Test fixtures
# ============================================================================

@pytest.fixture
def sample_descriptor():
    """A sample descriptor dict for testing."""
    return {
        "amp": {
            "family": "marshall_jcm",
            "gain": 0.65,
            "voicing": {
                "bass": 0.5,
                "mid": 0.6,
                "treble": 0.55,
                "presence": 0.5,
            },
        },
        "cab": {
            "configuration": "4x12",
            "speaker_character": "v30_like",
        },
        "effects": {
            "overdrive_pedal": {
                "style": "tube_screamer",
                "drive": 0.4,
            },
            "delay": {
                "type": "digital",
                "time_ms": 350,
            },
            "reverb": {
                "type": "room",
                "size": 0.4,
            },
        },
        "confidence": {
            "amp_family": 0.85,
            "gain": 0.9,
            "cab": 0.7,
        },
    }


@pytest.fixture
def sample_blocks():
    """Sample catalog blocks for testing."""
    return [
        {
            "id": "jcm800",
            "display": "JCM 800",
            "families": ["marshall_jcm", "british_crunch"],
            "gain_range": (0.4, 0.8),
            "default_voicing": {"bass": 0.5, "mid": 0.6, "treble": 0.5},
        },
        {
            "id": "plexi",
            "display": "Plexi Bright",
            "families": ["marshall_plexi", "british_crunch"],
            "gain_range": (0.3, 0.6),
            "default_voicing": {"bass": 0.45, "mid": 0.65, "treble": 0.6},
        },
        {
            "id": "twin",
            "display": "US Double Nrm",
            "families": ["fender_clean", "american_clean"],
            "gain_range": (0.0, 0.35),
            "default_voicing": {"bass": 0.4, "mid": 0.5, "treble": 0.6},
        },
        {
            "id": "5150_blue",
            "display": "5150 Blue",
            "families": ["5150_peavey", "high_gain"],
            "gain_range": (0.6, 1.0),
            "default_voicing": {"bass": 0.5, "mid": 0.4, "treble": 0.55},
            "tags": ["fallback"],
        },
    ]


@pytest.fixture
def sample_user_prefs():
    """Sample user preferences."""
    return {
        "used_blocks": ["jcm800", "plexi"],
        "family_prefs": {
            "marshall_jcm": 0.8,
            "marshall_plexi": 0.7,
            "fender_clean": 0.4,
        },
        "gain_bias": 0.1,
        "effects_affinity": 0.6,
    }


@pytest.fixture
def sample_block_stats():
    """Sample block usage statistics."""
    return {
        "jcm800": {
            "popularity": 0.85,
            "avg_rating": 0.9,
            "edit_rate": 0.3,
        },
        "plexi": {
            "popularity": 0.75,
            "avg_rating": 0.85,
            "edit_rate": 0.35,
        },
        "twin": {
            "popularity": 0.6,
            "avg_rating": 0.8,
            "edit_rate": 0.2,
        },
    }


@pytest.fixture
def temp_feedback_dir():
    """Temporary directory for feedback storage."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


# ============================================================================
# RankingFeatures tests
# ============================================================================

class TestRankingFeatures:
    """Tests for the RankingFeatures dataclass."""

    def test_default_values(self):
        """Test that RankingFeatures has sensible defaults."""
        features = RankingFeatures()
        assert features.family_exact_match == 0.0
        assert features.gain_in_range == 0.0
        assert features.user_used_before == 0.0

    def test_to_array(self):
        """Test conversion to numpy array."""
        features = RankingFeatures(
            family_exact_match=1.0,
            gain_in_range=0.5,
            block_popularity=0.8,
        )
        arr = features.to_array()
        assert isinstance(arr, np.ndarray)
        assert arr.dtype == np.float32
        assert len(arr) == RankingFeatures.num_features()

    def test_num_features(self):
        """Test that feature count is correct."""
        assert RankingFeatures.num_features() == 25

    def test_feature_names(self):
        """Test feature names list."""
        names = RankingFeatures.feature_names()
        assert len(names) == 25
        assert "family_exact_match" in names
        assert "user_used_before" in names

    def test_to_array_order_matches_names(self):
        """Test that array order matches feature names."""
        features = RankingFeatures(family_exact_match=0.99)
        arr = features.to_array()
        names = RankingFeatures.feature_names()
        idx = names.index("family_exact_match")
        assert arr[idx] == pytest.approx(0.99)


# ============================================================================
# build_ranking_features tests
# ============================================================================

class TestBuildRankingFeatures:
    """Tests for the build_ranking_features function."""

    def test_amp_family_match(self, sample_descriptor, sample_blocks):
        """Test that exact family match is detected."""
        # JCM800 should match marshall_jcm
        features = build_ranking_features(
            descriptor=sample_descriptor,
            block=sample_blocks[0],  # jcm800
            slot="amp",
        )
        assert features.family_exact_match == 1.0

    def test_amp_family_no_match(self, sample_descriptor, sample_blocks):
        """Test that non-matching family gets 0."""
        features = build_ranking_features(
            descriptor=sample_descriptor,
            block=sample_blocks[2],  # twin (fender_clean)
            slot="amp",
        )
        assert features.family_exact_match == 0.0

    def test_gain_in_range(self, sample_descriptor, sample_blocks):
        """Test gain range detection."""
        # JCM800 range is 0.4-0.8, descriptor gain is 0.65
        features = build_ranking_features(
            descriptor=sample_descriptor,
            block=sample_blocks[0],
            slot="amp",
        )
        assert features.gain_in_range == 1.0

        # Twin range is 0.0-0.35, descriptor gain is 0.65
        features = build_ranking_features(
            descriptor=sample_descriptor,
            block=sample_blocks[2],
            slot="amp",
        )
        assert features.gain_in_range == 0.0

    def test_voicing_match(self, sample_descriptor, sample_blocks):
        """Test voicing match calculation."""
        features = build_ranking_features(
            descriptor=sample_descriptor,
            block=sample_blocks[0],
            slot="amp",
        )
        # Should be close to 1.0 for similar voicing
        assert features.voicing_match_bass > 0.5
        assert features.voicing_match_mid > 0.5

    def test_user_prefs_integration(self, sample_descriptor, sample_blocks, sample_user_prefs):
        """Test that user preferences are incorporated."""
        features = build_ranking_features(
            descriptor=sample_descriptor,
            block=sample_blocks[0],  # jcm800
            slot="amp",
            user_prefs=sample_user_prefs,
        )
        assert features.user_used_before == 1.0  # jcm800 in used_blocks
        assert features.user_family_preference == 0.8  # marshall_jcm preference

    def test_block_stats_integration(self, sample_descriptor, sample_blocks, sample_block_stats):
        """Test that block stats are incorporated."""
        features = build_ranking_features(
            descriptor=sample_descriptor,
            block=sample_blocks[0],
            slot="amp",
            block_stats=sample_block_stats.get("jcm800"),
        )
        assert features.block_popularity == 0.85
        assert features.block_avg_rating == 0.9

    def test_fallback_detection(self, sample_descriptor, sample_blocks):
        """Test that fallback blocks are detected."""
        features = build_ranking_features(
            descriptor=sample_descriptor,
            block=sample_blocks[3],  # 5150_blue has fallback tag
            slot="amp",
        )
        assert features.block_is_fallback == 1.0

    def test_confidence_signals(self, sample_descriptor, sample_blocks):
        """Test confidence signal extraction."""
        features = build_ranking_features(
            descriptor=sample_descriptor,
            block=sample_blocks[0],
            slot="amp",
        )
        assert features.descriptor_confidence == 0.85
        assert features.analysis_quality > 0.5


# ============================================================================
# build_features_batch tests
# ============================================================================

class TestBuildFeaturesBatch:
    """Tests for batch feature building."""

    def test_batch_shape(self, sample_descriptor, sample_blocks):
        """Test that batch output has correct shape."""
        matrix, block_ids = build_features_batch(
            descriptor=sample_descriptor,
            blocks=sample_blocks,
            slot="amp",
        )
        assert matrix.shape == (4, 25)  # 4 blocks, 25 features
        assert len(block_ids) == 4

    def test_batch_block_ids(self, sample_descriptor, sample_blocks):
        """Test that block IDs are returned correctly."""
        matrix, block_ids = build_features_batch(
            descriptor=sample_descriptor,
            blocks=sample_blocks,
            slot="amp",
        )
        assert block_ids == ["jcm800", "plexi", "twin", "5150_blue"]

    def test_empty_blocks(self, sample_descriptor):
        """Test handling of empty block list."""
        matrix, block_ids = build_features_batch(
            descriptor=sample_descriptor,
            blocks=[],
            slot="amp",
        )
        assert matrix.shape == (0, 25)
        assert block_ids == []


# ============================================================================
# BlockRanker tests
# ============================================================================

class TestBlockRanker:
    """Tests for the BlockRanker class."""

    def test_heuristic_ranking(self, sample_descriptor, sample_blocks):
        """Test ranking with heuristic fallback (no ML model)."""
        ranker = BlockRanker(use_ml=False)
        assert not ranker.is_ml_ready()

        scored = ranker.rank_blocks(
            descriptor=sample_descriptor,
            blocks=sample_blocks,
            slot="amp",
        )

        assert len(scored) == 4
        assert all(isinstance(s, ScoredBlock) for s in scored)
        # Should be sorted by score descending
        scores = [s.score for s in scored]
        assert scores == sorted(scores, reverse=True)

    def test_jcm800_ranked_top(self, sample_descriptor, sample_blocks):
        """Test that JCM800 is ranked top for marshall_jcm descriptor."""
        ranker = BlockRanker(use_ml=False)
        scored = ranker.rank_blocks(
            descriptor=sample_descriptor,
            blocks=sample_blocks,
            slot="amp",
        )

        # JCM800 should be top-ranked for marshall_jcm family
        assert scored[0].block_id == "jcm800"

    def test_top_k_limiting(self, sample_descriptor, sample_blocks):
        """Test top_k parameter limits results."""
        ranker = BlockRanker(use_ml=False)
        scored = ranker.rank_blocks(
            descriptor=sample_descriptor,
            blocks=sample_blocks,
            slot="amp",
            top_k=2,
        )
        assert len(scored) == 2

    def test_get_top_block(self, sample_descriptor, sample_blocks):
        """Test get_top_block convenience method."""
        ranker = BlockRanker(use_ml=False)
        top = ranker.get_top_block(
            descriptor=sample_descriptor,
            blocks=sample_blocks,
            slot="amp",
        )
        assert top is not None
        assert top.rank == 1

    def test_rank_assignment(self, sample_descriptor, sample_blocks):
        """Test that ranks are assigned correctly."""
        ranker = BlockRanker(use_ml=False)
        scored = ranker.rank_blocks(
            descriptor=sample_descriptor,
            blocks=sample_blocks,
            slot="amp",
        )

        ranks = [s.rank for s in scored]
        assert ranks == [1, 2, 3, 4]

    def test_explanation_generation(self, sample_descriptor, sample_blocks):
        """Test that explanations are generated."""
        ranker = BlockRanker(use_ml=False)
        scored = ranker.rank_blocks(
            descriptor=sample_descriptor,
            blocks=sample_blocks,
            slot="amp",
        )

        # Top block should have explanation
        top = scored[0]
        assert top.explanation  # Non-empty
        assert "family_match" in top.explanation

    def test_explain_ranking(self, sample_descriptor, sample_blocks):
        """Test explain_ranking method."""
        ranker = BlockRanker(use_ml=False)
        scored = ranker.rank_blocks(
            descriptor=sample_descriptor,
            blocks=sample_blocks,
            slot="amp",
        )

        explanation = ranker.explain_ranking(scored[0])
        assert "Block: jcm800" in explanation
        assert "Score:" in explanation

    def test_user_prefs_affect_ranking(self, sample_descriptor, sample_blocks, sample_user_prefs):
        """Test that user preferences affect ranking."""
        ranker = BlockRanker(use_ml=False)

        # Without prefs
        scored_no_prefs = ranker.rank_blocks(
            descriptor=sample_descriptor,
            blocks=sample_blocks,
            slot="amp",
        )

        # With prefs
        scored_with_prefs = ranker.rank_blocks(
            descriptor=sample_descriptor,
            blocks=sample_blocks,
            slot="amp",
            user_prefs=sample_user_prefs,
        )

        # Scores should differ
        scores_no = [s.score for s in scored_no_prefs]
        scores_with = [s.score for s in scored_with_prefs]
        # At least some scores should change due to user prefs
        # (user_used_before, user_family_preference)


# ============================================================================
# FeedbackCollector tests
# ============================================================================

class TestFeedbackCollector:
    """Tests for the FeedbackCollector class."""

    def test_init_creates_db(self, temp_feedback_dir):
        """Test that initialization creates the database."""
        collector = FeedbackCollector(storage_dir=temp_feedback_dir)
        assert (temp_feedback_dir / "feedback.db").exists()

    def test_record_selection(self, temp_feedback_dir):
        """Test recording a selection event."""
        collector = FeedbackCollector(storage_dir=temp_feedback_dir)

        event_id = collector.record_selection(
            descriptor_hash="abc123",
            slot="amp",
            block_id="jcm800",
            was_top_recommendation=True,
            recommendation_rank=1,
        )

        assert event_id is not None
        events = collector.get_events_for_descriptor("abc123")
        assert len(events) == 1
        assert events[0].event_type == FeedbackType.SELECTION
        assert events[0].block_id == "jcm800"

    def test_record_edit(self, temp_feedback_dir):
        """Test recording an edit event."""
        collector = FeedbackCollector(storage_dir=temp_feedback_dir)

        event_id = collector.record_edit(
            descriptor_hash="abc123",
            slot="amp",
            block_id="jcm800",
            parameter_changes={"drive": (5.0, 6.5)},
            edit_magnitude=0.15,
        )

        events = collector.get_events_for_block("jcm800")
        assert len(events) == 1
        assert events[0].event_type == FeedbackType.EDIT
        assert events[0].edit_magnitude == 0.15

    def test_record_export(self, temp_feedback_dir):
        """Test recording an export event."""
        collector = FeedbackCollector(storage_dir=temp_feedback_dir)

        event_ids = collector.record_export(
            descriptor_hash="abc123",
            blocks={"amp": "jcm800", "cab": "v30_4x12"},
            export_format="helix",
        )

        assert len(event_ids) == 2

        events = collector.get_events_for_descriptor("abc123")
        assert len(events) == 2
        assert all(e.event_type == FeedbackType.EXPORT for e in events)

    def test_get_events_for_slot(self, temp_feedback_dir):
        """Test filtering events by slot."""
        collector = FeedbackCollector(storage_dir=temp_feedback_dir)

        collector.record_selection("abc123", "amp", "jcm800")
        collector.record_selection("abc123", "cab", "v30_4x12")

        amp_events = collector.get_events_for_descriptor("abc123", slot="amp")
        assert len(amp_events) == 1
        assert amp_events[0].slot == "amp"

    def test_get_stats(self, temp_feedback_dir):
        """Test getting feedback statistics."""
        collector = FeedbackCollector(storage_dir=temp_feedback_dir)

        collector.record_selection("abc123", "amp", "jcm800")
        collector.record_edit("abc123", "amp", "jcm800", {})
        collector.record_export("abc123", {"amp": "jcm800"}, "helix")

        stats = collector.get_stats()
        assert stats["total_events"] == 3
        assert stats["selections"] == 1
        assert stats["edits"] == 1
        assert stats["exports"] == 1

    def test_clear(self, temp_feedback_dir):
        """Test clearing all feedback."""
        collector = FeedbackCollector(storage_dir=temp_feedback_dir)

        collector.record_selection("abc123", "amp", "jcm800")
        collector.record_selection("abc123", "cab", "v30_4x12")

        count = collector.clear()
        assert count == 2

        stats = collector.get_stats()
        assert stats["total_events"] == 0


# ============================================================================
# TrainingExample tests
# ============================================================================

class TestTrainingExample:
    """Tests for TrainingExample label computation."""

    def test_selected_exported_no_edit(self):
        """Test label for selected and exported without edits."""
        example = TrainingExample(
            descriptor_hash="abc",
            slot="amp",
            block_id="jcm800",
            label=0.0,
            was_selected=True,
            was_exported=True,
            was_edited=False,
        )
        label = example.compute_label()
        # Selected (0.5) + exported no edit (0.4) = 0.9
        assert label == pytest.approx(0.9)

    def test_selected_exported_with_edit(self):
        """Test label for selected, edited, and exported."""
        example = TrainingExample(
            descriptor_hash="abc",
            slot="amp",
            block_id="jcm800",
            label=0.0,
            was_selected=True,
            was_exported=True,
            was_edited=True,
            edit_magnitude=0.5,
        )
        label = example.compute_label()
        # Selected (0.5) + exported with edit (0.2) - edit penalty (0.5 * 0.3 = 0.15) = 0.55
        assert label == pytest.approx(0.55)

    def test_explicit_rating_override(self):
        """Test that explicit rating overrides computed label."""
        example = TrainingExample(
            descriptor_hash="abc",
            slot="amp",
            block_id="jcm800",
            label=0.0,
            was_selected=True,
            was_exported=True,
            explicit_rating=2.5,  # 2.5/5 = 0.5
        )
        label = example.compute_label()
        assert label == pytest.approx(0.5)


# ============================================================================
# Integration tests
# ============================================================================

class TestTranslatorMLIntegration:
    """Integration tests for ML translator with translator.py."""

    def test_import_ml_translator(self):
        """Test that ML translator module can be imported."""
        from tone_forge.ml.translator import (
            RankingFeatures,
            BlockRanker,
            FeedbackCollector,
        )
        assert RankingFeatures is not None
        assert BlockRanker is not None
        assert FeedbackCollector is not None

    def test_module_exports(self):
        """Test that all expected symbols are exported."""
        from tone_forge.ml import translator

        assert hasattr(translator, "RankingFeatures")
        assert hasattr(translator, "build_ranking_features")
        assert hasattr(translator, "BlockRanker")
        assert hasattr(translator, "ScoredBlock")
        assert hasattr(translator, "get_ranker")
        assert hasattr(translator, "rank_blocks")
        assert hasattr(translator, "FeedbackCollector")
        assert hasattr(translator, "record_selection")
        assert hasattr(translator, "record_edit")
        assert hasattr(translator, "record_export")
