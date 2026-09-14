"""Best-version-per-pad guarantee.

The pan-split family {guitar, guitar_center, guitar_sides} can hold the same
bars as up to three renditions of different quality. These pin the whole
chain: the stereo-aware loader (the [x, -x] sides stem used to mono-fold to
EXACT silence in the graph), the delay-aware + L/R-spectral split gates (one
widened part must not split into two degraded copies), the additive Phrase
quality signals, and the kit-selection duel (raw parent wins unless the
children demonstrably separated two parts).

All synthetic audio, physical properties only — no perceptual claims (the
repo's doctrine: objective checks may veto gross defects, never fine-rank).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tone_forge.performance.builder import _default_stem_loader, _graph_from_dict
from tone_forge.performance.graph import (
    ContentType,
    GridPos,
    MusicalGraph,
    PerformanceAsset,
    Phrase,
)
from tone_forge.performance.kit_builder import AutoKitBuilder
from tone_forge.performance.phrase_analyzer import _PITCHED_STEMS
from tone_forge.stem_separator import split_stem_by_pan

SR = 22050


# --- fixtures ---------------------------------------------------------------

def _wav(tmp_path, name, data, sr=SR):
    import soundfile as sf

    p = tmp_path / name
    # float WAV: the loader tests compare sample values exactly; 16-bit
    # quantization noise is not what they are pinning.
    sf.write(str(p), data, sr, subtype="FLOAT")
    return p


def _pos(start, bars=2, bar_s=2.0):
    return GridPos(
        start_s=start, end_s=start + bars * bar_s,
        start_beat=0, length_beats=float(bars * 4),
        start_bar=0, length_bars=float(bars), is_bar_aligned=True,
    )


def _asset(stem, pos, *, score=0.9, loop_conf=0.8, source=None):
    return PerformanceAsset(
        stem=stem, source_id=source or f"{stem}-{pos.start_s}", pos=pos,
        content_type=ContentType.LEAD_LOOP, performance_score=score,
        difficulty=0.3, loopable=True, loop_confidence=loop_conf,
    ).with_id()


def _phrase(stem, pos, *, pid, overlap=0.0, collapse=1.0, energy=0.2):
    return Phrase(
        stem=stem, pos=pos, onset_density=1.0, pitched=True, energy=energy,
        bar_energies=(energy,) * int(pos.length_bars or 1),
        parent_overlap=overlap, collapse_ratio=collapse, id=pid,
    )


def _graph(assets, phrases):
    return MusicalGraph(
        song_id="s", content_hash="h", module_version="test",
        config_hash="cfg", grid_tempo_bpm=120.0, time_signature=(4, 4),
        phrases=tuple(phrases), assets=tuple(assets),
    )


def _family_graph(overlap, *, with_parent=True, collapse=1.0):
    """Parent guitar + center/sides children on the same window, plus a drums
    anchor so the kit builder has its groove pad."""
    w = _pos(0.0)
    dw = _pos(4.0)
    assets, phrases = [], []
    drums = PerformanceAsset(
        stem="drums", source_id="drums-4", pos=dw,
        content_type=ContentType.RHYTHM_LOOP, performance_score=0.9,
        difficulty=0.2, loopable=True, loop_confidence=0.9,
    ).with_id()
    assets.append(drums)
    phrases.append(_phrase("drums", dw, pid="drums-4"))
    if with_parent:
        assets.append(_asset("guitar", w, source="guitar-0"))
        phrases.append(_phrase("guitar", w, pid="guitar-0"))
    for child in ("guitar_center", "guitar_sides"):
        assets.append(_asset(child, w, source=f"{child}-0"))
        phrases.append(_phrase(child, w, pid=f"{child}-0",
                               overlap=overlap, collapse=collapse))
    return _graph(assets, phrases)


def _kit_stems(kit):
    return [p["stemSlice"]["stemRole"] for p in kit["pads"]]


# --- 1. loader: [x, -x] no longer folds to silence --------------------------

def test_loader_returns_content_for_phase_cancelling_stereo(tmp_path):
    x = np.sin(2 * np.pi * 220 * np.arange(SR * 2) / SR) * 0.5
    p = _wav(tmp_path, "sides.wav", np.stack([x, -x], axis=1))
    y, sr, meta = _default_stem_loader(str(p))
    assert float(np.sqrt(np.mean(y**2))) > 0.1, "sides stem folded to silence"
    assert meta["collapse_ratio"] < 0.10


def test_loader_ordinary_stereo_folds_to_mean(tmp_path):
    x = np.sin(2 * np.pi * 220 * np.arange(SR) / SR) * 0.5
    p = _wav(tmp_path, "st.wav", np.stack([x, 0.8 * x], axis=1))
    y, sr, meta = _default_stem_loader(str(p))
    assert meta["collapse_ratio"] > 0.7
    assert np.allclose(y[:100], (x[:100] + 0.8 * x[:100]) / 2.0, atol=1e-6)


# --- 2. split gates: one widened part never splits --------------------------

def _rng_part(seed, secs=6.0, lo=200, hi=2000):
    rng = np.random.default_rng(seed)
    n = int(SR * secs)
    x = rng.standard_normal(n)
    # crude bandpass via FFT mask so parts occupy DIFFERENT bands
    f = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(n, 1 / SR)
    f[(freqs < lo) | (freqs > hi)] = 0
    y = np.fft.irfft(f, n)
    return (y / (np.abs(y).max() + 1e-9) * 0.5).astype(np.float64)


def test_haas_widened_single_part_skips_split(tmp_path):
    x = _rng_part(1)
    d = int(0.015 * SR)  # 15 ms Haas delay
    r = np.concatenate([np.zeros(d), x[:-d]])
    p = _wav(tmp_path, "haas.wav", np.stack([x, 0.9 * r], axis=1))
    out = split_stem_by_pan(p, output_dir=tmp_path)
    assert set(out.keys()) == {"center"}, "Haas width must not split"


def test_two_distinct_hard_panned_parts_do_split(tmp_path):
    a = _rng_part(2, lo=150, hi=800)
    b = _rng_part(3, lo=2000, hi=6000)  # different band -> different spectra
    p = _wav(tmp_path, "two.wav", np.stack([a, b], axis=1))
    out = split_stem_by_pan(p, output_dir=tmp_path)
    assert set(out.keys()) == {"center", "sides"}, "real doubles must split"


def test_same_spectrum_widened_part_reverts_via_lr_sanity(tmp_path):
    # Time-varying decorrelation with identical L/R spectra: R is L reversed.
    # Reversal preserves the magnitude spectrum but kills waveform
    # correlation at every lag — only the L/R spectral sanity net catches it.
    x = _rng_part(4)
    p = _wav(tmp_path, "wide.wav", np.stack([x, x[::-1]], axis=1))
    out = split_stem_by_pan(p, output_dir=tmp_path)
    assert set(out.keys()) == {"center"}, "same-spectrum width must revert"


# --- 3. additive signals: legacy graphs rehydrate clean ---------------------

def test_legacy_graph_rehydrates_clean_defaults():
    g = _family_graph(overlap=0.95)
    d = g.to_dict()
    for p in d["phrases"]:  # simulate a graph persisted before the fields
        p.pop("parent_overlap", None)
        p.pop("collapse_ratio", None)
    g2 = _graph_from_dict(d)
    ph = next(p for p in g2.phrases if p.stem == "guitar_center")
    assert ph.parent_overlap == 0.0 and ph.collapse_ratio == 1.0


# --- 4. the duel ------------------------------------------------------------

def test_duplicate_children_lose_to_raw_parent():
    kit = AutoKitBuilder().build(_family_graph(overlap=0.95), pads=8)
    stems = _kit_stems(kit)
    assert "guitar" in stems
    assert "guitar_center" not in stems and "guitar_sides" not in stems


def test_distinct_children_win_and_parent_drops():
    kit = AutoKitBuilder().build(_family_graph(overlap=0.30), pads=8)
    stems = _kit_stems(kit)
    assert "guitar" not in stems
    assert "guitar_center" in stems and "guitar_sides" in stems


def test_children_only_legacy_graph_unchanged():
    kit = AutoKitBuilder().build(
        _family_graph(overlap=0.0, with_parent=False), pads=8)
    stems = _kit_stems(kit)
    assert "guitar_center" in stems or "guitar_sides" in stems


def test_collapsed_child_is_vetoed_when_parent_exists():
    kit = AutoKitBuilder().build(
        _family_graph(overlap=0.30, collapse=0.02), pads=8)
    stems = _kit_stems(kit)
    # both children carry the cancellation defect -> raw parent wins
    assert "guitar" in stems
    assert "guitar_center" not in stems and "guitar_sides" not in stems


def test_available_stems_guard_blocks_unservable_parent():
    kit = AutoKitBuilder().build(
        _family_graph(overlap=0.95), pads=8,
        available_stems={"drums", "bass", "guitar_center", "guitar_sides"})
    stems = _kit_stems(kit)
    assert "guitar" not in stems, "parent pad would 404 on this entry"
    assert "guitar_center" in stems or "guitar_sides" in stems


# --- 5. artifact gate covers the artifact-prone stems -----------------------

def test_pan_split_children_are_pitched():
    assert "guitar_center" in _PITCHED_STEMS
    assert "guitar_sides" in _PITCHED_STEMS


# --- 6. borrow serves the least-processed rendition -------------------------

def test_borrow_alias_prefers_raw_parent():
    from tone_forge.performance.borrow import _stem_aliases

    order = _stem_aliases("other")
    assert order.index("guitar") < order.index("guitar_center")
    assert order.index("other") < order.index("guitar_center")
