"""
ToneForge local engine — Connect helper supervisor.

The local engine already owns the "stuff running on the user's Mac"
surface area (Demucs, plugin scanning, model downloads, the tray icon).
The Connect Swift helper is more of the same: a long-running native
process the browser wants paired with the current session.

This module:
  * locates the Connect binary inside the repo (release first, debug
    fallback), or returns a clear "needs build" hint;
  * supervises a single `Connect bridge` child process — spawn, stop,
    restart;
  * surfaces enough state (pid, status, last_error) for the tray menu
    and the /api/connect/* endpoints to render.

Design notes:
  * We never `subprocess.call` — that blocks the event loop. Every
    operation here uses Popen + non-blocking poll().
  * Stdout/stderr are redirected to a log file under
    ~/Library/Logs/ToneForge/connect-bridge.log so the user has a
    breadcrumb when things misbehave; the parent never reads the pipe
    (avoids the classic deadlock on full pipe buffers).
  * The supervisor is process-global. We do not allow more than one
    Connect bridge per local engine — the bridge connects to a single
    session channel and there is no use case for parallel bridges.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("toneforge.connect")


# ---- Binary discovery -------------------------------------------------------

# We expect the Connect Swift package at <repo>/connect. The release
# binary lives at .build/release/Connect; debug is the fallback because
# in dev the user hasn't necessarily run `swift build -c release` yet.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_CONNECT_DIR = _REPO_ROOT / "connect"
_BINARY_CANDIDATES = [
    _CONNECT_DIR / ".build" / "release" / "Connect",
    _CONNECT_DIR / ".build" / "debug" / "Connect",
    # Future: a packaged .app bundle would land here. The local engine
    # supervisor doesn't care which path wins.
    _CONNECT_DIR / "Connect.app" / "Contents" / "MacOS" / "Connect",
]


def discover_connect_binary() -> Optional[Path]:
    """Return the first existing Connect binary candidate, or None."""
    for candidate in _BINARY_CANDIDATES:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def _log_path() -> Path:
    """User-visible log file for the supervised Connect process."""
    base = Path.home() / "Library" / "Logs" / "ToneForge"
    base.mkdir(parents=True, exist_ok=True)
    return base / "connect-bridge.log"


# ---- Supervisor -------------------------------------------------------------

# Auto-restart policy. Bounded exponential backoff so a permanently
# broken helper (missing entitlement, port already taken, etc.) doesn't
# burn CPU in a tight respawn loop. After the helper stays alive for
# _HEALTHY_SECONDS we treat the prior failure as transient and reset
# the attempt counter.
_MAX_RESTART_ATTEMPTS = 4
_HEALTHY_SECONDS = 30.0


@dataclass
class ConnectStatus:
    running: bool
    pid: Optional[int]
    session_id: str
    binary: Optional[str]
    last_error: Optional[str] = None
    log_path: Optional[str] = None


class ConnectSupervisor:
    """Single-process supervisor for the Connect bridge helper.

    Thread-safe. All public methods take a short lock so concurrent
    HTTP handlers / tray callbacks can't double-spawn or race the
    cleanup path.
    """

    def __init__(self, session_id: str = "default", server_url: Optional[str] = None) -> None:
        self.session_id = session_id
        # The bridge talks to /ws/connect-bridge on the main backend
        # (tone_forge_api.py). Default matches the dev convention.
        self.server_url = server_url or "ws://127.0.0.1:8000/ws/connect-bridge"
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._last_error: Optional[str] = None
        # Auto-restart state. ``_wanted_running`` is the user's
        # expressed intent: True between start() and stop(), so the
        # reaper can distinguish "crashed" from "we asked it to stop".
        # ``_restart_attempts`` tracks the bounded backoff window.
        # ``_spawn_ts`` is the monotonic timestamp of the most recent
        # successful spawn, used to detect healthy uptime.
        self._wanted_running: bool = False
        self._restart_attempts: int = 0
        self._spawn_ts: Optional[float] = None
        self._restart_timer: Optional[threading.Timer] = None

    # ---- public API ---------------------------------------------------------

    def status(self) -> ConnectStatus:
        with self._lock:
            self._reap()
            # Reset the auto-restart budget once the helper has been
            # alive long enough that we believe the prior failure was
            # transient. Without this, four flaky crashes spread over
            # an hour would permanently disable auto-restart.
            if (
                self._proc is not None
                and self._spawn_ts is not None
                and time.monotonic() - self._spawn_ts >= _HEALTHY_SECONDS
            ):
                self._restart_attempts = 0
            return self._status_locked()

    def start(self) -> ConnectStatus:
        with self._lock:
            # Cancel any pending auto-restart: an explicit start request
            # supersedes the backoff timer.
            if self._restart_timer is not None:
                self._restart_timer.cancel()
                self._restart_timer = None
            self._wanted_running = True
            self._reap()
            if self._proc is not None:
                return self._status_locked()

            binary = discover_connect_binary()
            if binary is None:
                self._last_error = (
                    "Connect binary not found. Build it once with:\n"
                    f"  cd {_CONNECT_DIR} && swift build -c release"
                )
                logger.warning("Connect: %s", self._last_error)
                return self._status_locked()

            try:
                log_file = open(_log_path(), "ab", buffering=0)
                # Use Popen with start_new_session so the child gets its
                # own process group; we can signal the group as a unit
                # without affecting our own.
                self._proc = subprocess.Popen(
                    [str(binary), "bridge", self.session_id, self.server_url],
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True,
                    close_fds=True,
                )
                self._spawn_ts = time.monotonic()
                self._last_error = None
                logger.info(
                    "Connect: spawned %s (pid=%d, session=%s)",
                    binary, self._proc.pid, self.session_id,
                )
            except Exception as e:
                self._last_error = f"spawn failed: {e}"
                logger.error("Connect: %s", self._last_error)
            return self._status_locked()

    def stop(self, grace_sec: float = 1.5) -> ConnectStatus:
        with self._lock:
            # User asked us to stop. Cancel any pending auto-restart and
            # reset the attempt counter so the next manual start gets a
            # clean budget.
            self._wanted_running = False
            if self._restart_timer is not None:
                self._restart_timer.cancel()
                self._restart_timer = None
            self._restart_attempts = 0
            self._reap()
            if self._proc is None:
                return self._status_locked()

            pid = self._proc.pid
            try:
                # Signal the process group so any descendants go with it.
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            except ProcessLookupError:
                pass
            except Exception as e:
                logger.warning("Connect: SIGTERM failed: %s", e)

            # Brief grace period, then SIGKILL if it's still alive.
            deadline = time.monotonic() + grace_sec
            while time.monotonic() < deadline:
                if self._proc.poll() is not None:
                    break
                time.sleep(0.05)
            if self._proc.poll() is None:
                try:
                    os.killpg(os.getpgid(pid), signal.SIGKILL)
                except Exception:
                    pass

            self._proc = None
            self._spawn_ts = None
            logger.info("Connect: stopped pid=%d", pid)
            return self._status_locked()

    def restart(self) -> ConnectStatus:
        self.stop()
        return self.start()

    # ---- internal -----------------------------------------------------------

    def _reap(self) -> None:
        """Drop the process handle if the child has exited.

        If the user still wants the helper alive (``_wanted_running``)
        and we have attempts left in the budget, schedule a delayed
        respawn via ``threading.Timer``. A clean exit (rc=0) that the
        user didn't ask for still counts as unexpected — the bridge is
        a long-running helper, not a one-shot command.
        """
        if self._proc is None or self._proc.poll() is None:
            return
        rc = self._proc.returncode
        self._proc = None
        self._spawn_ts = None
        if rc != 0:
            self._last_error = f"child exited with code {rc}"
            logger.warning("Connect: child exited rc=%s", rc)
        else:
            logger.info("Connect: child exited cleanly")
        if not self._wanted_running:
            return
        if self._restart_attempts >= _MAX_RESTART_ATTEMPTS:
            logger.warning(
                "Connect: auto-restart budget exhausted (%d attempts); "
                "manual restart required",
                self._restart_attempts,
            )
            return
        delay = min(60.0, float(2 ** self._restart_attempts))
        self._restart_attempts += 1
        logger.info(
            "Connect: scheduling auto-restart %d/%d in %.1fs",
            self._restart_attempts, _MAX_RESTART_ATTEMPTS, delay,
        )
        if self._restart_timer is not None:
            self._restart_timer.cancel()
        t = threading.Timer(delay, self._auto_restart)
        t.daemon = True
        self._restart_timer = t
        t.start()

    def _auto_restart(self) -> None:
        """Timer callback that respawns the helper if still wanted."""
        with self._lock:
            self._restart_timer = None
            if not self._wanted_running:
                return
            if self._proc is not None:
                # Someone (manual start, prior timer) already revived it.
                return
        # Release the lock before calling start() — it reacquires.
        try:
            self.start()
        except Exception as e:
            logger.warning("Connect: auto-restart attempt failed: %s", e)

    def _status_locked(self) -> ConnectStatus:
        # Caller already holds the lock.
        binary = discover_connect_binary()
        return ConnectStatus(
            running=self._proc is not None,
            pid=self._proc.pid if self._proc else None,
            session_id=self.session_id,
            binary=str(binary) if binary else None,
            last_error=self._last_error,
            log_path=str(_log_path()),
        )


# Module-global supervisor used by the FastAPI app and the tray menu.
# Lazy-instantiated so importing this module is free.
_supervisor: Optional[ConnectSupervisor] = None


def get_supervisor() -> ConnectSupervisor:
    global _supervisor
    if _supervisor is None:
        _supervisor = ConnectSupervisor()
    return _supervisor
