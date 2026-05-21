"""Artifact detection for separated stems.

Detects processing artifacts from source separation:
- Spectral smearing
- Musical noise (isolated spectral peaks)
- Transient artifacts
- Stereo artifacts
- Quantization artifacts
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


class ArtifactType(str, Enum):
    """Types of processing artifacts."""

    SPECTRAL_SMEARING = "spectral_smearing"
    MUSICAL_NOISE = "musical_noise"
    TRANSIENT_SMEARING = "transient_smearing"
    STEREO_ARTIFACT = "stereo_artifact"
    ALIASING = "aliasing"
    QUANTIZATION_NOISE = "quantization_noise"
    SPECTRAL_HOLE = "spectral_hole"
    PRE_ECHO = "pre_echo"


@dataclass
class DetectedArtifact:
    """A detected processing artifact."""

    artifact_type: ArtifactType
    time_start: float
    time_end: float
    severity: float  # 0-1
    confidence: float  # 0-1
    frequency_range: Optional[Tuple[float, float]] = None
    description: str = ""

    @property
    def duration(self) -> float:
        """Duration of the artifact."""
        return self.time_end - self.time_start


@dataclass
class ArtifactAnalysis:
    """Complete artifact analysis for a stem."""

    stem_type: str
    artifacts: List[DetectedArtifact] = field(default_factory=list)
    overall_artifact_score: float = 0.0  # 0-1, higher = more artifacts
    artifact_by_type: Dict[ArtifactType, float] = field(default_factory=dict)
    clean_regions: List[Tuple[float, float]] = field(default_factory=list)

    @property
    def artifact_count(self) -> int:
        """Total number of detected artifacts."""
        return len(self.artifacts)

    @property
    def total_artifact_duration(self) -> float:
        """Total duration affected by artifacts."""
        return sum(a.duration for a in self.artifacts)

    def get_artifacts_in_range(
        self,
        start: float,
        end: float,
    ) -> List[DetectedArtifact]:
        """Get artifacts within a time range."""
        return [
            a for a in self.artifacts
            if a.time_start < end and a.time_end > start
        ]

    def get_artifacts_by_type(
        self,
        artifact_type: ArtifactType,
    ) -> List[DetectedArtifact]:
        """Get artifacts of a specific type."""
        return [a for a in self.artifacts if a.artifact_type == artifact_type]


class ArtifactDetector:
    """Detect processing artifacts in separated stems.

    Identifies various artifacts introduced by source separation
    and other audio processing.
    """

    def __init__(
        self,
        hop_length: int = 512,
        n_fft: int = 2048,
        musical_noise_threshold: float = 0.3,
        smearing_threshold: float = 0.4,
    ):
        """Initialize the artifact detector.

        Args:
            hop_length: Hop length for analysis
            n_fft: FFT size
            musical_noise_threshold: Threshold for musical noise detection
            smearing_threshold: Threshold for smearing detection
        """
        self.hop_length = hop_length
        self.n_fft = n_fft
        self.musical_noise_threshold = musical_noise_threshold
        self.smearing_threshold = smearing_threshold

    def detect(
        self,
        stem_audio: np.ndarray,
        sr: int,
        stem_type: str,
        original_mix: Optional[np.ndarray] = None,
    ) -> ArtifactAnalysis:
        """Detect artifacts in a stem.

        Args:
            stem_audio: Audio data for the stem (mono or stereo)
            sr: Sample rate
            stem_type: Type of stem
            original_mix: Optional original mix for comparison

        Returns:
            ArtifactAnalysis with detected artifacts
        """
        # Convert to mono for most analysis
        if stem_audio.ndim == 2:
            audio_mono = np.mean(stem_audio, axis=0)
        else:
            audio_mono = stem_audio

        artifacts: List[DetectedArtifact] = []
        artifact_by_type: Dict[ArtifactType, float] = {}

        # Detect various artifact types
        spectral_artifacts = self._detect_spectral_smearing(audio_mono, sr)
        artifacts.extend(spectral_artifacts)

        noise_artifacts = self._detect_musical_noise(audio_mono, sr)
        artifacts.extend(noise_artifacts)

        transient_artifacts = self._detect_transient_smearing(audio_mono, sr, stem_type)
        artifacts.extend(transient_artifacts)

        if stem_audio.ndim == 2:
            stereo_artifacts = self._detect_stereo_artifacts(stem_audio, sr)
            artifacts.extend(stereo_artifacts)

        hole_artifacts = self._detect_spectral_holes(audio_mono, sr)
        artifacts.extend(hole_artifacts)

        pre_echo = self._detect_pre_echo(audio_mono, sr)
        artifacts.extend(pre_echo)

        # Aggregate by type
        for atype in ArtifactType:
            type_artifacts = [a for a in artifacts if a.artifact_type == atype]
            if type_artifacts:
                artifact_by_type[atype] = np.mean([a.severity for a in type_artifacts])

        # Calculate overall artifact score
        if artifacts:
            overall = np.mean([a.severity * a.confidence for a in artifacts])
        else:
            overall = 0.0

        # Find clean regions
        duration = len(audio_mono) / sr
        clean_regions = self._find_clean_regions(artifacts, duration)

        return ArtifactAnalysis(
            stem_type=stem_type,
            artifacts=artifacts,
            overall_artifact_score=float(overall),
            artifact_by_type=artifact_by_type,
            clean_regions=clean_regions,
        )

    def detect_all(
        self,
        stems: Dict[str, np.ndarray],
        sr: int,
        original_mix: Optional[np.ndarray] = None,
    ) -> Dict[str, ArtifactAnalysis]:
        """Detect artifacts in all stems.

        Args:
            stems: Dictionary mapping stem type to audio
            sr: Sample rate
            original_mix: Optional original mix

        Returns:
            Dictionary mapping stem type to ArtifactAnalysis
        """
        results = {}

        for stem_type, stem_audio in stems.items():
            results[stem_type] = self.detect(
                stem_audio=stem_audio,
                sr=sr,
                stem_type=stem_type,
                original_mix=original_mix,
            )

        return results

    def _detect_spectral_smearing(
        self,
        audio: np.ndarray,
        sr: int,
    ) -> List[DetectedArtifact]:
        """Detect spectral smearing artifacts."""
        artifacts = []

        try:
            import librosa
        except ImportError:
            logger.warning("librosa not available, skipping spectral smearing detection")
            return artifacts

        # Compute spectrogram
        spec = np.abs(librosa.stft(audio, n_fft=self.n_fft, hop_length=self.hop_length))

        # Spectral smearing: energy spread across adjacent bins
        # Look for frames where spectral peaks are wider than expected

        n_bins, n_frames = spec.shape
        smearing_scores = np.zeros(n_frames)

        for i in range(n_frames):
            frame = spec[:, i]

            # Find peaks
            peaks = self._find_spectral_peaks(frame)

            if len(peaks) > 0:
                # Measure width of peaks
                widths = []
                for peak_idx in peaks:
                    width = self._measure_peak_width(frame, peak_idx)
                    widths.append(width)

                # Average width - higher means more smearing
                avg_width = np.mean(widths) if widths else 0
                smearing_scores[i] = min(1.0, avg_width / 10.0)  # Normalize

        # Find regions of high smearing
        smearing_frames = smearing_scores > self.smearing_threshold

        regions = self._frames_to_regions(smearing_frames, sr, self.hop_length)

        for start, end in regions:
            start_frame = int(start * sr / self.hop_length)
            end_frame = min(int(end * sr / self.hop_length), len(smearing_scores))
            avg_score = np.mean(smearing_scores[start_frame:end_frame])

            artifacts.append(DetectedArtifact(
                artifact_type=ArtifactType.SPECTRAL_SMEARING,
                time_start=start,
                time_end=end,
                severity=float(avg_score),
                confidence=0.6,
                description="Spectral smearing detected",
            ))

        return artifacts

    def _detect_musical_noise(
        self,
        audio: np.ndarray,
        sr: int,
    ) -> List[DetectedArtifact]:
        """Detect musical noise (isolated spectral peaks that flicker)."""
        artifacts = []

        try:
            import librosa
        except ImportError:
            logger.warning("librosa not available, skipping musical noise detection")
            return artifacts

        # Compute spectrogram
        spec = np.abs(librosa.stft(audio, n_fft=self.n_fft, hop_length=self.hop_length))

        n_bins, n_frames = spec.shape

        # Musical noise: isolated peaks that appear/disappear rapidly
        # Look for high temporal variance in individual bins

        musical_noise_score = np.zeros(n_frames)

        # Window for temporal analysis
        window_size = 5

        for i in range(window_size, n_frames - window_size):
            # Get window of frames
            window = spec[:, i-window_size:i+window_size+1]

            # Calculate temporal variance per bin
            temporal_var = np.var(window, axis=1)

            # Calculate mean energy per bin
            mean_energy = np.mean(window, axis=1)

            # Normalized variance (coefficient of variation)
            with np.errstate(divide='ignore', invalid='ignore'):
                cv = temporal_var / (mean_energy + 1e-10)
                cv = np.nan_to_num(cv, nan=0.0)

            # High CV in bins with some energy indicates musical noise
            noise_bins = (cv > 1.0) & (mean_energy > np.percentile(mean_energy, 50))

            if np.any(noise_bins):
                musical_noise_score[i] = np.mean(cv[noise_bins])

        # Normalize
        if np.max(musical_noise_score) > 0:
            musical_noise_score = musical_noise_score / np.max(musical_noise_score)

        # Find regions
        noise_frames = musical_noise_score > self.musical_noise_threshold

        regions = self._frames_to_regions(noise_frames, sr, self.hop_length)

        for start, end in regions:
            start_frame = int(start * sr / self.hop_length)
            end_frame = min(int(end * sr / self.hop_length), len(musical_noise_score))
            avg_score = np.mean(musical_noise_score[start_frame:end_frame])

            artifacts.append(DetectedArtifact(
                artifact_type=ArtifactType.MUSICAL_NOISE,
                time_start=start,
                time_end=end,
                severity=float(avg_score),
                confidence=0.7,
                description="Musical noise (flickering spectral peaks) detected",
            ))

        return artifacts

    def _detect_transient_smearing(
        self,
        audio: np.ndarray,
        sr: int,
        stem_type: str,
    ) -> List[DetectedArtifact]:
        """Detect transient smearing (blurred attacks)."""
        artifacts = []

        try:
            import librosa
        except ImportError:
            logger.warning("librosa not available, skipping transient smearing detection")
            return artifacts

        # More important for drums, less for pads
        if stem_type in ("vocals", "other"):
            severity_multiplier = 0.5
        elif stem_type == "drums":
            severity_multiplier = 1.5
        else:
            severity_multiplier = 1.0

        # Detect onsets
        onset_env = librosa.onset.onset_strength(y=audio, sr=sr, hop_length=self.hop_length)
        onset_frames = librosa.onset.onset_detect(
            onset_envelope=onset_env,
            sr=sr,
            hop_length=self.hop_length,
            backtrack=False
        )

        # For each onset, analyze the attack shape
        for onset_frame in onset_frames:
            onset_sample = onset_frame * self.hop_length
            attack_window = int(0.01 * sr)  # 10ms attack window

            if onset_sample + attack_window > len(audio):
                continue

            # Get attack region
            pre_attack = audio[max(0, onset_sample - attack_window//2):onset_sample]
            attack = audio[onset_sample:onset_sample + attack_window]

            if len(attack) < 10 or len(pre_attack) < 10:
                continue

            # Calculate attack slope
            attack_env = np.abs(attack)
            attack_slope = np.max(attack_env) - np.mean(np.abs(pre_attack))

            # Calculate attack time (time to reach 90% of peak)
            peak_val = np.max(attack_env)
            threshold_90 = peak_val * 0.9

            attack_time_samples = np.argmax(attack_env >= threshold_90)
            attack_time_ms = attack_time_samples / sr * 1000

            # Smeared transients have slow attack times
            # For drums, expect < 5ms, for bass < 20ms
            if stem_type == "drums":
                expected_attack_ms = 5
            else:
                expected_attack_ms = 20

            if attack_time_ms > expected_attack_ms * 2:
                # Transient is smeared
                onset_time = onset_sample / sr
                severity = min(1.0, (attack_time_ms - expected_attack_ms) / (expected_attack_ms * 3))
                severity *= severity_multiplier

                artifacts.append(DetectedArtifact(
                    artifact_type=ArtifactType.TRANSIENT_SMEARING,
                    time_start=onset_time - 0.01,
                    time_end=onset_time + attack_time_ms / 1000 + 0.01,
                    severity=float(severity),
                    confidence=0.6,
                    description=f"Transient smearing: {attack_time_ms:.1f}ms attack (expected < {expected_attack_ms}ms)",
                ))

        return artifacts

    def _detect_stereo_artifacts(
        self,
        audio: np.ndarray,
        sr: int,
    ) -> List[DetectedArtifact]:
        """Detect stereo field artifacts."""
        artifacts = []

        if audio.ndim != 2 or audio.shape[0] != 2:
            return artifacts

        left = audio[0]
        right = audio[1]

        # Compute mid/side
        mid = (left + right) / 2
        side = (left - right) / 2

        # Check for unnatural stereo width changes
        frame_length = 2048
        hop = 512
        n_frames = (len(left) - frame_length) // hop + 1

        width_values = []
        for i in range(n_frames):
            start = i * hop
            end = start + frame_length

            mid_energy = np.sum(mid[start:end] ** 2)
            side_energy = np.sum(side[start:end] ** 2)

            # Width: ratio of side to total energy
            total_energy = mid_energy + side_energy
            if total_energy > 1e-10:
                width = side_energy / total_energy
            else:
                width = 0.5

            width_values.append(width)

        width_values = np.array(width_values)

        # Look for sudden width changes (artifact of separation)
        width_diff = np.abs(np.diff(width_values))

        # Find frames with sudden changes
        artifact_frames = np.concatenate([[False], width_diff > 0.3])

        regions = self._frames_to_regions(artifact_frames, sr, hop)

        for start, end in regions:
            artifacts.append(DetectedArtifact(
                artifact_type=ArtifactType.STEREO_ARTIFACT,
                time_start=start,
                time_end=end,
                severity=0.6,
                confidence=0.5,
                description="Stereo width discontinuity detected",
            ))

        return artifacts

    def _detect_spectral_holes(
        self,
        audio: np.ndarray,
        sr: int,
    ) -> List[DetectedArtifact]:
        """Detect spectral holes (missing frequency content)."""
        artifacts = []

        try:
            import librosa
        except ImportError:
            logger.warning("librosa not available, skipping spectral hole detection")
            return artifacts

        # Compute spectrogram
        spec = np.abs(librosa.stft(audio, n_fft=self.n_fft, hop_length=self.hop_length))

        n_bins, n_frames = spec.shape

        # Look for frames with unnatural spectral gaps
        hole_scores = np.zeros(n_frames)

        for i in range(n_frames):
            frame = spec[:, i]

            if np.max(frame) < 1e-10:
                continue

            # Normalize frame
            frame_norm = frame / np.max(frame)

            # Find "holes" - regions significantly lower than neighbors
            # Smooth the spectrum first
            from scipy.ndimage import uniform_filter1d
            smoothed = uniform_filter1d(frame_norm, size=10)

            # Find regions where actual is much lower than smoothed
            ratio = frame_norm / (smoothed + 1e-10)

            # Holes are where ratio is very low but neighbors are high
            hole_mask = ratio < 0.3
            hole_count = np.sum(hole_mask)

            # Score based on number and depth of holes
            if hole_count > 5:  # Multiple holes
                avg_depth = np.mean(1 - ratio[hole_mask])
                hole_scores[i] = min(1.0, (hole_count / 50) * avg_depth)

        # Find regions with holes
        hole_frames = hole_scores > 0.2

        regions = self._frames_to_regions(hole_frames, sr, self.hop_length)

        for start, end in regions:
            start_frame = int(start * sr / self.hop_length)
            end_frame = min(int(end * sr / self.hop_length), len(hole_scores))
            avg_score = np.mean(hole_scores[start_frame:end_frame])

            artifacts.append(DetectedArtifact(
                artifact_type=ArtifactType.SPECTRAL_HOLE,
                time_start=start,
                time_end=end,
                severity=float(avg_score),
                confidence=0.5,
                description="Spectral holes detected (missing frequency content)",
            ))

        return artifacts

    def _detect_pre_echo(
        self,
        audio: np.ndarray,
        sr: int,
    ) -> List[DetectedArtifact]:
        """Detect pre-echo artifacts."""
        artifacts = []

        try:
            import librosa
        except ImportError:
            logger.warning("librosa not available, skipping pre-echo detection")
            return artifacts

        # Detect onsets
        onset_env = librosa.onset.onset_strength(y=audio, sr=sr, hop_length=self.hop_length)
        onset_frames = librosa.onset.onset_detect(
            onset_envelope=onset_env,
            sr=sr,
            hop_length=self.hop_length,
            backtrack=False
        )

        # For each onset, check for pre-echo
        pre_echo_window = int(0.02 * sr)  # 20ms before onset

        for onset_frame in onset_frames:
            onset_sample = onset_frame * self.hop_length

            if onset_sample < pre_echo_window:
                continue

            # Get pre-onset region
            pre_onset = audio[onset_sample - pre_echo_window:onset_sample]
            post_onset = audio[onset_sample:min(onset_sample + pre_echo_window, len(audio))]

            if len(pre_onset) < 10 or len(post_onset) < 10:
                continue

            # Calculate energies
            pre_energy = np.mean(pre_onset ** 2)
            post_energy = np.mean(post_onset ** 2)

            if post_energy < 1e-10:
                continue

            # Pre-echo: significant energy before the onset that correlates with post-onset
            energy_ratio = pre_energy / post_energy

            # Also check correlation (pre-echo often similar to post-onset)
            min_len = min(len(pre_onset), len(post_onset))
            corr = np.corrcoef(pre_onset[:min_len], post_onset[:min_len])[0, 1]

            # Pre-echo: high correlation and moderate energy before onset
            if corr > 0.5 and energy_ratio > 0.1:
                onset_time = onset_sample / sr
                severity = min(1.0, corr * energy_ratio * 2)

                artifacts.append(DetectedArtifact(
                    artifact_type=ArtifactType.PRE_ECHO,
                    time_start=onset_time - 0.02,
                    time_end=onset_time,
                    severity=float(severity),
                    confidence=float(corr),
                    description=f"Pre-echo detected before transient",
                ))

        return artifacts

    def _find_spectral_peaks(
        self,
        frame: np.ndarray,
        threshold_ratio: float = 0.1,
    ) -> List[int]:
        """Find spectral peaks in a frame."""
        threshold = np.max(frame) * threshold_ratio

        # Simple peak finding
        peaks = []
        for i in range(1, len(frame) - 1):
            if frame[i] > frame[i-1] and frame[i] > frame[i+1] and frame[i] > threshold:
                peaks.append(i)

        return peaks

    def _measure_peak_width(
        self,
        frame: np.ndarray,
        peak_idx: int,
    ) -> float:
        """Measure the width of a spectral peak."""
        peak_val = frame[peak_idx]
        threshold = peak_val * 0.5  # -6dB width

        # Find left edge
        left = peak_idx
        while left > 0 and frame[left] > threshold:
            left -= 1

        # Find right edge
        right = peak_idx
        while right < len(frame) - 1 and frame[right] > threshold:
            right += 1

        return right - left

    def _frames_to_regions(
        self,
        frame_mask: np.ndarray,
        sr: int,
        hop_length: int,
    ) -> List[Tuple[float, float]]:
        """Convert boolean frame mask to time regions."""
        regions = []
        in_region = False
        start_frame = 0

        for i, active in enumerate(frame_mask):
            if active and not in_region:
                in_region = True
                start_frame = i
            elif not active and in_region:
                in_region = False
                start_time = start_frame * hop_length / sr
                end_time = i * hop_length / sr
                if end_time - start_time > 0.02:  # Minimum 20ms
                    regions.append((start_time, end_time))

        # Handle region at end
        if in_region:
            start_time = start_frame * hop_length / sr
            end_time = len(frame_mask) * hop_length / sr
            if end_time - start_time > 0.02:
                regions.append((start_time, end_time))

        return regions

    def _find_clean_regions(
        self,
        artifacts: List[DetectedArtifact],
        duration: float,
    ) -> List[Tuple[float, float]]:
        """Find regions without artifacts."""
        if not artifacts:
            return [(0.0, duration)]

        # Sort by start time
        sorted_artifacts = sorted(artifacts, key=lambda a: a.time_start)

        clean_regions = []
        current_pos = 0.0

        for artifact in sorted_artifacts:
            if artifact.time_start > current_pos:
                if artifact.time_start - current_pos > 0.05:  # Min 50ms
                    clean_regions.append((current_pos, artifact.time_start))
            current_pos = max(current_pos, artifact.time_end)

        if current_pos < duration - 0.05:
            clean_regions.append((current_pos, duration))

        return clean_regions


# Module-level singleton
_detector: Optional[ArtifactDetector] = None


def get_artifact_detector() -> ArtifactDetector:
    """Get the global artifact detector instance."""
    global _detector
    if _detector is None:
        _detector = ArtifactDetector()
    return _detector


def detect_artifacts(
    stem_audio: np.ndarray,
    sr: int,
    stem_type: str,
    original_mix: Optional[np.ndarray] = None,
) -> ArtifactAnalysis:
    """Convenience function to detect artifacts in a stem.

    Args:
        stem_audio: Audio data for the stem
        sr: Sample rate
        stem_type: Type of stem
        original_mix: Optional original mix

    Returns:
        ArtifactAnalysis
    """
    detector = get_artifact_detector()
    return detector.detect(
        stem_audio=stem_audio,
        sr=sr,
        stem_type=stem_type,
        original_mix=original_mix,
    )
