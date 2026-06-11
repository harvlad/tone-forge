"""Post-hoc calibration adjustment for MIDI extraction.

Provides calibration methods to adjust confidence scores:
- Isotonic regression (monotonic, non-parametric)
- Platt scaling (sigmoid, parametric)
- Temperature scaling

Goal: Make confidence scores reflect true probability of correctness.
"""
from __future__ import annotations

import json
import logging
import pickle
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .calibration_analyzer import NoteWithConfidence, CalibrationAnalysis, CalibrationAnalyzer

logger = logging.getLogger(__name__)


class CalibrationAdjuster(ABC):
    """Base class for calibration adjusters."""

    @abstractmethod
    def fit(
        self,
        confidences: np.ndarray,
        labels: np.ndarray,
    ) -> None:
        """Fit the calibrator on training data.

        Args:
            confidences: Original confidence scores [0, 1]
            labels: Binary correctness labels (0 or 1)
        """
        pass

    @abstractmethod
    def calibrate(self, confidences: np.ndarray) -> np.ndarray:
        """Apply calibration to confidence scores.

        Args:
            confidences: Original confidence scores

        Returns:
            Calibrated confidence scores
        """
        pass

    def fit_from_notes(self, notes: List[NoteWithConfidence]) -> None:
        """Fit from NoteWithConfidence list."""
        confidences = np.array([n.confidence for n in notes])
        labels = np.array([1.0 if n.is_correct else 0.0 for n in notes])
        self.fit(confidences, labels)

    @abstractmethod
    def save(self, path: Path) -> None:
        """Save calibrator to file."""
        pass

    @classmethod
    @abstractmethod
    def load(cls, path: Path) -> "CalibrationAdjuster":
        """Load calibrator from file."""
        pass


class IsotonicCalibrator(CalibrationAdjuster):
    """Isotonic regression calibrator.

    Non-parametric, monotonic calibration that maps raw confidence
    scores to calibrated probabilities using isotonic regression.

    Advantages:
    - No assumptions about relationship shape
    - Guaranteed monotonic
    - Works well with sufficient data

    Disadvantages:
    - Requires enough data per bin
    - Can overfit with small datasets
    """

    def __init__(self, min_samples: int = 50):
        """Initialize isotonic calibrator.

        Args:
            min_samples: Minimum samples required for fitting
        """
        self.min_samples = min_samples
        self._calibrator = None
        self._is_fitted = False

    def fit(
        self,
        confidences: np.ndarray,
        labels: np.ndarray,
    ) -> None:
        """Fit isotonic regression calibrator.

        Args:
            confidences: Original confidence scores
            labels: Binary correctness labels
        """
        if len(confidences) < self.min_samples:
            logger.warning(
                f"Insufficient samples for isotonic calibration "
                f"({len(confidences)} < {self.min_samples})"
            )
            return

        try:
            from sklearn.isotonic import IsotonicRegression

            self._calibrator = IsotonicRegression(
                y_min=0.0,
                y_max=1.0,
                out_of_bounds='clip'
            )
            self._calibrator.fit(confidences, labels)
            self._is_fitted = True
            logger.info(f"Fitted isotonic calibrator on {len(confidences)} samples")

        except ImportError:
            logger.warning("sklearn not available for isotonic calibration")

    def calibrate(self, confidences: np.ndarray) -> np.ndarray:
        """Apply isotonic calibration.

        Args:
            confidences: Original confidence scores

        Returns:
            Calibrated scores (unchanged if not fitted)
        """
        if not self._is_fitted or self._calibrator is None:
            return confidences

        return self._calibrator.predict(confidences)

    def save(self, path: Path) -> None:
        """Save calibrator to pickle file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, 'wb') as f:
            pickle.dump({
                'calibrator': self._calibrator,
                'is_fitted': self._is_fitted,
                'min_samples': self.min_samples,
            }, f)
        logger.info(f"Saved isotonic calibrator to {path}")

    @classmethod
    def load(cls, path: Path) -> "IsotonicCalibrator":
        """Load calibrator from pickle file."""
        with open(path, 'rb') as f:
            data = pickle.load(f)

        calibrator = cls(min_samples=data['min_samples'])
        calibrator._calibrator = data['calibrator']
        calibrator._is_fitted = data['is_fitted']
        return calibrator


class PlattCalibrator(CalibrationAdjuster):
    """Platt scaling calibrator.

    Parametric calibration using sigmoid function:
    calibrated = 1 / (1 + exp(a * confidence + b))

    Advantages:
    - Works with small datasets
    - Simple, fast

    Disadvantages:
    - Assumes sigmoid relationship
    - May not fit well if relationship is non-sigmoid
    """

    def __init__(self):
        """Initialize Platt calibrator."""
        self.a = 1.0
        self.b = 0.0
        self._is_fitted = False

    def fit(
        self,
        confidences: np.ndarray,
        labels: np.ndarray,
    ) -> None:
        """Fit Platt scaling parameters.

        Args:
            confidences: Original confidence scores
            labels: Binary correctness labels
        """
        if len(confidences) < 10:
            logger.warning("Insufficient samples for Platt scaling")
            return

        try:
            from scipy.optimize import minimize

            # Objective: minimize negative log likelihood
            def neg_log_likelihood(params):
                a, b = params
                # Apply sigmoid
                calibrated = 1.0 / (1.0 + np.exp(-(a * confidences + b)))
                # Clip for numerical stability
                calibrated = np.clip(calibrated, 1e-10, 1 - 1e-10)
                # NLL
                return -np.mean(
                    labels * np.log(calibrated) +
                    (1 - labels) * np.log(1 - calibrated)
                )

            # Optimize
            result = minimize(
                neg_log_likelihood,
                x0=[1.0, 0.0],
                method='L-BFGS-B',
            )

            self.a = result.x[0]
            self.b = result.x[1]
            self._is_fitted = True
            logger.info(f"Fitted Platt calibrator: a={self.a:.4f}, b={self.b:.4f}")

        except ImportError:
            logger.warning("scipy not available for Platt scaling")

    def calibrate(self, confidences: np.ndarray) -> np.ndarray:
        """Apply Platt scaling calibration.

        Args:
            confidences: Original confidence scores

        Returns:
            Calibrated scores
        """
        if not self._is_fitted:
            return confidences

        return 1.0 / (1.0 + np.exp(-(self.a * confidences + self.b)))

    def save(self, path: Path) -> None:
        """Save calibrator to JSON file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, 'w') as f:
            json.dump({
                'a': self.a,
                'b': self.b,
                'is_fitted': self._is_fitted,
            }, f)
        logger.info(f"Saved Platt calibrator to {path}")

    @classmethod
    def load(cls, path: Path) -> "PlattCalibrator":
        """Load calibrator from JSON file."""
        with open(path, 'r') as f:
            data = json.load(f)

        calibrator = cls()
        calibrator.a = data['a']
        calibrator.b = data['b']
        calibrator._is_fitted = data['is_fitted']
        return calibrator


class TemperatureCalibrator(CalibrationAdjuster):
    """Temperature scaling calibrator.

    Simple calibration that adjusts the sharpness of confidence scores:
    calibrated = confidence ^ (1 / temperature)

    temperature > 1: soften (move toward 0.5)
    temperature < 1: sharpen (move toward 0 or 1)

    Advantages:
    - Very simple, single parameter
    - Fast to fit and apply

    Disadvantages:
    - Limited expressiveness
    - Preserves ranking, only adjusts sharpness
    """

    def __init__(self):
        """Initialize temperature calibrator."""
        self.temperature = 1.0
        self._is_fitted = False

    def fit(
        self,
        confidences: np.ndarray,
        labels: np.ndarray,
    ) -> None:
        """Fit temperature parameter.

        Finds temperature that minimizes calibration error.
        """
        if len(confidences) < 20:
            logger.warning("Insufficient samples for temperature scaling")
            return

        # Grid search for optimal temperature
        best_temp = 1.0
        best_ece = float('inf')

        for temp in np.linspace(0.5, 2.0, 31):
            calibrated = confidences ** (1.0 / temp)

            # Simple ECE approximation
            buckets = np.digitize(calibrated, np.linspace(0, 1, 11))
            ece = 0.0
            for b in range(1, 11):
                mask = buckets == b
                if mask.sum() > 0:
                    bucket_conf = calibrated[mask].mean()
                    bucket_acc = labels[mask].mean()
                    weight = mask.sum() / len(confidences)
                    ece += weight * abs(bucket_conf - bucket_acc)

            if ece < best_ece:
                best_ece = ece
                best_temp = temp

        self.temperature = best_temp
        self._is_fitted = True
        logger.info(f"Fitted temperature calibrator: T={self.temperature:.3f}")

    def calibrate(self, confidences: np.ndarray) -> np.ndarray:
        """Apply temperature scaling.

        Args:
            confidences: Original confidence scores

        Returns:
            Calibrated scores
        """
        if not self._is_fitted:
            return confidences

        return confidences ** (1.0 / self.temperature)

    def save(self, path: Path) -> None:
        """Save to JSON."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, 'w') as f:
            json.dump({
                'temperature': self.temperature,
                'is_fitted': self._is_fitted,
            }, f)

    @classmethod
    def load(cls, path: Path) -> "TemperatureCalibrator":
        """Load from JSON."""
        with open(path, 'r') as f:
            data = json.load(f)

        calibrator = cls()
        calibrator.temperature = data['temperature']
        calibrator._is_fitted = data['is_fitted']
        return calibrator


@dataclass
class PerPipelineCalibrator:
    """Calibrator that uses different models per pipeline."""

    calibrators: Dict[str, CalibrationAdjuster] = field(default_factory=dict)
    default_calibrator: Optional[CalibrationAdjuster] = None
    calibrator_type: str = "isotonic"

    def fit(
        self,
        notes: List[NoteWithConfidence],
        min_samples_per_pipeline: int = 50,
    ) -> None:
        """Fit calibrators per pipeline.

        Args:
            notes: Training notes
            min_samples_per_pipeline: Minimum samples per pipeline
        """
        # Group by pipeline
        by_pipeline: Dict[str, List[NoteWithConfidence]] = {}
        for note in notes:
            pipeline = note.pipeline or "default"
            if pipeline not in by_pipeline:
                by_pipeline[pipeline] = []
            by_pipeline[pipeline].append(note)

        # Fit per-pipeline calibrators
        for pipeline, pipeline_notes in by_pipeline.items():
            if len(pipeline_notes) >= min_samples_per_pipeline:
                if self.calibrator_type == "isotonic":
                    calibrator = IsotonicCalibrator()
                elif self.calibrator_type == "platt":
                    calibrator = PlattCalibrator()
                else:
                    calibrator = TemperatureCalibrator()

                calibrator.fit_from_notes(pipeline_notes)
                self.calibrators[pipeline] = calibrator
                logger.info(f"Fitted calibrator for pipeline '{pipeline}' ({len(pipeline_notes)} samples)")

        # Fit default calibrator on all data
        if self.calibrator_type == "isotonic":
            self.default_calibrator = IsotonicCalibrator()
        elif self.calibrator_type == "platt":
            self.default_calibrator = PlattCalibrator()
        else:
            self.default_calibrator = TemperatureCalibrator()

        self.default_calibrator.fit_from_notes(notes)

    def calibrate(
        self,
        confidence: float,
        pipeline: str = "",
    ) -> float:
        """Calibrate a single confidence score.

        Args:
            confidence: Original confidence
            pipeline: Pipeline name

        Returns:
            Calibrated confidence
        """
        calibrator = self.calibrators.get(pipeline, self.default_calibrator)
        if calibrator is None:
            return confidence

        result = calibrator.calibrate(np.array([confidence]))
        return float(result[0])

    def calibrate_batch(
        self,
        confidences: np.ndarray,
        pipelines: List[str],
    ) -> np.ndarray:
        """Calibrate batch of confidence scores.

        Args:
            confidences: Original confidences
            pipelines: Pipeline names for each confidence

        Returns:
            Calibrated confidences
        """
        result = np.zeros_like(confidences)
        for i, (conf, pipeline) in enumerate(zip(confidences, pipelines)):
            result[i] = self.calibrate(conf, pipeline)
        return result

    def save(self, directory: Path) -> None:
        """Save all calibrators to directory."""
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)

        # Save metadata
        metadata = {
            'calibrator_type': self.calibrator_type,
            'pipelines': list(self.calibrators.keys()),
        }
        with open(directory / 'metadata.json', 'w') as f:
            json.dump(metadata, f)

        # Save per-pipeline calibrators
        for pipeline, calibrator in self.calibrators.items():
            safe_name = pipeline.replace('/', '_')
            ext = '.pkl' if isinstance(calibrator, IsotonicCalibrator) else '.json'
            calibrator.save(directory / f'{safe_name}{ext}')

        # Save default calibrator
        if self.default_calibrator:
            ext = '.pkl' if isinstance(self.default_calibrator, IsotonicCalibrator) else '.json'
            self.default_calibrator.save(directory / f'default{ext}')

        logger.info(f"Saved per-pipeline calibrator to {directory}")

    @classmethod
    def load(cls, directory: Path) -> "PerPipelineCalibrator":
        """Load from directory."""
        directory = Path(directory)

        with open(directory / 'metadata.json', 'r') as f:
            metadata = json.load(f)

        calibrator = cls(calibrator_type=metadata['calibrator_type'])

        # Load per-pipeline calibrators
        for pipeline in metadata['pipelines']:
            safe_name = pipeline.replace('/', '_')
            pkl_path = directory / f'{safe_name}.pkl'
            json_path = directory / f'{safe_name}.json'

            if pkl_path.exists():
                calibrator.calibrators[pipeline] = IsotonicCalibrator.load(pkl_path)
            elif json_path.exists():
                if metadata['calibrator_type'] == 'platt':
                    calibrator.calibrators[pipeline] = PlattCalibrator.load(json_path)
                else:
                    calibrator.calibrators[pipeline] = TemperatureCalibrator.load(json_path)

        # Load default
        if (directory / 'default.pkl').exists():
            calibrator.default_calibrator = IsotonicCalibrator.load(directory / 'default.pkl')
        elif (directory / 'default.json').exists():
            if metadata['calibrator_type'] == 'platt':
                calibrator.default_calibrator = PlattCalibrator.load(directory / 'default.json')
            else:
                calibrator.default_calibrator = TemperatureCalibrator.load(directory / 'default.json')

        return calibrator


def calibrate_notes(
    notes: List[Any],
    calibrator: CalibrationAdjuster,
) -> List[Any]:
    """Apply calibration to note confidence scores.

    Args:
        notes: Notes with confidence attribute
        calibrator: Calibrator to use

    Returns:
        Notes with updated confidence (modified in place and returned)
    """
    confidences = np.array([getattr(n, 'confidence', 0.5) for n in notes])
    calibrated = calibrator.calibrate(confidences)

    for note, cal_conf in zip(notes, calibrated):
        if hasattr(note, 'confidence'):
            note.confidence = float(cal_conf)

    return notes


def evaluate_calibration_improvement(
    notes: List[NoteWithConfidence],
    calibrator: CalibrationAdjuster,
) -> Dict[str, float]:
    """Evaluate how much calibration improves ECE.

    Args:
        notes: Notes with confidence and correctness
        calibrator: Calibrator to evaluate

    Returns:
        Dictionary with before/after metrics
    """
    analyzer = CalibrationAnalyzer()

    # Before calibration
    before = analyzer.analyze(notes)

    # After calibration
    confidences = np.array([n.confidence for n in notes])
    calibrated = calibrator.calibrate(confidences)

    calibrated_notes = [
        NoteWithConfidence(
            pitch=n.pitch,
            start=n.start,
            end=n.end,
            confidence=float(c),
            is_correct=n.is_correct,
            pipeline=n.pipeline,
            stem_type=n.stem_type,
        )
        for n, c in zip(notes, calibrated)
    ]
    after = analyzer.analyze(calibrated_notes)

    return {
        'ece_before': before.expected_calibration_error,
        'ece_after': after.expected_calibration_error,
        'ece_improvement': before.expected_calibration_error - after.expected_calibration_error,
        'mce_before': before.maximum_calibration_error,
        'mce_after': after.maximum_calibration_error,
        'brier_before': before.brier_score,
        'brier_after': after.brier_score,
    }
