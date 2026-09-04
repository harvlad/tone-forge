"""Per-song chain tuning — derive a tuned MonitorChain from a stem.

The bank's curated chains are starting points, not answers: a discrete
bank cannot cover a continuous tone space (the 89-clip blind-label
audit measured top-1 family fit at 6.7%, ranking AUC 0.506). This
module closes the gap the other way — take a base chain (picked by the
user's ear or the fallback policy) and bend its *continuous* knobs
toward the analyzed guitar stem.

V2 tunes the full continuous surface the wire exposes:

* EQ + input high-pass from a spectral comparison — smoothed magnitude
  ratio between the stem and the chain's rendered reference WAV,
  collapsed onto the four EQ bands, clamped (±12 dB after the v1 audit
  showed ±5 dB realized ~3 dB of correction: inaudible).
* Drive / reverb mix / comp ratio from robust envelope statistics
  (crest factor, tail floor), each a plain monotone map moved only
  halfway from the base value. Adaptive per-clip estimators have been
  blind-refuted in this codebase before (separation, 2026-08) — these
  ship nowhere until the blind audit passes them.
* No delay: the chain spec has no delay block; adding one is a
  Connect/jam-desktop DSP change, tracked separately.

The derived spec rides the existing wire shape (`parameters` dict is
forwarded verbatim to Connect, which clamps every field on decode), so
no protocol change is needed to audition a tuned chain.
"""
from __future__ import annotations

import copy
import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

import numpy as np

from tone_forge.contracts import MonitorChain
from tone_forge.ir_match import (
    IR_SAMPLE_RATE,
    _avg_magnitude,
    _fractional_octave_smooth,
    _load_mono_48k,
)
from tone_forge.monitor.loader import load_chain

logger = logging.getLogger(__name__)

_CHAINS_ROOT = Path(__file__).resolve().parent / "chains"

# The four wire EQ bands, as (name, lo_hz, hi_hz). Ranges follow the
# conventional guitar-amp voicing the chain renders were dialed
# against, not any DSP constant in Connect — the goal is "which knob
# moves this region", not a filter spec.
_EQ_BANDS: Tuple[Tuple[str, float, float], ...] = (
    ("bass_db", 60.0, 250.0),
    ("mid_db", 250.0, 2000.0),
    ("treble_db", 2000.0, 6000.0),
    ("presence_db", 6000.0, 12000.0),
)

# Per-band correction clamp. The v1 audit (±5 dB) realized only ~3 dB
# of audible correction against measured stem-vs-render gaps of
# 10-37 dB — verdict "doesn't sound like it's tuning at all". ±12 dB
# trades some base-chain character for corrections that exist.
_MAX_BAND_DELTA_DB = 12.0

# High-pass candidates the tuner may pick from. Discrete on purpose:
# the input HPF is a rumble gate, not a tone control, and a continuous
# estimate would just add jitter.
_HPF_CHOICES_HZ = (60.0, 90.0, 120.0, 150.0)

_SMOOTH_OCTAVES = 1.0 / 3.0
_NFFT = 8192


@dataclass(frozen=True)
class TunedChainResult:
    """A derived chain plus the evidence that produced it."""

    chain: MonitorChain
    band_deltas_db: Dict[str, float]     # applied (post-clamp) per band
    high_pass_hz: float
    debug: Dict[str, object]


def derive_tuned_chain(
    base_chain_id: str,
    stem_path: Path,
    *,
    chains_root: Optional[Path] = None,
) -> TunedChainResult:
    """Bend ``base_chain_id``'s EQ toward the guitar stem at ``stem_path``.

    Raises
    ------
    FileNotFoundError
        Missing base chain YAML or its reference render — a tuned
        chain without a measured reference would be tuning against a
        guess, so we refuse rather than degrade.
    ValueError
        Stem too short/silent to measure (needs >= 0.5 s of signal).
    """
    root = chains_root or _CHAINS_ROOT
    base = load_chain(base_chain_id)
    reference_wav = root / f"{base_chain_id}.wav"
    if not reference_wav.is_file():
        raise FileNotFoundError(
            f"no reference render for {base_chain_id} at {reference_wav}; "
            "render it (scripts/render_chain_references.py stage 1) "
            "before tuning against this chain"
        )

    stem = _load_mono_48k(Path(stem_path))
    ref = _load_mono_48k(reference_wav)
    if len(stem) < IR_SAMPLE_RATE // 2:
        raise ValueError("stem too short to tune against (need >= 0.5 s)")

    freqs = np.fft.rfftfreq(_NFFT, d=1.0 / IR_SAMPLE_RATE)
    stem_mag = _fractional_octave_smooth(
        _avg_magnitude(stem, _NFFT), freqs, _SMOOTH_OCTAVES,
    )
    ref_mag = _fractional_octave_smooth(
        _avg_magnitude(ref, _NFFT), freqs, _SMOOTH_OCTAVES,
    )

    # Level-invariant ratio: normalize both spectra to their mid-band
    # energy so overall loudness difference between the stem recording
    # and the reference render doesn't masquerade as an EQ move.
    ratio_db = 20.0 * np.log10(stem_mag / ref_mag)
    mid = (freqs >= 250.0) & (freqs < 2000.0)
    ratio_db -= float(np.mean(ratio_db[mid]))

    band_deltas: Dict[str, float] = {}
    for name, lo, hi in _EQ_BANDS:
        sel = (freqs >= lo) & (freqs < hi)
        raw = float(np.mean(ratio_db[sel]))
        band_deltas[name] = _clamp(raw, -_MAX_BAND_DELTA_DB, _MAX_BAND_DELTA_DB)

    high_pass = _pick_high_pass(stem, base)
    drive_est = _estimate_drive(stem)
    reverb_est = _estimate_reverb_mix(stem)
    comp_est = _estimate_comp(stem)

    parameters = copy.deepcopy(base.parameters)
    eq = dict(parameters.get("eq") or {})
    for name, delta in band_deltas.items():
        eq[name] = round(float(eq.get(name, 0.0)) + delta, 1)
    parameters["eq"] = eq
    parameters.setdefault("input", {})
    parameters["input"] = {**parameters["input"], "high_pass_hz": high_pass}

    # Nonlinear knobs move toward the stem's measured character, but
    # never further than halfway from the base value — the estimators
    # are envelope/crest proxies, not renders, and a half-step keeps a
    # bad estimate recoverable by ear. Each is a plain monotone map
    # from one robust statistic; anything cleverer needs the blind
    # audit to earn it first.
    gain_stage = dict(parameters.get("gain_stage") or {})
    base_drive = float(gain_stage.get("drive", 0.3))
    gain_stage["drive"] = round(base_drive + 0.5 * (drive_est - base_drive), 3)
    parameters["gain_stage"] = gain_stage

    reverb = dict(parameters.get("reverb") or {})
    base_mix = float(reverb.get("mix", 0.1))
    reverb["mix"] = round(base_mix + 0.5 * (reverb_est - base_mix), 3)
    parameters["reverb"] = reverb

    comp = dict(parameters.get("comp") or {})
    if comp_est is not None:
        comp["enabled"] = True
        comp["ratio"] = comp_est
    parameters["comp"] = comp

    chain = MonitorChain(
        id=f"{base.id}.tuned",
        family=base.family,
        display_name=f"{base.display_name} (tuned)",
        description=(
            f"{base.display_name} re-voiced against this song's guitar "
            "stem (EQ + high-pass only)."
        ),
        parameters=parameters,
    )
    debug = {
        "base_chain_id": base.id,
        "reference_wav": str(reference_wav),
        "band_deltas_db": dict(band_deltas),
        "high_pass_hz": high_pass,
        "drive_target": round(drive_est, 3),
        "reverb_mix_target": round(reverb_est, 3),
        "comp_ratio_target": comp_est,
        "ratio_db_range": (
            round(float(np.min(ratio_db)), 2),
            round(float(np.max(ratio_db)), 2),
        ),
    }
    return TunedChainResult(
        chain=chain,
        band_deltas_db=band_deltas,
        high_pass_hz=high_pass,
        debug=debug,
    )


def _estimate_drive(stem: np.ndarray) -> float:
    """Target drive in [0.05, 0.95] from the stem's crest factor.

    Heavily driven guitar is envelope-flattened: crest (peak/RMS)
    around 8-10 dB, versus 16-20 dB for clean playing. Linear map,
    clamped — a proxy, not physics, and the blind audit is its judge.
    """
    rms = float(np.sqrt(np.mean(stem**2))) or 1e-9
    peak = float(np.percentile(np.abs(stem), 99.9)) or 1e-9
    crest_db = 20.0 * math.log10(peak / rms)
    # crest 18 dB -> 0.15, crest 9 dB -> 0.85
    drive = 0.15 + (18.0 - crest_db) * (0.85 - 0.15) / (18.0 - 9.0)
    return _clamp(drive, 0.05, 0.95)


def _estimate_reverb_mix(stem: np.ndarray) -> float:
    """Target reverb mix in [0.03, 0.4] from the stem's tail floor.

    Wet stems keep energy between phrases: the ratio of the quiet-frame
    envelope floor to the loud-frame envelope tracks perceived space.
    """
    frame = 2048
    n = (len(stem) // frame) * frame
    if n == 0:
        return 0.1
    env = np.sqrt(np.mean(stem[:n].reshape(-1, frame) ** 2, axis=1))
    env = env[env > env.max() * 1e-4]
    if len(env) < 8:
        return 0.1
    floor = float(np.percentile(env, 20))
    loud = float(np.percentile(env, 90)) or 1e-9
    wetness = floor / loud            # ~0.03 dry staccato, ~0.4 washy
    return _clamp(0.03 + wetness * 0.9, 0.03, 0.4)


def _estimate_comp(stem: np.ndarray) -> Optional[float]:
    """Compressor ratio suggestion, or None to leave the base comp.

    A stem that is already envelope-flat (low crest) needs no monitor
    compression; a spiky clean stem gets a gentle ratio.
    """
    rms = float(np.sqrt(np.mean(stem**2))) or 1e-9
    peak = float(np.percentile(np.abs(stem), 99.9)) or 1e-9
    crest_db = 20.0 * math.log10(peak / rms)
    if crest_db < 12.0:
        return None
    return round(_clamp(1.0 + (crest_db - 12.0) * 0.35, 1.5, 4.0), 1)


def _pick_high_pass(stem: np.ndarray, base: MonitorChain) -> float:
    """Choose the input HPF from the stem's sub-100 Hz energy share.

    A stem with real low-end content (baritone/drop tunings, bleedy
    room mics) keeps a low HPF; a thin stem gets a higher one so the
    monitor chain isn't amplifying rumble the part doesn't use.
    Falls back to the base chain's value when measurement fails.
    """
    base_hpf = float(
        (base.parameters.get("input") or {}).get("high_pass_hz", 90.0)
    )
    try:
        spectrum = np.abs(np.fft.rfft(stem[: IR_SAMPLE_RATE * 8]))
        freqs = np.fft.rfftfreq(len(stem[: IR_SAMPLE_RATE * 8]), 1.0 / IR_SAMPLE_RATE)
        total = float(np.sum(spectrum**2)) or 1.0
        low_share = float(np.sum(spectrum[freqs < 100.0] ** 2)) / total
    except Exception:
        return base_hpf
    if not math.isfinite(low_share):
        return base_hpf
    # More low-end in the part -> lower fence.
    if low_share > 0.25:
        return _HPF_CHOICES_HZ[0]
    if low_share > 0.10:
        return _HPF_CHOICES_HZ[1]
    if low_share > 0.03:
        return _HPF_CHOICES_HZ[2]
    return _HPF_CHOICES_HZ[3]


def _clamp(value: float, lo: float, hi: float) -> float:
    if not math.isfinite(value):
        return 0.0
    return max(lo, min(hi, value))
