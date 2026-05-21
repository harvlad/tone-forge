"""Region-level confidence mapping for reconstruction.

Builds confidence maps that track extraction quality over time,
allowing downstream processes to weight decisions by confidence.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from .stem_quality import StemQuality, ConfidenceRegion
from .contamination import ContaminationAnalysis, ContaminationEvent
from .artifact_detection import ArtifactAnalysis, DetectedArtifact

logger = logging.getLogger(__name__)


@dataclass
class RegionConfidence:
    """Confidence assessment for a time region."""

    time_start: float
    time_end: float

    # Core confidence scores (0-1)
    note_confidence: float  # Confidence in MIDI note extraction
    descriptor_confidence: float  # Confidence in tone descriptor
    timing_confidence: float  # Confidence in timing accuracy

    # Quality factors
    contamination_probability: float  # 0=clean, 1=severe contamination
    artifact_probability: float  # 0=clean, 1=many artifacts
    harmonic_stability: float  # 0=unstable, 1=stable harmonics

    # Derived scores
    stem_quality_score: float = 0.0
    overall_confidence: float = 0.0

    @property
    def duration(self) -> float:
        """Duration of this region."""
        return self.time_end - self.time_start

    def compute_overall(self, weights: Optional[Dict[str, float]] = None) -> float:
        """Compute overall confidence from components."""
        if weights is None:
            weights = {
                "note": 0.25,
                "descriptor": 0.25,
                "timing": 0.15,
                "contamination": 0.15,
                "artifact": 0.10,
                "harmonic": 0.10,
            }

        # Contamination and artifact are penalties (invert)
        self.overall_confidence = (
            weights["note"] * self.note_confidence +
            weights["descriptor"] * self.descriptor_confidence +
            weights["timing"] * self.timing_confidence +
            weights["contamination"] * (1 - self.contamination_probability) +
            weights["artifact"] * (1 - self.artifact_probability) +
            weights["harmonic"] * self.harmonic_stability
        )

        return self.overall_confidence


@dataclass
class ConfidenceMap:
    """Complete confidence map for an audio file."""

    stem_type: str
    duration: float
    regions: List[RegionConfidence] = field(default_factory=list)
    global_confidence: float = 0.0
    low_confidence_regions: List[Tuple[float, float]] = field(default_factory=list)
    high_confidence_regions: List[Tuple[float, float]] = field(default_factory=list)

    @property
    def region_count(self) -> int:
        """Number of regions in the map."""
        return len(self.regions)

    def get_confidence_at(self, time: float) -> Optional[RegionConfidence]:
        """Get confidence for a specific time point."""
        for region in self.regions:
            if region.time_start <= time < region.time_end:
                return region
        return None

    def get_regions_in_range(
        self,
        start: float,
        end: float,
    ) -> List[RegionConfidence]:
        """Get regions overlapping a time range."""
        return [
            r for r in self.regions
            if r.time_start < end and r.time_end > start
        ]

    def get_average_confidence(
        self,
        start: Optional[float] = None,
        end: Optional[float] = None,
    ) -> float:
        """Get weighted average confidence for a time range."""
        if start is None:
            start = 0.0
        if end is None:
            end = self.duration

        regions = self.get_regions_in_range(start, end)
        if not regions:
            return self.global_confidence

        # Weight by duration
        total_duration = 0.0
        weighted_confidence = 0.0

        for region in regions:
            # Clip region to requested range
            r_start = max(region.time_start, start)
            r_end = min(region.time_end, end)
            duration = r_end - r_start

            total_duration += duration
            weighted_confidence += duration * region.overall_confidence

        if total_duration > 0:
            return weighted_confidence / total_duration
        return self.global_confidence

    def to_array(
        self,
        sr: int,
        hop_length: int = 512,
    ) -> np.ndarray:
        """Convert to frame-level confidence array."""
        n_frames = int(self.duration * sr / hop_length) + 1
        confidence_array = np.zeros(n_frames)

        for region in self.regions:
            start_frame = int(region.time_start * sr / hop_length)
            end_frame = int(region.time_end * sr / hop_length)
            confidence_array[start_frame:end_frame] = region.overall_confidence

        return confidence_array


class ConfidenceMapper:
    """Build confidence maps from quality analysis.

    Combines stem quality, contamination, and artifact analysis
    into actionable confidence maps.
    """

    def __init__(
        self,
        region_duration: float = 0.5,
        low_confidence_threshold: float = 0.4,
        high_confidence_threshold: float = 0.7,
    ):
        """Initialize the confidence mapper.

        Args:
            region_duration: Duration of each region in seconds
            low_confidence_threshold: Threshold for low confidence regions
            high_confidence_threshold: Threshold for high confidence regions
        """
        self.region_duration = region_duration
        self.low_confidence_threshold = low_confidence_threshold
        self.high_confidence_threshold = high_confidence_threshold

    def build_map(
        self,
        audio: np.ndarray,
        sr: int,
        stem_type: str,
        stem_quality: Optional[StemQuality] = None,
        contamination: Optional[ContaminationAnalysis] = None,
        artifacts: Optional[ArtifactAnalysis] = None,
    ) -> ConfidenceMap:
        """Build a confidence map for audio.

        Args:
            audio: Audio data (mono or stereo)
            sr: Sample rate
            stem_type: Type of stem
            stem_quality: Optional pre-computed stem quality
            contamination: Optional pre-computed contamination analysis
            artifacts: Optional pre-computed artifact analysis

        Returns:
            ConfidenceMap with region-level confidence
        """
        # Convert to mono for analysis
        if audio.ndim == 2:
            audio_mono = np.mean(audio, axis=0)
        else:
            audio_mono = audio

        duration = len(audio_mono) / sr

        # Divide into regions
        n_regions = int(np.ceil(duration / self.region_duration))
        regions: List[RegionConfidence] = []

        for i in range(n_regions):
            region_start = i * self.region_duration
            region_end = min((i + 1) * self.region_duration, duration)

            # Build confidence for this region
            region_confidence = self._build_region_confidence(
                audio_mono=audio_mono,
                sr=sr,
                stem_type=stem_type,
                region_start=region_start,
                region_end=region_end,
                stem_quality=stem_quality,
                contamination=contamination,
                artifacts=artifacts,
            )

            regions.append(region_confidence)

        # Compute global confidence
        if regions:
            global_confidence = np.mean([r.overall_confidence for r in regions])
        else:
            global_confidence = 0.5

        # Find low and high confidence regions
        low_conf_regions = self._find_threshold_regions(
            regions, self.low_confidence_threshold, below=True
        )
        high_conf_regions = self._find_threshold_regions(
            regions, self.high_confidence_threshold, below=False
        )

        return ConfidenceMap(
            stem_type=stem_type,
            duration=duration,
            regions=regions,
            global_confidence=float(global_confidence),
            low_confidence_regions=low_conf_regions,
            high_confidence_regions=high_conf_regions,
        )

    def _build_region_confidence(
        self,
        audio_mono: np.ndarray,
        sr: int,
        stem_type: str,
        region_start: float,
        region_end: float,
        stem_quality: Optional[StemQuality],
        contamination: Optional[ContaminationAnalysis],
        artifacts: Optional[ArtifactAnalysis],
    ) -> RegionConfidence:
        """Build confidence for a single region."""
        # Extract region audio
        start_sample = int(region_start * sr)
        end_sample = int(region_end * sr)
        region_audio = audio_mono[start_sample:end_sample]

        # Compute base confidence from signal analysis
        note_conf = self._estimate_note_confidence(region_audio, sr, stem_type)
        descriptor_conf = self._estimate_descriptor_confidence(region_audio, sr, stem_type)
        timing_conf = self._estimate_timing_confidence(region_audio, sr)
        harmonic_stability = self._estimate_harmonic_stability(region_audio, sr)

        # Get contamination probability for this region
        contamination_prob = 0.0
        if contamination:
            events_in_region = contamination.get_events_in_range(region_start, region_end)
            if events_in_region:
                # Weight by overlap and severity
                total_overlap = 0.0
                weighted_severity = 0.0
                region_duration = region_end - region_start

                for event in events_in_region:
                    overlap_start = max(event.time_start, region_start)
                    overlap_end = min(event.time_end, region_end)
                    overlap = overlap_end - overlap_start

                    total_overlap += overlap
                    weighted_severity += overlap * event.severity

                if total_overlap > 0:
                    contamination_prob = weighted_severity / region_duration

        # Get artifact probability for this region
        artifact_prob = 0.0
        if artifacts:
            artifacts_in_region = artifacts.get_artifacts_in_range(region_start, region_end)
            if artifacts_in_region:
                total_overlap = 0.0
                weighted_severity = 0.0
                region_duration = region_end - region_start

                for artifact in artifacts_in_region:
                    overlap_start = max(artifact.time_start, region_start)
                    overlap_end = min(artifact.time_end, region_end)
                    overlap = overlap_end - overlap_start

                    total_overlap += overlap
                    weighted_severity += overlap * artifact.severity

                if total_overlap > 0:
                    artifact_prob = weighted_severity / region_duration

        # Incorporate stem quality if available
        stem_quality_score = 0.5
        if stem_quality:
            stem_quality_score = stem_quality.overall_quality

            # Also check confidence regions from stem quality
            for conf_region in stem_quality.confidence_regions:
                if conf_region.start_time < region_end and conf_region.end_time > region_start:
                    # Blend with stem quality confidence
                    note_conf = (note_conf + conf_region.confidence) / 2
                    descriptor_conf = (descriptor_conf + conf_region.confidence) / 2

        # Build region confidence
        region_conf = RegionConfidence(
            time_start=region_start,
            time_end=region_end,
            note_confidence=float(note_conf),
            descriptor_confidence=float(descriptor_conf),
            timing_confidence=float(timing_conf),
            contamination_probability=float(contamination_prob),
            artifact_probability=float(artifact_prob),
            harmonic_stability=float(harmonic_stability),
            stem_quality_score=float(stem_quality_score),
        )

        # Compute overall confidence
        region_conf.compute_overall()

        return region_conf

    def _estimate_note_confidence(
        self,
        audio: np.ndarray,
        sr: int,
        stem_type: str,
    ) -> float:
        """Estimate confidence in MIDI note extraction for this region."""
        if len(audio) < 512:
            return 0.3

        # Factors affecting note extraction confidence:
        # 1. Signal energy (need signal to extract)
        # 2. Pitch clarity (clear pitch vs noise)
        # 3. Onset clarity (clear attacks)

        # Energy check
        rms = np.sqrt(np.mean(audio ** 2))
        if rms < 1e-5:
            return 0.1  # Very low signal

        # Pitch clarity using autocorrelation
        try:
            # Zero-mean
            audio_zm = audio - np.mean(audio)

            # Autocorrelation
            autocorr = np.correlate(audio_zm, audio_zm, mode='full')
            autocorr = autocorr[len(autocorr)//2:]

            # Normalize
            if autocorr[0] > 0:
                autocorr = autocorr / autocorr[0]

            # Find first significant peak (fundamental)
            min_lag = int(sr / 2000)  # Max 2000 Hz
            max_lag = int(sr / 30)    # Min 30 Hz

            if max_lag > len(autocorr):
                max_lag = len(autocorr) - 1

            if min_lag < max_lag:
                search_region = autocorr[min_lag:max_lag]
                if len(search_region) > 0:
                    peak_val = np.max(search_region)
                    pitch_clarity = float(peak_val)
                else:
                    pitch_clarity = 0.5
            else:
                pitch_clarity = 0.5

        except Exception:
            pitch_clarity = 0.5

        # Onset clarity (for non-pad instruments)
        if stem_type in ("drums", "bass"):
            onset_weight = 0.3
        else:
            onset_weight = 0.1

        # Simple onset estimate from envelope variance
        envelope = np.abs(audio)
        envelope_var = np.var(envelope) / (np.mean(envelope) + 1e-10)
        onset_clarity = min(1.0, envelope_var * 2)

        # Combine factors
        energy_score = min(1.0, rms * 100)  # Normalized energy
        note_conf = (
            0.3 * energy_score +
            0.5 * pitch_clarity +
            onset_weight * onset_clarity +
            (0.2 - onset_weight) * 0.5  # Base
        )

        return float(np.clip(note_conf, 0.1, 1.0))

    def _estimate_descriptor_confidence(
        self,
        audio: np.ndarray,
        sr: int,
        stem_type: str,
    ) -> float:
        """Estimate confidence in tone descriptor for this region."""
        if len(audio) < 512:
            return 0.3

        # Factors for descriptor confidence:
        # 1. Spectral stability
        # 2. Sufficient duration
        # 3. Clear timbre (not too noisy)

        # Duration check
        duration = len(audio) / sr
        duration_score = min(1.0, duration / 0.5)  # 0.5s is good

        # Spectral stability via short-term spectral variance
        try:
            n_fft = 1024
            hop = 256
            n_frames = (len(audio) - n_fft) // hop + 1

            if n_frames < 2:
                spectral_stability = 0.5
            else:
                # Compute spectral centroids
                centroids = []
                for i in range(n_frames):
                    start = i * hop
                    frame = audio[start:start + n_fft]
                    if len(frame) < n_fft:
                        break

                    # Hamming window
                    window = np.hamming(len(frame))
                    frame_windowed = frame * window

                    # FFT
                    spec = np.abs(np.fft.rfft(frame_windowed))
                    freqs = np.fft.rfftfreq(len(frame), 1/sr)

                    # Centroid
                    if np.sum(spec) > 0:
                        centroid = np.sum(freqs * spec) / np.sum(spec)
                        centroids.append(centroid)

                if len(centroids) > 1:
                    centroid_std = np.std(centroids)
                    centroid_mean = np.mean(centroids)
                    cv = centroid_std / (centroid_mean + 1e-10)
                    spectral_stability = 1.0 - min(1.0, cv * 2)
                else:
                    spectral_stability = 0.5

        except Exception:
            spectral_stability = 0.5

        # Noise ratio estimate
        try:
            # High-pass filter to isolate noise
            from scipy.signal import butter, filtfilt
            b, a = butter(2, 3000 / (sr / 2), btype='high')
            high_freq = filtfilt(b, a, audio)

            noise_energy = np.mean(high_freq ** 2)
            total_energy = np.mean(audio ** 2)

            if total_energy > 0:
                noise_ratio = noise_energy / total_energy
                timbre_clarity = 1.0 - min(1.0, noise_ratio * 5)
            else:
                timbre_clarity = 0.5

        except Exception:
            timbre_clarity = 0.5

        # Combine
        descriptor_conf = (
            0.2 * duration_score +
            0.5 * spectral_stability +
            0.3 * timbre_clarity
        )

        return float(np.clip(descriptor_conf, 0.1, 1.0))

    def _estimate_timing_confidence(
        self,
        audio: np.ndarray,
        sr: int,
    ) -> float:
        """Estimate confidence in timing accuracy."""
        if len(audio) < 512:
            return 0.5

        # Timing confidence depends on onset clarity
        try:
            # Compute envelope
            envelope = np.abs(audio)

            # Compute envelope derivative (onset detection)
            envelope_diff = np.diff(envelope)

            # Look for clear positive transients
            positive_diffs = envelope_diff[envelope_diff > 0]

            if len(positive_diffs) > 0:
                # Peak/mean ratio of positive differences
                peak_ratio = np.max(positive_diffs) / (np.mean(positive_diffs) + 1e-10)
                timing_conf = min(1.0, peak_ratio / 10)
            else:
                timing_conf = 0.3

        except Exception:
            timing_conf = 0.5

        return float(timing_conf)

    def _estimate_harmonic_stability(
        self,
        audio: np.ndarray,
        sr: int,
    ) -> float:
        """Estimate harmonic stability over the region."""
        if len(audio) < 1024:
            return 0.5

        try:
            # Compute spectrograms and track harmonic peaks
            n_fft = 1024
            hop = 256
            n_frames = (len(audio) - n_fft) // hop + 1

            if n_frames < 3:
                return 0.5

            # Track dominant frequency over time
            dominant_freqs = []

            for i in range(n_frames):
                start = i * hop
                frame = audio[start:start + n_fft]
                if len(frame) < n_fft:
                    break

                # Hamming window
                window = np.hamming(len(frame))
                frame_windowed = frame * window

                # FFT
                spec = np.abs(np.fft.rfft(frame_windowed))
                freqs = np.fft.rfftfreq(len(frame), 1/sr)

                # Find dominant frequency
                if np.max(spec) > 0:
                    dominant_idx = np.argmax(spec)
                    dominant_freqs.append(freqs[dominant_idx])

            if len(dominant_freqs) < 2:
                return 0.5

            # Stability: low variance in dominant frequency
            dominant_freqs = np.array(dominant_freqs)
            dominant_freqs = dominant_freqs[dominant_freqs > 0]

            if len(dominant_freqs) > 1:
                cv = np.std(dominant_freqs) / (np.mean(dominant_freqs) + 1e-10)
                stability = 1.0 - min(1.0, cv * 3)
            else:
                stability = 0.5

            return float(stability)

        except Exception:
            return 0.5

    def _find_threshold_regions(
        self,
        regions: List[RegionConfidence],
        threshold: float,
        below: bool,
    ) -> List[Tuple[float, float]]:
        """Find regions above or below a confidence threshold."""
        result = []

        for region in regions:
            if below:
                if region.overall_confidence < threshold:
                    result.append((region.time_start, region.time_end))
            else:
                if region.overall_confidence >= threshold:
                    result.append((region.time_start, region.time_end))

        # Merge adjacent regions
        if not result:
            return result

        merged = []
        current_start, current_end = result[0]

        for start, end in result[1:]:
            if start <= current_end + 0.01:  # Adjacent (with small tolerance)
                current_end = end
            else:
                merged.append((current_start, current_end))
                current_start, current_end = start, end

        merged.append((current_start, current_end))
        return merged


# Module-level singleton
_mapper: Optional[ConfidenceMapper] = None


def get_confidence_mapper() -> ConfidenceMapper:
    """Get the global confidence mapper instance."""
    global _mapper
    if _mapper is None:
        _mapper = ConfidenceMapper()
    return _mapper


def build_confidence_map(
    audio: np.ndarray,
    sr: int,
    stem_type: str,
    stem_quality: Optional[StemQuality] = None,
    contamination: Optional[ContaminationAnalysis] = None,
    artifacts: Optional[ArtifactAnalysis] = None,
) -> ConfidenceMap:
    """Convenience function to build a confidence map.

    Args:
        audio: Audio data
        sr: Sample rate
        stem_type: Type of stem
        stem_quality: Optional pre-computed stem quality
        contamination: Optional pre-computed contamination analysis
        artifacts: Optional pre-computed artifact analysis

    Returns:
        ConfidenceMap
    """
    mapper = get_confidence_mapper()
    return mapper.build_map(
        audio=audio,
        sr=sr,
        stem_type=stem_type,
        stem_quality=stem_quality,
        contamination=contamination,
        artifacts=artifacts,
    )
