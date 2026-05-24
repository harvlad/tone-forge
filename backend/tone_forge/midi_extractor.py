"""
MIDI extraction from audio using pitch detection and onset analysis.

Uses basic-pitch (Spotify's ML model) for polyphonic audio,
with fallback to librosa pyin for monophonic content.

Includes advanced post-processing:
- Quantization to musical grid
- Key detection and filtering
- Pattern-based noise removal
- Note merging and velocity normalization
- ML-based ghost note detection (when available)
- ML-based timing correction (when available)
- ML-based dynamics processing (when available)
"""

import io
import base64
import tempfile
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List, Tuple, Dict, Any
from collections import Counter

import numpy as np

logger = logging.getLogger(__name__)

# Provenance tracking (optional but recommended)
_PROVENANCE_AVAILABLE = False
try:
    from .provenance import ProvenanceChain, DecisionDomain
    _PROVENANCE_AVAILABLE = True
    logger.info("Provenance tracking available")
except ImportError:
    logger.debug("Provenance module not available")

# ML MIDI refinement (optional, graceful degradation)
_ML_MIDI_AVAILABLE = False
try:
    from .ml.midi import (
        refine_midi_notes,
        filter_ghost_notes as ml_filter_ghost_notes,
        correct_timing as ml_correct_timing,
        process_dynamics as ml_process_dynamics,
    )
    _ML_MIDI_AVAILABLE = True
    logger.info("ML MIDI refinement available")
except ImportError:
    logger.debug("ML MIDI refinement not available, using heuristics")

# Musical constants
NOTES_IN_OCTAVE = 12
NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']

# Scale patterns (semitones from root)
SCALE_PATTERNS = {
    'major': [0, 2, 4, 5, 7, 9, 11],
    'minor': [0, 2, 3, 5, 7, 8, 10],
    'dorian': [0, 2, 3, 5, 7, 9, 10],
    'mixolydian': [0, 2, 4, 5, 7, 9, 10],
    'pentatonic_major': [0, 2, 4, 7, 9],
    'pentatonic_minor': [0, 3, 5, 7, 10],
}

# =============================================================================
# GENRE-SPECIFIC EXTRACTION PROFILES
# =============================================================================

# Stem types for per-stem configuration
STEM_TYPES = ['bass', 'drums', 'synth', 'pad', 'lead', 'vocals', 'other']

# Default extraction profile
DEFAULT_PROFILE = {
    'onset_threshold': 0.5,
    'frame_threshold': 0.4,
    'min_note_ms': 50,
    'min_velocity': 30,
    'key_filter_strictness': 0.5,
    'isolated_min_neighbors': 1,
    'isolated_time_window': 2.0,
    'quantize_strength': 0.7,
    'merge_max_gap': 0.01,
}

# Synthwave-optimized profiles per stem type
SYNTHWAVE_PROFILES = {
    'bass': {
        'onset_threshold': 0.3,       # Lower - bass has soft attacks
        'frame_threshold': 0.2,       # Lower - sustain is important for sub-bass
        'min_note_ms': 50,            # Synth bass can have fast notes
        'min_velocity': 15,           # Keep quiet notes
        'key_filter_strictness': 0.3, # Bass often uses chromatic passing tones
        'isolated_min_neighbors': 0,  # Bass notes CAN be isolated
        'isolated_time_window': 3.0,
        'quantize_strength': 0.8,     # Bass should be tight
        'merge_max_gap': 0.03,        # Only merge notes <30ms apart (fragmented detections)
        'octave_shift_if_low': True,  # Shift up octave if detected too low
    },
    'drums': {
        # Drums use separate extraction, but these help for fallback
        'onset_threshold': 0.4,
        'frame_threshold': 0.3,
        'min_note_ms': 20,            # Drums are short
        'min_velocity': 25,
        'quantize_strength': 0.9,     # Drums should be quantized
    },
    'pad': {
        'onset_threshold': 0.45,      # Higher - reduce harmonic false positives
        'frame_threshold': 0.4,       # Higher - only strong sustained notes
        'min_note_ms': 300,           # Pads are long but not always 500ms
        'min_velocity': 25,           # Filter out very quiet harmonics
        'key_filter_strictness': 0.4, # Pads use extensions but filter obvious wrong notes
        'isolated_min_neighbors': 0,  # Single pad notes are valid
        'isolated_time_window': 5.0,
        'quantize_strength': 0.3,     # Pads are loose/free
        'merge_max_gap': 0.1,         # Merge sustained pad layers
        'filter_harmonics': True,     # Remove harmonic overtones (crucial for pads)
    },
    'lead': {
        'onset_threshold': 0.4,
        'frame_threshold': 0.3,
        'min_note_ms': 60,
        'min_velocity': 25,
        'key_filter_strictness': 0.5,
        'isolated_min_neighbors': 0,   # Lead lines can have isolated notes
        'isolated_time_window': 2.0,
        'quantize_strength': 0.5,      # Lead can be expressive
        'merge_max_gap': 0.08,         # Merge legato phrases
        'filter_delay_repeats': True,  # Remove echo/delay artifacts
    },
    'synth': {
        # Generic synth - optimized for synthwave arps and pads
        'onset_threshold': 0.35,
        'frame_threshold': 0.25,       # Lower to capture sustain
        'min_note_ms': 80,
        'min_velocity': 20,
        'key_filter_strictness': 0.3,  # Synths use extensions/chromatics
        'isolated_min_neighbors': 0,
        'isolated_time_window': 2.5,
        'quantize_strength': 0.6,
        'merge_max_gap': 0.1,          # Merge fragmented synth notes
    },
    'vocals': {
        'onset_threshold': 0.5,
        'frame_threshold': 0.4,
        'min_note_ms': 100,
        'min_velocity': 30,
        'key_filter_strictness': 0.7, # Vocals usually in key
        'isolated_min_neighbors': 1,
        'isolated_time_window': 1.5,
        'quantize_strength': 0.3,     # Vocals are expressive
        'merge_max_gap': 0.02,
    },
    'other': DEFAULT_PROFILE,
}

def get_extraction_profile(stem_type: str, genre: str = 'default') -> dict:
    """Get extraction parameters for a specific stem type and genre.

    Always applies stem-type specific profiles since they improve extraction
    quality regardless of genre. Genre-specific tweaks can be layered on top.
    """
    # Always use stem-specific profiles - they improve extraction for all genres
    profile = SYNTHWAVE_PROFILES.get(stem_type, DEFAULT_PROFILE).copy()

    # For non-synthwave genres, use slightly higher thresholds to reduce false positives
    if genre != 'synthwave' and stem_type in ('pad', 'synth'):
        # Pads and synths tend to over-detect harmonics - be stricter
        profile['onset_threshold'] = max(profile.get('onset_threshold', 0.5), 0.4)
        profile['frame_threshold'] = max(profile.get('frame_threshold', 0.4), 0.35)

    return profile


def detect_genre_from_audio(y: np.ndarray, sr: int) -> str:
    """
    Attempt to detect genre characteristics from audio.
    Returns 'synthwave' if audio has synthwave-like characteristics.
    """
    import librosa

    # Synthwave characteristics:
    # 1. Heavy low-end (sub bass)
    # 2. Lots of reverb (long decay)
    # 3. Tempo usually 80-120 BPM
    # 4. Dark/warm tone (less high frequency content)

    # Check frequency balance
    spec = np.abs(librosa.stft(y))
    freqs = librosa.fft_frequencies(sr=sr)

    # Low frequency energy (sub + bass: 20-200 Hz)
    low_mask = freqs < 200
    low_energy = spec[low_mask, :].sum()

    # Mid frequency energy (200-2000 Hz)
    mid_mask = (freqs >= 200) & (freqs < 2000)
    mid_energy = spec[mid_mask, :].sum()

    # High frequency energy (2000+ Hz)
    high_mask = freqs >= 2000
    high_energy = spec[high_mask, :].sum()

    total_energy = low_energy + mid_energy + high_energy
    if total_energy == 0:
        return 'default'

    low_ratio = low_energy / total_energy
    high_ratio = high_energy / total_energy

    # Estimate tempo
    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
    if hasattr(tempo, '__iter__'):
        tempo = float(tempo[0]) if len(tempo) > 0 else 100.0

    # Synthwave: heavy low end, rolled off highs, moderate tempo
    is_synthwave = (
        low_ratio > 0.25 and      # Strong bass
        high_ratio < 0.3 and      # Not too bright
        60 < tempo < 130          # Typical synthwave tempo range
    )

    if is_synthwave:
        logger.info(f"Detected synthwave characteristics (low={low_ratio:.2f}, high={high_ratio:.2f}, tempo={tempo:.0f})")
        return 'synthwave'

    return 'default'


def detect_optimal_extraction_method(y: np.ndarray, sr: int, stem_type: str = 'other') -> dict:
    """
    Auto-detect the optimal MIDI extraction method based on audio characteristics.

    Returns a dict with:
        - method: 'monophonic' or 'polyphonic'
        - reason: explanation for the choice
        - is_bass: whether audio appears to be bass
        - polyphony_estimate: estimated number of simultaneous voices
    """
    import librosa

    # Analyze frequency content
    spec = np.abs(librosa.stft(y))
    freqs = librosa.fft_frequencies(sr=sr)

    # Calculate energy in different bands
    sub_bass_mask = freqs < 100  # Sub-bass: 20-100 Hz
    bass_mask = (freqs >= 100) & (freqs < 300)  # Bass: 100-300 Hz
    mid_mask = (freqs >= 300) & (freqs < 2000)  # Mids: 300-2000 Hz
    high_mask = freqs >= 2000  # Highs: 2000+ Hz

    sub_bass_energy = spec[sub_bass_mask, :].sum() if sub_bass_mask.any() else 0
    bass_energy = spec[bass_mask, :].sum() if bass_mask.any() else 0
    mid_energy = spec[mid_mask, :].sum() if mid_mask.any() else 0
    high_energy = spec[high_mask, :].sum() if high_mask.any() else 0
    total_energy = sub_bass_energy + bass_energy + mid_energy + high_energy

    if total_energy == 0:
        return {'method': 'polyphonic', 'reason': 'No audio content', 'is_bass': False, 'polyphony_estimate': 1}

    low_ratio = (sub_bass_energy + bass_energy) / total_energy
    sub_bass_ratio = sub_bass_energy / total_energy

    # Check if this is bass-like audio
    is_bass_audio = (
        low_ratio > 0.6 or  # Dominated by low frequencies
        sub_bass_ratio > 0.3 or  # Strong sub-bass
        stem_type == 'bass'  # Explicitly marked as bass
    )

    # Estimate polyphony using chroma features
    # For truly monophonic audio, usually only 1-2 strong chroma bins at a time
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)

    # Count how many chroma bins are active (above threshold) per frame
    chroma_threshold = 0.3
    active_per_frame = (chroma > chroma_threshold).sum(axis=0)

    # Average and max simultaneous notes
    avg_polyphony = active_per_frame.mean()
    max_polyphony = active_per_frame.max()

    # Determine if monophonic
    # Bass is typically monophonic, but layered bass (multiple bass tracks) needs polyphonic
    # If avg_polyphony is high (>4), even bass should use polyphonic extraction
    is_truly_monophonic = avg_polyphony < 2.5

    if is_bass_audio and avg_polyphony < 6.0:
        # Simple bass - use monophonic for better pitch tracking
        method = 'monophonic'
        reason = f"Bass-like audio (low_ratio={low_ratio:.2f}, polyphony={avg_polyphony:.1f}) - using pYIN for better pitch tracking"
    elif is_bass_audio and avg_polyphony >= 6.0:
        # Layered/polyphonic bass (multiple bass tracks) - use polyphonic
        method = 'polyphonic'
        reason = f"Polyphonic bass detected (polyphony={avg_polyphony:.1f}) - using basic-pitch for layered bass"
    elif is_truly_monophonic and avg_polyphony < 2.0:
        method = 'monophonic'
        reason = f"Monophonic audio detected (avg_polyphony={avg_polyphony:.1f}) - using pYIN"
    else:
        method = 'polyphonic'
        reason = f"Polyphonic audio detected (avg_polyphony={avg_polyphony:.1f}) - using basic-pitch"

    logger.info(f"Auto-detection: {method} ({reason})")

    return {
        'method': method,
        'reason': reason,
        'is_bass': is_bass_audio,
        'polyphony_estimate': avg_polyphony,
        'low_ratio': low_ratio,
    }


@dataclass
class MIDIExtractionResult:
    """Result of MIDI extraction."""
    filename: str
    content: str  # base64 encoded MIDI data
    note_count: int
    duration_seconds: float
    tempo_bpm: float
    pitch_range: tuple[int, int]  # (lowest_note, highest_note)
    provenance: Optional[Dict[str, Any]] = field(default=None)  # Provenance tracking summary


def _sanitize_name(name: str) -> str:
    """Sanitize name for ASCII compatibility."""
    ascii_name = name.encode('ascii', 'ignore').decode('ascii').strip()
    return ascii_name if ascii_name else "Extracted MIDI"


# =============================================================================
# MIDI POST-PROCESSING PIPELINE
# =============================================================================

def detect_key(notes: List[Tuple[int, float, float, int]]) -> Tuple[int, str]:
    """
    Detect the musical key from a list of notes.

    Args:
        notes: List of (pitch, start, end, velocity) tuples

    Returns:
        (root_note, scale_type) - e.g., (0, 'major') for C major
    """
    if not notes:
        return (0, 'major')

    # Count pitch classes (0-11)
    pitch_classes = Counter()
    for pitch, start, end, vel in notes:
        duration = end - start
        # Weight by duration and velocity
        weight = duration * (vel / 127)
        pitch_classes[pitch % 12] += weight

    # Try each root and scale, find best match
    best_score = -1
    best_root = 0
    best_scale = 'major'

    for root in range(12):
        for scale_name, pattern in SCALE_PATTERNS.items():
            score = 0
            scale_notes = set((root + interval) % 12 for interval in pattern)

            for pc, weight in pitch_classes.items():
                if pc in scale_notes:
                    score += weight
                else:
                    score -= weight * 0.5  # Penalize out-of-scale notes

            if score > best_score:
                best_score = score
                best_root = root
                best_scale = scale_name

    logger.info(f"Detected key: {NOTE_NAMES[best_root]} {best_scale}")
    return (best_root, best_scale)


def filter_to_key(
    notes: List[Tuple[int, float, float, int]],
    root: int,
    scale: str,
    strictness: float = 0.8
) -> List[Tuple[int, float, float, int]]:
    """
    Filter notes to only include those in the detected key.

    Args:
        notes: List of (pitch, start, end, velocity) tuples
        root: Root note (0-11)
        scale: Scale type
        strictness: 0-1, how strict to be (1 = remove all out-of-key)

    Returns:
        Filtered notes
    """
    if scale not in SCALE_PATTERNS:
        return notes

    scale_notes = set((root + interval) % 12 for interval in SCALE_PATTERNS[scale])

    filtered = []
    for note in notes:
        pitch, start, end, vel = note
        pitch_class = pitch % 12

        if pitch_class in scale_notes:
            filtered.append(note)
        elif np.random.random() > strictness:
            # Keep some out-of-key notes for color (passing tones)
            filtered.append(note)

    logger.info(f"Key filter: {len(notes)} -> {len(filtered)} notes")
    return filtered


def quantize_notes(
    notes: List[Tuple[int, float, float, int]],
    tempo_bpm: float,
    grid_division: int = 16,
    strength: float = 1.0
) -> List[Tuple[int, float, float, int]]:
    """
    Quantize note start times to a musical grid.

    Args:
        notes: List of (pitch, start, end, velocity) tuples
        tempo_bpm: Tempo in BPM
        grid_division: Grid size (4=quarter, 8=eighth, 16=sixteenth)
        strength: 0-1, how much to quantize (1 = full snap)

    Returns:
        Quantized notes
    """
    if not notes:
        return notes

    seconds_per_beat = 60.0 / tempo_bpm
    grid_size = seconds_per_beat * (4.0 / grid_division)  # Grid size in seconds

    quantized = []
    for pitch, start, end, vel in notes:
        # Quantize start time
        grid_position = round(start / grid_size)
        quantized_start = grid_position * grid_size

        # Apply strength (blend between original and quantized)
        new_start = start + (quantized_start - start) * strength

        # Maintain note duration
        duration = end - start
        new_end = new_start + duration

        quantized.append((pitch, new_start, new_end, vel))

    logger.info(f"Quantized {len(notes)} notes to 1/{grid_division} grid")
    return quantized


def remove_isolated_notes(
    notes: List[Tuple[int, float, float, int]],
    min_neighbors: int = 1,
    time_window: float = 2.0
) -> List[Tuple[int, float, float, int]]:
    """
    Remove isolated notes that don't have nearby neighbors.
    Noise notes tend to be isolated; real musical notes come in groups.

    Args:
        notes: List of (pitch, start, end, velocity) tuples
        min_neighbors: Minimum number of notes within time_window
        time_window: Time window in seconds to look for neighbors

    Returns:
        Filtered notes
    """
    if len(notes) <= min_neighbors:
        return notes

    # Sort by start time
    sorted_notes = sorted(notes, key=lambda x: x[1])

    filtered = []
    for i, note in enumerate(sorted_notes):
        pitch, start, end, vel = note

        # Count neighbors within time window
        neighbors = 0
        for j, other in enumerate(sorted_notes):
            if i == j:
                continue
            other_start = other[1]
            if abs(other_start - start) <= time_window:
                neighbors += 1

        if neighbors >= min_neighbors:
            filtered.append(note)

    logger.info(f"Isolation filter: {len(notes)} -> {len(filtered)} notes")
    return filtered


def merge_overlapping_notes(
    notes: List[Tuple[int, float, float, int]],
    max_gap: float = 0.05
) -> List[Tuple[int, float, float, int]]:
    """
    Merge notes of the same pitch that overlap or are very close together.

    Args:
        notes: List of (pitch, start, end, velocity) tuples
        max_gap: Maximum gap in seconds to merge across

    Returns:
        Merged notes
    """
    if not notes:
        return notes

    # Group by pitch
    by_pitch = {}
    for note in notes:
        pitch = note[0]
        if pitch not in by_pitch:
            by_pitch[pitch] = []
        by_pitch[pitch].append(note)

    merged = []
    for pitch, pitch_notes in by_pitch.items():
        # Sort by start time
        pitch_notes.sort(key=lambda x: x[1])

        current = list(pitch_notes[0])  # [pitch, start, end, vel]

        for note in pitch_notes[1:]:
            _, start, end, vel = note

            # Check if this note overlaps or is close to current
            if start <= current[2] + max_gap:
                # Merge: extend end time, average velocity
                current[2] = max(current[2], end)
                current[3] = (current[3] + vel) // 2
            else:
                # Save current and start new
                merged.append(tuple(current))
                current = list(note)

        merged.append(tuple(current))

    logger.info(f"Merge: {len(notes)} -> {len(merged)} notes")
    return merged


def normalize_velocities(
    notes: List[Tuple[int, float, float, int]],
    min_vel: int = 60,
    max_vel: int = 110
) -> List[Tuple[int, float, float, int]]:
    """
    Normalize velocities to a consistent range.

    Args:
        notes: List of (pitch, start, end, velocity) tuples
        min_vel: Minimum output velocity
        max_vel: Maximum output velocity

    Returns:
        Notes with normalized velocities
    """
    if not notes:
        return notes

    velocities = [n[3] for n in notes]
    vel_min = min(velocities)
    vel_max = max(velocities)
    vel_range = vel_max - vel_min if vel_max > vel_min else 1

    normalized = []
    for pitch, start, end, vel in notes:
        # Normalize to 0-1, then scale to target range
        norm = (vel - vel_min) / vel_range
        new_vel = int(min_vel + norm * (max_vel - min_vel))
        normalized.append((pitch, start, end, new_vel))

    return normalized


def shift_octave_if_too_low(
    notes: List[Tuple[int, float, float, int]],
    min_reasonable_pitch: int = 28,  # E1 - lowest bass guitar note
    shift_amount: int = 12,  # One octave
) -> List[Tuple[int, float, float, int]]:
    """
    Shift notes up an octave if the average pitch is too low.

    This corrects for pitch detection that picks up sub-harmonics,
    common with deep sub-bass synths.

    Args:
        notes: List of (pitch, start, end, velocity) tuples
        min_reasonable_pitch: If average pitch is below this, shift up
        shift_amount: Semitones to shift (12 = one octave)

    Returns:
        Notes, possibly shifted up an octave
    """
    if not notes:
        return notes

    pitches = [n[0] for n in notes]
    avg_pitch = sum(pitches) / len(pitches)

    if avg_pitch < min_reasonable_pitch:
        logger.info(f"Bass pitch too low (avg={avg_pitch:.1f}), shifting up {shift_amount} semitones")
        return [(p + shift_amount, s, e, v) for p, s, e, v in notes]

    return notes


def filter_harmonic_overtones(
    notes: List[Tuple[int, float, float, int]],
    time_tolerance: float = 0.1,
    velocity_ratio: float = 0.8,
) -> List[Tuple[int, float, float, int]]:
    """
    Filter out harmonic overtones that are detected as separate notes.

    When a pad or sustained sound plays, the ML model often detects harmonics
    (octave, 5th, major 3rd) as separate notes. This filters them out by
    identifying notes that:
    1. Start at nearly the same time as a lower note
    2. Are at harmonic intervals (octave: +12, fifth: +7/+19, third: +4/+16)
    3. Have lower velocity than the fundamental

    Args:
        notes: List of (pitch, start, end, velocity) tuples
        time_tolerance: Max time difference to consider simultaneous (seconds)
        velocity_ratio: Harmonic must have velocity <= this ratio of fundamental

    Returns:
        Filtered notes with harmonics removed
    """
    if len(notes) < 2:
        return notes

    # Harmonic intervals in semitones (relative to fundamental)
    # Octave: 12, Perfect 5th: 7, Major 3rd: 4, and their octave equivalents
    HARMONIC_INTERVALS = {12, 19, 24, 7, 4, 16, 28, 31}

    # Sort by start time, then by pitch (lowest first)
    sorted_notes = sorted(notes, key=lambda x: (x[1], x[0]))

    # Group notes by approximate start time
    time_groups = []
    current_group = [sorted_notes[0]]

    for note in sorted_notes[1:]:
        if abs(note[1] - current_group[0][1]) <= time_tolerance:
            current_group.append(note)
        else:
            time_groups.append(current_group)
            current_group = [note]
    time_groups.append(current_group)

    # For each group, identify and remove harmonics
    filtered = []
    harmonics_removed = 0

    for group in time_groups:
        if len(group) == 1:
            filtered.append(group[0])
            continue

        # Sort group by pitch (lowest = likely fundamental)
        group_by_pitch = sorted(group, key=lambda x: x[0])

        # Mark which notes are likely harmonics
        is_harmonic = [False] * len(group_by_pitch)

        for i, note in enumerate(group_by_pitch):
            if is_harmonic[i]:
                continue

            pitch_i, start_i, end_i, vel_i = note

            # Check if higher notes are harmonics of this note
            for j in range(i + 1, len(group_by_pitch)):
                if is_harmonic[j]:
                    continue

                pitch_j, start_j, end_j, vel_j = group_by_pitch[j]
                interval = pitch_j - pitch_i

                # Check if interval matches a harmonic
                if interval in HARMONIC_INTERVALS:
                    # Check velocity - harmonic should be quieter
                    if vel_j <= vel_i * velocity_ratio:
                        is_harmonic[j] = True
                        harmonics_removed += 1

        # Keep non-harmonic notes
        for i, note in enumerate(group_by_pitch):
            if not is_harmonic[i]:
                filtered.append(note)

    if harmonics_removed > 0:
        logger.info(f"Harmonic filter: removed {harmonics_removed} overtones, {len(notes)} -> {len(filtered)} notes")

    return filtered


def filter_delay_repeats(
    notes: List[Tuple[int, float, float, int]],
    tempo_bpm: float,
    tolerance_ms: float = 30
) -> List[Tuple[int, float, float, int]]:
    """
    Filter out notes that appear to be delay/echo repeats.

    Delay repeats typically:
    - Same pitch as a preceding note
    - Lower velocity than the original
    - Spaced at rhythmic intervals (dotted 8th, quarter, etc.)

    Args:
        notes: List of (pitch, start, end, velocity) tuples
        tempo_bpm: Tempo in BPM
        tolerance_ms: Timing tolerance in milliseconds

    Returns:
        Filtered notes with delay repeats removed
    """
    if len(notes) < 2:
        return notes

    # Common delay times relative to beat
    beat_sec = 60.0 / tempo_bpm
    delay_intervals = [
        beat_sec * 0.75,   # Dotted 8th
        beat_sec * 0.5,    # 8th note
        beat_sec * 1.0,    # Quarter note
        beat_sec * 1.5,    # Dotted quarter
    ]
    tolerance_sec = tolerance_ms / 1000.0

    # Sort by start time
    sorted_notes = sorted(notes, key=lambda x: x[1])

    # Track which notes to keep
    keep = [True] * len(sorted_notes)

    for i, note in enumerate(sorted_notes):
        pitch, start, end, vel = note

        # Look for earlier notes at same pitch
        for j in range(i - 1, max(i - 10, -1), -1):  # Check last 10 notes
            prev_pitch, prev_start, prev_end, prev_vel = sorted_notes[j]

            if prev_pitch != pitch:
                continue

            time_diff = start - prev_start

            # Check if timing matches a delay interval
            for delay in delay_intervals:
                if abs(time_diff - delay) < tolerance_sec:
                    # Check if velocity is lower (typical of delay repeats)
                    if vel < prev_vel * 0.9:  # At least 10% quieter
                        keep[i] = False
                        break

            if not keep[i]:
                break

    filtered = [n for n, k in zip(sorted_notes, keep) if k]
    if len(filtered) < len(notes):
        logger.info(f"Delay filter: {len(notes)} -> {len(filtered)} notes")
    return filtered


def post_process_midi(
    midi_data,  # pretty_midi.PrettyMIDI object
    tempo_bpm: float,
    profile: dict = None,
    quantize: bool = True,
    detect_and_filter_key: bool = True,
    remove_isolated: bool = True,
    merge_notes: bool = True,
    normalize_vel: bool = True,
    filter_delays: bool = False,
    use_ml: bool = True,
    audio: np.ndarray = None,
    sr: int = 22050,
    track_provenance: bool = True,
) -> Optional[Dict[str, Any]]:
    """
    Apply full post-processing pipeline to MIDI data (modifies in place).

    Args:
        midi_data: pretty_midi.PrettyMIDI object
        tempo_bpm: Detected tempo
        profile: Extraction profile dict with parameters (overrides other args)
        quantize: Whether to quantize to grid
        detect_and_filter_key: Whether to detect key and filter
        remove_isolated: Whether to remove isolated noise notes
        merge_notes: Whether to merge overlapping notes
        normalize_vel: Whether to normalize velocities
        filter_delays: Whether to filter delay/echo repeats
        use_ml: Whether to use ML refinement when available
        audio: Audio array for ML context features (optional)
        sr: Sample rate for audio
        track_provenance: Whether to track processing decisions (default True)

    Returns:
        Optional provenance summary dict if track_provenance is True
    """
    # Initialize provenance chain if available
    provenance_chain = None
    if track_provenance and _PROVENANCE_AVAILABLE:
        provenance_chain = ProvenanceChain(domain=DecisionDomain.MIDI_EXTRACTION)
    # Use profile values if provided, otherwise use defaults
    if profile is None:
        profile = DEFAULT_PROFILE

    key_strictness = profile.get('key_filter_strictness', 0.5)
    iso_neighbors = profile.get('isolated_min_neighbors', 1)
    iso_window = profile.get('isolated_time_window', 2.0)
    quant_strength = profile.get('quantize_strength', 0.7)
    merge_gap = profile.get('merge_max_gap', 0.01)
    filter_delays = profile.get('filter_delay_repeats', filter_delays)
    octave_shift = profile.get('octave_shift_if_low', False)
    filter_harmonics = profile.get('filter_harmonics', False)

    # ML refinement settings
    use_ml_refinement = use_ml and _ML_MIDI_AVAILABLE

    for instrument in midi_data.instruments:
        if instrument.is_drum:
            continue  # Don't process drums with melodic pipeline

        # Convert to list of tuples for processing
        notes = [
            (n.pitch, n.start, n.end, n.velocity)
            for n in instrument.notes
        ]

        if not notes:
            continue

        original_count = len(notes)

        # 0a. Filter harmonic overtones (for pads - before other processing)
        if filter_harmonics:
            notes = filter_harmonic_overtones(notes)

        # 0b. Filter delay repeats (for lead synths)
        if filter_delays:
            notes = filter_delay_repeats(notes, tempo_bpm)

        # 1. Detect key and filter
        if detect_and_filter_key and key_strictness > 0:
            root, scale = detect_key(notes)
            notes = filter_to_key(notes, root, scale, strictness=key_strictness)

        # 2. Remove isolated notes (skip if min_neighbors is 0)
        if remove_isolated and iso_neighbors > 0:
            notes = remove_isolated_notes(notes, min_neighbors=iso_neighbors, time_window=iso_window)

        # 3. Quantize to grid
        if quantize and quant_strength > 0:
            notes = quantize_notes(notes, tempo_bpm, grid_division=16, strength=quant_strength)

        # 4. Merge overlapping notes
        if merge_notes and merge_gap > 0:
            notes = merge_overlapping_notes(notes, max_gap=merge_gap)

        # 5. Shift octave if bass is too low (sub-harmonic detection)
        if octave_shift:
            notes = shift_octave_if_too_low(notes)

        # 6. Normalize velocities
        if normalize_vel:
            notes = normalize_velocities(notes, min_vel=60, max_vel=110)

        # 7. ML refinement (when available)
        if use_ml_refinement and notes:
            try:
                # Detect key for ML context
                ml_key = None
                if detect_and_filter_key and key_strictness > 0:
                    ml_key = detect_key(notes)

                # Apply ML refinement pipeline with provenance tracking
                notes = refine_midi_notes(
                    notes,
                    audio=audio,
                    sr=sr,
                    tempo_bpm=tempo_bpm,
                    detected_key=ml_key,
                    instrument_type=instrument.name or "unknown",
                    filter_ghosts=True,
                    correct_time=quantize,
                    process_velocities=normalize_vel,
                    timing_strength=quant_strength,
                    velocity_range=(60, 110),
                    provenance_chain=provenance_chain,
                )
                logger.info(f"ML refinement applied: {len(notes)} notes")
            except Exception as e:
                logger.warning(f"ML refinement failed, using heuristic results: {e}")

        # Convert back to pretty_midi notes
        import pretty_midi
        instrument.notes = [
            pretty_midi.Note(
                velocity=vel,
                pitch=pitch,
                start=start,
                end=end
            )
            for pitch, start, end, vel in notes
        ]

        logger.info(f"Post-processing: {original_count} -> {len(instrument.notes)} notes")

    # Return provenance summary if tracking
    if provenance_chain:
        return provenance_chain.to_summary()
    return None


def extract_midi_polyphonic(
    audio_path: str,
    preset_name: str = "Extracted MIDI",
    onset_threshold: float = None,  # Use profile default if None
    frame_threshold: float = None,
    min_note_length_ms: float = None,
    stem_type: str = 'other',  # bass, drums, synth, pad, lead, vocals, other
    genre: str = None,  # None = auto-detect, 'synthwave', 'default'
) -> MIDIExtractionResult:
    """
    Extract MIDI notes from audio using basic-pitch (polyphonic).

    Uses genre-specific and stem-specific profiles for optimal extraction.

    Args:
        audio_path: Path to audio file
        preset_name: Name for the MIDI file
        onset_threshold: Threshold for note onset detection (0-1), None = use profile
        frame_threshold: Threshold for note frame detection (0-1), None = use profile
        min_note_length_ms: Minimum note length in milliseconds, None = use profile
        stem_type: Type of stem being extracted (bass, drums, synth, pad, lead, vocals, other)
        genre: Genre hint for parameter tuning (None = auto-detect)

    Returns:
        MIDIExtractionResult with base64-encoded MIDI data
    """
    from basic_pitch.inference import predict
    from basic_pitch import ICASSP_2022_MODEL_PATH
    import librosa
    import pretty_midi
    import soundfile as sf

    # Get audio duration and check for NaN/Inf values
    y, sr = librosa.load(audio_path, sr=22050, mono=True)
    duration = len(y) / sr

    # Auto-detect genre if not specified
    if genre is None:
        genre = detect_genre_from_audio(y, sr)
        logger.info(f"Auto-detected genre: {genre}")

    # Get extraction profile based on stem type and genre
    profile = get_extraction_profile(stem_type, genre)
    logger.info(f"Using profile for {stem_type}/{genre}: onset={profile.get('onset_threshold')}, frame={profile.get('frame_threshold')}")

    # Use profile values if parameters not explicitly set
    if onset_threshold is None:
        onset_threshold = profile.get('onset_threshold', 0.5)
    if frame_threshold is None:
        frame_threshold = profile.get('frame_threshold', 0.4)
    if min_note_length_ms is None:
        min_note_length_ms = profile.get('min_note_ms', 50)

    # Check if audio contains non-finite values (NaN, Inf)
    if y.size > 0 and not np.all(np.isfinite(y)):
        logger.warning("Audio contains non-finite values, cleaning...")
        y = np.nan_to_num(y, nan=0.0, posinf=0.0, neginf=0.0)
        # Ensure correct dtype for librosa
        y = y.astype(np.float32)

        # Save cleaned audio to temp file for basic-pitch
        clean_path = Path(tempfile.mktemp(suffix='.wav'))
        sf.write(str(clean_path), y, sr)
        audio_path_for_prediction = str(clean_path)
    else:
        audio_path_for_prediction = audio_path
        clean_path = None

    try:
        # Run basic-pitch prediction
        model_output, midi_data, note_events = predict(
            audio_path_for_prediction,
            model_or_model_path=ICASSP_2022_MODEL_PATH,
            onset_threshold=onset_threshold,
            frame_threshold=frame_threshold,
            minimum_note_length=min_note_length_ms / 1000.0,  # Convert to seconds
            midi_tempo=120.0,
        )
    finally:
        # Clean up temp file if we created one
        if clean_path and clean_path.exists():
            clean_path.unlink()

    # Get tempo estimate from librosa
    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
    if hasattr(tempo, '__iter__'):
        tempo = float(tempo[0]) if len(tempo) > 0 else 120.0
    tempo = float(tempo) if tempo > 0 else 120.0

    # Create a new MIDI file with the correct tempo
    # Basic-pitch outputs note times in seconds, so we copy them to a new file with correct tempo
    new_midi = pretty_midi.PrettyMIDI(initial_tempo=tempo)

    for old_inst in midi_data.instruments:
        new_inst = pretty_midi.Instrument(
            program=old_inst.program,
            is_drum=old_inst.is_drum,
            name=old_inst.name
        )
        # Copy all notes (times are in seconds, so they transfer correctly)
        for note in old_inst.notes:
            new_inst.notes.append(pretty_midi.Note(
                velocity=note.velocity,
                pitch=note.pitch,
                start=note.start,
                end=note.end
            ))
        new_midi.instruments.append(new_inst)

    # Use the new MIDI data with correct tempo
    midi_data = new_midi

    # Sanitize name for MIDI track
    ascii_name = _sanitize_name(preset_name)

    # Post-process: filter out low-velocity notes and very short notes
    min_velocity = profile.get('min_velocity', 30)
    min_duration_sec = min_note_length_ms / 1000.0

    for instrument in midi_data.instruments:
        # Filter notes by velocity and duration
        filtered_notes = [
            note for note in instrument.notes
            if note.velocity >= min_velocity
            and (note.end - note.start) >= min_duration_sec
        ]
        instrument.notes = filtered_notes

    # Apply comprehensive post-processing pipeline with profile
    logger.info(f"Applying post-processing pipeline for {stem_type}...")
    provenance_summary = post_process_midi(
        midi_data,
        tempo_bpm=tempo,
        profile=profile,
        quantize=True,
        detect_and_filter_key=True,
        remove_isolated=profile.get('isolated_min_neighbors', 1) > 0,
        merge_notes=True,
        normalize_vel=True,
        filter_delays=profile.get('filter_delay_repeats', False),
        use_ml=True,  # Enable ML refinement when available
        audio=y,
        sr=sr,
        track_provenance=True,
    )

    # Update track name in the MIDI data
    if midi_data.instruments:
        midi_data.instruments[0].name = ascii_name

    # Count notes and get pitch range
    all_notes = []
    for instrument in midi_data.instruments:
        all_notes.extend([note.pitch for note in instrument.notes])

    note_count = len(all_notes)
    if all_notes:
        pitch_range = (min(all_notes), max(all_notes))
    else:
        pitch_range = (0, 0)

    # Export to bytes
    with tempfile.NamedTemporaryFile(suffix='.mid', delete=False) as f:
        midi_data.write(f.name)
        with open(f.name, 'rb') as mf:
            midi_bytes = mf.read()
        Path(f.name).unlink()

    midi_b64 = base64.b64encode(midi_bytes).decode('ascii')

    # Safe filename
    safe_name = "".join(c if c.isalnum() or c in " -_" else "" for c in ascii_name).strip()
    if not safe_name:
        safe_name = "Extracted MIDI"

    return MIDIExtractionResult(
        filename=f"{safe_name}.mid",
        content=midi_b64,
        note_count=note_count,
        duration_seconds=duration,
        tempo_bpm=tempo,
        pitch_range=pitch_range,
        provenance=provenance_summary,
    )


def extract_midi_monophonic(
    audio_path: str,
    preset_name: str = "Extracted MIDI",
    min_note_duration_ms: float = 50,
    velocity_sensitivity: float = 1.0,
) -> MIDIExtractionResult:
    """
    Extract MIDI notes from monophonic audio using pyin.

    Args:
        audio_path: Path to audio file
        preset_name: Name for the MIDI file
        min_note_duration_ms: Minimum note duration in milliseconds
        velocity_sensitivity: Scale factor for velocity (1.0 = normal)

    Returns:
        MIDIExtractionResult with base64-encoded MIDI data
    """
    import librosa
    from midiutil import MIDIFile

    # Load audio
    y, sr = librosa.load(audio_path, sr=22050, mono=True)
    duration = len(y) / sr

    # Estimate tempo
    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
    if hasattr(tempo, '__iter__'):
        tempo = float(tempo[0]) if len(tempo) > 0 else 120.0
    tempo = float(tempo) if tempo > 0 else 120.0

    # Detect onsets
    onset_frames = librosa.onset.onset_detect(
        y=y, sr=sr, units='frames', backtrack=True,
        pre_max=3, post_max=3, pre_avg=3, post_avg=5, delta=0.07, wait=4
    )
    onset_times = librosa.frames_to_time(onset_frames, sr=sr)

    # Get onset strengths for velocity
    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    onset_strengths = onset_env[onset_frames] if len(onset_frames) > 0 else np.array([])

    if len(onset_strengths) > 0 and onset_strengths.max() > 0:
        velocities = 30 + (onset_strengths / onset_strengths.max()) * 97 * velocity_sensitivity
        velocities = np.clip(velocities, 30, 127).astype(int)
    else:
        velocities = np.full(len(onset_times), 80)

    # Pitch tracking with pyin
    f0, voiced_flag, voiced_prob = librosa.pyin(
        y, fmin=librosa.note_to_hz('C1'), fmax=librosa.note_to_hz('C7'),
        sr=sr, frame_length=2048, hop_length=512, fill_na=None
    )
    pitch_times = librosa.frames_to_time(np.arange(len(f0)), sr=sr, hop_length=512)

    # Convert frequency to MIDI note numbers
    midi_notes = np.full_like(f0, np.nan)
    valid_mask = ~np.isnan(f0) & (f0 > 0)
    midi_notes[valid_mask] = librosa.hz_to_midi(f0[valid_mask])

    # Create MIDI file
    midi = MIDIFile(1)
    track, channel, time = 0, 0, 0
    midi.addTempo(track, time, tempo)

    ascii_name = _sanitize_name(preset_name)
    midi.addTrackName(track, time, ascii_name)

    seconds_per_beat = 60.0 / tempo
    min_duration_beats = (min_note_duration_ms / 1000.0) / seconds_per_beat

    notes_added = []

    for i, onset_time in enumerate(onset_times):
        pitch_idx = np.searchsorted(pitch_times, onset_time)
        window_start = max(0, pitch_idx - 2)
        window_end = min(len(midi_notes), pitch_idx + 5)
        pitch_window = midi_notes[window_start:window_end]
        valid_pitches = pitch_window[~np.isnan(pitch_window)]

        if len(valid_pitches) == 0:
            continue

        note = int(round(np.median(valid_pitches)))
        note = max(0, min(127, note))

        if i < len(onset_times) - 1:
            note_duration_sec = onset_times[i + 1] - onset_time
        else:
            note_duration_sec = duration - onset_time

        onset_beat = onset_time / seconds_per_beat
        duration_beats = max(min_duration_beats, note_duration_sec / seconds_per_beat)
        velocity = int(velocities[i]) if i < len(velocities) else 80

        midi.addNote(track, channel, note, onset_beat, duration_beats, velocity)
        notes_added.append(note)

    # Export to bytes
    buffer = io.BytesIO()
    midi.writeFile(buffer)
    midi_bytes = buffer.getvalue()
    midi_b64 = base64.b64encode(midi_bytes).decode('ascii')

    safe_name = "".join(c if c.isalnum() or c in " -_" else "" for c in ascii_name).strip()
    if not safe_name:
        safe_name = "Extracted MIDI"

    if notes_added:
        pitch_range = (min(notes_added), max(notes_added))
    else:
        pitch_range = (0, 0)

    return MIDIExtractionResult(
        filename=f"{safe_name}.mid",
        content=midi_b64,
        note_count=len(notes_added),
        duration_seconds=duration,
        tempo_bpm=tempo,
        pitch_range=pitch_range,
        provenance=None,  # Monophonic extraction uses simpler heuristics
    )


def extract_midi(
    audio_path: str,
    preset_name: str = "Extracted MIDI",
    polyphonic: bool = None,  # None = auto-detect (recommended)
    min_note_duration_ms: float = 50,
    velocity_sensitivity: float = 1.0,
    quantize_to: Optional[int] = None,
    stem_type: str = 'other',  # bass, synth, pad, lead, vocals, other
    genre: str = None,  # None = auto-detect
) -> MIDIExtractionResult:
    """
    Extract MIDI notes from audio file.

    Auto-detects the best extraction method based on audio characteristics:
    - Bass-like audio (strong low frequencies) -> monophonic (pYIN) for better pitch tracking
    - Monophonic melodies -> monophonic (pYIN)
    - Polyphonic content (chords, pads) -> polyphonic (basic-pitch)

    Args:
        audio_path: Path to audio file
        preset_name: Name for the MIDI file
        polyphonic: Force extraction method. None = auto-detect (recommended),
                   True = force polyphonic (basic-pitch), False = force monophonic (pYIN)
        min_note_duration_ms: Minimum note duration in milliseconds
        velocity_sensitivity: Scale factor for velocity (monophonic only)
        quantize_to: Quantize to note division (monophonic only, e.g., 16 = 16th notes)
        stem_type: Type of stem for profile selection (bass, synth, pad, lead, vocals, other)
        genre: Genre hint for parameter tuning (None = auto-detect, 'synthwave', 'default')

    Returns:
        MIDIExtractionResult with base64-encoded MIDI data
    """
    import librosa

    # Auto-detect extraction method if not specified
    use_polyphonic = polyphonic
    detection_info = None

    if polyphonic is None:
        # Load audio for analysis
        y, sr = librosa.load(audio_path, sr=22050, mono=True, duration=30)  # Analyze first 30s

        # Auto-detect optimal method
        detection_info = detect_optimal_extraction_method(y, sr, stem_type)
        use_polyphonic = (detection_info['method'] == 'polyphonic')

        logger.info(f"MIDI extraction auto-detection for '{preset_name}': {detection_info['reason']}")

    if use_polyphonic:
        try:
            return extract_midi_polyphonic(
                audio_path,
                preset_name=preset_name,
                min_note_length_ms=min_note_duration_ms,
                stem_type=stem_type,
                genre=genre,
            )
        except Exception as e:
            # Fall back to monophonic if basic-pitch fails
            logger.warning(f"Polyphonic extraction failed, falling back to monophonic: {e}")

    return extract_midi_monophonic(
        audio_path,
        preset_name=preset_name,
        min_note_duration_ms=min_note_duration_ms,
        velocity_sensitivity=velocity_sensitivity,
    )


def extract_midi_from_array(
    y: np.ndarray,
    sr: int,
    preset_name: str = "Extracted MIDI",
    **kwargs
) -> MIDIExtractionResult:
    """
    Extract MIDI from audio array.

    Args:
        y: Audio time series
        sr: Sample rate
        preset_name: Name for the MIDI file
        **kwargs: Additional arguments passed to extract_midi

    Returns:
        MIDIExtractionResult with base64-encoded MIDI data
    """
    import soundfile as sf

    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
        sf.write(f.name, y, sr)
        result = extract_midi(f.name, preset_name, **kwargs)
        Path(f.name).unlink()

    return result


def extract_drum_midi(
    audio_path: str,
    preset_name: str = "Drums",
    min_velocity: int = 40,
    quantize_drums: bool = True,
) -> MIDIExtractionResult:
    """
    Extract drum MIDI from audio using advanced onset detection and spectral classification.

    Uses librosa's onset detection with band-pass filtering for each drum type,
    then applies quantization and ghost note removal for cleaner output.

    Classifies hits into kick, snare, and hihat based on spectral characteristics.
    Uses General MIDI drum mapping: kick=36, snare=38, closed hihat=42.

    Args:
        audio_path: Path to audio file (ideally an isolated drums stem)
        preset_name: Name for the MIDI file
        min_velocity: Minimum velocity threshold to include a hit
        quantize_drums: Whether to quantize to 16th note grid

    Returns:
        MIDIExtractionResult with drum MIDI data
    """
    import librosa
    from scipy import signal
    from midiutil import MIDIFile

    # GM Drum mapping
    KICK = 36
    SNARE = 38
    HIHAT_CLOSED = 42

    # Load audio
    y, sr = librosa.load(audio_path, sr=22050, mono=True)
    duration = len(y) / sr

    # Estimate tempo using multiple methods for robustness
    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
    if hasattr(tempo, '__iter__'):
        tempo = float(tempo[0]) if len(tempo) > 0 else 120.0
    tempo = float(tempo) if tempo > 0 else 120.0

    # Clamp tempo to reasonable range (60-200 BPM)
    if tempo < 60:
        tempo *= 2
    elif tempo > 200:
        tempo /= 2

    hop_length = 512

    # Use HPSS to separate percussive content
    y_harmonic, y_percussive = librosa.effects.hpss(y, margin=3.0)

    # Apply bandpass filters to isolate drum types
    def bandpass_filter(audio, low_freq, high_freq, sr):
        """Apply bandpass filter to audio with NaN handling."""
        # Handle non-finite values in input
        if audio.size > 0 and not np.all(np.isfinite(audio)):
            audio = np.nan_to_num(audio, nan=0.0, posinf=0.0, neginf=0.0)

        nyquist = sr / 2
        low = max(low_freq / nyquist, 0.001)
        high = min(high_freq / nyquist, 0.999)
        if low >= high:
            return audio

        try:
            b, a = signal.butter(4, [low, high], btype='band')
            filtered = signal.filtfilt(b, a, audio)
            # Ensure output is finite and correct dtype
            filtered = np.asarray(filtered, dtype=np.float32)
            if filtered.size > 0 and not np.all(np.isfinite(filtered)):
                filtered = np.nan_to_num(filtered, nan=0.0, posinf=0.0, neginf=0.0)
            return filtered
        except Exception as e:
            logger.warning(f"Bandpass filter failed: {e}")
            return np.asarray(audio, dtype=np.float32)

    # Ensure percussive signal is finite and has correct dtype
    if y_percussive.size > 0 and not np.all(np.isfinite(y_percussive)):
        y_percussive = np.nan_to_num(y_percussive, nan=0.0, posinf=0.0, neginf=0.0)
    y_percussive = np.asarray(y_percussive, dtype=np.float32)

    # Filter for each drum type
    # IMPORTANT: Use ORIGINAL signal for kick, not percussive!
    # 808/sub kicks are tonal with long decay, so HPSS puts them in harmonic component
    y_kick = bandpass_filter(y, 20, 120, sr)  # Sub/low frequencies from ORIGINAL
    y_snare = bandpass_filter(y_percussive, 150, 4000, sr)  # Mid frequencies
    y_hihat = bandpass_filter(y_percussive, 5000, 11000, sr)  # High frequencies

    # Use librosa's onset detection for each band
    def detect_onsets_advanced(y_band, threshold_multiplier=1.0, use_rms=False):
        """Detect onsets using librosa's onset detection.

        Args:
            y_band: Audio signal for this frequency band
            threshold_multiplier: Higher = more selective, lower = more sensitive
            use_rms: Use RMS envelope (better for soft kicks) instead of onset strength
        """
        # Ensure correct dtype for librosa
        y_band = np.asarray(y_band, dtype=np.float32)

        try:
            if use_rms:
                # RMS-based detection - better for soft/rounded transients
                rms = librosa.feature.rms(y=y_band, hop_length=hop_length)[0]
                # Compute derivative to find increases in energy
                rms_diff = np.diff(rms, prepend=0)
                rms_diff = np.maximum(rms_diff, 0)  # Only positive changes (attacks)
                onset_env = np.asarray(rms_diff, dtype=np.float64)
            else:
                onset_env = librosa.onset.onset_strength(
                    y=y_band, sr=sr, hop_length=hop_length,
                    aggregate=np.median
                )

            # Ensure onset envelope is finite and correct dtype
            onset_env = np.asarray(onset_env, dtype=np.float64)
            if onset_env.size > 0 and not np.all(np.isfinite(onset_env)):
                onset_env = np.nan_to_num(onset_env, nan=0.0, posinf=0.0, neginf=0.0)

            # Adaptive threshold based on signal statistics
            threshold = float(onset_env.mean() + onset_env.std() * threshold_multiplier)

            # Peak picking with backtracking for accurate timing
            # Reduced wait time (30ms) for faster electronic drum patterns
            onset_frames = librosa.onset.onset_detect(
                onset_envelope=onset_env, sr=sr, hop_length=hop_length,
                pre_max=2, post_max=2, pre_avg=3, post_avg=3,
                delta=threshold * 0.08, wait=int(sr * 0.03 / hop_length)  # 30ms min gap
            )
        except Exception as e:
            logger.warning(f"Onset detection failed: {e}")
            return [], []

        # Get onset strengths for velocity
        onset_strengths = []
        for frame in onset_frames:
            if 0 <= frame < len(onset_env):
                onset_strengths.append(onset_env[frame])
            else:
                onset_strengths.append(0.5)

        # Normalize strengths
        if onset_strengths:
            max_strength = max(onset_strengths) if max(onset_strengths) > 0 else 1
            onset_strengths = [s / max_strength for s in onset_strengths]

        return onset_frames, onset_strengths

    # Detect onsets in each band with different sensitivities (lower = more sensitive)
    # Kick uses RMS detection for soft/rounded kicks (common in synthwave)
    # Lower thresholds for electronic/synthwave drums which can be softer/more processed
    kick_frames, kick_strengths = detect_onsets_advanced(y_kick, threshold_multiplier=0.3, use_rms=True)
    snare_frames, snare_strengths = detect_onsets_advanced(y_snare, threshold_multiplier=0.4)
    hihat_frames, hihat_strengths = detect_onsets_advanced(y_hihat, threshold_multiplier=0.25)

    # Convert to times
    kick_times = librosa.frames_to_time(kick_frames, sr=sr, hop_length=hop_length)
    snare_times = librosa.frames_to_time(snare_frames, sr=sr, hop_length=hop_length)
    hihat_times = librosa.frames_to_time(hihat_frames, sr=sr, hop_length=hop_length)

    # Remove coincident hits (keep only the loudest when multiple drums hit together)
    min_gap = 0.025  # 25ms

    def filter_coincident_hits(primary_times, primary_strengths, secondary_times, secondary_strengths):
        """Keep only primary hits that aren't too close to secondary hits."""
        if len(primary_times) == 0 or len(secondary_times) == 0:
            return list(primary_times), list(primary_strengths)

        filtered_times = []
        filtered_strengths = []
        for t, s in zip(primary_times, primary_strengths):
            # Check if there's a stronger hit nearby in secondary
            is_dominated = False
            for t2, s2 in zip(secondary_times, secondary_strengths):
                if abs(t - t2) < min_gap and s2 > s * 1.2:  # 20% louder threshold
                    is_dominated = True
                    break
            if not is_dominated:
                filtered_times.append(t)
                filtered_strengths.append(s)

        return filtered_times, filtered_strengths

    # Snare often contains kick frequencies, so filter kick by snare
    kick_times, kick_strengths = filter_coincident_hits(
        kick_times, kick_strengths, snare_times, snare_strengths
    )

    # Quantize to grid if enabled
    if quantize_drums:
        seconds_per_beat = 60.0 / tempo
        grid_size = seconds_per_beat / 4  # 16th note grid

        def quantize_times(times):
            """Snap times to nearest grid position."""
            return [round(t / grid_size) * grid_size for t in times]

        kick_times = quantize_times(kick_times)
        snare_times = quantize_times(snare_times)
        hihat_times = quantize_times(hihat_times)

        # Remove duplicates after quantization
        def dedupe_times(times, strengths):
            seen = {}
            for t, s in zip(times, strengths):
                t_rounded = round(t, 4)  # Round to avoid floating point issues
                if t_rounded not in seen or s > seen[t_rounded][1]:
                    seen[t_rounded] = (t, s)
            times_out = [v[0] for v in seen.values()]
            strengths_out = [v[1] for v in seen.values()]
            return times_out, strengths_out

        kick_times, kick_strengths = dedupe_times(kick_times, kick_strengths)
        snare_times, snare_strengths = dedupe_times(snare_times, snare_strengths)
        hihat_times, hihat_strengths = dedupe_times(hihat_times, hihat_strengths)

    # Create MIDI file
    midi = MIDIFile(1)
    track, channel, time = 0, 9, 0  # Channel 10 (9 in 0-indexed) is drums in GM
    midi.addTempo(track, time, tempo)

    ascii_name = _sanitize_name(preset_name)
    midi.addTrackName(track, time, ascii_name)

    seconds_per_beat = 60.0 / tempo
    note_duration = 0.1  # Short duration for drum hits

    notes_added = []

    def add_drum_hits(times, strengths, note, drum_name):
        """Add drum hits to MIDI."""
        added = 0
        for t, s in zip(times, strengths):
            velocity = int(min_velocity + s * (127 - min_velocity))
            velocity = min(127, max(min_velocity, velocity))
            beat_time = t / seconds_per_beat
            midi.addNote(track, channel, note, beat_time, note_duration, velocity)
            notes_added.append(note)
            added += 1
        logger.info(f"Added {added} {drum_name} hits")

    # Add all drum hits
    add_drum_hits(kick_times, kick_strengths, KICK, "kick")
    add_drum_hits(snare_times, snare_strengths, SNARE, "snare")
    add_drum_hits(hihat_times, hihat_strengths, HIHAT_CLOSED, "hihat")

    # Write to bytes
    with tempfile.NamedTemporaryFile(suffix='.mid', delete=False) as f:
        midi.writeFile(f)
        f.flush()
        with open(f.name, 'rb') as mf:
            midi_bytes = mf.read()
        Path(f.name).unlink()

    midi_b64 = base64.b64encode(midi_bytes).decode('ascii')

    safe_name = "".join(c if c.isalnum() or c in " -_" else "" for c in ascii_name).strip()
    if not safe_name:
        safe_name = "Drums"

    # Pitch range for drums (GM drum notes used)
    used_notes = list(set(notes_added)) if notes_added else [KICK]
    pitch_range = (min(used_notes), max(used_notes))

    logger.info(f"Drum MIDI: {len(notes_added)} total hits, tempo={tempo:.1f} BPM")

    return MIDIExtractionResult(
        filename=f"{safe_name}.mid",
        content=midi_b64,
        note_count=len(notes_added),
        duration_seconds=duration,
        tempo_bpm=tempo,
        pitch_range=pitch_range
    )
