"""Synth behavior analysis for intelligent extraction.

Detects synth-specific characteristics that affect MIDI extraction:
- Supersaw: Multiple detuned oscillators
- Octave layering: Stacked octaves (common in bass/leads)
- Unison voices: Multiple voices at same pitch
- Glide/portamento: Smooth pitch transitions
- Arpeggiator: Rapid note sequences
- Sidechain: Rhythmic volume modulation
- Gate patterns: Rhythmic note chopping

Understanding these behaviors allows extraction to adapt:
- Supersaw: Don't suppress harmonics, preserve layering
- Glide: Merge rapid pitch changes into portamento
- Arpeggiator: Detect pattern, export as arp MIDI
- Sidechain: Modulate velocity based on pump pattern
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class SynthBehavior:
    """Detected synth behaviors and characteristics."""

    # Supersaw detection
    supersaw_detected: bool = False
    supersaw_voices: int = 1
    detune_width_cents: float = 0.0

    # Octave layering
    octave_layering: bool = False
    layered_octaves: List[int] = field(default_factory=list)  # e.g., [0, -12, 12]

    # Unison
    unison_voices: int = 1
    unison_detune_cents: float = 0.0

    # Glide/portamento
    glide_detected: bool = False
    glide_time_ms: float = 0.0
    glide_mode: str = "legato"  # legato, always, off

    # Arpeggiator
    arpeggiator_detected: bool = False
    arp_rate_hz: float = 0.0
    arp_pattern: str = "up"  # up, down, updown, random

    # Sidechain
    sidechain_detected: bool = False
    sidechain_depth: float = 0.0  # 0-1
    sidechain_rate_hz: float = 0.0

    # Gate pattern
    gate_pattern: Optional[List[float]] = None  # Rhythmic gate pattern

    # Vibrato/modulation
    vibrato_detected: bool = False
    vibrato_rate_hz: float = 0.0
    vibrato_depth_cents: float = 0.0

    # Chorus effect
    chorus_detected: bool = False
    chorus_rate_hz: float = 0.0
    chorus_depth: float = 0.0

    # Overall characteristics
    is_polyphonic: bool = False
    is_monophonic: bool = True
    dominant_behavior: str = "standard"  # supersaw, arp, pad, pluck, bass

    def to_dict(self) -> dict:
        return {
            "supersaw": {
                "detected": self.supersaw_detected,
                "voices": self.supersaw_voices,
                "detune_cents": self.detune_width_cents,
            },
            "octave_layering": {
                "detected": self.octave_layering,
                "octaves": self.layered_octaves,
            },
            "unison": {
                "voices": self.unison_voices,
                "detune_cents": self.unison_detune_cents,
            },
            "glide": {
                "detected": self.glide_detected,
                "time_ms": self.glide_time_ms,
                "mode": self.glide_mode,
            },
            "arpeggiator": {
                "detected": self.arpeggiator_detected,
                "rate_hz": self.arp_rate_hz,
                "pattern": self.arp_pattern,
            },
            "sidechain": {
                "detected": self.sidechain_detected,
                "depth": self.sidechain_depth,
                "rate_hz": self.sidechain_rate_hz,
            },
            "gate_pattern": self.gate_pattern,
            "vibrato": {
                "detected": self.vibrato_detected,
                "rate_hz": self.vibrato_rate_hz,
                "depth_cents": self.vibrato_depth_cents,
            },
            "chorus": {
                "detected": self.chorus_detected,
                "rate_hz": self.chorus_rate_hz,
                "depth": self.chorus_depth,
            },
            "is_polyphonic": self.is_polyphonic,
            "is_monophonic": self.is_monophonic,
            "dominant_behavior": self.dominant_behavior,
        }


class SynthBehaviorAnalyzer:
    """Analyzes audio to detect synth-specific behaviors.

    These detections inform the MIDI extraction pipeline to
    handle synth content appropriately.
    """

    def __init__(
        self,
        hop_length: int = 512,
        n_fft: int = 4096,
    ):
        self.hop_length = hop_length
        self.n_fft = n_fft

    def analyze(
        self,
        audio: np.ndarray,
        sr: int,
        tempo: Optional[float] = None,
    ) -> SynthBehavior:
        """Analyze audio for synth behaviors.

        Args:
            audio: Audio signal (mono)
            sr: Sample rate
            tempo: Optional tempo hint

        Returns:
            SynthBehavior with detected characteristics
        """
        import librosa

        if audio.ndim > 1:
            audio = np.mean(audio, axis=0)

        behavior = SynthBehavior()

        # Estimate tempo if not provided
        if tempo is None:
            tempo, _ = librosa.beat.beat_track(y=audio, sr=sr)
            if hasattr(tempo, "__iter__"):
                tempo = float(tempo[0]) if len(tempo) > 0 else 120.0
            tempo = float(tempo) if tempo > 0 else 120.0

        # Detect various behaviors
        self._detect_supersaw(audio, sr, behavior)
        self._detect_octave_layering(audio, sr, behavior)
        self._detect_glide(audio, sr, behavior)
        self._detect_arpeggiator(audio, sr, tempo, behavior)
        self._detect_sidechain(audio, sr, tempo, behavior)
        self._detect_vibrato_chorus(audio, sr, behavior)
        self._detect_polyphony(audio, sr, behavior)

        # Determine dominant behavior
        behavior.dominant_behavior = self._classify_dominant_behavior(behavior)

        return behavior

    def _detect_supersaw(
        self,
        audio: np.ndarray,
        sr: int,
        behavior: SynthBehavior,
    ):
        """Detect supersaw characteristics (multiple detuned oscillators)."""
        import librosa

        # Compute magnitude spectrum
        D = np.abs(librosa.stft(audio, n_fft=self.n_fft, hop_length=self.hop_length))

        # Look for peak spreading at harmonics
        # Supersaw has multiple peaks clustered around each harmonic

        freqs = librosa.fft_frequencies(sr=sr, n_fft=self.n_fft)

        # Analyze a few frames in the middle
        mid_frame = D.shape[1] // 2
        frame_range = max(1, D.shape[1] // 10)
        avg_spectrum = np.mean(D[:, max(0, mid_frame-frame_range):mid_frame+frame_range], axis=1)

        # Find peaks
        from scipy.signal import find_peaks
        peaks, _ = find_peaks(avg_spectrum, height=np.max(avg_spectrum) * 0.1, distance=3)

        if len(peaks) < 2:
            return

        # Check for detuned clusters around peaks
        detuned_clusters = 0
        detune_widths = []

        for peak_idx in peaks[:10]:  # Check first 10 peaks
            peak_freq = freqs[peak_idx]
            if peak_freq < 100:
                continue

            # Look for nearby peaks (potential detuned copies)
            search_range = int(peak_freq * 0.05 / (freqs[1] - freqs[0]))  # 5% of freq
            local_peaks, _ = find_peaks(
                avg_spectrum[max(0, peak_idx-search_range):peak_idx+search_range+1],
                height=avg_spectrum[peak_idx] * 0.3,
            )

            if len(local_peaks) >= 2:
                detuned_clusters += 1
                # Estimate detune width in cents
                local_freqs = freqs[max(0, peak_idx-search_range):peak_idx+search_range+1]
                if len(local_peaks) >= 2:
                    freq_spread = local_freqs[local_peaks[-1]] - local_freqs[local_peaks[0]]
                    if peak_freq > 0:
                        cents_spread = 1200 * np.log2((peak_freq + freq_spread/2) / peak_freq)
                        detune_widths.append(abs(cents_spread))

        # Supersaw if multiple detuned clusters found
        if detuned_clusters >= 3:
            behavior.supersaw_detected = True
            behavior.supersaw_voices = min(8, max(3, detuned_clusters // 2 + 2))
            behavior.detune_width_cents = float(np.mean(detune_widths)) if detune_widths else 25.0

    def _detect_octave_layering(
        self,
        audio: np.ndarray,
        sr: int,
        behavior: SynthBehavior,
    ):
        """Detect octave-layered sounds (common in bass and leads)."""
        import librosa

        # Compute chroma
        chroma = librosa.feature.chroma_cqt(y=audio, sr=sr)

        # Average chroma
        avg_chroma = np.mean(chroma, axis=1)

        # Find dominant pitch class
        dominant_pc = np.argmax(avg_chroma)

        # Check for strong octave relationships in spectrum
        D = np.abs(librosa.stft(audio, n_fft=self.n_fft))
        freqs = librosa.fft_frequencies(sr=sr, n_fft=self.n_fft)
        avg_spectrum = np.mean(D, axis=1)

        # Find fundamental
        from scipy.signal import find_peaks
        peaks, properties = find_peaks(avg_spectrum, height=np.max(avg_spectrum) * 0.1)

        if len(peaks) == 0:
            return

        # Get fundamental (first significant peak above 40Hz)
        fundamentals = [freqs[p] for p in peaks if freqs[p] >= 40]
        if not fundamentals:
            return

        fundamental = fundamentals[0]

        # Check for octave relationships
        octave_layers = [0]  # 0 = fundamental

        for octave in [-12, 12, 24]:
            expected_freq = fundamental * (2 ** (octave / 12))
            # Find closest peak
            freq_diffs = np.abs(freqs[peaks] - expected_freq)
            closest_idx = np.argmin(freq_diffs)

            if freq_diffs[closest_idx] < expected_freq * 0.05:  # Within 5%
                peak_height = avg_spectrum[peaks[closest_idx]]
                if peak_height > np.max(avg_spectrum) * 0.2:  # Significant
                    octave_layers.append(octave)

        if len(octave_layers) > 1:
            behavior.octave_layering = True
            behavior.layered_octaves = sorted(octave_layers)

    def _detect_glide(
        self,
        audio: np.ndarray,
        sr: int,
        behavior: SynthBehavior,
    ):
        """Detect glide/portamento between notes."""
        import librosa

        # Use pYIN for pitch tracking
        try:
            f0, voiced_flag, voiced_probs = librosa.pyin(
                audio,
                fmin=50,
                fmax=2000,
                sr=sr,
                hop_length=self.hop_length,
            )
        except Exception:
            return

        # Handle NaN
        f0 = np.nan_to_num(f0, nan=0)

        # Look for smooth pitch transitions
        # Glide = continuous pitch change over time (not step-wise)

        # Calculate pitch derivative
        pitch_deriv = np.diff(f0)

        # Find regions of sustained pitch change
        glide_regions = []
        in_glide = False
        glide_start = 0

        threshold = 0.5  # Minimum pitch change per frame

        for i in range(len(pitch_deriv)):
            if abs(pitch_deriv[i]) > threshold and f0[i] > 0 and f0[i+1] > 0:
                if not in_glide:
                    in_glide = True
                    glide_start = i
            else:
                if in_glide:
                    if i - glide_start >= 3:  # At least 3 frames
                        glide_regions.append((glide_start, i))
                    in_glide = False

        if len(glide_regions) >= 2:
            behavior.glide_detected = True

            # Estimate glide time
            glide_lengths = [r[1] - r[0] for r in glide_regions]
            avg_glide_frames = np.mean(glide_lengths)
            behavior.glide_time_ms = float(avg_glide_frames * self.hop_length / sr * 1000)

    def _detect_arpeggiator(
        self,
        audio: np.ndarray,
        sr: int,
        tempo: float,
        behavior: SynthBehavior,
    ):
        """Detect arpeggiator patterns."""
        import librosa

        # Compute onset strength
        onset_env = librosa.onset.onset_strength(y=audio, sr=sr)

        # Detect onsets
        onsets = librosa.onset.onset_detect(
            onset_envelope=onset_env, sr=sr, units="time"
        )

        if len(onsets) < 4:
            return

        # Calculate inter-onset intervals
        iois = np.diff(onsets)

        if len(iois) < 3:
            return

        # Check for regular intervals (arp characteristic)
        ioi_std = np.std(iois)
        ioi_mean = np.mean(iois)

        if ioi_mean == 0:
            return

        regularity = 1.0 - (ioi_std / ioi_mean)

        # Arp if highly regular AND fast
        if regularity > 0.7 and ioi_mean < 0.3:  # Regular and < 300ms between notes
            behavior.arpeggiator_detected = True
            behavior.arp_rate_hz = float(1.0 / ioi_mean)

            # Detect pattern direction using pitch tracking
            try:
                f0, _, _ = librosa.pyin(audio, fmin=50, fmax=2000, sr=sr)
                f0 = np.nan_to_num(f0, nan=0)

                # Sample pitches at onset times
                onset_frames = librosa.time_to_frames(onsets, sr=sr)
                onset_pitches = [f0[min(f, len(f0)-1)] for f in onset_frames]
                onset_pitches = [p for p in onset_pitches if p > 0]

                if len(onset_pitches) >= 3:
                    # Determine pattern
                    diffs = np.diff(onset_pitches)
                    up_count = np.sum(diffs > 0)
                    down_count = np.sum(diffs < 0)

                    if up_count > down_count * 2:
                        behavior.arp_pattern = "up"
                    elif down_count > up_count * 2:
                        behavior.arp_pattern = "down"
                    else:
                        behavior.arp_pattern = "updown"
            except Exception:
                pass

    def _detect_sidechain(
        self,
        audio: np.ndarray,
        sr: int,
        tempo: float,
        behavior: SynthBehavior,
    ):
        """Detect sidechain compression (ducking effect)."""
        import librosa

        # Compute RMS envelope
        rms = librosa.feature.rms(y=audio, hop_length=self.hop_length)[0]

        if len(rms) < 10:
            return

        # Look for regular dips in amplitude at beat positions
        beat_duration = 60.0 / tempo
        beat_samples = int(beat_duration * sr / self.hop_length)

        # Compute auto-correlation of RMS
        autocorr = np.correlate(rms - np.mean(rms), rms - np.mean(rms), mode="full")
        autocorr = autocorr[len(autocorr)//2:]

        if len(autocorr) < beat_samples * 2:
            return

        # Check for periodic dips at quarter note
        quarter_beat = beat_samples // 4
        half_beat = beat_samples // 2

        # Find peaks in autocorrelation
        from scipy.signal import find_peaks
        peaks, _ = find_peaks(autocorr[:beat_samples*2], height=np.max(autocorr) * 0.3)

        # Check if peaks align with common sidechain rates
        for peak in peaks:
            for divisor, rate_name in [(4, "quarter"), (2, "eighth"), (1, "sixteenth")]:
                expected = beat_samples // divisor
                if abs(peak - expected) < expected * 0.1:
                    behavior.sidechain_detected = True
                    behavior.sidechain_rate_hz = float(tempo / 60.0 * divisor)

                    # Estimate depth from RMS variation
                    rms_normalized = rms / np.max(rms)
                    behavior.sidechain_depth = float(1.0 - np.min(rms_normalized))
                    return

    def _detect_vibrato_chorus(
        self,
        audio: np.ndarray,
        sr: int,
        behavior: SynthBehavior,
    ):
        """Detect vibrato and chorus effects."""
        import librosa

        # Use pitch tracking for vibrato
        try:
            f0, voiced_flag, voiced_probs = librosa.pyin(
                audio,
                fmin=50,
                fmax=2000,
                sr=sr,
                hop_length=self.hop_length,
            )
        except Exception:
            return

        f0 = np.nan_to_num(f0, nan=0)

        # Find sustained regions
        voiced_regions = np.where(f0 > 0)[0]
        if len(voiced_regions) < 20:
            return

        # Analyze pitch modulation in sustained regions
        # Vibrato = regular pitch oscillation

        # Compute pitch difference from median in each region
        regions = self._find_continuous_regions(voiced_regions)

        for start, end in regions:
            if end - start < 20:
                continue

            region_f0 = f0[start:end]
            median_f0 = np.median(region_f0)

            if median_f0 == 0:
                continue

            # Convert to cents deviation
            cents_dev = 1200 * np.log2(region_f0 / median_f0)

            # Check for periodic modulation
            from scipy.signal import find_peaks
            fft = np.abs(np.fft.rfft(cents_dev - np.mean(cents_dev)))
            freqs = np.fft.rfftfreq(len(cents_dev), d=self.hop_length/sr)

            peaks, properties = find_peaks(fft, height=np.max(fft) * 0.3)

            for peak in peaks[:3]:
                if 4 < freqs[peak] < 8:  # Typical vibrato range: 4-8 Hz
                    behavior.vibrato_detected = True
                    behavior.vibrato_rate_hz = float(freqs[peak])
                    behavior.vibrato_depth_cents = float(np.std(cents_dev) * 2)
                    break

            if behavior.vibrato_detected:
                break

    def _detect_polyphony(
        self,
        audio: np.ndarray,
        sr: int,
        behavior: SynthBehavior,
    ):
        """Detect polyphony level."""
        import librosa

        # Use chroma energy to estimate simultaneous notes
        chroma = librosa.feature.chroma_cqt(y=audio, sr=sr)

        # Count active pitch classes per frame
        threshold = np.max(chroma) * 0.2
        active_per_frame = np.sum(chroma > threshold, axis=0)

        avg_polyphony = np.mean(active_per_frame)
        max_polyphony = np.max(active_per_frame)

        behavior.is_monophonic = avg_polyphony < 1.5
        behavior.is_polyphonic = avg_polyphony >= 2.0

    def _find_continuous_regions(
        self,
        indices: np.ndarray,
        gap_threshold: int = 2,
    ) -> List[Tuple[int, int]]:
        """Find continuous regions in index array."""
        if len(indices) == 0:
            return []

        regions = []
        start = indices[0]
        prev = indices[0]

        for idx in indices[1:]:
            if idx - prev > gap_threshold:
                regions.append((start, prev + 1))
                start = idx
            prev = idx

        regions.append((start, prev + 1))
        return regions

    def _classify_dominant_behavior(self, behavior: SynthBehavior) -> str:
        """Classify the dominant synth behavior."""
        if behavior.arpeggiator_detected:
            return "arp"
        if behavior.supersaw_detected:
            return "supersaw"
        if behavior.sidechain_detected and behavior.sidechain_depth > 0.5:
            return "pumping"
        if behavior.octave_layering and len(behavior.layered_octaves) >= 2:
            return "layered"
        if behavior.glide_detected:
            return "lead"
        if behavior.is_polyphonic and not behavior.arpeggiator_detected:
            return "pad"
        if behavior.is_monophonic:
            return "bass"

        return "standard"


def analyze_synth_behavior(
    audio: np.ndarray,
    sr: int,
    tempo: Optional[float] = None,
) -> SynthBehavior:
    """Convenience function for synth behavior analysis.

    Args:
        audio: Audio signal
        sr: Sample rate
        tempo: Optional tempo hint

    Returns:
        SynthBehavior with detected characteristics
    """
    analyzer = SynthBehaviorAnalyzer()
    return analyzer.analyze(audio, sr, tempo)
