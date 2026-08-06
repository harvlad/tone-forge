"""Performance Intelligence — Unified Musical Graph tests.

Covers the pure/synthetic path (no real audio stack): grid snapping + loop
enumeration, loop-confidence ordering, phrase cutting, pattern recurrence, the
end-to-end builder with an injected stem loader, cache determinism, and kit
assembly. The librosa-backed onset upgrade is exercised only on the backend env.
"""
import numpy as np
import pytest

from tone_forge.performance.grid import MusicalGrid
from tone_forge.performance.graph import ContentType, MusicalGraph, PerformanceAsset
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
    assert kit["provenance"]["source"] == "performance_intelligence"
