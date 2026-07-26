"""Cache-first inference orchestration.

`get_predictions` is the ONLY sanctioned way to obtain model predictions.
It checks the immutable cache first; the neural network runs only for
genuine misses (or explicit --force regeneration).
"""
from __future__ import annotations

import time
import traceback
from typing import Optional

import pandas as pd

from . import cache, corpus, schema
from .adapters import get_adapter


def stem_prediction_key(adapter, stem_row) -> str:
    return cache.prediction_key(
        audio_hash=stem_row["audio_hash"],
        model_id=adapter.model_id,
        model_version=adapter.model_version,
        checkpoint_hash=adapter.checkpoint_hash(),
        adapter_version=adapter.adapter_version,
        inference_config=adapter.inference_config(),
    )


def get_predictions(model_id: str, stem_row, *, allow_inference: bool = True,
                    force: bool = False, device: str = "local") -> Optional[pd.DataFrame]:
    """Predictions for one stem.  Cache hit -> load, no model run.
    Returns None when inference is disallowed/failed (caller records miss)."""
    adapter = get_adapter(model_id)
    key = stem_prediction_key(adapter, stem_row)

    if force:
        cache.invalidate(model_id, key)
    hit = cache.load(model_id, key)
    if hit is not None:
        return hit
    if not allow_inference:
        return None
    if adapter.requires_gpu:
        raise RuntimeError(
            f"{model_id} requires GPU; queue it via `lab pending-gpu` / export a job")
    if not adapter.available():
        raise RuntimeError(
            f"{model_id} dependencies not installed in this environment")

    audio_path = corpus.resolve_audio(stem_row)
    start = time.time()
    try:
        raw_notes = adapter.predict(audio_path)
        df = schema.normalize_predictions(raw_notes)
        schema.validate_predictions(df, audio_duration=stem_row.get("duration"))
    except Exception as e:
        cache.store_failure(model_id, key, stem_id=stem_row["stem_id"],
                            error=f"{e}\n{traceback.format_exc()}",
                            audio_hash=stem_row["audio_hash"],
                            inference_config=adapter.inference_config())
        return None
    runtime = time.time() - start
    cache.store(model_id, key, df,
                stem_id=stem_row["stem_id"], audio_hash=stem_row["audio_hash"],
                model_version=adapter.model_version,
                checkpoint_hash=adapter.checkpoint_hash(),
                adapter_version=adapter.adapter_version,
                inference_config=adapter.inference_config(),
                runtime_seconds=runtime, provenance="computed", device=device)
    _record_throughput(model_id, device, stem_row.get("duration"), runtime)
    return cache.load(model_id, key)


def missing_stems(model_id: str, stems: pd.DataFrame) -> pd.DataFrame:
    """Subset of `stems` with no valid cache entry for `model_id`.
    This is what a resumed/remote job actually processes."""
    adapter = get_adapter(model_id)
    ck = adapter.checkpoint_hash()
    cfg = adapter.inference_config()
    mask = []
    for _, row in stems.iterrows():
        key = cache.prediction_key(row["audio_hash"], adapter.model_id,
                                   adapter.model_version, ck,
                                   adapter.adapter_version, cfg)
        mask.append(not cache.has(model_id, key))
    return stems[pd.Series(mask, index=stems.index)]


def _record_throughput(model_id: str, device: str, audio_seconds, runtime: float) -> None:
    """Rolling throughput measurements -> models/throughput.json (used by
    GPU cost estimation)."""
    import json

    from . import config
    path = config.THROUGHPUT_PATH
    if not audio_seconds or runtime <= 0:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if path.exists():
        try:
            data = json.loads(path.read_text())
        except Exception:
            data = {}
    entry = data.setdefault(model_id, {}).setdefault(device, {
        "n": 0, "audio_seconds": 0.0, "runtime_seconds": 0.0})
    entry["n"] += 1
    entry["audio_seconds"] += float(audio_seconds)
    entry["runtime_seconds"] += float(runtime)
    entry["audio_sec_per_sec"] = entry["audio_seconds"] / entry["runtime_seconds"]
    path.write_text(json.dumps(data, indent=1, sort_keys=True))
