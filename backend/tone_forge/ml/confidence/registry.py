"""Model registry for lazy loading and management.

Handles model discovery, loading, versioning, and fallback behavior.
Models are loaded lazily on first use to minimize startup time.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Any
import logging
import os

from .models import ConfidenceModel, get_model

logger = logging.getLogger(__name__)


# Default model directory locations
DEFAULT_MODEL_DIRS = [
    Path.home() / ".toneforge" / "models",
    Path(__file__).parent.parent.parent.parent / "models",  # Project models dir
    Path("/opt/toneforge/models"),  # System-wide
]


@dataclass
class ModelInfo:
    """Information about a loaded model."""
    name: str
    version: str
    path: Path
    loaded: bool
    feature_count: int
    classes: Optional[list] = None


# Global state
_initialized = False
_model_dir: Optional[Path] = None
_model_info: Dict[str, ModelInfo] = {}


def _find_model_dir() -> Optional[Path]:
    """Find the model directory."""
    # Check environment variable first
    env_dir = os.environ.get("TONEFORGE_MODEL_DIR")
    if env_dir:
        path = Path(env_dir)
        if path.exists():
            return path

    # Check default locations
    for path in DEFAULT_MODEL_DIRS:
        if path.exists():
            return path

    return None


def _ensure_initialized() -> None:
    """Ensure the registry is initialized."""
    global _initialized, _model_dir

    if _initialized:
        return

    _model_dir = _find_model_dir()

    if _model_dir:
        logger.info(f"Using model directory: {_model_dir}")
        model = get_model()
        model.load(_model_dir)
    else:
        logger.info("No model directory found, using heuristic confidence")

    _initialized = True


def is_ready() -> bool:
    """Check if ML models are ready for use.

    Returns True if models are loaded, False if using heuristics.
    """
    _ensure_initialized()
    model = get_model()
    return model.is_loaded()


def load_models(model_dir: Optional[Path] = None) -> bool:
    """Explicitly load models from a directory.

    Args:
        model_dir: Directory containing model files. If None, uses default.

    Returns:
        True if models loaded successfully.
    """
    global _initialized, _model_dir

    if model_dir is not None:
        _model_dir = Path(model_dir)
    else:
        _model_dir = _find_model_dir()

    if _model_dir is None:
        logger.warning("No model directory specified or found")
        return False

    model = get_model()
    success = model.load(_model_dir)
    _initialized = True

    return success


def get_model_info() -> Dict[str, Any]:
    """Get information about loaded models.

    Returns:
        Dict with model information and status.
    """
    _ensure_initialized()
    model = get_model()

    info = {
        "loaded": model.is_loaded(),
        "model_dir": str(_model_dir) if _model_dir else None,
        "models": {},
    }

    if model.amp_family_model is not None:
        info["models"]["amp_family"] = {
            "loaded": True,
            "type": type(model.amp_family_model).__name__,
        }
    else:
        info["models"]["amp_family"] = {"loaded": False, "fallback": "heuristic"}

    if model.gain_model is not None:
        info["models"]["gain"] = {
            "loaded": True,
            "type": type(model.gain_model).__name__,
        }
    else:
        info["models"]["gain"] = {"loaded": False, "fallback": "heuristic"}

    if model.cab_model is not None:
        info["models"]["cab"] = {
            "loaded": True,
            "type": type(model.cab_model).__name__,
        }
    else:
        info["models"]["cab"] = {"loaded": False, "fallback": "heuristic"}

    if model.effects_model is not None:
        info["models"]["effects"] = {
            "loaded": True,
            "type": type(model.effects_model).__name__,
        }
    else:
        info["models"]["effects"] = {"loaded": False, "fallback": "heuristic"}

    return info


def reset() -> None:
    """Reset the registry state (for testing)."""
    global _initialized, _model_dir, _model_info

    _initialized = False
    _model_dir = None
    _model_info = {}
