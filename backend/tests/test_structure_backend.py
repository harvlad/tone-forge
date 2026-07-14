"""Tests for the All-In-One structure backend integration (task 11).

Covers:
* Harmonix label -> SectionType mapping
* SectionDetector.detect_sections_with_structure contract (labels,
  provenance marker, gap handling, degenerate fallback)
* structure._stage_stems demix-layout staging
* analyze_structure graceful degradation when allin1 is unavailable
* bundle read-path fixups leaving allin1 segmentations untouched
"""
import numpy as np
import pytest

from tone_forge.analysis.sections import (
    ArrangementSection,
    SectionDetector,
    SectionType,
    _structure_label_to_type,
)


# ---------------------------------------------------------------------------
# Label mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("label,expected", [
    ("start", SectionType.INTRO),
    ("intro", SectionType.INTRO),
    ("end", SectionType.OUTRO),
    ("outro", SectionType.OUTRO),
    ("break", SectionType.BREAKDOWN),
    ("bridge", SectionType.BRIDGE),
    ("inst", SectionType.INSTRUMENTAL),
    ("solo", SectionType.INSTRUMENTAL),
    ("verse", SectionType.VERSE),
    ("chorus", SectionType.CHORUS),
])
def test_structure_label_map(label, expected):
    assert _structure_label_to_type(label) is expected


def test_structure_label_map_case_and_whitespace():
    assert _structure_label_to_type(" Chorus ") is SectionType.CHORUS
    assert _structure_label_to_type("VERSE") is SectionType.VERSE


def test_structure_label_map_unknown():
    assert _structure_label_to_type("mystery") is SectionType.UNKNOWN
    assert _structure_label_to_type("") is SectionType.UNKNOWN


# ---------------------------------------------------------------------------
# detect_sections_with_structure
# ---------------------------------------------------------------------------


def _tone(duration_s: float, sr: int = 22050) -> np.ndarray:
    t = np.linspace(0, duration_s, int(duration_s * sr), endpoint=False)
    rng = np.random.default_rng(0)
    return (0.2 * np.sin(2 * np.pi * 220 * t)
            + 0.02 * rng.standard_normal(len(t))).astype(np.float32)


def test_detect_with_structure_applies_labels_and_provenance():
    sr = 22050
    y = _tone(30.0, sr)
    segments = [
        {"start": 0.0, "end": 8.0, "label": "intro"},
        {"start": 8.0, "end": 20.0, "label": "verse"},
        {"start": 20.0, "end": 30.0, "label": "chorus"},
    ]
    analysis = SectionDetector().detect_sections_with_structure(
        y, sr=sr, segments=segments, tempo=120.0,
    )
    types = [s.type for s in analysis.sections]
    assert types == [SectionType.INTRO, SectionType.VERSE, SectionType.CHORUS]
    assert all(s.label_source == "allin1" for s in analysis.sections)
    assert analysis.sections[0].start_time == pytest.approx(0.0)
    assert analysis.sections[-1].end_time == pytest.approx(30.0, abs=0.1)
    # to_dict carries the provenance marker for read-path gating.
    assert analysis.sections[0].to_dict()["label_source"] == "allin1"


def test_detect_with_structure_clips_to_duration():
    sr = 22050
    y = _tone(20.0, sr)
    segments = [
        {"start": 0.0, "end": 10.0, "label": "verse"},
        {"start": 10.0, "end": 45.0, "label": "chorus"},  # beyond audio
    ]
    analysis = SectionDetector().detect_sections_with_structure(
        y, sr=sr, segments=segments, tempo=120.0,
    )
    assert analysis.sections[-1].end_time <= 20.0 + 0.01


def test_detect_with_structure_empty_segments_falls_back():
    sr = 22050
    y = _tone(30.0, sr)
    analysis = SectionDetector().detect_sections_with_structure(
        y, sr=sr, segments=[], tempo=120.0,
    )
    # Falls back to the RMS-novelty detector: sections exist and none
    # carry the allin1 provenance marker.
    assert analysis.sections
    assert all(s.label_source == "" for s in analysis.sections)


def test_detect_with_structure_keeps_short_start_marker():
    # The Harmonix "start" silence marker is often ~0.3s; its end
    # boundary is the music-start boundary and must be preserved
    # (dropping it cost boundary F@0.5 on 9/24 SALAMI eval tracks).
    sr = 22050
    y = _tone(20.0, sr)
    segments = [
        {"start": 0.0, "end": 0.3, "label": "start"},
        {"start": 0.3, "end": 10.0, "label": "verse"},
        {"start": 10.0, "end": 20.0, "label": "chorus"},
    ]
    analysis = SectionDetector().detect_sections_with_structure(
        y, sr=sr, segments=segments, tempo=120.0,
    )
    types = [s.type for s in analysis.sections]
    assert types[0] is SectionType.INTRO
    assert analysis.sections[0].end_time == pytest.approx(0.3, abs=0.01)
    assert SectionType.VERSE in types and SectionType.CHORUS in types


# ---------------------------------------------------------------------------
# structure module
# ---------------------------------------------------------------------------


def test_analyze_structure_returns_none_when_unavailable(monkeypatch, tmp_path):
    import tone_forge.analysis.structure as structure

    monkeypatch.setattr(structure, "_ALLIN1_FAILED", True)
    wav = tmp_path / "mix.wav"
    wav.write_bytes(b"RIFF")
    assert structure.analyze_structure(wav) is None


def test_analyze_structure_missing_file(monkeypatch, tmp_path):
    import tone_forge.analysis.structure as structure

    monkeypatch.setattr(structure, "_ALLIN1_FAILED", False)
    monkeypatch.setattr(structure, "allin1_available", lambda: True)
    assert structure.analyze_structure(tmp_path / "nope.wav") is None


def _write_wav(path, seconds=0.1, sr=8000):
    import soundfile as sf

    t = np.zeros(int(seconds * sr), dtype=np.float32)
    sf.write(str(path), t, sr, subtype="PCM_16")
    return path


def test_stage_stems_full_set(tmp_path):
    from tone_forge.analysis.structure import _stage_stems

    stems = {
        name: _write_wav(tmp_path / f"{name}.wav")
        for name in ("bass", "drums", "other", "vocals")
    }
    demix = tmp_path / "demix"
    assert _stage_stems(stems, "mysong", demix) is True
    out = demix / "htdemucs" / "mysong"
    for name in ("bass", "drums", "other", "vocals"):
        assert (out / f"{name}.wav").exists()


def test_stage_stems_incomplete_returns_false(tmp_path):
    from tone_forge.analysis.structure import _stage_stems

    stems = {"bass": _write_wav(tmp_path / "bass.wav")}
    assert _stage_stems(stems, "mysong", tmp_path / "demix") is False


def test_stage_stems_sums_guitar_piano_into_other(tmp_path):
    import soundfile as sf

    from tone_forge.analysis.structure import _stage_stems

    sr = 8000
    n = 800

    def write(name, value):
        p = tmp_path / f"{name}.wav"
        sf.write(str(p), np.full(n, value, dtype=np.float32), sr,
                 subtype="FLOAT")
        return p

    stems = {
        "bass": write("bass", 0.0),
        "drums": write("drums", 0.0),
        "vocals": write("vocals", 0.0),
        "other": write("other", 0.1),
        "guitar": write("guitar", 0.2),
        "piano": write("piano", 0.3),
    }
    demix = tmp_path / "demix"
    assert _stage_stems(stems, "song", demix) is True
    summed, _ = sf.read(str(demix / "htdemucs" / "song" / "other.wav"))
    assert summed.mean() == pytest.approx(0.6, abs=0.01)


# ---------------------------------------------------------------------------
# Read-path gating
# ---------------------------------------------------------------------------


def _section_dict(start, end, stype, label_source=""):
    s = ArrangementSection(
        type=stype, start_time=start, end_time=end, confidence=1.0,
    )
    s.label_source = label_source
    return s.to_dict()


def test_read_fixups_leave_allin1_sections_untouched():
    from tone_forge.bundle_read_fixups import apply_bundle_read_fixups

    # A 90s chorus would normally trip Fix B (chorus_too_long) and be
    # split by Fix C / Fix 4 on read. With allin1 provenance the
    # boundaries and types must survive verbatim.
    sections = [
        _section_dict(0.0, 10.0, SectionType.INTRO, "allin1"),
        _section_dict(10.0, 100.0, SectionType.CHORUS, "allin1"),
        _section_dict(100.0, 120.0, SectionType.OUTRO, "allin1"),
    ]
    result = {
        "sections": sections,
        "chords": [
            {"start_s": float(i * 2), "end_s": float(i * 2 + 2),
             "symbol": ["C", "G", "Am", "F"][i % 4]}
            for i in range(60)
        ],
        "beats_s": [float(b) * 0.5 for b in range(240)],
        "energy_curve": [0.5] * 1200,
        "midi_stems": {},
    }
    apply_bundle_read_fixups(result)
    out = result["sections"]
    assert len(out) == 3
    assert [s["type"] for s in out] == ["intro", "chorus", "outro"]
    assert [round(s["end_time"], 1) for s in out] == [10.0, 100.0, 120.0]
    # Fix B advisory flag still applies (chorus 90s is suspicious).
    assert out[1]["duration_flag"] != ""


def test_read_fixups_still_process_heuristic_sections():
    from tone_forge.bundle_read_fixups import apply_bundle_read_fixups

    # Chorus is non-final: the final-chorus exemption in
    # flag_suspicious_durations must not apply.
    sections = [
        _section_dict(0.0, 10.0, SectionType.INTRO),
        _section_dict(10.0, 100.0, SectionType.CHORUS),
        _section_dict(100.0, 120.0, SectionType.OUTRO),
    ]
    result = {
        "sections": sections,
        "chords": [],
        "beats_s": [],
        "energy_curve": [],
        "midi_stems": {},
    }
    apply_bundle_read_fixups(result)
    # Fix B flags the over-long chorus on the legacy path too.
    assert result["sections"][1]["duration_flag"] != ""
