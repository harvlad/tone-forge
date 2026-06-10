"""``GET /api/device/probe`` route coverage.

The endpoint exposes ``tone_forge.devices.discovery.probe()`` to the
Jam onboarding modal so the UI can pre-fill the detected interface
name. Probe-internal failure modes (missing binary, malformed JSON,
timeout) are exhaustively covered in ``test_devices_discovery.py``;
this file only pins:

  1. The wire shape produced by the serializer for a successful probe.
  2. The wire shape for a failed probe (``probe_succeeded=False``)
     so the UI can tell "we have nothing to show" from "we have a
     suggestion".
  3. Belt-and-braces: if ``probe()`` ever violates its never-raise
     contract, the endpoint must still produce 200 + a soft-failure
     payload (never 500 the modal).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

import tone_forge_api
from tone_forge.contracts import AudioDeviceInfo, DeviceProbe
from tone_forge_api import app

client = TestClient(app)


def test_get_device_probe_serializes_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path: probe returns devices + a suggested input."""
    fake_probe = DeviceProbe(
        devices=(
            AudioDeviceInfo(
                device_id=0,
                name="Built-in Microphone",
                input_channels=1,
                output_channels=0,
            ),
            AudioDeviceInfo(
                device_id=1,
                name="Focusrite Scarlett 2i2",
                input_channels=2,
                output_channels=2,
            ),
        ),
        suggested_input=AudioDeviceInfo(
            device_id=1,
            name="Focusrite Scarlett 2i2",
            input_channels=2,
            output_channels=2,
        ),
        vendor_hint="Focusrite",
        probe_succeeded=True,
        error_message=None,
    )
    monkeypatch.setattr(tone_forge_api, "_device_probe", lambda: fake_probe)

    resp = client.get("/api/device/probe")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["probe_succeeded"] is True
    assert payload["vendor_hint"] == "Focusrite"
    assert payload["error_message"] is None
    assert payload["suggested_input"] == {
        "device_id": 1,
        "name": "Focusrite Scarlett 2i2",
        "input_channels": 2,
        "output_channels": 2,
    }
    assert payload["devices"] == [
        {
            "device_id": 0,
            "name": "Built-in Microphone",
            "input_channels": 1,
            "output_channels": 0,
        },
        {
            "device_id": 1,
            "name": "Focusrite Scarlett 2i2",
            "input_channels": 2,
            "output_channels": 2,
        },
    ]


def test_get_device_probe_serializes_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The probe contract reports failure inline (no exception). The
    serializer must surface ``probe_succeeded=False`` with empty
    devices so the modal can decide to omit the Detected row."""
    fake_probe = DeviceProbe(
        devices=(),
        suggested_input=None,
        vendor_hint=None,
        probe_succeeded=False,
        error_message="connect binary not found",
    )
    monkeypatch.setattr(tone_forge_api, "_device_probe", lambda: fake_probe)

    resp = client.get("/api/device/probe")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["probe_succeeded"] is False
    assert payload["devices"] == []
    assert payload["suggested_input"] is None
    assert payload["vendor_hint"] is None
    assert payload["error_message"] == "connect binary not found"


def test_get_device_probe_swallows_unexpected_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Belt-and-braces: ``probe()`` is documented as never-raise, but
    if a future change violates that contract the endpoint must
    still produce 200 + a soft-failure payload. A 500 here would
    pop a hard error in the Jam onboarding flow for what is purely
    a hint surface."""
    def _boom() -> DeviceProbe:
        raise RuntimeError("simulated probe explosion")

    monkeypatch.setattr(tone_forge_api, "_device_probe", _boom)

    resp = client.get("/api/device/probe")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["probe_succeeded"] is False
    assert payload["devices"] == []
    assert payload["suggested_input"] is None
    assert payload["error_message"] is not None
    assert "RuntimeError" in payload["error_message"]
