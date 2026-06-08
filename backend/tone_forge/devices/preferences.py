"""Load and save ``DevicePreferences`` to ``device.json``.

The onboarding flow asks one question (see EXECUTION_PLAN.md §8) and
persists the answer here. The UI re-prompts only when
``load_preferences()`` returns ``None`` or the user explicitly opens
device settings.

Storage location is ``~/Library/Application Support/ToneForge/device.json``
on macOS. The ``TONEFORGE_DEVICE_PREFS_PATH`` env var overrides for
tests and headless dev runs. Writes go through a temp-file + rename so
a crash mid-save cannot leave a half-written JSON the next load will
fail to parse.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from tone_forge.contracts import (
    DeviceClass,
    DevicePreferences,
    MonitorChainFamily,
)

_FILENAME = "device.json"
_APP_SUPPORT_SUBDIR = "ToneForge"
_ENV_OVERRIDE = "TONEFORGE_DEVICE_PREFS_PATH"


def preferences_path() -> Path:
    """Resolve the on-disk path for ``device.json``.

    Honors the ``TONEFORGE_DEVICE_PREFS_PATH`` env override (useful in
    tests and CI). Otherwise points at the macOS Application Support
    directory per the plan.
    """
    override = os.environ.get(_ENV_OVERRIDE)
    if override:
        return Path(override)

    home = Path.home()
    return home / "Library" / "Application Support" / _APP_SUPPORT_SUBDIR / _FILENAME


def _now_iso() -> str:
    """UTC ISO-8601 timestamp with seconds precision."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _to_dict(prefs: DevicePreferences) -> dict:
    return {
        "device_class": prefs.device_class.value,
        "audio_input_name": prefs.audio_input_name,
        "preferred_chain_family": (
            prefs.preferred_chain_family.value
            if prefs.preferred_chain_family is not None
            else None
        ),
        "first_seen_iso": prefs.first_seen_iso,
        "last_used_iso": prefs.last_used_iso,
    }


def _from_dict(payload: dict) -> DevicePreferences:
    """Project a parsed JSON dict into a :class:`DevicePreferences`.

    Unknown ``device_class`` or ``preferred_chain_family`` values raise
    ``ValueError`` so the caller can decide whether to delete the file
    or surface an error. The schema is small enough that silent
    coercion would mask real corruption.
    """
    raw_class = payload.get("device_class")
    if raw_class is None:
        raise ValueError("device.json missing device_class")
    device_class = DeviceClass(raw_class)

    raw_family = payload.get("preferred_chain_family")
    family: Optional[MonitorChainFamily]
    if raw_family is None:
        family = None
    else:
        family = MonitorChainFamily(raw_family)

    return DevicePreferences(
        device_class=device_class,
        audio_input_name=payload.get("audio_input_name"),
        preferred_chain_family=family,
        first_seen_iso=payload.get("first_seen_iso"),
        last_used_iso=payload.get("last_used_iso"),
    )


def load_preferences() -> Optional[DevicePreferences]:
    """Return the persisted preferences, or ``None`` if absent or invalid.

    Invalid files (malformed JSON or unknown enum values) return
    ``None`` so the UI can fall back to re-prompting rather than
    crashing the app on a corrupted file from an older build.
    """
    path = preferences_path()
    if not path.exists():
        return None

    try:
        payload = json.loads(path.read_text())
    except (OSError, ValueError):
        return None

    if not isinstance(payload, dict):
        return None

    try:
        return _from_dict(payload)
    except (ValueError, KeyError):
        return None


def save_preferences(prefs: DevicePreferences) -> DevicePreferences:
    """Persist ``prefs`` and return what was written.

    Stamps ``first_seen_iso`` (only if missing) and ``last_used_iso``
    automatically — callers do not have to set them. Returns the
    stamped instance so the caller has the canonical record.

    Atomic via temp-file + ``os.replace`` so a crash mid-write cannot
    leave the file half-serialized.
    """
    now = _now_iso()
    stamped = DevicePreferences(
        device_class=prefs.device_class,
        audio_input_name=prefs.audio_input_name,
        preferred_chain_family=prefs.preferred_chain_family,
        first_seen_iso=prefs.first_seen_iso or now,
        last_used_iso=now,
    )

    path = preferences_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    payload = json.dumps(_to_dict(stamped), indent=2, sort_keys=True)

    # Same-directory tempfile so os.replace is atomic on the same fs.
    fd, tmp_name = tempfile.mkstemp(
        prefix=".device-", suffix=".json.tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(payload)
        os.replace(tmp_name, path)
    except OSError:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise

    return stamped


def clear_preferences() -> None:
    """Delete ``device.json`` if present. Used by 'forget this device' UX."""
    path = preferences_path()
    try:
        path.unlink()
    except FileNotFoundError:
        pass
