"""HTTP-level coverage for the /debug visualizer endpoints.

Pins the wire contract for:

  - ``GET /debug``              → serves the static debug.html page.
  - ``GET /api/debug/corpus``   → returns the song-trial-corpus fixture.
  - ``GET /api/debug/sessions`` → returns a lightweight session catalog
    with the ``has_debug_features`` flag the Inspector dropdown uses
    to grey out legacy entries.

These tests are intentionally narrow — the visualizer itself is
client-side; the server's job is to expose the data shapes the
frontend depends on without coupling to UI concerns.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

import tone_forge_api


client = TestClient(tone_forge_api.app)


# ---------------------------------------------------------------------------
# /debug page
# ---------------------------------------------------------------------------

def test_debug_page_is_served():
    """The /debug route returns the static HTML shell."""
    r = client.get("/debug")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")
    assert "ToneForge Debug" in r.text
    # The three tabs are rendered server-side as part of the static shell.
    assert 'data-tab="inspector"' in r.text
    assert 'data-tab="corpus"' in r.text
    assert 'data-tab="history"' in r.text


# ---------------------------------------------------------------------------
# /api/debug/corpus
# ---------------------------------------------------------------------------

def test_debug_corpus_returns_trial_songs():
    """The corpus endpoint streams the song_trial_corpus.json fixture
    unchanged so the Corpus tab can compare ground truth to predictions."""
    r = client.get("/api/debug/corpus")
    assert r.status_code == 200
    payload = r.json()
    # Fixture may be wrapped in {"songs": [...]} or be a bare list — both
    # shapes are accepted by the client.
    songs = payload.get("songs") if isinstance(payload, dict) else payload
    assert isinstance(songs, list)
    assert len(songs) >= 1
    # Every entry carries the keys the Corpus tab reads.
    for song in songs:
        assert "title" in song or "slug" in song
        assert "ground_truth_sections" in song
        for gt in song["ground_truth_sections"]:
            assert gt.get("guidance_mode") in {"chord", "riff", "lead"}


# ---------------------------------------------------------------------------
# /api/debug/sessions
# ---------------------------------------------------------------------------

def test_debug_sessions_shape():
    """The Inspector picker reads ``id``, ``name``, ``timestamp``,
    ``section_count`` and ``has_debug_features`` for every entry."""
    r = client.get("/api/debug/sessions")
    assert r.status_code == 200
    payload = r.json()
    assert "sessions" in payload
    assert isinstance(payload["sessions"], list)
    for entry in payload["sessions"]:
        assert "id" in entry
        assert "name" in entry
        assert "has_debug_features" in entry
        assert isinstance(entry["has_debug_features"], bool)
        assert "section_count" in entry
        assert isinstance(entry["section_count"], int)


def test_debug_sessions_flag_uses_section_features(monkeypatch):
    """``has_debug_features`` flips true iff at least one section in
    the persisted ``result`` has a populated ``debug_features``. This
    is what the Inspector greys out the picker on."""
    fake_history = [
        {
            "id": "with-features",
            "name": "song A",
            "timestamp": "2026-06-19T12:00:00",
            "detected_type": "full_mix",
            "result": {
                "sections": [
                    {
                        "start_time": 0.0,
                        "end_time": 4.0,
                        "type": "verse",
                        "debug_features": [{"stem_name": "guitar"}],
                    }
                ],
            },
        },
        {
            "id": "legacy",
            "name": "song B",
            "timestamp": "2026-06-19T11:00:00",
            "detected_type": "full_mix",
            "result": {
                "sections": [
                    {"start_time": 0.0, "end_time": 4.0, "type": "verse"},
                ],
            },
        },
        {
            "id": "no-result",
            "name": "song C",
            "timestamp": "2026-06-19T10:00:00",
            "detected_type": "guitar",
            # No "result" at all — pure metadata stub.
        },
    ]
    monkeypatch.setattr(tone_forge_api, "_load_history", lambda: fake_history)

    r = client.get("/api/debug/sessions")
    assert r.status_code == 200
    sessions = {s["id"]: s for s in r.json()["sessions"]}
    assert sessions["with-features"]["has_debug_features"] is True
    assert sessions["with-features"]["section_count"] == 1
    assert sessions["legacy"]["has_debug_features"] is False
    assert sessions["legacy"]["section_count"] == 1
    assert sessions["no-result"]["has_debug_features"] is False
    assert sessions["no-result"]["section_count"] == 0
