"""Tests for Connect bridge wire-protocol v2 (Audio-Ownership Pivot).

Protocol v2 is purely additive over v1: it introduces five new message
types and three new server-side cache slots. Nothing in v1 changes
shape. These tests pin:

1. Version negotiation: a v2 client gets ``hello_ack`` with the
   server's version (``protocol_version: 2``); a v1 client still gets
   ``hello_ack`` with ``protocol_version: 2`` and the server accepts
   its traffic.

2. Pass-through broadcast: ``connect_state``, ``latency_report``,
   ``session_data``, ``transport_state``, ``input_meter`` all reach
   the partner peer with their payload unchanged. (v1 had a generic
   pass-through ``else`` branch that already did this; v2 explicitly
   names them with cache-or-skip logic, so it would be easy to
   regress.)

3. Cache + replay for the three low-rate snapshot types:
   ``connect_state`` → cached → replayed to a late joiner with
   ``replayed: True``; same for ``latency_report`` and
   ``session_data``.

4. **No** caching for high-rate types: ``transport_state`` and
   ``input_meter`` must broadcast but never replay on join.
   Replaying a stale tick would mislead late joiners.

What we don't pin
-----------------

* Specific ordering of replay frames vs. ``hello_ack`` / ``joined``.
  We follow the same "read ahead a few frames and look for the type
  we care about" idiom as ``test_connect_bridge_replay.py``.
* Connect-side emission (cadence, content) of the new frames — that
  is Phase 4 commit B and covered by Swift tests, not pytest.
* Browser-side consumption — covered by the jam.js dispatch in
  Phase 3 (latency comparison card), not here.
"""
from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tone_forge_api import (  # noqa: E402
    CONNECT_BRIDGE_PROTOCOL_VERSION,
    app,
)

client = TestClient(app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hello(role: str, session_id: str, version: int = 2) -> dict:
    return {
        "type": "hello",
        "role": role,
        "session_id": session_id,
        "protocol_version": version,
    }


def _read_until(ws, predicate, max_frames: int = 8) -> dict | None:
    """Skim past hello_ack / joined / unrelated replays until a
    matching frame appears, or give up after max_frames."""
    for _ in range(max_frames):
        frame = ws.receive_json()
        if predicate(frame):
            return frame
    return None


# ---------------------------------------------------------------------------
# Version negotiation
# ---------------------------------------------------------------------------


def test_server_advertises_v2_in_hello_ack() -> None:
    """The server-side version constant is 2 and ``hello_ack`` echoes
    it. Pins the contract that lets jam.js / Connect detect a
    downgraded server."""
    assert CONNECT_BRIDGE_PROTOCOL_VERSION == 2

    session = "test_v2_hello_ack"
    with client.websocket_connect("/ws/connect-bridge") as ws:
        ws.send_json(_hello("browser", session, version=2))
        ack = ws.receive_json()
        assert ack["type"] == "hello_ack"
        assert ack["protocol_version"] == 2


def test_v1_client_still_accepted_by_v2_server() -> None:
    """A v1 client (client_version <= server_version) is accepted; the
    server's hello_ack reports the server's actual version (2), not
    the client's. The v1 client treats unknown trailing fields as
    no-ops, so this is backward-compatible. Pins invariant #1 in the
    Audio-Ownership Pivot plan."""
    session = "test_v2_accepts_v1_client"
    with client.websocket_connect("/ws/connect-bridge") as ws:
        ws.send_json(_hello("connect", session, version=1))
        ack = ws.receive_json()
        assert ack["type"] == "hello_ack"
        assert ack["protocol_version"] == 2
        joined = ws.receive_json()
        assert joined["type"] == "joined"


# ---------------------------------------------------------------------------
# Pass-through broadcast of v2 types
# ---------------------------------------------------------------------------


def _make_pair(session: str):
    """Returns (browser_ws, connect_ws) both joined, drained of
    hello_ack/joined framing on both sides."""
    return session


def test_connect_state_broadcast_browser_receives_partner_frame() -> None:
    """A connect_state frame from one peer reaches the other peer with
    payload intact."""
    session = "test_v2_connect_state_broadcast"
    payload = {
        "type": "connect_state",
        "v": 2,
        "state": "running",
        "device": {
            "input_name": "Line 6 HX Stomp",
            "output_name": "MacBook Pro Speakers",
            "sample_rate": 48000,
            "channels_in": 2,
        },
        "monitor": {"enabled": True, "gain": 0.8, "muted": False},
        "dsp": {"amp_sim_enabled": True, "active_chain_id": "fender_clean"},
    }

    with client.websocket_connect("/ws/connect-bridge") as browser_ws:
        browser_ws.send_json(_hello("browser", session))
        browser_ws.receive_json()  # hello_ack
        browser_ws.receive_json()  # joined

        with client.websocket_connect("/ws/connect-bridge") as connect_ws:
            connect_ws.send_json(_hello("connect", session))
            # Drain ack + joined on connect side.
            _read_until(connect_ws, lambda f: f.get("type") == "joined")

            # Connect emits engine snapshot; browser must see it.
            connect_ws.send_json(payload)
            relayed = _read_until(
                browser_ws,
                lambda f: f.get("type") == "connect_state",
            )
            assert relayed is not None, (
                "Browser peer did not receive connect_state broadcast"
            )
            assert relayed["state"] == "running"
            assert relayed["device"]["sample_rate"] == 48000


def test_latency_report_broadcast_browser_receives_partner_frame() -> None:
    session = "test_v2_latency_report_broadcast"
    payload = {
        "type": "latency_report",
        "v": 2,
        "input_ms": 3.1,
        "output_ms": 4.2,
        "buffer_ms": 5.3,
        "estimated_round_trip_ms": 17.9,
    }

    with client.websocket_connect("/ws/connect-bridge") as browser_ws:
        browser_ws.send_json(_hello("browser", session))
        browser_ws.receive_json()
        browser_ws.receive_json()

        with client.websocket_connect("/ws/connect-bridge") as connect_ws:
            connect_ws.send_json(_hello("connect", session))
            _read_until(connect_ws, lambda f: f.get("type") == "joined")

            connect_ws.send_json(payload)
            relayed = _read_until(
                browser_ws,
                lambda f: f.get("type") == "latency_report",
            )
            assert relayed is not None
            assert relayed["estimated_round_trip_ms"] == 17.9


def test_session_data_broadcast_connect_receives_partner_frame() -> None:
    session = "test_v2_session_data_broadcast"
    payload = {
        "type": "session_data",
        "v": 2,
        "session_id": session,
        "song": {"id": "song-123", "title": "Test Track"},
        "bpm": 120.0,
        "key": {"root": 0, "scale": "Major"},
    }

    with client.websocket_connect("/ws/connect-bridge") as browser_ws:
        browser_ws.send_json(_hello("browser", session))
        browser_ws.receive_json()
        browser_ws.receive_json()

        with client.websocket_connect("/ws/connect-bridge") as connect_ws:
            connect_ws.send_json(_hello("connect", session))
            _read_until(connect_ws, lambda f: f.get("type") == "joined")

            browser_ws.send_json(payload)
            relayed = _read_until(
                connect_ws,
                lambda f: f.get("type") == "session_data",
            )
            assert relayed is not None
            assert relayed["bpm"] == 120.0
            assert relayed["song"]["title"] == "Test Track"


def test_transport_state_broadcast_but_not_cached() -> None:
    """transport_state must broadcast (so the partner peer can react in
    real time) but must NOT be cached for replay (high-rate, stale-
    on-arrival)."""
    session = "test_v2_transport_state_no_cache"
    payload = {
        "type": "transport_state",
        "v": 2,
        "playing": True,
        "position_s": 12.34,
    }

    with client.websocket_connect("/ws/connect-bridge") as browser_ws:
        browser_ws.send_json(_hello("browser", session))
        browser_ws.receive_json()
        browser_ws.receive_json()

        with client.websocket_connect("/ws/connect-bridge") as peer1:
            peer1.send_json(_hello("connect", session))
            _read_until(peer1, lambda f: f.get("type") == "joined")

            # Peer1 should see the broadcast.
            browser_ws.send_json(payload)
            relayed = _read_until(
                peer1,
                lambda f: f.get("type") == "transport_state",
            )
            assert relayed is not None
            assert relayed["position_s"] == 12.34

        # Now a fresh peer joins after the transport_state was already
        # broadcast. It must NOT receive a replay. Use the ping/pong
        # probe idiom (mirroring test_fresh_channel_replays_nothing in
        # test_connect_bridge_replay.py): after draining hello_ack +
        # joined, the very next inbound frame on this socket must be
        # pong. Any other frame (e.g. a transport_state replay) is a
        # bug.
        with client.websocket_connect("/ws/connect-bridge") as peer2:
            peer2.send_json(_hello("connect", session))
            peer2.receive_json()  # hello_ack
            peer2.receive_json()  # joined
            peer2.send_json({"type": "ping"})
            response = peer2.receive_json()
            assert response == {"type": "pong"}, (
                f"transport_state must not be cached for replay: "
                f"unexpected frame {response!r}"
            )


def test_input_meter_broadcast_but_not_cached() -> None:
    """input_meter must broadcast but not be cached. Same rationale as
    transport_state."""
    session = "test_v2_input_meter_no_cache"
    payload = {
        "type": "input_meter",
        "v": 2,
        "peak_dbfs": -12.4,
        "rms_dbfs": -18.7,
    }

    with client.websocket_connect("/ws/connect-bridge") as browser_ws:
        browser_ws.send_json(_hello("browser", session))
        browser_ws.receive_json()
        browser_ws.receive_json()

        with client.websocket_connect("/ws/connect-bridge") as connect_ws:
            connect_ws.send_json(_hello("connect", session))
            _read_until(connect_ws, lambda f: f.get("type") == "joined")
            connect_ws.send_json(payload)
            relayed = _read_until(
                browser_ws,
                lambda f: f.get("type") == "input_meter",
            )
            assert relayed is not None
            assert relayed["peak_dbfs"] == -12.4

        # ping/pong probe — see transport_state test for rationale.
        with client.websocket_connect("/ws/connect-bridge") as peer2:
            peer2.send_json(_hello("browser", session))
            peer2.receive_json()  # hello_ack
            peer2.receive_json()  # joined
            peer2.send_json({"type": "ping"})
            response = peer2.receive_json()
            assert response == {"type": "pong"}, (
                f"input_meter must not be cached for replay: "
                f"unexpected frame {response!r}"
            )


# ---------------------------------------------------------------------------
# Cache + replay for low-rate snapshot types
# ---------------------------------------------------------------------------


def test_connect_state_replayed_to_late_joining_peer() -> None:
    """A peer joining after Connect emitted a connect_state must
    receive that snapshot on join with replayed=True. Without this
    a JAM tab opened after Connect was already running would not
    know the engine state until the next 1Hz tick."""
    session = "test_v2_connect_state_replay"
    payload = {
        "type": "connect_state",
        "v": 2,
        "state": "running",
        "device": {
            "input_name": "Line 6 HX Stomp",
            "output_name": "Speakers",
            "sample_rate": 48000,
            "channels_in": 2,
        },
        "monitor": {"enabled": True, "gain": 1.0, "muted": False},
        "dsp": {"amp_sim_enabled": False, "active_chain_id": None},
    }

    with client.websocket_connect("/ws/connect-bridge") as connect_ws:
        connect_ws.send_json(_hello("connect", session))
        connect_ws.receive_json()
        connect_ws.receive_json()
        connect_ws.send_json(payload)

        with client.websocket_connect("/ws/connect-bridge") as browser_ws:
            browser_ws.send_json(_hello("browser", session))
            replay = _read_until(
                browser_ws,
                lambda f: f.get("type") == "connect_state",
            )
            assert replay is not None, (
                "Late joiner did not receive cached connect_state"
            )
            assert replay.get("replayed") is True
            assert replay.get("state") == "running"
            assert replay.get("device", {}).get("sample_rate") == 48000


def test_latency_report_replayed_to_late_joining_peer() -> None:
    session = "test_v2_latency_report_replay"
    payload = {
        "type": "latency_report",
        "v": 2,
        "input_ms": 3.1,
        "output_ms": 4.2,
        "buffer_ms": 5.3,
        "estimated_round_trip_ms": 17.9,
    }

    with client.websocket_connect("/ws/connect-bridge") as connect_ws:
        connect_ws.send_json(_hello("connect", session))
        connect_ws.receive_json()
        connect_ws.receive_json()
        connect_ws.send_json(payload)

        with client.websocket_connect("/ws/connect-bridge") as browser_ws:
            browser_ws.send_json(_hello("browser", session))
            replay = _read_until(
                browser_ws,
                lambda f: f.get("type") == "latency_report",
            )
            assert replay is not None
            assert replay.get("replayed") is True
            assert replay.get("estimated_round_trip_ms") == 17.9


def test_session_data_replayed_to_late_joining_peer() -> None:
    """A Connect helper that joins after JAM finished analysis must
    receive the cached session metadata so it can drive in-Connect
    overlays without waiting for a re-analysis."""
    session = "test_v2_session_data_replay"
    payload = {
        "type": "session_data",
        "v": 2,
        "session_id": session,
        "song": {"id": "song-456", "title": "Cached Song"},
        "bpm": 96.0,
        "key": {"root": 5, "scale": "Minor"},
    }

    with client.websocket_connect("/ws/connect-bridge") as browser_ws:
        browser_ws.send_json(_hello("browser", session))
        browser_ws.receive_json()
        browser_ws.receive_json()
        browser_ws.send_json(payload)

        with client.websocket_connect("/ws/connect-bridge") as connect_ws:
            connect_ws.send_json(_hello("connect", session))
            replay = _read_until(
                connect_ws,
                lambda f: f.get("type") == "session_data",
            )
            assert replay is not None, (
                "Connect helper did not receive cached session_data"
            )
            assert replay.get("replayed") is True
            assert replay.get("bpm") == 96.0
            assert replay.get("song", {}).get("title") == "Cached Song"


# ---------------------------------------------------------------------------
# measure_latency — LatencyProbe trigger (Audio-Ownership Pivot follow-up)
# ---------------------------------------------------------------------------


def test_measure_latency_broadcast_connect_receives_partner_frame() -> None:
    """A v2 measure_latency frame from the browser reaches the Connect
    peer with payload intact. This is the trigger JAM sends when the
    user clicks the "Measure" button in the audio-status card."""
    session = "test_v2_measure_latency_broadcast"
    payload = {"type": "measure_latency", "v": 2}

    with client.websocket_connect("/ws/connect-bridge") as connect_ws:
        connect_ws.send_json(_hello("connect", session))
        connect_ws.receive_json()  # hello_ack
        connect_ws.receive_json()  # joined

        with client.websocket_connect("/ws/connect-bridge") as browser_ws:
            browser_ws.send_json(_hello("browser", session))
            _read_until(browser_ws, lambda f: f.get("type") == "joined")
            browser_ws.send_json(payload)
            relayed = _read_until(
                connect_ws,
                lambda f: f.get("type") == "measure_latency",
            )
            assert relayed is not None, (
                "Connect peer did not receive measure_latency broadcast"
            )
            assert relayed.get("v") == 2


def test_measure_latency_not_cached_for_replay() -> None:
    """measure_latency is a one-shot user-initiated trigger. Replaying
    it to a late-joining Connect would re-fire the audible impulse
    probe, which is obnoxious. The server must broadcast it once and
    forget."""
    session = "test_v2_measure_latency_no_cache"
    payload = {"type": "measure_latency", "v": 2}

    with client.websocket_connect("/ws/connect-bridge") as connect1:
        connect1.send_json(_hello("connect", session))
        connect1.receive_json()
        connect1.receive_json()

        with client.websocket_connect("/ws/connect-bridge") as browser_ws:
            browser_ws.send_json(_hello("browser", session))
            _read_until(browser_ws, lambda f: f.get("type") == "joined")
            browser_ws.send_json(payload)
            # Drain so the trigger is processed by the server.
            _read_until(connect1, lambda f: f.get("type") == "measure_latency")

        # Fresh Connect peer joins after the trigger fired. It must
        # NOT see a replay of measure_latency. Same ping/pong probe
        # idiom as transport_state.
        with client.websocket_connect("/ws/connect-bridge") as connect2:
            connect2.send_json(_hello("connect", session))
            connect2.receive_json()  # hello_ack
            connect2.receive_json()  # joined
            connect2.send_json({"type": "ping"})
            response = connect2.receive_json()
            assert response == {"type": "pong"}, (
                "measure_latency must not be cached for replay; "
                f"unexpected frame {response!r}"
            )


def test_latency_report_with_measured_fields_round_trips() -> None:
    """The server must pass measured_round_trip_ms + measurement_confidence
    through untouched. They're the LatencyProbe result the Connect
    helper attached after the user clicked Measure."""
    session = "test_v2_latency_report_measured"
    payload = {
        "type": "latency_report",
        "v": 2,
        "input_ms": 3.1,
        "output_ms": 4.2,
        "buffer_ms": 5.3,
        "estimated_round_trip_ms": 17.9,
        "measured_round_trip_ms": 12.4,
        "measurement_confidence": "high",
    }

    with client.websocket_connect("/ws/connect-bridge") as browser_ws:
        browser_ws.send_json(_hello("browser", session))
        browser_ws.receive_json()
        browser_ws.receive_json()

        with client.websocket_connect("/ws/connect-bridge") as connect_ws:
            connect_ws.send_json(_hello("connect", session))
            _read_until(connect_ws, lambda f: f.get("type") == "joined")
            connect_ws.send_json(payload)
            relayed = _read_until(
                browser_ws,
                lambda f: f.get("type") == "latency_report",
            )
            assert relayed is not None
            assert relayed["measured_round_trip_ms"] == 12.4
            assert relayed["measurement_confidence"] == "high"


def test_latency_report_with_measured_fields_replayed_to_late_joiner() -> None:
    """A late-joining JAM must see the cached measurement, not just the
    floor estimate. The cache replays whatever was last sent — the
    measurement persists alongside the estimate."""
    session = "test_v2_latency_report_measured_replay"
    payload = {
        "type": "latency_report",
        "v": 2,
        "input_ms": 3.1,
        "output_ms": 4.2,
        "buffer_ms": 5.3,
        "estimated_round_trip_ms": 17.9,
        "measured_round_trip_ms": 11.2,
        "measurement_confidence": "high",
    }

    with client.websocket_connect("/ws/connect-bridge") as connect_ws:
        connect_ws.send_json(_hello("connect", session))
        connect_ws.receive_json()
        connect_ws.receive_json()
        connect_ws.send_json(payload)

        with client.websocket_connect("/ws/connect-bridge") as browser_ws:
            browser_ws.send_json(_hello("browser", session))
            replay = _read_until(
                browser_ws,
                lambda f: f.get("type") == "latency_report",
            )
            assert replay is not None
            assert replay.get("replayed") is True
            assert replay.get("measured_round_trip_ms") == 11.2
            assert replay.get("measurement_confidence") == "high"


# ---------------------------------------------------------------------------
# load_stems — stem-playback handoff (Audio-Ownership Pivot follow-up)
# ---------------------------------------------------------------------------


def test_load_stems_broadcast_connect_receives_partner_frame() -> None:
    """A v2 ``load_stems`` frame from the browser reaches the Connect
    peer with the stem list intact, so the helper can download each
    URL and attach via AudioEngine.loadStem."""
    session = "test_v2_load_stems_broadcast"
    payload = {
        "type": "load_stems",
        "v": 2,
        "stems": [
            {
                "id": "demucs.drums",
                "url": "http://127.0.0.1:7777/api/serve-file?path=/tmp/song_drums.wav",
                "display_name": "Drums",
            },
            {
                "id": "demucs.bass",
                "url": "http://127.0.0.1:7777/api/serve-file?path=/tmp/song_bass.wav",
                "display_name": "Bass",
            },
        ],
    }

    with client.websocket_connect("/ws/connect-bridge") as connect_ws:
        connect_ws.send_json(_hello("connect", session))
        connect_ws.receive_json()
        connect_ws.receive_json()

        with client.websocket_connect("/ws/connect-bridge") as browser_ws:
            browser_ws.send_json(_hello("browser", session))
            browser_ws.receive_json()
            browser_ws.receive_json()
            browser_ws.send_json(payload)

            received = _read_until(
                connect_ws,
                lambda f: f.get("type") == "load_stems",
            )
            assert received is not None
            assert received.get("v") == 2
            stems = received.get("stems") or []
            assert len(stems) == 2
            assert stems[0]["id"] == "demucs.drums"
            assert stems[0]["url"].endswith("song_drums.wav")
            assert stems[1]["display_name"] == "Bass"


def test_load_stems_replayed_to_late_joining_connect() -> None:
    """A Connect helper that joins after the browser pushed
    ``load_stems`` must receive the cached frame so it can take over
    playback without forcing the user to re-analyze the song."""
    session = "test_v2_load_stems_replay"
    payload = {
        "type": "load_stems",
        "v": 2,
        "stems": [
            {
                "id": "demucs.other",
                "url": "http://127.0.0.1:7777/api/serve-file?path=/tmp/song_other.wav",
                "display_name": "Other",
            }
        ],
    }

    with client.websocket_connect("/ws/connect-bridge") as browser_ws:
        browser_ws.send_json(_hello("browser", session))
        browser_ws.receive_json()
        browser_ws.receive_json()
        browser_ws.send_json(payload)

        with client.websocket_connect("/ws/connect-bridge") as connect_ws:
            connect_ws.send_json(_hello("connect", session))
            replay = _read_until(
                connect_ws,
                lambda f: f.get("type") == "load_stems",
            )
            assert replay is not None, (
                "Connect helper did not receive cached load_stems"
            )
            assert replay.get("replayed") is True
            stems = replay.get("stems") or []
            assert len(stems) == 1
            assert stems[0]["id"] == "demucs.other"
