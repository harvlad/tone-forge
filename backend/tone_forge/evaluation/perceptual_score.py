"""Perceptual reconstruction scoring.

Measures musical correctness rather than just note accuracy:
- Melodic contour preservation (pitch direction)
- Rhythmic structure similarity (timing patterns)
- Harmonic movement (chord progression similarity)
- Phrase consistency (musical phrase integrity)

These metrics better correlate with producer perception of
reconstruction quality than pure F1 scores.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class PerceptualScore:
    """Perceptual similarity scores for reconstruction."""

    # Core metrics (0-1, higher is better)
    melodic_contour: float = 0.0  # Shape preservation
    rhythmic_structure: float = 0.0  # Timing pattern similarity
    harmonic_movement: float = 0.0  # Chord progression similarity
    phrase_consistency: float = 0.0  # Musical phrase integrity

    # Combined score
    overall_perceptual: float = 0.0

    # Detailed breakdowns
    contour_details: Dict[str, float] = field(default_factory=dict)
    rhythm_details: Dict[str, float] = field(default_factory=dict)
    harmonic_details: Dict[str, float] = field(default_factory=dict)
    phrase_details: Dict[str, float] = field(default_factory=dict)

    # Quality assessment
    quality_level: str = "unknown"  # excellent, good, acceptable, poor
    usability_estimate: str = "unknown"  # drop-in, minor-edits, major-cleanup

    def to_dict(self) -> dict:
        return {
            "melodic_contour": self.melodic_contour,
            "rhythmic_structure": self.rhythmic_structure,
            "harmonic_movement": self.harmonic_movement,
            "phrase_consistency": self.phrase_consistency,
            "overall_perceptual": self.overall_perceptual,
            "quality_level": self.quality_level,
            "usability_estimate": self.usability_estimate,
            "details": {
                "contour": self.contour_details,
                "rhythm": self.rhythm_details,
                "harmonic": self.harmonic_details,
                "phrase": self.phrase_details,
            },
        }


@dataclass
class NoteEvent:
    """Simplified note for comparison."""
    pitch: int
    start: float
    end: float
    velocity: int = 80

    @property
    def duration(self) -> float:
        return self.end - self.start


class PerceptualScorer:
    """Scores reconstruction quality using perceptual metrics.

    Unlike F1 scoring which counts exact note matches, perceptual
    scoring measures whether the musical content "sounds right":

    1. Melodic Contour: Does the melody go up/down in the same places?
    2. Rhythmic Structure: Are notes placed at similar beat positions?
    3. Harmonic Movement: Do the chord changes happen correctly?
    4. Phrase Consistency: Are musical phrases properly captured?
    """

    def __init__(
        self,
        contour_weight: float = 0.3,
        rhythm_weight: float = 0.3,
        harmonic_weight: float = 0.2,
        phrase_weight: float = 0.2,
    ):
        """Initialize scorer.

        Args:
            contour_weight: Weight for melodic contour score
            rhythm_weight: Weight for rhythmic structure score
            harmonic_weight: Weight for harmonic movement score
            phrase_weight: Weight for phrase consistency score
        """
        self.weights = {
            "contour": contour_weight,
            "rhythm": rhythm_weight,
            "harmonic": harmonic_weight,
            "phrase": phrase_weight,
        }

        # Normalize weights
        total = sum(self.weights.values())
        self.weights = {k: v / total for k, v in self.weights.items()}

    def score(
        self,
        reference_notes: List[NoteEvent],
        extracted_notes: List[NoteEvent],
        tempo: float = 120.0,
        time_signature: Tuple[int, int] = (4, 4),
    ) -> PerceptualScore:
        """Score extracted notes against reference.

        Args:
            reference_notes: Ground truth notes
            extracted_notes: Extracted/reconstructed notes
            tempo: Tempo in BPM
            time_signature: Time signature as (numerator, denominator)

        Returns:
            PerceptualScore with all metrics
        """
        if len(reference_notes) == 0 or len(extracted_notes) == 0:
            return PerceptualScore(
                quality_level="poor",
                usability_estimate="major-cleanup",
            )

        # Sort notes by time
        ref_sorted = sorted(reference_notes, key=lambda n: n.start)
        ext_sorted = sorted(extracted_notes, key=lambda n: n.start)

        # Calculate individual scores
        contour_score, contour_details = self._score_melodic_contour(
            ref_sorted, ext_sorted
        )

        rhythm_score, rhythm_details = self._score_rhythmic_structure(
            ref_sorted, ext_sorted, tempo, time_signature
        )

        harmonic_score, harmonic_details = self._score_harmonic_movement(
            ref_sorted, ext_sorted
        )

        phrase_score, phrase_details = self._score_phrase_consistency(
            ref_sorted, ext_sorted, tempo
        )

        # Calculate weighted overall score
        overall = (
            contour_score * self.weights["contour"] +
            rhythm_score * self.weights["rhythm"] +
            harmonic_score * self.weights["harmonic"] +
            phrase_score * self.weights["phrase"]
        )

        # Determine quality level
        quality_level = self._assess_quality_level(overall)
        usability = self._estimate_usability(overall, rhythm_score, contour_score)

        return PerceptualScore(
            melodic_contour=float(contour_score),
            rhythmic_structure=float(rhythm_score),
            harmonic_movement=float(harmonic_score),
            phrase_consistency=float(phrase_score),
            overall_perceptual=float(overall),
            contour_details=contour_details,
            rhythm_details=rhythm_details,
            harmonic_details=harmonic_details,
            phrase_details=phrase_details,
            quality_level=quality_level,
            usability_estimate=usability,
        )

    def _score_melodic_contour(
        self,
        ref: List[NoteEvent],
        ext: List[NoteEvent],
    ) -> Tuple[float, Dict[str, float]]:
        """Score melodic contour preservation.

        Contour is the sequence of pitch directions (up/down/same).
        We compare whether the extracted melody moves in the same
        directions as the reference.
        """
        from .melodic_contour import (
            extract_contour,
            compare_contours,
            ContourType,
        )

        # Extract pitch sequences
        ref_pitches = [n.pitch for n in ref]
        ext_pitches = [n.pitch for n in ext]

        # Extract contours
        ref_contour = extract_contour(ref_pitches)
        ext_contour = extract_contour(ext_pitches)

        # Compare contours
        similarity = compare_contours(ref_contour, ext_contour)

        # Calculate detailed metrics
        details = {
            "direction_agreement": similarity,
            "interval_correlation": self._interval_correlation(ref_pitches, ext_pitches),
            "range_ratio": self._pitch_range_ratio(ref_pitches, ext_pitches),
        }

        # Combine into score
        score = (
            similarity * 0.5 +
            details["interval_correlation"] * 0.3 +
            details["range_ratio"] * 0.2
        )

        return float(np.clip(score, 0, 1)), details

    def _score_rhythmic_structure(
        self,
        ref: List[NoteEvent],
        ext: List[NoteEvent],
        tempo: float,
        time_signature: Tuple[int, int],
    ) -> Tuple[float, Dict[str, float]]:
        """Score rhythmic structure similarity.

        Compares timing patterns:
        - Inter-onset interval (IOI) histogram similarity
        - Beat alignment
        - Duration patterns
        """
        # Calculate IOIs
        ref_onsets = np.array([n.start for n in ref])
        ext_onsets = np.array([n.start for n in ext])

        ref_ioi = np.diff(ref_onsets) if len(ref_onsets) > 1 else np.array([0])
        ext_ioi = np.diff(ext_onsets) if len(ext_onsets) > 1 else np.array([0])

        # IOI histogram similarity
        ioi_similarity = self._histogram_similarity(ref_ioi, ext_ioi)

        # Beat alignment
        beat_duration = 60.0 / tempo
        ref_beat_phases = (ref_onsets / beat_duration) % 1.0
        ext_beat_phases = (ext_onsets / beat_duration) % 1.0
        beat_alignment = self._phase_similarity(ref_beat_phases, ext_beat_phases)

        # Duration pattern similarity
        ref_durations = np.array([n.duration for n in ref])
        ext_durations = np.array([n.duration for n in ext])
        duration_similarity = self._histogram_similarity(ref_durations, ext_durations)

        details = {
            "ioi_similarity": float(ioi_similarity),
            "beat_alignment": float(beat_alignment),
            "duration_similarity": float(duration_similarity),
            "onset_count_ratio": min(len(ext), len(ref)) / max(len(ext), len(ref), 1),
        }

        score = (
            ioi_similarity * 0.4 +
            beat_alignment * 0.35 +
            duration_similarity * 0.25
        )

        return float(np.clip(score, 0, 1)), details

    def _score_harmonic_movement(
        self,
        ref: List[NoteEvent],
        ext: List[NoteEvent],
    ) -> Tuple[float, Dict[str, float]]:
        """Score harmonic movement similarity.

        Compares chord progression patterns using:
        - Chroma sequence similarity
        - Harmonic rhythm (chord change timing)
        """
        # Bin notes into time windows
        window_size = 0.5  # 500ms windows
        max_time = max(
            max(n.end for n in ref) if ref else 0,
            max(n.end for n in ext) if ext else 0,
        )

        num_windows = int(np.ceil(max_time / window_size)) + 1

        # Build chroma for each window
        ref_chroma = self._notes_to_chroma_sequence(ref, num_windows, window_size)
        ext_chroma = self._notes_to_chroma_sequence(ext, num_windows, window_size)

        # Compare chroma sequences
        chroma_similarity = self._chroma_sequence_similarity(ref_chroma, ext_chroma)

        # Harmonic rhythm (when do harmonies change)
        ref_changes = self._detect_harmonic_changes(ref_chroma)
        ext_changes = self._detect_harmonic_changes(ext_chroma)
        rhythm_similarity = self._change_point_similarity(ref_changes, ext_changes)

        details = {
            "chroma_similarity": float(chroma_similarity),
            "harmonic_rhythm": float(rhythm_similarity),
        }

        score = chroma_similarity * 0.6 + rhythm_similarity * 0.4

        return float(np.clip(score, 0, 1)), details

    def _score_phrase_consistency(
        self,
        ref: List[NoteEvent],
        ext: List[NoteEvent],
        tempo: float,
    ) -> Tuple[float, Dict[str, float]]:
        """Score phrase structure consistency.

        Checks whether musical phrases are properly captured:
        - Phrase boundary alignment
        - Phrase length similarity
        - Phrase density patterns
        """
        # Detect phrase boundaries (gaps > 1 beat)
        beat_duration = 60.0 / tempo
        phrase_gap = beat_duration * 1.5

        ref_phrases = self._segment_into_phrases(ref, phrase_gap)
        ext_phrases = self._segment_into_phrases(ext, phrase_gap)

        # Phrase count similarity
        phrase_count_ratio = min(len(ext_phrases), len(ref_phrases)) / max(
            len(ext_phrases), len(ref_phrases), 1
        )

        # Phrase length similarity
        ref_lengths = [len(p) for p in ref_phrases]
        ext_lengths = [len(p) for p in ext_phrases]
        length_similarity = self._histogram_similarity(
            np.array(ref_lengths), np.array(ext_lengths)
        ) if ref_lengths and ext_lengths else 0

        # Phrase boundary alignment
        ref_boundaries = [p[0].start for p in ref_phrases if p]
        ext_boundaries = [p[0].start for p in ext_phrases if p]
        boundary_alignment = self._onset_alignment(
            np.array(ref_boundaries), np.array(ext_boundaries), tolerance=beat_duration
        ) if ref_boundaries and ext_boundaries else 0

        details = {
            "phrase_count_ratio": float(phrase_count_ratio),
            "length_similarity": float(length_similarity),
            "boundary_alignment": float(boundary_alignment),
            "ref_phrase_count": len(ref_phrases),
            "ext_phrase_count": len(ext_phrases),
        }

        score = (
            phrase_count_ratio * 0.3 +
            length_similarity * 0.3 +
            boundary_alignment * 0.4
        )

        return float(np.clip(score, 0, 1)), details

    def _interval_correlation(
        self,
        ref_pitches: List[int],
        ext_pitches: List[int],
    ) -> float:
        """Calculate correlation of pitch intervals."""
        if len(ref_pitches) < 2 or len(ext_pitches) < 2:
            return 0.5

        ref_intervals = np.diff(ref_pitches)
        ext_intervals = np.diff(ext_pitches)

        # Resample to same length
        if len(ref_intervals) != len(ext_intervals):
            target_len = min(len(ref_intervals), len(ext_intervals))
            ref_intervals = np.interp(
                np.linspace(0, 1, target_len),
                np.linspace(0, 1, len(ref_intervals)),
                ref_intervals,
            )
            ext_intervals = np.interp(
                np.linspace(0, 1, target_len),
                np.linspace(0, 1, len(ext_intervals)),
                ext_intervals,
            )

        if len(ref_intervals) == 0:
            return 0.5

        # Correlation
        correlation = np.corrcoef(ref_intervals, ext_intervals)[0, 1]
        if np.isnan(correlation):
            return 0.5

        # Convert from [-1, 1] to [0, 1]
        return (correlation + 1) / 2

    def _pitch_range_ratio(
        self,
        ref_pitches: List[int],
        ext_pitches: List[int],
    ) -> float:
        """Calculate pitch range ratio."""
        if not ref_pitches or not ext_pitches:
            return 0.0

        ref_range = max(ref_pitches) - min(ref_pitches)
        ext_range = max(ext_pitches) - min(ext_pitches)

        if ref_range == 0 and ext_range == 0:
            return 1.0
        if ref_range == 0 or ext_range == 0:
            return 0.5

        return min(ref_range, ext_range) / max(ref_range, ext_range)

    def _histogram_similarity(
        self,
        a: np.ndarray,
        b: np.ndarray,
        bins: int = 20,
    ) -> float:
        """Calculate histogram similarity using intersection."""
        if len(a) == 0 or len(b) == 0:
            return 0.0

        # Use same bins for both
        min_val = min(np.min(a), np.min(b))
        max_val = max(np.max(a), np.max(b))

        if max_val == min_val:
            return 1.0

        hist_a, _ = np.histogram(a, bins=bins, range=(min_val, max_val), density=True)
        hist_b, _ = np.histogram(b, bins=bins, range=(min_val, max_val), density=True)

        # Intersection
        intersection = np.minimum(hist_a, hist_b).sum()
        union = np.maximum(hist_a, hist_b).sum()

        if union == 0:
            return 1.0

        return intersection / union

    def _phase_similarity(
        self,
        ref_phases: np.ndarray,
        ext_phases: np.ndarray,
    ) -> float:
        """Calculate phase similarity using circular histogram."""
        if len(ref_phases) == 0 or len(ext_phases) == 0:
            return 0.0

        bins = 16
        hist_ref, _ = np.histogram(ref_phases, bins=bins, range=(0, 1))
        hist_ext, _ = np.histogram(ext_phases, bins=bins, range=(0, 1))

        # Normalize
        hist_ref = hist_ref / (hist_ref.sum() + 1e-8)
        hist_ext = hist_ext / (hist_ext.sum() + 1e-8)

        # Intersection
        return np.minimum(hist_ref, hist_ext).sum()

    def _notes_to_chroma_sequence(
        self,
        notes: List[NoteEvent],
        num_windows: int,
        window_size: float,
    ) -> np.ndarray:
        """Convert notes to chroma sequence."""
        chroma = np.zeros((num_windows, 12))

        for note in notes:
            start_window = int(note.start / window_size)
            end_window = int(note.end / window_size) + 1

            pitch_class = note.pitch % 12

            for w in range(start_window, min(end_window, num_windows)):
                chroma[w, pitch_class] += 1

        # Normalize each window
        row_sums = chroma.sum(axis=1, keepdims=True)
        chroma = np.divide(chroma, row_sums, where=row_sums != 0)

        return chroma

    def _chroma_sequence_similarity(
        self,
        ref_chroma: np.ndarray,
        ext_chroma: np.ndarray,
    ) -> float:
        """Calculate chroma sequence similarity using cosine."""
        similarities = []

        for i in range(min(len(ref_chroma), len(ext_chroma))):
            ref_vec = ref_chroma[i]
            ext_vec = ext_chroma[i]

            # Cosine similarity
            norm_ref = np.linalg.norm(ref_vec)
            norm_ext = np.linalg.norm(ext_vec)

            if norm_ref > 0 and norm_ext > 0:
                sim = np.dot(ref_vec, ext_vec) / (norm_ref * norm_ext)
                similarities.append(sim)

        if not similarities:
            return 0.0

        return float(np.mean(similarities))

    def _detect_harmonic_changes(
        self,
        chroma: np.ndarray,
        threshold: float = 0.3,
    ) -> List[int]:
        """Detect harmonic change points."""
        changes = []

        for i in range(1, len(chroma)):
            diff = np.linalg.norm(chroma[i] - chroma[i-1])
            if diff > threshold:
                changes.append(i)

        return changes

    def _change_point_similarity(
        self,
        ref_changes: List[int],
        ext_changes: List[int],
        tolerance: int = 2,
    ) -> float:
        """Calculate similarity of change point locations."""
        if not ref_changes and not ext_changes:
            return 1.0
        if not ref_changes or not ext_changes:
            return 0.0

        matches = 0
        for rc in ref_changes:
            for ec in ext_changes:
                if abs(rc - ec) <= tolerance:
                    matches += 1
                    break

        precision = matches / len(ext_changes)
        recall = matches / len(ref_changes)

        if precision + recall == 0:
            return 0.0

        return 2 * precision * recall / (precision + recall)

    def _segment_into_phrases(
        self,
        notes: List[NoteEvent],
        gap_threshold: float,
    ) -> List[List[NoteEvent]]:
        """Segment notes into phrases based on gaps."""
        if not notes:
            return []

        phrases = []
        current_phrase = [notes[0]]

        for i in range(1, len(notes)):
            gap = notes[i].start - notes[i-1].end
            if gap > gap_threshold:
                phrases.append(current_phrase)
                current_phrase = [notes[i]]
            else:
                current_phrase.append(notes[i])

        if current_phrase:
            phrases.append(current_phrase)

        return phrases

    def _onset_alignment(
        self,
        ref_onsets: np.ndarray,
        ext_onsets: np.ndarray,
        tolerance: float,
    ) -> float:
        """Calculate onset alignment score."""
        if len(ref_onsets) == 0 or len(ext_onsets) == 0:
            return 0.0

        matches = 0
        for ro in ref_onsets:
            for eo in ext_onsets:
                if abs(ro - eo) <= tolerance:
                    matches += 1
                    break

        return matches / len(ref_onsets)

    def _assess_quality_level(self, overall: float) -> str:
        """Assess overall quality level."""
        if overall >= 0.85:
            return "excellent"
        elif overall >= 0.7:
            return "good"
        elif overall >= 0.5:
            return "acceptable"
        else:
            return "poor"

    def _estimate_usability(
        self,
        overall: float,
        rhythm: float,
        contour: float,
    ) -> str:
        """Estimate usability for production."""
        # Rhythm is most important for usability
        if overall >= 0.8 and rhythm >= 0.7:
            return "drop-in"
        elif overall >= 0.6 and rhythm >= 0.5:
            return "minor-edits"
        else:
            return "major-cleanup"


def score_reconstruction(
    reference_notes: List[Tuple[int, float, float, int]],
    extracted_notes: List[Tuple[int, float, float, int]],
    tempo: float = 120.0,
) -> PerceptualScore:
    """Convenience function to score reconstruction.

    Args:
        reference_notes: Reference as (pitch, start, end, velocity) tuples
        extracted_notes: Extracted as (pitch, start, end, velocity) tuples
        tempo: Tempo in BPM

    Returns:
        PerceptualScore
    """
    ref = [NoteEvent(pitch=n[0], start=n[1], end=n[2], velocity=n[3]) for n in reference_notes]
    ext = [NoteEvent(pitch=n[0], start=n[1], end=n[2], velocity=n[3]) for n in extracted_notes]

    scorer = PerceptualScorer()
    return scorer.score(ref, ext, tempo=tempo)
