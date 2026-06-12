#!/usr/bin/env python3
"""Fit the tone-retrieval isotonic calibrator from a labeled JSONL.

This is the second half of the P6 calibration pipeline:

  1.  ``label_tone_clips.py``   walks a clip corpus, runs the retrieval
      pipeline against each clip, prompts the operator y/n on whether
      the top match was correct, and appends one record per clip to a
      JSONL file.
  2.  ``fit_tone_calibration.py`` (this script) reads that JSONL, fits
      a monotonically-decreasing ``IsotonicRegression`` mapping
      ``distance -> P(correct)``, and writes the fitted model via
      ``joblib.dump`` to the path that
      ``backend/tone_forge/tone/calibration.py`` auto-loads at import
      time (``calibration_v1.joblib`` next to that module).

Wire contract is intentionally narrow:

  Input JSONL line shape (only these two fields are required):
      {"distance": <float, nonnegative>, "label": <0 or 1>, ...}
  Output: a raw ``sklearn.isotonic.IsotonicRegression`` serialized via
      joblib. The loader at ``tone.calibration.IsotonicCalibrator
      .load_from_joblib`` wraps it; we do not write the wrapper class
      because the wrapper holds runtime sanitization logic that should
      live with the loader, not the artifact.

We use ``increasing=False`` because distance and correctness are
inversely related — a larger retrieval distance means a less likely
correct match. ``y_min=0.0`` / ``y_max=1.0`` / ``out_of_bounds="clip"``
match the loader's contract that the returned confidence lies in
``[0, 1]`` even for inputs outside the fit's distance range.

Refuses to fit below ``--min-samples`` (default 50) because an
under-sampled isotonic step function would happily emit overconfident
predictions on the convex hull's interior. We'd rather block the swap
to the fitted artifact than ship a calibrator that under-claims and
silently overrides the placeholder's HIGH-tier cap.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Tuple

# Default output path is hard-coded to the loader's expectation. The
# loader at ``backend/tone_forge/tone/calibration.py`` looks for
# ``calibration_v1.joblib`` next to itself; we resolve that statically
# rather than importing the loader because we want to be able to fit
# without paying the import cost of sklearn-via-tone-package.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_OUTPUT = (
    _REPO_ROOT / "tone_forge" / "tone" / "calibration_v1.joblib"
)
_DEFAULT_LABELS = _REPO_ROOT / "data" / "tone_calibration_labels.jsonl"
_DEFAULT_MIN_SAMPLES = 50


def _load_labels(path: Path) -> List[Tuple[float, int]]:
    """Parse the labels JSONL into ``(distance, label)`` pairs.

    Lines without the required two fields, or with non-binary labels,
    or with non-finite distances, are skipped with a warning to stderr
    rather than aborting — labeling sessions are append-only and a
    half-written line at the tail (e.g. the operator hit Ctrl-C mid
    keystroke) shouldn't poison the fit.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"labels file not found: {path}\n"
            f"run scripts/label_tone_clips.py first, or pass --labels"
        )
    pairs: List[Tuple[float, int]] = []
    with path.open() as fh:
        for line_no, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                print(
                    f"[warn] {path.name}:{line_no} not valid JSON ({exc}); skipping",
                    file=sys.stderr,
                )
                continue
            distance = record.get("distance")
            label = record.get("label")
            if not isinstance(distance, (int, float)) or distance < 0:
                print(
                    f"[warn] {path.name}:{line_no} bad distance "
                    f"{distance!r}; skipping",
                    file=sys.stderr,
                )
                continue
            if label not in (0, 1, True, False):
                print(
                    f"[warn] {path.name}:{line_no} bad label "
                    f"{label!r}; skipping",
                    file=sys.stderr,
                )
                continue
            pairs.append((float(distance), int(bool(label))))
    return pairs


def _fit_and_save(
    pairs: List[Tuple[float, int]],
    output_path: Path,
    min_samples: int,
) -> None:
    """Fit the isotonic model and dump it via joblib."""
    if len(pairs) < min_samples:
        raise SystemExit(
            f"refusing to fit: have {len(pairs)} samples, need "
            f"{min_samples}. Label more clips with "
            f"scripts/label_tone_clips.py first, or override with "
            f"--min-samples."
        )

    # Lazy imports: keep startup fast for `--help`, and don't force
    # numpy/sklearn on environments that only want to validate args.
    import numpy as np
    import joblib
    from sklearn.isotonic import IsotonicRegression

    distances = np.asarray([p[0] for p in pairs], dtype=float)
    labels = np.asarray([p[1] for p in pairs], dtype=float)

    n_pos = int(labels.sum())
    n_neg = len(labels) - n_pos
    if n_pos == 0 or n_neg == 0:
        raise SystemExit(
            f"refusing to fit: all {len(labels)} samples are the same "
            f"class (positives={n_pos}, negatives={n_neg}). Need both "
            f"correct and incorrect matches in the corpus."
        )

    model = IsotonicRegression(
        y_min=0.0,
        y_max=1.0,
        out_of_bounds="clip",
        increasing=False,
    )
    model.fit(distances, labels)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, output_path)

    # Spot-check what the saved model returns at a few representative
    # distances. This is the same diagnostic an operator would want
    # before swapping the artifact into production.
    probe_distances = sorted({
        float(distances.min()),
        float(np.median(distances)),
        float(distances.max()),
    })
    print(f"fit complete: {len(labels)} samples ({n_pos} correct, {n_neg} wrong)")
    print(f"  wrote {output_path}")
    print(f"  distance range: [{distances.min():.4f}, {distances.max():.4f}]")
    for d in probe_distances:
        p = float(model.predict([d])[0])
        print(f"  P(correct | distance={d:.4f}) = {p:.4f}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--labels",
        type=Path,
        default=_DEFAULT_LABELS,
        help=f"path to labeled JSONL (default: {_DEFAULT_LABELS})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=_DEFAULT_OUTPUT,
        help=(
            f"where to write the joblib artifact "
            f"(default: {_DEFAULT_OUTPUT}; this is the path the "
            f"tone.calibration loader auto-picks up)"
        ),
    )
    parser.add_argument(
        "--min-samples",
        type=int,
        default=_DEFAULT_MIN_SAMPLES,
        help=(
            f"minimum labeled samples required before fitting "
            f"(default: {_DEFAULT_MIN_SAMPLES})"
        ),
    )
    args = parser.parse_args()
    pairs = _load_labels(args.labels)
    _fit_and_save(pairs, args.output, args.min_samples)


if __name__ == "__main__":
    main()
