"""Stem quality analysis for contamination-aware reconstruction.

Demucs and other source separation models produce approximations,
not ground truth. This module quantifies separation quality to:
- Adjust downstream confidence scores
- Adapt extraction thresholds
- Gate low-quality regions

Quality Metrics:
- contamination_score: Bleed from other stems (0=clean, 1=severe)
- transient_integrity: Attack preservation (0=smeared, 1=intact)
- harmonic_purity: Single-source harmonics (0=mixed, 1=pure)
- reverb_density: Wet signal proportion (0=dry, 1=wash)
- stereo_coherence: Phase stability (0=unstable, 1=stable)
- snr_estimate: Signal-to-noise ratio in dB
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class ConfidenceRegion:
    """A time region with associated confidence."""

    start_time: float
    end_time: float
    confidence: float
    reason: str = ""  # Why confidence is low/high

    @property
    def duration(self) -> float:
        return self.end_time - self.start_time


@dataclass
class StemQuality:
    """Quality assessment for a separated stem."""

    stem_type: str

    # Core quality metrics (0-1, higher is better except contamination/reverb)
    contamination_score: float  # 0=clean, 1=severe bleed
    transient_integrity: float  # 0=smeared, 1=intact attacks
    harmonic_purity: float      # 0=mixed sources, 1=single source
    reverb_density: float       # 0=dry, 1=reverb wash
    stereo_coherence: float     # 0=phase issues, 1=stable stereo
    snr_estimate: float         # Signal-to-noise ratio in dB

    # Region-level confidence
    confidence_regions: List[ConfidenceRegion] = field(default_factory=list)

    # Detected issues
    issues: List[str] = field(default_factory=list)

    @property
    def overall_quality(self) -> float:
        """Compute weighted overall quality score (0-1)."""
        # Weights reflect importance for reconstruction
        weights = {
            "contamination": 0.25,
            "transient": 0.20,
            "harmonic": 0.20,
            "reverb": 0.15,
            "stereo": 0.10,
            "snr": 0.10,
        }

        scores = {
            "contamination": 1 - self.contamination_score,
            "transient": self.transient_integrity,
            "harmonic": self.harmonic_purity,
            "reverb": 1 - self.reverb_density,  # Less reverb = better for extraction
            "stereo": self.stereo_coherence,
            "snr": min(1.0, self.snr_estimate / 30),  # Normalize to 0-1, 30dB = perfect
        }

        return sum(weights[k] * scores[k] for k in weights)

    @property
    def is_usable(self) -> bool:
        """Whether stem quality is sufficient for reconstruction."""
        return self.overall_quality >= 0.4

    @property
    def low_confidence_regions(self) -> List[ConfidenceRegion]:
        """Get regions with low confidence."""
        return [r for r in self.confidence_regions if r.confidence < 0.5]

    def to_dict(self) -> Dict:
        """Convert to dictionary for serialization."""
        return {
            "stem_type": self.stem_type,
            "contamination_score": self.contamination_score,
            "transient_integrity": self.transient_integrity,
            "harmonic_purity": self.harmonic_purity,
            "reverb_density": self.reverb_density,
            "stereo_coherence": self.stereo_coherence,
            "snr_estimate": self.snr_estimate,
            "overall_quality": self.overall_quality,
            "is_usable": self.is_usable,
            "issues": self.issues,
            "confidence_regions": [
                {
                    "start": r.start_time,
                    "end": r.end_time,
                    "confidence": r.confidence,
                    "reason": r.reason,
                }
                for r in self.confidence_regions
            ],
        }


class StemQualityAnalyzer:
    """Analyzes quality of separated stems.

    Computes metrics that indicate how trustworthy a stem is
    for downstream tasks like descriptor extraction and MIDI.
    """

    def __init__(
        self,
        hop_length: int = 512,
        frame_length: int = 2048,
        region_duration: float = 1.0,
    ):
        """Initialize the analyzer.

        Args:
            hop_length: Hop length for analysis frames
            frame_length: Frame length for spectral analysis
            region_duration: Duration of confidence regions in seconds
        """
        self.hop_length = hop_length
        self.frame_length = frame_length
        self.region_duration = region_duration

    def analyze(
        self,
        stem_audio: np.ndarray,
        sr: int,
        stem_type: str,
        original_mix: Optional[np.ndarray] = None,
        other_stems: Optional[Dict[str, np.ndarray]] = None,
    ) -> StemQuality:
        """Analyze quality of a single stem.

        Args:
            stem_audio: Stem audio (mono or stereo)
            sr: Sample rate
            stem_type: Type of stem (drums, bass, other, vocals)
            original_mix: Original mix for comparison (optional)
            other_stems: Other stems for cross-bleed detection (optional)

        Returns:
            StemQuality assessment
        """
        import librosa

        # Convert to mono for analysis
        if len(stem_audio.shape) > 1:
            mono = np.mean(stem_audio, axis=0)
            is_stereo = True
        else:
            mono = stem_audio
            is_stereo = False

        # Ensure float32
        mono = mono.astype(np.float32)

        issues = []

        # 1. Contamination Score
        contamination = self._estimate_contamination(
            mono, sr, stem_type, other_stems
        )
        if contamination > 0.5:
            issues.append(f"High contamination detected ({contamination:.2f})")

        # 2. Transient Integrity
        transient_integrity = self._estimate_transient_integrity(mono, sr, stem_type)
        if transient_integrity < 0.5:
            issues.append("Transients appear smeared")

        # 3. Harmonic Purity
        harmonic_purity = self._estimate_harmonic_purity(mono, sr, stem_type)
        if harmonic_purity < 0.5:
            issues.append("Mixed harmonic sources detected")

        # 4. Reverb Density
        reverb_density = self._estimate_reverb_density(mono, sr)
        if reverb_density > 0.7:
            issues.append("Heavy reverb detected")

        # 5. Stereo Coherence
        if is_stereo:
            stereo_coherence = self._estimate_stereo_coherence(stem_audio, sr)
        else:
            stereo_coherence = 1.0  # Mono is fully coherent

        if stereo_coherence < 0.5:
            issues.append("Stereo phase issues detected")

        # 6. SNR Estimate
        snr = self._estimate_snr(mono, sr)
        if snr < 10:
            issues.append(f"Low SNR ({snr:.1f} dB)")

        # 7. Build confidence regions
        confidence_regions = self._build_confidence_regions(
            mono, sr, contamination, transient_integrity, reverb_density
        )

        return StemQuality(
            stem_type=stem_type,
            contamination_score=contamination,
            transient_integrity=transient_integrity,
            harmonic_purity=harmonic_purity,
            reverb_density=reverb_density,
            stereo_coherence=stereo_coherence,
            snr_estimate=snr,
            confidence_regions=confidence_regions,
            issues=issues,
        )

    def analyze_all(
        self,
        stems: Dict[str, np.ndarray],
        sr: int,
        original_mix: Optional[np.ndarray] = None,
    ) -> Dict[str, StemQuality]:
        """Analyze all stems with cross-stem bleed detection.

        Args:
            stems: Dictionary mapping stem_type -> audio array
            sr: Sample rate
            original_mix: Original mix for comparison (optional)

        Returns:
            Dictionary mapping stem_type -> StemQuality
        """
        results = {}

        for stem_type, stem_audio in stems.items():
            # Get other stems for cross-bleed detection
            other_stems = {k: v for k, v in stems.items() if k != stem_type}

            results[stem_type] = self.analyze(
                stem_audio=stem_audio,
                sr=sr,
                stem_type=stem_type,
                original_mix=original_mix,
                other_stems=other_stems,
            )

        return results

    def _estimate_contamination(
        self,
        mono: np.ndarray,
        sr: int,
        stem_type: str,
        other_stems: Optional[Dict[str, np.ndarray]] = None,
    ) -> float:
        """Estimate contamination from other stems.

        Uses spectral analysis to detect energy in frequency ranges
        that shouldn't be present in this stem type.
        """
        import librosa

        # Compute spectrum
        spec = np.abs(librosa.stft(mono, n_fft=self.frame_length, hop_length=self.hop_length))
        freqs = librosa.fft_frequencies(sr=sr, n_fft=self.frame_length)

        # Define expected frequency ranges per stem type
        expected_ranges = {
            "bass": (20, 300),      # Bass should be mostly low frequencies
            "drums": (20, 15000),   # Drums span wide range
            "vocals": (100, 8000),  # Vocals mid-range
            "other": (80, 12000),   # Guitar/synth mid-to-high
        }

        # Define contamination ranges (frequencies that shouldn't be dominant)
        contamination_ranges = {
            "bass": [(2000, 15000)],   # Bass shouldn't have much high content
            "drums": [],               # Drums can have anything
            "vocals": [(20, 80)],      # Vocals shouldn't have sub-bass
            "other": [(20, 60)],       # Guitar/synth shouldn't have sub-bass
        }

        if stem_type not in contamination_ranges:
            return 0.0

        # Calculate energy in contamination ranges
        total_energy = np.sum(spec ** 2)
        if total_energy == 0:
            return 0.0

        contamination_energy = 0.0
        for low, high in contamination_ranges.get(stem_type, []):
            mask = (freqs >= low) & (freqs <= high)
            contamination_energy += np.sum(spec[mask, :] ** 2)

        contamination = contamination_energy / total_energy

        # If we have other stems, check for spectral correlation (bleed)
        if other_stems:
            for other_type, other_audio in other_stems.items():
                if len(other_audio.shape) > 1:
                    other_mono = np.mean(other_audio, axis=0)
                else:
                    other_mono = other_audio

                bleed = self._estimate_spectral_bleed(mono, other_mono, sr)
                contamination = max(contamination, bleed * 0.5)

        return min(1.0, contamination)

    def _estimate_spectral_bleed(
        self,
        stem: np.ndarray,
        other: np.ndarray,
        sr: int,
    ) -> float:
        """Estimate spectral bleed between two stems."""
        import librosa

        # Compute spectrograms
        spec1 = np.abs(librosa.stft(stem, n_fft=self.frame_length, hop_length=self.hop_length))
        spec2 = np.abs(librosa.stft(other, n_fft=self.frame_length, hop_length=self.hop_length))

        # Truncate to same length
        min_len = min(spec1.shape[1], spec2.shape[1])
        spec1 = spec1[:, :min_len]
        spec2 = spec2[:, :min_len]

        # Compute correlation per frequency bin
        # High correlation suggests bleed
        correlations = []
        for i in range(spec1.shape[0]):
            if np.std(spec1[i]) > 0 and np.std(spec2[i]) > 0:
                corr = np.corrcoef(spec1[i], spec2[i])[0, 1]
                if not np.isnan(corr):
                    correlations.append(max(0, corr))

        if not correlations:
            return 0.0

        # Average positive correlation as bleed indicator
        return np.mean(correlations)

    def _estimate_transient_integrity(
        self,
        mono: np.ndarray,
        sr: int,
        stem_type: str,
    ) -> float:
        """Estimate how well transients are preserved.

        Source separation often smears transients. We detect this by
        analyzing onset sharpness.
        """
        import librosa

        # Compute onset strength
        onset_env = librosa.onset.onset_strength(
            y=mono, sr=sr, hop_length=self.hop_length
        )

        if len(onset_env) == 0 or np.max(onset_env) == 0:
            return 0.5  # Neutral if no onsets

        # Normalize
        onset_env = onset_env / np.max(onset_env)

        # Find peaks
        peaks = []
        for i in range(1, len(onset_env) - 1):
            if onset_env[i] > onset_env[i - 1] and onset_env[i] > onset_env[i + 1]:
                if onset_env[i] > 0.3:  # Threshold for significant onset
                    peaks.append(i)

        if len(peaks) == 0:
            return 0.5

        # Measure sharpness: ratio of peak to surrounding frames
        sharpness_scores = []
        for peak in peaks:
            # Look at 3 frames before and after
            window_start = max(0, peak - 3)
            window_end = min(len(onset_env), peak + 4)
            window = onset_env[window_start:window_end]

            peak_value = onset_env[peak]
            mean_surround = (np.sum(window) - peak_value) / (len(window) - 1 + 1e-8)

            if mean_surround > 0:
                sharpness = peak_value / mean_surround
                sharpness_scores.append(min(sharpness / 3.0, 1.0))  # Normalize

        if not sharpness_scores:
            return 0.5

        # Drums and bass need sharper transients
        base_score = np.mean(sharpness_scores)

        if stem_type in ("drums", "bass"):
            # Penalize more for smeared transients
            return base_score
        else:
            # Pads and sustained sounds can have softer transients
            return 0.3 + 0.7 * base_score

    def _estimate_harmonic_purity(
        self,
        mono: np.ndarray,
        sr: int,
        stem_type: str,
    ) -> float:
        """Estimate whether stem contains single or mixed harmonic sources.

        Mixed sources (guitar + synth in 'other' stem) have more
        complex/inconsistent harmonic structure.
        """
        import librosa

        # Use harmonic-percussive separation
        harmonic, percussive = librosa.effects.hpss(mono)

        # Compute harmonic ratio
        h_energy = np.sum(harmonic ** 2)
        p_energy = np.sum(percussive ** 2)
        total = h_energy + p_energy

        if total == 0:
            return 0.5

        harmonic_ratio = h_energy / total

        # For drums, we expect mostly percussive
        if stem_type == "drums":
            # High percussive content = good purity
            return 1 - harmonic_ratio

        # For other stems, check harmonic consistency
        # Use chroma features to see if harmonics are consistent
        chroma = librosa.feature.chroma_stft(
            y=harmonic, sr=sr, hop_length=self.hop_length
        )

        # High variance in chroma suggests mixed sources
        chroma_var = np.mean(np.var(chroma, axis=1))

        # More consistent chroma = higher purity
        purity = 1 / (1 + chroma_var * 5)

        return purity

    def _estimate_reverb_density(
        self,
        mono: np.ndarray,
        sr: int,
    ) -> float:
        """Estimate proportion of reverberant content.

        High reverb density makes MIDI extraction harder.
        """
        import librosa

        # Compute RMS envelope
        rms = librosa.feature.rms(
            y=mono, frame_length=self.frame_length, hop_length=self.hop_length
        )[0]

        if len(rms) == 0 or np.max(rms) == 0:
            return 0.0

        # Normalize
        rms = rms / np.max(rms)

        # Identify "quiet" frames (between notes/hits)
        threshold = 0.15
        quiet_frames = rms < threshold
        loud_frames = rms >= threshold

        if np.sum(loud_frames) == 0:
            return 0.5

        # Reverb shows as energy in quiet frames
        # High ratio of quiet frame energy = high reverb
        quiet_energy = np.sum(rms[quiet_frames]) if np.any(quiet_frames) else 0
        loud_energy = np.sum(rms[loud_frames])

        reverb_ratio = quiet_energy / (loud_energy + 1e-8)

        # Also check decay characteristics
        # Reverb has gradual decay after loud frames
        decay_score = self._estimate_decay_smoothness(rms)

        return min(1.0, (reverb_ratio * 0.7 + decay_score * 0.3))

    def _estimate_decay_smoothness(self, rms: np.ndarray) -> float:
        """Estimate how smooth the decay is (reverb indicator)."""
        if len(rms) < 10:
            return 0.0

        # Find peaks and measure decay smoothness
        peaks = []
        for i in range(1, len(rms) - 1):
            if rms[i] > rms[i - 1] and rms[i] > rms[i + 1]:
                if rms[i] > 0.3:
                    peaks.append(i)

        if len(peaks) == 0:
            return 0.0

        smoothness_scores = []
        for peak in peaks:
            # Look at decay after peak
            decay_start = peak
            decay_end = min(len(rms), peak + 20)  # 20 frames of decay

            if decay_end - decay_start < 5:
                continue

            decay = rms[decay_start:decay_end]

            # Smooth decay = reverb, choppy decay = dry
            diffs = np.diff(decay)
            # Reverb: mostly negative diffs (decaying), few positive spikes
            neg_ratio = np.sum(diffs < 0) / (len(diffs) + 1e-8)
            smoothness_scores.append(neg_ratio)

        if not smoothness_scores:
            return 0.0

        return np.mean(smoothness_scores)

    def _estimate_stereo_coherence(
        self,
        stereo: np.ndarray,
        sr: int,
    ) -> float:
        """Estimate stereo coherence (phase stability)."""
        if len(stereo.shape) != 2 or stereo.shape[0] != 2:
            return 1.0  # Mono is fully coherent

        left = stereo[0]
        right = stereo[1]

        # Compute correlation between channels
        if np.std(left) == 0 or np.std(right) == 0:
            return 1.0

        correlation = np.corrcoef(left, right)[0, 1]
        if np.isnan(correlation):
            return 0.5

        # Also check mid-side ratio
        mid = (left + right) / 2
        side = (left - right) / 2

        mid_energy = np.sum(mid ** 2)
        side_energy = np.sum(side ** 2)
        total = mid_energy + side_energy

        if total == 0:
            return 1.0

        # Very wide stereo (high side) can indicate phase issues from separation
        side_ratio = side_energy / total

        # Coherence combines correlation and reasonable stereo width
        coherence = correlation * (1 - 0.5 * min(side_ratio, 0.5))

        return max(0, min(1, coherence))

    def _estimate_snr(
        self,
        mono: np.ndarray,
        sr: int,
    ) -> float:
        """Estimate signal-to-noise ratio in dB."""
        import librosa

        # Compute RMS
        rms = librosa.feature.rms(
            y=mono, frame_length=self.frame_length, hop_length=self.hop_length
        )[0]

        if len(rms) == 0:
            return 0.0

        # Estimate signal as top 10% RMS values
        signal_threshold = np.percentile(rms, 90)
        signal_frames = rms[rms >= signal_threshold]

        # Estimate noise as bottom 10% RMS values
        noise_threshold = np.percentile(rms, 10)
        noise_frames = rms[rms <= noise_threshold]

        if len(signal_frames) == 0 or len(noise_frames) == 0:
            return 20.0  # Default reasonable SNR

        signal_rms = np.mean(signal_frames)
        noise_rms = np.mean(noise_frames)

        if noise_rms == 0:
            return 40.0  # Very clean

        snr_linear = signal_rms / noise_rms
        snr_db = 20 * np.log10(snr_linear + 1e-8)

        return max(0, min(60, snr_db))  # Clamp to reasonable range

    def _build_confidence_regions(
        self,
        mono: np.ndarray,
        sr: int,
        contamination: float,
        transient_integrity: float,
        reverb_density: float,
    ) -> List[ConfidenceRegion]:
        """Build time-segmented confidence regions."""
        import librosa

        duration = len(mono) / sr
        num_regions = max(1, int(duration / self.region_duration))

        regions = []
        samples_per_region = len(mono) // num_regions

        for i in range(num_regions):
            start_sample = i * samples_per_region
            end_sample = min((i + 1) * samples_per_region, len(mono))

            start_time = start_sample / sr
            end_time = end_sample / sr

            segment = mono[start_sample:end_sample]

            # Compute local quality metrics
            local_rms = np.sqrt(np.mean(segment ** 2))

            # Very quiet regions have lower confidence
            if local_rms < 0.01:
                confidence = 0.3
                reason = "Very quiet region"
            else:
                # Base confidence from global metrics
                confidence = (
                    0.4 +
                    0.2 * (1 - contamination) +
                    0.2 * transient_integrity +
                    0.2 * (1 - reverb_density * 0.5)
                )

                # Check for local issues
                local_onset_env = librosa.onset.onset_strength(
                    y=segment, sr=sr, hop_length=self.hop_length
                )

                if len(local_onset_env) > 0 and np.max(local_onset_env) < 0.1:
                    confidence *= 0.8
                    reason = "Low activity"
                else:
                    reason = ""

            regions.append(ConfidenceRegion(
                start_time=start_time,
                end_time=end_time,
                confidence=max(0, min(1, confidence)),
                reason=reason,
            ))

        return regions


# Module-level singleton
_analyzer: Optional[StemQualityAnalyzer] = None


def get_analyzer(
    hop_length: int = 512,
    frame_length: int = 2048,
    region_duration: float = 1.0,
) -> StemQualityAnalyzer:
    """Get or create the global StemQualityAnalyzer instance."""
    global _analyzer

    if _analyzer is None:
        _analyzer = StemQualityAnalyzer(
            hop_length=hop_length,
            frame_length=frame_length,
            region_duration=region_duration,
        )

    return _analyzer


def analyze_stem_quality(
    stems: Dict[str, np.ndarray],
    sr: int,
    original_mix: Optional[np.ndarray] = None,
) -> Dict[str, StemQuality]:
    """Convenience function to analyze all stems."""
    return get_analyzer().analyze_all(stems, sr, original_mix)
