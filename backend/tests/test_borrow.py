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


# --- optional session key/BPM target (Borrow retarget) ----------------------

def test_transpose_steps_signed_shortest_octave_equivalent():
    # Up a fifth C→G is +7 semitones, but the shorter path is -5 (down a fourth).
    assert borrow._transpose_steps("C major", "G major") == -5
    # Down C→A is -3 (up 9 folds to the shorter -3).
    assert borrow._transpose_steps("C major", "A minor") == -3
    # Same tonic, different mode → NO transpose (mode is colour, not pitch).
    assert borrow._transpose_steps("G minor", "G major") == 0
    assert borrow._transpose_steps("C major", "C major") == 0
    # Tritone stays +6 (the +6/-6 boundary maps to +6).
    assert borrow._transpose_steps("C major", "F# major") == 6
    # Every result is within the [-6, +6] band.
    keys = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    for kd in keys:
        for kt in keys:
            n = borrow._transpose_steps(f"{kd} major", f"{kt} major")
            assert -6 <= n <= 6
    # Unparseable either side → 0 (don't guess).
    assert borrow._transpose_steps(None, "G minor") == 0
    assert borrow._transpose_steps("C major", "garbage") == 0


def test_cache_key_default_is_byte_identical():
    # The whole point of "default = today": with no target_key the cache
    # filename must be exactly what it was before the feature (target_key
    # omitted from the hashed string entirely).
    span = (4.0, 12.0)
    base = borrow._cache_key("song1", "bass", 120.0, span)
    same = borrow._cache_key("song1", "bass", 120.0, span, None)
    # Reproduce the pre-feature hash string explicitly to pin the format.
    import hashlib
    legacy = (f"song1|bass|{round(120.0, 2)}|{round(span[0], 3)}|"
              f"{round(span[1], 3)}|v{borrow.BORROW_VERSION}")
    legacy_fn = f"borrow_{hashlib.sha1(legacy.encode()).hexdigest()[:20]}.wav"
    assert base == same == legacy_fn


def test_cache_key_targeted_caches_separately():
    span = (4.0, 12.0)
    base = borrow._cache_key("song1", "bass", 120.0, span)
    keyed = borrow._cache_key("song1", "bass", 120.0, span, "G minor")
    keyed2 = borrow._cache_key("song1", "bass", 120.0, span, "A minor")
    retimed = borrow._cache_key("song1", "bass", 140.0, span)
    # A transposed render, a differently-keyed render, and a retimed render all
    # land in distinct cache slots; none collides with the untargeted default.
    assert len({base, keyed, keyed2, retimed}) == 4


def test_pitch_shift_no_op_at_zero_returns_same_object():
    np = pytest.importorskip("numpy")
    librosa = pytest.importorskip("librosa")
    seg = (np.random.RandomState(0).randn(2048, 2) * 0.1).astype("float32")
    # 0 steps must be a true no-op (identity object) — the default pays nothing.
    assert borrow._pitch_shift(seg, 22050, 0, np, librosa) is seg


def test_pitch_shift_preserves_length_and_changes_content():
    np = pytest.importorskip("numpy")
    librosa = pytest.importorskip("librosa")
    sr = 22050
    # A pure tone so the shift is audible/measurable, not just noise.
    t = np.arange(sr) / sr
    tone = np.sin(2 * np.pi * 220.0 * t).astype("float32")
    seg = np.stack([tone, tone], axis=1)
    out = borrow._pitch_shift(seg, sr, 5, np, librosa)
    assert out.shape[1] == 2
    # Phase vocoder is length-preserving (keeps bar-lock intact after stretch).
    assert abs(out.shape[0] - seg.shape[0]) < 0.02 * sr
    assert not np.allclose(out[: seg.shape[0]], seg[: out.shape[0]])


def test_render_default_equals_host_path(tmp_path, monkeypatch):
    """(a) No target params ⇒ identical render to the host-tempo path. We prove
    it at the render layer: the untargeted render and an explicit
    target_key=None render produce the same cache file, and a real transpose
    produces a different one — the default is never silently altered."""
    np = pytest.importorskip("numpy")
    pytest.importorskip("soundfile")
    pytest.importorskip("librosa")
    import soundfile as sf

    monkeypatch.setenv("TONEFORGE_BORROW_CACHE", str(tmp_path))

    sr = 22050
    stem_wav = tmp_path / "bass.wav"
    tone = np.sin(2 * np.pi * 110.0 * (np.arange(16 * sr) / sr)).astype("float32")
    sf.write(str(stem_wav), np.stack([tone, tone], axis=1), sr)

    result = {
        "tempo_bpm": 120, "detected_key": "C major",
        "downbeats_s": [float(i) for i in range(17)], "duration_sec": 17.0,
        "sections": [
            {"type": "verse", "start_time": 0.0, "end_time": 8.0},
            {"type": "chorus", "start_time": 8.0, "end_time": 16.0},
        ],
        "stems_paths": {"bass": str(stem_wav)},
    }

    def _fake_materialize(res, td, roles=None):
        return {"bass": stem_wav}

    monkeypatch.setattr("tone_forge.stem_fetch.materialize_stems",
                        _fake_materialize)

    # Default (no key) vs explicit None: byte-identical set of cache files.
    pads_default = borrow.render_section_loops(
        "donorX", result, "bass", 120.0, donor_stem="bass",
        source_tag="donor")
    pads_none = borrow.render_section_loops(
        "donorX", result, "bass", 120.0, donor_stem="bass",
        source_tag="donor", target_key=None)
    assert pads_default and pads_none
    files_default = {p["sampleFile"] for p in pads_default}
    files_none = {p["sampleFile"] for p in pads_none}
    assert files_default == files_none

    # (c) A donor transpose to G minor writes DIFFERENT cache files (separate
    # slot) and does not disturb the default renders that already exist.
    pads_keyed = borrow.render_section_loops(
        "donorX", result, "bass", 120.0, donor_stem="bass",
        source_tag="donor", target_key="G minor")
    files_keyed = {p["sampleFile"] for p in pads_keyed}
    assert files_keyed and files_keyed.isdisjoint(files_default)
    # Every default file still present & untouched.
    for f in files_default:
        assert (tmp_path / f).exists()


def test_host_initial_pads_never_transpose(tmp_path, monkeypatch):
    """(c') source_tag='initial' (the host) is NEVER transposed even when a
    target_key is passed — the play-along recording stays TRUE. So an initial
    render with target_key='G minor' hits the SAME cache file as the default."""
    np = pytest.importorskip("numpy")
    pytest.importorskip("soundfile")
    pytest.importorskip("librosa")
    import soundfile as sf

    monkeypatch.setenv("TONEFORGE_BORROW_CACHE", str(tmp_path))
    sr = 22050
    stem_wav = tmp_path / "bass.wav"
    tone = np.sin(2 * np.pi * 110.0 * (np.arange(16 * sr) / sr)).astype("float32")
    sf.write(str(stem_wav), np.stack([tone, tone], axis=1), sr)
    result = {
        "tempo_bpm": 120, "detected_key": "C major",
        "downbeats_s": [float(i) for i in range(17)], "duration_sec": 17.0,
        "sections": [{"type": "verse", "start_time": 0.0, "end_time": 8.0},
                     {"type": "chorus", "start_time": 8.0, "end_time": 16.0}],
        "stems_paths": {"bass": str(stem_wav)},
    }
    monkeypatch.setattr("tone_forge.stem_fetch.materialize_stems",
                        lambda res, td, roles=None: {"bass": stem_wav})

    host_plain = {p["sampleFile"] for p in borrow.render_section_loops(
        "hostX", result, "bass", 120.0, donor_stem="bass",
        source_tag="initial")}
    host_keyed = {p["sampleFile"] for p in borrow.render_section_loops(
        "hostX", result, "bass", 120.0, donor_stem="bass",
        source_tag="initial", target_key="G minor")}
    assert host_plain and host_plain == host_keyed


def test_target_bpm_overrides_stretch_tempo(tmp_path, monkeypatch):
    """(b) target_bpm drives the stretch, OCTAVE-FOLDED (BORROW_VERSION 5):
    rendering the same 100 BPM donor to 120 vs 140 produces loops of different
    DURATION and separate cache files. 120 folds to ratio 1.2 (m=1); 140 folds
    to 0.7 (m=0.5, half-time) so the WSOLA stretch stays inside its clean band
    instead of a 1.4x shred — so the 140 loop is LONGER (half-time), not
    shorter. The point: target_bpm changes the render, folded to the nearest
    octave so the stretch never blows up."""
    np = pytest.importorskip("numpy")
    pytest.importorskip("soundfile")
    pytest.importorskip("librosa")
    import soundfile as sf

    monkeypatch.setenv("TONEFORGE_BORROW_CACHE", str(tmp_path))
    sr = 22050
    stem_wav = tmp_path / "bass.wav"
    tone = np.sin(2 * np.pi * 110.0 * (np.arange(16 * sr) / sr)).astype("float32")
    sf.write(str(stem_wav), np.stack([tone, tone], axis=1), sr)
    # Donor at 100 BPM so a target of 120 vs 140 gives clearly different ratios.
    result = {
        "tempo_bpm": 100, "detected_key": "C major",
        "downbeats_s": [float(i) for i in range(17)], "duration_sec": 17.0,
        "sections": [{"type": "verse", "start_time": 0.0, "end_time": 8.0},
                     {"type": "chorus", "start_time": 8.0, "end_time": 16.0}],
        "stems_paths": {"bass": str(stem_wav)},
    }
    monkeypatch.setattr("tone_forge.stem_fetch.materialize_stems",
                        lambda res, td, roles=None: {"bass": stem_wav})

    p120 = borrow.render_section_loops(
        "donorX", result, "bass", 120.0, donor_stem="bass", source_tag="donor")
    p140 = borrow.render_section_loops(
        "donorX", result, "bass", 140.0, donor_stem="bass", source_tag="donor")
    assert p120 and p140
    f120 = tmp_path / p120[0]["sampleFile"]
    f140 = tmp_path / p140[0]["sampleFile"]
    assert f120.name != f140.name          # separate cache slots per target BPM
    d120, _ = sf.read(str(f120))
    d140, _ = sf.read(str(f140))
    # Octave-folded: 100→120 stretches (ratio 1.2, shorter); 100→140 folds to
    # half-time (ratio 0.7, LONGER) so the stretch stays clean. Target BPM still
    # changes the render — just folded to the nearest octave.
    assert d120.shape[0] != d140.shape[0]
    assert d140.shape[0] > d120.shape[0]


# --- curated-kit borrow (render_kit_loops) ----------------------------------
# Borrow now serves each song's AutoKit (~12 stem-spread, quality-gated pads),
# NOT _LOOPS_PER_SOURCE section loops per stem per song. These pin that new
# contract: kit selection is REUSED (serve.kit_payload) and the loop regions run
# through the SAME conform DSP (fold ratio + stretch + pitch-shift + cache).

def _kit_borrow_fixture(tmp_path, monkeypatch, kit_pads, *, host_key="C major"):
    """Wire render_kit_loops for a test: a 16 s tone as every stem, a fake
    curated kit (serve.kit_payload), and a materialize_stems that hands the
    tone back for each requested role. Returns the source `result`."""
    np = pytest.importorskip("numpy")
    pytest.importorskip("soundfile")
    pytest.importorskip("librosa")
    import soundfile as sf

    monkeypatch.setenv("TONEFORGE_BORROW_CACHE", str(tmp_path))
    sr = 22050
    stem_wav = tmp_path / "stem.wav"
    tone = np.sin(2 * np.pi * 110.0 * (np.arange(16 * sr) / sr)).astype("float32")
    sf.write(str(stem_wav), np.stack([tone, tone], axis=1), sr)

    roles = sorted({p["stemSlice"]["stemRole"] for p in kit_pads})
    result = {
        "tempo_bpm": 120, "detected_key": host_key,
        "downbeats_s": [float(i) for i in range(17)], "duration_sec": 17.0,
        "stems_paths": {r: str(stem_wav) for r in roles},
    }

    monkeypatch.setattr(
        "tone_forge.performance.serve.kit_payload",
        lambda sid, res, skill="intermediate", pads=12: {"pads": kit_pads})
    monkeypatch.setattr(
        "tone_forge.stem_fetch.materialize_stems",
        lambda res, td, roles=None: {r: stem_wav for r in (roles or [])})
    return result


def _curated_kit():
    # A stem-spread kit: drums + bass + harmonic + vocal — what a direct load
    # gives. Deliberately SMALL (4 pads), the whole point vs the 8×4=32 flood.
    return [
        {"name": "Drums beat", "category": "DRUMS", "loopable": True,
         "loopScore": 0.82, "performanceScore": 0.71,
         "stemSlice": {"stemRole": "drums"},
         "loopStartSec": 0.0, "loopEndSec": 4.0},
        {"name": "Bass groove Verse", "category": "BASS", "loopable": True,
         "loopScore": 0.64, "performanceScore": 0.55,
         "stemSlice": {"stemRole": "bass"},
         "loopStartSec": 0.0, "loopEndSec": 4.0},
        {"name": "Guitar riff Chorus", "category": "CHORDS", "loopable": True,
         "loopScore": 0.70,
         "stemSlice": {"stemRole": "guitar_center"},
         "loopStartSec": 4.0, "loopEndSec": 8.0},
        {"name": "Vocal", "category": "VOCAL", "loopable": True,
         "loopScore": 0.51,
         "stemSlice": {"stemRole": "vocals"},
         "loopStartSec": 8.0, "loopEndSec": 12.0},
    ]


def test_kit_borrow_returns_curated_kit_not_section_flood(tmp_path, monkeypatch):
    """The core new contract: a borrow yields the DONOR's curated auto-kit —
    small, stem-spread, source-tagged, each pad conformed — instead of
    _LOOPS_PER_SOURCE loops per stem. One pad per kit entry (4), not 32."""
    kit = _curated_kit()
    result = _kit_borrow_fixture(tmp_path, monkeypatch, kit)

    pads = borrow.render_kit_loops(
        "donorX", result, 120.0, source_tag="donor",
        target_key="C major", source_name="Donor Song")

    # Curated, not flooded: one pad per kit entry, far under 8-per-stem.
    assert len(pads) == len(kit) == 4
    assert len(pads) < borrow._LOOPS_PER_SOURCE * 4
    # Stem-spread carried through so clients colour by category.
    assert {p["stem"] for p in pads} == {"drums", "bass", "other", "vocals"}
    # Every pad is donor-tagged, song-labelled, score- and file-backed.
    for p in pads:
        assert p["source"] == "donor"
        assert p["sourceName"] == "Donor Song"
        assert p["sampleFile"].startswith("borrow_") and p["sampleFile"].endswith(".wav")
        assert (tmp_path / p["sampleFile"]).exists()
        assert isinstance(p["loopScore"], (int, float))
    # Kit scores (loopScore/performanceScore) flow through for the compact
    # (16) best-of-both ranking — was a flat 1.0 in the old section path.
    by_name = {p["name"]: p for p in pads}
    assert by_name["Drums beat"]["loopScore"] == 0.82
    assert by_name["Drums beat"]["performanceScore"] == 0.71


def test_kit_borrow_pitchless_drums_conform_harmonic(tmp_path, monkeypatch):
    """Per-pad key conform: with a donor target_key, harmonic/melodic pads
    transpose (keyed cache slot) while DRUMS stay pitchless (untargeted slot) —
    the gate lives PER PAD because one kit mixes stems. C major → G minor is a
    real ±5-semitone move, so the two paths must produce distinct cache files."""
    kit = _curated_kit()
    result = _kit_borrow_fixture(tmp_path, monkeypatch, kit, host_key="C major")

    pads = borrow.render_kit_loops(
        "donorX", result, 120.0, source_tag="donor",
        target_key="G minor", source_name="Donor")
    by_name = {p["name"]: p for p in pads}

    # Drums: pitchless → the untargeted (default) cache filename.
    drums_fn = by_name["Drums beat"]["sampleFile"]
    assert drums_fn == borrow._cache_key("donorX", "drums", 120.0, (0.0, 4.0))
    # Bass: harmonic donor pad → transposed → the KEYED cache slot (distinct).
    bass_fn = by_name["Bass groove Verse"]["sampleFile"]
    assert bass_fn == borrow._cache_key(
        "donorX", "bass", 120.0, (0.0, 4.0), "G minor")
    assert bass_fn != borrow._cache_key("donorX", "bass", 120.0, (0.0, 4.0))


def test_kit_borrow_host_initial_never_transposes(tmp_path, monkeypatch):
    """The host's own kit (source_tag='initial') is NEVER transposed even if a
    target_key is passed — the play-along recording stays TRUE. So its harmonic
    pads land in the untargeted cache slot, identical to no-key."""
    kit = _curated_kit()
    result = _kit_borrow_fixture(tmp_path, monkeypatch, kit, host_key="C major")

    host = borrow.render_kit_loops(
        "hostX", result, 120.0, source_tag="initial",
        target_key="G minor", source_name="This song")
    bass = next(p for p in host if p["stem"] == "bass")
    # Untargeted filename — no transpose despite the target_key.
    assert bass["sampleFile"] == borrow._cache_key(
        "hostX", "bass", 120.0, (0.0, 4.0))
    assert all(p["colorHint"] == borrow._COLOR_INITIAL for p in host)


def test_kit_borrow_empty_kit_yields_no_pads(tmp_path, monkeypatch):
    """A song whose kit builder returns nothing borrows nothing (the handler
    then 422s) — never a crash."""
    result = _kit_borrow_fixture(tmp_path, monkeypatch, _curated_kit())
    monkeypatch.setattr(
        "tone_forge.performance.serve.kit_payload",
        lambda sid, res, skill="intermediate", pads=12: {"pads": []})
    assert borrow.render_kit_loops("d", result, 120.0, source_tag="donor") == []


def test_key_distance_ranking(tmp_path, monkeypatch):
    """(d) With an explicit target_key, candidates rank by key relationship to
    THAT key: a donor already in the target key beats a fifth-away donor, which
    beats a semitone-away one; a tritone clash is dropped entirely."""
    entries = [
        _entry("exact", 120, "G minor", ["bass"]),      # == target
        _entry("fifth", 120, "D minor", ["bass"]),      # fifth from G
        _entry("semi", 120, "G# minor", ["bass"]),      # semitone nudge
        _entry("clash", 120, "C# minor", ["bass"]),     # tritone from G → drop
    ]
    c = borrow.borrow_candidates(entries, "me", "bass", 120.0,
                                 target_key="G minor")
    ids = [x["entryId"] for x in c]
    assert ids[0] == "exact"
    assert "clash" not in ids
    assert ids.index("fifth") < ids.index("semi")   # fifth outranks semitone
