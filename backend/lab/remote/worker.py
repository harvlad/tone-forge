#!/usr/bin/env python3
"""Disposable remote GPU worker.  Runs inside an exported job bundle.

    python lab_worker/worker.py --bundle /path/to/bundle

Behavior:
  - validates environment (adapter importable) before any work
  - verifies each stem's audio hash before inference
  - processes ONLY stems without an existing result (resumable)
  - writes results incrementally + atomically (one parquet+json per stem)
  - records per-stem failures; one bad stem never kills the job

The bundle ships a copy of the `lab` package (lab_worker/lab), so the
EXACT adapter code that defines local cache identity runs remotely.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
import time
import traceback
from pathlib import Path


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", required=True)
    ap.add_argument("--skip-audio-hash-check", action="store_true")
    args = ap.parse_args()
    bundle = Path(args.bundle).resolve()

    job = json.loads((bundle / "job.json").read_text())
    results = bundle / "results"
    results.mkdir(exist_ok=True)

    # lab package shipped inside the bundle
    sys.path.insert(0, str(bundle / "lab_worker"))
    import os
    os.environ.setdefault("TONEFORGE_LAB_DATA", str(bundle / "lab_worker" / "_labdata"))
    from lab import schema  # noqa: E402
    from lab.adapters import get_adapter  # noqa: E402

    adapter = get_adapter(job["model_id"])
    if not adapter.available():
        print(f"FATAL: adapter '{job['model_id']}' deps not importable in this env",
              file=sys.stderr)
        return 2
    try:
        remote_ck = adapter.checkpoint_hash()
    except Exception:
        remote_ck = ""
    if remote_ck and remote_ck != job["checkpoint_hash"]:
        print(f"FATAL: checkpoint mismatch: remote {remote_ck[:12]} != "
              f"job {job['checkpoint_hash'][:12]}", file=sys.stderr)
        return 3

    todo = [s for s in job["stems"]
            if not (results / f"{s['prediction_key']}.parquet").exists()]
    print(f"[worker] {len(todo)}/{len(job['stems'])} stems to process", flush=True)

    ok = fail = 0
    for i, s in enumerate(todo):
        key = s["prediction_key"]
        audio = bundle / s["audio"]
        meta = {"stem_id": s["stem_id"], "key": key,
                "device": platform.platform(), "checkpoint_hash": remote_ck}
        try:
            if not args.skip_audio_hash_check:
                got = file_sha256(audio)
                if got != s["audio_hash"]:
                    raise RuntimeError(f"audio hash mismatch ({got[:12]})")
            start = time.time()
            notes = adapter.predict(audio)
            df = schema.normalize_predictions(notes)
            schema.validate_predictions(df, audio_duration=s.get("duration"))
            meta["runtime_seconds"] = time.time() - start
            meta["n_notes"] = int(len(df))
            meta["status"] = "ok"
            tmp = results / f"{key}.parquet.tmp"
            df.to_parquet(tmp, index=False)
            tmp.rename(results / f"{key}.parquet")
            ok += 1
        except Exception as e:
            meta["status"] = "failed"
            meta["error"] = f"{e}\n{traceback.format_exc()}"[:4000]
            fail += 1
        (results / f"{key}.json").write_text(json.dumps(meta))
        print(f"[worker] [{i+1}/{len(todo)}] {s['stem_id']}: {meta['status']}",
              flush=True)

    print(f"[worker] done: {ok} ok, {fail} failed", flush=True)
    (bundle / "worker_done.json").write_text(json.dumps(
        {"ok": ok, "failed": fail, "finished_at": time.time()}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
