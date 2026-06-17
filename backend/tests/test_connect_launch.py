"""Tests for ``POST /api/connect/launch`` (Audio-Ownership Pivot, Phase 6).

The endpoint is a best-effort launcher for Connect.app. It runs
``open(1)`` — first via Launch Services bundle-ID lookup, then via a
discovered path on disk — and returns a small envelope describing
which branch (if any) succeeded. We pin:

1. Bundle-ID success → ``{"launched": True, "method": "open_bundle"}``
   and *only one* subprocess call (the path fallback never fires).
2. Bundle-ID failure + discovered path + path success
   → ``{"launched": True, "method": "open_path"}`` and exactly two
   subprocess calls.
3. Bundle-ID failure + discovered path + path failure
   → ``{"launched": False, "method": "none"}``.
4. Bundle-ID failure + no discovered bundle
   → ``{"launched": False, "method": "none"}`` and *only one*
   subprocess call (we never call ``open`` with no path argument).
5. ``subprocess.TimeoutExpired`` on the bundle-ID call falls through
   to the path branch without 500ing the endpoint.
6. ``FileNotFoundError`` (no ``open(1)`` on PATH — e.g. a non-mac CI
   box) still returns 202 with method=none rather than a 500.

All tests inject a fake ``subprocess.run`` via monkeypatch so they
run identically on macOS, Linux CI, and developer laptops without
Connect installed.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tone_forge_api  # noqa: E402
from local_engine import connect_bridge  # noqa: E402
from tone_forge_api import app  # noqa: E402

client = TestClient(app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ok():
    """Fake a successful ``subprocess.run`` CompletedProcess."""
    return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")


def _fail(rc: int = 1):
    return SimpleNamespace(returncode=rc, stdout=b"", stderr=b"fail")


class _RunRecorder:
    """Capture every call to ``subprocess.run`` and reply per script."""

    def __init__(self, responses):
        # ``responses`` is a list of either CompletedProcess-likes or
        # Exception instances. We pop the head on each call.
        self._responses = list(responses)
        self.calls = []

    def __call__(self, argv, *args, **kwargs):
        self.calls.append(list(argv))
        if not self._responses:
            # Defensive: never want test to silently re-use the last
            # reply and mask a "too many calls" bug.
            raise AssertionError(
                f"subprocess.run called more times than scripted: argv={argv}"
            )
        reply = self._responses.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_launch_open_bundle_succeeds_first_try(monkeypatch):
    rec = _RunRecorder([_ok()])
    monkeypatch.setattr(tone_forge_api.subprocess, "run", rec)
    # discover should never be consulted on the happy path. Make it
    # explode so the test fails loudly if step-2 fires unexpectedly.
    monkeypatch.setattr(
        connect_bridge,
        "discover_connect_bundle",
        lambda: pytest.fail("discover_connect_bundle should not be called"),
    )

    resp = client.post("/api/connect/launch")
    assert resp.status_code == 202
    assert resp.json() == {"launched": True, "method": "open_bundle"}
    assert len(rec.calls) == 1
    assert rec.calls[0] == ["open", "-b", connect_bridge.CONNECT_BUNDLE_ID]


def test_launch_falls_back_to_open_path_when_bundle_id_lookup_fails(
    monkeypatch, tmp_path
):
    bundle = tmp_path / "Connect.app"
    bundle.mkdir()
    rec = _RunRecorder([_fail(), _ok()])
    monkeypatch.setattr(tone_forge_api.subprocess, "run", rec)
    monkeypatch.setattr(
        connect_bridge, "discover_connect_bundle", lambda: bundle
    )

    resp = client.post("/api/connect/launch")
    assert resp.status_code == 202
    assert resp.json() == {"launched": True, "method": "open_path"}
    assert len(rec.calls) == 2
    assert rec.calls[0] == ["open", "-b", connect_bridge.CONNECT_BUNDLE_ID]
    assert rec.calls[1] == ["open", str(bundle)]


# ---------------------------------------------------------------------------
# Sad paths
# ---------------------------------------------------------------------------


def test_launch_returns_none_when_both_branches_fail(monkeypatch, tmp_path):
    bundle = tmp_path / "Connect.app"
    bundle.mkdir()
    rec = _RunRecorder([_fail(), _fail()])
    monkeypatch.setattr(tone_forge_api.subprocess, "run", rec)
    monkeypatch.setattr(
        connect_bridge, "discover_connect_bundle", lambda: bundle
    )

    resp = client.post("/api/connect/launch")
    assert resp.status_code == 202
    assert resp.json() == {"launched": False, "method": "none"}
    # Both steps attempted, neither succeeded.
    assert len(rec.calls) == 2


def test_launch_returns_none_without_path_call_when_no_bundle_discovered(
    monkeypatch,
):
    """If Launch Services fails AND nothing is on disk, we must NOT
    call ``open`` a second time with an empty / nonsense path."""
    rec = _RunRecorder([_fail()])
    monkeypatch.setattr(tone_forge_api.subprocess, "run", rec)
    monkeypatch.setattr(connect_bridge, "discover_connect_bundle", lambda: None)

    resp = client.post("/api/connect/launch")
    assert resp.status_code == 202
    assert resp.json() == {"launched": False, "method": "none"}
    # Only the bundle-ID attempt; the path branch must short-circuit
    # on a None discovery.
    assert len(rec.calls) == 1
    assert rec.calls[0] == ["open", "-b", connect_bridge.CONNECT_BUNDLE_ID]


# ---------------------------------------------------------------------------
# Exception handling — the endpoint must never 500
# ---------------------------------------------------------------------------


def test_launch_survives_timeout_on_bundle_id_and_falls_back(
    monkeypatch, tmp_path
):
    bundle = tmp_path / "Connect.app"
    bundle.mkdir()
    rec = _RunRecorder([
        subprocess.TimeoutExpired(cmd=["open", "-b"], timeout=5),
        _ok(),
    ])
    monkeypatch.setattr(tone_forge_api.subprocess, "run", rec)
    monkeypatch.setattr(
        connect_bridge, "discover_connect_bundle", lambda: bundle
    )

    resp = client.post("/api/connect/launch")
    assert resp.status_code == 202
    assert resp.json() == {"launched": True, "method": "open_path"}
    assert len(rec.calls) == 2


def test_launch_survives_open_binary_missing(monkeypatch):
    """A box without ``/usr/bin/open`` on PATH (e.g. Linux CI) should
    return method=none rather than crash the request."""
    rec = _RunRecorder([
        FileNotFoundError("open"),
    ])
    monkeypatch.setattr(tone_forge_api.subprocess, "run", rec)
    monkeypatch.setattr(connect_bridge, "discover_connect_bundle", lambda: None)

    resp = client.post("/api/connect/launch")
    assert resp.status_code == 202
    assert resp.json() == {"launched": False, "method": "none"}
    assert len(rec.calls) == 1


def test_launch_survives_timeout_on_path_branch(monkeypatch, tmp_path):
    bundle = tmp_path / "Connect.app"
    bundle.mkdir()
    rec = _RunRecorder([
        _fail(),
        subprocess.TimeoutExpired(cmd=["open"], timeout=5),
    ])
    monkeypatch.setattr(tone_forge_api.subprocess, "run", rec)
    monkeypatch.setattr(
        connect_bridge, "discover_connect_bundle", lambda: bundle
    )

    resp = client.post("/api/connect/launch")
    assert resp.status_code == 202
    assert resp.json() == {"launched": False, "method": "none"}
    assert len(rec.calls) == 2
