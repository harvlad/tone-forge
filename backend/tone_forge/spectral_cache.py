"""Spectral feature cache for avoiding redundant FFT computations.

Computing STFT is expensive (~500ms for 3-minute audio). Many analysis
functions compute STFT independently, leading to redundant work.

This module provides a cache that computes features once and reuses them.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class SpectralFeatureCache:
    """Cache for expensive spectral features.

    Usage:
        cache = SpectralFeatureCache(audio, sr)
        stft = cache.stft  # Computed on first access
        centroid = cache.spectral_centroid  # Uses cached STFT

    All features are computed lazily on first access and cached.
    """

    audio: np.ndarray
    sr: int
    n_fft: int = 2048
    hop_length: int = 512

    # Cached values (computed lazily)
    _stft: Optional[np.ndarray] = field(default=None, repr=False)
    _stft_mag: Optional[np.ndarray] = field(default=None, repr=False)
    _freqs: Optional[np.ndarray] = field(default=None, repr=False)
    _chroma: Optional[np.ndarray] = field(default=None, repr=False)
    _tempo: Optional[float] = field(default=None, repr=False)
    _beat_frames: Optional[np.ndarray] = field(default=None, repr=False)
    _spectral_centroid: Optional[np.ndarray] = field(default=None, repr=False)
    _spectral_bandwidth: Optional[np.ndarray] = field(default=None, repr=False)
    _spectral_rolloff: Optional[np.ndarray] = field(default=None, repr=False)
    _spectral_flatness: Optional[np.ndarray] = field(default=None, repr=False)
    _rms: Optional[np.ndarray] = field(default=None, repr=False)
    _onset_env: Optional[np.ndarray] = field(default=None, repr=False)
    _onset_frames: Optional[np.ndarray] = field(default=None, repr=False)

    @property
    def stft(self) -> np.ndarray:
        """Complex STFT (computed once)."""
        if self._stft is None:
            import librosa
            self._stft = librosa.stft(
                self.audio, n_fft=self.n_fft, hop_length=self.hop_length
            )
            logger.debug(f"Computed STFT: shape={self._stft.shape}")
        return self._stft

    @property
    def stft_magnitude(self) -> np.ndarray:
        """Magnitude spectrogram (abs of STFT)."""
        if self._stft_mag is None:
            self._stft_mag = np.abs(self.stft)
        return self._stft_mag

    @property
    def frequencies(self) -> np.ndarray:
        """FFT frequency bins."""
        if self._freqs is None:
            import librosa
            self._freqs = librosa.fft_frequencies(sr=self.sr, n_fft=self.n_fft)
        return self._freqs

    @property
    def chroma(self) -> np.ndarray:
        """Chromagram (12-bin pitch class representation)."""
        if self._chroma is None:
            import librosa
            self._chroma = librosa.feature.chroma_cqt(
                y=self.audio, sr=self.sr, hop_length=self.hop_length
            )
            logger.debug(f"Computed chroma: shape={self._chroma.shape}")
        return self._chroma

    @property
    def tempo(self) -> float:
        """Estimated tempo in BPM."""
        if self._tempo is None:
            self._compute_tempo()
        return self._tempo

    @property
    def beat_frames(self) -> np.ndarray:
        """Beat frame indices."""
        if self._beat_frames is None:
            self._compute_tempo()
        return self._beat_frames

    def _compute_tempo(self) -> None:
        """Compute tempo and beat frames together."""
        import librosa
        tempo, beat_frames = librosa.beat.beat_track(
            y=self.audio, sr=self.sr, hop_length=self.hop_length
        )
        # Handle array vs scalar tempo
        if hasattr(tempo, '__iter__'):
            self._tempo = float(tempo[0]) if len(tempo) > 0 else 120.0
        else:
            self._tempo = float(tempo) if tempo > 0 else 120.0
        self._beat_frames = beat_frames
        logger.debug(f"Computed tempo: {self._tempo:.1f} BPM")

    @property
    def spectral_centroid(self) -> np.ndarray:
        """Spectral centroid (brightness indicator)."""
        if self._spectral_centroid is None:
            import librosa
            self._spectral_centroid = librosa.feature.spectral_centroid(
                S=self.stft_magnitude, sr=self.sr
            )[0]
        return self._spectral_centroid

    @property
    def spectral_bandwidth(self) -> np.ndarray:
        """Spectral bandwidth."""
        if self._spectral_bandwidth is None:
            import librosa
            self._spectral_bandwidth = librosa.feature.spectral_bandwidth(
                S=self.stft_magnitude, sr=self.sr
            )[0]
        return self._spectral_bandwidth

    @property
    def spectral_rolloff(self) -> np.ndarray:
        """Spectral rolloff (85%)."""
        if self._spectral_rolloff is None:
            import librosa
            self._spectral_rolloff = librosa.feature.spectral_rolloff(
                S=self.stft_magnitude, sr=self.sr, roll_percent=0.85
            )[0]
        return self._spectral_rolloff

    @property
    def spectral_flatness(self) -> np.ndarray:
        """Spectral flatness (noise vs tone indicator)."""
        if self._spectral_flatness is None:
            import librosa
            self._spectral_flatness = librosa.feature.spectral_flatness(
                S=self.stft_magnitude
            )[0]
        return self._spectral_flatness

    @property
    def rms(self) -> np.ndarray:
        """Root mean square energy."""
        if self._rms is None:
            import librosa
            self._rms = librosa.feature.rms(
                y=self.audio, hop_length=self.hop_length
            )[0]
        return self._rms

    @property
    def onset_envelope(self) -> np.ndarray:
        """Onset strength envelope."""
        if self._onset_env is None:
            import librosa
            self._onset_env = librosa.onset.onset_strength(
                y=self.audio, sr=self.sr, hop_length=self.hop_length
            )
        return self._onset_env

    @property
    def onset_frames(self) -> np.ndarray:
        """Detected onset frame indices."""
        if self._onset_frames is None:
            import librosa
            self._onset_frames = librosa.onset.onset_detect(
                onset_envelope=self.onset_envelope,
                sr=self.sr,
                hop_length=self.hop_length
            )
        return self._onset_frames

    def get_energy_by_band(self) -> Dict[str, float]:
        """Get energy distribution across frequency bands.

        Returns:
            Dict with keys: sub_bass, bass, low_mid, mid, high_mid, high
        """
        spec = self.stft_magnitude
        freqs = self.frequencies

        bands = {
            'sub_bass': (20, 60),
            'bass': (60, 250),
            'low_mid': (250, 500),
            'mid': (500, 2000),
            'high_mid': (2000, 4000),
            'high': (4000, 20000),
        }

        energies = {}
        total_energy = spec.sum()

        for band_name, (low, high) in bands.items():
            mask = (freqs >= low) & (freqs < high)
            band_energy = spec[mask, :].sum() if mask.any() else 0
            energies[band_name] = band_energy / total_energy if total_energy > 0 else 0

        return energies

    def get_polyphony_estimate(self, threshold: float = 0.3) -> Tuple[float, float]:
        """Estimate polyphony from chroma features.

        Args:
            threshold: Chroma activation threshold

        Returns:
            Tuple of (average_polyphony, max_polyphony)
        """
        active_per_frame = (self.chroma > threshold).sum(axis=0)
        return float(active_per_frame.mean()), float(active_per_frame.max())

    def clear(self) -> None:
        """Clear all cached values (free memory)."""
        self._stft = None
        self._stft_mag = None
        self._freqs = None
        self._chroma = None
        self._tempo = None
        self._beat_frames = None
        self._spectral_centroid = None
        self._spectral_bandwidth = None
        self._spectral_rolloff = None
        self._spectral_flatness = None
        self._rms = None
        self._onset_env = None
        self._onset_frames = None


def detect_genre_cached(cache: SpectralFeatureCache) -> str:
    """Detect genre using cached spectral features.

    This is a drop-in replacement for detect_genre_from_audio that uses
    the spectral cache to avoid redundant computation.
    """
    energies = cache.get_energy_by_band()

    # Calculate low/high ratios
    low_ratio = energies['sub_bass'] + energies['bass'] + energies['low_mid']
    high_ratio = energies['high_mid'] + energies['high']

    tempo = cache.tempo

    # Synthwave: heavy low end, rolled off highs, moderate tempo
    is_synthwave = (
        low_ratio > 0.4 and      # Strong bass
        high_ratio < 0.3 and     # Not too bright
        60 < tempo < 130         # Typical synthwave tempo range
    )

    if is_synthwave:
        logger.info(
            f"Detected synthwave characteristics "
            f"(low={low_ratio:.2f}, high={high_ratio:.2f}, tempo={tempo:.0f})"
        )
        return 'synthwave'

    return 'default'


def detect_extraction_method_cached(
    cache: SpectralFeatureCache,
    stem_type: str = 'other'
) -> Dict[str, Any]:
    """Detect optimal extraction method using cached features.

    This is a drop-in replacement for detect_optimal_extraction_method.
    """
    energies = cache.get_energy_by_band()
    low_ratio = energies['sub_bass'] + energies['bass']
    sub_bass_ratio = energies['sub_bass']

    # Check if this is bass-like audio
    is_bass_audio = (
        low_ratio > 0.6 or
        sub_bass_ratio > 0.3 or
        stem_type == 'bass'
    )

    # Estimate polyphony
    avg_polyphony, max_polyphony = cache.get_polyphony_estimate()

    # Determine method
    if is_bass_audio and avg_polyphony < 6.0:
        method = 'monophonic'
        reason = f"Bass-like audio (low_ratio={low_ratio:.2f}, polyphony={avg_polyphony:.1f})"
    elif is_bass_audio and avg_polyphony >= 6.0:
        method = 'polyphonic'
        reason = f"Polyphonic bass detected (polyphony={avg_polyphony:.1f})"
    elif avg_polyphony < 2.0:
        method = 'monophonic'
        reason = f"Monophonic audio detected (avg_polyphony={avg_polyphony:.1f})"
    else:
        method = 'polyphonic'
        reason = f"Polyphonic audio detected (avg_polyphony={avg_polyphony:.1f})"

    logger.info(f"Auto-detection: {method} ({reason})")

    return {
        'method': method,
        'reason': reason,
        'is_bass': is_bass_audio,
        'polyphony_estimate': avg_polyphony,
    }
