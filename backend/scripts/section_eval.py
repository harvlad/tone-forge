"""Section-segmentation eval vs SALAMI ground truth (task 11).

Ground truth: SALAMI public annotations (DDMAL/salami-data-public,
research-use annotations) paired with the Internet Archive subset,
whose audio is freely downloadable (Live Music Archive). Function
annotations (``parsed/textfile1_functions.txt``) provide timestamped
labels including Verse/Chorus/Intro/Outro — the same vocabulary
``bench.section_metrics.canonical_section_label`` maps.

Scope: scores the *detector-only* path (``SectionDetector`` Stage 0
boundaries + energy-heuristic labels), fed the same beat grid
production uses (``tone_forge.beat_tracking.track_beats``). The full
production pipeline additionally relabels with H2 chord recurrence
(Stage A/B) and resegments flagged sections (Fix C); those need
chords/stems and are scored separately once the boundary stage is
trustworthy — boundaries dominate every downstream metric.

Metrics (bench.section_metrics):

* boundary F-measure @0.5s (strict) and @3.0s (coarse) — MIREX HR
* time-weighted canonical section-label accuracy

Usage:
    python3 -m scripts.section_eval --download 24     # fetch audio once
    python3 -m scripts.section_eval --tracks 20 --json out.json
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

SALAMI_DIR = _BACKEND_DIR / "bench" / "data" / "salami-data-public"
AUDIO_DIR = _BACKEND_DIR / "bench" / "data" / "salami_ia" / "audio"
IA_INDEX = SALAMI_DIR / "metadata" / "id_index_internetarchive.csv"

# Terminal/limbo markers in SALAMI functions files: "End" marks the
# final timestamp; keep silence/no_function as regions (their
# boundaries are real structural events — music start/stop) but they
# never label-match detector output.
_TERMINAL = {"end"}


# ---------------------------------------------------------------------------
# Ground truth
# ---------------------------------------------------------------------------


def load_functions(song_id: int) -> Optional[List[Tuple[float, float, str]]]:
    """Parse a SALAMI functions annotation into (start, end, label) regions."""
    for fname in ("textfile1_functions.txt", "textfile2_functions.txt"):
        path = SALAMI_DIR / "annotations" / str(song_id) / "parsed" / fname
        if path.exists():
            break
    else:
        return None
    rows: List[Tuple[float, str]] = []
    for line in path.read_text().splitlines():
        parts = line.strip().split("\t")
        if len(parts) != 2:
            continue
        try:
            t = float(parts[0])
        except ValueError:
            continue
        rows.append((t, parts[1].strip()))
    regions: List[Tuple[float, float, str]] = []
    for (t0, lab), (t1, _) in zip(rows, rows[1:]):
        if t1 - t0 <= 0 or lab.lower() in _TERMINAL:
            continue
        regions.append((t0, t1, lab))
    return regions or None


def _has_song_structure(regions: List[Tuple[float, float, str]]) -> bool:
    """Track is useful when annotated with song-form functions."""
    labels = {lab.lower() for _, _, lab in regions}
    return bool(labels & {"verse", "chorus"}) and len(regions) >= 5


def iter_candidates() -> List[Dict[str, Any]]:
    """IA tracks with structured function annotations, stable order."""
    out = []
    with open(IA_INDEX, newline="", encoding="utf-8", errors="replace") as f:
        for row in csv.DictReader(f):
            try:
                song_id = int(row["SONG_ID"])
                duration = float(row["SONG_DURATION"])
            except (KeyError, ValueError):
                continue
            if not 120 <= duration <= 600:
                continue
            regions = load_functions(song_id)
            if regions is None or not _has_song_structure(regions):
                continue
            out.append({
                "song_id": song_id,
                "title": row.get("TITLE", ""),
                "artist": row.get("ARTIST", ""),
                "url": row.get("URL", ""),
                "duration": duration,
            })
    return out


# ---------------------------------------------------------------------------
# Audio download
# ---------------------------------------------------------------------------


def download(n: int) -> None:
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    candidates = iter_candidates()
    print(f"{len(candidates)} structured IA candidates; fetching first {n}")
    got = 0
    for cand in candidates:
        if got >= n:
            break
        dest = AUDIO_DIR / f"{cand['song_id']}.mp3"
        if dest.exists() and dest.stat().st_size > 100_000:
            got += 1
            continue
        url = cand["url"].replace("http://", "https://")
        print(f"  {cand['song_id']}: {cand['artist']} - {cand['title']}")
        proc = subprocess.run(
            ["curl", "-sfL", "--max-time", "300", "-o", str(dest), url],
        )
        if proc.returncode == 0 and dest.stat().st_size > 100_000:
            got += 1
        else:
            print(f"    download failed ({url})")
            dest.unlink(missing_ok=True)
    print(f"{got} tracks ready in {AUDIO_DIR}")


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def evaluate_track(
    song_id: int,
    max_dur: Optional[float] = None,
    backend: str = "detector",
    cached_segments: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Score one track.

    backend="detector": legacy RMS-novelty SectionDetector path.
    backend="structure": integrated production path — allin1 segments
        fed through ``detect_sections_with_structure`` (label map,
        boundary building, gap handling). Segments come from
        ``cached_segments`` (a prior A/B run's predictions JSON) when
        provided, else ``analyze_structure`` runs fresh.
    """
    import librosa

    from bench.section_metrics import boundary_f_measure, section_label_accuracy
    from tone_forge.analysis.sections import SectionDetector
    from tone_forge.beat_tracking import track_beats
    import numpy as np

    audio_path = AUDIO_DIR / f"{song_id}.mp3"
    if not audio_path.exists():
        return None
    regions = load_functions(song_id)
    if regions is None:
        return None

    y, sr = librosa.load(str(audio_path), sr=22050, mono=True,
                         duration=max_dur)
    duration = len(y) / sr
    if max_dur is not None:
        regions = [(s, min(e, duration), lab)
                   for s, e, lab in regions if s < duration]

    grid = track_beats(y, sr)
    beats = np.asarray(grid["beats_s"]) if grid["beats_s"] else None
    tempo = grid["tempo_bpm"] or None

    detector = SectionDetector()
    if backend == "structure":
        segments = None
        if cached_segments is not None:
            entry = cached_segments.get(str(song_id))
            if isinstance(entry, dict):
                segments = entry.get("segments")
            elif isinstance(entry, list):
                segments = entry
        if segments is None:
            from tone_forge.analysis.structure import analyze_structure
            structure = analyze_structure(audio_path)
            segments = structure["segments"] if structure else None
        if not segments:
            print(f"  {song_id}: no structure segments; skipping")
            return None
        analysis = detector.detect_sections_with_structure(
            y, sr=sr, segments=segments, tempo=tempo, beats_s=beats,
        )
    else:
        analysis = detector.detect_sections(
            y, sr=sr, tempo=tempo, beats_s=beats,
        )
    pred = analysis.sections

    p05, r05, f05 = boundary_f_measure(pred, regions, window_s=0.5)
    p3, r3, f3 = boundary_f_measure(pred, regions, window_s=3.0)
    label_acc = section_label_accuracy(pred, regions, duration)
    return {
        "song_id": song_id,
        "duration_s": round(duration, 1),
        "n_ref": len(regions),
        "n_pred": len(pred),
        "boundary_f_0.5": round(f05, 4),
        "boundary_p_0.5": round(p05, 4),
        "boundary_r_0.5": round(r05, 4),
        "boundary_f_3.0": round(f3, 4),
        "boundary_p_3.0": round(p3, 4),
        "boundary_r_3.0": round(r3, 4),
        "label_acc": round(label_acc, 4),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--download", type=int, metavar="N",
                    help="download N IA tracks then exit")
    ap.add_argument("--tracks", type=int, default=20)
    ap.add_argument("--max-dur", type=float, default=None,
                    help="truncate audio+refs to this many seconds")
    ap.add_argument("--json", type=str, default=None)
    ap.add_argument("--backend", choices=("detector", "structure"),
                    default="detector")
    ap.add_argument("--predictions", type=str, default=None,
                    help="cached allin1 predictions JSON "
                         "(song_id -> {segments,...}) to skip inference")
    args = ap.parse_args()

    cached_segments = None
    if args.predictions:
        cached_segments = json.loads(Path(args.predictions).read_text())

    if args.download:
        download(args.download)
        return 0

    ids = sorted(int(p.stem) for p in AUDIO_DIR.glob("*.mp3"))[: args.tracks]
    if not ids:
        print("no audio downloaded — run with --download N first")
        return 1

    rows: List[Dict[str, Any]] = []
    for song_id in ids:
        row = evaluate_track(
            song_id, max_dur=args.max_dur,
            backend=args.backend, cached_segments=cached_segments,
        )
        if row is None:
            continue
        rows.append(row)
        print(f"  {song_id}: F@0.5 {row['boundary_f_0.5']:.3f}  "
              f"F@3.0 {row['boundary_f_3.0']:.3f}  "
              f"label {row['label_acc']:.3f}  "
              f"(pred {row['n_pred']} vs ref {row['n_ref']})")

    if not rows:
        print("nothing evaluated")
        return 1

    def mean(key: str) -> float:
        return sum(r[key] for r in rows) / len(rows)

    summary = {
        "n_tracks": len(rows),
        "mean_boundary_f_0.5": round(mean("boundary_f_0.5"), 4),
        "mean_boundary_f_3.0": round(mean("boundary_f_3.0"), 4),
        "mean_label_acc": round(mean("label_acc"), 4),
    }
    print(f"\nMEAN over {summary['n_tracks']}: "
          f"F@0.5 {summary['mean_boundary_f_0.5']:.3f}  "
          f"F@3.0 {summary['mean_boundary_f_3.0']:.3f}  "
          f"label {summary['mean_label_acc']:.3f}")

    if args.json:
        Path(args.json).write_text(
            json.dumps({"summary": summary, "tracks": rows}, indent=2))
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
