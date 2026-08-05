"""Derived-audio extraction: turn a stored analysis into playable
note/chord event lists (no original audio involved).

Consumers: /api/debug/derived-audio (Jamn desktop Studio "Derived
Audio" section — the audible-evaluation surface for transcription and
harmony) and scripts/audition_artifacts.py.
"""
from __future__ import annotations

import base64
import io
import re
from typing import List, Optional

NOTE_RE = re.compile(r"^([A-G][#b]?)")
SEMITONE = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4,
            "F": 5, "F#": 6, "Gb": 6, "G": 7, "G#": 8, "Ab": 8, "A": 9,
            "A#": 10, "Bb": 10, "B": 11}


def notes_from_midi_b64(content_b64: str) -> List[dict]:
    """Decode a stored base64 MIDI stem into absolute-time note events."""
    import mido
    mid = mido.MidiFile(file=io.BytesIO(base64.b64decode(content_b64)))
    tempo = 500000
    for tr in mid.tracks:
        for m in tr:
            if m.type == "set_tempo":
                tempo = m.tempo
                break
    notes = []
    for tr in mid.tracks:
        t = 0.0
        active: dict = {}
        for m in tr:
            t += mido.tick2second(m.time, mid.ticks_per_beat, tempo)
            if m.type == "note_on" and m.velocity > 0:
                active[m.note] = (t, m.velocity)
            elif m.type == "note_off" or (m.type == "note_on" and m.velocity == 0):
                if m.note in active:
                    s, v = active.pop(m.note)
                    notes.append({"pitch": int(m.note), "onset": round(s, 4),
                                  "offset": round(t, 4), "velocity": int(v)})
    notes.sort(key=lambda n: (n["onset"], n["pitch"]))
    return notes


def chord_symbol_to_midi(symbol: str) -> List[int]:
    """Chord symbol -> simple pad voicing (C3-based triad/7th + bass root)."""
    m = NOTE_RE.match(symbol or "")
    if not m:
        return []
    root = SEMITONE[m.group(1)]
    rest = symbol[len(m.group(1)):]
    if rest.startswith(("m", "min")) and not rest.startswith(("maj", "M")):
        ints = [0, 3, 7]
    elif "dim" in rest:
        ints = [0, 3, 6]
    elif "aug" in rest or "+" in rest:
        ints = [0, 4, 8]
    elif "sus4" in rest:
        ints = [0, 5, 7]
    elif "sus2" in rest:
        ints = [0, 2, 7]
    else:
        ints = [0, 4, 7]
    if "maj7" in rest or "M7" in rest:
        ints = ints + [11]
    elif "7" in rest:
        ints = ints + [10]
    base = 48 + root
    return [base - 12] + [base + i for i in ints]


def derived_audio_payload(result: dict, role: Optional[str] = None) -> dict:
    """Build the wire payload for the derived-audio endpoint."""
    midi_stems = result.get("midi_stems") or {}
    roles = sorted(midi_stems.keys())
    role = role or ("guitar" if "guitar" in midi_stems else (roles[0] if roles else None))
    notes: List[dict] = []
    method = None
    if role and midi_stems.get(role, {}).get("content"):
        notes = notes_from_midi_b64(midi_stems[role]["content"])
        method = midi_stems[role].get("method")
    chords_src = (result.get("chords")
                  or (result.get("timeline") or {}).get("chords") or [])
    chords = []
    for c in chords_src:
        sym = c.get("symbol") or c.get("chord") or ""
        chords.append({
            "symbol": sym,
            "start": float(c.get("start", 0.0)),
            "end": float(c.get("end", 0.0)),
            "midi_notes": chord_symbol_to_midi(sym),
        })
    return {
        "role": role,
        "available_roles": roles,
        "method": method,
        "tempo_bpm": result.get("tempo_bpm"),
        "notes": notes,
        "chords": chords,
    }
