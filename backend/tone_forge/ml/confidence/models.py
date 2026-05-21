"""Confidence models using XGBoost classifiers.

Provides per-attribute classifiers for estimating confidence in
descriptor predictions. Falls back to heuristic confidence when
models aren't available.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
import logging

import numpy as np

from .feature_extractor import MLFeatureVector

logger = logging.getLogger(__name__)

# Try to import XGBoost
try:
    import xgboost as xgb
    XGBOOST_AVAILABLE = True
except ImportError:
    XGBOOST_AVAILABLE = False
    xgb = None

# Try to import joblib for model persistence
try:
    import joblib
    JOBLIB_AVAILABLE = True
except ImportError:
    JOBLIB_AVAILABLE = False
    joblib = None


# Amp family classes for classification
AMP_FAMILIES = [
    "fender_clean", "tweed", "vox_chime", "ac30",
    "marshall_plexi", "marshall_jcm", "mesa_rectifier",
    "5150_peavey", "bogner", "soldano", "dumble", "unknown"
]

# Speaker character classes
SPEAKER_CHARACTERS = [
    "v30_like", "g12m_like", "g12h_like",
    "alnico_blue_like", "jensen_like", "unknown"
]


@dataclass
class ConfidenceScores:
    """Confidence scores from ML models."""
    amp_family: float
    gain: float
    cab: float
    effects: float

    # Probabilities for each class (optional, for detailed output)
    amp_family_probs: Optional[Dict[str, float]] = None
    speaker_probs: Optional[Dict[str, float]] = None


class ConfidenceModel:
    """ML-based confidence estimation for descriptor attributes.

    Uses XGBoost classifiers trained on labeled audio data to predict
    confidence in each descriptor attribute. When models aren't loaded
    or available, falls back to heuristic confidence scoring.
    """

    def __init__(self):
        self.amp_family_model: Optional[Any] = None
        self.gain_model: Optional[Any] = None
        self.cab_model: Optional[Any] = None
        self.effects_model: Optional[Any] = None
        self._loaded = False

    def is_loaded(self) -> bool:
        """Check if models are loaded."""
        return self._loaded

    def load(self, model_dir: Path) -> bool:
        """Load models from directory.

        Args:
            model_dir: Directory containing model files

        Returns:
            True if all models loaded successfully
        """
        if not XGBOOST_AVAILABLE:
            logger.warning("XGBoost not available, using heuristic confidence")
            return False

        if not JOBLIB_AVAILABLE:
            logger.warning("joblib not available, cannot load models")
            return False

        model_dir = Path(model_dir)
        if not model_dir.exists():
            logger.warning(f"Model directory not found: {model_dir}")
            return False

        try:
            amp_path = model_dir / "amp_family_xgb.joblib"
            gain_path = model_dir / "gain_xgb.joblib"
            cab_path = model_dir / "cab_xgb.joblib"
            effects_path = model_dir / "effects_xgb.joblib"

            if amp_path.exists():
                self.amp_family_model = joblib.load(amp_path)
                logger.info("Loaded amp family model")

            if gain_path.exists():
                self.gain_model = joblib.load(gain_path)
                logger.info("Loaded gain model")

            if cab_path.exists():
                self.cab_model = joblib.load(cab_path)
                logger.info("Loaded cab model")

            if effects_path.exists():
                self.effects_model = joblib.load(effects_path)
                logger.info("Loaded effects model")

            self._loaded = any([
                self.amp_family_model,
                self.gain_model,
                self.cab_model,
                self.effects_model
            ])
            return self._loaded

        except Exception as e:
            logger.error(f"Error loading models: {e}")
            return False

    def predict(
        self,
        features: MLFeatureVector,
        predicted_amp_family: str,
        predicted_gain: float,
        predicted_cab: str,
        detected_effects: Dict[str, bool],
    ) -> ConfidenceScores:
        """Predict confidence scores using ML models.

        Args:
            features: Extracted ML feature vector
            predicted_amp_family: The amp family predicted by DSP analysis
            predicted_gain: The gain level predicted by DSP analysis
            predicted_cab: The cab/speaker predicted by DSP analysis
            detected_effects: Dict of effect types and whether detected

        Returns:
            ConfidenceScores with confidence for each attribute
        """
        X = features.to_array().reshape(1, -1)

        # Amp family confidence
        if self.amp_family_model is not None:
            try:
                probs = self.amp_family_model.predict_proba(X)[0]
                class_idx = AMP_FAMILIES.index(predicted_amp_family) if predicted_amp_family in AMP_FAMILIES else -1
                if class_idx >= 0 and class_idx < len(probs):
                    amp_conf = float(probs[class_idx])
                else:
                    amp_conf = float(np.max(probs))
                amp_probs = {fam: float(p) for fam, p in zip(AMP_FAMILIES, probs)}
            except Exception as e:
                logger.warning(f"Amp family prediction failed: {e}")
                amp_conf = self._heuristic_amp_confidence(features, predicted_amp_family)
                amp_probs = None
        else:
            amp_conf = self._heuristic_amp_confidence(features, predicted_amp_family)
            amp_probs = None

        # Gain confidence
        if self.gain_model is not None:
            try:
                # Regression model predicting actual gain
                predicted = float(self.gain_model.predict(X)[0])
                # Confidence based on how close prediction is to DSP estimate
                error = abs(predicted - predicted_gain)
                gain_conf = float(np.clip(1.0 - error * 2.0, 0.3, 0.95))
            except Exception as e:
                logger.warning(f"Gain prediction failed: {e}")
                gain_conf = self._heuristic_gain_confidence(features, predicted_gain)
        else:
            gain_conf = self._heuristic_gain_confidence(features, predicted_gain)

        # Cab confidence
        if self.cab_model is not None:
            try:
                probs = self.cab_model.predict_proba(X)[0]
                class_idx = SPEAKER_CHARACTERS.index(predicted_cab) if predicted_cab in SPEAKER_CHARACTERS else -1
                if class_idx >= 0 and class_idx < len(probs):
                    cab_conf = float(probs[class_idx])
                else:
                    cab_conf = float(np.max(probs))
                speaker_probs = {spk: float(p) for spk, p in zip(SPEAKER_CHARACTERS, probs)}
            except Exception as e:
                logger.warning(f"Cab prediction failed: {e}")
                cab_conf = self._heuristic_cab_confidence(features)
                speaker_probs = None
        else:
            cab_conf = self._heuristic_cab_confidence(features)
            speaker_probs = None

        # Effects confidence
        if self.effects_model is not None:
            try:
                # Multi-label classification for effects
                probs = self.effects_model.predict_proba(X)
                # Average confidence across detected effects
                effect_confs = []
                for effect, detected in detected_effects.items():
                    if detected:
                        # Higher confidence if model agrees with detection
                        effect_confs.append(0.7)
                if effect_confs:
                    fx_conf = float(np.mean(effect_confs))
                else:
                    fx_conf = 0.5
            except Exception as e:
                logger.warning(f"Effects prediction failed: {e}")
                fx_conf = self._heuristic_effects_confidence(features, detected_effects)
        else:
            fx_conf = self._heuristic_effects_confidence(features, detected_effects)

        return ConfidenceScores(
            amp_family=amp_conf,
            gain=gain_conf,
            cab=cab_conf,
            effects=fx_conf,
            amp_family_probs=amp_probs,
            speaker_probs=speaker_probs,
        )

    def _heuristic_amp_confidence(
        self,
        features: MLFeatureVector,
        predicted_family: str,
    ) -> float:
        """Heuristic confidence for amp family.

        Based on how well the spectral features match expected patterns
        for the predicted amp family.
        """
        # Clean amps should have high crest factor
        if predicted_family in ("fender_clean", "tweed"):
            if features.crest_factor_db > 15:
                return 0.75
            elif features.crest_factor_db > 10:
                return 0.55
            else:
                return 0.35

        # High gain amps should have high flatness, low crest
        if predicted_family in ("mesa_rectifier", "5150_peavey", "bogner", "soldano"):
            if features.spectral_flatness_mean > 0.15 and features.crest_factor_db < 12:
                return 0.70
            elif features.spectral_flatness_mean > 0.10:
                return 0.55
            else:
                return 0.40

        # Mid-gain (Marshall, etc) - moderate values
        return 0.55

    def _heuristic_gain_confidence(
        self,
        features: MLFeatureVector,
        predicted_gain: float,
    ) -> float:
        """Heuristic confidence for gain level.

        Based on agreement between flatness and crest factor indicators.
        """
        # Estimate gain from flatness
        flat_gain = float(np.clip((features.spectral_flatness_mean - 0.06) / 0.22, 0.0, 1.0))

        # Estimate from crest factor
        crest_gain = float(np.clip((20.0 - features.crest_factor_db) / 16.0, 0.0, 1.0))

        # Agreement between indicators
        agreement = 1.0 - abs(flat_gain - crest_gain)
        return float(np.clip(0.45 + 0.35 * agreement, 0.3, 0.85))

    def _heuristic_cab_confidence(self, features: MLFeatureVector) -> float:
        """Heuristic confidence for cab/speaker character.

        Based on presence of clear spectral peaks in the upper-mid range.
        """
        # If upper mids have clear character, higher confidence
        upper_mid_prominence = features.band_upper_mid / (features.band_mid + 1e-9)
        if upper_mid_prominence > 1.2:
            return 0.65
        elif upper_mid_prominence > 0.8:
            return 0.50
        else:
            return 0.40

    def _heuristic_effects_confidence(
        self,
        features: MLFeatureVector,
        detected_effects: Dict[str, bool],
    ) -> float:
        """Heuristic confidence for effects detection."""
        # More detected effects with clear signatures = higher confidence
        if not any(detected_effects.values()):
            return 0.5

        conf = 0.5
        if detected_effects.get("delay") and features.quiet_loud_ratio > 0.15:
            conf += 0.1
        if detected_effects.get("reverb") and features.quiet_loud_ratio > 0.20:
            conf += 0.1
        if detected_effects.get("compression") and features.compression_ratio_est > 0.3:
            conf += 0.1

        return float(np.clip(conf, 0.3, 0.85))


# Global model instance
_model: Optional[ConfidenceModel] = None


def get_model() -> ConfidenceModel:
    """Get the global confidence model instance."""
    global _model
    if _model is None:
        _model = ConfidenceModel()
    return _model


def compute_ml_confidence(
    features: MLFeatureVector,
    predicted_amp_family: str,
    predicted_gain: float,
    predicted_cab: str,
    detected_effects: Dict[str, bool],
) -> ConfidenceScores:
    """Compute ML-based confidence scores.

    Convenience function that uses the global model instance.
    """
    model = get_model()
    return model.predict(
        features=features,
        predicted_amp_family=predicted_amp_family,
        predicted_gain=predicted_gain,
        predicted_cab=predicted_cab,
        detected_effects=detected_effects,
    )
