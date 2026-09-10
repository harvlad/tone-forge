"""Performance Intelligence — Unified Musical Graph tests.

Covers the pure/synthetic path (no real audio stack): grid snapping + loop
enumeration, loop-confidence ordering, phrase cutting, pattern recurrence, the
end-to-end builder with an injected stem loader, cache determinism, and kit
assembly. The librosa-backed onset upgrade is exercised only on the backend env.
"""
import numpy as np
import pytest

from tone_forge.performance.grid import MusicalGrid
from tone_forge.performance.graph import (
    ContentType,
    GridPos,
    Loop,
    LoopQuality,
    MusicalGraph,
    PerformanceAsset,
    Phrase,
)
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


def _asset(stem, ctype, *, start, loop_conf, score, pattern=None, difficulty=0.3,
           bars=2):
    """A PerformanceAsset positioned on the bar grid, with scores pinned."""
    return PerformanceAsset(
        stem=stem,
        source_id=f"{stem}-{start}",
        pos=_grid().make_pos(start, start + bars * 4 * BP, snap="bar"),
        content_type=ctype,
        performance_score=score,
        difficulty=difficulty,
        loopable=loop_conf >= 0.55,
        loop_confidence=loop_conf,
        pattern_id=pattern,
    ).with_id()


def _synth_graph(assets, phrases=(), loops=(), tempo=BPM):
    return MusicalGraph(
        song_id="s", content_hash="h", module_version="test",
        config_hash="cfg", grid_tempo_bpm=tempo, time_signature=(4, 4),
        phrases=tuple(phrases), loops=tuple(loops), assets=tuple(assets),
    )


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


def test_kit_pads_are_grouped_by_category():
    """Final padIdx assignment lands in category rows, not ranking order.

    Ranking order scattered categories across the grid (the top-scoring chord
    loop grabbed pad 0, the second bass groove landed wherever the top-up left
    it). The layout pass groups pads per category — drums, bass, chords,
    lead, texture — while the drum anchor keeps pad 0 and relative rank
    within each category is preserved.
    """
    from tone_forge.performance.kit_builder import _CATEGORY_GROUP_ORDER

    drums_a = _asset("drums", ContentType.RHYTHM_LOOP, start=8.0, loop_conf=0.6, score=0.5)
    drums_b = _asset("drums", ContentType.RHYTHM_LOOP, start=12.0, loop_conf=0.5, score=0.45)
    bass_a = _asset("bass", ContentType.BASS_GROOVE, start=4.0, loop_conf=0.7, score=0.9)
    bass_b = _asset("bass", ContentType.BASS_GROOVE, start=20.0, loop_conf=0.65, score=0.6)
    # Top-scoring asset overall — under ranking-order layout it took pad 0.
    chords = _asset("other", ContentType.CHORD_LOOP, start=16.0, loop_conf=0.8, score=0.95)
    lead = _asset("other", ContentType.LEAD_LOOP, start=0.0, loop_conf=0.75, score=0.85)
    texture = _asset("other", ContentType.TEXTURE, start=24.0, loop_conf=0.6, score=0.4)
    g = MusicalGraph(
        song_id="s", content_hash="h", module_version="test",
        config_hash="cfg", grid_tempo_bpm=BPM, time_signature=(4, 4),
        assets=(chords, bass_a, lead, bass_b, drums_a, drums_b, texture),
    )

    kit = AutoKitBuilder().build(g, skill="intermediate", pads=8)
    pads = kit["pads"]
    cats = [p["category"] for p in pads]

    # Drum-groove anchor invariant: pad 0 is still the steadiest drum groove.
    assert pads[0]["assetId"] == drums_a.id
    # Categories form contiguous blocks — no category split across the grid.
    blocks = [c for i, c in enumerate(cats) if i == 0 or cats[i - 1] != c]
    assert len(blocks) == len(set(blocks)), f"category split across grid: {cats}"
    # Blocks follow the musical row order (drums → bass → chords → lead → …).
    rows = [_CATEGORY_GROUP_ORDER[c] for c in blocks]
    assert rows == sorted(rows), f"rows out of order: {cats}"
    # Relative rank within a category is preserved (bass 0.9 before bass 0.6).
    bass_ids = [p["assetId"] for p in pads if p["category"] == "BASS"]
    assert bass_ids == [bass_a.id, bass_b.id]
    # padIdx matches the grouped order and the layout bump busts kit caches.
    assert [p["padIdx"] for p in pads] == list(range(len(pads)))
    assert "kit=7" in kit["provenance"]


def test_stem_spread_quota_reserves_a_pad_per_stem():
    """A guitar-dominated song still gets bass + chords + melody pads.

    Before the stem-spread quota only drums was guaranteed (the anchor), so a
    song whose loudest material is all guitar came back e.g. drums + 5 guitar +
    0 bass + 0 vocals: bass that isn't loopable is a ONE_SHOT no role slot
    wants, and vocals have no dedicated slot and lose head-to-head to guitar.
    The quota reserves one pad each for bass, harmonic-chords and melody BEFORE
    the generic scan. With only 4 pads the plain score scan would seat the drum
    anchor + the three top-scoring guitars; the quota trades two of those for a
    non-loopable bass and the vocal line.
    """
    drums = _asset("drums", ContentType.RHYTHM_LOOP, start=8.0, loop_conf=0.6, score=0.55)
    # Non-loopable bass → a ONE_SHOT the "Bass groove" slot would never pick.
    bass = _asset("bass", ContentType.ONE_SHOT, start=4.0, loop_conf=0.12, score=0.45)
    guitars = [
        _asset("other", ContentType.CHORD_LOOP, start=0.0, loop_conf=0.85, score=0.95),
        _asset("other", ContentType.CHORD_LOOP, start=16.0, loop_conf=0.82, score=0.92),
        _asset("other", ContentType.CHORD_LOOP, start=20.0, loop_conf=0.8, score=0.90),
        _asset("other", ContentType.CHORD_LOOP, start=24.0, loop_conf=0.78, score=0.88),
    ]
    vocals = _asset("vocals", ContentType.LEAD_LOOP, start=12.0, loop_conf=0.5, score=0.5)
    g = _synth_graph([drums, bass, vocals] + guitars)

    kit = AutoKitBuilder().build(g, skill="intermediate", pads=4)
    cats = {p["category"] for p in kit["pads"]}
    ids = {p["assetId"] for p in kit["pads"]}
    assert cats == {"DRUMS", "BASS", "CHORDS", "VOCAL"}, f"spread not balanced: {cats}"
    assert bass.id in ids, "non-loopable bass earned no pad"
    assert vocals.id in ids, "vocal/melody line crowded out by guitar"


def test_composite_quality_vetoes_noisy_pitched_slice():
    """A pitched slice that is mostly broadband hiss (bad separation) is kept
    off the pads even though it loops steadily and scores well pre-quality.

    High spectral flatness on a PITCHED stem is the strongest bad-separation
    tell; the usable gate now vetoes it (additive to the energy floor) and the
    composite _score demotes anything that slips through, so the clean twin
    wins every bucket/slot. Drums are exempt — broadband by nature.
    """
    clean = _asset("other", ContentType.CHORD_LOOP, start=0.0, loop_conf=0.8, score=0.9)
    noisy = _asset("other", ContentType.CHORD_LOOP, start=4.0, loop_conf=0.8, score=0.9)
    bass = _asset("bass", ContentType.BASS_GROOVE, start=8.0, loop_conf=0.7, score=0.8)
    clean_ph = Phrase(stem="other", pos=clean.pos, energy=0.2, pitched=True,
                      flatness=0.10, id=clean.source_id)
    noisy_ph = Phrase(stem="other", pos=noisy.pos, energy=0.2, pitched=True,
                      flatness=0.60, id=noisy.source_id)  # > _FLATNESS_NOISE
    bass_ph = Phrase(stem="bass", pos=bass.pos, energy=0.2, pitched=True,
                     flatness=0.10, id=bass.source_id)
    kit = AutoKitBuilder().build(
        _synth_graph([clean, noisy, bass],
                     phrases=[clean_ph, noisy_ph, bass_ph]), pads=4)
    ids = {p["assetId"] for p in kit["pads"]}
    assert noisy.id not in ids, "noisy pitched slice cleared the quality gate"
    assert clean.id in ids and bass.id in ids


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


BAR_S = 4 * BP  # 2.0 s at 120 BPM 4/4


def test_pad_window_is_whole_bars_at_song_tempo():
    """loopStartSec/EndSec are the asset's bar-aligned span, capped in bars.

    The old fixed `start + 8.0 s` window ignored the phrase's musical
    boundaries, so the exported loop region was one the loop metrics were
    never measured on. A 2-bar phrase must export exactly its 2 bars; an
    8-bar phrase must truncate to the largest bar count that DIVIDES it and
    fits the 8 s cap (4 bars here), never a fractional cut.
    """
    short = _asset("other", ContentType.CHORD_LOOP, start=0.0, loop_conf=0.8,
                   score=0.9, bars=2)
    long = _asset("other", ContentType.LEAD_LOOP, start=8.0, loop_conf=0.7,
                  score=0.8, bars=8)  # 16 s span > 8 s cap
    kit = AutoKitBuilder().build(_synth_graph([short, long]), pads=4)
    by_id = {p["assetId"]: p for p in kit["pads"]}

    ps, pl = by_id[short.id], by_id[long.id]
    assert ps["loopEndSec"] - ps["loopStartSec"] == pytest.approx(2 * BAR_S)
    assert pl["loopEndSec"] - pl["loopStartSec"] == pytest.approx(4 * BAR_S)
    for p in (ps, pl):  # window is whole bars, and stemSlice matches it
        span = p["loopEndSec"] - p["loopStartSec"]
        assert span / BAR_S == pytest.approx(round(span / BAR_S))
        assert p["stemSlice"]["startSec"] == p["loopStartSec"]
        assert p["stemSlice"]["endSec"] == p["loopEndSec"]


def test_pad_window_prefers_the_optimized_loop_seam():
    """When the analyzer measured an optimized seam window, the pad plays it —
    but only when that window is whole bars.

    The optimized window is the region loop_confidence/crossfade_ms were
    computed on. The clients bar-snap loop length at the constant song tempo,
    so a zero-crossing-nudged window that isn't integer bars would have its
    seam moved by the client's snap anyway — those fall back to the phrase's
    bar-aligned span.
    """
    # 4.05..8.05 = exactly 2 bars at 120 BPM → the optimized seam is honored.
    a = _asset("other", ContentType.CHORD_LOOP, start=4.0, loop_conf=0.85,
               score=0.9, bars=2)
    lp = Loop(
        phrase_id="ph", stem="other", pos=a.pos, id=a.source_id,
        quality=LoopQuality(confidence=0.85, crossfade_ms=12.0,
                            optimized_start_s=4.05, optimized_end_s=8.05),
    )
    kit = AutoKitBuilder().build(_synth_graph([a], loops=[lp]), pads=2)
    pad = next(p for p in kit["pads"] if p["assetId"] == a.id)
    assert pad["loopStartSec"] == pytest.approx(4.05)
    assert pad["loopEndSec"] == pytest.approx(8.05)
    assert pad["crossfadeMs"] == pytest.approx(12.0)
    assert pad["loopScore"] == pytest.approx(0.85)


def test_non_bar_optimized_window_falls_back_to_phrase_span():
    """An optimized window 30 ms off whole bars is rejected: the client's
    bar-snap would shift its seam off the measured region, so the pad exports
    the phrase's bar-aligned span instead."""
    a = _asset("other", ContentType.CHORD_LOOP, start=4.0, loop_conf=0.85,
               score=0.9, bars=2)
    lp = Loop(
        phrase_id="ph", stem="other", pos=a.pos, id=a.source_id,
        quality=LoopQuality(confidence=0.85, crossfade_ms=12.0,
                            optimized_start_s=4.05, optimized_end_s=8.02),
    )
    kit = AutoKitBuilder().build(_synth_graph([a], loops=[lp]), pads=2)
    pad = next(p for p in kit["pads"] if p["assetId"] == a.id)
    assert pad["loopStartSec"] == pytest.approx(4.0)
    assert pad["loopEndSec"] == pytest.approx(8.0)


def test_truncated_window_lands_on_the_loudest_bars():
    """A silent-head phrase must not export its silence: with a per-bar energy
    profile the truncated window moves onto the loudest contiguous bar run."""
    # 8 bars at 120 BPM (16 s) truncates to 4 bars; content lives in bars 5-8.
    a = _asset("other", ContentType.CHORD_LOOP, start=0.0, loop_conf=0.8,
               score=0.9, bars=8)
    ph = Phrase(stem="other", pos=a.pos, energy=0.05,
                bar_energies=(1e-4, 1e-4, 1e-4, 1e-4, 0.1, 0.1, 0.1, 0.1),
                id=a.source_id)
    kit = AutoKitBuilder().build(_synth_graph([a], phrases=[ph]), pads=2)
    pad = next(p for p in kit["pads"] if p["assetId"] == a.id)
    assert pad["loopStartSec"] == pytest.approx(8.0)   # bars 5-8
    assert pad["loopEndSec"] == pytest.approx(16.0)


# --- Pad WINDOW invariants on a REAL (drifting) grid ---------------------
# The grid's real downbeats drift against any constant BPM, so these tests
# build GridPos directly with an explicit LOCAL bar length instead of
# snapping to the 120 BPM `_grid()`. The kit builder must derive every
# window from the phrase's own span (pos + length_beats), never from
# graph.grid_tempo_bpm.


def _pos(start, bars, bar_s, beats_per_bar=4):
    """A bar-aligned GridPos whose LOCAL bar length is explicit — and free to
    disagree with the graph's constant tempo, like real snapped phrases do."""
    return GridPos(
        start_s=start, end_s=start + bars * bar_s,
        start_beat=0, length_beats=float(bars * beats_per_bar),
        start_bar=0, length_bars=float(bars), is_bar_aligned=True,
    )


def _asset_at(stem, ctype, pos, *, loop_conf=0.8, score=0.9, source=None):
    """A PerformanceAsset on an explicit GridPos (see `_pos`)."""
    return PerformanceAsset(
        stem=stem, source_id=source or f"{stem}-{pos.start_s}", pos=pos,
        content_type=ctype, performance_score=score, difficulty=0.3,
        loopable=loop_conf >= 0.55, loop_confidence=loop_conf,
    ).with_id()


def test_truncation_picks_a_bar_count_that_divides_the_phrase():
    """A 4-bar groove over the cap truncates to 2 bars — the largest count
    that DIVIDES 4 — never the 3 that merely fits.

    At ~95 BPM a bar is ~2.52 s, so 4 bars (~10.1 s) exceed the 8 s cap and
    3 bars fit. But a 3-bar cut of a 4-bar pattern wraps bar 3 → bar 1,
    skipping the fill bar that leads back into the "1" — an audible dead
    spot at every wrap ("in time but not seamless": a 110 ms energy hole vs
    60 ms at ordinary bar boundaries on the real song). The 2-bar cut is
    pattern-coherent. A phrase that fits entirely is untouched.
    """
    bar = 2.523
    four = _asset_at("drums", ContentType.RHYTHM_LOOP, _pos(0.0, 4, bar))
    fits = _asset_at("other", ContentType.CHORD_LOOP, _pos(15.138, 3, bar),
                     score=0.85)  # 3 bars = 7.57 s < cap → untouched
    kit = AutoKitBuilder().build(_synth_graph([four, fits]), pads=4)
    by_id = {p["assetId"]: p for p in kit["pads"]}

    span4 = by_id[four.id]["loopEndSec"] - by_id[four.id]["loopStartSec"]
    assert span4 == pytest.approx(2 * bar, abs=1e-3), \
        f"expected the 2-bar divisor cut, got {span4 / bar:.3f} bars"
    span3 = by_id[fits.id]["loopEndSec"] - by_id[fits.id]["loopStartSec"]
    assert span3 == pytest.approx(3 * bar, abs=1e-3)
    assert by_id[fits.id]["loopStartSec"] == pytest.approx(15.138, abs=1e-3)


def test_truncation_prime_bar_count_falls_back_to_largest_fit():
    """When no divisor > 1 fits the cap (5 bars, cap 3) the cut is the
    largest that fits — 3 bars. A 1-bar loop of a 5-bar phrase is no more
    pattern-coherent than 3 bars and loses content."""
    bar = 2.523  # cap_bars = 3; divisors of 5 under it: only 1
    a = _asset_at("other", ContentType.CHORD_LOOP, _pos(0.0, 5, bar))
    kit = AutoKitBuilder().build(_synth_graph([a]), pads=2)
    pad = next(p for p in kit["pads"] if p["assetId"] == a.id)
    span = pad["loopEndSec"] - pad["loopStartSec"]
    assert span == pytest.approx(3 * bar, abs=1e-3)


def test_window_length_derives_from_local_bars_not_graph_tempo():
    """Window lengths come from the phrase's OWN span, not the constant
    graph tempo.

    Local bar 2.03 s vs the graph's constant 2.0 s at 120 BPM (~1.5% drift —
    Doomsday-sized). A constant-tempo window cuts short of the real downbeat
    so the wrap lands in the pre-beat gap and audibly pauses. The exported
    window must be an exact multiple of the LOCAL bar and NOT of the
    constant one.
    """
    local_bar = 2.03
    a = _asset_at("other", ContentType.CHORD_LOOP, _pos(0.0, 8, local_bar))
    kit = AutoKitBuilder().build(_synth_graph([a]), pads=2)
    pad = next(p for p in kit["pads"] if p["assetId"] == a.id)
    span = pad["loopEndSec"] - pad["loopStartSec"]
    assert span == pytest.approx(2 * local_bar, abs=1e-3)  # divisor cut, 4.06 s
    assert span / local_bar == pytest.approx(round(span / local_bar), abs=1e-3)
    # NOT a whole number of constant-tempo bars (4.06 / 2.0 = 2.03).
    assert abs(span / 2.0 - round(span / 2.0)) > 0.01


def test_loop_region_is_always_the_full_exported_slice():
    """loopStartSec/EndSec == stemSlice start/end on EVERY pad, whichever
    window path produced it (fits-entirely, bar-truncated, optimized seam).

    The app loops the whole exported slice; a loop sub-region diverging from
    the slice re-introduces the original defect — loop metrics describing a
    region the exported audio doesn't contain.
    """
    fits = _asset("other", ContentType.CHORD_LOOP, start=0.0, loop_conf=0.8,
                  score=0.9, bars=2)
    truncated = _asset_at("drums", ContentType.RHYTHM_LOOP, _pos(8.0, 4, 2.523))
    opt = _asset("bass", ContentType.BASS_GROOVE, start=24.0, loop_conf=0.85,
                 score=0.8, bars=2)
    lp = Loop(
        phrase_id="ph", stem="bass", pos=opt.pos, id=opt.source_id,
        quality=LoopQuality(confidence=0.85, crossfade_ms=12.0,
                            optimized_start_s=24.05, optimized_end_s=28.05),
    )
    kit = AutoKitBuilder().build(
        _synth_graph([fits, truncated, opt], loops=[lp]), pads=4)
    assert len(kit["pads"]) == 3
    for p in kit["pads"]:
        assert p["loopStartSec"] == p["stemSlice"]["startSec"]
        assert p["loopEndSec"] == p["stemSlice"]["endSec"]


def test_optimized_window_over_cap_falls_back_to_phrase_span():
    """An optimized seam window that is whole bars but EXCEEDS the cap is
    rejected — the guard requires both — and the pad exports the phrase's
    own bar-aligned span."""
    a = _asset("other", ContentType.CHORD_LOOP, start=4.0, loop_conf=0.85,
               score=0.9, bars=2)
    lp = Loop(
        phrase_id="ph", stem="other", pos=a.pos, id=a.source_id,
        # 10 s = exactly 5 bars at 120 BPM — whole bars, but over the 8 s cap.
        quality=LoopQuality(confidence=0.85, crossfade_ms=12.0,
                            optimized_start_s=4.0, optimized_end_s=14.0),
    )
    kit = AutoKitBuilder().build(_synth_graph([a], loops=[lp]), pads=2)
    pad = next(p for p in kit["pads"] if p["assetId"] == a.id)
    assert pad["loopStartSec"] == pytest.approx(4.0)
    assert pad["loopEndSec"] == pytest.approx(8.0)


def test_optimized_window_is_judged_in_local_bars():
    """The whole-bars gate on the optimized window measures LOCAL bars.

    2 local bars of 2.03 s = 4.06 s: exact whole local bars (honored), yet
    60 ms off whole constant-tempo bars — a constant-bar gate would wrongly
    reject exactly the drifting-grid songs the optimized seam matters for.
    """
    local_bar = 2.03
    pos = _pos(10.0, 2, local_bar)
    a = _asset_at("other", ContentType.CHORD_LOOP, pos, loop_conf=0.9,
                  source="src-opt")
    lp = Loop(
        phrase_id="ph", stem="other", pos=pos, id="src-opt",
        quality=LoopQuality(confidence=0.9, crossfade_ms=9.0,
                            optimized_start_s=10.04, optimized_end_s=14.10),
    )
    kit = AutoKitBuilder().build(_synth_graph([a], loops=[lp]), pads=2)
    pad = next(p for p in kit["pads"] if p["assetId"] == a.id)
    assert pad["loopStartSec"] == pytest.approx(10.04)
    assert pad["loopEndSec"] == pytest.approx(14.10)
    assert pad["crossfadeMs"] == pytest.approx(9.0)


def test_truncated_window_steps_in_whole_local_bars_to_the_loudest_run():
    """Loudest-run placement stays bar-aligned relative to the phrase start.

    With a per-bar energy profile the k-bar window slides in whole LOCAL-bar
    steps and lands on the loudest contiguous k-bar run — here bars 3-4 of a
    4-bar phrase whose head is near-silent. A non-bar offset would break the
    shared phase-lock cycle even when the length is right.
    """
    bar = 2.523
    pos = _pos(20.184, 4, bar)
    a = _asset_at("drums", ContentType.RHYTHM_LOOP, pos, source="ph-loud")
    ph = Phrase(stem="drums", pos=pos, energy=0.15,
                bar_energies=(0.01, 0.01, 0.2, 0.22), id="ph-loud")
    kit = AutoKitBuilder().build(_synth_graph([a], phrases=[ph]), pads=2)
    pad = next(p for p in kit["pads"] if p["assetId"] == a.id)
    off_bars = (pad["loopStartSec"] - pos.start_s) / bar
    assert off_bars == pytest.approx(round(off_bars), abs=1e-3), \
        "window offset is not a whole number of local bars"
    assert off_bars == pytest.approx(2.0, abs=1e-3)  # bars 3-4, loudest 2-run
    span = pad["loopEndSec"] - pad["loopStartSec"]
    assert span == pytest.approx(2 * bar, abs=1e-3)


def test_kit_from_real_world_drum_graph_exports_two_local_bars():
    """Today's production failure, reconstructed end-to-end.

    4-bar drum phrases whose real (local) bar is 2.523 s on a graph whose
    constant tempo is 95.43 BPM (const bar ~2.515 s), per-bar energy profile
    present, loops carrying measured crossfades. The shipped kit cut 3
    constant-tempo bars — in time, never seamless. The pad must export
    exactly 2 LOCAL bars starting on a phrase-bar boundary, stay loopable,
    and keep the analyzer's crossfade.
    """
    bar = 2.523
    ph_start = 20.184
    pos = _pos(ph_start, 4, bar)
    ph = Phrase(stem="drums", pos=pos, energy=0.18,
                bar_energies=(0.12, 0.14, 0.2, 0.22), id="ph-drums")
    lp = Loop(phrase_id="ph-drums", stem="drums", pos=pos, id="lp-drums",
              quality=LoopQuality(confidence=0.72, crossfade_ms=15.0))
    a = _asset_at("drums", ContentType.RHYTHM_LOOP, pos, loop_conf=0.72,
                  score=0.7, source="lp-drums")
    pos2 = _pos(2 * ph_start, 4, bar)
    ph2 = Phrase(stem="drums", pos=pos2, energy=0.16,
                 bar_energies=(0.15, 0.15, 0.14, 0.16), id="ph-drums-2")
    lp2 = Loop(phrase_id="ph-drums-2", stem="drums", pos=pos2, id="lp-drums-2",
               quality=LoopQuality(confidence=0.6, crossfade_ms=11.0))
    a2 = _asset_at("drums", ContentType.RHYTHM_LOOP, pos2, loop_conf=0.6,
                   score=0.6, source="lp-drums-2")
    kit = AutoKitBuilder().build(
        _synth_graph([a, a2], phrases=[ph, ph2], loops=[lp, lp2],
                     tempo=95.43),
        pads=4)
    pad = next(p for p in kit["pads"] if p["assetId"] == a.id)

    span = pad["loopEndSec"] - pad["loopStartSec"]
    assert span == pytest.approx(2 * bar, abs=1e-3), \
        f"expected 2 local bars, got {span:.3f} s"
    assert span < 3 * (240.0 / 95.43) - 0.5  # nowhere near the old 3-bar cut
    off_bars = (pad["loopStartSec"] - ph_start) / bar
    assert off_bars == pytest.approx(round(off_bars), abs=1e-3), \
        "window does not start on a phrase-bar boundary"
    assert pad["loopable"] is True
    assert pad["crossfadeMs"] == pytest.approx(15.0)
    assert pad["loopStartSec"] == pad["stemSlice"]["startSec"]
    assert pad["loopEndSec"] == pad["stemSlice"]["endSec"]


def test_near_silent_asset_excluded_when_alternatives_exist():
    """A whisper-quiet sustain must not earn a pad on loop steadiness alone.

    A steady near-silent tail aces loop_confidence (head==tail by
    construction) and cleared the old usable gate; the energy floor vetoes it
    on level. Energy lives on the Phrase, resolved via source_id.
    """
    quiet = _asset("other", ContentType.CHORD_LOOP, start=0.0, loop_conf=0.95,
                   score=0.9)
    quiet_ph = Phrase(stem="other", pos=quiet.pos, energy=0.001,
                      id=quiet.source_id)
    loud = [
        _asset("other", ContentType.CHORD_LOOP, start=4.0, loop_conf=0.8, score=0.85),
        _asset("bass", ContentType.BASS_GROOVE, start=8.0, loop_conf=0.7, score=0.8),
        _asset("other", ContentType.LEAD_LOOP, start=12.0, loop_conf=0.75, score=0.75),
    ]
    loud_phs = [Phrase(stem=a.stem, pos=a.pos, energy=0.2, id=a.source_id)
                for a in loud]
    kit = AutoKitBuilder().build(
        _synth_graph([quiet] + loud, phrases=[quiet_ph] + loud_phs), pads=4)
    ids = {p["assetId"] for p in kit["pads"]}
    assert quiet.id not in ids, "near-silent asset seated despite alternatives"
    assert ids == {a.id for a in loud}


def test_loopable_reflects_loop_confidence():
    """Seam-hostile material one-shots instead of force-looping.

    loopable was hardcoded True, so a pad with loop_confidence 0.1 stuttered
    at every seam. The flip threshold is the usable gate's own 0.2, NOT the
    classifier's 0.55 — that would flip far too many pads out of layering.
    """
    hostile = _asset("other", ContentType.ONE_SHOT, start=0.0, loop_conf=0.1,
                     score=0.9)
    seamy = _asset("other", ContentType.CHORD_LOOP, start=4.0, loop_conf=0.25,
                   score=0.8)
    clean = _asset("bass", ContentType.BASS_GROOVE, start=8.0, loop_conf=0.8,
                   score=0.7)
    kit = AutoKitBuilder().build(_synth_graph([hostile, seamy, clean]), pads=4)
    loopable = {p["assetId"]: p["loopable"] for p in kit["pads"]}
    assert loopable[hostile.id] is False
    assert loopable[seamy.id] is True
    assert loopable[clean.id] is True


def test_starvation_fallback_still_fills_a_quiet_kit():
    """If EVERY asset is under the energy floor, the kit still fills.

    The floor must not be able to return an empty kit — a quiet kit beats an
    empty one, so total starvation relaxes the floor and keeps the ranking.
    """
    assets = [
        _asset("other", ContentType.CHORD_LOOP, start=4.0 * i, loop_conf=0.8,
               score=0.9 - 0.1 * i)
        for i in range(3)
    ]
    phrases = [Phrase(stem=a.stem, pos=a.pos, energy=0.001, id=a.source_id)
               for a in assets]
    kit = AutoKitBuilder().build(_synth_graph(assets, phrases=phrases), pads=4)
    assert kit["pads"], "energy floor starved the kit to empty"
    assert len(kit["pads"]) == len(assets)
