#!/usr/bin/env python3
"""Single-Preset Equivalence Test — embedded-ALS vs direct-.adv render.

Compares two WAV files that should be acoustically identical:
    * ``wav_a``: rendered from an ALS produced by the new embed-`.adv` generator
    * ``wav_b``: rendered from the same `.adv` loaded directly into Analog

This is the HARD GATE between the engineering fix in
``preset_als_generator._load_adv_ultraanalog`` and any batch re-render of the
99-preset catalog. It converts the RCA conclusion (``LastPresetRef`` is
display metadata, not auto-load) from "high confidence" to "confirmed at the
audio level".

Pass criteria (all must hold):
    - mean log-mel-spectrum cosine similarity > 0.98
    - frame-level RMS envelope Pearson correlation > 0.90
    - spectral centroid trajectory Pearson correlation > 0.90
    - RMS ratio within [0.9, 1.1]

Waveform Pearson correlation is computed and reported as a diagnostic but is
NOT a gate: free-running analog oscillators / LFOs make sample-aligned
correlation unreachable even between two renders of the same .adv.

Fail (any one) -> exit 1; do NOT proceed to batch render.

Usage:
    scripts/equivalence_test.py <wav_a> <wav_b> [--report <json_path>]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Tuple

import numpy as np

try:
    import librosa
except ImportError:  # pragma: no cover — librosa is in backend requirements
    print("ERROR: librosa is required (pip install librosa)", file=sys.stderr)
    sys.exit(2)


SAMPLE_RATE = 48000
N_MELS = 128
FMIN = 20.0
FMAX = 20000.0

# Promotion gate thresholds.
# Waveform correlation is diagnostic-only (free-running LFOs/oscillators on
# analog synths make sample-aligned correlation unreachable).
MEL_COSINE_THRESHOLD = 0.98
ENVELOPE_CORR_THRESHOLD = 0.90
CENTROID_CORR_THRESHOLD = 0.90
RMS_RATIO_MIN = 0.9
RMS_RATIO_MAX = 1.1

ENV_HOP = 1024


def _load_mono(path: Path) -> np.ndarray:
    """Load WAV as mono float32 at SAMPLE_RATE."""
    y, _ = librosa.load(str(path), sr=SAMPLE_RATE, mono=True)
    return y.astype(np.float32)


def _trim_silence(y: np.ndarray, top_db: float = 40.0) -> np.ndarray:
    """Trim leading/trailing silence so envelope onset is aligned."""
    trimmed, _ = librosa.effects.trim(y, top_db=top_db)
    return trimmed if trimmed.size > 0 else y


def _rms(y: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(y))) + 1e-12)


def _normalise_rms(y: np.ndarray, target_rms: float = 0.1) -> np.ndarray:
    r = _rms(y)
    if r < 1e-9:
        return y
    return y * (target_rms / r)


def _waveform_correlation(a: np.ndarray, b: np.ndarray) -> float:
    """Pearson correlation of two waveforms, length-matched by truncation."""
    n = min(a.size, b.size)
    if n < 2:
        return 0.0
    a = a[:n]
    b = b[:n]
    # Standard Pearson via numpy.
    a_d = a - a.mean()
    b_d = b - b.mean()
    denom = float(np.sqrt(np.sum(a_d * a_d) * np.sum(b_d * b_d)) + 1e-12)
    return float(np.sum(a_d * b_d) / denom)


def _pearson(a: np.ndarray, b: np.ndarray) -> float:
    """Pearson correlation of two 1-D arrays, length-matched by truncation."""
    n = min(a.size, b.size)
    if n < 2:
        return 0.0
    a = a[:n].astype(np.float64)
    b = b[:n].astype(np.float64)
    a_d = a - a.mean()
    b_d = b - b.mean()
    denom = float(np.sqrt(np.sum(a_d * a_d) * np.sum(b_d * b_d)) + 1e-12)
    return float(np.sum(a_d * b_d) / denom)


def _envelope(y: np.ndarray) -> np.ndarray:
    """Frame-level RMS envelope."""
    return librosa.feature.rms(y=y, hop_length=ENV_HOP)[0]


def _spectral_centroid(y: np.ndarray) -> np.ndarray:
    """Spectral centroid trajectory (Hz per frame)."""
    return librosa.feature.spectral_centroid(y=y, sr=SAMPLE_RATE)[0]


def _mean_log_mel(y: np.ndarray) -> np.ndarray:
    """Mean log-mel spectrum (n_mels-vector) for cosine comparison."""
    S = librosa.feature.melspectrogram(
        y=y, sr=SAMPLE_RATE, n_mels=N_MELS, fmin=FMIN, fmax=FMAX
    )
    log_S = librosa.power_to_db(S + 1e-12)
    return log_S.mean(axis=1).astype(np.float32)


def _cosine(u: np.ndarray, v: np.ndarray) -> float:
    nu = float(np.linalg.norm(u))
    nv = float(np.linalg.norm(v))
    if nu < 1e-12 or nv < 1e-12:
        return 0.0
    return float(np.dot(u, v) / (nu * nv))


def compare(wav_a: Path, wav_b: Path) -> Tuple[dict, bool]:
    """Return (metrics dict, passed boolean)."""
    ya = _trim_silence(_load_mono(wav_a))
    yb = _trim_silence(_load_mono(wav_b))

    rms_a = _rms(ya)
    rms_b = _rms(yb)
    rms_ratio = rms_a / max(rms_b, 1e-9)

    # RMS-normalise both before waveform correlation so amplitude
    # mismatch does not penalise an otherwise-identical patch.
    ya_n = _normalise_rms(ya)
    yb_n = _normalise_rms(yb)
    corr = _waveform_correlation(ya_n, yb_n)

    # Mel cosine on the original (un-RMS-normalised) signals — log-mel is
    # already amplitude-insensitive enough that this avoids double-correcting.
    mel_a = _mean_log_mel(ya)
    mel_b = _mean_log_mel(yb)
    mel_cos = _cosine(mel_a, mel_b)

    # Envelope + centroid trajectories on RMS-normalised signals.
    env_corr = _pearson(_envelope(ya_n), _envelope(yb_n))
    centroid_corr = _pearson(_spectral_centroid(ya_n), _spectral_centroid(yb_n))

    peak_a = float(np.max(np.abs(ya)))
    peak_b = float(np.max(np.abs(yb)))
    peak_ratio = peak_a / max(peak_b, 1e-9)

    metrics = {
        "wav_a": str(wav_a),
        "wav_b": str(wav_b),
        "sample_rate": SAMPLE_RATE,
        "len_a_samples": int(ya.size),
        "len_b_samples": int(yb.size),
        "rms_a": rms_a,
        "rms_b": rms_b,
        "rms_ratio": rms_ratio,
        "peak_a": peak_a,
        "peak_b": peak_b,
        "peak_ratio": peak_ratio,
        "waveform_correlation": corr,
        "mel_cosine_similarity": mel_cos,
        "envelope_correlation": env_corr,
        "centroid_correlation": centroid_corr,
        "thresholds": {
            "mel_cosine_min": MEL_COSINE_THRESHOLD,
            "envelope_corr_min": ENVELOPE_CORR_THRESHOLD,
            "centroid_corr_min": CENTROID_CORR_THRESHOLD,
            "rms_ratio_min": RMS_RATIO_MIN,
            "rms_ratio_max": RMS_RATIO_MAX,
        },
    }

    checks = {
        "mel_cosine": mel_cos > MEL_COSINE_THRESHOLD,
        "envelope": env_corr > ENVELOPE_CORR_THRESHOLD,
        "centroid": centroid_corr > CENTROID_CORR_THRESHOLD,
        "rms_ratio": RMS_RATIO_MIN <= rms_ratio <= RMS_RATIO_MAX,
    }
    metrics["checks"] = checks
    metrics["passed"] = all(checks.values())
    return metrics, metrics["passed"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wav_a", type=Path, help="embedded-ALS render")
    parser.add_argument("wav_b", type=Path, help="direct-.adv render")
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="optional JSON output path (default: derive from wav_a)",
    )
    args = parser.parse_args()

    for p in (args.wav_a, args.wav_b):
        if not p.exists():
            print(f"ERROR: missing WAV: {p}", file=sys.stderr)
            return 2

    metrics, passed = compare(args.wav_a, args.wav_b)

    report_path = args.report
    if report_path is None:
        report_path = args.wav_a.parent / "equivalence_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(metrics, indent=2))

    # Console summary.
    print(f"wav_a: {args.wav_a}")
    print(f"wav_b: {args.wav_b}")
    print(f"  len_a={metrics['len_a_samples']:>8}  len_b={metrics['len_b_samples']:>8}")
    print(f"  RMS a={metrics['rms_a']:.4f}  b={metrics['rms_b']:.4f}  "
          f"ratio={metrics['rms_ratio']:.3f}  "
          f"[need {RMS_RATIO_MIN}..{RMS_RATIO_MAX}]  "
          f"{'PASS' if metrics['checks']['rms_ratio'] else 'FAIL'}")
    print(f"  Mel cosine similarity: {metrics['mel_cosine_similarity']:+.4f}  "
          f"[need > {MEL_COSINE_THRESHOLD}]  "
          f"{'PASS' if metrics['checks']['mel_cosine'] else 'FAIL'}")
    print(f"  Envelope correlation:  {metrics['envelope_correlation']:+.4f}  "
          f"[need > {ENVELOPE_CORR_THRESHOLD}]  "
          f"{'PASS' if metrics['checks']['envelope'] else 'FAIL'}")
    print(f"  Centroid correlation:  {metrics['centroid_correlation']:+.4f}  "
          f"[need > {CENTROID_CORR_THRESHOLD}]  "
          f"{'PASS' if metrics['checks']['centroid'] else 'FAIL'}")
    print(f"  Waveform correlation:  {metrics['waveform_correlation']:+.4f}  "
          f"[diagnostic only — not a gate]")
    print(f"  Report -> {report_path}")
    print()
    if passed:
        print("EQUIVALENCE TEST: PASS — embed-.adv reproduces direct-load synthesis.")
        return 0
    print("EQUIVALENCE TEST: FAIL — do NOT proceed to batch render.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
