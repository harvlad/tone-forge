"""Unit tests for ``bench.metrics``.

Each metric is validated against hand-crafted synthetic inputs with
hand-computed expected outputs. The goal is to lock the *behaviour*
of each metric, not to retest the underlying ``chord_eval`` helpers
(which have their own coverage in ``test_chord_eval_*``).

Region shape used throughout: ``(start_s, end_s, label)`` tuples
with optional dict-with-confidence shape for ECE tests.
"""
from __future__ import annotations

import math

import pytest

from bench.metrics import (
    boundary_iou,
    chord_error_rate,
    expected_calibration_error,
    region_stability,
    strict_wcsr_score,
    triad_relaxed_wcsr_score,
)


# ---------------------------------------------------------------------------
# WCSR wrappers
# ---------------------------------------------------------------------------


def test_triad_relaxed_wcsr_score_perfect_match() -> None:
    regions = [(0.0, 1.0, "C"), (1.0, 2.0, "G")]
    assert triad_relaxed_wcsr_score(regions, regions, 2.0) == pytest.approx(1.0)


def test_strict_wcsr_score_perfect_match() -> None:
    regions = [(0.0, 1.0, "C"), (1.0, 2.0, "Am")]
    assert strict_wcsr_score(regions, regions, 2.0) == pytest.approx(1.0)


def test_strict_wcsr_score_total_mismatch() -> None:
    ref = [(0.0, 2.0, "C")]
    pred = [(0.0, 2.0, "F")]
    assert strict_wcsr_score(pred, ref, 2.0) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# chord_error_rate
# ---------------------------------------------------------------------------


def test_chord_error_rate_perfect_match_is_zero() -> None:
    regions = [(0.0, 2.0, "C"), (2.0, 4.0, "G")]
    assert chord_error_rate(regions, regions, 4.0) == pytest.approx(0.0)


def test_chord_error_rate_all_wrong_equals_one() -> None:
    ref = [(0.0, 4.0, "C")]
    pred = [(0.0, 4.0, "F")]
    assert chord_error_rate(pred, ref, 4.0) == pytest.approx(1.0)


def test_chord_error_rate_partial_wrong() -> None:
    # 2 seconds correct (C), 2 seconds wrong (F over G)
    ref = [(0.0, 2.0, "C"), (2.0, 4.0, "G")]
    pred = [(0.0, 2.0, "C"), (2.0, 4.0, "F")]
    assert chord_error_rate(pred, ref, 4.0) == pytest.approx(0.5)


def test_chord_error_rate_zero_duration_returns_zero() -> None:
    ref = [(0.0, 1.0, "C")]
    pred = [(0.0, 1.0, "F")]
    assert chord_error_rate(pred, ref, 0.0) == 0.0


def test_chord_error_rate_uncovered_time_is_not_wrong() -> None:
    # Predicted only covers half the reference; uncovered time is
    # neither correct nor wrong, so error mass is 0 over 4.0s.
    ref = [(0.0, 4.0, "C")]
    pred = [(0.0, 2.0, "C")]
    assert chord_error_rate(pred, ref, 4.0) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# boundary_iou
# ---------------------------------------------------------------------------


def test_boundary_iou_single_chord_each_side_is_one() -> None:
    # Trivial "no internal structure" case, treated as perfect by
    # convention.
    pred = [(0.0, 4.0, "C")]
    ref = [(0.0, 4.0, "C")]
    assert boundary_iou(pred, ref, 4.0) == 1.0


def test_boundary_iou_exact_boundaries() -> None:
    pred = [(0.0, 1.0, "C"), (1.0, 2.0, "G"), (2.0, 3.0, "Am")]
    ref = [(0.0, 1.0, "C"), (1.0, 2.0, "G"), (2.0, 3.0, "Am")]
    # 2 internal boundaries on each side, both match -> IoU = 2 / 2 = 1.0
    assert boundary_iou(pred, ref, 3.0) == pytest.approx(1.0)


def test_boundary_iou_within_tolerance() -> None:
    pred = [(0.0, 0.9, "C"), (0.9, 2.0, "G")]
    ref = [(0.0, 1.0, "C"), (1.0, 2.0, "G")]
    # Internal boundary 0.9 vs 1.0 -- within default tol_s=0.5
    assert boundary_iou(pred, ref, 2.0) == pytest.approx(1.0)


def test_boundary_iou_outside_tolerance() -> None:
    pred = [(0.0, 0.2, "C"), (0.2, 2.0, "G")]
    ref = [(0.0, 1.0, "C"), (1.0, 2.0, "G")]
    # 0.2 vs 1.0 is 0.8 apart -- outside tol_s=0.5
    # matches=0, union = 1 + 1 - 0 = 2 -> IoU = 0
    assert boundary_iou(pred, ref, 2.0, tol_s=0.5) == pytest.approx(0.0)


def test_boundary_iou_extra_predicted_boundaries() -> None:
    # Predicted is over-segmented: 3 internal boundaries vs 1 reference
    pred = [(0.0, 0.5, "C"), (0.5, 1.0, "C"), (1.0, 1.5, "G"), (1.5, 2.0, "G")]
    ref = [(0.0, 1.0, "C"), (1.0, 2.0, "G")]
    # Pred internal: 0.5, 1.0, 1.5. Ref internal: 1.0.
    # Matches: 1.0<->1.0 (distance 0). matches=1.
    # IoU = 1 / (3 + 1 - 1) = 1/3
    assert boundary_iou(pred, ref, 2.0) == pytest.approx(1.0 / 3.0)


def test_boundary_iou_greedy_closest_first() -> None:
    # Two predicted boundaries near one reference boundary; greedy
    # picks the closer pred boundary first.
    pred = [(0.0, 0.95, "C"), (0.95, 1.3, "G"), (1.3, 2.0, "Am")]
    ref = [(0.0, 1.0, "C"), (1.0, 2.0, "G")]
    # Pred internal: 0.95, 1.3. Ref internal: 1.0.
    # |0.95-1.0|=0.05, |1.3-1.0|=0.3. Closer pair wins: matches=1.
    # IoU = 1 / (2 + 1 - 1) = 0.5
    assert boundary_iou(pred, ref, 2.0) == pytest.approx(0.5)


def test_boundary_iou_negative_tol_raises() -> None:
    pred = [(0.0, 1.0, "C")]
    ref = [(0.0, 1.0, "C")]
    with pytest.raises(ValueError):
        boundary_iou(pred, ref, 1.0, tol_s=-0.1)


def test_boundary_iou_zero_duration_returns_zero() -> None:
    pred = [(0.0, 1.0, "C")]
    ref = [(0.0, 1.0, "C")]
    assert boundary_iou(pred, ref, 0.0) == 0.0


# ---------------------------------------------------------------------------
# region_stability
# ---------------------------------------------------------------------------


def test_region_stability_no_transitions() -> None:
    # Single region -> no transitions
    pred = [(0.0, 60.0, "C")]
    assert region_stability(pred, 60.0) == 0.0


def test_region_stability_adjacent_same_label_is_not_transition() -> None:
    # Two regions with the same label -- not a transition.
    pred = [(0.0, 30.0, "C"), (30.0, 60.0, "C")]
    assert region_stability(pred, 60.0) == 0.0


def test_region_stability_one_transition_per_minute() -> None:
    # 1 transition in 60s -> 1 per minute
    pred = [(0.0, 30.0, "C"), (30.0, 60.0, "G")]
    assert region_stability(pred, 60.0) == pytest.approx(1.0)


def test_region_stability_scales_to_per_minute() -> None:
    # 4 transitions in 30s -> 8 per minute
    pred = [
        (0.0, 5.0, "C"),
        (5.0, 10.0, "G"),
        (10.0, 15.0, "Am"),
        (15.0, 20.0, "F"),
        (20.0, 30.0, "C"),
    ]
    assert region_stability(pred, 30.0) == pytest.approx(8.0)


def test_region_stability_empty_returns_zero() -> None:
    assert region_stability([], 60.0) == 0.0


def test_region_stability_zero_duration_returns_zero() -> None:
    pred = [(0.0, 1.0, "C"), (1.0, 2.0, "G")]
    assert region_stability(pred, 0.0) == 0.0


# ---------------------------------------------------------------------------
# expected_calibration_error
# ---------------------------------------------------------------------------


def _conf_region(start: float, end: float, label: str, conf: float) -> dict:
    return {"start": start, "end": end, "label": label, "confidence": conf}


def test_ece_perfect_calibration_all_correct_high_conf() -> None:
    # Every region correct, every confidence == 1.0 -> ECE = 0
    pred = [_conf_region(0.0, 1.0, "C", 1.0), _conf_region(1.0, 2.0, "G", 1.0)]
    ref = [(0.0, 1.0, "C"), (1.0, 2.0, "G")]
    assert expected_calibration_error(pred, ref, 2.0) == pytest.approx(0.0)


def test_ece_max_miscalibration() -> None:
    # Confidence 1.0 but every prediction wrong: acc=0, conf=1 -> gap=1
    pred = [_conf_region(0.0, 2.0, "F", 1.0)]
    ref = [(0.0, 2.0, "C")]
    assert expected_calibration_error(pred, ref, 2.0) == pytest.approx(1.0)


def test_ece_zero_when_confidence_matches_accuracy() -> None:
    # Two regions in distinct bins, each individually well-calibrated.
    # Bin 1 (0.05): one region, 1s, all wrong -> acc=0, mean_conf=0.05
    #               gap = 0.05
    # Bin 9 (0.95): one region, 1s, all correct -> acc=1, mean_conf=0.95
    #               gap = 0.05
    # ECE = (1/2)*0.05 + (1/2)*0.05 = 0.05
    pred = [
        _conf_region(0.0, 1.0, "F", 0.05),
        _conf_region(1.0, 2.0, "G", 0.95),
    ]
    ref = [(0.0, 1.0, "C"), (1.0, 2.0, "G")]
    assert expected_calibration_error(pred, ref, 2.0, bins=10) == pytest.approx(0.05)


def test_ece_zero_duration_returns_zero() -> None:
    pred = [_conf_region(0.0, 1.0, "C", 0.9)]
    ref = [(0.0, 1.0, "C")]
    assert expected_calibration_error(pred, ref, 0.0) == 0.0


def test_ece_empty_predictions_returns_zero() -> None:
    assert expected_calibration_error([], [(0.0, 1.0, "C")], 1.0) == 0.0


def test_ece_invalid_bins_raises() -> None:
    pred = [_conf_region(0.0, 1.0, "C", 0.9)]
    ref = [(0.0, 1.0, "C")]
    with pytest.raises(ValueError):
        expected_calibration_error(pred, ref, 1.0, bins=0)


def test_ece_default_confidence_is_one_for_plain_tuples() -> None:
    # Plain tuples have no confidence -> defaults to 1.0, lands in
    # last bin. All correct -> acc=1, mean_conf=1 -> ECE = 0.
    pred = [(0.0, 1.0, "C")]
    ref = [(0.0, 1.0, "C")]
    assert expected_calibration_error(pred, ref, 1.0) == pytest.approx(0.0)


def test_ece_duration_weighted_across_bins() -> None:
    # Bin A (conf=0.1, dur=3): all wrong -> acc=0, gap=0.1, weight=3/4
    # Bin B (conf=0.9, dur=1): all wrong -> acc=0, gap=0.9, weight=1/4
    # ECE = 0.75*0.1 + 0.25*0.9 = 0.075 + 0.225 = 0.3
    pred = [
        _conf_region(0.0, 3.0, "F", 0.1),
        _conf_region(3.0, 4.0, "F", 0.9),
    ]
    ref = [(0.0, 4.0, "C")]
    assert expected_calibration_error(pred, ref, 4.0, bins=10) == pytest.approx(0.3)


# ---------------------------------------------------------------------------
# Cross-shape adapter sanity
# ---------------------------------------------------------------------------


def test_metrics_accept_dict_regions() -> None:
    # Same metric, expressed via dict shape on the predicted side and
    # tuple shape on the reference side.
    pred = [
        {"start": 0.0, "end": 1.0, "label": "C"},
        {"start": 1.0, "end": 2.0, "label": "G"},
    ]
    ref = [(0.0, 1.0, "C"), (1.0, 2.0, "G")]
    assert triad_relaxed_wcsr_score(pred, ref, 2.0) == pytest.approx(1.0)
    assert chord_error_rate(pred, ref, 2.0) == pytest.approx(0.0)
    assert boundary_iou(pred, ref, 2.0) == pytest.approx(1.0)
    assert region_stability(pred, 2.0) == pytest.approx(30.0)  # 1 transition / 2s -> 30/min
