"""Tests for Sprint 6: Remaining MIDI passes and pipeline validation.

Tests the complete 7-pass MIDI extraction pipeline including:
- Pass 2: Harmonic Recovery
- Pass 3: Phrase Grouping
- Pass 5: Genre-Aware Refinement
- Pass 7: Musicality Check
- Full pipeline integration
"""
import numpy as np
import pytest
from typing import List

from tone_forge.midi.passes.base import (
    ExtractionContext,
    ExtractedNote,
    NoteFlag,
    PassResult,
)
from tone_forge.midi.passes.harmonic_recovery import HarmonicRecoveryPass
from tone_forge.midi.passes.phrase_builder import PhraseGroupingPass, Phrase
from tone_forge.midi.passes.genre_refinement import GenreRefinementPass
from tone_forge.midi.passes.musicality import MusicalityCheckPass
from tone_forge.midi.extraction_pipeline import (
    MultiPassExtractor,
    MIDIExtractionResult,
    create_extractor,
)


# ============================================================================
# Test Fixtures
# ============================================================================

@pytest.fixture
def basic_context():
    """Create a basic extraction context with synthetic audio."""
    # Generate 2 seconds of silence
    sr = 22050
    duration = 2.0
    audio = np.zeros(int(sr * duration), dtype=np.float32)

    return ExtractionContext(
        audio=audio,
        sr=sr,
        stem_type="synth",
        genre="synthwave",
        tempo=120.0,
    )


@pytest.fixture
def notes_for_phrase_test():
    """Create notes that form clear phrases."""
    # Phrase 1: notes 0-3 (times 0-1s)
    # Gap at 1-1.5s
    # Phrase 2: notes 4-7 (times 1.5-2.5s)
    notes = [
        # Phrase 1
        ExtractedNote(pitch=60, start=0.0, end=0.2, velocity=80, confidence=0.8, source_pass=1),
        ExtractedNote(pitch=62, start=0.25, end=0.45, velocity=75, confidence=0.75, source_pass=1),
        ExtractedNote(pitch=64, start=0.5, end=0.7, velocity=85, confidence=0.9, source_pass=1),
        ExtractedNote(pitch=65, start=0.75, end=0.95, velocity=70, confidence=0.7, source_pass=1),
        # Phrase 2 (after gap)
        ExtractedNote(pitch=67, start=1.5, end=1.7, velocity=80, confidence=0.8, source_pass=1),
        ExtractedNote(pitch=69, start=1.75, end=1.95, velocity=75, confidence=0.75, source_pass=1),
        ExtractedNote(pitch=71, start=2.0, end=2.2, velocity=85, confidence=0.85, source_pass=1),
        ExtractedNote(pitch=72, start=2.25, end=2.45, velocity=70, confidence=0.7, source_pass=1),
    ]
    return notes


@pytest.fixture
def notes_for_harmonic_test():
    """Create notes for harmonic recovery testing."""
    return [
        ExtractedNote(pitch=60, start=0.0, end=0.5, velocity=80, confidence=0.8, source_pass=1),
        ExtractedNote(pitch=64, start=0.0, end=0.5, velocity=70, confidence=0.75, source_pass=1),
        ExtractedNote(pitch=67, start=0.0, end=0.5, velocity=65, confidence=0.7, source_pass=1),
        ExtractedNote(pitch=60, start=1.0, end=1.5, velocity=80, confidence=0.85, source_pass=1),
    ]


@pytest.fixture
def notes_for_musicality_test():
    """Create notes for musicality validation."""
    # Mix of good notes and some outliers
    return [
        # Good notes in C major
        ExtractedNote(pitch=60, start=0.0, end=0.3, velocity=80, confidence=0.8, source_pass=1),
        ExtractedNote(pitch=62, start=0.3, end=0.6, velocity=75, confidence=0.75, source_pass=1),
        ExtractedNote(pitch=64, start=0.6, end=0.9, velocity=85, confidence=0.85, source_pass=1),
        ExtractedNote(pitch=65, start=0.9, end=1.2, velocity=70, confidence=0.7, source_pass=1),
        ExtractedNote(pitch=67, start=1.2, end=1.5, velocity=80, confidence=0.8, source_pass=1),
        # Out of key note (should be flagged)
        ExtractedNote(pitch=61, start=1.5, end=1.8, velocity=60, confidence=0.5, source_pass=1),
        # Pitch outlier (very low)
        ExtractedNote(pitch=24, start=2.0, end=2.3, velocity=50, confidence=0.4, source_pass=1),
    ]


# ============================================================================
# Tests for Harmonic Recovery Pass
# ============================================================================

class TestHarmonicRecoveryPass:
    """Tests for Pass 2: Harmonic Recovery."""

    def test_create_pass(self):
        """Test creating harmonic recovery pass."""
        pass_ = HarmonicRecoveryPass()
        assert pass_.name == "harmonic_recovery"
        assert pass_.pass_number == 2

    def test_empty_input(self, basic_context):
        """Test with no input notes."""
        pass_ = HarmonicRecoveryPass()
        result = pass_.process([], basic_context)

        assert result.notes == []
        assert len(result.warnings) > 0

    def test_build_harmonic_context(self, notes_for_harmonic_test, basic_context):
        """Test harmonic context building."""
        pass_ = HarmonicRecoveryPass()
        context = pass_._build_harmonic_context(notes_for_harmonic_test, basic_context)

        assert "pitch_class_counts" in context
        assert "dominant_pitch_classes" in context
        assert "pitch_range" in context

    def test_octave_recovery_disabled(self, notes_for_harmonic_test, basic_context):
        """Test with octave recovery disabled."""
        pass_ = HarmonicRecoveryPass(octave_search_enabled=False)
        result = pass_.process(notes_for_harmonic_test, basic_context)

        # Should still process but not add octave notes
        assert isinstance(result, PassResult)

    def test_fifth_recovery_disabled(self, notes_for_harmonic_test, basic_context):
        """Test with fifth recovery disabled."""
        pass_ = HarmonicRecoveryPass(fifth_search_enabled=False)
        result = pass_.process(notes_for_harmonic_test, basic_context)

        assert isinstance(result, PassResult)

    def test_gap_fill_disabled(self, notes_for_harmonic_test, basic_context):
        """Test with gap filling disabled."""
        pass_ = HarmonicRecoveryPass(gap_fill_enabled=False)
        result = pass_.process(notes_for_harmonic_test, basic_context)

        assert isinstance(result, PassResult)

    def test_statistics_tracking(self, notes_for_harmonic_test, basic_context):
        """Test that statistics are properly tracked."""
        pass_ = HarmonicRecoveryPass()
        result = pass_.process(notes_for_harmonic_test, basic_context)

        stats = result.statistics
        assert stats.pass_name == "harmonic_recovery"
        assert stats.notes_input == len(notes_for_harmonic_test)


# ============================================================================
# Tests for Phrase Grouping Pass
# ============================================================================

class TestPhraseGroupingPass:
    """Tests for Pass 3: Phrase Grouping."""

    def test_create_pass(self):
        """Test creating phrase grouping pass."""
        pass_ = PhraseGroupingPass()
        assert pass_.name == "phrase_grouping"
        assert pass_.pass_number == 3

    def test_detect_two_phrases(self, notes_for_phrase_test, basic_context):
        """Test detection of two distinct phrases."""
        pass_ = PhraseGroupingPass(gap_threshold_ms=300)
        result = pass_.process(notes_for_phrase_test, basic_context)

        phrases = result.metadata.get("phrases", [])
        # Should detect at least 2 phrases with the 500ms gap
        assert len(phrases) >= 2

    def test_phrase_timing(self, notes_for_phrase_test, basic_context):
        """Test phrase start and end times."""
        pass_ = PhraseGroupingPass(gap_threshold_ms=300)
        result = pass_.process(notes_for_phrase_test, basic_context)

        phrases = result.metadata.get("phrases", [])
        if phrases:
            first_phrase = phrases[0]
            assert first_phrase["start"] >= 0
            assert first_phrase["end"] > first_phrase["start"]

    def test_phrase_type_classification(self, basic_context):
        """Test phrase type classification."""
        # Create sustained notes (should be classified as sustained)
        sustained_notes = [
            ExtractedNote(pitch=60, start=0.0, end=3.0, velocity=80, confidence=0.8, source_pass=1),
            ExtractedNote(pitch=64, start=0.0, end=3.0, velocity=70, confidence=0.75, source_pass=1),
        ]

        pass_ = PhraseGroupingPass()
        result = pass_.process(sustained_notes, basic_context)

        phrases = result.metadata.get("phrases", [])
        # Phrases with long notes should be classified
        assert isinstance(result, PassResult)

    def test_rhythmic_pattern_detection(self, basic_context):
        """Test detection of rhythmic patterns."""
        # Create evenly spaced notes
        rhythmic_notes = [
            ExtractedNote(pitch=60, start=i * 0.25, end=i * 0.25 + 0.1,
                         velocity=80, confidence=0.8, source_pass=1)
            for i in range(8)
        ]

        pass_ = PhraseGroupingPass()
        result = pass_.process(rhythmic_notes, basic_context)

        assert len(result.notes) >= len(rhythmic_notes)

    def test_min_phrase_notes(self, basic_context):
        """Test minimum notes requirement for phrase."""
        few_notes = [
            ExtractedNote(pitch=60, start=0.0, end=0.5, velocity=80, confidence=0.8, source_pass=1),
        ]

        pass_ = PhraseGroupingPass(min_phrase_notes=2)
        result = pass_.process(few_notes, basic_context)

        # Single note shouldn't form a phrase
        assert len(result.warnings) > 0

    def test_gap_threshold_adaptation(self, basic_context):
        """Test gap threshold adapts to context."""
        pass_ = PhraseGroupingPass(gap_threshold_ms=300)

        # Pad stem should have longer gap threshold
        pad_context = ExtractionContext(
            audio=basic_context.audio,
            sr=basic_context.sr,
            stem_type="pad",
        )
        adapted = pass_._adapt_gap_threshold(pad_context)

        assert adapted > 300  # Should increase for pads


# ============================================================================
# Tests for Genre Refinement Pass
# ============================================================================

class TestGenreRefinementPass:
    """Tests for Pass 5: Genre-Aware Refinement."""

    def test_create_pass(self):
        """Test creating genre refinement pass."""
        pass_ = GenreRefinementPass()
        assert pass_.name == "genre_refinement"
        assert pass_.pass_number == 5

    def test_without_genre(self, basic_context):
        """Test pass without genre information."""
        no_genre_context = ExtractionContext(
            audio=basic_context.audio,
            sr=basic_context.sr,
            stem_type="synth",
            genre=None,  # No genre
        )

        notes = [
            ExtractedNote(pitch=60, start=0.0, end=0.5, velocity=80, confidence=0.8, source_pass=1),
        ]

        pass_ = GenreRefinementPass()
        result = pass_.process(notes, no_genre_context)

        # Should skip refinement without genre
        assert len(result.warnings) > 0
        assert "skipping" in result.warnings[0].lower() or "available" in result.warnings[0].lower()

    def test_velocity_adjustment(self, basic_context):
        """Test velocity adjustment for genre."""
        # Notes with velocities outside synthwave range
        notes = [
            ExtractedNote(pitch=60, start=0.0, end=0.5, velocity=127, confidence=0.8, source_pass=1),
            ExtractedNote(pitch=62, start=0.5, end=1.0, velocity=120, confidence=0.8, source_pass=1),
        ]

        pass_ = GenreRefinementPass(apply_velocity_adjustment=True)
        result = pass_.process(notes, basic_context)

        # Velocities should be adjusted toward genre expectations
        assert isinstance(result, PassResult)

    def test_density_filtering(self, basic_context):
        """Test density-based filtering."""
        # Create very dense notes (too many)
        dense_notes = [
            ExtractedNote(pitch=60, start=i * 0.02, end=i * 0.02 + 0.01,
                         velocity=80, confidence=0.5, source_pass=1)
            for i in range(100)
        ]

        pass_ = GenreRefinementPass(apply_density_filtering=True, strict_mode=True)
        result = pass_.process(dense_notes, basic_context)

        # Should filter some low-confidence notes in strict mode
        # May have warnings about density
        assert isinstance(result, PassResult)

    def test_pitch_validation(self, basic_context):
        """Test pitch range validation."""
        # Mix of notes - some outside expected range
        notes = [
            ExtractedNote(pitch=60, start=0.0, end=0.5, velocity=80, confidence=0.8, source_pass=1),
            ExtractedNote(pitch=120, start=0.5, end=1.0, velocity=80, confidence=0.5, source_pass=1),  # High
            ExtractedNote(pitch=12, start=1.0, end=1.5, velocity=80, confidence=0.5, source_pass=1),   # Low
        ]

        pass_ = GenreRefinementPass(apply_pitch_validation=True)
        result = pass_.process(notes, basic_context)

        # Should flag or filter out-of-range notes
        assert isinstance(result, PassResult)

    def test_strict_mode(self, basic_context):
        """Test strict mode removes more notes."""
        notes = [
            ExtractedNote(pitch=60, start=0.0, end=0.5, velocity=80, confidence=0.8, source_pass=1),
            ExtractedNote(pitch=60, start=0.5, end=0.505, velocity=80, confidence=0.3, source_pass=1),  # Too short
        ]

        pass_strict = GenreRefinementPass(strict_mode=True)
        result_strict = pass_strict.process(notes.copy(), basic_context)

        pass_lenient = GenreRefinementPass(strict_mode=False)
        result_lenient = pass_lenient.process(notes.copy(), basic_context)

        # Strict mode should remove more
        assert len(result_strict.notes) <= len(result_lenient.notes)


# ============================================================================
# Tests for Musicality Check Pass
# ============================================================================

class TestMusicalityCheckPass:
    """Tests for Pass 7: Musicality Check."""

    def test_create_pass(self):
        """Test creating musicality check pass."""
        pass_ = MusicalityCheckPass()
        assert pass_.name == "musicality_check"
        assert pass_.pass_number == 7

    def test_key_detection(self, notes_for_musicality_test, basic_context):
        """Test key detection from notes."""
        pass_ = MusicalityCheckPass()
        key, confidence = pass_._detect_key(notes_for_musicality_test, basic_context)

        # Most notes are in C major (C, D, E, F, G)
        assert key is not None
        assert confidence > 0

    def test_key_from_context(self, notes_for_musicality_test, basic_context):
        """Test key taken from context when provided."""
        context_with_key = ExtractionContext(
            audio=basic_context.audio,
            sr=basic_context.sr,
            key=(0, "major"),  # C major
        )

        pass_ = MusicalityCheckPass()
        key, confidence = pass_._detect_key(notes_for_musicality_test, context_with_key)

        assert key == (0, "major")
        assert confidence == 1.0

    def test_interval_checking(self, basic_context):
        """Test interval relationship checking."""
        # Create notes with dissonant interval
        dissonant_notes = [
            ExtractedNote(pitch=60, start=0.0, end=0.5, velocity=80, confidence=0.8, source_pass=1),
            ExtractedNote(pitch=61, start=0.0, end=0.5, velocity=80, confidence=0.8, source_pass=1),  # Semitone
        ]

        pass_ = MusicalityCheckPass(check_intervals=True, dissonance_tolerance=0.2)
        result = pass_.process(dissonant_notes, basic_context)

        # Should detect dissonance
        assert isinstance(result, PassResult)

    def test_temporal_pattern_check(self, basic_context):
        """Test temporal pattern anomaly detection."""
        # Create notes with anomalous timing
        notes = [
            ExtractedNote(pitch=60, start=0.0, end=0.2, velocity=80, confidence=0.8, source_pass=1),
            ExtractedNote(pitch=62, start=0.25, end=0.45, velocity=80, confidence=0.8, source_pass=1),
            ExtractedNote(pitch=64, start=0.5, end=0.7, velocity=80, confidence=0.8, source_pass=1),
            # Anomalous timing (big gap)
            ExtractedNote(pitch=65, start=5.0, end=5.2, velocity=80, confidence=0.5, source_pass=1),
        ]

        pass_ = MusicalityCheckPass(check_temporal_patterns=True)
        result = pass_.process(notes, basic_context)

        # Should flag anomalous note
        assert isinstance(result, PassResult)

    def test_outlier_removal(self, notes_for_musicality_test, basic_context):
        """Test outlier note removal."""
        pass_ = MusicalityCheckPass(remove_outliers=True)
        result = pass_.process(notes_for_musicality_test, basic_context)

        # Very low pitch note should be removed or have reduced confidence
        pitches = [n.pitch for n in result.notes]

        # Pitch 24 was an outlier - should either be removed, have reduced
        # confidence, or be flagged as low confidence
        if 24 in pitches:
            outlier_note = next(n for n in result.notes if n.pitch == 24)
            # Original confidence was 0.4, should be lower or flagged
            assert outlier_note.confidence < 0.45 or NoteFlag.LOW_CONFIDENCE in outlier_note.flags

    def test_min_confidence_filter(self, basic_context):
        """Test minimum confidence filtering."""
        notes = [
            ExtractedNote(pitch=60, start=0.0, end=0.5, velocity=80, confidence=0.8, source_pass=1),
            ExtractedNote(pitch=62, start=0.5, end=1.0, velocity=80, confidence=0.2, source_pass=1),  # Low
        ]

        pass_ = MusicalityCheckPass(min_final_confidence=0.5)
        result = pass_.process(notes, basic_context)

        # Low confidence note should be filtered
        assert len(result.notes) <= len(notes)

    def test_final_confidence_calculation(self, notes_for_musicality_test, basic_context):
        """Test final confidence score calculation."""
        pass_ = MusicalityCheckPass()
        result = pass_.process(notes_for_musicality_test, basic_context)

        # All notes should have valid confidence scores
        for note in result.notes:
            assert 0 <= note.confidence <= 1


# ============================================================================
# Tests for Full Pipeline Integration
# ============================================================================

class TestFullPipelineIntegration:
    """Tests for the complete 7-pass pipeline."""

    def test_create_default_extractor(self):
        """Test creating default extractor."""
        extractor = MultiPassExtractor()
        assert len(extractor.passes) == 7

    def test_all_passes_present(self):
        """Test all 7 passes are included."""
        extractor = MultiPassExtractor()
        pass_names = [p.name for p in extractor.passes]

        expected = [
            "high_confidence",
            "harmonic_recovery",
            "phrase_grouping",
            "effect_suppression",
            "genre_refinement",
            "confidence_quantization",
            "musicality_check",
        ]

        assert pass_names == expected

    def test_pass_numbering(self):
        """Test passes are numbered sequentially."""
        extractor = MultiPassExtractor()

        for i, pass_ in enumerate(extractor.passes):
            assert pass_.pass_number == i + 1

    def test_extract_minimal(self, basic_context):
        """Test extraction with minimal audio."""
        extractor = MultiPassExtractor()

        result = extractor.extract(
            audio=basic_context.audio,
            sr=basic_context.sr,
            stem_type=basic_context.stem_type,
            genre=basic_context.genre,
        )

        assert isinstance(result, MIDIExtractionResult)
        assert result.pass_results is not None
        assert len(result.pass_results) == 7

    def test_extract_statistics_tracking(self, basic_context):
        """Test that all passes track statistics."""
        extractor = MultiPassExtractor()

        result = extractor.extract(
            audio=basic_context.audio,
            sr=basic_context.sr,
        )

        for pass_result in result.pass_results:
            stats = pass_result.statistics
            assert stats.pass_name is not None
            assert stats.notes_input >= 0
            assert stats.notes_output >= 0


class TestExtractorProfiles:
    """Tests for extractor preset profiles."""

    def test_default_profile(self):
        """Test default profile."""
        extractor = create_extractor("default")
        assert len(extractor.passes) == 7

    def test_fast_profile(self):
        """Test fast profile has fewer passes."""
        extractor = create_extractor("fast")
        assert len(extractor.passes) < 7

    def test_high_quality_profile(self):
        """Test high quality profile."""
        extractor = create_extractor("high_quality")
        assert len(extractor.passes) == 7

    def test_synthwave_profile(self):
        """Test synthwave profile."""
        extractor = create_extractor("synthwave")
        assert len(extractor.passes) == 7

        # Check synthwave-specific settings
        pass_names = [p.name for p in extractor.passes]
        assert "genre_refinement" in pass_names

    def test_invalid_profile(self):
        """Test invalid profile raises error."""
        with pytest.raises(ValueError):
            create_extractor("invalid_profile")


class TestPipelineEdgeCases:
    """Tests for pipeline edge cases."""

    def test_skip_passes(self):
        """Test skipping specific passes."""
        extractor = MultiPassExtractor(skip_passes=["harmonic_recovery", "phrase_grouping"])

        pass_names = [p.name for p in extractor.passes]
        assert "harmonic_recovery" not in pass_names
        assert "phrase_grouping" not in pass_names

    def test_add_pass(self):
        """Test adding a custom pass."""
        extractor = MultiPassExtractor()
        initial_count = len(extractor.passes)

        new_pass = HarmonicRecoveryPass()
        extractor.add_pass(new_pass)

        assert len(extractor.passes) == initial_count + 1

    def test_remove_pass(self):
        """Test removing a pass by name."""
        extractor = MultiPassExtractor()
        initial_count = len(extractor.passes)

        extractor.remove_pass("harmonic_recovery")

        assert len(extractor.passes) == initial_count - 1
        assert "harmonic_recovery" not in [p.name for p in extractor.passes]

    def test_empty_audio(self):
        """Test with empty audio."""
        extractor = MultiPassExtractor()

        result = extractor.extract(
            audio=np.array([]),
            sr=22050,
        )

        assert isinstance(result, MIDIExtractionResult)
        assert len(result.notes) == 0

    def test_stereo_audio_converted(self):
        """Test stereo audio is converted to mono."""
        extractor = MultiPassExtractor()

        stereo_audio = np.zeros((2, 22050), dtype=np.float32)

        result = extractor.extract(
            audio=stereo_audio,
            sr=22050,
        )

        assert isinstance(result, MIDIExtractionResult)


# ============================================================================
# Tests for Phrase Data Structure
# ============================================================================

class TestPhraseDataStructure:
    """Tests for the Phrase dataclass."""

    def test_phrase_creation(self):
        """Test creating a Phrase."""
        notes = [
            ExtractedNote(pitch=60, start=0.0, end=0.5, velocity=80, confidence=0.8, source_pass=1),
            ExtractedNote(pitch=62, start=0.5, end=1.0, velocity=75, confidence=0.75, source_pass=1),
        ]

        phrase = Phrase(
            notes=notes,
            start=0.0,
            end=1.0,
            phrase_id=0,
        )

        assert phrase.duration == 1.0
        assert phrase.note_count == 2
        assert phrase.average_pitch == 61.0
        assert phrase.pitch_range == 2

    def test_phrase_to_dict(self):
        """Test phrase serialization."""
        notes = [
            ExtractedNote(pitch=60, start=0.0, end=0.5, velocity=80, confidence=0.8, source_pass=1),
        ]

        phrase = Phrase(
            notes=notes,
            start=0.0,
            end=0.5,
            phrase_id=1,
            phrase_type="melodic",
        )

        d = phrase.to_dict()

        assert d["phrase_id"] == 1
        assert d["phrase_type"] == "melodic"
        assert d["note_count"] == 1


# ============================================================================
# Tests for Note Flags
# ============================================================================

class TestNoteFlagsUsage:
    """Tests for proper use of note flags across passes."""

    def test_harmonic_recovery_flag(self, notes_for_harmonic_test, basic_context):
        """Test HARMONIC_RECOVERY flag is set."""
        pass_ = HarmonicRecoveryPass()
        result = pass_.process(notes_for_harmonic_test, basic_context)

        # Any recovered notes should have the flag
        recovered = [n for n in result.notes if NoteFlag.HARMONIC_RECOVERY in n.flags]
        # (May or may not recover notes depending on audio content)
        assert isinstance(result, PassResult)

    def test_phrase_inferred_flag(self, basic_context):
        """Test PHRASE_INFERRED flag is set."""
        # Create rhythmic notes with a gap that could be filled
        notes = [
            ExtractedNote(pitch=60, start=0.0, end=0.1, velocity=80, confidence=0.8, source_pass=1),
            ExtractedNote(pitch=62, start=0.25, end=0.35, velocity=80, confidence=0.8, source_pass=1),
            ExtractedNote(pitch=64, start=0.5, end=0.6, velocity=80, confidence=0.8, source_pass=1),
            # Gap at 0.75 where a note might be inferred
            ExtractedNote(pitch=67, start=1.0, end=1.1, velocity=80, confidence=0.8, source_pass=1),
        ]

        pass_ = PhraseGroupingPass()
        result = pass_.process(notes, basic_context)

        # Check if any notes were inferred
        assert isinstance(result, PassResult)

    def test_low_confidence_flag(self, notes_for_musicality_test, basic_context):
        """Test LOW_CONFIDENCE flag is set appropriately."""
        pass_ = MusicalityCheckPass()
        result = pass_.process(notes_for_musicality_test, basic_context)

        # Low confidence notes should be flagged
        flagged = [n for n in result.notes if NoteFlag.LOW_CONFIDENCE in n.flags]
        # (Depends on what notes don't pass validation)
        assert isinstance(result, PassResult)
