"""synth_patch translator: SynthDescriptor dict → client param dicts."""

from tone_forge.synth_patch import (
    pad_params_from_descriptor,
    synth_patch_from_descriptor,
    wavetable_params_from_descriptor,
)


def _descriptor(**overrides):
    base = {
        "oscillator": {
            "type": "saw",
            "detune": 12.0,
            "num_voices": 2,
            "sub_osc": False,
            "pulse_width": 0.5,
        },
        "filter": {
            "type": "lowpass",
            "cutoff_hz": 3200.0,
            "cutoff_normalized": 0.4,
            "resonance": 0.3,
            "envelope_amount": 0.2,
        },
        "amp_envelope": {
            "attack_ms": 5.0,
            "decay_ms": 250.0,
            "sustain": 0.7,
            "release_ms": 400.0,
        },
        "brightness": 0.5,
        "movement": 0.1,
        "stereo_width": 0.2,
        "has_chorus": False,
        "has_phaser": False,
        "has_reverb": True,
        "has_delay": False,
    }
    base.update(overrides)
    return base


class TestWavetableParams:
    def test_basic_mapping(self):
        p = wavetable_params_from_descriptor(_descriptor())
        assert p["attackSec"] == 0.005
        assert p["decaySec"] == 0.25
        assert p["sustainLevel"] == 0.7
        assert p["releaseSec"] == 0.4
        assert p["cutoffHz"] == 3200.0
        assert p["resonance"] == 0.3
        assert p["detuneCents"] == 12.0

    def test_keys_mirror_swift_struct(self):
        p = wavetable_params_from_descriptor(_descriptor())
        assert set(p) == {
            "attackSec", "decaySec", "sustainLevel", "releaseSec",
            "cutoffHz", "resonance", "detuneCents",
        }
        # masterGain deliberately absent — client gain staging owns it.
        assert "masterGain" not in p

    def test_clamps_hostile_values(self):
        d = _descriptor(
            filter={"cutoff_hz": 99_000.0, "resonance": 1.5},
            amp_envelope={"attack_ms": -4, "decay_ms": 60_000,
                          "sustain": 2.0, "release_ms": 0},
        )
        p = wavetable_params_from_descriptor(d)
        assert p["cutoffHz"] == 16_000.0
        assert p["resonance"] == 0.85
        assert p["attackSec"] == 0.001
        assert p["decaySec"] == 4.0
        assert p["sustainLevel"] == 1.0
        assert p["releaseSec"] == 0.05

    def test_dark_osc_pulls_cutoff_down(self):
        d = _descriptor(oscillator={"type": "sine", "detune": 0.0, "num_voices": 1})
        assert wavetable_params_from_descriptor(d)["cutoffHz"] <= 2_500.0

    def test_unison_forces_minimum_spread(self):
        d = _descriptor(oscillator={"type": "saw", "detune": 2.0, "num_voices": 4})
        assert wavetable_params_from_descriptor(d)["detuneCents"] >= 8.0

    def test_empty_descriptor_yields_playable_defaults(self):
        p = wavetable_params_from_descriptor({})
        assert 300.0 <= p["cutoffHz"] <= 16_000.0
        assert 0.0 <= p["resonance"] <= 0.85
        assert p["attackSec"] > 0


class TestPadParams:
    def test_keys_mirror_launchpad_schema(self):
        p = pad_params_from_descriptor(_descriptor())
        assert set(p) == {
            "brightness", "strumMs", "attackMs", "releaseSec",
            "sawMix", "detuneCents",
        }
        assert "masterGain" not in p

    def test_saw_mix_by_osc_type(self):
        for osc_type, expected in [("saw", 1.0), ("triangle", 0.0), ("sine", 0.1)]:
            d = _descriptor(oscillator={"type": osc_type, "detune": 6.0})
            assert pad_params_from_descriptor(d)["sawMix"] == expected

    def test_brightness_midpoint_is_neutral(self):
        p = pad_params_from_descriptor(_descriptor(brightness=0.5))
        assert p["brightness"] == 1.0
        assert pad_params_from_descriptor(_descriptor(brightness=1.0))["brightness"] == 2.0
        assert pad_params_from_descriptor(_descriptor(brightness=0.0))["brightness"] == 0.5

    def test_sharp_attack_disables_strum(self):
        p = pad_params_from_descriptor(_descriptor())
        assert p["strumMs"] == 0.0
        slow = _descriptor(amp_envelope={"attack_ms": 80.0, "release_ms": 900.0})
        assert pad_params_from_descriptor(slow)["strumMs"] == 15.0


class TestBundlePatch:
    def test_wire_shape(self):
        patch = synth_patch_from_descriptor(_descriptor())
        assert set(patch) == {"wavetable", "pad", "source"}
        assert patch["source"] == "synth_analyzer"

    def test_json_safe(self):
        import json
        json.dumps(synth_patch_from_descriptor(_descriptor()))
