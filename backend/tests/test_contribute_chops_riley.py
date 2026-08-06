"""Riley-sourced Launchpad chops.

`build_chops(slice_mode="phrase")` should prefer the Musical Graph the analysis
worker attaches under `performance_graph` — grid-aligned, loop-scored,
usefulness-ranked phrase assets — and fall back to the legacy slicers only when
no graph is present.
"""
from __future__ import annotations

from tone_forge.contribute_chops import build_chops, _chops_from_riley
from tone_forge.performance.graph import (
    ContentType,
    GridPos,
    Loop,
    LoopQuality,
    MusicalGraph,
    PerformanceAsset,
)


def _pos(start_s, end_s, start_beat=0, length_beats=4, start_bar=0, length_bars=1):
    return GridPos(
        start_s=start_s,
        end_s=end_s,
        start_beat=start_beat,
        length_beats=length_beats,
        start_bar=start_bar,
        length_bars=length_bars,
        is_bar_aligned=True,
    )


def _graph_result():
    """A result dict carrying a small but realistic performance graph:
    two guitar assets (one high-usefulness loop with an optimized seam,
    one lower) + one bass asset."""
    loop_pos = _pos(4.0, 8.0, start_beat=8, start_bar=2)
    loop = Loop(
        phrase_id="ph_a",
        stem="other",
        pos=loop_pos,
        quality=LoopQuality(
            confidence=0.9,
            crossfade_ms=12.0,
            optimized_start_s=4.02,
            optimized_end_s=7.98,
        ),
    ).with_id()

    a_hi = PerformanceAsset(
        source_id=loop.id,               # ties the asset to the optimized loop
        stem="other",
        pos=loop_pos,
        content_type=ContentType.RHYTHM_LOOP,
        performance_score=0.92,
        difficulty=0.3,
        loopable=True,
        loop_confidence=0.9,
        label="verse riff",
        pattern_id="pat_1",
    ).with_id()
    a_lo = PerformanceAsset(
        source_id="ph_b",
        stem="other",
        pos=_pos(12.0, 14.0, start_beat=24, start_bar=6),
        content_type=ContentType.LEAD_LOOP,
        performance_score=0.40,
        loopable=False,
        loop_confidence=0.2,
        label="fill",
        pattern_id="pat_2",
    ).with_id()
    a_bass = PerformanceAsset(
        source_id="ph_c",
        stem="bass",
        pos=_pos(0.0, 4.0),
        content_type=ContentType.BASS_GROOVE,
        performance_score=0.7,
        loopable=True,
        loop_confidence=0.6,
        label="bass groove",
        pattern_id="pat_3",
    ).with_id()

    graph = MusicalGraph(
        song_id="song1",
        content_hash="abc123",
        module_version="0.1.0",
        config_hash="cfg",
        grid_tempo_bpm=120.0,
        time_signature=(4, 4),
        loops=(loop,),
        assets=(a_hi, a_lo, a_bass),
    ).with_hash()

    return {
        "performance_graph": graph.to_dict(),
        "downbeats_s": [0.0, 2.0, 4.0, 6.0, 8.0],
        "beats_s": [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0],
        "sections": [],
    }


def test_phrase_mode_prefers_riley_and_ranks_by_usefulness():
    result = _graph_result()
    chops = build_chops(stem="other", slice_mode="phrase", analysis_result=result)

    # Only the two `other`-stem assets, ranked most-useful first.
    assert len(chops) == 2
    assert chops[0]["contentType"] == "rhythm_loop"
    assert chops[0]["performanceScore"] == 0.92
    assert chops[1]["performanceScore"] == 0.40
    # Grid-aligned + carries the additive loop fields for gapless playback.
    assert chops[0]["loopable"] is True
    assert chops[0]["loopScore"] == 0.9
    assert chops[0]["patternId"] == "pat_1"
    # Uses the loop's OPTIMIZED seam + crossfade, not the raw phrase bounds.
    assert chops[0]["startSec"] == 4.02
    assert chops[0]["endSec"] == 7.98
    assert chops[0]["crossfadeMs"] == 12.0
    # build_chops post-processing still applied.
    assert chops[0]["idx"] == 0
    assert chops[0]["durationSec"] == round(7.98 - 4.02, 4)


def test_stem_filter_and_mix_includes_all():
    result = _graph_result()
    bass = build_chops(stem="bass", slice_mode="phrase", analysis_result=result)
    assert len(bass) == 1
    assert bass[0]["contentType"] == "bass_groove"

    mix = build_chops(stem="mix", slice_mode="phrase", analysis_result=result)
    # All three assets surface for the full mix, still usefulness-ranked.
    assert len(mix) == 3
    assert [c["performanceScore"] for c in mix] == [0.92, 0.7, 0.40]


def test_phrase_mode_falls_back_when_no_graph():
    # No performance_graph → legacy section/downbeat waterfall, never errors.
    result = {"downbeats_s": [0.0, 2.0, 4.0, 6.0], "sections": [], "beats_s": []}
    assert _chops_from_riley(result, "other") == []
    chops = build_chops(stem="other", slice_mode="phrase", analysis_result=result)
    assert len(chops) >= 1  # bars from downbeats
    assert "contentType" not in chops[0]  # came from the legacy slicer
