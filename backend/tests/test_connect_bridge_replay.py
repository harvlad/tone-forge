"""Tests for ``_ConnectChannel.join()`` replay of cached session state.

The connect-bridge endpoint caches the last ``preset_push`` payload,
``set_gain`` value, and ``apply_chain`` spec on the in-memory
``_ConnectChannel`` (`tone_forge_api.py:398-417`). When a peer joins
a channel that already has cached state, those frames must be replayed
to the new peer before the channel sees any further broadcasts. That
is the entire mechanism behind "Connect crashed and reconnected — it
must come back up showing the same gain / preset / chain the browser
was driving before."

The ``apply_chain`` replay path was already pinned by
``test_last_chain_replayed_to_late_joining_peer`` in
``test_connect_bridge_apply_chain.py``. The ``set_gain`` and
``preset_push`` replay paths were implemented in the same block but
had no test coverage — a refactor that broke either path would have
shipped silently. This file closes that gap.

What we DO pin
--------------

* ``set_gain`` replay: a peer joining after ``last_gain`` is cached
  receives a ``set_gain`` frame carrying that value and
  ``replayed: True``.
* ``preset_push`` replay: same shape for ``last_preset``.
* Replay does not broadcast to other peers. The cached frames go
  only to the joiner; existing peers are not re-notified of state
  they already know.
* A channel with no cached state replays nothing. A peer joining a
  fresh channel sees ``hello_ack`` + ``joined`` and then silence.

What we don't pin
-----------------

* ``last_transport_state`` is mentioned in EXECUTION_PLAN §3E but is
  not a connect-bridge concern — transport state belongs on the
  Session Engine route, not the connect-bridge endpoint. The §3E
  text was updated to reflect the boundary; replay of transport
  state will land (or not) as part of Session Engine work.

* Ordering between ``hello_ack`` / replay frames / ``joined``. The
  current implementation emits replay frames after ``join()`` but
  before the ``joined`` ack on the new socket, and the existing
  apply_chain test already documents the "interleave by reading
  ahead a few frames" pattern. We do the same here. Pinning a
  specific order would couple the test to an implementation detail
  the contract doesn't require.
"""
from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tone_forge_api import app  # noqa: E402

client = TestClient(app)


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


def _read_until(ws, predicate, max_frames: int = 6) -> dict | None:
    """Read up to ``max_frames`` frames; return the first that matches
    ``predicate``, or ``None``. Used to skim past ``hello_ack`` /
    ``joined`` framing while looking for a specific replay frame
    without coupling to a fixed order."""
    for _ in range(max_frames):
        frame = ws.receive_json()
        if predicate(frame):
            return frame
    return None


# ---------------------------------------------------------------------------
# set_gain replay
# ---------------------------------------------------------------------------


def test_last_gain_replayed_to_late_joining_peer() -> None:
    """A peer joining after the browser has set the monitor gain must
    receive ``set_gain`` carrying ``replayed: True``. Pins the path
    that lets a crashed Connect helper reattach without the browser
    re-sending its last gain."""
    session = "test_replay_gain_basic"

    with client.websocket_connect("/ws/connect-bridge") as browser_ws:
        browser_ws.send_json(_hello("browser", session))
        # Drain hello_ack + joined.
        browser_ws.receive_json()
        browser_ws.receive_json()

        browser_ws.send_json({"type": "set_gain", "gain": 0.73})

        # Late-join a connect peer.
        with client.websocket_connect("/ws/connect-bridge") as connect_ws:
            connect_ws.send_json(_hello("connect", session))
            replay = _read_until(
                connect_ws,
                lambda f: f.get("type") == "set_gain",
            )
            assert replay is not None, (
                "Reconnecting peer did not receive cached set_gain"
            )
            assert replay.get("replayed") is True
            # Server clamps gain into [0, 1]; 0.73 round-trips intact.
            assert replay.get("gain") == 0.73


def test_last_gain_replay_carries_clamped_value() -> None:
    """The replay carries whatever the server cached, which is the
    clamped (post-validation) value — not the raw client value. A peer
    that joins after the channel saw an out-of-range gain sees the
    clamped result, matching what the partner peer already broadcast."""
    session = "test_replay_gain_clamped"

    with client.websocket_connect("/ws/connect-bridge") as browser_ws:
        browser_ws.send_json(_hello("browser", session))
        browser_ws.receive_json()
        browser_ws.receive_json()
        # Drive an out-of-range value; server clamps to 1.0.
        browser_ws.send_json({"type": "set_gain", "gain": 5.5})

        with client.websocket_connect("/ws/connect-bridge") as connect_ws:
            connect_ws.send_json(_hello("connect", session))
            replay = _read_until(
                connect_ws,
                lambda f: f.get("type") == "set_gain",
            )
            assert replay is not None
            assert replay.get("gain") == 1.0
            assert replay.get("replayed") is True


# ---------------------------------------------------------------------------
# preset_push replay
# ---------------------------------------------------------------------------


def test_last_preset_replayed_to_late_joining_peer() -> None:
    """A peer joining after a ``preset_push`` was cached must receive
    the cached preset as a replay frame. Pins the path that lets a
    fresh Connect helper inherit the currently-applied preset on
    reattach."""
    session = "test_replay_preset_basic"

    cached_preset = {
        "id": "preset-replay-fixture",
        "tone": {"gain_db": 4.0, "treble_db": 1.5},
    }

    with client.websocket_connect("/ws/connect-bridge") as browser_ws:
        browser_ws.send_json(_hello("browser", session))
        browser_ws.receive_json()
        browser_ws.receive_json()
        browser_ws.send_json({
            "type": "preset_push",
            "preset": cached_preset,
            "request_id": "req-preset-replay",
        })
        # browser gets its own ack (no broadcast back).
        ack = browser_ws.receive_json()
        assert ack["type"] == "ack"

        with client.websocket_connect("/ws/connect-bridge") as connect_ws:
            connect_ws.send_json(_hello("connect", session))
            replay = _read_until(
                connect_ws,
                lambda f: f.get("type") == "preset_push",
            )
            assert replay is not None, (
                "Reconnecting peer did not receive cached preset"
            )
            assert replay.get("replayed") is True
            assert replay.get("preset") == cached_preset


# ---------------------------------------------------------------------------
# Replay is targeted: it does not echo to existing peers
# ---------------------------------------------------------------------------


def test_replay_does_not_re_broadcast_to_existing_peers() -> None:
    """The cached state was already broadcast to existing peers when the
    intent originally arrived. A new joiner triggers replay *to the
    joiner*; existing peers must not see a second copy. Without this
    guarantee a reconnect cycle would double-fire every cached frame
    on the partner peer."""
    session = "test_replay_targeted"

    with client.websocket_connect("/ws/connect-bridge") as browser_ws:
        browser_ws.send_json(_hello("browser", session))
        browser_ws.receive_json()
        browser_ws.receive_json()
        browser_ws.send_json({"type": "set_gain", "gain": 0.42})

        # Late-join a connect peer; replay fires.
        with client.websocket_connect("/ws/connect-bridge") as connect_ws:
            connect_ws.send_json(_hello("connect", session))
            # Drain everything available on connect_ws so the channel
            # is quiescent.
            _read_until(connect_ws, lambda f: f.get("type") == "set_gain")

            # browser_ws must not have received a duplicate set_gain
            # frame just because a new peer joined. The only frame
            # we expect on browser_ws as a result of a connect
            # joining is *nothing* — the join itself is silent to
            # existing peers (peers counter changes are advisory and
            # not currently broadcast). Send a probe frame to flush
            # any pending state, and assert we don't see a set_gain
            # echo come back at us.
            #
            # We use ping/pong as the probe: any inbound frame other
            # than pong (or nothing at all) would be evidence of a
            # re-broadcast.
            browser_ws.send_json({"type": "ping"})
            response = browser_ws.receive_json()
            assert response == {"type": "pong"}, (
                f"existing peer saw an unexpected frame "
                f"(possible re-broadcast on join): {response!r}"
            )


# ---------------------------------------------------------------------------
# Fresh channel: nothing to replay
# ---------------------------------------------------------------------------


def test_fresh_channel_replays_nothing() -> None:
    """A peer joining a channel that has never cached any state sees
    only ``hello_ack`` + ``joined`` and nothing else. Pins the
    "no-cached-state → silent join" path so a future refactor that
    eagerly emits empty frames is caught."""
    session = "test_replay_fresh_channel"

    with client.websocket_connect("/ws/connect-bridge") as ws:
        ws.send_json(_hello("browser", session))
        hello_ack = ws.receive_json()
        joined = ws.receive_json()
        assert hello_ack["type"] == "hello_ack"
        assert joined["type"] == "joined"

        # Probe with ping; the next inbound frame should be pong,
        # proving no spurious replay frame snuck in.
        ws.send_json({"type": "ping"})
        response = ws.receive_json()
        assert response == {"type": "pong"}
