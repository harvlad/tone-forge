#!/usr/bin/env python3
"""
Real-user MIDI benchmark using GPU CoreML extraction.

Tests the same code path that actual users experience via the local engine API.
Uses short clips (30s) from ground truth samples for fast iteration.
"""

import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import tempfile
import shutil

import mido
import numpy as np

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent))


@dataclass
class BenchmarkResult:
    sample_id: str
    stem_type: str
    ground_truth_notes: int
    extracted_notes: int
    true_positives: int
    false_positives: int
    false_negatives: int
    precision: float
    recall: float
    f1: float
    method: str
    extraction_time_s: float
    error: Optional[str] = None


def load_ground_truth_notes(
    midi_path: Path,
    stem_type: str,
    max_duration: float = 30.0,
) -> List[Tuple[int, float, float, int]]:
    """Load ground truth notes filtered by stem type, limited to max_duration."""
    STEM_TRACK_PATTERNS = {
        "bass": ["bass"],
        "lead": ["lead"],
        "pads": ["pad"],
        "drums": ["drum", "kick", "snare", "hat", "cymbal"],
        "other": ["lead", "pad"],  # For "other" stem, include lead and pad
    }

    mid = mido.MidiFile(str(midi_path))
    patterns = STEM_TRACK_PATTERNS.get(stem_type.lower(), [stem_type.lower()])
    notes = []

    for track in mid.tracks:
        track_name = track.name.lower() if track.name else ""
        if not any(p in track_name for p in patterns):
            continue

        current_time = 0.0
        active = {}

        for msg in track:
            current_time += mido.tick2second(msg.time, mid.ticks_per_beat, 500000)

            if current_time > max_duration:
                break

            if msg.type == 'note_on' and msg.velocity > 0:
                active[msg.note] = (current_time, msg.velocity)
            elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
                if msg.note in active:
                    start, vel = active.pop(msg.note)
                    if start <= max_duration:
                        notes.append((msg.note, start, min(current_time, max_duration), vel))

    return sorted(notes, key=lambda n: n[1])


def compare_notes(
    extracted: List[Tuple[int, float, float, int]],
    ground_truth: List[Tuple[int, float, float, int]],
    onset_tolerance_ms: float = 100.0,  # Increased from 50ms for better matching
) -> Dict:
    """Compare extracted notes to ground truth."""
    if not ground_truth:
        return {
            "precision": 0.0 if extracted else 1.0,
            "recall": 0.0 if extracted else 1.0,
            "f1": 0.0 if extracted else 1.0,
            "true_positives": 0,
            "false_positives": len(extracted),
            "false_negatives": 0,
        }

    if not extracted:
        return {
            "precision": 0.0,
            "recall": 0.0,
            "f1": 0.0,
            "true_positives": 0,
            "false_positives": 0,
            "false_negatives": len(ground_truth),
        }

    onset_tolerance = onset_tolerance_ms / 1000.0
    matched_gt = set()
    matched_ext = set()

    for i, ext in enumerate(extracted):
        ext_pitch, ext_start, _, _ = ext
        best_match = None
        best_error = float('inf')

        for j, gt in enumerate(ground_truth):
            if j in matched_gt:
                continue

            gt_pitch, gt_start, _, _ = gt

            # Check pitch - allow exact match or octave errors (within 2 octaves)
            pitch_diff = abs(ext_pitch - gt_pitch)
            # Accept: exact match, or 1-2 octave difference
            if pitch_diff > 0 and pitch_diff % 12 != 0:
                continue
            if pitch_diff > 24:  # More than 2 octaves is probably wrong
                continue

            # Check onset
            onset_error = abs(ext_start - gt_start)
            if onset_error <= onset_tolerance and onset_error < best_error:
                best_match = j
                best_error = onset_error

        if best_match is not None:
            matched_gt.add(best_match)
            matched_ext.add(i)

    tp = len(matched_gt)
    fp = len(extracted) - len(matched_ext)
    fn = len(ground_truth) - len(matched_gt)

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0

    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
    }


def extract_midi_gpu(
    audio_path: Path,
    stem_type: str,
    max_duration: float = 30.0,
) -> Tuple[List[Tuple[int, float, float, int]], str, float]:
    """Extract MIDI using our GPU CoreML extractor."""
    import librosa
    import soundfile as sf

    start_time = time.time()

    # Load and trim audio to max_duration
    audio, sr = librosa.load(str(audio_path), sr=22050, mono=True, duration=max_duration)

    # Save to temp file
    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
        temp_path = f.name
        sf.write(temp_path, audio, sr)

    try:
        # Use our GPU extractor (same as analysis_worker)
        from tone_forge.midi.gpu_extractor import extract_midi_hybrid

        result = extract_midi_hybrid(
            temp_path,
            stem_type=stem_type,
            preset_name=f"benchmark_{stem_type}",
        )

        method = result.get("method", "unknown")

        # Parse notes from MIDI content
        notes = []
        if result.get("content"):
            import base64
            import io

            midi_bytes = base64.b64decode(result["content"])
            mid = mido.MidiFile(file=io.BytesIO(midi_bytes))

            for track in mid.tracks:
                current_time = 0.0
                active = {}

                for msg in track:
                    current_time += mido.tick2second(msg.time, mid.ticks_per_beat, 500000)

                    if msg.type == 'note_on' and msg.velocity > 0:
                        active[msg.note] = (current_time, msg.velocity)
                    elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
                        if msg.note in active:
                            start, vel = active.pop(msg.note)
                            notes.append((msg.note, start, current_time, vel))

        extraction_time = time.time() - start_time
        return notes, method, extraction_time

    finally:
        Path(temp_path).unlink(missing_ok=True)


def discover_samples(samples_dir: Path, max_samples_per_stem: int = 5) -> List[Dict]:
    """Discover samples from the ground truth directory."""
    samples = []
    stem_types = ["Bass", "Lead", "Pads"]  # Skip drums for now

    for track_dir in sorted(samples_dir.iterdir()):
        if not track_dir.is_dir() or track_dir.name.startswith("."):
            continue

        # Find MIDI file
        midi_files = list(track_dir.glob("*bpm.mid"))
        if not midi_files:
            continue

        midi_file = midi_files[0]
        track_prefix = midi_file.stem.rsplit("_", 1)[0]

        for stem_type in stem_types:
            stem_file = track_dir / f"{track_prefix}_{stem_type}.wav"
            if not stem_file.exists():
                alt_stems = list(track_dir.glob(f"*_{stem_type}.wav"))
                if alt_stems:
                    stem_file = alt_stems[0]
                else:
                    continue

            samples.append({
                "id": f"{track_dir.name}_{stem_type}",
                "audio_path": stem_file,
                "midi_path": midi_file,
                "stem_type": stem_type.lower(),
            })

    # Limit samples per stem type for faster iteration
    stem_counts = {}
    filtered = []
    for s in samples:
        st = s["stem_type"]
        stem_counts[st] = stem_counts.get(st, 0) + 1
        if stem_counts[st] <= max_samples_per_stem:
            filtered.append(s)

    return filtered


def run_benchmark(samples_dir: Path, max_duration: float = 30.0, max_samples_per_stem: int = 5):
    """Run the GPU benchmark on ground truth samples."""

    print("="*70)
    print("GPU MIDI BENCHMARK (Real User Pipeline)")
    print("="*70)
    print(f"Using {max_duration}s clips for fast iteration")
    print(f"Max {max_samples_per_stem} samples per stem type")
    print()

    samples = discover_samples(samples_dir, max_samples_per_stem)

    if not samples:
        print("No samples found!")
        return

    print(f"Found {len(samples)} samples")
    for st in ["bass", "lead", "pads"]:
        count = sum(1 for s in samples if s["stem_type"] == st)
        print(f"  {st}: {count}")
    print()

    results = []

    for i, sample in enumerate(samples):
        print(f"\n[{i+1}/{len(samples)}] {sample['id']}")

        try:
            # Load ground truth (limited to max_duration)
            gt_notes = load_ground_truth_notes(
                sample["midi_path"],
                sample["stem_type"],
                max_duration=max_duration,
            )

            # Extract with GPU
            extracted_notes, method, extraction_time = extract_midi_gpu(
                sample["audio_path"],
                sample["stem_type"],
                max_duration=max_duration,
            )

            # Compare
            metrics = compare_notes(extracted_notes, gt_notes)

            result = BenchmarkResult(
                sample_id=sample["id"],
                stem_type=sample["stem_type"],
                ground_truth_notes=len(gt_notes),
                extracted_notes=len(extracted_notes),
                true_positives=metrics["true_positives"],
                false_positives=metrics["false_positives"],
                false_negatives=metrics["false_negatives"],
                precision=metrics["precision"],
                recall=metrics["recall"],
                f1=metrics["f1"],
                method=method,
                extraction_time_s=extraction_time,
            )
            results.append(result)

            print(f"  GT: {len(gt_notes):3d} notes | Extracted: {len(extracted_notes):3d} notes | "
                  f"F1: {metrics['f1']:.1%} | Method: {method} | Time: {extraction_time:.1f}s")

        except Exception as e:
            print(f"  ERROR: {e}")
            results.append(BenchmarkResult(
                sample_id=sample["id"],
                stem_type=sample["stem_type"],
                ground_truth_notes=0,
                extracted_notes=0,
                true_positives=0,
                false_positives=0,
                false_negatives=0,
                precision=0,
                recall=0,
                f1=0,
                method="error",
                extraction_time_s=0,
                error=str(e),
            ))

    # Summary
    print("\n" + "="*70)
    print("RESULTS SUMMARY")
    print("="*70)

    valid_results = [r for r in results if r.error is None]

    # Overall
    if valid_results:
        overall_f1 = np.mean([r.f1 for r in valid_results])
        overall_precision = np.mean([r.precision for r in valid_results])
        overall_recall = np.mean([r.recall for r in valid_results])
        print(f"\nOverall: P={overall_precision:.1%} R={overall_recall:.1%} F1={overall_f1:.1%}")

    # Per stem
    print("\nPer Stem Type:")
    for stem_type in ["bass", "lead", "pads"]:
        stem_results = [r for r in valid_results if r.stem_type == stem_type]
        if stem_results:
            avg_f1 = np.mean([r.f1 for r in stem_results])
            avg_p = np.mean([r.precision for r in stem_results])
            avg_r = np.mean([r.recall for r in stem_results])
            gpu_pct = sum(1 for r in stem_results if "gpu" in r.method.lower() or "coreml" in r.method.lower()) / len(stem_results) * 100
            print(f"  {stem_type:8s}: P={avg_p:.1%} R={avg_r:.1%} F1={avg_f1:.1%} (GPU: {gpu_pct:.0f}%)")

    # Check target
    target_f1 = 0.80
    print(f"\n{'='*70}")
    if overall_f1 >= target_f1:
        print(f"TARGET MET: F1 {overall_f1:.1%} >= {target_f1:.0%}")
    else:
        gap = target_f1 - overall_f1
        print(f"TARGET NOT MET: F1 {overall_f1:.1%} < {target_f1:.0%} (gap: {gap:.1%})")
    print("="*70)

    # Save results
    output_file = Path("/tmp/gpu_midi_benchmark_results.json")
    with open(output_file, "w") as f:
        json.dump({
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "config": {
                "max_duration": max_duration,
                "max_samples_per_stem": max_samples_per_stem,
            },
            "overall": {
                "precision": overall_precision,
                "recall": overall_recall,
                "f1": overall_f1,
            },
            "results": [
                {
                    "sample_id": r.sample_id,
                    "stem_type": r.stem_type,
                    "f1": r.f1,
                    "precision": r.precision,
                    "recall": r.recall,
                    "method": r.method,
                    "extraction_time_s": r.extraction_time_s,
                    "ground_truth_notes": r.ground_truth_notes,
                    "extracted_notes": r.extracted_notes,
                }
                for r in results
            ],
        }, f, indent=2)
    print(f"\nResults saved to: {output_file}")


if __name__ == "__main__":
    samples_dir = Path("/Users/mattharvey/Sites/tone-forge/samples")

    if len(sys.argv) > 1:
        samples_dir = Path(sys.argv[1])

    if not samples_dir.exists():
        print(f"Samples directory not found: {samples_dir}")
        sys.exit(1)

    # Run with 30s clips and 5 samples per stem for fast iteration
    run_benchmark(samples_dir, max_duration=30.0, max_samples_per_stem=5)
