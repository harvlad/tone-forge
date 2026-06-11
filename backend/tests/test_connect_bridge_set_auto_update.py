"""Pin the cross-process behaviour of the ``set_auto_update`` frame.

Background (per EXECUTION_PLAN §3C "Auto-update"):

The browser surfaces a checkbox for Sparkle's
``SUEnableAutomaticChecks`` preference. When the user flips it,
``jam.js`` POSTs the new value to ``/api/device/preferences``. The
server persists the field into ``device.json`` AND broadcasts a
``set_auto_update`` frame to every active ``_ConnectChannel`` so any
running Connect helper writes the value into its own
``UserDefaults`` without waiting for a restart.

On Connect-side WS join (i.e. a fresh helper bring-up), the server
also replays the persisted value if one is set, so a helper that
spawned after the user toggled the preference still picks it up.

This file pins four properties of that wiring:

1. **Broadcast on POST**: a POST that flips ``auto_update_enabled``
   to ``False`` reaches every connected peer in the channel.

2. **Replay on join**: a Connect peer joining a channel after the
   preference is persisted receives the value with
   ``replayed: True``.

3. **No-op POST does NOT broadcast**: POSTing the same value twice
   broadcasts only the first time. Without this guard, the JS
   onboarding submit (which re-saves the whole record) would spam
   every connected helper with redundant frames every time the user
   tweaks an unrelated field.

4. **Null preference does NOT replay on join**: when the user has
   never expressed a preference (``auto_update_enabled`` absent from
   ``device.json``), the join handshake does not include a
   ``set_auto_update`` frame. This matters because Sparkle's
   built-in default would otherwise be silently overwritten with
   whatever last value the helper happened to carry.

Why this lives in its own file instead of being folded into
``test_api_device_preferences.py``:
  The HTTP test file pins wire-shape on the GET/POST/DELETE
  endpoints; this file pins cross-process semantics (WS broadcast
  fanout + join replay) which need a TestClient WebSocket. Keeping
  them split mirrors the device_lost / replay split.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tone_forge_api import app  # noqa: E402

client = TestClient(app)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_prefs_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ``device.json`` into a per-test tmp directory so the
    POST under test never writes to the operator's real Application
    Support directory and so a stale value from one test cannot bleed
    into the next.
    """
    target = tmp_path / "device.json"
    monkeypatch.setenv("TONEFORGE_DEVICE_PREFS_PATH", str(target))
    return target


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hello(role: str, session_id: str) -> dict:
    return {
        "type": "hello",
        "role": role,
        "session_id": session_id,
        "protocol_version": 1,
    }


def _read_until(ws, predicate, max_frames: int = 8):
    """Skim past framing (hello_ack / joined / replays) looking for a
    specific frame. Returns ``None`` if not seen within ``max_frames``."""
    for _ in range(max_frames):
        frame = ws.receive_json()
        if predicate(frame):
            return frame
    return None


# ---------------------------------------------------------------------------
# 1. POST broadcasts to every active Connect peer
# ---------------------------------------------------------------------------


def test_post_auto_update_false_broadcasts_to_connected_peer(
    temp_prefs_path: Path,
) -> None:
    """The browser POSTs ``auto_update_enabled: False`` to
    ``/api/device/preferences``. The server must fan that change out
    over the WS bridge to every connected Connect peer so the helper
    writes ``UserDefaults`` without waiting for the next launch.
    """
    session = "test_set_auto_update_broadcast"

    # Seed the record with an initial preference so the POST below is
    # a *change*, not a fresh write. (The POST handler only broadcasts
    # on change.)
    client.post(
        "/api/device/preferences",
        json={"device_class": "helix", "auto_update_enabled": True},
    )

    with client.websocket_connect("/ws/connect-bridge") as connect_ws:
        connect_ws.send_json(_hello("connect", session))
        # Drain hello_ack + joined + the replay of the seed value
        # (``set_auto_update enabled=True replayed=True``). Up to 3
        # frames; ``_read_until`` will skip past them.
        # Drain hello_ack:
        ack = connect_ws.receive_json()
        assert ack.get("type") == "hello_ack"
        # The next two frames can arrive in either order
        # (joined / set_auto_update replay); read both.
        for _ in range(2):
            connect_ws.receive_json()

        # Now flip the value from the browser side.
        post = client.post(
            "/api/device/preferences",
            json={
                "device_class": "helix",
                "auto_update_enabled": False,
            },
        )
        assert post.status_code == 200

        received = _read_until(
            connect_ws,
            lambda f: f.get("type") == "set_auto_update",
        )
        assert received is not None, (
            "Connect peer did not receive set_auto_update after the "
            "browser POST flipped the preference"
        )
        assert received.get("enabled") is False
        assert received.get("replayed") is False, (
            "POST-triggered broadcasts must carry replayed=False so "
            "the helper can distinguish live user action from "
            "join-time replay (today they behave identically; pinning "
            "the field now keeps the option open)"
        )


# ---------------------------------------------------------------------------
# 2. Replay on join
# ---------------------------------------------------------------------------


def test_connect_join_replays_persisted_auto_update(
    temp_prefs_path: Path,
) -> None:
    """A fresh helper bring-up must learn the persisted preference
    via the join-time replay path. Without this a Connect that spawned
    after the user toggled the preference (typical "supervisor
    restarted me" flow) would run Sparkle with stale defaults.
    """
    # Persist the preference before any helper connects.
    client.post(
        "/api/device/preferences",
        json={"device_class": "helix", "auto_update_enabled": False},
    )

    with client.websocket_connect("/ws/connect-bridge") as connect_ws:
        connect_ws.send_json(_hello("connect", "test_join_replay"))
        # Inside the join window we expect: hello_ack, joined, and a
        # set_auto_update replay (order between joined/replay is not
        # guaranteed). Pull up to 4 frames looking for the replay.
        received = _read_until(
            connect_ws,
            lambda f: f.get("type") == "set_auto_update",
            max_frames=4,
        )
        assert received is not None, (
            "Connect peer did not receive a join-time replay of the "
            "persisted auto_update_enabled preference"
        )
        assert received.get("enabled") is False
        assert received.get("replayed") is True, (
            "join-time replay must carry replayed=True so the helper "
            "(and any test assertion that distinguishes them) can "
            "tell it apart from a live broadcast"
        )


# ---------------------------------------------------------------------------
# 3. No-op POST does not broadcast
# ---------------------------------------------------------------------------


def test_post_same_value_twice_broadcasts_only_once(
    temp_prefs_path: Path,
) -> None:
    """POSTing the same ``auto_update_enabled`` value twice must
    broadcast on the first POST (transition None → False) and not on
    the second (no transition). Guard against the JS onboarding flow
    which re-saves the whole record every time any field changes:
    without this guard a user editing an unrelated field would spam
    every Connect helper.
    """
    session = "test_no_broadcast_when_unchanged"

    with client.websocket_connect("/ws/connect-bridge") as connect_ws:
        connect_ws.send_json(_hello("connect", session))
        # No prefs persisted yet — drain only hello_ack + joined.
        connect_ws.receive_json()  # hello_ack
        connect_ws.receive_json()  # joined

        # First POST: None → False, expected to broadcast.
        client.post(
            "/api/device/preferences",
            json={
                "device_class": "helix",
                "auto_update_enabled": False,
            },
        )
        first = _read_until(
            connect_ws,
            lambda f: f.get("type") == "set_auto_update",
            max_frames=3,
        )
        assert first is not None
        assert first.get("enabled") is False

        # Second POST: same value. No broadcast expected.
        client.post(
            "/api/device/preferences",
            json={
                "device_class": "helix",
                "auto_update_enabled": False,
            },
        )
        # Probe via ping/pong: if a duplicate set_auto_update were
        # queued behind the no-op POST we would see it here instead
        # of pong. Mirrors the assertion shape used in the device_lost
        # late-joiner test.
        connect_ws.send_json({"type": "ping"})
        response = connect_ws.receive_json()
        assert response == {"type": "pong"}, (
            f"second no-op POST broadcast an extra set_auto_update; "
            f"saw {response!r} where pong was expected"
        )


# ---------------------------------------------------------------------------
# 4. Null preference does NOT replay
# ---------------------------------------------------------------------------


def test_connect_join_does_not_replay_when_pref_unset(
    temp_prefs_path: Path,
) -> None:
    """When the user has never expressed an auto-update preference,
    a Connect join must not emit a ``set_auto_update`` frame. The
    helper should fall back to Sparkle's built-in default (which is
    "checks enabled" when no UserDefaults override exists). Emitting
    a ``set_auto_update`` here would force a UserDefaults write the
    user didn't ask for and pin the helper to a value Sparkle's
    future defaults can never change.
    """
    # Persist a record WITHOUT auto_update_enabled to model a user
    # who completed onboarding before the §3C toggle existed.
    client.post(
        "/api/device/preferences",
        json={"device_class": "helix"},
    )

    with client.websocket_connect("/ws/connect-bridge") as connect_ws:
        connect_ws.send_json(_hello("connect", "test_no_replay_when_null"))
        # Drain hello_ack + joined; assert neither is set_auto_update,
        # and that a ping/pong round-trip with nothing queued behind
        # confirms no replay was issued.
        for _ in range(2):
            frame = connect_ws.receive_json()
            assert frame.get("type") != "set_auto_update", (
                "set_auto_update must NOT replay when the persisted "
                "preference is null — that would force a UserDefaults "
                "write the user didn't request"
            )

        connect_ws.send_json({"type": "ping"})
        response = connect_ws.receive_json()
        assert response == {"type": "pong"}, (
            f"join handshake leaked an extra frame when no auto-update "
            f"preference was persisted: {response!r}"
        )
