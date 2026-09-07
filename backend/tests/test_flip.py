"""Auto-Flip (performance.flip) — pack shape, Swift-decodable pattern wire
format, and the core claim: the flip's drum steps are the SONG's own
per-class bar pattern, not a canned template.
"""
from __future__ import annotations

import pytest

from tone_forge.performance import flip
from tone_forge.performance.drum_kit import DRUM_HITS_RESULT_KEY, HITS_VERSION

BAR = 2.0  # 120 BPM, 4/4 → one bar = 2 s, one 16th = 0.125 s


def _result(n_bars=8):
    """Hits: kick on beats 1+3 (slots 0, 8), snare on 2+4 (slots 4, 12),
    hats on every 8th (even slots) — across n_bars bars from t=1.0."""
    hits = []
    downs = [1.0 + i * BAR for i in range(n_bars + 1)]
    for b in range(n_bars):
        t0 = downs[b]
        for slot in (0, 8):
            hits.append({"t": t0 + slot * BAR / 16, "end": t0 + slot * BAR / 16 + 0.3,
                         "cls": "kick", "strength": 1.0, "isolation": 1.0})
        for slot in (4, 12):
            hits.append({"t": t0 + slot * BAR / 16, "end": t0 + slot * BAR / 16 + 0.3,
                         "cls": "snare", "strength": 0.8, "isolation": 1.0})
        for slot in range(0, 16, 2):
            hits.append({"t": t0 + slot * BAR / 16, "end": t0 + slot * BAR / 16 + 0.1,
                         "cls": "hat_closed", "strength": 0.5, "isolation": 1.0})
    return {
        DRUM_HITS_RESULT_KEY: {"version": HITS_VERSION, "hits": hits},
        "downbeats_s": downs,
        "duration_sec": downs[-1] + 2.0,
    }


@pytest.fixture(scope="module")
def kit():
    return flip.build_flip("song1", _result())


def test_pack_shape(kit):
    assert kit["packId"] == "flip-song1"
    assert kit["manifestVersion"] == 2
    names = [p["name"] for p in kit["pads"]]
    assert "Kick" in names and "Snare" in names and "Hat Closed" in names
    assert [p["padIdx"] for p in kit["pads"]] == list(range(len(kit["pads"])))
    for p in kit["pads"]:
        assert p["stemSlice"]["endSec"] > p["stemSlice"]["startSec"]


def test_pattern_is_songs_own_groove(kit):
    pattern = kit["defaultSequence"]
    tracks = {t["name"]: t for t in pattern["tracks"]}

    def active(name):
        return [i for i, s in enumerate(tracks[name]["steps"])
                if s["velocity"] > 0]

    assert active("Kick") == [0, 8]
    assert active("Snare") == [4, 12]
    assert active("Hat Closed") == list(range(0, 16, 2))
    # Velocity carries the class's own dynamics (hats quieter than kicks).
    kick_v = tracks["Kick"]["steps"][0]["velocity"]
    hat_v = tracks["Hat Closed"]["steps"][0]["velocity"]
    assert kick_v > hat_v


def test_pattern_wire_format_swift_decodable(kit):
    """Pin the Swift-synthesized Codable shape: enum-with-associated-values
    as {"case": {label: value}}, UUID strings, step field names."""
    pattern = kit["defaultSequence"]
    assert pattern["stepCount"] == 16
    assert pattern["isLooping"] is True
    assert isinstance(pattern["id"], str) and len(pattern["id"]) == 36
    for t in pattern["tracks"]:
        ref = t["chopRef"]
        assert set(ref.keys()) == {"packPad"}
        assert ref["packPad"]["packId"] == kit["packId"]
        assert any(p["padIdx"] == ref["packPad"]["padIdx"] for p in kit["pads"])
        assert len(t["steps"]) == 16
        for s in t["steps"]:
            assert 0 <= s["velocity"] <= 1
            assert 0 < s["probability"] <= 1
        assert isinstance(t["id"], str) and len(t["id"]) == 36


def test_deterministic_pattern_id(kit):
    again = flip.build_flip("song1", _result())
    assert again["defaultSequence"]["id"] == kit["defaultSequence"]["id"]
    other = flip.build_flip("song2", _result())
    assert other["defaultSequence"]["id"] != kit["defaultSequence"]["id"]


def test_no_hits_raises():
    with pytest.raises(ValueError):
        flip.build_flip("x", {})


def test_no_downbeats_falls_back_to_universal_pattern():
    r = _result()
    r.pop("downbeats_s")
    kit = flip.build_flip("song3", r)
    tracks = {t["name"] for t in kit["defaultSequence"]["tracks"]}
    assert "Kick" in tracks  # fallback pattern still yields a playable beat
