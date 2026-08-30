#!/usr/bin/env python3
"""Octave Error Analysis Experiment.

Diagnostic phase for octave/fundamental errors:
1. Identify octave errors (pitch-class match, octave mismatch)
2. Extract acoustic features that might explain WHY
3. Compute oracle upper bound for octave correction
4. Test predictability from audio evidence
5. Compare per-note vs per-phrase vs per-stem correction

Does NOT implement correction - diagnostic only.
"""
import sys
from pathlib import Path
from typing import List, Tuple, Dict, Optional
import json
import argparse
import numpy as np
import librosa
from collections import defaultdict
from dataclasses import dataclass, field, asdict

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tone_forge.midi.gpu_extractor import extract_midi_lead_ensemble


@dataclass
class OctaveNoteData:
    """Per-note data for octave error analysis."""
    # Identifiers
    stem: str = ""
    inst_class: str = ""

    # Pitch info
    predicted_pitch: int = 0
    gt_pitch: int = 0
    octave_delta: int = 0  # predicted - gt, in octaves (positive = too high)
    pitch_class: int = 0  # 0-11
    is_octave_error: bool = False

    # Frequency info
    predicted_freq: float = 0.0
    gt_freq: float = 0.0

    # Timing
    onset: float = 0.0
    offset: float = 0.0
    duration: float = 0.0

    # Model info
    model_confidence: float = 0.0  # If available from basic_pitch

    # Harmonic energy features
    energy_at_f0: float = 0.0
    energy_at_half_f0: float = 0.0  # Subharmonic (octave down)
    energy_at_2f0: float = 0.0       # Second harmonic
    energy_at_3f0: float = 0.0       # Third harmonic
    energy_at_4f0: float = 0.0       # Fourth harmonic

    # Harmonic ratios
    ratio_half_to_f0: float = 0.0    # subharmonic strength
    ratio_2f0_to_f0: float = 0.0     # second harmonic strength
    ratio_3f0_to_f0: float = 0.0
    ratio_4f0_to_f0: float = 0.0

    # Spectral features
    spectral_centroid: float = 0.0
    spectral_rolloff: float = 0.0
    spectral_bandwidth: float = 0.0
    spectral_flatness: float = 0.0

    # Peak analysis
    strongest_peak_freq: float = 0.0
    strongest_peak_bin: int = 0
    num_significant_peaks: int = 0

    # Context
    local_polyphony: int = 0  # Number of concurrent notes

    # For analysis only
    inst_program: int = 0


def midi_to_freq(midi_pitch: int) -> float:
    """Convert MIDI pitch to frequency."""
    return 440.0 * (2.0 ** ((midi_pitch - 69) / 12.0))


def extract_harmonic_features(
    y: np.ndarray,
    sr: int,
    onset: float,
    offset: float,
    predicted_pitch: int,
    gt_pitch: int,
) -> Dict:
    """Extract harmonic energy features around predicted and GT fundamentals."""
    hop_length = 512
    n_fft = 4096  # Higher resolution for harmonic analysis

    # Extract relevant segment
    start_sample = int(onset * sr)
    end_sample = int(min(offset, onset + 2.0) * sr)  # Cap at 2 seconds
    segment = y[start_sample:end_sample]

    if len(segment) < n_fft:
        segment = np.pad(segment, (0, n_fft - len(segment)))

    # Compute STFT
    stft = np.abs(librosa.stft(segment, n_fft=n_fft, hop_length=hop_length))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=n_fft)

    # Average spectrum over note duration
    mean_spectrum = np.mean(stft, axis=1)

    # Predicted frequency and harmonics
    pred_f0 = midi_to_freq(predicted_pitch)
    gt_f0 = midi_to_freq(gt_pitch)

    def get_energy_at_freq(f: float, bandwidth_semitones: float = 0.5) -> float:
        """Get energy in band around frequency."""
        if f <= 0 or f > sr / 2:
            return 0.0
        f_low = f * (2.0 ** (-bandwidth_semitones / 12))
        f_high = f * (2.0 ** (bandwidth_semitones / 12))
        bin_low = max(0, np.searchsorted(freqs, f_low))
        bin_high = min(len(freqs) - 1, np.searchsorted(freqs, f_high))
        if bin_low >= bin_high:
            return 0.0
        return float(np.sum(mean_spectrum[bin_low:bin_high]))

    # Energy at GT fundamental and harmonics
    energy_f0 = get_energy_at_freq(gt_f0)
    energy_half = get_energy_at_freq(gt_f0 / 2) if gt_f0 / 2 > 20 else 0.0
    energy_2f0 = get_energy_at_freq(gt_f0 * 2)
    energy_3f0 = get_energy_at_freq(gt_f0 * 3)
    energy_4f0 = get_energy_at_freq(gt_f0 * 4)

    # Ratios (avoid division by zero)
    eps = 1e-10
    ratio_half = energy_half / (energy_f0 + eps)
    ratio_2f0 = energy_2f0 / (energy_f0 + eps)
    ratio_3f0 = energy_3f0 / (energy_f0 + eps)
    ratio_4f0 = energy_4f0 / (energy_f0 + eps)

    # Spectral features (using librosa on segment)
    if len(segment) > hop_length:
        try:
            centroid = float(np.mean(librosa.feature.spectral_centroid(y=segment, sr=sr)))
            rolloff = float(np.mean(librosa.feature.spectral_rolloff(y=segment, sr=sr)))
            bandwidth = float(np.mean(librosa.feature.spectral_bandwidth(y=segment, sr=sr)))
            flatness = float(np.mean(librosa.feature.spectral_flatness(y=segment)))
        except Exception:
            centroid = rolloff = bandwidth = flatness = 0.0
    else:
        centroid = rolloff = bandwidth = flatness = 0.0

    # Find strongest peak
    peak_idx = np.argmax(mean_spectrum)
    peak_freq = freqs[peak_idx] if peak_idx < len(freqs) else 0.0

    # Count significant peaks (above 10% of max)
    threshold = 0.1 * np.max(mean_spectrum)
    peaks = librosa.util.peak_pick(mean_spectrum, pre_max=3, post_max=3,
                                    pre_avg=3, post_avg=3, delta=threshold, wait=3)
    num_peaks = len(peaks)

    return {
        'energy_at_f0': energy_f0,
        'energy_at_half_f0': energy_half,
        'energy_at_2f0': energy_2f0,
        'energy_at_3f0': energy_3f0,
        'energy_at_4f0': energy_4f0,
        'ratio_half_to_f0': ratio_half,
        'ratio_2f0_to_f0': ratio_2f0,
        'ratio_3f0_to_f0': ratio_3f0,
        'ratio_4f0_to_f0': ratio_4f0,
        'spectral_centroid': centroid,
        'spectral_rolloff': rolloff,
        'spectral_bandwidth': bandwidth,
        'spectral_flatness': flatness,
        'strongest_peak_freq': peak_freq,
        'strongest_peak_bin': int(peak_idx),
        'num_significant_peaks': num_peaks,
    }


def load_midi_notes(midi_path: Path) -> List[Tuple[int, float, float]]:
    """Load GT notes from MIDI file."""
    import mido
    mid = mido.MidiFile(str(midi_path))

    tempo = 500000
    for track in mid.tracks:
        for msg in track:
            if msg.type == 'set_tempo':
                tempo = msg.tempo
                break

    notes = []
    for track in mid.tracks:
        current_time = 0
        active_notes = {}

        for msg in track:
            current_time += mido.tick2second(msg.time, mid.ticks_per_beat, tempo)
            if msg.type == 'note_on' and msg.velocity > 0:
                active_notes[msg.note] = current_time
            elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
                if msg.note in active_notes:
                    start = active_notes.pop(msg.note)
                    notes.append((msg.note, start, current_time))

    return notes


def match_notes_with_octave_tracking(
    gt_notes: List[Tuple[int, float, float]],
    ext_notes: List[Tuple[int, float, float]],
    onset_tol: float = 0.35,
) -> List[Dict]:
    """Match notes tracking octave errors specifically.

    Returns list of match info dicts with:
    - gt_idx, ext_idx
    - is_exact_match (same pitch)
    - is_octave_error (same pitch class, different octave)
    - octave_delta (in octaves, positive = predicted too high)
    """
    candidates = []
    for i, (ep, eo, ee) in enumerate(ext_notes):
        for j, (gp, go, ge) in enumerate(gt_notes):
            if abs(eo - go) > onset_tol:
                continue

            same_pc = (ep % 12) == (gp % 12)
            exact_match = ep == gp

            if exact_match or same_pc:
                octave_delta = (ep - gp) // 12
                candidates.append({
                    'gt_idx': j,
                    'ext_idx': i,
                    'exact_match': exact_match,
                    'octave_delta': octave_delta,
                    'onset_diff': abs(eo - go),
                })

    # Sort: prefer exact matches, then by onset proximity
    candidates.sort(key=lambda x: (not x['exact_match'], x['onset_diff']))

    gt_matched = set()
    ext_matched = set()
    matches = []

    for c in candidates:
        if c['gt_idx'] in gt_matched or c['ext_idx'] in ext_matched:
            continue
        gt_matched.add(c['gt_idx'])
        ext_matched.add(c['ext_idx'])
        matches.append(c)

    return matches


def find_stems(dataset_dir: Path, limit: int = 0) -> List[Dict]:
    """Find melodic stems in Slakh dataset."""
    import yaml

    MELODIC_CLASSES = {
        'Guitar', 'Piano', 'Bass', 'Brass', 'Synth Pad', 'Reed', 'Pipe',
        'Organ', 'Synth Lead', 'Strings', 'Strings (continued)',
        'Chromatic Percussion', 'Ethnic',
    }

    stems = []
    for subdir in ["train", "validation"]:
        split_dir = dataset_dir / subdir
        if not split_dir.exists():
            continue

        for track_dir in sorted(split_dir.iterdir()):
            if limit > 0 and len(stems) >= limit:
                break

            if not track_dir.is_dir():
                continue

            metadata_path = track_dir / "metadata.yaml"
            if not metadata_path.exists():
                continue

            with open(metadata_path) as f:
                metadata = yaml.safe_load(f)

            stems_info = metadata.get('stems', {})
            for stem_id, stem_meta in stems_info.items():
                if limit > 0 and len(stems) >= limit:
                    break

                inst_class = stem_meta.get('inst_class', '')
                if inst_class not in MELODIC_CLASSES:
                    continue

                audio_path = track_dir / "stems" / f"{stem_id}.flac"
                midi_path = track_dir / "MIDI" / f"{stem_id}.mid"

                if not audio_path.exists() or not midi_path.exists():
                    continue

                stems.append({
                    'track': track_dir.name,
                    'stem_id': stem_id,
                    'inst_class': inst_class,
                    'program': stem_meta.get('program_num', 0),
                    'audio_path': audio_path,
                    'midi_path': midi_path,
                })

    return stems


def compute_local_polyphony(
    onset: float,
    offset: float,
    all_notes: List[Tuple[int, float, float]],
) -> int:
    """Count concurrent notes at this time."""
    count = 0
    for p, o, e in all_notes:
        if o < offset and e > onset:
            count += 1
    return count


def run_octave_experiment(dataset_dir: Path, output_path: Path, limit: int = 50):
    """Run octave error analysis experiment."""
    stems = find_stems(dataset_dir, limit)
    print(f"Analyzing {len(stems)} stems for octave errors...", flush=True)

    all_note_data = []
    stem_summaries = []

    # Track overall stats
    total_matched = 0
    total_exact = 0
    total_octave_errors = 0

    for idx, stem in enumerate(stems):
        if (idx + 1) % 10 == 0:
            print(f"  [{idx+1}/{len(stems)}]...", flush=True)

        stem_name = f"{stem['track']}/{stem['stem_id']}"

        # Load ground truth
        try:
            gt_notes = load_midi_notes(stem['midi_path'])
        except Exception as e:
            print(f"  Warning: Could not load MIDI: {e}", flush=True)
            continue

        if not gt_notes:
            continue

        # Load audio for feature extraction first (faster failure)
        try:
            y, sr = librosa.load(str(stem['audio_path']), sr=22050, mono=True)
        except Exception as e:
            print(f"  Warning: Could not load audio: {e}", flush=True)
            continue

        # Run model
        print(f"Predicting MIDI for {stem['audio_path']}...", flush=True)
        try:
            midi_notes, _, _, _ = extract_midi_lead_ensemble(str(stem['audio_path']))
        except Exception as e:
            print(f"  Warning: Extraction failed: {e}", flush=True)
            continue

        # Convert MIDINote objects to (pitch, onset, offset) tuples
        ext_notes = [(n.pitch, n.start, n.end) for n in midi_notes]

        if not ext_notes:
            continue

        # Match notes
        matches = match_notes_with_octave_tracking(gt_notes, ext_notes)

        stem_exact = 0
        stem_octave_errors = 0
        stem_octave_up = 0
        stem_octave_down = 0

        for match in matches:
            gt_idx = match['gt_idx']
            ext_idx = match['ext_idx']

            gp, go, ge = gt_notes[gt_idx]
            ep, eo, ee = ext_notes[ext_idx]

            is_exact = match['exact_match']
            octave_delta = match['octave_delta']
            is_octave_error = not is_exact

            if is_exact:
                stem_exact += 1
            else:
                stem_octave_errors += 1
                if octave_delta > 0:
                    stem_octave_up += 1
                elif octave_delta < 0:
                    stem_octave_down += 1

            # Extract harmonic features
            features = extract_harmonic_features(y, sr, go, ge, ep, gp)

            # Local polyphony
            polyphony = compute_local_polyphony(go, ge, gt_notes)

            note_data = OctaveNoteData(
                stem=stem_name,
                inst_class=stem['inst_class'],
                predicted_pitch=int(ep),
                gt_pitch=int(gp),
                octave_delta=int(octave_delta),
                pitch_class=int(gp % 12),
                is_octave_error=bool(is_octave_error),
                predicted_freq=float(midi_to_freq(ep)),
                gt_freq=float(midi_to_freq(gp)),
                onset=float(go),
                offset=float(ge),
                duration=float(ge - go),
                local_polyphony=int(polyphony),
                inst_program=int(stem['program']),
                **features,
            )

            all_note_data.append(asdict(note_data))

        total_matched += len(matches)
        total_exact += stem_exact
        total_octave_errors += stem_octave_errors

        stem_summaries.append({
            'stem': stem_name,
            'inst_class': stem['inst_class'],
            'matched': len(matches),
            'exact': stem_exact,
            'octave_errors': stem_octave_errors,
            'octave_up': stem_octave_up,
            'octave_down': stem_octave_down,
            'octave_error_rate': stem_octave_errors / len(matches) if matches else 0,
        })

    # Save raw data
    print(f"\nSaved {len(all_note_data)} notes from {len(stem_summaries)} stems", flush=True)

    with open(output_path, 'w') as f:
        json.dump({
            'notes': all_note_data,
            'stems': stem_summaries,
        }, f, indent=2)

    # Print analysis
    print()
    print("=" * 70)
    print("OCTAVE ERROR OVERVIEW")
    print("=" * 70)
    print(f"Total matched notes: {total_matched}")
    print(f"Exact pitch match:   {total_exact} ({100*total_exact/total_matched:.1f}%)")
    print(f"Octave errors:       {total_octave_errors} ({100*total_octave_errors/total_matched:.1f}%)")
    print()

    # Directional analysis
    octave_up = sum(1 for n in all_note_data if n['octave_delta'] > 0)
    octave_down = sum(1 for n in all_note_data if n['octave_delta'] < 0)
    print(f"Octave UP errors:    {octave_up} ({100*octave_up/max(1,total_octave_errors):.1f}% of errors)")
    print(f"Octave DOWN errors:  {octave_down} ({100*octave_down/max(1,total_octave_errors):.1f}% of errors)")
    print()

    # Per-class breakdown
    print("=" * 70)
    print("PER-CLASS OCTAVE ERROR RATES")
    print("=" * 70)

    class_stats = defaultdict(lambda: {'total': 0, 'errors': 0, 'up': 0, 'down': 0})
    for n in all_note_data:
        cls = n['inst_class']
        class_stats[cls]['total'] += 1
        if n['is_octave_error']:
            class_stats[cls]['errors'] += 1
            if n['octave_delta'] > 0:
                class_stats[cls]['up'] += 1
            else:
                class_stats[cls]['down'] += 1

    print(f"{'Class':<25} {'Total':>7} {'Errors':>7} {'Rate':>7} {'Up':>6} {'Down':>6}")
    print("-" * 70)

    for cls in sorted(class_stats.keys(), key=lambda x: -class_stats[x]['errors']/max(1,class_stats[x]['total'])):
        s = class_stats[cls]
        rate = 100 * s['errors'] / max(1, s['total'])
        print(f"{cls:<25} {s['total']:>7} {s['errors']:>7} {rate:>6.1f}% {s['up']:>6} {s['down']:>6}")

    # ORACLE ANALYSIS
    print()
    print("=" * 70)
    print("ORACLE OCTAVE CORRECTION ANALYSIS")
    print("=" * 70)

    # If we could magically fix all octave errors, what would improve?
    # Current: only exact matches count for strict F1
    # Oracle: octave errors also become matches

    current_strict = total_exact
    oracle_strict = total_exact + total_octave_errors

    print(f"Current strict matches: {current_strict} / {total_matched} = {100*current_strict/total_matched:.1f}%")
    print(f"Oracle strict matches:  {oracle_strict} / {total_matched} = {100*oracle_strict/total_matched:.1f}%")
    print(f"Oracle improvement:     +{100*(oracle_strict-current_strict)/total_matched:.1f}%")
    print()

    # Per-class oracle potential
    print("Per-class oracle potential:")
    print(f"{'Class':<25} {'Current':>8} {'Oracle':>8} {'Delta':>8}")
    print("-" * 55)

    for cls in sorted(class_stats.keys(), key=lambda x: -class_stats[x]['errors']/max(1,class_stats[x]['total'])):
        s = class_stats[cls]
        current = s['total'] - s['errors']
        oracle = s['total']
        delta = s['errors']
        current_rate = 100 * current / max(1, s['total'])
        oracle_rate = 100.0
        delta_rate = 100 * delta / max(1, s['total'])
        print(f"{cls:<25} {current_rate:>7.1f}% {oracle_rate:>7.1f}% {delta_rate:>+7.1f}%")

    # MIDI range analysis
    print()
    print("=" * 70)
    print("MIDI RANGE ANALYSIS")
    print("=" * 70)

    # Bin by MIDI pitch range
    ranges = [
        ("Bass (21-48)", 21, 48),
        ("Low (49-60)", 49, 60),
        ("Mid (61-72)", 61, 72),
        ("High (73-84)", 73, 84),
        ("V.High (85-108)", 85, 108),
    ]

    print(f"{'Range':<20} {'Total':>7} {'Errors':>7} {'Rate':>7} {'Up':>6} {'Down':>6}")
    print("-" * 60)

    for name, lo, hi in ranges:
        range_notes = [n for n in all_note_data if lo <= n['gt_pitch'] <= hi]
        range_errors = [n for n in range_notes if n['is_octave_error']]
        range_up = sum(1 for n in range_errors if n['octave_delta'] > 0)
        range_down = sum(1 for n in range_errors if n['octave_delta'] < 0)
        rate = 100 * len(range_errors) / max(1, len(range_notes))
        print(f"{name:<20} {len(range_notes):>7} {len(range_errors):>7} {rate:>6.1f}% {range_up:>6} {range_down:>6}")

    # HARMONIC FEATURE ANALYSIS
    print()
    print("=" * 70)
    print("HARMONIC FEATURE ANALYSIS")
    print("=" * 70)

    # Compare features between correct and error notes
    correct_notes = [n for n in all_note_data if not n['is_octave_error']]
    error_notes = [n for n in all_note_data if n['is_octave_error']]

    features_to_compare = [
        ('ratio_half_to_f0', 'Subharmonic strength'),
        ('ratio_2f0_to_f0', 'Second harmonic'),
        ('ratio_3f0_to_f0', 'Third harmonic'),
        ('spectral_centroid', 'Spectral centroid'),
        ('spectral_flatness', 'Spectral flatness'),
        ('local_polyphony', 'Local polyphony'),
        ('duration', 'Note duration'),
    ]

    print(f"{'Feature':<25} {'Correct':>12} {'Errors':>12} {'Delta':>10}")
    print("-" * 65)

    for feat, label in features_to_compare:
        correct_vals = [n[feat] for n in correct_notes if n[feat] is not None]
        error_vals = [n[feat] for n in error_notes if n[feat] is not None]

        if correct_vals and error_vals:
            correct_mean = np.mean(correct_vals)
            error_mean = np.mean(error_vals)
            delta = error_mean - correct_mean
            print(f"{label:<25} {correct_mean:>12.3f} {error_mean:>12.3f} {delta:>+10.3f}")

    # Octave-down vs octave-up features
    print()
    print("Octave-DOWN vs Octave-UP notes:")
    down_notes = [n for n in error_notes if n['octave_delta'] < 0]
    up_notes = [n for n in error_notes if n['octave_delta'] > 0]

    print(f"{'Feature':<25} {'Down':>12} {'Up':>12} {'Delta':>10}")
    print("-" * 65)

    for feat, label in features_to_compare:
        down_vals = [n[feat] for n in down_notes if n[feat] is not None]
        up_vals = [n[feat] for n in up_notes if n[feat] is not None]

        if down_vals and up_vals:
            down_mean = np.mean(down_vals)
            up_mean = np.mean(up_vals)
            delta = up_mean - down_mean
            print(f"{label:<25} {down_mean:>12.3f} {up_mean:>12.3f} {delta:>+10.3f}")

    print()
    print(f"Data saved to: {output_path}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--dataset', type=str,
                       default='/Users/mattharvey/Sites/tone-forge/datasets/slakh2100_flac_redux')
    parser.add_argument('--output', type=str, default='/tmp/octave_oracle_data.json')
    parser.add_argument('--limit', type=int, default=50)
    args = parser.parse_args()

    run_octave_experiment(Path(args.dataset), Path(args.output), args.limit)
