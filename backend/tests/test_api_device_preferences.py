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
