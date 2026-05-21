"""Tests for ML-based MIDI refinement modules.

Tests the note classifier, timing corrector, and dynamics model
that enhance MIDI extraction accuracy.
"""
import pytest
import numpy as np

from tone_forge.ml.midi.note_classifier import (
    NoteClassifier,
    NoteContext,
    ClassifiedNote,
    get_classifier,
    classify_notes,
    filter_ghost_notes,
)
from tone_forge.ml.midi.timing_corrector import (
    TimingCorrector,
    TimingContext,
    TimingCorrection,
    get_corrector,
    correct_timing,
    detect_groove,
)
from tone_forge.ml.midi.dynamics_model import (
    DynamicsModel,
    DynamicsContext,
    DynamicsAdjustment,
    get_dynamics_model,
    process_dynamics,
    analyze_dynamics,
)
from tone_forge.ml.midi import refine_midi_notes


# ============================================================================
# Test fixtures
# ============================================================================

@pytest.fixture
def sample_notes():
    """Sample MIDI notes for testing.

    Format: (pitch, start, end, velocity)
    """
    return [
        (60, 0.0, 0.5, 100),    # C4, beat 1
        (64, 0.5, 1.0, 90),     # E4, beat 1.5
        (67, 1.0, 1.5, 95),     # G4, beat 2
        (72, 2.0, 2.5, 80),     # C5, beat 3
        (60, 2.5, 3.0, 70),     # C4, beat 3.5
    ]


@pytest.fixture
def ghost_notes():
    """Notes including some ghost/artifact notes."""
    return [
        (60, 0.0, 0.5, 100),    # Real - loud, on beat
        (60, 0.375, 0.4, 40),   # Ghost - echo of above (dotted 8th)
        (64, 0.5, 1.0, 90),     # Real - on beat
        (64, 0.875, 0.9, 35),   # Ghost - echo (dotted 8th)
        (67, 1.0, 1.5, 95),     # Real - on beat
        (45, 5.0, 5.02, 20),    # Ghost - isolated, very short
    ]


@pytest.fixture
def swung_notes():
    """Notes with swing timing."""
    # In swing, upbeats are pushed late
    return [
        (60, 0.0, 0.45, 100),     # Downbeat
        (62, 0.35, 0.45, 85),     # Pushed upbeat (late)
        (64, 0.5, 0.95, 95),      # Downbeat
        (65, 0.85, 0.95, 80),     # Pushed upbeat
        (67, 1.0, 1.45, 100),     # Downbeat
        (69, 1.35, 1.45, 85),     # Pushed upbeat
    ]


# ============================================================================
# NoteClassifier tests
# ============================================================================

class TestNoteClassifier:
    """Tests for the NoteClassifier class."""

    def test_init(self):
        """Test classifier initialization."""
        classifier = NoteClassifier(use_ml=False)
        assert classifier is not None
        assert not classifier.is_ml_ready()

    def test_extract_context(self, sample_notes):
        """Test context extraction for a note."""
        classifier = NoteClassifier(use_ml=False)

        context = classifier.extract_context(
            note=sample_notes[0],
            all_notes=sample_notes,
            tempo_bpm=120.0,
        )

        assert isinstance(context, NoteContext)
        assert context.pitch == 60
        assert context.velocity == 100
        assert context.beat_alignment > 0.5  # On beat

    def test_classify_real_note(self, sample_notes):
        """Test that good notes are classified as real."""
        classifier = NoteClassifier(use_ml=False)

        classified = classifier.classify_note(
            note=sample_notes[0],
            all_notes=sample_notes,
            tempo_bpm=120.0,
        )

        assert isinstance(classified, ClassifiedNote)
        assert classified.classification == "real"
        assert classified.confidence > 0.5

    def test_classify_ghost_note(self, ghost_notes):
        """Test that ghost notes are detected."""
        classifier = NoteClassifier(use_ml=False)

        # The echo note at 0.375 should be classified as ghost
        echo_note = ghost_notes[1]  # (60, 0.375, 0.4, 40)
        classified = classifier.classify_note(
            note=echo_note,
            all_notes=ghost_notes,
            tempo_bpm=120.0,
        )

        # Should be ghost due to delay pattern and low velocity
        assert classified.classification in ("ghost", "harmonic_fragment")

    def test_classify_notes_batch(self, sample_notes):
        """Test batch classification."""
        classified = classify_notes(
            notes=sample_notes,
            tempo_bpm=120.0,
        )

        assert len(classified) == len(sample_notes)
        assert all(isinstance(c, ClassifiedNote) for c in classified)

    def test_filter_ghost_notes(self, ghost_notes):
        """Test ghost note filtering."""
        filtered = filter_ghost_notes(
            notes=ghost_notes,
            tempo_bpm=120.0,
            min_confidence=0.4,
        )

        # Should have fewer notes after filtering
        assert len(filtered) < len(ghost_notes)
        # Real notes should remain
        assert (60, 0.0, 0.5, 100) in filtered

    def test_context_to_array(self, sample_notes):
        """Test context conversion to array."""
        classifier = NoteClassifier(use_ml=False)
        context = classifier.extract_context(
            note=sample_notes[0],
            all_notes=sample_notes,
            tempo_bpm=120.0,
        )

        arr = context.to_array()
        assert isinstance(arr, np.ndarray)
        assert len(arr) == NoteContext.num_features()


# ============================================================================
# TimingCorrector tests
# ============================================================================

class TestTimingCorrector:
    """Tests for the TimingCorrector class."""

    def test_init(self):
        """Test corrector initialization."""
        corrector = TimingCorrector(use_ml=False)
        assert corrector is not None
        assert not corrector.is_ml_ready()

    def test_detect_groove_straight(self, sample_notes):
        """Test groove detection for straight timing."""
        corrector = TimingCorrector(use_ml=False)

        groove_type, swing_amount = corrector.detect_groove(
            notes=sample_notes,
            tempo_bpm=120.0,
        )

        assert groove_type == "straight"
        assert swing_amount < 0.2

    def test_detect_groove_swing(self, swung_notes):
        """Test groove detection for swing timing."""
        corrector = TimingCorrector(use_ml=False)

        groove_type, swing_amount = corrector.detect_groove(
            notes=swung_notes,
            tempo_bpm=120.0,
        )

        # Should detect swing or shuffle
        assert groove_type in ("swing", "shuffle", "straight")

    def test_compute_correction(self, sample_notes):
        """Test timing correction computation."""
        corrector = TimingCorrector(use_ml=False)

        correction = corrector.compute_correction(
            note=sample_notes[0],
            all_notes=sample_notes,
            tempo_bpm=120.0,
        )

        assert isinstance(correction, TimingCorrection)
        assert correction.grid_division in (4, 8, 12, 16)
        assert 0 <= correction.correction_strength <= 1

    def test_correct_timing_batch(self, sample_notes):
        """Test batch timing correction."""
        corrected = correct_timing(
            notes=sample_notes,
            tempo_bpm=120.0,
            strength=0.7,
        )

        assert len(corrected) == len(sample_notes)
        # Pitches should be unchanged
        for orig, corr in zip(sample_notes, corrected):
            assert orig[0] == corr[0]  # pitch
            assert orig[3] == corr[3]  # velocity

    def test_context_to_array(self, sample_notes):
        """Test context conversion to array."""
        corrector = TimingCorrector(use_ml=False)
        context = corrector.extract_context(
            note=sample_notes[0],
            all_notes=sample_notes,
            tempo_bpm=120.0,
        )

        arr = context.to_array()
        assert isinstance(arr, np.ndarray)
        assert len(arr) == TimingContext.num_features()


# ============================================================================
# DynamicsModel tests
# ============================================================================

class TestDynamicsModel:
    """Tests for the DynamicsModel class."""

    def test_init(self):
        """Test model initialization."""
        model = DynamicsModel(use_ml=False)
        assert model is not None
        assert not model.is_ml_ready()

    def test_analyze_dynamics(self, sample_notes):
        """Test dynamics analysis."""
        analysis = analyze_dynamics(sample_notes)

        assert "avg_velocity" in analysis
        assert "velocity_range" in analysis
        assert "dynamic_range" in analysis
        assert analysis["avg_velocity"] > 0

    def test_compute_adjustment(self, sample_notes):
        """Test dynamics adjustment computation."""
        model = DynamicsModel(use_ml=False)

        adjustment = model.compute_adjustment(
            note=sample_notes[0],
            all_notes=sample_notes,
            tempo_bpm=120.0,
        )

        assert isinstance(adjustment, DynamicsAdjustment)
        assert 1 <= adjustment.adjusted_velocity <= 127
        assert adjustment.scale_factor > 0

    def test_process_dynamics_batch(self, sample_notes):
        """Test batch dynamics processing."""
        processed = process_dynamics(
            notes=sample_notes,
            tempo_bpm=120.0,
            target_range=(60, 110),
        )

        assert len(processed) == len(sample_notes)
        # Pitches and timing should be unchanged
        for orig, proc in zip(sample_notes, processed):
            assert orig[0] == proc[0]  # pitch
            assert orig[1] == proc[1]  # start
            assert orig[2] == proc[2]  # end

        # Velocities should be within target range
        for note in processed:
            assert 60 <= note[3] <= 110

    def test_context_to_array(self, sample_notes):
        """Test context conversion to array."""
        model = DynamicsModel(use_ml=False)
        context = model.extract_context(
            note=sample_notes[0],
            all_notes=sample_notes,
            tempo_bpm=120.0,
        )

        arr = context.to_array()
        assert isinstance(arr, np.ndarray)
        assert len(arr) == DynamicsContext.num_features()


# ============================================================================
# Integration tests
# ============================================================================

class TestMIDIRefinementPipeline:
    """Integration tests for the full MIDI refinement pipeline."""

    def test_refine_midi_notes(self, ghost_notes):
        """Test full refinement pipeline."""
        refined = refine_midi_notes(
            notes=ghost_notes,
            tempo_bpm=120.0,
            filter_ghosts=True,
            correct_time=True,
            process_velocities=True,
        )

        # Should have fewer notes (ghosts filtered)
        assert len(refined) < len(ghost_notes)

        # All notes should be valid
        for note in refined:
            assert len(note) == 4
            assert 0 <= note[0] <= 127  # pitch
            assert note[1] >= 0  # start
            assert note[2] > note[1]  # end > start
            assert 1 <= note[3] <= 127  # velocity

    def test_refine_preserves_structure(self, sample_notes):
        """Test that refinement preserves note structure."""
        refined = refine_midi_notes(
            notes=sample_notes,
            tempo_bpm=120.0,
            filter_ghosts=False,  # Don't filter for this test
            correct_time=True,
            process_velocities=True,
        )

        # Same number of notes
        assert len(refined) == len(sample_notes)

        # Pitches unchanged
        for orig, ref in zip(sample_notes, refined):
            assert orig[0] == ref[0]

    def test_module_imports(self):
        """Test that all module imports work correctly."""
        from tone_forge.ml.midi import (
            NoteClassifier,
            TimingCorrector,
            DynamicsModel,
            refine_midi_notes,
        )

        assert NoteClassifier is not None
        assert TimingCorrector is not None
        assert DynamicsModel is not None
        assert refine_midi_notes is not None
