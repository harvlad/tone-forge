"""Stem quality enhancement for lossy sources (YouTube-rip recovery v1).

Why: most analyzed songs arrive as lossy rips — AAC/Opus around
128 kbps with a hard spectral cliff near 15–16 kHz and codec-smeared
highs. Separation stacks a second loss on top: the separator's stem
sum never equals the mix, and the discarded residual carries real
"air" and glue. Nothing in the pipeline addressed either loss; stems
play back duller than the source they came from.

Two deterministic, model-free treatments (no hallucination beyond
harmonic extrapolation, CPU-cheap, offline-safe):

1. Bandwidth extension (``detect_lossy_cutoff`` + ``extend_bandwidth``):
   find the codec cliff, synthesize the missing top octave from the
   octave below it (full-wave rectification generates the 2f harmonics
   — the SBR trick), and shape the synthetic band to continue the
   measured spectral slope so the splice is level-honest rather than
   an exciter smiley.
2. Residual fill (``separation_residual``): mix − Σstems, the content
   every separator discards. Mixed back in at low level it restores
   the glue lost at separation time without touching stem identity.

Promotion rule (Riley house rule): NOTHING here ships user-facing
until it passes a blind ear gate — see
``scripts/make_stem_enhance_audit.py``. The spectral measurements
below are derivation tools, never the promotion metric (that would be
circular, and spectral objectives are false-positive machines).

Spectral measurement reuses ``ir_match`` primitives (median STFT
magnitude, fractional-octave smoothing) so enhancement and match-IR
export measure spectra with identical math — same anti-drift argument
as ``monitor.tuner``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np

from tone_forge.ir_match import _avg_magnitude, _fractional_octave_smooth

logger = logging.getLogger(__name__)

_NFFT = 8192

# Codec cliffs live here; a rolloff outside this window is either a
# clean full-band source (nothing to do) or a narrow-band stem whose
# darkness is musical, not codec damage.
_CUTOFF_SEARCH_LO_HZ = 9_000.0
_CUTOFF_SEARCH_HI_FRAC = 0.47  # of sample rate — just under Nyquist

# A codec cliff is steep: this much drop within a third of an octave.
_CLIFF_DROP_DB = 20.0
_CLIFF_WIDTH_OCTAVES = 1.0 / 3.0

# Synthetic-band level guard: never render the extension hotter than
# the extrapolated slope target, and cap total makeup so a mis-detected
# cutoff can't produce a hiss shelf.
_EXTENSION_MAX_GAIN_DB = 24.0


@dataclass(frozen=True)
class CutoffResult:
    """Detected lossy rolloff."""
    cutoff_hz: float
    drop_db: float          # measured cliff depth
    slope_db_per_octave: float  # natural rolloff below the cliff


def _median_spectrum_db(y: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray]:
    """(freqs, smoothed dB spectrum). ``_avg_magnitude``'s ``fs`` only
    labels the STFT — the bin math is nfft-driven, so the ir_match
    helper is reusable at any rate with our own frequency grid."""
    mag = _avg_magnitude(y, _NFFT)
    freqs = np.fft.rfftfreq(_NFFT, d=1.0 / sr)
    mag = _fractional_octave_smooth(mag, freqs, 1.0 / 6.0)
    return freqs, 20.0 * np.log10(mag)


def detect_lossy_cutoff(y: np.ndarray, sr: int) -> Optional[CutoffResult]:
    """Find the codec lowpass cliff, or None for full-band audio.

    Walk the smoothed median spectrum in the search window and pick the
    lowest frequency where level falls ``_CLIFF_DROP_DB`` within a
    third of an octave relative to the level just below it. Natural
    musical rolloff is gradual (a few dB/octave); only codec filters
    produce that step.
    """
    if len(y) < sr:  # < 1 s: spectrum estimate too flimsy to act on
        return None
    freqs, spec_db = _median_spectrum_db(y, sr)
    hi = min(_CUTOFF_SEARCH_HI_FRAC * sr, freqs[-1])
    search = (freqs >= _CUTOFF_SEARCH_LO_HZ) & (freqs <= hi)
    idxs = np.nonzero(search)[0]
    if len(idxs) < 8:
        return None

    # A codec cliff cuts from a *healthy* level. Content already deep
    # under the mid-band (a naturally dark stem far down its own
    # rolloff) is darkness, not codec damage — and extending noise
    # floor would only synthesize hiss.
    mid = (freqs >= 1_000.0) & (freqs <= 6_000.0)
    midband_db = float(np.median(spec_db[mid]))

    for i in idxs:
        f0 = freqs[i]
        f1 = f0 * (2.0 ** _CLIFF_WIDTH_OCTAVES)
        j = int(np.searchsorted(freqs, f1))
        if j >= len(freqs):
            break
        drop = spec_db[i] - spec_db[j]
        if drop < _CLIFF_DROP_DB:
            continue
        if spec_db[i] < midband_db - 45.0:
            continue
        # Natural slope over the two octaves below the candidate — the
        # curve the extension must continue, and the yardstick that
        # separates a cliff from a merely steep musical rolloff: the
        # cliff must drop well beyond what that slope predicts.
        lo = int(np.searchsorted(freqs, f0 / 4.0))
        band = slice(max(lo, 1), i + 1)
        octs = np.log2(freqs[band] / freqs[band][0])
        slope = float(np.polyfit(octs, spec_db[band], 1)[0]) if len(octs) > 4 else -3.0
        if drop < abs(slope) * _CLIFF_WIDTH_OCTAVES + 12.0:
            continue
        # Refine the knee: fractional-octave smoothing smears the cliff
        # downward in frequency, so the first bin that *sees* the drop
        # sits below the true corner. The corner is the highest bin (up
        # to an octave out) still within 6 dB of the trigger level.
        k_hi = int(np.searchsorted(freqs, min(f0 * 2.0, hi)))
        near = np.nonzero(spec_db[i:k_hi] >= spec_db[i] - 6.0)[0]
        knee = i + int(near.max()) if len(near) else i
        return CutoffResult(
            cutoff_hz=float(freqs[knee]),
            drop_db=float(drop),
            slope_db_per_octave=slope,
        )
    return None


def extend_bandwidth(
    y: np.ndarray,
    sr: int,
    cutoff: CutoffResult,
    amount: float = 1.0,
) -> np.ndarray:
    """Synthesize the band above the codec cliff (SBR-style).

    Source = the octave below the cliff; full-wave rectification
    doubles its frequencies into the dead band; the synthetic band is
    then spectrally shaped to continue ``slope_db_per_octave`` from
    the level at the cliff. ``amount`` scales the blend (1.0 = full
    slope-honest level; audit packs may render 0.5 variants).

    Returns a new array; the region below the cliff is bit-identical
    to the input (the synthesis path is high-passed above it).
    """
    from scipy.signal import butter, sosfiltfilt

    if amount <= 0.0:
        return y.copy()
    f_c = cutoff.cutoff_hz
    nyq = sr / 2.0
    if f_c >= nyq * 0.95:
        return y.copy()

    # Source band: the octave under the cliff.
    sos_band = butter(4, [f_c / 2.0 / nyq, min(f_c * 0.97, nyq * 0.99) / nyq],
                      btype="band", output="sos")
    src = sosfiltfilt(sos_band, y)

    # Full-wave rectification: |x| of a band at f contains 2f, 4f…
    # even harmonics — dense enough over a full octave of source
    # material to read as "air", not as a pitched artifact.
    harm = np.abs(src)
    harm -= harm.mean()

    # Keep only the synthetic region.
    sos_hp = butter(6, min(f_c * 0.99, nyq * 0.98) / nyq,
                    btype="high", output="sos")
    synth = sosfiltfilt(sos_hp, harm)
    if not np.any(np.abs(synth) > 1e-12):
        return y.copy()

    # Shape the synthetic band to the extrapolated slope target.
    freqs, spec_db = _median_spectrum_db(y, sr)
    _, synth_db = _median_spectrum_db(synth, sr)
    i_c = int(np.searchsorted(freqs, f_c))
    level_at_cliff = spec_db[max(i_c - 2, 0)]
    above = freqs > f_c
    octs_above = np.log2(np.maximum(freqs, 1.0) / f_c)
    target_db = level_at_cliff + cutoff.slope_db_per_octave * octs_above
    gain_db = np.zeros_like(freqs)
    gain_db[above] = np.clip(
        target_db[above] - synth_db[above],
        -80.0, _EXTENSION_MAX_GAIN_DB,
    )
    gain = 10.0 ** (gain_db / 20.0)
    gain = _fractional_octave_smooth(gain, freqs, 1.0 / 3.0)
    gain[~above] = 0.0  # below the cliff the synthesis contributes nothing

    # Zero-phase FFT filtering — offline path, latency-free is fine.
    n = len(synth)
    spec = np.fft.rfft(synth)
    grid = np.fft.rfftfreq(n, d=1.0 / sr)
    spec *= np.interp(grid, freqs, gain, left=0.0, right=gain[-1])
    shaped = np.fft.irfft(spec, n=n)

    return (y + amount * shaped).astype(y.dtype, copy=False)


def separation_residual(
    mix: np.ndarray,
    stems: Dict[str, np.ndarray],
) -> np.ndarray:
    """mix − Σstems: the content the separator discarded.

    All arrays must share the mix's sample rate; lengths are trimmed
    to the shortest (separators pad/truncate edges inconsistently).
    The result is the honest "glue" layer — mixed back at low level it
    restores air lost at separation without re-attributing it to any
    one stem (per-stem re-attribution is a v2 question for the ears).
    """
    if not stems:
        return np.zeros(0, dtype=np.float64)
    n = min(len(mix), *(len(s) for s in stems.values()))
    total = np.zeros(n, dtype=np.float64)
    for s in stems.values():
        total += s[:n]
    return mix[:n].astype(np.float64) - total


def enhance_stem(
    y: np.ndarray,
    sr: int,
    amount: float = 1.0,
) -> tuple[np.ndarray, Optional[CutoffResult]]:
    """Convenience: detect + extend one stem. No-op on full-band audio."""
    cut = detect_lossy_cutoff(y, sr)
    if cut is None:
        return y.copy(), None
    return extend_bandwidth(y, sr, cut, amount=amount), cut
