"""Cross-language parity gate for the connect-bridge wire protocol.

The protocol has three declarations that must stay in lockstep:

  1. ``backend/tone_forge/session/protocol.py`` — Python session-engine
     module: ``PROTOCOL_VERSION`` + ``MessageType`` class.
  2. ``backend/tone_forge_api.py`` — API edge: ``CONNECT_BRIDGE_PROTOCOL_VERSION``.
  3. ``connect/Sources/ConnectCore/Protocol.swift`` — Swift Connect
     helper: ``ConnectProtocol.version`` + ``ConnectProtocol.MessageType``.

Each side owns its own constant on purpose so neither subsystem has to
import the other. The drift risk is exactly that ownership decision —
nothing forces a developer who bumps the Python side to also bump the
Swift side. Without this test the divergence is silent until a
real client trips it in the field.

This file is schema-only: it parses the Swift source as text (no Xcode
toolchain involvement), parses the Python API edge similarly to avoid
importing the FastAPI app for a single constant, and asserts the three
versions agree and that every message-type string Swift declares is
also declared on the Python side (with the same value). The reverse is
not enforced — Python is allowed to be ahead of Swift for
session-engine frames Swift doesn't need to speak yet (transport,
device events, etc.).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from tone_forge.session.protocol import MessageType, PROTOCOL_VERSION

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SWIFT_PROTOCOL = REPO_ROOT / "connect" / "Sources" / "ConnectCore" / "Protocol.swift"
PYTHON_API = REPO_ROOT / "backend" / "tone_forge_api.py"


# ---------------------------------------------------------------------------
# Swift-side extraction
# ---------------------------------------------------------------------------


_SWIFT_VERSION_RE = re.compile(
    r"public\s+static\s+let\s+version\s*:\s*Int\s*=\s*(\d+)"
)
# Matches: public static let helloAck = "hello_ack"
_SWIFT_MSG_RE = re.compile(
    r'public\s+static\s+let\s+(\w+)\s*=\s*"([^"]+)"'
)


def _read_swift_protocol() -> str:
    assert SWIFT_PROTOCOL.exists(), (
        f"Swift protocol source missing at {SWIFT_PROTOCOL}. Either the "
        f"connect/ tree was removed (in which case this parity gate is "
        f"obsolete and should be deleted) or the file moved."
    )
    return SWIFT_PROTOCOL.read_text(encoding="utf-8")


def _parse_swift_version(src: str) -> int:
    m = _SWIFT_VERSION_RE.search(src)
    assert m, "could not find `public static let version: Int = N` in Protocol.swift"
    return int(m.group(1))


def _parse_swift_message_types(src: str) -> dict[str, str]:
    """Return {swift_camelCase_name: wire_string} for every declared type.

    Filters out the ``version`` declaration (an Int, not a String) by
    construction since the regex requires a quoted string RHS.
    """
    return {m.group(1): m.group(2) for m in _SWIFT_MSG_RE.finditer(src)}


# ---------------------------------------------------------------------------
# Python API edge extraction (avoid importing the FastAPI app)
# ---------------------------------------------------------------------------


_API_VERSION_RE = re.compile(
    r"^CONNECT_BRIDGE_PROTOCOL_VERSION\s*=\s*(\d+)", re.MULTILINE
)


def _parse_api_edge_version() -> int:
    src = PYTHON_API.read_text(encoding="utf-8")
    m = _API_VERSION_RE.search(src)
    assert m, (
        "could not find `CONNECT_BRIDGE_PROTOCOL_VERSION = N` at the "
        "top level of tone_forge_api.py — did the constant move?"
    )
    return int(m.group(1))


def _python_message_type_strings() -> set[str]:
    """Wire strings declared in ``MessageType``.

    Skips dunder attributes and any non-string values (none expected,
    but defensive).
    """
    return {
        value
        for name, value in vars(MessageType).items()
        if not name.startswith("_") and isinstance(value, str)
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_protocol_version_parity_python_session_module_and_api_edge():
    """``PROTOCOL_VERSION`` (session module) == ``CONNECT_BRIDGE_PROTOCOL_VERSION``
    (api edge). Both Python-side; should be trivially equal."""
    assert PROTOCOL_VERSION == _parse_api_edge_version(), (
        f"Python protocol version drift: session/protocol.py declares "
        f"{PROTOCOL_VERSION}, tone_forge_api.py declares "
        f"{_parse_api_edge_version()}. Bump them together."
    )


def test_protocol_version_parity_python_and_swift():
    """``ConnectProtocol.version`` (Swift) == ``PROTOCOL_VERSION`` (Python)."""
    swift_src = _read_swift_protocol()
    swift_version = _parse_swift_version(swift_src)
    assert swift_version == PROTOCOL_VERSION, (
        f"Cross-language protocol version drift: Swift "
        f"ConnectProtocol.version = {swift_version}, Python "
        f"PROTOCOL_VERSION = {PROTOCOL_VERSION}. Both clients negotiate "
        f"against the server's version on hello; a mismatch will lock "
        f"older helpers out of newer servers (or vice-versa)."
    )


def test_message_type_strings_swift_subset_of_python():
    """Every wire string Swift declares must exist in Python with the same
    value. Python is permitted to declare additional strings for messages
    Swift doesn't speak yet (session engine intents, transport state,
    Connect-emitted device events, etc.). The asymmetry is intentional:
    Python evolves the protocol; Swift catches up when a new frame
    actually needs to cross the Connect boundary."""
    swift_src = _read_swift_protocol()
    swift_types = _parse_swift_message_types(swift_src)
    assert swift_types, (
        "Protocol.swift parsed but yielded no message-type strings — "
        "did the declaration style change away from "
        "`public static let foo = \"...\"`?"
    )

    python_values = _python_message_type_strings()
    missing = {
        f"{swift_name} = {wire!r}"
        for swift_name, wire in swift_types.items()
        if wire not in python_values
    }
    assert not missing, (
        f"Swift declares wire-protocol message types Python does not: "
        f"{sorted(missing)}. Add the corresponding constant to "
        f"backend/tone_forge/session/protocol.py::MessageType (and "
        f"register a handler in tone_forge_api.py if appropriate) — "
        f"otherwise the server silently drops the frame on the default "
        f"branch and Connect appears to misbehave."
    )


def test_swift_message_type_names_use_known_wire_strings():
    """Belt-and-braces: every Swift declaration should follow the
    ``camelCase Swift name → snake_case wire string`` convention used
    today (``helloAck`` → ``"hello_ack"``, ``versionMismatch`` →
    ``"version_mismatch"``, etc.). A new Swift constant with a wire
    string that doesn't obey the convention is almost certainly a
    typo. This is a soft hint, not a hard contract — fail noisily so
    the author either fixes the typo or documents the exception."""
    swift_src = _read_swift_protocol()
    swift_types = _parse_swift_message_types(swift_src)

    def _camel_to_snake(name: str) -> str:
        # helloAck → hello_ack; versionMismatch → version_mismatch.
        return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()

    suspicious = {
        swift_name: wire
        for swift_name, wire in swift_types.items()
        if _camel_to_snake(swift_name) != wire
    }
    assert not suspicious, (
        f"Swift message-type names don't match their wire strings under "
        f"camelCase → snake_case: {suspicious}. If this is intentional "
        f"(legacy alias, etc.) document it here and relax this assertion."
    )


@pytest.mark.parametrize(
    "swift_name,wire_string",
    [
        ("hello", "hello"),
        ("helloAck", "hello_ack"),
        ("versionMismatch", "version_mismatch"),
        ("joined", "joined"),
        ("ping", "ping"),
        ("pong", "pong"),
        ("ack", "ack"),
        ("error", "error"),
        ("applyChain", "apply_chain"),
        ("setGain", "set_gain"),
        ("presetPush", "preset_push"),
    ],
)
def test_swift_declares_known_message_type(swift_name, wire_string):
    """Anchor test: each of the message types the helper actually speaks
    today must be present in Protocol.swift. If someone deletes
    ``setGain`` thinking it's unused, this fails before the helper
    rolls out a build that can't talk to the legacy server path."""
    swift_src = _read_swift_protocol()
    swift_types = _parse_swift_message_types(swift_src)
    assert swift_name in swift_types, (
        f"Swift Protocol.swift is missing `{swift_name}` — the helper "
        f"will no longer recognise wire string {wire_string!r}."
    )
    assert swift_types[swift_name] == wire_string, (
        f"Swift declared {swift_name} = {swift_types[swift_name]!r}; "
        f"expected {wire_string!r}."
    )
