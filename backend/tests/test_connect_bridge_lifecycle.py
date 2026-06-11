"""Regression tests for the Connect-bridge hardening pass.

Three behaviours are pinned here, one per failure mode the pass closed:

1. ``_ConnectChannel`` is removed from ``_connect_channels`` after the
   last peer leaves. Without this the per-session caches
   (``last_preset``/``last_gain``/``last_chain``) leak across browser
   reloads.

2. When ``broadcast()`` drops a dead client, the surviving peers
   receive a ``peer_left`` frame with the updated count so the Jam UI
   can flip out of the paired state immediately instead of waiting
   for the next reconnect tick.

3. ``ConnectSupervisor`` schedules a bounded auto-restart when the
   helper exits unexpectedly, and suppresses any pending restart once
   ``stop()`` makes the user's intent explicit.

These tests poke the data structures directly (FakeWS / FakeProc)
instead of going through the WebSocket / subprocess layer, because the
behaviours being verified are pure state-machine transitions — adding
network and OS plumbing would obscure the assertion without adding
coverage.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from local_engine import connect_bridge as cb_module  # noqa: E402
from local_engine.connect_bridge import ConnectSupervisor  # noqa: E402
import tone_forge_api as api  # noqa: E402


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeWS:
    """Minimal stand-in for a Starlette WebSocket.

    Exposes only ``send_json``. Set ``fail=True`` to simulate a peer
    that disconnected between accept and the next broadcast.
    """

    def __init__(self, fail: bool = False) -> None:
        self.sent: list[dict] = []
        self.fail = fail

    async def send_json(self, payload: dict) -> None:
        if self.fail:
            raise RuntimeError("simulated send to dead client")
        self.sent.append(payload)


class _FakeProc:
    """Minimal stand-in for ``subprocess.Popen``.

    The supervisor only touches ``pid``, ``poll()``, and
    ``returncode`` on the handle (kill paths use ``os.killpg``, which
    these tests never reach because we never call ``stop()`` on a live
    fake).
    """

    _next_pid = 50000

    def __init__(self) -> None:
        _FakeProc._next_pid += 1
        self.pid = _FakeProc._next_pid
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode

    def die(self, rc: int = 1) -> None:
        self.returncode = rc


# ---------------------------------------------------------------------------
# Failure 1 — empty channels are reaped
# ---------------------------------------------------------------------------


def test_channel_removed_from_registry_when_last_peer_leaves() -> None:
    session = "test_lifecycle_reap"

    async def runner() -> tuple[bool, bool]:
        ch = api._ConnectChannel(session_id=session)
        async with api._connect_channels_lock:
            api._connect_channels[session] = ch
        ws = _FakeWS()
        ch.clients.add(ws)  # type: ignore[arg-type]

        present_before = session in api._connect_channels
        await ch.leave(ws)  # type: ignore[arg-type]
        present_after = session in api._connect_channels
        return present_before, present_after

    before, after = asyncio.run(runner())
    assert before is True, "channel should be registered before leave"
    assert after is False, "channel must be reaped once empty"


def test_channel_kept_when_other_peers_remain() -> None:
    """Reaping is conditional on emptiness — losing one of two peers
    must not nuke the cached session state on the survivor.
    """
    session = "test_lifecycle_partial_leave"

    async def runner() -> bool:
        ch = api._ConnectChannel(session_id=session)
        async with api._connect_channels_lock:
            api._connect_channels[session] = ch
        a = _FakeWS()
        b = _FakeWS()
        ch.clients.update({a, b})  # type: ignore[arg-type]
        await ch.leave(a)  # type: ignore[arg-type]
        return session in api._connect_channels

    try:
        assert asyncio.run(runner()) is True
    finally:
        # Don't pollute the global registry for other tests.
        api._connect_channels.pop(session, None)


# ---------------------------------------------------------------------------
# Failure 2 — survivors learn about dead peers via peer_left
# ---------------------------------------------------------------------------


def test_broadcast_notifies_survivors_of_dead_peer() -> None:
    session = "test_lifecycle_peer_left"

    async def runner() -> tuple[list[dict], list[dict]]:
        ch = api._ConnectChannel(session_id=session)
        async with api._connect_channels_lock:
            api._connect_channels[session] = ch
        sender = _FakeWS()
        survivor = _FakeWS()
        dead = _FakeWS(fail=True)
        ch.clients.update({sender, survivor, dead})  # type: ignore[arg-type]

        await ch.broadcast(
            sender,  # type: ignore[arg-type]
            {"type": "preset_push", "preset": {"x": 1}},
        )
        return survivor.sent, dead.sent

    try:
        survivor_msgs, dead_msgs = asyncio.run(runner())
    finally:
        api._connect_channels.pop(session, None)

    types_seen = [m.get("type") for m in survivor_msgs]
    assert "preset_push" in types_seen, "survivor must still receive the broadcast"
    assert "peer_left" in types_seen, "survivor must be told the dead peer dropped"

    peer_left = next(m for m in survivor_msgs if m["type"] == "peer_left")
    # Survivor count excludes the dead one but includes the survivor
    # itself (the channel's view of remaining clients).
    assert peer_left["peers"] == 2
    assert peer_left["reason"] == "send_failed"

    # The dead client never received anything (would have raised first).
    assert dead_msgs == []


def test_broadcast_no_peer_left_when_no_one_died() -> None:
    """The new notification path must not fire on a normal broadcast."""
    session = "test_lifecycle_clean_broadcast"

    async def runner() -> list[dict]:
        ch = api._ConnectChannel(session_id=session)
        async with api._connect_channels_lock:
            api._connect_channels[session] = ch
        sender = _FakeWS()
        survivor = _FakeWS()
        ch.clients.update({sender, survivor})  # type: ignore[arg-type]
        await ch.broadcast(sender, {"type": "ping"})  # type: ignore[arg-type]
        return survivor.sent

    try:
        msgs = asyncio.run(runner())
    finally:
        api._connect_channels.pop(session, None)

    assert [m["type"] for m in msgs] == ["ping"], "no peer_left on healthy path"


# ---------------------------------------------------------------------------
# Failure 3 — supervisor auto-restart
# ---------------------------------------------------------------------------


def _make_patched_supervisor(monkeypatch) -> tuple[ConnectSupervisor, list[_FakeProc]]:
    """Build a supervisor wired to fake binary discovery + Popen.

    Returns the supervisor and a mutable list of all fakes created so
    far so tests can drive crashes on the most recent one.
    """
    created: list[_FakeProc] = []

    def fake_discover() -> Path:
        return Path("/tmp/fake-connect-binary")

    def fake_popen(*_args, **_kwargs) -> _FakeProc:
        # Close the log file the supervisor opens; we don't need the
        # fd hanging around in tests. The supervisor only holds a
        # reference inside ``start()``'s local scope, so the file
        # closes on return when the FakeProc keeps no fd.
        p = _FakeProc()
        created.append(p)
        return p

    # The supervisor calls discover_connect_binary() twice per
    # start() (once for spawn, once for _status_locked). Patch the
    # module attribute so both lookups see the fake.
    monkeypatch.setattr(cb_module, "discover_connect_binary", fake_discover)
    monkeypatch.setattr(cb_module.subprocess, "Popen", fake_popen)

    return ConnectSupervisor(session_id="lifecycle-test"), created


def test_supervisor_schedules_auto_restart_on_unexpected_exit(monkeypatch) -> None:
    sup, procs = _make_patched_supervisor(monkeypatch)

    status = sup.start()
    assert status.running is True
    assert sup._wanted_running is True
    assert len(procs) == 1
    assert sup._restart_attempts == 0
    assert sup._restart_timer is None

    # Simulate a crash and let the next status() tick observe it.
    procs[0].die(rc=1)
    status_after = sup.status()

    assert status_after.running is False
    assert sup._wanted_running is True, "wanted_running survives a crash"
    assert sup._restart_attempts == 1, "first auto-restart consumes one attempt"
    assert sup._restart_timer is not None, "respawn must be queued"
    assert isinstance(sup._restart_timer, threading.Timer)
    # First retry backs off by 2**0 = 1.0 seconds, capped at 60.
    assert sup._restart_timer.interval == 1.0
    assert "exited with code 1" in (status_after.last_error or "")

    # Cancel the queued respawn so the test process doesn't fire it.
    sup._restart_timer.cancel()


def test_supervisor_auto_restart_respawns_via_callback(monkeypatch) -> None:
    """Drive the timer callback synchronously and confirm a fresh
    Popen is issued. We bypass the real Timer so the test doesn't
    wait on real wall-clock backoff.
    """
    sup, procs = _make_patched_supervisor(monkeypatch)
    sup.start()
    assert len(procs) == 1
    procs[0].die(rc=1)
    sup.status()  # observes the crash, schedules a restart
    assert sup._restart_timer is not None
    sup._restart_timer.cancel()  # we'll invoke the callback ourselves
    sup._auto_restart()
    assert len(procs) == 2, "auto_restart must spawn a fresh process"
    assert sup.status().running is True


def test_supervisor_stop_suppresses_pending_restart(monkeypatch) -> None:
    sup, procs = _make_patched_supervisor(monkeypatch)
    sup.start()
    procs[0].die(rc=1)
    sup.status()  # schedules a restart
    timer = sup._restart_timer
    assert timer is not None

    # stop() must mark wanted=False, cancel the timer, and reset the
    # attempt counter so a subsequent manual start gets a clean budget.
    sup.stop()
    assert sup._wanted_running is False
    assert sup._restart_attempts == 0
    assert sup._restart_timer is None

    # If the user hadn't called stop(), the timer would have fired
    # _auto_restart and respawned. Drive it manually to prove the
    # guard inside _auto_restart honours wanted_running.
    sup._auto_restart()
    assert len(procs) == 1, "stop() must veto pending auto-restart"


def test_supervisor_auto_restart_budget_is_bounded(monkeypatch) -> None:
    """After ``_MAX_RESTART_ATTEMPTS`` crashes in a row with no
    healthy window in between, the supervisor stops scheduling and
    leaves ``last_error`` populated for the tray UI to show.
    """
    sup, procs = _make_patched_supervisor(monkeypatch)
    sup.start()

    for i in range(cb_module._MAX_RESTART_ATTEMPTS):
        procs[-1].die(rc=1)
        sup.status()  # observes crash, schedules restart
        assert sup._restart_timer is not None
        sup._restart_timer.cancel()
        sup._auto_restart()
        # _auto_restart spawns a fresh proc, which immediately dies in
        # the next loop iteration.
        assert sup._restart_attempts == i + 1

    # Budget exhausted. Next crash must NOT schedule another restart.
    procs[-1].die(rc=1)
    sup.status()
    assert sup._restart_timer is None
    assert sup._restart_attempts == cb_module._MAX_RESTART_ATTEMPTS
    assert "exited with code 1" in (sup.status().last_error or "")


# ---------------------------------------------------------------------------
# Failure 4 — audio_input_name flows from preferences into the child env
# (Priority 7 #38 follow-up). The Connect helper reads
# TONEFORGE_AUDIO_INPUT_NAME to pick a non-default CoreAudio input;
# the supervisor is responsible for sourcing it from persisted prefs.
# ---------------------------------------------------------------------------


def _patch_supervisor_capturing_env(
    monkeypatch,
) -> tuple[ConnectSupervisor, list[dict]]:
    """Variant of ``_make_patched_supervisor`` that records every
    ``Popen`` kwargs dict — tests assert on ``env`` to confirm the
    audio-input plumbing reaches the child without poking os.environ.
    """
    captured: list[dict] = []

    def fake_discover() -> Path:
        return Path("/tmp/fake-connect-binary")

    def fake_popen(*args, **kwargs):
        captured.append(kwargs)
        return _FakeProc()

    monkeypatch.setattr(cb_module, "discover_connect_binary", fake_discover)
    monkeypatch.setattr(cb_module.subprocess, "Popen", fake_popen)

    return ConnectSupervisor(session_id="audio-input-test"), captured


def test_supervisor_injects_audio_input_env_when_pref_set(monkeypatch) -> None:
    """A user with a pinned interface gets its name in the child env."""
    monkeypatch.setattr(
        cb_module,
        "_resolve_audio_input_name",
        lambda: "Focusrite Scarlett 2i2",
    )
    sup, captured = _patch_supervisor_capturing_env(monkeypatch)

    sup.start()
    assert len(captured) == 1
    env = captured[0].get("env")
    assert env is not None, "supervisor must pass an explicit env to Popen"
    assert env[cb_module._AUDIO_INPUT_ENV] == "Focusrite Scarlett 2i2"
    # We must not have mutated the parent's environment.
    assert cb_module._AUDIO_INPUT_ENV not in os.environ


def test_supervisor_omits_audio_input_env_when_pref_missing(monkeypatch) -> None:
    """No persisted pref → no env var → Swift side falls back to default."""
    monkeypatch.setattr(cb_module, "_resolve_audio_input_name", lambda: None)
    # Pre-seed os.environ to prove the supervisor strips an inherited
    # value rather than silently propagating it.
    monkeypatch.setenv(cb_module._AUDIO_INPUT_ENV, "Stale Inherited Mic")
    sup, captured = _patch_supervisor_capturing_env(monkeypatch)

    sup.start()
    assert len(captured) == 1
    env = captured[0].get("env")
    assert env is not None
    assert cb_module._AUDIO_INPUT_ENV not in env, (
        "stale inherited env must not propagate to the child when the "
        "user has no pinned interface"
    )


def test_resolve_audio_input_name_reads_persisted_pref(monkeypatch, tmp_path) -> None:
    """End-to-end: write a real device.json and confirm the helper
    surfaces ``audio_input_name`` without going through the API.
    Belt-and-braces — guards against an import-path drift between the
    supervisor and the persistence module.
    """
    import json

    prefs_path = tmp_path / "device.json"
    prefs_path.write_text(
        json.dumps(
            {
                "device_class": "interface_only",
                "audio_input_name": "MOTU M2",
                "preferred_chain_family": None,
                "first_seen_iso": None,
                "last_used_iso": None,
            }
        )
    )
    monkeypatch.setenv("TONEFORGE_DEVICE_PREFS_PATH", str(prefs_path))

    assert cb_module._resolve_audio_input_name() == "MOTU M2"


def test_resolve_audio_input_name_returns_none_without_file(
    monkeypatch, tmp_path
) -> None:
    """Absent file → None, never raise."""
    monkeypatch.setenv(
        "TONEFORGE_DEVICE_PREFS_PATH", str(tmp_path / "missing.json")
    )
    assert cb_module._resolve_audio_input_name() is None
