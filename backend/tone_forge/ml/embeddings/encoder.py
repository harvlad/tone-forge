"""Audio encoder using CLAP or OpenL3 for semantic embeddings.

CLAP (Contrastive Language-Audio Pretraining) is preferred for its
ability to capture semantic audio properties. Falls back to OpenL3
or a simple spectral embedding when CLAP isn't available.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union, Literal
import logging

import numpy as np

logger = logging.getLogger(__name__)

# Try to import CLAP
try:
    import laion_clap
    CLAP_AVAILABLE = True
except ImportError:
    CLAP_AVAILABLE = False
    laion_clap = None

# Try to import OpenL3
try:
    import openl3
    OPENL3_AVAILABLE = True
except ImportError:
    OPENL3_AVAILABLE = False
    openl3 = None

# Try to import librosa for fallback
try:
    import librosa
    LIBROSA_AVAILABLE = True
except ImportError:
    LIBROSA_AVAILABLE = False


# Embedding dimensions for each encoder
EMBEDDING_DIMS = {
    "clap": 512,
    "openl3": 512,
    "spectral": 128,
}


@dataclass
class AudioEmbedding:
    """Container for an audio embedding with metadata."""
    embedding: np.ndarray
    encoder_type: str
    duration_sec: float
    sample_rate: int

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "embedding": self.embedding.tolist(),
            "encoder_type": self.encoder_type,
            "duration_sec": self.duration_sec,
            "sample_rate": self.sample_rate,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AudioEmbedding":
        """Create from dictionary."""
        return cls(
            embedding=np.array(d["embedding"], dtype=np.float32),
            encoder_type=d["encoder_type"],
            duration_sec=d["duration_sec"],
            sample_rate=d["sample_rate"],
        )


class AudioEmbedder:
    """Generate semantic embeddings from audio.

    Supports multiple encoder backends:
    - clap: LAION CLAP model (512-dim, semantic)
    - openl3: OpenL3 model (512-dim, environmental)
    - spectral: Simple spectral features (128-dim, fallback)

    The embedder automatically selects the best available backend.
    """

    def __init__(
        self,
        encoder_type: Optional[Literal["clap", "openl3", "spectral"]] = None,
        model_dir: Optional[Path] = None,
    ):
        """Initialize the embedder.

        Args:
            encoder_type: Encoder to use. If None, auto-selects best available.
            model_dir: Directory containing model weights.
        """
        self.model_dir = model_dir
        self._model = None
        self._ready = False

        # Auto-select encoder if not specified
        if encoder_type is None:
            if CLAP_AVAILABLE:
                encoder_type = "clap"
            elif OPENL3_AVAILABLE:
                encoder_type = "openl3"
            else:
                encoder_type = "spectral"

        self.encoder_type = encoder_type
        self.embedding_dim = EMBEDDING_DIMS.get(encoder_type, 512)

        logger.info(f"Using audio encoder: {encoder_type}")

    def _ensure_loaded(self) -> None:
        """Ensure the model is loaded."""
        if self._ready:
            return

        if self.encoder_type == "clap":
            self._load_clap()
        elif self.encoder_type == "openl3":
            self._load_openl3()
        else:
            # Spectral doesn't need loading
            self._ready = True

    def _load_clap(self) -> None:
        """Load CLAP model."""
        if not CLAP_AVAILABLE:
            raise ImportError("CLAP not available. Install with: pip install laion-clap")

        try:
            # Use smaller model for efficiency
            self._model = laion_clap.CLAP_Module(enable_fusion=False)
            self._model.load_ckpt()
            self._ready = True
            logger.info("CLAP model loaded")
        except Exception as e:
            logger.error(f"Failed to load CLAP: {e}")
            # Fall back to spectral
            self.encoder_type = "spectral"
            self.embedding_dim = EMBEDDING_DIMS["spectral"]
            self._ready = True

    def _load_openl3(self) -> None:
        """Load OpenL3 model."""
        if not OPENL3_AVAILABLE:
            raise ImportError("OpenL3 not available. Install with: pip install openl3")

        try:
            # OpenL3 loads models on-demand, just verify it works
            self._ready = True
            logger.info("OpenL3 ready")
        except Exception as e:
            logger.error(f"Failed to initialize OpenL3: {e}")
            self.encoder_type = "spectral"
            self.embedding_dim = EMBEDDING_DIMS["spectral"]
            self._ready = True

    def encode(
        self,
        audio: np.ndarray,
        sr: int = 22050,
    ) -> AudioEmbedding:
        """Generate embedding from audio.

        Args:
            audio: Audio signal (mono or stereo)
            sr: Sample rate

        Returns:
            AudioEmbedding containing the embedding vector
        """
        self._ensure_loaded()

        # Ensure mono
        if len(audio.shape) > 1:
            audio = np.mean(audio, axis=1)

        duration_sec = len(audio) / sr

        if self.encoder_type == "clap":
            embedding = self._encode_clap(audio, sr)
        elif self.encoder_type == "openl3":
            embedding = self._encode_openl3(audio, sr)
        else:
            embedding = self._encode_spectral(audio, sr)

        return AudioEmbedding(
            embedding=embedding,
            encoder_type=self.encoder_type,
            duration_sec=duration_sec,
            sample_rate=sr,
        )

    def _encode_clap(self, audio: np.ndarray, sr: int) -> np.ndarray:
        """Encode using CLAP."""
        # CLAP expects 48kHz audio
        target_sr = 48000
        if sr != target_sr:
            if LIBROSA_AVAILABLE:
                audio = librosa.resample(audio, orig_sr=sr, target_sr=target_sr)
            else:
                # Simple resampling
                ratio = target_sr / sr
                new_len = int(len(audio) * ratio)
                indices = np.linspace(0, len(audio) - 1, new_len)
                audio = np.interp(indices, np.arange(len(audio)), audio)

        # Ensure float32 and normalized
        audio = audio.astype(np.float32)
        if np.max(np.abs(audio)) > 0:
            audio = audio / np.max(np.abs(audio))

        # Get embedding
        embedding = self._model.get_audio_embedding_from_data(
            [audio],
            use_tensor=False
        )
        return embedding[0].astype(np.float32)

    def _encode_openl3(self, audio: np.ndarray, sr: int) -> np.ndarray:
        """Encode using OpenL3."""
        # OpenL3 expects 48kHz
        target_sr = 48000
        if sr != target_sr:
            if LIBROSA_AVAILABLE:
                audio = librosa.resample(audio, orig_sr=sr, target_sr=target_sr)
                sr = target_sr

        # Get embedding (returns time series of embeddings)
        emb, ts = openl3.get_audio_embedding(
            audio,
            sr,
            embedding_size=512,
            hop_size=1.0,  # 1 second hop for efficiency
        )

        # Average across time
        return emb.mean(axis=0).astype(np.float32)

    def _encode_spectral(self, audio: np.ndarray, sr: int) -> np.ndarray:
        """Fallback: spectral feature embedding.

        Creates a simple but effective embedding from spectral statistics.
        Not as semantically rich as CLAP/OpenL3 but works without ML models.
        """
        if not LIBROSA_AVAILABLE:
            raise ImportError("librosa required for spectral embedding")

        # Compute spectral features
        n_fft = 2048
        hop_length = 512

        spec = np.abs(librosa.stft(audio, n_fft=n_fft, hop_length=hop_length))

        # Mel spectrogram statistics (64 features)
        mel = librosa.feature.melspectrogram(S=spec**2, sr=sr, n_mels=64)
        mel_db = librosa.power_to_db(mel, ref=np.max)
        mel_mean = np.mean(mel_db, axis=1)  # 64 features

        # Spectral statistics (20 features)
        centroid = librosa.feature.spectral_centroid(S=spec, sr=sr)[0]
        bandwidth = librosa.feature.spectral_bandwidth(S=spec, sr=sr)[0]
        rolloff = librosa.feature.spectral_rolloff(S=spec, sr=sr)[0]
        flatness = librosa.feature.spectral_flatness(S=spec)[0]

        spectral_stats = np.array([
            np.mean(centroid), np.std(centroid),
            np.percentile(centroid, 25), np.percentile(centroid, 75),
            np.mean(bandwidth), np.std(bandwidth),
            np.percentile(bandwidth, 25), np.percentile(bandwidth, 75),
            np.mean(rolloff), np.std(rolloff),
            np.percentile(rolloff, 25), np.percentile(rolloff, 75),
            np.mean(flatness), np.std(flatness),
            np.percentile(flatness, 25), np.percentile(flatness, 75),
            np.max(centroid), np.min(centroid),
            np.max(flatness), np.min(flatness),
        ])

        # MFCCs (32 features)
        mfcc = librosa.feature.mfcc(y=audio, sr=sr, n_mfcc=16, hop_length=hop_length)
        mfcc_mean = np.mean(mfcc, axis=1)  # 16
        mfcc_std = np.std(mfcc, axis=1)    # 16
        mfcc_features = np.concatenate([mfcc_mean, mfcc_std])

        # Chroma (12 features)
        chroma = librosa.feature.chroma_stft(S=spec, sr=sr)
        chroma_mean = np.mean(chroma, axis=1)

        # Combine all features (64 + 20 + 32 + 12 = 128)
        embedding = np.concatenate([
            mel_mean,
            spectral_stats,
            mfcc_features,
            chroma_mean,
        ])

        # Normalize
        norm = np.linalg.norm(embedding)
        if norm > 0:
            embedding = embedding / norm

        return embedding.astype(np.float32)

    def encode_file(self, path: Union[str, Path], sr: int = 22050) -> AudioEmbedding:
        """Encode audio from file.

        Args:
            path: Path to audio file
            sr: Target sample rate

        Returns:
            AudioEmbedding
        """
        if not LIBROSA_AVAILABLE:
            raise ImportError("librosa required for file loading")

        audio, file_sr = librosa.load(str(path), sr=sr, mono=True)
        return self.encode(audio, sr)


# Global embedder instance
_embedder: Optional[AudioEmbedder] = None


def get_embedder() -> AudioEmbedder:
    """Get the global embedder instance."""
    global _embedder
    if _embedder is None:
        _embedder = AudioEmbedder()
    return _embedder


def is_encoder_ready() -> bool:
    """Check if an encoder is available."""
    return CLAP_AVAILABLE or OPENL3_AVAILABLE or LIBROSA_AVAILABLE
