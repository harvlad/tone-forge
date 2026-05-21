"""ML Runtime for ToneForge Local Engine.

Manages ML model loading, caching, and inference:
- Lazy loading of models
- GPU acceleration when available
- Memory management
- Fallback to heuristics when models unavailable
"""
from __future__ import annotations

import gc
import logging
import pickle
import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

import numpy as np

from .model_downloader import (
    ModelDownloader,
    ModelInfo,
    MODELS,
    get_downloader,
    ensure_model,
)

logger = logging.getLogger(__name__)


class DeviceType(Enum):
    """Compute device types."""
    CPU = "cpu"
    CUDA = "cuda"
    MPS = "mps"  # Apple Silicon


@dataclass
class RuntimeConfig:
    """Configuration for ML runtime."""

    # Device settings
    prefer_gpu: bool = True
    max_gpu_memory_mb: int = 2048

    # Model settings
    preload_models: List[str] = None
    max_loaded_models: int = 5

    # Inference settings
    batch_size: int = 32
    num_threads: int = 4

    def __post_init__(self):
        if self.preload_models is None:
            self.preload_models = []


class MLRuntime:
    """Runtime environment for ML inference."""

    def __init__(
        self,
        config: Optional[RuntimeConfig] = None,
        downloader: Optional[ModelDownloader] = None,
    ):
        """Initialize the runtime.

        Args:
            config: Runtime configuration
            downloader: Model downloader instance
        """
        self.config = config or RuntimeConfig()
        self.downloader = downloader or get_downloader()

        # Loaded models cache
        self._models: Dict[str, Any] = {}
        self._model_load_order: List[str] = []
        self._lock = threading.Lock()

        # Device detection
        self._device = self._detect_device()

        logger.info("ML Runtime initialized with device: %s", self._device.value)

    def _detect_device(self) -> DeviceType:
        """Detect the best available compute device.

        Returns:
            DeviceType
        """
        if not self.config.prefer_gpu:
            return DeviceType.CPU

        # Check for CUDA
        try:
            import torch
            if torch.cuda.is_available():
                logger.info("CUDA available: %s", torch.cuda.get_device_name(0))
                return DeviceType.CUDA
        except ImportError:
            pass

        # Check for MPS (Apple Silicon)
        try:
            import torch
            if hasattr(torch.backends, 'mps') and torch.backends.mps.is_available():
                logger.info("MPS (Apple Silicon) available")
                return DeviceType.MPS
        except ImportError:
            pass

        return DeviceType.CPU

    def get_device(self) -> DeviceType:
        """Get the current compute device.

        Returns:
            DeviceType
        """
        return self._device

    def is_gpu_available(self) -> bool:
        """Check if GPU is available.

        Returns:
            True if GPU available
        """
        return self._device in (DeviceType.CUDA, DeviceType.MPS)

    def load_model(self, model_id: str) -> bool:
        """Load a model into memory.

        Args:
            model_id: Model identifier

        Returns:
            True if successful
        """
        with self._lock:
            if model_id in self._models:
                # Move to end of LRU list
                self._model_load_order.remove(model_id)
                self._model_load_order.append(model_id)
                return True

            # Ensure model is downloaded
            model_path = self.downloader.ensure_model(model_id)
            if not model_path:
                logger.warning("Model %s not available", model_id)
                return False

            # Evict old models if needed
            self._evict_if_needed()

            # Load the model
            try:
                model = self._load_model_file(model_id, model_path)
                self._models[model_id] = model
                self._model_load_order.append(model_id)
                logger.info("Loaded model %s", model_id)
                return True
            except Exception as e:
                logger.error("Failed to load model %s: %s", model_id, e)
                return False

    def unload_model(self, model_id: str):
        """Unload a model from memory.

        Args:
            model_id: Model identifier
        """
        with self._lock:
            if model_id in self._models:
                del self._models[model_id]
                self._model_load_order.remove(model_id)
                gc.collect()
                logger.debug("Unloaded model %s", model_id)

    def is_model_loaded(self, model_id: str) -> bool:
        """Check if a model is loaded.

        Args:
            model_id: Model identifier

        Returns:
            True if loaded
        """
        return model_id in self._models

    def get_model(self, model_id: str) -> Optional[Any]:
        """Get a loaded model.

        Args:
            model_id: Model identifier

        Returns:
            Model object if loaded
        """
        # Try to load if not already loaded
        if model_id not in self._models:
            self.load_model(model_id)

        return self._models.get(model_id)

    def predict(
        self,
        model_id: str,
        inputs: Union[np.ndarray, List[Any]],
        **kwargs,
    ) -> Optional[np.ndarray]:
        """Run inference with a model.

        Args:
            model_id: Model identifier
            inputs: Input data
            **kwargs: Additional arguments

        Returns:
            Predictions or None if model unavailable
        """
        model = self.get_model(model_id)
        if model is None:
            return None

        try:
            # Handle different model types
            model_info = MODELS.get(model_id)
            if not model_info:
                return None

            if model_info.framework in ("sklearn", "lightgbm"):
                return self._predict_sklearn(model, inputs, **kwargs)
            elif model_info.framework == "onnx":
                return self._predict_onnx(model, inputs, **kwargs)
            elif model_info.framework == "pytorch":
                return self._predict_pytorch(model, inputs, **kwargs)
            else:
                logger.warning("Unknown framework: %s", model_info.framework)
                return None

        except Exception as e:
            logger.error("Prediction failed for %s: %s", model_id, e)
            return None

    def _predict_sklearn(
        self,
        model: Any,
        inputs: np.ndarray,
        **kwargs,
    ) -> np.ndarray:
        """Run sklearn/lightgbm prediction.

        Args:
            model: Sklearn model
            inputs: Input array
            **kwargs: Additional arguments

        Returns:
            Predictions
        """
        if hasattr(model, 'predict_proba'):
            return model.predict_proba(inputs)
        return model.predict(inputs)

    def _predict_onnx(
        self,
        model: Any,
        inputs: np.ndarray,
        **kwargs,
    ) -> np.ndarray:
        """Run ONNX prediction.

        Args:
            model: ONNX runtime session
            inputs: Input array
            **kwargs: Additional arguments

        Returns:
            Predictions
        """
        # Get input name
        input_name = model.get_inputs()[0].name
        result = model.run(None, {input_name: inputs.astype(np.float32)})
        return result[0]

    def _predict_pytorch(
        self,
        model: Any,
        inputs: np.ndarray,
        **kwargs,
    ) -> np.ndarray:
        """Run PyTorch prediction.

        Args:
            model: PyTorch model
            inputs: Input array
            **kwargs: Additional arguments

        Returns:
            Predictions
        """
        import torch

        device = "cuda" if self._device == DeviceType.CUDA else "mps" if self._device == DeviceType.MPS else "cpu"

        with torch.no_grad():
            tensor = torch.from_numpy(inputs).float().to(device)
            output = model(tensor)
            return output.cpu().numpy()

    def _load_model_file(self, model_id: str, path: Path) -> Any:
        """Load model from file.

        Args:
            model_id: Model identifier
            path: Path to model file

        Returns:
            Loaded model
        """
        model_info = MODELS.get(model_id)
        if not model_info:
            raise ValueError(f"Unknown model: {model_id}")

        suffix = path.suffix.lower()

        if suffix == ".pkl":
            with open(path, 'rb') as f:
                return pickle.load(f)

        elif suffix == ".onnx":
            try:
                import onnxruntime as ort
                providers = ['CPUExecutionProvider']
                if self._device == DeviceType.CUDA:
                    providers.insert(0, 'CUDAExecutionProvider')
                return ort.InferenceSession(str(path), providers=providers)
            except ImportError:
                logger.warning("ONNX Runtime not available")
                return None

        elif suffix == ".pt":
            try:
                import torch
                device = "cuda" if self._device == DeviceType.CUDA else "cpu"
                return torch.load(path, map_location=device)
            except ImportError:
                logger.warning("PyTorch not available")
                return None

        else:
            raise ValueError(f"Unknown model format: {suffix}")

    def _evict_if_needed(self):
        """Evict oldest models if at capacity."""
        while len(self._models) >= self.config.max_loaded_models:
            if not self._model_load_order:
                break

            oldest = self._model_load_order.pop(0)
            if oldest in self._models:
                del self._models[oldest]
                gc.collect()
                logger.debug("Evicted model %s", oldest)

    def preload_models(self, model_ids: Optional[List[str]] = None):
        """Preload specified models.

        Args:
            model_ids: Models to preload (uses config if not specified)
        """
        models_to_load = model_ids or self.config.preload_models

        for model_id in models_to_load:
            self.load_model(model_id)

    def get_runtime_stats(self) -> Dict[str, Any]:
        """Get runtime statistics.

        Returns:
            Dictionary with stats
        """
        return {
            "device": self._device.value,
            "gpu_available": self.is_gpu_available(),
            "loaded_models": list(self._models.keys()),
            "loaded_count": len(self._models),
            "max_models": self.config.max_loaded_models,
            "available_models": self.downloader.get_available_models(),
        }

    def cleanup(self):
        """Clean up runtime resources."""
        with self._lock:
            self._models.clear()
            self._model_load_order.clear()
            gc.collect()
            logger.info("ML Runtime cleaned up")


# Singleton instance
_runtime: Optional[MLRuntime] = None


def get_runtime(config: Optional[RuntimeConfig] = None) -> MLRuntime:
    """Get the singleton runtime instance.

    Args:
        config: Optional configuration (only used if creating new instance)

    Returns:
        MLRuntime instance
    """
    global _runtime
    if _runtime is None:
        _runtime = MLRuntime(config)
    return _runtime


def predict(model_id: str, inputs: np.ndarray, **kwargs) -> Optional[np.ndarray]:
    """Convenience function for prediction.

    Args:
        model_id: Model identifier
        inputs: Input data
        **kwargs: Additional arguments

    Returns:
        Predictions or None
    """
    return get_runtime().predict(model_id, inputs, **kwargs)
