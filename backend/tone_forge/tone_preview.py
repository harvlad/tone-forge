"""
Tone preview and reconstruction playback.

Provides two preview modes:
1. Reference library — known examples of detected characteristics
2. IR convolution — render MIDI through amp sim + cab IR + effects

This allows users to hear "what the analysis thinks the tone sounds like"
without requiring external plugins or DAW rendering.
"""

import numpy as np
import base64
import io
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Sample rate for all preview generation
PREVIEW_SR = 44100


# =============================================================================
# REFERENCE TONE LIBRARY
# =============================================================================

@dataclass
class ToneReference:
    """A reference tone example."""
    name: str
    description: str
    amp_family: str
    gain_stage: str  # clean, edge, crunch, high_gain
    cab_type: str
    mic_type: str
    effects: List[str]
    audio_path: Optional[str] = None
    tags: List[str] = None

    def matches(self, descriptor: Dict, threshold: float = 0.6) -> float:
        """Score how well this reference matches a descriptor."""
        score = 0.0
        weights = 0.0

        # Amp family match (highest weight)
        if descriptor.get('amp', {}).get('family') == self.amp_family:
            score += 3.0
        weights += 3.0

        # Gain stage match
        desc_gain = descriptor.get('amp', {}).get('gain_normalized', 0.5)
        ref_gain = {'clean': 0.2, 'edge': 0.4, 'crunch': 0.6, 'high_gain': 0.85}.get(self.gain_stage, 0.5)
        gain_diff = abs(desc_gain - ref_gain)
        score += (1.0 - gain_diff) * 2.0
        weights += 2.0

        # Cab match
        if descriptor.get('cab', {}).get('type') == self.cab_type:
            score += 1.5
        weights += 1.5

        # Effects presence
        desc_fx = set(descriptor.get('effects', {}).keys())
        ref_fx = set(self.effects)
        if desc_fx and ref_fx:
            fx_overlap = len(desc_fx & ref_fx) / max(len(desc_fx | ref_fx), 1)
            score += fx_overlap
        weights += 1.0

        return score / weights if weights > 0 else 0.0


# Reference library - maps characteristics to known tone examples
# In production, these would link to actual audio files
REFERENCE_LIBRARY = [
    # Clean tones
    ToneReference(
        name="Fender Blackface Clean",
        description="Classic American clean with chimey highs and scooped mids",
        amp_family="fender_blackface",
        gain_stage="clean",
        cab_type="1x12",
        mic_type="dynamic_57",
        effects=[],
        tags=["clean", "american", "sparkle", "country", "jazz"],
    ),
    ToneReference(
        name="Vox Chime Clean",
        description="British jangle with prominent mids and treble",
        amp_family="vox_ac",
        gain_stage="clean",
        cab_type="2x12",
        mic_type="dynamic_57",
        effects=[],
        tags=["clean", "british", "jangle", "beatles", "indie"],
    ),
    ToneReference(
        name="Roland JC Clean",
        description="Pristine solid-state clean with stereo chorus",
        amp_family="roland_jc",
        gain_stage="clean",
        cab_type="2x12",
        mic_type="condenser",
        effects=["chorus"],
        tags=["clean", "stereo", "chorus", "80s", "new_wave"],
    ),

    # Edge of breakup
    ToneReference(
        name="Fender Edge Breakup",
        description="Blackface pushed to edge with light compression",
        amp_family="fender_blackface",
        gain_stage="edge",
        cab_type="1x12",
        mic_type="dynamic_57",
        effects=["compressor"],
        tags=["edge", "american", "blues", "srv"],
    ),
    ToneReference(
        name="Vox Top Boost Edge",
        description="AC30 top boost channel with harmonic breakup",
        amp_family="vox_ac",
        gain_stage="edge",
        cab_type="2x12",
        mic_type="ribbon",
        effects=[],
        tags=["edge", "british", "queen", "u2"],
    ),
    ToneReference(
        name="Marshall Plexi Edge",
        description="Plexi at moderate volume with natural compression",
        amp_family="marshall_plexi",
        gain_stage="edge",
        cab_type="4x12",
        mic_type="dynamic_57",
        effects=[],
        tags=["edge", "british", "classic_rock", "hendrix"],
    ),

    # Crunch
    ToneReference(
        name="Marshall JCM800 Crunch",
        description="Classic British crunch with tight low end",
        amp_family="marshall_jcm800",
        gain_stage="crunch",
        cab_type="4x12",
        mic_type="dynamic_57",
        effects=[],
        tags=["crunch", "british", "rock", "acdc"],
    ),
    ToneReference(
        name="Tweed Deluxe Crunch",
        description="Fat, saggy American crunch with natural compression",
        amp_family="fender_tweed",
        gain_stage="crunch",
        cab_type="1x12",
        mic_type="dynamic_57",
        effects=[],
        tags=["crunch", "american", "blues", "neil_young"],
    ),
    ToneReference(
        name="Dumble Overdrive",
        description="Smooth, dynamic crunch with touch sensitivity",
        amp_family="dumble",
        gain_stage="crunch",
        cab_type="2x12",
        mic_type="condenser",
        effects=[],
        tags=["crunch", "boutique", "fusion", "mayer"],
    ),

    # High gain
    ToneReference(
        name="Mesa Rectifier High Gain",
        description="Tight, aggressive modern high gain with scooped mids",
        amp_family="mesa_rectifier",
        gain_stage="high_gain",
        cab_type="4x12",
        mic_type="dynamic_57",
        effects=["gate"],
        tags=["high_gain", "modern", "metal", "djent"],
    ),
    ToneReference(
        name="5150 High Gain",
        description="Aggressive high gain with cutting mids",
        amp_family="peavey_5150",
        gain_stage="high_gain",
        cab_type="4x12",
        mic_type="dynamic_57",
        effects=["gate"],
        tags=["high_gain", "metal", "evh", "rock"],
    ),
    ToneReference(
        name="Soldano High Gain",
        description="Saturated lead tone with smooth sustain",
        amp_family="soldano",
        gain_stage="high_gain",
        cab_type="4x12",
        mic_type="dynamic_421",
        effects=[],
        tags=["high_gain", "lead", "80s", "shred"],
    ),

    # Effects-heavy
    ToneReference(
        name="Shoegaze Wall",
        description="Heavily effected clean with massive reverb and modulation",
        amp_family="fender_blackface",
        gain_stage="clean",
        cab_type="2x12",
        mic_type="condenser",
        effects=["reverb", "chorus", "delay", "tremolo"],
        tags=["ambient", "shoegaze", "mbv", "dream_pop"],
    ),
    ToneReference(
        name="Edge Dotted Eighth",
        description="Clean tone with prominent dotted eighth delay",
        amp_family="vox_ac",
        gain_stage="edge",
        cab_type="2x12",
        mic_type="dynamic_57",
        effects=["delay", "compression"],
        tags=["delay", "ambient", "u2", "post_punk"],
    ),
]


def find_matching_references(
    descriptor: Dict,
    top_n: int = 3,
    min_score: float = 0.5,
) -> List[Tuple[ToneReference, float]]:
    """
    Find reference tones that match the given descriptor.

    Returns list of (reference, match_score) tuples, sorted by score.
    """
    matches = []

    for ref in REFERENCE_LIBRARY:
        score = ref.matches(descriptor)
        if score >= min_score:
            matches.append((ref, score))

    # Sort by score descending
    matches.sort(key=lambda x: x[1], reverse=True)

    return matches[:top_n]


def get_reference_description(descriptor: Dict) -> Dict:
    """
    Get a description of what the detected tone sounds like,
    based on matching references.
    """
    matches = find_matching_references(descriptor)

    if not matches:
        return {
            "primary_match": None,
            "description": "Unable to find close reference match",
            "similar_tones": [],
        }

    primary = matches[0]

    return {
        "primary_match": {
            "name": primary[0].name,
            "description": primary[0].description,
            "confidence": round(primary[1], 2),
            "characteristics": {
                "amp_family": primary[0].amp_family,
                "gain_stage": primary[0].gain_stage,
                "cab": primary[0].cab_type,
                "mic": primary[0].mic_type,
                "effects": primary[0].effects,
            },
            "tags": primary[0].tags or [],
        },
        "similar_tones": [
            {
                "name": ref.name,
                "confidence": round(score, 2),
                "tags": ref.tags or [],
            }
            for ref, score in matches[1:]
        ],
    }


# =============================================================================
# IR CONVOLUTION PREVIEW
# =============================================================================

@dataclass
class CabinetIR:
    """Cabinet impulse response data."""
    name: str
    cab_type: str  # 1x12, 2x12, 4x12
    mic_type: str
    ir_data: Optional[np.ndarray] = None
    ir_length: int = 2048  # samples


# Synthetic IR generation (placeholder until real IRs are added)
def generate_synthetic_ir(
    cab_type: str = "4x12",
    mic_type: str = "dynamic_57",
    sr: int = PREVIEW_SR,
    length: int = 4096,
) -> np.ndarray:
    """
    Generate a synthetic cabinet IR based on characteristics.

    This is a placeholder that creates a reasonable approximation.
    Real IRs would be loaded from files.
    """
    t = np.arange(length) / sr

    # Base resonance frequency by cab type
    resonances = {
        "1x12": (120, 3500),   # Smaller cab, tighter, brighter
        "2x12": (100, 3000),   # Medium
        "4x12": (80, 2500),    # Large cab, more low end, darker
        "1x15": (60, 2000),    # Bass cab
    }
    low_res, high_roll = resonances.get(cab_type, (100, 3000))

    # Mic characteristics
    mic_chars = {
        "dynamic_57": (0.8, 4000, 0.3),      # presence peak, roll-off, proximity
        "dynamic_421": (0.6, 5000, 0.2),     # flatter, brighter
        "ribbon": (0.4, 3000, 0.1),          # darker, smoother
        "condenser": (0.9, 8000, 0.4),       # brightest, most detailed
    }
    presence, rolloff, proximity = mic_chars.get(mic_type, (0.7, 4000, 0.3))

    # Build IR from exponential decay with resonances
    decay = np.exp(-t * 50)  # Quick decay for cab

    # Low frequency resonance
    low_freq = low_res
    ir = decay * np.sin(2 * np.pi * low_freq * t) * 0.3

    # Speaker cone resonance
    cone_freq = 1200
    ir += decay * np.sin(2 * np.pi * cone_freq * t) * presence * 0.4

    # High frequency content (air/room)
    noise = np.random.randn(length) * 0.05 * decay
    ir += noise

    # Proximity effect (low boost)
    if proximity > 0:
        low_boost = np.exp(-t * 30) * proximity * 0.2
        ir += low_boost * np.sin(2 * np.pi * 80 * t)

    # Normalize
    ir = ir / (np.max(np.abs(ir)) + 1e-8)

    # Apply gentle high-frequency rolloff
    from scipy import signal
    nyq = sr / 2
    rolloff_norm = min(rolloff / nyq, 0.99)
    b, a = signal.butter(2, rolloff_norm, btype='low')
    ir = signal.filtfilt(b, a, ir)

    return ir.astype(np.float32)


def apply_amp_simulation(
    audio: np.ndarray,
    gain: float = 0.5,
    bass: float = 0.5,
    mid: float = 0.5,
    treble: float = 0.5,
    presence: float = 0.5,
) -> np.ndarray:
    """
    Apply simple amp simulation: gain staging, tone stack, waveshaping.
    """
    from scipy import signal

    sr = PREVIEW_SR

    # Input gain
    audio = audio * (1 + gain * 4)

    # Soft clipping / tube saturation approximation
    if gain > 0.3:
        # More aggressive clipping at higher gains
        threshold = 1.0 - (gain * 0.5)
        audio = np.tanh(audio / threshold) * threshold

    # Simple 3-band EQ (tone stack approximation)
    nyq = sr / 2

    # Bass (80-300 Hz)
    b, a = signal.butter(2, [80/nyq, 300/nyq], btype='band')
    bass_band = signal.filtfilt(b, a, audio)

    # Mid (300-3000 Hz)
    b, a = signal.butter(2, [300/nyq, 3000/nyq], btype='band')
    mid_band = signal.filtfilt(b, a, audio)

    # Treble (3000+ Hz)
    b, a = signal.butter(2, 3000/nyq, btype='high')
    treble_band = signal.filtfilt(b, a, audio)

    # Mix with tone controls
    audio = (
        bass_band * (0.5 + bass) +
        mid_band * (0.5 + mid * 0.8) +
        treble_band * (0.3 + treble * 0.7)
    )

    # Presence (high frequency boost)
    if presence > 0.5:
        b, a = signal.butter(1, 4000/nyq, btype='high')
        presence_band = signal.filtfilt(b, a, audio)
        audio += presence_band * (presence - 0.5) * 0.5

    # Normalize
    audio = audio / (np.max(np.abs(audio)) + 1e-8) * 0.9

    return audio.astype(np.float32)


def apply_cabinet_ir(audio: np.ndarray, ir: np.ndarray) -> np.ndarray:
    """Convolve audio with cabinet IR."""
    from scipy import signal

    # Convolve
    output = signal.fftconvolve(audio, ir, mode='same')

    # Normalize
    output = output / (np.max(np.abs(output)) + 1e-8) * 0.9

    return output.astype(np.float32)


def apply_effects(
    audio: np.ndarray,
    effects: Dict,
    sr: int = PREVIEW_SR,
) -> np.ndarray:
    """Apply detected effects to audio."""
    if not effects:
        return audio

    # Reverb
    reverb = effects.get('reverb')
    if reverb:
        decay = reverb.get('decay', 1.5)
        mix = reverb.get('mix', 0.3)
        audio = apply_simple_reverb(audio, decay, mix, sr)

    # Delay
    delay = effects.get('delay')
    if delay:
        time_ms = delay.get('time_ms', 400)
        feedback = delay.get('feedback', 0.4)
        mix = delay.get('mix', 0.3)
        audio = apply_simple_delay(audio, time_ms, feedback, mix, sr)

    # Chorus
    chorus = effects.get('chorus')
    if chorus:
        rate = chorus.get('rate', 1.0)
        depth = chorus.get('depth', 0.5)
        mix = chorus.get('mix', 0.3)
        audio = apply_simple_chorus(audio, rate, depth, mix, sr)

    return audio


def apply_simple_reverb(
    audio: np.ndarray,
    decay: float = 1.5,
    mix: float = 0.3,
    sr: int = PREVIEW_SR,
) -> np.ndarray:
    """Simple algorithmic reverb."""
    # Create reverb tail using multiple delays
    reverb = np.zeros_like(audio)

    delay_times = [0.029, 0.037, 0.041, 0.053, 0.067, 0.079]
    decay_factors = [0.8, 0.7, 0.6, 0.5, 0.4, 0.3]

    for dt, df in zip(delay_times, decay_factors):
        delay_samples = int(dt * decay * sr)
        if delay_samples < len(audio):
            delayed = np.zeros_like(audio)
            delayed[delay_samples:] = audio[:-delay_samples] * df
            reverb += delayed

    # Diffusion (simple lowpass on reverb)
    from scipy import signal
    b, a = signal.butter(2, 4000 / (sr/2), btype='low')
    reverb = signal.filtfilt(b, a, reverb)

    return audio * (1 - mix) + reverb * mix


def apply_simple_delay(
    audio: np.ndarray,
    time_ms: float = 400,
    feedback: float = 0.4,
    mix: float = 0.3,
    sr: int = PREVIEW_SR,
) -> np.ndarray:
    """Simple delay effect."""
    delay_samples = int(time_ms / 1000 * sr)

    if delay_samples >= len(audio):
        return audio

    output = audio.copy()
    delayed = np.zeros_like(audio)

    # Multi-tap feedback
    current_feedback = feedback
    for tap in range(4):
        tap_delay = delay_samples * (tap + 1)
        if tap_delay >= len(audio):
            break
        delayed[tap_delay:] += audio[:-tap_delay] * current_feedback
        current_feedback *= feedback

    return output * (1 - mix) + delayed * mix


def apply_simple_chorus(
    audio: np.ndarray,
    rate: float = 1.0,
    depth: float = 0.5,
    mix: float = 0.3,
    sr: int = PREVIEW_SR,
) -> np.ndarray:
    """Simple chorus effect using modulated delay."""
    # LFO
    t = np.arange(len(audio)) / sr
    lfo = np.sin(2 * np.pi * rate * t) * depth

    # Modulated delay (5-25ms)
    base_delay = 0.015  # 15ms
    mod_depth = 0.010   # +/- 10ms

    output = np.zeros_like(audio)

    for i in range(len(audio)):
        delay_time = base_delay + lfo[i] * mod_depth
        delay_samples = int(delay_time * sr)
        if i >= delay_samples:
            output[i] = audio[i - delay_samples]
        else:
            output[i] = audio[i]

    return audio * (1 - mix) + output * mix


# =============================================================================
# MIDI TO AUDIO RENDERING
# =============================================================================

def render_midi_to_audio(
    midi_content_b64: str,
    preset_type: str = "guitar",  # guitar, bass, synth
    sr: int = PREVIEW_SR,
) -> np.ndarray:
    """
    Render MIDI content to audio using a simple synthesizer.

    This creates a basic audio representation of the MIDI that can
    then be processed through the amp/cab/effects chain.
    """
    import pretty_midi

    # Decode MIDI
    midi_bytes = base64.b64decode(midi_content_b64)
    pm = pretty_midi.PrettyMIDI(io.BytesIO(midi_bytes))

    duration = pm.get_end_time()
    if duration <= 0:
        duration = 1.0

    num_samples = int(duration * sr) + sr  # Add 1 second for tail
    audio = np.zeros(num_samples, dtype=np.float32)

    # Synthesize each note
    for instrument in pm.instruments:
        for note in instrument.notes:
            note_audio = synthesize_note(
                pitch=note.pitch,
                start_time=note.start,
                duration=note.end - note.start,
                velocity=note.velocity,
                preset_type=preset_type,
                sr=sr,
            )

            # Add to output at correct position
            start_sample = int(note.start * sr)
            end_sample = start_sample + len(note_audio)

            if end_sample > len(audio):
                end_sample = len(audio)
                note_audio = note_audio[:end_sample - start_sample]

            audio[start_sample:end_sample] += note_audio

    # Normalize
    max_val = np.max(np.abs(audio))
    if max_val > 0:
        audio = audio / max_val * 0.8

    return audio


def synthesize_note(
    pitch: int,
    start_time: float,
    duration: float,
    velocity: int,
    preset_type: str = "guitar",
    sr: int = PREVIEW_SR,
) -> np.ndarray:
    """Synthesize a single note."""
    # Calculate frequency
    freq = 440.0 * (2.0 ** ((pitch - 69) / 12.0))

    # Number of samples
    num_samples = int((duration + 0.5) * sr)  # Add release time
    t = np.arange(num_samples) / sr

    # Velocity scaling
    amp = (velocity / 127.0) * 0.7

    # Waveform based on preset type
    if preset_type == "guitar":
        # Sawtooth-ish with harmonics (guitar-like)
        wave = (
            np.sin(2 * np.pi * freq * t) * 1.0 +
            np.sin(2 * np.pi * freq * 2 * t) * 0.5 +
            np.sin(2 * np.pi * freq * 3 * t) * 0.25 +
            np.sin(2 * np.pi * freq * 4 * t) * 0.125
        )
    elif preset_type == "bass":
        # More fundamental, less harmonics
        wave = (
            np.sin(2 * np.pi * freq * t) * 1.0 +
            np.sin(2 * np.pi * freq * 2 * t) * 0.3 +
            np.sin(2 * np.pi * freq * 3 * t) * 0.1
        )
    else:  # synth
        # Sawtooth
        wave = 2 * (t * freq % 1) - 1

    # Envelope (ADSR)
    attack = 0.01
    decay = 0.1
    sustain_level = 0.7
    release = 0.3

    envelope = np.ones(num_samples)

    attack_samples = int(attack * sr)
    decay_samples = int(decay * sr)
    release_samples = int(release * sr)
    sustain_samples = num_samples - attack_samples - decay_samples - release_samples

    if sustain_samples < 0:
        sustain_samples = 0

    # Build envelope
    idx = 0
    # Attack
    envelope[idx:idx+attack_samples] = np.linspace(0, 1, attack_samples)
    idx += attack_samples
    # Decay
    envelope[idx:idx+decay_samples] = np.linspace(1, sustain_level, decay_samples)
    idx += decay_samples
    # Sustain
    envelope[idx:idx+sustain_samples] = sustain_level
    idx += sustain_samples
    # Release
    envelope[idx:] = np.linspace(sustain_level, 0, len(envelope) - idx)

    return (wave * envelope * amp).astype(np.float32)


# =============================================================================
# MAIN PREVIEW GENERATION
# =============================================================================

def generate_reconstruction_preview(
    descriptor: Dict,
    midi_content_b64: Optional[str] = None,
    preset_type: str = "guitar",
    sr: int = PREVIEW_SR,
) -> Tuple[np.ndarray, Dict]:
    """
    Generate a preview of the reconstructed tone.

    Args:
        descriptor: Tone descriptor from analysis
        midi_content_b64: Optional MIDI content to render
        preset_type: Type of preset (guitar, bass, synth)
        sr: Sample rate

    Returns:
        Tuple of (audio_array, metadata_dict)
    """
    metadata = {
        "sr": sr,
        "amp": descriptor.get("amp", {}),
        "cab": descriptor.get("cab", {}),
        "effects_applied": [],
    }

    # If we have MIDI, render it; otherwise generate test signal
    if midi_content_b64:
        audio = render_midi_to_audio(midi_content_b64, preset_type, sr)
        metadata["source"] = "midi"
    else:
        # Generate a test chord/arpeggio
        audio = generate_test_signal(descriptor, preset_type, sr)
        metadata["source"] = "test_signal"

    # Extract amp settings from descriptor
    amp = descriptor.get("amp", {})
    gain = amp.get("gain_normalized", 0.5)
    bass = amp.get("bass", 0.5)
    mid = amp.get("mid", 0.5)
    treble = amp.get("treble", 0.5)
    presence = amp.get("presence", 0.5)

    # Apply amp simulation
    audio = apply_amp_simulation(audio, gain, bass, mid, treble, presence)
    metadata["effects_applied"].append("amp_sim")

    # Generate and apply cabinet IR
    cab = descriptor.get("cab", {})
    cab_type = cab.get("type", "4x12")
    mic_type = cab.get("mic", "dynamic_57")

    ir = generate_synthetic_ir(cab_type, mic_type, sr)
    audio = apply_cabinet_ir(audio, ir)
    metadata["effects_applied"].append(f"cab_ir_{cab_type}_{mic_type}")

    # Apply effects
    effects = descriptor.get("effects", {})
    if effects:
        audio = apply_effects(audio, effects, sr)
        metadata["effects_applied"].extend(effects.keys())

    # Final normalization
    max_val = np.max(np.abs(audio))
    if max_val > 0:
        audio = audio / max_val * 0.85

    return audio, metadata


def generate_test_signal(
    descriptor: Dict,
    preset_type: str = "guitar",
    sr: int = PREVIEW_SR,
    duration: float = 4.0,
) -> np.ndarray:
    """Generate a test signal (chord progression) for preview."""
    # Detect key from descriptor
    key_root = descriptor.get("key_root", 0)  # C
    key_scale = descriptor.get("key_scale", "major")

    # Simple I-IV-V-I progression
    if key_scale == "minor":
        chord_intervals = [0, 5, 7, 0]  # i - iv - v - i
        chord_qualities = ["minor", "minor", "minor", "minor"]
    else:
        chord_intervals = [0, 5, 7, 0]  # I - IV - V - I
        chord_qualities = ["major", "major", "major", "major"]

    audio = np.zeros(int(duration * sr), dtype=np.float32)
    chord_duration = duration / 4

    for i, (interval, quality) in enumerate(zip(chord_intervals, chord_qualities)):
        root_pitch = 48 + key_root + interval  # Start at C3

        # Build chord
        if quality == "minor":
            pitches = [root_pitch, root_pitch + 3, root_pitch + 7]
        else:
            pitches = [root_pitch, root_pitch + 4, root_pitch + 7]

        # Render chord
        chord_start = i * chord_duration
        for pitch in pitches:
            note_audio = synthesize_note(
                pitch=pitch,
                start_time=0,
                duration=chord_duration * 0.9,
                velocity=90,
                preset_type=preset_type,
                sr=sr,
            )

            start_sample = int(chord_start * sr)
            end_sample = start_sample + len(note_audio)

            if end_sample > len(audio):
                end_sample = len(audio)
                note_audio = note_audio[:end_sample - start_sample]

            audio[start_sample:end_sample] += note_audio * 0.5

    return audio


def audio_to_wav_base64(audio: np.ndarray, sr: int = PREVIEW_SR) -> str:
    """Convert audio array to base64-encoded WAV."""
    import wave
    import struct

    buffer = io.BytesIO()

    with wave.open(buffer, 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)  # 16-bit
        wav.setframerate(sr)

        # Convert to 16-bit PCM
        audio_int = (audio * 32767).astype(np.int16)
        wav.writeframes(audio_int.tobytes())

    buffer.seek(0)
    return base64.b64encode(buffer.read()).decode('ascii')


def generate_preview_response(
    descriptor: Dict,
    midi_content_b64: Optional[str] = None,
    preset_type: str = "guitar",
) -> Dict:
    """
    Generate complete preview response for API.

    Returns dict with:
    - audio_b64: Base64 WAV of reconstructed preview
    - reference: Matching reference tone info
    - metadata: Processing details
    """
    # Generate reconstruction preview
    audio, metadata = generate_reconstruction_preview(
        descriptor, midi_content_b64, preset_type
    )

    # Get reference match
    reference = get_reference_description(descriptor)

    return {
        "audio_b64": audio_to_wav_base64(audio),
        "audio_format": "wav",
        "sample_rate": PREVIEW_SR,
        "duration_seconds": len(audio) / PREVIEW_SR,
        "reference": reference,
        "metadata": metadata,
    }
