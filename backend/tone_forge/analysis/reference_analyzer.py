"""Reference track production style analyzer.

Analyzes reference tracks to understand production style:
- Section structure (verse, chorus, drop, breakdown)
- Energy curve and dynamics
- Layer density over time
- FX characteristics (reverb, delay patterns)
- Groove and swing analysis

This enables template-based reconstruction and style matching.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class SectionTransition:
    """Transition between arrangement sections."""

    from_section: str
    to_section: str
    time: float
    transition_type: str  # "hard", "fade", "buildup", "breakdown"
    energy_change: float  # -1 to 1 (negative = drop, positive = rise)


@dataclass
class ArrangementSection:
    """A section of the arrangement."""

    type: str  # "intro", "verse", "chorus", "drop", "breakdown", "bridge", "outro"
    start_time: float
    end_time: float
    energy_profile: List[float]  # Energy values over section
    avg_energy: float
    peak_energy: float
    density: float  # 0-1 layer density
    confidence: float  # 0-1 classification confidence

    @property
    def duration(self) -> float:
        return self.end_time - self.start_time

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration": self.duration,
            "avg_energy": self.avg_energy,
            "peak_energy": self.peak_energy,
            "density": self.density,
            "confidence": self.confidence,
        }


@dataclass
class ProductionStyle:
    """Complete production style analysis."""

    # Arrangement
    sections: List[ArrangementSection] = field(default_factory=list)
    section_transitions: List[SectionTransition] = field(default_factory=list)

    # Energy
    energy_curve: List[float] = field(default_factory=list)
    energy_timestamps: List[float] = field(default_factory=list)
    dynamic_range_db: float = 0.0

    # Layering
    layer_density_curve: List[float] = field(default_factory=list)
    peak_layers: int = 0
    avg_layers: float = 0.0

    # FX
    reverb_profile: str = "dry"  # "dry", "room", "hall", "infinite"
    reverb_amount: float = 0.0
    delay_pattern: Optional[str] = None  # "quarter", "eighth", "dotted", None
    delay_feedback: float = 0.0

    # Rhythm
    groove_template: List[float] = field(default_factory=list)
    swing_amount: float = 0.0
    syncopation: float = 0.0

    # Overall characteristics
    tempo: float = 120.0
    key: Optional[str] = None
    genre_hints: List[str] = field(default_factory=list)
    energy_type: str = "balanced"  # "chill", "balanced", "high_energy", "explosive"

    def to_dict(self) -> dict:
        return {
            "arrangement": {
                "sections": [s.to_dict() for s in self.sections],
                "section_count": len(self.sections),
                "transitions": [
                    {
                        "from": t.from_section,
                        "to": t.to_section,
                        "time": t.time,
                        "type": t.transition_type,
                    }
                    for t in self.section_transitions
                ],
            },
            "energy": {
                "dynamic_range_db": self.dynamic_range_db,
                "energy_type": self.energy_type,
            },
            "layering": {
                "peak_layers": self.peak_layers,
                "avg_layers": self.avg_layers,
            },
            "fx": {
                "reverb_profile": self.reverb_profile,
                "reverb_amount": self.reverb_amount,
                "delay_pattern": self.delay_pattern,
                "delay_feedback": self.delay_feedback,
            },
            "rhythm": {
                "swing_amount": self.swing_amount,
                "syncopation": self.syncopation,
            },
            "overall": {
                "tempo": self.tempo,
                "key": self.key,
                "genre_hints": self.genre_hints,
            },
        }


class ReferenceAnalyzer:
    """Analyzes reference tracks for production style.

    Extracts arrangement structure, energy flow, FX characteristics,
    and groove templates for style matching and reconstruction guidance.
    """

    def __init__(
        self,
        hop_length: int = 512,
        n_fft: int = 2048,
        section_min_duration: float = 4.0,  # Minimum section length in seconds
    ):
        self.hop_length = hop_length
        self.n_fft = n_fft
        self.section_min_duration = section_min_duration

    def analyze(
        self,
        audio: np.ndarray,
        sr: int,
        tempo: Optional[float] = None,
    ) -> ProductionStyle:
        """Analyze reference track for production style.

        Args:
            audio: Audio signal (mono or stereo)
            sr: Sample rate
            tempo: Optional tempo hint

        Returns:
            ProductionStyle with analysis results
        """
        import librosa

        # Ensure mono for analysis
        if audio.ndim > 1:
            audio_mono = np.mean(audio, axis=0)
        else:
            audio_mono = audio

        style = ProductionStyle()

        # Estimate tempo if not provided
        if tempo is None:
            tempo, _ = librosa.beat.beat_track(y=audio_mono, sr=sr)
            if hasattr(tempo, "__iter__"):
                tempo = float(tempo[0]) if len(tempo) > 0 else 120.0
            tempo = float(tempo) if tempo > 0 else 120.0
        style.tempo = tempo

        # Analyze components
        self._analyze_energy(audio_mono, sr, style)
        self._analyze_sections(audio_mono, sr, tempo, style)
        self._analyze_layering(audio_mono, sr, style)
        self._analyze_fx(audio_mono, sr, style)
        self._analyze_groove(audio_mono, sr, tempo, style)
        self._detect_key(audio_mono, sr, style)
        self._classify_energy_type(style)
        self._guess_genre(style)

        return style

    def _analyze_energy(
        self,
        audio: np.ndarray,
        sr: int,
        style: ProductionStyle,
    ):
        """Analyze energy curve and dynamics."""
        import librosa

        # RMS energy
        rms = librosa.feature.rms(y=audio, hop_length=self.hop_length)[0]

        # Normalize
        rms_max = np.max(rms)
        if rms_max > 0:
            rms_normalized = rms / rms_max
        else:
            rms_normalized = rms

        # Store energy curve (downsampled for efficiency)
        downsample_factor = max(1, len(rms_normalized) // 500)
        style.energy_curve = rms_normalized[::downsample_factor].tolist()
        style.energy_timestamps = [
            i * downsample_factor * self.hop_length / sr
            for i in range(len(style.energy_curve))
        ]

        # Dynamic range
        rms_db = librosa.amplitude_to_db(rms + 1e-10, ref=np.max)
        style.dynamic_range_db = float(np.max(rms_db) - np.percentile(rms_db, 10))

    def _analyze_sections(
        self,
        audio: np.ndarray,
        sr: int,
        tempo: float,
        style: ProductionStyle,
    ):
        """Detect arrangement sections."""
        import librosa

        # Use spectral contrast and RMS for section boundaries
        rms = librosa.feature.rms(y=audio, hop_length=self.hop_length)[0]
        spectral_contrast = librosa.feature.spectral_contrast(
            y=audio, sr=sr, hop_length=self.hop_length
        )
        contrast_mean = np.mean(spectral_contrast, axis=0)

        # Combine features
        combined = np.vstack([
            rms / (np.max(rms) + 1e-10),
            contrast_mean / (np.max(np.abs(contrast_mean)) + 1e-10),
        ])

        # Detect novelty (section boundaries)
        novelty = self._compute_novelty(combined)

        # Find peaks in novelty
        from scipy.signal import find_peaks
        min_frames = int(self.section_min_duration * sr / self.hop_length)
        peaks, _ = find_peaks(novelty, height=np.mean(novelty), distance=min_frames)

        # Convert to times
        boundary_times = [0.0]
        for peak in peaks:
            t = peak * self.hop_length / sr
            boundary_times.append(t)
        boundary_times.append(len(audio) / sr)

        # Create sections
        sections = []
        for i in range(len(boundary_times) - 1):
            start_t = boundary_times[i]
            end_t = boundary_times[i + 1]

            # Get energy for this section
            start_frame = int(start_t * sr / self.hop_length)
            end_frame = int(end_t * sr / self.hop_length)
            section_rms = rms[start_frame:end_frame]

            if len(section_rms) == 0:
                continue

            avg_energy = float(np.mean(section_rms))
            peak_energy = float(np.max(section_rms))

            # Estimate density from spectral complexity
            section_contrast = contrast_mean[start_frame:end_frame]
            density = float(np.mean(section_contrast) / (np.max(contrast_mean) + 1e-10))
            density = max(0, min(1, (density + 1) / 2))  # Normalize to 0-1

            # Classify section type
            section_type, confidence = self._classify_section(
                avg_energy / (np.max(rms) + 1e-10),
                peak_energy / (np.max(rms) + 1e-10),
                density,
                i,
                len(boundary_times) - 2,  # total sections
            )

            sections.append(ArrangementSection(
                type=section_type,
                start_time=start_t,
                end_time=end_t,
                energy_profile=section_rms.tolist(),
                avg_energy=avg_energy,
                peak_energy=peak_energy,
                density=density,
                confidence=confidence,
            ))

        style.sections = sections

        # Detect transitions
        transitions = []
        for i in range(1, len(sections)):
            prev = sections[i - 1]
            curr = sections[i]

            energy_change = (curr.avg_energy - prev.avg_energy) / (prev.avg_energy + 1e-10)
            energy_change = max(-1, min(1, energy_change))

            # Determine transition type
            if energy_change > 0.5:
                trans_type = "buildup"
            elif energy_change < -0.3:
                trans_type = "breakdown"
            elif abs(energy_change) < 0.1:
                trans_type = "fade"
            else:
                trans_type = "hard"

            transitions.append(SectionTransition(
                from_section=prev.type,
                to_section=curr.type,
                time=curr.start_time,
                transition_type=trans_type,
                energy_change=energy_change,
            ))

        style.section_transitions = transitions

    def _compute_novelty(self, features: np.ndarray) -> np.ndarray:
        """Compute novelty function from features."""
        # Self-similarity matrix
        if features.shape[1] < 2:
            return np.array([0.0])

        # Simple novelty: difference from running mean
        from scipy.ndimage import uniform_filter1d
        smoothed = uniform_filter1d(features, size=20, axis=1)
        novelty = np.sum(np.abs(features - smoothed), axis=0)

        return novelty

    def _classify_section(
        self,
        avg_energy: float,
        peak_energy: float,
        density: float,
        position: int,
        total_sections: int,
    ) -> Tuple[str, float]:
        """Classify section type based on features."""
        confidence = 0.5

        # Position-based heuristics
        relative_position = position / max(total_sections, 1)

        # Intro/outro by position
        if position == 0:
            if avg_energy < 0.3:
                return "intro", 0.8
            else:
                return "verse", 0.6

        if position == total_sections - 1:
            if avg_energy < 0.4:
                return "outro", 0.8
            else:
                return "chorus", 0.6

        # Energy-based classification
        if peak_energy > 0.8 and density > 0.6:
            if avg_energy > 0.7:
                return "drop", 0.7
            else:
                return "chorus", 0.7

        if avg_energy < 0.3:
            return "breakdown", 0.7

        if avg_energy < 0.5 and density < 0.5:
            return "verse", 0.6

        if avg_energy > 0.5 and density > 0.5:
            return "chorus", 0.6

        return "bridge", 0.4

    def _analyze_layering(
        self,
        audio: np.ndarray,
        sr: int,
        style: ProductionStyle,
    ):
        """Analyze layer density over time."""
        import librosa

        # Use spectral bandwidth and flatness as proxies for layering
        spec_bw = librosa.feature.spectral_bandwidth(y=audio, sr=sr, hop_length=self.hop_length)[0]
        spec_flat = librosa.feature.spectral_flatness(y=audio, hop_length=self.hop_length)[0]

        # Higher bandwidth + lower flatness = more layers
        layer_proxy = spec_bw * (1 - spec_flat)

        # Normalize to 0-1
        layer_normalized = layer_proxy / (np.max(layer_proxy) + 1e-10)

        # Estimate layer count (rough)
        # Assume 1-8 layers based on normalized score
        layer_counts = 1 + (layer_normalized * 7).astype(int)

        # Downsample for storage
        downsample_factor = max(1, len(layer_normalized) // 500)
        style.layer_density_curve = layer_normalized[::downsample_factor].tolist()
        style.peak_layers = int(np.max(layer_counts))
        style.avg_layers = float(np.mean(layer_counts))

    def _analyze_fx(
        self,
        audio: np.ndarray,
        sr: int,
        style: ProductionStyle,
    ):
        """Analyze FX characteristics (reverb, delay)."""
        import librosa

        # Reverb detection via RT60 estimation (simplified)
        # Use spectral decay characteristics
        spec = np.abs(librosa.stft(audio, n_fft=self.n_fft, hop_length=self.hop_length))

        # Look at energy decay after transients
        energy = np.sum(spec, axis=0)
        onset_env = librosa.onset.onset_strength(y=audio, sr=sr)

        # Find onsets
        onsets = librosa.onset.onset_detect(onset_envelope=onset_env, sr=sr, units="frames")

        decay_times = []
        for onset in onsets[:20]:  # Check first 20 onsets
            if onset + 50 < len(energy):
                peak = energy[onset]
                # Find -20dB point
                for i in range(onset, min(onset + 100, len(energy))):
                    if energy[i] < peak * 0.1:
                        decay_times.append((i - onset) * self.hop_length / sr)
                        break

        if decay_times:
            avg_decay = np.median(decay_times)

            if avg_decay < 0.1:
                style.reverb_profile = "dry"
                style.reverb_amount = 0.1
            elif avg_decay < 0.3:
                style.reverb_profile = "room"
                style.reverb_amount = 0.3
            elif avg_decay < 0.8:
                style.reverb_profile = "hall"
                style.reverb_amount = 0.6
            else:
                style.reverb_profile = "infinite"
                style.reverb_amount = 0.9

        # Delay detection via auto-correlation
        autocorr = np.correlate(energy[:1000], energy[:1000], mode="full")
        autocorr = autocorr[len(autocorr)//2:]

        # Look for echo peaks
        from scipy.signal import find_peaks
        peaks, properties = find_peaks(
            autocorr[10:],  # Skip first few frames
            height=np.max(autocorr) * 0.3,
            distance=5,
        )

        if len(peaks) >= 2:
            # Delay detected
            delay_frames = peaks[0] + 10
            delay_time = delay_frames * self.hop_length / sr

            beat_duration = 60.0 / style.tempo

            if abs(delay_time - beat_duration) < 0.05:
                style.delay_pattern = "quarter"
            elif abs(delay_time - beat_duration / 2) < 0.03:
                style.delay_pattern = "eighth"
            elif abs(delay_time - beat_duration * 0.75) < 0.05:
                style.delay_pattern = "dotted"

            if style.delay_pattern:
                style.delay_feedback = float(autocorr[peaks[0] + 10] / autocorr[0])

    def _analyze_groove(
        self,
        audio: np.ndarray,
        sr: int,
        tempo: float,
        style: ProductionStyle,
    ):
        """Analyze groove and swing."""
        import librosa

        # Onset detection
        onset_env = librosa.onset.onset_strength(y=audio, sr=sr)
        onsets = librosa.onset.onset_detect(
            onset_envelope=onset_env,
            sr=sr,
            units="time",
        )

        if len(onsets) < 8:
            return

        # Calculate IOIs (inter-onset intervals)
        iois = np.diff(onsets)

        beat_duration = 60.0 / tempo
        eighth_duration = beat_duration / 2

        # Group IOIs near eighth note values
        eighth_iois = [ioi for ioi in iois if abs(ioi - eighth_duration) < eighth_duration * 0.3]

        if len(eighth_iois) < 4:
            return

        # Swing detection: alternating long-short pattern
        ratios = []
        for i in range(0, len(eighth_iois) - 1, 2):
            if eighth_iois[i + 1] > 0:
                ratios.append(eighth_iois[i] / eighth_iois[i + 1])

        if ratios:
            avg_ratio = np.mean(ratios)
            # Perfect swing = 2:1, no swing = 1:1
            if avg_ratio > 1.2:
                style.swing_amount = float(min(1.0, (avg_ratio - 1) / 1.0))
            else:
                style.swing_amount = 0.0

        # Syncopation detection
        # Check for off-beat emphasis
        beat_positions = np.arange(0, onsets[-1], beat_duration)

        off_beat_count = 0
        for onset in onsets:
            # Find nearest beat
            nearest_beat = beat_positions[np.argmin(np.abs(beat_positions - onset))]
            offset = abs(onset - nearest_beat)

            # Off-beat if more than 25% away from beat
            if offset > beat_duration * 0.25:
                off_beat_count += 1

        style.syncopation = float(off_beat_count / len(onsets))

    def _detect_key(
        self,
        audio: np.ndarray,
        sr: int,
        style: ProductionStyle,
    ):
        """Detect musical key."""
        import librosa

        # Compute chroma
        chroma = librosa.feature.chroma_cqt(y=audio, sr=sr)
        avg_chroma = np.mean(chroma, axis=1)

        # Major and minor templates
        major_template = np.array([1, 0, 1, 0, 1, 1, 0, 1, 0, 1, 0, 1])
        minor_template = np.array([1, 0, 1, 1, 0, 1, 0, 1, 1, 0, 1, 0])

        note_names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

        best_key = None
        best_correlation = -1

        for i in range(12):
            # Rotate templates
            major_rotated = np.roll(major_template, i)
            minor_rotated = np.roll(minor_template, i)

            major_corr = np.corrcoef(avg_chroma, major_rotated)[0, 1]
            minor_corr = np.corrcoef(avg_chroma, minor_rotated)[0, 1]

            if major_corr > best_correlation:
                best_correlation = major_corr
                best_key = f"{note_names[i]} major"

            if minor_corr > best_correlation:
                best_correlation = minor_corr
                best_key = f"{note_names[i]} minor"

        style.key = best_key

    def _classify_energy_type(self, style: ProductionStyle):
        """Classify overall energy type."""
        if not style.energy_curve:
            return

        avg_energy = np.mean(style.energy_curve)
        energy_variance = np.var(style.energy_curve)

        if avg_energy < 0.3 and energy_variance < 0.02:
            style.energy_type = "chill"
        elif avg_energy < 0.5:
            style.energy_type = "balanced"
        elif energy_variance > 0.1:
            style.energy_type = "explosive"
        else:
            style.energy_type = "high_energy"

    def _guess_genre(self, style: ProductionStyle):
        """Guess genre hints from analysis."""
        hints = []

        # Based on tempo
        if style.tempo < 100:
            hints.append("downtempo")
        elif 100 <= style.tempo < 130:
            hints.append("house")
        elif 130 <= style.tempo < 145:
            hints.append("techno")
        elif 145 <= style.tempo < 160:
            hints.append("trance")
        elif style.tempo >= 160:
            hints.append("drum_and_bass")

        # Based on energy
        if style.energy_type == "explosive":
            hints.append("edm")
        elif style.energy_type == "chill":
            hints.append("ambient")

        # Based on sections
        section_types = [s.type for s in style.sections]
        if "drop" in section_types:
            if "edm" not in hints:
                hints.append("edm")

        # Based on FX
        if style.reverb_profile == "infinite":
            hints.append("ambient")
        elif style.reverb_profile == "dry" and style.swing_amount < 0.1:
            hints.append("electronic")

        style.genre_hints = hints


def analyze_reference(
    audio: np.ndarray,
    sr: int,
    tempo: Optional[float] = None,
) -> ProductionStyle:
    """Convenience function for reference track analysis.

    Args:
        audio: Audio signal
        sr: Sample rate
        tempo: Optional tempo hint

    Returns:
        ProductionStyle with analysis
    """
    analyzer = ReferenceAnalyzer()
    return analyzer.analyze(audio, sr, tempo)


def compare_styles(
    style_a: ProductionStyle,
    style_b: ProductionStyle,
) -> float:
    """Compare two production styles.

    Args:
        style_a: First production style
        style_b: Second production style

    Returns:
        Similarity score 0-1
    """
    scores = []

    # Tempo similarity
    tempo_diff = abs(style_a.tempo - style_b.tempo)
    tempo_sim = max(0, 1 - tempo_diff / 50)
    scores.append(tempo_sim)

    # Energy type similarity
    energy_types = ["chill", "balanced", "high_energy", "explosive"]
    try:
        idx_a = energy_types.index(style_a.energy_type)
        idx_b = energy_types.index(style_b.energy_type)
        energy_sim = 1 - abs(idx_a - idx_b) / len(energy_types)
    except ValueError:
        energy_sim = 0.5
    scores.append(energy_sim)

    # Dynamic range similarity
    dr_diff = abs(style_a.dynamic_range_db - style_b.dynamic_range_db)
    dr_sim = max(0, 1 - dr_diff / 20)
    scores.append(dr_sim)

    # Reverb similarity
    reverb_types = ["dry", "room", "hall", "infinite"]
    try:
        idx_a = reverb_types.index(style_a.reverb_profile)
        idx_b = reverb_types.index(style_b.reverb_profile)
        reverb_sim = 1 - abs(idx_a - idx_b) / len(reverb_types)
    except ValueError:
        reverb_sim = 0.5
    scores.append(reverb_sim)

    # Swing similarity
    swing_diff = abs(style_a.swing_amount - style_b.swing_amount)
    swing_sim = 1 - swing_diff
    scores.append(swing_sim)

    # Genre overlap
    common_genres = set(style_a.genre_hints) & set(style_b.genre_hints)
    all_genres = set(style_a.genre_hints) | set(style_b.genre_hints)
    genre_sim = len(common_genres) / max(len(all_genres), 1)
    scores.append(genre_sim)

    return float(np.mean(scores))
