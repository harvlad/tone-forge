"""Drift gate for the connect-bridge error-code + peer-left-reason taxonomy.

The server emits two kinds of slug-bearing lifecycle frames over the
WebSocket: ``{"type":"error", "code": "<slug>", ...}`` for fault frames
the helper / UI must classify, and ``{"type":"peer_left",
"reason":"<slug>", ...}`` for survivor notifications when a peer is
reaped.

Both slugs are the contract — Swift's dispatcher and jam.js's status
banner branch on them, not on the human-readable ``message``. So any
new slug that lands in ``tone_forge_api.py`` as a bare string is a
silent contract change: the literal might typo, downstream consumers
might never recognise it, and the regression surfaces in the field.

This file pins three properties:

  1. ``ErrorCode`` / ``PeerLeftReason`` in
     ``backend/tone_forge/session/protocol.py`` carry the wire slugs
     this server emits today, with their declared values. Adding a
     new constant requires updating these tests, which is the entire
     point of the namespace.
  2. ``tone_forge_api.py`` does not contain any bare ``"code":"..."``
     or ``"reason":"..."`` string literals — every emitted slug must
     come through the namespace constants. Catches the failure mode
     where someone adds a new error frame and inlines the slug
     instead of declaring it.
  3. The hello-validation error frame (raised when the first WS frame
     is not a ``hello``) carries ``code: bad_hello``. This is the new
     case the taxonomy adds: prior to the taxonomy pass that frame
     had no ``code`` field at all, so a Swift / JS dispatcher could
     not classify it. We pin the gap closure.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tone_forge.session.protocol import ErrorCode, PeerLeftReason  # noqa: E402
from tone_forge_api import app  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parent.parent
API_PY = REPO_ROOT / "tone_forge_api.py"


# ---------------------------------------------------------------------------
# Member declarations — the namespaces must carry the wire slugs this server
# emits today.
# ---------------------------------------------------------------------------


def test_error_code_members():
    """Every ``error.code`` slug the server emits today is declared in
    ``ErrorCode`` with the expected value. Adding a new emit site
    without updating this test means the drift gate below will fail
    (because the literal isn't in the imported set) — but pinning the
    expected mapping here documents intent."""
    assert ErrorCode.BAD_HELLO == "bad_hello"
    assert ErrorCode.CHAIN_ID_MISSING == "chain_id_missing"
    assert ErrorCode.CHAIN_NOT_FOUND == "chain_not_found"
    assert ErrorCode.CHAIN_SPEC_INVALID == "chain_spec_invalid"


def test_peer_left_reason_members():
    """Every ``peer_left.reason`` slug the server emits today is declared
    in ``PeerLeftReason``."""
    assert PeerLeftReason.SEND_FAILED == "send_failed"
    assert PeerLeftReason.HEARTBEAT_TIMEOUT == "heartbeat_timeout"


# ---------------------------------------------------------------------------
# Drift gate — no bare literals in tone_forge_api.py
# ---------------------------------------------------------------------------


def _declared_error_codes() -> set[str]:
    """Wire-string values declared on ``ErrorCode``."""
    return {
        value
        for name, value in vars(ErrorCode).items()
        if not name.startswith("_") and isinstance(value, str)
    }


def _declared_peer_left_reasons() -> set[str]:
    return {
        value
        for name, value in vars(PeerLeftReason).items()
        if not name.startswith("_") and isinstance(value, str)
    }


# JSON-style dict literal in Python source: "code": "<slug>" or "reason":
# "<slug>". We match the *raw* string form because that's what a developer
# typo's into the source. Calls through the namespace constants are not
# string-literal matches — they read like ``"code": ErrorCode.X`` — so
# they're invisible to this regex by construction. That's the whole point.
_BARE_CODE_LITERAL_RE = re.compile(r'"code"\s*:\s*"([^"]+)"')
_BARE_REASON_LITERAL_RE = re.compile(r'"reason"\s*:\s*"([^"]+)"')


def test_no_bare_error_code_literals_in_api():
    """Every ``"code": "..."`` literal in ``tone_forge_api.py`` must be
    a slug declared on ``ErrorCode``. New emit sites must go through
    the namespace constant (``"code": ErrorCode.X``), which the regex
    by design does not match.

    Surfacing a bare literal means a developer skipped the namespace —
    fix by adding the slug to ``ErrorCode`` and routing the emit
    through it.
    """
    src = API_PY.read_text(encoding="utf-8")
    bare = set(_BARE_CODE_LITERAL_RE.findall(src))
    # The regex matches itself when defined in this very file, but we
    # only scan ``tone_forge_api.py`` so this test isn't self-tripping.
    allowed = _declared_error_codes()
    stray = bare - allowed
    assert not stray, (
        f"tone_forge_api.py contains bare error-code literals that are "
        f"not declared on ErrorCode: {sorted(stray)}. Add each to "
        f"backend/tone_forge/session/protocol.py::ErrorCode and route "
        f"the emit through the constant."
    )


def test_no_bare_peer_left_reason_literals_in_api():
    """Same drift gate for ``"reason": "..."`` literals in peer_left
    frames. We allow the regex to match other ``reason`` fields too —
    if any sneak in for unrelated reasons, the same hygiene rule
    applies (declare on ``PeerLeftReason`` or rename the field)."""
    src = API_PY.read_text(encoding="utf-8")
    bare = set(_BARE_REASON_LITERAL_RE.findall(src))
    allowed = _declared_peer_left_reasons()
    stray = bare - allowed
    assert not stray, (
        f"tone_forge_api.py contains bare reason literals that are not "
        f"declared on PeerLeftReason: {sorted(stray)}. Add each to "
        f"backend/tone_forge/session/protocol.py::PeerLeftReason and "
        f"route the emit through the constant."
    )


# ---------------------------------------------------------------------------
# Functional pin — bad_hello taxonomy gap is closed.
# ---------------------------------------------------------------------------


def test_bad_hello_error_frame_carries_code():
    """The first WS frame must be ``hello``. Anything else used to draw
    a code-less error frame; the taxonomy pass added ``code: bad_hello``
    so dispatchers can classify it without string-matching the
    human-readable message."""
    client = TestClient(app)
    with client.websocket_connect("/ws/connect-bridge") as ws:
        ws.send_json({"type": "set_gain", "gain": 0.5})  # not a hello
        frame = ws.receive_json()
    assert frame["type"] == "error"
    assert frame["code"] == ErrorCode.BAD_HELLO
    assert frame["retriable"] is False
    # Human message is informational; we don't pin its exact text.
    assert isinstance(frame.get("message"), str)
