"""Pin the wire-contract that ``fit_tone_calibration.py`` honours.

The fitter is the producer side of the swap-in artifact that
``backend/tone_forge/tone/calibration.py`` consumes at import time.
Two contracts must hold for the swap to be lossless:

1. The artifact format must be a raw ``IsotonicRegression`` (the
   loader expects ``predict([d])`` on the deserialized object — see
   ``IsotonicCalibrator.load_from_joblib``). If the fitter ever
   started saving a dict or a custom wrapper, the loader would warn
   and fall back to the placeholder silently — drift the user can't
   see.

2. Refit-time guards (minimum-sample floor, both-classes-present
   check) must trip with a clear ``SystemExit`` rather than producing
   an artifact the loader would accept. A degenerate fit (all
   positives, no negatives) would happily emit constant 1.0 and
   silently unlock HIGH tier on every retrieval — the explicit
   refusal here is the only thing protecting the production caller
   from that.

This file tests the fitter in isolation by importing it as a module
and exercising its internal entry points; we do not shell out to
``python scripts/fit_tone_calibration.py`` because we want sklearn
import errors to surface as test failures, not as subprocess exit
codes hidden behind pytest's stdout capture.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import List

import pytest

# The fitter lives in scripts/ which is not a package. Load it via
# importlib so the test file doesn't have to mutate sys.path globally
# (which would leak to other tests sharing the worker).
_REPO_ROOT = Path(__file__).resolve().parents[1]
_FITTER_PATH = _REPO_ROOT / "scripts" / "fit_tone_calibration.py"


@pytest.fixture(scope="module")
def fitter_module():
    spec = importlib.util.spec_from_file_location(
        "fit_tone_calibration", _FITTER_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_labels(path: Path, rows: List[dict]) -> None:
    with path.open("w") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


# ---------------------------------------------------------------------------
# 1. Happy path: well-separated samples → loader-compatible artifact
# ---------------------------------------------------------------------------


def test_fit_writes_loader_compatible_isotonic(
    fitter_module, tmp_path: Path
) -> None:
    """The happy-path output must round-trip through the production
    loader. We don't test the loader and fitter independently — the
    interesting failure is that they DRIFT, so the test that catches
    that is the one that exercises both ends.
    """
    labels_path = tmp_path / "labels.jsonl"
    output_path = tmp_path / "calibration_v1.joblib"

    # Synthetic but realistic shape: low-distance clips are mostly
    # correct, high-distance clips are mostly wrong. 60 samples
    # clears the default min-samples=50 floor.
    rows = []
    for i in range(30):
        rows.append({"distance": 0.1 + 0.001 * i, "label": 1})
    for i in range(30):
        rows.append({"distance": 2.5 + 0.001 * i, "label": 0})
    _write_labels(labels_path, rows)

    pairs = fitter_module._load_labels(labels_path)
    fitter_module._fit_and_save(pairs, output_path, min_samples=50)
    assert output_path.exists(), "fitter did not write the output joblib"

    # Now load via the production loader. If the artifact format
    # diverges from what the loader expects, this raises.
    sys.path.insert(0, str(_REPO_ROOT))
    from tone_forge.tone.calibration import IsotonicCalibrator  # noqa: E402

    calibrator = IsotonicCalibrator.load_from_joblib(output_path)

    # Monotonic, decreasing-in-distance, in [0, 1]. A short distance
    # should be more confident than a long one; a degenerate fit (all
    # positives or all negatives) would emit a constant and fail this.
    near = calibrator(0.1)
    far = calibrator(2.5)
    assert 0.0 <= far <= near <= 1.0, (
        f"calibrator violates monotonicity / bound: "
        f"near={near}, far={far}"
    )
    assert near > far, (
        "calibrator failed to distinguish well-separated classes; "
        "the synthetic corpus splits at distance 0.13 vs 2.5"
    )


# ---------------------------------------------------------------------------
# 2. Too few samples → explicit refusal, no artifact written
# ---------------------------------------------------------------------------


def test_fit_refuses_below_min_samples(
    fitter_module, tmp_path: Path
) -> None:
    """The 50-sample floor is the only thing protecting the production
    caller from a 5-clip 'calibration' silently shipping. Pin the
    SystemExit + the absence of a written artifact so a future refactor
    that bypasses the floor surfaces here.
    """
    labels_path = tmp_path / "labels.jsonl"
    output_path = tmp_path / "calibration_v1.joblib"
    rows = [
        {"distance": 0.1, "label": 1},
        {"distance": 2.0, "label": 0},
        {"distance": 0.3, "label": 1},
    ]
    _write_labels(labels_path, rows)

    pairs = fitter_module._load_labels(labels_path)
    with pytest.raises(SystemExit) as exc_info:
        fitter_module._fit_and_save(pairs, output_path, min_samples=50)
    assert "refusing to fit" in str(exc_info.value), (
        f"floor refusal lost its operator-facing message: {exc_info.value!r}"
    )
    assert not output_path.exists(), (
        "fitter wrote a partial artifact despite refusing the fit"
    )


# ---------------------------------------------------------------------------
# 3. Single-class corpus → explicit refusal
# ---------------------------------------------------------------------------


def test_fit_refuses_single_class_corpus(
    fitter_module, tmp_path: Path
) -> None:
    """An all-positive corpus would happily produce constant-1.0
    predictions and silently unlock HIGH tier on every retrieval.
    The fitter must refuse — this is more important than the
    min-samples floor because a degenerate fit looks plausible
    until production hits a single misclassification.
    """
    labels_path = tmp_path / "labels.jsonl"
    output_path = tmp_path / "calibration_v1.joblib"
    rows = [{"distance": 0.1 + 0.01 * i, "label": 1} for i in range(60)]
    _write_labels(labels_path, rows)

    pairs = fitter_module._load_labels(labels_path)
    with pytest.raises(SystemExit) as exc_info:
        fitter_module._fit_and_save(pairs, output_path, min_samples=50)
    assert "same class" in str(exc_info.value), (
        f"single-class refusal lost its diagnostic: {exc_info.value!r}"
    )
    assert not output_path.exists()


# ---------------------------------------------------------------------------
# 4. Malformed JSONL lines are skipped, not fatal
# ---------------------------------------------------------------------------


def test_load_labels_skips_malformed_lines(
    fitter_module, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """A labeling session is append-only; a half-written tail line
    (operator hit Ctrl-C mid-keystroke) must not poison every future
    fit. Skipping + warning is the right shape, NOT raising.
    """
    labels_path = tmp_path / "labels.jsonl"
    with labels_path.open("w") as fh:
        fh.write('{"distance": 0.5, "label": 1}\n')
        fh.write('not json at all\n')
        fh.write('{"distance": -1.0, "label": 0}\n')         # bad distance
        fh.write('{"distance": 0.7, "label": "yes"}\n')      # bad label
        fh.write('{"distance": 1.2, "label": 0}\n')

    pairs = fitter_module._load_labels(labels_path)
    assert pairs == [(0.5, 1), (1.2, 0)], (
        f"expected exactly the two well-formed records; got {pairs!r}"
    )
    captured = capsys.readouterr()
    # We don't pin the exact wording, just that each bad line warned.
    assert captured.err.count("[warn]") >= 3, (
        "operator must see warnings for malformed lines; "
        f"got stderr: {captured.err!r}"
    )
