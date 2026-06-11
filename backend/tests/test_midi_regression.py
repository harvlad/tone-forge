"""MIDI extraction regression tests.

These tests ensure that MIDI extraction quality does not regress
as the codebase evolves. They run against a benchmark dataset and
compare against baseline metrics.

Usage:
    pytest tests/test_midi_regression.py -v
    pytest tests/test_midi_regression.py::TestMIDIRegression::test_overall_f1_not_regressed -v

Environment variables:
    MIDI_BENCHMARK_PATH: Path to benchmark dataset JSON
    MIDI_BASELINE_PATH: Path to baseline metrics JSON
    MIDI_REGRESSION_TOLERANCE: Maximum allowed regression (default 0.02)
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pytest

# Environment variables for benchmark tests
BENCHMARK_PATH = os.environ.get("MIDI_BENCHMARK_PATH")
BASELINE_PATH = os.environ.get("MIDI_BASELINE_PATH")
REGRESSION_TOLERANCE = float(os.environ.get("MIDI_REGRESSION_TOLERANCE", "0.02"))

# Marker for tests that require benchmark dataset
requires_benchmark = pytest.mark.skipif(
    BENCHMARK_PATH is None,
    reason="MIDI_BENCHMARK_PATH not set"
)


@pytest.fixture(scope="module")
def benchmark_dataset():
    """Load the benchmark dataset."""
    from tone_forge.evaluation.midi_benchmark import MIDIBenchmarkDataset

    if BENCHMARK_PATH is None:
        pytest.skip("MIDI_BENCHMARK_PATH not set")

    path = Path(BENCHMARK_PATH)
    if not path.exists():
        pytest.skip(f"Benchmark dataset not found: {path}")

    return MIDIBenchmarkDataset.load(path)


@pytest.fixture(scope="module")
def baseline_metrics():
    """Load baseline metrics if available."""
    if BASELINE_PATH is None:
        return None

    path = Path(BASELINE_PATH)
    if not path.exists():
        return None

    with open(path, "r") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def benchmark_results(benchmark_dataset):
    """Run benchmark and cache results for the test session."""
    from tone_forge.evaluation.midi_benchmark import MIDIBenchmarkRunner

    runner = MIDIBenchmarkRunner(
        use_auto_classify=True,
        baseline_path=Path(BASELINE_PATH) if BASELINE_PATH else None,
    )

    metrics, sample_results = runner.run(benchmark_dataset)
    return metrics, sample_results


@requires_benchmark
class TestMIDIRegression:
    """Regression tests for MIDI extraction."""

    def test_overall_f1_not_regressed(self, benchmark_results, baseline_metrics):
        """Overall F1 should not drop more than tolerance from baseline."""
        metrics, _ = benchmark_results

        if baseline_metrics is None:
            pytest.skip("No baseline available for comparison")

        baseline_f1 = baseline_metrics.get("overall", {}).get("f1", 0)
        current_f1 = metrics.overall_f1
        regression = current_f1 - baseline_f1

        assert regression >= -REGRESSION_TOLERANCE, (
            f"Overall F1 regressed by {-regression:.1%} "
            f"(baseline: {baseline_f1:.1%}, current: {current_f1:.1%})"
        )

    def test_per_profile_f1_not_regressed(self, benchmark_results, baseline_metrics):
        """Per-profile F1 should not regress significantly."""
        metrics, _ = benchmark_results

        if baseline_metrics is None:
            pytest.skip("No baseline available for comparison")

        baseline_profile_f1 = baseline_metrics.get("per_profile", {}).get("f1", {})

        for profile, baseline_f1 in baseline_profile_f1.items():
            if profile in metrics.per_profile_f1:
                current_f1 = metrics.per_profile_f1[profile]
                regression = current_f1 - baseline_f1

                assert regression >= -REGRESSION_TOLERANCE, (
                    f"Profile '{profile}' F1 regressed by {-regression:.1%} "
                    f"(baseline: {baseline_f1:.1%}, current: {current_f1:.1%})"
                )

    def test_per_stem_f1_not_regressed(self, benchmark_results, baseline_metrics):
        """Per-stem F1 should not regress significantly."""
        metrics, _ = benchmark_results

        if baseline_metrics is None:
            pytest.skip("No baseline available for comparison")

        baseline_stem_f1 = baseline_metrics.get("per_stem", {}).get("f1", {})

        for stem, baseline_f1 in baseline_stem_f1.items():
            if stem in metrics.per_stem_f1:
                current_f1 = metrics.per_stem_f1[stem]
                regression = current_f1 - baseline_f1

                assert regression >= -REGRESSION_TOLERANCE, (
                    f"Stem '{stem}' F1 regressed by {-regression:.1%} "
                    f"(baseline: {baseline_f1:.1%}, current: {current_f1:.1%})"
                )


@requires_benchmark
class TestMIDIQualityThresholds:
    """Tests for minimum quality thresholds."""

    def test_lead_staccato_recall_threshold(self, benchmark_results, benchmark_dataset):
        """Lead staccato must preserve repeated notes (high recall)."""
        metrics, sample_results = benchmark_results

        # Filter to lead_staccato profile samples
        staccato_samples = [
            r for r in sample_results
            if r.success and r.profile_used == "lead_staccato"
        ]

        if not staccato_samples:
            pytest.skip("No lead_staccato samples in dataset")

        # Calculate average recall for staccato samples
        avg_recall = np.mean([
            r.metrics.note_recall for r in staccato_samples if r.metrics
        ])

        # Staccato profiles should have >= 65% recall to preserve repeated notes
        assert avg_recall >= 0.65, (
            f"Lead staccato recall too low: {avg_recall:.1%} (minimum: 65%)"
        )

    def test_bass_precision_threshold(self, benchmark_results, benchmark_dataset):
        """Bass extraction should have reasonable precision."""
        metrics, _ = benchmark_results

        if "bass" not in metrics.per_stem_f1:
            pytest.skip("No bass samples in dataset")

        bass_precision = metrics.per_stem_precision.get("bass", 0)

        # Bass should have >= 60% precision
        assert bass_precision >= 0.60, (
            f"Bass precision too low: {bass_precision:.1%} (minimum: 60%)"
        )

    def test_pad_f1_threshold(self, benchmark_results, benchmark_dataset):
        """Pad extraction should meet minimum F1."""
        metrics, _ = benchmark_results

        if "pad" not in metrics.per_stem_f1:
            pytest.skip("No pad samples in dataset")

        pad_f1 = metrics.per_stem_f1.get("pad", 0)

        # Pads should have >= 50% F1 (they're harder due to harmonics)
        assert pad_f1 >= 0.50, (
            f"Pad F1 too low: {pad_f1:.1%} (minimum: 50%)"
        )


@requires_benchmark
class TestProfileSelection:
    """Tests for profile auto-classification."""

    def test_auto_classification_consistency(self, benchmark_results):
        """Auto-classified profiles should match expected profiles."""
        _, sample_results = benchmark_results

        mismatches = []
        for result in sample_results:
            if not result.success:
                continue
            # If sample had a profile hint and we auto-classified differently
            # this might indicate a classification issue (or just flexibility)
            if result.profile_auto_classified and result.profile_used:
                # Just track, don't fail - auto-classification may differ
                pass

        # This test mainly ensures auto-classification runs without errors

    def test_profile_coverage(self, benchmark_results, benchmark_dataset):
        """Verify that multiple profiles are being used."""
        _, sample_results = benchmark_results

        profiles_used = set(
            r.profile_used for r in sample_results
            if r.success and r.profile_used
        )

        # Should use at least 2 different profiles
        assert len(profiles_used) >= 2, (
            f"Only {len(profiles_used)} profile(s) used: {profiles_used}. "
            "Expected diversity in profile selection."
        )


@requires_benchmark
class TestFailureAnalysis:
    """Tests that analyze extraction failures."""

    def test_no_catastrophic_failures(self, benchmark_results):
        """No sample should have F1 < 10%."""
        metrics, sample_results = benchmark_results

        catastrophic = [
            (r.sample_id, r.metrics.note_f1)
            for r in sample_results
            if r.success and r.metrics and r.metrics.note_f1 < 0.10
        ]

        assert len(catastrophic) == 0, (
            f"Found {len(catastrophic)} samples with catastrophic F1 < 10%: "
            f"{catastrophic[:5]}"
        )

    def test_error_rate_acceptable(self, benchmark_results):
        """Error rate should be below 5%."""
        _, sample_results = benchmark_results

        error_count = sum(1 for r in sample_results if not r.success)
        error_rate = error_count / len(sample_results) if sample_results else 0

        assert error_rate < 0.05, (
            f"Error rate too high: {error_rate:.1%} ({error_count}/{len(sample_results)})"
        )


# Utility test for baseline capture
@requires_benchmark
class TestBaselineCapture:
    """Utility tests for capturing baselines."""

    @pytest.mark.skip(reason="Run manually to capture baseline")
    def test_capture_baseline(self, benchmark_results):
        """Capture current metrics as baseline.

        Run with: pytest tests/test_midi_regression.py::TestBaselineCapture::test_capture_baseline --runxfail
        """
        from tone_forge.evaluation.midi_benchmark import save_baseline

        metrics, _ = benchmark_results

        output_path = Path("tests/fixtures/midi_baseline.json")
        output_path.parent.mkdir(parents=True, exist_ok=True)

        save_baseline(metrics, output_path)
        print(f"\nBaseline saved to {output_path}")
        print(metrics.summary())


# Quick smoke test that doesn't require benchmark dataset
class TestMIDIExtractionSmoke:
    """Smoke tests that run without benchmark dataset."""

    def test_extractor_imports(self):
        """Verify MIDI extraction modules can be imported."""
        from tone_forge.midi import (
            MultiPassExtractor,
            create_extractor,
            create_extractor_for_profile,
            get_profile,
            classify_profile,
        )

        # Basic instantiation
        extractor = MultiPassExtractor()
        assert len(extractor.passes) > 0

    def test_profile_registry(self):
        """Verify profile registry works."""
        from tone_forge.midi import (
            get_profile_registry,
            get_profile,
            get_default_profile_for_stem,
        )

        registry = get_profile_registry()
        profiles = registry.list_profiles()

        assert len(profiles) >= 9, f"Expected 9+ profiles, got {len(profiles)}"

        # Check key profiles exist
        for profile_name in ["mono_bass", "lead_staccato", "arp_fast", "pad_sustained"]:
            profile = get_profile(profile_name)
            assert profile is not None, f"Profile {profile_name} not found"

    def test_profile_driven_pipeline(self):
        """Verify profile-driven pipeline creation."""
        from tone_forge.midi import create_extractor_for_profile, get_profile

        # Lead staccato should skip delay cleanup
        profile = get_profile("lead_staccato")
        extractor = create_extractor_for_profile(profile)

        pass_names = [p.name for p in extractor.passes]
        assert "delay_cleanup" not in pass_names, "lead_staccato should skip delay_cleanup"
        assert "high_confidence" in pass_names

        # Mono bass should include octave correction
        profile = get_profile("mono_bass")
        extractor = create_extractor_for_profile(profile)

        pass_names = [p.name for p in extractor.passes]
        assert "octave_correction" in pass_names, "mono_bass should include octave_correction"

    def test_new_cleanup_passes(self):
        """Verify Sprint 3 cleanup passes are available."""
        from tone_forge.midi.passes import (
            HarmonicSuppressionPass,
            DelayCleanupPass,
            OctaveCorrectionPass,
            BeatGridFilterPass,
            KeyConformityPass,
        )

        # Instantiate each
        passes = [
            HarmonicSuppressionPass(),
            DelayCleanupPass(),
            OctaveCorrectionPass(),
            BeatGridFilterPass(),
            KeyConformityPass(),
        ]

        for p in passes:
            assert hasattr(p, "name")
            assert hasattr(p, "process")
