#!/usr/bin/env python3
"""Align MIDI GT timing to match audio stem detection.

For each fixture, calculates the offset between:
- First note in GT MIDI lead track
- First note detected by lead extractor in audio

Then creates a modified MIDI file with lead track shifted to align.
Original MIDI files are backed up with .bak extension.

Usage:
    python scripts/align_midi_to_audio.py           # Show offsets (dry run)
    python scripts/align_midi_to_audio.py --apply   # Apply corrections
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import mido

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tone_forge.midi.gpu_extractor import extract_midi_lead_ensemble


SAMPLES_DIR = Path("/Users/mattharvey/Sites/tone-forge/samples")


def load_lead_notes_from_midi(midi_path: Path) -> list[dict]:
    """Extract lead track notes from MIDI file."""
    mid = mido.MidiFile(str(midi_path))
    tempo = 500000
    for track in mid.tracks:
        for msg in track:
            if msg.type == "set_tempo":
                tempo = msg.tempo
                break

    notes = []
    for track in mid.tracks:
        if "lead" not in track.name.lower():
            continue
        current_time = 0
        active = {}
        for msg in track:
            current_time += msg.time
            time_sec = mido.tick2second(current_time, mid.ticks_per_beat, tempo)
            if msg.type == "note_on" and msg.velocity > 0:
                active[msg.note] = time_sec
            elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
                if msg.note in active:
                    notes.append({"start": active.pop(msg.note)})
    return notes


def get_tempo(mid: mido.MidiFile) -> int:
    """Get tempo from MIDI file."""
    for track in mid.tracks:
        for msg in track:
            if msg.type == "set_tempo":
                return msg.tempo
    return 500000


def shift_track_timing(track: mido.MidiTrack, offset_ticks: int) -> mido.MidiTrack:
    """Shift entire track timing by offset_ticks.

    Shifts all events in the track by the specified offset.
    Positive offset_ticks = shift events EARLIER (subtract from absolute time)

    Approach: Convert to absolute time, shift, convert back to delta.
    """
    new_track = mido.MidiTrack()
    new_track.name = track.name

    # Convert to absolute times
    events = []
    abs_time = 0
    for msg in track:
        abs_time += msg.time
        events.append((abs_time, msg))

    # Shift all events
    shifted = []
    for abs_time, msg in events:
        new_abs = abs_time - offset_ticks  # Shift earlier
        if new_abs < 0:
            new_abs = 0  # Clamp to start
        shifted.append((new_abs, msg))

    # Sort by time (in case of clamping reordered anything)
    shifted.sort(key=lambda x: x[0])

    # Convert back to delta times
    prev_time = 0
    for abs_time, msg in shifted:
        delta = abs_time - prev_time
        new_track.append(msg.copy(time=delta))
        prev_time = abs_time

    return new_track


def calculate_offsets() -> dict[str, dict]:
    """Calculate timing offsets for all fixtures."""
    offsets = {}

    for track_dir in sorted(SAMPLES_DIR.iterdir()):
        if not track_dir.is_dir() or track_dir.name.startswith("."):
            continue

        midi_files = list(track_dir.glob("*bpm.mid"))
        if not midi_files:
            continue

        lead_files = list(track_dir.glob("*_Lead.wav")) + list(track_dir.glob("*_lead.wav"))
        if not lead_files:
            continue

        gt_notes = load_lead_notes_from_midi(midi_files[0])
        if not gt_notes:
            continue

        try:
            det_notes, _, _, _ = extract_midi_lead_ensemble(str(lead_files[0]))
            if not det_notes:
                continue

            gt_first = min(n["start"] for n in gt_notes)
            det_first = min(n.start for n in det_notes)
            offset = gt_first - det_first

            offsets[track_dir.name] = {
                "midi_file": midi_files[0],
                "offset_s": offset,
                "gt_first": gt_first,
                "det_first": det_first,
            }
        except Exception as e:
            print(f"Error processing {track_dir.name}: {e}")

    return offsets


def apply_offset(midi_path: Path, offset_s: float, dry_run: bool = True) -> None:
    """Apply timing offset to lead tracks in MIDI file."""
    mid = mido.MidiFile(str(midi_path))
    tempo = get_tempo(mid)

    # Convert offset from seconds to ticks
    offset_ticks = int(mido.second2tick(abs(offset_s), mid.ticks_per_beat, tempo))
    if offset_s < 0:
        offset_ticks = -offset_ticks

    # Find and shift lead tracks
    modified = False
    for i, track in enumerate(mid.tracks):
        if "lead" in track.name.lower():
            new_track = shift_track_timing(track, offset_ticks)
            mid.tracks[i] = new_track
            modified = True
            print(f"  Shifted track '{track.name}' by {offset_s:+.1f}s")

    if modified and not dry_run:
        # Backup original
        backup_path = midi_path.with_suffix(".mid.bak")
        if not backup_path.exists():
            shutil.copy(midi_path, backup_path)
            print(f"  Backed up to {backup_path.name}")

        # Save modified
        mid.save(str(midi_path))
        print(f"  Saved modified MIDI")


def main():
    parser = argparse.ArgumentParser(description="Align MIDI GT to audio stems")
    parser.add_argument("--apply", action="store_true", help="Apply corrections (default: dry run)")
    parser.add_argument("--threshold", type=float, default=1.0, help="Minimum offset to correct (seconds)")
    args = parser.parse_args()

    print("Calculating timing offsets...")
    offsets = calculate_offsets()

    print(f"\n{'Fixture':<25} | {'GT first':>10} | {'Det first':>10} | {'Offset':>10}")
    print("-" * 65)

    to_fix = []
    for name, info in sorted(offsets.items()):
        offset = info["offset_s"]
        needs_fix = abs(offset) > args.threshold
        marker = " <-- FIX" if needs_fix else ""
        print(f"{name:<25} | {info['gt_first']:>10.1f}s | {info['det_first']:>10.1f}s | {offset:>+10.1f}s{marker}")
        if needs_fix:
            to_fix.append((name, info))

    print(f"\nFixtures needing alignment: {len(to_fix)}")

    if to_fix and args.apply:
        print("\nApplying corrections...")
        for name, info in to_fix:
            print(f"\n{name}:")
            apply_offset(info["midi_file"], info["offset_s"], dry_run=False)
    elif to_fix and not args.apply:
        print("\nRun with --apply to apply corrections")


if __name__ == "__main__":
    main()
