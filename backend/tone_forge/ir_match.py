"""Match-EQ impulse response generation (tone-transfer v1).

Given a *source* recording (the user's guitar through a neutral or
generated preset) and a *target* recording (the song's separated guitar
stem), compute the smoothed spectral magnitude ratio target/source and
render it as a short minimum-phase FIR — a standard "match EQ → IR"
capture (Spectrum Thief / IR MatchMaker pattern).

The IR captures the *linear* spectral difference only: no distortion
character, no time-domain ambience. It rides on top of the coarse
amp/gain guess from the ToneDescriptor path and closes the cab/mic/EQ
fingerprint gap that amp-family classification leaves open.

Output conforms to the Helix IR import spec: 48 kHz, 16-bit, mono,
2048 samples (1024 optional to halve DSP cost). kb.line6.com: longer
files are truncated, shorter padded — we render exactly.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

IR_SAMPLE_RATE = 48_000
IR_TAPS_FULL = 2048
IR_TAPS_HALF = 1024

# Spectral-ratio guard rails. Separated stems have bleed and nulls;
# unbounded ratios turn artifacts into ±40 dB EQ swings.
_MAX_BOOST_DB = 18.0
_MAX_CUT_DB = -24.0
# Below/above the guitar band the ratio is bleed-dominated — flatten.
_BAND_LO_HZ = 70.0
_BAND_HI_HZ = 12_000.0
# Fractional-octave smoothing width for the log-domain ratio.
_SMOOTH_OCTAVES = 1.0 / 3.0


@dataclass(frozen=True)
class MatchIRResult:
    """A rendered match IR plus diagnostics."""
    ir: np.ndarray            # float32, len == taps
    sample_rate: int
    ratio_db_range: Tuple[float, float]   # applied min/max gain in dB
    source_seconds: float
    target_seconds: float


def _load_mono_48k(path: Path) -> np.ndarray:
    import soundfile as sf
    from scipy.signal import resample_poly

    data, sr = sf.read(str(path), dtype="float64", always_2d=True)
    mono = data.mean(axis=1)
    if sr != IR_SAMPLE_RATE:
        from math import gcd
        g = gcd(int(sr), IR_SAMPLE_RATE)
        mono = resample_poly(mono, IR_SAMPLE_RATE // g, int(sr) // g)
    return mono


def _avg_magnitude(x: np.ndarray, nfft: int) -> np.ndarray:
    """Median-averaged STFT magnitude — robust to transients/silence."""
    from scipy.signal import stft

    _, _, Z = stft(x, fs=IR_SAMPLE_RATE, nperseg=nfft, noverlap=nfft // 2,
                   window="hann", padded=True)
    mags = np.abs(Z)
    # Drop near-silent frames so gaps between phrases don't bias the
    # spectrum estimate toward the noise floor.
    frame_rms = np.sqrt((mags ** 2).mean(axis=0))
    keep = frame_rms > (frame_rms.max() * 1e-3)
    if keep.any():
        mags = mags[:, keep]
    return np.median(mags, axis=1) + 1e-12


def _fractional_octave_smooth(mag: np.ndarray, freqs: np.ndarray,
                              octaves: float) -> np.ndarray:
    """Gaussian smoothing of log-magnitude on a log-frequency grid."""
    from scipy.ndimage import gaussian_filter1d

    # Interpolate onto a uniform log-f grid (skip DC).
    f_lo, f_hi = max(freqs[1], 10.0), freqs[-1]
    n_pts = 1024
    log_grid = np.geomspace(f_lo, f_hi, n_pts)
    log_mag = np.interp(log_grid, freqs, np.log(mag))
    # Grid spacing in octaves is uniform: total octaves / n_pts.
    total_octaves = np.log2(f_hi / f_lo)
    sigma = (octaves / total_octaves) * n_pts / 2.0
    smoothed = gaussian_filter1d(log_mag, sigma=max(sigma, 1.0))
    # Back to the linear-f grid.
    out = np.interp(freqs, log_grid, smoothed,
                    left=smoothed[0], right=smoothed[-1])
    return np.exp(out)


def _minimum_phase_fir(mag: np.ndarray, taps: int) -> np.ndarray:
    """Minimum-phase FIR realizing `mag` (rfft grid) via real cepstrum."""
    n_full = (len(mag) - 1) * 2
    log_mag = np.log(mag)
    # Real cepstrum of the log-magnitude.
    ceps = np.fft.irfft(log_mag, n=n_full)
    # Fold: keep c[0], double 1..N-1, zero the anticausal half.
    fold = np.zeros_like(ceps)
    fold[0] = ceps[0]
    fold[1:n_full // 2] = 2.0 * ceps[1:n_full // 2]
    fold[n_full // 2] = ceps[n_full // 2]
    h_min_spec = np.exp(np.fft.rfft(fold, n=n_full))
    h = np.fft.irfft(h_min_spec, n=n_full)[:taps]
    # Fade the tail so truncation doesn't ring.
    fade_len = taps // 4
    window = np.ones(taps)
    window[-fade_len:] = 0.5 * (1 + np.cos(np.linspace(0, np.pi, fade_len)))
    return (h * window).astype(np.float64)


def compute_match_ir(
    source: np.ndarray,
    target: np.ndarray,
    taps: int = IR_TAPS_FULL,
    smooth_octaves: float = _SMOOTH_OCTAVES,
) -> MatchIRResult:
    """Compute the minimum-phase match IR from 48 kHz mono arrays."""
    if len(source) < IR_SAMPLE_RATE // 2 or len(target) < IR_SAMPLE_RATE // 2:
        raise ValueError("need at least 0.5 s of source and target audio")

    nfft = 8192
    freqs = np.fft.rfftfreq(nfft, d=1.0 / IR_SAMPLE_RATE)
    src_mag = _avg_magnitude(source, nfft)
    tgt_mag = _avg_magnitude(target, nfft)

    ratio = tgt_mag / src_mag
    ratio = _fractional_octave_smooth(ratio, freqs, smooth_octaves)

    # Normalize so the mid-band (200 Hz – 2 kHz) sits at unity gain —
    # the IR shapes tone, it must not restage loudness.
    band = (freqs >= 200.0) & (freqs <= 2_000.0)
    ratio /= np.exp(np.log(ratio[band]).mean())

    # Flatten outside the instrument band (bleed-dominated regions).
    ratio[freqs < _BAND_LO_HZ] = ratio[freqs >= _BAND_LO_HZ][0]
    ratio[freqs > _BAND_HI_HZ] = ratio[freqs <= _BAND_HI_HZ][-1]

    # Clamp to the guard rails.
    ratio_db = 20.0 * np.log10(ratio)
    ratio_db = np.clip(ratio_db, _MAX_CUT_DB, _MAX_BOOST_DB)
    ratio = 10.0 ** (ratio_db / 20.0)

    ir = _minimum_phase_fir(ratio, taps)
    # Peak-normalize with headroom for 16-bit render.
    peak = np.abs(ir).max()
    if peak > 0:
        ir = ir * (0.891 / peak)   # −1 dBFS

    return MatchIRResult(
        ir=ir.astype(np.float32),
        sample_rate=IR_SAMPLE_RATE,
        ratio_db_range=(float(ratio_db.min()), float(ratio_db.max())),
        source_seconds=len(source) / IR_SAMPLE_RATE,
        target_seconds=len(target) / IR_SAMPLE_RATE,
    )


def match_ir_from_files(
    source_path: str | Path,
    target_path: str | Path,
    out_path: Optional[str | Path] = None,
    taps: int = IR_TAPS_FULL,
) -> MatchIRResult:
    """File-level wrapper: load, compute, optionally write the IR wav."""
    result = compute_match_ir(
        _load_mono_48k(Path(source_path)),
        _load_mono_48k(Path(target_path)),
        taps=taps,
    )
    if out_path is not None:
        write_ir_wav(Path(out_path), result)
    return result


def write_ir_wav(path: Path, result: MatchIRResult) -> None:
    """Write the IR in Helix's native ingest format (48k/16-bit/mono)."""
    import soundfile as sf

    sf.write(str(path), result.ir, result.sample_rate, subtype="PCM_16")
    logger.info(
        "match IR written: %s (%d taps, gain %.1f..%.1f dB)",
        path, len(result.ir), *result.ratio_db_range,
    )
