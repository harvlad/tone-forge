"""Tests for scripts.analysis_eval (aggregation; no audio needed)."""
import pytest

from scripts.analysis_eval import aggregate


class TestAggregate:
    def test_means_only_over_present_metrics(self):
        rows = [
            {"name": "a", "wcsr_triad": 0.8, "wcsr_strict": 0.6,
             "key_score": 1.0},
            {"name": "b", "wcsr_triad": 0.4, "wcsr_strict": 0.2},
        ]
        s = aggregate(rows)
        assert s["n_fixtures"] == 2
        assert s["mean_wcsr_triad"] == pytest.approx(0.6)
        assert s["mean_key_score"] == 1.0  # only fixture a has key truth
        assert s["n_key_score"] == 1
        assert s["mean_boundary_f_05"] is None
        assert s["n_boundary_f_05"] == 0

    def test_below_floor_names(self):
        rows = [
            {"name": "a", "wcsr_triad": 0.1, "below_floor": True},
            {"name": "b", "wcsr_triad": 0.9, "below_floor": False},
        ]
        assert aggregate(rows)["below_floor"] == ["a"]

    def test_empty(self):
        s = aggregate([])
        assert s["n_fixtures"] == 0
        assert s["mean_wcsr_triad"] is None
        assert s["below_floor"] == []
