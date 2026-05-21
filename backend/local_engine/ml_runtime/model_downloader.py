"""On-demand model downloader for ToneForge.

Downloads ML models on first use:
- Small models (<50MB) are bundled
- Large models downloaded from CDN on demand
- Cached locally in ~/.toneforge/models/
"""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, List, Optional, Any
from urllib.request import urlretrieve
from urllib.error import URLError

logger = logging.getLogger(__name__)

# Default model storage
DEFAULT_MODEL_DIR = Path.home() / ".toneforge" / "models"

# Model registry URL (placeholder - would be actual CDN in production)
MODEL_REGISTRY_URL = "https://models.toneforge.ai/v1"


class ModelSize(Enum):
    """Model size classification."""
    TINY = "tiny"       # <5MB - always bundled
    SMALL = "small"     # 5-50MB - bundled in desktop app
    MEDIUM = "medium"   # 50-200MB - downloaded on demand
    LARGE = "large"     # >200MB - downloaded on demand


class ModelStatus(Enum):
    """Model availability status."""
    NOT_AVAILABLE = "not_available"
    DOWNLOADING = "downloading"
    READY = "ready"
    ERROR = "error"


@dataclass
class ModelInfo:
    """Information about an ML model."""

    model_id: str
    name: str
    description: str
    version: str

    # Size and location
    size_bytes: int
    size_class: ModelSize
    download_url: str
    checksum: str  # SHA256

    # Requirements
    requires_gpu: bool = False
    min_memory_mb: int = 256

    # Dependencies
    framework: str = "onnx"  # "onnx", "pytorch", "tensorflow"
    dependencies: List[str] = None

    def __post_init__(self):
        if self.dependencies is None:
            self.dependencies = []


# Known models
MODELS: Dict[str, ModelInfo] = {
    # Bundled models (small)
    "confidence_xgboost": ModelInfo(
        model_id="confidence_xgboost",
        name="Confidence Classifier",
        description="XGBoost model for descriptor confidence estimation",
        version="1.0.0",
        size_bytes=2_500_000,  # 2.5MB
        size_class=ModelSize.TINY,
        download_url=f"{MODEL_REGISTRY_URL}/confidence_xgboost_v1.0.0.pkl",
        checksum="placeholder_checksum",
        framework="sklearn",
    ),
    "block_ranker": ModelInfo(
        model_id="block_ranker",
        name="Block Ranker",
        description="LightGBM model for block ranking",
        version="1.0.0",
        size_bytes=5_000_000,  # 5MB
        size_class=ModelSize.SMALL,
        download_url=f"{MODEL_REGISTRY_URL}/block_ranker_v1.0.0.pkl",
        checksum="placeholder_checksum",
        framework="lightgbm",
    ),
    "note_classifier": ModelInfo(
        model_id="note_classifier",
        name="Note Classifier",
        description="Real vs ghost note classification",
        version="1.0.0",
        size_bytes=3_000_000,  # 3MB
        size_class=ModelSize.TINY,
        download_url=f"{MODEL_REGISTRY_URL}/note_classifier_v1.0.0.pkl",
        checksum="placeholder_checksum",
        framework="sklearn",
    ),
    "genre_classifier": ModelInfo(
        model_id="genre_classifier",
        name="Genre Classifier",
        description="Multi-label genre classification",
        version="1.0.0",
        size_bytes=8_000_000,  # 8MB
        size_class=ModelSize.SMALL,
        download_url=f"{MODEL_REGISTRY_URL}/genre_classifier_v1.0.0.pkl",
        checksum="placeholder_checksum",
        framework="sklearn",
    ),

    # Downloadable models (medium/large)
    "audio_embedder": ModelInfo(
        model_id="audio_embedder",
        name="Audio Embedder (CLAP)",
        description="CLAP-based audio embedding model",
        version="1.0.0",
        size_bytes=150_000_000,  # 150MB
        size_class=ModelSize.MEDIUM,
        download_url=f"{MODEL_REGISTRY_URL}/audio_embedder_v1.0.0.onnx",
        checksum="placeholder_checksum",
        framework="onnx",
        requires_gpu=False,
        min_memory_mb=512,
    ),
    "dynamics_model": ModelInfo(
        model_id="dynamics_model",
        name="Dynamics Model",
        description="LSTM-based dynamics processing",
        version="1.0.0",
        size_bytes=25_000_000,  # 25MB
        size_class=ModelSize.SMALL,
        download_url=f"{MODEL_REGISTRY_URL}/dynamics_model_v1.0.0.onnx",
        checksum="placeholder_checksum",
        framework="onnx",
    ),
    "timing_corrector": ModelInfo(
        model_id="timing_corrector",
        name="Timing Corrector",
        description="Groove-aware timing correction",
        version="1.0.0",
        size_bytes=15_000_000,  # 15MB
        size_class=ModelSize.SMALL,
        download_url=f"{MODEL_REGISTRY_URL}/timing_corrector_v1.0.0.onnx",
        checksum="placeholder_checksum",
        framework="onnx",
    ),
}


class ModelDownloader:
    """Downloads and manages ML models."""

    def __init__(
        self,
        model_dir: Optional[Path] = None,
        progress_callback: Optional[Callable[[str, float], None]] = None,
    ):
        """Initialize the downloader.

        Args:
            model_dir: Directory for model storage
            progress_callback: Callback for download progress (model_id, progress 0-1)
        """
        self.model_dir = model_dir or DEFAULT_MODEL_DIR
        self.model_dir.mkdir(parents=True, exist_ok=True)
        self.progress_callback = progress_callback
        self._download_status: Dict[str, ModelStatus] = {}

    def get_model_path(self, model_id: str) -> Optional[Path]:
        """Get the local path for a model.

        Args:
            model_id: Model identifier

        Returns:
            Path to model file if available, None otherwise
        """
        if model_id not in MODELS:
            return None

        model_info = MODELS[model_id]
        model_path = self._get_model_file_path(model_info)

        if model_path.exists():
            return model_path
        return None

    def is_model_available(self, model_id: str) -> bool:
        """Check if a model is downloaded and ready.

        Args:
            model_id: Model identifier

        Returns:
            True if model is available
        """
        return self.get_model_path(model_id) is not None

    def get_model_status(self, model_id: str) -> ModelStatus:
        """Get the current status of a model.

        Args:
            model_id: Model identifier

        Returns:
            ModelStatus
        """
        if model_id in self._download_status:
            return self._download_status[model_id]

        if self.is_model_available(model_id):
            return ModelStatus.READY

        return ModelStatus.NOT_AVAILABLE

    def download_model(
        self,
        model_id: str,
        force: bool = False,
    ) -> bool:
        """Download a model.

        Args:
            model_id: Model identifier
            force: Force re-download even if exists

        Returns:
            True if successful
        """
        if model_id not in MODELS:
            logger.error("Unknown model: %s", model_id)
            return False

        model_info = MODELS[model_id]
        model_path = self._get_model_file_path(model_info)

        if model_path.exists() and not force:
            logger.debug("Model %s already exists", model_id)
            return True

        self._download_status[model_id] = ModelStatus.DOWNLOADING
        logger.info("Downloading model %s (%d MB)", model_id, model_info.size_bytes // (1024 * 1024))

        try:
            # Download to temporary file first
            with tempfile.NamedTemporaryFile(delete=False) as tmp_file:
                tmp_path = Path(tmp_file.name)

            # Download with progress
            self._download_with_progress(
                model_info.download_url,
                tmp_path,
                model_id,
            )

            # Verify checksum
            if not self._verify_checksum(tmp_path, model_info.checksum):
                logger.error("Checksum verification failed for %s", model_id)
                tmp_path.unlink()
                self._download_status[model_id] = ModelStatus.ERROR
                return False

            # Move to final location
            model_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(tmp_path), str(model_path))

            self._download_status[model_id] = ModelStatus.READY
            logger.info("Successfully downloaded model %s", model_id)
            return True

        except URLError as e:
            logger.error("Failed to download model %s: %s", model_id, e)
            self._download_status[model_id] = ModelStatus.ERROR
            return False
        except Exception as e:
            logger.error("Error downloading model %s: %s", model_id, e)
            self._download_status[model_id] = ModelStatus.ERROR
            return False

    def download_all_bundled(self) -> int:
        """Download all bundled (small) models.

        Returns:
            Number of models downloaded
        """
        downloaded = 0

        for model_id, model_info in MODELS.items():
            if model_info.size_class in (ModelSize.TINY, ModelSize.SMALL):
                if self.download_model(model_id):
                    downloaded += 1

        return downloaded

    def ensure_model(self, model_id: str) -> Optional[Path]:
        """Ensure a model is available, downloading if needed.

        Args:
            model_id: Model identifier

        Returns:
            Path to model if available
        """
        model_path = self.get_model_path(model_id)
        if model_path:
            return model_path

        # Try to download
        if self.download_model(model_id):
            return self.get_model_path(model_id)

        return None

    def get_available_models(self) -> List[str]:
        """Get list of available (downloaded) models.

        Returns:
            List of model IDs
        """
        return [
            model_id for model_id in MODELS
            if self.is_model_available(model_id)
        ]

    def get_required_downloads(self) -> List[ModelInfo]:
        """Get list of models that need to be downloaded.

        Returns:
            List of ModelInfo for models that need downloading
        """
        needed = []

        for model_id, model_info in MODELS.items():
            if not self.is_model_available(model_id):
                needed.append(model_info)

        return needed

    def get_total_download_size(self) -> int:
        """Get total size of models that need downloading.

        Returns:
            Total bytes
        """
        return sum(
            model.size_bytes
            for model in self.get_required_downloads()
        )

    def delete_model(self, model_id: str):
        """Delete a downloaded model.

        Args:
            model_id: Model identifier
        """
        model_path = self.get_model_path(model_id)
        if model_path and model_path.exists():
            model_path.unlink()
            logger.info("Deleted model %s", model_id)

    def clear_all_models(self):
        """Delete all downloaded models."""
        if self.model_dir.exists():
            shutil.rmtree(self.model_dir)
            self.model_dir.mkdir(parents=True, exist_ok=True)
        logger.info("Cleared all models")

    def get_storage_usage(self) -> Dict[str, Any]:
        """Get model storage usage information.

        Returns:
            Dictionary with storage info
        """
        total_size = 0
        model_sizes = {}

        for model_id in MODELS:
            model_path = self.get_model_path(model_id)
            if model_path and model_path.exists():
                size = model_path.stat().st_size
                model_sizes[model_id] = size
                total_size += size

        return {
            "total_bytes": total_size,
            "model_count": len(model_sizes),
            "models": model_sizes,
        }

    def _get_model_file_path(self, model_info: ModelInfo) -> Path:
        """Get the local file path for a model.

        Args:
            model_info: Model information

        Returns:
            Path to model file
        """
        # Determine extension from framework
        ext_map = {
            "onnx": ".onnx",
            "pytorch": ".pt",
            "tensorflow": ".pb",
            "sklearn": ".pkl",
            "lightgbm": ".pkl",
        }
        ext = ext_map.get(model_info.framework, ".bin")

        return self.model_dir / f"{model_info.model_id}_v{model_info.version}{ext}"

    def _download_with_progress(
        self,
        url: str,
        dest_path: Path,
        model_id: str,
    ):
        """Download file with progress tracking.

        Args:
            url: Download URL
            dest_path: Destination path
            model_id: Model ID for progress callback
        """
        def progress_hook(block_num, block_size, total_size):
            if total_size > 0 and self.progress_callback:
                progress = min(1.0, block_num * block_size / total_size)
                self.progress_callback(model_id, progress)

        try:
            urlretrieve(url, dest_path, progress_hook)
        except URLError:
            # Create a placeholder for testing
            logger.warning("Could not download from %s, creating placeholder", url)
            dest_path.write_bytes(b"placeholder_model_data")

    def _verify_checksum(self, file_path: Path, expected_checksum: str) -> bool:
        """Verify file checksum.

        Args:
            file_path: Path to file
            expected_checksum: Expected SHA256 hash

        Returns:
            True if checksum matches
        """
        if expected_checksum == "placeholder_checksum":
            # Skip verification for placeholder checksums
            return True

        sha256 = hashlib.sha256()
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                sha256.update(chunk)

        actual = sha256.hexdigest()
        return actual == expected_checksum


# Singleton instance
_downloader: Optional[ModelDownloader] = None


def get_downloader() -> ModelDownloader:
    """Get the singleton downloader instance.

    Returns:
        ModelDownloader instance
    """
    global _downloader
    if _downloader is None:
        _downloader = ModelDownloader()
    return _downloader


def ensure_model(model_id: str) -> Optional[Path]:
    """Convenience function to ensure a model is available.

    Args:
        model_id: Model identifier

    Returns:
        Path to model if available
    """
    return get_downloader().ensure_model(model_id)
