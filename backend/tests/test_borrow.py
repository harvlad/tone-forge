"""Borrow Beat (performance.borrow) — key parsing, harmonic scoring, and
donor ranking (tempo for drums, tempo+key for melodic stems)."""
from __future__ import annotations

from tone_forge.performance import borrow


def test_key_parse():
    assert borrow._parse_key("C# minor") == (1, True)
    assert borrow._parse_key("D mixolydian") == (2, False)   # major-quality
    assert borrow._parse_key("A aeolian") == (9, True)
    assert borrow._parse_key("garbage") is None
    assert borrow._parse_key(None) is None


def test_harmonic_score():
    assert borrow._harmonic_score("C# minor", "C# minor") == 1.0
    assert borrow._harmonic_score("A minor", "C major") == 0.9   # relative
    assert borrow._harmonic_score("C major", "G major") == 0.8   # fifth
    assert borrow._harmonic_score("C major", "C# major") == 0.5  # semitone
    assert borrow._harmonic_score("C major", "F# major") == 0.0  # tritone clash
    assert borrow._harmonic_score(None, "C major") == 0.4        # unknown


def _entry(eid, tempo, key, stems):
    return {"id": eid, "name": eid, "result": {
        "tempo_bpm": tempo, "detected_key": key,
        "downbeats_s": [0.0, 1.0, 2.0, 3.0],
        "stems_paths": {s: f"/x/{s}.wav" for s in stems}}}


def test_drums_ranked_by_tempo_only():
    entries = [
        _entry("close", 118, "F# major", ["drums"]),
        _entry("far", 175, "C minor", ["drums"]),      # >50% off, octave-fold ok? 120/175=0.686 → *2=1.37 no; *0.5=... skip
        _entry("me", 120, "C major", ["drums"]),
    ]
    c = borrow.borrow_candidates(entries, "me", "drums", 120.0,
                                 target_key="C major")
    ids = [x["entryId"] for x in c]
    assert ids[0] == "close"       # 118 nearest 120
    assert "me" not in ids


def test_melodic_gated_by_key():
    entries = [
        _entry("relmatch", 120, "A minor", ["bass"]),   # relative to C major
        _entry("clash", 120, "F# major", ["bass"]),      # tritone → excluded
        _entry("me", 120, "C major", ["bass"]),
    ]
    c = borrow.borrow_candidates(entries, "me", "bass", 120.0,
                                 target_key="C major")
    ids = [x["entryId"] for x in c]
    assert "relmatch" in ids       # harmonically compatible
    assert "clash" not in ids      # tritone clash dropped
    assert c[0]["harmonic"] >= 0.9


def test_other_stem_alias_resolves():
    entries = [_entry("g", 120, "C major", ["guitar_center"])]
    c = borrow.borrow_candidates(entries, "me", "other", 120.0,
                                 target_key="C major")
    assert c and c[0]["donorStem"] == "guitar_center"
