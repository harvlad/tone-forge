"""Immutable prediction cache — the heart of the lab.

Cache identity = sha256 over (audio_hash, model_id, model_version,
checkpoint_hash, adapter_version, canonical inference config).  Changing
ANY of those yields a new key; changing evaluation parameters never
touches this cache.

Layout:
    lab_data/predictions/<model_id>/<key>.parquet   canonical note table
    lab_data/predictions/<model_id>/<key>.json      provenance sidecar

Writes are atomic (tmp + rename).  Entries are never deleted by the lab;
`invalidate` renames to *.invalid.* for manual inspection.
"""
from __future__ import annotations

import datetime
import json
import platform
from pathlib import Path
from typing import Optional

import pandas as pd

from . import config
from .hashing import canonical_json, config_hash, short


def prediction_key(audio_hash: str, model_id: str, model_version: str,
                   checkpoint_hash: str, adapter_version: str,
                   inference_config: dict) -> str:
    return short(config_hash({
        "audio_hash": audio_hash,
        "model_id": model_id,
        "model_version": model_version,
        "checkpoint_hash": checkpoint_hash,
        "adapter_version": adapter_version,
        "inference_config": inference_config,
    }), 24)


def _paths(model_id: str, key: str):
    d = config.PREDICTIONS_DIR / model_id
    return d / f"{key}.parquet", d / f"{key}.json"


def has(model_id: str, key: str) -> bool:
    pq, meta = _paths(model_id, key)
    if not (pq.exists() and meta.exists()):
        return False
    try:
        m = json.loads(meta.read_text())
        return m.get("status") == "ok"
    except Exception:
        return False


def load(model_id: str, key: str) -> Optional[pd.DataFrame]:
    pq, _ = _paths(model_id, key)
    if not has(model_id, key):
        return None
    try:
        return pd.read_parquet(pq)
    except Exception:
        return None  # corrupt parquet -> treated as miss; caller may re-run


def load_meta(model_id: str, key: str) -> Optional[dict]:
    _, meta = _paths(model_id, key)
    if not meta.exists():
        return None
    try:
        return json.loads(meta.read_text())
    except Exception:
        return None


def store(model_id: str, key: str, df: pd.DataFrame, *, stem_id: str,
          audio_hash: str, model_version: str, checkpoint_hash: str,
          adapter_version: str, inference_config: dict,
          runtime_seconds: Optional[float] = None,
          provenance: str = "computed", device: str = "",
          extra: Optional[dict] = None) -> None:
    pq, meta = _paths(model_id, key)
    pq.parent.mkdir(parents=True, exist_ok=True)
    tmp = pq.with_suffix(".parquet.tmp")
    df.to_parquet(tmp, index=False)
    tmp.rename(pq)
    meta_obj = {
        "status": "ok",
        "key": key,
        "stem_id": stem_id,
        "audio_hash": audio_hash,
        "model_id": model_id,
        "model_version": model_version,
        "checkpoint_hash": checkpoint_hash,
        "adapter_version": adapter_version,
        "inference_config": inference_config,
        "n_notes": int(len(df)),
        "runtime_seconds": runtime_seconds,
        "provenance": provenance,
        "device": device or platform.platform(),
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    if extra:
        meta_obj["extra"] = extra
    tmp_meta = meta.with_suffix(".json.tmp")
    tmp_meta.write_text(canonical_json(meta_obj))
    tmp_meta.rename(meta)


def store_failure(model_id: str, key: str, *, stem_id: str, error: str,
                  audio_hash: str = "", inference_config: Optional[dict] = None) -> None:
    """Record a per-stem failure (OOM, invalid audio, adapter error).
    Failures are NOT cache hits — a later run retries them — but the record
    preserves what happened."""
    _, meta = _paths(model_id, key)
    meta.parent.mkdir(parents=True, exist_ok=True)
    meta_obj = {
        "status": "failed",
        "key": key,
        "stem_id": stem_id,
        "audio_hash": audio_hash,
        "error": error[:2000],
        "inference_config": inference_config or {},
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    tmp_meta = meta.with_suffix(".json.tmp")
    tmp_meta.write_text(canonical_json(meta_obj))
    tmp_meta.rename(meta)


def invalidate(model_id: str, key: str) -> None:
    """Explicit regeneration request: move entry aside (never silent delete)."""
    pq, meta = _paths(model_id, key)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S")
    for p in (pq, meta):
        if p.exists():
            p.rename(p.with_name(p.name + f".invalid.{stamp}"))


def status() -> pd.DataFrame:
    """Summary of cache contents per model."""
    rows = []
    if config.PREDICTIONS_DIR.is_dir():
        for model_dir in sorted(config.PREDICTIONS_DIR.iterdir()):
            if not model_dir.is_dir():
                continue
            metas = list(model_dir.glob("*.json"))
            ok = failed = notes = 0
            size = 0
            for m in metas:
                try:
                    obj = json.loads(m.read_text())
                except Exception:
                    continue
                if obj.get("status") == "ok":
                    ok += 1
                    notes += obj.get("n_notes", 0) or 0
                elif obj.get("status") == "failed":
                    failed += 1
            for p in model_dir.glob("*.parquet"):
                size += p.stat().st_size
            rows.append({"model_id": model_dir.name, "cached": ok,
                         "failed": failed, "notes": notes,
                         "size_mb": round(size / 1e6, 1)})
    return pd.DataFrame(rows, columns=["model_id", "cached", "failed", "notes", "size_mb"])
