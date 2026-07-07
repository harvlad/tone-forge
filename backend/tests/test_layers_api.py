"""HTTP-level coverage for the /api/song/{id}/layers routes.

The mobile app POSTs LayerTimeline JSON here to sync recordings across
devices. These tests pin the contract:

  - POST rejects when the referenced song isn't in history.
  - POST rejects payloads missing required LayerTimeline fields.
  - POST rejects when the URL analysisId disagrees with the body.
  - POST writes a file under a tmp-scoped ``_LAYERS_ROOT``.
  - POST is idempotent — re-uploading the same layerId overwrites.
  - GET (list) returns metadata-only summaries sorted newest-first.
  - GET (list) skips corrupt files instead of surfacing 500.
  - GET (single) returns the full body; missing = 404.
  - Path-traversal in analysisId / layerId is rejected.

Fixtures monkeypatch the module-level history helpers so the tests
never touch the real backend history file.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402
import tone_forge_api  # noqa: E402


client = TestClient(tone_forge_api.app)


@pytest.fixture
def layers_root(tmp_path, monkeypatch):
    """Redirect layer storage to a per-test tmp dir."""
    monkeypatch.setattr(tone_forge_api, "_LAYERS_ROOT", tmp_path / "layers")
    return tmp_path / "layers"


@pytest.fixture
def fake_song(monkeypatch):
    """Pretend one song with id 'song-abc' exists in history."""
    def _lookup(entry_id):
        if entry_id == "song-abc":
            return {"id": "song-abc", "name": "Fake Song"}
        return None

    monkeypatch.setattr(tone_forge_api, "_get_history_item", _lookup)
    return "song-abc"


def _layer_body(**overrides):
    body = {
        "timelineVersion": 1,
        "layerId": "layer-1",
        "analysisId": "song-abc",
        "name": "First layer",
        "createdAtEpoch": 1700000000.0,
        "durationSec": 12.5,
        "events": [
            {
                "kind": "sampleOn",
                "songTimeSec": 0.1,
                "params": {"padIdx": 3, "velocity": 1.0},
            }
        ],
        "activePackId": "starter",
    }
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# POST
# ---------------------------------------------------------------------------

def test_post_layer_rejects_unknown_song(layers_root, monkeypatch):
    monkeypatch.setattr(tone_forge_api, "_get_history_item", lambda _id: None)
    r = client.post("/api/song/nope/layers", json=_layer_body())
    assert r.status_code == 404


def test_post_layer_rejects_missing_required_fields(layers_root, fake_song):
    body = _layer_body()
    body.pop("durationSec")
    r = client.post(f"/api/song/{fake_song}/layers", json=body)
    assert r.status_code == 400
    assert "durationSec" in r.json()["detail"]


def test_post_layer_rejects_mismatched_analysis_id(layers_root, fake_song):
    body = _layer_body(analysisId="other-song")
    r = client.post(f"/api/song/{fake_song}/layers", json=body)
    assert r.status_code == 400
    assert "analysisId" in r.json()["detail"]


def test_post_layer_rejects_non_list_events(layers_root, fake_song):
    body = _layer_body(events={"not": "a list"})
    r = client.post(f"/api/song/{fake_song}/layers", json=body)
    assert r.status_code == 400
    assert "events" in r.json()["detail"]


def test_post_layer_writes_file(layers_root, fake_song):
    body = _layer_body()
    r = client.post(f"/api/song/{fake_song}/layers", json=body)
    assert r.status_code == 200
    payload = r.json()
    assert payload["layerId"] == "layer-1"
    assert payload["analysisId"] == fake_song
    assert payload["url"].endswith("/layer-1")

    dest = layers_root / fake_song / "layer-1.json"
    assert dest.exists()
    with open(dest) as f:
        stored = json.load(f)
    assert stored["name"] == "First layer"
    assert len(stored["events"]) == 1


def test_post_layer_is_idempotent(layers_root, fake_song):
    """Re-uploading the same layerId overwrites (used for renames)."""
    body = _layer_body()
    r1 = client.post(f"/api/song/{fake_song}/layers", json=body)
    assert r1.status_code == 200

    body["name"] = "Renamed"
    r2 = client.post(f"/api/song/{fake_song}/layers", json=body)
    assert r2.status_code == 200

    dest = layers_root / fake_song / "layer-1.json"
    with open(dest) as f:
        stored = json.load(f)
    assert stored["name"] == "Renamed"


# ---------------------------------------------------------------------------
# GET (list)
# ---------------------------------------------------------------------------

def test_get_layers_empty_when_none_stored(layers_root, fake_song):
    r = client.get(f"/api/song/{fake_song}/layers")
    assert r.status_code == 200
    assert r.json() == {"analysisId": fake_song, "layers": []}


def test_get_layers_returns_summaries_sorted_newest_first(layers_root, fake_song):
    older = _layer_body(layerId="a", name="Older", createdAtEpoch=1000.0)
    newer = _layer_body(layerId="b", name="Newer", createdAtEpoch=2000.0)
    client.post(f"/api/song/{fake_song}/layers", json=older)
    client.post(f"/api/song/{fake_song}/layers", json=newer)

    r = client.get(f"/api/song/{fake_song}/layers")
    assert r.status_code == 200
    summaries = r.json()["layers"]
    assert [s["layerId"] for s in summaries] == ["b", "a"]
    # Full events blob is NOT included in the summary — only the count.
    assert "events" not in summaries[0]
    assert summaries[0]["eventCount"] == 1


def test_get_layers_skips_corrupt_files(layers_root, fake_song):
    body = _layer_body()
    client.post(f"/api/song/{fake_song}/layers", json=body)

    corrupt = layers_root / fake_song / "junk.json"
    corrupt.write_text("{ not json ")

    r = client.get(f"/api/song/{fake_song}/layers")
    assert r.status_code == 200
    # The valid layer still appears; corrupt file is silently skipped.
    assert len(r.json()["layers"]) == 1


# ---------------------------------------------------------------------------
# GET (single)
# ---------------------------------------------------------------------------

def test_get_single_layer_returns_full_body(layers_root, fake_song):
    body = _layer_body()
    client.post(f"/api/song/{fake_song}/layers", json=body)

    r = client.get(f"/api/song/{fake_song}/layers/layer-1")
    assert r.status_code == 200
    payload = r.json()
    assert payload["layerId"] == "layer-1"
    assert payload["events"][0]["kind"] == "sampleOn"


def test_get_single_layer_missing_yields_404(layers_root, fake_song):
    r = client.get(f"/api/song/{fake_song}/layers/nope")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Path-traversal guards
# ---------------------------------------------------------------------------

def test_layer_id_traversal_rejected(layers_root, fake_song):
    body = _layer_body(layerId="../evil")
    r = client.post(f"/api/song/{fake_song}/layers", json=body)
    assert r.status_code == 400
