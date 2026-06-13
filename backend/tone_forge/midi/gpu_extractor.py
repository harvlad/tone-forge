"""
GPU-accelerated MIDI extraction using torchcrepe + MPS.

This module provides fast pitch detection for monophonic stems (bass, lead)
using PyTorch with Apple Silicon GPU (MPS) acceleration.

For polyphonic content, falls back to basic_pitch with ONNX+CoreML.

All audio processing uses torchaudio on GPU for maximum acceleration.
"""

import logging
import tempfile
import base64
from pathlib import Path
from typing import Optional, Tuple, List, Dict
from dataclasses import dataclass

import numpy as np
import torch
import torchaudio

logger = logging.getLogger(__name__)


# =============================================================================
# Phase 2 feature flag — TorchCrepe-first chooser for the lead/vocals path.
#
# When True, ``extract_midi_lead_ensemble``'s chooser branch runs
# TorchCrepe (tiny) BEFORE pYIN and short-circuits on TorchCrepe's
# result for the bulk of songs, where the 27-stem corpus shows
# TorchCrepe wins 14/15 (93.3%) chooser-branch songs under the
# existing count-based rules. Two rescue conditions still invoke pYIN:
#
#   1. harm_ratio is in the "near-HCA" band (below
#      TC_FASTPATH_HARM_RESCUE). The single pYIN-win sample in the
#      corpus sat at harm_ratio=0.718, so the threshold is set
#      slightly above to capture the band of moderately polyphonic
#      content where pYIN tends to complement TC. The next observed
#      TC-win sample is at 0.759.
#
#   2. TorchCrepe produced fewer than TC_FASTPATH_MIN_TC_COUNT notes.
#      Mirrors the old chooser's "tc_count < 5 -> pyin" branch.
#
# When False, the legacy pYIN-first chooser runs unchanged. Toggle this
# (or override via environment) to bisect winner-distribution shifts
# against the legacy path during rollout. Final default-flag
# recommendation lives in the corpus-replay report.
# =============================================================================
import os as _os
ENABLE_TC_FASTPATH = _os.environ.get(
    "TONEFORGE_TC_FASTPATH", "1"
) not in ("0", "false", "False", "")
TC_FASTPATH_HARM_RESCUE = 0.75
TC_FASTPATH_MIN_TC_COUNT = 5


# =============================================================================
# HYBRID MERGE FUNCTIONS (Phase 3 Architecture Evolution)
# =============================================================================

def _compute_frame_entropy(posteriors: np.ndarray, threshold: float = 0.3) -> np.ndarray:
    """
    Compute polyphony indicators from pitch posteriors.

    Instead of traditional entropy (which breaks on sparse posteriors),
    we compute:
    1. Count of active pitches (above threshold)
    2. Dominance ratio (how much the top pitch dominates)

    For monophonic content, there's typically 1-2 active pitches with
    the top pitch being dominant. For polyphonic, there are 3+ active
    pitches with more even distribution.

    Args:
        posteriors: Shape (frames, 88) pitch posteriors from basic_pitch
        threshold: Minimum probability to consider a pitch active

    Returns:
        Tuple of (dominance_ratio, active_counts) arrays per frame
        - dominance_ratio: 0-1 where high = more monophonic
        - active_counts: number of pitches above threshold
    """
    probs = np.clip(posteriors, 0, 1)

    # Count active pitches per frame
    active_counts = np.sum(probs > threshold, axis=1)

    # Compute dominance ratio: top pitch vs sum of all active
    # For monophonic, this is high (close to 1)
    # For polyphonic, this is lower (more spread out)
    top_pitch_prob = np.max(probs, axis=1)
    sum_active = np.sum(probs * (probs > threshold), axis=1)

    # Avoid divide by zero - add small epsilon
    eps = 1e-10
    dominance_ratio = np.where(
        sum_active > eps,
        top_pitch_prob / (sum_active + eps),
        1.0  # No activity = consider monophonic
    )

    # Invert dominance so high values = more polyphonic (like entropy was)
    # But now it's bounded 0-1 and interpretable
    polyphony_score = 1.0 - dominance_ratio

    return polyphony_score, active_counts


def _segment_by_polyphony(
    polyphony_score: np.ndarray,
    active_counts: np.ndarray,
    frame_rate: float,
    poly_score_threshold: float = 0.5,
    count_threshold: int = 3,
    min_segment_duration: float = 0.1,
) -> List[Tuple[float, float, bool]]:
    """
    Segment audio into monophonic and polyphonic regions based on posteriors.

    Args:
        polyphony_score: Per-frame polyphony score (0-1, higher = more polyphonic)
        active_counts: Per-frame count of active pitches
        frame_rate: Frames per second
        poly_score_threshold: Score above this is polyphonic
        count_threshold: Active pitch count above this is polyphonic
        min_segment_duration: Minimum segment duration in seconds

    Returns:
        List of (start_time, end_time, is_polyphonic) tuples
    """
    # Determine polyphony per frame
    # Both conditions must be met for polyphonic classification
    is_poly_frame = (polyphony_score > poly_score_threshold) & (active_counts >= count_threshold)

    segments = []
    current_is_poly = is_poly_frame[0] if len(is_poly_frame) > 0 else False
    segment_start = 0

    for i, is_poly in enumerate(is_poly_frame):
        if is_poly != current_is_poly:
            # Segment boundary
            start_time = segment_start / frame_rate
            end_time = i / frame_rate
            if end_time - start_time >= min_segment_duration:
                segments.append((start_time, end_time, current_is_poly))
            segment_start = i
            current_is_poly = is_poly

    # Final segment
    if len(is_poly_frame) > 0:
        start_time = segment_start / frame_rate
        end_time = len(is_poly_frame) / frame_rate
        if end_time - start_time >= min_segment_duration:
            segments.append((start_time, end_time, current_is_poly))

    return segments


def _notes_in_timerange(
    notes: List['MIDINote'],
    start: float,
    end: float,
) -> List['MIDINote']:
    """Get notes that overlap with the given time range."""
    result = []
    for n in notes:
        # Note overlaps if it starts before end AND ends after start
        if n.start < end and n.end > start:
            # Clip to segment boundaries
            clipped = MIDINote(
                pitch=n.pitch,
                start=max(n.start, start),
                end=min(n.end, end),
                velocity=n.velocity,
            )
            if clipped.end - clipped.start >= 0.03:  # 30ms minimum
                result.append(clipped)
    return result


def hybrid_merge(
    mono_notes: List['MIDINote'],
    poly_notes: List['MIDINote'],
    posteriors: Optional[dict],
    duration: float,
    stem_type: str = "lead",
) -> Tuple[List['MIDINote'], dict]:
    """
    [EXPERIMENTAL — DISABLED BY DEFAULT — KNOWN REGRESSION]

    Merge monophonic and polyphonic detector outputs using frame-wise posteriors.

    Instead of binary routing (mono OR poly), this runs both and selects
    the best source for each segment based on posterior entropy.

    STATUS: This implementation is a known regression vs. the baseline
    (pYIN + octave validation + gap filling) on the current benchmark:
      - Baseline:     7/16 passing (43.8%), 65.5% avg F1
      - Hybrid merge: 2/16 passing (12.5%), 36.8% avg F1
    It is retained for future research only and MUST NOT be enabled in
    production extraction paths. See backend/EXTRACTION_STATUS.md and the
    "Extraction Floor Achieved" milestone for context.

    Args:
        mono_notes: Notes from monophonic detector (pYIN/CREPE)
        poly_notes: Notes from polyphonic detector (basic_pitch)
        posteriors: Dict with 'note' posteriors and 'frame_rate' from basic_pitch
        duration: Audio duration in seconds
        stem_type: 'bass' or 'lead' - affects polyphony thresholds

    Returns:
        Tuple of (merged_notes, merge_stats)
    """
    # If no posteriors, fall back to count-based heuristic
    if posteriors is None or posteriors.get('note') is None:
        logger.warning("No posteriors available, using count-based merge")
        # Simple heuristic: use poly if it has significantly more notes
        if len(poly_notes) > len(mono_notes) * 1.5:
            return poly_notes, {"method": "poly_count_heuristic", "segments": []}
        return mono_notes, {"method": "mono_count_heuristic", "segments": []}

    note_posteriors = posteriors['note']
    frame_rate = posteriors.get('frame_rate', 22050 / 256)

    # Compute frame-wise polyphony indicators
    polyphony_score, active_counts = _compute_frame_entropy(note_posteriors)

    # Stem-type-specific thresholds
    # Bass is typically monophonic, so use stricter polyphony detection
    if stem_type == "bass":
        poly_score_threshold = 0.6  # Higher threshold for bass (monophonic bias)
        count_threshold = 4         # Need 4+ active pitches to consider polyphonic
    else:
        poly_score_threshold = 0.4  # Lower threshold for lead
        count_threshold = 3         # 3+ active pitches

    # Segment audio by polyphony
    segments = _segment_by_polyphony(
        polyphony_score, active_counts, frame_rate,
        poly_score_threshold=poly_score_threshold,
        count_threshold=count_threshold,
    )

    if len(segments) == 0:
        # No valid segments, use mono by default
        return mono_notes, {"method": "mono_default", "segments": []}

    # Merge notes from best source per segment
    merged = []
    segment_stats = []

    for start, end, is_poly in segments:
        if is_poly:
            # Polyphonic segment - use basic_pitch
            segment_notes = _notes_in_timerange(poly_notes, start, end)
            source = "poly"
        else:
            # Monophonic segment - use pYIN/CREPE
            segment_notes = _notes_in_timerange(mono_notes, start, end)
            source = "mono"

        merged.extend(segment_notes)
        segment_stats.append({
            "start": start,
            "end": end,
            "is_poly": is_poly,
            "source": source,
            "note_count": len(segment_notes),
        })

    # Sort by start time
    merged.sort(key=lambda n: n.start)

    # Remove duplicates (notes that overlap significantly)
    if len(merged) > 1:
        deduplicated = [merged[0]]
        for note in merged[1:]:
            prev = deduplicated[-1]
            # Check for overlap
            overlap = min(prev.end, note.end) - max(prev.start, note.start)
            min_duration = min(prev.end - prev.start, note.end - note.start)
            if overlap > 0 and overlap > min_duration * 0.5:
                # Significant overlap - keep the longer one
                if (note.end - note.start) > (prev.end - prev.start):
                    deduplicated[-1] = note
            else:
                deduplicated.append(note)
        merged = deduplicated

    stats = {
        "method": "hybrid_merge",
        "total_segments": len(segments),
        "mono_segments": sum(1 for s in segments if not s[2]),
        "poly_segments": sum(1 for s in segments if s[2]),
        "segments": segment_stats,
    }

    logger.info(f"Hybrid merge: {stats['mono_segments']} mono + {stats['poly_segments']} poly segments -> {len(merged)} notes")

    return merged, stats


# =============================================================================
# OCTAVE VALIDATION FUNCTIONS (Phase 3 Step 3)
# =============================================================================

def _compute_cqt_energy(
    audio: np.ndarray,
    sr: int,
    hop_length: int = 512,
    min_note: int = 24,  # C1
    n_bins: int = 72,    # 6 octaves
) -> np.ndarray:
    """
    Compute CQT energy per pitch bin.

    Returns:
        Array of shape (n_bins, frames) with energy per pitch
    """
    import librosa

    cqt = np.abs(librosa.cqt(
        y=audio,
        sr=sr,
        hop_length=hop_length,
        fmin=librosa.note_to_hz(librosa.midi_to_note(min_note)),
        n_bins=n_bins,
        bins_per_octave=12,
    ))

    return cqt


def _get_frame_range(
    start_time: float,
    end_time: float,
    hop_length: int,
    sr: int,
    max_frames: int,
) -> Tuple[int, int]:
    """Convert time range to frame range."""
    frame_start = int(start_time * sr / hop_length)
    frame_end = int(end_time * sr / hop_length)
    frame_start = max(0, min(frame_start, max_frames - 1))
    frame_end = max(frame_start + 1, min(frame_end, max_frames))
    return frame_start, frame_end


def validate_octave_with_harmonics(
    notes: List['MIDINote'],
    audio: np.ndarray,
    sr: int,
    min_note: int = 24,
    hop_length: int = 512,
) -> Tuple[List['MIDINote'], Dict]:
    """
    Validate and correct octave errors using harmonic structure analysis.

    When pYIN detects sub-harmonics (octave below actual pitch), the CQT
    will show stronger energy at the actual pitch (octave above). This
    function detects and corrects such cases.

    Args:
        notes: List of detected MIDINotes
        audio: Audio signal
        sr: Sample rate
        min_note: Minimum MIDI note in CQT (default C1 = 24)
        hop_length: Hop length for CQT

    Returns:
        Tuple of (corrected_notes, correction_stats)
    """
    if len(notes) == 0:
        return notes, {"corrections": 0, "total": 0}

    # Compute CQT for the audio
    cqt = _compute_cqt_energy(audio, sr, hop_length, min_note)
    n_bins, n_frames = cqt.shape

    corrected = []
    corrections = 0

    for note in notes:
        # Get CQT bin for this pitch
        pitch_bin = note.pitch - min_note

        # Skip if pitch is out of range
        if pitch_bin < 0 or pitch_bin >= n_bins - 12:
            corrected.append(note)
            continue

        # Get frame range for this note
        frame_start, frame_end = _get_frame_range(
            note.start, note.end, hop_length, sr, n_frames
        )

        # Get average energy at detected pitch
        detected_energy = np.mean(cqt[pitch_bin, frame_start:frame_end])

        # Get average energy at octave above
        octave_up_bin = pitch_bin + 12
        if octave_up_bin < n_bins:
            octave_up_energy = np.mean(cqt[octave_up_bin, frame_start:frame_end])
        else:
            octave_up_energy = 0

        # Check harmonic structure
        # If octave above has significantly more energy, we likely detected sub-harmonic
        # Also check for presence of expected harmonics (5th = +7, 2nd octave = +24)

        should_correct = False

        if octave_up_energy > detected_energy * 1.5:
            # Octave above is significantly stronger - likely sub-harmonic error
            # Additional check: does octave-up have proper harmonic support?
            fifth_bin = pitch_bin + 12 + 7  # fifth above the corrected pitch
            if fifth_bin < n_bins:
                fifth_energy = np.mean(cqt[fifth_bin, frame_start:frame_end])
                # If fifth is present, octave-up is likely correct
                if fifth_energy > octave_up_energy * 0.2:
                    should_correct = True
            else:
                # Can't check fifth, use energy ratio alone
                if octave_up_energy > detected_energy * 2.0:
                    should_correct = True

        if should_correct:
            # Correct to octave above
            corrected.append(MIDINote(
                pitch=note.pitch + 12,
                start=note.start,
                end=note.end,
                velocity=note.velocity,
            ))
            corrections += 1
            logger.debug(f"Octave correction: {note.pitch} -> {note.pitch + 12} "
                        f"(energy ratio: {octave_up_energy/detected_energy:.2f})")
        else:
            corrected.append(note)

    stats = {
        "corrections": corrections,
        "total": len(notes),
        "correction_rate": corrections / len(notes) if len(notes) > 0 else 0,
    }

    if corrections > 0:
        logger.info(f"Octave validation: corrected {corrections}/{len(notes)} notes ({stats['correction_rate']:.1%})")

    return corrected, stats


# =============================================================================
# CHORD-AWARE GAP FILLING (Phase 3 Step 4)
# =============================================================================

def _find_gaps(
    notes: List['MIDINote'],
    min_gap_duration: float = 0.1,
    max_gap_duration: float = 2.0,
) -> List[Tuple[float, float]]:
    """
    Find temporal gaps between notes.

    Args:
        notes: Sorted list of notes
        min_gap_duration: Minimum gap to consider (seconds)
        max_gap_duration: Maximum gap to fill (seconds)

    Returns:
        List of (gap_start, gap_end) tuples
    """
    if len(notes) < 2:
        return []

    gaps = []
    sorted_notes = sorted(notes, key=lambda n: n.start)

    for i in range(len(sorted_notes) - 1):
        current_end = sorted_notes[i].end
        next_start = sorted_notes[i + 1].start

        gap_duration = next_start - current_end
        if min_gap_duration <= gap_duration <= max_gap_duration:
            gaps.append((current_end, next_start))

    return gaps


def _get_chord_at_time(
    chords: List,  # List of Chord from chord_detector
    time: float,
) -> Optional[dict]:
    """
    Get the active chord at a specific time.

    Returns:
        Dict with 'root', 'quality', 'notes' (pitch classes) or None
    """
    for chord in chords:
        if chord.start_time <= time < chord.end_time:
            return {
                'root': chord.root,
                'quality': chord.quality,
                'notes': chord.notes,  # pitch classes 0-11
                'confidence': chord.confidence,
            }
    return None


def _infer_notes_from_chord(
    chord_info: dict,
    gap_start: float,
    gap_end: float,
    surrounding_notes: List['MIDINote'],
    stem_type: str = "bass",
) -> List['MIDINote']:
    """
    Infer likely notes for a gap based on chord context.

    Args:
        chord_info: Dict with chord root, quality, notes (pitch classes)
        gap_start: Gap start time
        gap_end: Gap end time
        surrounding_notes: Notes before and after the gap
        stem_type: 'bass' or 'lead' affects octave selection

    Returns:
        List of inferred MIDINotes
    """
    if not chord_info or not surrounding_notes:
        return []

    chord_pitch_classes = set(chord_info['notes'])

    # Get octave context from surrounding notes
    surrounding_pitches = [n.pitch for n in surrounding_notes]
    if not surrounding_pitches:
        return []

    avg_pitch = np.mean(surrounding_pitches)

    # For bass, prefer lower octaves
    if stem_type == "bass":
        base_octave = int((avg_pitch - 12) / 12) * 12  # One octave below average
        base_octave = max(24, min(48, base_octave))  # Clamp to bass range
    else:
        base_octave = int(avg_pitch / 12) * 12
        base_octave = max(48, min(84, base_octave))  # Clamp to lead range

    # Infer likely pitch from chord
    # Prefer root or fifth
    root = chord_info['root']
    inferred_pitches = []

    # Root is most likely
    root_pitch = base_octave + root
    if 24 <= root_pitch <= 96:
        inferred_pitches.append((root_pitch, 0.8))  # Higher confidence

    # Fifth is second most likely
    fifth = (root + 7) % 12
    fifth_pitch = base_octave + fifth
    if 24 <= fifth_pitch <= 96:
        inferred_pitches.append((fifth_pitch, 0.5))  # Lower confidence

    # Create notes only if gap is appropriate duration
    gap_duration = gap_end - gap_start
    if gap_duration < 0.05 or gap_duration > 1.0:
        return []

    inferred_notes = []
    for pitch, confidence in inferred_pitches:
        # Only add if confidence is high enough
        if confidence >= 0.6 and chord_info['confidence'] > 0.4:
            inferred_notes.append(MIDINote(
                pitch=pitch,
                start=gap_start,
                end=gap_end,
                velocity=60,  # Conservative velocity
            ))
            break  # Only fill with one note per gap

    return inferred_notes


def fill_gaps_with_chord_context(
    notes: List['MIDINote'],
    audio: np.ndarray,
    sr: int,
    stem_type: str = "bass",
    min_gap_duration: float = 0.1,
    max_gap_duration: float = 0.5,
) -> Tuple[List['MIDINote'], Dict]:
    """
    Fill gaps in note sequence using detected chord context.

    When notes are missing (gaps), uses chord detection to infer
    likely pitches based on harmonic context.

    Args:
        notes: List of detected MIDINotes
        audio: Audio signal
        sr: Sample rate
        stem_type: 'bass' or 'lead'
        min_gap_duration: Minimum gap to fill (seconds)
        max_gap_duration: Maximum gap to fill (seconds)

    Returns:
        Tuple of (notes_with_fills, fill_stats)
    """
    if len(notes) < 3:
        return notes, {"gaps_found": 0, "gaps_filled": 0}

    try:
        from tone_forge.chord_detector import detect_chords_from_audio
    except ImportError:
        logger.warning("Chord detector not available, skipping gap filling")
        return notes, {"gaps_found": 0, "gaps_filled": 0, "error": "chord_detector_unavailable"}

    # Detect chords from audio
    try:
        chords = detect_chords_from_audio(audio, sr, min_chord_duration=0.3)
    except Exception as e:
        logger.warning(f"Chord detection failed: {e}")
        return notes, {"gaps_found": 0, "gaps_filled": 0, "error": str(e)}

    if not chords:
        return notes, {"gaps_found": 0, "gaps_filled": 0, "chords_detected": 0}

    # Find gaps
    gaps = _find_gaps(notes, min_gap_duration, max_gap_duration)

    if not gaps:
        return notes, {"gaps_found": 0, "gaps_filled": 0, "chords_detected": len(chords)}

    # Fill gaps with chord-inferred notes
    filled_notes = list(notes)
    gaps_filled = 0

    sorted_notes = sorted(notes, key=lambda n: n.start)

    for gap_start, gap_end in gaps:
        # Get chord at this time
        chord_info = _get_chord_at_time(chords, gap_start)

        if chord_info is None:
            continue

        # Get surrounding notes for context
        before_notes = [n for n in sorted_notes if n.end <= gap_start + 0.05][-3:]
        after_notes = [n for n in sorted_notes if n.start >= gap_end - 0.05][:3]
        surrounding = before_notes + after_notes

        # Infer notes to fill gap
        fill_notes = _infer_notes_from_chord(
            chord_info, gap_start, gap_end, surrounding, stem_type
        )

        if fill_notes:
            filled_notes.extend(fill_notes)
            gaps_filled += 1
            logger.debug(f"Filled gap [{gap_start:.2f}-{gap_end:.2f}] with {chord_info['root']} {chord_info['quality']}")

    # Sort by start time
    filled_notes.sort(key=lambda n: n.start)

    stats = {
        "gaps_found": len(gaps),
        "gaps_filled": gaps_filled,
        "chords_detected": len(chords),
        "fill_rate": gaps_filled / len(gaps) if gaps else 0,
    }

    if gaps_filled > 0:
        logger.info(f"Chord gap filling: filled {gaps_filled}/{len(gaps)} gaps using {len(chords)} detected chords")

    return filled_notes, stats


def estimate_polyphony(audio_path: str, threshold: float = 0.3) -> Tuple[bool, float]:
    """
    Estimate if audio content is polyphonic using spectral analysis.

    Uses multiple heuristics:
    1. Spectral flatness - polyphonic content has more evenly distributed energy
    2. Multi-pitch detection - count simultaneous f0 candidates
    3. Harmonic-to-noise ratio - clean monophonic has higher HNR

    Args:
        audio_path: Path to audio file
        threshold: Polyphony ratio threshold (default 0.3 = 30% simultaneous notes)

    Returns:
        Tuple of (is_polyphonic: bool, polyphony_ratio: float)
    """
    import librosa

    try:
        y, sr = librosa.load(audio_path, sr=22050, mono=True, duration=30.0)

        # Method 1: Check spectral flatness variance
        # Monophonic content has more variation in spectral flatness
        flatness = librosa.feature.spectral_flatness(y=y)
        flatness_std = np.std(flatness)

        # Method 2: Use pYIN multi-pitch detection
        # Count frames where multiple pitches could be present
        f0, voiced_flag, voiced_probs = librosa.pyin(
            y, fmin=80, fmax=1000, sr=sr, frame_length=2048, hop_length=512
        )

        # Count frames with high voicing confidence as "voiced"
        voiced_frames = np.sum(voiced_flag)
        total_frames = len(voiced_flag)

        # Method 3: Check for multiple spectral peaks in voiced regions
        # Use HPSS to separate harmonic content
        y_harmonic, _ = librosa.effects.hpss(y)

        # Compute chroma energy distribution
        chroma = librosa.feature.chroma_cqt(y=y_harmonic, sr=sr)
        # Count active pitch classes per frame
        active_per_frame = np.sum(chroma > 0.3, axis=0)
        multi_pitch_frames = np.sum(active_per_frame > 2)
        polyphony_ratio = multi_pitch_frames / len(active_per_frame) if len(active_per_frame) > 0 else 0

        # Additional check: spectral bandwidth variance
        # Polyphonic content typically has higher and more variable bandwidth
        bandwidth = librosa.feature.spectral_bandwidth(y=y, sr=sr)
        bandwidth_mean = np.mean(bandwidth)

        # Combine heuristics
        # High polyphony ratio OR (low flatness_std AND high bandwidth) suggests polyphony
        is_polyphonic = polyphony_ratio > threshold

        logger.debug(f"Polyphony estimate: ratio={polyphony_ratio:.2f}, flatness_std={flatness_std:.4f}, "
                    f"bandwidth_mean={bandwidth_mean:.0f}, is_polyphonic={is_polyphonic}")

        return is_polyphonic, polyphony_ratio

    except Exception as e:
        logger.warning(f"Polyphony estimation failed: {e}")
        return False, 0.0


# Check MPS availability
MPS_AVAILABLE = torch.backends.mps.is_available()
if MPS_AVAILABLE:
    logger.info("MPS GPU available for pitch detection")
else:
    logger.warning("MPS not available, will use CPU")


def _estimate_tempo_gpu(waveform: torch.Tensor, sr: int, device: str = "mps") -> float:
    """
    Estimate tempo using GPU-accelerated onset detection and autocorrelation.

    Args:
        waveform: Audio tensor on GPU (1, samples)
        sr: Sample rate
        device: PyTorch device

    Returns:
        Estimated tempo in BPM
    """
    try:
        # Compute mel spectrogram on GPU
        n_fft = 2048
        hop_length = 512
        n_mels = 128

        mel_transform = torchaudio.transforms.MelSpectrogram(
            sample_rate=sr,
            n_fft=n_fft,
            hop_length=hop_length,
            n_mels=n_mels,
        ).to(device)

        mel = mel_transform(waveform)

        # Convert to dB
        mel_db = 10 * torch.log10(mel + 1e-10)

        # Onset strength: first-order difference + half-wave rectification
        onset = torch.diff(mel_db, dim=-1)
        onset = torch.relu(onset)
        onset_env = onset.mean(dim=1).squeeze()

        # Normalize
        onset_env = onset_env - onset_env.mean()

        # Autocorrelation via FFT (fast on GPU)
        n = len(onset_env)
        fft = torch.fft.rfft(onset_env, n=2*n)
        autocorr = torch.fft.irfft(fft * fft.conj(), n=2*n)[:n]
        autocorr = autocorr / (autocorr[0] + 1e-10)

        # Find tempo peak between 60-200 BPM
        fps = sr / hop_length
        min_lag = max(1, int(fps * 60 / 200))  # 200 BPM
        max_lag = min(n - 1, int(fps * 60 / 60))  # 60 BPM

        search_region = autocorr[min_lag:max_lag]
        if len(search_region) == 0:
            return 120.0

        best_lag = search_region.argmax().item() + min_lag
        tempo = fps * 60 / best_lag

        return float(max(60, min(200, tempo)))

    except Exception as e:
        logger.warning(f"GPU tempo estimation failed: {e}, defaulting to 120 BPM")
        return 120.0


@dataclass
class MIDINote:
    """A single MIDI note."""
    pitch: int
    start: float
    end: float
    velocity: int


def hz_to_midi(hz: float) -> int:
    """Convert frequency in Hz to MIDI note number."""
    if hz <= 0:
        return 0
    return int(round(12 * np.log2(hz / 440.0) + 69))


def extract_midi_torchcrepe(
    audio_path: str,
    stem_type: str = "lead",
    device: str = "mps" if MPS_AVAILABLE else "cpu",
    model_size: str = "tiny",  # tiny or full (small/medium/large not available)
) -> Tuple[List[MIDINote], float, float]:
    """
    Extract MIDI using torchcrepe on GPU.

    Best for monophonic content (bass, lead, vocals).

    Args:
        audio_path: Path to audio file
        stem_type: Type of stem (bass, lead, vocals)
        device: PyTorch device (mps, cuda, cpu)
        model_size: CREPE model size

    Returns:
        Tuple of (notes, tempo, duration)
    """
    import torchcrepe
    import soundfile as sf

    logger.info(f"Extracting MIDI with torchcrepe on {device} for {stem_type}")

    # Load audio - use soundfile as backend (avoids torchcodec requirement)
    try:
        # Try torchaudio with soundfile backend
        torchaudio.set_audio_backend("soundfile")
        waveform, orig_sr = torchaudio.load(audio_path)
    except Exception:
        # Fallback: load with soundfile directly, convert to tensor
        data, orig_sr = sf.read(audio_path, dtype='float32')
        if data.ndim == 1:
            waveform = torch.from_numpy(data).unsqueeze(0)
        else:
            waveform = torch.from_numpy(data.T)  # (samples, channels) -> (channels, samples)

    # Convert to mono
    if waveform.shape[0] > 1:
        waveform = waveform.mean(dim=0, keepdim=True)

    # Resample to 16kHz on GPU
    sr = 16000
    if orig_sr != sr:
        resampler = torchaudio.transforms.Resample(orig_sr, sr).to(device)
        waveform = waveform.to(device)
        waveform = resampler(waveform)
    else:
        waveform = waveform.to(device)

    duration = waveform.shape[1] / sr

    # Estimate tempo using GPU autocorrelation
    tempo = _estimate_tempo_gpu(waveform, sr, device)

    # Audio tensor for torchcrepe (needs specific shape)
    audio_tensor = waveform.float()

    # For bass, pitch-shift up 1 octave before detection
    # torchcrepe was trained on speech/vocals and fails on very low frequencies
    # We'll shift the detected pitches back down after detection
    octave_shift = 0
    if stem_type == "bass":
        try:
            import librosa
            # Convert to numpy, shift up 1 octave, convert back
            audio_np = audio_tensor.squeeze().cpu().numpy()
            audio_shifted = librosa.effects.pitch_shift(audio_np, sr=sr, n_steps=12)
            audio_tensor = torch.from_numpy(audio_shifted).unsqueeze(0).to(device)
            octave_shift = 12  # Remember to shift pitches back down
            logger.info("Bass: pitch-shifted audio up 1 octave for better torchcrepe detection")
        except Exception as e:
            logger.warning(f"Bass pitch-shift failed: {e}, using original audio")

    # Set frequency range based on stem type
    if stem_type == "bass":
        # After shifting up, bass becomes mid-range (60-1000 Hz)
        fmin, fmax = (60, 1000) if octave_shift else (30, 500)
    elif stem_type == "vocals":
        fmin, fmax = 80, 1000  # Vocal range
    elif stem_type == "pads":
        fmin, fmax = 50, 1500  # Pad range (wider for chords)
    else:  # lead, other
        fmin, fmax = 70, 1700  # Lead melodic range (optimized 39.6% F1)

    # Run pitch detection on GPU
    try:
        pitch, periodicity = torchcrepe.predict(
            audio_tensor,
            sr,
            hop_length=512,
            fmin=fmin,
            fmax=fmax,
            model=model_size,
            decoder=torchcrepe.decode.viterbi,  # Smooth pitch tracking
            return_periodicity=True,
            device=device,
            batch_size=2048,  # Larger batch for GPU efficiency
        )

        pitch = pitch.squeeze().cpu().numpy()
        periodicity = periodicity.squeeze().cpu().numpy()

    except Exception as e:
        logger.warning(f"GPU pitch detection failed, falling back to CPU: {e}")
        # Move tensor to CPU for fallback
        audio_cpu = audio_tensor.cpu()
        pitch, periodicity = torchcrepe.predict(
            audio_cpu,
            sr,
            hop_length=512,
            fmin=fmin,
            fmax=fmax,
            model=model_size,
            decoder=torchcrepe.decode.viterbi,
            return_periodicity=True,
            device="cpu",
            batch_size=512,
        )
        pitch = pitch.squeeze().cpu().numpy()
        periodicity = periodicity.squeeze().cpu().numpy()

    # Convert pitch track to MIDI notes
    # Higher periodicity threshold = fewer false positives
    # Lower threshold = more notes detected but more noise
    if stem_type == "bass":
        period_thresh = 0.23  # Bass: optimized (68.2% F1)
    elif stem_type == "vocals":
        period_thresh = 0.35
    elif stem_type == "pads":
        period_thresh = 0.4  # Pads: balanced (sustained notes)
    else:  # lead
        period_thresh = 0.6  # Lead: balanced for mono + poly content

    # Set minimum note duration based on stem type
    if stem_type == "bass":
        min_dur = 0.08  # 80ms minimum for bass
    elif stem_type == "pads":
        min_dur = 0.15  # 150ms minimum for pads (sustained notes)
    else:
        min_dur = 0.05  # 50ms for lead/vocals

    # Detect onsets to handle repeated same-pitch notes
    # This is critical for content like rapid repeated notes on same pitch
    onset_frames = None
    try:
        import librosa
        # Get audio as numpy for onset detection
        audio_np = audio_tensor.squeeze().cpu().numpy()

        # Use different onset detection strategies per stem type:
        # - Lead: energy-based (aggregate=np.mean) with backtrack for repeated note detection
        # - Bass: spectral flux (default) without backtrack for cleaner separation
        if stem_type == "lead":
            # Energy-based detection aligns better with actual note attacks
            # especially for sustained instruments with repeated same-pitch notes
            onset_env = librosa.onset.onset_strength(
                y=audio_np, sr=sr, hop_length=512, aggregate=np.mean
            )
            onset_frames = librosa.onset.onset_detect(
                onset_envelope=onset_env,
                sr=sr,
                hop_length=512,
                backtrack=True,
                units='frames',
            )
        else:
            # Spectral flux works well for bass with distinct attacks
            onset_env = librosa.onset.onset_strength(y=audio_np, sr=sr, hop_length=512)
            onset_frames = librosa.onset.onset_detect(
                onset_envelope=onset_env,
                sr=sr,
                hop_length=512,
                backtrack=False,
                units='frames',
            )
        logger.debug(f"Detected {len(onset_frames)} onsets for same-pitch note splitting")
    except Exception as e:
        logger.debug(f"Onset detection failed: {e}, continuing without onset splitting")

    notes = pitch_to_notes(
        pitch,
        periodicity,
        sr=sr,
        hop_length=512,
        min_duration=min_dur,
        periodicity_threshold=period_thresh,
        stem_type=stem_type,
        octave_shift=octave_shift,
        onset_frames=onset_frames,
    )

    logger.info(f"torchcrepe extracted {len(notes)} notes on {device}")

    return notes, tempo, duration


def extract_midi_pyin(
    audio_path: str,
    stem_type: str = "bass",
) -> Tuple[List[MIDINote], float, float]:
    """
    Extract MIDI using librosa pYIN (DSP-based, no ML).

    Excellent for clean bass lines - can achieve 99%+ F1 on simple content.
    Falls back gracefully when content is too complex.

    Args:
        audio_path: Path to audio file
        stem_type: Type of stem (bass, lead)

    Returns:
        Tuple of (notes, tempo, duration)
    """
    import librosa

    logger.info(f"Extracting MIDI with pYIN (DSP) for {stem_type}")

    # Load audio
    y, sr = librosa.load(audio_path, sr=22050, mono=True)
    duration = len(y) / sr

    # Set frequency range based on stem type
    if stem_type == "bass":
        fmin, fmax = 30, 500
    else:
        fmin, fmax = 80, 1500

    # Run pYIN pitch detection
    f0, voiced_flag, voiced_probs = librosa.pyin(
        y,
        fmin=fmin,
        fmax=fmax,
        sr=sr,
        frame_length=2048,
        hop_length=512,
    )

    # Estimate tempo
    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
    if hasattr(tempo, '__len__'):
        tempo = float(tempo[0]) if len(tempo) > 0 else 120.0
    tempo = float(max(60, min(200, tempo)))

    # Convert to notes
    frame_dur = 512 / sr
    min_dur = 0.08 if stem_type == "bass" else 0.05

    # Detect onsets for same-pitch note splitting
    # Use different strategies per stem type (same as torchcrepe)
    onset_set = set()
    try:
        if stem_type == "lead":
            # Energy-based detection with backtrack for repeated note detection
            onset_env = librosa.onset.onset_strength(
                y=y, sr=sr, hop_length=512, aggregate=np.mean
            )
            onset_frames = librosa.onset.onset_detect(
                onset_envelope=onset_env,
                sr=sr,
                hop_length=512,
                backtrack=True,
                units='frames',
            )
        else:
            # Spectral flux for bass (cleaner separation)
            onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=512)
            onset_frames = librosa.onset.onset_detect(
                onset_envelope=onset_env,
                sr=sr,
                hop_length=512,
                backtrack=False,
                units='frames',
            )
        onset_set = set(onset_frames)
        logger.debug(f"pYIN: detected {len(onset_frames)} onsets for same-pitch splitting")
    except Exception as e:
        logger.debug(f"pYIN onset detection failed: {e}")

    notes = []
    current_midi = None
    note_start = 0

    for i, (hz, is_voiced) in enumerate(zip(f0, voiced_flag)):
        if is_voiced and not np.isnan(hz) and hz > 0:
            midi = int(round(12 * np.log2(hz / 440) + 69))
        else:
            midi = None

        # Check for pitch change or onset at same pitch
        is_onset_same_pitch = (i in onset_set and midi == current_midi and midi is not None)
        is_pitch_change = (midi != current_midi)

        if is_pitch_change or is_onset_same_pitch:
            if current_midi is not None:
                note_end = i * frame_dur
                if note_end - note_start >= min_dur:
                    notes.append(MIDINote(
                        pitch=current_midi,
                        start=note_start,
                        end=note_end,
                        velocity=80,
                    ))
            current_midi = midi
            note_start = i * frame_dur

    # Handle last note
    if current_midi is not None:
        note_end = len(f0) * frame_dur
        if note_end - note_start >= min_dur:
            notes.append(MIDINote(
                pitch=current_midi,
                start=note_start,
                end=note_end,
                velocity=80,
            ))

    logger.info(f"pYIN extracted {len(notes)} notes")
    return notes, tempo, duration


def _get_basic_pitch_notes_for_subdivision(audio_path: str, pitch_range: Tuple[int, int] = (28, 55)) -> List[Tuple[int, float, float]]:
    """
    Extract basic_pitch notes in specified pitch range for subdivision.

    Returns list of (pitch, start, end) tuples.
    """
    try:
        from basic_pitch.inference import predict
        from basic_pitch import ICASSP_2022_MODEL_PATH
        import tempfile
        import soundfile as sf
        import librosa
        import os

        y, sr = librosa.load(str(audio_path), sr=22050, mono=True)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            sf.write(tmp.name, y, sr)
            tmp_path = tmp.name

        try:
            _, _, note_events = predict(
                tmp_path,
                ICASSP_2022_MODEL_PATH,
                onset_threshold=0.5,
                frame_threshold=0.3,
                minimum_note_length=50.0,
            )

            bp_notes = []
            for start, end, pitch, amplitude, _ in note_events:
                if pitch_range[0] <= pitch <= pitch_range[1]:
                    bp_notes.append((int(pitch), float(start), float(end)))
            bp_notes.sort(key=lambda x: x[1])
            return bp_notes

        finally:
            try:
                os.unlink(tmp_path)
            except:
                pass

    except Exception as e:
        logger.warning(f"basic_pitch extraction failed for subdivision: {e}")
        return []


def _subdivide_notes_with_basic_pitch(
    primary_notes: List[MIDINote],
    bp_notes: List[Tuple[int, float, float]],
    min_subdivision_gap: float = 0.03,
) -> List[MIDINote]:
    """
    Subdivide primary notes using basic_pitch onset information.

    For each primary note, if basic_pitch has multiple notes starting within
    that note's timespan, split the primary note at those onsets.
    """
    result = []

    for pn in primary_notes:
        # Find basic_pitch notes that start within this primary note's timespan
        overlap_margin = 0.07  # 70ms margin - tuned for better recall
        bp_in_range = [
            bp for bp in bp_notes
            if pn.start - overlap_margin <= bp[1] < pn.end + overlap_margin
        ]

        if len(bp_in_range) <= 1:
            # No subdivision needed
            result.append(pn)
        else:
            # Multiple basic_pitch notes - subdivide at their onsets
            bp_onsets = sorted(set([bp[1] for bp in bp_in_range]))

            # Filter out onsets that are too close together
            filtered_onsets = [bp_onsets[0]]
            for onset in bp_onsets[1:]:
                if onset - filtered_onsets[-1] >= min_subdivision_gap:
                    filtered_onsets.append(onset)

            # Create subdivided notes
            for i, onset in enumerate(filtered_onsets):
                sub_start = max(pn.start, onset)
                if i + 1 < len(filtered_onsets):
                    sub_end = min(pn.end, filtered_onsets[i + 1])
                else:
                    sub_end = pn.end

                if sub_end - sub_start >= 0.05:  # 50ms minimum
                    result.append(MIDINote(
                        pitch=pn.pitch,
                        start=sub_start,
                        end=sub_end,
                        velocity=pn.velocity,
                    ))

    return result


def _should_apply_subdivision(
    pyin_notes: List[MIDINote],
    bp_notes: List[Tuple[int, float, float]],
) -> bool:
    """
    Determine if subdivision should be applied based on runtime features.

    Triggers when:
    1. BP median pitch is ~12 semitones above pYIN median (octave error detection)
    2. BP/pYIN count ratio is between 1.0 and 1.8 (modest difference)

    This catches under-detection due to octave locking while avoiding
    over-subdivision on samples where pYIN count is already correct.
    """
    if len(pyin_notes) < 20 or len(bp_notes) < 20:
        return False

    pyin_median = np.median([n.pitch for n in pyin_notes])
    bp_median = np.median([n[0] for n in bp_notes])

    pitch_diff = bp_median - pyin_median
    count_ratio = len(bp_notes) / len(pyin_notes)

    # Condition: BP is approximately one octave above pYIN AND modest count ratio
    should_apply = (
        10 <= pitch_diff <= 14  # ~12 semitones with tolerance
        and 1.0 <= count_ratio <= 1.8
    )

    if should_apply:
        logger.info(f"Subdivision triggered: BP-pYIN pitch diff={pitch_diff:.0f}, "
                   f"count ratio={count_ratio:.2f}")

    return should_apply


def extract_midi_bass_ensemble(
    audio_path: str,
    use_hybrid_merge: bool = False,  # EXPERIMENTAL — DISABLED — regression (2/16 vs 7/16)
) -> Tuple[List[MIDINote], float, float, str]:
    """
    Ensemble bass extraction. Production path uses pYIN + octave validation
    + chord-aware gap filling (the count-based fallback below).

    The hybrid mono+poly merge path (`use_hybrid_merge=True`) is
    [EXPERIMENTAL — DISABLED BY DEFAULT — KNOWN REGRESSION] and MUST NOT
    be re-enabled in production without fresh benchmark evidence. Current
    benchmark: hybrid 2/16 (12.5%) vs baseline 7/16 (43.8%).

    Args:
        audio_path: Path to audio file
        use_hybrid_merge: EXPERIMENTAL. Keep False for production.
                         If True, attempts frame-wise posterior merging
                         (known regression on bass benchmark).

    Returns:
        Tuple of (notes, tempo, duration, method_used)
    """
    import librosa

    # Load audio for duration
    y, sr = librosa.load(audio_path, sr=22050, mono=True)
    duration = len(y) / sr

    # Run pYIN (monophonic detector)
    try:
        pyin_notes, pyin_tempo, _ = extract_midi_pyin(audio_path, "bass")
    except Exception as e:
        logger.warning(f"pYIN failed: {e}")
        pyin_notes = []
        pyin_tempo = 120.0

    # Run basic_pitch WITH posteriors (polyphonic detector)
    bp_notes = []
    bp_posteriors = None

    if use_hybrid_merge:
        try:
            from tone_forge.midi.ensemble_extractor import BasicPitchDetector

            detector = BasicPitchDetector()
            bp_detected, bp_posteriors = detector.detect_with_posteriors(
                y, sr,
                onset_threshold=0.5,
                frame_threshold=0.3,
            )

            # Convert DetectedNote to MIDINote
            bp_notes = [
                MIDINote(
                    pitch=n.pitch,
                    start=n.start,
                    end=n.end,
                    velocity=n.velocity,
                )
                for n in bp_detected
            ]

            logger.info(f"basic_pitch: {len(bp_notes)} notes, posteriors shape={bp_posteriors['note'].shape if bp_posteriors and bp_posteriors.get('note') is not None else 'N/A'}")

        except Exception as e:
            logger.warning(f"basic_pitch with posteriors failed: {e}")
            bp_notes = []
            bp_posteriors = None

    pyin_count = len(pyin_notes)
    bp_count = len(bp_notes)

    # HYBRID MERGE: Use frame-wise posteriors to select best source per segment
    if use_hybrid_merge and bp_posteriors is not None and bp_count > 0:
        merged_notes, merge_stats = hybrid_merge(
            mono_notes=pyin_notes,
            poly_notes=bp_notes,
            posteriors=bp_posteriors,
            duration=duration,
            stem_type="bass",  # Bass-specific polyphony thresholds
        )

        if len(merged_notes) > 0:
            notes = merged_notes
            method = f"hybrid_merge_{merge_stats['mono_segments']}m_{merge_stats['poly_segments']}p"
            tempo = pyin_tempo
            logger.info(f"Hybrid merge: {pyin_count} pYIN + {bp_count} BP -> {len(notes)} merged notes")

            # Apply octave validation to correct sub-harmonic errors
            try:
                notes, octave_stats = validate_octave_with_harmonics(notes, y, sr)
                if octave_stats['corrections'] > 0:
                    method = method + "_octave_validated"
            except Exception as e:
                logger.warning(f"Octave validation failed: {e}")

            # Apply chord-aware gap filling
            try:
                notes, gap_stats = fill_gaps_with_chord_context(notes, y, sr, "bass")
                if gap_stats.get('gaps_filled', 0) > 0:
                    method = method + "_gap_filled"
            except Exception as e:
                logger.warning(f"Chord gap filling failed: {e}")

            return notes, tempo, duration, method

    # FALLBACK: Binary routing based on count heuristics (original logic)
    logger.info("Falling back to count-based heuristics")

    use_pyin = False

    if pyin_count >= 15 and bp_count > 0:
        ratio = pyin_count / bp_count
        if ratio >= 0.8:
            use_pyin = True
            logger.info(f"Ensemble: choosing pYIN ({pyin_count} notes) - ratio {ratio:.2f}")
        elif pyin_count > bp_count:
            use_pyin = True
            logger.info(f"Ensemble: choosing pYIN ({pyin_count} notes) - more than BP ({bp_count})")
    elif pyin_count >= 40:
        use_pyin = True
        logger.info(f"Ensemble: choosing pYIN ({pyin_count} notes) - strong detection")

    if use_pyin:
        notes = pyin_notes
        method = "pyin_dsp"
        tempo = pyin_tempo
    else:
        notes = bp_notes if bp_count > 0 else pyin_notes
        method = "basic_pitch" if bp_count > 0 else "pyin_dsp"
        tempo = pyin_tempo
        logger.info(f"Ensemble: choosing BP ({bp_count} notes) over pYIN ({pyin_count} notes)")

    # Note subdivision using basic_pitch for octave-error cases
    if use_pyin and len(pyin_notes) >= 20:
        try:
            bp_tuples = [(n.pitch, n.start, n.end) for n in bp_notes]
            if _should_apply_subdivision(pyin_notes, bp_tuples):
                subdivided = _subdivide_notes_with_basic_pitch(notes, bp_tuples)
                logger.info(f"Subdivision: {len(notes)} -> {len(subdivided)} notes")
                notes = subdivided
                method = "pyin_dsp_subdivided"
        except Exception as e:
            logger.warning(f"Note subdivision failed: {e}")

    # Apply octave validation to correct sub-harmonic errors
    try:
        notes, octave_stats = validate_octave_with_harmonics(notes, y, sr)
        if octave_stats['corrections'] > 0:
            method = method + "_octave_validated"
    except Exception as e:
        logger.warning(f"Octave validation failed: {e}")

    # Apply chord-aware gap filling
    try:
        notes, gap_stats = fill_gaps_with_chord_context(notes, y, sr, "bass")
        if gap_stats.get('gaps_filled', 0) > 0:
            method = method + "_gap_filled"
    except Exception as e:
        logger.warning(f"Chord gap filling failed: {e}")

    return notes, tempo, duration, method


def extract_midi_lead_ensemble(
    audio_path: str,
    use_hca_for_polyphony: bool = True,
    use_hybrid_merge: bool = False,  # EXPERIMENTAL — DISABLED — known regression
    harm_ratio: Optional[float] = None,
) -> Tuple[List[MIDINote], float, float, str]:
    """
    Ensemble lead extraction. Production path is HCA for very polyphonic
    content (harm_ratio < 0.65) and pYIN/torchcrepe routing with octave
    validation + chord-aware gap filling otherwise.

    The hybrid mono+poly merge path (`use_hybrid_merge=True`) is
    [EXPERIMENTAL — DISABLED BY DEFAULT — KNOWN REGRESSION] and MUST NOT
    be re-enabled in production without fresh benchmark evidence. See
    backend/EXTRACTION_STATUS.md for context.

    Args:
        audio_path: Path to audio file
        use_hca_for_polyphony: If True, use HCA for very polyphonic content
        use_hybrid_merge: EXPERIMENTAL. Keep False for production.
        harm_ratio: If provided, skip the internal estimate_harmonic_ratio
            call (which costs ~5-7s on a full song). Used by
            analysis_worker to overlap HPSS with drums+bass MIDI
            extraction (Phase 1 concurrency).

    Returns:
        Tuple of (notes, tempo, duration, method_used)
    """
    import librosa

    # Load audio
    y, sr = librosa.load(audio_path, sr=22050, mono=True)
    duration = len(y) / sr

    # Check for very polyphonic content - use HCA for dense chords
    if use_hca_for_polyphony:
        try:
            from tone_forge.midi.harmonic_cluster_analyzer import (
                HarmonicClusterAnalyzer,
                estimate_harmonic_ratio,
            )

            # Phase 1 (harm_ratio concurrency): if caller precomputed it
            # on a worker thread, accept the value; otherwise fall back
            # to the inline computation. Must be numerically identical
            # to the inline call when caller used estimate_harmonic_ratio
            # on the same y/sr.
            if harm_ratio is None:
                harm_ratio = estimate_harmonic_ratio(y, sr)

            # Only use HCA for VERY polyphonic content (tighter threshold)
            # Let hybrid merge handle moderately polyphonic content
            if harm_ratio < 0.65:
                logger.info(f"Lead: very polyphonic content (harm_ratio={harm_ratio:.2f}), using HCA")
                try:
                    analyzer = HarmonicClusterAnalyzer(sr=sr)
                    hca_notes = analyzer.extract(y)

                    if len(hca_notes) > 0:
                        notes = [
                            MIDINote(
                                pitch=n.pitch,
                                start=n.start,
                                end=n.end,
                                velocity=n.velocity,
                            )
                            for n in hca_notes
                        ]

                        onsets = sorted([n.start for n in notes])
                        if len(onsets) > 1:
                            iois = np.diff(onsets)
                            iois = iois[(iois > 0.1) & (iois < 2.0)]
                            if len(iois) > 0:
                                tempo = 60.0 / (np.median(iois) * 2)
                                tempo = float(np.clip(tempo, 60, 200))
                            else:
                                tempo = 120.0
                        else:
                            tempo = 120.0

                        logger.info(f"HCA extracted {len(notes)} notes for polyphonic lead")
                        return notes, tempo, duration, "hca_polyphonic"

                except Exception as e:
                    logger.warning(f"HCA failed: {e}, trying hybrid merge")

        except ImportError as e:
            logger.warning(f"HCA module not available: {e}")

    # Phase 2 (ENABLE_TC_FASTPATH) — feature-flagged TorchCrepe-first
    # chooser path.
    #
    # Production chooser (use_pyin block below) always runs pYIN before
    # TorchCrepe and decides between them by note-count ratios. On the
    # 27-stem corpus, TorchCrepe wins 14/15 chooser-branch songs (93.3%),
    # so pYIN's 10.83s/song mean cost is wasted in the vast majority of
    # cases. The single pYIN-win outlier (tlu522gu) sat at harm_ratio
    # 0.718, the lowest in the chooser branch — closest to the HCA
    # boundary at 0.65.
    #
    # When ENABLE_TC_FASTPATH is True:
    #   1. Run TorchCrepe tiny first (cheap, ~1s).
    #   2. Apply rescue heuristic: if harm_ratio is below
    #      TC_FASTPATH_HARM_RESCUE (the "near-HCA" band), the song is
    #      structurally close to the pYIN-win case; run pYIN and fall
    #      through to the existing count-based chooser.
    #   3. Otherwise, return TorchCrepe notes immediately without
    #      paying the pYIN cost.
    #
    # When ENABLE_TC_FASTPATH is False, this entire block is skipped
    # and the original pYIN-first path runs unchanged.
    #
    # Instrumentation (per chooser-branch call) is emitted via the
    # logger so corpus replay can recover tc_count / pyin_count /
    # rescue / winner without re-running the extraction. Tagged
    # ``TC_FASTPATH_TELEMETRY`` so callers can grep one prefix.
    tc_notes_fp = None
    tc_count_fp = 0
    pyin_notes = []
    pyin_tempo = 120.0
    pyin_count = 0
    rescue_triggered = False
    tc_fastpath_used = False

    if ENABLE_TC_FASTPATH:
        try:
            tc_notes_fp, tc_tempo_fp, tc_duration_fp = extract_midi_torchcrepe(
                audio_path, stem_type="lead", model_size="tiny"
            )
            tc_count_fp = len(tc_notes_fp)
        except Exception as e:
            logger.warning(f"torchcrepe (fastpath) failed for lead: {e}")
            tc_notes_fp = []
            tc_count_fp = 0

        # Rescue 1: harm_ratio close to the HCA threshold (0.65)
        # indicates moderately polyphonic content; pYIN tends to
        # complement TC here. The only pYIN-win sample in the corpus
        # had harm_ratio 0.718, so the threshold is set tight above
        # that to also catch nearby unobserved cases without
        # capturing the bulk of TC-win songs (next sample at 0.759+).
        if harm_ratio is not None and harm_ratio < TC_FASTPATH_HARM_RESCUE:
            rescue_triggered = True
            logger.info(
                f"TC_FASTPATH rescue: harm_ratio={harm_ratio:.3f} < "
                f"{TC_FASTPATH_HARM_RESCUE} (near-HCA band), running pYIN"
            )

        # Rescue 2: TC produced almost no notes. The old chooser
        # would have picked pYIN under the "tc_count < 5" rule.
        # We mirror that.
        elif tc_count_fp < TC_FASTPATH_MIN_TC_COUNT:
            rescue_triggered = True
            logger.info(
                f"TC_FASTPATH rescue: tc_count={tc_count_fp} < "
                f"{TC_FASTPATH_MIN_TC_COUNT} (TC under-produced), "
                f"running pYIN"
            )

        if not rescue_triggered and tc_count_fp > 0:
            # Fast path: return TC immediately.
            tc_fastpath_used = True
            logger.info(
                f"TC_FASTPATH_TELEMETRY branch=chooser flag=on "
                f"tc_count={tc_count_fp} pyin_count=skipped "
                f"harm_ratio={harm_ratio} winner=torchcrepe_gpu "
                f"rescue=False fastpath=True"
            )
            return tc_notes_fp, tc_tempo_fp, duration, "torchcrepe_gpu"

    # Run pYIN (monophonic detector)
    # Either ENABLE_TC_FASTPATH is False (legacy path) or rescue was
    # triggered. Either way the pYIN run is required.
    try:
        pyin_notes, pyin_tempo, _ = extract_midi_pyin(audio_path, "lead")
        pyin_count = len(pyin_notes)
    except Exception as e:
        logger.warning(f"pYIN failed for lead: {e}")
        pyin_notes = []
        pyin_tempo = 120.0
        pyin_count = 0

    # Run basic_pitch WITH posteriors (polyphonic detector)
    bp_notes = []
    bp_posteriors = None

    if use_hybrid_merge:
        try:
            from tone_forge.midi.ensemble_extractor import BasicPitchDetector

            detector = BasicPitchDetector()
            bp_detected, bp_posteriors = detector.detect_with_posteriors(
                y, sr,
                onset_threshold=0.5,
                frame_threshold=0.3,
            )

            # Convert DetectedNote to MIDINote
            bp_notes = [
                MIDINote(
                    pitch=n.pitch,
                    start=n.start,
                    end=n.end,
                    velocity=n.velocity,
                )
                for n in bp_detected
            ]

            logger.info(f"basic_pitch lead: {len(bp_notes)} notes")

        except Exception as e:
            logger.warning(f"basic_pitch with posteriors failed: {e}")
            bp_notes = []
            bp_posteriors = None

    # pyin_count is set inline after the pYIN call above (Phase 2);
    # this legacy recompute would shadow the explicit zero set by
    # the except branch. bp_count is still derived locally.
    bp_count = len(bp_notes)

    # HYBRID MERGE: Use frame-wise posteriors to select best source per segment
    if use_hybrid_merge and bp_posteriors is not None and bp_count > 0:
        merged_notes, merge_stats = hybrid_merge(
            mono_notes=pyin_notes,
            poly_notes=bp_notes,
            posteriors=bp_posteriors,
            duration=duration,
            stem_type="lead",  # Lead-specific polyphony thresholds
        )

        if len(merged_notes) > 0:
            notes = merged_notes
            method = f"hybrid_merge_{merge_stats['mono_segments']}m_{merge_stats['poly_segments']}p"
            logger.info(f"Lead hybrid merge: {pyin_count} pYIN + {bp_count} BP -> {len(notes)} merged notes")

            # Apply octave validation to correct sub-harmonic errors
            try:
                notes, octave_stats = validate_octave_with_harmonics(notes, y, sr)
                if octave_stats['corrections'] > 0:
                    method = method + "_octave_validated"
            except Exception as e:
                logger.warning(f"Octave validation failed: {e}")

            # Apply chord-aware gap filling
            try:
                notes, gap_stats = fill_gaps_with_chord_context(notes, y, sr, "lead")
                if gap_stats.get('gaps_filled', 0) > 0:
                    method = method + "_gap_filled"
            except Exception as e:
                logger.warning(f"Chord gap filling failed: {e}")

            return notes, pyin_tempo, duration, method

    # FALLBACK: Count-based heuristics
    logger.info("Lead: falling back to count-based heuristics")

    # Reuse the fastpath's TorchCrepe run when one happened (rescue
    # triggered before we returned early). Otherwise (flag off) run
    # TorchCrepe now. Skipping the second TC call when rescue fired
    # is the whole point of moving the TC call up: model load + GPU
    # work would have been duplicated, paying twice for the same
    # result. Option 1 (model_size="tiny") still applies.
    if tc_notes_fp is not None:
        tc_notes = tc_notes_fp
        tc_count = tc_count_fp
    else:
        try:
            tc_notes, tc_tempo, tc_duration = extract_midi_torchcrepe(
                audio_path, stem_type="lead", model_size="tiny"
            )
        except Exception as e:
            logger.warning(f"torchcrepe failed for lead: {e}")
            tc_notes = []
        tc_count = len(tc_notes)

    # Choose between pYIN and torchcrepe
    use_pyin = False

    if pyin_count > 0 and tc_count > 0:
        ratio = pyin_count / tc_count
        if ratio <= 0.5 and pyin_count >= 10:
            use_pyin = True
            logger.info(f"Lead: choosing pYIN ({pyin_count} notes) - torchcrepe over-detected ({tc_count})")
        elif 0.8 <= ratio <= 1.2 and pyin_count >= 20:
            use_pyin = True
            logger.info(f"Lead: choosing pYIN ({pyin_count} notes) - similar count, ratio {ratio:.2f}")
    elif pyin_count >= 20 and tc_count < 5:
        use_pyin = True
        logger.info(f"Lead: choosing pYIN ({pyin_count} notes) - torchcrepe failed")

    winner_method = "pyin_dsp" if use_pyin else "torchcrepe_gpu"

    # Phase 2 telemetry. Emitted from the count-based fallback so
    # corpus replay can recover (tc_count, pyin_count, winner,
    # rescue_triggered) without re-running. ``flag`` records the
    # state of ENABLE_TC_FASTPATH at call time; ``fastpath`` is False
    # here because we got to the legacy chooser (rescue OR flag-off).
    logger.info(
        f"TC_FASTPATH_TELEMETRY branch=chooser "
        f"flag={'on' if ENABLE_TC_FASTPATH else 'off'} "
        f"tc_count={tc_count} pyin_count={pyin_count} "
        f"harm_ratio={harm_ratio} winner={winner_method} "
        f"rescue={rescue_triggered} fastpath=False"
    )

    if use_pyin:
        return pyin_notes, pyin_tempo, duration, "pyin_dsp"
    else:
        logger.info(f"Lead: choosing torchcrepe ({tc_count} notes) over pYIN ({pyin_count} notes)")
        return tc_notes, pyin_tempo, duration, "torchcrepe_gpu"


def pitch_to_notes(
    pitch: np.ndarray,
    periodicity: np.ndarray,
    sr: int,
    hop_length: int,
    min_duration: float = 0.05,
    periodicity_threshold: float = 0.5,
    stem_type: str = "lead",
    octave_shift: int = 0,
    onset_frames: Optional[np.ndarray] = None,
) -> List[MIDINote]:
    """
    Convert pitch and periodicity arrays to MIDI notes.

    Args:
        pitch: Pitch values in Hz per frame
        periodicity: Confidence values per frame (0-1)
        sr: Sample rate
        hop_length: Hop length used for pitch detection
        min_duration: Minimum note duration in seconds
        periodicity_threshold: Minimum periodicity to consider a pitch valid
        stem_type: Type of stem for velocity scaling
        octave_shift: Semitones to subtract from detected pitches (for bass pitch-shift correction)
        onset_frames: Optional array of frame indices where onsets occur (for splitting same-pitch notes)

    Returns:
        List of MIDINote objects
    """
    frame_duration = hop_length / sr
    notes = []

    # Find voiced regions
    voiced = periodicity > periodicity_threshold

    # Convert pitch to MIDI notes
    # Apply octave_shift correction for bass (shift detected pitches back down)
    midi_pitches = np.zeros_like(pitch, dtype=int)
    for i, (hz, is_voiced) in enumerate(zip(pitch, voiced)):
        if is_voiced and hz > 0:
            detected_midi = hz_to_midi(hz)
            # Shift back down if audio was pitch-shifted up
            midi_pitches[i] = detected_midi - octave_shift
        else:
            midi_pitches[i] = 0

    # Create onset set for O(1) lookup
    onset_set = set(onset_frames) if onset_frames is not None else set()

    # Group consecutive frames with same pitch into notes
    # BUT split at onsets even if pitch is the same (for repeated notes)
    current_pitch = 0
    note_start = 0
    note_periodicity = []

    for i, (midi_pitch, period) in enumerate(zip(midi_pitches, periodicity)):
        # Check if we should start a new note:
        # 1. Pitch changed
        # 2. OR onset detected at same pitch (repeated note)
        is_onset_at_same_pitch = (i in onset_set and midi_pitch == current_pitch and midi_pitch > 0)
        is_pitch_change = (midi_pitch != current_pitch)

        if is_pitch_change or is_onset_at_same_pitch:
            # End previous note if it exists
            if current_pitch > 0:
                note_end = i * frame_duration
                note_duration = note_end - note_start

                if note_duration >= min_duration:
                    # Calculate velocity from periodicity
                    avg_periodicity = np.mean(note_periodicity) if note_periodicity else 0.5
                    velocity = int(60 + avg_periodicity * 60)  # 60-120 range
                    velocity = max(40, min(120, velocity))

                    notes.append(MIDINote(
                        pitch=current_pitch,
                        start=note_start,
                        end=note_end,
                        velocity=velocity,
                    ))

            # Start new note
            current_pitch = midi_pitch
            note_start = i * frame_duration
            note_periodicity = [period] if midi_pitch > 0 else []
        else:
            if current_pitch > 0:
                note_periodicity.append(period)

    # Handle last note
    if current_pitch > 0:
        note_end = len(midi_pitches) * frame_duration
        note_duration = note_end - note_start

        if note_duration >= min_duration:
            avg_periodicity = np.mean(note_periodicity) if note_periodicity else 0.5
            velocity = int(60 + avg_periodicity * 60)
            velocity = max(40, min(120, velocity))

            notes.append(MIDINote(
                pitch=current_pitch,
                start=note_start,
                end=note_end,
                velocity=velocity,
            ))

    # Post-process: merge very short gaps between same-pitch notes
    if len(notes) > 1:
        merged = [notes[0]]
        for note in notes[1:]:
            prev = merged[-1]
            gap = note.start - prev.end

            # Merge if same pitch and gap < 50ms
            if note.pitch == prev.pitch and gap < 0.05:
                merged[-1] = MIDINote(
                    pitch=prev.pitch,
                    start=prev.start,
                    end=note.end,
                    velocity=max(prev.velocity, note.velocity),
                )
            else:
                merged.append(note)
        notes = merged

    return notes


def notes_to_midi_file(
    notes: List[MIDINote],
    tempo: float,
    output_path: str,
    track_name: str = "Extracted",
) -> None:
    """Write notes to a MIDI file."""
    import pretty_midi

    midi = pretty_midi.PrettyMIDI(initial_tempo=tempo)
    instrument = pretty_midi.Instrument(program=0, name=track_name)

    for note in notes:
        midi_note = pretty_midi.Note(
            velocity=note.velocity,
            pitch=note.pitch,
            start=note.start,
            end=note.end,
        )
        instrument.notes.append(midi_note)

    midi.instruments.append(instrument)
    midi.write(output_path)


def extract_midi_hybrid(
    audio_path: str,
    stem_type: str = "other",
    preset_name: str = "Extracted MIDI",
    harm_ratio: Optional[float] = None,
) -> dict:
    """
    Hybrid MIDI extraction - uses GPU for monophonic, CPU for polyphonic.

    Args:
        audio_path: Path to audio file
        stem_type: Type of stem (bass, lead, drums, other, pad)
        preset_name: Name for the MIDI file
        harm_ratio: Precomputed harmonic ratio (overlap optimization).
            Only used for lead/vocals stems; forwarded to
            extract_midi_lead_ensemble to skip its internal HPSS call.

    Returns:
        Dict with MIDI data (compatible with MIDIExtractionResult)
    """
    from tone_forge.midi_extractor import extract_drum_midi, MIDIExtractionResult

    # Drums use specialized extraction
    if stem_type == "drums":
        result = extract_drum_midi(audio_path, preset_name)
        return {
            "filename": result.filename,
            "content": result.content,
            "note_count": result.note_count,
            "duration_seconds": result.duration_seconds,
            # Per-stem tempo estimate from this extractor's internal onset
            # analysis. NOT the canonical session tempo — that lives at
            # the top-level ``result.tempo_bpm`` from beat_track on the
            # full mix. Renamed from ``tempo_bpm`` to disambiguate after
            # observing drums=95.7 / bass=129.2 / guitar=107.66 on a
            # single song. See backend/local_engine/analysis_worker.py.
            "extraction_tempo_bpm": result.tempo_bpm,
            "pitch_range": result.pitch_range,
            "method": "onset_detection",
        }

    # Bass uses ensemble of pYIN (DSP) + torchcrepe (ML) for best results
    if stem_type == "bass":
        try:
            notes, tempo, duration, method = extract_midi_bass_ensemble(audio_path)

            if len(notes) > 0:
                with tempfile.NamedTemporaryFile(suffix='.mid', delete=False) as f:
                    notes_to_midi_file(notes, tempo, f.name, preset_name)
                    with open(f.name, 'rb') as mf:
                        midi_bytes = mf.read()
                    Path(f.name).unlink()

                midi_b64 = base64.b64encode(midi_bytes).decode('ascii')
                pitches = [n.pitch for n in notes]

                return {
                    "filename": f"{preset_name}.mid",
                    "content": midi_b64,
                    "note_count": len(notes),
                    "duration_seconds": duration,
                    # Bass-only tempo from pYIN+torchcrepe ensemble; can
                    # disagree with the canonical session tempo because
                    # bass plays on subdivisions. See per-stem rename note
                    # at the drum branch above.
                    "extraction_tempo_bpm": tempo,
                    "pitch_range": (min(pitches), max(pitches)),
                    "method": method,
                }
        except Exception as e:
            logger.warning(f"Bass ensemble failed, falling back to CoreML: {e}")

    # Lead/vocals use ensemble of pYIN (DSP) + torchcrepe (ML)
    if stem_type in ("lead", "vocals"):
        try:
            notes, tempo, duration, method = extract_midi_lead_ensemble(
                audio_path, harm_ratio=harm_ratio
            )
            logger.info(f"Lead ensemble chose: {method} with {len(notes)} notes")

            if len(notes) > 0:
                with tempfile.NamedTemporaryFile(suffix='.mid', delete=False) as f:
                    notes_to_midi_file(notes, tempo, f.name, preset_name)
                    with open(f.name, 'rb') as mf:
                        midi_bytes = mf.read()
                    Path(f.name).unlink()

                midi_b64 = base64.b64encode(midi_bytes).decode('ascii')
                pitches = [n.pitch for n in notes]

                return {
                    "filename": f"{preset_name}.mid",
                    "content": midi_b64,
                    "note_count": len(notes),
                    "duration_seconds": duration,
                    # Lead/vocals tempo from the same ensemble; same
                    # caveat as bass — onset-derived per-stem estimate,
                    # not the session-canonical tempo.
                    "extraction_tempo_bpm": tempo,
                    "pitch_range": (min(pitches), max(pitches)),
                    "method": method,
                }
        except Exception as e:
            logger.warning(f"Lead ensemble failed for {stem_type}, falling back to CoreML: {e}")

    # Polyphonic stems (other, pad, synth) or fallback use CoreML GPU
    try:
        from tone_forge.midi.coreml_extractor import extract_midi_coreml

        # Stem-specific thresholds for CoreML extraction
        # Higher thresholds = fewer notes (reduce false positives)
        # Lower thresholds = more notes (reduce false negatives)
        stem_thresholds = {
            "bass": {"onset": 0.5, "frame": 0.4},    # Bass: stricter to reduce FPs
            "lead": {"onset": 0.5, "frame": 0.4},    # Lead: balanced (81% F1)
            "pads": {"onset": 0.6, "frame": 0.5},     # Pads: best tested
            "other": {"onset": 0.55, "frame": 0.45}, # Other: balanced
        }
        thresholds = stem_thresholds.get(stem_type, {"onset": 0.5, "frame": 0.4})

        logger.info(f"Using CoreML GPU for polyphonic {stem_type} (onset={thresholds['onset']}, frame={thresholds['frame']})")
        return extract_midi_coreml(
            audio_path,
            preset_name=preset_name,
            onset_threshold=thresholds["onset"],
            frame_threshold=thresholds["frame"],
            stem_type=stem_type,
        )
    except Exception as e:
        logger.warning(f"CoreML extraction failed: {e}, falling back to ONNX")
        # Final fallback to ONNX basic_pitch
        from tone_forge.midi_extractor import extract_midi
        result = extract_midi(audio_path, preset_name, stem_type=stem_type)
        return {
            "filename": result.filename,
            "content": result.content,
            "note_count": result.note_count,
            "duration_seconds": result.duration_seconds,
            # basic_pitch ONNX fallback. Per-stem tempo estimate.
            "extraction_tempo_bpm": result.tempo_bpm,
            "pitch_range": result.pitch_range,
            "method": "basic_pitch_onnx",
            "provenance": result.provenance,
        }
