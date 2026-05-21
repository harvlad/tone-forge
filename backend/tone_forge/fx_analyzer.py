"""
FX Chain Analysis - Forensic DSP for detecting effects from audio.

Detects:
- Chorus/Flanger/Phaser (modulation effects)
- Delay (echo patterns, tempo sync)
- Reverb (decay time, type)
- Compression (dynamic range, attack/release)
- Saturation/Distortion (harmonic content)
- Stereo imaging (width, correlation)
"""

import numpy as np
import logging
from dataclasses import dataclass, field
from typing import List, Tuple, Optional, Dict
from scipy import signal

logger = logging.getLogger(__name__)


@dataclass
class ModulationFX:
    """Detected modulation effect (chorus, flanger, phaser)."""
    type: str  # 'chorus', 'flanger', 'phaser', 'vibrato', 'tremolo'
    rate_hz: float  # Modulation rate
    depth: float  # 0-1 modulation depth
    confidence: float


@dataclass
class DelayFX:
    """Detected delay effect."""
    time_ms: float  # Delay time in milliseconds
    feedback: float  # 0-1 feedback amount
    sync_note: Optional[str]  # e.g., '1/8', '1/4 dot'
    is_stereo: bool
    confidence: float


@dataclass
class ReverbFX:
    """Detected reverb effect."""
    type: str  # 'room', 'hall', 'plate', 'spring', 'ambient'
    decay_sec: float  # RT60 decay time
    pre_delay_ms: float
    wet_dry_mix: float  # 0-1
    confidence: float


@dataclass
class CompressionFX:
    """Detected compression characteristics."""
    ratio: float  # e.g., 4.0 for 4:1
    attack_ms: float
    release_ms: float
    threshold_db: float
    is_heavy: bool  # Brick-wall limiting
    confidence: float


@dataclass
class SaturationFX:
    """Detected saturation/distortion."""
    type: str  # 'tape', 'tube', 'transistor', 'digital', 'fuzz'
    amount: float  # 0-1
    harmonic_character: str  # 'even', 'odd', 'mixed'
    confidence: float


@dataclass
class StereoImage:
    """Stereo imaging analysis."""
    width: float  # 0-2 (0=mono, 1=normal, 2=super wide)
    correlation: float  # -1 to 1 (phase relationship)
    technique: str  # 'mono', 'stereo', 'wide_chorus', 'haas', 'mid_side'


@dataclass
class FXChainAnalysis:
    """Complete FX chain analysis result."""
    modulation: Optional[ModulationFX] = None
    delay: Optional[DelayFX] = None
    reverb: Optional[ReverbFX] = None
    compression: Optional[CompressionFX] = None
    saturation: Optional[SaturationFX] = None
    stereo: Optional[StereoImage] = None
    eq_character: str = 'neutral'  # 'bright', 'dark', 'mid_scoop', 'neutral'
    suggested_chain: List[str] = field(default_factory=list)


def analyze_fx_chain(
    y: np.ndarray,
    sr: int,
    y_stereo: Optional[np.ndarray] = None,
) -> FXChainAnalysis:
    """
    Analyze audio for FX chain characteristics.

    Args:
        y: Mono audio signal
        sr: Sample rate
        y_stereo: Optional stereo audio (2, N) for stereo analysis

    Returns:
        FXChainAnalysis with detected effects
    """
    result = FXChainAnalysis()

    # Analyze each effect type
    result.modulation = detect_modulation(y, sr)
    result.delay = detect_delay(y, sr)
    result.reverb = detect_reverb(y, sr)
    result.compression = detect_compression(y, sr)
    result.saturation = detect_saturation(y, sr)

    if y_stereo is not None and y_stereo.ndim == 2:
        result.stereo = analyze_stereo_image(y_stereo, sr)
    else:
        result.stereo = StereoImage(width=1.0, correlation=1.0, technique='mono')

    result.eq_character = analyze_eq_character(y, sr)

    # Build suggested chain
    result.suggested_chain = build_suggested_chain(result)

    return result


def detect_modulation(y: np.ndarray, sr: int) -> Optional[ModulationFX]:
    """
    Detect modulation effects (chorus, flanger, vibrato, tremolo).

    Uses spectral flux analysis to find periodic modulation patterns.
    """
    import librosa

    # Compute spectral centroid over time
    centroid = librosa.feature.spectral_centroid(y=y, sr=sr)[0]

    # Compute amplitude envelope
    rms = librosa.feature.rms(y=y)[0]

    # Look for periodic modulation in centroid (pitch modulation)
    pitch_mod_rate, pitch_mod_depth = _find_modulation_rate(centroid, sr)

    # Look for periodic modulation in amplitude (tremolo)
    amp_mod_rate, amp_mod_depth = _find_modulation_rate(rms, sr)

    # Determine modulation type
    if pitch_mod_rate > 0 and pitch_mod_depth > 0.05:
        # Pitch modulation detected
        if pitch_mod_rate < 1.0:
            mod_type = 'chorus'
        elif pitch_mod_rate < 5.0:
            mod_type = 'chorus'
        else:
            mod_type = 'vibrato'

        return ModulationFX(
            type=mod_type,
            rate_hz=pitch_mod_rate,
            depth=min(pitch_mod_depth, 1.0),
            confidence=0.6 + pitch_mod_depth * 0.3,
        )

    elif amp_mod_rate > 0 and amp_mod_depth > 0.1:
        # Amplitude modulation (tremolo)
        return ModulationFX(
            type='tremolo',
            rate_hz=amp_mod_rate,
            depth=min(amp_mod_depth, 1.0),
            confidence=0.5 + amp_mod_depth * 0.3,
        )

    return None


def detect_delay(y: np.ndarray, sr: int, tempo_bpm: float = 120.0) -> Optional[DelayFX]:
    """
    Detect delay/echo effects using autocorrelation.

    Looks for repeated patterns at rhythmic intervals.
    """
    # Compute autocorrelation
    # Limit to reasonable delay range (50ms to 2000ms)
    min_lag = int(0.05 * sr)
    max_lag = int(2.0 * sr)

    # Use normalized autocorrelation
    y_normalized = y / (np.max(np.abs(y)) + 1e-6)
    autocorr = np.correlate(y_normalized[:sr*3], y_normalized[:sr*3], mode='full')
    autocorr = autocorr[len(autocorr)//2:]  # Take positive lags only

    # Find peaks in autocorrelation
    if len(autocorr) > max_lag:
        autocorr_segment = autocorr[min_lag:max_lag]
    else:
        return None

    # Normalize
    autocorr_norm = autocorr_segment / (autocorr_segment[0] + 1e-6)

    # Find peaks
    peaks, properties = signal.find_peaks(autocorr_norm, height=0.1, distance=int(0.03 * sr))

    if len(peaks) == 0:
        return None

    # Get the strongest peak
    peak_heights = properties['peak_heights']
    best_peak_idx = np.argmax(peak_heights)
    best_peak = peaks[best_peak_idx]
    peak_strength = peak_heights[best_peak_idx]

    # Convert to time
    delay_samples = best_peak + min_lag
    delay_ms = (delay_samples / sr) * 1000

    # Check if delay syncs to tempo
    beat_ms = 60000 / tempo_bpm
    sync_note = None

    sync_divisions = {
        '1/16': 0.25,
        '1/8': 0.5,
        '1/8 dot': 0.75,
        '1/4': 1.0,
        '1/4 dot': 1.5,
        '1/2': 2.0,
    }

    for note, multiplier in sync_divisions.items():
        expected_ms = beat_ms * multiplier
        if abs(delay_ms - expected_ms) < expected_ms * 0.1:  # 10% tolerance
            sync_note = note
            break

    # Estimate feedback from subsequent peaks
    feedback = 0.3  # Default
    if len(peaks) > 1:
        # Ratio of second peak to first peak
        second_height = peak_heights[1] if len(peak_heights) > 1 else 0
        feedback = min(second_height / (peak_strength + 1e-6), 0.9)

    if peak_strength > 0.15:  # Significant delay detected
        return DelayFX(
            time_ms=delay_ms,
            feedback=feedback,
            sync_note=sync_note,
            is_stereo=False,  # Can't determine from mono
            confidence=min(peak_strength + 0.2, 1.0),
        )

    return None


def detect_reverb(y: np.ndarray, sr: int) -> Optional[ReverbFX]:
    """
    Detect reverb characteristics from audio.

    Analyzes decay envelope and early reflection patterns.
    """
    import librosa

    # Compute energy envelope
    hop_length = 512
    rms = librosa.feature.rms(y=y, hop_length=hop_length)[0]

    # Find transients/note onsets
    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop_length)
    onset_frames = librosa.onset.onset_detect(onset_envelope=onset_env, sr=sr, hop_length=hop_length)

    if len(onset_frames) < 2:
        return None

    # Analyze decay after transients
    decay_times = []
    frame_time = hop_length / sr

    for onset in onset_frames[:10]:  # Analyze first 10 onsets
        if onset + 50 >= len(rms):
            continue

        # Get energy after onset
        onset_energy = rms[onset]
        decay_segment = rms[onset:onset + 50]

        if onset_energy < 0.01:
            continue

        # Find time to decay to -60dB (RT60)
        threshold = onset_energy * 0.001  # -60dB
        decay_idx = np.where(decay_segment < threshold)[0]

        if len(decay_idx) > 0:
            rt60_frames = decay_idx[0]
            rt60_sec = rt60_frames * frame_time
            decay_times.append(rt60_sec)

    if not decay_times:
        return None

    avg_decay = np.median(decay_times)

    # Classify reverb type based on decay time
    if avg_decay < 0.3:
        reverb_type = 'room'
    elif avg_decay < 0.8:
        reverb_type = 'room'
    elif avg_decay < 1.5:
        reverb_type = 'plate'
    elif avg_decay < 3.0:
        reverb_type = 'hall'
    else:
        reverb_type = 'ambient'

    # Estimate wet/dry mix from tail energy
    tail_energy = np.mean(rms[-len(rms)//4:])
    total_energy = np.mean(rms)
    wet_dry = min(tail_energy / (total_energy + 1e-6) * 2, 1.0)

    return ReverbFX(
        type=reverb_type,
        decay_sec=avg_decay,
        pre_delay_ms=20.0,  # Hard to detect accurately
        wet_dry_mix=wet_dry,
        confidence=0.5 + min(len(decay_times) / 10, 0.3),
    )


def detect_compression(y: np.ndarray, sr: int) -> Optional[CompressionFX]:
    """
    Detect compression characteristics from dynamic range analysis.
    """
    import librosa

    # Compute RMS in frames
    hop_length = 512
    rms = librosa.feature.rms(y=y, hop_length=hop_length)[0]

    if len(rms) < 10:
        return None

    # Dynamic range analysis
    rms_db = 20 * np.log10(rms + 1e-6)
    dynamic_range = np.percentile(rms_db, 95) - np.percentile(rms_db, 5)

    # Crest factor (peak to RMS ratio)
    peak = np.max(np.abs(y))
    rms_total = np.sqrt(np.mean(y**2))
    crest_factor_db = 20 * np.log10(peak / (rms_total + 1e-6))

    # Analyze transient preservation
    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop_length)
    transient_ratio = np.max(onset_env) / (np.mean(onset_env) + 1e-6)

    # Estimate compression parameters
    if dynamic_range < 6:
        # Heavily compressed
        ratio = 8.0
        is_heavy = True
    elif dynamic_range < 12:
        ratio = 4.0
        is_heavy = False
    elif dynamic_range < 18:
        ratio = 2.0
        is_heavy = False
    else:
        # Minimal compression
        return None

    # Estimate attack from transient preservation
    if transient_ratio > 5:
        attack_ms = 30.0  # Slow attack, preserving transients
    elif transient_ratio > 2:
        attack_ms = 10.0
    else:
        attack_ms = 1.0  # Fast attack

    return CompressionFX(
        ratio=ratio,
        attack_ms=attack_ms,
        release_ms=100.0,  # Hard to detect
        threshold_db=-20.0,  # Estimated
        is_heavy=is_heavy,
        confidence=0.6 if dynamic_range < 15 else 0.4,
    )


def detect_saturation(y: np.ndarray, sr: int) -> Optional[SaturationFX]:
    """
    Detect saturation/distortion from harmonic analysis.
    """
    import librosa

    # Compute harmonic content
    # Look at ratio of harmonics to fundamental

    # Get spectral content
    S = np.abs(librosa.stft(y))
    freqs = librosa.fft_frequencies(sr=sr)

    # Compute harmonic energy distribution
    S_mean = np.mean(S, axis=1)

    # Split into frequency bands
    low_idx = np.where(freqs < 500)[0]
    mid_idx = np.where((freqs >= 500) & (freqs < 3000))[0]
    high_idx = np.where((freqs >= 3000) & (freqs < 8000))[0]
    ultra_high_idx = np.where(freqs >= 8000)[0]

    low_energy = np.sum(S_mean[low_idx]) if len(low_idx) > 0 else 0
    mid_energy = np.sum(S_mean[mid_idx]) if len(mid_idx) > 0 else 0
    high_energy = np.sum(S_mean[high_idx]) if len(high_idx) > 0 else 0
    ultra_high_energy = np.sum(S_mean[ultra_high_idx]) if len(ultra_high_idx) > 0 else 0

    total_energy = low_energy + mid_energy + high_energy + ultra_high_energy + 1e-6

    # High harmonic content suggests saturation
    harmonic_ratio = (high_energy + ultra_high_energy) / total_energy

    if harmonic_ratio < 0.1:
        return None  # Minimal saturation

    # Determine saturation type
    if ultra_high_energy / (high_energy + 1e-6) > 0.5:
        # Lots of ultra-high harmonics = digital/harsh
        sat_type = 'digital'
        character = 'odd'
    elif mid_energy / total_energy > 0.4:
        # Mid-focused = tube warmth
        sat_type = 'tube'
        character = 'even'
    else:
        sat_type = 'tape'
        character = 'mixed'

    amount = min(harmonic_ratio * 3, 1.0)

    return SaturationFX(
        type=sat_type,
        amount=amount,
        harmonic_character=character,
        confidence=0.5 + amount * 0.3,
    )


def analyze_stereo_image(y_stereo: np.ndarray, sr: int) -> StereoImage:
    """
    Analyze stereo width and imaging technique.
    """
    if y_stereo.ndim != 2 or y_stereo.shape[0] != 2:
        return StereoImage(width=1.0, correlation=1.0, technique='mono')

    left = y_stereo[0]
    right = y_stereo[1]

    # Mid-side analysis
    mid = (left + right) / 2
    side = (left - right) / 2

    mid_energy = np.sum(mid**2)
    side_energy = np.sum(side**2)

    # Width: ratio of side to total energy
    total_energy = mid_energy + side_energy + 1e-6
    width = 2 * side_energy / total_energy  # 0 = mono, 1 = normal, 2 = sides only

    # Correlation
    correlation = np.corrcoef(left, right)[0, 1]

    # Determine technique
    if width < 0.1:
        technique = 'mono'
    elif width > 1.5:
        if correlation < 0.3:
            technique = 'haas'  # Wide with low correlation = Haas delay
        else:
            technique = 'wide_chorus'
    elif width > 0.8:
        technique = 'stereo'
    else:
        technique = 'mid_side'

    return StereoImage(
        width=width,
        correlation=correlation,
        technique=technique,
    )


def analyze_eq_character(y: np.ndarray, sr: int) -> str:
    """
    Analyze overall EQ character of the sound.
    """
    import librosa

    # Compute spectral centroid (brightness indicator)
    centroid = np.mean(librosa.feature.spectral_centroid(y=y, sr=sr))

    # Compute spectral rolloff
    rolloff = np.mean(librosa.feature.spectral_rolloff(y=y, sr=sr))

    # Compute spectral bandwidth
    bandwidth = np.mean(librosa.feature.spectral_bandwidth(y=y, sr=sr))

    # Classify based on centroid
    if centroid > 3000:
        return 'bright'
    elif centroid < 1500:
        return 'dark'
    elif bandwidth > 2000:
        return 'mid_scoop'
    else:
        return 'neutral'


def build_suggested_chain(analysis: FXChainAnalysis) -> List[str]:
    """
    Build a suggested signal chain based on detected FX.
    """
    chain = []

    # Compression first (dynamics)
    if analysis.compression and analysis.compression.confidence > 0.4:
        comp = analysis.compression
        if comp.is_heavy:
            chain.append(f"Compressor (heavy, {comp.ratio:.0f}:1)")
        else:
            chain.append(f"Compressor ({comp.ratio:.0f}:1, {comp.attack_ms:.0f}ms attack)")

    # Saturation
    if analysis.saturation and analysis.saturation.confidence > 0.4:
        sat = analysis.saturation
        chain.append(f"{sat.type.title()} Saturation ({sat.amount*100:.0f}%)")

    # Modulation
    if analysis.modulation and analysis.modulation.confidence > 0.4:
        mod = analysis.modulation
        chain.append(f"{mod.type.title()} ({mod.rate_hz:.1f}Hz, {mod.depth*100:.0f}% depth)")

    # Delay
    if analysis.delay and analysis.delay.confidence > 0.4:
        dly = analysis.delay
        if dly.sync_note:
            chain.append(f"Delay ({dly.sync_note}, {dly.feedback*100:.0f}% feedback)")
        else:
            chain.append(f"Delay ({dly.time_ms:.0f}ms, {dly.feedback*100:.0f}% feedback)")

    # Reverb
    if analysis.reverb and analysis.reverb.confidence > 0.4:
        rev = analysis.reverb
        chain.append(f"{rev.type.title()} Reverb ({rev.decay_sec:.1f}s decay)")

    # Stereo
    if analysis.stereo and analysis.stereo.width > 1.2:
        chain.append(f"Stereo Widener ({analysis.stereo.technique})")

    return chain


def _find_modulation_rate(signal_1d: np.ndarray, sr: int, frame_rate: float = 43.0) -> Tuple[float, float]:
    """
    Find periodic modulation rate in a 1D signal (like spectral centroid).

    Returns (rate_hz, depth)
    """
    if len(signal_1d) < 64:
        return (0.0, 0.0)

    # Remove DC and normalize
    sig = signal_1d - np.mean(signal_1d)
    sig = sig / (np.std(sig) + 1e-6)

    # FFT to find dominant frequency
    fft = np.abs(np.fft.rfft(sig))
    freqs = np.fft.rfftfreq(len(sig), d=1/frame_rate)

    # Look for modulation in typical range (0.5 Hz to 10 Hz)
    valid_idx = np.where((freqs > 0.5) & (freqs < 10))[0]

    if len(valid_idx) == 0:
        return (0.0, 0.0)

    fft_valid = fft[valid_idx]
    freqs_valid = freqs[valid_idx]

    # Find peak
    peak_idx = np.argmax(fft_valid)
    peak_freq = freqs_valid[peak_idx]
    peak_strength = fft_valid[peak_idx]

    # Depth is relative to overall signal variation
    depth = peak_strength / (np.sum(fft_valid) + 1e-6)

    return (peak_freq, depth)
