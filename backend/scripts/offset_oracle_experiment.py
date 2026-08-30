#!/usr/bin/env python3
"""Offset Oracle Experiment - Per-Note Analysis.

Phases 1-3 of adaptive offset investigation:
1. Oracle upper bound (per-stem and per-note)
2. Audio feature extraction for decay-ability
3. Predict whether spectral correction helps

Captures per-note data for feature analysis and routing development.
"""
import sys
from pathlib import Path
from typing import List, Tuple, Dict, Optional
import json
import numpy as np
import librosa
from collections import defaultdict
from dataclasses import dataclass, field, asdict

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tone_forge.midi.gpu_extractor import extract_midi_lead_ensemble


@dataclass
class NoteAnalysis:
    """Per-note analysis data."""
    # Identifiers
    stem: str = ""
    inst_class: str = ""
    pitch: int = 0

    # Timing (seconds)
    gt_onset: float = 0.0
    gt_offset: float = 0.0
    gt_duration: float = 0.0
    model_onset: float = 0.0
    model_offset: float = 0.0
    model_duration: float = 0.0
    spectral_offset: float = 0.0
    rms_offset: float = 0.0

    # Errors (seconds, positive = too long)
    model_offset_error: float = 0.0
    spectral_offset_error: float = 0.0
    rms_offset_error: float = 0.0

    # Duration ratios
    model_duration_ratio: float = 0.0
    spectral_duration_ratio: float = 0.0
    rms_duration_ratio: float = 0.0

    # Which method is best?
    best_method: str = ""  # "model", "spectral", "rms"
    model_is_good: bool = False  # 0.8-1.2 ratio
    spectral_is_good: bool = False
    rms_is_good: bool = False

    # Labels for prediction
    spectral_helped: bool = False  # spectral closer to GT than model
    spectral_hurt: bool = False    # spectral farther from GT
    rms_helped: bool = False
    rms_hurt: bool = False

    # Audio features (Phase 2)
    attack_slope: float = 0.0
    attack_duration: float = 0.0
    peak_energy: float = 0.0
    sustain_ratio: float = 0.0
    energy_decay_slope: float = 0.0
    spectral_decay_slope: float = 0.0
    transient_strength: float = 0.0
    pitch_stability: float = 0.0
    rms_stability: float = 0.0
    local_dynamic_range: float = 0.0
    time_to_next_onset: float = 0.0
    time_to_next_same_pitch: float = 0.0


def detect_offset_spectral(
    y: np.ndarray,
    sr: int,
    onset_time: float,
    model_offset_time: float,
    pitch: int,
    decay_threshold: float = 0.3,
    min_duration: float = 0.05,
) -> float:
    """Detect note offset using spectral energy around fundamental."""
    hop_length = 512
    n_fft = 2048
    frame_rate = sr / hop_length

    f0 = 440.0 * (2.0 ** ((pitch - 69) / 12.0))

    stft = np.abs(librosa.stft(y, n_fft=n_fft, hop_length=hop_length))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=n_fft)

    f_low = f0 * (2.0 ** (-1/12))
    f_high = f0 * (2.0 ** (1/12))

    bin_low = np.searchsorted(freqs, f_low)
    bin_high = np.searchsorted(freqs, f_high)
    bin_high = min(bin_high, stft.shape[0] - 1)

    band_energy = np.sum(stft[bin_low:bin_high+1, :], axis=0)

    onset_frame = int(onset_time * frame_rate)
    model_offset_frame = int(model_offset_time * frame_rate)

    onset_frame = max(0, min(onset_frame, len(band_energy) - 1))

    onset_window = min(5, len(band_energy) - onset_frame)
    onset_energy = np.mean(band_energy[onset_frame:onset_frame + onset_window])

    if onset_energy < 1e-6:
        return model_offset_time

    threshold_energy = onset_energy * decay_threshold
    min_frames = int(min_duration * frame_rate)

    search_start = onset_frame + min_frames
    search_end = min(model_offset_frame + int(0.5 * frame_rate), len(band_energy))

    for frame in range(search_start, search_end):
        if band_energy[frame] < threshold_energy:
            return frame / frame_rate

    return model_offset_time


def detect_offset_rms(
    y: np.ndarray,
    sr: int,
    onset_time: float,
    model_offset_time: float,
    decay_threshold: float = 0.3,
    min_duration: float = 0.05,
) -> float:
    """Detect note offset using RMS amplitude decay."""
    hop_length = 512
    frame_rate = sr / hop_length

    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=hop_length)[0]

    onset_frame = int(onset_time * frame_rate)
    model_offset_frame = int(model_offset_time * frame_rate)

    onset_frame = max(0, min(onset_frame, len(rms) - 1))

    onset_window = min(5, len(rms) - onset_frame)
    onset_rms = np.mean(rms[onset_frame:onset_frame + onset_window])

    if onset_rms < 1e-6:
        return model_offset_time

    threshold_rms = onset_rms * decay_threshold
    min_frames = int(min_duration * frame_rate)

    search_start = onset_frame + min_frames
    search_end = min(model_offset_frame + int(0.5 * frame_rate), len(rms))

    for frame in range(search_start, search_end):
        if frame >= len(rms):
            break
        if rms[frame] < threshold_rms:
            return frame / frame_rate

    return model_offset_time


def extract_audio_features(
    y: np.ndarray,
    sr: int,
    onset_time: float,
    model_offset_time: float,
    pitch: int,
    all_ext_notes: List[Tuple[int, float, float]],
    note_idx: int,
) -> Dict:
    """Extract audio features for decay-ability prediction."""
    hop_length = 512
    frame_rate = sr / hop_length
    n_fft = 2048

    onset_frame = int(onset_time * frame_rate)
    offset_frame = int(model_offset_time * frame_rate)

    # Compute features
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=hop_length)[0]

    onset_frame = max(0, min(onset_frame, len(rms) - 1))
    offset_frame = max(onset_frame + 1, min(offset_frame, len(rms) - 1))

    note_rms = rms[onset_frame:offset_frame]

    if len(note_rms) < 3:
        return {
            'attack_slope': 0.0,
            'attack_duration': 0.0,
            'peak_energy': 0.0,
            'sustain_ratio': 0.0,
            'energy_decay_slope': 0.0,
            'spectral_decay_slope': 0.0,
            'transient_strength': 0.0,
            'pitch_stability': 0.0,
            'rms_stability': 0.0,
            'local_dynamic_range': 0.0,
            'time_to_next_onset': 0.0,
            'time_to_next_same_pitch': 0.0,
        }

    # Peak and timing
    peak_idx = np.argmax(note_rms)
    peak_energy = float(note_rms[peak_idx])

    # Attack: onset to peak
    if peak_idx > 0:
        attack_slope = float((peak_energy - note_rms[0]) / (peak_idx / frame_rate))
        attack_duration = float(peak_idx / frame_rate)
    else:
        attack_slope = 0.0
        attack_duration = 0.0

    # Sustain ratio: mean energy after peak vs peak
    if peak_idx < len(note_rms) - 1:
        sustain_energy = np.mean(note_rms[peak_idx:])
        sustain_ratio = float(sustain_energy / peak_energy) if peak_energy > 1e-6 else 0.0
    else:
        sustain_ratio = 1.0

    # Energy decay slope: linear fit from peak to end
    if peak_idx < len(note_rms) - 2:
        decay_frames = np.arange(len(note_rms) - peak_idx)
        decay_values = note_rms[peak_idx:]
        if len(decay_frames) > 1:
            slope, _ = np.polyfit(decay_frames, decay_values, 1)
            energy_decay_slope = float(slope * frame_rate)  # per second
        else:
            energy_decay_slope = 0.0
    else:
        energy_decay_slope = 0.0

    # Transient strength: ratio of peak to mean
    mean_energy = np.mean(note_rms)
    transient_strength = float(peak_energy / mean_energy) if mean_energy > 1e-6 else 1.0

    # RMS stability: std / mean
    rms_stability = float(np.std(note_rms) / mean_energy) if mean_energy > 1e-6 else 0.0

    # Local dynamic range
    local_dynamic_range = float(np.max(note_rms) - np.min(note_rms))

    # Spectral decay slope (around fundamental)
    f0 = 440.0 * (2.0 ** ((pitch - 69) / 12.0))
    stft = np.abs(librosa.stft(y, n_fft=n_fft, hop_length=hop_length))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=n_fft)

    f_low = f0 * (2.0 ** (-1/12))
    f_high = f0 * (2.0 ** (1/12))
    bin_low = np.searchsorted(freqs, f_low)
    bin_high = min(np.searchsorted(freqs, f_high), stft.shape[0] - 1)

    band_energy = np.sum(stft[bin_low:bin_high+1, :], axis=0)
    note_band = band_energy[onset_frame:offset_frame]

    if len(note_band) > 2:
        band_peak_idx = np.argmax(note_band)
        if band_peak_idx < len(note_band) - 2:
            decay_frames = np.arange(len(note_band) - band_peak_idx)
            decay_values = note_band[band_peak_idx:]
            if len(decay_frames) > 1:
                slope, _ = np.polyfit(decay_frames, decay_values, 1)
                spectral_decay_slope = float(slope * frame_rate)
            else:
                spectral_decay_slope = 0.0
        else:
            spectral_decay_slope = 0.0
    else:
        spectral_decay_slope = 0.0

    # Pitch stability: pyin-based if available, else 0
    pitch_stability = 0.0  # Would need pyin analysis

    # Time to next onset (any pitch)
    time_to_next_onset = float('inf')
    time_to_next_same_pitch = float('inf')
    ext_pitch = all_ext_notes[note_idx][0]
    ext_onset = all_ext_notes[note_idx][1]

    for i, (p, o, e) in enumerate(all_ext_notes):
        if i == note_idx:
            continue
        if o > ext_onset:
            if o - ext_onset < time_to_next_onset:
                time_to_next_onset = o - ext_onset
            if p == ext_pitch and o - ext_onset < time_to_next_same_pitch:
                time_to_next_same_pitch = o - ext_onset

    if time_to_next_onset == float('inf'):
        time_to_next_onset = 10.0  # Large value
    if time_to_next_same_pitch == float('inf'):
        time_to_next_same_pitch = 10.0

    return {
        'attack_slope': attack_slope,
        'attack_duration': attack_duration,
        'peak_energy': peak_energy,
        'sustain_ratio': sustain_ratio,
        'energy_decay_slope': energy_decay_slope,
        'spectral_decay_slope': spectral_decay_slope,
        'transient_strength': transient_strength,
        'pitch_stability': pitch_stability,
        'rms_stability': rms_stability,
        'local_dynamic_range': local_dynamic_range,
        'time_to_next_onset': time_to_next_onset,
        'time_to_next_same_pitch': time_to_next_same_pitch,
    }


def load_midi_notes(midi_path: Path) -> List[Tuple[int, float, float]]:
    """Load GT notes from MIDI."""
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


def match_notes(
    gt_notes: List[Tuple[int, float, float]],
    ext_notes: List[Tuple[int, float, float]],
    onset_tol: float = 0.35,
) -> List[Tuple[int, int]]:
    """Match GT to extracted notes. Returns list of (gt_idx, ext_idx) pairs."""
    candidates = []
    for i, (ep, eo, ee) in enumerate(ext_notes):
        for j, (gp, go, ge) in enumerate(gt_notes):
            if abs(eo - go) > onset_tol:
                continue
            same_pc = (ep % 12) == (gp % 12)
            if ep == gp or same_pc:
                candidates.append((j, i, ep == gp, abs(eo - go)))

    candidates.sort(key=lambda x: (not x[2], x[3]))

    gt_matched = set()
    ext_matched = set()
    matches = []

    for gt_idx, ext_idx, _, _ in candidates:
        if gt_idx in gt_matched or ext_idx in ext_matched:
            continue
        gt_matched.add(gt_idx)
        ext_matched.add(ext_idx)
        matches.append((gt_idx, ext_idx))

    return matches


def find_stems(dataset_dir: Path, limit: int = 0) -> List[Dict]:
    """Find melodic stems."""
    import yaml

    MELODIC_CLASSES = {
        'Guitar', 'Piano', 'Bass', 'Brass', 'Synth Pad', 'Reed', 'Pipe',
        'Organ', 'Synth Lead', 'Strings', 'Strings (continued)',
        'Chromatic Percussion', 'Ethnic',
    }
    EXCLUDED = {'Drums', 'Sound effects', 'Percussive'}

    stems = []
    for subdir in ["train", "validation"]:
        split_dir = dataset_dir / subdir
        if not split_dir.exists():
            continue

        for track_dir in sorted(split_dir.iterdir()):
            if limit > 0 and len(stems) >= limit:
                break
            if not track_dir.is_dir() or not track_dir.name.startswith("Track"):
                continue

            metadata_path = track_dir / "metadata.yaml"
            if not metadata_path.exists():
                continue

            try:
                with open(metadata_path) as f:
                    metadata = yaml.safe_load(f)
            except Exception:
                continue

            for stem_id, stem_info in metadata.get('stems', {}).items():
                if limit > 0 and len(stems) >= limit:
                    break
                inst_class = stem_info.get('inst_class', '')
                if inst_class in MELODIC_CLASSES and inst_class not in EXCLUDED:
                    audio = track_dir / "stems" / f"{stem_id}.flac"
                    midi = track_dir / "MIDI" / f"{stem_id}.mid"
                    if not audio.exists():
                        audio = track_dir / "stems" / f"{stem_id}.wav"
                    if audio.exists() and midi.exists():
                        stems.append({
                            'track': track_dir.name,
                            'stem_id': stem_id,
                            'audio_path': audio,
                            'midi_path': midi,
                            'inst_class': inst_class,
                        })

    return stems


def is_good_ratio(ratio: float) -> bool:
    """Duration ratio in acceptable range."""
    return 0.8 <= ratio <= 1.2


def run_oracle_experiment(
    dataset_dir: Path,
    output_file: Path,
    limit: int = 50,
):
    """Run comprehensive per-note analysis."""
    stems = find_stems(dataset_dir, limit=limit)

    if not stems:
        print("No stems found!")
        return

    print(f"Analyzing {len(stems)} stems with per-note data...", flush=True)

    all_notes: List[Dict] = []
    stem_results = []

    for i, stem in enumerate(stems):
        if (i + 1) % 10 == 0:
            print(f"  [{i+1}/{len(stems)}]...", flush=True)

        try:
            gt_notes = load_midi_notes(stem['midi_path'])
        except Exception:
            continue

        if not gt_notes:
            continue

        try:
            y, sr = librosa.load(str(stem['audio_path']), sr=22050, mono=True)
        except Exception:
            continue

        try:
            ext_notes_raw, _, _, _ = extract_midi_lead_ensemble(str(stem['audio_path']))
            ext_notes = [(n.pitch, n.start, n.end) for n in ext_notes_raw]
        except Exception:
            continue

        if not ext_notes:
            continue

        # Match notes
        matches = match_notes(gt_notes, ext_notes)

        if not matches:
            continue

        stem_name = f"{stem['track']}/{stem['stem_id']}"

        # Per-note analysis
        stem_note_data = []

        for gt_idx, ext_idx in matches:
            gp, go, ge = gt_notes[gt_idx]
            ep, eo, ee = ext_notes[ext_idx]

            # Compute alternative offsets
            spectral_offset = detect_offset_spectral(y, sr, eo, ee, ep)
            rms_offset = detect_offset_rms(y, sr, eo, ee)

            # Durations
            gt_dur = ge - go
            model_dur = ee - eo
            spectral_dur = spectral_offset - eo
            rms_dur = rms_offset - eo

            if gt_dur < 0.01:
                continue

            # Ratios
            model_ratio = model_dur / gt_dur
            spectral_ratio = spectral_dur / gt_dur
            rms_ratio = rms_dur / gt_dur

            # Offset errors (positive = too late/long)
            model_offset_error = ee - ge
            spectral_offset_error = spectral_offset - ge
            rms_offset_error = rms_offset - ge

            # Which is best? (smallest absolute offset error)
            errors = {
                'model': abs(model_offset_error),
                'spectral': abs(spectral_offset_error),
                'rms': abs(rms_offset_error),
            }
            best_method = min(errors, key=errors.get)

            # Good ratio flags
            model_good = is_good_ratio(model_ratio)
            spectral_good = is_good_ratio(spectral_ratio)
            rms_good = is_good_ratio(rms_ratio)

            # Helped/hurt labels
            spectral_helped = abs(spectral_offset_error) < abs(model_offset_error) - 0.01
            spectral_hurt = abs(spectral_offset_error) > abs(model_offset_error) + 0.01
            rms_helped = abs(rms_offset_error) < abs(model_offset_error) - 0.01
            rms_hurt = abs(rms_offset_error) > abs(model_offset_error) + 0.01

            # Audio features
            features = extract_audio_features(y, sr, eo, ee, ep, ext_notes, ext_idx)

            note_data = {
                'stem': stem_name,
                'inst_class': stem['inst_class'],
                'pitch': int(ep),
                'gt_onset': float(go),
                'gt_offset': float(ge),
                'gt_duration': float(gt_dur),
                'model_onset': float(eo),
                'model_offset': float(ee),
                'model_duration': float(model_dur),
                'spectral_offset': float(spectral_offset),
                'rms_offset': float(rms_offset),
                'model_offset_error': float(model_offset_error),
                'spectral_offset_error': float(spectral_offset_error),
                'rms_offset_error': float(rms_offset_error),
                'model_duration_ratio': float(model_ratio),
                'spectral_duration_ratio': float(spectral_ratio),
                'rms_duration_ratio': float(rms_ratio),
                'best_method': best_method,
                'model_is_good': bool(model_good),
                'spectral_is_good': bool(spectral_good),
                'rms_is_good': bool(rms_good),
                'spectral_helped': bool(spectral_helped),
                'spectral_hurt': bool(spectral_hurt),
                'rms_helped': bool(rms_helped),
                'rms_hurt': bool(rms_hurt),
                **features,
            }

            stem_note_data.append(note_data)
            all_notes.append(note_data)

        if stem_note_data:
            stem_results.append({
                'stem': stem_name,
                'inst_class': stem['inst_class'],
                'n_notes': len(stem_note_data),
            })

    # Save per-note data
    with open(output_file, 'w') as f:
        json.dump({
            'notes': all_notes,
            'stems': stem_results,
        }, f, indent=2)

    print(f"\nSaved {len(all_notes)} notes from {len(stem_results)} stems", flush=True)

    # Phase 1: Oracle analysis
    print_oracle_analysis(all_notes, stem_results)


def print_oracle_analysis(notes: List[Dict], stems: List[Dict]):
    """Print Phase 1 oracle analysis."""
    if not notes:
        print("No data")
        return

    print(f"\n{'='*80}")
    print("PHASE 1: ORACLE UPPER BOUND ANALYSIS")
    print(f"{'='*80}")

    total = len(notes)

    # Baseline
    baseline_good = sum(1 for n in notes if n['model_is_good'])
    spectral_good = sum(1 for n in notes if n['spectral_is_good'])
    rms_good = sum(1 for n in notes if n['rms_is_good'])

    print(f"\nTotal matched notes: {total}")
    print(f"\nGood duration rate (0.8-1.2 ratio):")
    print(f"  Baseline (model):     {baseline_good:5d} / {total} = {baseline_good/total*100:5.1f}%")
    print(f"  Spectral-only:        {spectral_good:5d} / {total} = {spectral_good/total*100:5.1f}%")
    print(f"  RMS-only:             {rms_good:5d} / {total} = {rms_good/total*100:5.1f}%")

    # Per-class routing (oracle)
    by_class = defaultdict(list)
    for n in notes:
        by_class[n['inst_class']].append(n)

    # Find best method per class
    class_best_method = {}
    for cls, cls_notes in by_class.items():
        model_rate = sum(1 for n in cls_notes if n['model_is_good']) / len(cls_notes)
        spectral_rate = sum(1 for n in cls_notes if n['spectral_is_good']) / len(cls_notes)
        rms_rate = sum(1 for n in cls_notes if n['rms_is_good']) / len(cls_notes)

        rates = {'model': model_rate, 'spectral': spectral_rate, 'rms': rms_rate}
        best = max(rates, key=rates.get)
        class_best_method[cls] = best

    # Per-class oracle routing
    class_oracle_good = 0
    for n in notes:
        best_for_class = class_best_method[n['inst_class']]
        if best_for_class == 'model' and n['model_is_good']:
            class_oracle_good += 1
        elif best_for_class == 'spectral' and n['spectral_is_good']:
            class_oracle_good += 1
        elif best_for_class == 'rms' and n['rms_is_good']:
            class_oracle_good += 1

    print(f"  Per-class oracle:     {class_oracle_good:5d} / {total} = {class_oracle_good/total*100:5.1f}%")

    # Per-stem oracle
    stem_oracle_good = 0
    stem_notes_by_stem = defaultdict(list)
    for n in notes:
        stem_notes_by_stem[n['stem']].append(n)

    for stem_name, stem_notes in stem_notes_by_stem.items():
        # Find best method for this stem
        model_rate = sum(1 for n in stem_notes if n['model_is_good']) / len(stem_notes)
        spectral_rate = sum(1 for n in stem_notes if n['spectral_is_good']) / len(stem_notes)
        rms_rate = sum(1 for n in stem_notes if n['rms_is_good']) / len(stem_notes)

        rates = {'model': model_rate, 'spectral': spectral_rate, 'rms': rms_rate}
        best = max(rates, key=rates.get)

        for n in stem_notes:
            if best == 'model' and n['model_is_good']:
                stem_oracle_good += 1
            elif best == 'spectral' and n['spectral_is_good']:
                stem_oracle_good += 1
            elif best == 'rms' and n['rms_is_good']:
                stem_oracle_good += 1

    print(f"  Per-stem oracle:      {stem_oracle_good:5d} / {total} = {stem_oracle_good/total*100:5.1f}%")

    # Per-note oracle (theoretical best)
    note_oracle_good = 0
    for n in notes:
        if n['model_is_good'] or n['spectral_is_good'] or n['rms_is_good']:
            note_oracle_good += 1

    print(f"  Per-note oracle:      {note_oracle_good:5d} / {total} = {note_oracle_good/total*100:5.1f}%")

    # Improvement potential
    print(f"\nImprovement potential:")
    print(f"  Class routing vs baseline: +{(class_oracle_good - baseline_good)/total*100:.1f}%")
    print(f"  Stem routing vs baseline:  +{(stem_oracle_good - baseline_good)/total*100:.1f}%")
    print(f"  Note routing vs baseline:  +{(note_oracle_good - baseline_good)/total*100:.1f}%")

    # Per-class breakdown
    print(f"\n{'='*80}")
    print("PER-CLASS ANALYSIS")
    print(f"{'='*80}")

    print(f"\n{'Class':25s} {'N':>5s} {'Model':>7s} {'Spec':>7s} {'RMS':>7s} {'Best':>8s}")
    print("-" * 65)

    for cls in sorted(by_class.keys()):
        cls_notes = by_class[cls]
        n = len(cls_notes)
        model_rate = sum(1 for x in cls_notes if x['model_is_good']) / n * 100
        spectral_rate = sum(1 for x in cls_notes if x['spectral_is_good']) / n * 100
        rms_rate = sum(1 for x in cls_notes if x['rms_is_good']) / n * 100

        rates = {'model': model_rate, 'spectral': spectral_rate, 'rms': rms_rate}
        best = max(rates, key=rates.get)

        print(f"{cls:25s} {n:5d} {model_rate:6.1f}% {spectral_rate:6.1f}% {rms_rate:6.1f}% {best}")

    # Spectral helped/hurt analysis
    print(f"\n{'='*80}")
    print("SPECTRAL CORRECTION ANALYSIS")
    print(f"{'='*80}")

    spectral_helped = sum(1 for n in notes if n['spectral_helped'])
    spectral_hurt = sum(1 for n in notes if n['spectral_hurt'])
    spectral_neutral = total - spectral_helped - spectral_hurt

    print(f"\nOverall:")
    print(f"  Spectral helped: {spectral_helped:5d} ({spectral_helped/total*100:.1f}%)")
    print(f"  Spectral hurt:   {spectral_hurt:5d} ({spectral_hurt/total*100:.1f}%)")
    print(f"  Neutral:         {spectral_neutral:5d} ({spectral_neutral/total*100:.1f}%)")

    print(f"\nPer-class spectral effect:")
    print(f"{'Class':25s} {'Helped':>8s} {'Hurt':>8s} {'Net':>8s}")
    print("-" * 55)

    for cls in sorted(by_class.keys()):
        cls_notes = by_class[cls]
        n = len(cls_notes)
        helped = sum(1 for x in cls_notes if x['spectral_helped']) / n * 100
        hurt = sum(1 for x in cls_notes if x['spectral_hurt']) / n * 100
        net = helped - hurt

        print(f"{cls:25s} {helped:7.1f}% {hurt:7.1f}% {net:+7.1f}%")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--output", type=str, default="/tmp/offset_oracle_data.json")
    args = parser.parse_args()

    dataset_dir = Path("/Users/mattharvey/Sites/tone-forge/datasets/slakh2100_flac_redux")
    run_oracle_experiment(dataset_dir, Path(args.output), args.limit)
