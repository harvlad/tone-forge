"""Wire-shape coverage for ``GET /api/song/{id}/kit?kind=drums``.

The builder itself is covered in ``test_drum_kit.py``; this pins the route
behavior: a persisted hits table serves a SamplePack manifest without any
backfill, an empty table maps ValueError → 422, and a missing entry 404s.
All cases pre-attach ``drum_hits`` so the process-pool backfill never runs
in tests.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

import tone_forge_api
from tone_forge.performance.drum_kit import DRUM_HITS_RESULT_KEY, HITS_VERSION

client = TestClient(tone_forge_api.app)

_HITS = {
    "version": HITS_VERSION,
    "hits": [
        {"t": 0.50, "end": 0.90, "cls": "kick", "strength": 1.0, "isolation": 1.0},
        {"t": 1.00, "end": 1.40, "cls": "snare", "strength": 0.9, "isolation": 1.0},
        {"t": 1.25, "end": 1.45, "cls": "hat_closed", "strength": 0.5, "isolation": 1.0},
        {"t": 3.50, "end": 3.90, "cls": "kick", "strength": 0.8, "isolation": 0.8},
        {"t": 4.00, "end": 4.40, "cls": "snare", "strength": 0.7, "isolation": 0.9},
    ],
}


def _entry(result):
    return {"id": "e1", "name": "Test Song", "result": result}


def test_kind_drums_serves_sample_pack(monkeypatch):
    result = {
        DRUM_HITS_RESULT_KEY: _HITS,
        "downbeats_s": [0.5 + i * 2.0 for i in range(9)],
        "duration_sec": 20.0,
    }
    monkeypatch.setattr(
        tone_forge_api, "_get_history_item",
        lambda eid: _entry(result) if eid == "e1" else None)

    r = client.get("/api/song/e1/kit", params={"kind": "drums"})
    assert r.status_code == 200
    kit = r.json()
    assert kit["packId"] == "drumkit-e1"
    assert kit["analysisId"] == "e1"
    assert kit["family"] == "percussion"
    names = [p["name"] for p in kit["pads"]]
    assert any(n.startswith("Kick") for n in names)
    one_shots = [p for p in kit["pads"] if not p["loopable"]]
    assert one_shots and all(
        p["stemSlice"]["stemRole"] == "drums" for p in one_shots)


def test_kind_drums_empty_hits_is_422(monkeypatch):
    result = {DRUM_HITS_RESULT_KEY: {"version": HITS_VERSION, "hits": []}}
    monkeypatch.setattr(
        tone_forge_api, "_get_history_item", lambda eid: _entry(result))
    r = client.get("/api/song/e1/kit", params={"kind": "drums"})
    assert r.status_code == 422


def test_kind_drums_unknown_song_is_404(monkeypatch):
    monkeypatch.setattr(
        tone_forge_api, "_get_history_item", lambda eid: None)
    r = client.get("/api/song/missing/kit", params={"kind": "drums"})
    assert r.status_code == 404
