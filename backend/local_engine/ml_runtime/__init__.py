"""ML Runtime for ToneForge Local Engine.

Provides model management and inference for local ML execution:

- model_downloader: On-demand model downloading
- ml_runtime: Model loading and memory management
- inference: Unified inference API with fallbacks

Usage:
    from local_engine.ml_runtime import get_engine, infer

    # Get inference engine
    engine = get_engine()

    # Run inference with automatic fallback
    result = infer("block_ranker", features)

    # Check capabilities
    caps = engine.get_available_capabilities()
"""
from __future__ import annotations

from .model_downloader import (
    ModelDownloader,
    ModelInfo,
    ModelSize,
    ModelStatus,
    MODELS,
    get_downloader,
    ensure_model,
)
from .ml_runtime import (
    MLRuntime,
    RuntimeConfig,
    DeviceType,
    get_runtime,
    predict,
)
from .inference import (
    InferenceEngine,
    InferenceResult,
    get_engine,
    infer,
    estimate_confidence,
    rank_blocks,
    classify_notes,
    classify_genre,
)

__all__ = [
    # Downloader
    "ModelDownloader",
    "ModelInfo",
    "ModelSize",
    "ModelStatus",
    "MODELS",
    "get_downloader",
    "ensure_model",
    # Runtime
    "MLRuntime",
    "RuntimeConfig",
    "DeviceType",
    "get_runtime",
    "predict",
    # Inference
    "InferenceEngine",
    "InferenceResult",
    "get_engine",
    "infer",
    "estimate_confidence",
    "rank_blocks",
    "classify_notes",
    "classify_genre",
]


def initialize_ml_runtime(
    preload_models: bool = True,
    prefer_gpu: bool = True,
) -> MLRuntime:
    """Initialize the ML runtime with default settings.

    Args:
        preload_models: Whether to preload bundled models
        prefer_gpu: Whether to prefer GPU when available

    Returns:
        Initialized MLRuntime
    """
    config = RuntimeConfig(
        prefer_gpu=prefer_gpu,
        preload_models=[
            "confidence_xgboost",
            "block_ranker",
        ] if preload_models else [],
    )

    runtime = get_runtime(config)

    if preload_models:
        runtime.preload_models()

    return runtime


def get_ml_status() -> dict:
    """Get status of ML runtime.

    Returns:
        Dictionary with runtime status
    """
    runtime = get_runtime()
    downloader = get_downloader()
    engine = get_engine()

    return {
        "runtime": runtime.get_runtime_stats(),
        "storage": downloader.get_storage_usage(),
        "capabilities": engine.get_available_capabilities(),
        "required_downloads": [
            m.model_id for m in downloader.get_required_downloads()
        ],
    }
