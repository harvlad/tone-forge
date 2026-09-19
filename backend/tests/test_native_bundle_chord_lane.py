"""Native bundle chord-lane plumbing — /api/song/{id}/bundle.

Locks the server-side half of the lane-parity fix: the mobile/desktop
bundle's ``timeline.chords`` must carry the RICHEST harmonic lane
(mirroring the web client's ``_richestChordLane`` default) instead of
the sparse legacy "other" residual, and must expose the per-stem dict
+ real chord confidence additively.

Hermetic — monkeypatches the history lookup and the R2 stem hooks so
no network or disk is touched (same pattern as test_attribution_meta).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

import tone_forge_api as api  # noqa: E402

client = TestClient(api.app)


def _timeline_for(result: dict, monkeypatch) -> dict:
    entry = {"id": "test-entry", "name": "Cross Bones Style", "result": result}
    monkeypatch.setattr(api, "_get_history_item", lambda _id: entry)
    monkeypatch.setattr(api, "_maybe_upload_stems_to_r2", lambda *_a, **_k: False)
    monkeypatch.setattr(api, "_refresh_r2_stem_urls", lambda *_a, **_k: None)
    resp = client.get("/api/song/test-entry/bundle")
    assert resp.status_code == 200, resp.text
    return resp.json()["timeline"]


def _cross_bones_result() -> dict:
    guitar = [
        {"start_s": t, "end_s": t + 8.85, "symbol": s, "confidence": 0.8}
        for t, s in [
            (0.0, "C#"), (8.85, "F#"), (17.7, "G#"), (26.55, "A#m"),
            (35.4, "C#"), (44.25, "F#"), (53.1, "G#"), (61.95, "D#m"),
            (70.8, "C#"), (79.65, "F#"), (88.5, "G#"), (97.35, "B"),
            (106.2, "C#"),
        ]
    ]
    return {
        "duration_sec": 120.0,
        # Legacy flat lane = the sparse "other" residual (~22s).
        "chords": [{"start_s": 0.0, "end_s": 22.0,
                    "symbol": "C#", "confidence": 0.5}],
        "chords_by_stem": {
            "other": [{"start_s": 0.0, "end_s": 22.0,
                       "symbol": "C#", "confidence": 0.5}],
            "guitar": guitar,
            "bass": [{"start_s": 0.0, "end_s": 40.0,
                      "symbol": "C#", "confidence": 0.6}],
            "vocals": [{"start_s": 0.0, "end_s": 200.0,
                        "symbol": "X", "confidence": 0.9}],
        },
    }


def test_bundle_timeline_uses_richest_guitar_lane(monkeypatch):
    tl = _timeline_for(_cross_bones_result(), monkeypatch)
    # The flat chords array is now the guitar lane (13 regions), NOT the
    # 1-region "other" residual it used to ship.
    assert len(tl["chords"]) == 13
    assert tl["chordLaneStem"] == "guitar"
    # First region is the guitar lane's, not the residual's 22s block.
    assert tl["chords"][0]["end"] < 10.0


def test_bundle_timeline_carries_confidence(monkeypatch):
    tl = _timeline_for(_cross_bones_result(), monkeypatch)
    assert all("confidence" in c for c in tl["chords"])
    assert tl["chords"][0]["confidence"] == 0.8


def test_bundle_timeline_exposes_by_stem_excluding_non_harmonic(monkeypatch):
    tl = _timeline_for(_cross_bones_result(), monkeypatch)
    by_stem = tl["chordsByStem"]
    assert set(by_stem.keys()) == {"other", "guitar", "bass"}
    # vocals/drums never offered to the native lane picker.
    assert "vocals" not in by_stem
    assert len(by_stem["guitar"]) == 13


def test_bundle_legacy_result_falls_back_to_flat_lane(monkeypatch):
    """A pre-per-stem result (only ``chords``) must still work: the flat
    lane ships and chordLaneStem is None."""
    result = {
        "duration_sec": 8.0,
        "chords": [
            {"start_s": 0.0, "end_s": 4.0, "symbol": "Am", "confidence": 0.9},
            {"start_s": 4.0, "end_s": 8.0, "symbol": "F", "confidence": 0.7},
        ],
    }
    tl = _timeline_for(result, monkeypatch)
    assert len(tl["chords"]) == 2
    assert tl["chordLaneStem"] is None
    assert tl["chordsByStem"] == {}
    assert tl["chords"][0]["symbol"] == "Am"


def test_bundle_no_chords_at_all(monkeypatch):
    tl = _timeline_for({"duration_sec": 3.0}, monkeypatch)
    assert tl["chords"] == []
    assert tl["chordLaneStem"] is None
    assert tl["chordsByStem"] == {}
