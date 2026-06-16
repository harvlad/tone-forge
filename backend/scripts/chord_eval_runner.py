"""CLI runner for the chord evaluation harness.

Loads an audio file, runs the production chord detector against it,
loads a ground-truth JSON fixture, and prints strict / triad-relaxed
/ root-only WCSR scores plus the top-N confusion matrix entries.

Usage:
    python -m scripts.chord_eval_runner \\
        --audio /path/to/song_other.wav \\
        --truth backend/tests/fixtures/chord_groundtruth/pub_feed.json

Audio should be the post-stem-separation "other" stem (harmonic
content only) to match what the production pipeline now feeds the
detector. If you point at a full mix, results will be polluted by
bass and drum chroma — that's a real measurement of the unpatched
path, but tag it in any reports as full-mix.

The fixture JSON schema:
    {
        "song": "...", "artist": "...",
        "duration_s": float,
        "regions": [
            {"start": float, "end": float, "label": "A5"},
            ...
        ]
    }
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Ensure we can import from the backend regardless of CWD.
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

import numpy as np  # noqa: E402

from tone_forge.analysis import detect_chords  # noqa: E402
from tone_forge.analysis.chord_eval import (  # noqa: E402
    wcsr,
    triad_relaxed_wcsr,
    root_only_wcsr,
    confusion_matrix,
    format_confusion_top_n,
)


def _load_audio(path: Path) -> tuple[np.ndarray, int]:
    """Load audio at 22050 Hz mono — same rate the detector uses."""
    import librosa
    y, sr = librosa.load(str(path), sr=22050, mono=True)
    return y, sr


def _load_truth(path: Path) -> tuple[list, float]:
    """Load ground-truth JSON fixture."""
    with open(path, 'r') as f:
        data = json.load(f)
    regions = data["regions"]
    duration = float(data["duration_s"])
    return regions, duration


def _print_predicted_regions(predicted, limit: int = 60) -> None:
    """Render the detector output as a region timeline for eyeball.

    Truncated to first `limit` entries to keep terminal output sane on
    songs where the heuristic stack has under-merged.
    """
    print(f"predicted regions ({len(predicted)}):")
    for i, c in enumerate(predicted[:limit]):
        print(f"  [{i:3d}] {c.start_s:7.2f} - {c.end_s:7.2f}  {c.symbol}")
    if len(predicted) > limit:
        print(f"  ... ({len(predicted) - limit} more)")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--audio", required=True, type=Path,
                   help="path to audio file (preferably stems[other])")
    p.add_argument("--truth", required=True, type=Path,
                   help="path to ground-truth JSON fixture")
    p.add_argument("--bass", type=Path, default=None,
                   help="optional path to bass-stem audio (Phase 5 "
                        "bass-routed emission bias)")
    p.add_argument("--beats", action="store_true",
                   help="compute beats via librosa.beat.beat_track on "
                        "the loaded audio and pass to the detector "
                        "(Phase 6 beat-synchronous aggregation)")
    p.add_argument("--top-n", type=int, default=12,
                   help="how many confusion entries to print")
    p.add_argument("--show-predicted", action="store_true",
                   help="dump predicted region list to stdout")
    args = p.parse_args()

    if not args.audio.exists():
        print(f"audio file not found: {args.audio}", file=sys.stderr)
        return 1
    if not args.truth.exists():
        print(f"truth fixture not found: {args.truth}", file=sys.stderr)
        return 1
    if args.bass is not None and not args.bass.exists():
        print(f"bass stem not found: {args.bass}", file=sys.stderr)
        return 1

    print(f"loading audio: {args.audio}")
    y, sr = _load_audio(args.audio)
    print(f"  duration: {len(y) / sr:.2f}s @ {sr}Hz")

    bass_y = None
    if args.bass is not None:
        print(f"loading bass: {args.bass}")
        bass_y, bass_sr = _load_audio(args.bass)
        if bass_sr != sr:
            print(
                f"  WARNING: bass sr ({bass_sr}) != audio sr ({sr}); "
                f"pyin frames will not align with chroma frames",
                file=sys.stderr,
            )
        print(f"  duration: {len(bass_y) / bass_sr:.2f}s @ {bass_sr}Hz")

    print(f"loading truth: {args.truth}")
    truth_regions, truth_duration = _load_truth(args.truth)
    print(f"  reference: {len(truth_regions)} regions, {truth_duration:.2f}s")

    beats_s = None
    if args.beats:
        import librosa
        tempo, beat_frames = librosa.beat.beat_track(
            y=y, sr=sr, hop_length=512,
        )
        beats_s = librosa.frames_to_time(
            beat_frames, sr=sr, hop_length=512,
        )
        try:
            tempo_val = float(tempo)
        except (TypeError, ValueError):
            tempo_val = float(np.asarray(tempo).reshape(-1)[0])
        print(
            f"beats: {len(beats_s)} beats, tempo {tempo_val:.2f} BPM"
        )

    print("running detector ...")
    predicted = detect_chords(y, sr, bass_audio=bass_y, beats_s=beats_s)
    print(f"  predicted: {len(predicted)} regions")

    if args.show_predicted:
        _print_predicted_regions(predicted)

    strict = wcsr(predicted, truth_regions, truth_duration)
    triad = triad_relaxed_wcsr(predicted, truth_regions, truth_duration)
    root = root_only_wcsr(predicted, truth_regions, truth_duration)

    print()
    print("=" * 60)
    print("WCSR scores (higher = better, range 0.0 - 1.0):")
    print(f"  strict           : {strict:.4f}")
    print(f"  triad-relaxed    : {triad:.4f}")
    print(f"  root-only        : {root:.4f}")
    print("=" * 60)

    cm = confusion_matrix(predicted, truth_regions)
    print()
    print(f"confusion matrix (top {args.top_n}):")
    print(format_confusion_top_n(cm, n=args.top_n))

    return 0


if __name__ == "__main__":
    sys.exit(main())
