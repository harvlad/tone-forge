"""Tests for tone_forge/reconstruction - Reconstruction quality analysis."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tone_forge.reconstruction import (
    # Stem quality
    StemQuality,
    StemQualityAnalyzer,
    ConfidenceRegion,
    get_analyzer,
    analyze_stem_quality,
    # Contamination
    ContaminationType,
    ContaminationEvent,
    ContaminationAnalysis,
    ContaminationDetector,
    get_detector,
    detect_contamination,
    # Artifacts
    ArtifactType,
    DetectedArtifact,
    ArtifactAnalysis,
    ArtifactDetector,
    get_artifact_detector,
    detect_artifacts,
    # Confidence
    RegionConfidence,
    ConfidenceMap,
    ConfidenceMapper,
    get_confidence_mapper,
    build_confidence_map,
    # Quality gates
    QualityThresholds,
    QualityReport,
    QualityGates,
    get_quality_gates,
)


SR = 22050


def _make_sine_wave(freq: float = 440, duration: float = 1.0, sr: int = SR) -> np.ndarray:
    """Generate a sine wave."""
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    return (np.sin(2 * np.pi * freq * t) * 0.5).astype(np.float32)


def _make_bass_audio(duration: float = 1.0) -> np.ndarray:
    """Generate bass-like audio (low frequency)."""
    t = np.linspace(0, duration, int(SR * duration), endpoint=False)
    # Low fundamental with harmonics
    sig = np.sin(2 * np.pi * 80 * t)
    sig += 0.5 * np.sin(2 * np.pi * 160 * t)
    sig += 0.25 * np.sin(2 * np.pi * 240 * t)
    return (sig * 0.5).astype(np.float32)


def _make_drum_audio(duration: float = 1.0) -> np.ndarray:
    """Generate drum-like audio (transient + noise)."""
    t = np.linspace(0, duration, int(SR * duration), endpoint=False)
    # Sharp envelope
    env = np.exp(-30 * t)
    # Low thump + noise
    sig = np.sin(2 * np.pi * 60 * t) * env
    noise = np.random.randn(len(t)) * 0.3 * env
    return ((sig + noise) * 0.5).astype(np.float32)


def _make_reverb_audio(duration: float = 2.0) -> np.ndarray:
    """Generate audio with reverb-like characteristics."""
    t = np.linspace(0, duration, int(SR * duration), endpoint=False)
    # Impulse at start
    sig = np.zeros_like(t)
    sig[:int(0.01 * SR)] = np.sin(2 * np.pi * 440 * t[:int(0.01 * SR)])
    # Long decay tail
    decay = np.exp(-2 * t)
    sig = sig * decay + np.random.randn(len(t)) * 0.05 * decay
    return (sig * 0.5).astype(np.float32)


def _make_stereo_audio(duration: float = 1.0) -> np.ndarray:
    """Generate stereo audio."""
    mono = _make_sine_wave(440, duration)
    # Slight phase difference
    t = np.linspace(0, duration, int(SR * duration), endpoint=False)
    left = mono
    right = np.sin(2 * np.pi * 440 * t + 0.1) * 0.5
    return np.stack([left, right.astype(np.float32)])


def _make_contaminated_bass(duration: float = 1.0) -> np.ndarray:
    """Generate bass audio with high frequency contamination."""
    bass = _make_bass_audio(duration)
    # Add high frequency content (simulating bleed)
    t = np.linspace(0, duration, int(SR * duration), endpoint=False)
    high = 0.3 * np.sin(2 * np.pi * 4000 * t)
    return (bass + high.astype(np.float32))


# ======================= Stem Quality Tests =======================

class TestConfidenceRegion:
    """Test ConfidenceRegion dataclass."""

    def test_create(self):
        region = ConfidenceRegion(
            start_time=0.0,
            end_time=1.0,
            confidence=0.8,
            reason="Good signal",
        )
        assert region.start_time == 0.0
        assert region.end_time == 1.0
        assert region.confidence == 0.8
        assert region.duration == 1.0


class TestStemQuality:
    """Test StemQuality dataclass."""

    def test_create_with_defaults(self):
        sq = StemQuality(
            stem_type="bass",
            contamination_score=0.1,
            transient_integrity=0.8,
            harmonic_purity=0.9,
            reverb_density=0.2,
            stereo_coherence=0.95,
            snr_estimate=25.0,
        )
        assert sq.stem_type == "bass"
        assert sq.contamination_score == 0.1

    def test_overall_quality(self):
        # Good quality stem
        sq_good = StemQuality(
            stem_type="bass",
            contamination_score=0.1,
            transient_integrity=0.9,
            harmonic_purity=0.9,
            reverb_density=0.1,
            stereo_coherence=0.95,
            snr_estimate=30.0,
        )
        assert sq_good.overall_quality > 0.7

        # Poor quality stem
        sq_poor = StemQuality(
            stem_type="bass",
            contamination_score=0.9,
            transient_integrity=0.2,
            harmonic_purity=0.3,
            reverb_density=0.8,
            stereo_coherence=0.4,
            snr_estimate=5.0,
        )
        assert sq_poor.overall_quality < 0.4

    def test_is_usable(self):
        sq_good = StemQuality(
            stem_type="bass",
            contamination_score=0.1,
            transient_integrity=0.8,
            harmonic_purity=0.8,
            reverb_density=0.2,
            stereo_coherence=0.9,
            snr_estimate=25.0,
        )
        assert sq_good.is_usable

        sq_poor = StemQuality(
            stem_type="bass",
            contamination_score=0.95,
            transient_integrity=0.1,
            harmonic_purity=0.1,
            reverb_density=0.95,
            stereo_coherence=0.2,
            snr_estimate=2.0,
        )
        assert not sq_poor.is_usable

    def test_to_dict(self):
        sq = StemQuality(
            stem_type="bass",
            contamination_score=0.1,
            transient_integrity=0.8,
            harmonic_purity=0.9,
            reverb_density=0.2,
            stereo_coherence=0.95,
            snr_estimate=25.0,
        )
        d = sq.to_dict()
        assert d["stem_type"] == "bass"
        assert "overall_quality" in d
        assert "is_usable" in d


class TestStemQualityAnalyzer:
    """Test StemQualityAnalyzer class."""

    def test_get_analyzer_singleton(self):
        analyzer1 = get_analyzer()
        analyzer2 = get_analyzer()
        assert analyzer1 is analyzer2

    def test_analyze_bass(self):
        analyzer = StemQualityAnalyzer()
        audio = _make_bass_audio(duration=2.0)
        result = analyzer.analyze(audio, SR, "bass")

        assert isinstance(result, StemQuality)
        assert result.stem_type == "bass"
        assert 0 <= result.contamination_score <= 1
        assert 0 <= result.transient_integrity <= 1
        assert 0 <= result.harmonic_purity <= 1

    def test_analyze_drums(self):
        analyzer = StemQualityAnalyzer()
        audio = _make_drum_audio(duration=2.0)
        result = analyzer.analyze(audio, SR, "drums")

        assert isinstance(result, StemQuality)
        assert result.stem_type == "drums"

    def test_analyze_stereo(self):
        analyzer = StemQualityAnalyzer()
        audio = _make_stereo_audio(duration=2.0)
        result = analyzer.analyze(audio, SR, "other")

        assert isinstance(result, StemQuality)
        assert 0 <= result.stereo_coherence <= 1

    def test_analyze_with_reverb(self):
        analyzer = StemQualityAnalyzer()
        audio = _make_reverb_audio(duration=2.0)
        result = analyzer.analyze(audio, SR, "other")

        # Should detect reverb
        assert result.reverb_density > 0.3

    def test_analyze_all(self):
        analyzer = StemQualityAnalyzer()
        stems = {
            "bass": _make_bass_audio(duration=2.0),
            "drums": _make_drum_audio(duration=2.0),
        }
        results = analyzer.analyze_all(stems, SR)

        assert "bass" in results
        assert "drums" in results
        assert isinstance(results["bass"], StemQuality)

    def test_analyze_stem_quality_convenience(self):
        stems = {
            "bass": _make_bass_audio(duration=2.0),
        }
        results = analyze_stem_quality(stems, SR)
        assert "bass" in results

    def test_confidence_regions(self):
        analyzer = StemQualityAnalyzer(region_duration=0.5)
        audio = _make_bass_audio(duration=2.0)
        result = analyzer.analyze(audio, SR, "bass")

        # Should have multiple confidence regions
        assert len(result.confidence_regions) >= 2


# ======================= Contamination Tests =======================

class TestContaminationType:
    """Test ContaminationType enum."""

    def test_enum_values(self):
        assert ContaminationType.CROSS_STEM_BLEED.value == "cross_stem_bleed"
        assert ContaminationType.REVERB_TAIL.value == "reverb_tail"
        assert ContaminationType.DELAY_ARTIFACT.value == "delay_artifact"


class TestContaminationEvent:
    """Test ContaminationEvent dataclass."""

    def test_create(self):
        event = ContaminationEvent(
            contamination_type=ContaminationType.CROSS_STEM_BLEED,
            time_start=1.0,
            time_end=2.0,
            severity=0.7,
            confidence=0.8,
            source_stem="drums",
        )
        assert event.duration == 1.0
        assert event.severity == 0.7


class TestContaminationAnalysis:
    """Test ContaminationAnalysis dataclass."""

    def test_empty_analysis(self):
        analysis = ContaminationAnalysis(stem_type="bass")
        assert analysis.event_count == 0
        assert analysis.total_contaminated_duration == 0.0

    def test_with_events(self):
        events = [
            ContaminationEvent(
                contamination_type=ContaminationType.CROSS_STEM_BLEED,
                time_start=0.0,
                time_end=0.5,
                severity=0.5,
                confidence=0.8,
            ),
            ContaminationEvent(
                contamination_type=ContaminationType.REVERB_TAIL,
                time_start=1.0,
                time_end=1.5,
                severity=0.3,
                confidence=0.7,
            ),
        ]
        analysis = ContaminationAnalysis(
            stem_type="bass",
            events=events,
            overall_contamination=0.4,
        )
        assert analysis.event_count == 2
        assert analysis.total_contaminated_duration == 1.0

    def test_get_events_in_range(self):
        events = [
            ContaminationEvent(
                contamination_type=ContaminationType.CROSS_STEM_BLEED,
                time_start=0.0,
                time_end=0.5,
                severity=0.5,
                confidence=0.8,
            ),
            ContaminationEvent(
                contamination_type=ContaminationType.REVERB_TAIL,
                time_start=2.0,
                time_end=2.5,
                severity=0.3,
                confidence=0.7,
            ),
        ]
        analysis = ContaminationAnalysis(stem_type="bass", events=events)

        # Should find first event
        found = analysis.get_events_in_range(0.0, 1.0)
        assert len(found) == 1

        # Should find nothing
        found = analysis.get_events_in_range(1.0, 1.5)
        assert len(found) == 0


class TestContaminationDetector:
    """Test ContaminationDetector class."""

    def test_get_detector_singleton(self):
        detector1 = get_detector()
        detector2 = get_detector()
        assert detector1 is detector2

    def test_detect_clean_audio(self):
        detector = ContaminationDetector()
        audio = _make_bass_audio(duration=2.0)
        result = detector.detect(audio, SR, "bass")

        assert isinstance(result, ContaminationAnalysis)
        assert result.stem_type == "bass"

    def test_detect_with_other_stems(self):
        detector = ContaminationDetector()
        bass = _make_bass_audio(duration=2.0)
        drums = _make_drum_audio(duration=2.0)

        result = detector.detect(
            bass, SR, "bass",
            other_stems={"drums": drums}
        )

        assert isinstance(result, ContaminationAnalysis)
        # Should check for cross-stem bleed

    def test_detect_all(self):
        detector = ContaminationDetector()
        stems = {
            "bass": _make_bass_audio(duration=2.0),
            "drums": _make_drum_audio(duration=2.0),
        }
        results = detector.detect_all(stems, SR)

        assert "bass" in results
        assert "drums" in results

    def test_detect_contamination_convenience(self):
        audio = _make_bass_audio(duration=2.0)
        result = detect_contamination(audio, SR, "bass")
        assert isinstance(result, ContaminationAnalysis)

    def test_clean_regions(self):
        detector = ContaminationDetector()
        audio = _make_bass_audio(duration=2.0)
        result = detector.detect(audio, SR, "bass")

        # Clean audio should have clean regions
        if not result.events:
            assert len(result.clean_regions) > 0


# ======================= Artifact Tests =======================

class TestArtifactType:
    """Test ArtifactType enum."""

    def test_enum_values(self):
        assert ArtifactType.SPECTRAL_SMEARING.value == "spectral_smearing"
        assert ArtifactType.MUSICAL_NOISE.value == "musical_noise"


class TestDetectedArtifact:
    """Test DetectedArtifact dataclass."""

    def test_create(self):
        artifact = DetectedArtifact(
            artifact_type=ArtifactType.SPECTRAL_SMEARING,
            time_start=0.5,
            time_end=1.0,
            severity=0.6,
            confidence=0.7,
        )
        assert artifact.duration == 0.5


class TestArtifactAnalysis:
    """Test ArtifactAnalysis dataclass."""

    def test_empty_analysis(self):
        analysis = ArtifactAnalysis(stem_type="bass")
        assert analysis.artifact_count == 0

    def test_with_artifacts(self):
        artifacts = [
            DetectedArtifact(
                artifact_type=ArtifactType.SPECTRAL_SMEARING,
                time_start=0.0,
                time_end=0.5,
                severity=0.5,
                confidence=0.7,
            ),
        ]
        analysis = ArtifactAnalysis(
            stem_type="bass",
            artifacts=artifacts,
            overall_artifact_score=0.3,
        )
        assert analysis.artifact_count == 1


class TestArtifactDetector:
    """Test ArtifactDetector class."""

    def test_get_detector_singleton(self):
        detector1 = get_artifact_detector()
        detector2 = get_artifact_detector()
        assert detector1 is detector2

    def test_detect_clean_audio(self):
        detector = ArtifactDetector()
        audio = _make_sine_wave(440, duration=2.0)
        result = detector.detect(audio, SR, "other")

        assert isinstance(result, ArtifactAnalysis)
        assert result.stem_type == "other"

    def test_detect_stereo(self):
        detector = ArtifactDetector()
        audio = _make_stereo_audio(duration=2.0)
        result = detector.detect(audio, SR, "other")

        assert isinstance(result, ArtifactAnalysis)

    def test_detect_all(self):
        detector = ArtifactDetector()
        stems = {
            "bass": _make_bass_audio(duration=2.0),
            "drums": _make_drum_audio(duration=2.0),
        }
        results = detector.detect_all(stems, SR)

        assert "bass" in results
        assert "drums" in results

    def test_detect_artifacts_convenience(self):
        audio = _make_bass_audio(duration=2.0)
        result = detect_artifacts(audio, SR, "bass")
        assert isinstance(result, ArtifactAnalysis)


# ======================= Confidence Map Tests =======================

class TestRegionConfidence:
    """Test RegionConfidence dataclass."""

    def test_create(self):
        region = RegionConfidence(
            time_start=0.0,
            time_end=0.5,
            note_confidence=0.8,
            descriptor_confidence=0.7,
            timing_confidence=0.9,
            contamination_probability=0.1,
            artifact_probability=0.05,
            harmonic_stability=0.85,
        )
        assert region.duration == 0.5

    def test_compute_overall(self):
        region = RegionConfidence(
            time_start=0.0,
            time_end=0.5,
            note_confidence=0.8,
            descriptor_confidence=0.7,
            timing_confidence=0.9,
            contamination_probability=0.1,
            artifact_probability=0.05,
            harmonic_stability=0.85,
        )
        overall = region.compute_overall()
        assert 0 <= overall <= 1


class TestConfidenceMap:
    """Test ConfidenceMap dataclass."""

    def test_empty_map(self):
        cmap = ConfidenceMap(
            stem_type="bass",
            duration=2.0,
        )
        assert cmap.region_count == 0

    def test_get_confidence_at(self):
        regions = [
            RegionConfidence(
                time_start=0.0,
                time_end=0.5,
                note_confidence=0.8,
                descriptor_confidence=0.7,
                timing_confidence=0.9,
                contamination_probability=0.1,
                artifact_probability=0.05,
                harmonic_stability=0.85,
            ),
        ]
        cmap = ConfidenceMap(
            stem_type="bass",
            duration=2.0,
            regions=regions,
        )

        # Should find region
        found = cmap.get_confidence_at(0.25)
        assert found is not None

        # Should not find
        found = cmap.get_confidence_at(1.0)
        assert found is None


class TestConfidenceMapper:
    """Test ConfidenceMapper class."""

    def test_get_mapper_singleton(self):
        mapper1 = get_confidence_mapper()
        mapper2 = get_confidence_mapper()
        assert mapper1 is mapper2

    def test_build_map(self):
        mapper = ConfidenceMapper(region_duration=0.5)
        audio = _make_bass_audio(duration=2.0)
        result = mapper.build_map(audio, SR, "bass")

        assert isinstance(result, ConfidenceMap)
        assert result.stem_type == "bass"
        assert result.duration == pytest.approx(2.0, abs=0.1)
        assert result.region_count >= 2

    def test_build_map_with_quality(self):
        mapper = ConfidenceMapper()
        audio = _make_bass_audio(duration=2.0)

        # Create stem quality
        sq = StemQuality(
            stem_type="bass",
            contamination_score=0.1,
            transient_integrity=0.8,
            harmonic_purity=0.9,
            reverb_density=0.2,
            stereo_coherence=0.95,
            snr_estimate=25.0,
            confidence_regions=[
                ConfidenceRegion(0.0, 1.0, 0.8, ""),
                ConfidenceRegion(1.0, 2.0, 0.7, ""),
            ],
        )

        result = mapper.build_map(audio, SR, "bass", stem_quality=sq)
        assert isinstance(result, ConfidenceMap)

    def test_build_confidence_map_convenience(self):
        audio = _make_bass_audio(duration=2.0)
        result = build_confidence_map(audio, SR, "bass")
        assert isinstance(result, ConfidenceMap)

    def test_to_array(self):
        mapper = ConfidenceMapper(region_duration=0.5)
        audio = _make_bass_audio(duration=2.0)
        cmap = mapper.build_map(audio, SR, "bass")

        arr = cmap.to_array(SR, hop_length=512)
        assert isinstance(arr, np.ndarray)
        assert len(arr) > 0


# ======================= Quality Gates Tests =======================

class TestQualityThresholds:
    """Test QualityThresholds dataclass."""

    def test_default(self):
        thresholds = QualityThresholds()
        assert thresholds.min_stem_quality == 0.4
        assert thresholds.max_contamination == 0.6

    def test_strict(self):
        thresholds = QualityThresholds.strict()
        assert thresholds.min_stem_quality > 0.5
        assert thresholds.max_contamination < 0.5

    def test_lenient(self):
        thresholds = QualityThresholds.lenient()
        assert thresholds.min_stem_quality < 0.3
        assert thresholds.max_contamination > 0.7

    def test_for_genre_synthwave(self):
        thresholds = QualityThresholds.for_genre("synthwave")
        # Synthwave should allow more reverb
        assert thresholds.max_reverb_density > 0.9


class TestQualityGates:
    """Test QualityGates class."""

    def test_get_gates(self):
        gates1 = get_quality_gates()
        gates2 = get_quality_gates()
        assert gates1 is gates2

    def test_evaluate_good_quality(self):
        gates = QualityGates()

        sq = StemQuality(
            stem_type="bass",
            contamination_score=0.1,
            transient_integrity=0.8,
            harmonic_purity=0.9,
            reverb_density=0.2,
            stereo_coherence=0.95,
            snr_estimate=25.0,
        )

        report = gates.evaluate("bass", stem_quality=sq)

        assert isinstance(report, QualityReport)
        assert report.passed
        assert report.overall_quality.value in ["excellent", "good", "acceptable"]

    def test_evaluate_poor_quality(self):
        gates = QualityGates()

        sq = StemQuality(
            stem_type="bass",
            contamination_score=0.9,
            transient_integrity=0.1,
            harmonic_purity=0.2,
            reverb_density=0.9,
            stereo_coherence=0.3,
            snr_estimate=3.0,
        )

        report = gates.evaluate("bass", stem_quality=sq)

        assert isinstance(report, QualityReport)
        assert not report.passed
        assert len(report.failed_gates) > 0

    def test_stem_quality_sufficient(self):
        gates = QualityGates()

        sq_good = StemQuality(
            stem_type="bass",
            contamination_score=0.1,
            transient_integrity=0.8,
            harmonic_purity=0.9,
            reverb_density=0.2,
            stereo_coherence=0.95,
            snr_estimate=25.0,
        )
        assert gates.stem_quality_sufficient(sq_good)

        sq_bad = StemQuality(
            stem_type="bass",
            contamination_score=0.9,
            transient_integrity=0.1,
            harmonic_purity=0.2,
            reverb_density=0.9,
            stereo_coherence=0.3,
            snr_estimate=3.0,
        )
        assert not gates.stem_quality_sufficient(sq_bad)

    def test_should_proceed(self):
        gates = QualityGates()

        sq = StemQuality(
            stem_type="bass",
            contamination_score=0.1,
            transient_integrity=0.8,
            harmonic_purity=0.9,
            reverb_density=0.2,
            stereo_coherence=0.95,
            snr_estimate=25.0,
        )
        report = gates.evaluate("bass", stem_quality=sq)

        assert gates.should_proceed(report)

    def test_get_quality_summary(self):
        gates = QualityGates()

        sq = StemQuality(
            stem_type="bass",
            contamination_score=0.1,
            transient_integrity=0.8,
            harmonic_purity=0.9,
            reverb_density=0.2,
            stereo_coherence=0.95,
            snr_estimate=25.0,
        )
        report = gates.evaluate("bass", stem_quality=sq)

        summary = gates.get_quality_summary(report)
        assert isinstance(summary, str)
        assert "bass" in summary

    def test_report_to_dict(self):
        gates = QualityGates()

        sq = StemQuality(
            stem_type="bass",
            contamination_score=0.1,
            transient_integrity=0.8,
            harmonic_purity=0.9,
            reverb_density=0.2,
            stereo_coherence=0.95,
            snr_estimate=25.0,
        )
        report = gates.evaluate("bass", stem_quality=sq)

        d = report.to_dict()
        assert "stem_type" in d
        assert "overall_status" in d
        assert "gate_results" in d


# ======================= Integration Tests =======================

class TestReconstructionIntegration:
    """Integration tests for the reconstruction pipeline."""

    def test_full_analysis_pipeline(self):
        """Test running all analysis components together."""
        # Create test audio
        bass = _make_bass_audio(duration=2.0)
        drums = _make_drum_audio(duration=2.0)
        stems = {"bass": bass, "drums": drums}

        # Analyze stem quality
        qualities = analyze_stem_quality(stems, SR)
        assert "bass" in qualities
        assert "drums" in qualities

        # Detect contamination
        bass_contam = detect_contamination(
            bass, SR, "bass",
            other_stems={"drums": drums}
        )
        assert isinstance(bass_contam, ContaminationAnalysis)

        # Detect artifacts
        bass_artifacts = detect_artifacts(bass, SR, "bass")
        assert isinstance(bass_artifacts, ArtifactAnalysis)

        # Build confidence map
        confidence_map = build_confidence_map(
            bass, SR, "bass",
            stem_quality=qualities["bass"],
            contamination=bass_contam,
            artifacts=bass_artifacts,
        )
        assert isinstance(confidence_map, ConfidenceMap)

        # Evaluate quality gates
        gates = get_quality_gates()
        report = gates.evaluate(
            "bass",
            stem_quality=qualities["bass"],
            contamination=bass_contam,
            artifacts=bass_artifacts,
            confidence_map=confidence_map,
        )
        assert isinstance(report, QualityReport)

    def test_quality_improves_with_clean_audio(self):
        """Test that cleaner audio gets better quality scores."""
        # Clean bass
        clean_bass = _make_bass_audio(duration=2.0)

        # Contaminated bass
        contaminated = _make_contaminated_bass(duration=2.0)

        analyzer = get_analyzer()
        clean_quality = analyzer.analyze(clean_bass, SR, "bass")
        contam_quality = analyzer.analyze(contaminated, SR, "bass")

        # Clean should have lower contamination
        assert clean_quality.contamination_score < contam_quality.contamination_score
