"""Unit tests for the rules engine.

Tests the mapping from ToneDescriptor to block picks.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tone_forge import rules_engine as rules
from tone_forge.descriptor import (
    Amp, Cab, Compressor, Confidence, Delay, Effects, Guitar,
    Modulation, Reverb, Source, ToneDescriptor, Voicing,
)


def _make_descriptor(
    amp_family: str = "marshall_plexi",
    gain: float = 0.5,
    bass: float = 0.5,
    mid: float = 0.5,
    treble: float = 0.5,
    presence: float = 0.5,
    mid_scoop: float = 0.0,
    cab_config: str = "4x12",
    speaker_char: str = "v30_like",
    amp_conf: float = 0.8,
    delay: Delay | None = None,
    reverb: Reverb | None = None,
    modulation: Modulation | None = None,
    overdrive: bool = False,
) -> ToneDescriptor:
    """Factory for creating test descriptors."""
    from tone_forge.descriptor import OverdrivePedal

    effects = Effects(
        overdrive_pedal=OverdrivePedal(style="tube_screamer", drive=0.5, level=0.5) if overdrive else None,
        compressor=None,
        modulation=modulation,
        delay=delay,
        reverb=reverb,
    )

    return ToneDescriptor(
        source=Source(kind="isolated_guitar", duration_sec=5.0, sample_rate=22050, filename="test.wav"),
        guitar=Guitar(pickup_brightness=0.5, playing_style="chord_riff", estimated_tuning="E_standard"),
        amp=Amp(
            family=amp_family,
            gain=gain,
            voicing=Voicing(bass=bass, mid=mid, treble=treble, presence=presence, mid_scoop=mid_scoop),
            alternates=[],
        ),
        cab=Cab(configuration=cab_config, speaker_character=speaker_char, mic_position="on_axis_cap"),
        effects=effects,
        confidence=Confidence(amp_family=amp_conf, gain=0.7, cab=0.6, effects=0.5),
    )


# Sample catalogs for testing
SAMPLE_AMP_CATALOG = [
    {"id": "amp.plexi_brt", "display": "Brit Plexi Brt", "families": ["marshall_plexi"]},
    {"id": "amp.jcm800", "display": "Brit 2204", "families": ["marshall_jcm"]},
    {"id": "amp.deluxe", "display": "US Deluxe Nrm", "families": ["fender_clean", "tweed"]},
    {"id": "amp.recto", "display": "Cali Rectifire", "families": ["mesa_rectifier"]},
    {"id": "amp.5150", "display": "PV Panama", "families": ["5150_peavey"]},
]

SAMPLE_CAB_CATALOG = [
    {"id": "cab.4x12_v30", "display": "4x12 Cali V30", "configuration": "4x12", "speaker_character": "v30_like"},
    {"id": "cab.4x12_gb", "display": "4x12 Greenback 25", "configuration": "4x12", "speaker_character": "g12m_like"},
    {"id": "cab.2x12_blue", "display": "2x12 Blue Bell", "configuration": "2x12", "speaker_character": "alnico_blue_like"},
    {"id": "cab.1x12_jensen", "display": "1x12 US Deluxe", "configuration": "1x12", "speaker_character": "jensen_like"},
]

SAMPLE_DRIVE_CATALOG = [
    {"id": "drive.ts808", "display": "Scream 808", "style": "tube_screamer"},
    {"id": "drive.klon", "display": "Minotaur", "style": "klon"},
    {"id": "drive.rat", "display": "Vermin Dist", "style": "rat"},
]

SAMPLE_DELAY_CATALOG = [
    {"id": "delay.digital", "display": "Simple Delay", "type": "digital"},
    {"id": "delay.analog", "display": "Transistor Tape", "type": "analog_bbd"},
]

SAMPLE_REVERB_CATALOG = [
    {"id": "reverb.plate", "display": "Plateaux", "type": "plate"},
    {"id": "reverb.hall", "display": "Ganymede", "type": "hall"},
    {"id": "reverb.room", "display": "Glitz", "type": "room"},
]

SAMPLE_MOD_CATALOG = [
    {"id": "mod.chorus", "display": "70s Chorus", "type": "chorus"},
    {"id": "mod.trem", "display": "Tremolo", "type": "tremolo"},
]


class TestPickAmp:
    """Test amp block selection."""

    def test_marshall_plexi_matches_plexi_amp(self):
        d = _make_descriptor(amp_family="marshall_plexi")
        pick = rules.pick_amp(d, SAMPLE_AMP_CATALOG)
        assert pick.slot == "amp"
        assert "plexi" in pick.block_id.lower() or "brit" in pick.display.lower()

    def test_marshall_jcm_matches_jcm_amp(self):
        d = _make_descriptor(amp_family="marshall_jcm")
        pick = rules.pick_amp(d, SAMPLE_AMP_CATALOG)
        assert "jcm" in pick.block_id.lower() or "2204" in pick.display

    def test_fender_clean_matches_deluxe(self):
        d = _make_descriptor(amp_family="fender_clean")
        pick = rules.pick_amp(d, SAMPLE_AMP_CATALOG)
        assert "deluxe" in pick.block_id.lower() or "deluxe" in pick.display.lower()

    def test_gain_mapped_to_drive_param(self):
        d = _make_descriptor(amp_family="marshall_plexi", gain=0.75)
        pick = rules.pick_amp(d, SAMPLE_AMP_CATALOG)
        assert "drive" in pick.params
        assert pick.params["drive"] == 7.5  # 0.75 * 10

    def test_voicing_mapped_to_eq_params(self):
        d = _make_descriptor(bass=0.6, mid=0.4, treble=0.8, presence=0.3)
        pick = rules.pick_amp(d, SAMPLE_AMP_CATALOG)
        assert pick.params["bass"] == 6.0
        assert pick.params["mid"] == 4.0
        assert pick.params["treble"] == 8.0
        assert pick.params["presence"] == 3.0

    def test_unknown_family_uses_fallback(self):
        d = _make_descriptor(amp_family="unknown")
        pick = rules.pick_amp(d, SAMPLE_AMP_CATALOG)
        # Should return first amp as fallback
        assert pick.block_id == SAMPLE_AMP_CATALOG[0]["id"]

    def test_rationale_mentions_family(self):
        d = _make_descriptor(amp_family="marshall_plexi")
        pick = rules.pick_amp(d, SAMPLE_AMP_CATALOG)
        assert "marshall_plexi" in pick.rationale.lower()


class TestPickCab:
    """Test cabinet block selection."""

    def test_v30_character_matches_v30_cab(self):
        d = _make_descriptor(speaker_char="v30_like", cab_config="4x12")
        pick = rules.pick_cab(d, SAMPLE_CAB_CATALOG)
        assert pick.slot == "cab"
        assert "v30" in pick.block_id.lower() or "v30" in pick.display.lower()

    def test_config_preference(self):
        """Should prefer matching configuration when available."""
        d = _make_descriptor(speaker_char="v30_like", cab_config="4x12")
        pick = rules.pick_cab(d, SAMPLE_CAB_CATALOG)
        assert "4x12" in pick.display

    def test_falls_back_to_character_match(self):
        """When config doesn't match, should still match on character."""
        d = _make_descriptor(speaker_char="alnico_blue_like", cab_config="4x12")
        pick = rules.pick_cab(d, SAMPLE_CAB_CATALOG)
        # Should get the alnico cab even though config doesn't match
        assert "blue" in pick.display.lower() or "alnico" in pick.block_id.lower()

    def test_mic_params_included(self):
        d = _make_descriptor()
        pick = rules.pick_cab(d, SAMPLE_CAB_CATALOG)
        assert "mic" in pick.params
        assert "distance" in pick.params


class TestPickDrive:
    """Test overdrive pedal selection."""

    def test_no_drive_when_not_detected(self):
        d = _make_descriptor(overdrive=False)
        pick = rules.pick_drive(d, SAMPLE_DRIVE_CATALOG)
        assert pick is None

    def test_drive_picked_when_detected(self):
        d = _make_descriptor(overdrive=True)
        pick = rules.pick_drive(d, SAMPLE_DRIVE_CATALOG)
        assert pick is not None
        assert pick.slot == "drive"

    def test_drive_style_matches(self):
        d = _make_descriptor(overdrive=True)
        # The descriptor has tube_screamer style
        pick = rules.pick_drive(d, SAMPLE_DRIVE_CATALOG)
        assert "808" in pick.display or "scream" in pick.display.lower()


class TestPickDelay:
    """Test delay block selection."""

    def test_no_delay_when_none(self):
        d = _make_descriptor(delay=None)
        pick = rules.pick_delay(d, SAMPLE_DELAY_CATALOG)
        assert pick is None

    def test_no_delay_when_type_none(self):
        d = _make_descriptor(delay=Delay(type="none", time_ms=0, feedback=0, mix=0))
        pick = rules.pick_delay(d, SAMPLE_DELAY_CATALOG)
        assert pick is None

    def test_delay_picked_when_detected(self):
        d = _make_descriptor(delay=Delay(type="digital", time_ms=350, feedback=0.4, mix=0.3))
        pick = rules.pick_delay(d, SAMPLE_DELAY_CATALOG)
        assert pick is not None
        assert pick.slot == "delay"
        assert pick.params["time_ms"] == 350

    def test_delay_type_matches(self):
        d = _make_descriptor(delay=Delay(type="analog_bbd", time_ms=400, feedback=0.5, mix=0.4))
        pick = rules.pick_delay(d, SAMPLE_DELAY_CATALOG)
        assert "analog" in pick.block_id or "tape" in pick.display.lower()


class TestPickReverb:
    """Test reverb block selection."""

    def test_no_reverb_when_none(self):
        d = _make_descriptor(reverb=None)
        pick = rules.pick_reverb(d, SAMPLE_REVERB_CATALOG)
        assert pick is None

    def test_reverb_picked_when_detected(self):
        d = _make_descriptor(reverb=Reverb(type="plate", size=0.5, mix=0.25))
        pick = rules.pick_reverb(d, SAMPLE_REVERB_CATALOG)
        assert pick is not None
        assert pick.slot == "reverb"

    def test_reverb_type_matches(self):
        d = _make_descriptor(reverb=Reverb(type="hall", size=0.7, mix=0.3))
        pick = rules.pick_reverb(d, SAMPLE_REVERB_CATALOG)
        assert "hall" in pick.block_id or "ganymede" in pick.display.lower()


class TestPickModulation:
    """Test modulation block selection."""

    def test_no_mod_when_none(self):
        d = _make_descriptor(modulation=None)
        pick = rules.pick_modulation(d, SAMPLE_MOD_CATALOG)
        assert pick is None

    def test_mod_picked_when_detected(self):
        d = _make_descriptor(modulation=Modulation(type="chorus", rate=0.5, depth=0.4))
        pick = rules.pick_modulation(d, SAMPLE_MOD_CATALOG)
        assert pick is not None
        assert pick.slot == "modulation"


class TestAmpAlternates:
    """Test alternate amp suggestions."""

    def test_no_alternates_when_confident(self):
        d = _make_descriptor(amp_conf=0.85)
        d.amp.alternates = [{"family": "marshall_jcm", "score": 0.5}]
        alts = rules.pick_amp_alternates(d, SAMPLE_AMP_CATALOG)
        assert len(alts) == 0  # High confidence, no alternates

    def test_alternates_shown_when_uncertain(self):
        d = _make_descriptor(amp_conf=0.55)
        d.amp.alternates = [
            {"family": "marshall_jcm", "score": 0.7},
            {"family": "mesa_rectifier", "score": 0.5},
        ]
        alts = rules.pick_amp_alternates(d, SAMPLE_AMP_CATALOG)
        assert len(alts) >= 1
        assert all(a.slot == "amp_alt" for a in alts)


class TestTweakHints:
    """Test tweak hint generation."""

    def test_returns_list(self):
        d = _make_descriptor()
        hints = rules.tweak_hints(d)
        assert isinstance(hints, list)

    def test_low_confidence_triggers_hint(self):
        d = _make_descriptor(amp_conf=0.45)
        hints = rules.tweak_hints(d)
        assert any("confidence" in h.lower() for h in hints)

    def test_dark_pickup_bright_amp_hint(self):
        """Dark pickups on bright amp family should suggest treble boost."""
        d = _make_descriptor(amp_family="fender_clean")
        d.guitar.pickup_brightness = 0.1
        hints = rules.tweak_hints(d)
        # Should suggest adjusting for dark pickups
        assert any("dark" in h.lower() or "presence" in h.lower() or "treble" in h.lower() for h in hints)


class TestChainCard:
    """Test the complete ChainCard structure."""

    def test_chain_card_structure(self):
        card = rules.ChainCard(
            picks=[rules.BlockPick(slot="amp", block_id="test", display="Test", params={}, rationale="test")],
            tweak_hints=["Test hint"],
        )
        assert len(card.picks) == 1
        assert len(card.tweak_hints) == 1

    def test_block_pick_structure(self):
        pick = rules.BlockPick(
            slot="amp",
            block_id="amp.test",
            display="Test Amp",
            params={"drive": 5.0},
            rationale="Test rationale",
        )
        assert pick.slot == "amp"
        assert pick.block_id == "amp.test"
        assert pick.display == "Test Amp"
        assert pick.params["drive"] == 5.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
