"""Chord-lane plumbing + stabilisation + confidence (native-parity fix).

Three defects were making the native (iOS/desktop) chord lane "not
follow the song" even though the chords were detected substantially
correct:

1. LANE PLUMBING — the native bundle shipped the flat legacy ``chords``
   field, which is the sparse demucs "other" residual lane (~10%
   coverage on Cross Bones), and DROPPED ``chords_by_stem`` entirely,
   so the real guitar-stem progression never reached native. Web
   already picks the richest-coverage lane client-side
   (jam.js ``_richestChordLane``). We now mirror that server-side
   (``select_richest_chord_lane``) so ``timeline.chords`` carries the
   guitar lane and ``timeline.chordsByStem`` is exposed additively.

2. SMOOTHING — ``detect_chords`` early-returned raw BTC frame-argmax
   regions before ``enforce_min_hold`` / ``collapse_same_root_regions``,
   so the shipped BTC path emitted sub-beat fragments + m/M flicker.
   Those two stability passes now run on the BTC path too.

3. CONFIDENCE — BTC's argmax head discarded the softmax posterior and
   every chord was hard-stamped 1.0. ``btc_chords`` now recovers the
   posterior from the same output projection and surfaces a real
   [0,1] region confidence; ``chords`` reads it instead of 1.0.

Hermetic: the detector / BTC adapter are monkeypatched so no real
audio decoding or model inference happens. A single real-model smoke
is guarded so it runs locally and skips where torch/checkpoints are
absent (CI).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

import tone_forge.analysis.chords as C
from tone_forge.analysis.chords import (
    chord_lane_coverage_s,
    select_richest_chord_lane,
)
from tone_forge.contracts import Chord


# ---------------------------------------------------------------------------
# Fixtures — the Cross Bones shape (sparse residual vs rich guitar lane)
# ---------------------------------------------------------------------------

def _cross_bones_by_stem() -> dict:
    """A stand-in for the Cross Bones persisted ``chords_by_stem``.

    Ground truth from the RCA: the "other" residual lane covers ~10%
    of the song while the guitar lane carries the real 13-chord
    progression (~55% coverage). ``vocals`` is a long garbage lane that
    must be excluded despite the highest raw coverage.
    """
    guitar = [
        {"start_s": t, "end_s": t + 8.85, "symbol": s, "confidence": 0.8}
        for t, s in [
            (0.0, "C#"), (8.85, "F#"), (17.7, "G#"), (26.55, "A#m"),
            (35.4, "C#"), (44.25, "F#"), (53.1, "G#"), (61.95, "D#m"),
            (70.8, "C#"), (79.65, "F#"), (88.5, "G#"), (97.35, "B"),
            (106.2, "C#"),
        ]
    ]
    return {
        # Sparse residual — one region, 22s.
        "other": [{"start_s": 0.0, "end_s": 22.0,
                   "symbol": "C#", "confidence": 0.5}],
        # The real harmony — 13 regions, ~115s.
        "guitar": guitar,
        "bass": [{"start_s": 0.0, "end_s": 40.0,
                  "symbol": "C#", "confidence": 0.6}],
        # Non-harmonic garbage lane with the HIGHEST raw coverage —
        # must be excluded so it can't win the race.
        "vocals": [{"start_s": 0.0, "end_s": 200.0,
                    "symbol": "X", "confidence": 0.9}],
    }


# ---------------------------------------------------------------------------
# 1. richest-lane selection
# ---------------------------------------------------------------------------

class TestRichestChordLane:
    def test_coverage_is_summed_region_seconds(self):
        lane = [
            {"start_s": 0.0, "end_s": 2.0, "symbol": "C"},
            {"start_s": 2.0, "end_s": 5.0, "symbol": "G"},
        ]
        assert chord_lane_coverage_s(lane) == pytest.approx(5.0)

    def test_coverage_accepts_typed_chords_and_bad_rows(self):
        lane = [
            Chord(0.0, 3.0, "C", 0.9),
            {"start_s": 0.0, "end_s": "oops", "symbol": "?"},  # unparseable → 0
        ]
        assert chord_lane_coverage_s(lane) == pytest.approx(3.0)

    def test_picks_guitar_lane_for_cross_bones(self):
        """The whole point: guitar (rich) beats the sparse 'other'
        residual AND the higher-raw-coverage 'vocals' garbage lane."""
        name, lane = select_richest_chord_lane(
            _cross_bones_by_stem(), fallback=[{"start_s": 0.0,
                                               "end_s": 22.0, "symbol": "C#"}],
        )
        assert name == "guitar"
        assert len(lane) == 13

    def test_excludes_non_harmonic_lanes(self):
        by_stem = {
            "vocals": [{"start_s": 0.0, "end_s": 300.0, "symbol": "Am"}],
            "drums": [{"start_s": 0.0, "end_s": 300.0, "symbol": "F"}],
            "other": [{"start_s": 0.0, "end_s": 10.0, "symbol": "C"}],
        }
        name, lane = select_richest_chord_lane(by_stem, fallback=[])
        assert name == "other"

    def test_ties_break_by_sorted_stem_name(self):
        by_stem = {
            "guitar": [{"start_s": 0.0, "end_s": 10.0, "symbol": "C"}],
            "other": [{"start_s": 0.0, "end_s": 10.0, "symbol": "G"}],
        }
        name, _ = select_richest_chord_lane(by_stem, fallback=[])
        assert name == "guitar"  # sorted first, wins the >-tie

    def test_falls_back_to_flat_lane_when_no_by_stem(self):
        fallback = [{"start_s": 0.0, "end_s": 4.0, "symbol": "Am"}]
        name, lane = select_richest_chord_lane({}, fallback=fallback)
        assert name is None
        assert lane == fallback
        assert lane is not fallback  # fresh copy

    def test_all_empty_per_stem_falls_back(self):
        fallback = [{"start_s": 0.0, "end_s": 4.0, "symbol": "C"}]
        name, lane = select_richest_chord_lane(
            {"other": [], "guitar": []}, fallback=fallback,
        )
        assert name is None
        assert len(lane) == 1

    def test_non_mapping_input_falls_back(self):
        name, lane = select_richest_chord_lane(None, fallback=[])
        assert name is None and lane == []


# ---------------------------------------------------------------------------
# 2. smoothing on the BTC path
# ---------------------------------------------------------------------------

class TestBTCPathSmoothing:
    """``detect_chords`` must stabilise the BTC winner, not just the
    chroma fallback. Patch the BTC entry point so no model runs."""

    def _fragmented(self):
        # A stable C# tonality shredded into m/M flicker + two sub-beat
        # slivers, then a genuine change to F#.
        return (
            Chord(0.0, 0.9, "C#m", 0.55),
            Chord(0.9, 1.0, "C#5", 0.40),   # 0.1s sliver (sub-beat)
            Chord(1.0, 1.9, "C#m", 0.60),
            Chord(1.9, 2.0, "C#", 0.35),    # 0.1s sliver (sub-beat)
            Chord(2.0, 4.0, "F#", 0.70),
        )

    def test_collapses_fragments_and_keeps_real_change(self, monkeypatch):
        frag = self._fragmented()
        monkeypatch.setattr(
            C, "detect_chords_btc_with_key",
            lambda a, sr, *, bass_audio=None: (frag, {}),
        )
        beats = np.arange(0.0, 6.0, 1.0)  # 1 s beats
        out = C.detect_chords(np.zeros(1000, np.float32), 22050, beats_s=beats)
        syms = [c.symbol for c in out]
        # The whole C# run collapses to exactly one region…
        cs = [c for c in out if c.symbol.startswith("C#")]
        assert len(cs) == 1
        # …and the genuine F# change is preserved (no cross-root merge).
        assert syms.count("F#") == 1
        # Contiguity preserved end-to-end.
        assert out[0].start_s == pytest.approx(0.0)
        assert out[-1].end_s == pytest.approx(4.0)

    def test_min_hold_absorbs_sub_beat_slivers(self, monkeypatch):
        # Two long DIFFERENT-root regions with a sub-beat sliver between
        # them: min-hold must absorb the sliver (collapse can't — the
        # roots differ).
        seq = (
            Chord(0.0, 2.0, "C", 0.9),
            Chord(2.0, 2.1, "G", 0.3),   # 0.1s sliver
            Chord(2.1, 4.0, "F", 0.9),
        )
        monkeypatch.setattr(
            C, "detect_chords_btc_with_key",
            lambda a, sr, *, bass_audio=None: (seq, {}),
        )
        beats = np.arange(0.0, 6.0, 1.0)
        out = C.detect_chords(np.zeros(1000, np.float32), 22050, beats_s=beats)
        assert "G" not in [c.symbol for c in out]  # sliver gone
        assert len(out) == 2

    def test_no_beats_still_collapses_same_root(self, monkeypatch):
        frag = self._fragmented()
        monkeypatch.setattr(
            C, "detect_chords_btc_with_key",
            lambda a, sr, *, bass_audio=None: (frag, {}),
        )
        out = C.detect_chords(np.zeros(1000, np.float32), 22050, beats_s=None)
        # min-hold no-ops without a beat grid, but same-root collapse
        # still fires so the C# run merges.
        cs = [c for c in out if c.symbol.startswith("C#")]
        assert len(cs) == 1
        assert [c.symbol for c in out].count("F#") == 1


# ---------------------------------------------------------------------------
# 3. real confidence surfacing
# ---------------------------------------------------------------------------

class TestBTCConfidence:
    def test_posteriors_pass_through_as_real_confidence(self, monkeypatch):
        import tone_forge.analysis.btc_chords as B
        monkeypatch.setattr(
            B, "detect_chords_btc",
            lambda y, sr, vocab="large_voca", device=None: [
                {"start": 0.0, "end": 2.0, "label": "C#", "confidence": 0.92},
                {"start": 2.0, "end": 3.0, "label": "F#", "confidence": 0.41},
                {"start": 3.0, "end": 5.0, "label": "G#", "confidence": 0.67},
            ],
        )
        monkeypatch.setattr(C, "_BTC_FAILED", False)
        res = C.detect_chords_btc_with_key(np.zeros(1000, np.float32), 22050)
        assert res is not None
        chords, _ = res
        confs = [c.confidence for c in chords]
        assert confs == pytest.approx([0.92, 0.41, 0.67])
        # A real distribution — NOT the old constant 1.0.
        assert len(set(confs)) > 1
        assert all(0.0 <= c <= 1.0 for c in confs)
        assert not all(c == 1.0 for c in confs)

    def test_legacy_regions_without_posterior_default_to_one(self, monkeypatch):
        import tone_forge.analysis.btc_chords as B
        monkeypatch.setattr(
            B, "detect_chords_btc",
            lambda y, sr, vocab="large_voca", device=None: [
                {"start": 0.0, "end": 1.0, "label": "C"},  # no confidence key
            ],
        )
        monkeypatch.setattr(C, "_BTC_FAILED", False)
        res = C.detect_chords_btc_with_key(np.zeros(1000, np.float32), 22050)
        chords, _ = res
        assert chords[0].confidence == 1.0

    def test_out_of_range_posterior_is_clamped(self, monkeypatch):
        import tone_forge.analysis.btc_chords as B
        monkeypatch.setattr(
            B, "detect_chords_btc",
            lambda y, sr, vocab="large_voca", device=None: [
                {"start": 0.0, "end": 1.0, "label": "C", "confidence": 1.7},
                {"start": 1.0, "end": 2.0, "label": "G", "confidence": -0.2},
            ],
        )
        monkeypatch.setattr(C, "_BTC_FAILED", False)
        chords, _ = C.detect_chords_btc_with_key(np.zeros(1000, np.float32), 22050)
        assert chords[0].confidence == 1.0
        assert chords[1].confidence == 0.0


# ---------------------------------------------------------------------------
# Real-model smoke — proves btc_chords actually recovers the posterior.
# Skips where torch or the vendored checkpoint is unavailable (CI).
# ---------------------------------------------------------------------------

_CKPT = (
    Path(__file__).resolve().parent.parent
    / "vendor" / "btc_ismir19" / "checkpoints" / "btc_model_large_voca.pt"
)


@pytest.mark.skipif(
    not _CKPT.exists(), reason="BTC checkpoint not present",
)
def test_real_model_emits_varied_non_unit_confidence():
    pytest.importorskip("torch")
    pytest.importorskip("librosa")
    from tone_forge.analysis.btc_chords import detect_chords_btc

    sr = 22050
    rng = np.random.default_rng(0)

    def chord(freqs, dur):
        t = np.arange(int(sr * dur)) / sr
        y = sum(np.sin(2 * np.pi * f * t)
                + 0.4 * np.sin(2 * np.pi * 2 * f * t) for f in freqs)
        return (y + 0.02 * rng.standard_normal(len(t))).astype(np.float32)

    prog = [
        ([130.8, 164.8, 196.0], 6), ([196.0, 246.9, 293.7], 6),
        ([110.0, 130.8, 164.8], 6), ([174.6, 220.0, 261.6], 6),
    ]
    y = np.concatenate([chord(f, d) for f, d in prog])
    y = 0.2 * y / np.abs(y).max()

    regions = detect_chords_btc(y, sr, vocab="large_voca", device="cpu")
    assert regions
    assert all("confidence" in r for r in regions)
    confs = [r["confidence"] for r in regions]
    assert all(0.0 <= c <= 1.0 for c in confs)
    # The regression this closes: confidence must not be the constant
    # 1.0 fiction. Real posteriors vary and sit below 1.
    assert any(c < 0.999 for c in confs)
