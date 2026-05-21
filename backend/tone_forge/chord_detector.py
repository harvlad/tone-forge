"""
Chord detection and harmonic analysis.

Detects chords from audio or MIDI data, identifies progressions,
and can group scattered notes into coherent chord voicings.
"""

import numpy as np
import logging
from typing import List, Tuple, Optional, Dict
from dataclasses import dataclass
from collections import Counter

logger = logging.getLogger(__name__)

# Chord templates (intervals from root)
CHORD_TEMPLATES = {
    'maj': [0, 4, 7],
    'min': [0, 3, 7],
    'dim': [0, 3, 6],
    'aug': [0, 4, 8],
    'maj7': [0, 4, 7, 11],
    'min7': [0, 3, 7, 10],
    'dom7': [0, 4, 7, 10],
    'dim7': [0, 3, 6, 9],
    'sus2': [0, 2, 7],
    'sus4': [0, 5, 7],
    'add9': [0, 4, 7, 14],
    'min9': [0, 3, 7, 10, 14],
    'maj9': [0, 4, 7, 11, 14],
}

NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']


@dataclass
class Chord:
    """Represents a detected chord."""
    root: int  # 0-11 (C=0, C#=1, etc.)
    quality: str  # 'maj', 'min', 'dom7', etc.
    start_time: float
    end_time: float
    confidence: float
    bass_note: Optional[int] = None  # For slash chords

    @property
    def name(self) -> str:
        """Get chord name like 'Cmaj7' or 'F#min'."""
        root_name = NOTE_NAMES[self.root]
        if self.quality == 'maj':
            return root_name
        elif self.quality == 'min':
            return f"{root_name}m"
        else:
            return f"{root_name}{self.quality}"

    @property
    def notes(self) -> List[int]:
        """Get pitch classes in this chord."""
        template = CHORD_TEMPLATES.get(self.quality, [0, 4, 7])
        return [(self.root + interval) % 12 for interval in template]


@dataclass
class ChordProgression:
    """A sequence of chords with timing."""
    chords: List[Chord]
    key_root: int
    key_quality: str  # 'major' or 'minor'
    tempo_bpm: float

    def __str__(self) -> str:
        chord_names = [c.name for c in self.chords]
        return f"{NOTE_NAMES[self.key_root]} {self.key_quality}: {' | '.join(chord_names)}"


def detect_chords_from_audio(
    y: np.ndarray,
    sr: int,
    hop_length: int = 512,
    min_chord_duration: float = 0.5,
) -> List[Chord]:
    """
    Detect chords from audio using chroma features.

    Args:
        y: Audio signal
        sr: Sample rate
        hop_length: Hop length for analysis
        min_chord_duration: Minimum chord duration in seconds

    Returns:
        List of detected Chord objects
    """
    import librosa

    # Compute chroma features
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=hop_length)

    # Smooth chroma to reduce noise
    chroma_smooth = librosa.decompose.nn_filter(
        chroma, aggregate=np.median, metric='cosine'
    )

    # Time array
    times = librosa.times_like(chroma, sr=sr, hop_length=hop_length)

    # Segment into chord regions using recurrence
    # Simple approach: find frames where chroma changes significantly
    chroma_diff = np.sum(np.abs(np.diff(chroma_smooth, axis=1)), axis=0)
    threshold = np.mean(chroma_diff) + np.std(chroma_diff)

    # Find segment boundaries
    boundaries = [0]
    min_frames = int(min_chord_duration * sr / hop_length)

    for i in range(1, len(chroma_diff)):
        if chroma_diff[i-1] > threshold and (i - boundaries[-1]) >= min_frames:
            boundaries.append(i)
    boundaries.append(chroma.shape[1])

    # Analyze each segment
    chords = []
    for i in range(len(boundaries) - 1):
        start_frame = boundaries[i]
        end_frame = boundaries[i + 1]

        # Get average chroma for this segment
        segment_chroma = np.mean(chroma_smooth[:, start_frame:end_frame], axis=1)

        # Match to chord template
        root, quality, confidence = _match_chord_template(segment_chroma)

        if confidence > 0.3:  # Minimum confidence threshold
            chord = Chord(
                root=root,
                quality=quality,
                start_time=times[start_frame],
                end_time=times[min(end_frame, len(times)-1)],
                confidence=confidence,
            )
            chords.append(chord)

    # Merge consecutive identical chords
    chords = _merge_consecutive_chords(chords)

    logger.info(f"Detected {len(chords)} chords from audio")
    return chords


def detect_chords_from_midi(
    notes: List[Tuple[int, float, float, int]],
    min_chord_duration: float = 0.25,
    min_notes_for_chord: int = 2,
) -> List[Chord]:
    """
    Detect chords from MIDI notes.

    Args:
        notes: List of (pitch, start, end, velocity) tuples
        min_chord_duration: Minimum duration for a chord
        min_notes_for_chord: Minimum simultaneous notes to form a chord

    Returns:
        List of detected Chord objects
    """
    if not notes:
        return []

    # Sort by start time
    sorted_notes = sorted(notes, key=lambda x: x[1])

    # Find time segments where notes overlap
    # Create a list of all note on/off events
    events = []
    for pitch, start, end, vel in sorted_notes:
        events.append((start, 'on', pitch, vel))
        events.append((end, 'off', pitch, vel))
    events.sort(key=lambda x: (x[0], x[1] == 'on'))  # Sort by time, offs before ons

    # Track active notes and detect chords
    active_notes = {}  # pitch -> velocity
    chords = []
    segment_start = 0.0
    last_pitches = set()

    for time, event_type, pitch, vel in events:
        if event_type == 'on':
            active_notes[pitch] = vel
        else:
            if pitch in active_notes:
                del active_notes[pitch]

        current_pitches = set(active_notes.keys())

        # Check if chord changed
        if current_pitches != last_pitches:
            # Save previous chord if valid
            if len(last_pitches) >= min_notes_for_chord and (time - segment_start) >= min_chord_duration:
                pitch_classes = [p % 12 for p in last_pitches]
                root, quality, confidence = _identify_chord_from_pitches(list(last_pitches))

                if confidence > 0.3:
                    # Find bass note (lowest pitch)
                    bass = min(last_pitches) % 12 if last_pitches else None

                    chord = Chord(
                        root=root,
                        quality=quality,
                        start_time=segment_start,
                        end_time=time,
                        confidence=confidence,
                        bass_note=bass if bass != root else None,
                    )
                    chords.append(chord)

            segment_start = time
            last_pitches = current_pitches

    # Merge consecutive identical chords
    chords = _merge_consecutive_chords(chords)

    logger.info(f"Detected {len(chords)} chords from MIDI")
    return chords


def group_notes_into_chords(
    notes: List[Tuple[int, float, float, int]],
    chords: List[Chord],
) -> List[Tuple[int, float, float, int]]:
    """
    Group scattered notes into coherent chord voicings.

    Takes messy extracted notes and aligns them to detected chords,
    removing spurious notes and ensuring clean chord voicings.

    Args:
        notes: Original extracted notes (pitch, start, end, velocity)
        chords: Detected chord progression

    Returns:
        Cleaned up notes aligned to chords
    """
    if not notes or not chords:
        return notes

    cleaned_notes = []

    for chord in chords:
        # Find notes that fall within this chord's time range
        chord_notes = [
            n for n in notes
            if n[1] < chord.end_time and n[2] > chord.start_time
        ]

        if not chord_notes:
            continue

        # Get the chord's pitch classes
        chord_pcs = set(chord.notes)

        # Filter to notes that match the chord
        matching_notes = []
        for pitch, start, end, vel in chord_notes:
            pc = pitch % 12
            if pc in chord_pcs:
                # Clip note to chord boundaries
                new_start = max(start, chord.start_time)
                new_end = min(end, chord.end_time)
                if new_end > new_start:
                    matching_notes.append((pitch, new_start, new_end, vel))

        # If we have matching notes, use them
        # Otherwise, generate chord tones from the chord
        if matching_notes:
            cleaned_notes.extend(matching_notes)
        else:
            # Generate basic chord voicing
            avg_vel = int(np.mean([n[3] for n in chord_notes])) if chord_notes else 80
            base_octave = 4  # Middle octave

            for interval in CHORD_TEMPLATES.get(chord.quality, [0, 4, 7])[:3]:  # Limit to triad
                pitch = (chord.root + interval) + (base_octave * 12)
                cleaned_notes.append((pitch, chord.start_time, chord.end_time, avg_vel))

    # Sort by start time
    cleaned_notes.sort(key=lambda x: (x[1], x[0]))

    logger.info(f"Grouped {len(notes)} notes into {len(cleaned_notes)} chord-aligned notes")
    return cleaned_notes


def _match_chord_template(chroma: np.ndarray) -> Tuple[int, str, float]:
    """
    Match a chroma vector to chord templates.

    Returns (root, quality, confidence)
    """
    best_match = (0, 'maj', 0.0)

    # Normalize chroma
    chroma_norm = chroma / (np.sum(chroma) + 1e-6)

    for root in range(12):
        for quality, intervals in CHORD_TEMPLATES.items():
            # Create template chroma
            template = np.zeros(12)
            for interval in intervals:
                template[(root + interval) % 12] = 1.0
            template /= np.sum(template)

            # Compute similarity (correlation)
            similarity = np.dot(chroma_norm, template)

            if similarity > best_match[2]:
                best_match = (root, quality, similarity)

    return best_match


def _identify_chord_from_pitches(pitches: List[int]) -> Tuple[int, str, float]:
    """
    Identify chord from a set of MIDI pitches.

    Returns (root, quality, confidence)
    """
    if not pitches:
        return (0, 'maj', 0.0)

    # Get pitch classes
    pitch_classes = set(p % 12 for p in pitches)

    best_match = (0, 'maj', 0.0)

    for root in range(12):
        for quality, intervals in CHORD_TEMPLATES.items():
            template_pcs = set((root + i) % 12 for i in intervals)

            # Calculate overlap
            overlap = len(pitch_classes & template_pcs)
            total = len(pitch_classes | template_pcs)

            if total > 0:
                similarity = overlap / total

                # Bonus for matching all template notes
                if template_pcs <= pitch_classes:
                    similarity += 0.2

                if similarity > best_match[2]:
                    best_match = (root, quality, min(similarity, 1.0))

    return best_match


def _merge_consecutive_chords(chords: List[Chord]) -> List[Chord]:
    """Merge consecutive chords with same root and quality."""
    if len(chords) < 2:
        return chords

    merged = [chords[0]]

    for chord in chords[1:]:
        last = merged[-1]
        if chord.root == last.root and chord.quality == last.quality:
            # Merge: extend the previous chord
            merged[-1] = Chord(
                root=last.root,
                quality=last.quality,
                start_time=last.start_time,
                end_time=chord.end_time,
                confidence=(last.confidence + chord.confidence) / 2,
                bass_note=last.bass_note,
            )
        else:
            merged.append(chord)

    return merged


def analyze_chord_progression(
    chords: List[Chord],
    key_root: int,
    key_quality: str = 'major',
) -> Dict:
    """
    Analyze a chord progression for music theory insights.

    Returns analysis including:
    - Roman numeral analysis
    - Common progressions detected
    - Harmonic rhythm
    """
    if not chords:
        return {'roman_numerals': [], 'progression_type': 'unknown'}

    # Convert chords to roman numerals
    roman_numerals = []
    scale = [0, 2, 4, 5, 7, 9, 11] if key_quality == 'major' else [0, 2, 3, 5, 7, 8, 10]

    for chord in chords:
        interval = (chord.root - key_root) % 12

        # Find closest scale degree
        closest_degree = min(range(7), key=lambda i: abs(scale[i] - interval))

        # Roman numeral based on quality
        numeral = ['I', 'II', 'III', 'IV', 'V', 'VI', 'VII'][closest_degree]
        if chord.quality in ['min', 'min7', 'min9']:
            numeral = numeral.lower()
        elif chord.quality in ['dim', 'dim7']:
            numeral = numeral.lower() + '°'
        elif chord.quality in ['dom7']:
            numeral += '7'
        elif chord.quality in ['maj7']:
            numeral += 'maj7'

        roman_numerals.append(numeral)

    # Detect common progressions
    progression_str = '-'.join(roman_numerals[:4])  # First 4 chords

    common_progressions = {
        'I-V-vi-IV': 'Pop/Rock (Axis)',
        'I-IV-V-I': 'Classic Rock',
        'ii-V-I': 'Jazz',
        'i-VI-III-VII': 'Epic/Cinematic',
        'I-vi-IV-V': '50s Doo-wop',
        'vi-IV-I-V': 'Emotional Pop',
    }

    progression_type = common_progressions.get(progression_str, 'Custom')

    # Calculate harmonic rhythm (average chord duration)
    if chords:
        durations = [c.end_time - c.start_time for c in chords]
        harmonic_rhythm = np.mean(durations)
    else:
        harmonic_rhythm = 0

    return {
        'roman_numerals': roman_numerals,
        'progression_type': progression_type,
        'harmonic_rhythm_sec': harmonic_rhythm,
        'chord_count': len(chords),
    }
