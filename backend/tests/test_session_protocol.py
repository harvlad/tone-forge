"""Pins the v1 wire protocol surface.

Mirrors ``connect/Tests/ConnectCoreTests/ProtocolTests.swift``. Any
divergence between the two suites is a bug: both sides MUST move
together when ``PROTOCOL_VERSION`` changes.
"""

from __future__ import annotations

import pytest

from tone_forge.session import protocol as P
from tone_forge.session.protocol import MessageType


def test_protocol_version_is_pinned() -> None:
    """If this fires, also bump ConnectProtocol.version (Swift) and
    CONNECT_BRIDGE_PROTOCOL_VERSION in tone_forge_api.py.

    Bumped 1->2 by the Audio-Ownership Pivot (Phase 4): added the
    additive v2 frame types (session_data, transport_state,
    connect_state, latency_report, input_meter, measure_latency,
    load_stems). v1 helpers still negotiate via hello_ack's
    back-compat path.
    """

    assert P.PROTOCOL_VERSION == 2


def test_message_type_strings_are_stable() -> None:
    """Every wire string. The literal values are the public contract;
    renaming any of these is a breaking protocol change."""

    # Framing
    assert MessageType.HELLO == "hello"
    assert MessageType.HELLO_ACK == "hello_ack"
    assert MessageType.VERSION_MISMATCH == "version_mismatch"
    assert MessageType.JOINED == "joined"
    assert MessageType.ACK == "ack"
    assert MessageType.ERROR == "error"
    assert MessageType.PING == "ping"
    assert MessageType.PONG == "pong"

    # Intent → Engine
    assert MessageType.SET_TRANSPORT == "set_transport"
    assert MessageType.SET_LOOP == "set_loop"
    assert MessageType.SET_USER_MUTE == "set_user_mute"
    assert MessageType.SET_MONITOR_GAIN == "set_monitor_gain"
    assert MessageType.APPLY_TONE == "apply_tone"
    assert MessageType.APPLY_CHAIN == "apply_chain"

    # Legacy aliases
    assert MessageType.SET_GAIN == "set_gain"
    assert MessageType.PRESET_PUSH == "preset_push"

    # State broadcasts
    assert MessageType.TRANSPORT_STATE == "transport_state"
    assert MessageType.TONE_APPLIED == "tone_applied"

    # Connect events
    assert MessageType.DEVICE_LOST == "device_lost"
    assert MessageType.DEVICE_CHANGED == "device_changed"
    assert MessageType.LATENCY_REPORT == "latency_report"


def test_envelope_carries_version_and_type() -> None:
    frame = P.envelope(MessageType.HELLO_ACK, {"protocol_version": 1})
    assert frame["v"] == P.PROTOCOL_VERSION
    assert frame["type"] == "hello_ack"
    assert frame["protocol_version"] == 1


def test_envelope_with_empty_body_still_frames() -> None:
    frame = P.envelope(MessageType.PING)
    assert frame == {"v": P.PROTOCOL_VERSION, "type": "ping"}


@pytest.mark.parametrize(
    "claimed,expected",
    [
        (None, True),    # missing version field → accept (v0 / pre-versioning)
        (0, True),       # explicit v0
        (1, True),       # legacy helper; server back-compat accepts v1
        (2, True),       # current (post-Phase-4 bump)
        (3, False),      # future client; server must reject
        (99, False),     # way-future client; server must reject
        ("bogus", True), # garbage → treated as missing rather than rejection
    ],
)
def test_is_supported_version_decides_correctly(
    claimed: object, expected: bool
) -> None:
    assert P.is_supported_version(claimed) is expected  # type: ignore[arg-type]
