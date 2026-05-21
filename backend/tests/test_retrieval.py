"""Tests for retrieval-augmented analysis.

Tests the reference library and retrieval augmentation.
"""
import pytest
import tempfile
import numpy as np
from pathlib import Path

from tone_forge.ml.retrieval.reference_library import (
    ReferenceLibrary,
    ToneReference,
    RetrievalResult,
)
from tone_forge.ml.retrieval.augmented_analysis import (
    RetrievalAugmenter,
    AugmentedContext,
    augment_descriptor,
)


# ============================================================================
# Test fixtures
# ============================================================================

@pytest.fixture
def temp_dir():
    """Create a temporary directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def library(temp_dir):
    """Create a reference library with temporary storage."""
    db_path = temp_dir / "test_references.db"
    return ReferenceLibrary(db_path=db_path)


@pytest.fixture
def sample_descriptor():
    """Create a sample descriptor."""
    return {
        "amp": {
            "family": "marshall_jcm",
            "gain": 0.7,
            "voicing": {"bass": 0.5, "mid": 0.6, "treble": 0.55},
        },
        "cab": {
            "configuration": "4x12",
            "speaker_character": "v30_like",
        },
        "effects": {
            "delay": {"time_ms": 350, "mix": 0.3},
            "reverb": {"type": "hall", "mix": 0.2},
        },
    }


@pytest.fixture
def sample_embedding():
    """Create a sample embedding."""
    np.random.seed(42)
    return np.random.randn(128).astype(np.float32)


# ============================================================================
# ReferenceLibrary tests
# ============================================================================

class TestReferenceLibrary:
    """Tests for ReferenceLibrary."""

    def test_init(self, library):
        """Test library initialization."""
        assert library.db_path.exists()

    def test_add_reference(self, library, sample_descriptor, sample_embedding):
        """Test adding a reference."""
        ref_id = library.add_reference(
            descriptor=sample_descriptor,
            embedding=sample_embedding,
            genre="rock",
            confidence=0.85,
        )

        assert ref_id is not None
        assert ref_id.startswith("ref_")

    def test_get_reference(self, library, sample_descriptor, sample_embedding):
        """Test retrieving a reference."""
        ref_id = library.add_reference(
            descriptor=sample_descriptor,
            embedding=sample_embedding,
            genre="rock",
        )

        ref = library.get_reference(ref_id)

        assert ref is not None
        assert ref.reference_id == ref_id
        assert ref.amp_family == "marshall_jcm"
        assert ref.genre == "rock"

    def test_duplicate_prevention(self, library, sample_descriptor):
        """Test that duplicate descriptors return existing reference."""
        ref_id1 = library.add_reference(sample_descriptor, genre="rock")
        ref_id2 = library.add_reference(sample_descriptor, genre="rock")

        # Should return same reference
        assert ref_id1 == ref_id2

    def test_search_similar(self, library, sample_descriptor, sample_embedding):
        """Test similarity search."""
        # Add several references
        for i in range(5):
            desc = sample_descriptor.copy()
            desc["amp"] = {"family": f"amp_{i}", "gain": 0.5 + i * 0.1}

            # Create embeddings similar to query
            emb = sample_embedding + np.random.randn(128).astype(np.float32) * 0.1

            library.add_reference(desc, embedding=emb, genre="rock")

        # Search
        results = library.search_similar(sample_embedding, k=3, min_similarity=0.0)

        assert len(results) <= 3
        for result in results:
            assert isinstance(result, RetrievalResult)
            assert result.similarity > 0

    def test_search_by_attributes(self, library, sample_descriptor):
        """Test attribute-based search."""
        # Add references with different genres
        for genre in ["rock", "metal", "blues"]:
            desc = sample_descriptor.copy()
            library.add_reference(desc, genre=genre)

        # Search by genre
        results = library.search_by_attributes(genre="rock")

        assert len(results) >= 1
        assert all(r.genre == "rock" for r in results)

    def test_search_by_gain_range(self, library, sample_descriptor):
        """Test searching by gain range."""
        # Add references with different gains
        for gain in [0.3, 0.5, 0.7, 0.9]:
            desc = sample_descriptor.copy()
            desc["amp"]["gain"] = gain
            library.add_reference(desc, genre="rock")

        # Search for high gain
        results = library.search_by_attributes(gain_range=(0.6, 1.0))

        assert len(results) >= 2
        for ref in results:
            assert ref.gain_level >= 0.6

    def test_update_rating(self, library, sample_descriptor):
        """Test updating rating."""
        ref_id = library.add_reference(sample_descriptor)

        library.update_rating(ref_id, 4.5)

        ref = library.get_reference(ref_id)
        assert ref.user_rating == 4.5

    def test_mark_exported(self, library, sample_descriptor):
        """Test marking as exported."""
        ref_id = library.add_reference(sample_descriptor)

        library.mark_exported(ref_id)

        ref = library.get_reference(ref_id)
        assert ref.was_exported is True

    def test_add_tags(self, library, sample_descriptor):
        """Test adding tags."""
        ref_id = library.add_reference(sample_descriptor)

        library.add_tags(ref_id, ["favorite", "live_sound"])

        ref = library.get_reference(ref_id)
        assert "favorite" in ref.tags
        assert "live_sound" in ref.tags

    def test_get_stats(self, library, sample_descriptor):
        """Test getting statistics."""
        # Add some references
        for genre in ["rock", "rock", "metal"]:
            desc = sample_descriptor.copy()
            library.add_reference(desc, genre=genre)
            sample_descriptor["amp"]["gain"] += 0.01  # Make unique

        stats = library.get_stats()

        assert stats["total_references"] >= 3
        assert "rock" in stats["genre_distribution"]

    def test_delete_reference(self, library, sample_descriptor):
        """Test deleting a reference."""
        ref_id = library.add_reference(sample_descriptor)

        library.delete_reference(ref_id)

        ref = library.get_reference(ref_id)
        assert ref is None

    def test_clear(self, library, sample_descriptor):
        """Test clearing all references."""
        library.add_reference(sample_descriptor)
        library.add_reference(sample_descriptor.copy())

        library.clear()

        stats = library.get_stats()
        assert stats["total_references"] == 0


# ============================================================================
# RetrievalAugmenter tests
# ============================================================================

class TestRetrievalAugmenter:
    """Tests for RetrievalAugmenter."""

    def test_init(self, library):
        """Test augmenter initialization."""
        augmenter = RetrievalAugmenter(library=library)
        assert augmenter.library is library

    def test_augment_with_embedding(self, library, sample_descriptor, sample_embedding):
        """Test augmentation with embedding."""
        # Add some reference data
        for i in range(3):
            desc = sample_descriptor.copy()
            emb = sample_embedding + np.random.randn(128).astype(np.float32) * 0.1
            library.add_reference(desc, embedding=emb, genre="rock")

        augmenter = RetrievalAugmenter(library=library, k=3, min_similarity=0.0)
        context = augmenter.augment_analysis(
            descriptor=sample_descriptor,
            embedding=sample_embedding,
            detected_genre="rock",
        )

        assert isinstance(context, AugmentedContext)
        assert len(context.similar_references) <= 3

    def test_augment_by_attributes(self, library, sample_descriptor):
        """Test attribute-based augmentation."""
        # Add reference data without embeddings
        for i in range(3):
            desc = sample_descriptor.copy()
            library.add_reference(desc, genre="rock")
            sample_descriptor["amp"]["gain"] += 0.01

        augmenter = RetrievalAugmenter(library=library)
        context = augmenter.augment_analysis(
            descriptor=sample_descriptor,
            embedding=None,
            detected_genre="rock",
        )

        assert isinstance(context, AugmentedContext)

    def test_build_consensus(self, library, sample_descriptor, sample_embedding):
        """Test consensus building from references."""
        # Add references with consistent genre
        for i in range(5):
            desc = sample_descriptor.copy()
            emb = sample_embedding + np.random.randn(128).astype(np.float32) * 0.1
            library.add_reference(
                desc,
                embedding=emb,
                genre="rock",
                archetype="classic_rock",
            )

        augmenter = RetrievalAugmenter(library=library, min_similarity=0.0)
        context = augmenter.augment_analysis(
            descriptor=sample_descriptor,
            embedding=sample_embedding,
        )

        # Should have consensus
        if len(context.similar_references) > 0:
            assert context.consensus_genre == "rock"
            assert context.consensus_strength > 0

    def test_store_analysis(self, library, sample_descriptor, sample_embedding):
        """Test storing an analysis."""
        augmenter = RetrievalAugmenter(library=library)

        ref_id = augmenter.store_analysis(
            descriptor=sample_descriptor,
            embedding=sample_embedding,
            genre="metal",
            confidence=0.9,
        )

        assert ref_id is not None

        ref = library.get_reference(ref_id)
        assert ref.genre == "metal"

    def test_mark_successful(self, library, sample_descriptor):
        """Test marking reference as successful."""
        augmenter = RetrievalAugmenter(library=library)

        ref_id = augmenter.store_analysis(
            descriptor=sample_descriptor,
            genre="rock",
        )

        augmenter.mark_successful(ref_id, rating=5.0)

        ref = library.get_reference(ref_id)
        assert ref.was_exported is True
        assert ref.user_rating == 5.0


# ============================================================================
# Augmented descriptor tests
# ============================================================================

class TestAugmentDescriptor:
    """Tests for descriptor augmentation."""

    def test_augment_descriptor(self, sample_descriptor):
        """Test applying augmentation to descriptor."""
        context = AugmentedContext(
            similar_references=[],
            consensus_amp_family="marshall_jcm",
            consensus_genre="rock",
            amp_confidence_boost=0.1,
            retrieval_quality=0.8,
            consensus_strength=0.9,
        )

        augmented = augment_descriptor(sample_descriptor, context)

        assert "_retrieval" in augmented
        assert augmented["_retrieval"]["augmented"] is True
        assert augmented["_retrieval"]["consensus_genre"] == "rock"

    def test_augment_adds_confidence_boost(self, sample_descriptor):
        """Test that confidence boost is applied."""
        sample_descriptor["amp"]["confidence"] = 0.7

        context = AugmentedContext(
            similar_references=[],
            amp_confidence_boost=0.1,
        )

        augmented = augment_descriptor(sample_descriptor, context)

        assert abs(augmented["amp"]["confidence"] - 0.8) < 0.001

    def test_augment_adds_suggestions(self, sample_descriptor):
        """Test that suggestions are added."""
        context = AugmentedContext(
            similar_references=[],
            suggested_gain_adjustment=0.1,
            suggested_effects=["chorus"],
        )

        augmented = augment_descriptor(sample_descriptor, context)

        assert augmented["_retrieval"]["suggested_gain_adjustment"] == 0.1
        assert "chorus" in augmented["_retrieval"]["suggested_effects"]


# ============================================================================
# Integration tests
# ============================================================================

class TestRetrievalIntegration:
    """Integration tests for retrieval module."""

    def test_full_workflow(self, library, sample_descriptor, sample_embedding):
        """Test complete retrieval workflow."""
        augmenter = RetrievalAugmenter(library=library, min_similarity=0.0)

        # Store initial analysis
        ref_id = augmenter.store_analysis(
            descriptor=sample_descriptor,
            embedding=sample_embedding,
            genre="rock",
            confidence=0.85,
        )

        # Mark as successful
        augmenter.mark_successful(ref_id, rating=4.0)

        # Later, augment a similar analysis
        new_embedding = sample_embedding + np.random.randn(128).astype(np.float32) * 0.05
        context = augmenter.augment_analysis(
            descriptor=sample_descriptor,
            embedding=new_embedding,
            detected_genre="rock",
        )

        # Should find the previous successful export
        assert len(context.similar_references) >= 1

    def test_module_imports(self):
        """Test that all module imports work."""
        from tone_forge.ml.retrieval import (
            ReferenceLibrary,
            ToneReference,
            RetrievalResult,
            RetrievalAugmenter,
            AugmentedContext,
            get_library,
            get_augmenter,
            augment_analysis,
        )

        assert ReferenceLibrary is not None
        assert RetrievalAugmenter is not None
        assert AugmentedContext is not None
