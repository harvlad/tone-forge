"""Synth tone analyzer.

Analyzes audio to detect synthesizer characteristics:
- Oscillator type (saw, square, sine, triangle, noise)
- Filter characteristics (cutoff, resonance, type)
- Envelope (ADSR)
- Modulation (LFO rate, depth, target)
- Effects (chorus, phaser, reverb)

This allows Tone Forge to suggest synth patches for recreating
electronic/synth sounds.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from typing import Literal, Optional
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

# Try to import librosa, but allow graceful failure
try:
    import librosa
    _LIBROSA_AVAILABLE = True
except ImportError:
    _LIBROSA_AVAILABLE = False


OscType = Literal["saw", "square", "sine", "triangle", "noise", "complex", "unknown"]
FilterType = Literal["lowpass", "highpass", "bandpass", "notch", "unknown"]


@dataclass
class SynthOscillator:
    """Oscillator characteristics."""
    type: OscType = "unknown"
    detune: float = 0.0  # cents
    num_voices: int = 1  # 1 = mono, 2+ = unison/supersaw
    sub_osc: bool = False
    pulse_width: float = 0.5  # for square waves


@dataclass
class SynthFilter:
    """Filter characteristics."""
    type: FilterType = "lowpass"
    cutoff_hz: float = 20000.0
    cutoff_normalized: float = 1.0  # 0-1 range
    resonance: float = 0.0  # 0-1
    envelope_amount: float = 0.0  # how much envelope affects cutoff


@dataclass
class SynthEnvelope:
    """ADSR envelope characteristics."""
    attack_ms: float = 10.0
    decay_ms: float = 100.0
    sustain: float = 0.8  # 0-1
    release_ms: float = 200.0


@dataclass
class SynthLFO:
    """LFO modulation characteristics."""
    rate_hz: float = 0.0
    depth: float = 0.0  # 0-1
    target: str = "none"  # "pitch", "filter", "amplitude", "pan"
    waveform: str = "sine"


@dataclass
class SynthDescriptor:
    """Complete synth tone descriptor."""
    oscillator: SynthOscillator
    filter: SynthFilter
    amp_envelope: SynthEnvelope
    filter_envelope: Optional[SynthEnvelope] = None
    lfo: Optional[SynthLFO] = None

    # Additional characteristics
    brightness: float = 0.5  # 0-1, overall tonal brightness
    movement: float = 0.0  # 0-1, how much the tone changes over time
    stereo_width: float = 0.0  # 0-1

    # Effects detected
    has_chorus: bool = False
    has_phaser: bool = False
    has_reverb: bool = False
    has_delay: bool = False

    # Source info
    duration_sec: float = 0.0
    sample_rate: int = 44100

    def to_dict(self) -> dict:
        d = asdict(self)
        if d["filter_envelope"] is None:
            del d["filter_envelope"]
        if d["lfo"] is None:
            del d["lfo"]
        # Convert numpy types to Python natives for JSON serialization
        return _convert_numpy(d)


def _convert_numpy(obj):
    """Recursively convert numpy types to Python natives."""
    if isinstance(obj, dict):
        return {k: _convert_numpy(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_convert_numpy(v) for v in obj]
    elif isinstance(obj, np.integer):
        return int(obj)
    elif isinstance(obj, np.floating):
        return float(obj)
    elif isinstance(obj, np.bool_):
        return bool(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    return obj


def analyze_synth(
    audio_path: str | Path,
    sr: int = 44100,
) -> SynthDescriptor:
    """Analyze an audio file to detect synth characteristics.

    Args:
        audio_path: Path to the audio file.
        sr: Sample rate for analysis.

    Returns:
        SynthDescriptor with detected characteristics.
    """
    if not _LIBROSA_AVAILABLE:
        raise ImportError("librosa is required for synth analysis")

    path = Path(audio_path)
    if not path.exists():
        raise FileNotFoundError(f"Audio file not found: {path}")

    logger.info(f"Analyzing synth tone: {path.name}")

    # Load audio
    y, sr = librosa.load(str(path), sr=sr, mono=True)
    duration = len(y) / sr

    # Analyze oscillator type
    osc = _analyze_oscillator(y, sr)

    # Analyze filter
    filt = _analyze_filter(y, sr)

    # Analyze amplitude envelope
    amp_env = _analyze_amplitude_envelope(y, sr)

    # Analyze LFO/modulation
    lfo = _analyze_lfo(y, sr)

    # Analyze overall characteristics
    brightness = _analyze_brightness(y, sr)
    movement = _analyze_movement(y, sr)
    stereo_width = 0.0  # Would need stereo signal

    # Detect effects
    has_chorus = _detect_chorus(y, sr)
    has_phaser = _detect_phaser(y, sr)
    has_reverb = _detect_reverb(y, sr)
    has_delay = _detect_delay(y, sr)

    return SynthDescriptor(
        oscillator=osc,
        filter=filt,
        amp_envelope=amp_env,
        lfo=lfo if lfo.rate_hz > 0 else None,
        brightness=brightness,
        movement=movement,
        stereo_width=stereo_width,
        has_chorus=has_chorus,
        has_phaser=has_phaser,
        has_reverb=has_reverb,
        has_delay=has_delay,
        duration_sec=duration,
        sample_rate=sr,
    )


def _analyze_oscillator(y: np.ndarray, sr: int) -> SynthOscillator:
    """Detect oscillator type from spectral characteristics."""
    # Compute spectrum
    S = np.abs(librosa.stft(y))
    freqs = librosa.fft_frequencies(sr=sr)

    # Average spectrum over time
    avg_spectrum = np.mean(S, axis=1)

    # Check spectral flatness first - true noise has high flatness
    flatness = librosa.feature.spectral_flatness(y=y)
    avg_flatness = float(np.mean(flatness))

    # Find fundamental frequency
    f0 = _estimate_f0(y, sr)

    # Only classify as noise if truly noisy (high flatness) and no clear pitch
    if (f0 is None or f0 < 20) and avg_flatness > 0.5:
        return SynthOscillator(type="noise")

    # For polyphonic content without clear f0, analyze overall spectrum shape
    if f0 is None or f0 < 20:
        # Use spectral centroid to guess oscillator type
        centroid = librosa.feature.spectral_centroid(y=y, sr=sr)
        avg_centroid = float(np.mean(centroid))

        # High centroid = bright = likely saw or square
        # Low centroid = mellow = likely sine or triangle
        if avg_centroid > 3000:
            osc_type = "saw"  # Bright, harmonically rich
        elif avg_centroid > 1500:
            osc_type = "square"  # Medium brightness
        elif avg_flatness < 0.1:
            osc_type = "sine"  # Very pure tone
        else:
            osc_type = "complex"  # Polyphonic/layered

        return SynthOscillator(
            type=osc_type,
            detune=0.0,
            num_voices=1,
            sub_osc=False,
            pulse_width=0.5,
        )

    # Analyze harmonic content with detected f0
    harmonic_ratios = _analyze_harmonics(avg_spectrum, freqs, f0)

    # Classify based on harmonic pattern
    osc_type = _classify_oscillator(harmonic_ratios)

    # Detect unison/detuning
    detune, num_voices = _detect_unison(y, sr, f0)

    # Detect sub oscillator (octave below)
    sub_osc = _detect_sub_oscillator(avg_spectrum, freqs, f0)

    return SynthOscillator(
        type=osc_type,
        detune=detune,
        num_voices=num_voices,
        sub_osc=sub_osc,
        pulse_width=0.5 if osc_type == "square" else 0.5,
    )


def _estimate_f0(y: np.ndarray, sr: int) -> Optional[float]:
    """Estimate fundamental frequency."""
    try:
        f0, voiced_flag, voiced_probs = librosa.pyin(
            y, fmin=20, fmax=2000, sr=sr
        )
        # Get median of voiced frames
        voiced_f0 = f0[voiced_flag]
        if len(voiced_f0) > 0:
            return float(np.median(voiced_f0))
    except Exception:
        pass
    return None


def _analyze_harmonics(
    spectrum: np.ndarray,
    freqs: np.ndarray,
    f0: float,
) -> list[float]:
    """Get relative amplitudes of first N harmonics."""
    harmonics = []
    for n in range(1, 9):  # First 8 harmonics
        harm_freq = f0 * n
        # Find closest bin
        idx = np.argmin(np.abs(freqs - harm_freq))
        if idx < len(spectrum):
            harmonics.append(spectrum[idx])
        else:
            harmonics.append(0.0)

    # Normalize
    max_val = max(harmonics) if harmonics else 1.0
    if max_val > 0:
        harmonics = [h / max_val for h in harmonics]

    return harmonics


def _classify_oscillator(harmonic_ratios: list[float]) -> OscType:
    """Classify oscillator type based on harmonic content."""
    if len(harmonic_ratios) < 4:
        return "unknown"

    # Saw wave: all harmonics present, decreasing as 1/n
    # Square wave: only odd harmonics (1, 3, 5, 7...)
    # Sine wave: only fundamental
    # Triangle: only odd harmonics, decreasing as 1/n^2

    # Check if mostly fundamental (sine)
    if harmonic_ratios[0] > 0.9 and sum(harmonic_ratios[1:]) < 0.3:
        return "sine"

    # Check odd vs even harmonics
    odd_sum = sum(harmonic_ratios[0::2])  # 1st, 3rd, 5th, 7th
    even_sum = sum(harmonic_ratios[1::2])  # 2nd, 4th, 6th, 8th

    if even_sum < 0.15 * odd_sum:
        # Only odd harmonics
        # Distinguish square from triangle by rolloff rate
        if len(harmonic_ratios) >= 5:
            ratio_3_to_1 = harmonic_ratios[2] / max(harmonic_ratios[0], 0.01)
            if ratio_3_to_1 > 0.2:
                return "square"
            else:
                return "triangle"
        return "square"

    # All harmonics present - likely saw
    if odd_sum > 0.5 and even_sum > 0.2:
        return "saw"

    return "complex"


def _detect_unison(y: np.ndarray, sr: int, f0: float) -> tuple[float, int]:
    """Detect unison voices and detuning amount."""
    # Look for frequency spreading around fundamental
    S = np.abs(librosa.stft(y, n_fft=4096))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=4096)

    # Find fundamental bin
    f0_idx = np.argmin(np.abs(freqs - f0))

    # Look at bins around fundamental
    window = 10
    if f0_idx > window and f0_idx < len(freqs) - window:
        region = S[f0_idx - window:f0_idx + window + 1, :]
        avg_region = np.mean(region, axis=1)

        # Find peaks in region
        peaks = []
        for i in range(1, len(avg_region) - 1):
            if avg_region[i] > avg_region[i-1] and avg_region[i] > avg_region[i+1]:
                if avg_region[i] > 0.3 * np.max(avg_region):
                    peaks.append(i)

        if len(peaks) > 1:
            # Multiple peaks = unison
            detune_bins = max(peaks) - min(peaks)
            detune_hz = detune_bins * (freqs[1] - freqs[0])
            detune_cents = 1200 * np.log2((f0 + detune_hz/2) / f0) if f0 > 0 else 0
            return abs(detune_cents), len(peaks)

    return 0.0, 1


def _detect_sub_oscillator(
    spectrum: np.ndarray,
    freqs: np.ndarray,
    f0: float,
) -> bool:
    """Detect presence of sub oscillator (octave below)."""
    sub_freq = f0 / 2
    sub_idx = np.argmin(np.abs(freqs - sub_freq))
    f0_idx = np.argmin(np.abs(freqs - f0))

    if sub_idx < len(spectrum) and f0_idx < len(spectrum):
        sub_level = spectrum[sub_idx]
        f0_level = spectrum[f0_idx]
        if f0_level > 0 and sub_level > 0.2 * f0_level:
            return True

    return False


def _analyze_filter(y: np.ndarray, sr: int) -> SynthFilter:
    """Analyze filter characteristics."""
    # Compute spectrum
    S = np.abs(librosa.stft(y))
    avg_spectrum = np.mean(S, axis=1)
    freqs = librosa.fft_frequencies(sr=sr)

    # Find spectral centroid (indicates filter cutoff)
    centroid = librosa.feature.spectral_centroid(y=y, sr=sr)
    avg_centroid = float(np.mean(centroid))

    # Estimate cutoff from rolloff
    rolloff = librosa.feature.spectral_rolloff(y=y, sr=sr, roll_percent=0.85)
    avg_rolloff = float(np.mean(rolloff))

    # Detect resonance (peak near cutoff)
    resonance = _estimate_resonance(avg_spectrum, freqs, avg_rolloff)

    # Normalize cutoff to 0-1 range (20Hz to 20kHz)
    cutoff_normalized = np.clip(np.log10(avg_rolloff / 20) / np.log10(1000), 0, 1)

    return SynthFilter(
        type="lowpass",  # Most common
        cutoff_hz=avg_rolloff,
        cutoff_normalized=cutoff_normalized,
        resonance=resonance,
        envelope_amount=0.0,
    )


def _estimate_resonance(
    spectrum: np.ndarray,
    freqs: np.ndarray,
    cutoff: float,
) -> float:
    """Estimate filter resonance from spectral peak near cutoff."""
    cutoff_idx = np.argmin(np.abs(freqs - cutoff))

    # Look for peak near cutoff
    window = 20
    start = max(0, cutoff_idx - window)
    end = min(len(spectrum), cutoff_idx + window)

    if end > start:
        region = spectrum[start:end]
        peak_val = np.max(region)
        avg_val = np.mean(spectrum[:cutoff_idx]) if cutoff_idx > 0 else 1

        if avg_val > 0:
            resonance = np.clip((peak_val / avg_val - 1) / 2, 0, 1)
            return float(resonance)

    return 0.0


def _analyze_amplitude_envelope(y: np.ndarray, sr: int) -> SynthEnvelope:
    """Analyze amplitude envelope (ADSR) by examining individual note onsets."""
    hop_length = 512

    # Detect onsets to find note beginnings
    onset_frames = librosa.onset.onset_detect(y=y, sr=sr, hop_length=hop_length)

    if len(onset_frames) < 2:
        # Not enough onsets - use default envelope for sustained sounds
        return SynthEnvelope(attack_ms=10, decay_ms=100, sustain=0.8, release_ms=200)

    # Compute RMS envelope
    rms = librosa.feature.rms(y=y, hop_length=hop_length)[0]
    if len(rms) < 10 or np.max(rms) == 0:
        return SynthEnvelope()

    rms = rms / np.max(rms)

    # Analyze envelope around each onset
    attacks = []
    decays = []
    sustains = []

    for i, onset in enumerate(onset_frames[:-1]):
        # Define note region (from this onset to next onset)
        next_onset = onset_frames[i + 1] if i + 1 < len(onset_frames) else len(rms)
        note_length = next_onset - onset

        if note_length < 5:  # Skip very short notes
            continue

        note_rms = rms[onset:next_onset]
        if len(note_rms) < 3:
            continue

        # Find peak within this note
        peak_idx = np.argmax(note_rms)
        peak_val = note_rms[peak_idx]

        if peak_val < 0.1:  # Skip quiet notes
            continue

        # Attack time (onset to peak)
        attack_frames = peak_idx
        attack_ms = (attack_frames * hop_length / sr) * 1000
        if attack_ms < 500:  # Reasonable attack time
            attacks.append(attack_ms)

        # Sustain level (average of middle portion)
        if len(note_rms) > 10:
            mid_start = max(peak_idx, len(note_rms) // 3)
            mid_end = int(len(note_rms) * 0.8)
            if mid_end > mid_start:
                sustain_level = np.median(note_rms[mid_start:mid_end]) / peak_val
                sustains.append(sustain_level)

        # Decay time (peak to sustain)
        if len(sustains) > 0 and peak_idx < len(note_rms) - 1:
            target_level = sustains[-1] * peak_val
            decay_idx = peak_idx
            for j in range(peak_idx, len(note_rms)):
                if note_rms[j] <= target_level * 1.1:
                    decay_idx = j
                    break
            decay_frames = decay_idx - peak_idx
            decay_ms = (decay_frames * hop_length / sr) * 1000
            if decay_ms < 2000:  # Reasonable decay
                decays.append(decay_ms)

    # Calculate averages, with reasonable defaults
    attack_ms = np.median(attacks) if attacks else 10
    decay_ms = np.median(decays) if decays else 100
    sustain = np.median(sustains) if sustains else 0.7

    # Estimate release from note endings
    release_ms = decay_ms * 1.5  # Typical relationship

    # Clamp to reasonable synth ranges
    return SynthEnvelope(
        attack_ms=float(np.clip(attack_ms, 1, 500)),  # Max 500ms attack for typical synths
        decay_ms=float(np.clip(decay_ms, 10, 2000)),
        sustain=float(np.clip(sustain, 0.1, 1.0)),
        release_ms=float(np.clip(release_ms, 20, 2000)),
    )


def _analyze_lfo(y: np.ndarray, sr: int) -> SynthLFO:
    """Detect LFO modulation."""
    # Look for periodic amplitude modulation
    hop_length = 512
    rms = librosa.feature.rms(y=y, hop_length=hop_length)[0]

    if len(rms) < 50:
        return SynthLFO()

    # Remove DC and normalize
    rms = rms - np.mean(rms)
    if np.max(np.abs(rms)) > 0:
        rms = rms / np.max(np.abs(rms))

    # Compute autocorrelation to find periodic modulation
    corr = np.correlate(rms, rms, mode='full')
    corr = corr[len(corr)//2:]

    # Find first significant peak (after zero lag)
    peak_threshold = 0.3
    for i in range(10, min(len(corr), 500)):
        if corr[i] > peak_threshold * corr[0]:
            if i > 0 and corr[i] > corr[i-1] and corr[i] > corr[i+1]:
                period_samples = i * hop_length
                lfo_rate = sr / period_samples
                if 0.5 < lfo_rate < 20:  # Typical LFO range
                    depth = float(np.std(rms) * 2)
                    return SynthLFO(
                        rate_hz=float(lfo_rate),
                        depth=np.clip(depth, 0, 1),
                        target="amplitude",
                        waveform="sine",
                    )

    return SynthLFO()


def _analyze_brightness(y: np.ndarray, sr: int) -> float:
    """Analyze overall tonal brightness (0-1)."""
    centroid = librosa.feature.spectral_centroid(y=y, sr=sr)
    avg_centroid = np.mean(centroid)

    # Map centroid to 0-1 (500Hz = 0, 5000Hz = 1)
    brightness = np.clip((avg_centroid - 500) / 4500, 0, 1)
    return float(brightness)


def _analyze_movement(y: np.ndarray, sr: int) -> float:
    """Analyze how much the tone changes over time (0-1)."""
    # Compute spectral flux
    S = np.abs(librosa.stft(y))
    flux = np.sqrt(np.mean(np.diff(S, axis=1)**2, axis=0))

    if len(flux) > 0:
        movement = np.mean(flux) / (np.mean(S) + 1e-6)
        return float(np.clip(movement, 0, 1))

    return 0.0


def _detect_chorus(y: np.ndarray, sr: int) -> bool:
    """Detect chorus effect."""
    # Chorus creates slight pitch/phase variations
    # Look for spectral smearing around harmonics
    S = np.abs(librosa.stft(y, n_fft=4096))
    avg_S = np.mean(S, axis=1)

    # Check for broadened peaks (indicative of chorus)
    peaks = []
    for i in range(2, len(avg_S) - 2):
        if avg_S[i] > avg_S[i-1] and avg_S[i] > avg_S[i+1]:
            if avg_S[i] > 0.1 * np.max(avg_S):
                # Measure peak width
                left = i
                while left > 0 and avg_S[left] > 0.5 * avg_S[i]:
                    left -= 1
                right = i
                while right < len(avg_S) - 1 and avg_S[right] > 0.5 * avg_S[i]:
                    right += 1
                width = right - left
                peaks.append(width)

    if len(peaks) > 2:
        avg_width = np.mean(peaks)
        return avg_width > 4  # Broader peaks suggest chorus

    return False


def _detect_phaser(y: np.ndarray, sr: int) -> bool:
    """Detect phaser effect."""
    # Phaser creates moving notches in spectrum
    S = np.abs(librosa.stft(y))

    if S.shape[1] < 10:
        return False

    # Look for spectral notches that move over time
    spectral_var = np.var(S, axis=1)
    avg_spectrum = np.mean(S, axis=1)

    if np.mean(avg_spectrum) > 0:
        variation_ratio = np.mean(spectral_var) / np.mean(avg_spectrum)**2
        return variation_ratio > 0.5

    return False


def _detect_reverb(y: np.ndarray, sr: int) -> bool:
    """Detect reverb."""
    # Check for extended decay after transients
    rms = librosa.feature.rms(y=y, hop_length=512)[0]

    if len(rms) < 10:
        return False

    # Find decay rate
    peak_idx = np.argmax(rms)
    if peak_idx < len(rms) - 5:
        decay_region = rms[peak_idx:min(peak_idx + 50, len(rms))]
        if len(decay_region) > 5:
            # Fit exponential decay
            decay_rate = -np.polyfit(
                np.arange(len(decay_region)),
                np.log(decay_region + 1e-10),
                1
            )[0]
            # Slow decay suggests reverb
            return decay_rate < 0.1

    return False


def _detect_delay(y: np.ndarray, sr: int) -> bool:
    """Detect delay effect."""
    # Look for autocorrelation peaks at delay times
    corr = np.correlate(y[:sr], y[:sr], mode='full')
    corr = corr[len(corr)//2:]

    # Look for peaks between 50ms and 800ms
    min_samples = int(0.05 * sr)
    max_samples = int(0.8 * sr)

    for i in range(min_samples, min(max_samples, len(corr))):
        if corr[i] > 0.3 * corr[0]:
            return True

    return False


def is_synth_tone(y: np.ndarray, sr: int) -> bool:
    """Heuristic to detect if audio is likely a synth tone vs guitar."""
    if not _LIBROSA_AVAILABLE:
        return False

    # Synth tones typically have:
    # - More consistent spectral content
    # - Less noise
    # - More regular harmonic structure

    # Check spectral flatness (synths are less flat than noise)
    flatness = librosa.feature.spectral_flatness(y=y)
    avg_flatness = np.mean(flatness)

    # Check onset strength (synths have cleaner attacks)
    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    onset_var = np.var(onset_env) if len(onset_env) > 0 else 0

    # Low flatness + low onset variation = likely synth
    return avg_flatness < 0.3 and onset_var < 0.5
