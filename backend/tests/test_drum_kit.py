"""Drum Kit builder (performance.drum_kit) — hit detection, classification,
and SamplePack assembly against a synthetic drum stem with known ground truth.

The stem is built from first principles so classification is verifiable:
  * kick  = 55 Hz decaying sine  (low band dominant)
  * snare = 180–4000 Hz noise burst (mid band dominant, noisy/flat spectrum)
  * hat   = 6–10 kHz noise tick     (high band dominant, very short decay)
placed on a 120 BPM grid: kicks on beats 0+2, snares on 1+3, hats on the
off-beat eighths — no simultaneous onsets, so each detected hit has one
unambiguous class.
"""
from __future__ import annotations

import numpy as np
import pytest

from tone_forge.performance import drum_kit as dk

SR = 22050
BPM = 120.0
BEAT = 60.0 / BPM  # 0.5 s
BARS = 4


def _synth_drum_stem(tmp_path):
    import soundfile as sf
    from scipy import signal as sig

    dur = BARS * 4 * BEAT + 1.0
    y = np.zeros(int(dur * SR), dtype=np.float64)

    def _add(t, x):
        i = int(t * SR)
        y[i:i + x.size] += x[: y.size - i]

    rng = np.random.default_rng(42)

    n_k = int(0.30 * SR)
    tk = np.arange(n_k) / SR
    kick = np.sin(2 * np.pi * 55.0 * tk) * np.exp(-tk / 0.08)

    n_s = int(0.25 * SR)
    ts = np.arange(n_s) / SR
    sos_s = sig.butter(4, [180 / (SR / 2), 4000 / (SR / 2)], btype="band", output="sos")
    snare = sig.sosfilt(sos_s, rng.standard_normal(n_s)) * np.exp(-ts / 0.06)
    snare *= 0.8 / (np.abs(snare).max() + 1e-9)

    n_h = int(0.08 * SR)
    th = np.arange(n_h) / SR
    sos_h = sig.butter(4, [6000 / (SR / 2), 10000 / (SR / 2)], btype="band", output="sos")
    hat = sig.sosfilt(sos_h, rng.standard_normal(n_h)) * np.exp(-th / 0.015)
    hat *= 0.5 / (np.abs(hat).max() + 1e-9)

    kick_times, snare_times, hat_times = [], [], []
    for bar in range(BARS):
        t0 = 0.5 + bar * 4 * BEAT  # 0.5 s lead-in silence
        for b in (0, 2):
            kick_times.append(t0 + b * BEAT)
        for b in (1, 3):
            snare_times.append(t0 + b * BEAT)
        for e in range(8):  # off-beat eighths only
            hat_times.append(t0 + e * BEAT + BEAT / 2)

    for t in kick_times:
        _add(t, kick)
    for t in snare_times:
        _add(t, snare)
    for t in hat_times:
        _add(t, hat)

    y *= 0.9 / (np.abs(y).max() + 1e-9)
    path = tmp_path / "drums.wav"
    sf.write(path, y.astype(np.float32), SR)
    return path, {"kick": kick_times, "snare": snare_times, "hat": hat_times}


@pytest.fixture(scope="module")
def hits_table(tmp_path_factory):
    path, truth = _synth_drum_stem(tmp_path_factory.mktemp("drumstem"))
    table = dk.detect_drum_hits(path)
    return table, truth


def test_detect_finds_and_classifies_hits(hits_table):
    table, truth = hits_table
    assert table["version"] == dk.HITS_VERSION
    hits = table["hits"]
    total_truth = sum(len(v) for v in truth.values())
    # Every synthetic onset is a strong isolated transient — expect near-full
    # recall (some tolerance for the detector's peak-picking at the seams).
    assert len(hits) >= int(total_truth * 0.8)

    classes = {h["cls"] for h in hits}
    assert "kick" in classes
    assert "snare" in classes
    assert classes & {"hat_closed", "hat_open"}

    def _nearest_truth(t):
        best_cls, best_dt = None, 1e9
        for cls, times in truth.items():
            for tt in times:
                if abs(tt - t) < best_dt:
                    best_cls, best_dt = cls, abs(tt - t)
        return best_cls, best_dt

    # Per-hit accuracy: match each detected hit to its nearest ground-truth
    # onset (within 60 ms) and check the class agrees. Signal-level
    # classification won't be perfect; the bar is "clearly working".
    matched, correct = 0, 0
    truth_of = {"kick": "kick", "snare": "snare", "hat": "hat"}
    for h in hits:
        cls_truth, dt = _nearest_truth(h["t"])
        if dt > 0.06:
            continue
        matched += 1
        got = "hat" if h["cls"].startswith("hat") else h["cls"]
        if got == truth_of[cls_truth]:
            correct += 1
    assert matched >= int(total_truth * 0.7)
    assert correct / matched >= 0.8


def test_hit_fields_sane(hits_table):
    table, _ = hits_table
    for h in table["hits"]:
        assert h["end"] > h["t"]
        assert 0.0 <= h["strength"] <= 1.0
        assert 0.0 <= h["isolation"] <= 1.0
        assert h["cls"] in dk._TAIL_SEC


def test_build_drum_kit_manifest_shape(hits_table):
    table, _ = hits_table
    # Downbeat grid longer than the synthetic stem is fine here — the groove
    # picker only needs the grid + duration; duration_sec caps the spans.
    downbeats = [0.5 + bar * 4 * BEAT for bar in range(17)]
    result = {
        dk.DRUM_HITS_RESULT_KEY: table,
        "downbeats_s": downbeats,
        "duration_sec": downbeats[-1] + 1.0,
        "sections": [{"start_time": 0.0, "end_time": 40.0, "type": "verse"}],
    }
    kit = dk.build_drum_kit("entry123", result)

    assert kit["manifestVersion"] == 2
    assert kit["packId"] == "drumkit-entry123"
    assert kit["family"] == "percussion"
    pads = kit["pads"]
    assert 1 <= len(pads) <= 16
    assert [p["padIdx"] for p in pads] == list(range(len(pads)))

    one_shots = [p for p in pads if not p["loopable"]]
    grooves = [p for p in pads if p["loopable"]]
    assert len(one_shots) >= 4
    assert len(grooves) == dk._GROOVE_PADS  # downbeat grid present → full row

    for p in one_shots:
        s = p["stemSlice"]
        assert s["stemRole"] == "drums"
        assert s["endSec"] > s["startSec"]
        # One-shots must trigger on the tap, not the next bar.
        assert p["defaultQuantize"] == "off"
        assert p["category"] == "DRUMS"
    # Hat pads choke each other (open/closed pair behaviour).
    hat_pads = [p for p in one_shots if p["name"].startswith("Hat")]
    assert all(p.get("chokeGroup") == 1 for p in hat_pads)

    for p in grooves:
        assert p["defaultQuantize"] == "1 bar"
        assert p["loopEndSec"] > p["loopStartSec"]

    # Variant exemplars of one class must be distinct audio, not the same bar.
    kick_pads = [p for p in one_shots if p["name"].startswith("Kick")]
    starts = [p["stemSlice"]["startSec"] for p in kick_pads]
    assert len(starts) == len(set(starts))


def test_build_drum_kit_without_hits_raises():
    with pytest.raises(ValueError):
        dk.build_drum_kit("x", {})
    with pytest.raises(ValueError):
        dk.build_drum_kit("x", {dk.DRUM_HITS_RESULT_KEY: {"version": 1, "hits": []}})


def test_groove_pads_absent_without_downbeats(hits_table):
    table, _ = hits_table
    kit = dk.build_drum_kit("y", {dk.DRUM_HITS_RESULT_KEY: table})
    assert all(not p["loopable"] for p in kit["pads"])
