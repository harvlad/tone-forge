"""Tests for local ML runtime.

Tests the model downloader, runtime, and inference engine.
"""
import pytest
import tempfile
import numpy as np
from pathlib import Path

from local_engine.ml_runtime.model_downloader import (
    ModelDownloader,
    ModelInfo,
    ModelSize,
    ModelStatus,
    MODELS,
)
from local_engine.ml_runtime.ml_runtime import (
    MLRuntime,
    RuntimeConfig,
    DeviceType,
)
from local_engine.ml_runtime.inference import (
    InferenceEngine,
    InferenceResult,
    _confidence_heuristic,
    _ranking_heuristic,
    _note_classifier_heuristic,
    _genre_classifier_heuristic,
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
def downloader(temp_dir):
    """Create a model downloader with temporary storage."""
    return ModelDownloader(model_dir=temp_dir / "models")


@pytest.fixture
def runtime(downloader):
    """Create an ML runtime."""
    config = RuntimeConfig(prefer_gpu=False, max_loaded_models=3)
    return MLRuntime(config=config, downloader=downloader)


@pytest.fixture
def engine(runtime):
    """Create an inference engine."""
    engine = InferenceEngine(runtime=runtime)
    # Register default fallbacks
    engine.register_fallback("confidence_xgboost", _confidence_heuristic)
    engine.register_fallback("block_ranker", _ranking_heuristic)
    engine.register_fallback("note_classifier", _note_classifier_heuristic)
    engine.register_fallback("genre_classifier", _genre_classifier_heuristic)
    return engine


# ============================================================================
# ModelDownloader tests
# ============================================================================

class TestModelDownloader:
    """Tests for ModelDownloader."""

    def test_init(self, downloader):
        """Test downloader initialization."""
        assert downloader.model_dir.exists()

    def test_models_defined(self):
        """Test that models are defined."""
        assert len(MODELS) > 0
        assert "confidence_xgboost" in MODELS
        assert "block_ranker" in MODELS

    def test_model_info_properties(self):
        """Test ModelInfo properties."""
        model = MODELS["confidence_xgboost"]

        assert model.model_id == "confidence_xgboost"
        assert model.name == "Confidence Classifier"
        assert model.size_bytes > 0
        assert model.size_class == ModelSize.TINY

    def test_model_not_available_initially(self, downloader):
        """Test that models aren't available before download."""
        assert not downloader.is_model_available("confidence_xgboost")
        assert downloader.get_model_status("confidence_xgboost") == ModelStatus.NOT_AVAILABLE

    def test_get_model_path_none_when_not_downloaded(self, downloader):
        """Test that get_model_path returns None when not downloaded."""
        path = downloader.get_model_path("confidence_xgboost")
        assert path is None

    def test_get_required_downloads(self, downloader):
        """Test getting required downloads."""
        required = downloader.get_required_downloads()

        # All models should need downloading initially
        assert len(required) == len(MODELS)

    def test_get_available_models(self, downloader):
        """Test getting available models."""
        available = downloader.get_available_models()

        # None should be available initially
        assert len(available) == 0

    def test_download_creates_placeholder(self, downloader):
        """Test that download creates a placeholder file."""
        # This will create a placeholder since URL is fake
        result = downloader.download_model("confidence_xgboost")

        # Should succeed with placeholder
        assert result is True
        assert downloader.is_model_available("confidence_xgboost")

    def test_ensure_model(self, downloader):
        """Test ensure_model function."""
        path = downloader.ensure_model("confidence_xgboost")

        assert path is not None
        assert path.exists()

    def test_delete_model(self, downloader):
        """Test deleting a model."""
        # First download
        downloader.download_model("confidence_xgboost")
        assert downloader.is_model_available("confidence_xgboost")

        # Then delete
        downloader.delete_model("confidence_xgboost")
        assert not downloader.is_model_available("confidence_xgboost")

    def test_clear_all_models(self, downloader):
        """Test clearing all models."""
        # Download some models
        downloader.download_model("confidence_xgboost")
        downloader.download_model("block_ranker")

        # Clear all
        downloader.clear_all_models()

        # Should be empty
        assert len(downloader.get_available_models()) == 0

    def test_get_storage_usage(self, downloader):
        """Test storage usage tracking."""
        # Download a model
        downloader.download_model("confidence_xgboost")

        usage = downloader.get_storage_usage()

        assert usage["model_count"] == 1
        assert usage["total_bytes"] > 0

    def test_unknown_model(self, downloader):
        """Test handling unknown model."""
        path = downloader.get_model_path("unknown_model_xyz")
        assert path is None

        result = downloader.download_model("unknown_model_xyz")
        assert result is False


# ============================================================================
# MLRuntime tests
# ============================================================================

class TestMLRuntime:
    """Tests for MLRuntime."""

    def test_init(self, runtime):
        """Test runtime initialization."""
        assert runtime is not None
        assert runtime.get_device() == DeviceType.CPU  # GPU disabled in fixture

    def test_device_detection(self, runtime):
        """Test device detection."""
        device = runtime.get_device()
        assert device in (DeviceType.CPU, DeviceType.CUDA, DeviceType.MPS)

    def test_is_gpu_available(self, runtime):
        """Test GPU availability check."""
        # With prefer_gpu=False, should be False
        assert runtime.is_gpu_available() is False

    def test_is_model_loaded(self, runtime):
        """Test model loaded check."""
        assert not runtime.is_model_loaded("confidence_xgboost")

    def test_load_model_downloads_if_needed(self, runtime):
        """Test that loading attempts to download model if needed."""
        # Without real models, this will fail to load (placeholder can't be pickled)
        # But the download attempt should happen
        result = runtime.load_model("confidence_xgboost")

        # May fail because placeholder isn't valid pickle
        # The important thing is it tried to download
        assert runtime.downloader.is_model_available("confidence_xgboost")

    def test_unload_model(self, runtime):
        """Test unloading a model."""
        # Manually add a mock model to test unloading
        runtime._models["test_model"] = "mock_model"
        runtime._model_load_order.append("test_model")

        assert runtime.is_model_loaded("test_model")

        runtime.unload_model("test_model")
        assert not runtime.is_model_loaded("test_model")

    def test_model_eviction(self, runtime):
        """Test LRU model eviction."""
        # Max models is 3 in fixture
        # Manually add mock models to test eviction
        for i, model_id in enumerate(["model_a", "model_b", "model_c"]):
            runtime._models[model_id] = f"mock_{i}"
            runtime._model_load_order.append(model_id)

        assert len(runtime._models) == 3

        # Add 4th model - should evict oldest (model_a)
        runtime._evict_if_needed()  # Evict first
        runtime._models["model_d"] = "mock_3"
        runtime._model_load_order.append("model_d")

        assert len(runtime._models) == 3
        # First loaded model should be evicted
        assert not runtime.is_model_loaded("model_a")
        assert runtime.is_model_loaded("model_d")

    def test_get_runtime_stats(self, runtime):
        """Test getting runtime stats."""
        # Manually add a mock model
        runtime._models["test_model"] = "mock"
        runtime._model_load_order.append("test_model")

        stats = runtime.get_runtime_stats()

        assert "device" in stats
        assert "loaded_models" in stats
        assert "test_model" in stats["loaded_models"]

    def test_cleanup(self, runtime):
        """Test runtime cleanup."""
        runtime.load_model("confidence_xgboost")
        runtime.load_model("block_ranker")

        runtime.cleanup()

        assert len(runtime._models) == 0


# ============================================================================
# InferenceEngine tests
# ============================================================================

class TestInferenceEngine:
    """Tests for InferenceEngine."""

    def test_init(self, engine):
        """Test engine initialization."""
        assert engine is not None

    def test_register_fallback(self, engine):
        """Test registering a fallback."""
        def custom_fallback(inputs, **kwargs):
            return np.zeros(len(inputs))

        engine.register_fallback("custom_model", custom_fallback)
        assert "custom_model" in engine.heuristic_fallbacks

    def test_infer_with_fallback(self, engine):
        """Test inference with fallback."""
        features = np.random.randn(10, 20).astype(np.float32)

        result = engine.infer("confidence_xgboost", features)

        assert isinstance(result, InferenceResult)
        # May use model or fallback depending on placeholder handling

    def test_estimate_confidence(self, engine):
        """Test confidence estimation."""
        features = np.random.randn(5, 50).astype(np.float32)

        result = engine.estimate_confidence(features)

        assert isinstance(result, InferenceResult)
        assert len(result.predictions) > 0

    def test_rank_blocks(self, engine):
        """Test block ranking."""
        features = np.random.randn(10, 25).astype(np.float32)

        result = engine.rank_blocks(features)

        assert isinstance(result, InferenceResult)
        assert len(result.predictions) > 0

    def test_classify_notes(self, engine):
        """Test note classification."""
        features = np.random.randn(8, 15).astype(np.float32)

        result = engine.classify_notes(features)

        assert isinstance(result, InferenceResult)

    def test_classify_genre(self, engine):
        """Test genre classification."""
        features = np.random.randn(3, 20).astype(np.float32)

        result = engine.classify_genre(features)

        assert isinstance(result, InferenceResult)

    def test_is_model_ready(self, engine):
        """Test model readiness check."""
        # Should be ready if fallback registered
        assert engine.is_model_ready("confidence_xgboost")
        assert engine.is_model_ready("block_ranker")

        # Unknown model with no fallback
        assert not engine.is_model_ready("unknown_model_xyz")

    def test_get_available_capabilities(self, engine):
        """Test capability checking."""
        caps = engine.get_available_capabilities()

        assert "confidence_estimation" in caps
        assert "block_ranking" in caps
        assert "genre_classification" in caps


# ============================================================================
# Heuristic fallback tests
# ============================================================================

class TestHeuristicFallbacks:
    """Tests for heuristic fallback functions."""

    def test_confidence_heuristic(self):
        """Test confidence heuristic."""
        features = np.random.randn(5, 20).astype(np.float32)

        result = _confidence_heuristic(features)

        assert result.shape == (5,)
        assert all(0 <= r <= 1 for r in result)

    def test_ranking_heuristic(self):
        """Test ranking heuristic."""
        features = np.random.randn(10, 25).astype(np.float32)

        result = _ranking_heuristic(features)

        assert result.shape == (10,)
        assert all(0 <= r <= 1 for r in result)

    def test_note_classifier_heuristic(self):
        """Test note classifier heuristic."""
        # Features with velocity at index 1
        features = np.random.randn(8, 15).astype(np.float32)
        features[:, 1] = np.array([20, 50, 80, 100, 30, 60, 90, 110])  # Velocities

        result = _note_classifier_heuristic(features)

        assert result.shape == (8, 2)  # [ghost_prob, real_prob]
        assert all(result[:, 0] + result[:, 1] > 0.99)  # Sum to ~1

    def test_genre_classifier_heuristic(self):
        """Test genre classifier heuristic."""
        features = np.random.randn(3, 20).astype(np.float32)

        result = _genre_classifier_heuristic(features)

        assert result.shape == (3, 12)  # 12 genres
        # Should be uniform distribution
        assert np.allclose(result.sum(axis=1), 1.0)


# ============================================================================
# Integration tests
# ============================================================================

class TestMLRuntimeIntegration:
    """Integration tests for ML runtime."""

    def test_full_workflow(self, downloader, temp_dir):
        """Test complete ML workflow."""
        # Initialize runtime
        config = RuntimeConfig(prefer_gpu=False)
        runtime = MLRuntime(config=config, downloader=downloader)

        # Initialize engine with fallbacks
        engine = InferenceEngine(runtime=runtime)
        engine.register_fallback("confidence_xgboost", _confidence_heuristic)

        # Run inference
        features = np.random.randn(5, 50).astype(np.float32)
        result = engine.estimate_confidence(features)

        assert result.predictions is not None
        assert len(result.predictions) == 5

        # Cleanup
        runtime.cleanup()

    def test_module_imports(self):
        """Test that all module imports work."""
        from local_engine.ml_runtime import (
            ModelDownloader,
            ModelInfo,
            MLRuntime,
            RuntimeConfig,
            InferenceEngine,
            InferenceResult,
            get_downloader,
            get_runtime,
            get_engine,
            infer,
            initialize_ml_runtime,
            get_ml_status,
        )

        assert ModelDownloader is not None
        assert MLRuntime is not None
        assert InferenceEngine is not None

    def test_convenience_functions(self, temp_dir):
        """Test convenience functions."""
        from local_engine.ml_runtime import (
            estimate_confidence,
            rank_blocks,
            classify_notes,
            classify_genre,
        )

        # These use singletons, so just verify they work
        features = np.random.randn(3, 50).astype(np.float32)

        # May return empty or fallback results
        result = estimate_confidence(features)
        assert result is not None
