"""Tests for ``tone_forge.devices.preferences``.

Covers:
  1. Round-trip: save -> load returns equivalent DevicePreferences.
  2. ``first_seen_iso`` stamped on first save, preserved on subsequent
     saves; ``last_used_iso`` updated every save.
  3. ``load_preferences`` returns None for: missing file, malformed
     JSON, non-dict payload, unknown enum value, missing required key.
  4. ``clear_preferences`` is idempotent.
  5. ``preferences_path`` honors the env override.
  6. ``save_preferences`` is atomic — no leftover temp file on success.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tone_forge.contracts import (
    DeviceClass,
    DevicePreferences,
    MonitorChainFamily,
)
from tone_forge.devices import preferences as prefs_mod
from tone_forge.devices.preferences import (
    clear_preferences,
    load_preferences,
    preferences_path,
    save_preferences,
)


@pytest.fixture
def temp_prefs_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect preferences storage into a per-test tmp directory."""
    target = tmp_path / "device.json"
    monkeypatch.setenv("TONEFORGE_DEVICE_PREFS_PATH", str(target))
    return target


# ---------------------------------------------------------------------------
# preferences_path env override
# ---------------------------------------------------------------------------


def test_preferences_path_honors_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "custom" / "device.json"
    monkeypatch.setenv("TONEFORGE_DEVICE_PREFS_PATH", str(target))
    assert preferences_path() == target


def test_preferences_path_default_under_app_support(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TONEFORGE_DEVICE_PREFS_PATH", raising=False)
    path = preferences_path()
    assert path.name == "device.json"
    assert "ToneForge" in path.parts
    assert "Application Support" in path.parts


# ---------------------------------------------------------------------------
# load_preferences — absent / invalid file cases
# ---------------------------------------------------------------------------


def test_load_returns_none_when_file_missing(temp_prefs_path: Path) -> None:
    assert not temp_prefs_path.exists()
    assert load_preferences() is None


def test_load_returns_none_on_malformed_json(temp_prefs_path: Path) -> None:
    temp_prefs_path.parent.mkdir(parents=True, exist_ok=True)
    temp_prefs_path.write_text("{not json")
    assert load_preferences() is None


def test_load_returns_none_on_non_dict_payload(temp_prefs_path: Path) -> None:
    temp_prefs_path.parent.mkdir(parents=True, exist_ok=True)
    temp_prefs_path.write_text(json.dumps(["a", "list", "is", "wrong"]))
    assert load_preferences() is None


def test_load_returns_none_on_unknown_device_class(temp_prefs_path: Path) -> None:
    temp_prefs_path.parent.mkdir(parents=True, exist_ok=True)
    temp_prefs_path.write_text(
        json.dumps({"device_class": "not_a_real_class"})
    )
    assert load_preferences() is None


def test_load_returns_none_on_unknown_chain_family(temp_prefs_path: Path) -> None:
    temp_prefs_path.parent.mkdir(parents=True, exist_ok=True)
    temp_prefs_path.write_text(
        json.dumps(
            {
                "device_class": "interface_only",
                "preferred_chain_family": "garbage_family",
            }
        )
    )
    assert load_preferences() is None


def test_load_returns_none_on_missing_device_class(temp_prefs_path: Path) -> None:
    temp_prefs_path.parent.mkdir(parents=True, exist_ok=True)
    temp_prefs_path.write_text(json.dumps({"audio_input_name": "x"}))
    assert load_preferences() is None


# ---------------------------------------------------------------------------
# save + round-trip
# ---------------------------------------------------------------------------


def test_save_round_trip_minimal(temp_prefs_path: Path) -> None:
    written = save_preferences(
        DevicePreferences(device_class=DeviceClass.INTERFACE_ONLY)
    )
    assert temp_prefs_path.exists()
    loaded = load_preferences()
    assert loaded == written


def test_save_round_trip_full(temp_prefs_path: Path) -> None:
    written = save_preferences(
        DevicePreferences(
            device_class=DeviceClass.HELIX,
            audio_input_name="Focusrite Scarlett 2i2",
            preferred_chain_family=MonitorChainFamily.CLEAN,
        )
    )
    loaded = load_preferences()
    assert loaded is not None
    assert loaded.device_class is DeviceClass.HELIX
    assert loaded.audio_input_name == "Focusrite Scarlett 2i2"
    assert loaded.preferred_chain_family is MonitorChainFamily.CLEAN
    assert loaded == written


def test_save_stamps_first_seen_and_last_used(temp_prefs_path: Path) -> None:
    written = save_preferences(
        DevicePreferences(device_class=DeviceClass.INTERFACE_ONLY)
    )
    assert written.first_seen_iso is not None
    assert written.last_used_iso is not None
    # On a single save the two timestamps should be identical (no clock skew).
    assert written.first_seen_iso == written.last_used_iso


def test_save_preserves_first_seen_on_subsequent_saves(
    temp_prefs_path: Path,
) -> None:
    first = save_preferences(
        DevicePreferences(device_class=DeviceClass.INTERFACE_ONLY)
    )
    # Force a tick by writing a synthetic prior timestamp into the
    # second save's input so we can prove first_seen is held while
    # last_used advances.
    second = save_preferences(
        DevicePreferences(
            device_class=DeviceClass.INTERFACE_ONLY,
            first_seen_iso=first.first_seen_iso,
        )
    )
    assert second.first_seen_iso == first.first_seen_iso
    # last_used_iso is monotonic non-decreasing (clock may not tick).
    assert second.last_used_iso is not None
    assert first.last_used_iso is not None
    assert second.last_used_iso >= first.last_used_iso


def test_save_creates_parent_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "nested" / "deeper" / "device.json"
    monkeypatch.setenv("TONEFORGE_DEVICE_PREFS_PATH", str(target))
    assert not target.parent.exists()
    save_preferences(DevicePreferences(device_class=DeviceClass.INTERFACE_ONLY))
    assert target.exists()


def test_save_writes_valid_json_on_disk(temp_prefs_path: Path) -> None:
    save_preferences(
        DevicePreferences(
            device_class=DeviceClass.NEURAL_DSP,
            audio_input_name="Apollo Twin X",
        )
    )
    raw = json.loads(temp_prefs_path.read_text())
    assert raw["device_class"] == "neural_dsp"
    assert raw["audio_input_name"] == "Apollo Twin X"
    assert raw["preferred_chain_family"] is None


def test_save_no_temp_file_left_on_success(temp_prefs_path: Path) -> None:
    save_preferences(DevicePreferences(device_class=DeviceClass.INTERFACE_ONLY))
    leftover = [
        p for p in temp_prefs_path.parent.iterdir() if p.name.startswith(".device-")
    ]
    assert leftover == []


# ---------------------------------------------------------------------------
# clear_preferences
# ---------------------------------------------------------------------------


def test_clear_preferences_removes_file(temp_prefs_path: Path) -> None:
    save_preferences(DevicePreferences(device_class=DeviceClass.INTERFACE_ONLY))
    assert temp_prefs_path.exists()
    clear_preferences()
    assert not temp_prefs_path.exists()


def test_clear_preferences_idempotent_when_absent(temp_prefs_path: Path) -> None:
    assert not temp_prefs_path.exists()
    # Should not raise.
    clear_preferences()
    clear_preferences()
