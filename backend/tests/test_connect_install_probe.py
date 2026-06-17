"""Tests for ``GET /api/connect/installed`` (Audio-Ownership Pivot, Phase 6).

The endpoint is intentionally side-effect-free: it just probes the
filesystem for Connect.app and (if found) peeks at the bundle's
``Info.plist``. These tests pin:

1. The happy path: a discovered bundle produces ``installed: True``
   with the bundle path and the ``CFBundleShortVersionString`` from
   ``Contents/Info.plist``.
2. The missing-bundle path: nothing on disk → ``installed: False``
   with both ``path`` and ``version`` set to ``None``.
3. The malformed-plist path: a bundle that exists but lacks the
   version key → ``installed: True``, ``version: None`` (we never
   503 on a present-but-incomplete bundle; that would punish users
   on dev builds that don't ship Info.plist).
4. A discover-helper import failure does not 500 the endpoint —
   it falls back to the not-installed shape so JAM can still
   render the install CTA.

Tests monkeypatch the discover helpers directly so they run on any
CI machine, with or without Connect actually installed.
"""
from __future__ import annotations

import plistlib
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tone_forge_api import app  # noqa: E402
from local_engine import connect_bridge  # noqa: E402

client = TestClient(app)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_bundle(tmp_path: Path) -> Path:
    """Create a minimal Connect.app directory with a valid Info.plist
    containing ``CFBundleShortVersionString = "1.2.3"``."""
    bundle = tmp_path / "Connect.app"
    contents = bundle / "Contents"
    contents.mkdir(parents=True)
    plist_path = contents / "Info.plist"
    with plist_path.open("wb") as f:
        plistlib.dump({
            "CFBundleIdentifier":          "com.toneforge.connect",
            "CFBundleShortVersionString":  "1.2.3",
        }, f)
    return bundle


@pytest.fixture
def bundle_no_version(tmp_path: Path) -> Path:
    """Connect.app bundle whose Info.plist exists but is missing
    ``CFBundleShortVersionString``."""
    bundle = tmp_path / "Connect.app"
    contents = bundle / "Contents"
    contents.mkdir(parents=True)
    plist_path = contents / "Info.plist"
    with plist_path.open("wb") as f:
        plistlib.dump({"CFBundleIdentifier": "com.toneforge.connect"}, f)
    return bundle


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_installed_happy_path_returns_path_and_version(monkeypatch, fake_bundle):
    monkeypatch.setattr(
        connect_bridge, "discover_connect_bundle", lambda: fake_bundle
    )
    resp = client.get("/api/connect/installed")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "installed": True,
        "path":      str(fake_bundle),
        "version":   "1.2.3",
    }


def test_installed_returns_false_when_no_bundle_on_disk(monkeypatch):
    monkeypatch.setattr(connect_bridge, "discover_connect_bundle", lambda: None)
    resp = client.get("/api/connect/installed")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "installed": False,
        "path":      None,
        "version":   None,
    }


def test_installed_returns_null_version_for_plist_without_key(
    monkeypatch, bundle_no_version
):
    monkeypatch.setattr(
        connect_bridge, "discover_connect_bundle", lambda: bundle_no_version
    )
    resp = client.get("/api/connect/installed")
    assert resp.status_code == 200
    body = resp.json()
    assert body["installed"] is True
    assert body["path"] == str(bundle_no_version)
    assert body["version"] is None


def test_installed_returns_null_version_for_missing_plist(monkeypatch, tmp_path):
    """A discovered bundle that doesn't even have an Info.plist
    still reports installed=true. The endpoint never blocks JAM on
    metadata that is nice-to-have but not load-bearing for pairing."""
    bundle = tmp_path / "Connect.app"
    bundle.mkdir()  # no Contents/Info.plist
    monkeypatch.setattr(
        connect_bridge, "discover_connect_bundle", lambda: bundle
    )
    resp = client.get("/api/connect/installed")
    assert resp.status_code == 200
    body = resp.json()
    assert body["installed"] is True
    assert body["path"] == str(bundle)
    assert body["version"] is None


# ---------------------------------------------------------------------------
# discover_connect_bundle() unit checks — kept in this file because they
# exercise the same surface the endpoint depends on.
# ---------------------------------------------------------------------------


def test_discover_helper_returns_none_when_no_candidates_exist(monkeypatch):
    """Force every candidate path to a guaranteed-nonexistent location."""
    monkeypatch.setattr(
        connect_bridge,
        "_BUNDLE_CANDIDATES",
        [Path("/nonexistent/__toneforge_test_a.app"),
         Path("/nonexistent/__toneforge_test_b.app")],
    )
    assert connect_bridge.discover_connect_bundle() is None


def test_discover_helper_returns_first_existing_candidate(monkeypatch, tmp_path):
    a = tmp_path / "first.app"
    b = tmp_path / "second.app"
    a.mkdir()
    b.mkdir()
    monkeypatch.setattr(connect_bridge, "_BUNDLE_CANDIDATES", [a, b])
    assert connect_bridge.discover_connect_bundle() == a


def test_read_version_returns_string_for_valid_plist(fake_bundle):
    assert connect_bridge.read_connect_bundle_version(fake_bundle) == "1.2.3"


def test_read_version_returns_none_for_missing_plist(tmp_path):
    bundle = tmp_path / "Connect.app"
    bundle.mkdir()
    assert connect_bridge.read_connect_bundle_version(bundle) is None


def test_read_version_returns_none_for_plist_without_key(bundle_no_version):
    assert connect_bridge.read_connect_bundle_version(bundle_no_version) is None
