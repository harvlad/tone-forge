"""Feature extraction for ML confidence models.

Extracts 50+ features from audio for use in ML classifiers. These features
complement the existing DSP analysis by providing additional statistics
and derived measures that ML models can learn from.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

try:
    import librosa
    LIBROSA_AVAILABLE = True
except ImportError:
    LIBROSA_AVAILABLE = False

# Analysis constants (matching analyzer.py)
_SR = 22050
_N_FFT = 2048
_HOP = 512


@dataclass
class MLFeatureVector:
    """Feature vector for ML confidence models.

    Contains 50+ audio features organized into categories:
    - Spectral features (13): centroid, bandwidth, rolloff, contrast, flatness
    - Temporal features (8): RMS stats, zero-crossing, onset strength
    - Band energies (8): sub-bass through air
    - Harmonic features (6): harmonic ratio, pitch stability
    - Dynamics features (6): crest factor, compression indicators
    - Timbral features (9): MFCC statistics
    """

    # Spectral features
    spectral_centroid_mean: float = 0.0
    spectral_centroid_std: float = 0.0
    spectral_bandwidth_mean: float = 0.0
    spectral_bandwidth_std: float = 0.0
    spectral_rolloff_95_mean: float = 0.0
    spectral_rolloff_85_mean: float = 0.0
    spectral_contrast_mean: float = 0.0
    spectral_contrast_std: float = 0.0
    spectral_flatness_mean: float = 0.0
    spectral_flatness_std: float = 0.0
    spectral_flux_mean: float = 0.0
    spectral_flux_std: float = 0.0
    spectral_skewness: float = 0.0

    # Temporal features
    rms_mean: float = 0.0
    rms_std: float = 0.0
    rms_max: float = 0.0
    rms_min: float = 0.0
    zero_crossing_rate_mean: float = 0.0
    zero_crossing_rate_std: float = 0.0
    onset_strength_mean: float = 0.0
    onset_strength_std: float = 0.0

    # Band energies (log scale, normalized)
    band_sub_bass: float = 0.0      # 20-80 Hz
    band_bass: float = 0.0          # 80-250 Hz
    band_low_mid: float = 0.0       # 250-500 Hz
    band_mid: float = 0.0           # 500-2000 Hz
    band_upper_mid: float = 0.0     # 2000-4000 Hz
    band_treble: float = 0.0        # 4000-6000 Hz
    band_presence: float = 0.0      # 6000-10000 Hz
    band_air: float = 0.0           # 10000-16000 Hz

    # Band ratios (useful for amp family classification)
    bass_to_mid_ratio: float = 0.0
    mid_to_treble_ratio: float = 0.0
    presence_to_mid_ratio: float = 0.0
    low_high_balance: float = 0.0

    # Harmonic features
    harmonic_ratio: float = 0.0      # Harmonic vs percussive energy
    pitch_stability: float = 0.0     # How stable the fundamental is
    harmonic_mean_freq: float = 0.0  # Mean harmonic frequency
    inharmonicity: float = 0.0       # Deviation from perfect harmonics
    odd_even_harmonic_ratio: float = 0.0  # Tube amp characteristic
    harmonic_decay_rate: float = 0.0  # How fast harmonics decay

    # Dynamics features
    crest_factor_db: float = 0.0
    crest_factor_windowed: float = 0.0
    dynamic_range_db: float = 0.0
    compression_ratio_est: float = 0.0
    quiet_loud_ratio: float = 0.0
    attack_time_ms: float = 0.0

    # Timbral features (MFCC-derived)
    mfcc_1_mean: float = 0.0
    mfcc_2_mean: float = 0.0
    mfcc_3_mean: float = 0.0
    mfcc_4_mean: float = 0.0
    mfcc_5_mean: float = 0.0
    mfcc_delta_energy: float = 0.0
    mfcc_std_mean: float = 0.0
    mfcc_spread: float = 0.0
    brightness_index: float = 0.0

    # Duration
    duration_sec: float = 0.0

    def to_array(self) -> np.ndarray:
        """Convert to numpy array for ML model input."""
        return np.array([
            # Spectral (13)
            self.spectral_centroid_mean,
            self.spectral_centroid_std,
            self.spectral_bandwidth_mean,
            self.spectral_bandwidth_std,
            self.spectral_rolloff_95_mean,
            self.spectral_rolloff_85_mean,
            self.spectral_contrast_mean,
            self.spectral_contrast_std,
            self.spectral_flatness_mean,
            self.spectral_flatness_std,
            self.spectral_flux_mean,
            self.spectral_flux_std,
            self.spectral_skewness,
            # Temporal (8)
            self.rms_mean,
            self.rms_std,
            self.rms_max,
            self.rms_min,
            self.zero_crossing_rate_mean,
            self.zero_crossing_rate_std,
            self.onset_strength_mean,
            self.onset_strength_std,
            # Band energies (8)
            self.band_sub_bass,
            self.band_bass,
            self.band_low_mid,
            self.band_mid,
            self.band_upper_mid,
            self.band_treble,
            self.band_presence,
            self.band_air,
            # Band ratios (4)
            self.bass_to_mid_ratio,
            self.mid_to_treble_ratio,
            self.presence_to_mid_ratio,
            self.low_high_balance,
            # Harmonic (6)
            self.harmonic_ratio,
            self.pitch_stability,
            self.harmonic_mean_freq,
            self.inharmonicity,
            self.odd_even_harmonic_ratio,
            self.harmonic_decay_rate,
            # Dynamics (6)
            self.crest_factor_db,
            self.crest_factor_windowed,
            self.dynamic_range_db,
            self.compression_ratio_est,
            self.quiet_loud_ratio,
            self.attack_time_ms,
            # Timbral (9)
            self.mfcc_1_mean,
            self.mfcc_2_mean,
            self.mfcc_3_mean,
            self.mfcc_4_mean,
            self.mfcc_5_mean,
            self.mfcc_delta_energy,
            self.mfcc_std_mean,
            self.mfcc_spread,
            self.brightness_index,
            # Duration (1)
            self.duration_sec,
        ], dtype=np.float32)

    @classmethod
    def feature_names(cls) -> List[str]:
        """Return list of feature names in same order as to_array()."""
        return [
            # Spectral
            "spectral_centroid_mean", "spectral_centroid_std",
            "spectral_bandwidth_mean", "spectral_bandwidth_std",
            "spectral_rolloff_95_mean", "spectral_rolloff_85_mean",
            "spectral_contrast_mean", "spectral_contrast_std",
            "spectral_flatness_mean", "spectral_flatness_std",
            "spectral_flux_mean", "spectral_flux_std",
            "spectral_skewness",
            # Temporal
            "rms_mean", "rms_std", "rms_max", "rms_min",
            "zero_crossing_rate_mean", "zero_crossing_rate_std",
            "onset_strength_mean", "onset_strength_std",
            # Band energies
            "band_sub_bass", "band_bass", "band_low_mid", "band_mid",
            "band_upper_mid", "band_treble", "band_presence", "band_air",
            # Band ratios
            "bass_to_mid_ratio", "mid_to_treble_ratio",
            "presence_to_mid_ratio", "low_high_balance",
            # Harmonic
            "harmonic_ratio", "pitch_stability", "harmonic_mean_freq",
            "inharmonicity", "odd_even_harmonic_ratio", "harmonic_decay_rate",
            # Dynamics
            "crest_factor_db", "crest_factor_windowed", "dynamic_range_db",
            "compression_ratio_est", "quiet_loud_ratio", "attack_time_ms",
            # Timbral
            "mfcc_1_mean", "mfcc_2_mean", "mfcc_3_mean", "mfcc_4_mean",
            "mfcc_5_mean", "mfcc_delta_energy", "mfcc_std_mean",
            "mfcc_spread", "brightness_index",
            # Duration
            "duration_sec",
        ]

    @classmethod
    def num_features(cls) -> int:
        """Return total number of features."""
        return 55


def extract_ml_features(
    y: np.ndarray,
    sr: int = _SR,
    existing_features: Optional[Dict] = None,
) -> MLFeatureVector:
    """Extract ML features from audio.

    Args:
        y: Audio signal (mono, normalized)
        sr: Sample rate
        existing_features: Optional dict of pre-computed features from analyzer

    Returns:
        MLFeatureVector containing 55 features
    """
    if not LIBROSA_AVAILABLE:
        raise ImportError("librosa is required for feature extraction")

    if len(y) < _N_FFT:
        y = np.pad(y, (0, _N_FFT - len(y)))

    duration_sec = len(y) / sr

    # Compute spectrogram once
    spec = np.abs(librosa.stft(y, n_fft=_N_FFT, hop_length=_HOP))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=_N_FFT)

    # Spectral features
    centroid = librosa.feature.spectral_centroid(S=spec, sr=sr)[0]
    bandwidth = librosa.feature.spectral_bandwidth(S=spec, sr=sr)[0]
    rolloff_95 = librosa.feature.spectral_rolloff(S=spec, sr=sr, roll_percent=0.95)[0]
    rolloff_85 = librosa.feature.spectral_rolloff(S=spec, sr=sr, roll_percent=0.85)[0]
    contrast = librosa.feature.spectral_contrast(S=spec, sr=sr)
    flatness = librosa.feature.spectral_flatness(S=spec)[0]

    # Spectral flux
    flux = np.sqrt(np.sum(np.diff(spec, axis=1) ** 2, axis=0))

    # Spectral skewness (shape of spectrum)
    spec_mean = np.mean(spec, axis=1)
    spec_mean_norm = spec_mean / (np.sum(spec_mean) + 1e-9)
    freq_norm = freqs / (np.max(freqs) + 1e-9)
    mean_freq = np.sum(freq_norm * spec_mean_norm)
    skewness = np.sum(((freq_norm - mean_freq) ** 3) * spec_mean_norm)

    # Temporal features
    rms = librosa.feature.rms(y=y, frame_length=_N_FFT, hop_length=_HOP)[0]
    zcr = librosa.feature.zero_crossing_rate(y, frame_length=_N_FFT, hop_length=_HOP)[0]
    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=_HOP)

    # Band energies
    def band_energy(lo: float, hi: float) -> float:
        mask = (freqs >= lo) & (freqs < hi)
        if not np.any(mask):
            return 0.0
        energy = np.mean(spec[mask, :])
        return float(np.log1p(energy))  # Log scale for better ML

    band_sub = band_energy(20, 80)
    band_bass = band_energy(80, 250)
    band_low_mid = band_energy(250, 500)
    band_mid = band_energy(500, 2000)
    band_upper_mid = band_energy(2000, 4000)
    band_treble = band_energy(4000, 6000)
    band_presence = band_energy(6000, 10000)
    band_air = band_energy(10000, 16000)

    # Normalize band energies relative to total
    total_band = sum([band_sub, band_bass, band_low_mid, band_mid,
                      band_upper_mid, band_treble, band_presence, band_air]) + 1e-9

    # Band ratios
    bass_total = band_bass + band_low_mid
    mid_total = band_mid
    treble_total = band_upper_mid + band_treble
    presence_total = band_presence

    bass_to_mid = bass_total / (mid_total + 1e-9)
    mid_to_treble = mid_total / (treble_total + 1e-9)
    presence_to_mid = presence_total / (mid_total + 1e-9)
    low_high = (band_bass + band_low_mid) / (band_treble + band_presence + 1e-9)

    # Harmonic features
    harmonic, percussive = librosa.effects.hpss(y)
    harm_energy = np.sum(harmonic ** 2)
    perc_energy = np.sum(percussive ** 2) + 1e-9
    harmonic_ratio = float(harm_energy / (harm_energy + perc_energy))

    # Pitch stability using pyin
    try:
        f0, voiced_flag, _ = librosa.pyin(
            y, fmin=librosa.note_to_hz('E2'),
            fmax=librosa.note_to_hz('E6'),
            sr=sr
        )
        voiced_f0 = f0[voiced_flag]
        if len(voiced_f0) > 1:
            pitch_stability = 1.0 - float(np.std(voiced_f0) / (np.mean(voiced_f0) + 1e-9))
            pitch_stability = max(0.0, min(1.0, pitch_stability))
            harmonic_mean_freq = float(np.mean(voiced_f0))
        else:
            pitch_stability = 0.5
            harmonic_mean_freq = 440.0
    except Exception:
        pitch_stability = 0.5
        harmonic_mean_freq = 440.0

    # Inharmonicity (deviation from perfect harmonic series)
    # Estimated from spectral peaks vs expected harmonics
    inharmonicity = 0.0  # Placeholder - would need peak detection

    # Odd/even harmonic ratio (tube amp saturation creates odd harmonics)
    # This is a simplified estimate based on spectral symmetry
    if harmonic_mean_freq > 0:
        f0_bin = int(harmonic_mean_freq * _N_FFT / sr)
        odd_energy = 0.0
        even_energy = 0.0
        for h in range(1, 8):
            bin_idx = min(f0_bin * h, len(spec_mean) - 1)
            if h % 2 == 1:
                odd_energy += spec_mean[bin_idx]
            else:
                even_energy += spec_mean[bin_idx]
        odd_even_ratio = float(odd_energy / (even_energy + 1e-9))
    else:
        odd_even_ratio = 1.0

    # Harmonic decay rate (how fast harmonics fall off)
    harmonic_decay = 0.0  # Placeholder

    # Dynamics features
    peak = float(np.max(np.abs(y))) + 1e-12
    rms_full = float(np.sqrt(np.mean(y ** 2))) + 1e-12
    crest_db = 20.0 * np.log10(peak / rms_full)

    # Windowed crest factor (50ms windows)
    win = max(int(0.05 * sr), 64)
    n_win = len(y) // win
    if n_win >= 2:
        chunks = y[: n_win * win].reshape(n_win, win)
        chunk_peaks = np.max(np.abs(chunks), axis=1)
        chunk_rms = np.sqrt(np.mean(chunks ** 2, axis=1)) + 1e-12
        mask = chunk_peaks > 0.05 * peak
        if np.any(mask):
            crest_windowed = float(np.median(20 * np.log10(chunk_peaks[mask] / chunk_rms[mask])))
        else:
            crest_windowed = crest_db
    else:
        crest_windowed = crest_db

    # Dynamic range
    rms_sorted = np.sort(rms)
    if len(rms_sorted) >= 5:
        p95 = float(rms_sorted[int(len(rms_sorted) * 0.95)])
        p5 = float(rms_sorted[int(len(rms_sorted) * 0.05)]) + 1e-9
        dynamic_range = 20 * np.log10(p95 / p5)
        p20 = float(rms_sorted[len(rms_sorted) // 5])
        p80 = float(rms_sorted[len(rms_sorted) * 4 // 5]) + 1e-9
        quiet_loud = p20 / p80
    else:
        dynamic_range = 0.0
        quiet_loud = 0.0

    # Compression estimate (lower dynamic range = more compressed)
    compression_est = float(np.clip((25 - dynamic_range) / 17.0, 0.0, 1.0))

    # Attack time estimate
    onsets = librosa.onset.onset_detect(y=y, sr=sr, hop_length=_HOP, units="samples")
    if len(onsets) > 0:
        # Measure time to reach 90% of peak after first onset
        first_onset = onsets[0]
        window_end = min(first_onset + int(0.1 * sr), len(y))
        window = np.abs(y[first_onset:window_end])
        if len(window) > 0:
            peak_90 = 0.9 * np.max(window)
            above_thresh = np.where(window >= peak_90)[0]
            if len(above_thresh) > 0:
                attack_samples = above_thresh[0]
                attack_time_ms = (attack_samples / sr) * 1000
            else:
                attack_time_ms = 50.0
        else:
            attack_time_ms = 50.0
    else:
        attack_time_ms = 50.0

    # MFCC features
    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13, hop_length=_HOP)
    mfcc_means = np.mean(mfcc, axis=1)
    mfcc_stds = np.std(mfcc, axis=1)

    # MFCC delta energy
    mfcc_delta = librosa.feature.delta(mfcc)
    mfcc_delta_energy = float(np.mean(np.abs(mfcc_delta)))

    # MFCC spread (variance across coefficients)
    mfcc_spread = float(np.std(mfcc_means[1:]))  # Skip first (energy)

    # Brightness index (ratio of high-frequency energy)
    brightness = float((band_treble + band_presence + band_air) / total_band)

    return MLFeatureVector(
        # Spectral
        spectral_centroid_mean=float(np.mean(centroid)),
        spectral_centroid_std=float(np.std(centroid)),
        spectral_bandwidth_mean=float(np.mean(bandwidth)),
        spectral_bandwidth_std=float(np.std(bandwidth)),
        spectral_rolloff_95_mean=float(np.mean(rolloff_95)),
        spectral_rolloff_85_mean=float(np.mean(rolloff_85)),
        spectral_contrast_mean=float(np.mean(contrast)),
        spectral_contrast_std=float(np.std(contrast)),
        spectral_flatness_mean=float(np.mean(flatness)),
        spectral_flatness_std=float(np.std(flatness)),
        spectral_flux_mean=float(np.mean(flux)) if len(flux) > 0 else 0.0,
        spectral_flux_std=float(np.std(flux)) if len(flux) > 0 else 0.0,
        spectral_skewness=float(skewness),
        # Temporal
        rms_mean=float(np.mean(rms)),
        rms_std=float(np.std(rms)),
        rms_max=float(np.max(rms)),
        rms_min=float(np.min(rms)),
        zero_crossing_rate_mean=float(np.mean(zcr)),
        zero_crossing_rate_std=float(np.std(zcr)),
        onset_strength_mean=float(np.mean(onset_env)),
        onset_strength_std=float(np.std(onset_env)),
        # Band energies
        band_sub_bass=band_sub,
        band_bass=band_bass,
        band_low_mid=band_low_mid,
        band_mid=band_mid,
        band_upper_mid=band_upper_mid,
        band_treble=band_treble,
        band_presence=band_presence,
        band_air=band_air,
        # Band ratios
        bass_to_mid_ratio=float(np.clip(bass_to_mid, 0, 10)),
        mid_to_treble_ratio=float(np.clip(mid_to_treble, 0, 10)),
        presence_to_mid_ratio=float(np.clip(presence_to_mid, 0, 10)),
        low_high_balance=float(np.clip(low_high, 0, 10)),
        # Harmonic
        harmonic_ratio=harmonic_ratio,
        pitch_stability=pitch_stability,
        harmonic_mean_freq=harmonic_mean_freq,
        inharmonicity=inharmonicity,
        odd_even_harmonic_ratio=float(np.clip(odd_even_ratio, 0, 10)),
        harmonic_decay_rate=harmonic_decay,
        # Dynamics
        crest_factor_db=float(crest_db),
        crest_factor_windowed=float(crest_windowed),
        dynamic_range_db=float(dynamic_range),
        compression_ratio_est=float(compression_est),
        quiet_loud_ratio=float(quiet_loud),
        attack_time_ms=float(np.clip(attack_time_ms, 0, 500)),
        # Timbral
        mfcc_1_mean=float(mfcc_means[1]) if len(mfcc_means) > 1 else 0.0,
        mfcc_2_mean=float(mfcc_means[2]) if len(mfcc_means) > 2 else 0.0,
        mfcc_3_mean=float(mfcc_means[3]) if len(mfcc_means) > 3 else 0.0,
        mfcc_4_mean=float(mfcc_means[4]) if len(mfcc_means) > 4 else 0.0,
        mfcc_5_mean=float(mfcc_means[5]) if len(mfcc_means) > 5 else 0.0,
        mfcc_delta_energy=mfcc_delta_energy,
        mfcc_std_mean=float(np.mean(mfcc_stds)),
        mfcc_spread=mfcc_spread,
        brightness_index=brightness,
        # Duration
        duration_sec=duration_sec,
    )
