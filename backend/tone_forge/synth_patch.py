"""SynthDescriptor → playable synth-patch translation.

Maps the analysis-side ``SynthDescriptor`` (synth_analyzer.py) onto the
two client synth engines so a song's synth color follows the bundle:

* ``WavetableSynthParams`` — ToneForgeEngine/DSP/WavetableSynth.swift
  (jam-desktop DesktopSynthNode + mobile WavetableSynthNode). Keys here
  must stay byte-identical to that struct's member names; the bundle
  field decodes straight into a Codable mirror.
* ``PadSynthParams`` — mobile PadSynth / launchpad.js slider schema.

The wavetable synth is saw-only with a ladder filter, so oscillator
type degrades to a cutoff/brightness adjustment; the pad synth exposes
a saw/triangle blend, so type maps to ``sawMix``. Neither engine has an
LFO or FX sends — lfo/chorus/reverb flags are dropped here and remain
available to richer targets via preset_export.export_synth_preset.

masterGain is intentionally NOT derived from the descriptor: client
gain staging is loudness-calibrated (D-010/D-013) and a per-song gain
would defeat it. Both dicts omit it so clients keep their defaults.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

# Engine-facing clamps. The wavetable ladder self-oscillates near 1.0
# and the mobile voice bus clips above ~18 kHz cutoff swings, so patch
# values stay inside a conservative playable window regardless of what
# the estimator produced.
_CUTOFF_MIN_HZ = 300.0
_CUTOFF_MAX_HZ = 16_000.0
_RESONANCE_MAX = 0.85
_DETUNE_MAX_CENTS = 30.0
_ATTACK_MAX_SEC = 2.0
_DECAY_MAX_SEC = 4.0
_RELEASE_MIN_SEC = 0.05
_RELEASE_MAX_SEC = 6.0

# Saw/triangle blend per detected oscillator class. Square/noise are
# not representable; they land on bright mixes so perceived harmonic
# density is at least directionally right.
_SAW_MIX_BY_OSC = {
    "saw": 1.0,
    "square": 0.8,
    "triangle": 0.0,
    "sine": 0.1,
    "noise": 0.7,
}


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def _get(d: Optional[Dict[str, Any]], key: str, default: float) -> float:
    if not isinstance(d, dict):
        return default
    v = d.get(key, default)
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def wavetable_params_from_descriptor(descriptor: Dict[str, Any]) -> Dict[str, Any]:
    """Translate a SynthDescriptor dict into WavetableSynthParams keys.

    Returns a JSON-safe dict whose keys mirror the Swift struct:
    attackSec, decaySec, sustainLevel, releaseSec, cutoffHz, resonance,
    detuneCents. masterGain omitted by design (see module docstring).
    """
    osc = descriptor.get("oscillator") or {}
    filt = descriptor.get("filter") or {}
    env = descriptor.get("amp_envelope") or {}

    cutoff = _get(filt, "cutoff_hz", 5_500.0)
    # Dark oscillator classes on a saw-only engine: pull the filter
    # down so the rendered spectrum lands near the target's.
    osc_type = str(osc.get("type", "saw"))
    if osc_type in ("sine", "triangle"):
        cutoff = min(cutoff, 2_500.0)

    # Descriptor detune is the estimator's per-voice spread; the engine
    # expects total spread between its two oscillators.
    detune = abs(_get(osc, "detune", 6.0))
    if _get(osc, "num_voices", 1) >= 2:
        detune = max(detune, 8.0)

    return {
        "attackSec": _clamp(_get(env, "attack_ms", 10.0) / 1000.0, 0.001, _ATTACK_MAX_SEC),
        "decaySec": _clamp(_get(env, "decay_ms", 180.0) / 1000.0, 0.01, _DECAY_MAX_SEC),
        "sustainLevel": _clamp(_get(env, "sustain", 0.65), 0.0, 1.0),
        "releaseSec": _clamp(
            _get(env, "release_ms", 220.0) / 1000.0, _RELEASE_MIN_SEC, _RELEASE_MAX_SEC
        ),
        "cutoffHz": _clamp(cutoff, _CUTOFF_MIN_HZ, _CUTOFF_MAX_HZ),
        "resonance": _clamp(_get(filt, "resonance", 0.18), 0.0, _RESONANCE_MAX),
        "detuneCents": _clamp(detune, 0.0, _DETUNE_MAX_CENTS),
    }


def pad_params_from_descriptor(descriptor: Dict[str, Any]) -> Dict[str, Any]:
    """Translate a SynthDescriptor dict into PadSynthParams keys.

    Keys mirror the launchpad.js slider schema / PadSynthParams.swift:
    brightness, strumMs, attackMs, releaseSec, sawMix, detuneCents.
    masterGain omitted (fixed loudness-calibrated trim on the client).
    """
    osc = descriptor.get("oscillator") or {}
    env = descriptor.get("amp_envelope") or {}

    # PadSynth brightness is a multiplier on its internal cutoff
    # (1.0 = default, 0.5 dull, 2.0 glassy). Map the descriptor's 0-1
    # brightness onto that range geometrically so 0.5 → 1.0 exactly.
    raw_brightness = descriptor.get("brightness")
    if raw_brightness is None:
        raw_brightness = 0.5
    brightness = _clamp(raw_brightness, 0.0, 1.0)
    brightness_mult = 2.0 ** ((brightness - 0.5) * 2.0)

    saw_mix = _SAW_MIX_BY_OSC.get(str(osc.get("type", "saw")), 0.5)

    # Pads voice chords; sharp-attack sources play as block chords,
    # slow pads keep the default gentle strum.
    attack_ms = _clamp(_get(env, "attack_ms", 6.0), 1.0, _ATTACK_MAX_SEC * 1000.0)
    strum_ms = 0.0 if attack_ms < 15.0 else 15.0

    return {
        "brightness": round(brightness_mult, 4),
        "strumMs": strum_ms,
        "attackMs": round(attack_ms, 2),
        "releaseSec": _clamp(
            _get(env, "release_ms", 2_500.0) / 1000.0, _RELEASE_MIN_SEC, _RELEASE_MAX_SEC
        ),
        "sawMix": saw_mix,
        "detuneCents": _clamp(abs(_get(osc, "detune", 6.0)), 0.0, _DETUNE_MAX_CENTS),
    }


def synth_patch_from_descriptor(descriptor: Dict[str, Any]) -> Dict[str, Any]:
    """Bundle-facing synth patch: both client translations + source label.

    Wire shape of the bundle's ``synthPatch`` field.
    """
    return {
        "wavetable": wavetable_params_from_descriptor(descriptor),
        "pad": pad_params_from_descriptor(descriptor),
        "source": "synth_analyzer",
    }
