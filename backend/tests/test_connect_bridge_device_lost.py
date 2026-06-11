"""Pin the server-side passthrough of the ``device_lost`` event frame.

Background (per EXECUTION_PLAN §3E "Audio device loss"):

The Connect helper's ``AudioEngine`` runs ``AVAudioEngineConfigurationChange``
recovery with a bounded retry budget (``maxReconfigAttempts``, default 5).
When the budget exhausts — typically the user unplugged their audio
interface and didn't plug it back in — the helper emits a one-shot
``device_lost`` frame to the WS bridge. The browser surfaces a
reconnection toast so the user knows the helper is alive but its
audio path is permanently broken.

The frame is **Connect → server → browser**. The server-side
``_ConnectChannel`` has no explicit handler for ``device_lost``; the
frame falls through to the default-broadcast branch at the WS edge
(``tone_forge_api.py:664-667``) and is relayed verbatim to every
other peer in the channel.

This test file pins three properties of that path:

1. A ``device_lost`` frame from a ``connect`` peer reaches a
   browser peer in the same channel.

2. The frame is **not cached for replay** — a late-joining peer does
   NOT receive a prior ``device_lost`` event. Rationale (see §3E):
   ``device_lost`` is a transient event signalling the *current*
   audio path is broken; on the next supervisor restart Connect
   spins up a fresh ``AudioEngine`` and either succeeds (no event
   needed) or re-emits ``device_lost`` (fresh event delivered).
   Replaying a stale event would lie to the late joiner.

3. The frame's ``reason`` field round-trips unmodified. The server
   does not interpret it; the slug is opaque to the bridge and is
   the contract between Swift (which sets it) and ``jam.js`` (which
   may branch on it). Pinning round-trip prevents an over-eager
   future refactor from rewriting / dropping it.

Why this lives in its own file instead of being folded into
``test_connect_bridge_replay.py``:
``device_lost`` deliberately escapes the replay cache; grouping it
with replay tests would muddle the contract. The cache-shape tests
(``last_preset`` / ``last_gain`` / ``last_chain``) and this
non-cache event test pin opposite contracts.
"""
from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tone_forge_api import app  # noqa: E402

client = TestClient(app)


def _hello(role: str, session_id: str) -> dict:
    return {
        "type": "hello",
        "role": role,
        "session_id": session_id,
        "protocol_version": 1,
    }


def _read_until(ws, predicate, max_frames: int = 6):
    """Skim past framing (hello_ack / joined / unrelated replays)
    looking for a specific frame. Returns ``None`` if not seen within
    ``max_frames``.
    """
    for _ in range(max_frames):
        frame = ws.receive_json()
        if predicate(frame):
            return frame
    return None


def _drive_server(ws) -> None:
    """Force the TestClient portal to process queued frames on ``ws``.

    See ``test_connect_bridge_apply_chain.py::_drive_server`` for the
    long-form rationale. Short version: starlette's TestClient runs the
    async server through a single blocking portal; ``send_json``
    enqueues but does not drive the loop. ``device_lost`` carries no
    ``request_id`` and gets no ack, so without an explicit drive the
    server never processes the frame and the cross-socket read for
    the broadcast hangs. Sending ``ping`` + reading ``pong`` on the
    sender's socket forces the queue to drain.
    """
    ws.send_json({"type": "ping"})
    pong = ws.receive_json()
    assert pong.get("type") == "pong"


# ---------------------------------------------------------------------------
# 1. Connect → server → browser passthrough
# ---------------------------------------------------------------------------


def test_device_lost_from_connect_peer_broadcasts_to_browser() -> None:
    """A ``device_lost`` frame emitted by the helper must land on the
    browser peer in the same channel. Pins the default-broadcast
    fallthrough at ``tone_forge_api.py:664-667``."""
    session = "test_device_lost_broadcast"

    with client.websocket_connect("/ws/connect-bridge") as browser_ws:
        browser_ws.send_json(_hello("browser", session))
        # Drain hello_ack + joined for the browser peer.
        browser_ws.receive_json()
        browser_ws.receive_json()

        with client.websocket_connect("/ws/connect-bridge") as connect_ws:
            connect_ws.send_json(_hello("connect", session))
            # Drain helper-side hello_ack + joined.
            connect_ws.receive_json()
            connect_ws.receive_json()

            # The helper learned its audio engine couldn't recover.
            connect_ws.send_json({
                "v": 1,
                "type": "device_lost",
                "reason": "reconfig_exhausted_after_5_attempts",
            })
            # Force the server's event loop to actually process the
            # fire-and-forget frame so the broadcast lands before the
            # cross-socket read below.
            _drive_server(connect_ws)

            # Browser should see the frame (possibly after an updated
            # `joined`/peer-count broadcast on the connect peer joining).
            received = _read_until(
                browser_ws,
                lambda f: f.get("type") == "device_lost",
            )
            assert received is not None, (
                "browser peer did not receive device_lost from helper"
            )
            assert received.get("reason") == (
                "reconfig_exhausted_after_5_attempts"
            ), "reason field must round-trip unmodified"


# ---------------------------------------------------------------------------
# 2. device_lost is NOT replayed to a late joiner
# ---------------------------------------------------------------------------


def test_device_lost_is_not_cached_for_late_joiners() -> None:
    """A peer joining *after* a ``device_lost`` event was broadcast
    must NOT receive a replay of that event. ``device_lost`` is a
    transient event, not a persistent cached state — replaying it
    would misrepresent the current device state to the late joiner.
    """
    session = "test_device_lost_not_replayed"

    with client.websocket_connect("/ws/connect-bridge") as browser_ws:
        browser_ws.send_json(_hello("browser", session))
        browser_ws.receive_json()  # hello_ack
        browser_ws.receive_json()  # joined

        with client.websocket_connect("/ws/connect-bridge") as connect_ws:
            connect_ws.send_json(_hello("connect", session))
            connect_ws.receive_json()
            connect_ws.receive_json()
            # First Connect emits device_lost.
            connect_ws.send_json({
                "v": 1,
                "type": "device_lost",
                "reason": "reconfig_exhausted_after_5_attempts",
            })
            _drive_server(connect_ws)
            # Drain the relayed frame on the browser side so the
            # channel is "clean" before the late joiner connects.
            relayed = _read_until(
                browser_ws,
                lambda f: f.get("type") == "device_lost",
            )
            assert relayed is not None, "precondition: browser must see it"

        # The original connect peer is gone. A fresh helper joins —
        # this is the "supervisor restarted me, am I caught up?" flow.
        with client.websocket_connect("/ws/connect-bridge") as late_ws:
            late_ws.send_json(_hello("connect", session))
            # Drain hello_ack + joined. If device_lost were being
            # replayed it would arrive *between* these two or right
            # after `joined` — drain enough frames to cover both
            # orderings, asserting none of them is device_lost.
            for _ in range(2):
                frame = late_ws.receive_json()
                assert frame.get("type") != "device_lost", (
                    "device_lost must not be replayed to a late joiner — "
                    "the event reports a *past* engine state that may no "
                    "longer be true on this fresh helper"
                )

            # Probe via ping/pong: if anything else (especially
            # device_lost) were queued behind the handshake, we would
            # see it here instead of pong. Mirrors the assertion shape
            # of ``test_fresh_channel_replays_nothing``.
            late_ws.send_json({"type": "ping"})
            response = late_ws.receive_json()
            assert response == {"type": "pong"}, (
                f"late joiner saw an unexpected frame after handshake "
                f"(expected only pong from probe): {response!r}"
            )


# ---------------------------------------------------------------------------
# 3. Reason slug round-trips unmodified
# ---------------------------------------------------------------------------


def test_device_lost_reason_round_trips_unmodified() -> None:
    """The server treats ``reason`` as opaque; whatever string the
    helper sends must arrive at the browser intact. Belt-and-braces
    against a future refactor that "normalises" or "validates" the
    slug at the bridge — the contract is between Swift (sets) and
    ``jam.js`` (consumes); the server is a relay.
    """
    session = "test_device_lost_reason_passthrough"
    custom_reason = "audio_unit_kSomeUnusualError_neg42"

    with client.websocket_connect("/ws/connect-bridge") as browser_ws:
        browser_ws.send_json(_hello("browser", session))
        browser_ws.receive_json()
        browser_ws.receive_json()

        with client.websocket_connect("/ws/connect-bridge") as connect_ws:
            connect_ws.send_json(_hello("connect", session))
            connect_ws.receive_json()
            connect_ws.receive_json()
            connect_ws.send_json({
                "v": 1,
                "type": "device_lost",
                "reason": custom_reason,
            })
            _drive_server(connect_ws)

            received = _read_until(
                browser_ws,
                lambda f: f.get("type") == "device_lost",
            )
            assert received is not None
            assert received["reason"] == custom_reason
