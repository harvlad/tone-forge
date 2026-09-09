"""Borrow Beat (performance.borrow) — key parsing, harmonic scoring, and
donor ranking (tempo for drums, tempo+key for melodic stems)."""
from __future__ import annotations

import pytest

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


def test_parallel_major_minor_scores():
    # New: C major ↔ C minor share a tonic — modal borrow, not a clash.
    assert borrow._harmonic_score("C major", "C minor") == 0.85


# --- content-based harmony (chord ribbon → pitch-class histogram) ----------

def test_chord_pcs_parses_quality():
    assert borrow._chord_pcs("C") == {0, 4, 7}           # major triad
    assert borrow._chord_pcs("Am") == {9, 0, 4}          # A minor
    assert borrow._chord_pcs("G7") == {7, 11, 2, 5}      # dom7 adds b7
    assert borrow._chord_pcs("Cmaj7") == {0, 4, 7, 11}   # natural 7
    assert borrow._chord_pcs("F#dim") == {6, 9, 0}
    assert borrow._chord_pcs("C/E") == {0, 4, 7}         # slash bass ignored
    assert borrow._chord_pcs("N.C.") is None


def _chorded(tempo, key, syms):
    chords = [{"symbol": s, "start_s": i, "end_s": i + 1}
              for i, s in enumerate(syms)]
    return {"tempo_bpm": tempo, "detected_key": key,
            "downbeats_s": [0.0, 1.0, 2.0, 3.0], "chords": chords,
            "stems_paths": {"bass": "/x/bass.wav"}}


def test_content_harmony_prefers_shared_material():
    # Two songs with the SAME chords score higher than same-key-label songs
    # that share little chord content.
    tgt = _chorded(120, "C major", ["C", "G", "Am", "F"])
    same = _chorded(120, "C major", ["C", "G", "Am", "F"])
    poor = _chorded(120, "C major", ["C#dim", "Bbm", "Ebm", "Abm"])
    hi = borrow.harmonic_compat(tgt, same)
    lo = borrow.harmonic_compat(tgt, poor)
    assert hi > lo
    assert hi >= 0.9


def test_content_ranking_uses_target_result():
    tgt = _chorded(120, "C major", ["C", "G", "Am", "F"])
    entries = [
        {"id": "twin", "name": "twin",
         "result": _chorded(120, "C major", ["C", "G", "Am", "F"])},
        {"id": "weak", "name": "weak",
         "result": _chorded(120, "C major", ["C#", "F#", "B", "Ebm"])},
    ]
    c = borrow.borrow_candidates(entries, "me", "bass", 120.0,
                                 target_key="C major", target_result=tgt)
    assert c[0]["entryId"] == "twin"    # real content beats shared label


# --- section-cut loop spans -------------------------------------------------

def _sectioned(n_downs=17):
    downs = [float(i) for i in range(n_downs)]   # one bar per second
    return {
        "tempo_bpm": 120, "downbeats_s": downs, "duration_sec": float(n_downs),
        "sections": [
            {"type": "intro", "start_time": 0.0, "end_time": 4.0},
            {"type": "verse", "start_time": 4.0, "end_time": 8.0},
            {"type": "chorus", "start_time": 8.0, "end_time": 12.0},
            {"type": "verse", "start_time": 12.0, "end_time": 16.0},
        ],
    }


def test_section_spans_skip_intro_prefer_distinct():
    spans = borrow._section_spans(_sectioned(), borrow._LOOPS_PER_SOURCE)
    labels = [s[2] for s in spans]
    assert "intro" not in labels           # intro skipped (enough body)
    assert labels[:2] == ["verse", "chorus"]   # distinct types first
    for a, b, _ in spans:                  # bar-locked windows
        assert a == float(int(a)) and b == float(int(b)) and b > a


# --- round-2 regressions: loops must lock to tempo, stretch right direction --

def test_snap_window_length_is_tempo_locked():
    # bar_sec 2.0s (120 BPM) × 4 bars = 8.0s, regardless of how many downbeats
    # land in the section. The old code sized the window by the span between
    # downbeat INDICES, so sparse downbeats gave 3/6-bar loops that never
    # phase-locked ("twice as slow" / drift).
    dense = [float(i) for i in range(20)]
    win = borrow._snap_window(4.0, 8.0, dense, 4, 2.0, 40.0)
    assert win and abs((win[1] - win[0]) - 8.0) < 1e-6


def test_snap_window_same_length_regardless_of_downbeat_density():
    sparse = [4.0, 9.0, 15.0, 22.0]              # irregular, few downbeats
    dense = [float(i) for i in range(30)]
    w1 = borrow._snap_window(4.0, 20.0, sparse, 4, 2.0, 60.0)
    w2 = borrow._snap_window(4.0, 20.0, dense, 4, 2.0, 60.0)
    assert w1 and w2
    assert abs((w1[1] - w1[0]) - (w2[1] - w2[0])) < 1e-6   # identical length
    assert abs((w1[1] - w1[0]) - 8.0) < 1e-6


def test_section_spans_all_equal_length_after_tempo_lock():
    spans = borrow._section_spans(_sectioned(), 8)
    lens = [round(b - a, 3) for a, b, _ in spans]
    assert len(set(lens)) == 1                   # every loop the same length
    assert lens[0] == borrow._SECTION_BARS * (240.0 / 120.0)   # 4 bars @120


def test_time_stretch_direction():
    # tempo × mult ⇒ duration ÷ mult. Guards the inverted-rate + octave-fold
    # bug that played donors twice as slow. Works whether _time_stretch uses
    # ffmpeg atempo or the librosa fallback (both map mult→dur/mult).
    np = pytest.importorskip("numpy")
    sf = pytest.importorskip("soundfile")
    librosa = pytest.importorskip("librosa")
    sr = 22050
    rng = np.random.RandomState(0)
    seg = (rng.randn(sr, 2) * 0.1).astype("float32")   # 1.0 s of noise
    for mult in (1.25, 0.8):
        out = borrow._time_stretch(seg, sr, mult, np, sf, librosa)
        assert abs(out.shape[0] / sr - 1.0 / mult) < 0.06


def test_stem_label_prefixes_names():
    spans = [(0.0, 8.0, "verse"), (8.0, 16.0, "chorus")]
    names = borrow._label_names(spans)
    assert [f"Bass {n}" for n in names] == ["Bass Verse", "Bass Chorus"]


def test_section_spans_fallback_when_no_sections():
    r = {"tempo_bpm": 120,
         "downbeats_s": [float(i) for i in range(9)], "duration_sec": 9.0}
    spans = borrow._section_spans(r, 4)
    assert spans and all(lbl == "loop" for _a, _b, lbl in spans)


def test_label_names_number_repeats():
    spans = [(0, 1, "verse"), (1, 2, "chorus"), (2, 3, "verse")]
    assert borrow._label_names(spans) == ["Verse", "Chorus", "Verse 2"]
