"""Base classes for precision recovery filters.

Precision filters operate AFTER note extraction to remove false positives
while preserving real musical content. They implement "safe suppression"
rules that consider musical context before removing notes.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np

from tone_forge.midi.passes.base import ExtractedNote


class SuppressionReason(Enum):
    """Reasons why a note might be suppressed."""
    OCTAVE_HALLUCINATION = auto()
    HARMONIC_DUPLICATE = auto()
    SUBHARMONIC_ARTIFACT = auto()
    TRANSIENT_SPIKE = auto()
    RESONANCE_ARTIFACT = auto()
    SUSTAIN_OVERLAP = auto()
    MODULATION_ARTIFACT = auto()
    LOW_SPECTRAL_SUPPORT = auto()
    PATTERN_INCONSISTENT = auto()


class ProtectionReason(Enum):
    """Reasons why a note should be protected from suppression."""
    RHYTHMIC_ALIGNMENT = auto()
    REPEATED_PATTERN = auto()
    KEY_CONFORMITY = auto()
    BEAT_GRID_ALIGNED = auto()
    PHRASE_CONTEXT = auto()
    HIGH_CONFIDENCE = auto()
    MELODIC_CONTINUITY = auto()
    STRONG_SPECTRAL_SUPPORT = auto()


@dataclass
class NoteScore:
    """Scoring for a note's validity."""
    note: ExtractedNote
    suppression_score: float = 0.0  # 0-1, higher = more likely false positive
    protection_score: float = 0.0   # 0-1, higher = more likely real note
    suppression_reasons: List[SuppressionReason] = field(default_factory=list)
    protection_reasons: List[ProtectionReason] = field(default_factory=list)
    spectral_support: float = 0.0   # 0-1, spectral energy at fundamental
    harmonic_support: float = 0.0   # 0-1, overtone structure match

    @property
    def net_score(self) -> float:
        """Net score: positive = keep, negative = suppress."""
        return self.protection_score - self.suppression_score

    @property
    def should_suppress(self) -> bool:
        """Whether this note should be suppressed based on scores."""
        # Require strong evidence to suppress
        return self.suppression_score > 0.7 and self.protection_score < 0.5


@dataclass
class FilterContext:
    """Context for precision filter processing."""
    audio: np.ndarray
    sr: int
    tempo: Optional[float] = None
    key: Optional[Tuple[int, str]] = None  # (root, mode)
    time_signature: Tuple[int, int] = (4, 4)
    stem_type: Optional[str] = None

    # Pre-computed spectral data (lazily populated)
    _stft: Optional[np.ndarray] = None
    _frequencies: Optional[np.ndarray] = None
    _times: Optional[np.ndarray] = None

    def get_stft(self) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Get or compute STFT. Returns (stft, frequencies, times)."""
        if self._stft is None:
            import librosa
            self._stft = librosa.stft(self.audio, n_fft=4096, hop_length=512)
            self._frequencies = librosa.fft_frequencies(sr=self.sr, n_fft=4096)
            self._times = librosa.times_like(self._stft, sr=self.sr, hop_length=512)
        return self._stft, self._frequencies, self._times

    def get_spectral_energy_at_freq(
        self,
        freq: float,
        time_start: float,
        time_end: float,
        bandwidth_hz: float = 50.0,
    ) -> float:
        """Get average spectral energy around a frequency in a time range."""
        stft, freqs, times = self.get_stft()

        # Find frequency bins
        freq_mask = (freqs >= freq - bandwidth_hz) & (freqs <= freq + bandwidth_hz)
        if not np.any(freq_mask):
            return 0.0

        # Find time bins
        time_mask = (times >= time_start) & (times <= time_end)
        if not np.any(time_mask):
            return 0.0

        # Get magnitude in region
        magnitude = np.abs(stft[freq_mask][:, time_mask])
        return float(np.mean(magnitude))


@dataclass
class FilterResult:
    """Result from a precision filter."""
    kept_notes: List[ExtractedNote]
    suppressed_notes: List[ExtractedNote]
    note_scores: List[NoteScore]
    filter_name: str
    stats: Dict[str, Any] = field(default_factory=dict)

    @property
    def suppression_rate(self) -> float:
        """Fraction of notes suppressed."""
        total = len(self.kept_notes) + len(self.suppressed_notes)
        return len(self.suppressed_notes) / total if total > 0 else 0.0


class PrecisionFilter(ABC):
    """Base class for precision recovery filters.

    Precision filters implement "safe suppression" - they remove likely
    false positives while protecting notes that have musical support.

    Key principles:
    1. DO NOT suppress notes with rhythmic alignment
    2. DO NOT suppress notes that form repeated patterns
    3. DO NOT suppress notes that fit the detected key
    4. DO NOT suppress notes aligned to beat grid
    5. DO require strong evidence before suppression
    """

    def __init__(
        self,
        min_suppression_confidence: float = 0.7,
        protection_weight: float = 1.5,
    ):
        """Initialize filter.

        Args:
            min_suppression_confidence: Minimum confidence to suppress a note
            protection_weight: How much to weight protection vs suppression
        """
        self.min_suppression_confidence = min_suppression_confidence
        self.protection_weight = protection_weight

    @property
    @abstractmethod
    def name(self) -> str:
        """Filter name for logging."""
        pass

    @abstractmethod
    def score_notes(
        self,
        notes: List[ExtractedNote],
        context: FilterContext,
    ) -> List[NoteScore]:
        """Score each note for suppression/protection.

        Args:
            notes: Notes to score
            context: Filter context with audio and metadata

        Returns:
            List of NoteScore objects
        """
        pass

    def filter(
        self,
        notes: List[ExtractedNote],
        context: FilterContext,
    ) -> FilterResult:
        """Apply filter to notes.

        Args:
            notes: Notes to filter
            context: Filter context

        Returns:
            FilterResult with kept/suppressed notes
        """
        if not notes:
            return FilterResult(
                kept_notes=[],
                suppressed_notes=[],
                note_scores=[],
                filter_name=self.name,
            )

        # Score all notes
        scores = self.score_notes(notes, context)

        # Apply safe suppression rules
        kept = []
        suppressed = []

        for score in scores:
            # Apply protection weight
            adjusted_protection = score.protection_score * self.protection_weight

            # Suppress only if confident and not protected
            if (score.suppression_score >= self.min_suppression_confidence and
                adjusted_protection < score.suppression_score):
                suppressed.append(score.note)
            else:
                kept.append(score.note)

        return FilterResult(
            kept_notes=kept,
            suppressed_notes=suppressed,
            note_scores=scores,
            filter_name=self.name,
            stats={
                "total_notes": len(notes),
                "kept": len(kept),
                "suppressed": len(suppressed),
                "suppression_rate": len(suppressed) / len(notes) if notes else 0,
            },
        )

    def _compute_rhythmic_alignment(
        self,
        note: ExtractedNote,
        tempo: float,
        time_signature: Tuple[int, int] = (4, 4),
    ) -> float:
        """Compute how well a note aligns with the beat grid.

        Returns 0-1 score where 1 = perfect alignment.
        """
        if tempo <= 0:
            return 0.5  # Neutral if no tempo

        beat_duration = 60.0 / tempo
        numerator, denominator = time_signature

        # Check alignment to various grid divisions
        divisions = [
            1.0,      # Whole note
            0.5,      # Half note
            0.25,     # Quarter note
            0.125,    # 8th note
            0.0625,   # 16th note
        ]

        best_alignment = 0.0
        for div in divisions:
            grid_interval = beat_duration * div * 4 / denominator
            if grid_interval > 0:
                offset = note.start % grid_interval
                # Normalize to 0-0.5 range (distance from nearest grid point)
                normalized_offset = min(offset, grid_interval - offset) / grid_interval
                alignment = 1.0 - (normalized_offset * 2)
                # Weight by grid importance (downbeats more important)
                weight = 1.0 / (divisions.index(div) + 1)
                best_alignment = max(best_alignment, alignment * weight)

        return best_alignment

    def _compute_key_conformity(
        self,
        note: ExtractedNote,
        key: Optional[Tuple[int, str]],
    ) -> float:
        """Compute how well a note fits the detected key.

        Returns 0-1 score where 1 = in key, 0 = chromatic.
        """
        if key is None:
            return 0.5  # Neutral if no key

        root, mode = key
        pitch_class = note.pitch % 12
        relative_pitch = (pitch_class - root) % 12

        # Scale patterns
        scales = {
            'major': [0, 2, 4, 5, 7, 9, 11],
            'minor': [0, 2, 3, 5, 7, 8, 10],
            'dorian': [0, 2, 3, 5, 7, 9, 10],
            'mixolydian': [0, 2, 4, 5, 7, 9, 10],
            'pentatonic_major': [0, 2, 4, 7, 9],
            'pentatonic_minor': [0, 3, 5, 7, 10],
        }

        scale = scales.get(mode, scales['major'])

        if relative_pitch in scale:
            return 1.0
        else:
            # Chromatic passing tones get partial credit
            return 0.3

    def _find_repeated_patterns(
        self,
        notes: List[ExtractedNote],
        note: ExtractedNote,
        tempo: float,
    ) -> float:
        """Check if note is part of a repeated pattern.

        Returns 0-1 score where 1 = strong pattern support.
        """
        if tempo <= 0 or len(notes) < 4:
            return 0.0

        beat_duration = 60.0 / tempo

        # Find notes with same pitch
        same_pitch = [n for n in notes if n.pitch == note.pitch and n != note]
        if not same_pitch:
            return 0.0

        # Check for regular intervals
        intervals = []
        for other in same_pitch:
            interval = abs(note.start - other.start)
            if interval > 0.01:  # Ignore very close notes
                intervals.append(interval)

        if not intervals:
            return 0.0

        # Check if intervals are consistent with beat divisions
        pattern_score = 0.0
        for interval in intervals:
            # Check against common beat divisions
            for div in [0.25, 0.5, 1.0, 2.0, 4.0]:
                expected = beat_duration * div
                tolerance = expected * 0.1
                if abs(interval - expected) < tolerance:
                    pattern_score = max(pattern_score, 1.0 - abs(interval - expected) / expected)

        return pattern_score
