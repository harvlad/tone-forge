"""Drum/percussion analyzer.

Analyzes drum recordings (especially electronic/programmed drums) to extract:
- Kick characteristics (pitch, decay, saturation)
- Snare characteristics (pitch, noise, snap)
- Hi-hat characteristics (open/closed, decay)
- Overall characteristics (tempo, swing, compression)
- Best matching drum machine style
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
class KickCharacteristics:
    """Kick drum characteristics."""
    pitch_hz: float = 60.0       # Fundamental pitch (40-100 Hz typical)
    decay_ms: float = 200.0      # Decay time
    saturation: float = 0.3      # Distortion/saturation amount (0-1)
    sub_presence: float = 0.5    # Sub-bass content (0-1)
    click: float = 0.3           # High-frequency click/attack (0-1)


@dataclass
class SnareCharacteristics:
    """Snare drum characteristics."""
    pitch_hz: float = 200.0      # Body pitch
    noise: float = 0.5           # Noise/snare wire amount (0-1)
    snap: float = 0.5            # Attack transient snap (0-1)
    decay_ms: float = 150.0      # Decay time
    body: float = 0.5            # Body/fundamental presence (0-1)


@dataclass
class HihatCharacteristics:
    """Hi-hat characteristics."""
    open_ratio: float = 0.3      # Ratio of open to closed (0-1)
    decay_ms: float = 50.0       # Average decay
    brightness: float = 0.5      # High frequency content (0-1)
    sizzle: float = 0.3          # Sustained sizzle/shimmer (0-1)


@dataclass
class DrumOverall:
    """Overall drum characteristics."""
    tempo_bpm: float = 120.0     # Estimated tempo
    swing: float = 0.0           # Swing amount (0-1)
    compression: float = 0.3     # Overall compression (0-1)
    saturation: float = 0.2      # Overall saturation (0-1)
    style: str = "electronic"    # acoustic, electronic, processed, hybrid


@dataclass
class DrumSource:
    """Source metadata."""
    kind: str = "drums"
    duration_sec: float = 0.0
    sample_rate: int = 22050
    filename: str = ""


@dataclass
class DrumConfidence:
    """Confidence scores."""
    tempo: float = 0.5
    style: float = 0.5
    kick: float = 0.5
    snare: float = 0.5


@dataclass
class DrumDescriptor:
    """Complete drum descriptor."""
    source: DrumSource = field(default_factory=DrumSource)
    kick: KickCharacteristics = field(default_factory=KickCharacteristics)
    snare: SnareCharacteristics = field(default_factory=SnareCharacteristics)
    hihat: HihatCharacteristics = field(default_factory=HihatCharacteristics)
    overall: DrumOverall = field(default_factory=DrumOverall)
    confidence: DrumConfidence = field(default_factory=DrumConfidence)
    matched_machine: str = "tr808"  # Best matching drum machine


# Drum machine characteristics for matching
DRUM_MACHINES = {
    "tr808": {
        "name": "Roland TR-808",
        "style": "electronic",
        "kick": {"pitch": 55, "decay": 400, "saturation": 0.2, "sub": 0.8},
        "snare": {"pitch": 180, "noise": 0.7, "snap": 0.4},
        "hihat": {"decay": 40, "brightness": 0.6},
        "character": "Boomy kicks, snappy snares, sizzly hats",
    },
    "tr909": {
        "name": "Roland TR-909",
        "style": "electronic",
        "kick": {"pitch": 60, "decay": 300, "saturation": 0.3, "sub": 0.6},
        "snare": {"pitch": 200, "noise": 0.5, "snap": 0.6},
        "hihat": {"decay": 60, "brightness": 0.7},
        "character": "Punchy kicks, crisp snares, bright hats",
    },
    "linndrum": {
        "name": "LinnDrum",
        "style": "electronic",
        "kick": {"pitch": 70, "decay": 200, "saturation": 0.1, "sub": 0.4},
        "snare": {"pitch": 220, "noise": 0.4, "snap": 0.7},
        "hihat": {"decay": 80, "brightness": 0.5},
        "character": "Tight kicks, acoustic-ish snares",
    },
    "dmx": {
        "name": "Oberheim DMX",
        "style": "electronic",
        "kick": {"pitch": 65, "decay": 250, "saturation": 0.15, "sub": 0.5},
        "snare": {"pitch": 190, "noise": 0.6, "snap": 0.5},
        "hihat": {"decay": 70, "brightness": 0.55},
        "character": "Classic 80s digital drums",
    },
    "sp1200": {
        "name": "E-mu SP-1200",
        "style": "sampled",
        "kick": {"pitch": 50, "decay": 350, "saturation": 0.4, "sub": 0.7},
        "snare": {"pitch": 200, "noise": 0.5, "snap": 0.5},
        "hihat": {"decay": 50, "brightness": 0.4},
        "character": "Crunchy lo-fi sampling",
    },
    "mpc": {
        "name": "Akai MPC",
        "style": "sampled",
        "kick": {"pitch": 55, "decay": 300, "saturation": 0.2, "sub": 0.6},
        "snare": {"pitch": 210, "noise": 0.5, "snap": 0.6},
        "hihat": {"decay": 60, "brightness": 0.6},
        "character": "Clean sampling with punch",
    },
    "volca_beats": {
        "name": "Korg Volca Beats",
        "style": "electronic",
        "kick": {"pitch": 50, "decay": 350, "saturation": 0.3, "sub": 0.7},
        "snare": {"pitch": 180, "noise": 0.8, "snap": 0.3},
        "hihat": {"decay": 30, "brightness": 0.5},
        "character": "Analog kick/snare, PCM hats",
    },
    "volca_drum": {
        "name": "Korg Volca Drum",
        "style": "electronic",
        "kick": {"pitch": 45, "decay": 500, "saturation": 0.4, "sub": 0.9},
        "snare": {"pitch": 150, "noise": 0.6, "snap": 0.5},
        "hihat": {"decay": 100, "brightness": 0.7},
        "character": "Digital modeling, experimental",
    },
    "drum_brute": {
        "name": "Arturia DrumBrute",
        "style": "electronic",
        "kick": {"pitch": 55, "decay": 380, "saturation": 0.25, "sub": 0.75},
        "snare": {"pitch": 185, "noise": 0.65, "snap": 0.45},
        "hihat": {"decay": 45, "brightness": 0.55},
        "character": "Pure analog, punchy and raw",
    },
    "dfam": {
        "name": "Moog DFAM",
        "style": "electronic",
        "kick": {"pitch": 40, "decay": 600, "saturation": 0.5, "sub": 0.85},
        "snare": {"pitch": 120, "noise": 0.3, "snap": 0.6},
        "hihat": {"decay": 150, "brightness": 0.4},
        "character": "Semi-modular, experimental percussion",
    },
    "digitakt": {
        "name": "Elektron Digitakt",
        "style": "sampled",
        "kick": {"pitch": 55, "decay": 300, "saturation": 0.15, "sub": 0.55},
        "snare": {"pitch": 200, "noise": 0.5, "snap": 0.55},
        "hihat": {"decay": 55, "brightness": 0.65},
        "character": "Modern sampling with effects",
    },
    "acoustic": {
        "name": "Acoustic Kit",
        "style": "acoustic",
        "kick": {"pitch": 80, "decay": 150, "saturation": 0.05, "sub": 0.3},
        "snare": {"pitch": 250, "noise": 0.4, "snap": 0.8},
        "hihat": {"decay": 100, "brightness": 0.6},
        "character": "Natural acoustic drums",
    },
}


def analyze_drums(
    audio_path: str | Path,
    sr: int = 22050,
) -> DrumDescriptor:
    """Analyze a drum recording.

    Args:
        audio_path: Path to audio file
        sr: Sample rate for analysis

    Returns:
        DrumDescriptor with detected characteristics
    """
    if not _LIBROSA_AVAILABLE:
        logger.warning("librosa not available, returning defaults")
        return DrumDescriptor()

    path = Path(audio_path)
    analysis_path = path  # May be overridden by stem separation
    source_kind = "drums"

    if not path.exists():
        logger.warning(f"File not found: {path}")
        return DrumDescriptor()

    # Check if this looks like a full mix (has vocals/other instruments)
    # For now, allow explicit full_mix parameter in the future
    # TODO: Add source_kind parameter and stem separation for full mixes
    # if source_kind == "full_mix":
    #     from . import stem_separator
    #     if stem_separator.is_available():
    #         analysis_path = stem_separator.separate_drums(path)
    #         source_kind = "stem_separated"

    try:
        y, sr = librosa.load(str(analysis_path), sr=sr, mono=True)
    except Exception as e:
        logger.warning(f"Failed to load audio: {e}")
        return DrumDescriptor()

    duration = len(y) / sr

    # Analyze components
    kick = _analyze_kick(y, sr)
    snare = _analyze_snare(y, sr)
    hihat = _analyze_hihat(y, sr)
    overall = _analyze_overall(y, sr)
    matched_machine, match_conf = _match_drum_machine(kick, snare, hihat, overall)

    return DrumDescriptor(
        source=DrumSource(
            kind="drums",
            duration_sec=duration,
            sample_rate=sr,
            filename=path.name,
        ),
        kick=kick,
        snare=snare,
        hihat=hihat,
        overall=overall,
        confidence=DrumConfidence(
            tempo=0.7 if overall.tempo_bpm > 0 else 0.3,
            style=match_conf,
            kick=0.6,
            snare=0.6,
        ),
        matched_machine=matched_machine,
    )


def _analyze_kick(y: np.ndarray, sr: int) -> KickCharacteristics:
    """Analyze kick drum characteristics."""
    # Focus on low frequencies
    S = np.abs(librosa.stft(y))
    freqs = librosa.fft_frequencies(sr=sr)

    # Sub-bass and bass region
    sub_mask = freqs < 80
    bass_mask = (freqs >= 80) & (freqs < 200)

    avg_spec = np.mean(S, axis=1)
    total_energy = np.sum(avg_spec) + 1e-10

    sub_energy = np.sum(avg_spec[sub_mask]) / total_energy
    bass_energy = np.sum(avg_spec[bass_mask]) / total_energy

    # Estimate kick pitch from peak in low frequency
    low_spec = avg_spec[freqs < 150]
    low_freqs = freqs[freqs < 150]
    if len(low_spec) > 0 and np.max(low_spec) > 0:
        pitch_idx = np.argmax(low_spec)
        pitch_hz = low_freqs[pitch_idx] if pitch_idx < len(low_freqs) else 60
    else:
        pitch_hz = 60

    # Decay estimation from envelope
    rms = librosa.feature.rms(y=y)[0]
    decay_frames = np.sum(rms > np.max(rms) * 0.1)
    decay_ms = decay_frames * 512 / sr * 1000

    # Saturation from spectral flatness in kick region
    flatness = np.mean(librosa.feature.spectral_flatness(y=y))
    saturation = min(flatness * 5, 1.0)

    # Click from high frequency transient content
    high_mask = freqs > 2000
    high_energy = np.sum(avg_spec[high_mask]) / total_energy
    click = min(high_energy * 20, 1.0)

    return KickCharacteristics(
        pitch_hz=max(40, min(100, pitch_hz)),
        decay_ms=max(100, min(600, decay_ms)),
        saturation=saturation,
        sub_presence=min(sub_energy * 5, 1.0),
        click=click,
    )


def _analyze_snare(y: np.ndarray, sr: int) -> SnareCharacteristics:
    """Analyze snare drum characteristics."""
    S = np.abs(librosa.stft(y))
    freqs = librosa.fft_frequencies(sr=sr)
    avg_spec = np.mean(S, axis=1)
    total_energy = np.sum(avg_spec) + 1e-10

    # Snare body is typically 150-400 Hz
    body_mask = (freqs >= 150) & (freqs < 400)
    body_energy = np.sum(avg_spec[body_mask]) / total_energy

    # Estimate pitch from peak in snare body region
    body_spec = avg_spec[body_mask]
    body_freqs = freqs[body_mask]
    if len(body_spec) > 0 and np.max(body_spec) > 0:
        pitch_idx = np.argmax(body_spec)
        pitch_hz = body_freqs[pitch_idx] if pitch_idx < len(body_freqs) else 200
    else:
        pitch_hz = 200

    # Noise estimation (high frequency content relative to body)
    noise_mask = freqs > 2000
    noise_energy = np.sum(avg_spec[noise_mask]) / total_energy
    noise = min(noise_energy * 10, 1.0)

    # Snap from transient sharpness
    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    snap = min(np.max(onset_env) / (np.mean(onset_env) + 1e-10) / 10, 1.0)

    return SnareCharacteristics(
        pitch_hz=max(150, min(300, pitch_hz)),
        noise=noise,
        snap=snap,
        decay_ms=150,  # Default, hard to estimate accurately
        body=min(body_energy * 8, 1.0),
    )


def _analyze_hihat(y: np.ndarray, sr: int) -> HihatCharacteristics:
    """Analyze hi-hat characteristics."""
    S = np.abs(librosa.stft(y))
    freqs = librosa.fft_frequencies(sr=sr)
    avg_spec = np.mean(S, axis=1)
    total_energy = np.sum(avg_spec) + 1e-10

    # Hi-hats are primarily high frequency
    high_mask = freqs > 5000
    high_energy = np.sum(avg_spec[high_mask]) / total_energy

    # Brightness from spectral centroid in high region
    centroid = np.mean(librosa.feature.spectral_centroid(y=y, sr=sr))
    brightness = min(centroid / 10000, 1.0)

    # Open vs closed estimation from decay
    rms = librosa.feature.rms(y=y)[0]
    decay_frames = np.sum(rms > np.max(rms) * 0.3)
    decay_ms = decay_frames * 512 / sr * 1000

    # Longer decay = more open hats
    open_ratio = min(decay_ms / 200, 1.0)

    # Sizzle from sustained high frequency
    sizzle = min(high_energy * 15, 1.0)

    return HihatCharacteristics(
        open_ratio=open_ratio,
        decay_ms=max(20, min(300, decay_ms)),
        brightness=brightness,
        sizzle=sizzle,
    )


def _analyze_overall(y: np.ndarray, sr: int) -> DrumOverall:
    """Analyze overall drum characteristics."""
    # Tempo estimation
    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
    if isinstance(tempo, np.ndarray):
        tempo = float(tempo[0]) if len(tempo) > 0 else 120.0
    tempo = float(tempo)

    # Swing estimation (deviation from grid)
    # This is simplified - real swing detection is complex
    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    onset_times = librosa.onset.onset_detect(y=y, sr=sr, units='time')
    if len(onset_times) > 2:
        intervals = np.diff(onset_times)
        interval_var = np.std(intervals) / (np.mean(intervals) + 1e-10)
        swing = min(interval_var, 1.0)
    else:
        swing = 0.0

    # Compression from dynamic range
    rms = librosa.feature.rms(y=y)[0]
    dynamic_range = np.max(rms) / (np.mean(rms) + 1e-10)
    compression = max(0, 1.0 - (dynamic_range - 1.5) / 5)

    # Saturation from spectral flatness
    flatness = np.mean(librosa.feature.spectral_flatness(y=y))
    saturation = min(flatness * 5, 1.0)

    # Style detection
    y_harm, y_perc = librosa.effects.hpss(y)
    harm_ratio = np.sum(y_harm**2) / (np.sum(y_harm**2) + np.sum(y_perc**2) + 1e-10)

    if harm_ratio > 0.3:
        style = "hybrid"  # Has melodic elements
    elif saturation > 0.4:
        style = "processed"
    elif compression > 0.5:
        style = "electronic"
    else:
        style = "acoustic"

    return DrumOverall(
        tempo_bpm=max(60, min(200, tempo)),
        swing=swing,
        compression=compression,
        saturation=saturation,
        style=style,
    )


def _match_drum_machine(
    kick: KickCharacteristics,
    snare: SnareCharacteristics,
    hihat: HihatCharacteristics,
    overall: DrumOverall,
) -> tuple[str, float]:
    """Match characteristics to closest drum machine."""
    scores = {}

    for machine_id, traits in DRUM_MACHINES.items():
        score = 0.0
        kt = traits["kick"]
        st = traits["snare"]
        ht = traits["hihat"]

        # Kick matching
        score += (1.0 - abs(kick.pitch_hz - kt["pitch"]) / 50) * 0.15
        score += (1.0 - abs(kick.decay_ms - kt["decay"]) / 300) * 0.1
        score += (1.0 - abs(kick.saturation - kt["saturation"])) * 0.1
        score += (1.0 - abs(kick.sub_presence - kt["sub"])) * 0.1

        # Snare matching
        score += (1.0 - abs(snare.pitch_hz - st["pitch"]) / 100) * 0.1
        score += (1.0 - abs(snare.noise - st["noise"])) * 0.1
        score += (1.0 - abs(snare.snap - st["snap"])) * 0.1

        # Hihat matching
        score += (1.0 - abs(hihat.decay_ms - ht["decay"]) / 100) * 0.1
        score += (1.0 - abs(hihat.brightness - ht["brightness"])) * 0.1

        # Style matching
        if overall.style == traits["style"]:
            score += 0.05

        scores[machine_id] = max(0, score)

    # Find best match
    best_machine = max(scores, key=scores.get)
    best_score = scores[best_machine]

    return best_machine, min(best_score, 0.9)


def match_drum_machine(desc: DrumDescriptor) -> dict | None:
    """Get full drum machine info for a matched descriptor.

    Args:
        desc: DrumDescriptor with matched_machine set

    Returns:
        Dict with machine info including display name, description, price, etc.
    """
    import json
    from pathlib import Path

    if not desc.matched_machine:
        return None

    # Load drum machines catalog
    catalog_path = Path(__file__).parent.parent / "data" / "drum_machines.json"
    if not catalog_path.exists():
        return None

    with open(catalog_path) as f:
        catalog = json.load(f)

    # Find the matched machine
    for machine in catalog.get("machines", []):
        if machine["id"] == desc.matched_machine:
            # Calculate suggested parameters based on descriptor
            suggested_params = {}

            # Map descriptor values to machine parameter ranges
            if "params" in machine:
                params = machine["params"]
                if "kick" in params:
                    kick_params = {}
                    if "level" in params["kick"]:
                        kick_params["level"] = round(desc.kick.saturation * params["kick"]["level"][1], 1)
                    if "tone" in params["kick"]:
                        kick_params["tone"] = round((desc.kick.pitch_hz - 40) / 60 * params["kick"]["tone"][1], 1)
                    if "decay" in params["kick"]:
                        kick_params["decay"] = round(desc.kick.decay_ms / 500 * params["kick"]["decay"][1], 1)
                    if kick_params:
                        suggested_params["kick"] = kick_params

                if "snare" in params:
                    snare_params = {}
                    if "level" in params["snare"]:
                        snare_params["level"] = round(desc.snare.body * params["snare"]["level"][1], 1)
                    if "snappy" in params["snare"]:
                        snare_params["snappy"] = round(desc.snare.snap * params["snare"]["snappy"][1], 1)
                    if "tone" in params["snare"]:
                        snare_params["tone"] = round(desc.snare.noise * params["snare"]["tone"][1], 1)
                    if snare_params:
                        suggested_params["snare"] = snare_params

                if "hihat" in params:
                    hihat_params = {}
                    if "level" in params["hihat"]:
                        hihat_params["level"] = round(desc.hihat.brightness * params["hihat"]["level"][1], 1)
                    if "decay" in params["hihat"]:
                        hihat_params["decay"] = round(desc.hihat.decay_ms / 200 * params["hihat"]["decay"][1], 1)
                    if hihat_params:
                        suggested_params["hihat"] = hihat_params

            return {
                "id": machine["id"],
                "display": machine["display"],
                "description": machine.get("description", ""),
                "price_estimate": machine.get("price_estimate", ""),
                "style": machine.get("style", ""),
                "year": machine.get("year"),
                "sounds": machine.get("sounds", []),
                "suggested_params": suggested_params,
                "match_score": desc.confidence.style if desc.confidence else 0.5,
            }

    return None
