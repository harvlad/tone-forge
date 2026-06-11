"""HTTP-level coverage for the ``/api/device/preferences`` routes.

The persistence layer is already covered by
``test_devices_preferences.py``; this file pins the *wire shape* and
the *route behavior*:

  1. ``GET`` returns ``null`` when no record exists (so the Jam UI
     can short-circuit to onboarding with a single check).
  2. ``POST`` round-trips: a follow-up ``GET`` returns the saved
     record with ``first_seen_iso`` / ``last_used_iso`` stamped.
  3. ``POST`` preserves ``first_seen_iso`` across re-saves but
     refreshes ``last_used_iso`` (matches the §8 "re-prompt only on
     new install" policy).
  4. ``POST`` returns 400 on unknown ``device_class`` /
     ``preferred_chain_family`` so the UI fails fast rather than
     writing a record that ``load_preferences`` will later reject.
  5. ``DELETE`` clears the record and is idempotent (matches
     "Reset device choice" UX).
  6. The env override (``TONEFORGE_DEVICE_PREFS_PATH``) is honored
     so the test never writes to the operator's real Application
     Support directory.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from tone_forge_api import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_prefs_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect device.json into a per-test tmp directory."""
    target = tmp_path / "device.json"
    monkeypatch.setenv("TONEFORGE_DEVICE_PREFS_PATH", str(target))
    return target


# ---------------------------------------------------------------------------
# GET — absent record
# ---------------------------------------------------------------------------


def test_get_returns_null_when_no_record(temp_prefs_path: Path) -> None:
    response = client.get("/api/device/preferences")
    assert response.status_code == 200
    assert response.json() is None


# ---------------------------------------------------------------------------
# POST round-trip
# ---------------------------------------------------------------------------


def test_post_then_get_round_trips(temp_prefs_path: Path) -> None:
    payload = {
        "device_class": "helix",
        "audio_input_name": "Focusrite Scarlett 2i2",
        "preferred_chain_family": "edge_of_breakup",
    }
    post = client.post("/api/device/preferences", json=payload)
    assert post.status_code == 200
    saved = post.json()
    assert saved["device_class"] == "helix"
    assert saved["audio_input_name"] == "Focusrite Scarlett 2i2"
    assert saved["preferred_chain_family"] == "edge_of_breakup"
    assert saved["first_seen_iso"] is not None
    assert saved["last_used_iso"] is not None

    get = client.get("/api/device/preferences")
    assert get.status_code == 200
    assert get.json() == saved


def test_post_accepts_minimal_payload(temp_prefs_path: Path) -> None:
    """Only ``device_class`` is required — everything else is optional."""
    response = client.post(
        "/api/device/preferences",
        json={"device_class": "interface_only"},
    )
    assert response.status_code == 200
    saved = response.json()
    assert saved["device_class"] == "interface_only"
    assert saved["audio_input_name"] is None
    assert saved["preferred_chain_family"] is None


# ---------------------------------------------------------------------------
# POST — first_seen preserved across re-saves
# ---------------------------------------------------------------------------


def test_post_preserves_first_seen_iso_across_resaves(temp_prefs_path: Path) -> None:
    first = client.post(
        "/api/device/preferences",
        json={"device_class": "helix"},
    ).json()

    second = client.post(
        "/api/device/preferences",
        json={"device_class": "quad_cortex"},
    ).json()

    assert second["first_seen_iso"] == first["first_seen_iso"]
    # device_class updated to the new answer.
    assert second["device_class"] == "quad_cortex"


# ---------------------------------------------------------------------------
# POST — validation errors
# ---------------------------------------------------------------------------


def test_post_rejects_unknown_device_class(temp_prefs_path: Path) -> None:
    response = client.post(
        "/api/device/preferences",
        json={"device_class": "not_a_real_device"},
    )
    assert response.status_code == 400
    assert "device_class" in response.json()["detail"]


def test_post_rejects_unknown_chain_family(temp_prefs_path: Path) -> None:
    response = client.post(
        "/api/device/preferences",
        json={
            "device_class": "interface_only",
            "preferred_chain_family": "made_up_family",
        },
    )
    assert response.status_code == 400
    assert "preferred_chain_family" in response.json()["detail"]


def test_post_rejects_missing_device_class(temp_prefs_path: Path) -> None:
    """Pydantic returns 422 on missing required field."""
    response = client.post("/api/device/preferences", json={})
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# DELETE
# ---------------------------------------------------------------------------


def test_delete_clears_record(temp_prefs_path: Path) -> None:
    client.post(
        "/api/device/preferences",
        json={"device_class": "helix"},
    )
    assert client.get("/api/device/preferences").json() is not None

    response = client.delete("/api/device/preferences")
    assert response.status_code == 200
    assert response.json() == {"ok": True}

    assert client.get("/api/device/preferences").json() is None


def test_delete_is_idempotent(temp_prefs_path: Path) -> None:
    """Deleting a non-existent record is not an error."""
    response = client.delete("/api/device/preferences")
    assert response.status_code == 200
    assert response.json() == {"ok": True}

    # Second delete also succeeds.
    response = client.delete("/api/device/preferences")
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# auto_update_enabled (§3C — Sparkle opt-in toggle)
# ---------------------------------------------------------------------------
#
# Wire-shape regression tests for the new ``auto_update_enabled`` field
# on the existing preferences record. The cross-process semantics — the
# WS broadcast to the Connect peer, the replay-on-join — live in
# ``test_connect_bridge_set_auto_update.py`` so this file stays
# focused on the HTTP layer.


def test_get_returns_auto_update_null_on_fresh_record(temp_prefs_path: Path) -> None:
    """A fresh record (POST without ``auto_update_enabled``) returns
    ``null`` for the field. ``null`` is the wire signal for "no
    expressed preference"; the browser maps it to checked-by-default
    because Sparkle's runtime default is "enabled" when no override
    is set. See ``updateAutoUpdateSetting`` in ``jam.js``.
    """
    saved = client.post(
        "/api/device/preferences",
        json={"device_class": "helix"},
    ).json()
    assert saved["auto_update_enabled"] is None

    fetched = client.get("/api/device/preferences").json()
    assert fetched["auto_update_enabled"] is None


def test_post_round_trips_auto_update_false(temp_prefs_path: Path) -> None:
    """Explicit opt-out persists across GET. Pinning ``False`` not
    ``None`` matters because the Connect helper interprets these
    differently: ``False`` → ``UserDefaults.SUEnableAutomaticChecks =
    NO``; ``None`` → no UserDefaults write (Sparkle default applies).
    """
    saved = client.post(
        "/api/device/preferences",
        json={"device_class": "helix", "auto_update_enabled": False},
    ).json()
    assert saved["auto_update_enabled"] is False

    fetched = client.get("/api/device/preferences").json()
    assert fetched["auto_update_enabled"] is False


def test_post_round_trips_auto_update_true(temp_prefs_path: Path) -> None:
    """Explicit opt-in also persists. Belt-and-braces for the case
    where a user opts out and later opts back in — we must write
    ``True``, not ``None``, so the UserDefaults override stays in
    place even if Sparkle's default ever changes.
    """
    saved = client.post(
        "/api/device/preferences",
        json={"device_class": "helix", "auto_update_enabled": True},
    ).json()
    assert saved["auto_update_enabled"] is True

    fetched = client.get("/api/device/preferences").json()
    assert fetched["auto_update_enabled"] is True


def test_post_auto_update_does_not_clobber_other_fields(
    temp_prefs_path: Path,
) -> None:
    """A POST that re-sends the existing device_class + flips only
    the auto-update bool must not drop other fields. Guards the JS
    settings handler which re-reads the record via GET before
    POSTing the toggle change — a regression here would silently
    drop ``audio_input_name`` from the persisted record.
    """
    first = client.post(
        "/api/device/preferences",
        json={
            "device_class": "helix",
            "audio_input_name": "Focusrite Scarlett 2i2",
            "preferred_chain_family": "edge_of_breakup",
        },
    ).json()
    assert first["audio_input_name"] == "Focusrite Scarlett 2i2"

    second = client.post(
        "/api/device/preferences",
        json={
            "device_class": first["device_class"],
            "audio_input_name": first["audio_input_name"],
            "preferred_chain_family": first["preferred_chain_family"],
            "auto_update_enabled": False,
        },
    ).json()
    assert second["audio_input_name"] == "Focusrite Scarlett 2i2"
    assert second["preferred_chain_family"] == "edge_of_breakup"
    assert second["auto_update_enabled"] is False
    # first_seen_iso preserved (re-saves rule).
    assert second["first_seen_iso"] == first["first_seen_iso"]
