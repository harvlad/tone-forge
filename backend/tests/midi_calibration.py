#!/usr/bin/env python3
"""
MIDI Extraction Calibration Test Suite

Compares extracted MIDI against ground-truth MIDI files to measure extraction accuracy.
Run with: python -m tests.midi_calibration

Calibration tracks should have both audio stems and original MIDI files.
"""
import os
import sys
import json
import argparse
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Optional

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from mido import MidiFile


@dataclass
class StemResult:
    """Result for a single stem extraction."""
    stem_name: str
    stem_type: str
    original_notes: int
    extracted_notes: int
    diff: int
    diff_percent: float
    original_pitch_range: tuple
    extracted_pitch_range: tuple
    method_used: str  # 'monophonic' or 'polyphonic'
    status: str  # 'pass', 'warn', 'fail'


@dataclass
class TrackResult:
    """Result for a full track."""
    track_name: str
    bpm: int
    stems: list[StemResult]


@dataclass
class CalibrationResult:
    """Full calibration run result."""
    tracks: list[TrackResult]
    summary: dict


# Default calibration tracks (Will You Snail soundtrack)
DEFAULT_TRACKS = {
    'Final Encounter': {
        'bpm': 140,
        'stems': {
            'bass': 'FinalEncounter_Bass.wav',
            'lead': 'FinalEncounter_Lead.wav',
            'pad': 'FinalEncounter_Pads.wav',
        },
        'midi': 'FinalEncounter_140bpm.mid',
    },
    'Mr Dance': {
        'bpm': 120,
        'stems': {
            'bass': 'MrDance_Bass.wav',
            'lead': 'MrDance_Lead.wav',
            'pad': 'MrDance_Pads.wav',
        },
        'midi': 'MrDance_120bpm.mid',
    }
}


def find_calibration_tracks(base_path: str) -> dict:
    """Find calibration track folders in the given base path."""
    base = Path(base_path)
    tracks = {}

    for name, config in DEFAULT_TRACKS.items():
        # Look for folder patterns like "20 - Final Encounter" or just "Final Encounter"
        for pattern in [f"*{name}*", f"*{name.replace(' ', '_')}*"]:
            matches = list(base.glob(pattern))
            if matches:
                folder = matches[0]
                midi_path = folder / config['midi']
                if midi_path.exists():
                    stems = {}
                    for stem_type, stem_file in config['stems'].items():
                        stem_path = folder / stem_file
                        if stem_path.exists():
                            stems[stem_type] = str(stem_path)

                    if stems:
                        tracks[name] = {
                            'bpm': config['bpm'],
                            'stems': stems,
                            'midi': str(midi_path),
                        }
                break

    return tracks


def count_notes_by_stem(midi_path: str, stem_keyword: str) -> int:
    """Count notes in MIDI tracks matching the stem keyword."""
    mid = MidiFile(midi_path)
    total = 0
    keyword = stem_keyword.lower()

    for track in mid.tracks:
        track_name = (track.name or '').lower()
        if keyword in track_name:
            notes = sum(1 for msg in track if msg.type == 'note_on' and msg.velocity > 0)
            total += notes

    return total


def get_pitch_range(midi_path: str, stem_keyword: str) -> tuple:
    """Get pitch range from MIDI tracks matching the stem keyword."""
    mid = MidiFile(midi_path)
    all_notes = []
    keyword = stem_keyword.lower()

    for track in mid.tracks:
        track_name = (track.name or '').lower()
        if keyword in track_name:
            for msg in track:
                if msg.type == 'note_on' and msg.velocity > 0:
                    all_notes.append(msg.note)

    if all_notes:
        return (min(all_notes), max(all_notes))
    return (0, 0)


def extract_and_analyze(audio_path: str, stem_type: str, verbose: bool = False) -> tuple:
    """Extract MIDI from audio and return note count, pitch range, and method used."""
    from tone_forge import midi_extractor
    import importlib
    importlib.reload(midi_extractor)  # Ensure latest code

    result = midi_extractor.extract_midi(
        audio_path,
        preset_name=Path(audio_path).stem,
        stem_type=stem_type,
    )

    # Determine which method was used by checking detection
    import librosa
    y, sr = librosa.load(audio_path, sr=22050, mono=True, duration=30)
    detection = midi_extractor.detect_optimal_extraction_method(y, sr, stem_type)
    method = detection['method']

    pitch_range = result.pitch_range
    # Convert numpy types to Python types
    if hasattr(pitch_range[0], 'item'):
        pitch_range = (pitch_range[0].item(), pitch_range[1].item())

    return result.note_count, pitch_range, method


def run_calibration(
    tracks: dict,
    verbose: bool = False,
    output_json: Optional[str] = None,
) -> CalibrationResult:
    """Run calibration on all tracks and return results."""

    track_results = []
    total_pass = 0
    total_warn = 0
    total_fail = 0

    for track_name, track_info in tracks.items():
        if verbose:
            print(f"\n{'=' * 70}")
            print(f"=== {track_name.upper()} ({track_info['bpm']} BPM) ===")
            print('=' * 70)

        stem_results = []

        for stem_type, stem_path in track_info['stems'].items():
            if verbose:
                print(f"\n--- {Path(stem_path).name} (stem_type={stem_type}) ---")

            # Get original counts
            orig_notes = count_notes_by_stem(track_info['midi'], stem_type)
            orig_pitch = get_pitch_range(track_info['midi'], stem_type)

            # Extract MIDI
            ext_notes, ext_pitch, method = extract_and_analyze(stem_path, stem_type, verbose)

            # Calculate difference
            diff = ext_notes - orig_notes
            pct = (diff / orig_notes * 100) if orig_notes > 0 else 0

            # Determine status
            if abs(pct) <= 20:
                status = 'pass'
                symbol = '✓'
                total_pass += 1
            elif abs(pct) <= 35:
                status = 'warn'
                symbol = '⚠'
                total_warn += 1
            else:
                status = 'fail'
                symbol = '✗'
                total_fail += 1

            if verbose:
                print(f"  {symbol} Original: {orig_notes} | Extracted: {ext_notes} | "
                      f"Diff: {diff:+d} ({pct:+.1f}%) [{method}]")
                print(f"    Pitch: orig {orig_pitch} vs extracted {ext_pitch}")

            stem_results.append(StemResult(
                stem_name=Path(stem_path).name,
                stem_type=stem_type,
                original_notes=orig_notes,
                extracted_notes=ext_notes,
                diff=diff,
                diff_percent=round(pct, 1),
                original_pitch_range=orig_pitch,
                extracted_pitch_range=ext_pitch,
                method_used=method,
                status=status,
            ))

        track_results.append(TrackResult(
            track_name=track_name,
            bpm=track_info['bpm'],
            stems=stem_results,
        ))

    summary = {
        'total_stems': total_pass + total_warn + total_fail,
        'pass': total_pass,
        'warn': total_warn,
        'fail': total_fail,
        'pass_rate': round(total_pass / (total_pass + total_warn + total_fail) * 100, 1) if (total_pass + total_warn + total_fail) > 0 else 0,
    }

    result = CalibrationResult(tracks=track_results, summary=summary)

    if verbose:
        print(f"\n{'=' * 70}")
        print(f"SUMMARY: {summary['pass']} pass, {summary['warn']} warn, {summary['fail']} fail "
              f"({summary['pass_rate']}% pass rate)")
        print(f"Legend: ✓ = within 20% | ⚠ = within 35% | ✗ = over 35%")
        print('=' * 70)

    # Save JSON if requested
    if output_json:
        def serialize(obj):
            if isinstance(obj, (StemResult, TrackResult, CalibrationResult)):
                return asdict(obj)
            return obj

        with open(output_json, 'w') as f:
            json.dump(asdict(result), f, indent=2, default=serialize)
        if verbose:
            print(f"\nResults saved to: {output_json}")

    return result


def main():
    parser = argparse.ArgumentParser(description='MIDI Extraction Calibration Test')
    parser.add_argument('--base-path', '-p', default=os.path.expanduser('~/Downloads'),
                        help='Base path to search for calibration tracks')
    parser.add_argument('--output', '-o', help='Save results to JSON file')
    parser.add_argument('--quiet', '-q', action='store_true', help='Suppress verbose output')
    args = parser.parse_args()

    print("MIDI Extraction Calibration Test")
    print("=" * 70)

    # Find tracks
    tracks = find_calibration_tracks(args.base_path)

    if not tracks:
        print(f"No calibration tracks found in {args.base_path}")
        print("Expected folders containing stems and MIDI like:")
        for name in DEFAULT_TRACKS:
            print(f"  - *{name}*/")
        sys.exit(1)

    print(f"Found {len(tracks)} calibration tracks: {', '.join(tracks.keys())}")

    # Run calibration
    result = run_calibration(
        tracks,
        verbose=not args.quiet,
        output_json=args.output,
    )

    # Return exit code based on results
    if result.summary['fail'] > 0:
        sys.exit(1)
    elif result.summary['warn'] > 0:
        sys.exit(0)  # Warnings are acceptable
    else:
        sys.exit(0)


if __name__ == '__main__':
    main()
