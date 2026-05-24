"""Polyphony confidence estimation for MIDI extraction.

Classifies audio segments as:
- Monophonic: Single voice (bass, lead)
- Lightly polyphonic: 2-4 voices (arps, simple chords)
- Dense polyphonic: 5+ voices (pads, chord stacks)

This drives:
- Extraction strategy (aggressive vs conservative)
- Merge behavior (enabled/disabled)
- Harmonic cleanup aggressiveness
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Tuple

import numpy as np


class PolyphonyClass(Enum):
    """Polyphony classification."""
    MONOPHONIC = "monophonic"
    LIGHT_POLY = "light_polyphonic"
    DENSE_POLY = "dense_polyphonic"


@dataclass
class PolyphonyEstimate:
    """Result of polyphony estimation."""
    # Classification
    polyphony_class: PolyphonyClass
    confidence: float  # 0-1

    # Detailed estimates
    avg_voices: float  # Average simultaneous voices
    max_voices: int    # Maximum detected
    min_voices: int    # Minimum detected

    # Feature scores (for debugging/tuning)
    spectral_complexity: float  # 0-1
    onset_density: float        # Onsets per second
    harmonic_overlap: float     # 0-1, degree of harmonic overlap
    pitch_spread: float         # Average pitch range in semitones

    # Recommendations
    recommended_merge_gap: float     # Suggested merge gap in seconds
    recommended_cleanup_level: str   # "aggressive", "moderate", "conservative"
    monophonic_enforcement: bool     # Should enforce single voice?


class PolyphonyEstimator:
    """Estimates polyphony level from audio.

    Uses multiple features:
    - Spectral complexity (number of peaks in spectrum)
    - Onset density and patterns
    - Harmonic series overlap
    - Pitch tracking variance
    """

    def __init__(
        self,
        hop_length: int = 512,
        n_fft: int = 2048,
        mono_threshold: float = 1.5,     # avg_voices below this = mono
        light_poly_threshold: float = 4.0, # avg_voices below this = light poly
    ):
        """Initialize estimator.

        Args:
            hop_length: Hop length for analysis
            n_fft: FFT size
            mono_threshold: Voice count threshold for monophonic
            light_poly_threshold: Voice count threshold for light polyphony
        """
        self.hop_length = hop_length
        self.n_fft = n_fft
        self.mono_threshold = mono_threshold
        self.light_poly_threshold = light_poly_threshold

    def estimate(
        self,
        audio: np.ndarray,
        sr: int,
        stem_type: Optional[str] = None,
    ) -> PolyphonyEstimate:
        """Estimate polyphony level of audio.

        Args:
            audio: Audio signal
            sr: Sample rate
            stem_type: Optional stem type hint

        Returns:
            PolyphonyEstimate with classification and recommendations
        """
        import librosa

        if len(audio) < self.n_fft:
            return self._default_estimate(stem_type)

        # 1. Spectral peak analysis
        spectral_complexity, peak_counts = self._analyze_spectral_peaks(audio, sr)

        # 2. Multi-pitch estimation
        avg_voices, max_voices, min_voices = self._estimate_voice_count(
            audio, sr, peak_counts
        )

        # 3. Onset density analysis
        onset_density = self._compute_onset_density(audio, sr)

        # 4. Harmonic overlap detection
        harmonic_overlap = self._compute_harmonic_overlap(audio, sr)

        # 5. Pitch spread analysis
        pitch_spread = self._compute_pitch_spread(audio, sr)

        # Apply stem type priors
        if stem_type:
            avg_voices = self._apply_stem_prior(avg_voices, stem_type)

        # Classify
        polyphony_class, confidence = self._classify(
            avg_voices, spectral_complexity, harmonic_overlap, stem_type
        )

        # Generate recommendations
        merge_gap = self._recommend_merge_gap(polyphony_class, onset_density)
        cleanup_level = self._recommend_cleanup_level(
            polyphony_class, harmonic_overlap
        )
        mono_enforce = self._should_enforce_mono(polyphony_class, stem_type)

        return PolyphonyEstimate(
            polyphony_class=polyphony_class,
            confidence=confidence,
            avg_voices=avg_voices,
            max_voices=max_voices,
            min_voices=min_voices,
            spectral_complexity=spectral_complexity,
            onset_density=onset_density,
            harmonic_overlap=harmonic_overlap,
            pitch_spread=pitch_spread,
            recommended_merge_gap=merge_gap,
            recommended_cleanup_level=cleanup_level,
            monophonic_enforcement=mono_enforce,
        )

    def _analyze_spectral_peaks(
        self,
        audio: np.ndarray,
        sr: int,
    ) -> Tuple[float, np.ndarray]:
        """Analyze spectral peaks over time.

        Returns (complexity_score, peak_counts_per_frame).
        """
        import librosa
        from scipy.signal import find_peaks

        # Compute STFT
        stft = np.abs(librosa.stft(
            audio,
            n_fft=self.n_fft,
            hop_length=self.hop_length
        ))

        # Find peaks in each frame
        peak_counts = []
        for frame in stft.T:
            # Normalize
            if np.max(frame) > 0:
                frame_norm = frame / np.max(frame)
            else:
                peak_counts.append(0)
                continue

            # Find significant peaks (above threshold)
            peaks, properties = find_peaks(
                frame_norm,
                height=0.1,
                distance=5  # Minimum distance between peaks
            )

            peak_counts.append(len(peaks))

        peak_counts = np.array(peak_counts)

        # Spectral complexity = normalized mean peak count
        if len(peak_counts) > 0:
            mean_peaks = np.mean(peak_counts)
            # Normalize to 0-1 (assume max ~50 peaks for complex signal)
            complexity = np.clip(mean_peaks / 50.0, 0, 1)
        else:
            complexity = 0.0

        return complexity, peak_counts

    def _estimate_voice_count(
        self,
        audio: np.ndarray,
        sr: int,
        peak_counts: np.ndarray,
    ) -> Tuple[float, int, int]:
        """Estimate simultaneous voice count.

        Uses multiple methods and combines:
        - Spectral peak grouping (harmonics -> fundamentals)
        - Chromagram analysis
        - NMF-based estimation

        Returns (avg_voices, max_voices, min_voices).
        """
        import librosa

        # Method 1: Chromagram-based (simpler, faster)
        chroma = librosa.feature.chroma_stft(
            y=audio, sr=sr,
            n_fft=self.n_fft,
            hop_length=self.hop_length
        )

        # Count active pitch classes per frame (above threshold)
        threshold = 0.3 * np.max(chroma) if np.max(chroma) > 0 else 0.1
        active_per_frame = np.sum(chroma > threshold, axis=0)

        # Method 2: Peak-based estimation
        # Assume ~6 peaks per harmonic series on average
        voice_estimate_peaks = peak_counts / 6.0

        # Combine estimates (weighted average)
        if len(active_per_frame) > 0 and len(voice_estimate_peaks) > 0:
            # Align lengths
            min_len = min(len(active_per_frame), len(voice_estimate_peaks))
            combined = (
                0.6 * active_per_frame[:min_len] +
                0.4 * voice_estimate_peaks[:min_len]
            )

            # Filter out silent frames (low activity)
            valid_frames = combined[combined > 0.5]

            if len(valid_frames) > 0:
                avg_voices = float(np.mean(valid_frames))
                max_voices = int(np.ceil(np.max(valid_frames)))
                min_voices = max(1, int(np.floor(np.min(valid_frames))))
            else:
                avg_voices, max_voices, min_voices = 1.0, 1, 1
        else:
            avg_voices, max_voices, min_voices = 1.0, 1, 1

        return avg_voices, max_voices, min_voices

    def _compute_onset_density(
        self,
        audio: np.ndarray,
        sr: int,
    ) -> float:
        """Compute onset density (onsets per second)."""
        import librosa

        # Detect onsets
        onset_frames = librosa.onset.onset_detect(
            y=audio, sr=sr,
            hop_length=self.hop_length,
            backtrack=False
        )

        # Duration in seconds
        duration = len(audio) / sr

        if duration > 0:
            return len(onset_frames) / duration
        return 0.0

    def _compute_harmonic_overlap(
        self,
        audio: np.ndarray,
        sr: int,
    ) -> float:
        """Compute degree of harmonic overlap.

        High overlap suggests multiple notes sharing harmonics,
        which can confuse extraction.
        """
        import librosa

        # Get harmonic component
        harmonic, percussive = librosa.effects.hpss(audio)

        # Compute chroma for harmonic component
        chroma = librosa.feature.chroma_stft(
            y=harmonic, sr=sr,
            n_fft=self.n_fft,
            hop_length=self.hop_length
        )

        # Check for common harmonic intervals (5ths, 4ths, 3rds)
        # These often appear together and share harmonics
        overlap_score = 0.0

        for frame in chroma.T:
            if np.max(frame) == 0:
                continue

            frame_norm = frame / np.max(frame)

            # Find active pitch classes
            active = np.where(frame_norm > 0.3)[0]

            if len(active) >= 2:
                # Check intervals between active pitches
                for i in range(len(active)):
                    for j in range(i + 1, len(active)):
                        interval = (active[j] - active[i]) % 12
                        # Perfect 5th (7 semitones) or 4th (5) or 3rd (4)
                        if interval in [3, 4, 5, 7, 8, 9]:
                            overlap_score += 1

        # Normalize by frame count
        n_frames = chroma.shape[1]
        if n_frames > 0:
            overlap_score = min(1.0, overlap_score / (n_frames * 3))

        return overlap_score

    def _compute_pitch_spread(
        self,
        audio: np.ndarray,
        sr: int,
    ) -> float:
        """Compute pitch spread (range in semitones)."""
        import librosa

        # Track pitch
        f0, voiced_flag, _ = librosa.pyin(
            audio,
            fmin=librosa.note_to_hz('C2'),
            fmax=librosa.note_to_hz('C7'),
            sr=sr,
            hop_length=self.hop_length
        )

        # Get voiced pitches
        voiced_f0 = f0[voiced_flag]

        if len(voiced_f0) < 2:
            return 0.0

        # Convert to MIDI and compute range
        midi_pitches = librosa.hz_to_midi(voiced_f0[voiced_f0 > 0])

        if len(midi_pitches) > 0:
            return float(np.max(midi_pitches) - np.min(midi_pitches))
        return 0.0

    def _apply_stem_prior(
        self,
        avg_voices: float,
        stem_type: str,
    ) -> float:
        """Apply stem type prior to voice estimate.

        Some stem types have known polyphony characteristics.
        """
        priors = {
            "bass": 0.7,    # Strongly biased toward mono
            "lead": 0.8,    # Usually mono or light poly
            "pad": 1.3,     # Often more polyphonic than detected
            "synth": 1.0,   # Neutral
            "guitar": 1.1,  # Slightly biased toward poly (chords)
            "arp": 0.9,     # Usually single notes fast
        }

        prior = priors.get(stem_type.lower(), 1.0)
        return avg_voices * prior

    def _classify(
        self,
        avg_voices: float,
        spectral_complexity: float,
        harmonic_overlap: float,
        stem_type: Optional[str] = None,
    ) -> Tuple[PolyphonyClass, float]:
        """Classify polyphony level with confidence.

        Returns (polyphony_class, confidence).
        """
        # Strong stem type hints override detection
        if stem_type:
            stem_lower = stem_type.lower()
            if stem_lower in ["bass"]:
                if avg_voices < 2.5:
                    return PolyphonyClass.MONOPHONIC, 0.9
            elif stem_lower in ["pad"]:
                if spectral_complexity > 0.4:
                    return PolyphonyClass.DENSE_POLY, 0.8

        # Classify based on voice count
        if avg_voices < self.mono_threshold:
            poly_class = PolyphonyClass.MONOPHONIC
            # Confidence decreases as we approach threshold
            distance = self.mono_threshold - avg_voices
            confidence = min(1.0, 0.5 + distance * 0.5)

        elif avg_voices < self.light_poly_threshold:
            poly_class = PolyphonyClass.LIGHT_POLY
            # Highest confidence in middle of range
            mid = (self.mono_threshold + self.light_poly_threshold) / 2
            distance = abs(avg_voices - mid)
            max_distance = (self.light_poly_threshold - self.mono_threshold) / 2
            confidence = max(0.5, 1.0 - distance / max_distance * 0.5)

        else:
            poly_class = PolyphonyClass.DENSE_POLY
            # More voices = more confidence
            confidence = min(1.0, 0.5 + (avg_voices - self.light_poly_threshold) * 0.1)

        # Adjust confidence based on complexity
        if poly_class == PolyphonyClass.MONOPHONIC and spectral_complexity > 0.5:
            confidence *= 0.8  # Reduce confidence if complex spectrum
        elif poly_class == PolyphonyClass.DENSE_POLY and spectral_complexity < 0.3:
            confidence *= 0.8  # Reduce confidence if simple spectrum

        return poly_class, float(np.clip(confidence, 0.3, 1.0))

    def _recommend_merge_gap(
        self,
        polyphony_class: PolyphonyClass,
        onset_density: float,
    ) -> float:
        """Recommend merge gap based on polyphony and onset density."""
        base_gaps = {
            PolyphonyClass.MONOPHONIC: 0.05,   # 50ms - can merge more
            PolyphonyClass.LIGHT_POLY: 0.03,   # 30ms - moderate merge
            PolyphonyClass.DENSE_POLY: 0.01,   # 10ms - minimal merge
        }

        gap = base_gaps[polyphony_class]

        # Reduce gap for fast onset density (lots of articulation)
        if onset_density > 8:  # More than 8 onsets/sec
            gap *= 0.5
        elif onset_density > 4:
            gap *= 0.7

        return gap

    def _recommend_cleanup_level(
        self,
        polyphony_class: PolyphonyClass,
        harmonic_overlap: float,
    ) -> str:
        """Recommend harmonic cleanup aggressiveness."""
        if polyphony_class == PolyphonyClass.MONOPHONIC:
            # Aggressive cleanup safe for mono
            return "aggressive"

        elif polyphony_class == PolyphonyClass.LIGHT_POLY:
            # Moderate - need to preserve some harmonics
            if harmonic_overlap > 0.5:
                return "conservative"  # High overlap, be careful
            return "moderate"

        else:
            # Dense poly - conservative to preserve intended notes
            return "conservative"

    def _should_enforce_mono(
        self,
        polyphony_class: PolyphonyClass,
        stem_type: Optional[str],
    ) -> bool:
        """Determine if monophonic enforcement should be used."""
        # Always enforce for bass
        if stem_type and stem_type.lower() == "bass":
            return True

        # Enforce if confidently monophonic
        if polyphony_class == PolyphonyClass.MONOPHONIC:
            return True

        return False

    def _default_estimate(
        self,
        stem_type: Optional[str],
    ) -> PolyphonyEstimate:
        """Return default estimate for too-short audio."""
        # Use stem type hints for defaults
        if stem_type:
            stem_lower = stem_type.lower()
            if stem_lower == "bass":
                return PolyphonyEstimate(
                    polyphony_class=PolyphonyClass.MONOPHONIC,
                    confidence=0.7,
                    avg_voices=1.0,
                    max_voices=1,
                    min_voices=1,
                    spectral_complexity=0.3,
                    onset_density=2.0,
                    harmonic_overlap=0.2,
                    pitch_spread=12.0,
                    recommended_merge_gap=0.05,
                    recommended_cleanup_level="aggressive",
                    monophonic_enforcement=True,
                )
            elif stem_lower == "pad":
                return PolyphonyEstimate(
                    polyphony_class=PolyphonyClass.DENSE_POLY,
                    confidence=0.6,
                    avg_voices=5.0,
                    max_voices=8,
                    min_voices=3,
                    spectral_complexity=0.6,
                    onset_density=1.0,
                    harmonic_overlap=0.5,
                    pitch_spread=24.0,
                    recommended_merge_gap=0.01,
                    recommended_cleanup_level="conservative",
                    monophonic_enforcement=False,
                )

        # Generic default
        return PolyphonyEstimate(
            polyphony_class=PolyphonyClass.LIGHT_POLY,
            confidence=0.5,
            avg_voices=2.0,
            max_voices=4,
            min_voices=1,
            spectral_complexity=0.4,
            onset_density=3.0,
            harmonic_overlap=0.3,
            pitch_spread=18.0,
            recommended_merge_gap=0.03,
            recommended_cleanup_level="moderate",
            monophonic_enforcement=False,
        )


def estimate_polyphony(
    audio: np.ndarray,
    sr: int,
    stem_type: Optional[str] = None,
) -> PolyphonyEstimate:
    """Convenience function to estimate polyphony.

    Args:
        audio: Audio signal
        sr: Sample rate
        stem_type: Optional stem type hint

    Returns:
        PolyphonyEstimate with classification and recommendations
    """
    estimator = PolyphonyEstimator()
    return estimator.estimate(audio, sr, stem_type)


def get_extraction_config_for_polyphony(
    estimate: PolyphonyEstimate,
) -> dict:
    """Convert polyphony estimate to extraction configuration.

    Returns dict with keys that can be passed to extraction functions.
    """
    config = {
        "monophonic_enforcement": estimate.monophonic_enforcement,
        "merge_gap_seconds": estimate.recommended_merge_gap,
        "harmonic_cleanup_level": estimate.recommended_cleanup_level,
        "max_voices": estimate.max_voices,
    }

    # Adjust thresholds based on polyphony
    if estimate.polyphony_class == PolyphonyClass.MONOPHONIC:
        config["onset_threshold"] = 0.4
        config["frame_threshold"] = 0.3
        config["enable_octave_correction"] = True
    elif estimate.polyphony_class == PolyphonyClass.LIGHT_POLY:
        config["onset_threshold"] = 0.5
        config["frame_threshold"] = 0.4
        config["enable_octave_correction"] = False
    else:  # Dense poly
        config["onset_threshold"] = 0.45
        config["frame_threshold"] = 0.35
        config["enable_octave_correction"] = False

    return config
