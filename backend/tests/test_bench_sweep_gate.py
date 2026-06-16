"""Tests for the M1.6 acceptance gate in ``bench.sweep``.

Exercises ``evaluate_acceptance`` in isolation with synthetic
``RunRecord`` objects. Every gate condition gets its own test;
together they pin every branch in ``evaluate_acceptance``.
"""
from __future__ import annotations

from typing import Mapping

import pytest

from bench.store import CorpusResult, FixtureResult, RunRecord
from bench.sweep import (
    AcceptanceConfig,
    AcceptanceVerdict,
    enumerate_candidates,
    evaluate_acceptance,
    load_space,
    SweepSpace,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_DEFAULT_RULES = AcceptanceConfig(
    corpus_metric="wcsr_triad_relaxed_mean",
    corpus_must_strictly_improve=True,
    max_per_fixture_drop_pp=5.0,
    max_runtime_factor=2.0,
    max_memory_factor=1.5,
)


def _fr(wcsr_tr: float, **overrides) -> FixtureResult:
    defaults = dict(
        wcsr_triad_relaxed=wcsr_tr,
        wcsr_strict=wcsr_tr,
        chord_error_rate=1.0 - wcsr_tr,
        boundary_iou_0p5=0.5,
        region_stability_per_min=20.0,
        expected_calibration_error=0.1,
        wall_seconds=1.0,
        peak_rss_mb=100.0,
    )
    defaults.update(overrides)
    return FixtureResult(**defaults)


def _record(
    *,
    per_fixture: Mapping[str, FixtureResult],
    wcsr_mean: float,
    wall_mean: float = 1.0,
    rss_max: float = 100.0,
    run_id: str = "test",
) -> RunRecord:
    corpus = CorpusResult(
        n_fixtures=len(per_fixture),
        wcsr_triad_relaxed_mean=wcsr_mean,
        wcsr_strict_mean=wcsr_mean,
        chord_error_rate_mean=1.0 - wcsr_mean,
        boundary_iou_0p5_mean=0.5,
        region_stability_per_min_mean=20.0,
        expected_calibration_error_mean=0.1,
        wall_seconds_mean=wall_mean,
        peak_rss_mb_max=rss_max,
    )
    return RunRecord(
        run_id=run_id,
        timestamp_utc="2026-06-16T00:00:00+00:00",
        git_sha=None,
        config={},
        corpus_dir="/tmp/fake",
        per_fixture=dict(per_fixture),
        corpus=corpus,
        wall_seconds_total=wall_mean * len(per_fixture),
    )


# ---------------------------------------------------------------------------
# Rule 1: corpus must strictly improve
# ---------------------------------------------------------------------------


def test_gate_accepts_strict_improvement() -> None:
    baseline = _record(per_fixture={"a": _fr(0.50), "b": _fr(0.60)}, wcsr_mean=0.55)
    candidate = _record(per_fixture={"a": _fr(0.55), "b": _fr(0.65)}, wcsr_mean=0.60)
    v = evaluate_acceptance(candidate, baseline, _DEFAULT_RULES)
    assert v.accepted is True
    assert v.rejection_reason is None
    assert v.corpus_delta == pytest.approx(0.05)


def test_gate_rejects_equal_corpus_score() -> None:
    baseline = _record(per_fixture={"a": _fr(0.50)}, wcsr_mean=0.50)
    candidate = _record(per_fixture={"a": _fr(0.50)}, wcsr_mean=0.50)
    v = evaluate_acceptance(candidate, baseline, _DEFAULT_RULES)
    assert v.accepted is False
    assert "did not strictly improve" in v.rejection_reason


def test_gate_rejects_worse_corpus_score() -> None:
    baseline = _record(per_fixture={"a": _fr(0.50)}, wcsr_mean=0.50)
    candidate = _record(per_fixture={"a": _fr(0.45)}, wcsr_mean=0.45)
    v = evaluate_acceptance(candidate, baseline, _DEFAULT_RULES)
    assert v.accepted is False
    assert v.corpus_delta == pytest.approx(-0.05)


def test_gate_allows_equal_when_strict_improvement_disabled() -> None:
    rules = AcceptanceConfig(
        corpus_metric="wcsr_triad_relaxed_mean",
        corpus_must_strictly_improve=False,
        max_per_fixture_drop_pp=5.0,
        max_runtime_factor=2.0,
        max_memory_factor=1.5,
    )
    baseline = _record(per_fixture={"a": _fr(0.50)}, wcsr_mean=0.50)
    candidate = _record(per_fixture={"a": _fr(0.50)}, wcsr_mean=0.50)
    v = evaluate_acceptance(candidate, baseline, rules)
    assert v.accepted is True


# ---------------------------------------------------------------------------
# Rule 2: per-fixture drop tolerance
# ---------------------------------------------------------------------------


def test_gate_rejects_per_fixture_drop_over_threshold() -> None:
    # Corpus improves 5pp, but fixture 'a' drops 6pp (> 5pp threshold)
    baseline = _record(per_fixture={"a": _fr(0.60), "b": _fr(0.40)}, wcsr_mean=0.50)
    candidate = _record(per_fixture={"a": _fr(0.54), "b": _fr(0.60)}, wcsr_mean=0.57)
    v = evaluate_acceptance(candidate, baseline, _DEFAULT_RULES)
    assert v.accepted is False
    assert "'a'" in v.rejection_reason
    assert "6.00pp" in v.rejection_reason


def test_gate_accepts_per_fixture_drop_at_threshold() -> None:
    # Exactly 5pp drop is OK (> means strictly greater)
    baseline = _record(per_fixture={"a": _fr(0.60), "b": _fr(0.40)}, wcsr_mean=0.50)
    candidate = _record(per_fixture={"a": _fr(0.55), "b": _fr(0.60)}, wcsr_mean=0.575)
    v = evaluate_acceptance(candidate, baseline, _DEFAULT_RULES)
    assert v.accepted is True


def test_gate_rejects_missing_fixture_in_candidate() -> None:
    baseline = _record(per_fixture={"a": _fr(0.50), "b": _fr(0.50)}, wcsr_mean=0.50)
    candidate = _record(per_fixture={"a": _fr(0.60)}, wcsr_mean=0.60)
    v = evaluate_acceptance(candidate, baseline, _DEFAULT_RULES)
    assert v.accepted is False
    assert "missing fixture 'b'" in v.rejection_reason


# ---------------------------------------------------------------------------
# Rule 3: runtime factor
# ---------------------------------------------------------------------------


def test_gate_rejects_runtime_over_cap() -> None:
    baseline = _record(per_fixture={"a": _fr(0.50)}, wcsr_mean=0.50, wall_mean=1.0)
    candidate = _record(
        per_fixture={"a": _fr(0.60)}, wcsr_mean=0.60, wall_mean=2.1
    )
    v = evaluate_acceptance(candidate, baseline, _DEFAULT_RULES)
    assert v.accepted is False
    assert "wall_seconds_mean" in v.rejection_reason


def test_gate_accepts_runtime_at_cap() -> None:
    baseline = _record(per_fixture={"a": _fr(0.50)}, wcsr_mean=0.50, wall_mean=1.0)
    candidate = _record(
        per_fixture={"a": _fr(0.60)}, wcsr_mean=0.60, wall_mean=2.0
    )
    v = evaluate_acceptance(candidate, baseline, _DEFAULT_RULES)
    assert v.accepted is True


def test_gate_skips_runtime_check_when_baseline_zero() -> None:
    # No spurious rejection when the baseline didn't measure runtime
    baseline = _record(per_fixture={"a": _fr(0.50)}, wcsr_mean=0.50, wall_mean=0.0)
    candidate = _record(
        per_fixture={"a": _fr(0.60)}, wcsr_mean=0.60, wall_mean=999.0
    )
    v = evaluate_acceptance(candidate, baseline, _DEFAULT_RULES)
    assert v.accepted is True


# ---------------------------------------------------------------------------
# Rule 4: memory factor
# ---------------------------------------------------------------------------


def test_gate_rejects_memory_over_cap() -> None:
    baseline = _record(
        per_fixture={"a": _fr(0.50)}, wcsr_mean=0.50, rss_max=100.0,
    )
    candidate = _record(
        per_fixture={"a": _fr(0.60)}, wcsr_mean=0.60, rss_max=200.0,
    )
    v = evaluate_acceptance(candidate, baseline, _DEFAULT_RULES)
    assert v.accepted is False
    assert "peak_rss_mb_max" in v.rejection_reason


def test_gate_accepts_memory_at_cap() -> None:
    baseline = _record(
        per_fixture={"a": _fr(0.50)}, wcsr_mean=0.50, rss_max=100.0,
    )
    candidate = _record(
        per_fixture={"a": _fr(0.60)}, wcsr_mean=0.60, rss_max=150.0,
    )
    v = evaluate_acceptance(candidate, baseline, _DEFAULT_RULES)
    assert v.accepted is True


# ---------------------------------------------------------------------------
# corpus_metric is configurable
# ---------------------------------------------------------------------------


def test_gate_uses_configured_corpus_metric() -> None:
    rules = AcceptanceConfig(
        corpus_metric="boundary_iou_0p5_mean",
        corpus_must_strictly_improve=True,
        max_per_fixture_drop_pp=100.0,  # disable fixture rule for this test
        max_runtime_factor=100.0,
        max_memory_factor=100.0,
    )
    baseline = _record(per_fixture={"a": _fr(0.50)}, wcsr_mean=0.50)
    candidate = _record(per_fixture={"a": _fr(0.50)}, wcsr_mean=0.50)
    # Both have the same boundary_iou_0p5_mean (0.5) -> rejected
    v = evaluate_acceptance(candidate, baseline, rules)
    assert v.accepted is False


def test_gate_unknown_corpus_metric_raises() -> None:
    rules = AcceptanceConfig(
        corpus_metric="nonexistent_field",
        corpus_must_strictly_improve=True,
        max_per_fixture_drop_pp=5.0,
        max_runtime_factor=2.0,
        max_memory_factor=1.5,
    )
    baseline = _record(per_fixture={"a": _fr(0.50)}, wcsr_mean=0.50)
    candidate = _record(per_fixture={"a": _fr(0.50)}, wcsr_mean=0.50)
    with pytest.raises(ValueError, match="nonexistent_field"):
        evaluate_acceptance(candidate, baseline, rules)


# ---------------------------------------------------------------------------
# Verdict shape
# ---------------------------------------------------------------------------


def test_verdict_is_frozen_dataclass() -> None:
    import dataclasses

    v = AcceptanceVerdict(True, None, 0.05)
    with pytest.raises(dataclasses.FrozenInstanceError):
        v.accepted = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# YAML space loader + candidate enumeration
# ---------------------------------------------------------------------------


def test_load_space_parses_baseline_neighborhood() -> None:
    from pathlib import Path

    p = Path(__file__).resolve().parents[1] / "bench" / "spaces" / "baseline_neighborhood.yaml"
    space = load_space(p)
    assert space.strategy == "random"
    assert space.seed == 1729
    assert space.budget == 64
    assert space.acceptance.corpus_metric == "wcsr_triad_relaxed_mean"
    assert space.acceptance.max_per_fixture_drop_pp == pytest.approx(5.0)
    assert "diatonic_bias" in space.parameters
    assert "bass_root_bias" in space.parameters


def test_load_space_rejects_unknown_strategy(tmp_path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text(
        "strategy: laser_beam\n"
        "parameters:\n"
        "  diatonic_bias: {type: float, range: [0, 1], step: 0.1}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="strategy must be"):
        load_space(p)


def test_load_space_rejects_empty_parameters(tmp_path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text("strategy: grid\nparameters: {}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="parameters must be a non-empty mapping"):
        load_space(p)


def test_enumerate_candidates_grid(tmp_path) -> None:
    p = tmp_path / "grid.yaml"
    p.write_text(
        "strategy: grid\n"
        "parameters:\n"
        "  diatonic_bias: {type: float, range: [0.10, 0.20], step: 0.05}\n"
        "  bass_root_bias: {type: float, range: [0.00, 0.10], step: 0.05}\n",
        encoding="utf-8",
    )
    space = load_space(p)
    cands = enumerate_candidates(space)
    # 3 * 3 = 9 grid points
    assert len(cands) == 9
    # First candidate has both at low end
    assert cands[0]["diatonic_bias"] == pytest.approx(0.10)


def test_enumerate_candidates_random_is_deterministic_per_seed(tmp_path) -> None:
    p = tmp_path / "rand.yaml"
    p.write_text(
        "strategy: random\nseed: 42\nbudget: 4\n"
        "parameters:\n"
        "  diatonic_bias: {type: float, range: [0.10, 0.20], step: 0.05}\n"
        "  bass_root_bias: {type: float, range: [0.00, 0.10], step: 0.05}\n",
        encoding="utf-8",
    )
    a = enumerate_candidates(load_space(p))
    b = enumerate_candidates(load_space(p))
    assert a == b
    assert len(a) == 4


def test_enumerate_candidates_coordinate_descent(tmp_path) -> None:
    p = tmp_path / "cd.yaml"
    p.write_text(
        "strategy: coordinate_descent\n"
        "parameters:\n"
        "  diatonic_bias: {type: float, range: [0.10, 0.20], step: 0.05}\n"
        "  bass_root_bias: {type: float, range: [0.00, 0.10], step: 0.05}\n",
        encoding="utf-8",
    )
    cands = enumerate_candidates(load_space(p))
    # Each axis has 3 values. CD: 3 + 3 - 1 (anchor shared) = 5 unique configs.
    assert len(cands) == 5
