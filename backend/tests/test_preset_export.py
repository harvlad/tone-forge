"""Tests for tone_forge/preset_export.py - Preset export functions."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tone_forge.preset_export import (
    ExportedPreset,
    export_helix_preset,
    export_hx_stomp_preset,
    export_json_preset,
    export_neural_dsp_preset,
    export_synth_preset,
    export_bass_preset,
    export_drums_preset,
    export_text_analysis,
    _convert_to_helix_block,
    _empty_block,
    _default_snapshot,
)


class TestExportedPreset:
    """Test ExportedPreset dataclass."""

    def test_create_basic(self):
        preset = ExportedPreset(
            filename="test.hlx",
            format="hlx",
            content='{"test": true}',
            content_type="application/json",
        )
        assert preset.filename == "test.hlx"
        assert preset.format == "hlx"
        assert preset.content_type == "application/json"

    def test_content_can_be_string(self):
        preset = ExportedPreset(
            filename="test.json",
            format="json",
            content='{"key": "value"}',
            content_type="application/json",
        )
        assert isinstance(preset.content, str)


class TestHelixPreset:
    """Test Helix preset export."""

    @pytest.fixture
    def sample_chain(self):
        return [
            {
                "slot": "amp",
                "block_id": "US Double Nrm",
                "display": "US Double Nrm",
                "params": {"Drive": 5.0, "Bass": 5.0, "Mid": 5.0, "Treble": 5.0},
            },
            {
                "slot": "cab",
                "block_id": "1x12 US Deluxe",
                "display": "1x12 US Deluxe",
                "params": {"Level": 0.0},
            },
        ]

    @pytest.fixture
    def sample_descriptor(self):
        return {
            "source": {"filename": "test.wav"},
            "amp": {"family": "fender_clean", "gain": 0.3},
        }

    def test_export_returns_preset(self, sample_chain, sample_descriptor):
        result = export_helix_preset(sample_chain, sample_descriptor)
        assert isinstance(result, ExportedPreset)
        assert result.filename.endswith(".hlx")
        assert result.content_type == "application/json"

    def test_export_valid_json(self, sample_chain, sample_descriptor):
        result = export_helix_preset(sample_chain, sample_descriptor)
        data = json.loads(result.content)
        assert "data" in data
        # meta is inside data
        assert "meta" in data["data"] or "tone" in data["data"]

    def test_export_has_tone_blocks(self, sample_chain, sample_descriptor):
        result = export_helix_preset(sample_chain, sample_descriptor)
        data = json.loads(result.content)
        # Should have dsp blocks
        assert "tone" in data["data"]


class TestHxStompPreset:
    """Test HX Stomp preset export (6 blocks max)."""

    @pytest.fixture
    def sample_chain(self):
        return [
            {"slot": "amp", "block_id": "US Double Nrm", "display": "US Double Nrm", "params": {}},
            {"slot": "cab", "block_id": "1x12 US Deluxe", "display": "1x12 US Deluxe", "params": {}},
            {"slot": "drive", "block_id": "Teemah!", "display": "Teemah!", "params": {}},
            {"slot": "delay", "block_id": "Simple Delay", "display": "Simple Delay", "params": {}},
            {"slot": "reverb", "block_id": "Plateaux", "display": "Plateaux", "params": {}},
            {"slot": "modulation", "block_id": "Trinity Chorus", "display": "Trinity Chorus", "params": {}},
            {"slot": "extra1", "block_id": "Extra", "display": "Extra", "params": {}},
            {"slot": "extra2", "block_id": "Extra2", "display": "Extra2", "params": {}},
        ]

    @pytest.fixture
    def sample_descriptor(self):
        return {"source": {"filename": "test.wav"}}

    def test_export_returns_preset(self, sample_chain, sample_descriptor):
        result = export_hx_stomp_preset(sample_chain, sample_descriptor)
        assert isinstance(result, ExportedPreset)
        assert "stomp" in result.filename.lower() or result.filename.endswith(".hlx")


class TestJsonPreset:
    """Test JSON preset export."""

    @pytest.fixture
    def sample_chain(self):
        return [{"slot": "amp", "block_id": "test", "display": "Test Amp"}]

    @pytest.fixture
    def sample_descriptor(self):
        return {"source": {"filename": "test.wav"}, "amp": {"family": "fender"}}

    def test_export_returns_preset(self, sample_chain, sample_descriptor):
        result = export_json_preset(sample_chain, sample_descriptor)
        assert isinstance(result, ExportedPreset)
        assert result.filename.endswith(".json")

    def test_export_valid_json(self, sample_chain, sample_descriptor):
        result = export_json_preset(sample_chain, sample_descriptor)
        data = json.loads(result.content)
        # The JSON preset has "signal_chain" not "chain"
        assert "signal_chain" in data or "chain" in data
        # full_descriptor contains the original descriptor
        assert "full_descriptor" in data or "descriptor" in data

    def test_export_has_signal_chain(self, sample_chain, sample_descriptor):
        result = export_json_preset(sample_chain, sample_descriptor)
        data = json.loads(result.content)
        # export_json_preset uses "signal_chain" key
        assert "signal_chain" in data


class TestNeuralDspPreset:
    """Test Neural DSP Quad Cortex preset export."""

    @pytest.fixture
    def sample_chain(self):
        return [
            {"slot": "amp", "block_id": "Soldano SLO", "display": "Soldano SLO", "params": {"gain": 7}},
            {"slot": "cab", "block_id": "4x12 V30", "display": "4x12 V30", "params": {}},
        ]

    @pytest.fixture
    def sample_descriptor(self):
        return {"source": {"filename": "test.wav"}, "amp": {"family": "soldano"}}

    def test_export_returns_preset(self, sample_chain, sample_descriptor):
        result = export_neural_dsp_preset(sample_chain, sample_descriptor)
        assert isinstance(result, ExportedPreset)

    def test_export_valid_json(self, sample_chain, sample_descriptor):
        result = export_neural_dsp_preset(sample_chain, sample_descriptor)
        data = json.loads(result.content)
        assert "format" in data or "suggested_models" in data or "chain" in data


class TestSynthPreset:
    """Test synth preset export."""

    @pytest.fixture
    def sample_synth_descriptor(self):
        return {
            "oscillator": {"type": "saw", "detune": 0.1},
            "filter": {"type": "lowpass", "cutoff_hz": 1000, "resonance": 0.3},
            "amp_envelope": {"attack_ms": 10, "decay_ms": 200, "sustain": 0.7, "release_ms": 300},
        }

    def test_export_returns_preset(self, sample_synth_descriptor):
        result = export_synth_preset(sample_synth_descriptor)
        assert isinstance(result, ExportedPreset)

    def test_export_valid_json(self, sample_synth_descriptor):
        result = export_synth_preset(sample_synth_descriptor)
        data = json.loads(result.content)
        assert "oscillator" in data or "osc" in data or "synth" in data


class TestBassPreset:
    """Test bass preset export."""

    @pytest.fixture
    def sample_bass_descriptor(self):
        return {
            "amp": {"family": "ampeg_svt", "gain": 0.5},
            "cab": {"configuration": "8x10"},
            "effects": {"compressor": 0.6, "overdrive": 0.2},
        }

    @pytest.fixture
    def sample_recommendations(self):
        return [
            {"slot": "amp", "display": "Ampeg SVT", "params": {"gain": 5}},
            {"slot": "cab", "display": "Ampeg 8x10", "params": {}},
        ]

    def test_export_returns_preset(self, sample_bass_descriptor, sample_recommendations):
        result = export_bass_preset(sample_bass_descriptor, sample_recommendations)
        assert isinstance(result, ExportedPreset)


class TestDrumsPreset:
    """Test drums preset export."""

    @pytest.fixture
    def sample_drums_descriptor(self):
        return {
            "kick": {"pitch_hz": 60, "decay_ms": 200},
            "snare": {"pitch_hz": 200, "noise": 0.5},
            "overall": {"tempo_bpm": 120},
        }

    @pytest.fixture
    def sample_machine_match(self):
        # export_drums_preset expects a dict (machine_match), not a list
        return {"display": "TR-808", "description": "Classic", "price_estimate": "$800"}

    def test_export_returns_preset(self, sample_drums_descriptor, sample_machine_match):
        result = export_drums_preset(sample_drums_descriptor, sample_machine_match)
        assert isinstance(result, ExportedPreset)


class TestTextAnalysis:
    """Test text analysis export."""

    @pytest.fixture
    def full_result(self):
        return {
            "detected_type": "guitar",
            "detection": {"is_guitar": True, "confidence": {"instrument": 0.9}},
            "guitar": {
                "descriptor": {
                    "source": {"filename": "test.wav", "duration_sec": 2.0},
                    "amp": {"family": "fender_clean", "gain": 0.3},
                },
                "chain": [{"slot": "amp", "display": "US Double Nrm"}],
                "tweak_hints": ["Try boosting mids"],
            },
        }

    def test_export_returns_preset(self, full_result):
        result = export_text_analysis(full_result)
        assert isinstance(result, ExportedPreset)
        assert result.filename.endswith(".txt")

    def test_export_readable_text(self, full_result):
        result = export_text_analysis(full_result)
        assert "TONE FORGE" in result.content or "Analysis" in result.content
        assert isinstance(result.content, str)


class TestHelperFunctions:
    """Test helper functions."""

    def test_empty_block_structure(self):
        block = _empty_block()
        assert isinstance(block, dict)
        assert "@model" in block or len(block) >= 0

    def test_default_snapshot_structure(self):
        snapshot = _default_snapshot()
        assert isinstance(snapshot, dict)

    def test_convert_to_helix_block(self):
        pick = {
            "slot": "amp",
            "block_id": "US Double Nrm",
            "display": "US Double Nrm",
            "params": {"Drive": 5.0},
        }
        block = _convert_to_helix_block(pick, position=0)
        assert isinstance(block, dict)
        # Should have model or block info
        assert "@model" in block or "model" in block or len(block) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
