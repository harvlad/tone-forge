"""Synth hardware translation.

Translates detected synth parameters to real hardware synth settings.
Supports multiple hardware synths with their specific control ranges.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class SynthControl:
    """A single synth control/knob setting."""
    name: str
    value: float  # 0-10 or 0-100 depending on synth
    display: str  # Human-readable value
    note: str = ""  # Optional tip


@dataclass
class SynthHardwareConfig:
    """Configuration for a specific hardware synth."""
    synth_name: str
    synth_model: str
    description: str
    controls: list[SynthControl]
    notes: list[str]


# Hardware synth definitions with control mappings
SYNTH_HARDWARE = {
    "volca_keys": {
        "name": "Korg Volca Keys",
        "description": "3-voice polyphonic analog synth. Great for pads and leads.",
        "price": "$160",
        "controls": {
            "voice": {"min": 0, "max": 5, "type": "discrete"},  # Poly, Unison, Octave, Fifth, etc.
            "octave": {"min": 0, "max": 2, "type": "discrete"},
            "detune": {"min": 0, "max": 127, "type": "continuous"},
            "portamento": {"min": 0, "max": 127, "type": "continuous"},
            "vcf_cutoff": {"min": 0, "max": 127, "type": "continuous"},
            "vcf_eg_int": {"min": 0, "max": 127, "type": "continuous"},
            "attack": {"min": 0, "max": 127, "type": "continuous"},
            "decay_release": {"min": 0, "max": 127, "type": "continuous"},
            "sustain": {"min": 0, "max": 127, "type": "continuous"},
            "delay_time": {"min": 0, "max": 127, "type": "continuous"},
            "delay_feedback": {"min": 0, "max": 127, "type": "continuous"},
        }
    },
    "minilogue": {
        "name": "Korg Minilogue",
        "description": "4-voice polyphonic analog synth with sequencer.",
        "price": "$500",
        "controls": {
            "vco1_wave": {"min": 0, "max": 3, "type": "discrete"},  # Saw, Triangle, Square
            "vco1_octave": {"min": 0, "max": 3, "type": "discrete"},
            "vco1_pitch": {"min": 0, "max": 1023, "type": "continuous"},
            "vco2_wave": {"min": 0, "max": 3, "type": "discrete"},
            "vco2_octave": {"min": 0, "max": 3, "type": "discrete"},
            "vco2_pitch": {"min": 0, "max": 1023, "type": "continuous"},
            "cross_mod": {"min": 0, "max": 1023, "type": "continuous"},
            "pitch_eg_int": {"min": 0, "max": 1023, "type": "continuous"},
            "cutoff": {"min": 0, "max": 1023, "type": "continuous"},
            "resonance": {"min": 0, "max": 1023, "type": "continuous"},
            "eg_int": {"min": 0, "max": 1023, "type": "continuous"},
            "attack": {"min": 0, "max": 1023, "type": "continuous"},
            "decay": {"min": 0, "max": 1023, "type": "continuous"},
            "sustain": {"min": 0, "max": 1023, "type": "continuous"},
            "release": {"min": 0, "max": 1023, "type": "continuous"},
            "lfo_rate": {"min": 0, "max": 1023, "type": "continuous"},
            "lfo_int": {"min": 0, "max": 1023, "type": "continuous"},
        }
    },
    "microfreak": {
        "name": "Arturia MicroFreak",
        "description": "Digital oscillator hybrid with analog filter.",
        "price": "$350",
        "controls": {
            "type": {"min": 0, "max": 13, "type": "discrete"},  # Various digital oscillators
            "wave": {"min": 0, "max": 127, "type": "continuous"},
            "timbre": {"min": 0, "max": 127, "type": "continuous"},
            "shape": {"min": 0, "max": 127, "type": "continuous"},
            "filter": {"min": 0, "max": 127, "type": "continuous"},
            "resonance": {"min": 0, "max": 127, "type": "continuous"},
            "attack": {"min": 0, "max": 127, "type": "continuous"},
            "decay": {"min": 0, "max": 127, "type": "continuous"},
            "sustain": {"min": 0, "max": 127, "type": "continuous"},
            "filter_amt": {"min": 0, "max": 127, "type": "continuous"},
            "lfo_rate": {"min": 0, "max": 127, "type": "continuous"},
            "lfo_amt": {"min": 0, "max": 127, "type": "continuous"},
        }
    },
    "monologue": {
        "name": "Korg Monologue",
        "description": "Monophonic analog synth, great for bass and leads.",
        "price": "$300",
        "controls": {
            "vco1_wave": {"min": 0, "max": 2, "type": "discrete"},
            "vco1_octave": {"min": 0, "max": 3, "type": "discrete"},
            "vco2_wave": {"min": 0, "max": 2, "type": "discrete"},
            "vco2_octave": {"min": 0, "max": 3, "type": "discrete"},
            "vco2_pitch": {"min": 0, "max": 1023, "type": "continuous"},
            "cutoff": {"min": 0, "max": 1023, "type": "continuous"},
            "resonance": {"min": 0, "max": 1023, "type": "continuous"},
            "eg_int": {"min": 0, "max": 1023, "type": "continuous"},
            "attack": {"min": 0, "max": 1023, "type": "continuous"},
            "decay": {"min": 0, "max": 1023, "type": "continuous"},
            "sustain": {"min": 0, "max": 1023, "type": "continuous"},
            "release": {"min": 0, "max": 1023, "type": "continuous"},
            "lfo_rate": {"min": 0, "max": 1023, "type": "continuous"},
            "lfo_int": {"min": 0, "max": 1023, "type": "continuous"},
        }
    },
    "bass_station_2": {
        "name": "Novation Bass Station II",
        "description": "Analog mono synth with sub-oscillator, great for bass.",
        "price": "$500",
        "controls": {
            "osc1_wave": {"min": 0, "max": 3, "type": "discrete"},
            "osc1_range": {"min": 0, "max": 4, "type": "discrete"},
            "osc2_wave": {"min": 0, "max": 3, "type": "discrete"},
            "osc2_range": {"min": 0, "max": 4, "type": "discrete"},
            "osc2_fine": {"min": 0, "max": 127, "type": "continuous"},
            "sub_osc": {"min": 0, "max": 127, "type": "continuous"},
            "filter_freq": {"min": 0, "max": 127, "type": "continuous"},
            "resonance": {"min": 0, "max": 127, "type": "continuous"},
            "filter_env": {"min": 0, "max": 127, "type": "continuous"},
            "attack": {"min": 0, "max": 127, "type": "continuous"},
            "decay": {"min": 0, "max": 127, "type": "continuous"},
            "sustain": {"min": 0, "max": 127, "type": "continuous"},
            "release": {"min": 0, "max": 127, "type": "continuous"},
            "lfo_speed": {"min": 0, "max": 127, "type": "continuous"},
        }
    },
    "volca_fm": {
        "name": "Korg Volca FM",
        "description": "6-voice FM synth, compatible with DX7 patches. Great for bells, basses, and electric pianos.",
        "price": "$160",
        "controls": {
            "algorithm": {"min": 1, "max": 32, "type": "discrete"},
            "modulator_attack": {"min": 0, "max": 127, "type": "continuous"},
            "modulator_decay": {"min": 0, "max": 127, "type": "continuous"},
            "carrier_attack": {"min": 0, "max": 127, "type": "continuous"},
            "carrier_decay": {"min": 0, "max": 127, "type": "continuous"},
            "lfo_rate": {"min": 0, "max": 127, "type": "continuous"},
            "lfo_pitch_depth": {"min": 0, "max": 127, "type": "continuous"},
            "chorus": {"min": 0, "max": 127, "type": "continuous"},
        }
    },
    "volca_bass": {
        "name": "Korg Volca Bass",
        "description": "3-oscillator analog bass synth with filter. Huge bass sounds.",
        "price": "$160",
        "controls": {
            "vco1": {"min": 0, "max": 127, "type": "continuous"},
            "vco2": {"min": 0, "max": 127, "type": "continuous"},
            "vco3": {"min": 0, "max": 127, "type": "continuous"},
            "vco_pitch_1": {"min": 0, "max": 127, "type": "continuous"},
            "vco_pitch_2": {"min": 0, "max": 127, "type": "continuous"},
            "vco_pitch_3": {"min": 0, "max": 127, "type": "continuous"},
            "attack": {"min": 0, "max": 127, "type": "continuous"},
            "decay_release": {"min": 0, "max": 127, "type": "continuous"},
            "cutoff_eg_int": {"min": 0, "max": 127, "type": "continuous"},
            "gate_time": {"min": 0, "max": 127, "type": "continuous"},
            "peak": {"min": 0, "max": 127, "type": "continuous"},
            "cutoff": {"min": 0, "max": 127, "type": "continuous"},
        }
    },
    "volca_modular": {
        "name": "Korg Volca Modular",
        "description": "Semi-modular West Coast synth. Unique, experimental sounds.",
        "price": "$200",
        "controls": {
            "ratio": {"min": 0, "max": 127, "type": "continuous"},
            "fold": {"min": 0, "max": 127, "type": "continuous"},
            "source_mix": {"min": 0, "max": 127, "type": "continuous"},
            "cutoff": {"min": 0, "max": 127, "type": "continuous"},
            "shape": {"min": 0, "max": 127, "type": "continuous"},
            "mod_rate": {"min": 0, "max": 127, "type": "continuous"},
            "mod_int": {"min": 0, "max": 127, "type": "continuous"},
            "attack": {"min": 0, "max": 127, "type": "continuous"},
            "release": {"min": 0, "max": 127, "type": "continuous"},
        }
    },
    "circuit_tracks": {
        "name": "Novation Circuit Tracks",
        "description": "Groovebox with 2 synth tracks and 4 drum tracks. Great for full productions.",
        "price": "$400",
        "controls": {
            "osc1_wave": {"min": 0, "max": 127, "type": "continuous"},
            "osc1_v_sw": {"min": 0, "max": 127, "type": "continuous"},
            "osc2_wave": {"min": 0, "max": 127, "type": "continuous"},
            "osc2_v_sw": {"min": 0, "max": 127, "type": "continuous"},
            "osc2_semitones": {"min": 0, "max": 127, "type": "continuous"},
            "filter": {"min": 0, "max": 127, "type": "continuous"},
            "resonance": {"min": 0, "max": 127, "type": "continuous"},
            "mod_rate": {"min": 0, "max": 127, "type": "continuous"},
            "env_attack": {"min": 0, "max": 127, "type": "continuous"},
            "env_decay": {"min": 0, "max": 127, "type": "continuous"},
            "env_sustain": {"min": 0, "max": 127, "type": "continuous"},
            "env_release": {"min": 0, "max": 127, "type": "continuous"},
        }
    },
    "peak": {
        "name": "Novation Peak",
        "description": "8-voice hybrid synth with analog filters. Premium quality sound.",
        "price": "$1,400",
        "controls": {
            "osc1_wave": {"min": 0, "max": 4, "type": "discrete"},
            "osc1_coarse": {"min": 0, "max": 127, "type": "continuous"},
            "osc2_wave": {"min": 0, "max": 4, "type": "discrete"},
            "osc2_coarse": {"min": 0, "max": 127, "type": "continuous"},
            "osc2_fine": {"min": 0, "max": 127, "type": "continuous"},
            "osc3_wave": {"min": 0, "max": 4, "type": "discrete"},
            "filter_freq": {"min": 0, "max": 127, "type": "continuous"},
            "filter_res": {"min": 0, "max": 127, "type": "continuous"},
            "filter_env": {"min": 0, "max": 127, "type": "continuous"},
            "amp_attack": {"min": 0, "max": 127, "type": "continuous"},
            "amp_decay": {"min": 0, "max": 127, "type": "continuous"},
            "amp_sustain": {"min": 0, "max": 127, "type": "continuous"},
            "amp_release": {"min": 0, "max": 127, "type": "continuous"},
            "lfo1_rate": {"min": 0, "max": 127, "type": "continuous"},
        }
    },
    "summit": {
        "name": "Novation Summit",
        "description": "16-voice bi-timbral flagship synth. Two Peaks in one.",
        "price": "$2,000",
        "controls": {
            "osc1_wave": {"min": 0, "max": 4, "type": "discrete"},
            "osc1_coarse": {"min": 0, "max": 127, "type": "continuous"},
            "osc2_wave": {"min": 0, "max": 4, "type": "discrete"},
            "osc2_coarse": {"min": 0, "max": 127, "type": "continuous"},
            "osc2_fine": {"min": 0, "max": 127, "type": "continuous"},
            "osc3_wave": {"min": 0, "max": 4, "type": "discrete"},
            "filter_freq": {"min": 0, "max": 127, "type": "continuous"},
            "filter_res": {"min": 0, "max": 127, "type": "continuous"},
            "filter_env": {"min": 0, "max": 127, "type": "continuous"},
            "amp_attack": {"min": 0, "max": 127, "type": "continuous"},
            "amp_decay": {"min": 0, "max": 127, "type": "continuous"},
            "amp_sustain": {"min": 0, "max": 127, "type": "continuous"},
            "amp_release": {"min": 0, "max": 127, "type": "continuous"},
            "lfo1_rate": {"min": 0, "max": 127, "type": "continuous"},
        }
    },
    "mininova": {
        "name": "Novation MiniNova",
        "description": "Compact VA synth with vocoder. Versatile and portable.",
        "price": "$400",
        "controls": {
            "osc1_wave": {"min": 0, "max": 20, "type": "discrete"},
            "osc1_pitch": {"min": 0, "max": 127, "type": "continuous"},
            "osc2_wave": {"min": 0, "max": 20, "type": "discrete"},
            "osc2_pitch": {"min": 0, "max": 127, "type": "continuous"},
            "osc_mix": {"min": 0, "max": 127, "type": "continuous"},
            "filter_freq": {"min": 0, "max": 127, "type": "continuous"},
            "filter_res": {"min": 0, "max": 127, "type": "continuous"},
            "filter_env": {"min": 0, "max": 127, "type": "continuous"},
            "amp_attack": {"min": 0, "max": 127, "type": "continuous"},
            "amp_decay": {"min": 0, "max": 127, "type": "continuous"},
            "amp_sustain": {"min": 0, "max": 127, "type": "continuous"},
            "amp_release": {"min": 0, "max": 127, "type": "continuous"},
        }
    },
}


def translate_to_hardware(synth_descriptor, hardware_id: str) -> Optional[SynthHardwareConfig]:
    """Translate a synth descriptor to hardware synth settings.

    Args:
        synth_descriptor: SynthDescriptor from synth_analyzer
        hardware_id: ID of the target hardware (e.g., "volca_keys")

    Returns:
        SynthHardwareConfig with knob positions for the hardware
    """
    if hardware_id not in SYNTH_HARDWARE:
        return None

    hw = SYNTH_HARDWARE[hardware_id]

    # Korg Volcas
    if hardware_id == "volca_keys":
        return _translate_volca_keys(synth_descriptor, hw)
    elif hardware_id == "volca_fm":
        return _translate_volca_fm(synth_descriptor, hw)
    elif hardware_id == "volca_bass":
        return _translate_volca_bass(synth_descriptor, hw)
    elif hardware_id == "volca_modular":
        return _translate_volca_modular(synth_descriptor, hw)
    # Korg Logues
    elif hardware_id == "minilogue":
        return _translate_minilogue(synth_descriptor, hw)
    elif hardware_id == "monologue":
        return _translate_monologue(synth_descriptor, hw)
    # Arturia
    elif hardware_id == "microfreak":
        return _translate_microfreak(synth_descriptor, hw)
    # Novation
    elif hardware_id == "bass_station_2":
        return _translate_bass_station(synth_descriptor, hw)
    elif hardware_id == "circuit_tracks":
        return _translate_circuit_tracks(synth_descriptor, hw)
    elif hardware_id == "peak":
        return _translate_peak(synth_descriptor, hw)
    elif hardware_id == "summit":
        return _translate_peak(synth_descriptor, hw)  # Same engine as Peak
    elif hardware_id == "mininova":
        return _translate_mininova(synth_descriptor, hw)

    return None


def _translate_volca_keys(desc, hw: dict) -> SynthHardwareConfig:
    """Translate to Korg Volca Keys settings."""
    controls = []
    notes = []

    # Voice mode based on unison voices
    if desc.oscillator.num_voices > 1:
        voice_mode = 1  # Unison
        controls.append(SynthControl("VOICE", 1, "Unison", "Creates thick detuned sound"))
        detune_val = min(127, int(desc.oscillator.detune * 2))
        controls.append(SynthControl("DETUNE", detune_val, f"{detune_val}", "Spread between voices"))
    else:
        controls.append(SynthControl("VOICE", 0, "Poly", "Standard polyphonic mode"))
        controls.append(SynthControl("DETUNE", 0, "0", ""))

    # Filter cutoff (normalized 0-1 to 0-127)
    cutoff_val = int(desc.filter.cutoff_normalized * 127)
    controls.append(SynthControl("VCF CUTOFF", cutoff_val, f"{cutoff_val}",
        "Turn down for darker sound" if cutoff_val < 80 else ""))

    # VCF EG intensity based on filter movement (approximated)
    eg_int = 64  # Middle position as default
    controls.append(SynthControl("VCF EG INT", eg_int, f"{eg_int}", "Filter envelope amount"))

    # ADSR envelope
    attack_val = min(127, int(desc.amp_envelope.attack_ms / 10))
    controls.append(SynthControl("ATTACK", attack_val, f"{attack_val}",
        "Slow attack for pads" if attack_val > 50 else "Fast attack for plucks"))

    decay_val = min(127, int(desc.amp_envelope.decay_ms / 20))
    sustain_val = int(desc.amp_envelope.sustain * 127)
    controls.append(SynthControl("DECAY/RELEASE", decay_val, f"{decay_val}", ""))
    controls.append(SynthControl("SUSTAIN", sustain_val, f"{sustain_val}", ""))

    # Delay if reverb detected (Volca Keys has delay, not reverb)
    if desc.has_delay or desc.has_reverb:
        controls.append(SynthControl("DELAY TIME", 64, "64", "Add space"))
        controls.append(SynthControl("DELAY FEEDBACK", 50, "50", ""))
        notes.append("Volca Keys has delay instead of reverb - use it to add space.")

    # Tips based on oscillator type
    if desc.oscillator.type == "saw":
        notes.append("Volca Keys is perfect for this - it's based on sawtooth waves.")
    elif desc.oscillator.type == "square":
        notes.append("Volca Keys uses saw waves; try the RING control for more hollow tones.")

    return SynthHardwareConfig(
        synth_name=hw["name"],
        synth_model="volca_keys",
        description=hw["description"],
        controls=controls,
        notes=notes,
    )


def _translate_minilogue(desc, hw: dict) -> SynthHardwareConfig:
    """Translate to Korg Minilogue settings."""
    controls = []
    notes = []

    # VCO1 wave selection
    wave_map = {"saw": 0, "triangle": 1, "square": 2, "sine": 1}
    wave_val = wave_map.get(desc.oscillator.type, 0)
    wave_names = ["SAW", "TRI", "SQR"]
    controls.append(SynthControl("VCO1 WAVE", wave_val, wave_names[wave_val], ""))
    controls.append(SynthControl("VCO1 OCTAVE", 1, "8'", "Middle octave"))

    # VCO2 for detuning/unison effect
    if desc.oscillator.num_voices > 1:
        controls.append(SynthControl("VCO2 WAVE", wave_val, wave_names[wave_val], ""))
        detune_val = int(512 + desc.oscillator.detune * 5)  # Center + detune
        controls.append(SynthControl("VCO2 PITCH", min(1023, detune_val), f"{detune_val}", "Detune for thickness"))
        notes.append("Use Voice Mode: UNISON for maximum fatness.")

    # Filter
    cutoff_val = int(desc.filter.cutoff_normalized * 1023)
    res_val = int(desc.filter.resonance * 1023)
    controls.append(SynthControl("CUTOFF", cutoff_val, f"{cutoff_val}", ""))
    controls.append(SynthControl("RESONANCE", res_val, f"{res_val}",
        "Add resonance for quack" if res_val > 300 else ""))

    # Envelope
    attack_val = min(1023, int(desc.amp_envelope.attack_ms * 2))
    decay_val = min(1023, int(desc.amp_envelope.decay_ms))
    sustain_val = int(desc.amp_envelope.sustain * 1023)
    release_val = min(1023, int(desc.amp_envelope.release_ms))

    controls.append(SynthControl("ATTACK", attack_val, f"{attack_val}", ""))
    controls.append(SynthControl("DECAY", decay_val, f"{decay_val}", ""))
    controls.append(SynthControl("SUSTAIN", sustain_val, f"{sustain_val}", ""))
    controls.append(SynthControl("RELEASE", release_val, f"{release_val}", ""))

    # LFO
    if desc.lfo and desc.lfo.rate_hz > 0:
        lfo_rate = min(1023, int(desc.lfo.rate_hz * 100))
        lfo_int = int(desc.lfo.depth * 1023)
        controls.append(SynthControl("LFO RATE", lfo_rate, f"{lfo_rate}", ""))
        controls.append(SynthControl("LFO INT", lfo_int, f"{lfo_int}", f"Modulating {desc.lfo.target}"))

    if desc.has_chorus:
        notes.append("Enable the built-in DELAY in short mode for chorus-like effect.")

    return SynthHardwareConfig(
        synth_name=hw["name"],
        synth_model="minilogue",
        description=hw["description"],
        controls=controls,
        notes=notes,
    )


def _translate_microfreak(desc, hw: dict) -> SynthHardwareConfig:
    """Translate to Arturia MicroFreak settings."""
    controls = []
    notes = []

    # Oscillator type - MicroFreak has digital oscillators
    osc_map = {
        "saw": (0, "Basic Waves"),
        "square": (0, "Basic Waves"),
        "sine": (0, "Basic Waves"),
        "triangle": (0, "Basic Waves"),
    }
    osc_type, osc_name = osc_map.get(desc.oscillator.type, (0, "Basic Waves"))
    controls.append(SynthControl("TYPE", osc_type, osc_name, ""))

    # Wave morph based on oscillator type
    wave_map = {"saw": 0, "square": 64, "triangle": 32, "sine": 96}
    wave_val = wave_map.get(desc.oscillator.type, 0)
    controls.append(SynthControl("WAVE", wave_val, f"{wave_val}", "Morphs between waveforms"))

    # Filter (analog!)
    cutoff_val = int(desc.filter.cutoff_normalized * 127)
    res_val = int(desc.filter.resonance * 127)
    controls.append(SynthControl("FILTER", cutoff_val, f"{cutoff_val}", ""))
    controls.append(SynthControl("RESONANCE", res_val, f"{res_val}", ""))

    # Envelope
    attack_val = min(127, int(desc.amp_envelope.attack_ms / 10))
    decay_val = min(127, int(desc.amp_envelope.decay_ms / 20))
    sustain_val = int(desc.amp_envelope.sustain * 127)

    controls.append(SynthControl("ATTACK", attack_val, f"{attack_val}", ""))
    controls.append(SynthControl("DECAY", decay_val, f"{decay_val}", ""))
    controls.append(SynthControl("SUSTAIN", sustain_val, f"{sustain_val}", ""))

    # LFO
    if desc.lfo and desc.lfo.rate_hz > 0:
        lfo_rate = min(127, int(desc.lfo.rate_hz * 20))
        lfo_amt = int(desc.lfo.depth * 127)
        controls.append(SynthControl("LFO RATE", lfo_rate, f"{lfo_rate}", ""))
        controls.append(SynthControl("LFO AMT", lfo_amt, f"{lfo_amt}", ""))

    if desc.oscillator.num_voices > 1:
        notes.append("Try the 'Superwave' oscillator type for built-in unison.")

    notes.append("MicroFreak has a unique analog filter paired with digital oscillators.")

    return SynthHardwareConfig(
        synth_name=hw["name"],
        synth_model="microfreak",
        description=hw["description"],
        controls=controls,
        notes=notes,
    )


def _translate_monologue(desc, hw: dict) -> SynthHardwareConfig:
    """Translate to Korg Monologue settings."""
    controls = []
    notes = []

    # Wave selection
    wave_map = {"saw": 0, "triangle": 1, "square": 2, "sine": 1}
    wave_val = wave_map.get(desc.oscillator.type, 0)
    wave_names = ["SAW", "TRI", "SQR"]

    controls.append(SynthControl("VCO1 WAVE", wave_val, wave_names[wave_val], ""))
    controls.append(SynthControl("VCO1 OCTAVE", 1, "8'", ""))

    # VCO2 for thickness
    if desc.oscillator.num_voices > 1 or desc.oscillator.detune > 0:
        controls.append(SynthControl("VCO2 WAVE", wave_val, wave_names[wave_val], ""))
        detune_val = int(512 + desc.oscillator.detune * 5)
        controls.append(SynthControl("VCO2 PITCH", min(1023, detune_val), f"{detune_val}", "Slight detune"))

    # Filter
    cutoff_val = int(desc.filter.cutoff_normalized * 1023)
    res_val = int(desc.filter.resonance * 1023)
    controls.append(SynthControl("CUTOFF", cutoff_val, f"{cutoff_val}", ""))
    controls.append(SynthControl("RESONANCE", res_val, f"{res_val}", ""))

    # Envelope
    attack_val = min(1023, int(desc.amp_envelope.attack_ms * 2))
    decay_val = min(1023, int(desc.amp_envelope.decay_ms))
    sustain_val = int(desc.amp_envelope.sustain * 1023)
    release_val = min(1023, int(desc.amp_envelope.release_ms))

    controls.append(SynthControl("ATTACK", attack_val, f"{attack_val}", ""))
    controls.append(SynthControl("DECAY", decay_val, f"{decay_val}", ""))
    controls.append(SynthControl("SUSTAIN", sustain_val, f"{sustain_val}", ""))
    controls.append(SynthControl("RELEASE", release_val, f"{release_val}", ""))

    notes.append("Monologue is monophonic - perfect for bass and lead lines.")

    return SynthHardwareConfig(
        synth_name=hw["name"],
        synth_model="monologue",
        description=hw["description"],
        controls=controls,
        notes=notes,
    )


def _translate_bass_station(desc, hw: dict) -> SynthHardwareConfig:
    """Translate to Novation Bass Station II settings."""
    controls = []
    notes = []

    # Oscillator wave
    wave_map = {"saw": 0, "triangle": 2, "square": 1, "sine": 3}
    wave_val = wave_map.get(desc.oscillator.type, 0)
    wave_names = ["SAW", "SQR", "TRI", "SINE"]

    controls.append(SynthControl("OSC1 WAVE", wave_val, wave_names[wave_val], ""))
    controls.append(SynthControl("OSC1 RANGE", 2, "8'", "Middle range"))

    # OSC2 for detuning
    if desc.oscillator.num_voices > 1:
        controls.append(SynthControl("OSC2 WAVE", wave_val, wave_names[wave_val], ""))
        detune_val = min(127, int(64 + desc.oscillator.detune))
        controls.append(SynthControl("OSC2 FINE", detune_val, f"{detune_val}", "Detune for fatness"))

    # Sub oscillator
    if desc.oscillator.sub_osc:
        controls.append(SynthControl("SUB OSC", 80, "80", "Add low-end weight"))
        notes.append("Sub oscillator adds serious low-end punch.")

    # Filter
    cutoff_val = int(desc.filter.cutoff_normalized * 127)
    res_val = int(desc.filter.resonance * 127)
    filter_env = int(desc.filter.cutoff_normalized * 64)  # Moderate env amount

    controls.append(SynthControl("FILTER FREQ", cutoff_val, f"{cutoff_val}", ""))
    controls.append(SynthControl("RESONANCE", res_val, f"{res_val}", ""))
    controls.append(SynthControl("FILTER ENV", filter_env, f"{filter_env}", ""))

    # Envelope
    attack_val = min(127, int(desc.amp_envelope.attack_ms / 10))
    decay_val = min(127, int(desc.amp_envelope.decay_ms / 20))
    sustain_val = int(desc.amp_envelope.sustain * 127)
    release_val = min(127, int(desc.amp_envelope.release_ms / 20))

    controls.append(SynthControl("ATTACK", attack_val, f"{attack_val}", ""))
    controls.append(SynthControl("DECAY", decay_val, f"{decay_val}", ""))
    controls.append(SynthControl("SUSTAIN", sustain_val, f"{sustain_val}", ""))
    controls.append(SynthControl("RELEASE", release_val, f"{release_val}", ""))

    notes.append("Bass Station II excels at bass sounds but handles leads well too.")

    return SynthHardwareConfig(
        synth_name=hw["name"],
        synth_model="bass_station_2",
        description=hw["description"],
        controls=controls,
        notes=notes,
    )


def _translate_volca_fm(desc, hw: dict) -> SynthHardwareConfig:
    """Translate to Korg Volca FM settings."""
    controls = []
    notes = []

    # FM synthesis uses algorithms - suggest based on sound type
    if desc.oscillator.type == "sine":
        algo = 1  # Simple stacked operators
        controls.append(SynthControl("ALGORITHM", algo, "1", "Clean sine stacks"))
    elif desc.oscillator.type == "saw":
        algo = 5  # More complex for harmonic richness
        controls.append(SynthControl("ALGORITHM", algo, "5", "Harmonically rich"))
    else:
        algo = 8
        controls.append(SynthControl("ALGORITHM", algo, "8", "Versatile bell-like"))

    # Modulator envelope (affects brightness/harmonics)
    mod_attack = min(127, int(desc.amp_envelope.attack_ms / 5))
    mod_decay = min(127, int(desc.amp_envelope.decay_ms / 10))
    controls.append(SynthControl("MOD ATTACK", mod_attack, f"{mod_attack}", "Modulator brightness attack"))
    controls.append(SynthControl("MOD DECAY", mod_decay, f"{mod_decay}", "Brightness decay"))

    # Carrier envelope (affects volume)
    car_attack = min(127, int(desc.amp_envelope.attack_ms / 10))
    car_decay = min(127, int(desc.amp_envelope.decay_ms / 15))
    controls.append(SynthControl("CAR ATTACK", car_attack, f"{car_attack}", ""))
    controls.append(SynthControl("CAR DECAY", car_decay, f"{car_decay}", ""))

    # LFO for vibrato
    if desc.lfo and desc.lfo.rate_hz > 0:
        lfo_rate = min(127, int(desc.lfo.rate_hz * 15))
        lfo_depth = int(desc.lfo.depth * 80)
        controls.append(SynthControl("LFO RATE", lfo_rate, f"{lfo_rate}", ""))
        controls.append(SynthControl("LFO DEPTH", lfo_depth, f"{lfo_depth}", "Pitch modulation"))

    if desc.has_chorus:
        controls.append(SynthControl("CHORUS", 80, "80", "Built-in chorus"))
        notes.append("Volca FM's chorus adds nice width to FM sounds.")

    notes.append("FM synthesis is different from subtractive - experiment with algorithm selection.")
    notes.append("Try importing classic DX7 patches via SysEx for more options.")

    return SynthHardwareConfig(
        synth_name=hw["name"],
        synth_model="volca_fm",
        description=hw["description"],
        controls=controls,
        notes=notes,
    )


def _translate_volca_bass(desc, hw: dict) -> SynthHardwareConfig:
    """Translate to Korg Volca Bass settings."""
    controls = []
    notes = []

    # VCO levels - Volca Bass has 3 oscillators
    if desc.oscillator.num_voices > 1:
        # Use multiple oscillators for thickness
        controls.append(SynthControl("VCO1", 100, "100", "Main oscillator"))
        controls.append(SynthControl("VCO2", 90, "90", "Adds thickness"))
        controls.append(SynthControl("VCO3", 80, "80", "Extra fatness"))
        notes.append("All 3 oscillators engaged for maximum thickness.")
    else:
        controls.append(SynthControl("VCO1", 127, "127", "Single oscillator"))
        controls.append(SynthControl("VCO2", 0, "0", "Off"))
        controls.append(SynthControl("VCO3", 0, "0", "Off"))

    # Detune oscillators if needed
    if desc.oscillator.detune > 0:
        detune_val = min(127, int(64 + desc.oscillator.detune * 0.5))
        controls.append(SynthControl("VCO2 PITCH", detune_val, f"{detune_val}", "Slight detune"))
        controls.append(SynthControl("VCO3 PITCH", 64 - int(desc.oscillator.detune * 0.3),
                                    f"{64 - int(desc.oscillator.detune * 0.3)}", "Opposite detune"))

    # Filter
    cutoff_val = int(desc.filter.cutoff_normalized * 127)
    peak_val = int(desc.filter.resonance * 127)
    controls.append(SynthControl("CUTOFF", cutoff_val, f"{cutoff_val}", ""))
    controls.append(SynthControl("PEAK", peak_val, f"{peak_val}", "Resonance"))

    # EG Int for filter envelope
    eg_int = min(127, int(desc.filter.cutoff_normalized * 80))
    controls.append(SynthControl("EG INT", eg_int, f"{eg_int}", "Filter envelope amount"))

    # Envelope
    attack_val = min(127, int(desc.amp_envelope.attack_ms / 10))
    decay_val = min(127, int(desc.amp_envelope.decay_ms / 20))
    controls.append(SynthControl("ATTACK", attack_val, f"{attack_val}", ""))
    controls.append(SynthControl("DECAY/REL", decay_val, f"{decay_val}", ""))

    notes.append("Volca Bass excels at thick, squelchy bass lines.")

    return SynthHardwareConfig(
        synth_name=hw["name"],
        synth_model="volca_bass",
        description=hw["description"],
        controls=controls,
        notes=notes,
    )


def _translate_volca_modular(desc, hw: dict) -> SynthHardwareConfig:
    """Translate to Korg Volca Modular settings."""
    controls = []
    notes = []

    # Volca Modular is West Coast style - different approach
    # RATIO affects the carrier/modulator relationship
    if desc.oscillator.type in ("saw", "square"):
        ratio_val = 80  # More complex harmonics
        fold_val = 70
    else:
        ratio_val = 40  # Simpler
        fold_val = 30

    controls.append(SynthControl("RATIO", ratio_val, f"{ratio_val}", "Oscillator ratio"))
    controls.append(SynthControl("FOLD", fold_val, f"{fold_val}", "Wavefolder - adds harmonics"))

    # Source mix
    controls.append(SynthControl("SOURCE MIX", 64, "64", "Balance between sources"))

    # Lowpass gate (West Coast filter)
    cutoff_val = int(desc.filter.cutoff_normalized * 127)
    controls.append(SynthControl("CUTOFF", cutoff_val, f"{cutoff_val}", "Lowpass gate"))

    # Shape
    controls.append(SynthControl("SHAPE", 64, "64", "Waveshaping"))

    # Modulation
    if desc.lfo and desc.lfo.rate_hz > 0:
        mod_rate = min(127, int(desc.lfo.rate_hz * 20))
        mod_int = int(desc.lfo.depth * 100)
        controls.append(SynthControl("MOD RATE", mod_rate, f"{mod_rate}", ""))
        controls.append(SynthControl("MOD INT", mod_int, f"{mod_int}", ""))

    # Envelope
    attack_val = min(127, int(desc.amp_envelope.attack_ms / 10))
    release_val = min(127, int(desc.amp_envelope.release_ms / 20))
    controls.append(SynthControl("ATTACK", attack_val, f"{attack_val}", ""))
    controls.append(SynthControl("RELEASE", release_val, f"{release_val}", ""))

    notes.append("West Coast synthesis is different - embrace the unusual!")
    notes.append("Try patching the micro-patch cables for more complex sounds.")
    notes.append("The FOLD parameter adds harmonics through waveshaping.")

    return SynthHardwareConfig(
        synth_name=hw["name"],
        synth_model="volca_modular",
        description=hw["description"],
        controls=controls,
        notes=notes,
    )


def _translate_circuit_tracks(desc, hw: dict) -> SynthHardwareConfig:
    """Translate to Novation Circuit Tracks settings."""
    controls = []
    notes = []

    # Oscillator waves (Circuit uses wavetable-style morphing)
    wave_map = {"saw": 20, "square": 64, "triangle": 40, "sine": 0}
    wave_val = wave_map.get(desc.oscillator.type, 32)
    controls.append(SynthControl("OSC1 WAVE", wave_val, f"{wave_val}", "Wavetable position"))
    controls.append(SynthControl("OSC2 WAVE", wave_val, f"{wave_val}", ""))

    # Detuning via semitones
    if desc.oscillator.detune > 0:
        semi_offset = min(127, int(64 + desc.oscillator.detune / 10))
        controls.append(SynthControl("OSC2 SEMI", semi_offset, f"{semi_offset}", "Slight detune"))
    else:
        controls.append(SynthControl("OSC2 SEMI", 64, "64", "Unison"))

    # Filter
    cutoff_val = int(desc.filter.cutoff_normalized * 127)
    res_val = int(desc.filter.resonance * 127)
    controls.append(SynthControl("FILTER", cutoff_val, f"{cutoff_val}", ""))
    controls.append(SynthControl("RESONANCE", res_val, f"{res_val}", ""))

    # Modulation rate
    if desc.lfo and desc.lfo.rate_hz > 0:
        mod_rate = min(127, int(desc.lfo.rate_hz * 15))
        controls.append(SynthControl("MOD RATE", mod_rate, f"{mod_rate}", "LFO speed"))

    # Envelope ADSR
    attack_val = min(127, int(desc.amp_envelope.attack_ms / 10))
    decay_val = min(127, int(desc.amp_envelope.decay_ms / 20))
    sustain_val = int(desc.amp_envelope.sustain * 127)
    release_val = min(127, int(desc.amp_envelope.release_ms / 20))

    controls.append(SynthControl("ATTACK", attack_val, f"{attack_val}", ""))
    controls.append(SynthControl("DECAY", decay_val, f"{decay_val}", ""))
    controls.append(SynthControl("SUSTAIN", sustain_val, f"{sustain_val}", ""))
    controls.append(SynthControl("RELEASE", release_val, f"{release_val}", ""))

    notes.append("Circuit Tracks has a Nova-based synth engine - very versatile.")
    notes.append("Use the Components software to access all parameters.")

    return SynthHardwareConfig(
        synth_name=hw["name"],
        synth_model="circuit_tracks",
        description=hw["description"],
        controls=controls,
        notes=notes,
    )


def _translate_peak(desc, hw: dict) -> SynthHardwareConfig:
    """Translate to Novation Peak/Summit settings."""
    controls = []
    notes = []

    # Oscillator waves
    wave_map = {"saw": 0, "triangle": 1, "square": 2, "sine": 3}
    wave_val = wave_map.get(desc.oscillator.type, 0)
    wave_names = ["SAW", "TRI", "SQR", "SINE", "WTBL"]
    controls.append(SynthControl("OSC1 WAVE", wave_val, wave_names[wave_val], ""))
    controls.append(SynthControl("OSC2 WAVE", wave_val, wave_names[wave_val], ""))
    controls.append(SynthControl("OSC3 WAVE", wave_val, wave_names[wave_val], "Third oscillator"))

    # Detuning
    if desc.oscillator.detune > 0:
        fine_val = min(127, int(64 + desc.oscillator.detune))
        controls.append(SynthControl("OSC2 FINE", fine_val, f"{fine_val}", "Detune for thickness"))
    else:
        controls.append(SynthControl("OSC2 FINE", 64, "64", "Centered"))

    # Filter (analog!)
    cutoff_val = int(desc.filter.cutoff_normalized * 127)
    res_val = int(desc.filter.resonance * 127)
    env_amt = int(desc.filter.cutoff_normalized * 64)

    controls.append(SynthControl("FILTER FREQ", cutoff_val, f"{cutoff_val}", "Dual analog filters"))
    controls.append(SynthControl("RESONANCE", res_val, f"{res_val}", ""))
    controls.append(SynthControl("FILTER ENV", env_amt, f"{env_amt}", "Envelope to filter"))

    # Amp envelope
    attack_val = min(127, int(desc.amp_envelope.attack_ms / 10))
    decay_val = min(127, int(desc.amp_envelope.decay_ms / 20))
    sustain_val = int(desc.amp_envelope.sustain * 127)
    release_val = min(127, int(desc.amp_envelope.release_ms / 20))

    controls.append(SynthControl("AMP ATTACK", attack_val, f"{attack_val}", ""))
    controls.append(SynthControl("AMP DECAY", decay_val, f"{decay_val}", ""))
    controls.append(SynthControl("AMP SUSTAIN", sustain_val, f"{sustain_val}", ""))
    controls.append(SynthControl("AMP RELEASE", release_val, f"{release_val}", ""))

    # LFO
    if desc.lfo and desc.lfo.rate_hz > 0:
        lfo_rate = min(127, int(desc.lfo.rate_hz * 15))
        controls.append(SynthControl("LFO1 RATE", lfo_rate, f"{lfo_rate}", f"Modulating {desc.lfo.target}"))

    notes.append("Peak has Oxford oscillators with NCOs - incredibly deep sound design.")
    if desc.has_reverb:
        notes.append("Use the built-in reverb for lush, ambient sounds.")
    if desc.oscillator.num_voices > 1:
        notes.append("Enable Diverge for analog-style voice spreading.")

    return SynthHardwareConfig(
        synth_name=hw["name"],
        synth_model=hw.get("model", "peak"),
        description=hw["description"],
        controls=controls,
        notes=notes,
    )


def _translate_mininova(desc, hw: dict) -> SynthHardwareConfig:
    """Translate to Novation MiniNova settings."""
    controls = []
    notes = []

    # MiniNova has many waveforms - map to closest
    wave_map = {"saw": 1, "square": 5, "triangle": 3, "sine": 0}
    wave_val = wave_map.get(desc.oscillator.type, 1)
    wave_names = {0: "SINE", 1: "SAW", 3: "TRI", 5: "SQR/PWM"}

    controls.append(SynthControl("OSC1 WAVE", wave_val, wave_names.get(wave_val, "SAW"), ""))
    controls.append(SynthControl("OSC2 WAVE", wave_val, wave_names.get(wave_val, "SAW"), ""))

    # Pitch for detune
    if desc.oscillator.detune > 0:
        pitch_offset = min(127, int(64 + desc.oscillator.detune / 2))
        controls.append(SynthControl("OSC2 PITCH", pitch_offset, f"{pitch_offset}", "Detune"))

    # Mix between oscillators
    controls.append(SynthControl("OSC MIX", 64, "64", "Balance OSC1/OSC2"))

    # Filter
    cutoff_val = int(desc.filter.cutoff_normalized * 127)
    res_val = int(desc.filter.resonance * 127)
    env_amt = int(desc.filter.cutoff_normalized * 64)

    controls.append(SynthControl("FILTER FREQ", cutoff_val, f"{cutoff_val}", ""))
    controls.append(SynthControl("RESONANCE", res_val, f"{res_val}", ""))
    controls.append(SynthControl("FILTER ENV", env_amt, f"{env_amt}", ""))

    # Envelope
    attack_val = min(127, int(desc.amp_envelope.attack_ms / 10))
    decay_val = min(127, int(desc.amp_envelope.decay_ms / 20))
    sustain_val = int(desc.amp_envelope.sustain * 127)
    release_val = min(127, int(desc.amp_envelope.release_ms / 20))

    controls.append(SynthControl("ATTACK", attack_val, f"{attack_val}", ""))
    controls.append(SynthControl("DECAY", decay_val, f"{decay_val}", ""))
    controls.append(SynthControl("SUSTAIN", sustain_val, f"{sustain_val}", ""))
    controls.append(SynthControl("RELEASE", release_val, f"{release_val}", ""))

    notes.append("MiniNova has the Animate feature - try assigning parameters to it.")
    if desc.has_chorus or desc.has_reverb:
        notes.append("Built-in effects include great chorus, reverb, and vocoder.")

    return SynthHardwareConfig(
        synth_name=hw["name"],
        synth_model="mininova",
        description=hw["description"],
        controls=controls,
        notes=notes,
    )


def get_available_hardware() -> list[dict]:
    """Get list of available hardware synths."""
    return [
        {
            "id": hw_id,
            "name": hw["name"],
            "description": hw["description"],
            "price": hw.get("price", ""),
        }
        for hw_id, hw in SYNTH_HARDWARE.items()
    ]
