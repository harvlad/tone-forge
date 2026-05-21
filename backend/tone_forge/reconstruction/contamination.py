"""Contamination detection for separated stems.

Detects various types of contamination in separated stems:
- Cross-stem bleed (bass in drums, guitar in bass)
- Reverb tails from other instruments
- Delay artifacts
- Harmonic confusion (octave errors)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


class ContaminationType(str, Enum):
    """Types of contamination detected in stems."""

    CROSS_STEM_BLEED = "cross_stem_bleed"
    REVERB_TAIL = "reverb_tail"
    DELAY_ARTIFACT = "delay_artifact"
    HARMONIC_CONFUSION = "harmonic_confusion"
    PHASE_ARTIFACT = "phase_artifact"
    ALIASING = "aliasing"
    SPECTRAL_SMEARING = "spectral_smearing"


@dataclass
class ContaminationEvent:
    """A detected contamination event."""

    contamination_type: ContaminationType
    time_start: float
    time_end: float
    severity: float  # 0-1
    confidence: float  # 0-1
    source_stem: Optional[str] = None  # For cross-stem bleed
    frequency_range: Optional[Tuple[float, float]] = None
    description: str = ""

    @property
    def duration(self) -> float:
        """Duration of the contamination event."""
        return self.time_end - self.time_start


@dataclass
class ContaminationAnalysis:
    """Complete contamination analysis for a stem."""

    stem_type: str
    events: List[ContaminationEvent] = field(default_factory=list)
    overall_contamination: float = 0.0  # 0-1
    contamination_by_type: Dict[ContaminationType, float] = field(default_factory=dict)
    clean_regions: List[Tuple[float, float]] = field(default_factory=list)

    @property
    def event_count(self) -> int:
        """Total number of contamination events."""
        return len(self.events)

    @property
    def total_contaminated_duration(self) -> float:
        """Total duration of contaminated regions."""
        return sum(e.duration for e in self.events)

    def get_events_in_range(
        self,
        start: float,
        end: float,
    ) -> List[ContaminationEvent]:
        """Get contamination events within a time range."""
        return [
            e for e in self.events
            if e.time_start < end and e.time_end > start
        ]

    def get_events_by_type(
        self,
        contamination_type: ContaminationType,
    ) -> List[ContaminationEvent]:
        """Get events of a specific type."""
        return [e for e in self.events if e.contamination_type == contamination_type]


class ContaminationDetector:
    """Detect contamination in separated stems.

    Analyzes stems for various types of contamination including
    cross-stem bleed, reverb tails, delay artifacts, and harmonic
    confusion.
    """

    def __init__(
        self,
        hop_length: int = 512,
        n_fft: int = 2048,
        bleed_threshold: float = 0.15,
        reverb_decay_threshold: float = 0.3,
        delay_correlation_threshold: float = 0.6,
    ):
        """Initialize the contamination detector.

        Args:
            hop_length: Hop length for analysis
            n_fft: FFT size
            bleed_threshold: Threshold for cross-stem bleed detection
            reverb_decay_threshold: Threshold for reverb tail detection
            delay_correlation_threshold: Threshold for delay artifact detection
        """
        self.hop_length = hop_length
        self.n_fft = n_fft
        self.bleed_threshold = bleed_threshold
        self.reverb_decay_threshold = reverb_decay_threshold
        self.delay_correlation_threshold = delay_correlation_threshold

    def detect(
        self,
        stem_audio: np.ndarray,
        sr: int,
        stem_type: str,
        other_stems: Optional[Dict[str, np.ndarray]] = None,
        original_mix: Optional[np.ndarray] = None,
    ) -> ContaminationAnalysis:
        """Detect contamination in a stem.

        Args:
            stem_audio: Audio data for the stem (mono or stereo)
            sr: Sample rate
            stem_type: Type of stem (bass, drums, vocals, other)
            other_stems: Optional dict of other separated stems
            original_mix: Optional original mix for comparison

        Returns:
            ContaminationAnalysis with detected contamination events
        """
        # Convert to mono if stereo
        if stem_audio.ndim == 2:
            audio = np.mean(stem_audio, axis=0)
        else:
            audio = stem_audio

        events: List[ContaminationEvent] = []
        contamination_by_type: Dict[ContaminationType, float] = {}

        # Detect various contamination types
        bleed_events = self._detect_cross_stem_bleed(
            audio, sr, stem_type, other_stems
        )
        events.extend(bleed_events)

        reverb_events = self._detect_reverb_tails(audio, sr, stem_type)
        events.extend(reverb_events)

        delay_events = self._detect_delay_artifacts(audio, sr)
        events.extend(delay_events)

        harmonic_events = self._detect_harmonic_confusion(audio, sr, stem_type)
        events.extend(harmonic_events)

        phase_events = self._detect_phase_artifacts(stem_audio, sr)
        events.extend(phase_events)

        # Aggregate contamination by type
        for ctype in ContaminationType:
            type_events = [e for e in events if e.contamination_type == ctype]
            if type_events:
                contamination_by_type[ctype] = np.mean([e.severity for e in type_events])

        # Calculate overall contamination
        if events:
            overall = np.mean([e.severity * e.confidence for e in events])
        else:
            overall = 0.0

        # Find clean regions
        duration = len(audio) / sr
        clean_regions = self._find_clean_regions(events, duration)

        return ContaminationAnalysis(
            stem_type=stem_type,
            events=events,
            overall_contamination=float(overall),
            contamination_by_type=contamination_by_type,
            clean_regions=clean_regions,
        )

    def detect_all(
        self,
        stems: Dict[str, np.ndarray],
        sr: int,
        original_mix: Optional[np.ndarray] = None,
    ) -> Dict[str, ContaminationAnalysis]:
        """Detect contamination in all stems.

        Args:
            stems: Dictionary mapping stem type to audio
            sr: Sample rate
            original_mix: Optional original mix

        Returns:
            Dictionary mapping stem type to ContaminationAnalysis
        """
        results = {}

        for stem_type, stem_audio in stems.items():
            # Other stems for cross-bleed detection
            other_stems = {k: v for k, v in stems.items() if k != stem_type}

            results[stem_type] = self.detect(
                stem_audio=stem_audio,
                sr=sr,
                stem_type=stem_type,
                other_stems=other_stems,
                original_mix=original_mix,
            )

        return results

    def _detect_cross_stem_bleed(
        self,
        audio: np.ndarray,
        sr: int,
        stem_type: str,
        other_stems: Optional[Dict[str, np.ndarray]],
    ) -> List[ContaminationEvent]:
        """Detect cross-stem bleed from other instruments."""
        events = []

        if other_stems is None:
            return events

        try:
            import librosa
        except ImportError:
            logger.warning("librosa not available, skipping cross-stem bleed detection")
            return events

        # Compute spectrogram of target stem
        target_spec = np.abs(librosa.stft(audio, n_fft=self.n_fft, hop_length=self.hop_length))

        for other_type, other_audio in other_stems.items():
            # Convert to mono if needed
            if other_audio.ndim == 2:
                other_mono = np.mean(other_audio, axis=0)
            else:
                other_mono = other_audio

            # Ensure same length
            min_len = min(len(audio), len(other_mono))
            audio_aligned = audio[:min_len]
            other_aligned = other_mono[:min_len]

            # Compute spectrogram of other stem
            other_spec = np.abs(librosa.stft(other_aligned, n_fft=self.n_fft, hop_length=self.hop_length))

            # Align spectrograms
            min_frames = min(target_spec.shape[1], other_spec.shape[1])
            target_aligned = target_spec[:, :min_frames]
            other_aligned_spec = other_spec[:, :min_frames]

            # Normalize
            target_norm = target_aligned / (np.max(target_aligned) + 1e-10)
            other_norm = other_aligned_spec / (np.max(other_aligned_spec) + 1e-10)

            # Compute correlation per frame
            correlation = np.sum(target_norm * other_norm, axis=0)
            correlation = correlation / (np.sqrt(np.sum(target_norm**2, axis=0) + 1e-10) *
                                         np.sqrt(np.sum(other_norm**2, axis=0) + 1e-10) + 1e-10)

            # Find regions of high correlation (bleed)
            bleed_frames = correlation > self.bleed_threshold

            # Convert to time regions
            regions = self._frames_to_regions(bleed_frames, sr, self.hop_length)

            for start, end in regions:
                # Get average correlation in region
                start_frame = int(start * sr / self.hop_length)
                end_frame = int(end * sr / self.hop_length)
                avg_corr = np.mean(correlation[start_frame:end_frame]) if end_frame > start_frame else 0.0

                # Determine frequency range of bleed
                freq_range = self._get_bleed_frequency_range(
                    target_aligned[:, start_frame:end_frame],
                    other_aligned_spec[:, start_frame:end_frame],
                    sr
                )

                events.append(ContaminationEvent(
                    contamination_type=ContaminationType.CROSS_STEM_BLEED,
                    time_start=start,
                    time_end=end,
                    severity=float(avg_corr),
                    confidence=min(1.0, avg_corr * 1.5),  # Higher correlation = more confident
                    source_stem=other_type,
                    frequency_range=freq_range,
                    description=f"Bleed from {other_type} detected",
                ))

        return events

    def _detect_reverb_tails(
        self,
        audio: np.ndarray,
        sr: int,
        stem_type: str,
    ) -> List[ContaminationEvent]:
        """Detect reverb tails that may contain other instruments."""
        events = []

        try:
            import librosa
        except ImportError:
            logger.warning("librosa not available, skipping reverb tail detection")
            return events

        # Compute amplitude envelope
        frame_length = 2048
        hop = 512
        rms = librosa.feature.rms(y=audio, frame_length=frame_length, hop_length=hop)[0]

        # Find decay regions (where signal decreases smoothly)
        decay_threshold = self.reverb_decay_threshold

        # Detect transients
        onset_env = librosa.onset.onset_strength(y=audio, sr=sr, hop_length=hop)
        onset_frames = librosa.onset.onset_detect(
            onset_envelope=onset_env,
            sr=sr,
            hop_length=hop,
            backtrack=False
        )

        # For each onset, check the decay
        for i, onset_frame in enumerate(onset_frames):
            # Find next onset or end
            if i < len(onset_frames) - 1:
                next_onset = onset_frames[i + 1]
            else:
                next_onset = len(rms)

            # Analyze decay in this region
            if next_onset - onset_frame < 5:  # Too short
                continue

            region_rms = rms[onset_frame:next_onset]
            if len(region_rms) < 3:
                continue

            # Check for reverb tail characteristics
            # Reverb: smooth exponential decay
            # Clean: sharp decay or sustained level

            # Fit exponential decay
            x = np.arange(len(region_rms))
            region_rms_safe = np.maximum(region_rms, 1e-10)
            log_rms = np.log(region_rms_safe)

            # Linear fit in log domain
            if len(x) > 1:
                slope, intercept = np.polyfit(x, log_rms, 1)

                # Reverb has slow decay (small negative slope)
                # and smooth decay (low variance from fit)
                predicted = slope * x + intercept
                residual_variance = np.var(log_rms - predicted)

                # Check for reverb characteristics
                is_reverb = (
                    -0.1 < slope < -0.001 and  # Slow decay
                    residual_variance < 0.5 and  # Smooth
                    np.mean(region_rms) > 0.01  # Not silence
                )

                if is_reverb:
                    start_time = onset_frame * hop / sr
                    end_time = next_onset * hop / sr

                    # Severity based on decay length and smoothness
                    decay_duration = end_time - start_time
                    severity = min(1.0, decay_duration / 2.0) * (1 - min(1.0, residual_variance))

                    events.append(ContaminationEvent(
                        contamination_type=ContaminationType.REVERB_TAIL,
                        time_start=start_time,
                        time_end=end_time,
                        severity=float(severity),
                        confidence=0.6 if residual_variance < 0.2 else 0.4,
                        description=f"Reverb tail detected ({decay_duration:.2f}s decay)",
                    ))

        return events

    def _detect_delay_artifacts(
        self,
        audio: np.ndarray,
        sr: int,
    ) -> List[ContaminationEvent]:
        """Detect delay/echo artifacts."""
        events = []

        # Check for periodic correlation (delay repeats)
        # Common delay times: 1/8, 1/4, 1/2 note at various tempos

        # Estimate common delay times (in samples)
        # For tempos 80-160 BPM, 1/4 note = 0.375s - 0.75s
        min_delay_samples = int(0.05 * sr)  # 50ms minimum
        max_delay_samples = int(1.0 * sr)   # 1s maximum

        # Compute autocorrelation
        autocorr = np.correlate(audio, audio, mode='full')
        autocorr = autocorr[len(autocorr)//2:]  # Keep positive lags

        # Normalize
        autocorr = autocorr / (autocorr[0] + 1e-10)

        # Find peaks in autocorrelation (potential delay times)
        from scipy import signal
        peaks, properties = signal.find_peaks(
            autocorr[min_delay_samples:max_delay_samples],
            height=self.delay_correlation_threshold,
            distance=int(0.02 * sr)  # Minimum 20ms between peaks
        )

        # Adjust peak indices for the offset
        peaks = peaks + min_delay_samples

        for peak_idx in peaks[:5]:  # Check top 5 potential delays
            delay_time = peak_idx / sr
            correlation = autocorr[peak_idx]

            # Check if this is a musical delay (relates to tempo)
            # This is a simplified check
            if correlation > self.delay_correlation_threshold:
                # Find regions where delay is active
                # Use short-term correlation analysis

                window_samples = int(0.5 * sr)  # 500ms window
                hop_samples = int(0.1 * sr)  # 100ms hop

                delay_active_regions = []
                pos = 0

                while pos + window_samples + peak_idx < len(audio):
                    window = audio[pos:pos + window_samples]
                    delayed = audio[pos + peak_idx:pos + peak_idx + window_samples]

                    # Local correlation
                    local_corr = np.corrcoef(window, delayed)[0, 1]

                    if local_corr > self.delay_correlation_threshold * 0.8:
                        delay_active_regions.append(pos / sr)

                    pos += hop_samples

                # Merge adjacent regions
                if delay_active_regions:
                    merged = self._merge_time_points(delay_active_regions, gap_threshold=0.3)

                    for start, end in merged:
                        events.append(ContaminationEvent(
                            contamination_type=ContaminationType.DELAY_ARTIFACT,
                            time_start=start,
                            time_end=end,
                            severity=float(correlation),
                            confidence=0.7,
                            description=f"Delay artifact detected ({delay_time*1000:.0f}ms delay)",
                        ))

        return events

    def _detect_harmonic_confusion(
        self,
        audio: np.ndarray,
        sr: int,
        stem_type: str,
    ) -> List[ContaminationEvent]:
        """Detect harmonic confusion (octave errors, etc.)."""
        events = []

        try:
            import librosa
        except ImportError:
            logger.warning("librosa not available, skipping harmonic confusion detection")
            return events

        # This is particularly important for bass stems
        if stem_type not in ("bass", "other"):
            return events

        # Use pitch tracking
        f0, voiced_flag, voiced_probs = librosa.pyin(
            audio,
            fmin=librosa.note_to_hz('C1'),
            fmax=librosa.note_to_hz('C6'),
            sr=sr,
            hop_length=self.hop_length
        )

        # Replace NaN with 0
        f0 = np.nan_to_num(f0, nan=0.0)

        # For bass, expect frequencies mostly below 250Hz
        if stem_type == "bass":
            expected_max = 250  # Hz

            # Find regions where f0 exceeds expected range
            high_regions = f0 > expected_max

            # Also check for octave jumps (potential octave errors)
            f0_nonzero = f0.copy()
            f0_nonzero[f0_nonzero == 0] = np.nan

            # Detect sudden octave jumps
            ratio = f0_nonzero[1:] / (f0_nonzero[:-1] + 1e-10)
            ratio = np.nan_to_num(ratio, nan=1.0)

            octave_jumps = np.abs(ratio - 2.0) < 0.1  # Within 10% of octave
            octave_jumps = np.concatenate([[False], octave_jumps])

            # Combine high frequency and octave jump detections
            confusion_frames = high_regions | octave_jumps

            # Convert to time regions
            regions = self._frames_to_regions(confusion_frames, sr, self.hop_length)

            for start, end in regions:
                start_frame = int(start * sr / self.hop_length)
                end_frame = int(end * sr / self.hop_length)

                # Get average f0 in region
                region_f0 = f0[start_frame:end_frame]
                region_f0 = region_f0[region_f0 > 0]
                avg_f0 = np.mean(region_f0) if len(region_f0) > 0 else 0

                # Severity based on how far above expected range
                if avg_f0 > 0:
                    severity = min(1.0, (avg_f0 - expected_max) / expected_max)
                else:
                    severity = 0.3

                events.append(ContaminationEvent(
                    contamination_type=ContaminationType.HARMONIC_CONFUSION,
                    time_start=start,
                    time_end=end,
                    severity=float(severity),
                    confidence=0.6,
                    frequency_range=(expected_max, avg_f0) if avg_f0 > 0 else None,
                    description=f"Harmonic confusion: unexpected high frequencies ({avg_f0:.0f}Hz)",
                ))

        return events

    def _detect_phase_artifacts(
        self,
        audio: np.ndarray,
        sr: int,
    ) -> List[ContaminationEvent]:
        """Detect phase artifacts in stereo audio."""
        events = []

        # Only applicable to stereo
        if audio.ndim != 2 or audio.shape[0] != 2:
            return events

        left = audio[0]
        right = audio[1]

        # Compute short-term phase correlation
        frame_length = 2048
        hop = 512
        n_frames = (len(left) - frame_length) // hop + 1

        phase_issues = []

        for i in range(n_frames):
            start = i * hop
            end = start + frame_length

            l_frame = left[start:end]
            r_frame = right[start:end]

            # Check for phase cancellation
            sum_signal = l_frame + r_frame
            diff_signal = l_frame - r_frame

            sum_energy = np.sum(sum_signal ** 2)
            diff_energy = np.sum(diff_signal ** 2)

            # If diff energy >> sum energy, phase cancellation
            if sum_energy > 0:
                phase_ratio = diff_energy / (sum_energy + 1e-10)

                if phase_ratio > 3.0:  # Significant phase issue
                    phase_issues.append((i * hop / sr, phase_ratio))

        # Convert to events
        if phase_issues:
            # Group adjacent issues
            times = [t for t, _ in phase_issues]
            severities = [min(1.0, r / 10.0) for _, r in phase_issues]

            regions = self._merge_time_points(times, gap_threshold=0.1)

            for i, (start, end) in enumerate(regions):
                # Get average severity for region
                region_severities = [
                    s for (t, _), s in zip(phase_issues, severities)
                    if start <= t <= end
                ]
                avg_severity = np.mean(region_severities) if region_severities else 0.5

                events.append(ContaminationEvent(
                    contamination_type=ContaminationType.PHASE_ARTIFACT,
                    time_start=start,
                    time_end=end,
                    severity=float(avg_severity),
                    confidence=0.7,
                    description="Phase cancellation detected in stereo field",
                ))

        return events

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
                if end_time - start_time > 0.05:  # Minimum 50ms
                    regions.append((start_time, end_time))

        # Handle region at end
        if in_region:
            start_time = start_frame * hop_length / sr
            end_time = len(frame_mask) * hop_length / sr
            if end_time - start_time > 0.05:
                regions.append((start_time, end_time))

        return regions

    def _merge_time_points(
        self,
        times: List[float],
        gap_threshold: float,
    ) -> List[Tuple[float, float]]:
        """Merge adjacent time points into regions."""
        if not times:
            return []

        times = sorted(times)
        regions = []
        start = times[0]
        end = times[0]

        for t in times[1:]:
            if t - end <= gap_threshold:
                end = t
            else:
                regions.append((start, end + 0.1))  # Add small buffer
                start = t
                end = t

        regions.append((start, end + 0.1))
        return regions

    def _find_clean_regions(
        self,
        events: List[ContaminationEvent],
        duration: float,
    ) -> List[Tuple[float, float]]:
        """Find regions without contamination."""
        if not events:
            return [(0.0, duration)]

        # Sort events by start time
        sorted_events = sorted(events, key=lambda e: e.time_start)

        clean_regions = []
        current_pos = 0.0

        for event in sorted_events:
            if event.time_start > current_pos:
                # Gap before this event
                if event.time_start - current_pos > 0.1:  # Minimum 100ms
                    clean_regions.append((current_pos, event.time_start))
            current_pos = max(current_pos, event.time_end)

        # Check for clean region at end
        if current_pos < duration - 0.1:
            clean_regions.append((current_pos, duration))

        return clean_regions

    def _get_bleed_frequency_range(
        self,
        target_spec: np.ndarray,
        source_spec: np.ndarray,
        sr: int,
    ) -> Optional[Tuple[float, float]]:
        """Determine frequency range of bleed."""
        if target_spec.size == 0 or source_spec.size == 0:
            return None

        # Find frequency bins with high correlation
        correlation_per_bin = np.sum(target_spec * source_spec, axis=1)
        correlation_per_bin = correlation_per_bin / (
            np.sqrt(np.sum(target_spec**2, axis=1) + 1e-10) *
            np.sqrt(np.sum(source_spec**2, axis=1) + 1e-10) + 1e-10
        )

        # Find bins above threshold
        high_corr_bins = correlation_per_bin > 0.3

        if not np.any(high_corr_bins):
            return None

        # Convert to frequency range
        freqs = np.fft.rfftfreq(self.n_fft, 1/sr)[:len(correlation_per_bin)]

        high_corr_freqs = freqs[high_corr_bins]
        if len(high_corr_freqs) > 0:
            return (float(high_corr_freqs[0]), float(high_corr_freqs[-1]))

        return None


# Module-level singleton
_detector: Optional[ContaminationDetector] = None


def get_detector() -> ContaminationDetector:
    """Get the global contamination detector instance."""
    global _detector
    if _detector is None:
        _detector = ContaminationDetector()
    return _detector


def detect_contamination(
    stem_audio: np.ndarray,
    sr: int,
    stem_type: str,
    other_stems: Optional[Dict[str, np.ndarray]] = None,
    original_mix: Optional[np.ndarray] = None,
) -> ContaminationAnalysis:
    """Convenience function to detect contamination in a stem.

    Args:
        stem_audio: Audio data for the stem
        sr: Sample rate
        stem_type: Type of stem
        other_stems: Optional other stems for cross-bleed detection
        original_mix: Optional original mix

    Returns:
        ContaminationAnalysis
    """
    detector = get_detector()
    return detector.detect(
        stem_audio=stem_audio,
        sr=sr,
        stem_type=stem_type,
        other_stems=other_stems,
        original_mix=original_mix,
    )
