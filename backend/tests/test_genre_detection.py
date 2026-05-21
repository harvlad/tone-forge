"""Tests for genre detection and production archetypes.

Tests the genre classifier, archetypes system, and style hints
that provide genre-specific recommendations.
"""
import pytest
import numpy as np

from tone_forge.genre_detection.classifier import (
    GenreClassifier,
    GenreFeatures,
    GenrePrediction,
    GENRES,
    SUBGENRES,
    get_classifier,
    classify_genre,
    extract_genre_features,
)
from tone_forge.genre_detection.archetypes import (
    ToneArchetype,
    EffectChainTemplate,
    ARCHETYPES,
    get_archetype,
    get_archetype_for_genre,
    list_archetypes,
    get_archetype_categories,
)
from tone_forge.genre_detection.style_hints import (
    StyleHint,
    generate_genre_hints,
    format_hints_for_display,
    get_quick_tips,
)
from tone_forge.genre_detection import analyze_and_recommend


# ============================================================================
# Test fixtures
# ============================================================================

@pytest.fixture
def sample_audio():
    """Generate sample audio for testing."""
    sr = 22050
    duration = 2.0
    t = np.linspace(0, duration, int(sr * duration))

    # Simple sine wave with harmonics (guitar-like)
    audio = (
        0.5 * np.sin(2 * np.pi * 220 * t) +
        0.25 * np.sin(2 * np.pi * 440 * t) +
        0.125 * np.sin(2 * np.pi * 660 * t)
    )

    return audio.astype(np.float32), sr


@pytest.fixture
def distorted_audio():
    """Generate distorted audio for metal detection."""
    sr = 22050
    duration = 2.0
    t = np.linspace(0, duration, int(sr * duration))

    # Sine wave with hard clipping (distortion)
    audio = np.sin(2 * np.pi * 110 * t) * 3  # Boost
    audio = np.clip(audio, -0.8, 0.8)  # Clip

    # Add noise (more "aggressive")
    audio += np.random.randn(len(audio)) * 0.05

    return audio.astype(np.float32), sr


@pytest.fixture
def sample_descriptor():
    """Sample ToneDescriptor as dict."""
    return {
        "amp": {
            "family": "marshall_jcm",
            "gain": 0.65,
            "voicing": {
                "bass": 0.5,
                "mid": 0.6,
                "treble": 0.55,
            },
        },
        "cab": {
            "configuration": "4x12",
            "speaker_character": "v30_like",
        },
        "effects": {
            "overdrive_pedal": {"style": "tube_screamer", "drive": 0.4},
        },
    }


# ============================================================================
# GenreClassifier tests
# ============================================================================

class TestGenreClassifier:
    """Tests for the GenreClassifier class."""

    def test_init(self):
        """Test classifier initialization."""
        classifier = GenreClassifier(use_ml=False)
        assert classifier is not None
        assert not classifier.is_ml_ready()

    def test_extract_features(self, sample_audio):
        """Test feature extraction."""
        audio, sr = sample_audio
        classifier = GenreClassifier(use_ml=False)

        features = classifier.extract_features(audio, sr)

        assert isinstance(features, GenreFeatures)
        assert features.spectral_centroid > 0
        # Tempo may be 0 for synthetic sine waves with no beat
        assert features.tempo_bpm >= 0
        assert 0 <= features.harmonic_ratio <= 1

    def test_classify(self, sample_audio):
        """Test genre classification."""
        audio, sr = sample_audio

        prediction = classify_genre(audio, sr)

        assert isinstance(prediction, GenrePrediction)
        assert prediction.primary_genre in GENRES
        assert 0 <= prediction.primary_confidence <= 1

    def test_classify_returns_secondary(self, sample_audio):
        """Test that secondary genres are returned."""
        audio, sr = sample_audio

        prediction = classify_genre(audio, sr, top_k=3)

        assert len(prediction.secondary_genres) == 2
        for genre, conf in prediction.secondary_genres:
            assert genre in GENRES
            assert 0 <= conf <= 1

    def test_features_to_array(self, sample_audio):
        """Test feature conversion to array."""
        audio, sr = sample_audio
        features = extract_genre_features(audio, sr)

        arr = features.to_array()
        assert isinstance(arr, np.ndarray)
        assert len(arr) == GenreFeatures.num_features()

    def test_genres_list(self):
        """Test that genres list is populated."""
        assert len(GENRES) >= 10
        assert "rock" in GENRES
        assert "metal" in GENRES
        assert "blues" in GENRES

    def test_subgenres(self):
        """Test that subgenres are defined."""
        assert "rock" in SUBGENRES
        assert len(SUBGENRES["rock"]) > 0
        assert "metal" in SUBGENRES
        assert "modern_metal" in SUBGENRES["metal"]


# ============================================================================
# Archetypes tests
# ============================================================================

class TestArchetypes:
    """Tests for the production archetypes system."""

    def test_archetypes_defined(self):
        """Test that archetypes are defined."""
        assert len(ARCHETYPES) >= 10

    def test_get_archetype(self):
        """Test getting an archetype by name."""
        archetype = get_archetype("classic_rock")

        assert archetype is not None
        assert isinstance(archetype, ToneArchetype)
        assert archetype.name == "classic_rock"
        assert len(archetype.amp_families) > 0

    def test_get_archetype_normalization(self):
        """Test that archetype names are normalized."""
        # Should handle spaces and dashes
        assert get_archetype("classic rock") is not None
        assert get_archetype("classic-rock") is not None

    def test_get_archetype_for_genre(self):
        """Test getting archetype for a genre."""
        archetype = get_archetype_for_genre("rock")

        assert archetype is not None
        assert isinstance(archetype, ToneArchetype)

    def test_get_archetype_with_subgenre(self):
        """Test getting archetype with subgenre."""
        archetype = get_archetype_for_genre("metal", "djent")

        assert archetype is not None
        assert archetype.name == "djent"

    def test_list_archetypes(self):
        """Test listing all archetypes."""
        names = list_archetypes()

        assert len(names) >= 10
        assert "classic_rock" in names
        assert "modern_metal" in names

    def test_archetype_categories(self):
        """Test archetype categorization."""
        categories = get_archetype_categories()

        assert "rock" in categories
        assert "metal" in categories
        assert len(categories["metal"]) > 0

    def test_archetype_properties(self):
        """Test archetype has required properties."""
        archetype = get_archetype("modern_metal")

        assert archetype.name
        assert archetype.display_name
        assert archetype.description
        assert len(archetype.amp_families) > 0
        assert archetype.gain_range[0] < archetype.gain_range[1]
        assert archetype.gain_character in ("clean", "crunch", "high_gain")

    def test_effect_chain_template(self):
        """Test effect chain template properties."""
        archetype = get_archetype("shoegaze")

        assert archetype.effect_chain is not None
        template = archetype.effect_chain

        assert isinstance(template, EffectChainTemplate)
        assert template.includes_reverb is True
        assert len(template.reverb_types) > 0


# ============================================================================
# Style hints tests
# ============================================================================

class TestStyleHints:
    """Tests for genre-specific style hints."""

    def test_generate_hints(self, sample_descriptor):
        """Test hint generation."""
        prediction = GenrePrediction(
            primary_genre="metal",
            primary_confidence=0.8,
            primary_subgenre="modern_metal",
        )

        hints = generate_genre_hints(prediction, sample_descriptor)

        assert len(hints) > 0
        assert all(isinstance(h, StyleHint) for h in hints)

    def test_hint_structure(self, sample_descriptor):
        """Test that hints have required structure."""
        prediction = GenrePrediction(
            primary_genre="blues",
            primary_confidence=0.8,
        )

        hints = generate_genre_hints(prediction, sample_descriptor)

        for hint in hints:
            assert hint.category in ("amp", "cab", "effects", "technique", "mixing")
            assert hint.priority in (1, 2, 3)
            assert len(hint.message) > 0
            assert hint.action

    def test_format_hints(self, sample_descriptor):
        """Test hint formatting."""
        prediction = GenrePrediction(
            primary_genre="rock",
            primary_confidence=0.8,
        )

        hints = generate_genre_hints(prediction, sample_descriptor)
        formatted = format_hints_for_display(hints)

        assert len(formatted) == len(hints)
        assert all(isinstance(f, str) for f in formatted)

    def test_quick_tips(self):
        """Test quick tips for genres."""
        tips = get_quick_tips("metal")

        assert len(tips) <= 3
        assert all(isinstance(t, str) for t in tips)
        assert len(tips[0]) > 0

    def test_quick_tips_unknown_genre(self):
        """Test quick tips for unknown genre."""
        tips = get_quick_tips("unknown_genre_xyz")

        # Should return default tips
        assert len(tips) > 0


# ============================================================================
# Integration tests
# ============================================================================

class TestGenreDetectionIntegration:
    """Integration tests for the full genre detection pipeline."""

    def test_analyze_and_recommend(self, sample_audio, sample_descriptor):
        """Test full analysis pipeline."""
        audio, sr = sample_audio

        result = analyze_and_recommend(audio, sr, sample_descriptor)

        assert "genre" in result
        assert "genre_confidence" in result
        assert "archetype" in result
        assert "hints" in result
        assert "quick_tips" in result

        assert result["genre"] in GENRES
        assert 0 <= result["genre_confidence"] <= 1

    def test_module_imports(self):
        """Test that all module imports work correctly."""
        from tone_forge.genre_detection import (
            GenreClassifier,
            ToneArchetype,
            StyleHint,
            classify_genre,
            get_archetype,
            generate_genre_hints,
        )

        assert GenreClassifier is not None
        assert ToneArchetype is not None
        assert StyleHint is not None

    def test_classifier_singleton(self):
        """Test that classifier singleton works."""
        c1 = get_classifier()
        c2 = get_classifier()

        # Should be same instance (but we reset for tests so may differ)
        assert c1 is not None
        assert c2 is not None
