"""Test MIDI sequence generator for preset rendering.

Creates standardized MIDI sequences that exercise presets to reveal
their timbral characteristics for fingerprinting.

The test sequence includes:
1. Chromatic scale - tests pitch response across range
2. Sustained chord - tests polyphony and harmonic content
3. Arpeggio pattern - tests attack and release characteristics
4. Single sustained note - tests sustain and modulation
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Tuple
import io

logger = logging.getLogger(__name__)

# MIDI note numbers
C3 = 48
C4 = 60
C5 = 72


@dataclass
class TestNote:
    """A note in the test sequence."""

    pitch: int          # MIDI note number
    start_beats: float  # Start time in beats
    duration_beats: float
    velocity: int = 100


def generate_test_sequence(
    tempo: float = 120.0,
    root_note: int = C4,
    duration_seconds: float = 5.0,
) -> List[TestNote]:
    """Generate a comprehensive test sequence.

    The sequence is designed to:
    - Exercise the full pitch range around the root
    - Test polyphonic capability with a chord
    - Reveal attack/decay characteristics
    - Show sustain and modulation behavior

    Args:
        tempo: BPM (affects beat timing but not total duration)
        root_note: Root note for the sequence (default C4)
        duration_seconds: Target duration (approximately)

    Returns:
        List of TestNote objects
    """
    notes = []
    current_beat = 0.0

    # 1. Chromatic scale (8 notes, 0.25 beats each = 2 beats total)
    # Tests pitch response
    for i in range(8):
        notes.append(TestNote(
            pitch=root_note + i,
            start_beats=current_beat,
            duration_beats=0.2,
            velocity=80 + i * 2,  # Slight velocity ramp
        ))
        current_beat += 0.25

    # Small gap
    current_beat = 2.5

    # 2. Sustained chord (C major, 2 beats)
    # Tests polyphony and harmonic content
    chord_notes = [root_note, root_note + 4, root_note + 7]  # C E G
    for pitch in chord_notes:
        notes.append(TestNote(
            pitch=pitch,
            start_beats=current_beat,
            duration_beats=2.0,
            velocity=90,
        ))

    current_beat = 5.0

    # 3. Arpeggio pattern (8 notes, staccato)
    # Tests attack and release
    arp_pattern = [0, 4, 7, 12, 7, 4, 0, -5]  # Up and down with octave
    for i, interval in enumerate(arp_pattern):
        notes.append(TestNote(
            pitch=root_note + interval,
            start_beats=current_beat,
            duration_beats=0.15,
            velocity=85,
        ))
        current_beat += 0.25

    current_beat = 7.5

    # 4. Single sustained note (long, for sustain/modulation)
    notes.append(TestNote(
        pitch=root_note,
        start_beats=current_beat,
        duration_beats=2.5,
        velocity=100,
    ))

    return notes


def generate_bass_test_sequence(
    tempo: float = 120.0,
    root_note: int = C3 - 12,  # C2
) -> List[TestNote]:
    """Generate test sequence optimized for bass presets.

    Designed to exercise the parameters that actually distinguish bass
    presets: filter envelope, sub-harmonics, glide / pitch stability,
    attack transient, and release tail. A pure chromatic run (the old
    sequence) under-discriminates — it produces near-identical mean-pooled
    mel spectra for presets whose differences live in dynamics-driven
    filter behaviour.

    Structure:
      1. Walking line on a scale (root, 5, b7, octave, 5, root) — exercises
         filter envelope retriggering and exposes glide / portamento.
      2. Velocity ramp on root note — reveals dynamics → filter / amp
         envelope routing (a key analog discriminator).
      3. Octave-down sustain — exposes sub-harmonic / sub-osc content.
      4. Octave-up staccato stab — exposes attack transient + release.
    """
    notes: List[TestNote] = []

    # 1. Walking line: 1 - 5 - b7 - 8 - 5 - 1   (minor pentatonic skeleton)
    walk = [0, 7, 10, 12, 7, 0]
    current_beat = 0.0
    for interval in walk:
        notes.append(TestNote(
            pitch=root_note + interval,
            start_beats=current_beat,
            duration_beats=0.45,
            velocity=100,
        ))
        current_beat += 0.5

    # 2. Velocity ramp on root (4 hits, 60 → 120) — filter env discriminator.
    current_beat = 3.25
    for vel in (60, 85, 105, 120):
        notes.append(TestNote(
            pitch=root_note,
            start_beats=current_beat,
            duration_beats=0.35,
            velocity=vel,
        ))
        current_beat += 0.5

    # 3. Sustained octave-down — exposes sub-bass content.
    notes.append(TestNote(
        pitch=root_note - 12,
        start_beats=5.5,
        duration_beats=2.5,
        velocity=100,
    ))

    # 4. Octave-up staccato stab — short, exposes attack/release.
    notes.append(TestNote(
        pitch=root_note + 12,
        start_beats=8.25,
        duration_beats=0.15,
        velocity=110,
    ))

    return notes


def generate_pad_test_sequence(
    tempo: float = 120.0,
    root_note: int = C4,
) -> List[TestNote]:
    """Generate test sequence optimized for pad presets.

    Pads are characterised by slow attack, long release, filter
    sweeps, and modulation tails. The test must hold notes long
    enough for those tails to develop and must overlap voicings
    so release tails are audible (the previous version had a 0.5-beat
    gap between chords that cut releases short on slow-release pads).

    Structure:
      1. Cm7 stack, 4 beats, overlapping into G7 with 1-beat overlap.
      2. G7 stack, 4 beats, slow release tail.
      3. Tail single root note held an extra 3 beats — pure release
         + modulation reveal.
    """
    notes: List[TestNote] = []

    # 1. Cm7 (C, Eb, G, Bb)
    for pitch in (root_note, root_note + 3, root_note + 7, root_note + 10):
        notes.append(TestNote(
            pitch=pitch,
            start_beats=0.0,
            duration_beats=4.5,  # overlaps next chord by 0.5 beats
            velocity=75,
        ))

    # 2. G7 (G, B, D, F) — entered with overlap
    for pitch in (root_note - 5, root_note - 1, root_note + 2, root_note + 5):
        notes.append(TestNote(
            pitch=pitch,
            start_beats=4.0,
            duration_beats=4.0,
            velocity=80,
        ))

    # 3. Tail root — exposes pure release / modulation behaviour.
    notes.append(TestNote(
        pitch=root_note,
        start_beats=8.0,
        duration_beats=3.0,
        velocity=70,
    ))

    return notes


def generate_lead_test_sequence(
    tempo: float = 120.0,
    root_note: int = C5,  # Higher range for lead
) -> List[TestNote]:
    """Generate test sequence optimized for lead presets.

    Leads live at the top of the mix; the test must exercise the high
    register, dynamic expression, and the sustain tail where vibrato
    / LFO modulation lives. Lines stay strictly monophonic (no overlap)
    so glide / portamento parameters retrigger cleanly.

    Structure:
      1. Ascending arpeggio (1-3-5-octave) — exposes high-register
         response + filter tracking.
      2. Expressive phrase (varied velocities) — reveals dynamics
         → filter / amp envelope routing.
      3. Octave jump → long sustained 5th — exposes vibrato / LFO /
         release.
    """
    notes: List[TestNote] = []

    # 1. Arpeggio sweep up: root, 3, 5, octave (each 0.25 beats)
    arp = [0, 4, 7, 12]
    current_beat = 0.0
    for interval in arp:
        notes.append(TestNote(
            pitch=root_note + interval,
            start_beats=current_beat,
            duration_beats=0.22,
            velocity=95,
        ))
        current_beat += 0.25

    # 2. Expressive phrase with velocity dynamics
    current_beat = 1.25
    phrase = [
        (0, 0.4, 80),
        (2, 0.25, 70),
        (4, 0.25, 95),
        (5, 0.65, 110),   # accent
        (4, 0.25, 75),
        (2, 0.4, 80),
        (0, 0.8, 105),
    ]
    for interval, dur, vel in phrase:
        notes.append(TestNote(
            pitch=root_note + interval,
            start_beats=current_beat,
            duration_beats=dur,
            velocity=vel,
        ))
        current_beat += dur + 0.1  # tiny mono gap

    # 3. Octave jump → long sustained 5th (LFO / vibrato discriminator)
    current_beat += 0.4
    notes.append(TestNote(
        pitch=root_note + 12,
        start_beats=current_beat,
        duration_beats=0.3,
        velocity=115,
    ))
    notes.append(TestNote(
        pitch=root_note + 7,
        start_beats=current_beat + 0.4,
        duration_beats=3.0,
        velocity=100,
    ))

    return notes


def generate_keys_test_sequence(
    tempo: float = 120.0,
    root_note: int = C4,
) -> List[TestNote]:
    """Generate test sequence optimized for keys / electric-piano presets.

    Keys patches sit between leads and pads — they need both the
    percussive attack of comping stabs and the harmonic richness of
    held voicings. Previously this `sound_type` fell through to the
    generic chromatic-plus-chord sequence, which under-discriminated.

    Structure:
      1. Two-handed I-IV-V-I comping stabs (Cmaj7 / Fmaj7 / G7 / Cmaj7).
      2. Held closing voicing — exposes electric-piano tine decay,
         clavinet-style sustain, and CP-80-style release.
      3. Single bass-register root note overlapping the held voicing
         — exposes split / layered behaviour.
    """
    notes: List[TestNote] = []

    # 1. Four comping stabs, voice-led: Cmaj7 → Fmaj7 → G7 → Cmaj7
    voicings = [
        (root_note, root_note + 4, root_note + 7, root_note + 11),   # Cmaj7
        (root_note - 7, root_note + 5, root_note + 9, root_note + 12),  # Fmaj7
        (root_note - 5, root_note + 2, root_note + 5, root_note + 11),  # G7
        (root_note, root_note + 4, root_note + 7, root_note + 11),   # Cmaj7
    ]
    current_beat = 0.0
    for chord in voicings:
        for pitch in chord:
            notes.append(TestNote(
                pitch=pitch,
                start_beats=current_beat,
                duration_beats=0.4,
                velocity=92,
            ))
        current_beat += 1.0

    # 2. Held closing Cmaj7 — release / tine-decay reveal.
    current_beat = 4.25
    for pitch in (root_note, root_note + 4, root_note + 7, root_note + 11):
        notes.append(TestNote(
            pitch=pitch,
            start_beats=current_beat,
            duration_beats=3.5,
            velocity=85,
        ))

    # 3. Bass root overlap — exposes any split / layer behaviour.
    notes.append(TestNote(
        pitch=root_note - 24,
        start_beats=current_beat + 0.25,
        duration_beats=3.0,
        velocity=100,
    ))

    return notes


def notes_to_midi_bytes(
    notes: List[TestNote],
    tempo: float = 120.0,
) -> bytes:
    """Convert test notes to MIDI file bytes.

    Args:
        notes: List of TestNote objects
        tempo: BPM

    Returns:
        MIDI file as bytes
    """
    import mido

    mid = mido.MidiFile(type=0)
    track = mido.MidiTrack()
    mid.tracks.append(track)

    # Set tempo
    track.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(tempo)))

    # Convert beats to ticks
    ticks_per_beat = mid.ticks_per_beat

    # Build event list
    events = []
    for note in notes:
        start_ticks = int(note.start_beats * ticks_per_beat)
        end_ticks = int((note.start_beats + note.duration_beats) * ticks_per_beat)

        events.append((start_ticks, "note_on", note.pitch, note.velocity))
        events.append((end_ticks, "note_off", note.pitch, 0))

    # Sort by time
    events.sort(key=lambda x: (x[0], 0 if x[1] == "note_off" else 1))

    # Convert to delta times
    last_tick = 0
    for tick, msg_type, pitch, velocity in events:
        delta = tick - last_tick
        if msg_type == "note_on":
            track.append(mido.Message("note_on", note=pitch, velocity=velocity, time=delta))
        else:
            track.append(mido.Message("note_off", note=pitch, velocity=0, time=delta))
        last_tick = tick

    # End of track
    track.append(mido.MetaMessage("end_of_track", time=0))

    # Write to bytes
    buffer = io.BytesIO()
    mid.save(file=buffer)
    return buffer.getvalue()


def get_test_sequence_for_type(
    sound_type: str,
    tempo: float = 120.0,
) -> Tuple[List[TestNote], bytes]:
    """Get appropriate test sequence for a sound type.

    Args:
        sound_type: bass, lead, pad, or other
        tempo: BPM

    Returns:
        Tuple of (notes, midi_bytes)
    """
    if sound_type == "bass":
        notes = generate_bass_test_sequence(tempo)
    elif sound_type == "pad":
        notes = generate_pad_test_sequence(tempo)
    elif sound_type == "lead":
        notes = generate_lead_test_sequence(tempo)
    elif sound_type == "keys":
        notes = generate_keys_test_sequence(tempo)
    else:
        notes = generate_test_sequence(tempo)

    midi_bytes = notes_to_midi_bytes(notes, tempo)
    return notes, midi_bytes


if __name__ == "__main__":
    import sys

    # Generate all test sequences
    for sound_type in ["default", "bass", "pad", "lead", "keys"]:
        if sound_type == "default":
            notes = generate_test_sequence()
        else:
            notes, _ = get_test_sequence_for_type(sound_type)

        print(f"\n{sound_type.upper()} test sequence ({len(notes)} notes):")
        total_beats = max(n.start_beats + n.duration_beats for n in notes)
        print(f"  Duration: {total_beats:.1f} beats")
        print(f"  Pitch range: {min(n.pitch for n in notes)} - {max(n.pitch for n in notes)}")
