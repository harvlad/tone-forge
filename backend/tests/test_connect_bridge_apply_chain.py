"""Tests for the ``apply_chain`` branch of the connect-bridge WS handler.

The server validates the inbound ``chain_id`` against the bundled monitor
bank, then either:

  * broadcasts the resolved spec to the other peer (Connect) and ACKs the
    sender, or
  * sends an ``error`` frame back to the sender and emits no broadcast.

These tests pin both branches and the replay-on-reconnect behaviour. They
double as the integration contract between the monitor loader (Priority 3a)
and the connect-bridge router (Priority 3b): if either side renames a
chain id, this file fails CI before a release ships.
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


def _drain_handshake(ws) -> None:
    """Read hello_ack + joined so subsequent recv_json sees real traffic."""
    ws.receive_json()  # hello_ack
    ws.receive_json()  # joined


def _drive_server(ws) -> None:
    """Force the TestClient portal to process queued frames on ``ws``.

    starlette's TestClient bridges sync test code to the async server via a
    single blocking portal. ``send_json`` enqueues a frame but does not
    drive the server's event loop; the loop only advances when a
    ``receive_*`` on the same socket is awaiting a server-produced frame.

    When a test sends a fire-and-forget message (no ``request_id`` → no
    ack to read) and then tries to read on a *different* socket for the
    forwarded broadcast, the server never gets a chance to run the
    handler that would produce that broadcast, so the read hangs.

    Sending ``ping`` and consuming ``pong`` on the sender's socket forces
    the server to drain its inbound queue (the prior no-rid frame first,
    then the ping), which produces the side effects the cross-socket
    read is waiting on.
    """
    ws.send_json({"type": "ping"})
    pong = ws.receive_json()
    assert pong.get("type") == "pong"


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_apply_chain_broadcasts_resolved_spec_to_peer() -> None:
    """Browser sends ``apply_chain`` with a valid id; the Connect peer
    must receive the resolved spec (id + family + parameters), not just
    the bare id, so it doesn't need a YAML parser on the Swift side."""
    session = "test_apply_chain_broadcast"
    with client.websocket_connect("/ws/connect-bridge") as connect_ws:
        connect_ws.send_json(_hello("connect", session))
        _drain_handshake(connect_ws)

        with client.websocket_connect("/ws/connect-bridge") as browser_ws:
            browser_ws.send_json(_hello("browser", session))
            _drain_handshake(browser_ws)

            browser_ws.send_json({
                "type": "apply_chain",
                "chain_id": "tfc.classic_rock",
                "request_id": "req-1",
            })

            # Sender gets an ACK; never sees its own broadcast.
            ack = browser_ws.receive_json()
            assert ack == {"type": "ack", "request_id": "req-1"}

            # Connect peer receives the resolved spec.
            forwarded = connect_ws.receive_json()
            assert forwarded["type"] == "apply_chain"
            assert forwarded["chain_id"] == "tfc.classic_rock"
            assert forwarded["chain"]["id"] == "tfc.classic_rock"
            assert forwarded["chain"]["family"] == "classic_rock"
            assert "parameters" in forwarded["chain"]
            # The placeholder banks always carry every required section.
            assert set(forwarded["chain"]["parameters"].keys()) >= {
                "input", "gain_stage", "eq", "comp", "reverb", "output",
            }


def test_apply_chain_ack_omitted_when_no_request_id() -> None:
    """Senders may omit ``request_id`` (fire-and-forget); the server then
    skips the ACK but still broadcasts to peers."""
    session = "test_apply_chain_no_rid"
    with client.websocket_connect("/ws/connect-bridge") as connect_ws:
        connect_ws.send_json(_hello("connect", session))
        _drain_handshake(connect_ws)

        with client.websocket_connect("/ws/connect-bridge") as browser_ws:
            browser_ws.send_json(_hello("browser", session))
            _drain_handshake(browser_ws)

            browser_ws.send_json({
                "type": "apply_chain",
                "chain_id": "tfc.ambient",
            })
            # No request_id → no ack. Drive the server to flush the
            # apply_chain handler so the broadcast reaches connect_ws.
            _drive_server(browser_ws)

            forwarded = connect_ws.receive_json()
            assert forwarded["type"] == "apply_chain"
            assert forwarded["chain_id"] == "tfc.ambient"


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_apply_chain_missing_id_returns_error_frame() -> None:
    session = "test_apply_chain_missing_id"
    with client.websocket_connect("/ws/connect-bridge") as ws:
        ws.send_json(_hello("browser", session))
        _drain_handshake(ws)

        ws.send_json({"type": "apply_chain"})
        err = ws.receive_json()
        assert err["type"] == "error"
        assert err["code"] == "chain_id_missing"
        assert err["retriable"] is False


def test_apply_chain_empty_id_returns_error_frame() -> None:
    session = "test_apply_chain_empty_id"
    with client.websocket_connect("/ws/connect-bridge") as ws:
        ws.send_json(_hello("browser", session))
        _drain_handshake(ws)

        ws.send_json({"type": "apply_chain", "chain_id": ""})
        err = ws.receive_json()
        assert err["type"] == "error"
        assert err["code"] == "chain_id_missing"


def test_apply_chain_unknown_id_returns_not_found() -> None:
    """A chain id the loader can't resolve must surface as an explicit
    error frame back to the sender — no silent passthrough to Connect.
    """
    session = "test_apply_chain_unknown_id"
    with client.websocket_connect("/ws/connect-bridge") as connect_ws:
        connect_ws.send_json(_hello("connect", session))
        _drain_handshake(connect_ws)

        with client.websocket_connect("/ws/connect-bridge") as browser_ws:
            browser_ws.send_json(_hello("browser", session))
            _drain_handshake(browser_ws)

            browser_ws.send_json({
                "type": "apply_chain",
                "chain_id": "tfc.does_not_exist",
                "request_id": "req-x",
            })

            err = browser_ws.receive_json()
            assert err["type"] == "error"
            assert err["code"] == "chain_not_found"
            assert "tfc.does_not_exist" in err["message"]

            # Send a follow-up apply_chain that succeeds so we can prove
            # Connect saw exactly one broadcast (no orphan from the bad
            # request). receive_json on a stream of one frame returns
            # that frame.
            browser_ws.send_json({
                "type": "apply_chain",
                "chain_id": "tfc.clean_strat",
            })
            # No request_id on the follow-up → drive the portal so the
            # successful broadcast reaches connect_ws.
            _drive_server(browser_ws)
            forwarded = connect_ws.receive_json()
            assert forwarded["type"] == "apply_chain"
            assert forwarded["chain_id"] == "tfc.clean_strat"


# ---------------------------------------------------------------------------
# Replay on reconnect
# ---------------------------------------------------------------------------


def test_last_chain_replayed_to_late_joining_peer() -> None:
    """Connect helper crashed and reconnected — it must see the cached
    chain so it can rebuild its graph without the user re-clicking the
    chain in the UI.
    """
    session = "test_apply_chain_replay"

    # Step 1: browser sends apply_chain with no Connect peer yet.
    with client.websocket_connect("/ws/connect-bridge") as browser_ws:
        browser_ws.send_json(_hello("browser", session))
        _drain_handshake(browser_ws)
        browser_ws.send_json({
            "type": "apply_chain",
            "chain_id": "tfc.edge_of_breakup",
            "request_id": "req-replay",
        })
        ack = browser_ws.receive_json()
        assert ack["type"] == "ack"

        # Step 2: Connect joins the channel after the chain was cached.
        with client.websocket_connect("/ws/connect-bridge") as connect_ws:
            connect_ws.send_json(_hello("connect", session))
            connect_ws.receive_json()  # hello_ack

            # Replay frames arrive between hello_ack and joined.
            # Read until we see the apply_chain frame; tolerate
            # interleaving with the joined ack.
            saw_apply_chain = False
            for _ in range(4):
                frame = connect_ws.receive_json()
                if frame.get("type") == "apply_chain":
                    assert frame["chain_id"] == "tfc.edge_of_breakup"
                    assert frame.get("replayed") is True
                    assert frame["chain"]["family"] == "edge_of_breakup"
                    saw_apply_chain = True
                    break
            assert saw_apply_chain, "Reconnecting peer did not receive cached chain"
