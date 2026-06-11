"""Pitch stability analysis for modulation artifact detection.

Analyzes pitch stability to detect and handle:
- Vibrato
- Chorus effects
- Detune
- Pitch modulation
- Phase coherence issues

This helps:
- Reduce false note splits from pitch wobble
- Suppress modulation artifacts
- Preserve intentional vibrato without creating extra notes
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np


@dataclass
class PitchStabilityMetrics:
    """Pitch stability metrics for an audio segment."""
    # Core stability measures
    frequency_variance: float  # Hz variance around detected pitch
    frequency_std: float       # Hz standard deviation
    cents_deviation: float     # Average deviation in cents

    # Modulation characteristics
    vibrato_rate_hz: float     # Detected vibrato rate (0 if none)
    vibrato_depth_cents: float # Vibrato depth in cents
    has_vibrato: bool          # Whether vibrato is detected

    # Chorus/detune detection
    spectral_spread: float     # Spread of energy around fundamental
    phase_coherence: float     # 0-1, how coherent the phase is
    has_chorus: bool           # Whether chorus/detune is detected

    # Overall stability score
    stability_score: float     # 0-1, higher = more stable pitch

    # Classification
    modulation_type: str       # "stable", "vibrato", "chorus", "unstable"


@dataclass
class NoteStabilityAnalysis:
    """Stability analysis for a detected note."""
    pitch: int
    start_time: float
    end_time: float
    metrics: PitchStabilityMetrics

    # Recommendations
    should_merge_with_neighbors: bool
    confidence_adjustment: float  # Multiply original confidence by this
    suppress_as_artifact: bool


class PitchStabilityAnalyzer:
    """Analyzes pitch stability to detect modulation artifacts.

    Many synth false positives come from:
    - Vibrato being detected as separate notes
    - Chorus creating phantom parallel notes
    - Detune causing pitch detection wobble
    - Modulation creating spurious note boundaries

    This analyzer provides metrics to help filter these artifacts.
    """

    def __init__(
        self,
        hop_length: int = 512,
        frame_length: int = 2048,
        vibrato_min_rate: float = 4.0,   # Hz
        vibrato_max_rate: float = 8.0,   # Hz
        vibrato_min_depth: float = 20.0, # cents
        chorus_spread_threshold: float = 30.0,  # cents
    ):
        """Initialize analyzer.

        Args:
            hop_length: Hop length for analysis
            frame_length: Frame length for pitch tracking
            vibrato_min_rate: Minimum vibrato rate to detect
            vibrato_max_rate: Maximum vibrato rate to detect
            vibrato_min_depth: Minimum vibrato depth to consider
            chorus_spread_threshold: Spectral spread threshold for chorus
        """
        self.hop_length = hop_length
        self.frame_length = frame_length
        self.vibrato_min_rate = vibrato_min_rate
        self.vibrato_max_rate = vibrato_max_rate
        self.vibrato_min_depth = vibrato_min_depth
        self.chorus_spread_threshold = chorus_spread_threshold

    def analyze_segment(
        self,
        audio: np.ndarray,
        sr: int,
        expected_pitch: Optional[int] = None,
    ) -> PitchStabilityMetrics:
        """Analyze pitch stability of an audio segment.

        Args:
            audio: Audio segment to analyze
            sr: Sample rate
            expected_pitch: Expected MIDI pitch (for reference)

        Returns:
            PitchStabilityMetrics with analysis results
        """
        import librosa

        if len(audio) < self.frame_length:
            return self._empty_metrics()

        # Track pitch over time using pyin
        f0, voiced_flag, voiced_probs = librosa.pyin(
            audio,
            fmin=librosa.note_to_hz('C2'),
            fmax=librosa.note_to_hz('C7'),
            sr=sr,
            hop_length=self.hop_length,
            frame_length=self.frame_length,
        )

        # Filter to voiced frames
        voiced_f0 = f0[voiced_flag]

        if len(voiced_f0) < 3:
            return self._empty_metrics()

        # Calculate basic statistics
        mean_f0 = np.nanmean(voiced_f0)
        std_f0 = np.nanstd(voiced_f0)
        var_f0 = np.nanvar(voiced_f0)

        # Convert to cents deviation
        if mean_f0 > 0:
            cents_dev = 1200 * np.log2(voiced_f0 / mean_f0)
            cents_deviation = np.nanmean(np.abs(cents_dev))
        else:
            cents_deviation = 0.0

        # Detect vibrato
        vibrato_rate, vibrato_depth, has_vibrato = self._detect_vibrato(
            voiced_f0, sr
        )

        # Analyze spectral spread (chorus detection)
        spectral_spread = self._analyze_spectral_spread(
            audio, sr, mean_f0
        )

        # Analyze phase coherence
        phase_coherence = self._analyze_phase_coherence(
            audio, sr, mean_f0
        )

        # Detect chorus/detune
        has_chorus = spectral_spread > self.chorus_spread_threshold

        # Calculate overall stability score
        stability_score = self._compute_stability_score(
            std_f0, mean_f0, vibrato_depth, spectral_spread, phase_coherence
        )

        # Classify modulation type
        modulation_type = self._classify_modulation(
            has_vibrato, has_chorus, stability_score
        )

        return PitchStabilityMetrics(
            frequency_variance=float(var_f0),
            frequency_std=float(std_f0),
            cents_deviation=float(cents_deviation),
            vibrato_rate_hz=float(vibrato_rate),
            vibrato_depth_cents=float(vibrato_depth),
            has_vibrato=has_vibrato,
            spectral_spread=float(spectral_spread),
            phase_coherence=float(phase_coherence),
            has_chorus=has_chorus,
            stability_score=float(stability_score),
            modulation_type=modulation_type,
        )

    def analyze_note(
        self,
        audio: np.ndarray,
        sr: int,
        pitch: int,
        start_time: float,
        end_time: float,
    ) -> NoteStabilityAnalysis:
        """Analyze stability for a specific note.

        Args:
            audio: Full audio signal
            sr: Sample rate
            pitch: MIDI pitch of the note
            start_time: Note start time in seconds
            end_time: Note end time in seconds

        Returns:
            NoteStabilityAnalysis with recommendations
        """
        # Extract note segment
        start_sample = int(start_time * sr)
        end_sample = int(end_time * sr)

        # Add small buffer
        buffer_samples = int(0.02 * sr)  # 20ms buffer
        start_sample = max(0, start_sample - buffer_samples)
        end_sample = min(len(audio), end_sample + buffer_samples)

        segment = audio[start_sample:end_sample]

        # Analyze stability
        metrics = self.analyze_segment(segment, sr, expected_pitch=pitch)

        # Generate recommendations
        should_merge = self._should_merge(metrics)
        confidence_adj = self._compute_confidence_adjustment(metrics)
        suppress = self._should_suppress(metrics)

        return NoteStabilityAnalysis(
            pitch=pitch,
            start_time=start_time,
            end_time=end_time,
            metrics=metrics,
            should_merge_with_neighbors=should_merge,
            confidence_adjustment=confidence_adj,
            suppress_as_artifact=suppress,
        )

    def find_modulation_artifacts(
        self,
        notes: List[Tuple[int, float, float]],
        audio: np.ndarray,
        sr: int,
    ) -> List[int]:
        """Find notes that are likely modulation artifacts.

        Args:
            notes: List of (pitch, start, end) tuples
            audio: Audio signal
            sr: Sample rate

        Returns:
            List of indices of notes that are likely artifacts
        """
        artifacts = []

        for i, (pitch, start, end) in enumerate(notes):
            analysis = self.analyze_note(audio, sr, pitch, start, end)

            if analysis.suppress_as_artifact:
                artifacts.append(i)

        return artifacts

    def _detect_vibrato(
        self,
        f0_track: np.ndarray,
        sr: int,
    ) -> Tuple[float, float, bool]:
        """Detect vibrato in pitch track.

        Returns (rate_hz, depth_cents, has_vibrato).
        """
        if len(f0_track) < 10:
            return 0.0, 0.0, False

        # Normalize pitch track
        mean_f0 = np.nanmean(f0_track)
        if mean_f0 <= 0:
            return 0.0, 0.0, False

        # Convert to cents deviation from mean
        cents = 1200 * np.log2(f0_track / mean_f0)
        cents = np.nan_to_num(cents, nan=0.0)

        # FFT to find dominant modulation frequency
        n = len(cents)
        fft = np.fft.rfft(cents)
        freqs = np.fft.rfftfreq(n, d=self.hop_length / sr)

        magnitudes = np.abs(fft)

        # Look for peak in vibrato range
        vibrato_mask = (freqs >= self.vibrato_min_rate) & (freqs <= self.vibrato_max_rate)

        if not np.any(vibrato_mask):
            return 0.0, 0.0, False

        vibrato_magnitudes = magnitudes[vibrato_mask]
        vibrato_freqs = freqs[vibrato_mask]

        if len(vibrato_magnitudes) == 0:
            return 0.0, 0.0, False

        peak_idx = np.argmax(vibrato_magnitudes)
        peak_mag = vibrato_magnitudes[peak_idx]
        peak_freq = vibrato_freqs[peak_idx]

        # Estimate depth from magnitude
        vibrato_depth = peak_mag * 2 / n  # Approximate peak-to-peak

        # Check if vibrato is significant
        total_energy = np.sum(magnitudes)
        vibrato_energy = peak_mag

        has_vibrato = (
            vibrato_depth >= self.vibrato_min_depth and
            vibrato_energy > total_energy * 0.1
        )

        return float(peak_freq), float(vibrato_depth), has_vibrato

    def _analyze_spectral_spread(
        self,
        audio: np.ndarray,
        sr: int,
        fundamental_freq: float,
    ) -> float:
        """Analyze spectral spread around fundamental (chorus detection).

        Returns spread in cents.
        """
        if fundamental_freq <= 0 or len(audio) < 2048:
            return 0.0

        import librosa

        # Compute STFT
        stft = np.abs(librosa.stft(audio, n_fft=4096, hop_length=512))
        freqs = librosa.fft_frequencies(sr=sr, n_fft=4096)

        # Average over time
        spectrum = np.mean(stft, axis=1)

        # Find bins around fundamental
        bandwidth = fundamental_freq * 0.1  # 10% bandwidth
        mask = (freqs >= fundamental_freq - bandwidth) & (freqs <= fundamental_freq + bandwidth)

        if not np.any(mask):
            return 0.0

        local_spectrum = spectrum[mask]
        local_freqs = freqs[mask]

        if np.sum(local_spectrum) == 0:
            return 0.0

        # Compute weighted standard deviation of frequencies
        weights = local_spectrum / np.sum(local_spectrum)
        weighted_mean = np.sum(local_freqs * weights)
        weighted_var = np.sum(weights * (local_freqs - weighted_mean) ** 2)
        weighted_std = np.sqrt(weighted_var)

        # Convert to cents
        if fundamental_freq > 0:
            spread_cents = 1200 * np.log2((fundamental_freq + weighted_std) / fundamental_freq)
        else:
            spread_cents = 0.0

        return float(spread_cents)

    def _analyze_phase_coherence(
        self,
        audio: np.ndarray,
        sr: int,
        fundamental_freq: float,
    ) -> float:
        """Analyze phase coherence (lower = more chorus-like).

        Returns coherence 0-1.
        """
        if fundamental_freq <= 0 or len(audio) < 2048:
            return 1.0

        import librosa

        # Compute STFT with phase
        stft = librosa.stft(audio, n_fft=4096, hop_length=512)
        phases = np.angle(stft)
        freqs = librosa.fft_frequencies(sr=sr, n_fft=4096)

        # Find fundamental bin
        fund_bin = np.argmin(np.abs(freqs - fundamental_freq))

        if fund_bin >= len(phases):
            return 1.0

        # Get phase at fundamental over time
        fund_phase = phases[fund_bin, :]

        # Unwrap phase
        unwrapped = np.unwrap(fund_phase)

        # Calculate expected phase progression
        expected_rate = 2 * np.pi * fundamental_freq * self.hop_length / sr
        expected_phase = np.arange(len(unwrapped)) * expected_rate

        # Compute correlation between actual and expected
        if len(unwrapped) < 2:
            return 1.0

        # Normalize and compute correlation
        actual_diff = np.diff(unwrapped)
        expected_diff = np.diff(expected_phase)

        if np.std(actual_diff) == 0 or np.std(expected_diff) == 0:
            return 1.0

        correlation = np.corrcoef(actual_diff, expected_diff)[0, 1]
        coherence = (correlation + 1) / 2  # Map -1 to 1 -> 0 to 1

        return float(np.clip(coherence, 0, 1))

    def _compute_stability_score(
        self,
        std_f0: float,
        mean_f0: float,
        vibrato_depth: float,
        spectral_spread: float,
        phase_coherence: float,
    ) -> float:
        """Compute overall pitch stability score 0-1."""
        if mean_f0 <= 0:
            return 0.5

        # Relative frequency deviation
        relative_std = std_f0 / mean_f0 if mean_f0 > 0 else 0

        # Score components
        freq_stability = 1.0 / (1.0 + relative_std * 100)  # Penalize deviation
        vibrato_penalty = min(vibrato_depth / 100.0, 0.3)  # Cap vibrato penalty
        spread_penalty = min(spectral_spread / 100.0, 0.3)  # Cap spread penalty
        coherence_bonus = phase_coherence * 0.2

        score = freq_stability - vibrato_penalty - spread_penalty + coherence_bonus
        return float(np.clip(score, 0, 1))

    def _classify_modulation(
        self,
        has_vibrato: bool,
        has_chorus: bool,
        stability_score: float,
    ) -> str:
        """Classify the type of modulation present."""
        if stability_score > 0.8:
            return "stable"
        elif has_vibrato and not has_chorus:
            return "vibrato"
        elif has_chorus:
            return "chorus"
        else:
            return "unstable"

    def _should_merge(self, metrics: PitchStabilityMetrics) -> bool:
        """Determine if this note should be merged with neighbors."""
        # Merge if unstable pitch that might cause note splits
        return (
            metrics.modulation_type == "unstable" or
            (metrics.has_vibrato and metrics.vibrato_depth_cents > 50)
        )

    def _compute_confidence_adjustment(
        self,
        metrics: PitchStabilityMetrics,
    ) -> float:
        """Compute confidence adjustment factor."""
        base = 1.0

        # Reduce confidence for unstable pitches
        if metrics.stability_score < 0.5:
            base *= metrics.stability_score + 0.5

        # Reduce confidence for chorus (likely artifacts)
        if metrics.has_chorus:
            base *= 0.7

        return float(np.clip(base, 0.3, 1.0))

    def _should_suppress(self, metrics: PitchStabilityMetrics) -> bool:
        """Determine if note should be suppressed as artifact."""
        return (
            metrics.stability_score < 0.3 or
            (metrics.has_chorus and metrics.spectral_spread > 50)
        )

    def _empty_metrics(self) -> PitchStabilityMetrics:
        """Return empty metrics for insufficient data."""
        return PitchStabilityMetrics(
            frequency_variance=0.0,
            frequency_std=0.0,
            cents_deviation=0.0,
            vibrato_rate_hz=0.0,
            vibrato_depth_cents=0.0,
            has_vibrato=False,
            spectral_spread=0.0,
            phase_coherence=1.0,
            has_chorus=False,
            stability_score=0.5,
            modulation_type="stable",
        )


# Convenience function
def analyze_pitch_stability(
    audio: np.ndarray,
    sr: int,
    expected_pitch: Optional[int] = None,
) -> PitchStabilityMetrics:
    """Analyze pitch stability of an audio segment."""
    analyzer = PitchStabilityAnalyzer()
    return analyzer.analyze_segment(audio, sr, expected_pitch)
