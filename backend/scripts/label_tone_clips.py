#!/usr/bin/env python3
"""Interactive labeling tool for the tone-retrieval calibration corpus.

Operator-facing companion to ``fit_tone_calibration.py``. Walks a
directory of audio clips, runs the preset-retrieval pipeline against
each clip, displays the top match, and prompts the operator to mark
the match as correct or incorrect. Labels are appended to a JSONL file
that ``fit_tone_calibration.py`` consumes.

Workflow:

  1. Drop guitar / synth / instrument clips into the corpus directory
     (default ``backend/data/tone_calibration_clips/``).
  2. Run this script. For each clip not already labeled, the script
     extracts the same 8-feature fingerprint the production retrieval
     uses, queries the preset catalog, and shows the top-1 match:

         clip: my_clip.wav
         top match: Tape Wood Hybrid Bass (distance=0.7421)
         correct? [y/n/s/q]

     ``y`` records label=1, ``n`` records label=0, ``s`` skips without
     recording (the clip will be re-prompted on the next run), and
     ``q`` quits cleanly.

  3. Once enough samples are labeled (default fitter floor: 50, both
     classes represented), run ``fit_tone_calibration.py`` to drop a
     fitted ``calibration_v1.joblib`` into the loader's directory.

Design choices worth noting:

* The script is *resumable* by design — a labeling session is
  expensive operator attention, and an interruption (Ctrl-C, ssh
  disconnect, accidental window close) must not waste prior work.
  Labels are flushed to disk after each y/n keystroke, and on next
  invocation we skip any ``clip_path`` already present in the JSONL.

* The script never deletes or rewrites the JSONL — it appends only.
  Re-labeling a clip is a deliberate operator action: delete the
  matching line from the JSONL by hand and re-run.

* We do NOT try to validate clip audio (sample rate, channel count,
  duration) here — ``match_audio_file`` and its downstream feature
  extractor own that error surface. A clip that fails to fingerprint
  is reported and skipped; the operator can investigate offline.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Set

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_CLIPS_DIR = _REPO_ROOT / "data" / "tone_calibration_clips"
_DEFAULT_LABELS = _REPO_ROOT / "data" / "tone_calibration_labels.jsonl"

# Audio file extensions we'll try to fingerprint. Everything else in
# the corpus directory (READMEs, .DS_Store, etc.) is silently skipped.
_AUDIO_EXTENSIONS = {".wav", ".mp3", ".flac", ".aif", ".aiff", ".m4a", ".ogg"}


def _iter_clips(clips_dir: Path) -> Iterable[Path]:
    """Yield audio clips under ``clips_dir`` in sorted order.

    Sort is deterministic so that an interrupted session resumes at
    exactly the next unlabeled clip, not at whatever order ``rglob``
    happened to return on this filesystem.
    """
    if not clips_dir.exists():
        raise SystemExit(
            f"clips directory does not exist: {clips_dir}\n"
            f"create it and drop audio clips inside, or pass --clips-dir."
        )
    for path in sorted(clips_dir.rglob("*")):
        if path.is_file() and path.suffix.lower() in _AUDIO_EXTENSIONS:
            yield path


def _already_labeled(labels_path: Path) -> Set[str]:
    """Read existing labels, return the set of ``clip_path`` strings."""
    if not labels_path.exists():
        return set()
    seen: Set[str] = set()
    with labels_path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                # A half-written tail line should not block resumption.
                continue
            cp = record.get("clip_path")
            if isinstance(cp, str):
                seen.add(cp)
    return seen


def _prompt(message: str) -> str:
    """Wrap ``input`` so Ctrl-C exits cleanly with a status message."""
    try:
        return input(message).strip().lower()
    except (KeyboardInterrupt, EOFError):
        print("\n[labeler] interrupted; resume next run", file=sys.stderr)
        raise SystemExit(0)


def _append_label(
    labels_path: Path,
    clip_path: Path,
    preset_id: str,
    distance: float,
    label: int,
) -> None:
    """Atomically append a single record to the JSONL.

    Each line is one record + newline. We open in append mode and
    flush immediately so a crash on the next line still preserves
    everything labeled so far.
    """
    record = {
        "clip_path": str(clip_path),
        "preset_id": preset_id,
        "distance": float(distance),
        "label": int(label),
        "ts": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    labels_path.parent.mkdir(parents=True, exist_ok=True)
    with labels_path.open("a") as fh:
        fh.write(json.dumps(record) + "\n")
        fh.flush()


def _run(clips_dir: Path, labels_path: Path, instrument: str) -> None:
    # Lazy import so ``--help`` works without paying for the catalog
    # load + sklearn import chain.
    sys.path.insert(0, str(_REPO_ROOT))
    from tone_forge.preset_catalog.preset_retrieval import match_audio_file

    seen = _already_labeled(labels_path)
    if seen:
        print(f"[labeler] resuming: {len(seen)} clip(s) already labeled")

    labeled_this_session = 0
    skipped_this_session = 0
    failed_this_session = 0

    for clip_path in _iter_clips(clips_dir):
        if str(clip_path) in seen:
            continue

        try:
            matches = match_audio_file(clip_path, k=1, instrument=instrument)
        except Exception as exc:
            print(
                f"[labeler] fingerprint failed for {clip_path}: {exc}",
                file=sys.stderr,
            )
            failed_this_session += 1
            continue
        if not matches:
            print(
                f"[labeler] no matches returned for {clip_path}; skipping",
                file=sys.stderr,
            )
            failed_this_session += 1
            continue

        top = matches[0]
        preset_id = top.get("preset_id", "<unknown>")
        preset_name = top.get("preset_name", preset_id)
        distance = float(top["distance"])

        print()
        print(f"clip:      {clip_path.name}")
        print(f"top match: {preset_name} ({preset_id})")
        print(f"distance:  {distance:.4f}")
        answer = _prompt("correct? [y/n/s/q]: ")
        if answer in ("q", "quit"):
            break
        if answer in ("s", "skip", ""):
            skipped_this_session += 1
            continue
        if answer in ("y", "yes"):
            label = 1
        elif answer in ("n", "no"):
            label = 0
        else:
            print(f"[labeler] unrecognised input {answer!r}; treating as skip")
            skipped_this_session += 1
            continue
        _append_label(labels_path, clip_path, preset_id, distance, label)
        labeled_this_session += 1

    print()
    print(
        f"[labeler] session: labeled={labeled_this_session} "
        f"skipped={skipped_this_session} failed={failed_this_session}"
    )
    total = len(_already_labeled(labels_path))
    print(f"[labeler] total labeled in {labels_path}: {total}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--clips-dir",
        type=Path,
        default=_DEFAULT_CLIPS_DIR,
        help=f"directory of audio clips to label (default: {_DEFAULT_CLIPS_DIR})",
    )
    parser.add_argument(
        "--labels",
        type=Path,
        default=_DEFAULT_LABELS,
        help=f"append-only JSONL of labels (default: {_DEFAULT_LABELS})",
    )
    parser.add_argument(
        "--instrument",
        default="Analog",
        help="catalog instrument to query (default: Analog)",
    )
    args = parser.parse_args()
    _run(args.clips_dir, args.labels, args.instrument)


if __name__ == "__main__":
    main()
