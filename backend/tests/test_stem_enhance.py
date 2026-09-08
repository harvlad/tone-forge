"""Unit tests for stems.enhance (lossy-cutoff detection, bandwidth
extension, separation residual).

Synthetic signals only — the audible question is the blind pack's job
(scripts/make_stem_enhance_audit.py); these tests pin the DSP
contracts: cutoffs are found where they were rendered, extension adds
energy ONLY above the cliff, full-band audio is left untouched.
"""
from __future__ import annotations

import numpy as np
import pytest

from tone_forge.ir_match import _avg_magnitude
from tone_forge.stems.enhance import (
    detect_lossy_cutoff,
    enhance_stem,
    extend_bandwidth,
    separation_residual,
)

_SR = 44_100


def _band_energy_db(y: np.ndarray, sr: int, f_lo: float, f_hi: float) -> float:
    mag = _avg_magnitude(y, 8192)
    freqs = np.fft.rfftfreq(8192, d=1.0 / sr)
    band = (freqs >= f_lo) & (freqs < f_hi)
    return float(20.0 * np.log10(np.sqrt((mag[band] ** 2).mean()) + 1e-15))


def _rich_signal(seconds: float = 4.0, sr: int = _SR, seed: int = 7) -> np.ndarray:
    """Broadband harmonic-ish content: filtered noise + partial stack."""
    rng = np.random.default_rng(seed)
    n = int(seconds * sr)
    t = np.arange(n) / sr
    y = 0.3 * rng.standard_normal(n)
    for f0 in (110.0, 220.0, 330.0):
        for k in range(1, 60):
            f = f0 * k
            if f > sr / 2 * 0.95:
                break
            y += (0.2 / k) * np.sin(2 * np.pi * f * t + rng.uniform(0, 6.28))
    return (y / np.abs(y).max() * 0.5).astype(np.float64)


def _lowpassed(y: np.ndarray, sr: int, cutoff_hz: float) -> np.ndarray:
    """Brick-ish codec-style lowpass via FFT zeroing + slight edge taper."""
    spec = np.fft.rfft(y)
    freqs = np.fft.rfftfreq(len(y), d=1.0 / sr)
    gain = np.ones_like(freqs)
    gain[freqs > cutoff_hz] = 0.0
    taper = (freqs > cutoff_hz * 0.98) & (freqs <= cutoff_hz)
    gain[taper] = np.linspace(1.0, 0.1, taper.sum())
    return np.fft.irfft(spec * gain, n=len(y))


class TestDetectLossyCutoff:
    def test_finds_rendered_cliff(self):
        for target in (12_000.0, 15_500.0):
            y = _lowpassed(_rich_signal(), _SR, target)
            cut = detect_lossy_cutoff(y, _SR)
            assert cut is not None, f"missed cliff at {target}"
            assert abs(cut.cutoff_hz - target) / target < 0.15
            assert cut.drop_db >= 20.0

    def test_full_band_returns_none(self):
        assert detect_lossy_cutoff(_rich_signal(), _SR) is None

    def test_short_audio_returns_none(self):
        assert detect_lossy_cutoff(_rich_signal(seconds=0.5), _SR) is None

    def test_naturally_dark_stem_not_flagged(self):
        # Gentle 12 dB/oct musical rolloff from 2 kHz — dark, not lossy.
        from scipy.signal import butter, sosfiltfilt
        sos = butter(2, 2_000.0 / (_SR / 2), btype="low", output="sos")
        y = sosfiltfilt(sos, _rich_signal())
        assert detect_lossy_cutoff(y, _SR) is None


class TestExtendBandwidth:
    def test_adds_energy_only_above_cliff(self):
        y = _lowpassed(_rich_signal(), _SR, 12_000.0)
        cut = detect_lossy_cutoff(y, _SR)
        assert cut is not None
        out = extend_bandwidth(y, _SR, cut)
        # Dead band gains real energy…
        before = _band_energy_db(y, _SR, 13_000, 20_000)
        after = _band_energy_db(out, _SR, 13_000, 20_000)
        assert after - before > 10.0
        # …while the band below the cliff stays put (< 0.5 dB shift).
        assert abs(
            _band_energy_db(out, _SR, 200, 8_000)
            - _band_energy_db(y, _SR, 200, 8_000)
        ) < 0.5

    def test_extension_continues_slope_not_hiss_shelf(self):
        y = _lowpassed(_rich_signal(), _SR, 12_000.0)
        cut = detect_lossy_cutoff(y, _SR)
        out = extend_bandwidth(y, _SR, cut)
        below = _band_energy_db(out, _SR, 8_000, 12_000)
        above = _band_energy_db(out, _SR, 13_000, 19_000)
        # Synthetic band must sit BELOW the band under the cliff —
        # continuation of a falling slope, never a boosted shelf.
        assert above < below

    def test_amount_zero_is_identity(self):
        y = _lowpassed(_rich_signal(), _SR, 12_000.0)
        cut = detect_lossy_cutoff(y, _SR)
        np.testing.assert_array_equal(extend_bandwidth(y, _SR, cut, amount=0.0), y)

    def test_enhance_stem_noop_on_full_band(self):
        y = _rich_signal()
        out, cut = enhance_stem(y, _SR)
        assert cut is None
        np.testing.assert_array_equal(out, y)


class TestSeparationResidual:
    def test_exact_residual_recovered(self):
        rng = np.random.default_rng(3)
        a = rng.standard_normal(_SR)
        b = rng.standard_normal(_SR)
        glue = 0.1 * rng.standard_normal(_SR)
        mix = a + b + glue
        res = separation_residual(mix, {"a": a, "b": b})
        np.testing.assert_allclose(res, glue, atol=1e-9)

    def test_length_mismatch_trims(self):
        mix = np.ones(1000)
        res = separation_residual(mix, {"a": np.ones(990)})
        assert len(res) == 990

    def test_empty_stems(self):
        assert len(separation_residual(np.ones(100), {})) == 0


@pytest.mark.parametrize("cutoff", [11_000.0, 16_000.0])
def test_roundtrip_detect_then_extend(cutoff):
    y = _lowpassed(_rich_signal(seed=11), _SR, cutoff)
    out, cut = enhance_stem(y, _SR)
    assert cut is not None
    assert _band_energy_db(out, _SR, cut.cutoff_hz * 1.1, _SR / 2 * 0.9) > \
        _band_energy_db(y, _SR, cut.cutoff_hz * 1.1, _SR / 2 * 0.9) + 6.0
