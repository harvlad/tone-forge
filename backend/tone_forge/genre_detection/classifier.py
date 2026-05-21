"""Multi-label genre classification from audio.

Classifies audio into one or more genre categories using
audio features and optional ML models. Used for:
- Selecting appropriate production archetypes
- Generating genre-specific tweak hints
- Improving block recommendations

Falls back to rule-based classification when ML models
aren't available.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

import numpy as np

logger = logging.getLogger(__name__)

# Model paths
DEFAULT_MODEL_DIR = Path.home() / ".toneforge" / "models" / "genre"


# Genre taxonomy
GENRES = [
    "rock",
    "metal",
    "blues",
    "jazz",
    "country",
    "funk",
    "pop",
    "indie",
    "ambient",
    "electronic",
    "punk",
    "progressive",
]

# Subgenres/styles for more specific classification
SUBGENRES = {
    "rock": ["classic_rock", "hard_rock", "alternative", "grunge"],
    "metal": ["80s_metal", "thrash", "modern_metal", "djent", "doom"],
    "blues": ["blues", "blues_rock"],
    "jazz": ["jazz", "fusion"],
    "country": ["country", "country_rock"],
    "funk": ["funk", "r&b"],
    "pop": ["pop", "pop_rock"],
    "indie": ["indie_rock", "shoegaze", "dream_pop"],
    "ambient": ["ambient", "post_rock"],
    "electronic": ["synthwave", "edm", "industrial"],
    "punk": ["punk", "hardcore"],
    "progressive": ["prog_rock", "prog_metal"],
}


@dataclass
class GenreFeatures:
    """Audio features used for genre classification."""

    # Spectral features
    spectral_centroid: float = 0.0
    spectral_bandwidth: float = 0.0
    spectral_rolloff: float = 0.0
    spectral_flatness: float = 0.0

    # Frequency band energies (normalized)
    sub_bass_energy: float = 0.0     # 20-60 Hz
    bass_energy: float = 0.0         # 60-250 Hz
    low_mid_energy: float = 0.0      # 250-500 Hz
    mid_energy: float = 0.0          # 500-2000 Hz
    high_mid_energy: float = 0.0     # 2000-4000 Hz
    high_energy: float = 0.0         # 4000-8000 Hz
    brilliance_energy: float = 0.0   # 8000+ Hz

    # Temporal features
    tempo_bpm: float = 120.0
    tempo_stability: float = 0.5     # How consistent the tempo is
    beat_strength: float = 0.5       # Strength of beat detection

    # Dynamics
    rms_mean: float = 0.0
    rms_std: float = 0.0
    dynamic_range: float = 0.0
    crest_factor: float = 0.0

    # Timbral
    harmonic_ratio: float = 0.5      # Harmonic vs percussive
    zero_crossing_rate: float = 0.0

    def to_array(self) -> np.ndarray:
        """Convert to feature array for ML model."""
        return np.array([
            self.spectral_centroid / 8000,  # Normalize
            self.spectral_bandwidth / 4000,
            self.spectral_rolloff / 12000,
            self.spectral_flatness,
            self.sub_bass_energy,
            self.bass_energy,
            self.low_mid_energy,
            self.mid_energy,
            self.high_mid_energy,
            self.high_energy,
            self.brilliance_energy,
            self.tempo_bpm / 200,
            self.tempo_stability,
            self.beat_strength,
            self.rms_mean,
            self.rms_std,
            self.dynamic_range,
            self.crest_factor / 20,
            self.harmonic_ratio,
            self.zero_crossing_rate,
        ], dtype=np.float32)

    @classmethod
    def num_features(cls) -> int:
        """Number of features."""
        return 20


@dataclass
class GenrePrediction:
    """Genre classification result."""

    primary_genre: str
    primary_confidence: float

    secondary_genres: List[Tuple[str, float]] = field(default_factory=list)

    # Subgenre predictions
    primary_subgenre: Optional[str] = None
    subgenre_confidence: float = 0.0

    # Feature-based indicators
    is_distorted: bool = False
    is_clean: bool = False
    is_ambient: bool = False
    is_aggressive: bool = False

    # Production era hint
    production_era: str = "modern"  # "vintage", "80s", "90s", "modern"


class GenreClassifier:
    """Multi-label genre classifier.

    Uses audio features to classify into genre categories.
    Falls back to rule-based classification when models aren't available.
    """

    def __init__(
        self,
        model_dir: Optional[Path] = None,
        use_ml: bool = True,
    ):
        """Initialize the genre classifier.

        Args:
            model_dir: Directory containing trained models
            use_ml: Whether to use ML models (vs pure heuristics)
        """
        self.model_dir = model_dir or DEFAULT_MODEL_DIR
        self.use_ml = use_ml
        self._model = None
        self._model_loaded = False

        if use_ml:
            self._try_load_model()

    def _try_load_model(self) -> bool:
        """Try to load the classification model."""
        model_path = self.model_dir / "genre_classifier.lgb"

        if not model_path.exists():
            logger.debug("No genre classifier model found at %s", model_path)
            return False

        try:
            import lightgbm as lgb
            self._model = lgb.Booster(model_file=str(model_path))
            self._model_loaded = True
            logger.info("Loaded genre classifier from %s", model_path)
            return True
        except ImportError:
            logger.debug("LightGBM not available, using heuristic classification")
            return False
        except Exception as e:
            logger.warning("Failed to load genre classifier: %s", e)
            return False

    def is_ml_ready(self) -> bool:
        """Check if ML model is loaded."""
        return self._model_loaded and self._model is not None

    def extract_features(
        self,
        audio: np.ndarray,
        sr: int = 22050,
    ) -> GenreFeatures:
        """Extract features from audio for genre classification.

        Args:
            audio: Audio time series
            sr: Sample rate

        Returns:
            GenreFeatures with extracted values
        """
        try:
            import librosa

            features = GenreFeatures()

            # Ensure audio is valid
            if len(audio) == 0:
                return features

            if not np.all(np.isfinite(audio)):
                audio = np.nan_to_num(audio, nan=0.0)

            # Spectral features
            spec_cent = librosa.feature.spectral_centroid(y=audio, sr=sr)[0]
            features.spectral_centroid = float(np.mean(spec_cent))

            spec_bw = librosa.feature.spectral_bandwidth(y=audio, sr=sr)[0]
            features.spectral_bandwidth = float(np.mean(spec_bw))

            spec_roll = librosa.feature.spectral_rolloff(y=audio, sr=sr)[0]
            features.spectral_rolloff = float(np.mean(spec_roll))

            spec_flat = librosa.feature.spectral_flatness(y=audio)[0]
            features.spectral_flatness = float(np.mean(spec_flat))

            # Band energies
            spec = np.abs(librosa.stft(audio))
            freqs = librosa.fft_frequencies(sr=sr)

            def band_energy(spec, freqs, low, high):
                mask = (freqs >= low) & (freqs < high)
                if np.any(mask):
                    return float(np.mean(spec[mask, :]))
                return 0.0

            total_energy = np.sum(spec) + 1e-10

            features.sub_bass_energy = band_energy(spec, freqs, 20, 60) / total_energy
            features.bass_energy = band_energy(spec, freqs, 60, 250) / total_energy
            features.low_mid_energy = band_energy(spec, freqs, 250, 500) / total_energy
            features.mid_energy = band_energy(spec, freqs, 500, 2000) / total_energy
            features.high_mid_energy = band_energy(spec, freqs, 2000, 4000) / total_energy
            features.high_energy = band_energy(spec, freqs, 4000, 8000) / total_energy
            features.brilliance_energy = band_energy(spec, freqs, 8000, sr/2) / total_energy

            # Tempo
            tempo, beat_frames = librosa.beat.beat_track(y=audio, sr=sr)
            if hasattr(tempo, '__iter__'):
                tempo = float(tempo[0]) if len(tempo) > 0 else 120.0
            features.tempo_bpm = float(tempo)

            # Beat strength
            onset_env = librosa.onset.onset_strength(y=audio, sr=sr)
            features.beat_strength = float(np.std(onset_env))

            # Dynamics
            rms = librosa.feature.rms(y=audio)[0]
            features.rms_mean = float(np.mean(rms))
            features.rms_std = float(np.std(rms))

            if rms.max() > 0 and rms.min() > 0:
                features.dynamic_range = float(20 * np.log10(rms.max() / rms.min()))

            # Crest factor
            peak = np.max(np.abs(audio))
            rms_val = np.sqrt(np.mean(audio ** 2))
            if rms_val > 0:
                features.crest_factor = float(20 * np.log10(peak / rms_val))

            # Harmonic ratio
            harmonic, percussive = librosa.effects.hpss(audio)
            h_energy = np.sum(harmonic ** 2)
            p_energy = np.sum(percussive ** 2)
            total = h_energy + p_energy
            if total > 0:
                features.harmonic_ratio = float(h_energy / total)

            # Zero crossing rate
            zcr = librosa.feature.zero_crossing_rate(audio)[0]
            features.zero_crossing_rate = float(np.mean(zcr))

        except Exception as e:
            logger.warning("Feature extraction failed: %s", e)

        return features

    def classify(
        self,
        audio: np.ndarray,
        sr: int = 22050,
        top_k: int = 3,
    ) -> GenrePrediction:
        """Classify audio into genres.

        Args:
            audio: Audio time series
            sr: Sample rate
            top_k: Number of top genres to return

        Returns:
            GenrePrediction with classification results
        """
        features = self.extract_features(audio, sr)

        if self.is_ml_ready():
            prediction = self._classify_ml(features, top_k)
        else:
            prediction = self._classify_heuristic(features, top_k)

        return prediction

    def _classify_ml(
        self,
        features: GenreFeatures,
        top_k: int,
    ) -> GenrePrediction:
        """Classify using ML model."""
        feature_array = features.to_array().reshape(1, -1)
        probs = self._model.predict(feature_array)[0]

        # Map probabilities to genres
        genre_probs = list(zip(GENRES, probs))
        genre_probs.sort(key=lambda x: x[1], reverse=True)

        primary = genre_probs[0]
        secondary = genre_probs[1:top_k]

        return GenrePrediction(
            primary_genre=primary[0],
            primary_confidence=float(primary[1]),
            secondary_genres=[(g, float(p)) for g, p in secondary],
        )

    def _classify_heuristic(
        self,
        features: GenreFeatures,
        top_k: int,
    ) -> GenrePrediction:
        """Classify using heuristics based on audio features."""
        scores = {}

        # Base scores
        for genre in GENRES:
            scores[genre] = 0.1  # Base probability

        # Distortion indicators
        is_distorted = (
            features.spectral_flatness > 0.1 and
            features.high_mid_energy > 0.1
        )
        is_clean = features.spectral_flatness < 0.05

        # Rock: moderate tempo, mid-focused, some distortion
        if 100 < features.tempo_bpm < 140:
            scores["rock"] += 0.2
        if features.mid_energy > 0.2:
            scores["rock"] += 0.1
        if is_distorted:
            scores["rock"] += 0.1

        # Metal: high distortion, fast or heavy tempo, scooped mids
        if is_distorted:
            scores["metal"] += 0.3
        if features.bass_energy > 0.15:
            scores["metal"] += 0.1
        if features.tempo_bpm > 140 or features.tempo_bpm < 80:
            scores["metal"] += 0.1

        # Blues: clean to light crunch, slow-moderate tempo
        if is_clean or features.spectral_flatness < 0.08:
            scores["blues"] += 0.2
        if 70 < features.tempo_bpm < 120:
            scores["blues"] += 0.1
        if features.harmonic_ratio > 0.6:
            scores["blues"] += 0.1

        # Jazz: clean, complex harmonics, moderate tempo
        if is_clean:
            scores["jazz"] += 0.2
        if features.harmonic_ratio > 0.7:
            scores["jazz"] += 0.2
        if 80 < features.tempo_bpm < 160:
            scores["jazz"] += 0.1

        # Country: clean, bright, moderate tempo
        if is_clean:
            scores["country"] += 0.2
        if features.brilliance_energy > 0.05:
            scores["country"] += 0.1
        if 90 < features.tempo_bpm < 140:
            scores["country"] += 0.1

        # Funk: strong beat, mid-focused, moderate tempo
        if features.beat_strength > 0.3:
            scores["funk"] += 0.2
        if features.mid_energy > 0.2:
            scores["funk"] += 0.1
        if 90 < features.tempo_bpm < 130:
            scores["funk"] += 0.1

        # Pop: clean to moderate, strong beat, typical tempo
        if 100 < features.tempo_bpm < 140:
            scores["pop"] += 0.2
        if features.beat_strength > 0.25:
            scores["pop"] += 0.1

        # Indie: varied, often clean with effects
        if features.harmonic_ratio > 0.5:
            scores["indie"] += 0.1
        if 80 < features.tempo_bpm < 140:
            scores["indie"] += 0.1

        # Ambient: slow, sustained, high harmonic ratio
        if features.tempo_bpm < 100:
            scores["ambient"] += 0.2
        if features.harmonic_ratio > 0.7:
            scores["ambient"] += 0.2
        if features.dynamic_range < 20:
            scores["ambient"] += 0.1

        # Electronic: heavy bass, steady tempo, processed sound
        if features.sub_bass_energy > 0.1 or features.bass_energy > 0.2:
            scores["electronic"] += 0.2
        if features.spectral_flatness > 0.05:
            scores["electronic"] += 0.1
        if 100 < features.tempo_bpm < 160:
            scores["electronic"] += 0.1

        # Punk: fast, aggressive, raw
        if features.tempo_bpm > 140:
            scores["punk"] += 0.2
        if is_distorted:
            scores["punk"] += 0.1
        if features.dynamic_range > 25:
            scores["punk"] += 0.1

        # Progressive: varied tempo, complex
        if features.harmonic_ratio > 0.6:
            scores["progressive"] += 0.1

        # Normalize scores to probabilities
        total = sum(scores.values())
        probs = {g: s / total for g, s in scores.items()}

        # Sort by probability
        sorted_genres = sorted(probs.items(), key=lambda x: x[1], reverse=True)

        primary = sorted_genres[0]
        secondary = sorted_genres[1:top_k]

        # Determine subgenre
        primary_subgenre = self._detect_subgenre(features, primary[0])

        # Determine production era
        production_era = self._detect_era(features)

        return GenrePrediction(
            primary_genre=primary[0],
            primary_confidence=primary[1],
            secondary_genres=list(secondary),
            primary_subgenre=primary_subgenre,
            subgenre_confidence=0.6,
            is_distorted=is_distorted,
            is_clean=is_clean,
            is_ambient=features.tempo_bpm < 100 and features.harmonic_ratio > 0.7,
            is_aggressive=is_distorted and features.tempo_bpm > 140,
            production_era=production_era,
        )

    def _detect_subgenre(
        self,
        features: GenreFeatures,
        primary_genre: str,
    ) -> Optional[str]:
        """Detect subgenre based on features and primary genre."""
        if primary_genre not in SUBGENRES:
            return None

        subgenres = SUBGENRES[primary_genre]

        if primary_genre == "metal":
            if features.tempo_bpm < 80:
                return "doom"
            elif features.tempo_bpm > 160:
                return "thrash"
            elif features.bass_energy > 0.2:
                return "modern_metal"
            else:
                return "80s_metal"

        elif primary_genre == "electronic":
            if features.bass_energy > 0.2 and 80 < features.tempo_bpm < 130:
                return "synthwave"
            else:
                return "edm"

        elif primary_genre == "indie":
            if features.harmonic_ratio > 0.75:
                return "shoegaze"
            else:
                return "indie_rock"

        return subgenres[0] if subgenres else None

    def _detect_era(
        self,
        features: GenreFeatures,
    ) -> str:
        """Detect production era from audio characteristics."""
        # Vintage: lower bandwidth, warmer
        if features.spectral_rolloff < 6000 and features.brilliance_energy < 0.03:
            return "vintage"

        # 80s: characteristic brightness and compression
        if features.dynamic_range < 15 and features.brilliance_energy > 0.05:
            return "80s"

        # 90s: scooped mids, heavy compression
        if features.mid_energy < 0.15 and features.dynamic_range < 18:
            return "90s"

        return "modern"


# Module-level singleton
_classifier: Optional[GenreClassifier] = None


def get_classifier(
    model_dir: Optional[Path] = None,
    use_ml: bool = True,
) -> GenreClassifier:
    """Get or create the global GenreClassifier instance."""
    global _classifier

    if _classifier is None:
        _classifier = GenreClassifier(model_dir=model_dir, use_ml=use_ml)

    return _classifier


def classify_genre(
    audio: np.ndarray,
    sr: int = 22050,
    top_k: int = 3,
) -> GenrePrediction:
    """Classify audio genre using the global classifier."""
    return get_classifier().classify(audio, sr, top_k)


def extract_genre_features(
    audio: np.ndarray,
    sr: int = 22050,
) -> GenreFeatures:
    """Extract genre features from audio."""
    return get_classifier().extract_features(audio, sr)
