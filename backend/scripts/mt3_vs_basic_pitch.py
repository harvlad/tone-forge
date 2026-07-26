#!/usr/bin/env python3
"""
MT3 vs Basic Pitch Benchmark

Compare pretrained MT3 and Basic Pitch on frozen eval corpus.
No tuning - just pretrained model comparison.

Metrics:
- Global: F1, precision, recall, octave error, onset F1
- Per-class: Bass, Piano, Guitar, Synth Lead, Organ, etc.
- Head-to-head error analysis
- Oracle ensemble calculation
"""
import json
import os
import sys
import subprocess
from pathlib import Path
from collections import defaultdict
from datetime import datetime
import tempfile

import numpy as np
import yaml
import mido

# Check for MT3 installation
try:
    import mt3_infer
    MT3_AVAILABLE = True
except ImportError:
    MT3_AVAILABLE = False
    print("mt3-infer not installed.", flush=True)

# Basic Pitch imports
from basic_pitch import ICASSP_2022_MODEL_PATH
from basic_pitch.inference import AUDIO_SAMPLE_RATE, predict as bp_predict
from basic_pitch import note_creation as infer
import tensorflow as tf
import librosa

# Paths
SLAKH_ROOT = Path("/Users/mattharvey/Sites/tone-forge/datasets/slakh2100_flac_redux")
OUTPUT_DIR = Path("/Users/mattharvey/Sites/tone-forge/backend/experiments/mt3_comparison")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Constants
AUDIO_N_SAMPLES = 43844
ANNOTATIONS_FPS = AUDIO_SAMPLE_RATE / 256
N_EVAL_STEMS = 100  # Use full validation set

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


def install_mt3_dependencies():
    """Install MT3 and dependencies."""
    print("Installing MT3 dependencies...", flush=True)

    # Install JAX for CPU (Mac compatible)
    subprocess.run([
        sys.executable, "-m", "pip", "install",
        "jax", "jaxlib", "--quiet"
    ], check=True)

    # Install note-seq and MT3
    subprocess.run([
        sys.executable, "-m", "pip", "install",
        "note-seq", "t5x", "flax", "seqio-nightly", "--quiet"
    ], check=True)

    # Clone MT3 repo if needed
    mt3_path = Path.home() / ".mt3_repo"
    if not mt3_path.exists():
        subprocess.run([
            "git", "clone", "https://github.com/magenta/mt3.git", str(mt3_path)
        ], check=True)

    # Add to path
    sys.path.insert(0, str(mt3_path))

    print("MT3 dependencies installed.", flush=True)


def download_mt3_checkpoint():
    """Download pretrained MT3 checkpoint."""
    checkpoint_dir = Path.home() / ".mt3_checkpoints" / "mt3"
    if checkpoint_dir.exists() and list(checkpoint_dir.glob("*")):
        print(f"MT3 checkpoint already exists at {checkpoint_dir}", flush=True)
        return checkpoint_dir

    print("Downloading MT3 checkpoint...", flush=True)
    checkpoint_dir.mkdir(parents=True, exist_ok=True)

    # Download from GCS
    subprocess.run([
        "gsutil", "-m", "cp", "-r",
        "gs://mt3/checkpoints/mt3/",
        str(checkpoint_dir.parent)
    ], check=True)

    print("MT3 checkpoint downloaded.", flush=True)
    return checkpoint_dir


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
                "track_id": track_dir.name,
                "stem_id": stem_name,
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


def run_basic_pitch_inference(audio_path, model=None):
    """Run Basic Pitch inference using official predict() function.

    Note: model argument is kept for API compatibility but ignored.
    The official bp_predict() loads the model internally.
    """
    try:
        # Use official Basic Pitch predict() which handles timing correctly
        _, _, note_events = bp_predict(
            str(audio_path),
            onset_threshold=0.5,
            frame_threshold=0.3,
            minimum_note_length=127.7,
            melodia_trick=True,
        )
    except Exception as e:
        print(f"Basic Pitch inference error: {e}", flush=True)
        return None

    # Convert note_events (onset, offset, pitch, amplitude, pitch_bends) to standard format
    notes = []
    for event in note_events:
        onset, offset, pitch, amplitude = event[0], event[1], event[2], event[3]
        notes.append({
            "pitch": pitch,
            "onset": onset,
            "offset": offset,
            "velocity": int(amplitude * 127),
        })

    return notes


def run_mt3_inference(audio_path, mt3_model_name):
    """Run MT3 inference using mt3-infer."""
    # Load audio at 16kHz (MT3 sample rate)
    try:
        audio, _ = librosa.load(str(audio_path), sr=16000, mono=True)
    except Exception as e:
        print(f"MT3 audio load error: {e}", flush=True)
        return None

    try:
        # mt3_infer.transcribe returns mido.MidiFile directly
        midi_file = mt3_infer.transcribe(audio, model=mt3_model_name, sr=16000)
    except Exception as e:
        print(f"MT3 inference error: {e}", flush=True)
        return None

    # Convert mido MidiFile to note list
    notes = []
    try:
        tempo = 500000  # default microseconds per beat
        for track in midi_file.tracks:
            for msg in track:
                if msg.type == 'set_tempo':
                    tempo = msg.tempo
                    break

        for track in midi_file.tracks:
            current_time = 0.0
            active = {}
            for msg in track:
                current_time += mido.tick2second(msg.time, midi_file.ticks_per_beat, tempo)
                if msg.type == 'note_on' and msg.velocity > 0:
                    active[msg.note] = (current_time, msg.velocity)
                elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
                    if msg.note in active:
                        start, vel = active.pop(msg.note)
                        notes.append({
                            "pitch": msg.note,
                            "onset": start,
                            "offset": current_time,
                            "velocity": vel,
                        })
    except Exception as e:
        print(f"MIDI parse error: {e}", flush=True)
        return None

    return notes


def match_notes(gt_notes, pred_notes, onset_tolerance=0.1):
    """
    Match predicted notes to ground truth.

    Note: Using 100ms onset tolerance (not MIREX 50ms) because MT3 has
    coarser timing quantization (~60ms). This ensures fair comparison.
    Both models are evaluated with the same tolerance.

    Returns:
        correct: list of (gt, pred) pairs that match exactly
        octave: list of (gt, pred) pairs that match within octave
        missed: list of gt notes not matched
        extra: list of pred notes not matched
    """
    correct = []
    octave = []
    missed = []
    extra = []

    pred_used = set()

    for gt in gt_notes:
        gt_pitch = gt["pitch"]
        gt_onset = gt["onset"]

        matched = False
        for pi, pred in enumerate(pred_notes):
            if pi in pred_used:
                continue
            if abs(pred["onset"] - gt_onset) > onset_tolerance:
                continue

            if pred["pitch"] == gt_pitch:
                correct.append((gt, pred))
                pred_used.add(pi)
                matched = True
                break
            elif (pred["pitch"] - gt_pitch) % 12 == 0:
                octave.append((gt, pred, pred["pitch"] - gt_pitch))
                pred_used.add(pi)
                matched = True
                break

        if not matched:
            missed.append(gt)

    for pi, pred in enumerate(pred_notes):
        if pi not in pred_used:
            extra.append(pred)

    return correct, octave, missed, extra


def compute_metrics(gt_notes, pred_notes, onset_tolerance=0.1):
    """Compute comprehensive metrics."""
    correct, octave, missed, extra = match_notes(gt_notes, pred_notes, onset_tolerance)

    n_gt = len(gt_notes)
    n_pred = len(pred_notes)
    n_correct = len(correct)
    n_octave = len(octave)

    # Basic metrics
    precision = n_correct / n_pred if n_pred > 0 else 0
    recall = n_correct / n_gt if n_gt > 0 else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0

    # Octave analysis
    octave_up = sum(1 for _, _, diff in octave if diff > 0)
    octave_down = sum(1 for _, _, diff in octave if diff < 0)
    octave_12_up = sum(1 for _, _, diff in octave if diff == 12)
    octave_12_down = sum(1 for _, _, diff in octave if diff == -12)

    return {
        "n_gt": n_gt,
        "n_pred": n_pred,
        "n_correct": n_correct,
        "n_octave": n_octave,
        "n_missed": len(missed),
        "n_extra": len(extra),
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "octave_error_rate": n_octave / n_gt if n_gt > 0 else 0,
        "octave_up": octave_up,
        "octave_down": octave_down,
        "octave_12_up": octave_12_up,
        "octave_12_down": octave_12_down,
        "correct_notes": correct,
        "octave_notes": octave,
        "missed_notes": missed,
        "extra_notes": extra,
    }


def head_to_head_analysis(gt_notes, bp_notes, mt3_notes):
    """
    Head-to-head comparison between Basic Pitch and MT3.

    Categories:
    1. both_correct: Both models got it right
    2. bp_only: Only Basic Pitch correct
    3. mt3_only: Only MT3 correct
    4. both_wrong: Neither got it right
    """
    bp_correct, bp_octave, _, _ = match_notes(gt_notes, bp_notes)
    mt3_correct, mt3_octave, _, _ = match_notes(gt_notes, mt3_notes)

    bp_correct_pitches = {(n["pitch"], round(n["onset"], 2)) for n, _ in bp_correct}
    mt3_correct_pitches = {(n["pitch"], round(n["onset"], 2)) for n, _ in mt3_correct}

    both_correct = []
    bp_only = []
    mt3_only = []
    both_wrong = []

    for gt in gt_notes:
        key = (gt["pitch"], round(gt["onset"], 2))
        bp_hit = key in bp_correct_pitches
        mt3_hit = key in mt3_correct_pitches

        if bp_hit and mt3_hit:
            both_correct.append(gt)
        elif bp_hit:
            bp_only.append(gt)
        elif mt3_hit:
            mt3_only.append(gt)
        else:
            both_wrong.append(gt)

    return {
        "both_correct": len(both_correct),
        "bp_only": len(bp_only),
        "mt3_only": len(mt3_only),
        "both_wrong": len(both_wrong),
        "oracle_correct": len(both_correct) + len(bp_only) + len(mt3_only),
        "total": len(gt_notes),
        "details": {
            "both_correct": both_correct,
            "bp_only": bp_only,
            "mt3_only": mt3_only,
            "both_wrong": both_wrong,
        }
    }


def run_comparison(stems, bp_model, mt3_model_name=None):
    """Run full comparison on stems."""
    results = {
        "basic_pitch": {
            "global": defaultdict(float),
            "per_class": defaultdict(lambda: defaultdict(float)),
        },
        "mt3": {
            "global": defaultdict(float),
            "per_class": defaultdict(lambda: defaultdict(float)),
        },
        "head_to_head": {
            "global": defaultdict(int),
            "per_class": defaultdict(lambda: defaultdict(int)),
        },
    }

    for i, stem in enumerate(stems):
        if (i + 1) % 10 == 0:
            print(f"  [{i+1}/{len(stems)}]...", flush=True)

        inst_class = stem["inst_class"]
        gt_notes = load_gt_notes(stem["midi_path"])

        if not gt_notes:
            continue

        # Basic Pitch inference
        bp_notes = run_basic_pitch_inference(stem["audio_path"], bp_model)
        if bp_notes is None:
            continue

        bp_metrics = compute_metrics(gt_notes, bp_notes)

        # Aggregate Basic Pitch results
        for key in ["n_gt", "n_pred", "n_correct", "n_octave", "octave_up", "octave_down"]:
            results["basic_pitch"]["global"][key] += bp_metrics[key]
            results["basic_pitch"]["per_class"][inst_class][key] += bp_metrics[key]

        # MT3 inference (if available)
        if mt3_model_name:
            mt3_notes = run_mt3_inference(stem["audio_path"], mt3_model_name)
            if mt3_notes:
                mt3_metrics = compute_metrics(gt_notes, mt3_notes)

                for key in ["n_gt", "n_pred", "n_correct", "n_octave", "octave_up", "octave_down"]:
                    results["mt3"]["global"][key] += mt3_metrics[key]
                    results["mt3"]["per_class"][inst_class][key] += mt3_metrics[key]

                # Head-to-head
                h2h = head_to_head_analysis(gt_notes, bp_notes, mt3_notes)
                for key in ["both_correct", "bp_only", "mt3_only", "both_wrong"]:
                    results["head_to_head"]["global"][key] += h2h[key]
                    results["head_to_head"]["per_class"][inst_class][key] += h2h[key]

    # Compute derived metrics
    for model in ["basic_pitch", "mt3"]:
        g = results[model]["global"]
        if g["n_gt"] > 0:
            g["recall"] = g["n_correct"] / g["n_gt"]
            g["octave_error_rate"] = g["n_octave"] / g["n_gt"]
        if g["n_pred"] > 0:
            g["precision"] = g["n_correct"] / g["n_pred"]
        if g.get("precision", 0) + g.get("recall", 0) > 0:
            g["f1"] = 2 * g["precision"] * g["recall"] / (g["precision"] + g["recall"])

        for cls, stats in results[model]["per_class"].items():
            if stats["n_gt"] > 0:
                stats["recall"] = stats["n_correct"] / stats["n_gt"]
                stats["octave_error_rate"] = stats["n_octave"] / stats["n_gt"]
            if stats["n_pred"] > 0:
                stats["precision"] = stats["n_correct"] / stats["n_pred"]
            if stats.get("precision", 0) + stats.get("recall", 0) > 0:
                stats["f1"] = 2 * stats["precision"] * stats["recall"] / (stats["precision"] + stats["recall"])

    # Oracle metrics
    h2h = results["head_to_head"]["global"]
    total = h2h.get("both_correct", 0) + h2h.get("bp_only", 0) + h2h.get("mt3_only", 0) + h2h.get("both_wrong", 0)
    if total > 0:
        h2h["oracle_recall"] = (h2h["both_correct"] + h2h["bp_only"] + h2h["mt3_only"]) / total
        h2h["bp_unique_contribution"] = h2h["bp_only"] / total
        h2h["mt3_unique_contribution"] = h2h["mt3_only"] / total

    return results


def main():
    print("="*70, flush=True)
    print("MT3 vs BASIC PITCH BENCHMARK", flush=True)
    print("="*70, flush=True)
    print(f"Start time: {datetime.now()}", flush=True)

    # Load validation stems
    print("\nLoading validation stems...", flush=True)
    stems = find_stems(SLAKH_ROOT, "validation", max_stems=N_EVAL_STEMS)
    print(f"Found {len(stems)} stems", flush=True)

    # Count by class
    class_counts = defaultdict(int)
    for stem in stems:
        class_counts[stem["inst_class"]] += 1
    print("Distribution:", dict(class_counts), flush=True)

    # Basic Pitch uses official predict() - no manual model loading needed
    print("\nBasic Pitch: Using official predict() function", flush=True)
    bp_model = None  # Not needed - official predict() handles model loading

    # Load MT3 model
    mt3_model_name = None
    if MT3_AVAILABLE:
        print("\nInitializing MT3 (mt3_pytorch)...", flush=True)
        print("Note: First run downloads checkpoint (~176MB)", flush=True)
        mt3_model_name = "mt3_pytorch"  # Best accuracy model
        # Warm-up call to trigger checkpoint download
        try:
            test_audio = np.zeros(16000, dtype=np.float32)  # 1 sec silence
            _ = mt3_infer.transcribe(test_audio, model=mt3_model_name, sr=16000)
            print("MT3 ready.", flush=True)
        except Exception as e:
            print(f"MT3 init error: {e}", flush=True)
            mt3_model_name = None
    else:
        print("\nMT3 not available (mt3-infer not installed).", flush=True)

    # Run comparison
    print("\nRunning comparison...", flush=True)
    results = run_comparison(stems, bp_model, mt3_model_name)

    # Print results
    print("\n" + "="*70, flush=True)
    print("RESULTS", flush=True)
    print("="*70, flush=True)

    print("\n--- BASIC PITCH ---", flush=True)
    g = results["basic_pitch"]["global"]
    print(f"Global: F1={g.get('f1', 0):.3f}, Recall={g.get('recall', 0):.3f}, "
          f"Precision={g.get('precision', 0):.3f}, OctErr={g.get('octave_error_rate', 0):.3f}", flush=True)

    print("\nPer-class:", flush=True)
    for cls in ["Bass", "Piano", "Guitar", "Synth Lead", "Organ", "Chromatic Percussion", "Reed", "Brass"]:
        if cls in results["basic_pitch"]["per_class"]:
            c = results["basic_pitch"]["per_class"][cls]
            print(f"  {cls}: Recall={c.get('recall', 0):.3f}, OctErr={c.get('octave_error_rate', 0):.3f}", flush=True)

    if mt3_model_name:
        print("\n--- MT3 ---", flush=True)
        g = results["mt3"]["global"]
        print(f"Global: F1={g.get('f1', 0):.3f}, Recall={g.get('recall', 0):.3f}, "
              f"Precision={g.get('precision', 0):.3f}, OctErr={g.get('octave_error_rate', 0):.3f}", flush=True)

        print("\nPer-class:", flush=True)
        for cls in ["Bass", "Piano", "Guitar", "Synth Lead", "Organ", "Chromatic Percussion", "Reed", "Brass"]:
            if cls in results["mt3"]["per_class"]:
                c = results["mt3"]["per_class"][cls]
                print(f"  {cls}: Recall={c.get('recall', 0):.3f}, OctErr={c.get('octave_error_rate', 0):.3f}", flush=True)

        print("\n--- HEAD-TO-HEAD ---", flush=True)
        h2h = results["head_to_head"]["global"]
        print(f"Both correct: {h2h.get('both_correct', 0)}", flush=True)
        print(f"BP only: {h2h.get('bp_only', 0)}", flush=True)
        print(f"MT3 only: {h2h.get('mt3_only', 0)}", flush=True)
        print(f"Both wrong: {h2h.get('both_wrong', 0)}", flush=True)
        print(f"Oracle recall: {h2h.get('oracle_recall', 0):.3f}", flush=True)

    # Save results
    with open(OUTPUT_DIR / "comparison_results.json", "w") as f:
        def convert(obj):
            if isinstance(obj, (np.floating, float)):
                return float(obj)
            elif isinstance(obj, (np.integer, int)):
                return int(obj)
            elif isinstance(obj, dict):
                return {k: convert(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [convert(x) for x in obj]
            elif isinstance(obj, defaultdict):
                return {k: convert(v) for k, v in obj.items()}
            return obj
        json.dump(convert(results), f, indent=2)

    print(f"\nResults saved to {OUTPUT_DIR}", flush=True)
    print(f"End time: {datetime.now()}", flush=True)


if __name__ == "__main__":
    main()
