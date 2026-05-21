"""Tests for tone_forge/translator.py - Signal chain translation."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tone_forge.translator import (
    BlockRecommendation,
    SignalChainCard,
    load_catalog,
    translate,
    _get_price,
)
from tone_forge.descriptor import (
    ToneDescriptor,
    Source,
    Guitar,
    Amp,
    Voicing,
    Cab,
    Effects,
    Confidence,
    Delay,
    Reverb,
    Modulation,
)


class TestBlockRecommendation:
    """Test BlockRecommendation dataclass."""

    def test_create_basic(self):
        rec = BlockRecommendation(
            slot="amp",
            block_id="US Double Nrm",
            display="US Double Nrm",
            platform="helix",
        )
        assert rec.slot == "amp"
        assert rec.block_id == "US Double Nrm"
        assert rec.platform == "helix"
        assert rec.params == {}
        assert rec.rationale == ""

    def test_create_with_params(self):
        rec = BlockRecommendation(
            slot="amp",
            block_id="test",
            display="Test Amp",
            platform="helix",
            params={"drive": 5.0, "bass": 6.0},
            rationale="Good for clean tones",
            price_estimate="$1,500",
        )
        assert rec.params["drive"] == 5.0
        assert rec.rationale == "Good for clean tones"
        assert rec.price_estimate == "$1,500"


class TestSignalChainCard:
    """Test SignalChainCard dataclass."""

    def test_create_basic(self):
        card = SignalChainCard(picks=[])
        assert card.picks == []
        assert card.tweak_hints == []
        assert card.platform == "helix"

    def test_create_with_picks(self):
        picks = [
            BlockRecommendation("amp", "test", "Test", "helix"),
            BlockRecommendation("cab", "cab1", "Cab 1", "helix"),
        ]
        card = SignalChainCard(picks=picks, platform="helix")
        assert len(card.picks) == 2


class TestLoadCatalog:
    """Test catalog loading."""

    def test_load_helix_catalog(self):
        catalog = load_catalog("helix")
        assert catalog is not None
        assert "amps" in catalog
        assert "cabs" in catalog
        assert len(catalog["amps"]) > 0

    def test_load_pedals_catalog(self):
        catalog = load_catalog("pedals")
        # May or may not exist
        assert isinstance(catalog, dict)

    def test_load_nonexistent_catalog(self):
        catalog = load_catalog("nonexistent_platform")
        assert catalog == {}


class TestGetPrice:
    """Test price lookup."""

    def test_get_price_found(self):
        catalog_list = [
            {"id": "amp1", "price": "$500"},
            {"id": "amp2", "price": "$1,000"},
        ]
        assert _get_price(catalog_list, "amp1") == "$500"
        assert _get_price(catalog_list, "amp2") == "$1,000"

    def test_get_price_not_found(self):
        catalog_list = [{"id": "amp1", "price": "$500"}]
        assert _get_price(catalog_list, "nonexistent") is None

    def test_get_price_no_price_field(self):
        catalog_list = [{"id": "amp1"}]
        assert _get_price(catalog_list, "amp1") is None


def _make_descriptor(
    amp_family="fender_clean",
    gain=0.3,
    voicing=None,
    cab_config="1x12",
    speaker_char="unknown",
    effects=None,
) -> ToneDescriptor:
    """Helper to create a ToneDescriptor for testing."""
    if voicing is None:
        voicing = Voicing(bass=0.5, mid=0.5, treble=0.6, presence=0.4)
    if effects is None:
        effects = Effects()

    return ToneDescriptor(
        source=Source(kind="isolated_guitar", duration_sec=2.0),
        guitar=Guitar(),
        amp=Amp(family=amp_family, gain=gain, voicing=voicing),
        cab=Cab(configuration=cab_config, speaker_character=speaker_char),
        effects=effects,
        confidence=Confidence(amp_family=0.8, gain=0.7, cab=0.6, effects=0.5),
    )


class TestTranslate:
    """Test the main translate function."""

    @pytest.fixture
    def clean_descriptor(self):
        """Create a clean tone descriptor."""
        return _make_descriptor(
            amp_family="fender_clean",
            gain=0.3,
            cab_config="1x12",
            speaker_char="unknown",
        )

    @pytest.fixture
    def high_gain_descriptor(self):
        """Create a high gain tone descriptor."""
        return _make_descriptor(
            amp_family="mesa_rectifier",
            gain=0.85,
            voicing=Voicing(bass=0.6, mid=0.4, treble=0.7, presence=0.5),
            cab_config="4x12",
            speaker_char="v30_like",
        )

    def test_translate_returns_signal_chain_card(self, clean_descriptor):
        result = translate(clean_descriptor, platform="helix")
        assert isinstance(result, SignalChainCard)

    def test_translate_includes_amp(self, clean_descriptor):
        result = translate(clean_descriptor, platform="helix")
        slots = [p.slot for p in result.picks]
        assert "amp" in slots

    def test_translate_includes_cab(self, clean_descriptor):
        result = translate(clean_descriptor, platform="helix")
        slots = [p.slot for p in result.picks]
        assert "cab" in slots

    def test_translate_includes_tweak_hints(self, clean_descriptor):
        result = translate(clean_descriptor, platform="helix")
        assert isinstance(result.tweak_hints, list)

    def test_translate_high_gain_includes_amp(self, high_gain_descriptor):
        result = translate(high_gain_descriptor, platform="helix")
        slots = [p.slot for p in result.picks]
        # High gain should have amp
        assert "amp" in slots

    def test_translate_invalid_platform_raises(self, clean_descriptor):
        with pytest.raises(ValueError, match="No catalog found"):
            translate(clean_descriptor, platform="nonexistent")

    def test_translate_sets_platform_on_picks(self, clean_descriptor):
        result = translate(clean_descriptor, platform="helix")
        for pick in result.picks:
            assert pick.platform == "helix"

    def test_translate_picks_have_required_fields(self, clean_descriptor):
        result = translate(clean_descriptor, platform="helix")
        for pick in result.picks:
            assert pick.slot is not None
            assert pick.block_id is not None
            assert pick.display is not None


class TestTranslateWithEffects:
    """Test translation with various effects."""

    @pytest.fixture
    def descriptor_with_delay(self):
        return _make_descriptor(
            amp_family="fender_clean",
            gain=0.3,
            effects=Effects(
                delay=Delay(type="tape", time_ms=350, feedback=0.4, mix=0.3),
            ),
        )

    @pytest.fixture
    def descriptor_with_reverb(self):
        return _make_descriptor(
            amp_family="vox_chime",
            gain=0.5,
            voicing=Voicing(bass=0.4, mid=0.6, treble=0.7, presence=0.5),
            cab_config="2x12",
            effects=Effects(
                reverb=Reverb(type="spring", size=0.5, mix=0.3),
            ),
        )

    def test_delay_included_when_detected(self, descriptor_with_delay):
        result = translate(descriptor_with_delay, platform="helix")
        slots = [p.slot for p in result.picks]
        assert "delay" in slots

    def test_reverb_included_when_detected(self, descriptor_with_reverb):
        result = translate(descriptor_with_reverb, platform="helix")
        slots = [p.slot for p in result.picks]
        assert "reverb" in slots


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
