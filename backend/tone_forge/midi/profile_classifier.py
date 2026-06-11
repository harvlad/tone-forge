"""Automatic profile classification for MIDI extraction.

Classifies audio to select the optimal extraction profile based on
DSP features like onset density, sustain ratio, and polyphony.

This enables adaptive extraction without requiring manual profile selection.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from .profiles import (
    ExtractionProfile,
    get_profile_registry,
    get_profile,
)

logger = logging.getLogger(__name__)

# Optional imports
try:
    import librosa
    _LIBROSA_AVAILABLE = True
except ImportError:
    _LIBROSA_AVAILABLE = False


@dataclass
class ClassificationFeatures:
    """Features extracted for profile classification.

    These features map directly to profile bounds (onset_density, sustain_ratio,
    polyphony) enabling automatic profile selection.
    """

    # Temporal features
    onset_density: float = 0.0  # Onsets per second
    transient_sharpness: float = 0.0  # Attack envelope steepness (0-1)
    sustain_ratio: float = 0.0  # Sustain vs total duration (0-1)
    note_repetition_density: float = 0.0  # Same-pitch notes per second

    # Spectral features
    spectral_flux_mean: float = 0.0  # Spectral change over time
    spectral_flux_std: float = 0.0
    harmonic_stability: float = 0.0  # Pitch stability over time (0-1)

    # Polyphony features
    polyphony_estimate: float = 1.0  # Average simultaneous notes
    polyphony_max: int = 1  # Maximum simultaneous notes

    # Energy features
    dynamic_range: float = 0.0  # RMS std / mean (normalized)
    low_freq_ratio: float = 0.0  # Energy below 250Hz

    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            "onset_density": self.onset_density,
            "transient_sharpness": self.transient_sharpness,
            "sustain_ratio": self.sustain_ratio,
            "note_repetition_density": self.note_repetition_density,
            "spectral_flux_mean": self.spectral_flux_mean,
            "spectral_flux_std": self.spectral_flux_std,
            "harmonic_stability": self.harmonic_stability,
            "polyphony_estimate": self.polyphony_estimate,
            "polyphony_max": self.polyphony_max,
            "dynamic_range": self.dynamic_range,
            "low_freq_ratio": self.low_freq_ratio,
        }


@dataclass
class ProfileClassification:
    """Result of profile classification."""

    profile_name: str  # Selected profile name
    confidence: float  # Classification confidence (0-1)
    features: ClassificationFeatures  # Extracted features
    candidate_profiles: List[Tuple[str, float]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            "profile_name": self.profile_name,
            "confidence": self.confidence,
            "features": self.features.to_dict(),
            "candidates": [
                {"profile": name, "score": score}
                for name, score in self.candidate_profiles
            ],
            "warnings": self.warnings,
        }


class ProfileClassifier:
    """Classify audio to select optimal MIDI extraction profile.

    Uses lightweight DSP features to match audio characteristics
    to profile bounds defined in profiles.py.
    """

    def __init__(
        self,
        hop_length: int = 512,
        n_fft: int = 2048,
    ):
        """Initialize the classifier.

        Args:
            hop_length: Hop length for analysis
            n_fft: FFT size
        """
        self.hop_length = hop_length
        self.n_fft = n_fft

    def extract_features(
        self,
        audio: np.ndarray,
        sr: int,
    ) -> ClassificationFeatures:
        """Extract classification features from audio.

        Args:
            audio: Audio data (mono or stereo)
            sr: Sample rate

        Returns:
            ClassificationFeatures for profile matching
        """
        if not _LIBROSA_AVAILABLE:
            logger.warning("librosa not available, returning default features")
            return ClassificationFeatures()

        # Convert to mono
        if audio.ndim == 2:
            audio_mono = np.mean(audio, axis=0)
        else:
            audio_mono = audio

        features = ClassificationFeatures()
        duration = len(audio_mono) / sr

        if duration <= 0:
            return features

        # Onset detection
        onset_env = librosa.onset.onset_strength(
            y=audio_mono, sr=sr, hop_length=self.hop_length
        )
        onset_frames = librosa.onset.onset_detect(
            onset_envelope=onset_env, sr=sr, hop_length=self.hop_length
        )

        # Onset density (onsets per second)
        features.onset_density = len(onset_frames) / duration

        # Transient sharpness from onset envelope
        if len(onset_env) > 0:
            # Look at onset envelope peaks
            onset_peaks = onset_env[onset_frames] if len(onset_frames) > 0 else onset_env
            # Sharpness is how peaked the onsets are vs mean
            if np.mean(onset_env) > 0:
                peak_ratio = np.mean(onset_peaks) / np.mean(onset_env)
                features.transient_sharpness = min(1.0, (peak_ratio - 1) / 5)  # Normalize
            else:
                features.transient_sharpness = 0.0

        # Spectral flux (change over time)
        spec = np.abs(librosa.stft(audio_mono, n_fft=self.n_fft, hop_length=self.hop_length))
        spectral_flux = np.sqrt(np.sum(np.diff(spec, axis=1) ** 2, axis=0))
        features.spectral_flux_mean = float(np.mean(spectral_flux))
        features.spectral_flux_std = float(np.std(spectral_flux))

        # Frequency band energy
        freqs = librosa.fft_frequencies(sr=sr, n_fft=self.n_fft)
        total_energy = np.sum(spec ** 2)
        if total_energy > 0:
            low_mask = freqs < 250
            features.low_freq_ratio = float(np.sum(spec[low_mask, :] ** 2) / total_energy)

        # RMS for sustain and dynamic range
        rms = librosa.feature.rms(y=audio_mono, hop_length=self.hop_length)[0]
        features.dynamic_range = float(np.std(rms) / (np.mean(rms) + 1e-10))

        # Sustain ratio: frames above threshold vs total
        rms_threshold = np.percentile(rms, 75) * 0.5
        sustained_frames = np.sum(rms > rms_threshold)
        features.sustain_ratio = float(sustained_frames / len(rms)) if len(rms) > 0 else 0.5

        # Pitch tracking for harmonic stability and polyphony
        try:
            f0, voiced_flag, voiced_probs = librosa.pyin(
                audio_mono,
                fmin=librosa.note_to_hz('C1'),
                fmax=librosa.note_to_hz('C7'),
                sr=sr,
                hop_length=self.hop_length,
            )
            f0_valid = f0[~np.isnan(f0)]

            if len(f0_valid) > 0:
                # Harmonic stability: inverse of coefficient of variation
                if np.mean(f0_valid) > 0:
                    cv = np.std(f0_valid) / np.mean(f0_valid)
                    features.harmonic_stability = float(1 / (1 + cv))
                else:
                    features.harmonic_stability = 0.0

                # Note repetition: estimate from pitch contour
                # Count pitch "resets" - where pitch changes then returns
                midi_pitches = librosa.hz_to_midi(f0_valid)
                midi_quantized = np.round(midi_pitches).astype(int)
                pitch_changes = np.diff(midi_quantized)
                # A repetition is same pitch appearing again after different pitches
                repetitions = np.sum(
                    (pitch_changes[:-1] != 0) & (pitch_changes[1:] != 0) &
                    (midi_quantized[:-2] == midi_quantized[2:])
                ) if len(pitch_changes) > 1 else 0
                features.note_repetition_density = float(repetitions / duration)

        except Exception as e:
            logger.debug(f"Pitch tracking failed: {e}")
            features.harmonic_stability = 0.5
            features.note_repetition_density = 0.0

        # Polyphony estimation from chroma
        try:
            chroma = librosa.feature.chroma_stft(
                y=audio_mono, sr=sr, hop_length=self.hop_length
            )
            # Count chroma bins above threshold per frame
            chroma_threshold = 0.3
            active_per_frame = np.sum(chroma > chroma_threshold, axis=0)
            features.polyphony_estimate = float(np.mean(active_per_frame))
            features.polyphony_max = int(np.max(active_per_frame))
        except Exception as e:
            logger.debug(f"Chroma analysis failed: {e}")
            features.polyphony_estimate = 1.0
            features.polyphony_max = 1

        return features

    def classify(
        self,
        audio: np.ndarray,
        sr: int,
        stem_type: Optional[str] = None,
    ) -> ProfileClassification:
        """Classify audio to select optimal extraction profile.

        Args:
            audio: Audio data
            sr: Sample rate
            stem_type: Optional stem type hint (bass, lead, synth, pad)

        Returns:
            ProfileClassification with selected profile and confidence
        """
        features = self.extract_features(audio, sr)
        warnings = []

        # Get all profiles for the stem type, or all profiles if no hint
        registry = get_profile_registry()
        if stem_type:
            candidate_profiles = registry.get_profiles_for_stem(stem_type)
            if not candidate_profiles:
                # Fall back to all profiles
                candidate_profiles = [
                    registry.get(name) for name in registry.list_profiles()
                    if registry.get(name) is not None
                ]
                warnings.append(f"No profiles for stem_type '{stem_type}', using all")
        else:
            candidate_profiles = [
                registry.get(name) for name in registry.list_profiles()
                if registry.get(name) is not None
            ]

        # Score each profile based on feature matching
        profile_scores: List[Tuple[str, float]] = []

        for profile in candidate_profiles:
            score = self._score_profile(profile, features)
            profile_scores.append((profile.name, score))

        # Sort by score descending
        profile_scores.sort(key=lambda x: x[1], reverse=True)

        if not profile_scores:
            # Fallback to default
            default_name = "lead_legato"
            if stem_type:
                default_profile = registry.get_default_for_stem(stem_type)
                if default_profile:
                    default_name = default_profile.name
            warnings.append("No profiles scored, using default")
            return ProfileClassification(
                profile_name=default_name,
                confidence=0.0,
                features=features,
                candidate_profiles=[],
                warnings=warnings,
            )

        best_name, best_score = profile_scores[0]

        # Confidence based on score and separation from second best
        if len(profile_scores) > 1:
            second_score = profile_scores[1][1]
            separation = best_score - second_score
            # Higher separation = higher confidence
            confidence = min(1.0, best_score * 0.5 + separation * 0.5)
        else:
            confidence = best_score

        return ProfileClassification(
            profile_name=best_name,
            confidence=confidence,
            features=features,
            candidate_profiles=profile_scores[:5],  # Top 5
            warnings=warnings,
        )

    def _score_profile(
        self,
        profile: ExtractionProfile,
        features: ClassificationFeatures,
    ) -> float:
        """Score how well features match a profile's bounds.

        Args:
            profile: Profile to score against
            features: Extracted features

        Returns:
            Score from 0-1 (1 = perfect match)
        """
        score = 1.0
        matches = 0
        total_checks = 0

        # Onset density bounds
        if profile.min_onset_density > 0 or profile.max_onset_density < float('inf'):
            total_checks += 1
            if profile.min_onset_density <= features.onset_density <= profile.max_onset_density:
                matches += 1
            else:
                # Partial credit for being close
                if features.onset_density < profile.min_onset_density:
                    distance = profile.min_onset_density - features.onset_density
                    score *= max(0.3, 1 - distance / profile.min_onset_density)
                elif features.onset_density > profile.max_onset_density:
                    distance = features.onset_density - profile.max_onset_density
                    score *= max(0.3, 1 - distance / (features.onset_density + 1))

        # Sustain ratio bounds
        if profile.min_sustain_ratio > 0 or profile.max_sustain_ratio < 1:
            total_checks += 1
            if profile.min_sustain_ratio <= features.sustain_ratio <= profile.max_sustain_ratio:
                matches += 1
            else:
                # Partial credit
                if features.sustain_ratio < profile.min_sustain_ratio:
                    distance = profile.min_sustain_ratio - features.sustain_ratio
                    score *= max(0.3, 1 - distance)
                elif features.sustain_ratio > profile.max_sustain_ratio:
                    distance = features.sustain_ratio - profile.max_sustain_ratio
                    score *= max(0.3, 1 - distance)

        # Polyphony bounds
        poly_min, poly_max = profile.polyphony_range
        if poly_min > 0 or poly_max < float('inf'):
            total_checks += 1
            if poly_min <= features.polyphony_estimate <= poly_max:
                matches += 1
            else:
                # Partial credit
                if features.polyphony_estimate < poly_min:
                    distance = poly_min - features.polyphony_estimate
                    score *= max(0.3, 1 - distance / (poly_min + 1))
                elif features.polyphony_estimate > poly_max:
                    distance = features.polyphony_estimate - poly_max
                    score *= max(0.3, 1 - distance / (features.polyphony_estimate + 1))

        # Additional heuristics based on profile characteristics

        # Staccato profiles benefit from high transient sharpness
        if "staccato" in profile.name or profile.merge_max_gap == 0:
            if features.transient_sharpness > 0.5:
                score *= 1.1
            elif features.transient_sharpness < 0.2:
                score *= 0.8

        # Legato/sustained profiles benefit from high sustain
        if "legato" in profile.name or "sustained" in profile.name or "drone" in profile.name:
            if features.sustain_ratio > 0.5:
                score *= 1.1
            elif features.sustain_ratio < 0.3:
                score *= 0.8

        # Fast profiles (arp, pluck) benefit from high onset density
        if "fast" in profile.name or "pluck" in profile.name:
            if features.onset_density > 4:
                score *= 1.1
            elif features.onset_density < 2:
                score *= 0.8

        # Bass profiles benefit from low frequency energy
        if "bass" in profile.name:
            if features.low_freq_ratio > 0.3:
                score *= 1.15
            elif features.low_freq_ratio < 0.15:
                score *= 0.7

        # Harmonic stability affects profiles differently
        if profile.enable_harmonic_suppression:
            # Profiles that suppress harmonics expect complex harmonic content
            if features.harmonic_stability < 0.5:
                score *= 1.05

        # Clamp score to 0-1
        return max(0.0, min(1.0, score))


# Module-level singleton
_classifier: Optional[ProfileClassifier] = None


def get_profile_classifier() -> ProfileClassifier:
    """Get the global profile classifier instance."""
    global _classifier
    if _classifier is None:
        _classifier = ProfileClassifier()
    return _classifier


def classify_profile(
    audio: np.ndarray,
    sr: int,
    stem_type: Optional[str] = None,
) -> ProfileClassification:
    """Convenience function to classify profile for audio.

    Args:
        audio: Audio data
        sr: Sample rate
        stem_type: Optional stem type hint

    Returns:
        ProfileClassification with selected profile
    """
    classifier = get_profile_classifier()
    return classifier.classify(audio, sr, stem_type)


def classify_profile_from_role(
    role_classification,
    features: Optional[ClassificationFeatures] = None,
) -> str:
    """Map a RoleClassification to an extraction profile name.

    Uses the musical role and its features to select the most
    appropriate extraction profile.

    Args:
        role_classification: RoleClassification from role_classifier
        features: Optional pre-computed ClassificationFeatures

    Returns:
        Profile name string
    """
    # Import here to avoid circular dependency
    from ..reconstruction.role_classifier import MusicalRole

    role = role_classification.primary_role
    role_features = role_classification.features

    # Map roles to default profiles
    role_to_profile = {
        MusicalRole.BASS_FOUNDATION: "mono_bass",
        MusicalRole.LEAD_MELODY: "lead_legato",  # Default, may override
        MusicalRole.PAD_ATMOSPHERE: "pad_sustained",
        MusicalRole.ARP_RHYTHM: "arp_fast",
        MusicalRole.TEXTURE_LAYER: "pad_sustained",
        MusicalRole.TRANSIENT_FX: "pluck_transient",
        MusicalRole.RHYTHMIC_ELEMENT: "arp_fast",
        MusicalRole.DRUMS: "drums",  # Drum/percussive content
        MusicalRole.UNKNOWN: "lead_legato",
    }

    profile_name = role_to_profile.get(role, "lead_legato")

    # Refine based on features if available
    if role_features:
        # Lead melody: distinguish staccato vs legato
        if role == MusicalRole.LEAD_MELODY:
            if role_features.onset_rate > 3.0 and role_features.sustain_ratio < 0.4:
                profile_name = "lead_staccato"
            elif role_features.sustain_ratio > 0.5:
                profile_name = "lead_legato"

        # Bass: distinguish mono vs poly
        elif role == MusicalRole.BASS_FOUNDATION:
            # Use polyphony hint from chromagram entropy
            if role_features.chromagram_entropy > 0.6:
                profile_name = "poly_bass"
            else:
                profile_name = "mono_bass"

        # Pad: distinguish drone vs sustained
        elif role == MusicalRole.PAD_ATMOSPHERE:
            if role_features.onset_rate < 0.5 and role_features.sustain_ratio > 0.7:
                profile_name = "drone"
            else:
                profile_name = "pad_sustained"

    # Also consider classification features if provided
    if features:
        if profile_name == "lead_legato" and features.onset_density > 4.0:
            profile_name = "lead_staccato"
        elif profile_name == "mono_bass" and features.polyphony_estimate > 1.5:
            profile_name = "poly_bass"

    return profile_name
