#!/usr/bin/env python3
"""Audition stored analysis artifacts: synthesize a session's transcribed
MIDI stem and/or its chord timeline to WAV so a musician can HEAR what
the pipeline stored (nothing in the UI renders these yet).

Usage (from backend/):
    python3 scripts/audition_artifacts.py HISTORY_ID [--role guitar]
        [--chords] [--out DIR] [--play]

Renders:
    <out>/<id>_<role>_midi.wav      the transcribed notes (simple synth)
    <out>/<id>_chords.wav           the chord timeline as sustained pads

Pure numpy synth — no fluidsynth/soundfont dependency. Timbre is a
plucked-ish triangle for notes and soft pad for chords; pitch/timing are
exactly as stored, which is the point.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SR = 44100

NOTE_RE = re.compile(r"^([A-G][#b]?)")
SEMITONE = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4,
            "F": 5, "F#": 6, "Gb": 6, "G": 7, "G#": 8, "Ab": 8, "A": 9,
            "A#": 10, "Bb": 10, "B": 11}


def _find_entry(history_id: str) -> dict:
    hist = json.loads((Path(__file__).parent.parent / "data" / "history.json").read_text())
    items = hist if isinstance(hist, list) else hist.get("history", [])
    for it in items:
        if it.get("id", "").startswith(history_id):
            return it
    raise SystemExit(f"history id {history_id!r} not found")


def _midi_notes(entry: dict, role: str):
    import mido
    result = entry.get("result") or {}
    stem = (result.get("midi_stems") or {}).get(role)
    if not stem or not stem.get("content"):
        raise SystemExit(f"no MIDI stored for role {role!r}; "
                         f"available: {sorted((result.get('midi_stems') or {}))}")
    mid = mido.MidiFile(file=io.BytesIO(base64.b64decode(stem["content"])))
    tempo = 500000
    for tr in mid.tracks:
        for m in tr:
            if m.type == "set_tempo":
                tempo = m.tempo
                break
    notes = []
    for tr in mid.tracks:
        t = 0.0
        active = {}
        for m in tr:
            t += mido.tick2second(m.time, mid.ticks_per_beat, tempo)
            if m.type == "note_on" and m.velocity > 0:
                active[m.note] = (t, m.velocity)
            elif m.type == "note_off" or (m.type == "note_on" and m.velocity == 0):
                if m.note in active:
                    s, v = active.pop(m.note)
                    notes.append((s, t, m.note, v))
    return notes, stem.get("method", "?")


def _render_notes(notes, kind: str = "pluck") -> np.ndarray:
    end = max((off for _, off, _, _ in notes), default=1.0) + 1.0
    buf = np.zeros(int(end * SR))
    for onset, offset, pitch, vel in notes:
        dur = max(0.05, offset - onset)
        n = int(dur * SR)
        t = np.arange(n) / SR
        f = 440.0 * 2 ** ((pitch - 69) / 12)
        # triangle-ish: fundamental + odd harmonics
        w = (np.sin(2 * np.pi * f * t)
             + 0.35 * np.sin(2 * np.pi * 2 * f * t)
             + 0.15 * np.sin(2 * np.pi * 3 * f * t))
        if kind == "pluck":
            env = np.exp(-3.0 * t / max(dur, 0.15))
        else:  # pad
            a = min(0.05, dur / 4)
            env = np.minimum(1, t / a) * np.minimum(1, (dur - t) / max(a, 1e-3))
            env = np.clip(env, 0, 1)
        i0 = int(onset * SR)
        seg = w * env * (vel / 127.0) * 0.25
        buf[i0:i0 + n] += seg[:max(0, len(buf) - i0)]
    peak = np.abs(buf).max()
    return buf / peak * 0.85 if peak > 0 else buf


def _chord_midi(symbol: str) -> list:
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
    base = 48 + root  # C3-based voicing
    return [base + i for i in ints] + [base - 12]  # + bass root


def _render_chords(entry: dict) -> np.ndarray:
    result = entry.get("result") or {}
    chords = (result.get("chords") or
              (result.get("timeline") or {}).get("chords") or [])
    notes = []
    for c in chords:
        sym = c.get("symbol") or c.get("chord") or ""
        start = float(c.get("start", 0))
        end = float(c.get("end", start + 1))
        for p in _chord_midi(sym):
            notes.append((start, end, p, 70))
    if not notes:
        raise SystemExit("no chords stored on this entry")
    return _render_notes(notes, kind="pad")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("history_id")
    ap.add_argument("--role", default="guitar")
    ap.add_argument("--chords", action="store_true", help="also render chord timeline")
    ap.add_argument("--out", default="/tmp/audition")
    ap.add_argument("--play", action="store_true", help="afplay results")
    args = ap.parse_args()

    entry = _find_entry(args.history_id)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    made = []

    notes, method = _midi_notes(entry, args.role)
    wav = out / f"{entry['id'][:12]}_{args.role}_midi.wav"
    sf.write(str(wav), _render_notes(notes), SR)
    print(f"rendered {len(notes)} notes ({method}) -> {wav}")
    made.append(wav)

    if args.chords:
        cwav = out / f"{entry['id'][:12]}_chords.wav"
        sf.write(str(cwav), _render_chords(entry), SR)
        print(f"rendered chords -> {cwav}")
        made.append(cwav)

    if args.play:
        for w in made:
            print(f"playing {w.name} … (ctrl-c to skip)")
            subprocess.run(["afplay", str(w)])


if __name__ == "__main__":
    main()
