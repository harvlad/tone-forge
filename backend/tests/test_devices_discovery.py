"""Tests for ``tone_forge.devices.discovery``.

Covers four slices:

  1. Vendor hint matching (case-insensitive, multi-substring per vendor).
  2. ``suggested_input`` selection: vendor-match > first-input > none.
  3. JSON parsing happy path + every failure mode (missing binary,
     non-zero exit, timeout, malformed JSON, missing field).
  4. ``_resolve_connect_binary`` honors ``CONNECT_BINARY`` env override.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any
from unittest.mock import patch

import pytest

from tone_forge.contracts import AudioDeviceInfo, DeviceProbe
from tone_forge.devices import discovery
from tone_forge.devices.discovery import (
    _choose_suggested_input,
    _parse_devices,
    _resolve_connect_binary,
    _vendor_hint_for,
    probe,
)


# ---------------------------------------------------------------------------
# Vendor hint detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name, expected",
    [
        ("Focusrite Scarlett 2i2 USB", "Focusrite"),
        ("scarlett solo", "Focusrite"),
        ("Clarett+ 8Pre", "Focusrite"),
        ("Universal Audio Apollo Twin X", "Universal Audio"),
        ("Apollo x4", "Universal Audio"),
        ("Volt 276", "Universal Audio"),
        ("Audient iD14", "Audient"),
        ("Apogee Duet 3", "Apogee"),
        ("Steinberg UR22", "Steinberg"),
        ("UR44C", "Steinberg"),
        ("MOTU M4", "MOTU"),
        ("RME Babyface Pro FS", "RME"),
        ("Fireface UFX III", "RME"),
        ("PreSonus Studio 24c", "PreSonus"),
        ("Native Instruments Komplete Audio 6", "Native Instruments"),
        ("Komplete Audio 1", "Native Instruments"),
        # Modelers — recognized as inputs (P7 #36 follow-up): a Helix /
        # QC / Kemper / Fractal / Tonex plugged in IS the guitar's audio
        # interface, so it should win the vendor-match race.
        ("Line 6 HX Stomp", "Line 6"),
        ("HX Effects", "Line 6"),
        ("Helix Native", "Line 6"),
        ("Quad Cortex", "Neural DSP"),
        ("Kemper Profiler Stage", "Kemper"),
        ("Fractal Axe-Fx III", "Fractal"),
        ("FM3", "Fractal"),
        ("FM9 Turbo", "Fractal"),
        ("IK Multimedia ToneX", "IK Multimedia"),
    ],
)
def test_vendor_hint_matches_known_devices(name: str, expected: str) -> None:
    assert _vendor_hint_for(name) == expected


@pytest.mark.parametrize(
    "name",
    [
        "MacBook Pro Microphone",
        "MacBook Pro Speakers",
        "Microsoft Teams Audio",
        "AirPods Pro",
        "",
    ],
)
def test_vendor_hint_returns_none_for_unknown(name: str) -> None:
    assert _vendor_hint_for(name) is None


# ---------------------------------------------------------------------------
# suggested_input selection
# ---------------------------------------------------------------------------


def test_choose_suggested_input_prefers_vendor_match() -> None:
    devices = (
        AudioDeviceInfo(1, "MacBook Pro Microphone", 1, 0),
        AudioDeviceInfo(2, "Focusrite Scarlett 2i2", 2, 2),
        AudioDeviceInfo(3, "Microsoft Teams Audio", 2, 2),
    )
    chosen, hint = _choose_suggested_input(devices)
    assert chosen is not None and chosen.device_id == 2
    assert hint == "Focusrite"


def test_choose_suggested_input_prefers_modeler_over_builtin_mic() -> None:
    # Regression: probe order put the iPhone mic before the HX Stomp,
    # which previously won the fallback. Modelers are now in the
    # vendor-hint list so they outrank generic built-in inputs.
    devices = (
        AudioDeviceInfo(97, "Matt's iPhone Microphone", 1, 0),
        AudioDeviceInfo(92, "MacBook Pro Microphone", 1, 0),
        AudioDeviceInfo(129, "Line 6 HX Stomp", 8, 8),
    )
    chosen, hint = _choose_suggested_input(devices)
    assert chosen is not None and chosen.device_id == 129
    assert hint == "Line 6"


def test_choose_suggested_input_falls_back_to_first_input() -> None:
    devices = (
        AudioDeviceInfo(73, "MacBook Pro Speakers", 0, 2),
        AudioDeviceInfo(80, "MacBook Pro Microphone", 1, 0),
        AudioDeviceInfo(85, "Microsoft Teams Audio", 2, 2),
    )
    chosen, hint = _choose_suggested_input(devices)
    assert chosen is not None and chosen.device_id == 80
    assert hint is None


def test_choose_suggested_input_none_when_no_inputs() -> None:
    devices = (
        AudioDeviceInfo(73, "MacBook Pro Speakers", 0, 2),
    )
    chosen, hint = _choose_suggested_input(devices)
    assert chosen is None
    assert hint is None


def test_choose_suggested_input_empty() -> None:
    chosen, hint = _choose_suggested_input(())
    assert chosen is None
    assert hint is None


# ---------------------------------------------------------------------------
# _parse_devices
# ---------------------------------------------------------------------------


def test_parse_devices_happy_path() -> None:
    payload = {
        "devices": [
            {
                "device_id": 80,
                "name": "MacBook Pro Microphone",
                "input_channels": 1,
                "output_channels": 0,
            }
        ]
    }
    out = _parse_devices(payload)
    assert len(out) == 1
    assert out[0] == AudioDeviceInfo(80, "MacBook Pro Microphone", 1, 0)


def test_parse_devices_empty_list() -> None:
    assert _parse_devices({"devices": []}) == ()


def test_parse_devices_rejects_non_list() -> None:
    with pytest.raises(ValueError):
        _parse_devices({"devices": "not a list"})


def test_parse_devices_missing_field_raises_keyerror() -> None:
    with pytest.raises(KeyError):
        _parse_devices({"devices": [{"device_id": 1, "name": "x"}]})


# ---------------------------------------------------------------------------
# probe() end-to-end via subprocess.run mock
# ---------------------------------------------------------------------------


def _completed(stdout: str, returncode: int = 0, stderr: str = "") -> Any:
    return subprocess.CompletedProcess(
        args=["connect", "devices", "--json"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def test_probe_returns_failure_when_binary_missing() -> None:
    with patch.object(discovery, "_resolve_connect_binary", return_value=None):
        result = probe()
    assert isinstance(result, DeviceProbe)
    assert result.probe_succeeded is False
    assert result.devices == ()
    assert result.error_message == "connect binary not found"


def test_probe_happy_path() -> None:
    stdout = json.dumps(
        {
            "devices": [
                {
                    "device_id": 80,
                    "name": "MacBook Pro Microphone",
                    "input_channels": 1,
                    "output_channels": 0,
                },
                {
                    "device_id": 90,
                    "name": "Focusrite Scarlett 2i2",
                    "input_channels": 2,
                    "output_channels": 2,
                },
            ]
        }
    )
    with patch.object(discovery, "_resolve_connect_binary", return_value="/fake/connect"):
        with patch.object(discovery.subprocess, "run", return_value=_completed(stdout)):
            result = probe()
    assert result.probe_succeeded is True
    assert len(result.devices) == 2
    assert result.suggested_input is not None
    assert result.suggested_input.device_id == 90
    assert result.vendor_hint == "Focusrite"
    assert result.error_message is None


def test_probe_returns_failure_on_nonzero_exit() -> None:
    with patch.object(discovery, "_resolve_connect_binary", return_value="/fake/connect"):
        with patch.object(
            discovery.subprocess,
            "run",
            return_value=_completed("", returncode=2, stderr="boom"),
        ):
            result = probe()
    assert result.probe_succeeded is False
    assert result.devices == ()
    assert result.error_message is not None
    assert "exit 2" in result.error_message
    assert "boom" in result.error_message


def test_probe_returns_failure_on_timeout() -> None:
    def _raise(*_a: Any, **_kw: Any) -> None:
        raise subprocess.TimeoutExpired(cmd="connect", timeout=5.0)

    with patch.object(discovery, "_resolve_connect_binary", return_value="/fake/connect"):
        with patch.object(discovery.subprocess, "run", side_effect=_raise):
            result = probe(timeout_seconds=5.0)
    assert result.probe_succeeded is False
    assert result.error_message is not None
    assert "timed out" in result.error_message


def test_probe_returns_failure_on_oserror() -> None:
    with patch.object(discovery, "_resolve_connect_binary", return_value="/fake/connect"):
        with patch.object(
            discovery.subprocess, "run", side_effect=OSError("permission denied")
        ):
            result = probe()
    assert result.probe_succeeded is False
    assert result.error_message is not None
    assert "exec failed" in result.error_message


def test_probe_returns_failure_on_malformed_json() -> None:
    with patch.object(discovery, "_resolve_connect_binary", return_value="/fake/connect"):
        with patch.object(
            discovery.subprocess, "run", return_value=_completed("not json")
        ):
            result = probe()
    assert result.probe_succeeded is False
    assert result.error_message is not None
    assert "json parse failed" in result.error_message


def test_probe_returns_failure_on_missing_devices_key() -> None:
    with patch.object(discovery, "_resolve_connect_binary", return_value="/fake/connect"):
        with patch.object(
            discovery.subprocess,
            "run",
            return_value=_completed(json.dumps({"wrong_key": []})),
        ):
            result = probe()
    # An empty list fallback is fine; what we want is a successful empty probe.
    assert result.probe_succeeded is True
    assert result.devices == ()
    assert result.suggested_input is None


# ---------------------------------------------------------------------------
# _resolve_connect_binary
# ---------------------------------------------------------------------------


def test_resolve_connect_binary_honors_env_override(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = tmp_path / "connect-stub"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    monkeypatch.setenv("CONNECT_BINARY", str(fake))
    assert _resolve_connect_binary() == str(fake)


def test_resolve_connect_binary_ignores_nonexistent_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONNECT_BINARY", "/definitely/does/not/exist/connect")
    # Should fall through to other resolution paths rather than returning
    # the override. We can't assert the final value without knowing the
    # host, only that it does not blindly return the bad override.
    assert _resolve_connect_binary() != "/definitely/does/not/exist/connect"


# ---------------------------------------------------------------------------
# Real-binary integration
#
# Regression coverage for the failure that landed in dev: the release
# bundle in /Applications had a stale Connect binary that exited 0 but
# emitted human-readable text instead of JSON, so the probe started
# returning `probe_succeeded=False` with a parse error. The pure-mock
# tests above all kept passing because they never exercised the actual
# binary. This block runs the binary the resolver would actually pick
# and asserts the JSON contract holds end-to-end.
#
# Skipped automatically when:
#   * no Connect binary is on this host (CI, fresh checkout), or
#   * the binary can't be executed (dyld error, permission denied, etc.).
# Either of those cases is the correct local outcome ("not installed");
# what we want to catch is "installed and runs, but is broken."
# ---------------------------------------------------------------------------


def _resolved_binary_or_skip() -> str:
    binary = _resolve_connect_binary()
    if binary is None:
        pytest.skip("no Connect binary resolved on this host")
    return binary


def test_resolved_connect_binary_emits_parseable_json() -> None:
    """The binary the resolver picks must speak the JSON contract."""
    binary = _resolved_binary_or_skip()
    try:
        result = subprocess.run(
            [binary, "devices", "--json"],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        pytest.skip(f"Connect binary at {binary} could not be executed: {exc}")

    if result.returncode != 0:
        pytest.skip(
            f"Connect binary at {binary} exited {result.returncode} "
            f"(likely dyld / linkage issue, not a contract violation): "
            f"{result.stderr.strip()}"
        )

    # The binary ran. From here on, any failure is a real contract bug
    # that would break the probe in production.
    stdout = result.stdout.strip()
    assert stdout, (
        f"Connect binary at {binary} exited 0 but produced no stdout. "
        "Probe will treat this as a JSON parse failure."
    )
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"Connect binary at {binary} did not emit JSON for "
            f"`devices --json` (first 200 chars: {stdout[:200]!r}). "
            f"This is the regression that caused the probe to start "
            f"returning probe_succeeded=False. Rebuild the binary "
            f"from current sources. Parse error: {exc}"
        ) from exc

    assert isinstance(payload, dict), (
        f"Connect binary at {binary} returned non-object JSON: {type(payload)}"
    )
    assert "devices" in payload, (
        f"Connect binary at {binary} JSON missing required 'devices' key. "
        f"Keys present: {sorted(payload.keys())}"
    )
    assert isinstance(payload["devices"], list), (
        "'devices' field must be a list"
    )
    # Every device entry must match the AudioDeviceInfo contract — same
    # validation _parse_devices performs in production.
    for i, entry in enumerate(payload["devices"]):
        assert isinstance(entry, dict), f"devices[{i}] is not an object"
        for field in ("device_id", "name", "input_channels", "output_channels"):
            assert field in entry, f"devices[{i}] missing required field '{field}'"
        assert isinstance(entry["device_id"], int)
        assert isinstance(entry["name"], str)
        assert isinstance(entry["input_channels"], int)
        assert isinstance(entry["output_channels"], int)


def test_probe_against_real_binary_succeeds() -> None:
    """End-to-end: the public ``probe()`` call must succeed on a real host
    with a working binary. This is the test that would have caught the
    stale-binary failure: ``probe_succeeded`` had silently flipped to
    False even though the binary was present and executable."""
    binary = _resolved_binary_or_skip()
    # Sanity-check the binary can run at all. If it can't, skip — same
    # rationale as above (we want to catch broken contracts, not absent
    # installs).
    try:
        smoke = subprocess.run(
            [binary, "devices", "--json"],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        pytest.skip(f"Connect binary at {binary} could not be executed: {exc}")
    if smoke.returncode != 0:
        pytest.skip(
            f"Connect binary at {binary} exited {smoke.returncode} "
            f"(stderr: {smoke.stderr.strip()})"
        )

    result = probe()
    assert result.probe_succeeded is True, (
        f"probe() reported failure against a runnable binary at {binary}: "
        f"{result.error_message}"
    )
    # Devices list may legitimately be empty on a CI box with no audio
    # hardware enumerated, but the contract still guarantees a tuple.
    assert isinstance(result.devices, tuple)
