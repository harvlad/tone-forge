"""Tests for ``tone_forge.devices.caps``.

Covers the seam between Discovery and the rest of the system:

  1. ``caps_from_class`` returns a ``DeviceCaps`` for every
     ``DeviceClass`` enum value with the right capability flags per
     EXECUTION_PLAN.md §8.
  2. ``can_monitor`` is False for ``NO_HARDWARE`` and True for every
     other class (MVP scope per §8).
  3. ``can_receive_preset`` is False everywhere (preset adapters are
     Phase 2 per §10).
  4. Display names match the §8 onboarding prompt strings.
  5. ``caps_from_preferences`` returns ``None`` for ``None`` so the
     session bundle builder falls back to its own default.
  6. ``caps_from_preferences`` carries ``preferred_chain_family``
     from the preferences record into the caps record so retrieval
     can prefer that family in LOW/UNKNOWN fallbacks.
"""

from __future__ import annotations

import pytest

from tone_forge.contracts import (
    DeviceCaps,
    DeviceClass,
    DevicePreferences,
    MonitorChainFamily,
)
from tone_forge.devices.caps import caps_from_class, caps_from_preferences


# ---------------------------------------------------------------------------
# caps_from_class
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cls", list(DeviceClass))
def test_caps_from_class_returns_device_caps_for_every_class(cls: DeviceClass) -> None:
    """Every enum value must map to a ``DeviceCaps`` instance."""
    caps = caps_from_class(cls)
    assert isinstance(caps, DeviceCaps)
    assert caps.cls is cls


@pytest.mark.parametrize("cls", list(DeviceClass))
def test_caps_from_class_display_name_is_non_empty(cls: DeviceClass) -> None:
    caps = caps_from_class(cls)
    assert isinstance(caps.display_name, str)
    assert caps.display_name.strip() != ""


def test_caps_from_class_no_hardware_cannot_monitor() -> None:
    caps = caps_from_class(DeviceClass.NO_HARDWARE)
    assert caps.can_monitor is False
    assert caps.can_receive_preset is False


@pytest.mark.parametrize(
    "cls",
    [c for c in DeviceClass if c is not DeviceClass.NO_HARDWARE],
)
def test_caps_from_class_non_no_hardware_can_monitor(cls: DeviceClass) -> None:
    caps = caps_from_class(cls)
    assert caps.can_monitor is True


@pytest.mark.parametrize("cls", list(DeviceClass))
def test_caps_from_class_can_receive_preset_is_false_at_mvp(cls: DeviceClass) -> None:
    """Per §10 Phase-2 defer, no class advertises preset capability yet."""
    caps = caps_from_class(cls)
    assert caps.can_receive_preset is False


def test_caps_from_class_display_strings_match_onboarding_prompt() -> None:
    """Display labels match the §8 onboarding prompt exactly."""
    assert caps_from_class(DeviceClass.INTERFACE_ONLY).display_name == "Audio interface"
    assert caps_from_class(DeviceClass.HELIX).display_name == "Line 6 Helix"
    assert caps_from_class(DeviceClass.QUAD_CORTEX).display_name == "Neural DSP Quad Cortex"
    assert caps_from_class(DeviceClass.KEMPER).display_name == "Kemper"
    assert caps_from_class(DeviceClass.FRACTAL).display_name == "Fractal"
    assert caps_from_class(DeviceClass.TONEX).display_name == "IK Multimedia Tonex"
    assert caps_from_class(DeviceClass.NEURAL_DSP).display_name == "Neural DSP plugin"
    assert caps_from_class(DeviceClass.OTHER).display_name == "Other"


def test_caps_from_class_returns_frozen_dataclass() -> None:
    """``DeviceCaps`` is frozen so the API can hand out the same caps
    record to multiple consumers without defensive copying."""
    caps = caps_from_class(DeviceClass.INTERFACE_ONLY)
    with pytest.raises((AttributeError, TypeError)):
        caps.display_name = "Spoofed"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# caps_from_preferences
# ---------------------------------------------------------------------------


def test_caps_from_preferences_none_returns_none() -> None:
    """``None`` in -> ``None`` out so the session builder defaults."""
    assert caps_from_preferences(None) is None


def test_caps_from_preferences_projects_device_class() -> None:
    prefs = DevicePreferences(device_class=DeviceClass.HELIX)
    caps = caps_from_preferences(prefs)
    assert caps is not None
    assert caps.cls is DeviceClass.HELIX
    assert caps.display_name == "Line 6 Helix"
    assert caps.can_monitor is True
    assert caps.can_receive_preset is False


def test_caps_from_preferences_carries_preferred_chain_family() -> None:
    prefs = DevicePreferences(
        device_class=DeviceClass.INTERFACE_ONLY,
        preferred_chain_family=MonitorChainFamily.AMBIENT,
    )
    caps = caps_from_preferences(prefs)
    assert caps is not None
    assert caps.preferred_chain_family is MonitorChainFamily.AMBIENT


def test_caps_from_preferences_omits_chain_family_when_unset() -> None:
    prefs = DevicePreferences(device_class=DeviceClass.INTERFACE_ONLY)
    caps = caps_from_preferences(prefs)
    assert caps is not None
    assert caps.preferred_chain_family is None
