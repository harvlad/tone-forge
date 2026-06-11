"""Temporal continuity analysis for sustained content.

Tracks harmonic content and phrase structure over time.
Critical for:
- Ambient/shoegaze reconstruction
- Synthwave pads
- Sustained guitar tones
- Reverberant textures

Components:
- TemporalContinuityAnalyzer: Track sustained harmonic regions
- HarmonicTracker: Track individual harmonics over time
- PhraseDetector: Identify musical phrases
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


class EnvelopeType(str, Enum):
    """Types of amplitude envelopes."""

    SUSTAINED = "sustained"
    DECAYING = "decaying"
    SWELLING = "swelling"
    PULSING = "pulsing"
    STATIC = "static"


class PhraseType(str, Enum):
    """Types of musical phrases."""

    MELODIC = "melodic"
    HARMONIC = "harmonic"
    RHYTHMIC = "rhythmic"
    TEXTURAL = "textural"
    TRANSITIONAL = "transitional"


@dataclass
class HarmonicTrack:
    """A tracked harmonic over time."""

    fundamental_hz: float
    start_time: float
    end_time: float
    harmonic_indices: List[int]  # Which harmonics are present (1=fundamental, 2=octave, etc.)
    amplitude_contour: np.ndarray  # Amplitude over time
    frequency_contour: np.ndarray  # Frequency drift over time
    stability: float  # 0-1, how stable the harmonic is

    @property
    def duration(self) -> float:
        """Duration of the harmonic track."""
        return self.end_time - self.start_time

    @property
    def is_stable(self) -> bool:
        """Whether this is a stable harmonic."""
        return self.stability > 0.7


@dataclass
class ContinuityRegion:
    """A region of sustained harmonic content."""

    start_time: float
    end_time: float
    fundamental_hz: float
    harmonics: List[float]  # Frequencies of detected harmonics
    stability: float  # 0-1
    envelope_type: EnvelopeType
    harmonic_tracks: List[HarmonicTrack] = field(default_factory=list)

    # Quality metrics
    pitch_drift: float = 0.0  # Amount of pitch drift in cents
    amplitude_variation: float = 0.0  # Coefficient of variation

    @property
    def duration(self) -> float:
        """Duration of this region."""
        return self.end_time - self.start_time

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "start_time": self.start_time,
            "end_time": self.end_time,
            "fundamental_hz": self.fundamental_hz,
            "harmonics": self.harmonics,
            "stability": self.stability,
            "envelope_type": self.envelope_type.value,
            "duration": self.duration,
            "pitch_drift": self.pitch_drift,
            "amplitude_variation": self.amplitude_variation,
        }


@dataclass
class Phrase:
    """A detected musical phrase."""

    start_time: float
    end_time: float
    phrase_type: PhraseType
    confidence: float
    notes: List[Tuple[float, float, float]] = field(default_factory=list)  # (pitch_hz, start, end)
    continuity_regions: List[ContinuityRegion] = field(default_factory=list)

    # Phrase characteristics
    pitch_range_semitones: float = 0.0
    note_density: float = 0.0  # Notes per second
    harmonic_coherence: float = 0.0  # How harmonically related are the notes

    @property
    def duration(self) -> float:
        """Duration of the phrase."""
        return self.end_time - self.start_time

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "start_time": self.start_time,
            "end_time": self.end_time,
            "phrase_type": self.phrase_type.value,
            "confidence": self.confidence,
            "duration": self.duration,
            "pitch_range_semitones": self.pitch_range_semitones,
            "note_density": self.note_density,
            "harmonic_coherence": self.harmonic_coherence,
        }


@dataclass
class ContinuityAnalysis:
    """Complete temporal continuity analysis."""

    duration: float
    regions: List[ContinuityRegion]
    phrases: List[Phrase]
    harmonic_tracks: List[HarmonicTrack]

    # Summary metrics
    sustained_ratio: float = 0.0  # Ratio of time with sustained content
    average_stability: float = 0.0
    phrase_count: int = 0
    dominant_envelope: EnvelopeType = EnvelopeType.STATIC

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "duration": self.duration,
            "regions": [r.to_dict() for r in self.regions],
            "phrases": [p.to_dict() for p in self.phrases],
            "sustained_ratio": self.sustained_ratio,
            "average_stability": self.average_stability,
            "phrase_count": self.phrase_count,
            "dominant_envelope": self.dominant_envelope.value,
        }


class HarmonicTracker:
    """Track harmonics over time.

    Uses pitch detection and harmonic analysis to track
    fundamental frequencies and their harmonics across frames.
    """

    def __init__(
        self,
        hop_length: int = 512,
        n_fft: int = 2048,
        min_track_duration: float = 0.1,
        frequency_tolerance: float = 0.05,  # 5% tolerance
    ):
        """Initialize the tracker.

        Args:
            hop_length: Hop length for analysis
            n_fft: FFT size
            min_track_duration: Minimum duration for a valid track
            frequency_tolerance: Tolerance for frequency matching
        """
        self.hop_length = hop_length
        self.n_fft = n_fft
        self.min_track_duration = min_track_duration
        self.frequency_tolerance = frequency_tolerance

    def track(
        self,
        audio: np.ndarray,
        sr: int,
    ) -> List[HarmonicTrack]:
        """Track harmonics over time.

        Args:
            audio: Audio data (mono)
            sr: Sample rate

        Returns:
            List of HarmonicTrack objects
        """
        try:
            import librosa
        except ImportError:
            logger.warning("librosa not available, returning empty tracks")
            return []

        # For long audio, process in chunks to avoid slow pyin
        # pyin is O(n) but with high constant factor
        max_chunk_seconds = 30
        max_chunk_samples = max_chunk_seconds * sr
        n_samples = len(audio)

        if n_samples > max_chunk_samples:
            # Process multiple chunks and combine results
            all_tracks = []
            chunk_starts = list(range(0, n_samples, max_chunk_samples))

            for chunk_idx, chunk_start in enumerate(chunk_starts):
                chunk_end = min(chunk_start + max_chunk_samples, n_samples)
                chunk_audio = audio[chunk_start:chunk_end]
                time_offset = chunk_start / sr

                chunk_tracks = self._track_chunk(chunk_audio, sr, time_offset)
                all_tracks.extend(chunk_tracks)

            return all_tracks
        else:
            return self._track_chunk(audio, sr, 0.0)

    def _track_chunk(
        self,
        audio: np.ndarray,
        sr: int,
        time_offset: float = 0.0,
    ) -> List[HarmonicTrack]:
        """Track harmonics in a single chunk."""
        import librosa

        # Get pitch estimates
        f0, voiced_flag, voiced_probs = librosa.pyin(
            audio,
            fmin=librosa.note_to_hz('C1'),
            fmax=librosa.note_to_hz('C7'),
            sr=sr,
            hop_length=self.hop_length,
        )

        # Get spectrogram for harmonic analysis
        spec = np.abs(librosa.stft(audio, n_fft=self.n_fft, hop_length=self.hop_length))
        freqs = librosa.fft_frequencies(sr=sr, n_fft=self.n_fft)

        # Track continuous pitch regions
        tracks = []
        current_track = None
        track_start_frame = 0

        for i in range(len(f0)):
            if not np.isnan(f0[i]) and voiced_probs[i] > 0.5:
                if current_track is None:
                    # Start new track
                    current_track = {
                        "fundamental": f0[i],
                        "f0_values": [f0[i]],
                        "amplitudes": [],
                        "start_frame": i,
                    }
                    track_start_frame = i
                else:
                    # Check if this continues the current track
                    freq_ratio = f0[i] / current_track["fundamental"]
                    if abs(freq_ratio - 1.0) < self.frequency_tolerance:
                        # Continues track
                        current_track["f0_values"].append(f0[i])
                    else:
                        # New pitch - finish current track and start new
                        tracks.append(self._finalize_track(
                            current_track, spec, freqs, sr, track_start_frame, time_offset
                        ))
                        current_track = {
                            "fundamental": f0[i],
                            "f0_values": [f0[i]],
                            "amplitudes": [],
                            "start_frame": i,
                        }
                        track_start_frame = i
            else:
                # Unvoiced/silence
                if current_track is not None:
                    tracks.append(self._finalize_track(
                        current_track, spec, freqs, sr, track_start_frame, time_offset
                    ))
                    current_track = None

        # Finalize last track
        if current_track is not None:
            tracks.append(self._finalize_track(
                current_track, spec, freqs, sr, track_start_frame, time_offset
            ))

        # Filter short tracks
        duration_threshold = self.min_track_duration
        tracks = [t for t in tracks if t is not None and t.duration >= duration_threshold]

        return tracks

    def _finalize_track(
        self,
        track_data: dict,
        spec: np.ndarray,
        freqs: np.ndarray,
        sr: int,
        start_frame: int,
        time_offset: float = 0.0,
    ) -> Optional[HarmonicTrack]:
        """Convert track data to HarmonicTrack."""
        f0_values = np.array(track_data["f0_values"])
        if len(f0_values) < 2:
            return None

        fundamental = np.median(f0_values)
        end_frame = start_frame + len(f0_values)

        # Find which harmonics are present
        harmonic_indices = []
        for h in range(1, 9):  # Check up to 8th harmonic
            harmonic_freq = fundamental * h
            freq_idx = np.argmin(np.abs(freqs - harmonic_freq))

            # Check if there's energy at this harmonic
            if freq_idx < len(freqs):
                harmonic_energy = np.mean(spec[freq_idx, start_frame:end_frame])
                fundamental_energy = np.mean(spec[np.argmin(np.abs(freqs - fundamental)), start_frame:end_frame])

                if harmonic_energy > fundamental_energy * 0.1:  # 10% threshold
                    harmonic_indices.append(h)

        # Get amplitude contour
        fund_idx = np.argmin(np.abs(freqs - fundamental))
        amplitude_contour = spec[fund_idx, start_frame:end_frame]

        # Compute stability
        if np.mean(f0_values) > 0:
            cv = np.std(f0_values) / np.mean(f0_values)
            stability = 1 / (1 + cv * 10)  # Scale CV to 0-1
        else:
            stability = 0.0

        start_time = start_frame * self.hop_length / sr + time_offset
        end_time = end_frame * self.hop_length / sr + time_offset

        return HarmonicTrack(
            fundamental_hz=float(fundamental),
            start_time=start_time,
            end_time=end_time,
            harmonic_indices=harmonic_indices if harmonic_indices else [1],
            amplitude_contour=amplitude_contour,
            frequency_contour=f0_values,
            stability=float(stability),
        )


class PhraseDetector:
    """Detect musical phrases in audio.

    Identifies phrase boundaries based on:
    - Silence/pauses
    - Melodic contour changes
    - Harmonic cadences
    - Energy changes
    """

    def __init__(
        self,
        hop_length: int = 512,
        min_phrase_duration: float = 1.0,
        max_phrase_duration: float = 16.0,
        silence_threshold: float = 0.01,
    ):
        """Initialize the detector.

        Args:
            hop_length: Hop length for analysis
            min_phrase_duration: Minimum phrase duration in seconds
            max_phrase_duration: Maximum phrase duration in seconds
            silence_threshold: RMS threshold for silence detection
        """
        self.hop_length = hop_length
        self.min_phrase_duration = min_phrase_duration
        self.max_phrase_duration = max_phrase_duration
        self.silence_threshold = silence_threshold

    def detect(
        self,
        audio: np.ndarray,
        sr: int,
        continuity_regions: Optional[List[ContinuityRegion]] = None,
    ) -> List[Phrase]:
        """Detect phrases in audio.

        Args:
            audio: Audio data (mono)
            sr: Sample rate
            continuity_regions: Optional pre-computed continuity regions

        Returns:
            List of detected Phrase objects
        """
        try:
            import librosa
        except ImportError:
            logger.warning("librosa not available, returning empty phrases")
            return []

        duration = len(audio) / sr

        # Compute features for phrase detection
        rms = librosa.feature.rms(y=audio, hop_length=self.hop_length)[0]
        onset_env = librosa.onset.onset_strength(y=audio, sr=sr, hop_length=self.hop_length)

        # Find silence regions (phrase boundaries)
        silence_mask = rms < self.silence_threshold
        silence_regions = self._find_regions(silence_mask, sr, self.hop_length)

        # Find phrase boundaries
        phrase_boundaries = [0.0]

        # Add silence-based boundaries
        for start, end in silence_regions:
            if end - start > 0.1:  # Significant pause
                phrase_boundaries.append((start + end) / 2)

        # Add onset-based boundaries (significant energy increases)
        onset_frames = librosa.onset.onset_detect(
            onset_envelope=onset_env,
            sr=sr,
            hop_length=self.hop_length,
        )

        # Find onsets that follow low energy regions
        for onset in onset_frames:
            onset_time = onset * self.hop_length / sr
            # Check if this is a "phrase start" onset
            lookback = max(0, onset - 10)
            pre_onset_energy = np.mean(rms[lookback:onset]) if onset > 0 else 0
            post_onset_energy = np.mean(rms[onset:min(onset+5, len(rms))])

            if post_onset_energy > pre_onset_energy * 3:  # Significant jump
                phrase_boundaries.append(onset_time)

        phrase_boundaries.append(duration)
        phrase_boundaries = sorted(set(phrase_boundaries))

        # Build phrases from boundaries
        phrases = []
        for i in range(len(phrase_boundaries) - 1):
            start = phrase_boundaries[i]
            end = phrase_boundaries[i + 1]

            # Skip too short or too long
            if end - start < self.min_phrase_duration:
                continue
            if end - start > self.max_phrase_duration:
                # Split long phrases
                num_splits = int((end - start) / self.max_phrase_duration) + 1
                split_duration = (end - start) / num_splits
                for j in range(num_splits):
                    split_start = start + j * split_duration
                    split_end = start + (j + 1) * split_duration
                    phrase = self._analyze_phrase(
                        audio, sr, split_start, split_end, continuity_regions
                    )
                    if phrase is not None:
                        phrases.append(phrase)
            else:
                phrase = self._analyze_phrase(
                    audio, sr, start, end, continuity_regions
                )
                if phrase is not None:
                    phrases.append(phrase)

        return phrases

    def _analyze_phrase(
        self,
        audio: np.ndarray,
        sr: int,
        start: float,
        end: float,
        continuity_regions: Optional[List[ContinuityRegion]],
    ) -> Optional[Phrase]:
        """Analyze a single phrase."""
        try:
            import librosa
        except ImportError:
            return None

        start_sample = int(start * sr)
        end_sample = int(end * sr)
        phrase_audio = audio[start_sample:end_sample]

        if len(phrase_audio) < 1024:
            return None

        # Determine phrase type
        # Compute features
        rms = librosa.feature.rms(y=phrase_audio, hop_length=self.hop_length)[0]
        onset_env = librosa.onset.onset_strength(y=phrase_audio, sr=sr, hop_length=self.hop_length)

        # Pitch tracking
        f0, voiced_flag, voiced_probs = librosa.pyin(
            phrase_audio,
            fmin=librosa.note_to_hz('C1'),
            fmax=librosa.note_to_hz('C7'),
            sr=sr,
            hop_length=self.hop_length,
        )

        f0_valid = f0[~np.isnan(f0)]

        # Classify phrase type
        has_clear_pitch = len(f0_valid) > len(f0) * 0.3 and np.mean(voiced_probs[~np.isnan(f0)]) > 0.5
        has_rhythmic_onsets = len(onset_env) > 0 and np.std(onset_env) > np.mean(onset_env) * 0.5
        is_sustained = np.mean(rms > self.silence_threshold * 2) > 0.7

        if has_clear_pitch and not is_sustained:
            phrase_type = PhraseType.MELODIC
            confidence = 0.7
        elif has_clear_pitch and is_sustained:
            phrase_type = PhraseType.HARMONIC
            confidence = 0.7
        elif has_rhythmic_onsets and not has_clear_pitch:
            phrase_type = PhraseType.RHYTHMIC
            confidence = 0.6
        elif is_sustained and not has_clear_pitch:
            phrase_type = PhraseType.TEXTURAL
            confidence = 0.6
        else:
            phrase_type = PhraseType.TRANSITIONAL
            confidence = 0.5

        # Compute phrase characteristics
        if len(f0_valid) > 1:
            pitch_range = np.max(f0_valid) / np.min(f0_valid)
            pitch_range_semitones = 12 * np.log2(pitch_range) if pitch_range > 0 else 0
        else:
            pitch_range_semitones = 0

        onset_frames = librosa.onset.onset_detect(
            onset_envelope=onset_env,
            sr=sr,
            hop_length=self.hop_length,
        )
        duration = end - start
        note_density = len(onset_frames) / duration if duration > 0 else 0

        # Get continuity regions for this phrase
        phrase_regions = []
        if continuity_regions:
            phrase_regions = [
                r for r in continuity_regions
                if r.start_time < end and r.end_time > start
            ]

        return Phrase(
            start_time=start,
            end_time=end,
            phrase_type=phrase_type,
            confidence=confidence,
            continuity_regions=phrase_regions,
            pitch_range_semitones=float(pitch_range_semitones),
            note_density=float(note_density),
            harmonic_coherence=0.5,  # Would need more analysis
        )

    def _find_regions(
        self,
        mask: np.ndarray,
        sr: int,
        hop_length: int,
    ) -> List[Tuple[float, float]]:
        """Find contiguous regions in a boolean mask."""
        regions = []
        in_region = False
        start_frame = 0

        for i, active in enumerate(mask):
            if active and not in_region:
                in_region = True
                start_frame = i
            elif not active and in_region:
                in_region = False
                start_time = start_frame * hop_length / sr
                end_time = i * hop_length / sr
                regions.append((start_time, end_time))

        if in_region:
            start_time = start_frame * hop_length / sr
            end_time = len(mask) * hop_length / sr
            regions.append((start_time, end_time))

        return regions


class TemporalContinuityAnalyzer:
    """Analyze temporal continuity of audio.

    Combines harmonic tracking and phrase detection to understand
    the sustained content structure of audio.
    """

    def __init__(
        self,
        hop_length: int = 512,
        n_fft: int = 2048,
        min_region_duration: float = 0.2,
    ):
        """Initialize the analyzer.

        Args:
            hop_length: Hop length for analysis
            n_fft: FFT size
            min_region_duration: Minimum duration for continuity regions
        """
        self.hop_length = hop_length
        self.n_fft = n_fft
        self.min_region_duration = min_region_duration
        self.harmonic_tracker = HarmonicTracker(hop_length, n_fft)
        self.phrase_detector = PhraseDetector(hop_length)

    def analyze(
        self,
        audio: np.ndarray,
        sr: int,
    ) -> ContinuityAnalysis:
        """Analyze temporal continuity.

        Args:
            audio: Audio data (mono or stereo)
            sr: Sample rate

        Returns:
            ContinuityAnalysis with regions, phrases, and tracks
        """
        # Convert to mono
        if audio.ndim == 2:
            audio_mono = np.mean(audio, axis=0)
        else:
            audio_mono = audio

        duration = len(audio_mono) / sr

        # Track harmonics
        harmonic_tracks = self.harmonic_tracker.track(audio_mono, sr)

        # Build continuity regions from harmonic tracks
        regions = self._build_regions(harmonic_tracks, audio_mono, sr)

        # Detect phrases
        phrases = self.phrase_detector.detect(audio_mono, sr, regions)

        # Compute summary metrics
        if regions:
            total_sustained_time = sum(r.duration for r in regions)
            sustained_ratio = total_sustained_time / duration
            average_stability = np.mean([r.stability for r in regions])
        else:
            sustained_ratio = 0.0
            average_stability = 0.0

        # Determine dominant envelope type
        if regions:
            envelope_counts = {}
            for r in regions:
                envelope_counts[r.envelope_type] = envelope_counts.get(r.envelope_type, 0) + r.duration
            dominant_envelope = max(envelope_counts.items(), key=lambda x: x[1])[0]
        else:
            dominant_envelope = EnvelopeType.STATIC

        return ContinuityAnalysis(
            duration=duration,
            regions=regions,
            phrases=phrases,
            harmonic_tracks=harmonic_tracks,
            sustained_ratio=sustained_ratio,
            average_stability=average_stability,
            phrase_count=len(phrases),
            dominant_envelope=dominant_envelope,
        )

    def _build_regions(
        self,
        tracks: List[HarmonicTrack],
        audio: np.ndarray,
        sr: int,
    ) -> List[ContinuityRegion]:
        """Build continuity regions from harmonic tracks."""
        if not tracks:
            return []

        regions = []

        for track in tracks:
            if track.duration < self.min_region_duration:
                continue

            # Determine envelope type
            envelope_type = self._classify_envelope(track.amplitude_contour)

            # Compute pitch drift
            if len(track.frequency_contour) > 1:
                freq_range = np.max(track.frequency_contour) / np.min(track.frequency_contour)
                pitch_drift = 1200 * np.log2(freq_range)  # In cents
            else:
                pitch_drift = 0.0

            # Compute amplitude variation
            if np.mean(track.amplitude_contour) > 0:
                amplitude_variation = np.std(track.amplitude_contour) / np.mean(track.amplitude_contour)
            else:
                amplitude_variation = 0.0

            # Get harmonics in Hz
            harmonics = [track.fundamental_hz * h for h in track.harmonic_indices]

            regions.append(ContinuityRegion(
                start_time=track.start_time,
                end_time=track.end_time,
                fundamental_hz=track.fundamental_hz,
                harmonics=harmonics,
                stability=track.stability,
                envelope_type=envelope_type,
                harmonic_tracks=[track],
                pitch_drift=float(pitch_drift),
                amplitude_variation=float(amplitude_variation),
            ))

        # Merge overlapping regions with same fundamental
        regions = self._merge_regions(regions)

        return regions

    def _classify_envelope(self, amplitude_contour: np.ndarray) -> EnvelopeType:
        """Classify the envelope type from amplitude contour."""
        if len(amplitude_contour) < 3:
            return EnvelopeType.STATIC

        # Normalize
        if np.max(amplitude_contour) > 0:
            norm_amp = amplitude_contour / np.max(amplitude_contour)
        else:
            return EnvelopeType.STATIC

        # Compute trend
        x = np.arange(len(norm_amp))
        slope, _ = np.polyfit(x, norm_amp, 1)

        # Compute variation
        residuals = norm_amp - (slope * x + np.mean(norm_amp))
        variation = np.std(residuals)

        # Classify
        if abs(slope) < 0.001 and variation < 0.1:
            return EnvelopeType.STATIC
        elif slope < -0.01:
            return EnvelopeType.DECAYING
        elif slope > 0.01:
            return EnvelopeType.SWELLING
        elif variation > 0.2:
            return EnvelopeType.PULSING
        else:
            return EnvelopeType.SUSTAINED

    def _merge_regions(
        self,
        regions: List[ContinuityRegion],
    ) -> List[ContinuityRegion]:
        """Merge overlapping regions with similar fundamentals."""
        if len(regions) <= 1:
            return regions

        # Sort by start time
        regions = sorted(regions, key=lambda r: r.start_time)

        merged = []
        current = regions[0]

        for next_region in regions[1:]:
            # Check if overlapping and similar fundamental
            overlaps = next_region.start_time < current.end_time
            similar_freq = abs(next_region.fundamental_hz - current.fundamental_hz) / current.fundamental_hz < 0.1

            if overlaps and similar_freq:
                # Merge
                current = ContinuityRegion(
                    start_time=current.start_time,
                    end_time=max(current.end_time, next_region.end_time),
                    fundamental_hz=(current.fundamental_hz + next_region.fundamental_hz) / 2,
                    harmonics=list(set(current.harmonics + next_region.harmonics)),
                    stability=(current.stability + next_region.stability) / 2,
                    envelope_type=current.envelope_type,
                    harmonic_tracks=current.harmonic_tracks + next_region.harmonic_tracks,
                    pitch_drift=max(current.pitch_drift, next_region.pitch_drift),
                    amplitude_variation=(current.amplitude_variation + next_region.amplitude_variation) / 2,
                )
            else:
                merged.append(current)
                current = next_region

        merged.append(current)
        return merged

    def merge_with_notes(
        self,
        notes: List[Tuple[int, float, float, int]],
        regions: List[ContinuityRegion],
    ) -> List[Tuple[int, float, float, int]]:
        """Refine note boundaries using continuity information.

        Args:
            notes: List of (pitch, start, end, velocity) tuples
            regions: Continuity regions

        Returns:
            Refined notes with adjusted boundaries
        """
        if not regions:
            return notes

        refined_notes = []

        for pitch, start, end, velocity in notes:
            # Find overlapping continuity regions
            overlapping = [
                r for r in regions
                if r.start_time < end and r.end_time > start
            ]

            if not overlapping:
                refined_notes.append((pitch, start, end, velocity))
                continue

            # Check if note should be extended based on continuity
            for region in overlapping:
                # If the region is stable and matches the note pitch
                note_freq = 440 * 2 ** ((pitch - 69) / 12)
                freq_ratio = note_freq / region.fundamental_hz

                # Check if note is fundamental or harmonic of region
                is_harmonic = any(
                    abs(freq_ratio - h) < 0.1 for h in [1, 2, 3, 4]
                )

                if is_harmonic and region.stability > 0.6:
                    # Extend note to match region
                    new_start = min(start, region.start_time)
                    new_end = max(end, region.end_time)

                    # Only extend if it makes sense
                    if new_end - new_start < (end - start) * 3:  # Max 3x extension
                        start = new_start
                        end = new_end

            refined_notes.append((pitch, start, end, velocity))

        return refined_notes


# Module-level singletons
_analyzer: Optional[TemporalContinuityAnalyzer] = None
_tracker: Optional[HarmonicTracker] = None
_phrase_detector: Optional[PhraseDetector] = None


def get_continuity_analyzer() -> TemporalContinuityAnalyzer:
    """Get the global continuity analyzer instance."""
    global _analyzer
    if _analyzer is None:
        _analyzer = TemporalContinuityAnalyzer()
    return _analyzer


def get_harmonic_tracker() -> HarmonicTracker:
    """Get the global harmonic tracker instance."""
    global _tracker
    if _tracker is None:
        _tracker = HarmonicTracker()
    return _tracker


def get_phrase_detector() -> PhraseDetector:
    """Get the global phrase detector instance."""
    global _phrase_detector
    if _phrase_detector is None:
        _phrase_detector = PhraseDetector()
    return _phrase_detector


def analyze_continuity(
    audio: np.ndarray,
    sr: int,
) -> ContinuityAnalysis:
    """Convenience function to analyze temporal continuity.

    Args:
        audio: Audio data
        sr: Sample rate

    Returns:
        ContinuityAnalysis
    """
    analyzer = get_continuity_analyzer()
    return analyzer.analyze(audio, sr)
