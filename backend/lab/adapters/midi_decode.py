"""Shared MIDI-file decode for adapters whose official inference writes a
MIDI file.  Identical logic to the validated MT3 decode (first set_tempo
wins, per-track pairing) so all adapters normalize the same way.
"""
from __future__ import annotations

from pathlib import Path
from typing import List

import mido


def decode_midi_file(midi_path: Path) -> List[dict]:
    midi_file = mido.MidiFile(str(midi_path))
    tempo = 500000
    for track in midi_file.tracks:
        for msg in track:
            if msg.type == "set_tempo":
                tempo = msg.tempo
                break
    notes = []
    for track in midi_file.tracks:
        current_time = 0.0
        active = {}
        for msg in track:
            current_time += mido.tick2second(msg.time, midi_file.ticks_per_beat, tempo)
            if msg.type == "note_on" and msg.velocity > 0:
                active[msg.note] = (current_time, msg.velocity)
            elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
                if msg.note in active:
                    start, vel = active.pop(msg.note)
                    notes.append({
                        "pitch": int(msg.note),
                        "onset": float(start),
                        "offset": float(current_time),
                        "velocity": int(vel),
                    })
    return notes
