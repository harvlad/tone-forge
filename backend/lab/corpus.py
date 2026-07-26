"""Canonical corpus manifest.

One Parquet table describing every (audio stem, GT MIDI) pair across the
registered datasets.  Stem identity is `dataset:track:stem` — never an
absolute path.  Rebuild is incremental: files whose (size, mtime) are
unchanged reuse the previously computed hashes, so a rebuild after adding
a dataset does not re-read 46 GB.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd
import soundfile as sf
import yaml

from . import config
from .hashing import file_sha256

MANIFEST_VERSION = 1

COLUMNS = [
    "stem_id", "dataset", "dataset_version", "split", "track_id", "stem_name",
    "audio_path", "audio_hash", "audio_size", "audio_mtime",
    "midi_path", "midi_hash", "midi_size", "midi_mtime",
    "inst_class_gm", "inst_class_meta", "program_num", "is_drum",
    "plugin_name", "duration", "sample_rate", "channels",
]


def make_stem_id(dataset: str, track_id: str, stem_name: str) -> str:
    return f"{dataset}:{track_id}:{stem_name}"


def _audio_info(path: Path):
    try:
        info = sf.info(str(path))
        return float(info.duration), int(info.samplerate), int(info.channels)
    except Exception:
        return float("nan"), 0, 0


def _scan_slakh(dataset: str, root: Path, prev: dict, progress: bool = True):
    """Yield manifest rows for a Slakh-layout dataset (split/Track*/stems)."""
    split_dirs = [d for d in config.SLAKH_SPLITS if (root / d).is_dir()]
    if not split_dirs:  # flat layout (babyslakh)
        split_dirs = [""]
    for split in split_dirs:
        base = root / split if split else root
        track_dirs = sorted(p for p in base.iterdir() if p.is_dir())
        for ti, track_dir in enumerate(track_dirs):
            meta_path = track_dir / "metadata.yaml"
            if not meta_path.exists():
                continue
            try:
                with open(meta_path) as f:
                    meta = yaml.safe_load(f)
            except Exception:
                continue
            if not isinstance(meta, dict):
                continue
            if progress and ti % 100 == 0:
                print(f"  [{dataset}/{split or 'flat'}] {ti}/{len(track_dirs)} tracks",
                      file=sys.stderr, flush=True)
            stems_dir = track_dir / "stems"
            midi_dir = track_dir / "MIDI"
            for stem_name, stem_info in (meta.get("stems") or {}).items():
                audio_path = None
                for ext in (".flac", ".wav"):
                    cand = stems_dir / f"{stem_name}{ext}"
                    if cand.exists():
                        audio_path = cand
                        break
                midi_path = midi_dir / f"{stem_name}.mid"
                if audio_path is None or not midi_path.exists():
                    continue
                program = int(stem_info.get("program_num", 0) or 0)
                row = {
                    "stem_id": make_stem_id(dataset, track_dir.name, stem_name),
                    "dataset": dataset,
                    "dataset_version": str(meta.get("UUID", "") or ""),
                    "split": split or "flat",
                    "track_id": track_dir.name,
                    "stem_name": stem_name,
                    "audio_path": str(audio_path.relative_to(root)),
                    "midi_path": str(midi_path.relative_to(root)),
                    "inst_class_gm": config.get_inst_class(program),
                    "inst_class_meta": str(stem_info.get("inst_class", "") or ""),
                    "program_num": program,
                    "is_drum": bool(stem_info.get("is_drum", False)),
                    "plugin_name": str(stem_info.get("plugin_name", "") or ""),
                }
                for kind, path in (("audio", audio_path), ("midi", midi_path)):
                    st = path.stat()
                    row[f"{kind}_size"] = st.st_size
                    row[f"{kind}_mtime"] = st.st_mtime
                    cached = prev.get((row["stem_id"], kind))
                    if cached and cached[0] == st.st_size and cached[1] == st.st_mtime:
                        row[f"{kind}_hash"] = cached[2]
                    else:
                        row[f"{kind}_hash"] = file_sha256(path)
                cached_ai = prev.get((row["stem_id"], "audioinfo"))
                if cached_ai and row["audio_hash"] == cached_ai[0]:
                    row["duration"], row["sample_rate"], row["channels"] = cached_ai[1:]
                else:
                    row["duration"], row["sample_rate"], row["channels"] = _audio_info(audio_path)
                yield row


def _prev_hash_index(manifest: Optional[pd.DataFrame]) -> dict:
    prev: dict = {}
    if manifest is None:
        return prev
    for r in manifest.itertuples():
        prev[(r.stem_id, "audio")] = (r.audio_size, r.audio_mtime, r.audio_hash)
        prev[(r.stem_id, "midi")] = (r.midi_size, r.midi_mtime, r.midi_hash)
        prev[(r.stem_id, "audioinfo")] = (r.audio_hash, r.duration, r.sample_rate, r.channels)
    return prev


def build_manifest(datasets: Optional[Iterable[str]] = None, progress: bool = True) -> pd.DataFrame:
    """(Re)build the manifest incrementally and persist it."""
    config.ensure_dirs()
    prev = _prev_hash_index(load_manifest(missing_ok=True))
    rows = []
    for name in (datasets or config.DATASETS):
        root = config.DATASETS[name]
        if not root.is_dir():
            print(f"  dataset '{name}' root missing: {root} — skipped", file=sys.stderr)
            continue
        rows.extend(_scan_slakh(name, root, prev, progress=progress))
    df = pd.DataFrame(rows, columns=COLUMNS)
    dupes = df[df["stem_id"].duplicated(keep=False)]
    if len(dupes):
        raise RuntimeError(
            f"stem_id collisions in manifest (identity must be unique): "
            f"{sorted(dupes['stem_id'].unique())[:5]} ...")
    tmp = config.MANIFEST_PATH.with_suffix(".parquet.tmp")
    df.to_parquet(tmp, index=False)
    tmp.rename(config.MANIFEST_PATH)
    return df


def load_manifest(missing_ok: bool = False) -> Optional[pd.DataFrame]:
    if not config.MANIFEST_PATH.exists():
        if missing_ok:
            return None
        raise FileNotFoundError(
            f"No corpus manifest at {config.MANIFEST_PATH}. Run: lab corpus build")
    return pd.read_parquet(config.MANIFEST_PATH)


def resolve_audio(row) -> Path:
    return config.DATASETS[row["dataset"]] / row["audio_path"]


def resolve_midi(row) -> Path:
    return config.DATASETS[row["dataset"]] / row["midi_path"]


def select_stems(manifest: pd.DataFrame, dataset: Optional[str] = None,
                 split: Optional[str] = None, inst_class: Optional[str] = None,
                 exclude_unknown: bool = True) -> pd.DataFrame:
    df = manifest
    if dataset:
        df = df[df["dataset"] == dataset]
    if split:
        df = df[df["split"] == split]
    if inst_class:
        df = df[df["inst_class_gm"] == inst_class]
    elif exclude_unknown:
        df = df[df["inst_class_gm"] != "Unknown"]
    return df.sort_values("stem_id").reset_index(drop=True)
