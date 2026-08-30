#!/usr/bin/env python3
"""Audio-Driven Note Offset Reconstruction Experiment.

Tests whether audio analysis can improve note offset detection:
1. RMS amplitude decay - detect when note energy drops
2. Spectral centroid shift - detect timbral decay
3. Pitch confidence decay - use basic_pitch posteriors

Compares to baseline (model offsets) and simple duration scaling.
"""
import sys
from pathlib import Path
from typing import List, Tuple, Dict, Optional
import json
import numpy as np
import librosa
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tone_forge.midi.gpu_extractor import extract_midi_lead_ensemble


def detect_note_offset_rms(
    y: np.ndarray,
    sr: int,
    onset_time: float,
    model_offset_time: float,
    decay_threshold: float = 0.3,
    min_duration: float = 0.05,
    max_search_window: float = 2.0,
) -> float:
    """Detect note offset using RMS amplitude decay.

    Finds the time when RMS drops below decay_threshold * onset_rms.

    Args:
        y: Audio samples
        sr: Sample rate
        onset_time: Note onset in seconds
        model_offset_time: Model's predicted offset
        decay_threshold: Fraction of onset RMS to trigger offset (0.3 = 30%)
        min_duration: Minimum note duration
        max_search_window: Maximum time to search past onset

    Returns:
        Detected offset time in seconds
    """
    hop_length = 512
    frame_rate = sr / hop_length

    # Compute RMS envelope
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=hop_length)[0]

    onset_frame = int(onset_time * frame_rate)
    model_offset_frame = int(model_offset_time * frame_rate)
    max_search_frame = int((onset_time + max_search_window) * frame_rate)

    # Clamp to valid range
    onset_frame = max(0, min(onset_frame, len(rms) - 1))
    model_offset_frame = max(0, min(model_offset_frame, len(rms) - 1))
    max_search_frame = max(0, min(max_search_frame, len(rms) - 1))

    if onset_frame >= len(rms):
        return model_offset_time

    # Get onset RMS (average over first ~50ms)
    onset_window = min(5, len(rms) - onset_frame)
    onset_rms = np.mean(rms[onset_frame:onset_frame + onset_window])

    if onset_rms < 1e-6:
        return model_offset_time  # Silent, use model

    # Search for decay point
    threshold_rms = onset_rms * decay_threshold
    min_duration_frames = int(min_duration * frame_rate)

    search_start = onset_frame + min_duration_frames
    search_end = min(model_offset_frame + int(0.5 * frame_rate), max_search_frame)  # Search up to 500ms past model

    for frame in range(search_start, search_end):
        if frame >= len(rms):
            break
        if rms[frame] < threshold_rms:
            detected_time = frame / frame_rate
            return detected_time

    # No decay found, use model offset (but cap at max_search_window)
    return min(model_offset_time, onset_time + max_search_window)


def detect_note_offset_spectral(
    y: np.ndarray,
    sr: int,
    onset_time: float,
    model_offset_time: float,
    pitch: int,
    decay_threshold: float = 0.3,
    min_duration: float = 0.05,
) -> float:
    """Detect note offset using spectral energy around fundamental.

    More robust than RMS for pitched content - measures energy
    specifically in the note's frequency band.

    Args:
        y: Audio samples
        sr: Sample rate
        onset_time: Note onset
        model_offset_time: Model's predicted offset
        pitch: MIDI pitch number
        decay_threshold: Energy decay threshold
        min_duration: Minimum note duration

    Returns:
        Detected offset time
    """
    hop_length = 512
    n_fft = 2048
    frame_rate = sr / hop_length

    # Get fundamental frequency
    f0 = 440.0 * (2.0 ** ((pitch - 69) / 12.0))

    # Compute STFT
    stft = np.abs(librosa.stft(y, n_fft=n_fft, hop_length=hop_length))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=n_fft)

    # Find frequency bins for fundamental +/- 1 semitone
    f_low = f0 * (2.0 ** (-1/12))
    f_high = f0 * (2.0 ** (1/12))

    bin_low = np.searchsorted(freqs, f_low)
    bin_high = np.searchsorted(freqs, f_high)

    # Energy in fundamental band
    band_energy = np.sum(stft[bin_low:bin_high+1, :], axis=0)

    onset_frame = int(onset_time * frame_rate)
    model_offset_frame = int(model_offset_time * frame_rate)

    onset_frame = max(0, min(onset_frame, len(band_energy) - 1))

    # Onset energy
    onset_window = min(5, len(band_energy) - onset_frame)
    onset_energy = np.mean(band_energy[onset_frame:onset_frame + onset_window])

    if onset_energy < 1e-6:
        return model_offset_time

    threshold_energy = onset_energy * decay_threshold
    min_frames = int(min_duration * frame_rate)

    search_start = onset_frame + min_frames
    search_end = min(model_offset_frame + int(0.3 * frame_rate), len(band_energy))

    for frame in range(search_start, search_end):
        if band_energy[frame] < threshold_energy:
            return frame / frame_rate

    return model_offset_time


def load_midi_notes(midi_path: Path) -> List[Tuple[int, float, float]]:
    """Load notes from MIDI file."""
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


def compute_metrics(
    gt_notes: List[Tuple[int, float, float]],
    ext_notes: List[Tuple[int, float, float]],
    onset_tol: float = 0.35,
) -> Dict:
    """Compute F1 and duration metrics."""
    if not gt_notes or not ext_notes:
        return {'f1': 0, 'good_ratio_pct': 0, 'mean_ratio': 0}

    # Match notes
    candidates = []
    for i, (ep, eo, ee) in enumerate(ext_notes):
        for j, (gp, go, ge) in enumerate(gt_notes):
            if abs(eo - go) > onset_tol:
                continue
            same_pc = (ep % 12) == (gp % 12)
            if ep == gp or same_pc:
                candidates.append((j, i, ep == gp, abs(eo - go), go, ge, eo, ee))

    candidates.sort(key=lambda x: (not x[2], x[3]))

    gt_matched = set()
    ext_matched = set()
    duration_ratios = []

    for gt_idx, ext_idx, _, _, go, ge, eo, ee in candidates:
        if gt_idx in gt_matched or ext_idx in ext_matched:
            continue
        gt_matched.add(gt_idx)
        ext_matched.add(ext_idx)

        gt_dur = ge - go
        ext_dur = ee - eo
        if gt_dur > 0.01:
            duration_ratios.append(ext_dur / gt_dur)

    tp = len(gt_matched)
    fp = len(ext_notes) - len(ext_matched)
    fn = len(gt_notes) - len(gt_matched)

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0

    good_count = sum(1 for r in duration_ratios if 0.8 <= r <= 1.2)
    good_pct = good_count / len(duration_ratios) * 100 if duration_ratios else 0

    return {
        'f1': float(f1),
        'precision': float(precision),
        'recall': float(recall),
        'good_ratio_pct': float(good_pct),
        'mean_ratio': float(np.mean(duration_ratios)) if duration_ratios else 0,
        'median_ratio': float(np.median(duration_ratios)) if duration_ratios else 0,
    }


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


def run_audio_offset_experiment(
    dataset_dir: Path,
    output_file: Path,
    limit: int = 50,
):
    """Run audio-driven offset reconstruction experiment."""
    stems = find_stems(dataset_dir, limit=limit)

    if not stems:
        print("No stems found!")
        return

    print(f"Testing audio-driven offset detection on {len(stems)} stems")

    results = []

    for i, stem in enumerate(stems):
        if (i + 1) % 10 == 0:
            print(f"  [{i+1}/{len(stems)}]...")

        try:
            gt_notes = load_midi_notes(stem['midi_path'])
        except Exception:
            continue

        if not gt_notes:
            continue

        # Load audio
        try:
            y, sr = librosa.load(str(stem['audio_path']), sr=22050, mono=True)
        except Exception:
            continue

        # Get model predictions
        try:
            ext_notes_raw, _, _, _ = extract_midi_lead_ensemble(str(stem['audio_path']))
            model_notes = [(n.pitch, n.start, n.end) for n in ext_notes_raw]
        except Exception:
            continue

        if not model_notes:
            continue

        # Method 1: Baseline (model offsets)
        baseline_metrics = compute_metrics(gt_notes, model_notes)

        # Method 2: RMS-based offset detection
        rms_notes = []
        for pitch, onset, model_offset in model_notes:
            rms_offset = detect_note_offset_rms(y, sr, onset, model_offset, decay_threshold=0.3)
            rms_notes.append((pitch, onset, rms_offset))
        rms_metrics = compute_metrics(gt_notes, rms_notes)

        # Method 3: Spectral band energy decay
        spectral_notes = []
        for pitch, onset, model_offset in model_notes:
            spec_offset = detect_note_offset_spectral(y, sr, onset, model_offset, pitch, decay_threshold=0.3)
            spectral_notes.append((pitch, onset, spec_offset))
        spectral_metrics = compute_metrics(gt_notes, spectral_notes)

        # Method 4: More aggressive RMS (20% threshold)
        rms_aggressive_notes = []
        for pitch, onset, model_offset in model_notes:
            rms_offset = detect_note_offset_rms(y, sr, onset, model_offset, decay_threshold=0.2)
            rms_aggressive_notes.append((pitch, onset, rms_offset))
        rms_agg_metrics = compute_metrics(gt_notes, rms_aggressive_notes)

        # Method 5: Conservative RMS (40% threshold)
        rms_conservative_notes = []
        for pitch, onset, model_offset in model_notes:
            rms_offset = detect_note_offset_rms(y, sr, onset, model_offset, decay_threshold=0.4)
            rms_conservative_notes.append((pitch, onset, rms_offset))
        rms_cons_metrics = compute_metrics(gt_notes, rms_conservative_notes)

        results.append({
            'stem': f"{stem['track']}/{stem['stem_id']}",
            'inst_class': stem['inst_class'],
            'baseline': baseline_metrics,
            'rms_30': rms_metrics,
            'rms_20': rms_agg_metrics,
            'rms_40': rms_cons_metrics,
            'spectral_30': spectral_metrics,
        })

    # Summary
    print(f"\n{'='*80}")
    print("AUDIO-DRIVEN OFFSET EXPERIMENT RESULTS")
    print(f"{'='*80}")

    methods = ['baseline', 'rms_30', 'rms_20', 'rms_40', 'spectral_30']

    # Overall averages
    print(f"\n{'Method':20s} {'F1':>6s} {'Good%':>7s} {'MedRatio':>9s}")
    print("-" * 50)

    for method in methods:
        f1s = [r[method]['f1'] for r in results]
        good_pcts = [r[method]['good_ratio_pct'] for r in results]
        med_ratios = [r[method]['median_ratio'] for r in results if r[method]['median_ratio'] > 0]

        print(f"{method:20s} {np.mean(f1s)*100:5.1f}% {np.mean(good_pcts):6.1f}% {np.median(med_ratios):8.2f}")

    # By instrument class
    print(f"\n{'='*80}")
    print("BY INSTRUMENT CLASS (Good Duration %)")
    print(f"{'='*80}")

    by_class = defaultdict(list)
    for r in results:
        by_class[r['inst_class']].append(r)

    print(f"\n{'Class':25s} {'Base':>6s} {'RMS30':>6s} {'RMS20':>6s} {'Spec30':>6s} {'Best':>8s}")
    print("-" * 65)

    for cls in sorted(by_class.keys()):
        cls_results = by_class[cls]
        base = np.mean([r['baseline']['good_ratio_pct'] for r in cls_results])
        rms30 = np.mean([r['rms_30']['good_ratio_pct'] for r in cls_results])
        rms20 = np.mean([r['rms_20']['good_ratio_pct'] for r in cls_results])
        spec30 = np.mean([r['spectral_30']['good_ratio_pct'] for r in cls_results])

        values = {'base': base, 'rms30': rms30, 'rms20': rms20, 'spec30': spec30}
        best = max(values, key=values.get)
        delta = values[best] - base

        print(f"{cls:25s} {base:5.1f}% {rms30:5.1f}% {rms20:5.1f}% {spec30:5.1f}% {best}:{delta:+5.1f}%")

    # Save results
    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2)

    print(f"\nResults saved to {output_file}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--output", type=str, default="/tmp/audio_offset_results.json")
    args = parser.parse_args()

    dataset_dir = Path("/Users/mattharvey/Sites/tone-forge/datasets/slakh2100_flac_redux")
    run_audio_offset_experiment(dataset_dir, Path(args.output), args.limit)
