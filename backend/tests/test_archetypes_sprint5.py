"""Tests for Sprint 5: Archetypes and Reconstruction Priors.

Tests cover:
- ProductionArchetype base class
- Synthwave, shoegaze, and ambient archetypes
- ArchetypeRegistry lookups
- ExtractionPriors generation
- Pipeline integration with priors
"""
import pytest
import numpy as np

from tone_forge.archetypes import (
    # Base types
    ProductionArchetype,
    AudioCharacteristics,
    ExtractionParameters,
    ExpectedPatterns,
    TransientClarity,
    HarmonicComplexity,
    # Synthwave
    SYNTHWAVE,
    DARKWAVE,
    DREAMWAVE,
    create_synthwave_archetype,
    # Shoegaze
    SHOEGAZE,
    DREAM_POP,
    create_shoegaze_archetype,
    # Ambient
    AMBIENT,
    DRONE,
    DARK_AMBIENT,
    create_ambient_archetype,
    # Registry
    ArchetypeRegistry,
    get_registry,
    get_archetype,
    get_archetype_or_default,
    # Priors
    ExtractionPriors,
    ValidationBounds,
    ReconstructionPriors,
    get_extraction_priors,
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
def registry():
    """Get a fresh registry."""
    return ArchetypeRegistry()


# =============================================================================
# AudioCharacteristics Tests
# =============================================================================

class TestAudioCharacteristics:
    """Tests for AudioCharacteristics."""

    def test_default_values(self):
        """Test default characteristics."""
        audio = AudioCharacteristics()

        assert audio.reverb_density_range == (0.2, 0.5)
        assert audio.transient_clarity == TransientClarity.MEDIUM
        assert not audio.uses_delay

    def test_custom_values(self):
        """Test custom characteristics."""
        audio = AudioCharacteristics(
            reverb_density_range=(0.8, 0.99),
            transient_clarity=TransientClarity.SMEARED,
            uses_delay=True,
            typical_delay_time_ms=250.0,
        )

        assert audio.reverb_density_range[1] > 0.9
        assert audio.transient_clarity == TransientClarity.SMEARED
        assert audio.uses_delay


# =============================================================================
# ExtractionParameters Tests
# =============================================================================

class TestExtractionParameters:
    """Tests for ExtractionParameters."""

    def test_default_values(self):
        """Test default parameters."""
        params = ExtractionParameters()

        assert params.onset_threshold_multiplier == 1.0
        assert params.quantization_strength == 0.7
        assert params.note_merge_time_ms == 50.0

    def test_multipliers(self):
        """Test threshold multipliers."""
        params = ExtractionParameters(
            onset_threshold_multiplier=0.7,
            frame_threshold_multiplier=0.8,
        )

        # Apply multipliers
        base_onset = 0.5
        adjusted = base_onset * params.onset_threshold_multiplier
        assert adjusted == 0.35


# =============================================================================
# ProductionArchetype Tests
# =============================================================================

class TestProductionArchetype:
    """Tests for ProductionArchetype base class."""

    def test_create_basic(self):
        """Test creating a basic archetype."""
        archetype = ProductionArchetype(
            name="test",
            description="Test archetype",
            audio_characteristics=AudioCharacteristics(),
            extraction_parameters=ExtractionParameters(),
            expected_patterns=ExpectedPatterns(),
        )

        assert archetype.name == "test"
        assert archetype.description == "Test archetype"

    def test_get_extraction_params(self):
        """Test getting extraction parameters."""
        archetype = ProductionArchetype(
            name="test",
            description="Test",
            audio_characteristics=AudioCharacteristics(),
            extraction_parameters=ExtractionParameters(
                onset_threshold_multiplier=0.8,
            ),
            expected_patterns=ExpectedPatterns(),
        )

        params = archetype.get_extraction_params()
        assert params.onset_threshold_multiplier == 0.8

    def test_get_extraction_params_bass(self):
        """Test stem-specific parameter adjustment."""
        archetype = ProductionArchetype(
            name="test",
            description="Test",
            audio_characteristics=AudioCharacteristics(),
            extraction_parameters=ExtractionParameters(
                onset_threshold_multiplier=0.8,
                quantization_strength=0.7,
            ),
            expected_patterns=ExpectedPatterns(),
        )

        params = archetype.get_extraction_params(stem_type="bass")
        # Bass should get tighter parameters
        assert params.onset_threshold_multiplier > 0.8
        assert params.quantization_strength >= 0.7

    def test_get_extraction_params_pad(self):
        """Test stem-specific parameter adjustment for pads."""
        archetype = ProductionArchetype(
            name="test",
            description="Test",
            audio_characteristics=AudioCharacteristics(),
            extraction_parameters=ExtractionParameters(
                onset_threshold_multiplier=0.8,
            ),
            expected_patterns=ExpectedPatterns(),
        )

        params = archetype.get_extraction_params(stem_type="pad")
        # Pads should get looser parameters
        assert params.onset_threshold_multiplier < 0.8

    def test_is_applicable(self):
        """Test genre applicability check."""
        archetype = ProductionArchetype(
            name="synthwave",
            description="Test",
            audio_characteristics=AudioCharacteristics(),
            extraction_parameters=ExtractionParameters(),
            expected_patterns=ExpectedPatterns(),
            related_genres=["retrowave", "outrun"],
        )

        assert archetype.is_applicable("synthwave")
        assert archetype.is_applicable("retrowave")
        assert not archetype.is_applicable("metal")

    def test_get_quality_thresholds(self):
        """Test quality threshold generation."""
        archetype = ProductionArchetype(
            name="test",
            description="Test",
            audio_characteristics=AudioCharacteristics(
                reverb_density_range=(0.7, 0.95),
            ),
            extraction_parameters=ExtractionParameters(),
            expected_patterns=ExpectedPatterns(),
        )

        thresholds = archetype.get_quality_thresholds()
        # High reverb genre should have looser reverb threshold
        assert thresholds["max_reverb_density"] >= 0.9

    def test_to_dict(self):
        """Test serialization."""
        archetype = ProductionArchetype(
            name="test",
            description="Test archetype",
            audio_characteristics=AudioCharacteristics(),
            extraction_parameters=ExtractionParameters(),
            expected_patterns=ExpectedPatterns(),
        )

        d = archetype.to_dict()
        assert d["name"] == "test"
        assert "audio_characteristics" in d
        assert "extraction_parameters" in d


# =============================================================================
# Synthwave Archetype Tests
# =============================================================================

class TestSynthwaveArchetype:
    """Tests for synthwave archetype."""

    def test_synthwave_exists(self):
        """Test synthwave archetype is defined."""
        assert SYNTHWAVE is not None
        assert SYNTHWAVE.name == "synthwave"

    def test_synthwave_characteristics(self):
        """Test synthwave audio characteristics."""
        audio = SYNTHWAVE.audio_characteristics

        # Synthwave has heavy reverb
        assert audio.reverb_density_range[1] > 0.7
        assert audio.uses_delay
        assert audio.transient_clarity == TransientClarity.SOFT

    def test_synthwave_extraction(self):
        """Test synthwave extraction parameters."""
        params = SYNTHWAVE.extraction_parameters

        # Synthwave needs lower thresholds (soft attacks)
        assert params.onset_threshold_multiplier < 1.0
        # Less strict quantization
        assert params.quantization_strength < 0.7

    def test_synthwave_patterns(self):
        """Test synthwave expected patterns."""
        patterns = SYNTHWAVE.expected_patterns

        # Long sustains
        assert patterns.typical_sustain_ratio > 0.5
        assert patterns.uses_long_notes

    def test_darkwave_variant(self):
        """Test darkwave variant."""
        assert DARKWAVE is not None
        assert DARKWAVE.name == "darkwave"
        # Darker = less bright
        assert DARKWAVE.audio_characteristics.brightness < SYNTHWAVE.audio_characteristics.brightness

    def test_dreamwave_variant(self):
        """Test dreamwave variant."""
        assert DREAMWAVE is not None
        assert DREAMWAVE.name == "dreamwave"
        # More reverb
        assert DREAMWAVE.audio_characteristics.reverb_density_range[1] > SYNTHWAVE.audio_characteristics.reverb_density_range[1]


# =============================================================================
# Shoegaze Archetype Tests
# =============================================================================

class TestShoegazeArchetype:
    """Tests for shoegaze archetype."""

    def test_shoegaze_exists(self):
        """Test shoegaze archetype is defined."""
        assert SHOEGAZE is not None
        assert SHOEGAZE.name == "shoegaze"

    def test_shoegaze_extreme_reverb(self):
        """Test shoegaze has extreme reverb."""
        audio = SHOEGAZE.audio_characteristics

        assert audio.reverb_density_range[1] >= 0.95
        assert audio.transient_clarity == TransientClarity.SMEARED

    def test_shoegaze_low_thresholds(self):
        """Test shoegaze has very low detection thresholds."""
        params = SHOEGAZE.extraction_parameters

        assert params.onset_threshold_multiplier <= 0.5
        assert params.quantization_strength <= 0.3

    def test_dream_pop_variant(self):
        """Test dream pop is lighter than shoegaze."""
        assert DREAM_POP is not None
        # Less extreme reverb
        assert DREAM_POP.audio_characteristics.reverb_density_range[1] < SHOEGAZE.audio_characteristics.reverb_density_range[1]


# =============================================================================
# Ambient Archetype Tests
# =============================================================================

class TestAmbientArchetype:
    """Tests for ambient archetype."""

    def test_ambient_exists(self):
        """Test ambient archetype is defined."""
        assert AMBIENT is not None
        assert AMBIENT.name == "ambient"

    def test_ambient_maximum_reverb(self):
        """Test ambient has maximum reverb."""
        audio = AMBIENT.audio_characteristics

        assert audio.reverb_density_range[1] >= 0.95
        assert audio.typical_reverb_time >= 5.0

    def test_ambient_minimal_quantization(self):
        """Test ambient has minimal quantization."""
        params = AMBIENT.extraction_parameters

        assert params.quantization_strength <= 0.2

    def test_ambient_low_density(self):
        """Test ambient expects low note density."""
        patterns = AMBIENT.expected_patterns

        assert patterns.note_density_range[1] <= 1.0

    def test_drone_variant(self):
        """Test drone is more extreme than ambient."""
        assert DRONE is not None
        assert DRONE.extraction_parameters.quantization_strength < AMBIENT.extraction_parameters.quantization_strength


# =============================================================================
# ArchetypeRegistry Tests
# =============================================================================

class TestArchetypeRegistry:
    """Tests for ArchetypeRegistry."""

    def test_create_registry(self, registry):
        """Test creating a registry."""
        assert registry is not None
        assert len(registry.list_archetypes()) > 0

    def test_get_by_name(self, registry):
        """Test getting archetype by name."""
        synthwave = registry.get("synthwave")
        assert synthwave is not None
        assert synthwave.name == "synthwave"

    def test_get_by_alias(self, registry):
        """Test getting archetype by alias."""
        # "retrowave" should map to synthwave
        retrowave = registry.get("retrowave")
        assert retrowave is not None
        assert retrowave.name == "synthwave"

    def test_get_unknown(self, registry):
        """Test getting unknown genre."""
        unknown = registry.get("unknown_genre_xyz")
        assert unknown is None

    def test_get_or_default(self, registry):
        """Test get with default."""
        result = registry.get_or_default("unknown_genre")
        assert result is not None
        # Should return synthwave as default
        assert result.name == "synthwave"

    def test_get_best_match(self, registry):
        """Test best match with confidence."""
        archetype, confidence = registry.get_best_match("synthwave")
        assert archetype is not None
        assert confidence == 1.0

        archetype, confidence = registry.get_best_match("retrowave")
        assert archetype is not None
        assert confidence < 1.0  # Alias match

    def test_list_archetypes(self, registry):
        """Test listing all archetypes."""
        archetypes = registry.list_archetypes()

        assert "synthwave" in archetypes
        assert "shoegaze" in archetypes
        assert "ambient" in archetypes

    def test_list_all_genres(self, registry):
        """Test listing all recognized genres."""
        genres = registry.list_all_genres()

        # Should include archetypes and aliases
        assert "synthwave" in genres
        assert "retrowave" in genres
        assert "shoegaze" in genres

    def test_register_custom(self, registry):
        """Test registering custom archetype."""
        custom = ProductionArchetype(
            name="custom_genre",
            description="Custom test genre",
            audio_characteristics=AudioCharacteristics(),
            extraction_parameters=ExtractionParameters(),
            expected_patterns=ExpectedPatterns(),
        )

        registry.register(custom)

        result = registry.get("custom_genre")
        assert result is not None
        assert result.name == "custom_genre"


class TestModuleFunctions:
    """Tests for module-level functions."""

    def test_get_registry(self):
        """Test getting global registry."""
        reg1 = get_registry()
        reg2 = get_registry()

        # Should be same instance
        assert reg1 is reg2

    def test_get_archetype(self):
        """Test convenience function."""
        archetype = get_archetype("synthwave")
        assert archetype is not None
        assert archetype.name == "synthwave"

    def test_get_archetype_or_default(self):
        """Test convenience function with default."""
        archetype = get_archetype_or_default("unknown")
        assert archetype is not None


# =============================================================================
# ExtractionPriors Tests
# =============================================================================

class TestExtractionPriors:
    """Tests for ExtractionPriors."""

    def test_default_priors(self):
        """Test default priors."""
        priors = ExtractionPriors()

        assert priors.expected_note_density == (0.5, 5.0)
        assert priors.quantization_strength == 0.7
        assert priors.suggested_onset_threshold == 0.5

    def test_custom_priors(self):
        """Test custom priors."""
        priors = ExtractionPriors(
            expected_note_density=(0.1, 1.0),
            quantization_strength=0.3,
            likely_effects=["reverb", "delay"],
        )

        assert priors.expected_note_density == (0.1, 1.0)
        assert "reverb" in priors.likely_effects

    def test_to_dict(self):
        """Test serialization."""
        priors = ExtractionPriors(source_genre="synthwave")
        d = priors.to_dict()

        assert d["source_genre"] == "synthwave"
        assert "expected_note_density" in d


class TestValidationBounds:
    """Tests for ValidationBounds."""

    def test_default_bounds(self):
        """Test default bounds."""
        bounds = ValidationBounds()

        assert bounds.min_notes == 1
        assert bounds.max_notes == 10000
        assert bounds.min_velocity == 1

    def test_validate_empty(self):
        """Test validating empty extraction."""
        bounds = ValidationBounds()
        warnings = bounds.validate([], 10.0)

        assert len(warnings) > 0
        assert any("Too few" in w for w in warnings)

    def test_validate_good(self):
        """Test validating good extraction."""
        bounds = ValidationBounds()

        # Create mock notes
        class MockNote:
            pass

        notes = [MockNote() for _ in range(20)]
        warnings = bounds.validate(notes, 10.0)

        # Should have no major warnings
        assert not any("Too few" in w or "Too many" in w for w in warnings)


class TestReconstructionPriors:
    """Tests for ReconstructionPriors generator."""

    def test_create(self):
        """Test creating priors generator."""
        generator = ReconstructionPriors()
        assert generator.use_archetypes

    def test_get_priors_no_genre(self):
        """Test getting priors without genre."""
        generator = ReconstructionPriors()
        priors = generator.get_priors()

        assert isinstance(priors, ExtractionPriors)

    def test_get_priors_synthwave(self):
        """Test getting priors for synthwave."""
        generator = ReconstructionPriors()
        priors = generator.get_priors(genre="synthwave")

        assert priors.source_archetype == "synthwave"
        # Synthwave has lower thresholds
        assert priors.suggested_onset_threshold < 0.5

    def test_get_priors_ambient(self):
        """Test getting priors for ambient."""
        generator = ReconstructionPriors()
        priors = generator.get_priors(genre="ambient")

        assert priors.source_archetype == "ambient"
        # Ambient has minimal quantization
        assert priors.quantization_strength < 0.2

    def test_get_priors_with_stem_type(self):
        """Test priors adjustment for stem type."""
        generator = ReconstructionPriors()

        bass_priors = generator.get_priors(genre="synthwave", stem_type="bass")
        pad_priors = generator.get_priors(genre="synthwave", stem_type="pad")

        # Bass should have tighter quantization than pads
        assert bass_priors.quantization_strength > pad_priors.quantization_strength

    def test_get_validation_bounds(self):
        """Test getting validation bounds."""
        generator = ReconstructionPriors()
        bounds = generator.get_validation_bounds(genre="synthwave")

        assert isinstance(bounds, ValidationBounds)


class TestGetExtractionPriors:
    """Tests for convenience function."""

    def test_get_extraction_priors(self):
        """Test getting priors via convenience function."""
        priors = get_extraction_priors(genre="synthwave")

        assert isinstance(priors, ExtractionPriors)
        assert priors.source_archetype == "synthwave"

    def test_get_extraction_priors_bass(self):
        """Test getting bass-specific priors."""
        priors = get_extraction_priors(genre="synthwave", stem_type="bass")

        assert priors.expected_pitch_range[0] >= 24
        assert priors.expected_pitch_range[1] <= 72


# =============================================================================
# Pipeline Integration Tests
# =============================================================================

class TestPipelineIntegration:
    """Tests for pipeline integration with archetypes."""

    def test_pipeline_uses_priors(self, sample_audio):
        """Test that pipeline uses priors when genre specified."""
        from tone_forge.reconstruction import (
            ReconstructionPipeline,
            ReconstructionConfig,
        )

        audio, sr = sample_audio
        config = ReconstructionConfig(use_archetypes=True)
        pipeline = ReconstructionPipeline(config=config)

        result = pipeline.process(
            audio=audio,
            sr=sr,
            stem_type="synth",
            genre="synthwave",
        )

        # Should have priors in analysis
        assert result.analysis.priors is not None
        assert result.analysis.priors.source_archetype == "synthwave"

    def test_pipeline_without_archetypes(self, sample_audio):
        """Test pipeline works without archetypes."""
        from tone_forge.reconstruction import (
            ReconstructionPipeline,
            ReconstructionConfig,
        )

        audio, sr = sample_audio
        config = ReconstructionConfig(use_archetypes=False)
        pipeline = ReconstructionPipeline(config=config)

        result = pipeline.process(
            audio=audio,
            sr=sr,
            stem_type="synth",
        )

        # Should work but no priors
        assert result is not None

    def test_archetype_affects_extraction(self, sample_audio):
        """Test that archetype affects extraction parameters."""
        from tone_forge.reconstruction import ReconstructionPipeline

        audio, sr = sample_audio
        pipeline = ReconstructionPipeline()

        # Extract with synthwave (low thresholds)
        result = pipeline.process(
            audio=audio,
            sr=sr,
            stem_type="synth",
            genre="synthwave",
        )

        # Should use lower thresholds from archetype
        if result.analysis.priors:
            assert result.analysis.priors.suggested_onset_threshold < 0.5


# =============================================================================
# Edge Cases
# =============================================================================

class TestEdgeCases:
    """Tests for edge cases."""

    def test_unknown_genre_fallback(self):
        """Test handling unknown genre."""
        priors = get_extraction_priors(genre="unknown_genre_xyz")

        # Should return default priors
        assert isinstance(priors, ExtractionPriors)
        assert priors.source_archetype is None

    def test_case_insensitive_lookup(self):
        """Test case insensitive genre lookup."""
        archetype = get_archetype("SYNTHWAVE")
        assert archetype is not None
        assert archetype.name == "synthwave"

    def test_hyphenated_genre(self):
        """Test handling hyphenated genre names."""
        archetype = get_archetype("dream-pop")
        # Should match dream_pop
        assert archetype is not None
