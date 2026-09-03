"""Performance Intelligence — Unified Musical Graph tests.

Covers the pure/synthetic path (no real audio stack): grid snapping + loop
enumeration, loop-confidence ordering, phrase cutting, pattern recurrence, the
end-to-end builder with an injected stem loader, cache determinism, and kit
assembly. The librosa-backed onset upgrade is exercised only on the backend env.
"""
import numpy as np
import pytest

from tone_forge.performance.grid import MusicalGrid
from tone_forge.performance.graph import ContentType, MusicalGraph, PerformanceAsset, Phrase
from tone_forge.performance.loop_analyzer import LoopAnalyzer
from tone_forge.performance.phrase_analyzer import PhraseAnalyzer
from tone_forge.performance.pattern_discovery import PatternDiscovery
from tone_forge.performance.builder import PerformanceBuilder, to_motifs
from tone_forge.performance.kit_builder import AutoKitBuilder
from tone_forge.performance.cache import GraphCache

BPM = 120
BP = 60 / BPM


def _grid(dur=32.0):
    beats = [i * BP for i in range(int(dur / BP) + 1)]
    downbeats = [i * 4 * BP for i in range(int(dur / (4 * BP)) + 1)]
    return MusicalGrid(beats, downbeats, BPM, (4, 4), dur)


def test_grid_snap_and_loop_windows():
    g = _grid()
    assert g.beat_period_s == pytest.approx(0.5)
    assert g.snap_to_bar(3.3) == pytest.approx(4.0)
    pos = g.make_pos(4.1, 8.05, snap="bar")
    assert pos.is_bar_aligned and pos.length_bars == pytest.approx(2.0)
    wins = g.loop_windows(0, 16)
    lens = {w.length_bars for w in wins}
    assert {1.0, 2.0, 4.0}.issubset(lens)  # musically-sane bar lengths only


def test_loop_confidence_orders_clean_above_chopped_and_oneshot():
    sr = 22050
    g = _grid()
    n = int(sr * 32)
    t = np.arange(n) / sr
    dur = 4 * BP * 2
    cyc = round(220 * dur) / dur
    la = LoopAnalyzer()
    clean = la.analyze(0.5 * np.sin(2 * np.pi * cyc * t), sr, g.make_pos(0, dur, "bar"))
    chopped = la.analyze((0.1 + 0.9 * t / 32) * np.sin(2 * np.pi * 333 * t), sr, g.make_pos(1.3, 1.3 + dur, "beat"))
    os = np.zeros(n); os[: int(sr * 0.3)] = np.random.RandomState(0).randn(int(sr * 0.3)) * 0.4
    oneshot = la.analyze(os, sr, g.make_pos(0, dur, "bar"))
    assert clean.confidence > chopped.confidence > oneshot.confidence


def _riff(freqs, length, sr):
    t = np.arange(int(sr * length)) / sr
    y = np.zeros_like(t)
    for k, f in enumerate(freqs):
        a, b = int(len(t) * k / len(freqs)), int(len(t) * (k + 1) / len(freqs))
        y[a:b] = 0.35 * np.sin(2 * np.pi * f * t[a:b])
    return y


def test_phrase_cutting_is_grid_aligned():
    sr = 22050
    g = _grid()
    y = np.concatenate([_riff([220, 262, 294, 330], 4 * BP * 2, sr)] * 8)[: int(sr * 32)]
    phrases = PhraseAnalyzer().analyze(y, sr, g, "other", [(0, 16, "verse"), (16, 32, "chorus")])
    assert phrases
    for p in phrases:  # every phrase is a whole number of bars, on a downbeat
        assert p.pos.is_bar_aligned
        assert p.pos.length_bars >= 1


def test_pattern_recurrence_detected():
    sr = 22050
    g = _grid()
    y = np.concatenate([_riff([220, 262, 294, 330], 4 * BP * 2, sr)] * 8)[: int(sr * 32)]
    phrases = PhraseAnalyzer().analyze(y, sr, g, "other", [(0, 32, "verse")])
    pats, _ = PatternDiscovery().discover(y, sr, "other", phrases)
    assert pats
    assert max(p.recurrence_count for p in pats) >= 2  # the riff repeats


def _fixture_build(tmp_root):
    sr = 22050
    dur = 32.0
    beats = [i * BP for i in range(64)]
    downbeats = [i * 4 * BP for i in range(16)]
    other = np.concatenate([_riff([220, 262, 294, 330], 4 * BP * 2, sr)] * 8)[: int(sr * dur)]
    bass = np.concatenate([_riff([65, 65, 73, 55], 4 * BP * 2, sr)] * 8)[: int(sr * dur)]
    drums = np.random.RandomState(3).randn(int(sr * dur)) * 0.15
    stems = {"other": other, "bass": bass, "drums": drums}
    result = {
        "tempo_bpm": BPM, "beats_s": beats, "downbeats_s": downbeats,
        "time_signature": [4, 4], "duration_sec": dur,
        "sections": [{"start_s": 0, "end_s": 16, "label": "verse"},
                     {"start_s": 16, "end_s": 32, "label": "chorus"}],
        "stems_paths": {"other": "other", "bass": "bass", "drums": "drums"},
    }
    pb = PerformanceBuilder(stem_loader=lambda p: (stems[p], sr), cache=GraphCache(root=tmp_root))
    return pb, result


def test_builder_end_to_end_and_cache_determinism(tmp_path):
    pb, result = _fixture_build(str(tmp_path))
    g = pb.build(result, song_id="s", content_hash="h")
    assert g.phrases and g.loops and g.patterns and g.assets
    assert g.graph_hash
    # ranked best-first
    scores = [a.performance_score for a in g.ranked_assets()]
    assert scores == sorted(scores, reverse=True)
    # cache hit → identical
    g2 = pb.build(result, song_id="s", content_hash="h")
    assert g2.graph_hash == g.graph_hash and len(g2.assets) == len(g.assets)
    # motifs derived from patterns
    assert len(to_motifs(g)) == len(g.patterns)


def test_auto_kit_builder_emits_sample_pack(tmp_path):
    pb, result = _fixture_build(str(tmp_path))
    g = pb.build(result, song_id="s", content_hash="h")
    kit = AutoKitBuilder().build(g, skill="intermediate", pads=8)
    assert kit["manifestVersion"] == 2
    assert 1 <= len(kit["pads"]) <= 8
    p0 = kit["pads"][0]
    assert "stemSlice" in p0 and "loopScore" in p0 and "contentType" in p0
    # provenance is a STRING on the wire (SamplePack.provenance: String?) — a
    # dict here fails the whole kit decode in the app with a typeMismatch.
    assert isinstance(kit["provenance"], str)
    assert "performance_intelligence" in kit["provenance"]


def _asset(stem, ctype, *, start, loop_conf, score, pattern=None, difficulty=0.3):
    """A PerformanceAsset positioned on the bar grid, with scores pinned."""
    return PerformanceAsset(
        stem=stem,
        source_id=f"{stem}-{start}",
        pos=_grid().make_pos(start, start + 8 * BP, snap="bar"),
        content_type=ctype,
        performance_score=score,
        difficulty=difficulty,
        loopable=loop_conf >= 0.55,
        loop_confidence=loop_conf,
        pattern_id=pattern,
    ).with_id()


def test_drum_anchor_survives_the_usable_gate():
    """A drums asset scoring below `usable` still earns its pad.

    Regression for kits arriving with no drum pad at all. The anchor used to
    select from the post-gate pool, so percussion — which scores worst on
    exactly the two numbers that gate cuts on — was already discarded by the
    time the anchor looked for it. Drums here sit under BOTH thresholds
    (loop_confidence <= 0.2 and performance_score <= 0.4) while the melodic
    material clears them comfortably.
    """
    drums = _asset("drums", ContentType.RHYTHM_LOOP, start=8.0,
                   loop_conf=0.12, score=0.28)
    others = [
        _asset("other", ContentType.CHORD_LOOP, start=16.0, loop_conf=0.8, score=0.9),
        _asset("bass", ContentType.BASS_GROOVE, start=24.0, loop_conf=0.7, score=0.8),
        _asset("other", ContentType.LEAD_LOOP, start=32.0, loop_conf=0.75, score=0.75),
    ]
    g = MusicalGraph(
        song_id="s", content_hash="h", module_version="test",
        config_hash="cfg", grid_tempo_bpm=BPM, time_signature=(4, 4),
        assets=tuple([drums] + others),
    )

    kit = AutoKitBuilder().build(g, skill="intermediate", pads=4)
    cats = [p.get("category") for p in kit["pads"]]
    assert "DRUMS" in cats, f"kit lost the only drums asset: {cats}"


def test_percussion_loop_confidence_ignores_harmonic_carryover():
    """Percussion is scored on grid + level, not on head/tail tone match.

    A bar of drums that opens on a kick and closes on a hat has almost no
    spectral carry-over by construction. Under the pitched weighting that
    reads as an unloopable seam; under the percussion weighting the same
    window scores as the solid bar-aligned loop it audibly is.
    """
    sr = 22050
    g = _grid()
    rs = np.random.RandomState(0)
    dur = 4 * BP  # one bar
    y = np.zeros(int(sr * 32))
    # kick on beats, a bright hat between them — different spectra head vs tail
    for i in range(64):
        at = int(i * BP * sr)
        y[at:at + int(sr * 0.05)] += rs.randn(int(sr * 0.05)) * 0.05 + 0.5
        mid = at + int(BP * sr / 2)
        y[mid:mid + int(sr * 0.02)] += rs.randn(int(sr * 0.02)) * 0.4
    pos = g.make_pos(4.0, 4.0 + dur, snap="bar")
    la = LoopAnalyzer()
    as_pitched = la.analyze(y, sr, pos, pitched=True).confidence
    as_perc = la.analyze(y, sr, pos, pitched=False).confidence
    assert as_perc > as_pitched
    # and it must clear the kit's `usable` gate on its own merit
    assert as_perc > 0.2


def test_clarity_does_not_punish_a_loud_stem_like_silence():
    """A well-levelled loud phrase must outrank a silent one.

    `clarity` was a symmetric triangle peaking at RMS 0.15 and reaching
    exactly 0.0 at 0.3 — so a normal drum bus scored identically to silence
    on that term. Drums were the routine victim, being the hottest stem in
    most mixes.
    """
    from tone_forge.performance.classifier import performance_score

    def _phrase_at(energy):
        return Phrase(
            stem="drums",
            pos=_grid().make_pos(8.0, 8.0 + 8 * BP, snap="bar"),
            onset_density=2.0,
            pitched=False,
            energy=energy,
        ).with_id()

    args = (None, None, ContentType.RHYTHM_LOOP)
    silent = performance_score(_phrase_at(0.0), *args)
    healthy = performance_score(_phrase_at(0.15), *args)
    loud = performance_score(_phrase_at(0.32), *args)

    assert loud > silent, "a loud stem scored no better than silence"
    assert loud == pytest.approx(healthy), "0.32 RMS is a good level, not a defect"
    # genuinely hot material is nudged, never zeroed
    assert silent < performance_score(_phrase_at(0.9), *args) < loud
