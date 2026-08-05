"""Ground-truth MIDI normalization + cache.

Parser is a verbatim port of the VALIDATED benchmark's `load_gt_notes`
(mt3_vs_basic_pitch.py): first `set_tempo` wins, per-track note_on/off
pairing.  Known limitation (inherited deliberately so cached GT matches
the validated numbers): tempo changes after the first are ignored.
Bump GT_PARSER_VERSION if the parser ever changes — old cache entries
then become unreachable, never silently reused.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import mido
import pandas as pd

from . import config

GT_PARSER_VERSION = "v1"

GT_COLUMNS = ["pitch", "onset", "offset", "velocity", "channel", "note_index"]


def parse_gt_midi(midi_path: Path) -> pd.DataFrame:
    """Normalize a GT MIDI file into a note table (validated parser port)."""
    try:
        mid = mido.MidiFile(str(midi_path))
    except Exception:
        return pd.DataFrame(columns=GT_COLUMNS)
    tempo = 500000
    for track in mid.tracks:
        for msg in track:
            if msg.type == "set_tempo":
                tempo = msg.tempo
                break
    notes = []
    idx = 0
    for track in mid.tracks:
        current_time = 0.0
        active = {}
        for msg in track:
            current_time += mido.tick2second(msg.time, mid.ticks_per_beat, tempo)
            if msg.type == "note_on" and msg.velocity > 0:
                active[msg.note] = (current_time, msg.velocity, getattr(msg, "channel", 0))
            elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
                if msg.note in active:
                    start, vel, ch = active.pop(msg.note)
                    notes.append({
                        "pitch": int(msg.note),
                        "onset": float(start),
                        "offset": float(current_time),
                        "velocity": int(vel),
                        "channel": int(ch),
                        "note_index": idx,
                    })
                    idx += 1
    return pd.DataFrame(notes, columns=GT_COLUMNS)


def _gt_cache_path(midi_hash: str) -> Path:
    return config.GT_DIR / GT_PARSER_VERSION / f"{midi_hash}.parquet"


def get_gt_notes(midi_path: Path, midi_hash: str) -> pd.DataFrame:
    """Cached GT notes for a MIDI file (content-addressed by hash)."""
    path = _gt_cache_path(midi_hash)
    if path.exists():
        try:
            return pd.read_parquet(path)
        except Exception:
            path.unlink()  # corrupt — recompute below
    df = parse_gt_midi(midi_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".parquet.tmp")
    df.to_parquet(tmp, index=False)
    tmp.rename(path)
    return df


def gt_identity(midi_hash: str) -> str:
    """Identity string used in match-cache keys."""
    return f"{midi_hash}:{GT_PARSER_VERSION}"
