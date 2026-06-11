"""Role classification for musical content.

Classifies audio by musical role rather than instrument type.
This allows for better extraction parameter adaptation since
the same instrument can play different roles (bass as foundation
vs bass as lead melody).

Musical Roles:
- bass_foundation: Low-end harmonic foundation
- lead_melody: Primary melodic content
- pad_atmosphere: Sustained harmonic wash
- arp_rhythm: Rhythmic melodic patterns
- texture_layer: Non-melodic texture
- transient_fx: Percussive non-drum sounds
- rhythmic_element: Beat-aligned rhythmic content
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


class MusicalRole(str, Enum):
    """Musical roles for audio classification."""

    BASS_FOUNDATION = "bass_foundation"
    LEAD_MELODY = "lead_melody"
    PAD_ATMOSPHERE = "pad_atmosphere"
    ARP_RHYTHM = "arp_rhythm"
    TEXTURE_LAYER = "texture_layer"
    TRANSIENT_FX = "transient_fx"
    RHYTHMIC_ELEMENT = "rhythmic_element"
    DRUMS = "drums"  # Percussive drum content
    UNKNOWN = "unknown"


class SpectralProfile(str, Enum):
    """Spectral profile categories."""

    BASS_HEAVY = "bass_heavy"
    MID_FOCUSED = "mid_focused"
    BRIGHT = "bright"
    FULL_RANGE = "full_range"
    NARROW_BAND = "narrow_band"


class TemporalProfile(str, Enum):
    """Temporal behavior categories."""

    SUSTAINED = "sustained"
    TRANSIENT = "transient"
    RHYTHMIC = "rhythmic"
    EVOLVING = "evolving"
    STATIC = "static"


@dataclass
class RoleFeatures:
    """Features extracted for role classification."""

    # Spectral features
    spectral_centroid_mean: float = 0.0
    spectral_centroid_std: float = 0.0
    spectral_bandwidth_mean: float = 0.0
    spectral_rolloff_mean: float = 0.0
    low_freq_ratio: float = 0.0  # Energy below 250Hz
    mid_freq_ratio: float = 0.0  # Energy 250Hz-2kHz
    high_freq_ratio: float = 0.0  # Energy above 2kHz

    # Temporal features
    onset_rate: float = 0.0  # Onsets per second
    onset_strength_mean: float = 0.0
    onset_strength_std: float = 0.0
    rms_mean: float = 0.0
    rms_std: float = 0.0
    zero_crossing_rate: float = 0.0

    # Harmonic features
    harmonic_ratio: float = 0.0  # Harmonic vs percussive
    pitch_salience: float = 0.0  # How clear is the pitch
    pitch_stability: float = 0.0  # How stable is the pitch
    chromagram_entropy: float = 0.0  # Harmonic complexity

    # Envelope features
    attack_time_mean: float = 0.0
    decay_rate_mean: float = 0.0
    sustain_ratio: float = 0.0  # Sustain vs total duration

    # Rhythmic features
    tempo_strength: float = 0.0  # How rhythmic/tempo-aligned
    beat_alignment: float = 0.0  # Alignment to beat grid

    def to_array(self) -> np.ndarray:
        """Convert to feature array for ML."""
        return np.array([
            self.spectral_centroid_mean,
            self.spectral_centroid_std,
            self.spectral_bandwidth_mean,
            self.spectral_rolloff_mean,
            self.low_freq_ratio,
            self.mid_freq_ratio,
            self.high_freq_ratio,
            self.onset_rate,
            self.onset_strength_mean,
            self.onset_strength_std,
            self.rms_mean,
            self.rms_std,
            self.zero_crossing_rate,
            self.harmonic_ratio,
            self.pitch_salience,
            self.pitch_stability,
            self.chromagram_entropy,
            self.attack_time_mean,
            self.decay_rate_mean,
            self.sustain_ratio,
            self.tempo_strength,
            self.beat_alignment,
        ], dtype=np.float32)


@dataclass
class RoleClassification:
    """Result of role classification."""

    primary_role: MusicalRole
    confidence: float
    secondary_roles: List[Tuple[MusicalRole, float]] = field(default_factory=list)
    spectral_profile: SpectralProfile = SpectralProfile.FULL_RANGE
    temporal_profile: TemporalProfile = TemporalProfile.SUSTAINED
    features: Optional[RoleFeatures] = None

    # Extraction parameter recommendations
    recommended_onset_threshold: float = 0.5
    recommended_frame_threshold: float = 0.5
    recommended_note_merge_time: float = 0.05
    recommended_min_note_duration: float = 0.05
    recommended_quantization_strength: float = 0.5

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "primary_role": self.primary_role.value,
            "confidence": self.confidence,
            "secondary_roles": [
                {"role": r.value, "confidence": c}
                for r, c in self.secondary_roles
            ],
            "spectral_profile": self.spectral_profile.value,
            "temporal_profile": self.temporal_profile.value,
            "recommendations": {
                "onset_threshold": self.recommended_onset_threshold,
                "frame_threshold": self.recommended_frame_threshold,
                "note_merge_time": self.recommended_note_merge_time,
                "min_note_duration": self.recommended_min_note_duration,
                "quantization_strength": self.recommended_quantization_strength,
            },
        }


class RoleClassifier:
    """Classify audio by musical role.

    Uses spectral, temporal, and harmonic features to determine
    what musical role the audio is playing rather than just
    what instrument it is.
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

        # Role-specific thresholds (heuristic-based, can be learned)
        self._role_profiles = {
            MusicalRole.BASS_FOUNDATION: {
                "low_freq_ratio_min": 0.5,
                "spectral_centroid_max": 500,
                "harmonic_ratio_min": 0.5,
                "onset_rate_max": 4.0,
            },
            MusicalRole.LEAD_MELODY: {
                "pitch_salience_min": 0.5,
                "pitch_stability_min": 0.4,
                "spectral_centroid_range": (300, 3000),
                "onset_rate_range": (1.0, 8.0),
            },
            MusicalRole.PAD_ATMOSPHERE: {
                "sustain_ratio_min": 0.6,
                "onset_rate_max": 1.0,
                "harmonic_ratio_min": 0.6,
                "attack_time_min": 0.05,
            },
            MusicalRole.ARP_RHYTHM: {
                "onset_rate_min": 4.0,
                "tempo_strength_min": 0.5,
                "pitch_salience_min": 0.4,
            },
            MusicalRole.TEXTURE_LAYER: {
                "chromagram_entropy_min": 0.7,
                "pitch_salience_max": 0.4,
                "sustain_ratio_min": 0.4,
            },
            MusicalRole.TRANSIENT_FX: {
                "harmonic_ratio_max": 0.4,
                "attack_time_max": 0.02,
                "sustain_ratio_max": 0.3,
            },
            MusicalRole.RHYTHMIC_ELEMENT: {
                "tempo_strength_min": 0.6,
                "beat_alignment_min": 0.5,
                "onset_rate_min": 2.0,
            },
        }

    def classify(
        self,
        audio: np.ndarray,
        sr: int,
        stem_type: Optional[str] = None,
    ) -> RoleClassification:
        """Classify musical role of audio.

        Args:
            audio: Audio data (mono or stereo)
            sr: Sample rate
            stem_type: Optional stem type hint

        Returns:
            RoleClassification with role and confidence
        """
        # Convert to mono
        if audio.ndim == 2:
            audio_mono = np.mean(audio, axis=0)
        else:
            audio_mono = audio

        # Extract features
        features = self._extract_features(audio_mono, sr)

        # Classify based on features
        role_scores = self._compute_role_scores(features, stem_type)

        # Get primary and secondary roles
        sorted_roles = sorted(role_scores.items(), key=lambda x: x[1], reverse=True)
        primary_role = sorted_roles[0][0]
        primary_confidence = sorted_roles[0][1]

        secondary_roles = [
            (role, score) for role, score in sorted_roles[1:4]
            if score > 0.2
        ]

        # Determine profiles
        spectral_profile = self._determine_spectral_profile(features)
        temporal_profile = self._determine_temporal_profile(features)

        # Get extraction recommendations
        recommendations = self._get_extraction_recommendations(
            primary_role, features, spectral_profile, temporal_profile
        )

        return RoleClassification(
            primary_role=primary_role,
            confidence=primary_confidence,
            secondary_roles=secondary_roles,
            spectral_profile=spectral_profile,
            temporal_profile=temporal_profile,
            features=features,
            **recommendations,
        )

    def classify_with_context(
        self,
        audio: np.ndarray,
        sr: int,
        other_stems: Optional[Dict[str, np.ndarray]] = None,
        genre: Optional[str] = None,
    ) -> RoleClassification:
        """Classify with awareness of other stems and genre.

        Args:
            audio: Audio data
            sr: Sample rate
            other_stems: Other stems for context
            genre: Genre hint

        Returns:
            RoleClassification with context-aware role
        """
        # Get base classification
        result = self.classify(audio, sr)

        if other_stems is None and genre is None:
            return result

        # Adjust based on context
        role_adjustments = {}

        # If other stems have certain roles, adjust probabilities
        if other_stems:
            other_roles = {}
            for stem_type, stem_audio in other_stems.items():
                other_result = self.classify(stem_audio, sr, stem_type)
                other_roles[stem_type] = other_result.primary_role

            # If bass is already covered, reduce bass_foundation probability
            if MusicalRole.BASS_FOUNDATION in other_roles.values():
                role_adjustments[MusicalRole.BASS_FOUNDATION] = -0.2

            # If lead is covered, reduce lead_melody probability
            if MusicalRole.LEAD_MELODY in other_roles.values():
                role_adjustments[MusicalRole.LEAD_MELODY] = -0.1

        # Genre-based adjustments
        if genre:
            genre_lower = genre.lower()
            if genre_lower in ("ambient", "drone", "shoegaze"):
                role_adjustments[MusicalRole.PAD_ATMOSPHERE] = role_adjustments.get(
                    MusicalRole.PAD_ATMOSPHERE, 0
                ) + 0.15
                role_adjustments[MusicalRole.TEXTURE_LAYER] = role_adjustments.get(
                    MusicalRole.TEXTURE_LAYER, 0
                ) + 0.1
            elif genre_lower in ("synthwave", "retrowave"):
                role_adjustments[MusicalRole.ARP_RHYTHM] = role_adjustments.get(
                    MusicalRole.ARP_RHYTHM, 0
                ) + 0.1
                role_adjustments[MusicalRole.PAD_ATMOSPHERE] = role_adjustments.get(
                    MusicalRole.PAD_ATMOSPHERE, 0
                ) + 0.1
            elif genre_lower in ("edm", "house", "techno"):
                role_adjustments[MusicalRole.RHYTHMIC_ELEMENT] = role_adjustments.get(
                    MusicalRole.RHYTHMIC_ELEMENT, 0
                ) + 0.15

        # Apply adjustments
        if role_adjustments:
            # Recompute with adjustments
            features = result.features
            role_scores = self._compute_role_scores(features, None)

            for role, adjustment in role_adjustments.items():
                if role in role_scores:
                    role_scores[role] = max(0, min(1, role_scores[role] + adjustment))

            # Re-sort
            sorted_roles = sorted(role_scores.items(), key=lambda x: x[1], reverse=True)
            primary_role = sorted_roles[0][0]
            primary_confidence = sorted_roles[0][1]

            secondary_roles = [
                (role, score) for role, score in sorted_roles[1:4]
                if score > 0.2
            ]

            # Update recommendations for new role
            recommendations = self._get_extraction_recommendations(
                primary_role, features, result.spectral_profile, result.temporal_profile
            )

            return RoleClassification(
                primary_role=primary_role,
                confidence=primary_confidence,
                secondary_roles=secondary_roles,
                spectral_profile=result.spectral_profile,
                temporal_profile=result.temporal_profile,
                features=features,
                **recommendations,
            )

        return result

    def _extract_features(
        self,
        audio: np.ndarray,
        sr: int,
    ) -> RoleFeatures:
        """Extract features for role classification."""
        try:
            import librosa
        except ImportError:
            logger.warning("librosa not available, using basic features")
            return RoleFeatures()

        features = RoleFeatures()

        # Spectral features
        spec = np.abs(librosa.stft(audio, n_fft=self.n_fft, hop_length=self.hop_length))
        freqs = librosa.fft_frequencies(sr=sr, n_fft=self.n_fft)

        # Spectral centroid
        centroid = librosa.feature.spectral_centroid(
            y=audio, sr=sr, hop_length=self.hop_length
        )[0]
        features.spectral_centroid_mean = float(np.mean(centroid))
        features.spectral_centroid_std = float(np.std(centroid))

        # Spectral bandwidth
        bandwidth = librosa.feature.spectral_bandwidth(
            y=audio, sr=sr, hop_length=self.hop_length
        )[0]
        features.spectral_bandwidth_mean = float(np.mean(bandwidth))

        # Spectral rolloff
        rolloff = librosa.feature.spectral_rolloff(
            y=audio, sr=sr, hop_length=self.hop_length
        )[0]
        features.spectral_rolloff_mean = float(np.mean(rolloff))

        # Frequency band ratios
        total_energy = np.sum(spec ** 2)
        if total_energy > 0:
            low_mask = freqs < 250
            mid_mask = (freqs >= 250) & (freqs < 2000)
            high_mask = freqs >= 2000

            features.low_freq_ratio = float(np.sum(spec[low_mask, :] ** 2) / total_energy)
            features.mid_freq_ratio = float(np.sum(spec[mid_mask, :] ** 2) / total_energy)
            features.high_freq_ratio = float(np.sum(spec[high_mask, :] ** 2) / total_energy)

        # Onset features
        onset_env = librosa.onset.onset_strength(y=audio, sr=sr, hop_length=self.hop_length)
        onset_frames = librosa.onset.onset_detect(
            onset_envelope=onset_env, sr=sr, hop_length=self.hop_length
        )
        duration = len(audio) / sr
        features.onset_rate = float(len(onset_frames) / duration) if duration > 0 else 0
        features.onset_strength_mean = float(np.mean(onset_env))
        features.onset_strength_std = float(np.std(onset_env))

        # RMS
        rms = librosa.feature.rms(y=audio, hop_length=self.hop_length)[0]
        features.rms_mean = float(np.mean(rms))
        features.rms_std = float(np.std(rms))

        # Zero crossing rate
        zcr = librosa.feature.zero_crossing_rate(audio, hop_length=self.hop_length)[0]
        features.zero_crossing_rate = float(np.mean(zcr))

        # Harmonic-percussive ratio
        harmonic, percussive = librosa.effects.hpss(audio)
        h_energy = np.sum(harmonic ** 2)
        p_energy = np.sum(percussive ** 2)
        total_hp = h_energy + p_energy
        features.harmonic_ratio = float(h_energy / total_hp) if total_hp > 0 else 0.5

        # Pitch features
        f0, voiced_flag, voiced_probs = librosa.pyin(
            audio,
            fmin=librosa.note_to_hz('C1'),
            fmax=librosa.note_to_hz('C7'),
            sr=sr,
            hop_length=self.hop_length,
        )
        f0_valid = f0[~np.isnan(f0)]

        if len(f0_valid) > 0:
            features.pitch_salience = float(np.mean(voiced_probs[~np.isnan(f0)]))
            # Pitch stability: inverse of coefficient of variation
            if np.mean(f0_valid) > 0:
                cv = np.std(f0_valid) / np.mean(f0_valid)
                features.pitch_stability = float(1 / (1 + cv))
            else:
                features.pitch_stability = 0.0
        else:
            features.pitch_salience = 0.0
            features.pitch_stability = 0.0

        # Chromagram entropy (harmonic complexity)
        chroma = librosa.feature.chroma_stft(y=audio, sr=sr, hop_length=self.hop_length)
        chroma_mean = np.mean(chroma, axis=1)
        chroma_mean = chroma_mean / (np.sum(chroma_mean) + 1e-10)
        features.chromagram_entropy = float(-np.sum(chroma_mean * np.log(chroma_mean + 1e-10)))

        # Envelope features
        envelope = np.abs(audio)
        # Estimate attack time from onsets
        if len(onset_frames) > 0:
            attack_times = []
            for onset in onset_frames[:10]:  # Sample first 10 onsets
                onset_sample = onset * self.hop_length
                if onset_sample + int(0.1 * sr) < len(envelope):
                    attack_region = envelope[onset_sample:onset_sample + int(0.1 * sr)]
                    peak_idx = np.argmax(attack_region)
                    attack_times.append(peak_idx / sr)
            features.attack_time_mean = float(np.mean(attack_times)) if attack_times else 0.02

        # Sustain ratio estimation
        rms_threshold = np.percentile(rms, 75) * 0.5
        sustained_frames = np.sum(rms > rms_threshold)
        features.sustain_ratio = float(sustained_frames / len(rms)) if len(rms) > 0 else 0.5

        # Rhythmic features
        tempo, beat_frames = librosa.beat.beat_track(y=audio, sr=sr, hop_length=self.hop_length)
        features.tempo_strength = float(len(beat_frames) / (duration * 4)) if duration > 0 else 0

        # Beat alignment: how well onsets align with beats
        if len(beat_frames) > 0 and len(onset_frames) > 0:
            aligned_count = 0
            tolerance = 3  # frames
            for onset in onset_frames:
                if np.min(np.abs(beat_frames - onset)) <= tolerance:
                    aligned_count += 1
            features.beat_alignment = float(aligned_count / len(onset_frames))
        else:
            features.beat_alignment = 0.0

        return features

    def _compute_role_scores(
        self,
        features: RoleFeatures,
        stem_type: Optional[str],
    ) -> Dict[MusicalRole, float]:
        """Compute score for each role based on features."""
        scores = {role: 0.0 for role in MusicalRole if role != MusicalRole.UNKNOWN}

        # Bass foundation
        bass_score = 0.0
        if features.low_freq_ratio > 0.5:
            bass_score += 0.3
        if features.spectral_centroid_mean < 500:
            bass_score += 0.3
        if features.harmonic_ratio > 0.5:
            bass_score += 0.2
        if features.onset_rate < 4.0:
            bass_score += 0.2
        scores[MusicalRole.BASS_FOUNDATION] = bass_score

        # Lead melody
        lead_score = 0.0
        if features.pitch_salience > 0.5:
            lead_score += 0.3
        if features.pitch_stability > 0.4:
            lead_score += 0.2
        if 300 < features.spectral_centroid_mean < 3000:
            lead_score += 0.3
        if 1.0 < features.onset_rate < 8.0:
            lead_score += 0.2
        scores[MusicalRole.LEAD_MELODY] = lead_score

        # Pad/atmosphere
        pad_score = 0.0
        if features.sustain_ratio > 0.6:
            pad_score += 0.3
        if features.onset_rate < 1.0:
            pad_score += 0.3
        if features.harmonic_ratio > 0.6:
            pad_score += 0.2
        if features.attack_time_mean > 0.05:
            pad_score += 0.2
        scores[MusicalRole.PAD_ATMOSPHERE] = pad_score

        # Arp/rhythm
        arp_score = 0.0
        if features.onset_rate > 4.0:
            arp_score += 0.3
        if features.tempo_strength > 0.5:
            arp_score += 0.3
        if features.pitch_salience > 0.4:
            arp_score += 0.2
        if features.beat_alignment > 0.5:
            arp_score += 0.2
        scores[MusicalRole.ARP_RHYTHM] = arp_score

        # Texture layer
        texture_score = 0.0
        if features.chromagram_entropy > 0.7:
            texture_score += 0.3
        if features.pitch_salience < 0.4:
            texture_score += 0.3
        if features.sustain_ratio > 0.4:
            texture_score += 0.2
        if features.spectral_bandwidth_mean > 2000:
            texture_score += 0.2
        scores[MusicalRole.TEXTURE_LAYER] = texture_score

        # Transient FX
        transient_score = 0.0
        if features.harmonic_ratio < 0.4:
            transient_score += 0.3
        if features.attack_time_mean < 0.02:
            transient_score += 0.3
        if features.sustain_ratio < 0.3:
            transient_score += 0.2
        if features.zero_crossing_rate > 0.1:
            transient_score += 0.2
        scores[MusicalRole.TRANSIENT_FX] = transient_score

        # Rhythmic element
        rhythmic_score = 0.0
        if features.tempo_strength > 0.6:
            rhythmic_score += 0.3
        if features.beat_alignment > 0.5:
            rhythmic_score += 0.3
        if features.onset_rate > 2.0:
            rhythmic_score += 0.2
        if features.onset_strength_std > features.onset_strength_mean * 0.5:
            rhythmic_score += 0.2
        scores[MusicalRole.RHYTHMIC_ELEMENT] = rhythmic_score

        # Apply stem type hints
        if stem_type:
            if stem_type == "bass":
                scores[MusicalRole.BASS_FOUNDATION] += 0.2
            elif stem_type == "drums":
                scores[MusicalRole.RHYTHMIC_ELEMENT] += 0.2
                scores[MusicalRole.TRANSIENT_FX] += 0.1
            elif stem_type == "vocals":
                scores[MusicalRole.LEAD_MELODY] += 0.2
            elif stem_type == "guitar":
                # Guitar is typically melodic - lead, pad, or arp
                scores[MusicalRole.LEAD_MELODY] += 0.15
                scores[MusicalRole.PAD_ATMOSPHERE] += 0.1
                scores[MusicalRole.ARP_RHYTHM] += 0.1
                # Reduce unlikely roles for guitar
                scores[MusicalRole.BASS_FOUNDATION] *= 0.5
                scores[MusicalRole.TEXTURE_LAYER] *= 0.7
            elif stem_type == "other":
                # Could be anything, slight boost to melodic roles
                scores[MusicalRole.LEAD_MELODY] += 0.05
                scores[MusicalRole.PAD_ATMOSPHERE] += 0.05

        # Normalize scores
        total = sum(scores.values())
        if total > 0:
            scores = {k: v / total for k, v in scores.items()}

        return scores

    def _determine_spectral_profile(self, features: RoleFeatures) -> SpectralProfile:
        """Determine spectral profile from features."""
        if features.low_freq_ratio > 0.5:
            return SpectralProfile.BASS_HEAVY
        elif features.high_freq_ratio > 0.4:
            return SpectralProfile.BRIGHT
        elif features.mid_freq_ratio > 0.5:
            return SpectralProfile.MID_FOCUSED
        elif features.spectral_bandwidth_mean < 1000:
            return SpectralProfile.NARROW_BAND
        else:
            return SpectralProfile.FULL_RANGE

    def _determine_temporal_profile(self, features: RoleFeatures) -> TemporalProfile:
        """Determine temporal profile from features."""
        if features.sustain_ratio > 0.7 and features.onset_rate < 1.0:
            return TemporalProfile.SUSTAINED
        elif features.attack_time_mean < 0.02 and features.sustain_ratio < 0.3:
            return TemporalProfile.TRANSIENT
        elif features.tempo_strength > 0.5 and features.beat_alignment > 0.4:
            return TemporalProfile.RHYTHMIC
        elif features.rms_std / (features.rms_mean + 1e-10) > 0.5:
            return TemporalProfile.EVOLVING
        else:
            return TemporalProfile.STATIC

    def _get_extraction_recommendations(
        self,
        role: MusicalRole,
        features: RoleFeatures,
        spectral_profile: SpectralProfile,
        temporal_profile: TemporalProfile,
    ) -> Dict[str, float]:
        """Get extraction parameter recommendations based on role."""
        # Default recommendations
        recommendations = {
            "recommended_onset_threshold": 0.5,
            "recommended_frame_threshold": 0.5,
            "recommended_note_merge_time": 0.05,
            "recommended_min_note_duration": 0.05,
            "recommended_quantization_strength": 0.5,
        }

        # Role-specific adjustments
        if role == MusicalRole.BASS_FOUNDATION:
            recommendations["recommended_onset_threshold"] = 0.4
            recommendations["recommended_frame_threshold"] = 0.4
            recommendations["recommended_note_merge_time"] = 0.08
            recommendations["recommended_min_note_duration"] = 0.1
            recommendations["recommended_quantization_strength"] = 0.7

        elif role == MusicalRole.LEAD_MELODY:
            recommendations["recommended_onset_threshold"] = 0.5
            recommendations["recommended_frame_threshold"] = 0.5
            recommendations["recommended_note_merge_time"] = 0.03
            recommendations["recommended_min_note_duration"] = 0.05
            recommendations["recommended_quantization_strength"] = 0.4

        elif role == MusicalRole.PAD_ATMOSPHERE:
            recommendations["recommended_onset_threshold"] = 0.3
            recommendations["recommended_frame_threshold"] = 0.3
            recommendations["recommended_note_merge_time"] = 0.2
            recommendations["recommended_min_note_duration"] = 0.3
            recommendations["recommended_quantization_strength"] = 0.2

        elif role == MusicalRole.ARP_RHYTHM:
            recommendations["recommended_onset_threshold"] = 0.6
            recommendations["recommended_frame_threshold"] = 0.6
            recommendations["recommended_note_merge_time"] = 0.02
            recommendations["recommended_min_note_duration"] = 0.03
            recommendations["recommended_quantization_strength"] = 0.8

        elif role == MusicalRole.TEXTURE_LAYER:
            recommendations["recommended_onset_threshold"] = 0.3
            recommendations["recommended_frame_threshold"] = 0.3
            recommendations["recommended_note_merge_time"] = 0.15
            recommendations["recommended_min_note_duration"] = 0.2
            recommendations["recommended_quantization_strength"] = 0.2

        elif role == MusicalRole.TRANSIENT_FX:
            recommendations["recommended_onset_threshold"] = 0.7
            recommendations["recommended_frame_threshold"] = 0.7
            recommendations["recommended_note_merge_time"] = 0.01
            recommendations["recommended_min_note_duration"] = 0.02
            recommendations["recommended_quantization_strength"] = 0.3

        elif role == MusicalRole.RHYTHMIC_ELEMENT:
            recommendations["recommended_onset_threshold"] = 0.6
            recommendations["recommended_frame_threshold"] = 0.6
            recommendations["recommended_note_merge_time"] = 0.02
            recommendations["recommended_min_note_duration"] = 0.03
            recommendations["recommended_quantization_strength"] = 0.9

        # Temporal profile adjustments
        if temporal_profile == TemporalProfile.SUSTAINED:
            recommendations["recommended_note_merge_time"] *= 1.5
            recommendations["recommended_min_note_duration"] *= 1.5

        elif temporal_profile == TemporalProfile.TRANSIENT:
            recommendations["recommended_note_merge_time"] *= 0.5
            recommendations["recommended_min_note_duration"] *= 0.5

        return recommendations


# Module-level singleton
_classifier: Optional[RoleClassifier] = None


def get_role_classifier() -> RoleClassifier:
    """Get the global role classifier instance."""
    global _classifier
    if _classifier is None:
        _classifier = RoleClassifier()
    return _classifier


def classify_role(
    audio: np.ndarray,
    sr: int,
    stem_type: Optional[str] = None,
) -> RoleClassification:
    """Convenience function to classify musical role.

    Args:
        audio: Audio data
        sr: Sample rate
        stem_type: Optional stem type hint

    Returns:
        RoleClassification
    """
    classifier = get_role_classifier()
    return classifier.classify(audio, sr, stem_type)
