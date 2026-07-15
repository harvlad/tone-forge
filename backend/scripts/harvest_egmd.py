"""E-GMD drum-dataset harvester (Stage A — onset manifest).

Turns the Expanded Groove MIDI Dataset (E-GMD, Magenta, CC BY 4.0 —
~444h of real electronic-drum audio + time-aligned MIDI) into an
*onset manifest* the Swift ``BeatModelTrainer harvest`` subcommand
(Stage B) reads to extract features and emit trainer corrections rows.

Split rationale: features MUST come from the same Swift
``OnsetFeatures.extract`` used at inference (a Python librosa
re-implementation would drift and silently poison the model). So this
stage does only what Python is good at — download the dataset, parse
MIDI, and decide *where* the onsets are and *what role* each is. It
emits ``wav_path,onset_sec,role`` and hands audio+features to Swift.

Ground truth: each MIDI note number is a General MIDI percussion
instrument, which maps to one of the seven ``DrumRole`` values the
classifier predicts (see ``GM_NOTE_TO_ROLE``). Notes outside the map
are skipped by default (``--unmapped perc`` folds them into perc).

Simultaneous hits: notes within ``--epsilon`` seconds are one event.
A group with more than one *distinct* role is dropped (a blended
kick+snare+hat slice cannot be cleanly labelled); a group that is
several notes of the *same* role (e.g. two toms) yields one onset at
the earliest time. This mirrors the runtime onset debounce.

Class balance: E-GMD is heavy on kick/snare/hat and light on
clap/rim, so ``--max-per-role`` caps each role with deterministic
seeded subsampling; the trainer backfills rare roles from the
synthetic corpus.

Usage::

    # Local tree already on disk (tests, dev) — no download:
    python3 -m scripts.harvest_egmd --local-root tests/fixtures/egmd \
        --out /tmp/manifest.csv

    # CI / remote: E-GMD is a ~96 GB monolithic zip, so --limit streams
    # only the first N pairs over HTTP range (see _ensure_dataset):
    python3 -m scripts.harvest_egmd --out $RUNNER_TEMP/egmd_manifest.csv \
        --cache-dir $RUNNER_TEMP/e-gmd --max-per-role 8000 --limit 2000
"""
from __future__ import annotations

import argparse
import csv
import random
import sys
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Dict, List, NamedTuple, Optional, Sequence, Tuple

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

# Default dataset location + download source.
DATASET_URL = (
    "https://storage.googleapis.com/magentadata/datasets/e-gmd/"
    "v1.0.0/e-gmd-v1.0.0.zip"
)
DEFAULT_CACHE_DIR = _BACKEND_ROOT / "bench" / "data" / "e-gmd"

# The seven roles the classifier predicts — must match DrumRole.swift
# raw values (mobile-ios/.../BeatCapture/DrumRole.swift).
DRUM_ROLES = ("kick", "snare", "closed_hat", "open_hat", "clap", "rim", "perc")

# General MIDI percussion note number -> DrumRole raw value. Cymbals
# and toms collapse to `perc` (the model has no dedicated cymbal/tom
# role). Notes absent here are handled by --unmapped.
GM_NOTE_TO_ROLE: Dict[int, str] = {
    35: "kick",        # Acoustic Bass Drum
    36: "kick",        # Bass Drum 1
    37: "rim",         # Side Stick / Rimshot
    38: "snare",       # Acoustic Snare
    39: "clap",        # Hand Clap
    40: "snare",       # Electric Snare
    41: "perc",        # Low Floor Tom
    42: "closed_hat",  # Closed Hi-Hat
    43: "perc",        # High Floor Tom
    44: "closed_hat",  # Pedal Hi-Hat
    45: "perc",        # Low Tom
    46: "open_hat",    # Open Hi-Hat
    47: "perc",        # Low-Mid Tom
    48: "perc",        # Hi-Mid Tom
    49: "perc",        # Crash Cymbal 1
    50: "perc",        # High Tom
    51: "perc",        # Ride Cymbal 1
    52: "perc",        # Chinese Cymbal
    53: "perc",        # Ride Bell
    55: "perc",        # Splash Cymbal
    57: "perc",        # Crash Cymbal 2
    59: "perc",        # Ride Cymbal 2
}


class ManifestRow(NamedTuple):
    """One labelled onset: absolute wav path, onset time, drum role."""

    wav_path: str
    onset_sec: float
    role: str


# ---------------------------------------------------------------------------
# MIDI -> onset rows (pure; unit-tested)
# ---------------------------------------------------------------------------


def build_manifest_rows(
    midi_path: Path,
    wav_path: Path,
    *,
    epsilon: float = 0.010,
    unmapped: str = "skip",
) -> List[ManifestRow]:
    """Parse one MIDI file into labelled onset rows.

    ``epsilon`` groups near-simultaneous notes; a group with >1 distinct
    role is dropped, a same-role group collapses to its earliest onset.
    ``unmapped`` is "skip" (drop notes outside GM_NOTE_TO_ROLE) or
    "perc" (fold them into perc). Returns rows sorted by onset time.
    """
    import pretty_midi

    pm = pretty_midi.PrettyMIDI(str(midi_path))

    # (start_sec, role) for every note we can label.
    events: List[Tuple[float, str]] = []
    for inst in pm.instruments:
        for note in inst.notes:
            role = GM_NOTE_TO_ROLE.get(int(note.pitch))
            if role is None:
                if unmapped == "perc":
                    role = "perc"
                else:
                    continue
            events.append((float(note.start), role))

    events.sort(key=lambda e: e[0])

    rows: List[ManifestRow] = []
    wav_str = str(wav_path)
    i = 0
    n = len(events)
    while i < n:
        group_start = events[i][0]
        roles = {events[i][1]}
        j = i + 1
        # Chain on the *group start* so a dense roll doesn't merge past
        # epsilon, matching how a single physical hit clusters.
        while j < n and events[j][0] - group_start < epsilon:
            roles.add(events[j][1])
            j += 1
        if len(roles) == 1:
            rows.append(ManifestRow(wav_str, group_start, next(iter(roles))))
        # else: mixed-role coincidence — unlabelable, skip the group.
        i = j
    return rows


# ---------------------------------------------------------------------------
# Dataset discovery
# ---------------------------------------------------------------------------


def _iter_pairs(root: Path) -> List[Tuple[Path, Path]]:
    """Return (midi_path, wav_path) pairs under a dataset tree.

    Prefers the official ``e-gmd-v1.0.0.csv`` index (columns
    ``midi_filename`` / ``audio_filename`` relative to the index dir);
    falls back to globbing ``*.mid``/``*.midi`` with a sibling wav.
    """
    index = next(root.rglob("e-gmd-v1.0.0.csv"), None)
    pairs: List[Tuple[Path, Path]] = []
    if index is not None:
        base = index.parent
        with open(index, newline="", encoding="utf-8", errors="replace") as f:
            for row in csv.DictReader(f):
                midi_rel = row.get("midi_filename")
                audio_rel = row.get("audio_filename")
                if not midi_rel or not audio_rel:
                    continue
                mid = base / midi_rel
                wav = base / audio_rel
                if mid.exists() and wav.exists():
                    pairs.append((mid, wav))
        if pairs:
            return pairs

    # Fallback: glob MIDI files and look for a same-stem audio sibling.
    for mid in sorted(root.rglob("*.mid")) + sorted(root.rglob("*.midi")):
        for ext in (".wav", ".flac", ".ogg", ".mp3"):
            wav = mid.with_suffix(ext)
            if wav.exists():
                pairs.append((mid, wav))
                break
    return pairs


def _find_index_name(names: Sequence[str]) -> Optional[str]:
    """Return the E-GMD index CSV member name, if present in the zip."""
    for n in names:
        if n.endswith("e-gmd-v1.0.0.csv"):
            return n
    return None


def _pairs_from_index(
    index_name: str,
    rows: Sequence[Dict[str, str]],
    names: set,
    limit: int,
) -> List[Tuple[str, str]]:
    """Pick (midi_member, audio_member) zip entries from the index CSV.

    ``midi_filename`` / ``audio_filename`` are relative to the index
    dir; joined back into zip member names. Only pairs whose members
    both exist in ``names`` are kept. Capped to ``limit`` (<=0 = all).
    Pure — unit-tested without any network.
    """
    base = PurePosixPath(index_name).parent
    out: List[Tuple[str, str]] = []
    for row in rows:
        midi_rel = (row.get("midi_filename") or "").strip()
        audio_rel = (row.get("audio_filename") or "").strip()
        if not midi_rel or not audio_rel:
            continue
        mid = str(base / midi_rel)
        wav = str(base / audio_rel)
        if mid in names and wav in names:
            out.append((mid, wav))
            if limit > 0 and len(out) >= limit:
                break
    return out


def _pairs_from_stems(names: Sequence[str], limit: int) -> List[Tuple[str, str]]:
    """Fallback member selection: MIDI member + same-stem audio sibling.

    Used when the zip lacks an index CSV. Deterministic (sorted). Pure.
    """
    audio_exts = (".wav", ".flac", ".ogg", ".mp3")
    name_set = set(names)
    out: List[Tuple[str, str]] = []
    for n in sorted(names):
        if not (n.endswith(".mid") or n.endswith(".midi")):
            continue
        stem = n.rsplit(".", 1)[0]
        for ext in audio_exts:
            cand = stem + ext
            if cand in name_set:
                out.append((n, cand))
                break
        if limit > 0 and len(out) >= limit:
            break
    return out


def _ensure_dataset(cache_dir: Path, dataset_url: str, limit: int) -> Path:
    """Stream a *subset* of E-GMD into ``cache_dir``; idempotent.

    E-GMD is a ~96 GB monolithic zip, so a full download is infeasible
    on a laptop or a CI runner. Instead we read the archive's central
    directory over HTTP range requests (``remotezip``) and pull only the
    members we intend to train on — the first ``limit`` MIDI/audio pairs
    (from the index CSV, else stem-matched). ``limit<=0`` means every
    pair, which for the real E-GMD URL is the full 96 GB; always pass
    ``--limit`` for the remote dataset.

    A ``.complete`` marker guards re-fetch. Best-effort: raises on hard
    failure so the caller can degrade to synthetic-only training.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    marker = cache_dir / ".complete"
    if marker.exists():
        return cache_dir

    try:
        from remotezip import RemoteZip
    except ImportError as exc:  # trainer-only dep; see requirements.txt
        raise RuntimeError(
            "remotezip not installed (pip install remotezip)"
        ) from exc

    print(f"[egmd] streaming subset from {dataset_url} (limit={limit})",
          file=sys.stderr)
    with RemoteZip(dataset_url) as z:
        names = z.namelist()
        pairs: List[Tuple[str, str]] = []
        index_name = _find_index_name(names)
        if index_name is not None:
            z.extract(index_name, str(cache_dir))
            with open(cache_dir / index_name, newline="",
                      encoding="utf-8", errors="replace") as f:
                rows = list(csv.DictReader(f))
            pairs = _pairs_from_index(index_name, rows, set(names), limit)
        if not pairs:
            pairs = _pairs_from_stems(names, limit)
        if not pairs:
            raise RuntimeError("no MIDI/audio pairs found in archive")
        print(f"[egmd] fetching {len(pairs)} pair(s) via range requests",
              file=sys.stderr)
        for mid_member, wav_member in pairs:
            z.extract(mid_member, str(cache_dir))
            z.extract(wav_member, str(cache_dir))

    marker.write_text(f"pairs={len(pairs)}\n")
    return cache_dir


# ---------------------------------------------------------------------------
# Harvest driver
# ---------------------------------------------------------------------------


def harvest(
    root: Path,
    *,
    epsilon: float,
    unmapped: str,
    limit: int,
    max_per_role: int,
    seed: int,
) -> List[ManifestRow]:
    """Walk a dataset tree into capped, balanced manifest rows."""
    pairs = _iter_pairs(root)
    if not pairs:
        print(f"[egmd] no MIDI/audio pairs under {root}", file=sys.stderr)
        return []
    if limit > 0:
        pairs = pairs[:limit]
    print(f"[egmd] {len(pairs)} MIDI/audio pair(s)", file=sys.stderr)

    rows: List[ManifestRow] = []
    for mid, wav in pairs:
        try:
            rows.extend(
                build_manifest_rows(
                    mid, wav, epsilon=epsilon, unmapped=unmapped
                )
            )
        except Exception as exc:  # keep going; one bad file mustn't abort
            print(f"[egmd] skip {mid.name}: {exc}", file=sys.stderr)

    _log_histogram("pre-cap", rows)
    if max_per_role > 0:
        rows = _cap_per_role(rows, max_per_role, seed)
        _log_histogram("post-cap", rows)
    return rows


def _cap_per_role(
    rows: List[ManifestRow], cap: int, seed: int
) -> List[ManifestRow]:
    """Deterministically subsample each over-cap role to ``cap`` rows.

    Reproducible + order-independent: bucket row indices by role, sort
    each bucket canonically, seeded-sample the kept indices, then emit
    in the original row order so the CSV is stable across runs.
    """
    by_role: Dict[str, List[int]] = {}
    for i, r in enumerate(rows):
        by_role.setdefault(r.role, []).append(i)

    kept: set = set()
    for role, indices in by_role.items():
        if len(indices) <= cap:
            kept.update(indices)
            continue
        # Sort by row content (not raw index) so the sampled subset is
        # independent of file-walk order for a given seed.
        ordered = sorted(indices, key=lambda i: rows[i])
        rng = random.Random(f"{seed}:{role}")
        kept.update(rng.sample(ordered, cap))

    return [r for i, r in enumerate(rows) if i in kept]


def _log_histogram(tag: str, rows: List[ManifestRow]) -> None:
    counts = Counter(r.role for r in rows)
    parts = "  ".join(f"{role}={counts.get(role, 0)}" for role in DRUM_ROLES)
    print(f"[egmd] {tag}: {len(rows)} rows  {parts}", file=sys.stderr)


def write_manifest(rows: List[ManifestRow], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["wav_path", "onset_sec", "role"])
        for r in rows:
            w.writerow([r.wav_path, f"{r.onset_sec:.6f}", r.role])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m scripts.harvest_egmd",
        description="Harvest E-GMD into an onset manifest for the Swift trainer.",
    )
    parser.add_argument("--out", type=Path, required=True,
                        help="Onset manifest CSV to write.")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR,
                        help="Download/extract dir (default backend/bench/data/e-gmd).")
    parser.add_argument("--dataset-url", default=DATASET_URL,
                        help="Override the E-GMD archive URL (e.g. smaller GMD).")
    parser.add_argument("--local-root", type=Path, default=None,
                        help="Use an existing extracted tree; skip download.")
    parser.add_argument("--limit", type=int, default=0,
                        help="Only the first N MIDI/audio pairs (0=all). For the "
                             "remote dataset this also bounds what is downloaded "
                             "(subset streamed via HTTP range) — always set it.")
    parser.add_argument("--max-per-role", type=int, default=8000,
                        help="Cap rows per role with seeded subsampling (0=uncapped).")
    parser.add_argument("--epsilon", type=float, default=0.010,
                        help="Seconds within which notes are one onset group.")
    parser.add_argument("--unmapped", choices=("skip", "perc"), default="skip",
                        help="Handle GM notes outside the role map.")
    parser.add_argument("--seed", type=int, default=42,
                        help="Seed for per-role subsampling.")
    args = parser.parse_args(argv)

    if args.local_root is not None:
        root = args.local_root
        if not root.exists():
            sys.stderr.write(f"--local-root does not exist: {root}\n")
            return 2
    else:
        try:
            root = _ensure_dataset(args.cache_dir, args.dataset_url, args.limit)
        except Exception as exc:
            sys.stderr.write(f"[egmd] dataset unavailable: {exc}\n")
            return 1

    rows = harvest(
        root,
        epsilon=args.epsilon,
        unmapped=args.unmapped,
        limit=args.limit,
        max_per_role=args.max_per_role,
        seed=args.seed,
    )
    if not rows:
        sys.stderr.write("[egmd] no rows harvested\n")
        return 1

    write_manifest(rows, args.out)
    print(f"[egmd] wrote {len(rows)} rows to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
