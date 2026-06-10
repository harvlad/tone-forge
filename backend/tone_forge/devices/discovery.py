"""Non-blocking CoreAudio probe via the Connect CLI.

``probe()`` shells out to ``connect devices --json`` and projects the
result into a :class:`DeviceProbe`. The probe never fails the caller —
when the binary is missing or the JSON is malformed it returns an empty
probe with ``probe_succeeded=False`` and an ``error_message``. The
onboarding flow uses that to fall back to the manual picker.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Iterable, Optional, Tuple

from tone_forge.contracts import AudioDeviceInfo, DeviceProbe

# Vendor substrings to look for in CoreAudio device names. Order matters
# only to humans reading the list; matching is first-hit-wins on the
# lowest-id input device (which is typically the active interface).
_VENDOR_HINTS: Tuple[Tuple[str, str], ...] = (
    # Modelers / amp sims first — they ARE the guitar's audio
    # interface in most rigs, so they should win the "suggested
    # input" race over generic interfaces and built-in mics.
    ("helix", "Line 6"),
    ("hx stomp", "Line 6"),
    ("hx effects", "Line 6"),
    ("line 6", "Line 6"),
    ("quad cortex", "Neural DSP"),
    ("qcortex", "Neural DSP"),
    ("kemper", "Kemper"),
    ("profiler", "Kemper"),
    ("axe-fx", "Fractal"),
    ("axefx", "Fractal"),
    ("fm3", "Fractal"),
    ("fm9", "Fractal"),
    ("tonex", "IK Multimedia"),
    # Standalone audio interfaces.
    ("focusrite", "Focusrite"),
    ("scarlett", "Focusrite"),
    ("clarett", "Focusrite"),
    ("universal audio", "Universal Audio"),
    ("apollo", "Universal Audio"),
    ("volt", "Universal Audio"),
    ("audient", "Audient"),
    ("apogee", "Apogee"),
    ("steinberg", "Steinberg"),
    ("ur22", "Steinberg"),
    ("ur44", "Steinberg"),
    ("motu", "MOTU"),
    ("rme", "RME"),
    ("babyface", "RME"),
    ("fireface", "RME"),
    ("presonus", "PreSonus"),
    ("native instruments", "Native Instruments"),
    ("komplete audio", "Native Instruments"),
)


def _resolve_connect_binary() -> Optional[str]:
    """Locate the Connect CLI binary.

    Search order:
    1. ``CONNECT_BINARY`` env var (explicit override).
    2. ``/Applications/Connect.app/Contents/MacOS/Connect`` (release install).
    3. ``connect`` on PATH.
    4. ``connect/.build/debug/Connect`` relative to repo root (dev).
    """
    override = os.environ.get("CONNECT_BINARY")
    if override and Path(override).exists():
        return override

    release = "/Applications/Connect.app/Contents/MacOS/Connect"
    if Path(release).exists():
        return release

    on_path = shutil.which("connect")
    if on_path:
        return on_path

    # backend/tone_forge/devices/discovery.py -> repo root is 3 parents up.
    repo_root = Path(__file__).resolve().parents[3]
    dev_build = repo_root / "connect" / ".build" / "debug" / "Connect"
    if dev_build.exists():
        return str(dev_build)

    return None


def _vendor_hint_for(name: str) -> Optional[str]:
    lowered = name.lower()
    for needle, label in _VENDOR_HINTS:
        if needle in lowered:
            return label
    return None


def _choose_suggested_input(
    devices: Iterable[AudioDeviceInfo],
) -> Tuple[Optional[AudioDeviceInfo], Optional[str]]:
    """Pick the most likely guitar-input device + vendor hint.

    Preference: first input-capable device whose name matches a known
    vendor substring. Falls back to the first input-capable device.
    Returns ``(None, None)`` when nothing has inputs.
    """
    inputs = [d for d in devices if d.input_channels > 0]
    if not inputs:
        return None, None

    for dev in inputs:
        hint = _vendor_hint_for(dev.name)
        if hint is not None:
            return dev, hint

    return inputs[0], None


def _parse_devices(payload: dict) -> Tuple[AudioDeviceInfo, ...]:
    raw = payload.get("devices", [])
    if not isinstance(raw, list):
        raise ValueError("devices field must be a list")
    out = []
    for entry in raw:
        out.append(
            AudioDeviceInfo(
                device_id=int(entry["device_id"]),
                name=str(entry["name"]),
                input_channels=int(entry["input_channels"]),
                output_channels=int(entry["output_channels"]),
            )
        )
    return tuple(out)


def probe(timeout_seconds: float = 5.0) -> DeviceProbe:
    """Run ``connect devices --json`` and return a :class:`DeviceProbe`.

    Never raises. On any failure, returns an empty probe with
    ``probe_succeeded=False`` and a populated ``error_message``.
    """
    binary = _resolve_connect_binary()
    if binary is None:
        return DeviceProbe(
            devices=(),
            probe_succeeded=False,
            error_message="connect binary not found",
        )

    try:
        result = subprocess.run(
            [binary, "devices", "--json"],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return DeviceProbe(
            devices=(),
            probe_succeeded=False,
            error_message=f"probe timed out after {timeout_seconds}s",
        )
    except OSError as exc:
        return DeviceProbe(
            devices=(),
            probe_succeeded=False,
            error_message=f"probe exec failed: {exc}",
        )

    if result.returncode != 0:
        return DeviceProbe(
            devices=(),
            probe_succeeded=False,
            error_message=f"probe exit {result.returncode}: {result.stderr.strip()}",
        )

    try:
        payload = json.loads(result.stdout)
        devices = _parse_devices(payload)
    except (ValueError, KeyError, TypeError) as exc:
        return DeviceProbe(
            devices=(),
            probe_succeeded=False,
            error_message=f"probe json parse failed: {exc}",
        )

    suggested, vendor_hint = _choose_suggested_input(devices)
    return DeviceProbe(
        devices=devices,
        suggested_input=suggested,
        vendor_hint=vendor_hint,
        probe_succeeded=True,
    )
