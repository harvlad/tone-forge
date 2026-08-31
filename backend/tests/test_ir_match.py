"""ir_match: minimum-phase match-EQ IR against known synthetic filters."""

import numpy as np
import pytest
from scipy import signal

from tone_forge.ir_match import (
    IR_SAMPLE_RATE,
    IR_TAPS_FULL,
    IR_TAPS_HALF,
    compute_match_ir,
)


def _noise(seconds=4.0, seed=7):
    rng = np.random.default_rng(seed)
    return rng.standard_normal(int(IR_SAMPLE_RATE * seconds))


def _ir_response_db(ir, freqs_hz):
    w, h = signal.freqz(ir, worN=4096, fs=IR_SAMPLE_RATE)
    mag_db = 20 * np.log10(np.abs(h) + 1e-12)
    return np.interp(freqs_hz, w, mag_db)


def test_identity_yields_flat_ir():
    x = _noise()
    result = compute_match_ir(x, x)
    probe = np.array([150, 300, 800, 1500, 3000, 6000])
    resp = _ir_response_db(result.ir, probe)
    assert np.all(np.abs(resp - resp.mean()) < 1.0)


def test_recovers_known_lowpass_shape():
    x = _noise(seconds=6.0)
    # Target = source through a 2 kHz 2nd-order Butterworth lowpass.
    sos = signal.butter(2, 2_000, btype="low", fs=IR_SAMPLE_RATE, output="sos")
    y = signal.sosfilt(sos, x)
    result = compute_match_ir(x, y)

    probe = np.array([500.0, 4_000.0, 8_000.0])
    resp = _ir_response_db(result.ir, probe)
    # Relative tilt: 4 kHz should sit ~12 dB under 500 Hz (24 dB/oct
    # above the knee, 1/3-oct smoothing softens it), 8 kHz well below.
    assert resp[0] - resp[1] > 8.0
    assert resp[0] - resp[2] > 15.0


def test_boost_clamped():
    x = _noise(seconds=6.0)
    # Target with a violent +40 dB high shelf — clamps must cap it.
    sos = signal.butter(2, 4_000, btype="high", fs=IR_SAMPLE_RATE, output="sos")
    y = x + 100.0 * signal.sosfilt(sos, x)
    result = compute_match_ir(x, y)
    assert result.ratio_db_range[1] <= 18.0 + 1e-6
    assert result.ratio_db_range[0] >= -24.0 - 1e-6


def test_output_format_helix_spec():
    x = _noise()
    result = compute_match_ir(x, x)
    assert result.sample_rate == 48_000
    assert len(result.ir) == IR_TAPS_FULL
    assert result.ir.dtype == np.float32
    assert np.abs(result.ir).max() <= 0.9

    half = compute_match_ir(x, x, taps=IR_TAPS_HALF)
    assert len(half.ir) == IR_TAPS_HALF


def test_rejects_too_short_input():
    x = _noise(seconds=0.2)
    with pytest.raises(ValueError):
        compute_match_ir(x, x)
