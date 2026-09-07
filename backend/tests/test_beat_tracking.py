"""Contract tests for tone_forge.beat_tracking (task 10).

Locks the shared tracker's observable behavior for both consumers
(unified_pipeline._track_beats, local_engine worker step 4b):

* pulse audio -> non-zero tempo, ascending beats, sane downbeats
* silence -> tempo 0.0, empty lists, method "none" or degraded
* librosa fallback path works when beat_this is unavailable
"""
import numpy as np
import pytest

from tone_forge import beat_tracking
from tone_forge.beat_tracking import track_beats

SR = 22050


def _pulse_clip(bpm: float = 120.0, duration_s: float = 8.0) -> np.ndarray:
    """Synthetic kick-like pulse train at the given tempo."""
    n = int(SR * duration_s)
    y = np.zeros(n, dtype=np.float32)
    period = int(SR * 60.0 / bpm)
    click_len = int(SR * 0.03)
    t = np.arange(click_len) / SR
    click = (np.sin(2 * np.pi * 180.0 * t)
             * np.exp(-t * 80.0)).astype(np.float32)
    for start in range(0, n - click_len, period):
        y[start:start + click_len] += click
    return y


def test_pulse_produces_tempo_and_grids():
    result = track_beats(_pulse_clip(), SR)

    assert set(result.keys()) == {
        "tempo_bpm", "beats_s", "downbeats_s", "method"
    }
    assert result["tempo_bpm"] > 0
    assert 50 <= result["tempo_bpm"] <= 260
    assert len(result["beats_s"]) >= 2
    beats = np.asarray(result["beats_s"])
    assert np.all(np.diff(beats) > 0)
    downbeats = np.asarray(result["downbeats_s"])
    assert len(downbeats) >= 1
    assert len(downbeats) <= len(beats)
    assert np.all(np.diff(downbeats) > 0)
    assert result["method"] in ("beat_this", "librosa")


def test_silence_degrades_without_raising():
    result = track_beats(np.zeros(SR * 3, dtype=np.float32), SR)

    assert result["tempo_bpm"] == 0.0
    assert result["beats_s"] == []
    assert result["downbeats_s"] == []


def test_librosa_fallback_when_beat_this_unavailable(monkeypatch):
    monkeypatch.setattr(beat_tracking, "_get_beat_this", lambda: None)

    result = track_beats(_pulse_clip(), SR)

    assert result["method"] == "librosa"
    assert result["tempo_bpm"] > 0
    assert len(result["beats_s"]) >= 2
    # librosa path derives downbeats as every 4th beat.
    assert result["downbeats_s"] == result["beats_s"][::4]


def test_stereo_input_downmixed():
    mono = _pulse_clip(duration_s=4.0)
    stereo = np.stack([mono, mono], axis=-1)

    result = track_beats(stereo, SR)

    assert result["tempo_bpm"] > 0


def test_tempo_from_beats_sanity_window():
    # 120 BPM -> 0.5s interval
    beats = np.arange(0, 10, 0.5)
    assert beat_tracking._tempo_from_beats(beats) == pytest.approx(120.0)
    # 20 BPM (3s interval) is outside the 40-240 window -> 0.0
    slow = np.arange(0, 30, 3.0)
    assert beat_tracking._tempo_from_beats(slow) == 0.0
    # fewer than 2 beats -> 0.0
    assert beat_tracking._tempo_from_beats(np.array([1.0])) == 0.0


def test_tempo_from_beats_unbiased_under_frame_quantization():
    """beat_this emits frame-quantized times: a true 0.629s beat arrives as
    a bimodal mix of 0.62s and 0.64s intervals. The median snapped to the
    majority mode (96.8 BPM instead of ~95.4) — a 1.6% bias that cut bar
    windows ~120ms short of the real downbeat and made kit-pad loops
    stutter at the wrap. The trimmed mean must recover the true tempo."""
    true = 0.629
    # quantize each cumulative beat time to a 20ms frame grid, majority
    # of intervals land on 0.62 with the remainder on 0.64
    times = np.round(np.cumsum(np.full(400, true)) / 0.02) * 0.02
    bpm = beat_tracking._tempo_from_beats(times)
    assert bpm == pytest.approx(60.0 / true, rel=0.005)
    # median on the same input demonstrates the failure this guards against
    assert abs(60.0 / float(np.median(np.diff(times))) - 60.0 / true) > 0.8


def test_tempo_from_downbeats_period():
    # downbeats every 2.5157s in 4/4 -> 95.4 BPM
    db = np.arange(0, 120, 2.5157)
    bpm = beat_tracking._tempo_from_beats(db, period_beats=4.0)
    assert bpm == pytest.approx(240.0 / 2.5157, rel=1e-3)
