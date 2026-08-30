#!/usr/bin/env python3
"""Basic Pitch Fine-Tuning Experiments.

Sequential, gated experiments to test whether domain adaptation
improves Basic Pitch's internal representation.

Experiments:
A. Baseline reproduction (pretrained model, new eval pipeline)
B. Standard BCE fine-tune on Slakh
C. Targeted class weighting (4x for error-prone classes)
D. Octave-aware loss (hard negatives for P±12)
"""
import sys
import os
import json
import argparse
import random
from pathlib import Path
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass, asdict
from collections import defaultdict
import numpy as np
import yaml

import tensorflow as tf
import librosa
import mido

# Suppress TF warnings
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '2'

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Basic Pitch imports
from basic_pitch import ICASSP_2022_MODEL_PATH
from basic_pitch.inference import predict
from basic_pitch.constants import (
    AUDIO_SAMPLE_RATE,
    AUDIO_N_SAMPLES,
    ANNOT_N_FRAMES,
    N_FREQ_BINS_NOTES,
    N_FREQ_BINS_CONTOURS,
    FFT_HOP,
    ANNOTATIONS_FPS,
)

# Constants
MIDI_OFFSET = 21  # Basic Pitch uses 88 keys starting at MIDI 21
WINDOW_DURATION = AUDIO_N_SAMPLES / AUDIO_SAMPLE_RATE  # ~2 seconds

# Instrument classes and their weights
PROBLEMATIC_CLASSES = {'Bass', 'Synth Lead', 'Chromatic Percussion', 'Organ'}
STRONG_CLASSES = {'Piano', 'Reed', 'Brass', 'Guitar'}
MELODIC_CLASSES = PROBLEMATIC_CLASSES | STRONG_CLASSES | {
    'Strings', 'Strings (continued)', 'Synth Pad', 'Pipe', 'Ethnic'
}


@dataclass
class EvalMetrics:
    """Evaluation metrics for a model."""
    # Global metrics
    note_f1: float = 0.0
    strict_f1: float = 0.0
    pass_rate: float = 0.0
    pitch_precision: float = 0.0
    pitch_recall: float = 0.0
    octave_error_rate: float = 0.0
    onset_f1: float = 0.0
    duration_ratio: float = 0.0

    # Internal representation metrics
    representation_failure_pct: float = 0.0
    ambiguous_pct: float = 0.0
    gt_recoverable_pct: float = 0.0

    # Per-class breakdown
    per_class: Dict = None


def load_midi_to_matrices(
    midi_path: Path,
    duration: float,
    fps: float = ANNOTATIONS_FPS,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Load MIDI file and convert to note/onset/contour matrices.

    Returns:
        notes: (n_frames, 88) binary matrix
        onsets: (n_frames, 88) binary matrix
        contours: (n_frames, 264) continuous matrix
    """
    try:
        mid = mido.MidiFile(str(midi_path))
    except Exception:
        return None, None, None

    # Get tempo
    tempo = 500000
    for track in mid.tracks:
        for msg in track:
            if msg.type == 'set_tempo':
                tempo = msg.tempo
                break

    n_frames = int(np.ceil(duration * fps))
    notes = np.zeros((n_frames, N_FREQ_BINS_NOTES), dtype=np.float32)
    onsets = np.zeros((n_frames, N_FREQ_BINS_NOTES), dtype=np.float32)
    contours = np.zeros((n_frames, N_FREQ_BINS_CONTOURS), dtype=np.float32)

    # Parse MIDI events
    for track in mid.tracks:
        current_time = 0
        active_notes = {}

        for msg in track:
            current_time += mido.tick2second(msg.time, mid.ticks_per_beat, tempo)

            if msg.type == 'note_on' and msg.velocity > 0:
                active_notes[msg.note] = current_time
            elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
                if msg.note in active_notes:
                    start_time = active_notes.pop(msg.note)
                    end_time = current_time

                    # Convert to bin index
                    pitch_idx = msg.note - MIDI_OFFSET
                    if not (0 <= pitch_idx < N_FREQ_BINS_NOTES):
                        continue

                    # Convert times to frames
                    start_frame = int(start_time * fps)
                    end_frame = int(end_time * fps)
                    onset_frame = start_frame

                    if start_frame >= n_frames or end_frame < 0:
                        continue

                    start_frame = max(0, start_frame)
                    end_frame = min(n_frames, end_frame)

                    # Fill matrices
                    notes[start_frame:end_frame, pitch_idx] = 1.0
                    if 0 <= onset_frame < n_frames:
                        onsets[onset_frame, pitch_idx] = 1.0

                    # Contours: 3 bins per semitone
                    contour_idx = pitch_idx * 3 + 1  # Center bin
                    if contour_idx < N_FREQ_BINS_CONTOURS:
                        contours[start_frame:end_frame, contour_idx] = 1.0

    return notes, onsets, contours


def load_audio_windows(
    audio_path: Path,
    n_windows: int = 5,
    random_seed: Optional[int] = None,
) -> List[np.ndarray]:
    """Load audio and extract random windows.

    Returns:
        List of (AUDIO_N_SAMPLES, 1) arrays
    """
    try:
        audio, sr = librosa.load(str(audio_path), sr=AUDIO_SAMPLE_RATE, mono=True)
    except Exception:
        return []

    if len(audio) < AUDIO_N_SAMPLES:
        return []

    if random_seed is not None:
        np.random.seed(random_seed)

    windows = []
    max_start = len(audio) - AUDIO_N_SAMPLES

    for _ in range(n_windows):
        start = np.random.randint(0, max_start + 1)
        window = audio[start:start + AUDIO_N_SAMPLES]
        windows.append(window.reshape(-1, 1).astype(np.float32))

    return windows


def find_stems(
    dataset_dir: Path,
    split: str = 'train',
    limit: int = 0,
) -> List[Dict]:
    """Find melodic stems in Slakh dataset."""
    stems = []
    split_dir = dataset_dir / split

    if not split_dir.exists():
        return stems

    for track_dir in sorted(split_dir.iterdir()):
        if limit > 0 and len(stems) >= limit:
            break

        if not track_dir.is_dir():
            continue

        metadata_path = track_dir / "metadata.yaml"
        if not metadata_path.exists():
            continue

        try:
            with open(metadata_path) as f:
                metadata = yaml.safe_load(f)
        except Exception:
            continue  # Skip corrupted metadata files

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
                'audio_path': audio_path,
                'midi_path': midi_path,
            })

    return stems


class SlakhDataGenerator(tf.keras.utils.Sequence):
    """Data generator for Slakh stems."""

    def __init__(
        self,
        stems: List[Dict],
        batch_size: int = 8,
        windows_per_stem: int = 3,
        class_weights: Optional[Dict[str, float]] = None,
        shuffle: bool = True,
        seed: int = 42,
    ):
        self.stems = stems
        self.batch_size = batch_size
        self.windows_per_stem = windows_per_stem
        self.class_weights = class_weights or {}
        self.shuffle = shuffle
        self.seed = seed

        # Build sample list with class weighting
        self.samples = []
        for stem in stems:
            weight = self.class_weights.get(stem['inst_class'], 1.0)
            # Repeat samples based on weight
            n_copies = max(1, int(weight))
            for _ in range(n_copies):
                self.samples.append(stem)

        self.indices = list(range(len(self.samples)))
        if shuffle:
            random.seed(seed)
            random.shuffle(self.indices)

    def __len__(self):
        return len(self.indices) // self.batch_size

    def __getitem__(self, idx):
        batch_indices = self.indices[idx * self.batch_size:(idx + 1) * self.batch_size]

        audio_batch = []
        notes_batch = []
        onsets_batch = []
        contours_batch = []

        for i in batch_indices:
            stem = self.samples[i]

            # Load audio windows
            windows = load_audio_windows(
                stem['audio_path'],
                n_windows=1,
                random_seed=self.seed + idx + i,
            )
            if not windows:
                continue

            # Get audio duration for MIDI conversion
            audio, _ = librosa.load(str(stem['audio_path']), sr=AUDIO_SAMPLE_RATE, mono=True)
            duration = len(audio) / AUDIO_SAMPLE_RATE

            # Load MIDI
            notes, onsets, contours = load_midi_to_matrices(stem['midi_path'], duration)
            if notes is None:
                continue

            # Extract corresponding window from annotations
            for window in windows:
                # Random start time (same as audio)
                np.random.seed(self.seed + idx + i)
                max_start = len(audio) - AUDIO_N_SAMPLES
                start_sample = np.random.randint(0, max_start + 1)
                start_time = start_sample / AUDIO_SAMPLE_RATE

                # Convert to annotation frames
                start_frame = int(start_time * ANNOTATIONS_FPS)
                end_frame = start_frame + ANNOT_N_FRAMES

                if end_frame > len(notes):
                    continue

                audio_batch.append(window)
                notes_batch.append(notes[start_frame:end_frame])
                onsets_batch.append(onsets[start_frame:end_frame])
                contours_batch.append(contours[start_frame:end_frame])

        if not audio_batch:
            # Return dummy batch if no valid samples
            return (
                np.zeros((1, AUDIO_N_SAMPLES, 1), dtype=np.float32),
                {
                    'note': np.zeros((1, ANNOT_N_FRAMES, N_FREQ_BINS_NOTES), dtype=np.float32),
                    'onset': np.zeros((1, ANNOT_N_FRAMES, N_FREQ_BINS_NOTES), dtype=np.float32),
                    'contour': np.zeros((1, ANNOT_N_FRAMES, N_FREQ_BINS_CONTOURS), dtype=np.float32),
                }
            )

        return (
            np.array(audio_batch),
            {
                'note': np.array(notes_batch),
                'onset': np.array(onsets_batch),
                'contour': np.array(contours_batch),
            }
        )

    def on_epoch_end(self):
        if self.shuffle:
            random.shuffle(self.indices)


def match_notes_with_octave(
    gt_notes: List[Tuple[int, float, float]],
    ext_notes: List[Tuple[float, float, int, float]],
    onset_tol: float = 0.35,
) -> Tuple[int, int, int, int, List[Dict]]:
    """Match notes and count errors.

    Returns:
        n_correct, n_octave_error, n_other_error, n_missed, matches
    """
    ext_converted = [(n[2], n[0], n[1]) for n in ext_notes]  # (pitch, onset, offset)

    gt_matched = set()
    ext_matched = set()
    matches = []

    n_correct = 0
    n_octave_error = 0
    n_other_error = 0

    # Match extracted to GT
    for i, (ep, eo, ee) in enumerate(ext_converted):
        best_match = None
        best_diff = float('inf')

        for j, (gp, go, ge) in enumerate(gt_notes):
            if j in gt_matched:
                continue

            onset_diff = abs(eo - go)
            if onset_diff > onset_tol:
                continue

            # Check pitch class match
            if (ep % 12) == (gp % 12):
                if onset_diff < best_diff:
                    best_diff = onset_diff
                    best_match = j

        if best_match is not None:
            gt_matched.add(best_match)
            ext_matched.add(i)

            gp = gt_notes[best_match][0]
            exact = (ep == gp)
            octave_delta = (ep - gp) // 12

            if exact:
                n_correct += 1
            else:
                n_octave_error += 1

            matches.append({
                'gt_idx': best_match,
                'ext_idx': i,
                'exact': exact,
                'octave_delta': octave_delta,
            })

    n_missed = len(gt_notes) - len(gt_matched)
    n_other_error = len(ext_notes) - len(ext_matched)  # False positives

    return n_correct, n_octave_error, n_other_error, n_missed, matches


def run_custom_inference(
    model: tf.keras.Model,
    audio_path: Path,
) -> Tuple[Dict, List[Tuple]]:
    """Run inference using a custom/fine-tuned model.

    Returns:
        model_output: Dict with 'note', 'onset', 'contour' arrays
        note_events: List of (onset, offset, pitch, velocity, confidence)
    """
    # Load audio
    audio, _ = librosa.load(str(audio_path), sr=AUDIO_SAMPLE_RATE, mono=True)

    if len(audio) < AUDIO_N_SAMPLES:
        return None, []

    # Process in overlapping windows
    hop = AUDIO_N_SAMPLES // 2  # 50% overlap
    n_windows = (len(audio) - AUDIO_N_SAMPLES) // hop + 1

    all_notes = []
    all_onsets = []
    all_contours = []

    for i in range(n_windows):
        start = i * hop
        end = start + AUDIO_N_SAMPLES
        window = audio[start:end].reshape(1, -1, 1).astype(np.float32)

        # Predict
        output = model(window, training=False)
        all_notes.append(output['note'].numpy()[0])
        all_onsets.append(output['onset'].numpy()[0])
        all_contours.append(output['contour'].numpy()[0])

    # Stitch together (simple averaging for overlaps)
    # For simplicity, just concatenate middle portions
    if len(all_notes) == 1:
        final_notes = all_notes[0]
        final_onsets = all_onsets[0]
        final_contours = all_contours[0]
    else:
        # Take non-overlapping middle portions
        half_frames = ANNOT_N_FRAMES // 2
        final_notes = []
        final_onsets = []
        final_contours = []

        for i, (n, o, c) in enumerate(zip(all_notes, all_onsets, all_contours)):
            if i == 0:
                final_notes.append(n[:half_frames + half_frames // 2])
                final_onsets.append(o[:half_frames + half_frames // 2])
                final_contours.append(c[:half_frames + half_frames // 2])
            elif i == len(all_notes) - 1:
                final_notes.append(n[half_frames // 2:])
                final_onsets.append(o[half_frames // 2:])
                final_contours.append(c[half_frames // 2:])
            else:
                final_notes.append(n[half_frames // 2:half_frames + half_frames // 2])
                final_onsets.append(o[half_frames // 2:half_frames + half_frames // 2])
                final_contours.append(c[half_frames // 2:half_frames + half_frames // 2])

        final_notes = np.concatenate(final_notes, axis=0)
        final_onsets = np.concatenate(final_onsets, axis=0)
        final_contours = np.concatenate(final_contours, axis=0)

    model_output = {
        'note': final_notes,
        'onset': final_onsets,
        'contour': final_contours,
    }

    # Convert to note events using simple thresholding
    note_events = extract_notes_simple(final_notes, final_onsets)

    return model_output, note_events


def extract_notes_simple(
    notes: np.ndarray,
    onsets: np.ndarray,
    note_threshold: float = 0.5,
    onset_threshold: float = 0.3,
    min_duration_frames: int = 3,
) -> List[Tuple]:
    """Extract note events from posteriorgrams.

    Returns:
        List of (onset_time, offset_time, pitch, velocity, confidence)
    """
    fps = ANNOTATIONS_FPS
    n_frames, n_pitches = notes.shape

    events = []

    for pitch_idx in range(n_pitches):
        pitch = pitch_idx + MIDI_OFFSET
        note_col = notes[:, pitch_idx]
        onset_col = onsets[:, pitch_idx]

        # Find note regions
        in_note = False
        note_start = 0
        note_conf = 0

        for frame in range(n_frames):
            is_active = note_col[frame] > note_threshold
            is_onset = onset_col[frame] > onset_threshold

            if not in_note and (is_active or is_onset):
                in_note = True
                note_start = frame
                note_conf = note_col[frame]
            elif in_note and not is_active:
                # Note ended
                if frame - note_start >= min_duration_frames:
                    onset_time = note_start / fps
                    offset_time = frame / fps
                    events.append((onset_time, offset_time, pitch, 80, float(note_conf)))
                in_note = False

        # Handle note that extends to end
        if in_note and n_frames - note_start >= min_duration_frames:
            onset_time = note_start / fps
            offset_time = n_frames / fps
            events.append((onset_time, offset_time, pitch, 80, float(note_conf)))

    return events


def evaluate_model_on_stems(
    model_or_path,
    stems: List[Dict],
    limit: int = 100,
) -> EvalMetrics:
    """Evaluate model on a set of stems.

    Args:
        model_or_path: Either a Keras model, path to model, or 'pretrained'
        stems: List of stem dicts
        limit: Max stems to evaluate
    """
    use_pretrained = (model_or_path == 'pretrained')
    custom_model = None

    if not use_pretrained:
        if isinstance(model_or_path, tf.keras.Model):
            custom_model = model_or_path
        elif isinstance(model_or_path, (str, Path)):
            custom_model = tf.keras.models.load_model(str(model_or_path), compile=False)
        else:
            raise ValueError(f"Unknown model type: {type(model_or_path)}")

    total_correct = 0
    total_octave_error = 0
    total_other_error = 0
    total_missed = 0
    total_gt_notes = 0
    total_ext_notes = 0

    # Per-class tracking
    class_stats = defaultdict(lambda: {
        'correct': 0, 'octave_error': 0, 'other_error': 0,
        'missed': 0, 'gt_notes': 0, 'ext_notes': 0
    })

    # Internal representation tracking
    n_rep_failure = 0
    n_ambiguous = 0
    n_gt_recoverable = 0
    n_total_errors = 0

    eval_stems = stems[:limit]

    for idx, stem in enumerate(eval_stems):
        if (idx + 1) % 20 == 0:
            print(f"  [{idx+1}/{len(eval_stems)}]...", flush=True)

        inst_class = stem['inst_class']

        # Load GT from MIDI
        try:
            mid = mido.MidiFile(str(stem['midi_path']))
        except Exception:
            continue

        tempo = 500000
        for track in mid.tracks:
            for msg in track:
                if msg.type == 'set_tempo':
                    tempo = msg.tempo
                    break

        gt_notes = []
        for track in mid.tracks:
            current_time = 0
            active = {}
            for msg in track:
                current_time += mido.tick2second(msg.time, mid.ticks_per_beat, tempo)
                if msg.type == 'note_on' and msg.velocity > 0:
                    active[msg.note] = current_time
                elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
                    if msg.note in active:
                        start = active.pop(msg.note)
                        gt_notes.append((msg.note, start, current_time))

        if not gt_notes:
            continue

        # Run inference
        try:
            if use_pretrained:
                model_output, _, note_events = predict(str(stem['audio_path']))
            else:
                model_output, note_events = run_custom_inference(custom_model, stem['audio_path'])
                if model_output is None:
                    continue
        except Exception as e:
            continue

        if not note_events:
            total_missed += len(gt_notes)
            class_stats[inst_class]['missed'] += len(gt_notes)
            class_stats[inst_class]['gt_notes'] += len(gt_notes)
            total_gt_notes += len(gt_notes)
            continue

        # Match notes
        correct, octave_err, other_err, missed, matches = match_notes_with_octave(
            gt_notes, note_events
        )

        total_correct += correct
        total_octave_error += octave_err
        total_other_error += other_err
        total_missed += missed
        total_gt_notes += len(gt_notes)
        total_ext_notes += len(note_events)

        class_stats[inst_class]['correct'] += correct
        class_stats[inst_class]['octave_error'] += octave_err
        class_stats[inst_class]['other_error'] += other_err
        class_stats[inst_class]['missed'] += missed
        class_stats[inst_class]['gt_notes'] += len(gt_notes)
        class_stats[inst_class]['ext_notes'] += len(note_events)

        # Analyze internal representation for errors
        if octave_err > 0 and use_pretrained:
            note_activations = model_output['note']

            for match in matches:
                if match['exact']:
                    continue

                n_total_errors += 1

                gt_pitch = gt_notes[match['gt_idx']][0]
                ext_note = note_events[match['ext_idx']]
                ext_pitch = ext_note[2]

                # Check if GT in candidate set
                candidates = [ext_pitch - 24, ext_pitch - 12, ext_pitch, ext_pitch + 12, ext_pitch + 24]
                if gt_pitch in candidates:
                    n_gt_recoverable += 1

                # Analyze activations (simplified)
                gt_idx = gt_pitch - MIDI_OFFSET
                ext_idx = ext_pitch - MIDI_OFFSET

                onset_frame = int(ext_note[0] * ANNOTATIONS_FPS)
                if 0 <= onset_frame < len(note_activations) and 0 <= gt_idx < 88 and 0 <= ext_idx < 88:
                    gt_act = float(np.mean(note_activations[onset_frame:onset_frame+5, gt_idx]))
                    ext_act = float(np.mean(note_activations[onset_frame:onset_frame+5, ext_idx]))

                    if gt_act < 0.1:
                        n_rep_failure += 1
                    elif abs(gt_act - ext_act) < 0.1:
                        n_ambiguous += 1

    # Calculate metrics
    precision = total_correct / total_ext_notes if total_ext_notes > 0 else 0
    recall = total_correct / total_gt_notes if total_gt_notes > 0 else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0

    matched_notes = total_correct + total_octave_error
    octave_error_rate = total_octave_error / matched_notes if matched_notes > 0 else 0

    # Per-class metrics
    per_class = {}
    for cls, stats in class_stats.items():
        cls_matched = stats['correct'] + stats['octave_error']
        cls_octave_rate = stats['octave_error'] / cls_matched if cls_matched > 0 else 0
        cls_precision = stats['correct'] / stats['ext_notes'] if stats['ext_notes'] > 0 else 0
        cls_recall = stats['correct'] / stats['gt_notes'] if stats['gt_notes'] > 0 else 0
        cls_f1 = 2 * cls_precision * cls_recall / (cls_precision + cls_recall) if (cls_precision + cls_recall) > 0 else 0

        per_class[cls] = {
            'n_notes': stats['gt_notes'],
            'octave_error_rate': cls_octave_rate,
            'precision': cls_precision,
            'recall': cls_recall,
            'f1': cls_f1,
        }

    return EvalMetrics(
        note_f1=f1,
        strict_f1=f1,  # Same for now
        pass_rate=recall,
        pitch_precision=precision,
        pitch_recall=recall,
        octave_error_rate=octave_error_rate,
        onset_f1=f1,  # Simplified
        duration_ratio=1.0,  # Not computed
        representation_failure_pct=n_rep_failure / n_total_errors if n_total_errors > 0 else 0,
        ambiguous_pct=n_ambiguous / n_total_errors if n_total_errors > 0 else 0,
        gt_recoverable_pct=n_gt_recoverable / n_total_errors if n_total_errors > 0 else 0,
        per_class=per_class,
    )


def print_metrics(metrics: EvalMetrics, title: str = "Results"):
    """Print evaluation metrics."""
    print()
    print("=" * 70)
    print(title)
    print("=" * 70)
    print()
    print("GLOBAL METRICS")
    print(f"  Note F1:              {100*metrics.note_f1:.1f}%")
    print(f"  Pitch Precision:      {100*metrics.pitch_precision:.1f}%")
    print(f"  Pitch Recall:         {100*metrics.pitch_recall:.1f}%")
    print(f"  Octave Error Rate:    {100*metrics.octave_error_rate:.1f}%")
    print()
    print("INTERNAL REPRESENTATION (for errors)")
    print(f"  Representation Fail:  {100*metrics.representation_failure_pct:.1f}%")
    print(f"  Ambiguous:            {100*metrics.ambiguous_pct:.1f}%")
    print(f"  GT Recoverable:       {100*metrics.gt_recoverable_pct:.1f}%")
    print()
    print("PER-CLASS BREAKDOWN")
    print(f"{'Class':<25} {'Notes':>6} {'OctErr%':>8} {'Prec%':>7} {'Rec%':>7} {'F1%':>6}")
    print("-" * 70)

    if metrics.per_class:
        for cls in sorted(metrics.per_class.keys(), key=lambda c: -metrics.per_class[c]['n_notes']):
            stats = metrics.per_class[cls]
            print(f"{cls:<25} {stats['n_notes']:>6} {100*stats['octave_error_rate']:>7.1f}% "
                  f"{100*stats['precision']:>6.1f}% {100*stats['recall']:>6.1f}% {100*stats['f1']:>5.1f}%")


def run_experiment_a(dataset_dir: Path, output_dir: Path, limit: int = 100):
    """Experiment A: Baseline reproduction with pretrained model."""
    print("\n" + "=" * 70)
    print("EXPERIMENT A: BASELINE REPRODUCTION")
    print("=" * 70)
    print()
    print("Using pretrained Basic Pitch model on validation set")
    print(f"Evaluating {limit} stems...")

    # Load validation stems
    val_stems = find_stems(dataset_dir, split='validation', limit=limit)
    print(f"Found {len(val_stems)} validation stems")

    # Evaluate pretrained model
    metrics = evaluate_model_on_stems('pretrained', val_stems, limit=limit)

    print_metrics(metrics, "EXPERIMENT A: PRETRAINED BASELINE")

    # Save results
    results = {
        'experiment': 'A',
        'description': 'Pretrained baseline',
        'n_stems': len(val_stems),
        'metrics': asdict(metrics),
    }

    output_path = output_dir / 'experiment_a_results.json'
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w') as f:
        json.dump(results, f, indent=2, default=str)

    print(f"\nResults saved to: {output_path}")

    return metrics


def run_experiment_b(
    dataset_dir: Path,
    output_dir: Path,
    epochs: int = 50,
    batch_size: int = 8,
    learning_rate: float = 0.0001,
    train_limit: int = 500,
    val_limit: int = 100,
):
    """Experiment B: Standard BCE fine-tune on Slakh."""
    print("\n" + "=" * 70)
    print("EXPERIMENT B: STANDARD BCE FINE-TUNE")
    print("=" * 70)
    print()

    # Load pretrained model
    print("Loading pretrained model...")
    model = tf.keras.models.load_model(ICASSP_2022_MODEL_PATH, compile=False)

    # Compile with standard BCE loss
    def transcription_loss(y_true, y_pred):
        return tf.keras.losses.binary_crossentropy(y_true, y_pred, label_smoothing=0.2)

    model.compile(
        loss={
            'note': transcription_loss,
            'onset': transcription_loss,
            'contour': transcription_loss,
        },
        optimizer=tf.keras.optimizers.Adam(learning_rate),
    )

    print(f"Model compiled with BCE loss, lr={learning_rate}")

    # Load stems
    train_stems = find_stems(dataset_dir, split='train', limit=train_limit)
    val_stems = find_stems(dataset_dir, split='validation', limit=val_limit)

    print(f"Training stems: {len(train_stems)}")
    print(f"Validation stems: {len(val_stems)}")

    # Create data generators
    train_gen = SlakhDataGenerator(train_stems, batch_size=batch_size, shuffle=True)
    val_gen = SlakhDataGenerator(val_stems, batch_size=batch_size, shuffle=False)

    # Callbacks
    checkpoint_path = output_dir / 'experiment_b' / 'best_model'
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)

    callbacks = [
        tf.keras.callbacks.EarlyStopping(patience=10, verbose=1, restore_best_weights=True),
        tf.keras.callbacks.ReduceLROnPlateau(patience=5, factor=0.5, verbose=1),
        tf.keras.callbacks.ModelCheckpoint(
            str(checkpoint_path),
            save_best_only=True,
            verbose=1,
        ),
    ]

    # Train
    print(f"\nTraining for {epochs} epochs...")
    history = model.fit(
        train_gen,
        epochs=epochs,
        validation_data=val_gen,
        callbacks=callbacks,
        verbose=1,
    )

    # Save training history
    history_path = output_dir / 'experiment_b' / 'history.json'
    with open(history_path, 'w') as f:
        json.dump({k: [float(v) for v in vals] for k, vals in history.history.items()}, f, indent=2)

    print(f"\nTraining complete. Best model saved to: {checkpoint_path}")

    # Evaluate fine-tuned model
    print("\nEvaluating fine-tuned model on validation set...")
    metrics = evaluate_model_on_stems(model, val_stems, limit=val_limit)

    print_metrics(metrics, "EXPERIMENT B: FINE-TUNED MODEL")

    # Save results
    results = {
        'experiment': 'B',
        'description': 'Standard BCE fine-tune on Slakh',
        'n_train_stems': len(train_stems),
        'n_val_stems': len(val_stems),
        'epochs': epochs,
        'batch_size': batch_size,
        'learning_rate': learning_rate,
        'training_history': {k: [float(v) for v in vals] for k, vals in history.history.items()},
        'metrics': asdict(metrics),
    }

    results_path = output_dir / 'experiment_b' / 'results.json'
    with open(results_path, 'w') as f:
        json.dump(results, f, indent=2, default=str)

    print(f"\nResults saved to: {results_path}")

    return metrics


def main():
    parser = argparse.ArgumentParser(description="Basic Pitch Fine-Tuning Experiments")
    parser.add_argument('--dataset', type=str,
                        default='/Users/mattharvey/Sites/tone-forge/datasets/slakh2100_flac_redux',
                        help='Path to Slakh dataset')
    parser.add_argument('--output', type=str,
                        default='/Users/mattharvey/Sites/tone-forge/backend/experiments/basic_pitch_finetune',
                        help='Output directory for results')
    parser.add_argument('--experiment', type=str, default='A',
                        choices=['A', 'B', 'C', 'D', 'all'],
                        help='Which experiment to run')
    parser.add_argument('--limit', type=int, default=100,
                        help='Limit stems for faster testing')
    parser.add_argument('--epochs', type=int, default=50,
                        help='Training epochs')
    parser.add_argument('--batch-size', type=int, default=8,
                        help='Batch size')
    parser.add_argument('--learning-rate', type=float, default=0.0001,
                        help='Learning rate')

    args = parser.parse_args()

    dataset_dir = Path(args.dataset)
    output_dir = Path(args.output)

    if args.experiment == 'A' or args.experiment == 'all':
        run_experiment_a(dataset_dir, output_dir, limit=args.limit)

    if args.experiment == 'B' or args.experiment == 'all':
        run_experiment_b(
            dataset_dir, output_dir,
            epochs=args.epochs,
            batch_size=args.batch_size,
            learning_rate=args.learning_rate,
            train_limit=args.limit * 5,
            val_limit=args.limit,
        )


if __name__ == '__main__':
    main()
