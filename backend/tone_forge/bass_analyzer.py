"""Bass guitar tone analyzer.

Analyzes bass guitar recordings to extract tone characteristics including:
- Amp family (Ampeg, Fender, Darkglass, etc.)
- Gain/overdrive level
- EQ voicing (bass, low-mid, mid, treble)
- Cabinet character
- Effects (compression, overdrive, chorus, octaver)
- Playing technique (fingerstyle, pick, slap, fretless)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

try:
    import librosa
    _LIBROSA_AVAILABLE = True
except ImportError:
    _LIBROSA_AVAILABLE = False


@dataclass
class BassVoicing:
    """EQ voicing for bass."""
    bass: float = 0.5      # Sub and bass (0-1)
    low_mid: float = 0.5   # 200-500 Hz punch
    mid: float = 0.5       # 500-2000 Hz growl
    treble: float = 0.5    # 2000+ Hz presence/clank


@dataclass
class BassAmp:
    """Bass amplifier characteristics."""
    family: str = "ampeg_svt"  # Amp family
    gain: float = 0.3          # Overdrive amount (0-1)
    voicing: BassVoicing = field(default_factory=BassVoicing)
    alternates: list[str] = field(default_factory=list)  # Runner-up amps


@dataclass
class BassCab:
    """Bass cabinet characteristics."""
    configuration: str = "4x10"  # 4x10, 8x10, 1x15, 2x12, etc.
    speaker_size: str = "10"     # Dominant speaker size
    character: str = "punchy"    # punchy, warm, modern, vintage


@dataclass
class BassEffects:
    """Detected bass effects."""
    compressor: float = 0.0      # Compression amount (0-1)
    overdrive: float = 0.0       # Overdrive/distortion amount (0-1)
    chorus: float = 0.0          # Chorus depth (0-1)
    octaver: float = 0.0         # Octave effect amount (0-1)
    envelope_filter: float = 0.0 # Auto-wah amount (0-1)


@dataclass
class BassSource:
    """Source metadata."""
    kind: str = "isolated_bass"
    duration_sec: float = 0.0
    sample_rate: int = 22050
    filename: str = ""


@dataclass
class BassConfidence:
    """Confidence scores for various detections."""
    amp_family: float = 0.5
    gain: float = 0.5
    cab: float = 0.5
    technique: float = 0.5


@dataclass
class BassDescriptor:
    """Complete bass tone descriptor."""
    source: BassSource = field(default_factory=BassSource)
    technique: str = "fingerstyle"  # fingerstyle, pick, slap, fretless
    amp: BassAmp = field(default_factory=BassAmp)
    cab: BassCab = field(default_factory=BassCab)
    effects: BassEffects = field(default_factory=BassEffects)
    confidence: BassConfidence = field(default_factory=BassConfidence)


# Bass amp families with their characteristics
BASS_AMP_FAMILIES = {
    "ampeg_svt": {
        "name": "Ampeg SVT",
        "character": "Classic tube warmth with growl",
        "bass": 0.7, "low_mid": 0.6, "mid": 0.5, "treble": 0.4,
        "typical_gain": 0.4,
    },
    "ampeg_b15": {
        "name": "Ampeg B-15",
        "character": "Vintage warm and round",
        "bass": 0.6, "low_mid": 0.7, "mid": 0.4, "treble": 0.3,
        "typical_gain": 0.2,
    },
    "fender_bassman": {
        "name": "Fender Bassman",
        "character": "Clean and punchy with sparkle",
        "bass": 0.5, "low_mid": 0.5, "mid": 0.6, "treble": 0.6,
        "typical_gain": 0.3,
    },
    "darkglass": {
        "name": "Darkglass",
        "character": "Modern aggressive with tight low end",
        "bass": 0.6, "low_mid": 0.5, "mid": 0.7, "treble": 0.7,
        "typical_gain": 0.6,
    },
    "mesa_bass": {
        "name": "Mesa Boogie Bass",
        "character": "Thick and articulate with presence",
        "bass": 0.7, "low_mid": 0.6, "mid": 0.6, "treble": 0.5,
        "typical_gain": 0.5,
    },
    "gallien_krueger": {
        "name": "Gallien-Krueger",
        "character": "Hi-fi clean with growl on demand",
        "bass": 0.5, "low_mid": 0.4, "mid": 0.6, "treble": 0.7,
        "typical_gain": 0.3,
    },
    "hartke": {
        "name": "Hartke",
        "character": "Bright and punchy aluminum cone sound",
        "bass": 0.5, "low_mid": 0.5, "mid": 0.5, "treble": 0.8,
        "typical_gain": 0.3,
    },
    "markbass": {
        "name": "Markbass",
        "character": "Clean and transparent with warmth",
        "bass": 0.6, "low_mid": 0.6, "mid": 0.5, "treble": 0.5,
        "typical_gain": 0.2,
    },
    "orange_bass": {
        "name": "Orange Bass Terror",
        "character": "British grit with fat low end",
        "bass": 0.7, "low_mid": 0.6, "mid": 0.5, "treble": 0.4,
        "typical_gain": 0.5,
    },
    "aguilar": {
        "name": "Aguilar",
        "character": "Modern vintage with organic warmth",
        "bass": 0.6, "low_mid": 0.6, "mid": 0.5, "treble": 0.5,
        "typical_gain": 0.3,
    },
}


def analyze_bass(
    audio_path: str | Path,
    source_kind: str = "isolated_bass",
    sr: int = 22050,
) -> BassDescriptor:
    """Analyze a bass guitar recording.

    Args:
        audio_path: Path to audio file
        source_kind: Type of source (isolated_bass, full_mix, etc.)
        sr: Sample rate for analysis

    Returns:
        BassDescriptor with detected tone characteristics
    """
    if not _LIBROSA_AVAILABLE:
        logger.warning("librosa not available, returning defaults")
        return BassDescriptor()

    path = Path(audio_path)
    analysis_path = path  # May be overridden by stem separation

    if not path.exists():
        logger.warning(f"File not found: {path}")
        return BassDescriptor()

    # For full mix input, run stem separation first to isolate bass
    if source_kind == "full_mix":
        from . import stem_separator
        if not stem_separator.is_available():
            raise RuntimeError(
                "Full mix analysis requires Demucs. "
                "Install with: pip install demucs torch torchaudio"
            )
        # Separate bass stem and analyze that instead
        analysis_path = stem_separator.separate_bass(path)
        source_kind = "stem_separated"

    try:
        y, sr = librosa.load(str(analysis_path), sr=sr, mono=True)
    except Exception as e:
        logger.warning(f"Failed to load audio: {e}")
        return BassDescriptor()

    duration = len(y) / sr

    # Compute features
    features = _compute_features(y, sr)

    # Analyze components
    voicing = _estimate_voicing(features)
    gain, gain_conf = _estimate_gain(features)
    amp_family, amp_conf, alternates = _classify_amp_family(voicing, gain, features)
    cab, cab_conf = _classify_cab(features, voicing)
    technique, tech_conf = _detect_technique(features)
    effects = _detect_effects(y, sr, features)

    return BassDescriptor(
        source=BassSource(
            kind=source_kind,
            duration_sec=duration,
            sample_rate=sr,
            filename=path.name,
        ),
        technique=technique,
        amp=BassAmp(
            family=amp_family,
            gain=gain,
            voicing=voicing,
            alternates=alternates,
        ),
        cab=cab,
        effects=effects,
        confidence=BassConfidence(
            amp_family=amp_conf,
            gain=gain_conf,
            cab=cab_conf,
            technique=tech_conf,
        ),
    )


@dataclass
class _Features:
    """Computed audio features for bass analysis."""
    rms: np.ndarray
    centroid: float
    flatness: float
    crest_factor: float
    band_energy: dict
    onset_density: float
    attack_time: float
    harm_ratio: float


def _compute_features(y: np.ndarray, sr: int) -> _Features:
    """Compute all features needed for bass analysis."""
    # RMS energy
    rms = librosa.feature.rms(y=y)[0]

    # Spectral features
    centroid = np.mean(librosa.feature.spectral_centroid(y=y, sr=sr))
    flatness = np.mean(librosa.feature.spectral_flatness(y=y))

    # Crest factor (peak to RMS ratio)
    peak = np.max(np.abs(y))
    rms_total = np.sqrt(np.mean(y**2))
    crest_factor = peak / (rms_total + 1e-10)

    # Band energy analysis
    S = np.abs(librosa.stft(y))
    freqs = librosa.fft_frequencies(sr=sr)
    avg_spec = np.mean(S, axis=1)
    total_energy = np.sum(avg_spec) + 1e-10

    sub_bass = np.sum(avg_spec[freqs < 80]) / total_energy
    bass = np.sum(avg_spec[(freqs >= 80) & (freqs < 250)]) / total_energy
    low_mid = np.sum(avg_spec[(freqs >= 250) & (freqs < 500)]) / total_energy
    mid = np.sum(avg_spec[(freqs >= 500) & (freqs < 2000)]) / total_energy
    treble = np.sum(avg_spec[freqs >= 2000]) / total_energy

    band_energy = {
        "sub_bass": sub_bass,
        "bass": bass,
        "low_mid": low_mid,
        "mid": mid,
        "treble": treble,
    }

    # Onset density
    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    onset_density = np.sum(onset_env > np.mean(onset_env) * 2) / len(onset_env)

    # Attack time estimation
    attack_frames = np.argmax(rms > np.max(rms) * 0.9)
    attack_time = attack_frames * 512 / sr  # Assuming 512 hop length

    # Harmonic ratio
    y_harm, y_perc = librosa.effects.hpss(y)
    harm_energy = np.sum(y_harm**2)
    perc_energy = np.sum(y_perc**2)
    harm_ratio = harm_energy / (harm_energy + perc_energy + 1e-10)

    return _Features(
        rms=rms,
        centroid=centroid,
        flatness=flatness,
        crest_factor=crest_factor,
        band_energy=band_energy,
        onset_density=onset_density,
        attack_time=attack_time,
        harm_ratio=harm_ratio,
    )


def _estimate_voicing(f: _Features) -> BassVoicing:
    """Estimate EQ voicing from features."""
    be = f.band_energy

    # Normalize to 0-1 range
    bass_val = min((be["sub_bass"] + be["bass"]) * 2, 1.0)
    low_mid_val = min(be["low_mid"] * 4, 1.0)
    mid_val = min(be["mid"] * 5, 1.0)
    treble_val = min(be["treble"] * 10, 1.0)

    return BassVoicing(
        bass=bass_val,
        low_mid=low_mid_val,
        mid=mid_val,
        treble=treble_val,
    )


def _estimate_gain(f: _Features) -> tuple[float, float]:
    """Estimate overdrive/gain amount."""
    # Higher flatness = more harmonics = more gain
    # Lower crest factor = more compression/saturation
    gain_from_flatness = min(f.flatness * 10, 1.0)
    gain_from_crest = max(0, 1.0 - (f.crest_factor - 3) / 10)

    gain = (gain_from_flatness * 0.6 + gain_from_crest * 0.4)
    confidence = 0.6  # Bass gain estimation is tricky

    return gain, confidence


def _classify_amp_family(voicing: BassVoicing, gain: float, f: _Features) -> tuple[str, float, list[str]]:
    """Classify the bass amp family."""
    scores = {}

    for family, traits in BASS_AMP_FAMILIES.items():
        score = 0.0

        # Match voicing
        score += (1.0 - abs(voicing.bass - traits["bass"])) * 0.25
        score += (1.0 - abs(voicing.low_mid - traits["low_mid"])) * 0.2
        score += (1.0 - abs(voicing.mid - traits["mid"])) * 0.2
        score += (1.0 - abs(voicing.treble - traits["treble"])) * 0.15

        # Match gain
        score += (1.0 - abs(gain - traits["typical_gain"])) * 0.2

        scores[family] = score

    # Sort by score
    sorted_families = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    best_family = sorted_families[0][0]
    best_score = sorted_families[0][1]

    # Get alternates
    alternates = [f[0] for f in sorted_families[1:3]]

    confidence = min(best_score, 0.9)

    return best_family, confidence, alternates


def _classify_cab(f: _Features, voicing: BassVoicing) -> tuple[BassCab, float]:
    """Classify cabinet characteristics."""
    be = f.band_energy

    # Determine speaker size tendency
    if be["sub_bass"] > 0.15:
        # Strong sub-bass suggests 15" speakers
        configuration = "1x15"
        speaker_size = "15"
        character = "warm"
    elif voicing.treble > 0.6:
        # Bright and punchy suggests 10" speakers
        configuration = "4x10"
        speaker_size = "10"
        character = "punchy"
    elif voicing.mid > 0.6:
        # Mid-forward suggests modern cab
        configuration = "2x12"
        speaker_size = "12"
        character = "modern"
    else:
        # Default to classic 4x10
        configuration = "4x10"
        speaker_size = "10"
        character = "punchy"

    return BassCab(
        configuration=configuration,
        speaker_size=speaker_size,
        character=character,
    ), 0.5


def _detect_technique(f: _Features) -> tuple[str, float]:
    """Detect bass playing technique."""
    # Slap: high treble, percussive, fast attack
    # Pick: bright, punchy attack, higher mids
    # Fingerstyle: warmer, rounder attack
    # Fretless: smooth, less defined attack, pitch slides

    be = f.band_energy
    confidence = 0.5

    # Slap detection
    if be["treble"] > 0.1 and f.onset_density > 0.25 and f.attack_time < 0.01:
        return "slap", 0.7

    # Pick detection
    if be["treble"] > 0.08 and f.attack_time < 0.015 and f.centroid > 400:
        return "pick", 0.6

    # Fretless detection (smooth, less percussive)
    if f.harm_ratio > 0.85 and f.onset_density < 0.1:
        return "fretless", 0.5

    # Default to fingerstyle
    return "fingerstyle", confidence


def _detect_effects(y: np.ndarray, sr: int, f: _Features) -> BassEffects:
    """Detect bass effects."""
    # Compression detection (low dynamic range)
    rms = f.rms
    dynamic_range = np.max(rms) / (np.mean(rms) + 1e-10)
    compressor = max(0, 1.0 - (dynamic_range - 1.5) / 3)

    # Overdrive detection (from flatness)
    overdrive = min(f.flatness * 8, 1.0)

    # Chorus detection (spectral width variation)
    spec_flux = np.mean(np.diff(librosa.feature.spectral_centroid(y=y, sr=sr)))
    chorus = min(abs(spec_flux) / 100, 1.0)

    # Octaver detection (sub-octave energy)
    octaver = 0.0
    if f.band_energy["sub_bass"] > 0.2:
        octaver = min((f.band_energy["sub_bass"] - 0.1) * 3, 1.0)

    return BassEffects(
        compressor=compressor,
        overdrive=overdrive,
        chorus=chorus,
        octaver=octaver,
        envelope_filter=0.0,  # TODO: detect envelope filter
    )
