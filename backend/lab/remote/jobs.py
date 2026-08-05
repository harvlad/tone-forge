"""Remote GPU job lifecycle (provider-neutral).

Flow:  pending-gpu -> estimate -> USER APPROVES -> export bundle ->
       (rsync/scp bundle to rented box, run worker) -> import-results ->
       terminate machine.

A bundle is a self-contained directory:
    job.json            manifest: model, config, stems (ids + hashes)
    audio/<stem>.flac   only the MISSING stems' audio
    lab_worker/         standalone worker (worker.py + adapter deps list)
    results/            worker writes one parquet+json per stem, incrementally

Nothing here provisions machines or spends money.
"""
from __future__ import annotations

import datetime
import json
import shutil
from pathlib import Path
from typing import List, Optional

import pandas as pd

from .. import cache, config, corpus, runner, schema
from ..adapters import get_adapter
from ..hashing import file_sha256

def _queue_path() -> Path:
    return config.JOBS_DIR / "pending.json"


# ---------------------------------------------------------------------------
# Pending-work queue
# ---------------------------------------------------------------------------

def queue_add(model_id: str, stems: pd.DataFrame, tier: str = "",
              inst_class: Optional[str] = None, note: str = "") -> dict:
    """Record missing predictions as pending GPU work (no provisioning)."""
    missing = runner.missing_stems(model_id, stems)
    entry = {
        "model_id": model_id,
        "tier": tier,
        "inst_class": inst_class,
        "stem_ids": sorted(missing["stem_id"].tolist()),
        "audio_seconds": float(missing["duration"].fillna(0).sum()),
        "note": note,
        "added_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    q = queue_load()
    # replace an existing entry for same (model, tier, class)
    q = [e for e in q if not (e["model_id"] == model_id and e["tier"] == tier
                              and e.get("inst_class") == inst_class)]
    if entry["stem_ids"]:
        q.append(entry)
    config.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    _queue_path().write_text(json.dumps(q, indent=1))
    return entry


def queue_load() -> List[dict]:
    if _queue_path().exists():
        try:
            return json.loads(_queue_path().read_text())
        except Exception:
            return []
    return []


# ---------------------------------------------------------------------------
# Cost estimation
# ---------------------------------------------------------------------------

def estimate(model_id: str, stems: pd.DataFrame, gpu_name: str = "RTX 4090",
             hourly_usd: float = 0.40, throughput_audio_sec_per_sec: Optional[float] = None,
             setup_minutes: float = 10.0) -> dict:
    """Preflight summary for a remote run.  Throughput comes from measured
    values in models/throughput.json when available; otherwise the caller
    must supply one (be explicit, don't guess silently)."""
    missing = runner.missing_stems(model_id, stems)
    audio_sec = float(missing["duration"].fillna(0).sum())
    tp = throughput_audio_sec_per_sec
    tp_source = "user-supplied"
    if tp is None and config.THROUGHPUT_PATH.exists():
        data = json.loads(config.THROUGHPUT_PATH.read_text())
        devices = data.get(model_id, {})
        gpu_entries = {d: e for d, e in devices.items() if "cuda" in d or "gpu" in d}
        pick = gpu_entries or devices
        if pick:
            d, e = sorted(pick.items())[0]
            tp = e.get("audio_sec_per_sec")
            tp_source = f"measured on {d}"
    est = {
        "model": model_id,
        "gpu": gpu_name,
        "hourly_usd": hourly_usd,
        "total_stems": int(len(stems)),
        "cached": int(len(stems) - len(missing)),
        "missing": int(len(missing)),
        "audio_minutes": round(audio_sec / 60, 1),
        "throughput_audio_sec_per_sec": tp,
        "throughput_source": tp_source if tp else "UNKNOWN — supply --throughput",
        "setup_minutes": setup_minutes,
    }
    if tp:
        runtime_min = audio_sec / tp / 60 + setup_minutes
        est["est_runtime_minutes"] = round(runtime_min, 1)
        est["est_cost_usd"] = round(runtime_min / 60 * hourly_usd, 2)
        if len(missing):
            est["est_cost_per_1000_stems_usd"] = round(
                est["est_cost_usd"] / len(missing) * 1000, 2)
    return est


# ---------------------------------------------------------------------------
# Bundle export
# ---------------------------------------------------------------------------

def export_job(model_id: str, stems: pd.DataFrame, job_id: Optional[str] = None,
               copy_audio: bool = True) -> Path:
    """Create a job bundle containing ONLY missing predictions' inputs."""
    adapter = get_adapter(model_id)
    missing = runner.missing_stems(model_id, stems)
    if len(missing) == 0:
        raise RuntimeError("nothing missing — no job to export")
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
    job_id = job_id or f"job_{model_id}_{stamp}"
    bundle = config.JOBS_DIR / job_id
    (bundle / "audio").mkdir(parents=True, exist_ok=True)
    (bundle / "results").mkdir(exist_ok=True)

    stems_out = []
    for _, row in missing.iterrows():
        src = corpus.resolve_audio(row)
        rel = f"audio/{row['stem_id'].replace(':', '__')}{src.suffix}"
        if copy_audio:
            shutil.copy2(src, bundle / rel)
        stems_out.append({
            "stem_id": row["stem_id"],
            "audio": rel,
            "audio_hash": row["audio_hash"],
            "duration": None if pd.isna(row["duration"]) else float(row["duration"]),
            "prediction_key": runner.stem_prediction_key(adapter, row),
        })

    job = {
        "job_id": job_id,
        "model_id": model_id,
        "model_version": adapter.model_version,
        "adapter_version": adapter.adapter_version,
        "checkpoint_hash": adapter.checkpoint_hash(),
        "inference_config": adapter.inference_config(),
        "environment": adapter.environment,
        "n_stems": len(stems_out),
        "stems": stems_out,
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    (bundle / "job.json").write_text(json.dumps(job, indent=1))

    # standalone worker copy — ships the lab package so the EXACT adapter
    # code defining cache identity runs remotely
    worker_dir = bundle / "lab_worker"
    worker_dir.mkdir(exist_ok=True)
    shutil.copy2(Path(__file__).parent / "worker.py", worker_dir / "worker.py")
    lab_src = Path(__file__).parent.parent
    dst = worker_dir / "lab"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(lab_src, dst,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (worker_dir / "README.txt").write_text(
        "On the rented GPU machine:\n"
        "  1. install the model environment (see job.json 'environment')\n"
        "  2. pip install pandas pyarrow\n"
        "  3. python lab_worker/worker.py --bundle <this dir>\n"
        "Worker is resumable: it skips stems already in results/.\n"
        "Sync results/ back, then locally: lab import-results <this dir>\n")
    return bundle


# ---------------------------------------------------------------------------
# Result import
# ---------------------------------------------------------------------------

def import_results(bundle: Path) -> dict:
    """Validate + import a bundle's results/ into the local prediction cache."""
    job = json.loads((bundle / "job.json").read_text())
    model_id = job["model_id"]
    by_id = {s["stem_id"]: s for s in job["stems"]}
    imported, skipped, failed = [], [], []
    for s in job["stems"]:
        key = s["prediction_key"]
        pq = bundle / "results" / f"{key}.parquet"
        meta_p = bundle / "results" / f"{key}.json"
        if not pq.exists():
            skipped.append(s["stem_id"])
            continue
        if cache.has(model_id, key):
            skipped.append(s["stem_id"])  # already imported/cached
            continue
        try:
            df = pd.read_parquet(pq)
            df = df[schema.PREDICTION_COLUMNS]
            schema.validate_predictions(df, audio_duration=s.get("duration"))
            rmeta = json.loads(meta_p.read_text()) if meta_p.exists() else {}
            if rmeta.get("status") == "failed":
                raise RuntimeError(f"remote failure: {rmeta.get('error', '')[:200]}")
            rck = rmeta.get("checkpoint_hash")
            if rck and rck != job["checkpoint_hash"]:
                raise RuntimeError("remote checkpoint hash mismatch")
        except Exception as e:
            failed.append({"stem_id": s["stem_id"], "error": str(e)})
            continue
        cache.store(model_id, key, df,
                    stem_id=s["stem_id"], audio_hash=s["audio_hash"],
                    model_version=job["model_version"],
                    checkpoint_hash=job["checkpoint_hash"],
                    adapter_version=job["adapter_version"],
                    inference_config=job["inference_config"],
                    runtime_seconds=rmeta.get("runtime_seconds"),
                    provenance=f"remote_job:{job['job_id']}",
                    device=rmeta.get("device", "remote"),
                    extra={"remote_meta": rmeta} if rmeta else None)
        imported.append(s["stem_id"])
    return {"job_id": job["job_id"], "model_id": model_id,
            "imported": imported, "skipped": skipped, "failed": failed}
