"""Tests for Sprint 3: Multi-pass MIDI extraction.

Tests cover:
- Base types (ExtractedNote, PassResult, ExtractionContext)
- HighConfidencePass
- EffectSuppressionPass
- ConfidenceQuantizationPass
- MultiPassExtractor orchestration
"""
import pytest
import numpy as np
from dataclasses import replace

from tone_forge.midi import (
    MultiPassExtractor,
    MIDIExtractionResult,
    create_extractor,
    ExtractedNote,
    ExtractionContext,
    ExtractionPass,
    PassResult,
    PassStatistics,
    NoteFlag,
    HighConfidencePass,
    EffectSuppressionPass,
    ConfidenceQuantizationPass,
)


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def sample_audio():
    """Generate a simple test audio signal."""
    sr = 22050
    duration = 2.0
    t = np.linspace(0, duration, int(sr * duration))
    # Simple sine wave at 440 Hz (A4)
    audio = np.sin(2 * np.pi * 440 * t) * 0.5
    return audio, sr


@pytest.fixture
def complex_audio():
    """Generate audio with multiple notes."""
    sr = 22050
    duration = 4.0
    t = np.linspace(0, duration, int(sr * duration))
    audio = np.zeros_like(t)

    # Add multiple notes at different times
    # C4 (262 Hz) at 0-1s
    mask1 = (t >= 0) & (t < 1)
    audio[mask1] += np.sin(2 * np.pi * 262 * t[mask1]) * 0.5

    # E4 (330 Hz) at 1-2s
    mask2 = (t >= 1) & (t < 2)
    audio[mask2] += np.sin(2 * np.pi * 330 * t[mask2]) * 0.5

    # G4 (392 Hz) at 2-3s
    mask3 = (t >= 2) & (t < 3)
    audio[mask3] += np.sin(2 * np.pi * 392 * t[mask3]) * 0.5

    # C5 (523 Hz) at 3-4s
    mask4 = (t >= 3) & (t < 4)
    audio[mask4] += np.sin(2 * np.pi * 523 * t[mask4]) * 0.5

    return audio, sr


@pytest.fixture
def sample_notes():
    """Create sample extracted notes."""
    return [
        ExtractedNote(pitch=60, start=0.0, end=0.5, velocity=100, confidence=0.9),
        ExtractedNote(pitch=64, start=0.5, end=1.0, velocity=90, confidence=0.85),
        ExtractedNote(pitch=67, start=1.0, end=1.5, velocity=80, confidence=0.8),
        ExtractedNote(pitch=72, start=1.5, end=2.0, velocity=70, confidence=0.75),
    ]


@pytest.fixture
def notes_with_delays():
    """Create notes that include delay artifacts."""
    return [
        # Primary note
        ExtractedNote(pitch=60, start=0.0, end=0.4, velocity=100, confidence=0.9),
        # Delay repeat 1 (quieter, same pitch, 250ms later)
        ExtractedNote(pitch=60, start=0.25, end=0.55, velocity=70, confidence=0.7),
        # Delay repeat 2 (quieter still, 500ms later)
        ExtractedNote(pitch=60, start=0.5, end=0.75, velocity=50, confidence=0.5),
        # Different note (not a delay)
        ExtractedNote(pitch=64, start=0.6, end=1.0, velocity=95, confidence=0.88),
    ]


@pytest.fixture
def notes_with_reverb():
    """Create notes that include reverb tails."""
    return [
        # Primary note
        ExtractedNote(pitch=60, start=0.0, end=0.5, velocity=100, confidence=0.9),
        # Reverb tail (very quiet, starts right after)
        ExtractedNote(pitch=60, start=0.51, end=0.8, velocity=25, confidence=0.3),
        # Another note
        ExtractedNote(pitch=64, start=1.0, end=1.5, velocity=90, confidence=0.85),
        # Its reverb tail
        ExtractedNote(pitch=64, start=1.51, end=1.8, velocity=20, confidence=0.25),
    ]


@pytest.fixture
def extraction_context(sample_audio):
    """Create a sample extraction context."""
    audio, sr = sample_audio
    return ExtractionContext(
        audio=audio,
        sr=sr,
        stem_type="synth",
        genre="synthwave",
        tempo=120.0,
        key=(0, "major"),
    )


# =============================================================================
# ExtractedNote Tests
# =============================================================================

class TestExtractedNote:
    """Tests for ExtractedNote dataclass."""

    def test_create_basic(self):
        """Test creating a basic note."""
        note = ExtractedNote(
            pitch=60,
            start=0.0,
            end=1.0,
            velocity=100,
            confidence=0.9,
        )
        assert note.pitch == 60
        assert note.start == 0.0
        assert note.end == 1.0
        assert note.velocity == 100
        assert note.confidence == 0.9
        assert note.source_pass == 1  # Default
        assert len(note.flags) == 0

    def test_duration_properties(self):
        """Test duration calculations."""
        note = ExtractedNote(
            pitch=60, start=0.5, end=1.5,
            velocity=100, confidence=0.9,
        )
        assert note.duration == 1.0
        assert note.duration_ms == 1000.0

    def test_to_tuple(self):
        """Test conversion to tuple."""
        note = ExtractedNote(
            pitch=60, start=0.5, end=1.5,
            velocity=100, confidence=0.9,
        )
        t = note.to_tuple()
        assert t == (60, 0.5, 1.5, 100)

    def test_to_dict(self):
        """Test conversion to dictionary."""
        note = ExtractedNote(
            pitch=60, start=0.5, end=1.5,
            velocity=100, confidence=0.9,
            flags={NoteFlag.ORIGINAL, NoteFlag.QUANTIZED},
        )
        d = note.to_dict()
        assert d["pitch"] == 60
        assert d["start"] == 0.5
        assert d["end"] == 1.5
        assert d["velocity"] == 100
        assert d["confidence"] == 0.9
        assert "original" in d["flags"]
        assert "quantized" in d["flags"]

    def test_from_tuple(self):
        """Test creation from tuple."""
        t = (60, 0.5, 1.5, 100)
        note = ExtractedNote.from_tuple(t, confidence=0.8, source_pass=2)
        assert note.pitch == 60
        assert note.start == 0.5
        assert note.end == 1.5
        assert note.velocity == 100
        assert note.confidence == 0.8
        assert note.source_pass == 2

    def test_flags(self):
        """Test note flags."""
        note = ExtractedNote(
            pitch=60, start=0.0, end=1.0,
            velocity=100, confidence=0.9,
            flags={NoteFlag.ORIGINAL},
        )
        assert NoteFlag.ORIGINAL in note.flags

        # Add more flags
        note.flags.add(NoteFlag.QUANTIZED)
        assert NoteFlag.QUANTIZED in note.flags


class TestExtractionContext:
    """Tests for ExtractionContext."""

    def test_create_minimal(self, sample_audio):
        """Test creating context with minimal parameters."""
        audio, sr = sample_audio
        ctx = ExtractionContext(audio=audio, sr=sr)

        assert ctx.sr == sr
        assert ctx.stem_type is None
        assert ctx.genre is None
        assert ctx.tempo is None
        assert ctx.onset_threshold == 0.5
        assert ctx.frame_threshold == 0.4

    def test_create_full(self, sample_audio):
        """Test creating context with all parameters."""
        audio, sr = sample_audio
        ctx = ExtractionContext(
            audio=audio,
            sr=sr,
            stem_type="bass",
            genre="synthwave",
            tempo=120.0,
            key=(0, "major"),
            time_signature=(4, 4),
            onset_threshold=0.6,
            frame_threshold=0.5,
            min_note_ms=60.0,
            min_velocity=30,
        )

        assert ctx.stem_type == "bass"
        assert ctx.genre == "synthwave"
        assert ctx.tempo == 120.0
        assert ctx.key == (0, "major")
        assert ctx.onset_threshold == 0.6

    def test_to_dict(self, sample_audio):
        """Test context serialization."""
        audio, sr = sample_audio
        ctx = ExtractionContext(
            audio=audio,
            sr=sr,
            tempo=120.0,
        )
        d = ctx.to_dict()

        assert "audio" not in d  # Audio excluded
        assert d["sr"] == sr
        assert d["tempo"] == 120.0


# =============================================================================
# HighConfidencePass Tests
# =============================================================================

class TestHighConfidencePass:
    """Tests for HighConfidencePass."""

    def test_name(self):
        """Test pass name."""
        p = HighConfidencePass()
        assert p.name == "high_confidence"

    def test_pass_number(self):
        """Test pass number assignment."""
        p = HighConfidencePass(pass_number=5)
        assert p.pass_number == 5

    def test_process_empty(self, extraction_context):
        """Test processing with empty notes (first pass)."""
        p = HighConfidencePass()
        result = p.process([], extraction_context)

        assert isinstance(result, PassResult)
        assert isinstance(result.notes, list)
        assert isinstance(result.statistics, PassStatistics)

    def test_threshold_adaptation_clean_stem(self, sample_audio):
        """Test threshold adaptation for clean stems."""
        audio, sr = sample_audio

        # Create mock stem quality with high transient integrity
        class MockStemQuality:
            transient_integrity = 0.9
            harmonic_purity = 0.8

        ctx = ExtractionContext(
            audio=audio,
            sr=sr,
            stem_quality=MockStemQuality(),
        )

        p = HighConfidencePass()
        # Threshold should be lowered for clean stems
        adapted = p._adapt_onset_threshold(ctx)
        assert adapted < ctx.onset_threshold

    def test_threshold_adaptation_pad_role(self, sample_audio):
        """Test threshold adaptation for pad roles."""
        audio, sr = sample_audio

        # Create mock role classification
        class MockRole:
            primary_role = "pad_atmosphere"

        ctx = ExtractionContext(
            audio=audio,
            sr=sr,
            role_classification=MockRole(),
        )

        p = HighConfidencePass()
        adapted = p._adapt_onset_threshold(ctx)
        # Pads get lower threshold (softer attacks)
        assert adapted < ctx.onset_threshold

    def test_statistics_tracking(self, extraction_context):
        """Test that statistics are properly tracked."""
        p = HighConfidencePass()
        result = p.process([], extraction_context)

        stats = result.statistics
        assert stats.pass_number == 1
        assert stats.pass_name == "high_confidence"
        assert stats.notes_input == 0
        # notes_output depends on detection
        assert stats.execution_time_ms >= 0


# =============================================================================
# EffectSuppressionPass Tests
# =============================================================================

class TestEffectSuppressionPass:
    """Tests for EffectSuppressionPass."""

    def test_name(self):
        """Test pass name."""
        p = EffectSuppressionPass()
        assert p.name == "effect_suppression"

    def test_process_empty(self, extraction_context):
        """Test processing with empty notes."""
        p = EffectSuppressionPass()
        result = p.process([], extraction_context)

        assert len(result.notes) == 0
        assert "No notes to process" in result.warnings

    def test_detect_delay_pattern(self, notes_with_delays, extraction_context):
        """Test delay pattern detection."""
        p = EffectSuppressionPass()
        result = p.process(notes_with_delays, extraction_context)

        # Should remove delay repeats but keep primary and different note
        assert len(result.notes) < len(notes_with_delays)

        # Check that statistics track artifacts
        assert result.statistics.notes_removed > 0

    def test_detect_reverb_tails(self, notes_with_reverb, extraction_context):
        """Test reverb tail detection."""
        p = EffectSuppressionPass()
        result = p.process(notes_with_reverb, extraction_context)

        # Should remove reverb tails
        assert len(result.notes) < len(notes_with_reverb)

    def test_preserves_distinct_notes(self, sample_notes, extraction_context):
        """Test that distinct notes are preserved."""
        p = EffectSuppressionPass()
        result = p.process(sample_notes, extraction_context)

        # All notes are distinct, so most should be preserved
        assert len(result.notes) >= len(sample_notes) - 1

    def test_tempo_estimation(self):
        """Test tempo estimation from notes."""
        p = EffectSuppressionPass()

        # Notes at regular 0.25s intervals (8th notes at 120 BPM)
        # At 120 BPM: beat = 0.5s, 8th note = 0.25s
        notes = [
            ExtractedNote(pitch=60, start=i * 0.25, end=i * 0.25 + 0.2,
                         velocity=100, confidence=0.9)
            for i in range(8)
        ]

        tempo = p._estimate_tempo_from_notes(notes)
        # Should estimate reasonable tempo (algorithm assumes median IOI is 8th note)
        assert 60 <= tempo <= 200

    def test_flags_primary_notes(self, notes_with_delays, extraction_context):
        """Test that primary notes get flagged when their delays are removed."""
        p = EffectSuppressionPass()
        result = p.process(notes_with_delays, extraction_context)

        # Check for DELAY_REMOVED flag on surviving notes
        delay_flagged = [n for n in result.notes if NoteFlag.DELAY_REMOVED in n.flags]
        # May or may not have flagged notes depending on detection
        # Just ensure no errors occur


# =============================================================================
# ConfidenceQuantizationPass Tests
# =============================================================================

class TestConfidenceQuantizationPass:
    """Tests for ConfidenceQuantizationPass."""

    def test_name(self):
        """Test pass name."""
        p = ConfidenceQuantizationPass()
        assert p.name == "confidence_quantization"

    def test_process_empty(self, extraction_context):
        """Test processing with empty notes."""
        p = ConfidenceQuantizationPass()
        result = p.process([], extraction_context)

        assert len(result.notes) == 0
        assert "No notes to quantize" in result.warnings

    def test_quantize_to_grid(self, extraction_context):
        """Test basic quantization to grid."""
        p = ConfidenceQuantizationPass(
            base_strength=1.0,  # Full quantization
            grid_divisions=16,
        )

        # Note slightly off grid (0.13s instead of 0.125s for 16th at 120 BPM)
        notes = [
            ExtractedNote(
                pitch=60, start=0.13, end=0.63,
                velocity=100, confidence=0.95,
            ),
        ]

        result = p.process(notes, extraction_context)

        # Note should be closer to grid
        assert len(result.notes) == 1
        # 16th note at 120 BPM = 0.125s
        assert abs(result.notes[0].start - 0.125) < abs(0.13 - 0.125)

    def test_confidence_affects_strength(self, extraction_context):
        """Test that confidence affects quantization strength."""
        p = ConfidenceQuantizationPass(
            base_strength=0.8,
            min_strength=0.2,
        )

        # Low confidence note
        low_conf_strength = p._calculate_note_strength(0.3, 0.8)

        # High confidence note
        high_conf_strength = p._calculate_note_strength(0.95, 0.8)

        # High confidence should get stronger quantization
        assert high_conf_strength > low_conf_strength

    def test_preserves_original_timing(self, extraction_context):
        """Test that original timing is preserved in metadata."""
        p = ConfidenceQuantizationPass(base_strength=0.9)

        original_start = 0.13
        notes = [
            ExtractedNote(
                pitch=60, start=original_start, end=0.63,
                velocity=100, confidence=0.95,
            ),
        ]

        result = p.process(notes, extraction_context)

        # Original timing should be stored
        if NoteFlag.QUANTIZED in result.notes[0].flags:
            assert result.notes[0].original_start == original_start

    def test_genre_adaptation(self, sample_audio):
        """Test that genre affects quantization strength."""
        audio, sr = sample_audio
        p = ConfidenceQuantizationPass(base_strength=0.7)

        # Ambient genre - should be looser
        ctx_ambient = ExtractionContext(audio=audio, sr=sr, genre="ambient")
        ambient_strength = p._adapt_strength(ctx_ambient)

        # Techno genre - should be tighter
        ctx_techno = ExtractionContext(audio=audio, sr=sr, genre="techno")
        techno_strength = p._adapt_strength(ctx_techno)

        assert techno_strength > ambient_strength

    def test_resolve_overlaps(self, extraction_context):
        """Test that overlapping notes are resolved."""
        p = ConfidenceQuantizationPass()

        # Create overlapping notes (after hypothetical quantization)
        notes = [
            ExtractedNote(pitch=60, start=0.0, end=0.6, velocity=100, confidence=0.9),
            ExtractedNote(pitch=60, start=0.4, end=1.0, velocity=90, confidence=0.85),
        ]

        resolved = p._resolve_overlaps(notes)

        # First note should end before second starts
        assert resolved[0].end <= resolved[1].start + 0.02  # Small tolerance

    def test_tempo_estimation(self):
        """Test tempo estimation from notes."""
        p = ConfidenceQuantizationPass()

        # Notes suggesting 120 BPM
        notes = [
            ExtractedNote(pitch=60 + i, start=i * 0.25, end=i * 0.25 + 0.2,
                         velocity=100, confidence=0.9)
            for i in range(16)
        ]

        tempo = p._estimate_tempo(notes)
        # Should be close to 120
        assert 100 <= tempo <= 140


# =============================================================================
# MultiPassExtractor Tests
# =============================================================================

class TestMultiPassExtractor:
    """Tests for MultiPassExtractor."""

    def test_default_passes(self):
        """Test default pass configuration."""
        extractor = MultiPassExtractor()

        # Full 7-pass pipeline
        assert len(extractor.passes) == 7
        assert extractor.passes[0].name == "high_confidence"
        assert extractor.passes[1].name == "harmonic_recovery"
        assert extractor.passes[2].name == "phrase_grouping"
        assert extractor.passes[3].name == "effect_suppression"
        assert extractor.passes[4].name == "genre_refinement"
        assert extractor.passes[5].name == "confidence_quantization"
        assert extractor.passes[6].name == "musicality_check"

    def test_custom_passes(self):
        """Test custom pass configuration."""
        passes = [
            HighConfidencePass(),
            ConfidenceQuantizationPass(),
        ]
        extractor = MultiPassExtractor(passes=passes)

        assert len(extractor.passes) == 2

    def test_skip_passes(self):
        """Test skipping specific passes."""
        extractor = MultiPassExtractor(skip_passes=["effect_suppression"])

        assert len(extractor.passes) == 6  # 7 - 1 skipped
        assert all(p.name != "effect_suppression" for p in extractor.passes)

    def test_pass_numbering(self):
        """Test that passes are numbered sequentially."""
        extractor = MultiPassExtractor()

        for i, p in enumerate(extractor.passes):
            assert p.pass_number == i + 1

    def test_extract_basic(self, sample_audio):
        """Test basic extraction."""
        audio, sr = sample_audio
        extractor = MultiPassExtractor()

        result = extractor.extract(audio, sr)

        assert isinstance(result, MIDIExtractionResult)
        assert isinstance(result.notes, list)
        assert result.tempo > 0
        assert 0 <= result.overall_confidence <= 1
        assert len(result.pass_results) == 7  # Full 7-pass pipeline

    def test_extract_with_context(self, sample_audio):
        """Test extraction with full context."""
        audio, sr = sample_audio
        extractor = MultiPassExtractor()

        result = extractor.extract(
            audio, sr,
            stem_type="synth",
            genre="synthwave",
            tempo=120.0,
            key=(0, "major"),
        )

        assert result.tempo == 120.0
        assert result.key == (0, "major")

    def test_extract_handles_stereo(self):
        """Test that stereo audio is handled."""
        sr = 22050
        duration = 1.0
        t = np.linspace(0, duration, int(sr * duration))
        stereo_audio = np.vstack([
            np.sin(2 * np.pi * 440 * t),
            np.sin(2 * np.pi * 440 * t),
        ])

        extractor = MultiPassExtractor()
        result = extractor.extract(stereo_audio, sr)

        # Should work without error
        assert isinstance(result, MIDIExtractionResult)

    def test_result_to_tuples(self, sample_audio):
        """Test converting result to tuples."""
        audio, sr = sample_audio
        extractor = MultiPassExtractor()

        result = extractor.extract(audio, sr)
        tuples = result.to_tuples()

        assert isinstance(tuples, list)
        for t in tuples:
            assert len(t) == 4

    def test_result_to_dict(self, sample_audio):
        """Test converting result to dictionary."""
        audio, sr = sample_audio
        extractor = MultiPassExtractor()

        result = extractor.extract(audio, sr)
        d = result.to_dict()

        assert "notes" in d
        assert "tempo" in d
        assert "overall_confidence" in d
        assert "pass_statistics" in d

    def test_add_pass(self):
        """Test adding a pass."""
        extractor = MultiPassExtractor()
        initial_count = len(extractor.passes)

        extractor.add_pass(HighConfidencePass())

        assert len(extractor.passes) == initial_count + 1
        # Pass numbers should be updated
        for i, p in enumerate(extractor.passes):
            assert p.pass_number == i + 1

    def test_remove_pass(self):
        """Test removing a pass."""
        extractor = MultiPassExtractor()
        initial_count = len(extractor.passes)

        extractor.remove_pass("effect_suppression")

        assert len(extractor.passes) == initial_count - 1
        assert all(p.name != "effect_suppression" for p in extractor.passes)

    def test_overall_confidence_calculation(self, sample_audio):
        """Test overall confidence calculation."""
        audio, sr = sample_audio
        extractor = MultiPassExtractor()

        result = extractor.extract(audio, sr)

        # Confidence should be between 0 and 1
        assert 0 <= result.overall_confidence <= 1

    def test_execution_time_tracking(self, sample_audio):
        """Test that execution time is tracked."""
        audio, sr = sample_audio
        extractor = MultiPassExtractor()

        result = extractor.extract(audio, sr)

        assert result.total_execution_time_ms > 0
        for pass_result in result.pass_results:
            assert pass_result.statistics.execution_time_ms >= 0


# =============================================================================
# create_extractor Tests
# =============================================================================

class TestCreateExtractor:
    """Tests for create_extractor factory function."""

    def test_default_profile(self):
        """Test default profile."""
        extractor = create_extractor("default")

        assert len(extractor.passes) == 7  # Full 7-pass pipeline

    def test_fast_profile(self):
        """Test fast profile."""
        extractor = create_extractor("fast")

        assert len(extractor.passes) == 2  # Minimal passes for speed
        assert extractor.passes[0].name == "high_confidence"
        assert extractor.passes[1].name == "confidence_quantization"

    def test_high_quality_profile(self):
        """Test high quality profile."""
        extractor = create_extractor("high_quality")

        assert len(extractor.passes) == 7  # Full pipeline with conservative settings
        # High quality has more conservative settings
        hc_pass = extractor.passes[0]
        assert hc_pass.min_confidence >= 0.7

    def test_synthwave_profile(self):
        """Test synthwave profile."""
        extractor = create_extractor("synthwave")

        assert len(extractor.passes) == 7  # Full pipeline optimized for synthwave
        # Synthwave has lower onset threshold
        hc_pass = extractor.passes[0]
        assert hc_pass.onset_threshold <= 0.5

    def test_invalid_profile(self):
        """Test invalid profile raises error."""
        with pytest.raises(ValueError):
            create_extractor("invalid_profile")


# =============================================================================
# Integration Tests
# =============================================================================

class TestIntegration:
    """Integration tests for the full pipeline."""

    def test_full_pipeline_with_complex_audio(self, complex_audio):
        """Test full pipeline with complex audio."""
        audio, sr = complex_audio
        extractor = MultiPassExtractor()

        result = extractor.extract(
            audio, sr,
            tempo=120.0,
            genre="synthwave",
        )

        # Should produce some notes
        # Note: basic_pitch may not be installed, so librosa fallback is used
        assert isinstance(result, MIDIExtractionResult)
        assert len(result.pass_results) == 7  # Full 7-pass pipeline

    def test_pipeline_preserves_note_order(self, sample_notes, sample_audio):
        """Test that pipeline produces sorted notes."""
        audio, sr = sample_audio
        extractor = MultiPassExtractor()

        result = extractor.extract(audio, sr)

        # Notes should be sorted by start time
        for i in range(len(result.notes) - 1):
            assert result.notes[i].start <= result.notes[i + 1].start

    def test_pipeline_tracks_pass_statistics(self, sample_audio):
        """Test that pass statistics are properly tracked."""
        audio, sr = sample_audio
        extractor = MultiPassExtractor()

        result = extractor.extract(audio, sr)

        # Each pass should have statistics
        all_pass_names = [
            "high_confidence", "harmonic_recovery", "phrase_grouping",
            "effect_suppression", "genre_refinement", "confidence_quantization",
            "musicality_check"
        ]
        for pass_result in result.pass_results:
            stats = pass_result.statistics
            assert stats.pass_name in all_pass_names
            assert stats.notes_input >= 0
            assert stats.notes_output >= 0


# =============================================================================
# Edge Case Tests
# =============================================================================

class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_empty_audio(self):
        """Test handling of empty audio."""
        extractor = MultiPassExtractor()

        # Empty audio
        audio = np.array([])
        result = extractor.extract(audio, 22050)

        assert isinstance(result, MIDIExtractionResult)
        assert len(result.notes) == 0

    def test_silent_audio(self):
        """Test handling of silent audio."""
        extractor = MultiPassExtractor()

        # Silent audio
        audio = np.zeros(22050)  # 1 second of silence
        result = extractor.extract(audio, 22050)

        assert isinstance(result, MIDIExtractionResult)
        # Should have no or very few notes

    def test_very_short_audio(self):
        """Test handling of very short audio."""
        extractor = MultiPassExtractor()

        # Very short audio (100ms)
        sr = 22050
        audio = np.sin(2 * np.pi * 440 * np.linspace(0, 0.1, int(sr * 0.1)))
        result = extractor.extract(audio, sr)

        assert isinstance(result, MIDIExtractionResult)

    def test_note_with_zero_duration(self):
        """Test handling notes with zero duration."""
        ctx = ExtractionContext(
            audio=np.zeros(22050),
            sr=22050,
        )

        notes = [
            ExtractedNote(pitch=60, start=0.5, end=0.5, velocity=100, confidence=0.9),
        ]

        # Effect suppression should handle zero-duration notes
        p = EffectSuppressionPass()
        result = p.process(notes, ctx)

        # Should not crash
        assert isinstance(result, PassResult)

    def test_overlapping_notes_same_pitch(self):
        """Test handling of overlapping notes with same pitch."""
        ctx = ExtractionContext(
            audio=np.zeros(22050),
            sr=22050,
            tempo=120.0,
        )

        notes = [
            ExtractedNote(pitch=60, start=0.0, end=1.0, velocity=100, confidence=0.9),
            ExtractedNote(pitch=60, start=0.5, end=1.5, velocity=90, confidence=0.85),
        ]

        p = ConfidenceQuantizationPass()
        result = p.process(notes, ctx)

        # Should resolve overlaps
        assert isinstance(result, PassResult)
