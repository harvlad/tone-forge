"""Auto-loader + IsotonicCalibrator wrapper behavior.

The companion file ``test_tone_calibration.py`` pins the placeholder
contract; this file pins the fitted-model path:

* :class:`IsotonicCalibrator` wraps any predict-shaped object and
  honors the same sanitization + ``[0, 1]`` clamp the placeholder
  does, so the public ``calibrate`` surface looks identical whether
  the placeholder or the fitted model is active.

* :func:`_try_load_fitted_calibrator` is the auto-loader called at
  module import time. It must be silent when the artifact is absent,
  silent (with a logged warning) when it is broken, and return a
  ``IsotonicCalibrator`` when the artifact is valid.

* The auto-loader's "valid" path is exercised end-to-end with a real
  ``sklearn.isotonic.IsotonicRegression`` saved through ``joblib`` so
  the integration is verified, not just the seams.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import List

import pytest

from tone_forge.tone import calibration


# ---------------------------------------------------------------------------
# IsotonicCalibrator wrapper
# ---------------------------------------------------------------------------


class _StubModel:
    """Predict-shaped stub. Records calls so the wrapper's contract
    can be inspected without depending on sklearn for these tests."""

    def __init__(self, response):
        self._response = response
        self.calls: List = []

    def predict(self, xs):
        self.calls.append(list(xs))
        # Pretend the model returns the same response regardless of
        # input — the wrapper does not depend on shape, only on the
        # first element being a float.
        return [self._response]


def test_isotonic_calibrator_calls_model_predict_with_distance() -> None:
    """The wrapper packages the scalar distance as a 1-element list
    because sklearn's ``IsotonicRegression.predict`` expects an
    array-like."""
    stub = _StubModel(response=0.5)
    cal = calibration.IsotonicCalibrator(stub)
    cal(0.42)
    assert stub.calls == [[0.42]]


def test_isotonic_calibrator_returns_model_prediction() -> None:
    stub = _StubModel(response=0.73)
    cal = calibration.IsotonicCalibrator(stub)
    assert cal(0.1) == pytest.approx(0.73)


def test_isotonic_calibrator_clamps_above_unit() -> None:
    """A poorly-bounded model that returns >1 must not propagate;
    the wrapper enforces the ``calibrate`` contract for callers."""
    cal = calibration.IsotonicCalibrator(_StubModel(response=1.7))
    assert cal(0.1) == 1.0


def test_isotonic_calibrator_clamps_below_zero() -> None:
    cal = calibration.IsotonicCalibrator(_StubModel(response=-0.4))
    assert cal(0.1) == 0.0


def test_isotonic_calibrator_returns_zero_on_nan_prediction() -> None:
    """A model that emits NaN on an out-of-domain input must not poison
    downstream tier classification."""
    cal = calibration.IsotonicCalibrator(_StubModel(response=float("nan")))
    assert cal(0.1) == 0.0


def test_isotonic_calibrator_returns_zero_on_inf_prediction() -> None:
    cal = calibration.IsotonicCalibrator(_StubModel(response=float("inf")))
    assert cal(0.1) == 0.0


def test_isotonic_calibrator_returns_zero_on_predict_raise() -> None:
    """A model that raises mid-predict is treated like a broken
    distance — under-claim rather than 500 the retrieval path."""

    class _Broken:
        def predict(self, xs):
            raise RuntimeError("simulated model failure")

    cal = calibration.IsotonicCalibrator(_Broken())
    assert cal(0.1) == 0.0


# Defensive inputs — same gate as the placeholder.


@pytest.mark.parametrize("bad", [None, "garbage", float("nan"), float("inf"), -1.0])
def test_isotonic_calibrator_sanitizes_distance_inputs(bad) -> None:
    """Bad distances must short-circuit before the model is called
    at all — the model is not responsible for input validation."""
    stub = _StubModel(response=0.9)
    cal = calibration.IsotonicCalibrator(stub)
    assert cal(bad) == 0.0  # type: ignore[arg-type]
    assert stub.calls == [], "broken inputs must not reach the model"


def test_isotonic_calibrator_passes_zero_distance_through() -> None:
    """Distance=0 is a valid self-match; not a sanitization failure."""
    stub = _StubModel(response=0.95)
    cal = calibration.IsotonicCalibrator(stub)
    assert cal(0.0) == pytest.approx(0.95)
    assert stub.calls == [[0.0]]


# ---------------------------------------------------------------------------
# Auto-loader — absence + breakage paths
# ---------------------------------------------------------------------------


def test_try_load_returns_none_when_artifact_missing(monkeypatch, tmp_path) -> None:
    """No file → silent return. The placeholder stays in charge."""
    # Point the loader at a directory we know is empty by faking
    # __file__'s parent via a wrapper. Simpler: patch Path resolution
    # by monkeypatching the constant lookup. We bypass that by
    # leveraging the loader's documented file-existence check — point
    # the FILENAME at something that cannot exist under the module
    # directory.
    monkeypatch.setattr(
        calibration, "FITTED_MODEL_FILENAME", "definitely_not_a_real_file.joblib"
    )
    assert calibration._try_load_fitted_calibrator() is None


def test_try_load_returns_none_on_broken_artifact(monkeypatch, tmp_path) -> None:
    """A corrupt pickle does not raise into the import path."""
    # Write a garbage file at the expected location. We have to land
    # it next to the calibration module because that's where the
    # loader looks.
    module_dir = Path(calibration.__file__).parent
    broken_name = "calibration_v1_broken_test_artifact.joblib"
    broken_path = module_dir / broken_name
    broken_path.write_bytes(b"this is not a pickle")
    try:
        monkeypatch.setattr(calibration, "FITTED_MODEL_FILENAME", broken_name)
        assert calibration._try_load_fitted_calibrator() is None
    finally:
        broken_path.unlink(missing_ok=True)


def test_try_load_returns_none_when_object_lacks_predict(
    monkeypatch, tmp_path
) -> None:
    """A joblib file containing a non-model object (someone pickled the
    wrong thing) must be rejected loudly enough to log but quietly
    enough not to break the import chain."""
    import joblib

    module_dir = Path(calibration.__file__).parent
    name = "calibration_v1_wrong_object_test_artifact.joblib"
    path = module_dir / name
    joblib.dump({"not": "a model"}, path)
    try:
        monkeypatch.setattr(calibration, "FITTED_MODEL_FILENAME", name)
        assert calibration._try_load_fitted_calibrator() is None
    finally:
        path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Auto-loader — happy path with a real IsotonicRegression
# ---------------------------------------------------------------------------


def test_load_from_joblib_round_trips_real_isotonic_regression(
    monkeypatch, tmp_path
) -> None:
    """End-to-end: fit a real ``IsotonicRegression``, save through
    joblib, load via the public ``IsotonicCalibrator.load_from_joblib``,
    and verify the wrapped calibrator obeys monotonicity on a fresh
    distance grid.

    This is the contract the production refit will rely on. If the
    sklearn / joblib versions ever drift in a breaking way, this
    test will flag it before the artifact reaches the calibrator.
    """
    from sklearn.isotonic import IsotonicRegression
    import joblib

    # Tiny synthetic dataset — confidence-shaped: as distance grows,
    # the probability of a correct match drops. IsotonicRegression
    # learns this with no parameters to tune.
    distances = [0.0, 0.2, 0.5, 1.0, 1.5, 2.0, 3.0]
    correctness = [1.0, 1.0, 0.9, 0.6, 0.3, 0.1, 0.0]
    model = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    model.fit(distances, correctness)

    artifact = tmp_path / "fitted.joblib"
    joblib.dump(model, artifact)

    cal = calibration.IsotonicCalibrator.load_from_joblib(artifact)

    # Wrapped calibrator preserves monotonic decreasing behavior on a
    # different test grid (the property the fitted model is supposed
    # to enforce, and the only public contract callers depend on).
    out = [cal(d) for d in [0.0, 0.25, 0.75, 1.25, 2.5]]
    for prev, cur in zip(out, out[1:]):
        assert cur <= prev + 1e-9, f"expected monotone decreasing, got {out}"
    # And the values are usable as confidence directly.
    for value in out:
        assert 0.0 <= value <= 1.0


def test_load_from_joblib_raises_on_missing_predict(tmp_path) -> None:
    """Direct (not auto-loader) call must surface the bad-object case
    — the auto-loader is the place that catches it; the loader
    primitive itself reports."""
    import joblib

    artifact = tmp_path / "wrong.joblib"
    joblib.dump({"not": "a model"}, artifact)
    with pytest.raises(ValueError, match="does not expose predict"):
        calibration.IsotonicCalibrator.load_from_joblib(artifact)


# ---------------------------------------------------------------------------
# Auto-loader — module-level wiring
# ---------------------------------------------------------------------------


def test_module_level_calibrator_is_callable() -> None:
    """Whichever calibrator the auto-loader settled on, it must be
    callable with the contract every caller depends on."""
    assert callable(calibration._CALIBRATOR)


def test_module_level_calibrator_obeys_unit_interval() -> None:
    """Sanity grid: the *active* calibrator (placeholder today, fitted
    once the artifact lands) must obey the [0, 1] contract on a
    coarse distance grid. No placeholder-specific assertions here."""
    for d in [0.0, 0.1, 0.5, 1.0, 2.0, 5.0, 50.0]:
        value = calibration.calibrate(d)
        assert 0.0 <= value <= 1.0
        assert not math.isnan(value)


def test_module_level_calibrator_sanitizes_inputs() -> None:
    """Defensive inputs must collapse to 0.0 regardless of which
    calibrator is active — this is the part of the contract the
    auto-loader cannot break."""
    for bad in [None, math.nan, math.inf, -1.0, "garbage"]:
        assert calibration.calibrate(bad) == 0.0  # type: ignore[arg-type]
