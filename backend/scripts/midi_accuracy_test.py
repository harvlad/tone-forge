#!/usr/bin/env python3
"""
Multi-genre MIDI extraction accuracy test.

Tests MIDI extraction across different music genres using YouTube samples.
Evaluates extraction quality using various metrics.
"""

import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional
import tempfile

# Test tracks across different genres (using 30-second clips)
TEST_TRACKS = [
    # Rock/Pop
    {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "genre": "pop", "name": "Never Gonna Give You Up", "start": 30, "end": 60},
    # Electronic/Synth
    {"url": "https://www.youtube.com/watch?v=Eo-KmOd3i7s", "genre": "electronic", "name": "Daft Punk - Get Lucky", "start": 60, "end": 90},
    # Metal (bass-heavy)
    {"url": "https://www.youtube.com/watch?v=aJOTlE1K90k", "genre": "metal", "name": "Metallica - Enter Sandman", "start": 30, "end": 60},
    # Jazz
    {"url": "https://www.youtube.com/watch?v=vmDDOFXSgAs", "genre": "jazz", "name": "Take Five", "start": 30, "end": 60},
    # Acoustic/Folk
    {"url": "https://www.youtube.com/watch?v=6NXnxTNIWkc", "genre": "acoustic", "name": "Blackbird", "start": 0, "end": 30},
]


@dataclass
class MIDIMetrics:
    """Metrics for evaluating MIDI extraction quality."""
    stem: str
    note_count: int
    pitch_range: tuple
    method: str
    has_content: bool
    # Derived metrics
    note_density: float  # notes per second
    pitch_span: int  # max - min pitch


@dataclass
class TrackResult:
    """Result for a single track test."""
    name: str
    genre: str
    duration: float
    success: bool
    error: Optional[str]
    stems: Dict[str, MIDIMetrics]
    processing_time: float


def analyze_track(track: dict, timeout: int = 180) -> TrackResult:
    """Analyze a single track and return metrics."""
    import requests

    print(f"\n{'='*60}")
    print(f"Testing: {track['name']} ({track['genre']})")
    print(f"URL: {track['url']}")
    print(f"Clip: {track['start']}s - {track['end']}s")
    print(f"{'='*60}")

    start_time = time.time()

    try:
        # Call the analyze-url endpoint
        response = requests.post(
            "http://127.0.0.1:7777/api/analyze-url",
            json={
                "url": track["url"],
                "start_time": track.get("start", 0),
                "end_time": track.get("end", 30),
            },
            stream=True,
            timeout=timeout,
        )

        result_data = None
        for line in response.iter_lines():
            if line:
                line_str = line.decode('utf-8')
                if line_str.startswith('data: {"type": "result"'):
                    result_data = json.loads(line_str[6:])['data']
                    break
                elif line_str.startswith('data: {"type": "progress"'):
                    progress = json.loads(line_str[6:])
                    print(f"  [{progress.get('percent', 0):3d}%] {progress.get('message', '')}")

        if not result_data:
            return TrackResult(
                name=track["name"],
                genre=track["genre"],
                duration=0,
                success=False,
                error="No result data received",
                stems={},
                processing_time=time.time() - start_time,
            )

        # Extract MIDI metrics for each stem
        stems = {}
        midi_stems = result_data.get("midi_stems", {})
        duration = result_data.get("duration_sec", 30)

        for stem_name, midi_data in midi_stems.items():
            note_count = midi_data.get("note_count", 0)
            pitch_range = midi_data.get("pitch_range", [0, 0])
            if isinstance(pitch_range, list):
                pitch_range = tuple(pitch_range)

            stems[stem_name] = MIDIMetrics(
                stem=stem_name,
                note_count=note_count,
                pitch_range=pitch_range,
                method=midi_data.get("method", "unknown"),
                has_content=bool(midi_data.get("content")),
                note_density=note_count / max(duration, 1),
                pitch_span=pitch_range[1] - pitch_range[0] if len(pitch_range) == 2 else 0,
            )

        return TrackResult(
            name=track["name"],
            genre=track["genre"],
            duration=duration,
            success=True,
            error=None,
            stems=stems,
            processing_time=time.time() - start_time,
        )

    except Exception as e:
        return TrackResult(
            name=track["name"],
            genre=track["genre"],
            duration=0,
            success=False,
            error=str(e),
            stems={},
            processing_time=time.time() - start_time,
        )


def evaluate_quality(results: List[TrackResult]) -> Dict:
    """Evaluate overall extraction quality across all tracks."""

    # Aggregate metrics
    total_tracks = len(results)
    successful = sum(1 for r in results if r.success)

    stem_metrics = {
        "drums": {"note_counts": [], "densities": [], "methods": []},
        "bass": {"note_counts": [], "densities": [], "pitch_spans": [], "methods": []},
        "other": {"note_counts": [], "densities": [], "pitch_spans": [], "methods": []},
    }

    for result in results:
        if not result.success:
            continue
        for stem_name, metrics in result.stems.items():
            if stem_name in stem_metrics:
                stem_metrics[stem_name]["note_counts"].append(metrics.note_count)
                stem_metrics[stem_name]["densities"].append(metrics.note_density)
                stem_metrics[stem_name]["methods"].append(metrics.method)
                if stem_name != "drums":
                    stem_metrics[stem_name]["pitch_spans"].append(metrics.pitch_span)

    # Calculate quality scores
    quality = {
        "total_tracks": total_tracks,
        "successful": successful,
        "success_rate": successful / total_tracks if total_tracks > 0 else 0,
        "stems": {},
    }

    for stem_name, data in stem_metrics.items():
        if not data["note_counts"]:
            continue

        avg_notes = sum(data["note_counts"]) / len(data["note_counts"])
        avg_density = sum(data["densities"]) / len(data["densities"])
        gpu_usage = sum(1 for m in data["methods"] if "gpu" in m.lower() or "coreml" in m.lower()) / len(data["methods"])

        quality["stems"][stem_name] = {
            "avg_note_count": round(avg_notes, 1),
            "avg_density": round(avg_density, 2),
            "gpu_usage_pct": round(gpu_usage * 100, 1),
            "tracks_with_notes": sum(1 for n in data["note_counts"] if n > 0),
        }

        if stem_name != "drums" and data["pitch_spans"]:
            quality["stems"][stem_name]["avg_pitch_span"] = round(sum(data["pitch_spans"]) / len(data["pitch_spans"]), 1)

    return quality


def print_results(results: List[TrackResult], quality: Dict):
    """Print formatted results."""

    print("\n" + "="*80)
    print("MIDI EXTRACTION TEST RESULTS")
    print("="*80)

    # Per-track results
    for result in results:
        status = "✓" if result.success else "✗"
        print(f"\n{status} {result.name} ({result.genre}) - {result.processing_time:.1f}s")

        if not result.success:
            print(f"  Error: {result.error}")
            continue

        for stem_name, metrics in result.stems.items():
            print(f"  {stem_name:8s}: {metrics.note_count:4d} notes, "
                  f"density={metrics.note_density:.2f}/s, "
                  f"method={metrics.method}")

    # Overall quality
    print("\n" + "="*80)
    print("OVERALL QUALITY")
    print("="*80)
    print(f"Success rate: {quality['successful']}/{quality['total_tracks']} ({quality['success_rate']*100:.0f}%)")

    print("\nPer-stem metrics:")
    for stem_name, metrics in quality.get("stems", {}).items():
        print(f"  {stem_name}:")
        print(f"    Avg notes: {metrics['avg_note_count']}")
        print(f"    Avg density: {metrics['avg_density']} notes/sec")
        print(f"    GPU usage: {metrics['gpu_usage_pct']}%")
        print(f"    Tracks with notes: {metrics['tracks_with_notes']}")
        if "avg_pitch_span" in metrics:
            print(f"    Avg pitch span: {metrics['avg_pitch_span']} semitones")


def main():
    """Run the multi-genre MIDI accuracy test."""

    # Check if server is running
    import requests
    try:
        r = requests.get("http://127.0.0.1:7777/health", timeout=5)
        if r.status_code != 200:
            print("Error: Local engine not responding")
            sys.exit(1)
    except Exception as e:
        print(f"Error: Cannot connect to local engine: {e}")
        sys.exit(1)

    print("ToneForge MIDI Extraction Accuracy Test")
    print(f"Testing {len(TEST_TRACKS)} tracks across multiple genres")
    print(f"Using 30-second clips for faster testing")

    # Run tests
    results = []
    for track in TEST_TRACKS:
        result = analyze_track(track)
        results.append(result)

    # Evaluate quality
    quality = evaluate_quality(results)

    # Print results
    print_results(results, quality)

    # Save results to file
    output = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "tracks": [
            {
                "name": r.name,
                "genre": r.genre,
                "success": r.success,
                "error": r.error,
                "processing_time": r.processing_time,
                "stems": {
                    name: {
                        "note_count": m.note_count,
                        "pitch_range": m.pitch_range,
                        "method": m.method,
                        "note_density": m.note_density,
                    }
                    for name, m in r.stems.items()
                } if r.success else {}
            }
            for r in results
        ],
        "quality": quality,
    }

    with open("/tmp/midi_accuracy_results.json", "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nResults saved to /tmp/midi_accuracy_results.json")


if __name__ == "__main__":
    main()
