"""Detector arbitration for ensemble pitch extraction.

Provides intelligent strategies for combining results from
multiple pitch detectors with conflict resolution.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple, Any

import numpy as np

from .ensemble_extractor import DetectedNote, DetectorType, EnsembleNote

logger = logging.getLogger(__name__)


class ArbitrationStrategy(str, Enum):
    """Arbitration strategy for combining detector results."""
    POLYPHONIC_WEIGHTED = "polyphonic_weighted"  # Trust basic-pitch more
    MONOPHONIC_PRIORITY = "monophonic_priority"  # Trust CREPE/pYIN more
    BASS_FOCUSED = "bass_focused"  # Aggressive octave correction
    UNANIMOUS_ONLY = "unanimous_only"  # Only notes all detectors agree on
    CONFIDENCE_WEIGHTED = "confidence_weighted"  # Pure confidence weighting
    UNION_MERGE = "union_merge"  # Take all notes, merge overlapping - maximizes recall


@dataclass
class NoteCluster:
    """Cluster of notes from different detectors at same time."""
    notes: List[DetectedNote]
    center_time: float
    time_spread: float
    pitch_candidates: Dict[int, float]  # pitch -> combined confidence
    detector_votes: Dict[int, List[DetectorType]]  # pitch -> detectors

    def get_best_pitch(self) -> Tuple[int, float]:
        """Get pitch with highest combined confidence."""
        if not self.pitch_candidates:
            return 60, 0.0
        best_pitch = max(self.pitch_candidates, key=self.pitch_candidates.get)
        return best_pitch, self.pitch_candidates[best_pitch]


class DetectorArbitrator:
    """Arbitrates between multiple pitch detector results.

    Uses clustering and voting to resolve conflicts:
    1. Cluster notes by time overlap
    2. For each cluster, collect pitch votes from detectors
    3. Apply strategy-specific weighting
    4. Resolve octave ambiguity using spectral evidence
    5. Output consensus notes with provenance
    """

    # Detector weights by strategy
    WEIGHTS = {
        ArbitrationStrategy.POLYPHONIC_WEIGHTED: {
            DetectorType.BASIC_PITCH: 1.0,
            DetectorType.CREPE: 0.6,
            DetectorType.PYIN: 0.6,
            DetectorType.SPECTRAL: 0.4,
        },
        ArbitrationStrategy.MONOPHONIC_PRIORITY: {
            DetectorType.BASIC_PITCH: 0.6,
            DetectorType.CREPE: 1.0,
            DetectorType.PYIN: 0.9,
            DetectorType.SPECTRAL: 0.5,
        },
        ArbitrationStrategy.BASS_FOCUSED: {
            DetectorType.BASIC_PITCH: 0.5,
            DetectorType.CREPE: 1.0,
            DetectorType.PYIN: 0.9,
            DetectorType.SPECTRAL: 0.8,  # Spectral important for octave
        },
        ArbitrationStrategy.UNANIMOUS_ONLY: {
            DetectorType.BASIC_PITCH: 1.0,
            DetectorType.CREPE: 1.0,
            DetectorType.PYIN: 1.0,
            DetectorType.SPECTRAL: 1.0,
        },
        ArbitrationStrategy.CONFIDENCE_WEIGHTED: {
            DetectorType.BASIC_PITCH: 1.0,
            DetectorType.CREPE: 1.0,
            DetectorType.PYIN: 1.0,
            DetectorType.SPECTRAL: 1.0,
        },
        ArbitrationStrategy.UNION_MERGE: {
            DetectorType.BASIC_PITCH: 1.0,
            DetectorType.CREPE: 1.0,
            DetectorType.PYIN: 1.0,
            DetectorType.SPECTRAL: 0.8,
        },
    }

    def __init__(
        self,
        strategy: ArbitrationStrategy = ArbitrationStrategy.CONFIDENCE_WEIGHTED,
        time_tolerance: float = 0.05,  # 50ms
        octave_correction: bool = True,
        min_agreement: float = 0.3,  # Minimum agreement score
    ):
        """Initialize arbitrator.

        Args:
            strategy: Arbitration strategy to use
            time_tolerance: Time window for clustering notes
            octave_correction: Whether to apply octave correction
            min_agreement: Minimum agreement threshold
        """
        self.strategy = strategy
        self.time_tolerance = time_tolerance
        self.octave_correction = octave_correction
        self.min_agreement = min_agreement

        self.weights = self.WEIGHTS.get(
            strategy,
            self.WEIGHTS[ArbitrationStrategy.CONFIDENCE_WEIGHTED]
        )

    def arbitrate(
        self,
        detector_results: Dict[DetectorType, List[DetectedNote]],
    ) -> Tuple[List[EnsembleNote], Dict[str, Any]]:
        """Arbitrate between detector results.

        Args:
            detector_results: Notes from each detector

        Returns:
            Tuple of (ensemble notes, arbitration statistics)
        """
        stats = {
            "strategy": self.strategy.value,
            "input_notes": {d.value: len(n) for d, n in detector_results.items()},
            "clusters_formed": 0,
            "octave_corrections": 0,
            "unanimous_notes": 0,
            "conflict_resolutions": 0,
        }

        if not detector_results:
            return [], stats

        # For UNION_MERGE strategy, use different logic that maximizes recall
        if self.strategy == ArbitrationStrategy.UNION_MERGE:
            return self._arbitrate_union_merge(detector_results, stats)

        # Flatten all notes with their detector type
        all_notes = []
        for detector_type, notes in detector_results.items():
            all_notes.extend(notes)

        if not all_notes:
            return [], stats

        # Cluster by time
        clusters = self._cluster_notes(all_notes)
        stats["clusters_formed"] = len(clusters)

        # Resolve each cluster
        ensemble_notes = []
        for cluster in clusters:
            result = self._resolve_cluster(cluster, detector_results)
            if result is not None:
                ensemble_notes.append(result)

                if result.agreement_score >= 0.9:
                    stats["unanimous_notes"] += 1
                if result.octave_correction_applied:
                    stats["octave_corrections"] += 1
                if len(cluster.pitch_candidates) > 1:
                    stats["conflict_resolutions"] += 1

        stats["output_notes"] = len(ensemble_notes)

        # Sort by time
        ensemble_notes.sort(key=lambda n: n.start)

        return ensemble_notes, stats

    def _arbitrate_union_merge(
        self,
        detector_results: Dict[DetectorType, List[DetectedNote]],
        stats: Dict[str, Any],
    ) -> Tuple[List[EnsembleNote], Dict[str, Any]]:
        """Union merge strategy - take all notes and merge duplicates.

        This maximizes recall by including all detections, only merging
        notes with same pitch that overlap in time.
        """
        # Collect all notes with detector info
        all_notes = []
        for detector_type, notes in detector_results.items():
            for note in notes:
                weight = self.weights.get(detector_type, 1.0)
                all_notes.append((note, detector_type, weight))

        if not all_notes:
            return [], stats

        # Sort by start time, then by pitch
        all_notes.sort(key=lambda x: (x[0].start, x[0].pitch))

        # Merge overlapping notes with same/similar pitch
        merged_notes = []
        used = set()

        for i, (note, detector, weight) in enumerate(all_notes):
            if i in used:
                continue

            # Find all overlapping notes with same pitch (allowing octave match)
            group = [(i, note, detector, weight)]
            used.add(i)

            for j, (other_note, other_detector, other_weight) in enumerate(all_notes[i+1:], start=i+1):
                if j in used:
                    continue

                # Check time overlap
                overlap_start = max(note.start, other_note.start)
                overlap_end = min(note.end, other_note.end)

                if overlap_start < overlap_end + self.time_tolerance:
                    # Check pitch match (exact or octave)
                    pitch_diff = abs(note.pitch - other_note.pitch)
                    if pitch_diff == 0 or pitch_diff == 12:
                        group.append((j, other_note, other_detector, other_weight))
                        used.add(j)

            # Create merged note
            merged = self._merge_note_group(group)
            if merged is not None:
                merged_notes.append(merged)

        stats["clusters_formed"] = len(merged_notes)
        stats["output_notes"] = len(merged_notes)

        # Sort by time
        merged_notes.sort(key=lambda n: n.start)

        return merged_notes, stats

    def _merge_note_group(
        self,
        group: List[Tuple[int, "DetectedNote", DetectorType, float]],
    ) -> Optional[EnsembleNote]:
        """Merge a group of overlapping same-pitch notes."""
        if not group:
            return None

        # Extract note data
        notes = [g[1] for g in group]
        detectors = [g[2] for g in group]
        weights = [g[3] for g in group]

        # Choose pitch - prefer lower octave for bass content
        pitches = [n.pitch for n in notes]
        # Group by pitch class (mod 12)
        pitch_class = pitches[0] % 12
        same_class_pitches = [p for p in pitches if p % 12 == pitch_class]

        # For bass (pitch < 60), prefer lower octave
        if min(pitches) < 60:
            best_pitch = min(same_class_pitches)
        else:
            # Take most common pitch
            from collections import Counter
            pitch_counts = Counter(same_class_pitches)
            best_pitch = pitch_counts.most_common(1)[0][0]

        # Check if octave correction was applied
        octave_correction = best_pitch != pitches[0]
        original_pitch = pitches[0] if octave_correction else None

        # Timing: use earliest start, latest end
        start = min(n.start for n in notes)
        end = max(n.end for n in notes)

        # Velocity: weighted average
        total_weight = sum(weights)
        velocity = int(sum(n.velocity * w for n, w in zip(notes, weights)) / total_weight)

        # Confidence: weighted average
        confidence = sum(n.confidence * w for n, w in zip(notes, weights)) / total_weight

        # Agreement score based on how many detectors found it
        unique_detectors = set(detectors)
        agreement = len(unique_detectors) / 3.0  # Assume 3 detectors max

        # Detector contributions
        contributions = {}
        for detector, weight in zip(detectors, weights):
            contributions[detector.value] = weight

        return EnsembleNote(
            pitch=best_pitch,
            start=float(start),
            end=float(end),
            velocity=velocity,
            confidence=float(np.clip(confidence, 0, 1)),
            detector_contributions=contributions,
            agreement_score=float(agreement),
            octave_correction_applied=octave_correction,
            original_pitch=original_pitch,
        )

    def _cluster_notes(
        self,
        notes: List[DetectedNote],
    ) -> List[NoteCluster]:
        """Cluster notes by time overlap."""
        if not notes:
            return []

        # Sort by start time
        sorted_notes = sorted(notes, key=lambda n: n.start)

        clusters = []
        current_cluster_notes = [sorted_notes[0]]
        cluster_end = sorted_notes[0].end

        for note in sorted_notes[1:]:
            # Check if note overlaps with current cluster
            if note.start <= cluster_end + self.time_tolerance:
                current_cluster_notes.append(note)
                cluster_end = max(cluster_end, note.end)
            else:
                # Save current cluster and start new one
                clusters.append(self._create_cluster(current_cluster_notes))
                current_cluster_notes = [note]
                cluster_end = note.end

        # Don't forget last cluster
        if current_cluster_notes:
            clusters.append(self._create_cluster(current_cluster_notes))

        return clusters

    def _create_cluster(
        self,
        notes: List[DetectedNote],
    ) -> NoteCluster:
        """Create a NoteCluster from overlapping notes."""
        starts = [n.start for n in notes]
        ends = [n.end for n in notes]

        center_time = np.mean(starts)
        time_spread = max(ends) - min(starts)

        # Collect pitch votes
        pitch_candidates: Dict[int, float] = {}
        detector_votes: Dict[int, List[DetectorType]] = {}

        for note in notes:
            pitch = note.pitch
            weight = self.weights.get(note.detector, 1.0)
            confidence = note.confidence * weight

            if pitch not in pitch_candidates:
                pitch_candidates[pitch] = 0.0
                detector_votes[pitch] = []

            pitch_candidates[pitch] += confidence
            detector_votes[pitch].append(note.detector)

        return NoteCluster(
            notes=notes,
            center_time=center_time,
            time_spread=time_spread,
            pitch_candidates=pitch_candidates,
            detector_votes=detector_votes,
        )

    def _resolve_cluster(
        self,
        cluster: NoteCluster,
        detector_results: Dict[DetectorType, List[DetectedNote]],
    ) -> Optional[EnsembleNote]:
        """Resolve a cluster to a single ensemble note."""
        if not cluster.pitch_candidates:
            return None

        # Get best pitch
        best_pitch, best_confidence = cluster.get_best_pitch()

        # Check for octave ambiguity
        octave_correction_applied = False
        original_pitch = None

        if self.octave_correction:
            corrected_pitch = self._check_octave_ambiguity(
                cluster, best_pitch, detector_results
            )
            if corrected_pitch != best_pitch:
                original_pitch = best_pitch
                best_pitch = corrected_pitch
                octave_correction_applied = True

        # Calculate agreement score
        total_detectors = len(detector_results)
        voting_detectors = len(cluster.detector_votes.get(best_pitch, []))
        agreement_score = voting_detectors / max(1, total_detectors)

        # Apply strategy-specific filtering
        if self.strategy == ArbitrationStrategy.UNANIMOUS_ONLY:
            if agreement_score < 0.9:
                return None

        if agreement_score < self.min_agreement:
            return None

        # Build detector contributions
        contributions = {}
        for detector_type in cluster.detector_votes.get(best_pitch, []):
            contributions[detector_type.value] = self.weights.get(detector_type, 1.0)

        # Calculate timing from cluster
        starts = [n.start for n in cluster.notes]
        ends = [n.end for n in cluster.notes]
        velocities = [n.velocity for n in cluster.notes]

        return EnsembleNote(
            pitch=best_pitch,
            start=float(np.min(starts)),
            end=float(np.max(ends)),
            velocity=int(np.mean(velocities)),
            confidence=float(np.clip(best_confidence / total_detectors, 0, 1)),
            detector_contributions=contributions,
            agreement_score=float(agreement_score),
            octave_correction_applied=octave_correction_applied,
            original_pitch=original_pitch,
        )

    def _check_octave_ambiguity(
        self,
        cluster: NoteCluster,
        proposed_pitch: int,
        detector_results: Dict[DetectorType, List[DetectedNote]],
    ) -> int:
        """Check and potentially correct octave errors.

        Octave errors are common because:
        - Bass notes often have strong harmonics
        - High frequencies can mask fundamentals
        - Different detectors have different octave biases

        Resolution strategy:
        1. Check if octave up/down has votes
        2. For bass content, prefer lower octave
        3. Use spectral detector as tiebreaker
        4. Consider harmonic ratio from spectral detector
        """
        candidates = cluster.pitch_candidates

        # Check for octave variants
        octave_up = proposed_pitch + 12
        octave_down = proposed_pitch - 12

        octave_up_score = candidates.get(octave_up, 0)
        octave_down_score = candidates.get(octave_down, 0)
        proposed_score = candidates.get(proposed_pitch, 0)

        # For bass-focused strategy, bias toward lower octave
        if self.strategy == ArbitrationStrategy.BASS_FOCUSED:
            # Give bonus to lower octave for bass content
            if proposed_pitch > 48:  # Above E2
                if octave_down_score > 0:
                    octave_down_score *= 1.5

        # Check spectral detector evidence
        spectral_notes = [
            n for n in cluster.notes
            if n.detector == DetectorType.SPECTRAL
        ]

        if spectral_notes:
            # Spectral detector is good at finding true fundamental
            spectral_pitch = spectral_notes[0].pitch
            harmonic_ratio = getattr(spectral_notes[0], 'harmonic_ratio', 1.0)

            # If spectral agrees with octave down, likely correct
            if spectral_pitch == octave_down and harmonic_ratio > 0.5:
                return octave_down

            # If spectral agrees with proposed, keep it
            if spectral_pitch == proposed_pitch and harmonic_ratio > 0.5:
                return proposed_pitch

        # Default resolution: highest score wins
        scores = {
            proposed_pitch: proposed_score,
            octave_up: octave_up_score,
            octave_down: octave_down_score,
        }

        # Filter to valid pitches (21-108 = A0-C8)
        valid_scores = {
            p: s for p, s in scores.items()
            if 21 <= p <= 108 and s > 0
        }

        if not valid_scores:
            return proposed_pitch

        return max(valid_scores, key=valid_scores.get)


class AdaptiveArbitrator(DetectorArbitrator):
    """Arbitrator that adapts strategy based on content analysis."""

    def __init__(self, **kwargs):
        # Start with confidence weighting, adapt later
        super().__init__(
            strategy=ArbitrationStrategy.CONFIDENCE_WEIGHTED,
            **kwargs
        )

    def arbitrate_adaptive(
        self,
        detector_results: Dict[DetectorType, List[DetectedNote]],
        polyphony_estimate: float = 1.0,
        is_bass: bool = False,
    ) -> Tuple[List[EnsembleNote], Dict[str, Any]]:
        """Arbitrate with adaptive strategy selection.

        Args:
            detector_results: Notes from each detector
            polyphony_estimate: Estimated polyphony level
            is_bass: Whether content is bass-focused

        Returns:
            Tuple of (ensemble notes, arbitration statistics)
        """
        # Select strategy based on content
        if is_bass:
            self.strategy = ArbitrationStrategy.BASS_FOCUSED
            self.weights = self.WEIGHTS[ArbitrationStrategy.BASS_FOCUSED]
        elif polyphony_estimate <= 1.5:
            self.strategy = ArbitrationStrategy.MONOPHONIC_PRIORITY
            self.weights = self.WEIGHTS[ArbitrationStrategy.MONOPHONIC_PRIORITY]
        elif polyphony_estimate >= 4:
            self.strategy = ArbitrationStrategy.POLYPHONIC_WEIGHTED
            self.weights = self.WEIGHTS[ArbitrationStrategy.POLYPHONIC_WEIGHTED]
        else:
            self.strategy = ArbitrationStrategy.CONFIDENCE_WEIGHTED
            self.weights = self.WEIGHTS[ArbitrationStrategy.CONFIDENCE_WEIGHTED]

        return self.arbitrate(detector_results)


def arbitrate_detectors(
    detector_results: Dict[DetectorType, List[DetectedNote]],
    strategy: ArbitrationStrategy = ArbitrationStrategy.CONFIDENCE_WEIGHTED,
    **kwargs,
) -> Tuple[List[EnsembleNote], Dict[str, Any]]:
    """Convenience function for detector arbitration.

    Args:
        detector_results: Notes from each detector
        strategy: Arbitration strategy
        **kwargs: Additional arbitrator options

    Returns:
        Tuple of (ensemble notes, statistics)
    """
    arbitrator = DetectorArbitrator(strategy=strategy, **kwargs)
    return arbitrator.arbitrate(detector_results)
