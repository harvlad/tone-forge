"""ToneForge ML module - Machine learning augmentation for audio analysis.

This module provides ML-based enhancements to the core DSP analysis:
- Confidence models for descriptor accuracy estimation
- Audio embeddings for similarity search
- Translator ranking for block recommendations
- MIDI refinement for cleaner extraction
- Preference learning for personalization

Design principles:
- DSP-first: ML augments, never replaces DSP fundamentals
- Graceful degradation: Works without ML models
- Privacy-first: Audio stays local
- Transparent: All decisions explainable
"""
from __future__ import annotations

__all__ = [
    "ml_available",
    "get_ml_status",
]


def ml_available() -> bool:
    """Check if ML models are available and loaded."""
    try:
        from tone_forge.ml.confidence.registry import is_ready
        return is_ready()
    except ImportError:
        return False


def get_ml_status() -> dict:
    """Get status of all ML subsystems."""
    status = {
        "confidence_models": False,
        "embeddings": False,
        "ranker": False,
        "vector_store": False,
    }

    try:
        from tone_forge.ml.confidence.registry import is_ready
        status["confidence_models"] = is_ready()
    except ImportError:
        pass

    try:
        from tone_forge.ml.embeddings.encoder import is_encoder_ready
        status["embeddings"] = is_encoder_ready()
    except ImportError:
        pass

    try:
        from tone_forge.ml.embeddings.vector_store import is_store_ready
        status["vector_store"] = is_store_ready()
    except ImportError:
        pass

    return status
