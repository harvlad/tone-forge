"""Tests for tone_forge/hardware.py - Hardware definitions and user profile management."""
from __future__ import annotations

import json
import sys
from pathlib import Path
import tempfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tone_forge.hardware import (
    HardwareBlock,
    UserGear,
    UserProfile,
    MULTI_FX_UNITS,
    create_user_gear_from_unit,
    save_user_profile,
    load_user_profile,
)


class TestHardwareBlock:
    """Test HardwareBlock dataclass."""

    def test_create_basic(self):
        block = HardwareBlock(
            id="us_double_nrm",
            display="US Double Nrm",
            category="amp",
            platform="helix",
        )
        assert block.id == "us_double_nrm"
        assert block.display == "US Double Nrm"
        assert block.category == "amp"
        assert block.platform == "helix"

    def test_create_with_models(self):
        block = HardwareBlock(
            id="us_double_nrm",
            display="US Double Nrm",
            category="amp",
            platform="helix",
            models="Fender Twin Reverb",
        )
        assert block.models == "Fender Twin Reverb"

    def test_create_with_families(self):
        block = HardwareBlock(
            id="us_double_nrm",
            display="US Double Nrm",
            category="amp",
            platform="helix",
            families=["fender_clean", "twin"],
        )
        assert "fender_clean" in block.families

    def test_create_with_params(self):
        block = HardwareBlock(
            id="us_double_nrm",
            display="US Double Nrm",
            category="amp",
            platform="helix",
            params={"gain": (0.0, 10.0), "bass": (0.0, 10.0)},
        )
        assert block.params["gain"] == (0.0, 10.0)

    def test_defaults(self):
        block = HardwareBlock(
            id="test",
            display="Test",
            category="amp",
            platform="helix",
        )
        assert block.models is None
        assert block.families == []
        assert block.styles == []
        assert block.params == {}
        assert block.meta == {}


class TestUserGear:
    """Test UserGear dataclass."""

    def test_create_basic(self):
        gear = UserGear(
            id="helix_floor",
            display="Line 6 Helix Floor",
            category="amp",
            platform="helix",
        )
        assert gear.id == "helix_floor"
        assert gear.platform == "helix"

    def test_create_with_available_blocks(self):
        gear = UserGear(
            id="helix_floor",
            display="Line 6 Helix Floor",
            category="amp",
            platform="helix",
            available_blocks=["us_double_nrm", "brit_plexi"],
        )
        assert len(gear.available_blocks) == 2


class TestUserProfile:
    """Test UserProfile dataclass and methods."""

    def test_create_default(self):
        profile = UserProfile()
        assert profile.name == "default"
        assert profile.gear == []
        assert profile.preferred_platforms == []
        assert profile.budget_max is None

    def test_create_with_gear(self):
        gear = UserGear(
            id="helix_floor",
            display="Line 6 Helix Floor",
            category="amp",
            platform="helix",
        )
        profile = UserProfile(
            name="my_profile",
            gear=[gear],
            preferred_platforms=["helix", "pedals"],
        )
        assert len(profile.gear) == 1
        assert "helix" in profile.preferred_platforms

    def test_has_platform_true(self):
        gear = UserGear(
            id="helix_floor",
            display="Line 6 Helix Floor",
            category="amp",
            platform="helix",
        )
        profile = UserProfile(gear=[gear])
        assert profile.has_platform("helix") is True

    def test_has_platform_false(self):
        gear = UserGear(
            id="helix_floor",
            display="Line 6 Helix Floor",
            category="amp",
            platform="helix",
        )
        profile = UserProfile(gear=[gear])
        assert profile.has_platform("boss") is False

    def test_has_platform_empty(self):
        profile = UserProfile()
        assert profile.has_platform("helix") is False

    def test_get_available_blocks_direct(self):
        gear = UserGear(
            id="ts808",
            display="Tube Screamer",
            category="drive",
            platform="pedals",
        )
        profile = UserProfile(gear=[gear])
        blocks = profile.get_available_blocks("drive")
        assert "ts808" in blocks

    def test_get_available_blocks_from_multifx(self):
        gear = UserGear(
            id="helix_floor",
            display="Line 6 Helix Floor",
            category="amp",
            platform="helix",
            available_blocks=["us_double_nrm", "scream_808"],
        )
        profile = UserProfile(gear=[gear])
        blocks = profile.get_available_blocks("drive")
        assert "us_double_nrm" in blocks
        assert "scream_808" in blocks

    def test_to_dict(self):
        gear = UserGear(
            id="helix_floor",
            display="Line 6 Helix Floor",
            category="amp",
            platform="helix",
        )
        profile = UserProfile(
            name="test",
            gear=[gear],
            preferred_platforms=["helix"],
        )
        d = profile.to_dict()
        assert d["name"] == "test"
        assert len(d["gear"]) == 1
        assert d["gear"][0]["id"] == "helix_floor"

    def test_from_dict(self):
        d = {
            "name": "test",
            "gear": [
                {
                    "id": "helix_floor",
                    "display": "Line 6 Helix Floor",
                    "category": "amp",
                    "platform": "helix",
                    "available_blocks": [],
                }
            ],
            "preferred_platforms": ["helix"],
            "budget_max": 1500.0,
        }
        profile = UserProfile.from_dict(d)
        assert profile.name == "test"
        assert len(profile.gear) == 1
        assert profile.budget_max == 1500.0

    def test_from_dict_empty(self):
        profile = UserProfile.from_dict({})
        assert profile.name == "default"
        assert profile.gear == []


class TestMultiFXUnits:
    """Test MULTI_FX_UNITS dictionary."""

    def test_helix_floor_exists(self):
        assert "helix_floor" in MULTI_FX_UNITS

    def test_helix_floor_has_required_fields(self):
        unit = MULTI_FX_UNITS["helix_floor"]
        assert "display" in unit
        assert "platform" in unit
        assert "categories" in unit

    def test_helix_floor_categories(self):
        unit = MULTI_FX_UNITS["helix_floor"]
        assert "amp" in unit["categories"]
        assert "cab" in unit["categories"]
        assert "drive" in unit["categories"]

    def test_hx_stomp_exists(self):
        assert "hx_stomp" in MULTI_FX_UNITS

    def test_quad_cortex_exists(self):
        assert "quad_cortex" in MULTI_FX_UNITS

    def test_kemper_profiler_exists(self):
        assert "kemper_profiler" in MULTI_FX_UNITS

    def test_fractal_axe3_exists(self):
        assert "fractal_axe3" in MULTI_FX_UNITS


class TestCreateUserGearFromUnit:
    """Test create_user_gear_from_unit function."""

    def test_create_helix_floor(self):
        gear = create_user_gear_from_unit("helix_floor")
        assert gear is not None
        assert gear.id == "helix_floor"
        assert gear.display == "Line 6 Helix Floor"
        assert gear.platform == "helix"

    def test_create_quad_cortex(self):
        gear = create_user_gear_from_unit("quad_cortex")
        assert gear is not None
        assert gear.platform == "neural_dsp"

    def test_create_invalid_unit_returns_none(self):
        gear = create_user_gear_from_unit("nonexistent_unit")
        assert gear is None


class TestSaveLoadUserProfile:
    """Test save_user_profile and load_user_profile functions."""

    def test_save_and_load(self, tmp_path):
        gear = UserGear(
            id="helix_floor",
            display="Line 6 Helix Floor",
            category="amp",
            platform="helix",
        )
        profile = UserProfile(
            name="test_profile",
            gear=[gear],
            preferred_platforms=["helix"],
            budget_max=2000.0,
        )

        profile_path = tmp_path / "profile.json"
        save_user_profile(profile, profile_path)

        # Verify file was created
        assert profile_path.exists()

        # Load and verify
        loaded = load_user_profile(profile_path)
        assert loaded is not None
        assert loaded.name == "test_profile"
        assert len(loaded.gear) == 1
        assert loaded.gear[0].id == "helix_floor"
        assert loaded.budget_max == 2000.0

    def test_load_nonexistent_returns_none(self, tmp_path):
        profile_path = tmp_path / "nonexistent.json"
        loaded = load_user_profile(profile_path)
        assert loaded is None

    def test_save_creates_valid_json(self, tmp_path):
        profile = UserProfile(name="test")
        profile_path = tmp_path / "profile.json"
        save_user_profile(profile, profile_path)

        # Verify it's valid JSON
        with open(profile_path) as f:
            data = json.load(f)
        assert data["name"] == "test"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
