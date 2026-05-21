"""Tests for tone_forge/ml/confidence - ML confidence models."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tone_forge.ml.confidence.feature_extractor import (
    MLFeatureVector,
    extract_ml_features,
)
from tone_forge.ml.confidence.models import (
    ConfidenceModel,
    ConfidenceScores,
    compute_ml_confidence,
    get_model,
    AMP_FAMILIES,
    SPEAKER_CHARACTERS,
)
from tone_forge.ml.confidence.registry import (
    is_ready,
    get_model_info,
    reset,
)


SR = 22050


def _make_sine_wave(freq: float = 440, duration: float = 1.0) -> np.ndarray:
    """Generate a sine wave."""
    t = np.linspace(0, duration, int(SR * duration), endpoint=False)
    return (np.sin(2 * np.pi * freq * t) * 0.5).astype(np.float32)


def _make_distorted_audio(duration: float = 1.0) -> np.ndarray:
    """Generate distorted-sounding audio (clipped + harmonics)."""
    t = np.linspace(0, duration, int(SR * duration), endpoint=False)
    # Base signal
    sig = np.sin(2 * np.pi * 220 * t)
    # Add harmonics
    for k in range(2, 8):
        sig += (0.5 / k) * np.sin(2 * np.pi * k * 220 * t)
    # Soft clipping (tanh)
    sig = np.tanh(sig * 3)
    return (sig * 0.7).astype(np.float32)


def _make_clean_audio(duration: float = 1.0) -> np.ndarray:
    """Generate clean-sounding audio."""
    t = np.linspace(0, duration, int(SR * duration), endpoint=False)
    # Pure tone with slight decay
    env = np.exp(-0.5 * t)
    sig = np.sin(2 * np.pi * 440 * t) * env
    return (sig * 0.5).astype(np.float32)


class TestMLFeatureVector:
    """Test MLFeatureVector dataclass."""

    def test_create_default(self):
        vec = MLFeatureVector()
        assert vec.spectral_centroid_mean == 0.0
        assert vec.duration_sec == 0.0

    def test_to_array(self):
        vec = MLFeatureVector(
            spectral_centroid_mean=1000.0,
            rms_mean=0.5,
            duration_sec=2.0,
        )
        arr = vec.to_array()
        assert isinstance(arr, np.ndarray)
        assert arr.dtype == np.float32
        assert len(arr) == MLFeatureVector.num_features()

    def test_feature_names_match_array_length(self):
        names = MLFeatureVector.feature_names()
        assert len(names) == MLFeatureVector.num_features()

    def test_num_features(self):
        assert MLFeatureVector.num_features() == 55


class TestExtractMLFeatures:
    """Test extract_ml_features function."""

    def test_extract_from_sine(self):
        audio = _make_sine_wave(440, duration=1.0)
        features = extract_ml_features(audio, SR)
        assert isinstance(features, MLFeatureVector)
        assert features.duration_sec == pytest.approx(1.0, abs=0.1)
        assert features.spectral_centroid_mean > 0

    def test_extract_from_distorted(self):
        audio = _make_distorted_audio(duration=1.0)
        features = extract_ml_features(audio, SR)
        # Distorted audio should have higher flatness
        assert features.spectral_flatness_mean > 0

    def test_extract_from_clean(self):
        audio = _make_clean_audio(duration=1.0)
        features = extract_ml_features(audio, SR)
        # Clean audio should have relatively high crest factor
        # (compared to heavily distorted audio which would be ~3 dB)
        assert features.crest_factor_db > 3

    def test_extract_short_audio(self):
        # Use longer audio to avoid librosa delta issues
        audio = _make_sine_wave(440, duration=0.5)
        features = extract_ml_features(audio, SR)
        assert features.duration_sec == pytest.approx(0.5, abs=0.1)

    def test_extract_band_energies(self):
        audio = _make_distorted_audio(duration=1.0)
        features = extract_ml_features(audio, SR)
        # Should have non-zero band energies
        assert features.band_bass > 0
        assert features.band_mid > 0

    def test_harmonic_features(self):
        audio = _make_sine_wave(440, duration=1.0)
        features = extract_ml_features(audio, SR)
        # Pure sine should have high harmonic ratio
        assert features.harmonic_ratio > 0.5


class TestConfidenceModel:
    """Test ConfidenceModel class."""

    def test_create_model(self):
        model = ConfidenceModel()
        assert model is not None
        assert not model.is_loaded()

    def test_predict_without_models_uses_heuristic(self):
        model = ConfidenceModel()
        features = MLFeatureVector(
            crest_factor_db=18.0,
            spectral_flatness_mean=0.05,
        )
        scores = model.predict(
            features=features,
            predicted_amp_family="fender_clean",
            predicted_gain=0.3,
            predicted_cab="jensen_like",
            detected_effects={"delay": False, "reverb": False},
        )
        assert isinstance(scores, ConfidenceScores)
        assert 0 <= scores.amp_family <= 1
        assert 0 <= scores.gain <= 1
        assert 0 <= scores.cab <= 1
        assert 0 <= scores.effects <= 1

    def test_heuristic_clean_detection(self):
        model = ConfidenceModel()
        # Clean signal features
        features = MLFeatureVector(
            crest_factor_db=20.0,
            spectral_flatness_mean=0.03,
        )
        scores = model.predict(
            features=features,
            predicted_amp_family="fender_clean",
            predicted_gain=0.2,
            predicted_cab="jensen_like",
            detected_effects={},
        )
        # Clean amp prediction with clean features should have decent confidence
        assert scores.amp_family > 0.5

    def test_heuristic_high_gain_detection(self):
        model = ConfidenceModel()
        # High gain signal features
        features = MLFeatureVector(
            crest_factor_db=8.0,
            spectral_flatness_mean=0.20,
        )
        scores = model.predict(
            features=features,
            predicted_amp_family="mesa_rectifier",
            predicted_gain=0.85,
            predicted_cab="v30_like",
            detected_effects={},
        )
        # High gain prediction with high gain features
        assert scores.amp_family > 0.5


class TestConfidenceScores:
    """Test ConfidenceScores dataclass."""

    def test_create_scores(self):
        scores = ConfidenceScores(
            amp_family=0.8,
            gain=0.7,
            cab=0.6,
            effects=0.5,
        )
        assert scores.amp_family == 0.8
        assert scores.gain == 0.7

    def test_optional_probs(self):
        scores = ConfidenceScores(
            amp_family=0.8,
            gain=0.7,
            cab=0.6,
            effects=0.5,
            amp_family_probs={"fender_clean": 0.8, "tweed": 0.1},
        )
        assert scores.amp_family_probs is not None
        assert "fender_clean" in scores.amp_family_probs


class TestComputeMLConfidence:
    """Test compute_ml_confidence convenience function."""

    def test_compute_confidence(self):
        features = MLFeatureVector(
            crest_factor_db=15.0,
            spectral_flatness_mean=0.10,
        )
        scores = compute_ml_confidence(
            features=features,
            predicted_amp_family="marshall_plexi",
            predicted_gain=0.5,
            predicted_cab="g12m_like",
            detected_effects={"delay": True, "reverb": False},
        )
        assert isinstance(scores, ConfidenceScores)


class TestRegistry:
    """Test registry functions."""

    def test_is_ready_without_models(self):
        reset()
        # Without models loaded, should still work (heuristic fallback)
        ready = is_ready()
        # May be True or False depending on model availability
        assert isinstance(ready, bool)

    def test_get_model_info(self):
        reset()
        info = get_model_info()
        assert isinstance(info, dict)
        assert "loaded" in info
        assert "models" in info

    def test_reset(self):
        reset()
        # Should not raise
        info = get_model_info()
        assert info is not None


class TestAmpFamilyConstants:
    """Test amp family constants."""

    def test_all_families_present(self):
        expected = [
            "fender_clean", "tweed", "vox_chime", "ac30",
            "marshall_plexi", "marshall_jcm", "mesa_rectifier",
            "5150_peavey", "bogner", "soldano", "dumble", "unknown"
        ]
        for family in expected:
            assert family in AMP_FAMILIES


class TestSpeakerCharacterConstants:
    """Test speaker character constants."""

    def test_all_characters_present(self):
        expected = [
            "v30_like", "g12m_like", "g12h_like",
            "alnico_blue_like", "jensen_like", "unknown"
        ]
        for char in expected:
            assert char in SPEAKER_CHARACTERS


class TestIntegrationWithAnalyzer:
    """Test ML confidence integration with analyzer."""

    def test_extract_features_for_analyzer(self):
        """Test that extracted features can be used by analyzer."""
        audio = _make_distorted_audio(duration=2.0)
        features = extract_ml_features(audio, SR)

        # Verify features are extracted
        assert features.spectral_centroid_mean > 0
        assert features.band_mid > 0
        # Distorted audio (tanh clipping) has low crest factor
        assert features.crest_factor_db < 5


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
