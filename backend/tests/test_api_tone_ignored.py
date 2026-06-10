"""``POST /api/tone/ignored`` route coverage.

Telemetry-only endpoint. The Jam UI's dismiss handler at
``static/jam.js:510`` POSTs ``{chain_id, reason, session_id,
source_url}`` here when the user closes the tone card or switches
songs. The handler appends an ``ignored`` event to the tone log so
the calibrator refit has a negative label.

The contract for this endpoint is "never returns a hard failure" —
even a missing instrumentation module or a write-failure on the log
file must still produce 200 so the UI does not surface an error for
a pure-telemetry path.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from tone_forge_api import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _isolate_tone_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Redirect tone_log writes to a tmp file so assertions don't
    depend on the real backend/data/tone_log.jsonl."""
    log_path = tmp_path / "tone_log.jsonl"
    monkeypatch.setenv("TONE_LOG_PATH", str(log_path))
    return log_path


def test_post_tone_ignored_appends_event(_isolate_tone_log: Path) -> None:
    """Happy path: full payload writes one JSON line with all fields."""
    payload = {
        "chain_id": "tfc.ambient",
        "reason": "card_closed",
        "session_id": "sess-abc",
        "source_url": "https://example.com/song",
    }

    resp = client.post("/api/tone/ignored", json=payload)
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    raw = _isolate_tone_log.read_text(encoding="utf-8").strip().splitlines()
    assert len(raw) == 1
    event = json.loads(raw[0])
    assert event["event_type"] == "ignored"
    assert event["chain_id"] == "tfc.ambient"
    assert event["session_id"] == "sess-abc"
    assert event["source_url"] == "https://example.com/song"
    assert event["reason"] == "card_closed"
    assert "ts" in event


def test_post_tone_ignored_accepts_minimal_payload(
    _isolate_tone_log: Path,
) -> None:
    """Only ``chain_id`` is required; everything else can be omitted.
    The Jam dismiss handler always sends a reason but tests pin the
    contract."""
    resp = client.post(
        "/api/tone/ignored",
        json={"chain_id": "tfc.clean_strat"},
    )
    assert resp.status_code == 200

    event = json.loads(_isolate_tone_log.read_text().strip())
    assert event["chain_id"] == "tfc.clean_strat"
    assert event["session_id"] == "unattached"
    assert event["reason"] is None


def test_post_tone_ignored_rejects_missing_chain_id() -> None:
    """``chain_id`` is required by the pydantic schema; FastAPI must
    surface 422 rather than silently logging an empty event."""
    resp = client.post("/api/tone/ignored", json={"reason": "card_closed"})
    assert resp.status_code == 422


def test_post_tone_ignored_swallows_log_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even if the log writer raises, the endpoint must still 200 —
    the Jam UI is allowed to be on a UX path where a failed log
    line must never block the user's dismiss action."""
    # Point TONE_LOG_PATH at an unwritable location. The instrumentation
    # module's _append swallows the OSError; the endpoint's outer
    # try/except is the second line of defense for an import-time
    # failure of the module itself. We exercise the writer path here
    # because that's the failure mode users hit in production
    # (permission errors, full disk).
    bad_path = tmp_path / "no-such-dir" / "cannot-create" / "tone_log.jsonl"
    monkeypatch.setenv("TONE_LOG_PATH", str(bad_path))
    # Also make the parent's parent non-writable so mkdir(parents=True)
    # cannot succeed.
    (tmp_path / "no-such-dir").mkdir()
    (tmp_path / "no-such-dir").chmod(0o400)

    try:
        resp = client.post(
            "/api/tone/ignored",
            json={"chain_id": "tfc.modern_gain", "reason": "song_switched"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
    finally:
        # Restore perms so pytest can clean up.
        (tmp_path / "no-such-dir").chmod(0o700)
