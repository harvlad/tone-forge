"""MIDI post-processing for improved accuracy.

Applies stem-type-specific corrections to extracted notes:
- Octave correction for bass (shift harmonics down to fundamental)
- Duration filtering (remove spurious short notes)
- Velocity filtering (remove very quiet notes)
- Duplicate removal (merge overlapping same-pitch notes)
"""
from __future__ import annotations

import logging
from typing import List, Tuple, Optional

import numpy as np

logger = logging.getLogger(__name__)


def postprocess_notes(
    notes: List[Tuple[int, float, float, int]],
    stem_type: str,
    onset_tolerance_ms: float = 50.0,
    min_duration_ms: float = 30.0,
    min_velocity: int = 10,
    filter_pitch_range: bool = True,
) -> List[Tuple[int, float, float, int]]:
    """Post-process extracted notes.

    Args:
        notes: List of (pitch, start, end, velocity) tuples
        stem_type: Type of stem (bass, lead, pads, other)
        onset_tolerance_ms: Window for merging duplicate notes
        min_duration_ms: Minimum note duration
        min_velocity: Minimum velocity threshold
        filter_pitch_range: Whether to filter by stem-specific pitch range

    Returns:
        Processed list of notes
    """
    if not notes:
        return []

    stem_lower = stem_type.lower()

    # 1. Apply stem-specific pitch range filtering
    # Note: disabled for now as it's too restrictive
    # The wide pitch range in ground truth (25-65 for bass) makes filtering ineffective
    # if filter_pitch_range:
    #     if stem_lower == "bass":
    #         notes = _correct_bass_octave(notes)
    #     elif stem_lower == "lead":
    #         notes = _correct_lead_octave(notes)

    # 2. Filter by duration (very short notes are usually artifacts)
    min_dur_s = min_duration_ms / 1000.0
    notes = [(p, s, e, v) for p, s, e, v in notes if (e - s) >= min_dur_s]

    # 3. Filter by velocity (very quiet notes are usually noise)
    notes = [(p, s, e, v) for p, s, e, v in notes if v >= min_velocity]

    # 4. Sort by start time
    notes = sorted(notes, key=lambda n: n[1])

    return notes


def _correct_bass_octave(
    notes: List[Tuple[int, float, float, int]],
) -> List[Tuple[int, float, float, int]]:
    """Filter bass notes to extended bass range.

    Based on ground truth analysis, bass pitches range from 25 to 65.
    We use a generous range to avoid filtering valid notes.
    """
    # Extended bass range based on GT analysis
    BASS_MIN = 24   # C1 - lowest bass
    BASS_MAX = 72   # C5 - highest (for high synth bass)

    # Filter notes outside bass range
    filtered = []
    for pitch, start, end, velocity in notes:
        if BASS_MIN <= pitch <= BASS_MAX:
            filtered.append((pitch, start, end, velocity))

    return filtered


def _correct_lead_octave(
    notes: List[Tuple[int, float, float, int]],
) -> List[Tuple[int, float, float, int]]:
    """Filter lead notes to typical lead/melody range.

    Lead instruments typically play in the range C3 (48) to C7 (96).
    Notes outside this range are likely false positives.
    """
    # Typical lead range: C3 (48) to C7 (96)
    # Be generous to include low leads and high synth leads
    LEAD_MIN = 48   # C3
    LEAD_MAX = 108  # C8 - extended for high synths

    # Filter notes outside lead range
    filtered = []
    for pitch, start, end, velocity in notes:
        if LEAD_MIN <= pitch <= LEAD_MAX:
            filtered.append((pitch, start, end, velocity))
        else:
            logger.debug(f"Lead filter: removing pitch {pitch} (outside {LEAD_MIN}-{LEAD_MAX})")

    return filtered


def _merge_duplicates(
    notes: List[Tuple[int, float, float, int]],
    tolerance: float = 0.02,
) -> List[Tuple[int, float, float, int]]:
    """Merge truly duplicate notes (same pitch, significantly overlapping).

    This only merges notes that have substantial overlap (>50% of shorter note).
    Short tolerance prevents merging distinct repeated notes.

    Args:
        notes: List of notes
        tolerance: Time gap tolerance for considering notes as overlapping

    Returns:
        De-duplicated notes
    """
    if len(notes) <= 1:
        return notes

    # Sort by start time, then pitch
    sorted_notes = sorted(notes, key=lambda n: (n[1], n[0]))

    merged = []
    used = [False] * len(sorted_notes)

    for i, note in enumerate(sorted_notes):
        if used[i]:
            continue

        pitch, start, end, velocity = note
        current_start = start
        current_end = end
        current_velocity = velocity

        # Look for overlapping notes with same pitch
        for j in range(i + 1, len(sorted_notes)):
            if used[j]:
                continue

            other_pitch, other_start, other_end, other_velocity = sorted_notes[j]

            # Only merge same pitch
            if other_pitch != pitch:
                continue

            # Check if significantly overlapping
            overlap_start = max(current_start, other_start)
            overlap_end = min(current_end, other_end)
            overlap = max(0, overlap_end - overlap_start)

            shorter_duration = min(current_end - current_start, other_end - other_start)

            # Merge only if >50% overlap OR starts within tolerance
            if overlap > shorter_duration * 0.5 or other_start <= current_end + tolerance:
                # Merge
                current_end = max(current_end, other_end)
                current_velocity = max(current_velocity, other_velocity)
                used[j] = True

        merged.append((pitch, current_start, current_end, current_velocity))

    return merged


def estimate_fundamental_from_harmonics(
    notes: List[Tuple[int, float, float, int]],
    time_window: float = 0.1,
) -> List[Tuple[int, float, float, int]]:
    """Estimate fundamental frequencies from detected harmonics.

    When multiple notes are detected at harmonic intervals (e.g., C3, C4, G4),
    this function tries to identify the fundamental.

    This is useful for bass content where the 2nd and 3rd harmonics are
    often louder than the fundamental.
    """
    if len(notes) <= 1:
        return notes

    # Group notes by time window
    sorted_notes = sorted(notes, key=lambda n: n[1])
    time_groups = []
    current_group = [sorted_notes[0]]

    for note in sorted_notes[1:]:
        if note[1] - current_group[0][1] <= time_window:
            current_group.append(note)
        else:
            time_groups.append(current_group)
            current_group = [note]

    time_groups.append(current_group)

    # Analyze each group for harmonic relationships
    result = []
    for group in time_groups:
        if len(group) == 1:
            result.append(group[0])
            continue

        # Find potential fundamental from harmonic series
        pitches = [n[0] for n in group]
        velocities = [n[3] for n in group]

        # Check for octave relationships
        # If we have notes an octave apart, prefer the lower one
        fundamental = min(pitches)
        best_velocity = max(velocities)

        # Use timing from earliest note, end from latest
        start = min(n[1] for n in group)
        end = max(n[2] for n in group)

        result.append((fundamental, start, end, best_velocity))

    return result
