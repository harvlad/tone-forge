"""TransportState reducer behavior.

Pure-function tests: feed in (state, frame), assert (new_state, identity).
The reducer is the canonical authority for transport state; everything
else is either UI optimism or a downstream broadcast consumer, so the
range/edge handling here matters disproportionately.
"""

from __future__ import annotations

import pytest

from tone_forge.contracts import TransportState
from tone_forge.session.protocol import MessageType
from tone_forge.session.transport import initial_state, reduce


# ---------------------------------------------------------------------------
# Initial state
# ---------------------------------------------------------------------------

def test_initial_state_is_safe_defaults() -> None:
    """Cold start: stopped, muted, no loop, gain at zero. First launch
    must never produce sound until the user un-mutes."""
    s = initial_state()
    assert s.playing is False
    assert s.position_s == 0.0
    assert s.tempo_pct == 1.0
    assert s.loop_in_s is None
    assert s.loop_out_s is None
    assert s.user_mute is True
    assert s.monitor_gain == 0.0


# ---------------------------------------------------------------------------
# Unknown / malformed frames
# ---------------------------------------------------------------------------

def test_unknown_type_returns_same_instance() -> None:
    s = initial_state()
    out = reduce(s, {"type": "definitely_not_v1"})
    assert out is s  # identity, not just equality — caller debounces on this


def test_missing_type_returns_same_instance() -> None:
    s = initial_state()
    assert reduce(s, {"playing": True}) is s


def test_non_string_type_returns_same_instance() -> None:
    s = initial_state()
    assert reduce(s, {"type": 7}) is s


# ---------------------------------------------------------------------------
# SET_TRANSPORT
# ---------------------------------------------------------------------------

def test_set_transport_play_from_stop_flips_playing() -> None:
    s = initial_state()
    out = reduce(s, {"type": MessageType.SET_TRANSPORT, "playing": True})
    assert out.playing is True
    assert out.position_s == s.position_s
    assert out.tempo_pct == s.tempo_pct


def test_set_transport_redundant_playing_returns_same_instance() -> None:
    s = initial_state()
    out = reduce(s, {"type": MessageType.SET_TRANSPORT, "playing": False})
    assert out is s


def test_set_transport_requires_playing_field() -> None:
    s = initial_state()
    # No ``playing`` field — drop the frame instead of silently mutating
    # only the optional fields.
    out = reduce(s, {"type": MessageType.SET_TRANSPORT, "position_s": 30.0})
    assert out is s


def test_set_transport_with_position_seeks() -> None:
    s = initial_state()
    out = reduce(s, {
        "type": MessageType.SET_TRANSPORT,
        "playing": True,
        "position_s": 42.5,
    })
    assert out.playing is True
    assert out.position_s == 42.5


def test_set_transport_clamps_tempo_above_max() -> None:
    s = initial_state()
    out = reduce(s, {
        "type": MessageType.SET_TRANSPORT,
        "playing": True,
        "tempo_pct": 1.5,  # absurd value; clamp to 1.0
    })
    assert out.tempo_pct == 1.0


def test_set_transport_clamps_tempo_below_min() -> None:
    s = initial_state()
    out = reduce(s, {
        "type": MessageType.SET_TRANSPORT,
        "playing": True,
        "tempo_pct": 0.1,  # too slow; clamp to 0.5
    })
    assert out.tempo_pct == 0.5


def test_set_transport_ignores_non_numeric_position() -> None:
    s = initial_state()
    out = reduce(s, {
        "type": MessageType.SET_TRANSPORT,
        "playing": True,
        "position_s": "thirty",
    })
    # Plays, but at the prior position (0.0).
    assert out.playing is True
    assert out.position_s == 0.0


def test_set_transport_rejects_boolean_position() -> None:
    """bool is a subclass of int in Python; we don't want
    ``{"position_s": true}`` to silently become 1.0."""
    s = initial_state()
    out = reduce(s, {
        "type": MessageType.SET_TRANSPORT,
        "playing": True,
        "position_s": True,
    })
    assert out.position_s == s.position_s


# ---------------------------------------------------------------------------
# SET_LOOP
# ---------------------------------------------------------------------------

def test_set_loop_sets_both_endpoints() -> None:
    s = initial_state()
    out = reduce(s, {
        "type": MessageType.SET_LOOP,
        "loop_in_s": 60.0,
        "loop_out_s": 90.0,
    })
    assert out.loop_in_s == 60.0
    assert out.loop_out_s == 90.0


def test_set_loop_with_explicit_none_clears_endpoint() -> None:
    s = TransportState(
        playing=False, position_s=0.0, tempo_pct=1.0,
        loop_in_s=10.0, loop_out_s=20.0,
        user_mute=True, monitor_gain=0.0,
    )
    out = reduce(s, {
        "type": MessageType.SET_LOOP,
        "loop_in_s": None,
    })
    assert out.loop_in_s is None
    assert out.loop_out_s == 20.0  # untouched


def test_set_loop_missing_key_keeps_endpoint() -> None:
    """Distinguishes ``"loop_in_s": null`` (clear) from key absent (keep)."""
    s = TransportState(
        playing=False, position_s=0.0, tempo_pct=1.0,
        loop_in_s=10.0, loop_out_s=20.0,
        user_mute=True, monitor_gain=0.0,
    )
    out = reduce(s, {"type": MessageType.SET_LOOP, "loop_out_s": 99.0})
    assert out.loop_in_s == 10.0
    assert out.loop_out_s == 99.0


def test_set_loop_does_not_enforce_ordering() -> None:
    """The reducer applies; validation is API-edge concern."""
    s = initial_state()
    out = reduce(s, {
        "type": MessageType.SET_LOOP,
        "loop_in_s": 80.0,
        "loop_out_s": 20.0,
    })
    assert out.loop_in_s == 80.0
    assert out.loop_out_s == 20.0


def test_set_loop_garbage_keeps_state() -> None:
    s = TransportState(
        playing=False, position_s=0.0, tempo_pct=1.0,
        loop_in_s=10.0, loop_out_s=20.0,
        user_mute=True, monitor_gain=0.0,
    )
    out = reduce(s, {
        "type": MessageType.SET_LOOP,
        "loop_in_s": "garbage",
    })
    assert out.loop_in_s == 10.0  # prior value preserved


# ---------------------------------------------------------------------------
# SET_USER_MUTE
# ---------------------------------------------------------------------------

def test_set_user_mute_toggles_off() -> None:
    s = initial_state()
    out = reduce(s, {"type": MessageType.SET_USER_MUTE, "muted": False})
    assert out.user_mute is False


def test_set_user_mute_non_bool_is_noop() -> None:
    s = initial_state()
    assert reduce(s, {"type": MessageType.SET_USER_MUTE, "muted": "yes"}) is s


def test_set_user_mute_no_value_is_noop() -> None:
    s = initial_state()
    assert reduce(s, {"type": MessageType.SET_USER_MUTE}) is s


# ---------------------------------------------------------------------------
# SET_MONITOR_GAIN (and SET_GAIN legacy alias)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("type_str", [
    MessageType.SET_MONITOR_GAIN,
    MessageType.SET_GAIN,
])
def test_monitor_gain_accepts_both_names_during_transition(type_str: str) -> None:
    s = initial_state()
    out = reduce(s, {"type": type_str, "gain": 0.42})
    assert out.monitor_gain == pytest.approx(0.42)


def test_monitor_gain_clamps_above_one() -> None:
    s = initial_state()
    out = reduce(s, {"type": MessageType.SET_MONITOR_GAIN, "gain": 7.5})
    assert out.monitor_gain == 1.0


def test_monitor_gain_clamps_below_zero() -> None:
    s = initial_state()
    out = reduce(s, {"type": MessageType.SET_MONITOR_GAIN, "gain": -1.0})
    assert out.monitor_gain == 0.0


def test_monitor_gain_accepts_integer() -> None:
    s = initial_state()
    out = reduce(s, {"type": MessageType.SET_MONITOR_GAIN, "gain": 1})
    assert out.monitor_gain == 1.0


def test_monitor_gain_missing_value_is_noop() -> None:
    s = initial_state()
    assert reduce(s, {"type": MessageType.SET_MONITOR_GAIN}) is s


def test_monitor_gain_redundant_value_returns_same_instance() -> None:
    s = initial_state()  # monitor_gain == 0.0
    out = reduce(s, {"type": MessageType.SET_MONITOR_GAIN, "gain": 0.0})
    assert out is s


# ---------------------------------------------------------------------------
# Composition smoke test
# ---------------------------------------------------------------------------

def test_sequence_of_intents_composes() -> None:
    """Smoke test: a realistic user-session intent sequence produces a
    coherent final state. Loop, mute off, gain up, then play."""
    s = initial_state()
    s = reduce(s, {"type": MessageType.SET_LOOP,
                   "loop_in_s": 12.0, "loop_out_s": 36.0})
    s = reduce(s, {"type": MessageType.SET_USER_MUTE, "muted": False})
    s = reduce(s, {"type": MessageType.SET_MONITOR_GAIN, "gain": 0.6})
    s = reduce(s, {"type": MessageType.SET_TRANSPORT, "playing": True,
                   "position_s": 12.0, "tempo_pct": 0.75})

    assert s.playing is True
    assert s.position_s == 12.0
    assert s.tempo_pct == 0.75
    assert s.loop_in_s == 12.0
    assert s.loop_out_s == 36.0
    assert s.user_mute is False
    assert s.monitor_gain == pytest.approx(0.6)
