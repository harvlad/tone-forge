"""Confidence models for descriptor accuracy estimation.

Uses XGBoost classifiers trained on labeled audio data to estimate
confidence in each descriptor attribute. Falls back to heuristic
confidence scoring when models aren't available.
"""
from __future__ import annotations

from .feature_extractor import MLFeatureVector, extract_ml_features
from .models import ConfidenceModel, compute_ml_confidence
from .registry import (
    get_model,
    is_ready,
    load_models,
    get_model_info,
)

__all__ = [
    "MLFeatureVector",
    "extract_ml_features",
    "ConfidenceModel",
    "compute_ml_confidence",
    "get_model",
    "is_ready",
    "load_models",
    "get_model_info",
]
