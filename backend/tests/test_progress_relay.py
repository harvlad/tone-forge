"""Progress relay: bursts must coalesce, and the bar must never reverse.

The worker emits stage markers in bursts — 0.05/0.07/0.10 back-to-back
at startup, and the parallel MIDI stems all announce themselves within
milliseconds of "Stems separated". The old rate limiter DISCARDED
anything arriving inside its 2s window, so the client was posted 9% and
then nothing until separation ended, then 50% and nothing until MIDI
ended. On a CPU pod that is minutes of a frozen number, which reads as a
hang. These tests pin the coalescing behaviour that replaced it.

The relay logic lives inline in RemoteWorker.run_job, so it is
reproduced here in the same shape rather than imported — the invariants
under test are the throttle's, not the surrounding job plumbing's.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from local_engine.remote_worker import _PROGRESS_MIN_INTERVAL_SEC


class _Relay:
    """The run_job progress path: coalesce inside the window, clamp
    monotonically, flush on the 1s idle tick."""

    def __init__(self):
        self.posts: list[tuple[float, str]] = []
        self._pending = None
        self._last_sent = 0.0
        self._high_water = 2.0
        self.now = 0.0

    def flush(self) -> None:
        if (self._pending is None
                or self.now - self._last_sent < _PROGRESS_MIN_INTERVAL_SEC):
            return
        self._last_sent = self.now
        pct, message = self._pending
        self._pending = None
        self.posts.append((pct, message))

    def event(self, progress: float, message: str = "") -> None:
        pct = max(5 + 90 * progress, self._high_water)
        self._high_water = pct
        self._pending = (pct, message)
        self.flush()

    def tick(self, seconds: float = 1.0) -> None:
        self.now += seconds
        self.flush()


def test_startup_burst_lands_on_the_newest_value_not_the_first():
    """0.05/0.07/0.10 arrive together. The old code posted 9% and threw
    the rest away; the bar then sat at 9% for all of separation."""
    r = _Relay()
    r.event(0.05, "Processing on CPU...")
    r.event(0.07, "Converted audio")
    r.event(0.10, "Separating stems on CPU...")
    r.tick(); r.tick()
    assert r.posts[-1][0] == 5 + 90 * 0.10
    assert r.posts[-1][1] == "Separating stems on CPU..."


def test_no_event_is_lost_when_the_next_one_is_minutes_away():
    """The burst at the start of MIDI extraction is followed by silence
    while the stems run. The idle tick must flush it."""
    r = _Relay()
    r.event(0.50, "Stems separated")
    r.tick(); r.tick()
    r.event(0.52, "Extracting drums MIDI...")   # inside the window
    assert len(r.posts) == 1                    # not posted yet
    r.tick(); r.tick()                          # idle ticks flush it
    assert r.posts[-1][1] == "Extracting drums MIDI..."


def test_rate_stays_capped_under_a_flood():
    r = _Relay()
    for i in range(100):
        r.event(0.5 + i * 0.001)
        r.now += 0.05        # 5s of wall clock, 100 events
        r.flush()
    assert len(r.posts) <= 5


def test_percent_never_runs_backwards():
    """Parallel MIDI stems finish out of band order — a fast stem
    reporting after a slow one used to move the bar backwards."""
    r = _Relay()
    for progress in (0.66, 0.58, 0.76, 0.52):
        r.event(progress)
        r.tick(); r.tick()
    percents = [p for p, _ in r.posts]
    assert percents == sorted(percents)
    assert percents[-1] == 5 + 90 * 0.76
