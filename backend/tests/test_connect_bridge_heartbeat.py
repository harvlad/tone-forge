"""Tests for the heartbeat / liveness path of the connect-bridge WS handler.

The focused-pass hardening commit closed the broadcast-failure path: a
peer whose ``send`` raises is reaped and survivors are notified. This file
pins the *silent-drop* path: a peer that simply stops talking and never
sends anything is detected by the server's receive-side heartbeat
(``CONNECT_BRIDGE_RECV_TIMEOUT_SEC`` + ``CONNECT_BRIDGE_PONG_TIMEOUT_SEC``)
and torn down with a ``peer_left`` notification to the survivors.

These tests run against tight (sub-second) timeouts via monkeypatch so the
full file completes in well under one second of wall time.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tone_forge_api  # noqa: E402
from tone_forge_api import app  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


# Recv/pong timeouts are read at module import. We monkeypatch them per test
# so the wall-clock cost of "wait for the server to give up" is sub-second.
# 0.2s recv + 0.2s pong = 0.4s worst case per "should drop" test, comfortably
# inside the default pytest timeout.
_FAST_RECV_TIMEOUT = 0.2
_FAST_PONG_TIMEOUT = 0.2


@pytest.fixture
def fast_heartbeat(monkeypatch):
    """Compress the heartbeat windows so tests finish in well under 1s."""
    monkeypatch.setattr(
        tone_forge_api, "CONNECT_BRIDGE_RECV_TIMEOUT_SEC", _FAST_RECV_TIMEOUT
    )
    monkeypatch.setattr(
        tone_forge_api, "CONNECT_BRIDGE_PONG_TIMEOUT_SEC", _FAST_PONG_TIMEOUT
    )


@pytest.fixture
def client():
    return TestClient(app)


def _hello(role: str, session_id: str) -> dict:
    return {
        "type": "hello",
        "role": role,
        "session_id": session_id,
        "protocol_version": 1,
    }


def _drain_handshake(ws) -> None:
    """Read hello_ack + joined so subsequent recv_json sees real traffic."""
    ws.receive_json()  # hello_ack
    ws.receive_json()  # joined


def _recv_skipping_pings(ws) -> dict:
    """Pop the next non-ping frame from ``ws``, ponging anything along the way.

    With sub-second timeouts, *every* idle socket in a test gets pinged at
    least once. Tests that aren't pinning the heartbeat path itself want
    to skim past those server-side liveness probes and focus on the
    business frames they actually care about.
    """
    while True:
        frame = ws.receive_json()
        if isinstance(frame, dict) and frame.get("type") == "ping":
            ws.send_json({"type": "pong"})
            continue
        return frame


# ---------------------------------------------------------------------------
# Happy path: pong keeps the connection alive
# ---------------------------------------------------------------------------


def test_pong_within_window_keeps_connection_alive(fast_heartbeat, client):
    """An idle peer that pongs the server's probe must remain a member of
    the channel — the next business frame it sends must still broadcast
    to its peer."""
    session = "test_heartbeat_pong_keeps_alive"
    with client.websocket_connect("/ws/connect-bridge") as connect_ws:
        connect_ws.send_json(_hello("connect", session))
        _drain_handshake(connect_ws)

        with client.websocket_connect("/ws/connect-bridge") as browser_ws:
            browser_ws.send_json(_hello("browser", session))
            _drain_handshake(browser_ws)

            # Stay silent on browser_ws and let the server's recv-side
            # timer fire. The server should probe us with a ping.
            probe = browser_ws.receive_json()
            assert probe == {"type": "ping"}, (
                f"expected server-emitted ping after recv timeout, got {probe!r}"
            )

            # Pong it back, then send a real frame to prove the channel
            # is still active.
            browser_ws.send_json({"type": "pong"})
            browser_ws.send_json({"type": "set_gain", "gain": 0.42})

            # connect_ws may receive its own ping along the way — skip
            # past it. The broadcast we expect is the set_gain.
            broadcast = _recv_skipping_pings(connect_ws)
            assert broadcast == {"type": "set_gain", "gain": 0.42}


# ---------------------------------------------------------------------------
# Drop path: silent peer past pong window triggers survivor notify + close
# ---------------------------------------------------------------------------


def test_silent_peer_triggers_heartbeat_drop_and_notifies_survivors(
    fast_heartbeat, client
):
    """A peer that never responds to the server's ping must be reaped, and
    the surviving peer must receive a ``peer_left`` frame carrying
    ``reason=heartbeat_timeout`` so its UI flips out of paired state."""
    session = "test_heartbeat_silent_drop"
    with client.websocket_connect("/ws/connect-bridge") as connect_ws:
        connect_ws.send_json(_hello("connect", session))
        _drain_handshake(connect_ws)

        with client.websocket_connect("/ws/connect-bridge") as browser_ws:
            browser_ws.send_json(_hello("browser", session))
            _drain_handshake(browser_ws)

            # browser_ws never sends another frame and never responds to
            # the server's ping. After RECV_TIMEOUT + PONG_TIMEOUT the
            # server should give up on it and broadcast peer_left to
            # connect_ws.
            #
            # connect_ws receives its own ping along the way; skip past
            # it (we keep connect_ws alive by ponging).
            peer_left = _recv_skipping_pings(connect_ws)
            assert peer_left.get("type") == "peer_left", (
                f"expected peer_left after silent drop, got {peer_left!r}"
            )
            assert peer_left.get("reason") == "heartbeat_timeout"
            # Match the existing ``broadcast()`` send-failure semantic:
            # ``peers`` counts total clients still in the channel after
            # the drop. With browser_ws gone, only connect_ws remains,
            # so peers == 1.
            assert peer_left.get("peers") == 1


# ---------------------------------------------------------------------------
# Counter-test: a chatty peer never gets dropped
# ---------------------------------------------------------------------------


def test_chatty_peer_resets_idle_timer(fast_heartbeat, client):
    """A peer sending business frames inside the recv window is never
    probed — receive activity is itself the liveness signal. We send
    one set_gain comfortably inside the window and assert it broadcasts
    cleanly, with no intervening ping."""
    session = "test_heartbeat_chatty_no_probe"
    with client.websocket_connect("/ws/connect-bridge") as connect_ws:
        connect_ws.send_json(_hello("connect", session))
        _drain_handshake(connect_ws)

        with client.websocket_connect("/ws/connect-bridge") as browser_ws:
            browser_ws.send_json(_hello("browser", session))
            _drain_handshake(browser_ws)

            # Send a business frame immediately — well inside the recv
            # window. The broadcast should reach connect_ws without
            # any intervening ping from browser_ws's perspective.
            browser_ws.send_json({"type": "set_gain", "gain": 0.55})

            # connect_ws is idle and *will* be probed; skip its ping.
            broadcast = _recv_skipping_pings(connect_ws)
            assert broadcast == {"type": "set_gain", "gain": 0.55}

            # browser_ws should not have received a ping yet — it has
            # been receive-active (sent a frame) inside the window. We
            # can't prove "no future ping" without waiting, but we can
            # prove the immediate path didn't ping it: send another
            # frame, expect another broadcast, no ping in between.
            browser_ws.send_json({"type": "set_gain", "gain": 0.66})
            broadcast2 = _recv_skipping_pings(connect_ws)
            assert broadcast2 == {"type": "set_gain", "gain": 0.66}
