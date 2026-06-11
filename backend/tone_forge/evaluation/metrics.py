"""Evaluation metrics for ToneForge systems.

Provides quantitative metrics for measuring the quality of:
- Descriptor predictions vs ground truth
- MIDI extraction accuracy
- Retrieval relevance
- Ranking quality

These metrics enable principled model iteration and prevent
subjective evaluation chaos.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any
import math

import numpy as np


@dataclass
class DescriptorAccuracy:
    """Accuracy metrics for descriptor predictions.

    Measures how well predicted descriptors match ground truth labels.
    """
    # Classification metrics (amp family, cab, effects)
    amp_family_accuracy: float = 0.0
    amp_family_top3_accuracy: float = 0.0  # In top 3 predictions
    cab_accuracy: float = 0.0
    effects_precision: float = 0.0  # Detected effects that are correct
    effects_recall: float = 0.0     # Correct effects that were detected
    effects_f1: float = 0.0

    # Regression metrics (gain, voicing)
    gain_mae: float = 0.0           # Mean absolute error
    gain_mse: float = 0.0           # Mean squared error
    gain_within_10pct: float = 0.0  # Percentage within 10% of true value

    voicing_bass_mae: float = 0.0
    voicing_mid_mae: float = 0.0
    voicing_treble_mae: float = 0.0
    voicing_presence_mae: float = 0.0

    # Confidence calibration
    confidence_calibration_error: float = 0.0  # Expected vs actual accuracy
    confidence_brier_score: float = 0.0

    # Aggregate
    overall_score: float = 0.0

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "amp_family_accuracy": self.amp_family_accuracy,
            "amp_family_top3_accuracy": self.amp_family_top3_accuracy,
            "cab_accuracy": self.cab_accuracy,
            "effects_precision": self.effects_precision,
            "effects_recall": self.effects_recall,
            "effects_f1": self.effects_f1,
            "gain_mae": self.gain_mae,
            "gain_mse": self.gain_mse,
            "gain_within_10pct": self.gain_within_10pct,
            "voicing_bass_mae": self.voicing_bass_mae,
            "voicing_mid_mae": self.voicing_mid_mae,
            "voicing_treble_mae": self.voicing_treble_mae,
            "voicing_presence_mae": self.voicing_presence_mae,
            "confidence_calibration_error": self.confidence_calibration_error,
            "confidence_brier_score": self.confidence_brier_score,
            "overall_score": self.overall_score,
        }


@dataclass
class MIDIQualityMetrics:
    """Quality metrics for MIDI extraction.

    Measures how well extracted MIDI matches ground truth or
    expected musical qualities.
    """
    # Note-level metrics
    note_precision: float = 0.0  # Extracted notes that are correct
    note_recall: float = 0.0     # Correct notes that were extracted
    note_f1: float = 0.0

    # Note counts
    true_positives: int = 0      # Correctly extracted notes
    false_positives: int = 0     # Spurious notes
    false_negatives: int = 0     # Missed notes

    # Pitch accuracy
    pitch_accuracy: float = 0.0        # Notes with correct pitch
    pitch_mean_error_cents: float = 0.0  # Average pitch error in cents

    # Timing accuracy
    onset_mean_error_ms: float = 0.0   # Average onset timing error
    offset_mean_error_ms: float = 0.0  # Average offset timing error
    onset_within_50ms: float = 0.0     # Percentage within 50ms

    # Velocity accuracy
    velocity_mae: float = 0.0          # Mean absolute error (0-127)
    velocity_correlation: float = 0.0  # Correlation with true velocities

    # Structural metrics
    spurious_note_ratio: float = 0.0   # Ghost notes / total extracted
    missed_note_ratio: float = 0.0     # Missed notes / total true

    # Musicality metrics
    key_detection_accuracy: float = 0.0
    tempo_error_bpm: float = 0.0
    quantization_alignment: float = 0.0  # How well notes align to grid

    # Aggregate
    overall_score: float = 0.0

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "note_precision": self.note_precision,
            "note_recall": self.note_recall,
            "note_f1": self.note_f1,
            "true_positives": self.true_positives,
            "false_positives": self.false_positives,
            "false_negatives": self.false_negatives,
            "pitch_accuracy": self.pitch_accuracy,
            "pitch_mean_error_cents": self.pitch_mean_error_cents,
            "onset_mean_error_ms": self.onset_mean_error_ms,
            "offset_mean_error_ms": self.offset_mean_error_ms,
            "onset_within_50ms": self.onset_within_50ms,
            "velocity_mae": self.velocity_mae,
            "velocity_correlation": self.velocity_correlation,
            "spurious_note_ratio": self.spurious_note_ratio,
            "missed_note_ratio": self.missed_note_ratio,
            "key_detection_accuracy": self.key_detection_accuracy,
            "tempo_error_bpm": self.tempo_error_bpm,
            "quantization_alignment": self.quantization_alignment,
            "overall_score": self.overall_score,
        }


@dataclass
class RetrievalMetrics:
    """Metrics for similarity search and retrieval.

    Measures how well retrieved results match expected relevance.
    """
    # Precision at K
    precision_at_1: float = 0.0
    precision_at_3: float = 0.0
    precision_at_5: float = 0.0
    precision_at_10: float = 0.0

    # Recall at K
    recall_at_5: float = 0.0
    recall_at_10: float = 0.0

    # Ranking metrics
    mean_reciprocal_rank: float = 0.0  # MRR
    ndcg_at_5: float = 0.0            # Normalized DCG
    ndcg_at_10: float = 0.0

    # Semantic similarity
    mean_similarity_score: float = 0.0
    min_similarity_in_top5: float = 0.0

    # Category coherence
    same_amp_family_ratio: float = 0.0  # Retrieved have same amp family
    same_gain_range_ratio: float = 0.0  # Retrieved within 0.2 gain

    # Aggregate
    overall_score: float = 0.0

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "precision_at_1": self.precision_at_1,
            "precision_at_3": self.precision_at_3,
            "precision_at_5": self.precision_at_5,
            "precision_at_10": self.precision_at_10,
            "recall_at_5": self.recall_at_5,
            "recall_at_10": self.recall_at_10,
            "mean_reciprocal_rank": self.mean_reciprocal_rank,
            "ndcg_at_5": self.ndcg_at_5,
            "ndcg_at_10": self.ndcg_at_10,
            "mean_similarity_score": self.mean_similarity_score,
            "min_similarity_in_top5": self.min_similarity_in_top5,
            "same_amp_family_ratio": self.same_amp_family_ratio,
            "same_gain_range_ratio": self.same_gain_range_ratio,
            "overall_score": self.overall_score,
        }


@dataclass
class RankingMetrics:
    """Metrics for recommendation/ranking quality.

    Measures how well ranked recommendations match user preferences
    or ground truth orderings.
    """
    # Ranking accuracy
    top1_accuracy: float = 0.0       # Best recommendation is correct
    top3_accuracy: float = 0.0       # Correct in top 3
    kendall_tau: float = 0.0         # Rank correlation
    spearman_rho: float = 0.0        # Rank correlation

    # User satisfaction proxies
    click_through_rate: float = 0.0  # First rec accepted
    scroll_depth: float = 0.0        # How far user scrolls
    edit_rate: float = 0.0           # How often user edits

    # Recommendation diversity
    diversity_score: float = 0.0     # How diverse are recommendations
    coverage: float = 0.0            # Fraction of catalog recommended

    # Aggregate
    overall_score: float = 0.0

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "top1_accuracy": self.top1_accuracy,
            "top3_accuracy": self.top3_accuracy,
            "kendall_tau": self.kendall_tau,
            "spearman_rho": self.spearman_rho,
            "click_through_rate": self.click_through_rate,
            "scroll_depth": self.scroll_depth,
            "edit_rate": self.edit_rate,
            "diversity_score": self.diversity_score,
            "coverage": self.coverage,
            "overall_score": self.overall_score,
        }


def compute_descriptor_accuracy(
    predictions: List[Dict],
    ground_truth: List[Dict],
) -> DescriptorAccuracy:
    """Compute descriptor accuracy metrics.

    Args:
        predictions: List of predicted descriptor dicts
        ground_truth: List of ground truth descriptor dicts

    Returns:
        DescriptorAccuracy with all metrics
    """
    if len(predictions) != len(ground_truth):
        raise ValueError("Predictions and ground truth must have same length")

    if len(predictions) == 0:
        return DescriptorAccuracy()

    # Amp family accuracy
    amp_correct = 0
    amp_top3_correct = 0
    for pred, true in zip(predictions, ground_truth):
        pred_amp = pred.get("amp", {}).get("family", "unknown")
        true_amp = true.get("amp", {}).get("family", "unknown")
        pred_alts = pred.get("amp", {}).get("alternates", [])

        if pred_amp == true_amp:
            amp_correct += 1
            amp_top3_correct += 1
        elif any(alt.get("family") == true_amp for alt in pred_alts[:2]):
            amp_top3_correct += 1

    amp_accuracy = amp_correct / len(predictions)
    amp_top3_accuracy = amp_top3_correct / len(predictions)

    # Gain error
    gain_errors = []
    gain_within_10 = 0
    for pred, true in zip(predictions, ground_truth):
        pred_gain = pred.get("amp", {}).get("gain", 0.5)
        true_gain = true.get("amp", {}).get("gain", 0.5)
        error = abs(pred_gain - true_gain)
        gain_errors.append(error)
        if error <= 0.1:
            gain_within_10 += 1

    gain_mae = np.mean(gain_errors) if gain_errors else 0.0
    gain_mse = np.mean([e**2 for e in gain_errors]) if gain_errors else 0.0
    gain_within_10pct = gain_within_10 / len(predictions)

    # Cab accuracy
    cab_correct = 0
    for pred, true in zip(predictions, ground_truth):
        pred_cab = pred.get("cab", {}).get("speaker_character", "unknown")
        true_cab = true.get("cab", {}).get("speaker_character", "unknown")
        if pred_cab == true_cab:
            cab_correct += 1
    cab_accuracy = cab_correct / len(predictions)

    # Effects precision/recall
    effect_types = ["delay", "reverb", "modulation", "compressor"]
    tp, fp, fn = 0, 0, 0
    for pred, true in zip(predictions, ground_truth):
        pred_effects = pred.get("effects", {})
        true_effects = true.get("effects", {})
        for etype in effect_types:
            pred_has = pred_effects.get(etype) is not None
            true_has = true_effects.get(etype) is not None
            if pred_has and true_has:
                tp += 1
            elif pred_has and not true_has:
                fp += 1
            elif not pred_has and true_has:
                fn += 1

    effects_precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    effects_recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    effects_f1 = (
        2 * effects_precision * effects_recall / (effects_precision + effects_recall)
        if (effects_precision + effects_recall) > 0 else 0.0
    )

    # Voicing MAE
    voicing_bass_errors = []
    voicing_mid_errors = []
    voicing_treble_errors = []
    voicing_presence_errors = []
    for pred, true in zip(predictions, ground_truth):
        pred_v = pred.get("amp", {}).get("voicing", {})
        true_v = true.get("amp", {}).get("voicing", {})
        if pred_v and true_v:
            voicing_bass_errors.append(abs(pred_v.get("bass", 0.5) - true_v.get("bass", 0.5)))
            voicing_mid_errors.append(abs(pred_v.get("mid", 0.5) - true_v.get("mid", 0.5)))
            voicing_treble_errors.append(abs(pred_v.get("treble", 0.5) - true_v.get("treble", 0.5)))
            voicing_presence_errors.append(abs(pred_v.get("presence", 0.5) - true_v.get("presence", 0.5)))

    # Confidence calibration
    conf_errors = []
    for pred, true in zip(predictions, ground_truth):
        pred_conf = pred.get("confidence", {})
        amp_conf = pred_conf.get("amp_family", 0.5)
        pred_amp = pred.get("amp", {}).get("family", "unknown")
        true_amp = true.get("amp", {}).get("family", "unknown")
        is_correct = 1.0 if pred_amp == true_amp else 0.0
        conf_errors.append(abs(amp_conf - is_correct))

    # Overall score (weighted average)
    overall = (
        0.3 * amp_accuracy +
        0.2 * (1.0 - min(gain_mae, 1.0)) +
        0.2 * cab_accuracy +
        0.2 * effects_f1 +
        0.1 * gain_within_10pct
    )

    return DescriptorAccuracy(
        amp_family_accuracy=amp_accuracy,
        amp_family_top3_accuracy=amp_top3_accuracy,
        cab_accuracy=cab_accuracy,
        effects_precision=effects_precision,
        effects_recall=effects_recall,
        effects_f1=effects_f1,
        gain_mae=gain_mae,
        gain_mse=gain_mse,
        gain_within_10pct=gain_within_10pct,
        voicing_bass_mae=np.mean(voicing_bass_errors) if voicing_bass_errors else 0.0,
        voicing_mid_mae=np.mean(voicing_mid_errors) if voicing_mid_errors else 0.0,
        voicing_treble_mae=np.mean(voicing_treble_errors) if voicing_treble_errors else 0.0,
        voicing_presence_mae=np.mean(voicing_presence_errors) if voicing_presence_errors else 0.0,
        confidence_calibration_error=np.mean(conf_errors) if conf_errors else 0.0,
        overall_score=overall,
    )


def _find_time_offset(
    extracted_notes: List[Tuple[int, float, float, int]],
    ground_truth_notes: List[Tuple[int, float, float, int]],
    step: float = 0.05,
) -> float:
    """Find the best time offset to align extracted notes with ground truth.

    Uses cross-correlation of note onset times to find optimal alignment.

    Args:
        extracted_notes: List of (pitch, onset_sec, offset_sec, velocity)
        ground_truth_notes: List of (pitch, onset_sec, offset_sec, velocity)
        step: Step size for fine search (in seconds)

    Returns:
        Best time offset in seconds (add to ground truth to align with extracted)
    """
    if not extracted_notes or not ground_truth_notes:
        return 0.0

    # Get first note times for initial estimate
    ext_first = min(n[1] for n in extracted_notes)
    gt_first = min(n[1] for n in ground_truth_notes)
    initial_offset = ext_first - gt_first

    # Coarse search around initial offset
    best_offset = initial_offset
    best_matches = 0

    # Search from initial_offset - 3s to initial_offset + 3s in 0.1s steps
    for offset in np.arange(initial_offset - 3.0, initial_offset + 3.0, 0.1):
        matches = 0
        matched_gt = set()
        for ext in extracted_notes:
            for i, gt in enumerate(ground_truth_notes):
                if i in matched_gt:
                    continue
                # Match if same pitch and timing within 0.15s
                if ext[0] == gt[0] and abs(ext[1] - (gt[1] + offset)) < 0.15:
                    matches += 1
                    matched_gt.add(i)
                    break
        if matches > best_matches:
            best_matches = matches
            best_offset = offset

    # Fine search around best coarse offset
    for offset in np.arange(best_offset - 0.2, best_offset + 0.2, step):
        matches = 0
        matched_gt = set()
        for ext in extracted_notes:
            for i, gt in enumerate(ground_truth_notes):
                if i in matched_gt:
                    continue
                if ext[0] == gt[0] and abs(ext[1] - (gt[1] + offset)) < 0.1:
                    matches += 1
                    matched_gt.add(i)
                    break
        if matches > best_matches:
            best_matches = matches
            best_offset = offset

    return best_offset


def _find_time_scale(
    extracted_notes: List[Tuple[int, float, float, int]],
    ground_truth_notes: List[Tuple[int, float, float, int]],
) -> float:
    """Find the best time scale factor to align extracted notes with ground truth.

    Handles tempo drift where extraction runs at slightly different tempo than ground truth.

    Args:
        extracted_notes: List of (pitch, onset_sec, offset_sec, velocity)
        ground_truth_notes: List of (pitch, onset_sec, offset_sec, velocity)

    Returns:
        Scale factor to apply to ground truth times (GT * scale = aligned)
    """
    if len(extracted_notes) < 10 or len(ground_truth_notes) < 10:
        return 1.0  # Not enough notes to estimate scale

    # Get time spans
    ext_times = sorted([n[1] for n in extracted_notes])
    gt_times = sorted([n[1] for n in ground_truth_notes])

    ext_first, ext_last = ext_times[0], ext_times[-1]
    gt_first, gt_last = gt_times[0], gt_times[-1]

    ext_duration = ext_last - ext_first
    gt_duration = gt_last - gt_first

    if gt_duration < 1.0 or ext_duration < 1.0:
        return 1.0  # Too short to estimate scale

    # Calculate scale factor
    scale = ext_duration / gt_duration

    # Only apply if within reasonable range (0.9 to 1.1 = ±10%)
    if 0.9 <= scale <= 1.1:
        return scale
    else:
        return 1.0  # Scale too extreme, likely wrong


def compute_midi_quality(
    extracted_notes: List[Tuple[int, float, float, int]],
    ground_truth_notes: List[Tuple[int, float, float, int]],
    onset_tolerance_ms: float = 300.0,  # Increased to handle tempo drift (up to 3% drift)
    pitch_tolerance_cents: float = 50.0,
    auto_align: bool = True,
    allow_octave_equivalence: bool = True,  # Allow octave-shifted matches
) -> MIDIQualityMetrics:
    """Compute MIDI extraction quality metrics.

    Args:
        extracted_notes: List of (pitch, onset_sec, offset_sec, velocity)
        ground_truth_notes: List of (pitch, onset_sec, offset_sec, velocity)
        onset_tolerance_ms: Tolerance for onset matching (200ms default handles tempo drift)
        pitch_tolerance_cents: Tolerance for pitch matching
        auto_align: Automatically detect and correct time offset

    Returns:
        MIDIQualityMetrics with all metrics
    """
    if len(ground_truth_notes) == 0:
        return MIDIQualityMetrics()

    # Auto-align if needed (handles both offset and tempo drift)
    if auto_align and len(extracted_notes) > 0:
        # First apply time scaling to correct tempo drift
        time_scale = _find_time_scale(extracted_notes, ground_truth_notes)
        if abs(time_scale - 1.0) > 0.005:  # Apply if scale differs by > 0.5%
            # Scale ground truth times around the first note
            gt_first = min(n[1] for n in ground_truth_notes)
            ground_truth_notes = [
                (p, gt_first + (o - gt_first) * time_scale,
                    gt_first + (off - gt_first) * time_scale, v)
                for p, o, off, v in ground_truth_notes
            ]

        # Then find and apply time offset
        time_offset = _find_time_offset(extracted_notes, ground_truth_notes)
        if abs(time_offset) > 0.05:  # Apply if offset > 50ms
            # Shift ground truth to align with extracted
            ground_truth_notes = [
                (p, o + time_offset, off + time_offset, v)
                for p, o, off, v in ground_truth_notes
            ]

    onset_tol_sec = onset_tolerance_ms / 1000.0

    # Match notes
    matched_extracted = set()
    matched_truth = set()
    onset_errors = []
    offset_errors = []
    velocity_errors = []
    pitch_errors_cents = []

    for i, ext in enumerate(extracted_notes):
        ext_pitch, ext_onset, ext_offset, ext_vel = ext
        best_match = None
        best_dist = float('inf')

        for j, truth in enumerate(ground_truth_notes):
            if j in matched_truth:
                continue
            truth_pitch, truth_onset, truth_offset, truth_vel = truth

            # Check pitch match (within tolerance, optionally allowing octave equivalence)
            pitch_diff_semitones = abs(ext_pitch - truth_pitch)
            if allow_octave_equivalence:
                # Reduce to pitch class difference (within octave)
                pitch_diff_semitones = pitch_diff_semitones % 12
                if pitch_diff_semitones > 6:  # Wrap around (e.g., 11 -> 1)
                    pitch_diff_semitones = 12 - pitch_diff_semitones
            pitch_diff_cents = pitch_diff_semitones * 100
            if pitch_diff_cents > pitch_tolerance_cents:
                continue

            # Check onset match
            onset_diff = abs(ext_onset - truth_onset)
            if onset_diff > onset_tol_sec:
                continue

            # Found a match candidate
            dist = onset_diff + pitch_diff_cents / 1000
            if dist < best_dist:
                best_dist = dist
                best_match = j

        if best_match is not None:
            matched_extracted.add(i)
            matched_truth.add(best_match)

            truth = ground_truth_notes[best_match]
            onset_errors.append(abs(ext_onset - truth[1]) * 1000)
            offset_errors.append(abs(ext_offset - truth[2]) * 1000)
            velocity_errors.append(abs(ext_vel - truth[3]))
            pitch_errors_cents.append(abs(ext_pitch - truth[0]) * 100)

    # Precision, recall, F1
    tp = len(matched_extracted)
    fp = len(extracted_notes) - tp
    fn = len(ground_truth_notes) - len(matched_truth)

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    # Pitch accuracy
    pitch_accuracy = len([e for e in pitch_errors_cents if e < 50]) / tp if tp > 0 else 0.0

    # Onset accuracy
    onset_within_50ms = len([e for e in onset_errors if e <= 50]) / tp if tp > 0 else 0.0

    # Velocity correlation
    if len(velocity_errors) > 1:
        matched_ext_vels = [extracted_notes[i][3] for i in matched_extracted]
        matched_truth_vels = [ground_truth_notes[j][3] for j in matched_truth]
        vel_corr = float(np.corrcoef(matched_ext_vels, matched_truth_vels)[0, 1])
        if np.isnan(vel_corr):
            vel_corr = 0.0
    else:
        vel_corr = 0.0

    # Overall score
    overall = (
        0.3 * f1 +
        0.2 * pitch_accuracy +
        0.2 * onset_within_50ms +
        0.15 * (1.0 - min(np.mean(velocity_errors) / 127 if velocity_errors else 1.0, 1.0)) +
        0.15 * (1.0 - fp / max(len(extracted_notes), 1))
    )

    return MIDIQualityMetrics(
        note_precision=precision,
        note_recall=recall,
        note_f1=f1,
        true_positives=tp,
        false_positives=fp,
        false_negatives=fn,
        pitch_accuracy=pitch_accuracy,
        pitch_mean_error_cents=np.mean(pitch_errors_cents) if pitch_errors_cents else 0.0,
        onset_mean_error_ms=np.mean(onset_errors) if onset_errors else 0.0,
        offset_mean_error_ms=np.mean(offset_errors) if offset_errors else 0.0,
        onset_within_50ms=onset_within_50ms,
        velocity_mae=np.mean(velocity_errors) if velocity_errors else 0.0,
        velocity_correlation=vel_corr,
        spurious_note_ratio=fp / max(len(extracted_notes), 1),
        missed_note_ratio=fn / len(ground_truth_notes),
        overall_score=overall,
    )


def compute_retrieval_relevance(
    query_descriptor: Dict,
    retrieved: List[Dict],
    relevance_scores: Optional[List[float]] = None,
) -> RetrievalMetrics:
    """Compute retrieval relevance metrics.

    Args:
        query_descriptor: The query descriptor
        retrieved: List of retrieved descriptor dicts
        relevance_scores: Optional ground truth relevance scores (0-1)

    Returns:
        RetrievalMetrics with all metrics
    """
    if len(retrieved) == 0:
        return RetrievalMetrics()

    query_amp = query_descriptor.get("amp", {}).get("family", "unknown")
    query_gain = query_descriptor.get("amp", {}).get("gain", 0.5)

    # Check amp family and gain range matches
    same_amp = 0
    same_gain = 0
    for r in retrieved:
        r_amp = r.get("amp", {}).get("family", "unknown")
        r_gain = r.get("amp", {}).get("gain", 0.5)
        if r_amp == query_amp:
            same_amp += 1
        if abs(r_gain - query_gain) <= 0.2:
            same_gain += 1

    same_amp_ratio = same_amp / len(retrieved)
    same_gain_ratio = same_gain / len(retrieved)

    # Precision at K
    def precision_at_k(k: int) -> float:
        if relevance_scores is None:
            # Use heuristic: same amp family = relevant
            relevant_in_k = sum(
                1 for i, r in enumerate(retrieved[:k])
                if r.get("amp", {}).get("family") == query_amp
            )
            return relevant_in_k / min(k, len(retrieved))
        else:
            relevant_in_k = sum(relevance_scores[:k])
            return relevant_in_k / min(k, len(retrieved))

    p_at_1 = precision_at_k(1)
    p_at_3 = precision_at_k(3)
    p_at_5 = precision_at_k(5)
    p_at_10 = precision_at_k(10)

    # MRR
    mrr = 0.0
    for i, r in enumerate(retrieved):
        if relevance_scores is not None and relevance_scores[i] > 0.5:
            mrr = 1.0 / (i + 1)
            break
        elif r.get("amp", {}).get("family") == query_amp:
            mrr = 1.0 / (i + 1)
            break

    # NDCG (simplified)
    def dcg_at_k(k: int) -> float:
        dcg = 0.0
        for i in range(min(k, len(retrieved))):
            if relevance_scores is not None:
                rel = relevance_scores[i]
            else:
                rel = 1.0 if retrieved[i].get("amp", {}).get("family") == query_amp else 0.0
            dcg += rel / math.log2(i + 2)
        return dcg

    def idcg_at_k(k: int) -> float:
        # Ideal DCG with all relevant items first
        idcg = 0.0
        for i in range(min(k, len(retrieved))):
            idcg += 1.0 / math.log2(i + 2)
        return idcg

    ndcg_5 = dcg_at_k(5) / idcg_at_k(5) if idcg_at_k(5) > 0 else 0.0
    ndcg_10 = dcg_at_k(10) / idcg_at_k(10) if idcg_at_k(10) > 0 else 0.0

    # Overall score
    overall = (
        0.2 * p_at_1 +
        0.2 * p_at_5 +
        0.2 * mrr +
        0.2 * ndcg_5 +
        0.1 * same_amp_ratio +
        0.1 * same_gain_ratio
    )

    return RetrievalMetrics(
        precision_at_1=p_at_1,
        precision_at_3=p_at_3,
        precision_at_5=p_at_5,
        precision_at_10=p_at_10,
        mean_reciprocal_rank=mrr,
        ndcg_at_5=ndcg_5,
        ndcg_at_10=ndcg_10,
        same_amp_family_ratio=same_amp_ratio,
        same_gain_range_ratio=same_gain_ratio,
        overall_score=overall,
    )
