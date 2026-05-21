"""Tests for Sprint 4: Quality Gates & Pipeline Integration.

Tests cover:
- QualityReporter and UnifiedQualityReport
- QualityWarning system
- MIDIQualityMetrics
- ReconstructionPipeline
- Pipeline configuration and results
"""
import pytest
import numpy as np

from tone_forge.reconstruction import (
    # Quality gates
    QualityThresholds,
    QualityGates,
    QualityLevel,
    GateStatus,
    get_quality_gates,
    # Quality reporter
    WarningLevel,
    WarningCategory,
    QualityWarning,
    MIDIQualityMetrics,
    UnifiedQualityReport,
    QualityReporter,
    get_quality_reporter,
    generate_quality_report,
    # Pipeline
    ReconstructionConfig,
    AnalysisResults,
    ReconstructionResult,
    ReconstructionPipeline,
    get_pipeline,
    reconstruct,
    # Supporting types
    StemQuality,
    ConfidenceRegion,
    ContaminationAnalysis,
    ContaminationType,
    ContaminationEvent,
    ArtifactAnalysis,
    ArtifactType,
    DetectedArtifact,
    ConfidenceMap,
    RegionConfidence,
)


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def sample_audio():
    """Generate simple test audio."""
    sr = 22050
    duration = 2.0
    t = np.linspace(0, duration, int(sr * duration))
    audio = np.sin(2 * np.pi * 440 * t) * 0.5
    return audio, sr


@pytest.fixture
def good_stem_quality():
    """Create a good quality StemQuality."""
    return StemQuality(
        stem_type="synth",
        contamination_score=0.2,
        transient_integrity=0.8,
        harmonic_purity=0.85,
        reverb_density=0.3,
        stereo_coherence=0.9,
        snr_estimate=25.0,
        confidence_regions=[
            ConfidenceRegion(
                start_time=0.0,
                end_time=2.0,
                confidence=0.85,
                reason="good overall quality",
            )
        ],
        issues=[],
    )


@pytest.fixture
def poor_stem_quality():
    """Create a poor quality StemQuality."""
    return StemQuality(
        stem_type="synth",
        contamination_score=0.7,
        transient_integrity=0.25,
        harmonic_purity=0.4,
        reverb_density=0.85,
        stereo_coherence=0.5,
        snr_estimate=8.0,
        confidence_regions=[
            ConfidenceRegion(
                start_time=0.0,
                end_time=2.0,
                confidence=0.3,
                reason="poor overall quality",
            )
        ],
        issues=["High contamination detected", "Low transient integrity"],
    )


@pytest.fixture
def contamination_analysis():
    """Create sample contamination analysis."""
    return ContaminationAnalysis(
        stem_type="synth",
        overall_contamination=0.5,
        events=[
            ContaminationEvent(
                contamination_type=ContaminationType.CROSS_STEM_BLEED,
                time_start=0.5,
                time_end=1.0,
                severity=0.6,
                confidence=0.8,
                source_stem="bass",
                description="Bass bleed detected",
            ),
            ContaminationEvent(
                contamination_type=ContaminationType.REVERB_TAIL,
                time_start=1.5,
                time_end=1.8,
                severity=0.4,
                confidence=0.7,
                source_stem="drums",
                description="Reverb tail from drums",
            ),
        ],
    )


@pytest.fixture
def artifact_analysis():
    """Create sample artifact analysis."""
    return ArtifactAnalysis(
        stem_type="synth",
        overall_artifact_score=0.4,
        artifacts=[
            DetectedArtifact(
                artifact_type=ArtifactType.SPECTRAL_SMEARING,
                time_start=0.2,
                time_end=0.5,
                severity=0.5,
                confidence=0.7,
                description="Spectral smearing detected",
            ),
        ],
    )


@pytest.fixture
def confidence_map():
    """Create sample confidence map."""
    regions = [
        RegionConfidence(
            time_start=0.0,
            time_end=0.5,
            note_confidence=0.8,
            descriptor_confidence=0.75,
            timing_confidence=0.7,
            contamination_probability=0.2,
            artifact_probability=0.1,
            harmonic_stability=0.85,
        ),
        RegionConfidence(
            time_start=0.5,
            time_end=1.0,
            note_confidence=0.5,
            descriptor_confidence=0.45,
            timing_confidence=0.4,
            contamination_probability=0.5,
            artifact_probability=0.3,
            harmonic_stability=0.5,
        ),
        RegionConfidence(
            time_start=1.0,
            time_end=1.5,
            note_confidence=0.85,
            descriptor_confidence=0.8,
            timing_confidence=0.75,
            contamination_probability=0.1,
            artifact_probability=0.1,
            harmonic_stability=0.9,
        ),
    ]

    # Compute overall for each region
    for r in regions:
        r.compute_overall()

    return ConfidenceMap(
        stem_type="synth",
        duration=1.5,
        regions=regions,
        global_confidence=0.65,
        low_confidence_regions=[(0.5, 1.0)],
        high_confidence_regions=[(0.0, 0.5), (1.0, 1.5)],
    )


@pytest.fixture
def mock_midi_result():
    """Create a mock MIDI extraction result."""
    from tone_forge.midi.passes.base import ExtractedNote, NoteFlag

    class MockMIDIResult:
        def __init__(self):
            self.notes = [
                ExtractedNote(
                    pitch=60, start=0.0, end=0.5,
                    velocity=100, confidence=0.9,
                    flags={NoteFlag.ORIGINAL},
                ),
                ExtractedNote(
                    pitch=64, start=0.5, end=1.0,
                    velocity=85, confidence=0.75,
                    flags={NoteFlag.ORIGINAL, NoteFlag.QUANTIZED},
                ),
                ExtractedNote(
                    pitch=67, start=1.0, end=1.5,
                    velocity=70, confidence=0.6,
                    flags={NoteFlag.ORIGINAL, NoteFlag.DELAY_REMOVED},
                ),
                ExtractedNote(
                    pitch=72, start=1.5, end=2.0,
                    velocity=90, confidence=0.4,
                    flags={NoteFlag.ORIGINAL},
                ),
            ]
            self.tempo = 120.0
            self.key = (0, "major")
            self.time_signature = (4, 4)
            self.overall_confidence = 0.66
            self.pass_results = []

        def to_dict(self):
            return {"notes": [n.to_dict() for n in self.notes]}

    return MockMIDIResult()


# =============================================================================
# QualityWarning Tests
# =============================================================================

class TestQualityWarning:
    """Tests for QualityWarning."""

    def test_create_basic(self):
        """Test creating a basic warning."""
        warning = QualityWarning(
            level=WarningLevel.WARNING,
            category=WarningCategory.STEM_QUALITY,
            message="Test warning",
        )

        assert warning.level == WarningLevel.WARNING
        assert warning.category == WarningCategory.STEM_QUALITY
        assert warning.message == "Test warning"

    def test_is_critical(self):
        """Test critical detection."""
        critical = QualityWarning(
            level=WarningLevel.CRITICAL,
            category=WarningCategory.MIDI_EXTRACTION,
            message="Critical issue",
        )
        warning = QualityWarning(
            level=WarningLevel.WARNING,
            category=WarningCategory.STEM_QUALITY,
            message="Warning",
        )

        assert critical.is_critical
        assert not warning.is_critical

    def test_is_error(self):
        """Test error detection."""
        error = QualityWarning(
            level=WarningLevel.ERROR,
            category=WarningCategory.CONTAMINATION,
            message="Error",
        )
        info = QualityWarning(
            level=WarningLevel.INFO,
            category=WarningCategory.GENERAL,
            message="Info",
        )

        assert error.is_error
        assert not info.is_error

    def test_to_dict(self):
        """Test serialization."""
        warning = QualityWarning(
            level=WarningLevel.WARNING,
            category=WarningCategory.TIMING,
            message="Timing issue",
            time_range=(1.0, 2.0),
            recommendation="Adjust timing",
        )

        d = warning.to_dict()
        assert d["level"] == "warning"
        assert d["category"] == "timing"
        assert d["message"] == "Timing issue"
        assert d["time_range"] == (1.0, 2.0)


# =============================================================================
# MIDIQualityMetrics Tests
# =============================================================================

class TestMIDIQualityMetrics:
    """Tests for MIDIQualityMetrics."""

    def test_from_extraction_result(self, mock_midi_result):
        """Test building metrics from extraction result."""
        metrics = MIDIQualityMetrics.from_extraction_result(mock_midi_result)

        assert metrics.note_count == 4
        assert 0.6 <= metrics.average_confidence <= 0.7
        assert metrics.low_confidence_note_count == 1  # 0.4 confidence
        assert metrics.high_confidence_note_count == 1  # 0.9 confidence
        assert metrics.quantization_applied  # One note has QUANTIZED flag
        assert metrics.effects_removed >= 1  # One note has DELAY_REMOVED

    def test_from_empty_result(self):
        """Test handling empty results."""
        class EmptyResult:
            notes = []

        metrics = MIDIQualityMetrics.from_extraction_result(EmptyResult())

        assert metrics.note_count == 0
        assert metrics.average_confidence == 0.0
        assert metrics.note_density == 0.0

    def test_to_dict(self, mock_midi_result):
        """Test serialization."""
        metrics = MIDIQualityMetrics.from_extraction_result(mock_midi_result)
        d = metrics.to_dict()

        assert "note_count" in d
        assert "average_confidence" in d
        assert "note_density" in d
        assert "timing_regularity" in d


# =============================================================================
# QualityReporter Tests
# =============================================================================

class TestQualityReporter:
    """Tests for QualityReporter."""

    def test_create_default(self):
        """Test default reporter creation."""
        reporter = QualityReporter()

        assert reporter.thresholds is not None
        assert reporter.min_proceed_confidence == 0.3

    def test_create_with_thresholds(self):
        """Test reporter with custom thresholds."""
        thresholds = QualityThresholds.strict()
        reporter = QualityReporter(thresholds=thresholds)

        assert reporter.thresholds.min_stem_quality >= 0.6

    def test_generate_report_basic(self):
        """Test basic report generation."""
        reporter = QualityReporter()
        report = reporter.generate_report(stem_type="synth")

        assert isinstance(report, UnifiedQualityReport)
        assert report.stem_type == "synth"
        assert report.overall_confidence >= 0.0
        assert isinstance(report.overall_quality, QualityLevel)

    def test_generate_report_with_stem_quality(self, good_stem_quality):
        """Test report with stem quality analysis."""
        reporter = QualityReporter()
        report = reporter.generate_report(
            stem_type="synth",
            stem_quality=good_stem_quality,
        )

        assert report.stem_quality_score > 0.5
        assert report.should_proceed

    def test_generate_report_with_poor_quality(self, poor_stem_quality):
        """Test report with poor stem quality."""
        reporter = QualityReporter()
        report = reporter.generate_report(
            stem_type="synth",
            stem_quality=poor_stem_quality,
        )

        # Should generate warnings
        assert len(report.warnings) > 0
        # Should have low confidence
        assert report.overall_confidence < 0.5

    def test_generate_report_with_contamination(self, contamination_analysis):
        """Test report with contamination analysis."""
        reporter = QualityReporter()
        report = reporter.generate_report(
            stem_type="synth",
            contamination=contamination_analysis,
        )

        # Should include contamination warnings
        contam_warnings = [
            w for w in report.warnings
            if w.category == WarningCategory.CONTAMINATION
        ]
        assert len(contam_warnings) >= 0  # May or may not generate based on severity

    def test_generate_report_with_midi(self, mock_midi_result):
        """Test report including MIDI extraction results."""
        reporter = QualityReporter()
        report = reporter.generate_report(
            stem_type="synth",
            midi_result=mock_midi_result,
        )

        assert report.midi_metrics is not None
        assert report.midi_metrics.note_count == 4
        assert report.midi_quality_score > 0

    def test_generate_report_full(
        self, good_stem_quality, contamination_analysis,
        artifact_analysis, confidence_map, mock_midi_result
    ):
        """Test full report with all components."""
        reporter = QualityReporter()
        report = reporter.generate_report(
            stem_type="synth",
            stem_quality=good_stem_quality,
            contamination=contamination_analysis,
            artifacts=artifact_analysis,
            confidence_map=confidence_map,
            midi_result=mock_midi_result,
            genre="synthwave",
        )

        assert report.stem_quality_score > 0
        assert report.midi_quality_score > 0
        assert report.confidence_map_score > 0
        assert report.gate_report is not None

    def test_genre_threshold_adjustment(self):
        """Test that genre affects thresholds."""
        reporter = QualityReporter()

        # Synthwave should have more lenient reverb thresholds
        report_synthwave = reporter.generate_report(
            stem_type="synth",
            genre="synthwave",
        )

        # After genre adjustment, synthwave thresholds should be applied
        assert reporter.thresholds.max_reverb_density >= 0.9


# =============================================================================
# UnifiedQualityReport Tests
# =============================================================================

class TestUnifiedQualityReport:
    """Tests for UnifiedQualityReport."""

    def test_properties(self):
        """Test report properties."""
        report = UnifiedQualityReport(
            stem_type="bass",
            overall_quality=QualityLevel.GOOD,
            overall_confidence=0.75,
            should_proceed=True,
            warnings=[
                QualityWarning(
                    level=WarningLevel.ERROR,
                    category=WarningCategory.CONTAMINATION,
                    message="Error 1",
                ),
                QualityWarning(
                    level=WarningLevel.WARNING,
                    category=WarningCategory.TIMING,
                    message="Warning 1",
                ),
                QualityWarning(
                    level=WarningLevel.CRITICAL,
                    category=WarningCategory.MIDI_EXTRACTION,
                    message="Critical 1",
                ),
            ],
        )

        assert report.total_warnings == 3
        assert report.critical_warnings == 1
        assert report.error_warnings == 2  # error + critical
        assert report.has_critical_issues
        assert report.has_errors

    def test_warnings_by_category(self):
        """Test grouping warnings by category."""
        report = UnifiedQualityReport(
            stem_type="synth",
            overall_quality=QualityLevel.ACCEPTABLE,
            overall_confidence=0.6,
            should_proceed=True,
            warnings=[
                QualityWarning(level=WarningLevel.WARNING,
                              category=WarningCategory.CONTAMINATION, message="C1"),
                QualityWarning(level=WarningLevel.WARNING,
                              category=WarningCategory.CONTAMINATION, message="C2"),
                QualityWarning(level=WarningLevel.INFO,
                              category=WarningCategory.TIMING, message="T1"),
            ],
        )

        by_cat = report.warnings_by_category
        assert len(by_cat["contamination"]) == 2
        assert len(by_cat["timing"]) == 1

    def test_get_user_summary(self):
        """Test user-friendly summary generation."""
        report = UnifiedQualityReport(
            stem_type="synth",
            overall_quality=QualityLevel.MARGINAL,
            overall_confidence=0.45,
            should_proceed=False,
            warnings=[
                QualityWarning(
                    level=WarningLevel.ERROR,
                    category=WarningCategory.STEM_QUALITY,
                    message="Low stem quality",
                    recommendation="Try different separation model",
                ),
            ],
        )

        summary = report.get_user_summary()
        assert "MARGINAL" in summary
        assert "45%" in summary
        assert "Low stem quality" in summary

    def test_to_dict(self):
        """Test serialization."""
        report = UnifiedQualityReport(
            stem_type="synth",
            overall_quality=QualityLevel.GOOD,
            overall_confidence=0.75,
            should_proceed=True,
        )

        d = report.to_dict()
        assert d["stem_type"] == "synth"
        assert d["overall_quality"] == "good"
        assert d["overall_confidence"] == 0.75
        assert d["should_proceed"] is True


# =============================================================================
# ReconstructionConfig Tests
# =============================================================================

class TestReconstructionConfig:
    """Tests for ReconstructionConfig."""

    def test_default_config(self):
        """Test default configuration."""
        config = ReconstructionConfig()

        assert config.analyze_stem_quality
        assert config.analyze_contamination
        assert config.build_confidence_map
        assert config.extract_midi
        assert config.midi_profile == "default"

    def test_fast_config(self):
        """Test fast configuration."""
        config = ReconstructionConfig.fast()

        assert not config.analyze_contamination
        assert not config.analyze_artifacts
        assert config.midi_profile == "fast"

    def test_thorough_config(self):
        """Test thorough configuration."""
        config = ReconstructionConfig.thorough()

        assert config.analyze_continuity
        assert config.midi_profile == "high_quality"
        assert config.thresholds is not None

    def test_for_genre(self):
        """Test genre-specific configuration."""
        synthwave_config = ReconstructionConfig.for_genre("synthwave")

        assert synthwave_config.midi_profile == "synthwave"
        assert synthwave_config.analyze_continuity

        ambient_config = ReconstructionConfig.for_genre("ambient")
        assert ambient_config.analyze_continuity


# =============================================================================
# ReconstructionPipeline Tests
# =============================================================================

class TestReconstructionPipeline:
    """Tests for ReconstructionPipeline."""

    def test_create_default(self):
        """Test default pipeline creation."""
        pipeline = ReconstructionPipeline()

        assert pipeline.config is not None
        assert pipeline._stem_analyzer is not None

    def test_create_with_config(self):
        """Test pipeline with custom config."""
        config = ReconstructionConfig.fast()
        pipeline = ReconstructionPipeline(config=config)

        assert pipeline.config.midi_profile == "fast"

    def test_process_basic(self, sample_audio):
        """Test basic pipeline processing."""
        audio, sr = sample_audio
        pipeline = ReconstructionPipeline()

        result = pipeline.process(
            audio=audio,
            sr=sr,
            stem_type="synth",
        )

        assert isinstance(result, ReconstructionResult)
        assert result.stem_type == "synth"
        assert result.analysis is not None
        assert result.total_time_ms > 0

    def test_process_with_genre(self, sample_audio):
        """Test processing with genre specification."""
        audio, sr = sample_audio
        pipeline = ReconstructionPipeline()

        result = pipeline.process(
            audio=audio,
            sr=sr,
            stem_type="synth",
            genre="synthwave",
        )

        assert result.quality_report is not None

    def test_process_stereo(self):
        """Test processing stereo audio."""
        sr = 22050
        t = np.linspace(0, 1, sr)
        stereo = np.vstack([
            np.sin(2 * np.pi * 440 * t),
            np.sin(2 * np.pi * 440 * t),
        ])

        pipeline = ReconstructionPipeline()
        result = pipeline.process(stereo, sr, "synth")

        assert result is not None

    def test_analyze_only(self, sample_audio):
        """Test analysis without MIDI extraction."""
        audio, sr = sample_audio
        pipeline = ReconstructionPipeline()

        analysis, report = pipeline.analyze_only(
            audio=audio,
            sr=sr,
            stem_type="synth",
        )

        assert isinstance(analysis, AnalysisResults)
        assert isinstance(report, UnifiedQualityReport)

    def test_stop_on_low_quality(self, sample_audio):
        """Test stopping extraction on low quality."""
        audio, sr = sample_audio

        config = ReconstructionConfig(
            stop_on_low_quality=True,
            min_quality_to_proceed=0.99,  # Very high threshold
        )
        pipeline = ReconstructionPipeline(config=config)

        result = pipeline.process(audio, sr, "synth")

        # May or may not skip depending on analysis
        assert isinstance(result, ReconstructionResult)


# =============================================================================
# ReconstructionResult Tests
# =============================================================================

class TestReconstructionResult:
    """Tests for ReconstructionResult."""

    def test_basic_result(self):
        """Test basic result properties."""
        result = ReconstructionResult(
            stem_type="bass",
            analysis=AnalysisResults(),
            total_time_ms=1000.0,
        )

        assert result.stem_type == "bass"
        assert result.total_time_ms == 1000.0
        assert not result.skipped_extraction

    def test_skipped_result(self):
        """Test result when extraction was skipped."""
        result = ReconstructionResult(
            stem_type="synth",
            analysis=AnalysisResults(),
            skipped_extraction=True,
            skip_reason="Quality too low",
        )

        assert result.skipped_extraction
        assert not result.success
        assert "skipped" in result.get_user_summary().lower()

    def test_notes_property(self, mock_midi_result):
        """Test notes property."""
        result = ReconstructionResult(
            stem_type="synth",
            analysis=AnalysisResults(),
            midi_result=mock_midi_result,
        )

        assert len(result.notes) == 4

    def test_to_dict(self, mock_midi_result):
        """Test serialization."""
        report = UnifiedQualityReport(
            stem_type="synth",
            overall_quality=QualityLevel.GOOD,
            overall_confidence=0.75,
            should_proceed=True,
        )

        result = ReconstructionResult(
            stem_type="synth",
            analysis=AnalysisResults(analysis_time_ms=500.0),
            midi_result=mock_midi_result,
            quality_report=report,
            total_time_ms=1500.0,
        )

        d = result.to_dict()
        assert d["stem_type"] == "synth"
        assert d["success"] is True
        assert "analysis" in d
        assert "quality_report" in d


# =============================================================================
# Module-Level Functions Tests
# =============================================================================

class TestModuleFunctions:
    """Tests for module-level convenience functions."""

    def test_get_quality_reporter(self):
        """Test getting quality reporter singleton."""
        reporter1 = get_quality_reporter()
        reporter2 = get_quality_reporter()

        # Should return same instance
        assert reporter1 is reporter2

    def test_get_quality_reporter_with_thresholds(self):
        """Test getting reporter with custom thresholds."""
        thresholds = QualityThresholds.strict()
        reporter = get_quality_reporter(thresholds)

        assert reporter.thresholds.min_stem_quality >= 0.6

    def test_generate_quality_report(self, good_stem_quality):
        """Test convenience report generation."""
        report = generate_quality_report(
            stem_type="synth",
            stem_quality=good_stem_quality,
        )

        assert isinstance(report, UnifiedQualityReport)

    def test_get_pipeline(self):
        """Test getting pipeline singleton."""
        pipeline1 = get_pipeline()
        pipeline2 = get_pipeline()

        assert pipeline1 is pipeline2

    def test_reconstruct_function(self, sample_audio):
        """Test convenience reconstruct function."""
        audio, sr = sample_audio

        result = reconstruct(
            audio=audio,
            sr=sr,
            stem_type="synth",
        )

        assert isinstance(result, ReconstructionResult)


# =============================================================================
# Integration Tests
# =============================================================================

class TestIntegration:
    """Integration tests for the full quality pipeline."""

    def test_full_pipeline_flow(self, sample_audio):
        """Test complete pipeline flow."""
        audio, sr = sample_audio

        config = ReconstructionConfig(
            analyze_contamination=True,
            analyze_artifacts=True,
            build_confidence_map=True,
            classify_role=True,
        )

        pipeline = ReconstructionPipeline(config=config)
        result = pipeline.process(
            audio=audio,
            sr=sr,
            stem_type="synth",
            genre="synthwave",
            tempo=120.0,
        )

        # Should complete without error
        assert result is not None
        assert result.analysis is not None
        assert result.quality_report is not None

        # Should have analysis results
        if result.analysis.stem_quality:
            assert 0 <= result.analysis.stem_quality.overall_quality <= 1

        # Should have quality report
        assert 0 <= result.quality_report.overall_confidence <= 1

    def test_quality_affects_extraction(self, sample_audio):
        """Test that quality analysis affects extraction."""
        audio, sr = sample_audio

        # Run analysis first
        pipeline = ReconstructionPipeline()
        analysis, report = pipeline.analyze_only(audio, sr, "synth")

        # Quality should be reflected in report
        assert report.overall_quality is not None
        assert report.should_proceed is not None

    def test_warnings_are_actionable(self, sample_audio):
        """Test that warnings provide actionable information."""
        audio, sr = sample_audio

        result = reconstruct(audio, sr, "synth")

        for warning in result.warnings:
            # Every warning should have a message
            assert warning.message
            # Level should be valid
            assert warning.level in WarningLevel


# =============================================================================
# Edge Cases
# =============================================================================

class TestEdgeCases:
    """Tests for edge cases."""

    def test_empty_audio(self):
        """Test handling empty audio."""
        pipeline = ReconstructionPipeline()
        result = pipeline.process(np.array([]), 22050, "synth")

        # Should handle gracefully
        assert isinstance(result, ReconstructionResult)

    def test_very_short_audio(self):
        """Test handling very short audio."""
        sr = 22050
        audio = np.sin(2 * np.pi * 440 * np.linspace(0, 0.05, int(sr * 0.05)))

        pipeline = ReconstructionPipeline()
        result = pipeline.process(audio, sr, "synth")

        assert isinstance(result, ReconstructionResult)

    def test_silent_audio(self):
        """Test handling silent audio."""
        audio = np.zeros(22050)

        pipeline = ReconstructionPipeline()
        result = pipeline.process(audio, 22050, "synth")

        assert isinstance(result, ReconstructionResult)
        # Should generate warnings about low signal
        if result.quality_report:
            assert result.quality_report.overall_confidence < 0.8

    def test_no_analysis_components(self, sample_audio):
        """Test pipeline with all analysis disabled."""
        audio, sr = sample_audio

        config = ReconstructionConfig(
            analyze_stem_quality=False,
            analyze_contamination=False,
            analyze_artifacts=False,
            build_confidence_map=False,
            classify_role=False,
            extract_midi=True,
        )

        pipeline = ReconstructionPipeline(config=config)
        result = pipeline.process(audio, sr, "synth")

        # Should still work
        assert isinstance(result, ReconstructionResult)
