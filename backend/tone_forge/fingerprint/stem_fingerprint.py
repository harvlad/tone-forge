"""Stem fingerprinting for matching and templates.

Generates multi-dimensional fingerprints from audio stems that capture:
- Spectral characteristics (brightness, density, spread)
- Temporal characteristics (attack, decay, transients)
- Modulation (vibrato, chorus, filter movement)
- Rhythmic (density, syncopation)
- Timbral (saturation, harmonics, noise)

These fingerprints enable:
- Preset matching ("find synth that sounds like this")
- Template-based reconstruction
- Producer style clustering
- "Make this sound like that" workflows
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class StemFingerprint:
    """Multi-dimensional fingerprint for audio stem."""

    # Identification
    stem_id: str = ""
    stem_type: str = "unknown"
    duration_sec: float = 0.0

    # Spectral characteristics
    harmonic_density: float = 0.0  # Ratio of harmonic to noise energy
    spectral_brightness: float = 0.0  # Spectral centroid normalized
    spectral_spread: float = 0.0  # Spectral bandwidth
    spectral_flatness: float = 0.0  # Noise-like vs tonal
    spectral_rolloff: float = 0.0  # Frequency below which 85% energy

    # Temporal characteristics
    transient_shape: str = "medium"  # sharp, medium, soft
    attack_time_ms: float = 0.0  # Time to peak
    decay_character: str = "sustain"  # sustain, decay, pluck
    release_time_ms: float = 0.0

    # Modulation characteristics
    vibrato_rate: Optional[float] = None  # Hz, None if not detected
    vibrato_depth: Optional[float] = None  # Cents
    chorus_depth: Optional[float] = None  # 0-1
    filter_movement: float = 0.0  # Spectral flux measure

    # Rhythmic characteristics
    rhythmic_density: float = 0.0  # Notes per second
    syncopation: float = 0.0  # Off-beat emphasis
    regularity: float = 0.0  # How regular the rhythm

    # Spatial characteristics
    stereo_width: float = 0.0  # 0=mono, 1=full stereo

    # Timbral characteristics
    saturation_amount: float = 0.0  # Harmonic distortion estimate
    noise_amount: float = 0.0  # Noise floor
    sub_bass_presence: float = 0.0  # Energy below 100Hz

    # Envelope characteristics
    filter_envelope: Optional[str] = None  # pluck, sweep, static

    # Combined embedding vector for similarity search
    embedding: np.ndarray = field(default_factory=lambda: np.zeros(128))

    def to_dict(self) -> dict:
        return {
            "stem_id": self.stem_id,
            "stem_type": self.stem_type,
            "duration_sec": self.duration_sec,
            "spectral": {
                "harmonic_density": self.harmonic_density,
                "brightness": self.spectral_brightness,
                "spread": self.spectral_spread,
                "flatness": self.spectral_flatness,
                "rolloff": self.spectral_rolloff,
            },
            "temporal": {
                "transient_shape": self.transient_shape,
                "attack_time_ms": self.attack_time_ms,
                "decay_character": self.decay_character,
                "release_time_ms": self.release_time_ms,
            },
            "modulation": {
                "vibrato_rate": self.vibrato_rate,
                "vibrato_depth": self.vibrato_depth,
                "chorus_depth": self.chorus_depth,
                "filter_movement": self.filter_movement,
            },
            "rhythmic": {
                "density": self.rhythmic_density,
                "syncopation": self.syncopation,
                "regularity": self.regularity,
            },
            "spatial": {
                "stereo_width": self.stereo_width,
            },
            "timbral": {
                "saturation": self.saturation_amount,
                "noise": self.noise_amount,
                "sub_bass": self.sub_bass_presence,
            },
            "filter_envelope": self.filter_envelope,
            "embedding_dim": len(self.embedding),
        }

    def to_vector(self) -> np.ndarray:
        """Convert to feature vector for similarity computation."""
        features = [
            self.harmonic_density,
            self.spectral_brightness,
            self.spectral_spread,
            self.spectral_flatness,
            self.spectral_rolloff,
            self.attack_time_ms / 500.0,  # Normalize to 0-1 range
            self.release_time_ms / 1000.0,
            self.filter_movement,
            self.rhythmic_density / 10.0,
            self.syncopation,
            self.regularity,
            self.stereo_width,
            self.saturation_amount,
            self.noise_amount,
            self.sub_bass_presence,
            # One-hot for transient shape
            1.0 if self.transient_shape == "sharp" else 0.0,
            1.0 if self.transient_shape == "medium" else 0.0,
            1.0 if self.transient_shape == "soft" else 0.0,
            # One-hot for decay character
            1.0 if self.decay_character == "sustain" else 0.0,
            1.0 if self.decay_character == "decay" else 0.0,
            1.0 if self.decay_character == "pluck" else 0.0,
        ]

        # Add vibrato/chorus if present
        features.extend([
            (self.vibrato_rate or 0) / 10.0,
            (self.vibrato_depth or 0) / 100.0,
            self.chorus_depth or 0,
        ])

        return np.array(features, dtype=np.float32)


class FingerprintExtractor:
    """Extracts fingerprints from audio stems."""

    def __init__(
        self,
        hop_length: int = 512,
        n_fft: int = 2048,
        n_mels: int = 128,
    ):
        self.hop_length = hop_length
        self.n_fft = n_fft
        self.n_mels = n_mels

    def extract(
        self,
        audio: np.ndarray,
        sr: int,
        stem_id: str = "",
        stem_type: str = "unknown",
    ) -> StemFingerprint:
        """Extract fingerprint from audio.

        Args:
            audio: Audio signal (mono or stereo)
            sr: Sample rate
            stem_id: Identifier for the stem
            stem_type: Type of stem (bass, lead, pad, etc.)

        Returns:
            StemFingerprint with extracted features
        """
        import librosa

        # Handle stereo
        is_stereo = audio.ndim > 1
        if is_stereo:
            audio_mono = np.mean(audio, axis=0) if audio.ndim > 1 else audio
            stereo_width = self._compute_stereo_width(audio, sr)
        else:
            audio_mono = audio
            stereo_width = 0.0

        fingerprint = StemFingerprint(
            stem_id=stem_id,
            stem_type=stem_type,
            duration_sec=len(audio_mono) / sr,
            stereo_width=stereo_width,
        )

        # Extract features
        self._extract_spectral_features(audio_mono, sr, fingerprint)
        self._extract_temporal_features(audio_mono, sr, fingerprint)
        self._extract_modulation_features(audio_mono, sr, fingerprint)
        self._extract_rhythmic_features(audio_mono, sr, fingerprint)
        self._extract_timbral_features(audio_mono, sr, fingerprint)

        # Build embedding
        fingerprint.embedding = self._build_embedding(fingerprint, audio_mono, sr)

        return fingerprint

    def _extract_spectral_features(
        self,
        audio: np.ndarray,
        sr: int,
        fingerprint: StemFingerprint,
    ):
        """Extract spectral characteristics."""
        import librosa

        # Compute spectrogram
        D = np.abs(librosa.stft(audio, n_fft=self.n_fft, hop_length=self.hop_length))

        # Spectral centroid (brightness)
        centroid = librosa.feature.spectral_centroid(S=D, sr=sr)[0]
        fingerprint.spectral_brightness = float(np.mean(centroid) / (sr / 2))

        # Spectral bandwidth (spread)
        bandwidth = librosa.feature.spectral_bandwidth(S=D, sr=sr)[0]
        fingerprint.spectral_spread = float(np.mean(bandwidth) / (sr / 2))

        # Spectral flatness
        flatness = librosa.feature.spectral_flatness(S=D)[0]
        fingerprint.spectral_flatness = float(np.mean(flatness))

        # Spectral rolloff
        rolloff = librosa.feature.spectral_rolloff(S=D, sr=sr)[0]
        fingerprint.spectral_rolloff = float(np.mean(rolloff) / sr)

        # Harmonic density (ratio of harmonic to percussive)
        y_harmonic, y_percussive = librosa.effects.hpss(audio)
        harmonic_energy = np.sum(y_harmonic ** 2)
        total_energy = np.sum(audio ** 2)
        fingerprint.harmonic_density = float(harmonic_energy / (total_energy + 1e-8))

    def _extract_temporal_features(
        self,
        audio: np.ndarray,
        sr: int,
        fingerprint: StemFingerprint,
    ):
        """Extract temporal/envelope characteristics."""
        import librosa

        # Compute envelope
        envelope = np.abs(librosa.effects.preemphasis(audio))
        envelope = np.convolve(envelope, np.ones(512) / 512, mode='same')

        # Find attack time
        peak_idx = np.argmax(envelope)
        if peak_idx > 0:
            # Find 10% threshold
            threshold = envelope[peak_idx] * 0.1
            attack_start = 0
            for i in range(peak_idx):
                if envelope[i] > threshold:
                    attack_start = i
                    break

            attack_samples = peak_idx - attack_start
            fingerprint.attack_time_ms = float(attack_samples / sr * 1000)

            # Classify transient shape
            if fingerprint.attack_time_ms < 5:
                fingerprint.transient_shape = "sharp"
            elif fingerprint.attack_time_ms < 30:
                fingerprint.transient_shape = "medium"
            else:
                fingerprint.transient_shape = "soft"

        # Analyze decay
        if peak_idx < len(envelope) - 1:
            decay_portion = envelope[peak_idx:]

            # Find time to drop to 37% (1/e)
            target = envelope[peak_idx] * 0.37
            decay_idx = np.argmax(decay_portion < target)

            if decay_idx > 0:
                fingerprint.release_time_ms = float(decay_idx / sr * 1000)

            # Classify decay character
            if len(decay_portion) > 0:
                sustain_ratio = np.mean(decay_portion[len(decay_portion)//2:]) / envelope[peak_idx]
                if sustain_ratio > 0.5:
                    fingerprint.decay_character = "sustain"
                elif sustain_ratio > 0.1:
                    fingerprint.decay_character = "decay"
                else:
                    fingerprint.decay_character = "pluck"

    def _extract_modulation_features(
        self,
        audio: np.ndarray,
        sr: int,
        fingerprint: StemFingerprint,
    ):
        """Extract modulation characteristics."""
        import librosa

        # Spectral flux (filter movement)
        D = np.abs(librosa.stft(audio, n_fft=self.n_fft, hop_length=self.hop_length))
        flux = np.sum(np.diff(D, axis=1) ** 2, axis=0)
        fingerprint.filter_movement = float(np.mean(flux) / (np.mean(D ** 2) + 1e-8))

        # Vibrato detection using pitch tracking
        try:
            f0, voiced, probs = librosa.pyin(
                audio, fmin=50, fmax=2000, sr=sr, hop_length=self.hop_length
            )
            f0 = np.nan_to_num(f0, nan=0)

            # Find sustained regions
            voiced_regions = np.where(f0 > 0)[0]
            if len(voiced_regions) > 50:
                # Look for periodic pitch modulation
                f0_voiced = f0[voiced_regions]
                f0_centered = f0_voiced - np.mean(f0_voiced)

                # FFT of pitch deviation
                pitch_fft = np.abs(np.fft.rfft(f0_centered))
                freqs = np.fft.rfftfreq(len(f0_centered), d=self.hop_length/sr)

                # Find peak in vibrato range (4-8 Hz)
                vibrato_range = (freqs >= 4) & (freqs <= 8)
                if np.any(vibrato_range):
                    vibrato_idx = np.argmax(pitch_fft * vibrato_range)
                    if pitch_fft[vibrato_idx] > np.mean(pitch_fft) * 2:
                        fingerprint.vibrato_rate = float(freqs[vibrato_idx])
                        fingerprint.vibrato_depth = float(np.std(f0_centered) * 2)

        except Exception:
            pass

        # Chorus detection (stereo correlation fluctuation)
        # Note: Would need stereo input for proper detection
        fingerprint.chorus_depth = None  # Placeholder

    def _extract_rhythmic_features(
        self,
        audio: np.ndarray,
        sr: int,
        fingerprint: StemFingerprint,
    ):
        """Extract rhythmic characteristics."""
        import librosa

        # Onset detection
        onset_env = librosa.onset.onset_strength(y=audio, sr=sr)
        onsets = librosa.onset.onset_detect(onset_envelope=onset_env, sr=sr, units="time")

        # Rhythmic density (notes per second)
        duration = len(audio) / sr
        fingerprint.rhythmic_density = float(len(onsets) / duration) if duration > 0 else 0

        if len(onsets) < 3:
            return

        # Inter-onset intervals
        iois = np.diff(onsets)

        # Regularity (how consistent are IOIs)
        if len(iois) > 0 and np.mean(iois) > 0:
            fingerprint.regularity = float(1.0 - np.std(iois) / np.mean(iois))
            fingerprint.regularity = max(0, min(1, fingerprint.regularity))

        # Syncopation (estimate from beat alignment)
        try:
            tempo, beats = librosa.beat.beat_track(y=audio, sr=sr)
            if hasattr(tempo, "__iter__"):
                tempo = float(tempo[0]) if len(tempo) > 0 else 120.0

            if len(beats) > 0:
                beat_times = librosa.frames_to_time(beats, sr=sr)

                # Count onsets that are off-beat
                off_beat_count = 0
                beat_window = 60.0 / tempo / 4  # Quarter of a beat

                for onset in onsets:
                    # Find closest beat
                    closest_beat = beat_times[np.argmin(np.abs(beat_times - onset))]
                    if abs(onset - closest_beat) > beat_window:
                        off_beat_count += 1

                fingerprint.syncopation = float(off_beat_count / len(onsets))

        except Exception:
            pass

    def _extract_timbral_features(
        self,
        audio: np.ndarray,
        sr: int,
        fingerprint: StemFingerprint,
    ):
        """Extract timbral characteristics."""
        import librosa

        # Sub-bass presence
        b, a = self._design_lowpass(100, sr)
        from scipy.signal import filtfilt
        try:
            low_freq = filtfilt(b, a, audio)
            fingerprint.sub_bass_presence = float(
                np.sum(low_freq ** 2) / (np.sum(audio ** 2) + 1e-8)
            )
        except Exception:
            fingerprint.sub_bass_presence = 0.0

        # Saturation estimation (harmonic distortion)
        # Compare odd vs even harmonics
        D = np.abs(librosa.stft(audio, n_fft=self.n_fft))
        freqs = librosa.fft_frequencies(sr=sr, n_fft=self.n_fft)
        avg_spectrum = np.mean(D, axis=1)

        # Find fundamental
        from scipy.signal import find_peaks
        peaks, _ = find_peaks(avg_spectrum, height=np.max(avg_spectrum) * 0.1)

        if len(peaks) > 0:
            fundamental_idx = peaks[0]
            fundamental_freq = freqs[fundamental_idx]

            if fundamental_freq > 50:
                # Check for odd harmonics (sign of saturation)
                odd_energy = 0
                even_energy = 0

                for h in range(2, 8):
                    harmonic_freq = fundamental_freq * h
                    harmonic_idx = np.argmin(np.abs(freqs - harmonic_freq))

                    if h % 2 == 0:
                        even_energy += avg_spectrum[harmonic_idx]
                    else:
                        odd_energy += avg_spectrum[harmonic_idx]

                if even_energy + odd_energy > 0:
                    # More odd harmonics = more saturation
                    fingerprint.saturation_amount = float(
                        odd_energy / (even_energy + odd_energy + 1e-8)
                    )

        # Noise estimation (spectral flatness already captured this)
        fingerprint.noise_amount = fingerprint.spectral_flatness

    def _compute_stereo_width(
        self,
        audio: np.ndarray,
        sr: int,
    ) -> float:
        """Compute stereo width from stereo audio."""
        if audio.ndim != 2 or audio.shape[0] != 2:
            return 0.0

        left = audio[0]
        right = audio[1]

        # Correlation between channels
        correlation = np.corrcoef(left, right)[0, 1]

        # Width = 1 - correlation (0 = mono, 1 = fully decorrelated)
        return float(1.0 - abs(correlation))

    def _design_lowpass(self, cutoff: float, sr: int):
        """Design simple lowpass filter."""
        from scipy.signal import butter
        nyquist = sr / 2
        normalized_cutoff = cutoff / nyquist
        return butter(2, normalized_cutoff, btype='low')

    def _build_embedding(
        self,
        fingerprint: StemFingerprint,
        audio: np.ndarray,
        sr: int,
    ) -> np.ndarray:
        """Build combined embedding vector."""
        import librosa

        # Start with hand-crafted features
        hand_crafted = fingerprint.to_vector()

        # Add mel-spectrogram summary statistics
        mel = librosa.feature.melspectrogram(
            y=audio, sr=sr, n_mels=self.n_mels, hop_length=self.hop_length
        )
        mel_db = librosa.power_to_db(mel, ref=np.max)

        # Summarize mel bands
        mel_mean = np.mean(mel_db, axis=1)
        mel_std = np.std(mel_db, axis=1)

        # Reduce to 64 dimensions
        mel_summary = np.concatenate([
            mel_mean[::2],  # Every other band mean
            mel_std[::4],   # Every 4th band std
        ])[:64]

        # Pad if needed
        if len(mel_summary) < 64:
            mel_summary = np.pad(mel_summary, (0, 64 - len(mel_summary)))

        # Combine
        embedding = np.concatenate([
            hand_crafted,
            mel_summary,
        ])

        # Pad/truncate to 128 dimensions
        if len(embedding) < 128:
            embedding = np.pad(embedding, (0, 128 - len(embedding)))
        else:
            embedding = embedding[:128]

        # L2 normalize
        norm = np.linalg.norm(embedding)
        if norm > 0:
            embedding = embedding / norm

        return embedding.astype(np.float32)


def extract_fingerprint(
    audio: np.ndarray,
    sr: int,
    stem_id: str = "",
    stem_type: str = "unknown",
) -> StemFingerprint:
    """Convenience function for fingerprint extraction.

    Args:
        audio: Audio signal
        sr: Sample rate
        stem_id: Identifier for the stem
        stem_type: Type of stem

    Returns:
        StemFingerprint
    """
    extractor = FingerprintExtractor()
    return extractor.extract(audio, sr, stem_id, stem_type)


def fingerprint_similarity(
    fp1: StemFingerprint,
    fp2: StemFingerprint,
    method: str = "cosine",
) -> float:
    """Compute similarity between two fingerprints.

    Args:
        fp1: First fingerprint
        fp2: Second fingerprint
        method: Similarity method (cosine, euclidean)

    Returns:
        Similarity score 0-1 (higher = more similar)
    """
    if method == "cosine":
        # Cosine similarity
        dot = np.dot(fp1.embedding, fp2.embedding)
        norm1 = np.linalg.norm(fp1.embedding)
        norm2 = np.linalg.norm(fp2.embedding)

        if norm1 > 0 and norm2 > 0:
            return float((dot / (norm1 * norm2) + 1) / 2)  # Map to 0-1
        return 0.0

    elif method == "euclidean":
        # Euclidean distance converted to similarity
        dist = np.linalg.norm(fp1.embedding - fp2.embedding)
        return float(1 / (1 + dist))

    else:
        return fingerprint_similarity(fp1, fp2, method="cosine")
