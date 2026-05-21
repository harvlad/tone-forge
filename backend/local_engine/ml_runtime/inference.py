"""Unified inference API for ToneForge ML models.

Provides a simple interface for all ML inference tasks:
- Automatically handles model loading
- Falls back to heuristics when models unavailable
- Batches requests for efficiency
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import numpy as np

from .ml_runtime import MLRuntime, get_runtime
from .model_downloader import MODELS

logger = logging.getLogger(__name__)


@dataclass
class InferenceResult:
    """Result from inference."""

    predictions: np.ndarray
    model_used: str
    used_fallback: bool
    confidence: float = 1.0
    metadata: Dict[str, Any] = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class InferenceEngine:
    """Unified inference engine for ToneForge.

    Provides a high-level API for all ML inference, with automatic
    fallback to heuristics when models aren't available.
    """

    def __init__(
        self,
        runtime: Optional[MLRuntime] = None,
        heuristic_fallbacks: Optional[Dict[str, Callable]] = None,
    ):
        """Initialize the inference engine.

        Args:
            runtime: ML runtime instance
            heuristic_fallbacks: Dictionary of fallback functions by model ID
        """
        self.runtime = runtime or get_runtime()
        self.heuristic_fallbacks = heuristic_fallbacks or {}

    def register_fallback(self, model_id: str, fallback_fn: Callable):
        """Register a heuristic fallback for a model.

        Args:
            model_id: Model identifier
            fallback_fn: Fallback function (inputs -> predictions)
        """
        self.heuristic_fallbacks[model_id] = fallback_fn

    def infer(
        self,
        model_id: str,
        inputs: Union[np.ndarray, List[Any]],
        allow_fallback: bool = True,
        **kwargs,
    ) -> InferenceResult:
        """Run inference.

        Args:
            model_id: Model identifier
            inputs: Input data
            allow_fallback: Whether to use heuristic fallback
            **kwargs: Additional arguments

        Returns:
            InferenceResult
        """
        # Convert inputs to numpy array if needed
        if isinstance(inputs, list):
            inputs = np.array(inputs)

        # Try ML model first
        predictions = self.runtime.predict(model_id, inputs, **kwargs)

        if predictions is not None:
            return InferenceResult(
                predictions=predictions,
                model_used=model_id,
                used_fallback=False,
            )

        # Fall back to heuristics
        if allow_fallback and model_id in self.heuristic_fallbacks:
            try:
                fallback_fn = self.heuristic_fallbacks[model_id]
                predictions = fallback_fn(inputs, **kwargs)
                return InferenceResult(
                    predictions=predictions,
                    model_used=f"{model_id}_heuristic",
                    used_fallback=True,
                    confidence=0.7,  # Lower confidence for heuristics
                    metadata={"reason": "ML model not available"},
                )
            except Exception as e:
                logger.warning("Heuristic fallback failed for %s: %s", model_id, e)

        # Return empty result
        return InferenceResult(
            predictions=np.array([]),
            model_used="none",
            used_fallback=True,
            confidence=0.0,
            metadata={"error": "No model or fallback available"},
        )

    # Specific inference methods

    def estimate_confidence(
        self,
        features: np.ndarray,
    ) -> InferenceResult:
        """Estimate descriptor confidence.

        Args:
            features: Feature vector(s)

        Returns:
            Confidence predictions
        """
        return self.infer("confidence_xgboost", features)

    def rank_blocks(
        self,
        features: np.ndarray,
    ) -> InferenceResult:
        """Rank blocks for translation.

        Args:
            features: Ranking feature vectors

        Returns:
            Block scores
        """
        return self.infer("block_ranker", features)

    def classify_notes(
        self,
        note_features: np.ndarray,
    ) -> InferenceResult:
        """Classify notes as real or ghost.

        Args:
            note_features: Note context feature vectors

        Returns:
            Classification predictions
        """
        return self.infer("note_classifier", note_features)

    def classify_genre(
        self,
        audio_features: np.ndarray,
    ) -> InferenceResult:
        """Classify genre from audio features.

        Args:
            audio_features: Audio feature vectors

        Returns:
            Genre predictions
        """
        return self.infer("genre_classifier", audio_features)

    def compute_embedding(
        self,
        audio: np.ndarray,
        sr: int = 22050,
    ) -> InferenceResult:
        """Compute audio embedding.

        Args:
            audio: Audio waveform
            sr: Sample rate

        Returns:
            Embedding vector
        """
        # Audio embedder expects specific input format
        # Ensure correct shape
        if len(audio.shape) == 1:
            audio = audio.reshape(1, -1)

        return self.infer("audio_embedder", audio, sr=sr)

    def process_dynamics(
        self,
        note_features: np.ndarray,
    ) -> InferenceResult:
        """Process dynamics with ML model.

        Args:
            note_features: Note context feature vectors

        Returns:
            Velocity adjustments
        """
        return self.infer("dynamics_model", note_features)

    def correct_timing(
        self,
        timing_features: np.ndarray,
    ) -> InferenceResult:
        """Correct timing with ML model.

        Args:
            timing_features: Timing context feature vectors

        Returns:
            Timing corrections
        """
        return self.infer("timing_corrector", timing_features)

    def is_model_ready(self, model_id: str) -> bool:
        """Check if a model is ready for inference.

        Args:
            model_id: Model identifier

        Returns:
            True if model is available (loaded or downloadable)
        """
        return (
            self.runtime.is_model_loaded(model_id) or
            self.runtime.downloader.is_model_available(model_id) or
            model_id in self.heuristic_fallbacks
        )

    def get_available_capabilities(self) -> Dict[str, bool]:
        """Get available inference capabilities.

        Returns:
            Dictionary of capability -> availability
        """
        capabilities = {}

        capability_models = {
            "confidence_estimation": "confidence_xgboost",
            "block_ranking": "block_ranker",
            "note_classification": "note_classifier",
            "genre_classification": "genre_classifier",
            "audio_embedding": "audio_embedder",
            "dynamics_processing": "dynamics_model",
            "timing_correction": "timing_corrector",
        }

        for capability, model_id in capability_models.items():
            capabilities[capability] = self.is_model_ready(model_id)

        return capabilities


# Default heuristic fallbacks

def _confidence_heuristic(features: np.ndarray, **kwargs) -> np.ndarray:
    """Heuristic confidence estimation.

    Returns confidence based on feature variance and magnitude.
    """
    if len(features.shape) == 1:
        features = features.reshape(1, -1)

    # Simple heuristic: lower variance = higher confidence
    variances = np.var(features, axis=1)
    confidences = 1 / (1 + variances)
    return confidences


def _ranking_heuristic(features: np.ndarray, **kwargs) -> np.ndarray:
    """Heuristic block ranking.

    Returns scores based on weighted feature sum.
    """
    if len(features.shape) == 1:
        features = features.reshape(1, -1)

    # Use first few features as key indicators
    weights = np.ones(features.shape[1])
    weights[:5] = 2.0  # Weight first features more

    scores = np.sum(features * weights, axis=1)
    # Normalize to 0-1
    scores = (scores - scores.min()) / (scores.max() - scores.min() + 1e-8)
    return scores


def _note_classifier_heuristic(features: np.ndarray, **kwargs) -> np.ndarray:
    """Heuristic note classification.

    Classifies based on velocity and duration.
    """
    if len(features.shape) == 1:
        features = features.reshape(1, -1)

    # Assume features have velocity at index 1
    # Higher velocity = more likely real
    if features.shape[1] > 1:
        velocities = features[:, 1]
        probs = velocities / 127.0  # Normalize MIDI velocity
    else:
        probs = np.ones(features.shape[0]) * 0.5

    return np.column_stack([1 - probs, probs])  # [ghost_prob, real_prob]


def _genre_classifier_heuristic(features: np.ndarray, **kwargs) -> np.ndarray:
    """Heuristic genre classification.

    Returns uniform distribution over genres.
    """
    if len(features.shape) == 1:
        features = features.reshape(1, -1)

    # Return uniform distribution over 12 genres
    num_samples = features.shape[0]
    num_genres = 12
    return np.ones((num_samples, num_genres)) / num_genres


# Singleton instance
_engine: Optional[InferenceEngine] = None


def get_engine() -> InferenceEngine:
    """Get the singleton inference engine.

    Returns:
        InferenceEngine instance
    """
    global _engine
    if _engine is None:
        _engine = InferenceEngine()

        # Register default fallbacks
        _engine.register_fallback("confidence_xgboost", _confidence_heuristic)
        _engine.register_fallback("block_ranker", _ranking_heuristic)
        _engine.register_fallback("note_classifier", _note_classifier_heuristic)
        _engine.register_fallback("genre_classifier", _genre_classifier_heuristic)

    return _engine


# Convenience functions

def infer(model_id: str, inputs: np.ndarray, **kwargs) -> InferenceResult:
    """Run inference.

    Args:
        model_id: Model identifier
        inputs: Input data
        **kwargs: Additional arguments

    Returns:
        InferenceResult
    """
    return get_engine().infer(model_id, inputs, **kwargs)


def estimate_confidence(features: np.ndarray) -> np.ndarray:
    """Estimate descriptor confidence.

    Args:
        features: Feature vector(s)

    Returns:
        Confidence values
    """
    result = get_engine().estimate_confidence(features)
    return result.predictions


def rank_blocks(features: np.ndarray) -> np.ndarray:
    """Rank blocks for translation.

    Args:
        features: Ranking feature vectors

    Returns:
        Block scores
    """
    result = get_engine().rank_blocks(features)
    return result.predictions


def classify_notes(note_features: np.ndarray) -> np.ndarray:
    """Classify notes as real or ghost.

    Args:
        note_features: Note context feature vectors

    Returns:
        Classification predictions
    """
    result = get_engine().classify_notes(note_features)
    return result.predictions


def classify_genre(audio_features: np.ndarray) -> np.ndarray:
    """Classify genre from audio features.

    Args:
        audio_features: Audio feature vectors

    Returns:
        Genre predictions
    """
    result = get_engine().classify_genre(audio_features)
    return result.predictions
