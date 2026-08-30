#!/usr/bin/env python3
"""
Experiment B3: Conservative Fine-Tuning
Ablation: learning rate / trainable layers

B3.1: LR=1e-5, no freezing
B3.2: LR=1e-5, freeze shared backbone (conv2d_1 + batch_norms)
B3.3: LR=1e-5, freeze backbone + note pathway, train only output convs
B3.4: LR=3e-5, best freezing config from B3.2/B3.3
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

# Paths
SLAKH_ROOT = Path("/Users/mattharvey/Sites/tone-forge/datasets/slakh2100_flac_redux")
OUTPUT_DIR = Path("/Users/mattharvey/Sites/tone-forge/backend/experiments/basic_pitch_finetune/b3")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Constants
AUDIO_N_SAMPLES = 43844
ANNOTATIONS_FPS = AUDIO_SAMPLE_RATE / 256
BATCH_SIZE = 8
EPOCHS = 10
EVAL_EVERY = 1  # Evaluate after every epoch
N_EVAL_STEMS = 50  # Smaller for speed

# GM classes
GM_CLASSES = {
    (1, 8): "Piano", (9, 16): "Chromatic Percussion",
    (17, 24): "Organ", (25, 32): "Guitar", (33, 40): "Bass",
    (41, 48): "Strings (continued)", (49, 56): "Ensemble",
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

    # Initialize matrices
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

        # Note frames
        note_matrix[onset_frame:offset_frame+1, pitch_idx] = 1.0

        # Onset frame
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

            # Pad/trim to exact length
            if len(audio) < AUDIO_N_SAMPLES:
                audio = np.pad(audio, (0, AUDIO_N_SAMPLES - len(audio)))
            else:
                audio = audio[:AUDIO_N_SAMPLES]

            note_mat, onset_mat = midi_to_matrices(stem["midi_path"], self.n_frames)

            X.append(audio.reshape(-1, 1))
            Y_note.append(note_mat)
            Y_onset.append(onset_mat)

        if not X:
            # Return dummy batch if all failed
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
    """Evaluate model on stems, return detailed metrics."""
    results = {
        "global": defaultdict(float),
        "per_class": defaultdict(lambda: defaultdict(float)),
        "activations": defaultdict(list),
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

            # Octave competitors
            oct_up = pitch_idx + 12 if pitch_idx + 12 < 88 else None
            oct_down = pitch_idx - 12 if pitch_idx - 12 >= 0 else None
            oct_up_act = activations["note"][onset_frame:offset_frame+1, oct_up].mean() if oct_up else 0
            oct_down_act = activations["note"][onset_frame:offset_frame+1, oct_down].mean() if oct_down else 0

            gt_margin = (note_act.mean() if len(note_act) > 0 else 0) - max(oct_up_act, oct_down_act)

            results["activations"][inst_class].append({
                "frame_act": float(note_act.mean()) if len(note_act) > 0 else 0,
                "onset_act": float(onset_act),
                "gt_margin": float(gt_margin),
            })

        # Extract notes and compute metrics
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

        # Convert to time-based
        pred_notes = [(f[0] / ANNOTATIONS_FPS, f[1] / ANNOTATIONS_FPS, f[2] + 21, f[3]) for f in pred_notes]

        # Match notes
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

        # Aggregate activations
        acts = results["activations"].get(cls, [])
        if acts:
            stats["mean_frame_act"] = np.mean([a["frame_act"] for a in acts])
            stats["mean_onset_act"] = np.mean([a["onset_act"] for a in acts])
            stats["mean_gt_margin"] = np.mean([a["gt_margin"] for a in acts])
        else:
            stats["mean_frame_act"] = 0
            stats["mean_onset_act"] = 0
            stats["mean_gt_margin"] = 0

    return dict(results)


def freeze_layers(model, freeze_config):
    """
    Freeze layers based on config.

    freeze_config options:
    - "none": all trainable
    - "backbone": freeze conv2d_1 + batch_normalizations
    - "backbone+pathway": freeze backbone + conv2d_4, train only output convs
    """
    # First make all trainable
    for layer in model.layers:
        layer.trainable = True

    if freeze_config == "none":
        return

    backbone_layers = ["batch_normalization", "conv2d_1", "batch_normalization_2"]
    pathway_layers = ["conv2d_4", "batch_normalization_3", "conv2d_2"]

    if freeze_config == "backbone":
        for layer in model.layers:
            if layer.name in backbone_layers:
                layer.trainable = False
                print(f"  Frozen: {layer.name} ({layer.count_params()} params)")

    elif freeze_config == "backbone+pathway":
        for layer in model.layers:
            if layer.name in backbone_layers + pathway_layers:
                layer.trainable = False
                print(f"  Frozen: {layer.name} ({layer.count_params()} params)")

    # Count trainable params
    trainable = sum(l.count_params() for l in model.layers if l.trainable and l.count_params() > 0)
    frozen = model.count_params() - trainable
    print(f"  Trainable: {trainable:,} / Frozen: {frozen:,}")


def run_experiment(variant_name, lr, freeze_config, train_stems, val_stems):
    """Run a single B3 experiment variant."""
    print(f"\n{'='*70}")
    print(f"EXPERIMENT {variant_name}: LR={lr}, Freeze={freeze_config}")
    print(f"{'='*70}")

    # Load fresh model
    model = tf.keras.models.load_model(str(ICASSP_2022_MODEL_PATH), compile=False)

    # Apply freezing
    print("Freezing layers...")
    freeze_layers(model, freeze_config)

    # Compile
    model.compile(
        optimizer=tf.keras.optimizers.Adam(learning_rate=lr),
        loss={
            "note": tf.keras.losses.BinaryCrossentropy(label_smoothing=0.2),
            "onset": tf.keras.losses.BinaryCrossentropy(label_smoothing=0.2),
            "contour": tf.keras.losses.BinaryCrossentropy(label_smoothing=0.2),
        },
    )

    # Data generator
    train_gen = SlakhDataGenerator(train_stems, batch_size=BATCH_SIZE)

    # Track results per epoch
    history = {
        "epochs": [],
        "train_loss": [],
        "eval_metrics": [],
    }

    best_f1 = 0
    best_epoch = 0
    best_model_path = OUTPUT_DIR / f"{variant_name}_best_model"

    print(f"\nTraining for {EPOCHS} epochs...")
    for epoch in range(EPOCHS):
        print(f"\n  Epoch {epoch+1}/{EPOCHS}")

        # Train one epoch
        epoch_losses = []
        for batch_idx in range(len(train_gen)):
            X, Y = train_gen[batch_idx]
            loss = model.train_on_batch(X, Y)
            epoch_losses.append(loss[0] if isinstance(loss, list) else loss)

        train_loss = np.mean(epoch_losses)
        print(f"    Train loss: {train_loss:.4f}")

        train_gen.on_epoch_end()

        # Evaluate
        if (epoch + 1) % EVAL_EVERY == 0:
            print(f"    Evaluating...")
            metrics = evaluate_model(model, val_stems, verbose=False)

            history["epochs"].append(epoch + 1)
            history["train_loss"].append(float(train_loss))
            history["eval_metrics"].append(metrics)

            # Print summary
            g = metrics["global"]
            print(f"    Global: F1={g['f1']:.3f}, Recall={g['recall']:.3f}, OctErr={g['octave_error_rate']:.3f}")

            for cls in ["Bass", "Piano", "Guitar", "Reed"]:
                if cls in metrics["per_class"]:
                    c = metrics["per_class"][cls]
                    print(f"    {cls}: Recall={c['recall']:.3f}, OctErr={c['octave_error_rate']:.3f}, Margin={c['mean_gt_margin']:.4f}")

            # Save best model
            if g["f1"] > best_f1:
                best_f1 = g["f1"]
                best_epoch = epoch + 1
                model.save(str(best_model_path))
                print(f"    ** New best F1: {best_f1:.4f} at epoch {best_epoch}")

    # Final summary
    print(f"\n  Best F1: {best_f1:.4f} at epoch {best_epoch}")

    # Save history
    history_path = OUTPUT_DIR / f"{variant_name}_history.json"
    with open(history_path, "w") as f:
        # Convert numpy types
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
    print("="*70)
    print("EXPERIMENT B3: CONSERVATIVE FINE-TUNING")
    print("="*70)
    print(f"Start time: {datetime.now()}")

    # Load data
    print("\nLoading data...")
    train_stems = find_stems(SLAKH_ROOT, "train", max_stems=200)  # Subset for speed
    val_stems = find_stems(SLAKH_ROOT, "validation", max_stems=N_EVAL_STEMS)
    print(f"Train stems: {len(train_stems)}, Val stems: {len(val_stems)}")

    # Evaluate pretrained baseline first
    print("\nEvaluating pretrained baseline...")
    pretrained_model = tf.keras.models.load_model(str(ICASSP_2022_MODEL_PATH), compile=False)
    baseline_metrics = evaluate_model(pretrained_model, val_stems)

    print("\nPRETRAINED BASELINE:")
    g = baseline_metrics["global"]
    print(f"  Global: F1={g['f1']:.3f}, Recall={g['recall']:.3f}, OctErr={g['octave_error_rate']:.3f}")
    for cls in ["Bass", "Piano", "Guitar", "Reed", "Brass"]:
        if cls in baseline_metrics["per_class"]:
            c = baseline_metrics["per_class"][cls]
            print(f"  {cls}: Recall={c['recall']:.3f}, OctErr={c['octave_error_rate']:.3f}, Margin={c['mean_gt_margin']:.4f}")

    # Save baseline
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

    # Run experiments
    experiments = [
        ("B3.1", 1e-5, "none"),
        ("B3.2", 1e-5, "backbone"),
        ("B3.3", 1e-5, "backbone+pathway"),
    ]

    results_summary = {}

    for name, lr, freeze in experiments:
        history, best_epoch, best_f1 = run_experiment(name, lr, freeze, train_stems, val_stems)
        results_summary[name] = {
            "lr": lr,
            "freeze": freeze,
            "best_epoch": best_epoch,
            "best_f1": best_f1,
            "history": history,
        }

    # Determine best config for B3.4
    best_config = max(results_summary.items(), key=lambda x: x[1]["best_f1"])
    print(f"\nBest config from B3.1-B3.3: {best_config[0]} with F1={best_config[1]['best_f1']:.4f}")

    # Run B3.4 with higher LR using best freeze config
    best_freeze = best_config[1]["freeze"]
    history, best_epoch, best_f1 = run_experiment("B3.4", 3e-5, best_freeze, train_stems, val_stems)
    results_summary["B3.4"] = {
        "lr": 3e-5,
        "freeze": best_freeze,
        "best_epoch": best_epoch,
        "best_f1": best_f1,
        "history": history,
    }

    # Final summary
    print("\n" + "="*70)
    print("FINAL SUMMARY")
    print("="*70)

    print(f"\n{'Variant':<10} {'LR':>10} {'Freeze':>20} {'Best Epoch':>12} {'Best F1':>10}")
    print("-"*70)
    for name, res in results_summary.items():
        print(f"{name:<10} {res['lr']:>10.0e} {res['freeze']:>20} {res['best_epoch']:>12} {res['best_f1']:>10.4f}")

    # Save summary
    with open(OUTPUT_DIR / "summary.json", "w") as f:
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
        json.dump(convert(results_summary), f, indent=2)

    print(f"\nEnd time: {datetime.now()}")
    print(f"Results saved to {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
