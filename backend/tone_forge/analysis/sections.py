"""Section detection for arrangement analysis.

Detects high-level arrangement sections:
- Intro / Outro
- Verse
- Chorus
- Drop
- Breakdown
- Bridge
- Transition

Builds on temporal continuity analysis and energy curves.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


class SectionType(str, Enum):
    """Types of arrangement sections."""

    INTRO = "intro"
    VERSE = "verse"
    PRECHORUS = "prechorus"
    CHORUS = "chorus"
    DROP = "drop"
    BREAKDOWN = "breakdown"
    BRIDGE = "bridge"
    BUILDUP = "buildup"
    TRANSITION = "transition"
    OUTRO = "outro"
    UNKNOWN = "unknown"


@dataclass
class ArrangementSection:
    """A detected arrangement section."""

    type: SectionType
    start_time: float
    end_time: float
    confidence: float

    # Energy characteristics
    energy_mean: float = 0.0
    energy_peak: float = 0.0
    energy_profile: np.ndarray = field(default_factory=lambda: np.array([]))

    # Density metrics
    note_density: float = 0.0
    harmonic_density: float = 0.0

    # Transitions
    has_buildup: bool = False
    has_drop: bool = False

    # Per-section practice-guidance classification (chord/riff/lead).
    # Populated by the guidance_mode classifier (see
    # ``analysis.guidance_mode``). Defaults keep legacy ArrangementSection
    # construction (e.g. SectionDetector._classify_sections, older bundle
    # round-trips) producing the same behaviour as before — silent
    # fallback to the chord ribbon.
    guidance_mode: str = "chord"
    guidance_confidence: float = 0.0
    guidance_reason: str = ""

    # Engine-as-source-of-truth for the JAM riff/lead lane (see
    # riff-first plan §"renderRiffLane / renderLeadPhraseLane").
    # ``dominant_stem`` names the stem whose notes the riff/lead
    # lane should render from (chosen by the guidance_mode classifier
    # as argmax over ``voiced_frame_ratio × duration_s``). Empty
    # string when no stems contributed (all silent or empty input).
    # ``landmark_notes`` is a pre-computed, density-capped sequence
    # of dicts ``{pitch, start, end, velocity}`` for that stem
    # inside the section window — selected upstream via
    # ``analysis.section_features.select_landmark_notes`` so the UI
    # doesn't need to re-rank notes or worry about smear.
    dominant_stem: str = ""
    landmark_notes: tuple = field(default_factory=tuple)

    @property
    def duration(self) -> float:
        """Duration of the section."""
        return self.end_time - self.start_time

    def to_dict(self) -> dict:
        """Convert to dictionary for API."""
        return {
            "type": self.type.value,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration": self.duration,
            "confidence": self.confidence,
            "energy_mean": float(self.energy_mean),
            "energy_peak": float(self.energy_peak),
            "note_density": self.note_density,
            "harmonic_density": self.harmonic_density,
            "has_buildup": self.has_buildup,
            "has_drop": self.has_drop,
            "guidance_mode": self.guidance_mode,
            "guidance_confidence": float(self.guidance_confidence),
            "guidance_reason": self.guidance_reason,
            "dominant_stem": self.dominant_stem,
            # ``landmark_notes`` is already a tuple of JSON-clean dicts
            # (see ``select_landmark_notes``); list() so JSON encoders
            # that special-case tuples don't trip.
            "landmark_notes": [dict(n) for n in self.landmark_notes],
        }


@dataclass
class SectionTransition:
    """A transition between sections."""

    from_section: int  # Index of previous section
    to_section: int  # Index of next section
    time: float  # Transition time
    type: str  # "cut", "fade", "buildup", "breakdown"
    energy_change: float  # Relative energy change (-1 to 1)


@dataclass
class ArrangementAnalysis:
    """Complete arrangement analysis result."""

    sections: List[ArrangementSection]
    transitions: List[SectionTransition]
    duration: float

    # Global metrics
    tempo_bpm: float = 120.0
    time_signature: Tuple[int, int] = (4, 4)
    key: Optional[str] = None

    # Energy curve
    energy_curve: np.ndarray = field(default_factory=lambda: np.array([]))
    energy_curve_sr: float = 10.0  # Samples per second

    def to_dict(self) -> dict:
        """Convert to dictionary for API."""
        return {
            "sections": [s.to_dict() for s in self.sections],
            "transitions": [
                {
                    "from_section": t.from_section,
                    "to_section": t.to_section,
                    "time": t.time,
                    "type": t.type,
                    "energy_change": t.energy_change,
                }
                for t in self.transitions
            ],
            "duration": self.duration,
            "tempo_bpm": self.tempo_bpm,
            "time_signature": list(self.time_signature),
            "key": self.key,
            "energy_curve": self.energy_curve.tolist()
            if len(self.energy_curve) > 0
            else [],
            "energy_curve_sr": self.energy_curve_sr,
        }


class SectionDetector:
    """Detect arrangement sections in audio.

    Uses energy analysis, structural repetition, and phrase detection
    to identify verse/chorus/drop/breakdown sections.
    """

    def __init__(
        self,
        hop_length: int = 512,
        sr: int = 22050,
        min_section_duration: float = 4.0,
        max_section_duration: float = 64.0,
        energy_resolution: float = 0.1,  # Energy curve resolution in seconds
    ):
        """Initialize the detector.

        Args:
            hop_length: Hop length for analysis
            sr: Sample rate
            min_section_duration: Minimum section duration in seconds
            max_section_duration: Maximum section duration in seconds
            energy_resolution: Resolution of energy curve in seconds
        """
        self.hop_length = hop_length
        self.sr = sr
        self.min_section_duration = min_section_duration
        self.max_section_duration = max_section_duration
        self.energy_resolution = energy_resolution

    def detect_sections(
        self,
        audio: np.ndarray,
        sr: int = None,
        tempo: float = None,
    ) -> ArrangementAnalysis:
        """Detect arrangement sections.

        Args:
            audio: Audio samples (mono)
            sr: Sample rate (uses default if None)
            tempo: Tempo in BPM (detected if None)

        Returns:
            ArrangementAnalysis with detected sections
        """
        import librosa

        sr = sr or self.sr
        duration = len(audio) / sr

        # Estimate tempo if not provided
        if tempo is None:
            tempo, _ = librosa.beat.beat_track(y=audio, sr=sr)
            if hasattr(tempo, "__iter__"):
                tempo = float(tempo[0]) if len(tempo) > 0 else 120.0
            tempo = float(tempo) if tempo > 0 else 120.0

        # Compute energy curve
        energy_curve = self._compute_energy_curve(audio, sr)

        # Detect section boundaries from energy
        boundaries = self._detect_boundaries(energy_curve, duration, tempo)

        # Classify each section
        sections = self._classify_sections(audio, sr, boundaries, energy_curve)

        # Detect transitions
        transitions = self._detect_transitions(sections, energy_curve)

        # Refine intro/outro
        sections = self._refine_intro_outro(sections, energy_curve)

        return ArrangementAnalysis(
            sections=sections,
            transitions=transitions,
            duration=duration,
            tempo_bpm=tempo,
            energy_curve=energy_curve,
            energy_curve_sr=1.0 / self.energy_resolution,
        )

    def _compute_energy_curve(self, audio: np.ndarray, sr: int) -> np.ndarray:
        """Compute smoothed energy curve."""
        import librosa

        # Frame-based RMS energy
        frame_length = int(sr * self.energy_resolution)
        hop_length = frame_length // 2

        rms = librosa.feature.rms(y=audio, frame_length=frame_length, hop_length=hop_length)[0]

        # Smooth with moving average
        window_size = max(1, int(0.5 / self.energy_resolution))  # 0.5s smoothing
        if len(rms) > window_size:
            rms = np.convolve(rms, np.ones(window_size) / window_size, mode="same")

        # Normalize
        rms_max = rms.max()
        if rms_max > 0:
            rms = rms / rms_max

        return rms

    def _detect_boundaries(
        self,
        energy_curve: np.ndarray,
        duration: float,
        tempo: float,
    ) -> List[float]:
        """Detect section boundaries from energy curve.

        Uses novelty detection on energy to find structural changes.
        """
        # Calculate novelty function (derivative of energy)
        novelty = np.abs(np.diff(energy_curve))

        # Smooth novelty
        window = max(1, int(1.0 / self.energy_resolution))  # 1s window
        if len(novelty) > window:
            novelty = np.convolve(novelty, np.ones(window) / window, mode="same")

        # Find peaks in novelty (potential boundaries)
        threshold = np.mean(novelty) + np.std(novelty)
        peaks = []

        # Minimum spacing based on min section duration
        min_spacing = int(self.min_section_duration / self.energy_resolution)

        for i in range(1, len(novelty) - 1):
            if novelty[i] > threshold:
                if novelty[i] > novelty[i - 1] and novelty[i] > novelty[i + 1]:
                    # Check minimum spacing
                    if not peaks or (i - peaks[-1]) >= min_spacing:
                        peaks.append(i)

        # Convert to times (hop_length = frame_length//2, so time_per_sample = energy_resolution/2)
        times_per_sample = self.energy_resolution / 2
        boundaries = [0.0]  # Always start at 0
        boundaries.extend([p * times_per_sample for p in peaks])
        boundaries.append(duration)  # Always end at duration

        # Quantize to bar boundaries if we have tempo
        beats_per_bar = 4
        bar_duration = (60.0 / tempo) * beats_per_bar

        quantized = [0.0]
        for b in boundaries[1:-1]:
            # Round to nearest bar
            bar_num = round(b / bar_duration)
            quantized_time = bar_num * bar_duration
            # Ensure we don't exceed duration
            if quantized_time >= duration:
                continue
            if quantized_time > quantized[-1] + self.min_section_duration:
                quantized.append(quantized_time)
        quantized.append(duration)

        return quantized

    def _classify_sections(
        self,
        audio: np.ndarray,
        sr: int,
        boundaries: List[float],
        energy_curve: np.ndarray,
    ) -> List[ArrangementSection]:
        """Classify each section based on characteristics."""
        import librosa

        sections = []
        num_sections = len(boundaries) - 1

        # Compute global stats for normalization
        global_energy_mean = np.mean(energy_curve)

        for i in range(num_sections):
            start_time = boundaries[i]
            end_time = boundaries[i + 1]

            # Get energy slice for this section (clamp to valid range)
            # Energy curve uses hop_length = frame_length // 2, so adjust time conversion
            time_per_sample = self.energy_resolution / 2  # Account for hop
            start_idx = int(start_time / time_per_sample)
            end_idx = int(end_time / time_per_sample)
            # Clamp indices to valid range
            start_idx = max(0, min(start_idx, len(energy_curve) - 1))
            end_idx = max(start_idx + 1, min(end_idx, len(energy_curve)))
            section_energy = energy_curve[start_idx:end_idx] if end_idx > start_idx else np.array([0.5])

            # Compute section features
            energy_mean = float(np.mean(section_energy))
            energy_peak = float(np.max(section_energy))
            energy_std = float(np.std(section_energy))

            # Extract audio segment for analysis
            start_sample = int(start_time * sr)
            end_sample = int(end_time * sr)
            segment = audio[start_sample:end_sample]

            # Compute spectral features for classification
            if len(segment) > 0:
                spectral_centroid = np.mean(
                    librosa.feature.spectral_centroid(y=segment, sr=sr)
                )
                onset_env = librosa.onset.onset_strength(y=segment, sr=sr)
                note_density = len(librosa.onset.onset_detect(onset_envelope=onset_env, sr=sr)) / max(
                    0.1, end_time - start_time
                )
            else:
                spectral_centroid = 0
                note_density = 0

            # Classify based on position and features
            section_type = self._classify_section_type(
                index=i,
                num_sections=num_sections,
                energy_mean=energy_mean,
                energy_peak=energy_peak,
                energy_std=energy_std,
                global_energy_mean=global_energy_mean,
                spectral_centroid=spectral_centroid,
                note_density=note_density,
                duration=end_time - start_time,
            )

            # Check for buildup (rising energy at end)
            has_buildup = False
            if len(section_energy) > 10:
                last_quarter = section_energy[int(len(section_energy) * 0.75) :]
                first_quarter = section_energy[: int(len(section_energy) * 0.25)]
                if np.mean(last_quarter) > np.mean(first_quarter) * 1.3:
                    has_buildup = True

            # Check for drop (sudden energy increase at start)
            has_drop = False
            if i > 0:
                prev_start = int(boundaries[i - 1] / time_per_sample)
                prev_end = int(boundaries[i] / time_per_sample)
                # Clamp indices to valid range
                prev_start = max(0, min(prev_start, len(energy_curve) - 1))
                prev_end = max(prev_start + 1, min(prev_end, len(energy_curve)))
                prev_energy = energy_curve[prev_start:prev_end]
                if len(prev_energy) > 0 and len(section_energy) > 0:
                    if section_energy[0] > np.mean(prev_energy[-10:]) * 1.5:
                        has_drop = True

            # Confidence based on how well it matches the classification
            confidence = self._compute_confidence(
                section_type, energy_mean, global_energy_mean, note_density
            )

            sections.append(
                ArrangementSection(
                    type=section_type,
                    start_time=start_time,
                    end_time=end_time,
                    confidence=confidence,
                    energy_mean=energy_mean,
                    energy_peak=energy_peak,
                    energy_profile=section_energy,
                    note_density=note_density,
                    has_buildup=has_buildup,
                    has_drop=has_drop,
                )
            )

        return sections

    def _classify_section_type(
        self,
        index: int,
        num_sections: int,
        energy_mean: float,
        energy_peak: float,
        energy_std: float,
        global_energy_mean: float,
        spectral_centroid: float,
        note_density: float,
        duration: float,
    ) -> SectionType:
        """Classify a section based on its features."""
        # Position-based hints
        is_first = index == 0
        is_last = index == num_sections - 1
        is_early = index < num_sections * 0.2
        is_late = index > num_sections * 0.8

        # Energy-based classification
        energy_ratio = energy_mean / max(0.01, global_energy_mean)
        is_high_energy = energy_ratio > 1.2
        is_low_energy = energy_ratio < 0.6
        is_medium_energy = 0.6 <= energy_ratio <= 1.2

        # Intro/Outro detection (position + energy)
        if is_first and duration < 16 and is_low_energy:
            return SectionType.INTRO

        if is_last and duration < 16 and is_low_energy:
            return SectionType.OUTRO

        # Drop detection (high energy peak with sudden increase)
        if is_high_energy and energy_peak > 0.9 and energy_std < 0.15:
            return SectionType.DROP

        # Breakdown detection (low energy, sparse)
        if is_low_energy and note_density < 2.0:
            return SectionType.BREAKDOWN

        # Buildup detection (rising energy pattern in medium energy section)
        if is_medium_energy and energy_std > 0.2:
            return SectionType.BUILDUP

        # Chorus detection (high energy, high density)
        if is_high_energy and note_density > 3.0:
            return SectionType.CHORUS

        # Verse detection (medium energy, moderate density)
        if is_medium_energy and note_density >= 2.0:
            return SectionType.VERSE

        # Bridge detection (medium energy, different from surrounding)
        if is_medium_energy and not is_early and not is_late:
            return SectionType.BRIDGE

        # Default
        return SectionType.UNKNOWN

    def _compute_confidence(
        self,
        section_type: SectionType,
        energy_mean: float,
        global_energy_mean: float,
        note_density: float,
    ) -> float:
        """Compute confidence score for section classification."""
        energy_ratio = energy_mean / max(0.01, global_energy_mean)

        # Base confidence
        confidence = 0.5

        # Adjust based on how well features match expected patterns
        if section_type == SectionType.CHORUS:
            if energy_ratio > 1.2:
                confidence += 0.2
            if note_density > 3.0:
                confidence += 0.15

        elif section_type == SectionType.VERSE:
            if 0.6 <= energy_ratio <= 1.2:
                confidence += 0.2
            if 1.5 <= note_density <= 4.0:
                confidence += 0.15

        elif section_type == SectionType.BREAKDOWN:
            if energy_ratio < 0.5:
                confidence += 0.25
            if note_density < 1.5:
                confidence += 0.15

        elif section_type == SectionType.DROP:
            if energy_ratio > 1.3:
                confidence += 0.25

        elif section_type in (SectionType.INTRO, SectionType.OUTRO):
            if energy_ratio < 0.7:
                confidence += 0.2

        return min(1.0, confidence)

    def _detect_transitions(
        self,
        sections: List[ArrangementSection],
        energy_curve: np.ndarray,
    ) -> List[SectionTransition]:
        """Detect transition types between sections."""
        transitions = []

        for i in range(len(sections) - 1):
            current = sections[i]
            next_section = sections[i + 1]

            # Get energy at boundary (time_per_sample = energy_resolution / 2)
            time_per_sample = self.energy_resolution / 2
            boundary_idx = int(current.end_time / time_per_sample)
            boundary_idx = max(0, min(boundary_idx, len(energy_curve) - 1))
            before_energy = energy_curve[max(0, boundary_idx - 5) : boundary_idx]
            after_energy = energy_curve[boundary_idx : min(len(energy_curve), boundary_idx + 5)]

            energy_before = float(np.mean(before_energy)) if len(before_energy) > 0 else 0
            energy_after = float(np.mean(after_energy)) if len(after_energy) > 0 else 0

            energy_change = (energy_after - energy_before) / max(0.01, energy_before)

            # Classify transition
            if current.has_buildup and next_section.has_drop:
                trans_type = "buildup_drop"
            elif abs(energy_change) < 0.2:
                trans_type = "smooth"
            elif energy_change > 0.5:
                trans_type = "buildup"
            elif energy_change < -0.5:
                trans_type = "breakdown"
            else:
                trans_type = "cut"

            transitions.append(
                SectionTransition(
                    from_section=i,
                    to_section=i + 1,
                    time=current.end_time,
                    type=trans_type,
                    energy_change=energy_change,
                )
            )

        return transitions

    def _refine_intro_outro(
        self,
        sections: List[ArrangementSection],
        energy_curve: np.ndarray,
    ) -> List[ArrangementSection]:
        """Refine intro/outro detection based on energy patterns."""
        if not sections:
            return sections

        # Check if first section should be intro
        if len(sections) > 1:
            first = sections[0]
            second = sections[1]

            if (
                first.type not in (SectionType.INTRO, SectionType.CHORUS, SectionType.DROP)
                and first.energy_mean < second.energy_mean * 0.7
                and first.duration < 20
            ):
                sections[0] = ArrangementSection(
                    type=SectionType.INTRO,
                    start_time=first.start_time,
                    end_time=first.end_time,
                    confidence=first.confidence,
                    energy_mean=first.energy_mean,
                    energy_peak=first.energy_peak,
                    energy_profile=first.energy_profile,
                    note_density=first.note_density,
                )

        # Check if last section should be outro
        if len(sections) > 1:
            last = sections[-1]
            second_last = sections[-2]

            if (
                last.type not in (SectionType.OUTRO, SectionType.CHORUS, SectionType.DROP)
                and last.energy_mean < second_last.energy_mean * 0.7
                and last.duration < 20
            ):
                sections[-1] = ArrangementSection(
                    type=SectionType.OUTRO,
                    start_time=last.start_time,
                    end_time=last.end_time,
                    confidence=last.confidence,
                    energy_mean=last.energy_mean,
                    energy_peak=last.energy_peak,
                    energy_profile=last.energy_profile,
                    note_density=last.note_density,
                )

        return sections


def detect_sections(
    audio: np.ndarray,
    sr: int = 22050,
    tempo: float = None,
) -> ArrangementAnalysis:
    """Convenience function to detect sections.

    Args:
        audio: Audio samples (mono)
        sr: Sample rate
        tempo: Tempo in BPM (detected if None)

    Returns:
        ArrangementAnalysis with detected sections
    """
    detector = SectionDetector(sr=sr)
    return detector.detect_sections(audio, sr, tempo)
