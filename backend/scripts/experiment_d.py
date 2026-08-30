#!/usr/bin/env python3
"""
Experiment D: Octave-Aware Loss

D1: BCE + auxiliary octave-discrimination loss (hard-negative)
    - For GT pitch P, discourage activation at P±12
    - CRITICAL: Never penalize if P±12 is also in GT (polyphony)
    - Lambda sweep: 0.05, 0.10, 0.20

D2: Margin-based octave loss
    - Encourage score(P) > score(P±12) + margin
    - Relative discrimination, not absolute suppression

Goal: Better fundamental-vs-octave discrimination without damaging
      Piano/Guitar representations.
"""
import json
import os
import sys
from pathlib import Path
from collections import defaultdict
from datetime import datetime
import copy

import numpy as np
import yaml
import mido
import tensorflow as tf
from basic_pitch import ICASSP_2022_MODEL_PATH
from basic_pitch.inference import AUDIO_SAMPLE_RATE
from basic_pitch import note_creation as infer
import librosa

# Flush output immediately
sys.stdout = sys.stderr

# Paths
SLAKH_ROOT = Path("/Users/mattharvey/Sites/tone-forge/datasets/slakh2100_flac_redux")
OUTPUT_DIR = Path("/Users/mattharvey/Sites/tone-forge/backend/experiments/basic_pitch_finetune/d")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Constants
AUDIO_N_SAMPLES = 43844
ANNOTATIONS_FPS = AUDIO_SAMPLE_RATE / 256
BATCH_SIZE = 8
EPOCHS = 15  # More epochs to see learning curves
EVAL_EVERY = 1
N_TRAIN_STEMS = 200
N_EVAL_STEMS = 50

# GM classes
GM_CLASSES = {
    (1, 8): "Piano", (9, 16): "Chromatic Percussion",
    (17, 24): "Organ", (25, 32): "Guitar", (33, 40): "Bass",
    (41, 48): "Strings", (49, 56): "Ensemble",
    (57, 64): "Brass", (65, 72): "Reed", (73, 80): "Pipe",
    (81, 88): "Synth Lead", (89, 96): "Synth Pad",
}

def get_inst_class(program: int) -> str:
    for (lo, hi), name in GM_CLASSES.items():
        if lo <= program + 1 <= hi:
            return name
    return "Unknown"


def find_stems(dataset_dir: Path, split: str, max_stems: int = 0):
    """Find stems in Slakh dataset."""
    split_dir = dataset_dir / split
    stems = []
    for track_dir in sorted(split_dir.iterdir()):
        if not track_dir.is_dir():
            continue
        metadata_path = track_dir / "metadata.yaml"
        if not metadata_path.exists():
            continue
        try:
            with open(metadata_path) as f:
                meta = yaml.safe_load(f)
        except:
            continue
        stems_dir = track_dir / "stems"
        midi_dir = track_dir / "MIDI"
        for stem_name, stem_info in meta.get("stems", {}).items():
            audio_path = stems_dir / f"{stem_name}.flac"
            midi_path = midi_dir / f"{stem_name}.mid"
            if not audio_path.exists() or not midi_path.exists():
                continue
            inst_class = get_inst_class(stem_info.get("program_num", 0))
            if inst_class == "Unknown":
                continue
            stems.append({
                "audio_path": audio_path,
                "midi_path": midi_path,
                "inst_class": inst_class,
            })
            if max_stems and len(stems) >= max_stems:
                return stems
    return stems


def load_gt_notes(midi_path):
    """Load ground truth notes from MIDI."""
    try:
        mid = mido.MidiFile(str(midi_path))
    except:
        return []
    tempo = 500000
    for track in mid.tracks:
        for msg in track:
            if msg.type == 'set_tempo':
                tempo = msg.tempo
                break
    notes = []
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
                    notes.append({
                        "pitch": msg.note,
                        "onset": start,
                        "offset": current_time,
                    })
    return notes


def midi_to_matrices(midi_path, n_frames):
    """Convert MIDI to frame-level matrices for training."""
    notes = load_gt_notes(midi_path)

    note_matrix = np.zeros((n_frames, 88), dtype=np.float32)
    onset_matrix = np.zeros((n_frames, 88), dtype=np.float32)

    for note in notes:
        pitch = note["pitch"]
        if pitch < 21 or pitch > 108:
            continue
        pitch_idx = pitch - 21

        onset_frame = int(note["onset"] * ANNOTATIONS_FPS)
        offset_frame = int(note["offset"] * ANNOTATIONS_FPS)

        if onset_frame >= n_frames:
            continue
        offset_frame = min(offset_frame, n_frames - 1)

        note_matrix[onset_frame:offset_frame+1, pitch_idx] = 1.0

        if onset_frame < n_frames:
            onset_matrix[onset_frame, pitch_idx] = 1.0

    return note_matrix, onset_matrix


class SlakhDataGenerator(tf.keras.utils.Sequence):
    """Data generator for Slakh stems."""

    def __init__(self, stems, batch_size=8, n_frames=172):
        self.stems = stems
        self.batch_size = batch_size
        self.n_frames = n_frames
        self.indices = list(range(len(stems)))
        np.random.shuffle(self.indices)

    def __len__(self):
        return len(self.stems) // self.batch_size

    def __getitem__(self, idx):
        batch_indices = self.indices[idx * self.batch_size:(idx + 1) * self.batch_size]

        X = []
        Y_note = []
        Y_onset = []

        for i in batch_indices:
            stem = self.stems[i]

            try:
                audio, _ = librosa.load(str(stem["audio_path"]), sr=AUDIO_SAMPLE_RATE, mono=True)
            except:
                continue

            if len(audio) < AUDIO_N_SAMPLES:
                audio = np.pad(audio, (0, AUDIO_N_SAMPLES - len(audio)))
            else:
                audio = audio[:AUDIO_N_SAMPLES]

            note_mat, onset_mat = midi_to_matrices(stem["midi_path"], self.n_frames)

            X.append(audio.reshape(-1, 1))
            Y_note.append(note_mat)
            Y_onset.append(onset_mat)

        if not X:
            X = [np.zeros((AUDIO_N_SAMPLES, 1), dtype=np.float32)]
            Y_note = [np.zeros((self.n_frames, 88), dtype=np.float32)]
            Y_onset = [np.zeros((self.n_frames, 88), dtype=np.float32)]

        return (
            np.array(X, dtype=np.float32),
            {
                "note": np.array(Y_note, dtype=np.float32),
                "onset": np.array(Y_onset, dtype=np.float32),
                "contour": np.zeros((len(X), self.n_frames, 264), dtype=np.float32),
            }
        )

    def on_epoch_end(self):
        np.random.shuffle(self.indices)


def compute_octave_mask(y_true):
    """
    Compute mask of octave competitors that should NOT be penalized.

    For each GT pitch P, if P+12 or P-12 is ALSO active in GT,
    we should not penalize that octave (real octave doubling).

    Returns mask where 1 = OK to penalize, 0 = genuine octave doubling
    """
    # y_true: (batch, frames, 88)
    batch_size = tf.shape(y_true)[0]
    n_frames = tf.shape(y_true)[1]

    # Shift up by octave (pad left with zeros)
    y_up = tf.concat([tf.zeros((batch_size, n_frames, 12)), y_true[:, :, :-12]], axis=-1)
    # Shift down by octave (pad right with zeros)
    y_down = tf.concat([y_true[:, :, 12:], tf.zeros((batch_size, n_frames, 12))], axis=-1)

    # Mask: penalize octave competitor only if it's NOT in GT
    # If y_up > 0.5, that means P+12 is active, so don't penalize P
    # Actually we need the inverse: we want to penalize P-12 when P is active but P-12 is not

    # For pitch P with GT=1:
    #   - P+12 is a competitor we might penalize (unless P+12 is also GT)
    #   - P-12 is a competitor we might penalize (unless P-12 is also GT)

    # Competitor at P+12: should penalize if y_true[P]=1 AND y_true[P+12]=0
    # Competitor at P-12: should penalize if y_true[P]=1 AND y_true[P-12]=0

    # This will be applied in the loss function
    return y_up, y_down


def octave_hard_negative_loss(y_true, y_pred, lambda_octave=0.1):
    """
    D1: Octave hard-negative loss.

    For each GT pitch P, add BCE loss to suppress activations at P±12,
    UNLESS those pitches are also active in GT (polyphony protection).

    Args:
        y_true: GT note matrix (batch, frames, 88)
        y_pred: Predicted activations (batch, frames, 88)
        lambda_octave: Weight for octave suppression term

    Returns:
        Scalar loss value
    """
    epsilon = 1e-7

    # Standard BCE loss
    bce = -tf.reduce_mean(
        y_true * tf.math.log(y_pred + epsilon) +
        (1 - y_true) * tf.math.log(1 - y_pred + epsilon)
    )

    # Octave suppression loss
    # For each active GT pitch, penalize octave competitors

    batch_size = tf.shape(y_true)[0]
    n_frames = tf.shape(y_true)[1]

    # Create shifted versions of y_true to identify octave competitors
    # y_true_up12[i] = y_true[i-12] (pitch 12 semitones down has GT)
    # y_true_down12[i] = y_true[i+12] (pitch 12 semitones up has GT)

    # Pad and shift for octave up (competitor is 12 semitones higher)
    y_true_shifted_up = tf.concat([
        tf.zeros((batch_size, n_frames, 12), dtype=y_true.dtype),
        y_true[:, :, :-12]
    ], axis=-1)

    # Pad and shift for octave down (competitor is 12 semitones lower)
    y_true_shifted_down = tf.concat([
        y_true[:, :, 12:],
        tf.zeros((batch_size, n_frames, 12), dtype=y_true.dtype)
    ], axis=-1)

    # Predicted activations at octave competitors
    y_pred_up = tf.concat([
        tf.zeros((batch_size, n_frames, 12), dtype=y_pred.dtype),
        y_pred[:, :, :-12]
    ], axis=-1)

    y_pred_down = tf.concat([
        y_pred[:, :, 12:],
        tf.zeros((batch_size, n_frames, 12), dtype=y_pred.dtype)
    ], axis=-1)

    # Mask: penalize competitor only where GT is active AND competitor is NOT in GT
    # For octave up: penalize y_pred[P+12] where y_true[P]=1 AND y_true[P+12]=0
    mask_up = y_true * (1 - y_true_shifted_down)  # GT here, no GT at P+12
    mask_down = y_true * (1 - y_true_shifted_up)  # GT here, no GT at P-12

    # Loss: push octave competitors toward 0 where mask is active
    # BCE with target=0 for competitors
    octave_loss_up = -tf.reduce_sum(mask_up * tf.math.log(1 - y_pred_down + epsilon)) / (tf.reduce_sum(mask_up) + epsilon)
    octave_loss_down = -tf.reduce_sum(mask_down * tf.math.log(1 - y_pred_up + epsilon)) / (tf.reduce_sum(mask_down) + epsilon)

    octave_loss = (octave_loss_up + octave_loss_down) / 2

    total_loss = bce + lambda_octave * octave_loss

    return total_loss, bce, octave_loss


def margin_octave_loss(y_true, y_pred, margin=0.1, lambda_margin=0.1):
    """
    D2: Margin-based octave loss.

    For each GT pitch P, encourage:
        score(P) > score(P+12) + margin
        score(P) > score(P-12) + margin

    Unless P±12 is also in GT (polyphony protection).

    Uses hinge loss: max(0, margin - (score(P) - score(competitor)))

    Args:
        y_true: GT note matrix (batch, frames, 88)
        y_pred: Predicted activations (batch, frames, 88)
        margin: Required margin between GT and competitor
        lambda_margin: Weight for margin term

    Returns:
        Scalar loss value
    """
    epsilon = 1e-7

    # Standard BCE loss
    bce = -tf.reduce_mean(
        y_true * tf.math.log(y_pred + epsilon) +
        (1 - y_true) * tf.math.log(1 - y_pred + epsilon)
    )

    batch_size = tf.shape(y_true)[0]
    n_frames = tf.shape(y_true)[1]

    # Shifted GT for polyphony check
    y_true_up12 = tf.concat([
        y_true[:, :, 12:],
        tf.zeros((batch_size, n_frames, 12), dtype=y_true.dtype)
    ], axis=-1)

    y_true_down12 = tf.concat([
        tf.zeros((batch_size, n_frames, 12), dtype=y_true.dtype),
        y_true[:, :, :-12]
    ], axis=-1)

    # Predicted activations at competitors
    y_pred_up12 = tf.concat([
        y_pred[:, :, 12:],
        tf.zeros((batch_size, n_frames, 12), dtype=y_pred.dtype)
    ], axis=-1)

    y_pred_down12 = tf.concat([
        tf.zeros((batch_size, n_frames, 12), dtype=y_pred.dtype),
        y_pred[:, :, :-12]
    ], axis=-1)

    # Mask: apply margin loss only where GT active AND competitor not in GT
    mask_up = y_true * (1 - y_true_up12)
    mask_down = y_true * (1 - y_true_down12)

    # Hinge loss: max(0, margin - (y_pred - y_pred_competitor))
    hinge_up = tf.nn.relu(margin - (y_pred - y_pred_up12))
    hinge_down = tf.nn.relu(margin - (y_pred - y_pred_down12))

    # Apply mask and average
    margin_loss_up = tf.reduce_sum(mask_up * hinge_up) / (tf.reduce_sum(mask_up) + epsilon)
    margin_loss_down = tf.reduce_sum(mask_down * hinge_down) / (tf.reduce_sum(mask_down) + epsilon)

    margin_loss = (margin_loss_up + margin_loss_down) / 2

    total_loss = bce + lambda_margin * margin_loss

    return total_loss, bce, margin_loss


def run_model_inference(audio, model):
    """Run inference with windowed processing."""
    n_overlapping_frames = 30
    overlap_len = n_overlapping_frames * 256
    hop_size = AUDIO_N_SAMPLES - overlap_len

    if len(audio) < AUDIO_N_SAMPLES:
        audio = np.pad(audio, (0, AUDIO_N_SAMPLES - len(audio)))

    outputs = {"onset": [], "note": [], "contour": []}

    for start in range(0, len(audio) - AUDIO_N_SAMPLES + 1, hop_size):
        window = audio[start:start + AUDIO_N_SAMPLES]
        window = window.reshape(1, -1, 1).astype(np.float32)
        pred = model(window, training=False)
        for key in outputs:
            outputs[key].append(pred[key].numpy()[0])

    if not outputs["onset"]:
        window = audio[:AUDIO_N_SAMPLES]
        if len(window) < AUDIO_N_SAMPLES:
            window = np.pad(window, (0, AUDIO_N_SAMPLES - len(window)))
        window = window.reshape(1, -1, 1).astype(np.float32)
        pred = model(window, training=False)
        for key in outputs:
            outputs[key].append(pred[key].numpy()[0])

    result = {}
    for key in outputs:
        if len(outputs[key]) == 1:
            result[key] = outputs[key][0]
        else:
            result[key] = np.concatenate(outputs[key], axis=0)

    return result


def evaluate_model(model, stems, verbose=True):
    """Evaluate model on stems with detailed per-class metrics."""
    results = {
        "global": defaultdict(float),
        "per_class": defaultdict(lambda: defaultdict(float)),
    }

    for i, stem in enumerate(stems):
        if verbose and (i + 1) % 10 == 0:
            print(f"    Eval [{i+1}/{len(stems)}]...", flush=True)

        inst_class = stem["inst_class"]
        gt_notes = load_gt_notes(stem["midi_path"])
        if not gt_notes:
            continue

        try:
            audio, _ = librosa.load(str(stem["audio_path"]), sr=AUDIO_SAMPLE_RATE, mono=True)
        except:
            continue

        try:
            activations = run_model_inference(audio, model)
        except:
            continue

        n_frames = activations["note"].shape[0]

        # Collect activation stats for GT notes
        frame_acts = []
        onset_acts = []
        gt_margins = []
        gt_ranks = []

        for note in gt_notes:
            pitch = note["pitch"]
            if pitch < 21 or pitch > 108:
                continue
            pitch_idx = pitch - 21
            onset_frame = min(int(note["onset"] * ANNOTATIONS_FPS), n_frames - 1)
            offset_frame = min(int(note["offset"] * ANNOTATIONS_FPS), n_frames - 1)
            if onset_frame >= offset_frame:
                offset_frame = min(onset_frame + 1, n_frames - 1)

            note_act = activations["note"][onset_frame:offset_frame+1, pitch_idx]
            onset_act = activations["onset"][onset_frame, pitch_idx] if onset_frame < activations["onset"].shape[0] else 0

            frame_acts.append(float(note_act.mean()) if len(note_act) > 0 else 0)
            onset_acts.append(float(onset_act))

            # Octave competitors
            oct_up = pitch_idx + 12 if pitch_idx + 12 < 88 else None
            oct_down = pitch_idx - 12 if pitch_idx - 12 >= 0 else None
            oct_up_act = activations["note"][onset_frame:offset_frame+1, oct_up].mean() if oct_up else 0
            oct_down_act = activations["note"][onset_frame:offset_frame+1, oct_down].mean() if oct_down else 0

            gt_margin = (note_act.mean() if len(note_act) > 0 else 0) - max(oct_up_act, oct_down_act)
            gt_margins.append(float(gt_margin))

            # GT rank (position among all 88 pitches)
            mean_acts = activations["note"][onset_frame:offset_frame+1, :].mean(axis=0)
            gt_rank = 88 - np.searchsorted(np.sort(mean_acts), mean_acts[pitch_idx])
            gt_ranks.append(int(gt_rank))

        results["per_class"][inst_class]["n_notes"] += len(gt_margins)
        results["per_class"][inst_class]["sum_frame_act"] += sum(frame_acts)
        results["per_class"][inst_class]["sum_onset_act"] += sum(onset_acts)
        results["per_class"][inst_class]["sum_gt_margin"] += sum(gt_margins)
        results["per_class"][inst_class]["sum_gt_rank"] += sum(gt_ranks)

        # Note matching for recall/octave error
        pred_notes = infer.output_to_notes_polyphonic(
            frames=activations["note"],
            onsets=activations["onset"],
            onset_thresh=0.5,
            frame_thresh=0.3,
            min_note_len=int(np.round(127.7 / 1000 * ANNOTATIONS_FPS)),
            infer_onsets=True,
            min_freq=None,
            max_freq=None,
            melodia_trick=True,
            energy_tol=11,
        )

        pred_notes = [(f[0] / ANNOTATIONS_FPS, f[1] / ANNOTATIONS_FPS, f[2] + 21, f[3]) for f in pred_notes]

        n_gt = len(gt_notes)
        n_correct = 0
        n_octave = 0
        pred_used = set()

        for gt in gt_notes:
            gt_pitch = gt["pitch"]
            gt_onset = gt["onset"]

            for pi, pred in enumerate(pred_notes):
                if pi in pred_used:
                    continue
                if abs(pred[0] - gt_onset) > 0.05:
                    continue
                if pred[2] == gt_pitch:
                    n_correct += 1
                    pred_used.add(pi)
                    break
                elif (pred[2] - gt_pitch) % 12 == 0:
                    n_octave += 1
                    pred_used.add(pi)
                    break

        results["global"]["n_gt"] += n_gt
        results["global"]["n_correct"] += n_correct
        results["global"]["n_octave"] += n_octave
        results["global"]["n_pred"] += len(pred_notes)

        results["per_class"][inst_class]["n_gt"] += n_gt
        results["per_class"][inst_class]["n_correct"] += n_correct
        results["per_class"][inst_class]["n_octave"] += n_octave

    # Compute aggregate metrics
    g = results["global"]
    g["recall"] = g["n_correct"] / g["n_gt"] if g["n_gt"] > 0 else 0
    g["precision"] = g["n_correct"] / g["n_pred"] if g["n_pred"] > 0 else 0
    g["f1"] = 2 * g["precision"] * g["recall"] / (g["precision"] + g["recall"]) if (g["precision"] + g["recall"]) > 0 else 0
    g["octave_error_rate"] = g["n_octave"] / g["n_gt"] if g["n_gt"] > 0 else 0

    for cls, stats in results["per_class"].items():
        stats["recall"] = stats["n_correct"] / stats["n_gt"] if stats["n_gt"] > 0 else 0
        stats["octave_error_rate"] = stats["n_octave"] / stats["n_gt"] if stats["n_gt"] > 0 else 0

        n = stats["n_notes"]
        if n > 0:
            stats["mean_frame_act"] = stats["sum_frame_act"] / n
            stats["mean_onset_act"] = stats["sum_onset_act"] / n
            stats["mean_gt_margin"] = stats["sum_gt_margin"] / n
            stats["mean_gt_rank"] = stats["sum_gt_rank"] / n
        else:
            stats["mean_frame_act"] = 0
            stats["mean_onset_act"] = 0
            stats["mean_gt_margin"] = 0
            stats["mean_gt_rank"] = 88

        # Clean up sums
        del stats["sum_frame_act"]
        del stats["sum_onset_act"]
        del stats["sum_gt_margin"]
        del stats["sum_gt_rank"]
        del stats["n_notes"]

    return dict(results)


def compute_param_distance(model, pretrained_weights):
    """Compute L2 distance of parameters from pretrained checkpoint."""
    distances = {}
    for layer in model.layers:
        if layer.weights:
            layer_dist = 0
            for w, pw in zip(layer.weights, pretrained_weights.get(layer.name, [])):
                layer_dist += float(np.sum((w.numpy() - pw) ** 2))
            distances[layer.name] = np.sqrt(layer_dist)
    return distances


def freeze_onset_head(model):
    """Freeze onset head to protect it from degradation."""
    for layer in model.layers:
        if layer.name == "conv2d_5":  # Onset head
            layer.trainable = False
            print(f"  Frozen onset head: {layer.name} ({layer.count_params()} params)")


def run_experiment_d1(variant_name, lambda_octave, train_stems, val_stems,
                      pretrained_weights, freeze_onset=True):
    """Run D1: Octave hard-negative loss experiment."""
    print(f"\n{'='*70}", flush=True)
    print(f"EXPERIMENT {variant_name}: BCE + Octave Hard-Negative (λ={lambda_octave})", flush=True)
    print(f"{'='*70}", flush=True)

    # Load fresh model
    model = tf.keras.models.load_model(str(ICASSP_2022_MODEL_PATH), compile=False)

    if freeze_onset:
        freeze_onset_head(model)

    # Optimizer
    optimizer = tf.keras.optimizers.Adam(learning_rate=1e-5)

    # Data generator
    train_gen = SlakhDataGenerator(train_stems, batch_size=BATCH_SIZE)

    history = {
        "epochs": [],
        "train_loss": [],
        "train_bce": [],
        "train_octave_loss": [],
        "eval_metrics": [],
        "param_distances": [],
    }

    best_f1 = 0
    best_epoch = 0
    best_model_path = OUTPUT_DIR / f"{variant_name}_best_model"

    print(f"\nTraining for {EPOCHS} epochs...", flush=True)
    for epoch in range(EPOCHS):
        print(f"\n  Epoch {epoch+1}/{EPOCHS}", flush=True)

        epoch_losses = []
        epoch_bce = []
        epoch_octave = []

        for batch_idx in range(len(train_gen)):
            X, Y = train_gen[batch_idx]
            y_note = Y["note"]
            y_onset = Y["onset"]

            with tf.GradientTape() as tape:
                pred = model(X, training=True)

                # Note loss with octave term
                note_loss, bce, oct_loss = octave_hard_negative_loss(
                    tf.cast(y_note, tf.float32),
                    pred["note"],
                    lambda_octave=lambda_octave
                )

                # Standard BCE for onset (protected)
                onset_loss = tf.keras.losses.binary_crossentropy(
                    y_onset, pred["onset"]
                )
                onset_loss = tf.reduce_mean(onset_loss)

                # Contour loss (minimal weight)
                contour_loss = tf.keras.losses.binary_crossentropy(
                    Y["contour"], pred["contour"]
                )
                contour_loss = tf.reduce_mean(contour_loss)

                total_loss = note_loss + onset_loss + 0.1 * contour_loss

            # Get trainable variables
            trainable_vars = [v for v in model.trainable_variables]
            grads = tape.gradient(total_loss, trainable_vars)
            optimizer.apply_gradients(zip(grads, trainable_vars))

            epoch_losses.append(float(total_loss))
            epoch_bce.append(float(bce))
            epoch_octave.append(float(oct_loss))

        train_loss = np.mean(epoch_losses)
        train_bce = np.mean(epoch_bce)
        train_octave = np.mean(epoch_octave)

        print(f"    Train: loss={train_loss:.4f}, BCE={train_bce:.4f}, OctLoss={train_octave:.4f}", flush=True)

        train_gen.on_epoch_end()

        # Evaluate
        print(f"    Evaluating...", flush=True)
        metrics = evaluate_model(model, val_stems, verbose=False)

        # Parameter distances
        param_dist = compute_param_distance(model, pretrained_weights)

        history["epochs"].append(epoch + 1)
        history["train_loss"].append(float(train_loss))
        history["train_bce"].append(float(train_bce))
        history["train_octave_loss"].append(float(train_octave))
        history["eval_metrics"].append(metrics)
        history["param_distances"].append(param_dist)

        # Print summary
        g = metrics["global"]
        print(f"    Global: F1={g['f1']:.3f}, Recall={g['recall']:.3f}, OctErr={g['octave_error_rate']:.3f}", flush=True)

        for cls in ["Bass", "Piano", "Guitar", "Synth Lead", "Organ", "Chromatic Percussion"]:
            if cls in metrics["per_class"]:
                c = metrics["per_class"][cls]
                print(f"    {cls}: Recall={c['recall']:.3f}, OctErr={c['octave_error_rate']:.3f}, Margin={c['mean_gt_margin']:.4f}", flush=True)

        # Save checkpoint for early epochs (critical for Pareto analysis)
        if epoch + 1 <= 5:
            checkpoint_path = OUTPUT_DIR / f"{variant_name}_epoch{epoch+1}"
            model.save(str(checkpoint_path))
            print(f"    ** Saved checkpoint: epoch {epoch+1}", flush=True)

        # Save best model
        if g["f1"] > best_f1:
            best_f1 = g["f1"]
            best_epoch = epoch + 1
            model.save(str(best_model_path))
            print(f"    ** New best F1: {best_f1:.4f} at epoch {best_epoch}", flush=True)

    print(f"\n  Best F1: {best_f1:.4f} at epoch {best_epoch}", flush=True)

    # Save history
    history_path = OUTPUT_DIR / f"{variant_name}_history.json"
    with open(history_path, "w") as f:
        def convert(obj):
            if isinstance(obj, np.floating):
                return float(obj)
            elif isinstance(obj, np.integer):
                return int(obj)
            elif isinstance(obj, dict):
                return {k: convert(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [convert(x) for x in obj]
            return obj
        json.dump(convert(history), f, indent=2)

    return history, best_epoch, best_f1


def run_experiment_d2(variant_name, lambda_margin, margin, train_stems, val_stems,
                      pretrained_weights, freeze_onset=True):
    """Run D2: Margin-based octave loss experiment."""
    print(f"\n{'='*70}", flush=True)
    print(f"EXPERIMENT {variant_name}: BCE + Margin Loss (λ={lambda_margin}, M={margin})", flush=True)
    print(f"{'='*70}", flush=True)

    model = tf.keras.models.load_model(str(ICASSP_2022_MODEL_PATH), compile=False)

    if freeze_onset:
        freeze_onset_head(model)

    optimizer = tf.keras.optimizers.Adam(learning_rate=1e-5)
    train_gen = SlakhDataGenerator(train_stems, batch_size=BATCH_SIZE)

    history = {
        "epochs": [],
        "train_loss": [],
        "train_bce": [],
        "train_margin_loss": [],
        "eval_metrics": [],
        "param_distances": [],
    }

    best_f1 = 0
    best_epoch = 0
    best_model_path = OUTPUT_DIR / f"{variant_name}_best_model"

    print(f"\nTraining for {EPOCHS} epochs...", flush=True)
    for epoch in range(EPOCHS):
        print(f"\n  Epoch {epoch+1}/{EPOCHS}", flush=True)

        epoch_losses = []
        epoch_bce = []
        epoch_margin = []

        for batch_idx in range(len(train_gen)):
            X, Y = train_gen[batch_idx]
            y_note = Y["note"]
            y_onset = Y["onset"]

            with tf.GradientTape() as tape:
                pred = model(X, training=True)

                note_loss, bce, margin_loss = margin_octave_loss(
                    tf.cast(y_note, tf.float32),
                    pred["note"],
                    margin=margin,
                    lambda_margin=lambda_margin
                )

                onset_loss = tf.reduce_mean(tf.keras.losses.binary_crossentropy(
                    y_onset, pred["onset"]
                ))

                contour_loss = tf.reduce_mean(tf.keras.losses.binary_crossentropy(
                    Y["contour"], pred["contour"]
                ))

                total_loss = note_loss + onset_loss + 0.1 * contour_loss

            trainable_vars = [v for v in model.trainable_variables]
            grads = tape.gradient(total_loss, trainable_vars)
            optimizer.apply_gradients(zip(grads, trainable_vars))

            epoch_losses.append(float(total_loss))
            epoch_bce.append(float(bce))
            epoch_margin.append(float(margin_loss))

        train_loss = np.mean(epoch_losses)
        train_bce = np.mean(epoch_bce)
        train_margin = np.mean(epoch_margin)

        print(f"    Train: loss={train_loss:.4f}, BCE={train_bce:.4f}, MarginLoss={train_margin:.4f}", flush=True)

        train_gen.on_epoch_end()

        print(f"    Evaluating...", flush=True)
        metrics = evaluate_model(model, val_stems, verbose=False)
        param_dist = compute_param_distance(model, pretrained_weights)

        history["epochs"].append(epoch + 1)
        history["train_loss"].append(float(train_loss))
        history["train_bce"].append(float(train_bce))
        history["train_margin_loss"].append(float(train_margin))
        history["eval_metrics"].append(metrics)
        history["param_distances"].append(param_dist)

        g = metrics["global"]
        print(f"    Global: F1={g['f1']:.3f}, Recall={g['recall']:.3f}, OctErr={g['octave_error_rate']:.3f}", flush=True)

        for cls in ["Bass", "Piano", "Guitar", "Synth Lead", "Organ", "Chromatic Percussion"]:
            if cls in metrics["per_class"]:
                c = metrics["per_class"][cls]
                print(f"    {cls}: Recall={c['recall']:.3f}, OctErr={c['octave_error_rate']:.3f}, Margin={c['mean_gt_margin']:.4f}", flush=True)

        # Save checkpoint for early epochs (critical for Pareto analysis)
        if epoch + 1 <= 5:
            checkpoint_path = OUTPUT_DIR / f"{variant_name}_epoch{epoch+1}"
            model.save(str(checkpoint_path))
            print(f"    ** Saved checkpoint: epoch {epoch+1}", flush=True)

        if g["f1"] > best_f1:
            best_f1 = g["f1"]
            best_epoch = epoch + 1
            model.save(str(best_model_path))
            print(f"    ** New best F1: {best_f1:.4f} at epoch {best_epoch}", flush=True)

    print(f"\n  Best F1: {best_f1:.4f} at epoch {best_epoch}", flush=True)

    history_path = OUTPUT_DIR / f"{variant_name}_history.json"
    with open(history_path, "w") as f:
        def convert(obj):
            if isinstance(obj, np.floating):
                return float(obj)
            elif isinstance(obj, np.integer):
                return int(obj)
            elif isinstance(obj, dict):
                return {k: convert(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [convert(x) for x in obj]
            return obj
        json.dump(convert(history), f, indent=2)

    return history, best_epoch, best_f1


def main():
    print("="*70, flush=True)
    print("EXPERIMENT D: OCTAVE-AWARE LOSS", flush=True)
    print("="*70, flush=True)
    print(f"Start time: {datetime.now()}", flush=True)

    # Load data
    print("\nLoading data...", flush=True)
    train_stems = find_stems(SLAKH_ROOT, "train", max_stems=N_TRAIN_STEMS)
    val_stems = find_stems(SLAKH_ROOT, "validation", max_stems=N_EVAL_STEMS)
    print(f"Train stems: {len(train_stems)}, Val stems: {len(val_stems)}", flush=True)

    # Store pretrained weights for distance tracking
    print("\nLoading pretrained model for reference...", flush=True)
    pretrained_model = tf.keras.models.load_model(str(ICASSP_2022_MODEL_PATH), compile=False)
    pretrained_weights = {}
    for layer in pretrained_model.layers:
        if layer.weights:
            pretrained_weights[layer.name] = [w.numpy().copy() for w in layer.weights]

    # Evaluate pretrained baseline
    print("\nEvaluating pretrained baseline...", flush=True)
    baseline_metrics = evaluate_model(pretrained_model, val_stems)

    print("\nPRETRAINED BASELINE:", flush=True)
    g = baseline_metrics["global"]
    print(f"  Global: F1={g['f1']:.3f}, Recall={g['recall']:.3f}, OctErr={g['octave_error_rate']:.3f}", flush=True)
    for cls in ["Bass", "Piano", "Guitar", "Synth Lead", "Organ", "Chromatic Percussion", "Reed", "Brass"]:
        if cls in baseline_metrics["per_class"]:
            c = baseline_metrics["per_class"][cls]
            print(f"  {cls}: Recall={c['recall']:.3f}, OctErr={c['octave_error_rate']:.3f}, Margin={c['mean_gt_margin']:.4f}", flush=True)

    with open(OUTPUT_DIR / "baseline_metrics.json", "w") as f:
        def convert(obj):
            if isinstance(obj, (np.floating, float)):
                return float(obj)
            elif isinstance(obj, (np.integer, int)):
                return int(obj)
            elif isinstance(obj, dict):
                return {k: convert(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [convert(x) for x in obj]
            return obj
        json.dump(convert(baseline_metrics), f, indent=2)

    results_summary = {}

    # =========================================================
    # D1: Octave Hard-Negative Loss - Lambda sweep
    # =========================================================
    print("\n" + "="*70, flush=True)
    print("PHASE D1: OCTAVE HARD-NEGATIVE LOSS", flush=True)
    print("="*70, flush=True)

    for lambda_val in [0.05, 0.10, 0.20]:
        variant_name = f"D1_lambda{lambda_val:.2f}"
        history, best_epoch, best_f1 = run_experiment_d1(
            variant_name, lambda_val, train_stems, val_stems, pretrained_weights
        )
        results_summary[variant_name] = {
            "type": "D1",
            "lambda": lambda_val,
            "best_epoch": best_epoch,
            "best_f1": best_f1,
        }

    # =========================================================
    # D2: Margin-Based Octave Loss
    # =========================================================
    print("\n" + "="*70, flush=True)
    print("PHASE D2: MARGIN-BASED OCTAVE LOSS", flush=True)
    print("="*70, flush=True)

    for lambda_val, margin_val in [(0.10, 0.05), (0.10, 0.10), (0.20, 0.10)]:
        variant_name = f"D2_lambda{lambda_val:.2f}_margin{margin_val:.2f}"
        history, best_epoch, best_f1 = run_experiment_d2(
            variant_name, lambda_val, margin_val, train_stems, val_stems, pretrained_weights
        )
        results_summary[variant_name] = {
            "type": "D2",
            "lambda": lambda_val,
            "margin": margin_val,
            "best_epoch": best_epoch,
            "best_f1": best_f1,
        }

    # Final summary
    print("\n" + "="*70, flush=True)
    print("FINAL SUMMARY", flush=True)
    print("="*70, flush=True)

    print(f"\n{'Variant':<30} {'Type':<6} {'Best Epoch':>12} {'Best F1':>10}", flush=True)
    print("-"*70, flush=True)
    for name, res in results_summary.items():
        print(f"{name:<30} {res['type']:<6} {res['best_epoch']:>12} {res['best_f1']:>10.4f}", flush=True)

    with open(OUTPUT_DIR / "summary.json", "w") as f:
        json.dump(results_summary, f, indent=2)

    print(f"\nEnd time: {datetime.now()}", flush=True)
    print(f"Results saved to {OUTPUT_DIR}", flush=True)


if __name__ == "__main__":
    main()
