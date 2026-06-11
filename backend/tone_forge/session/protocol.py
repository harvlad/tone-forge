"""Session Engine wire protocol — v1.

Single source of truth for every WebSocket message that crosses the
Jam UI ↔ Session Engine ↔ Connect boundary. Per EXECUTION_PLAN.md §6,
every frame carries ``{"v": 1, "type": ..., ...}``. Today's connect-bridge
endpoint omits the ``v`` field; new clients send it, the server treats
its absence as v1 (back-compat with the helper builds in the field).

The Swift mirror lives at ``connect/Sources/ConnectCore/Protocol.swift``.
``PROTOCOL_VERSION`` here, ``ConnectProtocol.version`` there, and
``CONNECT_BRIDGE_PROTOCOL_VERSION`` in ``tone_forge_api.py`` must move
together. The boundary test in ``tests/test_subsystem_boundaries.py``
enforces that this module only imports ``contracts`` (and stdlib).

Schemas use ``TypedDict`` so static type checkers can validate handlers
without imposing runtime overhead. ``TypedDict`` is structural — extra
keys are ignored, missing optional keys are fine, missing required keys
are a type error.
"""

from __future__ import annotations

from typing import Any, Literal, Optional, TypedDict


# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------

PROTOCOL_VERSION: int = 1
"""Wire-protocol version this server speaks.

Bump when adding required fields or changing the semantics of any
existing message type. Optional additive fields do not require a bump.
When this moves, also update:

* ``CONNECT_BRIDGE_PROTOCOL_VERSION`` in ``tone_forge_api.py``
* ``ConnectProtocol.version`` in ``connect/Sources/ConnectCore/Protocol.swift``
* ``protocol_version`` literal sent by ``backend/static/jam.js``
"""


# ---------------------------------------------------------------------------
# Message type strings
# ---------------------------------------------------------------------------

class MessageType:
    """Canonical message type strings.

    Grouped by direction in the comments. The class is a plain namespace —
    we do not use a ``str``-Enum because unknown frame types from a
    future server must land in the default branch of a dispatcher, not
    raise a ``ValueError``.
    """

    # --- Framing (both directions) -----------------------------------------
    HELLO = "hello"
    HELLO_ACK = "hello_ack"
    VERSION_MISMATCH = "version_mismatch"
    JOINED = "joined"
    ACK = "ack"
    ERROR = "error"
    PING = "ping"
    PONG = "pong"

    # --- Intent → Engine (from UI) -----------------------------------------
    SET_TRANSPORT = "set_transport"
    SET_LOOP = "set_loop"
    SET_USER_MUTE = "set_user_mute"
    SET_MONITOR_GAIN = "set_monitor_gain"
    APPLY_TONE = "apply_tone"
    APPLY_CHAIN = "apply_chain"

    # --- Legacy aliases ----------------------------------------------------
    # ``set_gain`` is the pre-v1 spelling of ``set_monitor_gain``. The
    # server accepts both during the transition window; jam.js still
    # emits ``set_gain`` until Priority 5e flips it.
    SET_GAIN = "set_gain"
    PRESET_PUSH = "preset_push"

    # --- State → Subscribers (broadcast by Engine) -------------------------
    TRANSPORT_STATE = "transport_state"
    TONE_APPLIED = "tone_applied"

    # --- Events from Connect -----------------------------------------------
    DEVICE_LOST = "device_lost"
    DEVICE_CHANGED = "device_changed"
    LATENCY_REPORT = "latency_report"

    # --- Lifecycle (broadcast by server) -----------------------------------
    # ``peer_left`` is emitted to the survivors when a peer is reaped — by
    # a broadcast send failure today, by the heartbeat probe added in the
    # second-wave Connect hardening pass, or by future reapers. The wire
    # frame carries ``reason`` (a ``PeerLeftReason`` slug) and ``peers``
    # (count of clients still in the channel after the drop).
    PEER_LEFT = "peer_left"


# ---------------------------------------------------------------------------
# Error code taxonomy (per §3F)
# ---------------------------------------------------------------------------

class ErrorCode:
    """Canonical ``error.code`` slugs.

    Every ``{"type": "error", ...}`` frame the server emits MUST carry a
    ``code`` field whose value is one of the slugs declared here. The slug
    is the contract — the ``message`` field is human-readable colour for
    logs and (eventually) Connect's status surface, but downstream code
    (Swift dispatcher, jam.js status banner, future Sentry-grouping rules)
    matches on ``code``.

    Plain namespace, not a ``str``-Enum: a future server may emit a code
    a v1 Connect doesn't recognise; the Swift dispatcher must land that
    in its default branch and surface a generic "unknown error" rather
    than crash on JSON decode. Same rationale as ``MessageType``.

    The drift gate in ``tests/test_connect_error_codes.py`` reads
    ``tone_forge_api.py`` as text and asserts every emitted ``"code":"..."``
    literal is a member of this namespace. Adding a new slug requires
    declaring it here first, which is the entire point.
    """

    # First frame on the WS was not ``hello`` — handshake aborted.
    BAD_HELLO = "bad_hello"

    # ``apply_chain`` frame arrived without a non-empty ``chain_id``.
    CHAIN_ID_MISSING = "chain_id_missing"

    # ``apply_chain`` ``chain_id`` did not resolve to a chain in the
    # bundled monitor bank.
    CHAIN_NOT_FOUND = "chain_not_found"

    # ``apply_chain`` resolved a chain but ``ChainSpec.from_loader``
    # rejected the YAML.
    CHAIN_SPEC_INVALID = "chain_spec_invalid"


class PeerLeftReason:
    """Canonical ``peer_left.reason`` slugs.

    ``peer_left`` is the survivor-notification frame emitted when a peer
    is reaped from a channel. The ``reason`` slug tells the survivor's
    UI why (so it can choose whether to retry, surface an error, or just
    flip back to unpaired silently).
    """

    # ``broadcast()`` tried to send to a peer and the send raised.
    SEND_FAILED = "send_failed"

    # The recv-side heartbeat fired its probe and got no response inside
    # ``CONNECT_BRIDGE_PONG_TIMEOUT_SEC``.
    HEARTBEAT_TIMEOUT = "heartbeat_timeout"


# ---------------------------------------------------------------------------
# Frame envelopes
# ---------------------------------------------------------------------------
#
# All frames carry ``type`` (always required) and ``v`` (optional today;
# absence is treated as PROTOCOL_VERSION by the server, present-but-newer
# triggers ``version_mismatch``). The dataclasses below model the body of
# each frame in addition to those framing fields.


class _Envelope(TypedDict, total=False):
    """Shared framing fields. ``type`` is required on every frame."""

    v: int
    type: str


# --- Framing -----------------------------------------------------------------

class HelloFrame(_Envelope, total=False):
    role: Literal["browser", "connect"]
    session_id: str
    protocol_version: int
    client_kind: str  # informational; e.g. "jam-web", "connect-cli"


class HelloAckFrame(_Envelope, total=False):
    protocol_version: int


class VersionMismatchFrame(_Envelope, total=False):
    required: int
    client: int


class JoinedFrame(_Envelope, total=False):
    peers: int
    session_id: str


class AckFrame(_Envelope, total=False):
    request_id: str


class ErrorFrame(_Envelope, total=False):
    code: str           # taxonomy slug per §3F
    message: str        # human-readable
    retriable: bool


class PingFrame(_Envelope, total=False):
    nonce: str


class PongFrame(_Envelope, total=False):
    nonce: str


# --- Intent → Engine ---------------------------------------------------------

class SetTransportFrame(_Envelope, total=False):
    """UI requests a transport state change.

    ``position_s`` and ``tempo_pct`` are optional — omitting them means
    "keep current". ``playing`` is always present on the wire even if
    it matches the current state, so the reducer can debounce by value.
    """

    playing: bool
    position_s: float
    tempo_pct: float       # 0.5..1.0


class SetLoopFrame(_Envelope, total=False):
    """UI requests loop in/out points. Send ``null`` for either side to clear."""

    loop_in_s: Optional[float]
    loop_out_s: Optional[float]


class SetUserMuteFrame(_Envelope, total=False):
    muted: bool


class SetMonitorGainFrame(_Envelope, total=False):
    gain: float            # 0..1, clamped by the reducer


class ApplyToneFrame(_Envelope, total=False):
    """UI overrides auto-applied match with a specific candidate."""

    candidate_id: str


class ApplyChainFrame(_Envelope, total=False):
    """UI overrides auto-applied tone with a curated monitor chain."""

    chain_id: str


# --- State broadcasts --------------------------------------------------------

class TransportStateFrame(_Envelope, total=False):
    """Canonical transport snapshot broadcast to UI and Connect.

    Mirrors ``contracts.TransportState`` exactly. The reducer emits this
    whenever any intent produces a real state change.
    """

    playing: bool
    position_s: float
    tempo_pct: float
    loop_in_s: Optional[float]
    loop_out_s: Optional[float]
    user_mute: bool
    monitor_gain: float


class ToneAppliedFrame(_Envelope, total=False):
    """Engine reports which tone is now active and why.

    Exactly one of ``candidate_id``/``chain_id`` is populated. ``source``
    explains how we got here so the UI can render the right badge.
    """

    candidate_id: Optional[str]
    chain_id: Optional[str]
    source: Literal["auto", "user", "fallback"]


# --- Connect events ----------------------------------------------------------

class DeviceLostFrame(_Envelope, total=False):
    reason: str


class DeviceChangedFrame(_Envelope, total=False):
    input_device: str
    output_device: str


class LatencyReportFrame(_Envelope, total=False):
    estimated_round_trip_sec: float
    buffer_duration_sec: float


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def envelope(message_type: str, body: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Build a v1-framed outbound frame.

    The server side uses this to keep every emitted frame carrying the
    same ``v`` field; clients (jam.js, ConnectCore) build their own
    envelopes but the shape matches.
    """

    frame: dict[str, Any] = {"v": PROTOCOL_VERSION, "type": message_type}
    if body:
        frame.update(body)
    return frame


def is_supported_version(claimed: Optional[int]) -> bool:
    """Return True if ``claimed`` is a version this server can serve.

    Missing (``None``) and any value ``<= PROTOCOL_VERSION`` are accepted.
    Strictly greater is rejected so the client surfaces an update prompt
    instead of silently downgrading and corrupting state.
    """

    if claimed is None:
        return True
    try:
        return int(claimed) <= PROTOCOL_VERSION
    except (TypeError, ValueError):
        # Garbage version field — treat as v0 (accept). The handshake
        # validator above this catches obviously malformed hellos.
        return True
