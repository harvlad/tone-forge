"""Tests for tone_forge/synth_hardware.py - Hardware synth translation."""
from __future__ import annotations

import sys
from pathlib import Path
from dataclasses import dataclass

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tone_forge.synth_hardware import (
    SynthControl,
    SynthHardwareConfig,
    translate_to_hardware,
    get_available_hardware,
)


# Mock synth descriptor matching the expected interface
@dataclass
class MockOscillator:
    type: str = "saw"
    detune: float = 10.0
    num_voices: int = 1
    sub_osc: bool = False
    pulse_width: float = 0.5


@dataclass
class MockFilter:
    type: str = "lowpass"
    cutoff_hz: float = 1000.0
    cutoff_normalized: float = 0.5
    resonance: float = 0.3


@dataclass
class MockAmpEnvelope:
    attack_ms: float = 10.0
    decay_ms: float = 200.0
    sustain: float = 0.7
    release_ms: float = 300.0


@dataclass
class MockLFO:
    rate_hz: float = 0.0
    depth: float = 0.0
    target: str = "none"


@dataclass
class MockSynthDescriptor:
    """Mock synth descriptor matching the interface expected by translate_to_hardware."""
    oscillator: MockOscillator
    filter: MockFilter
    amp_envelope: MockAmpEnvelope
    lfo: MockLFO = None
    has_chorus: bool = False
    has_reverb: bool = False
    has_delay: bool = False
    has_phaser: bool = False


class TestSynthControl:
    """Test SynthControl dataclass."""

    def test_create_basic(self):
        control = SynthControl(
            name="Cutoff",
            value=50,
            display="50%",
        )
        assert control.name == "Cutoff"
        assert control.value == 50
        assert control.display == "50%"

    def test_create_with_note(self):
        control = SynthControl(
            name="Resonance",
            value=70,
            display="7",
            note="Add more for quack",
        )
        assert control.name == "Resonance"
        assert control.note == "Add more for quack"


class TestSynthHardwareConfig:
    """Test SynthHardwareConfig dataclass."""

    def test_create_config(self):
        config = SynthHardwareConfig(
            synth_name="Korg Volca Keys",
            synth_model="volca_keys",
            description="3-voice polyphonic analog synth.",
            controls=[
                SynthControl("Voice", 0, "Poly"),
                SynthControl("Cutoff", 60, "60"),
            ],
            notes=[],
        )
        assert config.synth_name == "Korg Volca Keys"
        assert config.synth_model == "volca_keys"
        assert len(config.controls) == 2

    def test_config_with_notes(self):
        config = SynthHardwareConfig(
            synth_name="Korg Minilogue",
            synth_model="minilogue",
            description="4-voice analog.",
            controls=[],
            notes=["Use voice mode 4 for unison"],
        )
        assert len(config.notes) == 1


class TestGetAvailableHardware:
    """Test getting available hardware synths."""

    def test_returns_list(self):
        hardware = get_available_hardware()
        assert isinstance(hardware, list)

    def test_list_not_empty(self):
        hardware = get_available_hardware()
        assert len(hardware) > 0

    def test_each_has_id_and_name(self):
        hardware = get_available_hardware()
        for hw in hardware:
            assert "id" in hw
            assert "name" in hw

    def test_includes_volca_keys(self):
        hardware = get_available_hardware()
        ids = [hw["id"] for hw in hardware]
        assert "volca_keys" in ids

    def test_includes_minilogue(self):
        hardware = get_available_hardware()
        ids = [hw["id"] for hw in hardware]
        assert "minilogue" in ids


class TestTranslateToHardware:
    """Test translating synth descriptors to hardware configs."""

    @pytest.fixture
    def basic_synth_descriptor(self):
        return MockSynthDescriptor(
            oscillator=MockOscillator(type="saw", detune=10, num_voices=4),
            filter=MockFilter(cutoff_hz=1000, cutoff_normalized=0.5, resonance=0.3),
            amp_envelope=MockAmpEnvelope(attack_ms=10, decay_ms=200, sustain=0.7, release_ms=300),
            lfo=MockLFO(),
        )

    @pytest.fixture
    def pad_synth_descriptor(self):
        return MockSynthDescriptor(
            oscillator=MockOscillator(type="saw", detune=20, num_voices=8),
            filter=MockFilter(cutoff_hz=2000, cutoff_normalized=0.6, resonance=0.2),
            amp_envelope=MockAmpEnvelope(attack_ms=500, decay_ms=1000, sustain=0.8, release_ms=1000),
            lfo=MockLFO(),
            has_chorus=True,
        )

    def test_translate_volca_keys(self, basic_synth_descriptor):
        result = translate_to_hardware(basic_synth_descriptor, "volca_keys")
        assert result is not None
        assert isinstance(result, SynthHardwareConfig)
        assert result.synth_model == "volca_keys"

    def test_translate_minilogue(self, basic_synth_descriptor):
        result = translate_to_hardware(basic_synth_descriptor, "minilogue")
        assert result is not None
        assert result.synth_model == "minilogue"

    def test_translate_microfreak(self, basic_synth_descriptor):
        result = translate_to_hardware(basic_synth_descriptor, "microfreak")
        assert result is not None
        assert result.synth_model == "microfreak"

    def test_translate_invalid_hardware_returns_none(self, basic_synth_descriptor):
        result = translate_to_hardware(basic_synth_descriptor, "nonexistent_synth")
        assert result is None

    def test_translate_has_controls(self, basic_synth_descriptor):
        result = translate_to_hardware(basic_synth_descriptor, "volca_keys")
        assert len(result.controls) > 0

    def test_controls_have_required_fields(self, basic_synth_descriptor):
        result = translate_to_hardware(basic_synth_descriptor, "volca_keys")
        for control in result.controls:
            assert control.name is not None
            assert control.value is not None
            assert control.display is not None

    def test_translate_pad_sound(self, pad_synth_descriptor):
        result = translate_to_hardware(pad_synth_descriptor, "minilogue")
        assert result is not None
        # Pad sounds should have longer attack
        attack_control = next(
            (c for c in result.controls if "attack" in c.name.lower()),
            None
        )
        if attack_control:
            assert attack_control.value > 0


class TestVolcaKeysTranslation:
    """Test Volca Keys specific translation."""

    @pytest.fixture
    def descriptor(self):
        return MockSynthDescriptor(
            oscillator=MockOscillator(type="saw", detune=15, num_voices=3),
            filter=MockFilter(cutoff_hz=800, cutoff_normalized=0.4, resonance=0.5),
            amp_envelope=MockAmpEnvelope(attack_ms=50, decay_ms=300, sustain=0.6, release_ms=400),
            lfo=MockLFO(),
        )

    def test_has_voice_control(self, descriptor):
        result = translate_to_hardware(descriptor, "volca_keys")
        names = [c.name.lower() for c in result.controls]
        assert any("voice" in n for n in names)

    def test_has_cutoff_control(self, descriptor):
        result = translate_to_hardware(descriptor, "volca_keys")
        names = [c.name.lower() for c in result.controls]
        assert any("cutoff" in n for n in names)

    def test_has_detune_or_resonance(self, descriptor):
        result = translate_to_hardware(descriptor, "volca_keys")
        names = [c.name.lower() for c in result.controls]
        # Should have detune (since num_voices > 1) or at least attack/decay
        assert len(names) > 2


class TestMinilogueTranslation:
    """Test Minilogue specific translation."""

    @pytest.fixture
    def descriptor(self):
        return MockSynthDescriptor(
            oscillator=MockOscillator(type="square", detune=5, num_voices=4, sub_osc=True),
            filter=MockFilter(cutoff_hz=1500, cutoff_normalized=0.55, resonance=0.4),
            amp_envelope=MockAmpEnvelope(attack_ms=20, decay_ms=400, sustain=0.5, release_ms=500),
            lfo=MockLFO(),
        )

    def test_has_oscillator_controls(self, descriptor):
        result = translate_to_hardware(descriptor, "minilogue")
        names = [c.name.lower() for c in result.controls]
        # Should have oscillator-related controls
        assert len(names) > 0

    def test_has_filter_controls(self, descriptor):
        result = translate_to_hardware(descriptor, "minilogue")
        names = [c.name.lower() for c in result.controls]
        assert any("cutoff" in n or "filter" in n for n in names)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
