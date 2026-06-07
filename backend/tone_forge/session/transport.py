"""TransportState reducer.

Single pure function ``reduce(state, frame) -> TransportState`` that
maps an inbound v1 intent frame onto a new canonical state. Used by
the WebSocket dispatch loop in ``tone_forge_api.py`` (P5d) and by the
``SessionBundle.build()`` flow to construct ``initial_transport`` on
reload.

The reducer:

* Never mutates ``state`` (TransportState is frozen).
* Returns the same instance if the intent produces no value change,
  so the dispatch loop can debounce identical broadcasts.
* Clamps numeric inputs at the boundary. The server is the source of
  truth for valid ranges — the UI clamps for UX, Connect double-clamps
  as defense-in-depth, but everything that hits the wire goes through
  here.
* Ignores unknown ``type`` values rather than raising. Forward-compat:
  a v2 intent should land in the legacy handler's default branch, not
  blow up the connection.

Loop semantics:

* ``loop_in_s`` / ``loop_out_s`` are independently nullable. Sending
  ``None`` for either side clears that endpoint. Sending both ``None``
  in one frame clears the loop entirely.
* The reducer does NOT enforce ``loop_in_s < loop_out_s``; that's a
  validation concern at the API edge (which can return an ``error``
  frame). The reducer's job is to apply intents, not to police them.

The reducer does not touch ``apply_tone`` / ``apply_chain`` — those
intents affect tone matching, not transport state, and live in their
own dispatcher.
"""

from __future__ import annotations

from typing import Any, Mapping

from tone_forge.contracts import TransportState
from tone_forge.session.protocol import MessageType


# ---------------------------------------------------------------------------
# Clamping
# ---------------------------------------------------------------------------

_TEMPO_MIN = 0.5
_TEMPO_MAX = 1.0
_GAIN_MIN = 0.0
_GAIN_MAX = 1.0


def _clamp(value: float, lo: float, hi: float) -> float:
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value


def _coerce_float(value: Any) -> float | None:
    """Permissive float coercion.

    JSON arrives as int/float/str depending on how the client built
    the frame. Return None for anything that can't be parsed, so the
    caller can decide whether to drop the field or echo an error.
    """

    if value is None:
        return None
    if isinstance(value, bool):
        # bool is a subclass of int in Python; reject explicitly so
        # ``{"position_s": true}`` doesn't silently become 1.0.
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

def initial_state() -> TransportState:
    """Cold-start TransportState. Muted, stopped, at the head."""

    return TransportState(
        playing=False,
        position_s=0.0,
        tempo_pct=1.0,
        loop_in_s=None,
        loop_out_s=None,
        user_mute=True,
        monitor_gain=0.0,
    )


# ---------------------------------------------------------------------------
# Reducer
# ---------------------------------------------------------------------------

def reduce(state: TransportState, frame: Mapping[str, Any]) -> TransportState:
    """Apply one intent frame to ``state`` and return the resulting state.

    Returns ``state`` unchanged (the same object) when the intent
    produces no real change — caller can identity-compare to decide
    whether to re-broadcast.
    """

    type_str = frame.get("type")
    if not isinstance(type_str, str):
        return state

    if type_str == MessageType.SET_TRANSPORT:
        return _reduce_set_transport(state, frame)

    if type_str == MessageType.SET_LOOP:
        return _reduce_set_loop(state, frame)

    if type_str == MessageType.SET_USER_MUTE:
        return _reduce_set_user_mute(state, frame)

    if type_str == MessageType.SET_MONITOR_GAIN:
        return _reduce_set_monitor_gain(state, frame)

    # Legacy alias for set_monitor_gain. Accepted during the transition
    # window; jam.js still emits it as of this commit.
    if type_str == MessageType.SET_GAIN:
        return _reduce_set_monitor_gain(state, frame)

    return state


def _reduce_set_transport(
    state: TransportState, frame: Mapping[str, Any]
) -> TransportState:
    """SET_TRANSPORT — playing always present, position/tempo optional."""

    playing = frame.get("playing")
    if not isinstance(playing, bool):
        # ``playing`` is required for this intent. Drop the frame
        # rather than silently keeping the prior value, because that
        # leads to UX bugs where a botched frame appears to succeed.
        return state

    position_raw = frame.get("position_s", _UNSET)
    if position_raw is _UNSET:
        position_s = state.position_s
    else:
        coerced = _coerce_float(position_raw)
        position_s = coerced if coerced is not None else state.position_s

    tempo_raw = frame.get("tempo_pct", _UNSET)
    if tempo_raw is _UNSET:
        tempo_pct = state.tempo_pct
    else:
        coerced = _coerce_float(tempo_raw)
        if coerced is None:
            tempo_pct = state.tempo_pct
        else:
            tempo_pct = _clamp(coerced, _TEMPO_MIN, _TEMPO_MAX)

    return _maybe_new(
        state,
        playing=playing,
        position_s=position_s,
        tempo_pct=tempo_pct,
    )


def _reduce_set_loop(
    state: TransportState, frame: Mapping[str, Any]
) -> TransportState:
    """SET_LOOP — explicit None clears each endpoint independently.

    Missing key (not present at all) means "keep prior"; present-but-
    None means "clear this endpoint". The dispatch handler is
    responsible for the distinction.
    """

    if "loop_in_s" in frame:
        in_raw = frame["loop_in_s"]
        loop_in_s = None if in_raw is None else _coerce_float(in_raw)
        if loop_in_s is None and in_raw is not None:
            # Garbage value — treat as "no change" rather than clear.
            loop_in_s = state.loop_in_s
    else:
        loop_in_s = state.loop_in_s

    if "loop_out_s" in frame:
        out_raw = frame["loop_out_s"]
        loop_out_s = None if out_raw is None else _coerce_float(out_raw)
        if loop_out_s is None and out_raw is not None:
            loop_out_s = state.loop_out_s
    else:
        loop_out_s = state.loop_out_s

    return _maybe_new(state, loop_in_s=loop_in_s, loop_out_s=loop_out_s)


def _reduce_set_user_mute(
    state: TransportState, frame: Mapping[str, Any]
) -> TransportState:
    muted = frame.get("muted")
    if not isinstance(muted, bool):
        return state
    return _maybe_new(state, user_mute=muted)


def _reduce_set_monitor_gain(
    state: TransportState, frame: Mapping[str, Any]
) -> TransportState:
    raw = frame.get("gain")
    coerced = _coerce_float(raw)
    if coerced is None:
        return state
    return _maybe_new(state, monitor_gain=_clamp(coerced, _GAIN_MIN, _GAIN_MAX))


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

class _Unset:
    """Sentinel distinct from ``None`` for "field absent" vs "field is null"."""

    __slots__ = ()


_UNSET = _Unset()


def _maybe_new(state: TransportState, **changes: Any) -> TransportState:
    """Return a new TransportState if ``changes`` would actually mutate
    any field; otherwise return ``state`` unchanged.

    Identity equality (``new is old``) is the caller's debounce signal.
    """

    same = all(getattr(state, k) == v for k, v in changes.items())
    if same:
        return state
    return TransportState(
        playing=changes.get("playing", state.playing),
        position_s=changes.get("position_s", state.position_s),
        tempo_pct=changes.get("tempo_pct", state.tempo_pct),
        loop_in_s=changes.get("loop_in_s", state.loop_in_s),
        loop_out_s=changes.get("loop_out_s", state.loop_out_s),
        user_mute=changes.get("user_mute", state.user_mute),
        monitor_gain=changes.get("monitor_gain", state.monitor_gain),
    )


__all__ = ["initial_state", "reduce"]
