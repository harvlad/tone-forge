"""Auto-detection of audio source type.

Automatically detects:
1. Is this a full mix (multiple instruments) or isolated/stem?
2. Is this guitar, bass, synth, or drums?

This eliminates the need for users to manually select source type.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

try:
    import librosa
    _LIBROSA_AVAILABLE = True
except ImportError:
    _LIBROSA_AVAILABLE = False


@dataclass
class AudioDetection:
    """Results of audio auto-detection."""
    # Source type
    is_full_mix: bool = False  # Multiple instruments detected
    is_isolated: bool = True   # Single instrument/source

    # Instrument type
    is_guitar: bool = True
    is_synth: bool = False
    is_bass: bool = False
    is_drums: bool = False
    is_vocal: bool = False

    # Confidence scores (0-1)
    mix_confidence: float = 0.5
    instrument_confidence: float = 0.5

    # Recommended processing
    needs_stem_separation: bool = False
    recommended_source_kind: str = "isolated_guitar"

    # Human-readable summary
    summary: str = "Isolated guitar detected"


def detect_audio_type(audio_path: str | Path, sr: int = 22050) -> AudioDetection:
    """Analyze audio and detect its type automatically.

    Args:
        audio_path: Path to audio file
        sr: Sample rate for analysis

    Returns:
        AudioDetection with detected characteristics
    """
    if not _LIBROSA_AVAILABLE:
        return AudioDetection()

    path = Path(audio_path)
    if not path.exists():
        return AudioDetection()

    try:
        y, sr = librosa.load(str(path), sr=sr, mono=True, duration=60)  # First 60s
    except Exception as e:
        logger.warning(f"Failed to load audio for detection: {e}")
        return AudioDetection()

    # Detect if full mix or isolated
    is_mix, mix_conf = _detect_full_mix(y, sr)

    # Detect instrument type (now includes bass and drums)
    detection_result = _detect_instrument_type(y, sr)
    is_guitar = detection_result["is_guitar"]
    is_synth = detection_result["is_synth"]
    is_bass = detection_result["is_bass"]
    is_drums = detection_result["is_drums"]
    inst_conf = detection_result["confidence"]

    # Determine recommendations based on detected types
    needs_separation = is_mix and not is_synth and not is_drums

    # Build summary listing all detected instruments
    detected_types = []
    if is_drums:
        detected_types.append("drums")
    if is_synth:
        detected_types.append("synth")
    if is_bass:
        detected_types.append("bass")
    if is_guitar:
        detected_types.append("guitar")

    # Primary type determines source_kind
    primary = detection_result.get("primary", "guitar")
    if primary == "drums":
        source_kind = "drums"
    elif primary == "synth":
        source_kind = "synth"
    elif primary == "bass":
        source_kind = "bass"
    elif is_mix:
        source_kind = "full_mix"
    else:
        source_kind = "isolated_guitar"

    # Generate summary
    if len(detected_types) > 1:
        summary = f"Detected: {', '.join(detected_types)}"
    elif detected_types:
        type_names = {
            "drums": "Drums/percussion",
            "synth": "Synthesizer/electronic",
            "bass": "Bass guitar",
            "guitar": "Guitar"
        }
        summary = f"{type_names.get(detected_types[0], detected_types[0])} detected"
    else:
        summary = "Audio analyzed"

    return AudioDetection(
        is_full_mix=is_mix,
        is_isolated=not is_mix,
        is_guitar=is_guitar,
        is_synth=is_synth,
        is_bass=is_bass,
        is_drums=is_drums,
        mix_confidence=mix_conf,
        instrument_confidence=inst_conf,
        needs_stem_separation=needs_separation,
        recommended_source_kind=source_kind,
        summary=summary,
    )


def _detect_full_mix(y: np.ndarray, sr: int) -> tuple[bool, float]:
    """Detect if audio is a full mix with multiple instruments.

    Full mixes typically have:
    - Wider frequency spread (bass + highs)
    - Higher spectral complexity
    - Multiple distinct frequency bands with energy
    - Less dynamic range (more compressed)
    """
    # Compute spectrum
    S = np.abs(librosa.stft(y))
    freqs = librosa.fft_frequencies(sr=sr)

    # Average spectrum
    avg_spec = np.mean(S, axis=1)

    # Check energy distribution across frequency bands
    # Full mixes have energy in bass AND mids AND highs

    # Define bands
    bass_mask = freqs < 250
    low_mid_mask = (freqs >= 250) & (freqs < 500)
    mid_mask = (freqs >= 500) & (freqs < 2000)
    high_mid_mask = (freqs >= 2000) & (freqs < 6000)
    high_mask = freqs >= 6000

    total_energy = np.sum(avg_spec) + 1e-10

    bass_ratio = np.sum(avg_spec[bass_mask]) / total_energy
    low_mid_ratio = np.sum(avg_spec[low_mid_mask]) / total_energy
    mid_ratio = np.sum(avg_spec[mid_mask]) / total_energy
    high_mid_ratio = np.sum(avg_spec[high_mid_mask]) / total_energy
    high_ratio = np.sum(avg_spec[high_mask]) / total_energy

    # Full mix indicators:
    # 1. Significant bass energy (drums, bass guitar)
    has_bass = bass_ratio > 0.15

    # 2. Energy spread across all bands
    band_energies = [bass_ratio, low_mid_ratio, mid_ratio, high_mid_ratio, high_ratio]
    bands_with_energy = sum(1 for e in band_energies if e > 0.05)

    # 3. Spectral flatness (mixes are more "full")
    flatness = librosa.feature.spectral_flatness(y=y)
    avg_flatness = np.mean(flatness)

    # 4. Dynamic range (mixes are usually more compressed)
    rms = librosa.feature.rms(y=y)[0]
    dynamic_range = np.max(rms) / (np.mean(rms) + 1e-10)
    is_compressed = dynamic_range < 3.0

    # 5. Check for percussion (onset density)
    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    onset_density = np.sum(onset_env > np.mean(onset_env) * 2) / len(onset_env)
    has_drums = onset_density > 0.15

    # Score the likelihood of full mix
    mix_score = 0.0

    if has_bass:
        mix_score += 0.25
    if bands_with_energy >= 4:
        mix_score += 0.25
    if avg_flatness > 0.01:
        mix_score += 0.15
    if is_compressed:
        mix_score += 0.15
    if has_drums:
        mix_score += 0.20

    is_mix = mix_score > 0.5
    confidence = min(mix_score * 1.5, 1.0)  # Scale confidence

    return is_mix, confidence


def _detect_instrument_type(y: np.ndarray, sr: int) -> dict:
    """Detect if audio is guitar, bass, synth, or drums.

    Returns:
        dict with keys: is_guitar, is_bass, is_synth, is_drums, confidence
    """
    # Spectral features
    centroid = librosa.feature.spectral_centroid(y=y, sr=sr)
    avg_centroid = np.mean(centroid)

    flatness = librosa.feature.spectral_flatness(y=y)
    avg_flatness = np.mean(flatness)

    # Frequency band analysis
    S = np.abs(librosa.stft(y))
    freqs = librosa.fft_frequencies(sr=sr)
    avg_spec = np.mean(S, axis=1)
    total_energy = np.sum(avg_spec) + 1e-10

    # Sub-bass and bass energy (key for bass guitar detection)
    sub_bass_mask = freqs < 80
    bass_mask = (freqs >= 80) & (freqs < 250)
    low_mid_mask = (freqs >= 250) & (freqs < 500)
    mid_mask = (freqs >= 500) & (freqs < 2000)
    high_mask = freqs >= 2000

    sub_bass_ratio = np.sum(avg_spec[sub_bass_mask]) / total_energy
    bass_ratio = np.sum(avg_spec[bass_mask]) / total_energy
    low_mid_ratio = np.sum(avg_spec[low_mid_mask]) / total_energy
    mid_ratio = np.sum(avg_spec[mid_mask]) / total_energy
    high_ratio = np.sum(avg_spec[high_mask]) / total_energy

    # Harmonic vs percussive separation
    y_harm, y_perc = librosa.effects.hpss(y)
    harm_energy = np.sum(y_harm**2)
    perc_energy = np.sum(y_perc**2)
    harm_ratio = harm_energy / (harm_energy + perc_energy + 1e-10)
    perc_ratio = 1.0 - harm_ratio

    # Pitch stability (synths often have very stable pitch)
    f0, voiced_flag, _ = librosa.pyin(y, fmin=30, fmax=2000, sr=sr)  # Lower fmin for bass
    if np.any(voiced_flag):
        voiced_f0 = f0[voiced_flag]
        avg_f0 = np.mean(voiced_f0)
        pitch_stability = 1.0 - min(np.std(voiced_f0) / (avg_f0 + 1e-10), 1.0)
        voiced_ratio = np.sum(voiced_flag) / len(voiced_flag)
    else:
        avg_f0 = 0
        pitch_stability = 0.5
        voiced_ratio = 0.0

    # Onset characteristics
    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    onset_var = np.var(onset_env) / (np.mean(onset_env) + 1e-10)
    onset_density = np.sum(onset_env > np.mean(onset_env) * 2) / len(onset_env)

    # =========================================================================
    # DRUMS DETECTION
    # For full mixes, detect drums by:
    # - Some percussive content (even mixed with melodic)
    # - Regular onset patterns (rhythmic)
    # - Energy in typical drum frequency ranges
    # - High onset density
    # =========================================================================
    drums_score = 0.0

    # Percussive content - lower threshold for mixes
    if perc_ratio > 0.3:  # Some percussive content
        drums_score += 0.25
    elif perc_ratio > 0.15:  # Even a little percussive content
        drums_score += 0.15

    # High onset density indicates drums
    if onset_density > 0.15:
        drums_score += 0.25
    elif onset_density > 0.08:
        drums_score += 0.15

    # Sub-bass energy (kick drum)
    if sub_bass_ratio > 0.08:
        drums_score += 0.2
    elif sub_bass_ratio > 0.03:
        drums_score += 0.1

    # Energy spread across drum frequency ranges
    if sub_bass_ratio > 0.03 and mid_ratio > 0.08 and high_ratio > 0.03:
        drums_score += 0.2

    # Bonus for very rhythmic content (low onset variance = regular pattern)
    if onset_var > 0.5 and onset_density > 0.1:  # Regular strong transients
        drums_score += 0.1

    # =========================================================================
    # SYNTH DETECTION
    # For full mixes (synthwave, electronic), synths are detected by:
    # - Mid-high frequency harmonic content
    # - Regular timing (mechanical/sequenced)
    # - Sustained notes (long decay)
    # - Presence with drums doesn't exclude synth
    # =========================================================================
    synth_score = 0.0

    # Analyze harmonic content on just the harmonic separated signal
    # This helps ignore drum transients
    if harm_energy > 0:
        harm_S = np.abs(librosa.stft(y_harm))
        harm_avg_spec = np.mean(harm_S, axis=1)
        harm_total = np.sum(harm_avg_spec) + 1e-10
        harm_mid_ratio = np.sum(harm_avg_spec[mid_mask]) / harm_total
        harm_high_ratio = np.sum(harm_avg_spec[high_mask]) / harm_total
    else:
        harm_mid_ratio = mid_ratio
        harm_high_ratio = high_ratio

    # Mid-frequency harmonic content (typical synth pads, leads)
    if harm_mid_ratio > 0.3:
        synth_score += 0.2
    elif harm_mid_ratio > 0.2:
        synth_score += 0.1

    # High frequency harmonic content (bright synths, arpeggios)
    if harm_high_ratio > 0.15:
        synth_score += 0.15
    elif harm_high_ratio > 0.08:
        synth_score += 0.1

    # Pitch stability on harmonic signal (more lenient for mixes)
    if pitch_stability > 0.7:
        synth_score += 0.2
    elif pitch_stability > 0.5:
        synth_score += 0.1

    # Low spectral flatness = pure/harmonic tones (synths)
    if avg_flatness < 0.05:
        synth_score += 0.15
    elif avg_flatness < 0.12:
        synth_score += 0.08

    # Regular timing (mechanical/sequenced) - key for synthwave
    if onset_var < 0.5:
        synth_score += 0.15
    elif onset_var < 0.8:
        synth_score += 0.1

    # If drums detected but also significant harmonic content, likely synthwave
    if drums_score > 0.4 and harm_ratio > 0.4:
        synth_score += 0.15

    # Bonus: significant mid+high harmonic content = likely synth
    if harm_mid_ratio + harm_high_ratio > 0.45:
        synth_score += 0.1

    # =========================================================================
    # BASS GUITAR DETECTION
    # - Low fundamental frequency (40-250 Hz typical)
    # - High energy in sub-bass and bass bands
    # - Low spectral centroid (<800 Hz)
    # - Harmonic content similar to guitar but in lower register
    # - Less high frequency content than guitar
    # =========================================================================
    bass_score = 0.0
    # Strong low frequency energy
    if (sub_bass_ratio + bass_ratio) > 0.4:
        bass_score += 0.3
    # Low spectral centroid (bass fundamentals are low)
    if avg_centroid < 800:
        bass_score += 0.25
    # Low average pitch if detected
    if avg_f0 > 0 and avg_f0 < 250:
        bass_score += 0.25
    # Little high frequency content
    if high_ratio < 0.15:
        bass_score += 0.1
    # Has harmonic content (not drums)
    if harm_ratio > 0.5:
        bass_score += 0.1

    # =========================================================================
    # GUITAR DETECTION
    # - Some pitch variation (vibrato, bends)
    # - Moderate spectral flatness (strings have noise)
    # - Natural attack/decay patterns
    # - Centroid typically 1000-4000 Hz
    # =========================================================================
    guitar_score = 0.0
    if 0.5 < pitch_stability < 0.95:
        guitar_score += 0.25
    if 0.01 < avg_flatness < 0.15:
        guitar_score += 0.2
    if 0.6 < harm_ratio < 0.95:
        guitar_score += 0.2
    if 1000 < avg_centroid < 4000:
        guitar_score += 0.2
    if onset_var > 0.2:
        guitar_score += 0.15

    # =========================================================================
    # Determine winner (priority: drums > synth > bass > guitar)
    # Drums are most distinct, then synth, then bass vs guitar
    # =========================================================================
    scores = {
        "drums": drums_score,
        "synth": synth_score,
        "bass": bass_score,
        "guitar": guitar_score,
    }

    # Find the highest scoring type (for primary detection)
    max_type = max(scores, key=scores.get)
    max_score = scores[max_type]

    # For full mixes, detect ALL instruments above threshold independently
    # This allows showing multiple tabs (drums, bass, synth, guitar)
    # Lower thresholds to detect multiple instruments in mixed tracks
    is_drums = drums_score > 0.15  # Very low threshold - drums are hard to detect in mixes
    is_synth = synth_score > 0.2
    is_bass = bass_score > 0.2
    is_guitar = guitar_score > 0.2

    # If nothing detected above threshold, fall back to highest scorer
    if not (is_drums or is_synth or is_bass or is_guitar):
        if max_type == "drums":
            is_drums = True
        elif max_type == "synth":
            is_synth = True
        elif max_type == "bass":
            is_bass = True
        else:
            is_guitar = True

    logger.info(f"Detection scores - drums: {drums_score:.2f}, synth: {synth_score:.2f}, "
                f"bass: {bass_score:.2f}, guitar: {guitar_score:.2f}")
    logger.info(f"Detected - drums: {is_drums}, synth: {is_synth}, bass: {is_bass}, guitar: {is_guitar}")

    return {
        "is_guitar": is_guitar,
        "is_bass": is_bass,
        "is_synth": is_synth,
        "is_drums": is_drums,
        "confidence": max_score,
        "primary": max_type,  # Which type scored highest
    }
