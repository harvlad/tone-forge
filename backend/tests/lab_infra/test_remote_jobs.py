"""Remote job bundle: export missing-only, worker resume, validated import."""
from __future__ import annotations

import json
import subprocess
import sys

import pandas as pd

from lab import cache, config, runner
from lab.remote import jobs


def test_export_contains_only_missing(fake_corpus, fake_adapter):
    stems = fake_corpus[fake_corpus["split"] == "validation"]
    # pre-cache 2 stems locally
    for _, row in stems.head(2).iterrows():
        runner.get_predictions("fake_model", row)
    bundle = jobs.export_job("fake_model", stems)
    job = json.loads((bundle / "job.json").read_text())
    assert job["n_stems"] == len(stems) - 2
    assert (bundle / "lab_worker" / "worker.py").exists()
    assert (bundle / "lab_worker" / "lab" / "adapters" / "base.py").exists()
    audio_files = list((bundle / "audio").glob("*"))
    assert len(audio_files) == len(stems) - 2


def test_estimate_reports_cache_split(fake_corpus, fake_adapter):
    stems = fake_corpus[fake_corpus["split"] == "validation"]
    for _, row in stems.head(2).iterrows():
        runner.get_predictions("fake_model", row)
    est = jobs.estimate("fake_model", stems, hourly_usd=0.5,
                        throughput_audio_sec_per_sec=10.0)
    assert est["cached"] == 2
    assert est["missing"] == len(stems) - 2
    assert "est_cost_usd" in est


def test_queue_add_and_load(fake_corpus, fake_adapter):
    stems = fake_corpus[fake_corpus["split"] == "validation"]
    entry = jobs.queue_add("fake_model", stems, tier="scout")
    assert len(entry["stem_ids"]) == len(stems)
    q = jobs.queue_load()
    assert q and q[0]["model_id"] == "fake_model"


def test_import_results_roundtrip(fake_corpus, fake_adapter):
    """Simulate a remote worker by writing results, then import."""
    stems = fake_corpus[fake_corpus["split"] == "validation"].head(2)
    bundle = jobs.export_job("fake_model", stems)
    job = json.loads((bundle / "job.json").read_text())
    from lab import schema
    for s in job["stems"]:
        df = schema.normalize_predictions(
            [{"pitch": 40, "onset": 0.1, "offset": 0.4, "velocity": 70}])
        df.to_parquet(bundle / "results" / f"{s['prediction_key']}.parquet", index=False)
        (bundle / "results" / f"{s['prediction_key']}.json").write_text(json.dumps(
            {"status": "ok", "runtime_seconds": 1.0,
             "checkpoint_hash": job["checkpoint_hash"]}))
    r = jobs.import_results(bundle)
    assert len(r["imported"]) == 2 and not r["failed"]
    # imported predictions are indistinguishable cache hits: zero inference
    for _, row in stems.iterrows():
        runner.get_predictions("fake_model", row)
    assert fake_adapter.calls == 0
    for s in job["stems"]:
        meta = cache.load_meta("fake_model", s["prediction_key"])
        assert meta["provenance"].startswith("remote_job:")


def test_import_rejects_checkpoint_mismatch(fake_corpus, fake_adapter):
    stems = fake_corpus[fake_corpus["split"] == "validation"].head(1)
    bundle = jobs.export_job("fake_model", stems)
    job = json.loads((bundle / "job.json").read_text())
    s = job["stems"][0]
    from lab import schema
    df = schema.normalize_predictions([{"pitch": 40, "onset": 0.1, "offset": 0.4}])
    df.to_parquet(bundle / "results" / f"{s['prediction_key']}.parquet", index=False)
    (bundle / "results" / f"{s['prediction_key']}.json").write_text(json.dumps(
        {"status": "ok", "checkpoint_hash": "WRONG"}))
    r = jobs.import_results(bundle)
    assert len(r["failed"]) == 1 and not r["imported"]


def test_import_rejects_invalid_timing(fake_corpus, fake_adapter):
    stems = fake_corpus[fake_corpus["split"] == "validation"].head(1)
    bundle = jobs.export_job("fake_model", stems)
    job = json.loads((bundle / "job.json").read_text())
    s = job["stems"][0]
    df = pd.DataFrame([{"pitch": 60, "onset": 500.0, "offset": 501.0,
                        "velocity": 0, "confidence": 0.0, "instrument": "",
                        "source_index": 0}])
    df.to_parquet(bundle / "results" / f"{s['prediction_key']}.parquet", index=False)
    r = jobs.import_results(bundle)
    assert len(r["failed"]) == 1
