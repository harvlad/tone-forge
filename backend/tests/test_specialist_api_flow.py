"""HTTP-level coverage for the experimental-specialist API surfaces.

Pins the wire contract for:
  - engine/target_family multipart fields on /api/analyze-upload
    landing in the job payload and the engine claim descriptor;
  - the terminal job message preserving the worker's engine self-report
    (rebuilt from result.analysis_engine at /complete);
  - the eager R2 push hook firing after /complete;
  - /api/debug/specialist-feedback POST/GET validation;
  - /api/debug/specialist-provenance shape.
"""
from __future__ import annotations

import asyncio
import io
import json
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

import tone_forge_api
from tone_forge.specialist import feedback as fb

client = TestClient(tone_forge_api.app)


@pytest.fixture(autouse=True)
def _isolated_storage(tmp_path, monkeypatch):
    """Never touch the real shared history.json (R2-synced) or uploads
    dir from tests — same isolation pattern as test_history_*.py."""
    monkeypatch.setattr(tone_forge_api, "_HISTORY_FILE", tmp_path / "history.json")
    up = tmp_path / "uploads"
    up.mkdir()
    monkeypatch.setattr(tone_forge_api, "_UPLOADS_DIR", up)


def _tiny_wav() -> bytes:
    buf = io.BytesIO()
    sr = 22050
    t = np.arange(sr) / sr
    samples = (np.sin(2 * np.pi * 220 * t) * 0.4 * 32767).astype(np.int16)
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(samples.tobytes())
    return buf.getvalue()


@pytest.fixture()
def engine_token(monkeypatch):
    monkeypatch.setenv("TONEFORGE_ENGINE_TOKEN", "test-engine-token")
    return {"X-Engine-Token": "test-engine-token"}


# ---------------------------------------------------------------------------
# engine/target_family field plumbing
# ---------------------------------------------------------------------------

def test_upload_fields_reach_payload_and_claim(engine_token):
    r = client.post(
        "/api/analyze-upload",
        files={"file": ("t.wav", _tiny_wav(), "audio/wav")},
        data={"attested": "true", "engine": "experimental_specialist",
              "target_family": "guitar"},
    )
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]

    job = tone_forge_api._JOBS.get(job_id)
    assert job.payload["analysis_engine"] == "experimental_specialist"
    assert job.payload["target_family"] == "guitar"

    c = client.post("/api/engine/claim",
                    json={"worker_id": "t", "device": "cpu", "wait_sec": 0},
                    headers=engine_token)
    assert c.status_code == 200, c.text
    desc = c.json()
    assert desc["job_id"] == job_id
    assert desc["analysis_engine"] == "experimental_specialist"
    assert desc["target_family"] == "guitar"


def test_upload_without_fields_defaults_to_current(engine_token):
    r = client.post(
        "/api/analyze-upload",
        files={"file": ("t.wav", _tiny_wav(), "audio/wav")},
        data={"attested": "true"},
    )
    assert r.status_code == 200
    job = tone_forge_api._JOBS.get(r.json()["job_id"])
    assert "analysis_engine" not in (job.payload or {})
    c = client.post("/api/engine/claim",
                    json={"worker_id": "t", "device": "cpu", "wait_sec": 0},
                    headers=engine_token)
    assert c.status_code == 200
    assert c.json()["analysis_engine"] is None


# ---------------------------------------------------------------------------
# complete: engine banner + eager R2 hook
# ---------------------------------------------------------------------------

def _claimed_job(engine_token) -> str:
    r = client.post(
        "/api/analyze-upload",
        files={"file": ("t.wav", _tiny_wav(), "audio/wav")},
        data={"attested": "true", "engine": "experimental_specialist",
              "target_family": "guitar"},
    )
    job_id = r.json()["job_id"]
    c = client.post("/api/engine/claim",
                    json={"worker_id": "t", "device": "cpu", "wait_sec": 0},
                    headers=engine_token)
    assert c.json()["job_id"] == job_id
    return job_id


def test_complete_preserves_engine_banner_and_warms_r2(engine_token, monkeypatch):
    calls = []
    monkeypatch.setattr(tone_forge_api, "_maybe_upload_stems_to_r2",
                        lambda eid, entry: calls.append(eid) or False)
    job_id = _claimed_job(engine_token)
    result = {
        "duration_sec": 1.0,
        "detected_type": "guitar",
        "analysis_engine": "experimental_specialist",
        "specialist_provenance": {"engine": "experimental_specialist",
                                  "routed": True, "transcriber": "riley_guitar"},
    }
    r = client.post(f"/api/engine/job/{job_id}/complete",
                    json=result, headers=engine_token)
    assert r.status_code == 200, r.text
    job = tone_forge_api._JOBS.get(job_id)
    assert job.status == "done"
    assert "experimental specialist" in job.message
    # eager push scheduled + executed (TestClient portal runs the task)
    hid = r.json()["history_id"]
    if not calls:  # scheduling raced the response — run the hook directly
        asyncio.get_event_loop().run_until_complete(
            tone_forge_api._eager_r2_push(hid))
    assert calls and calls[0] == hid


def test_complete_current_engine_plain_banner(engine_token, monkeypatch):
    monkeypatch.setattr(tone_forge_api, "_maybe_upload_stems_to_r2",
                        lambda eid, entry: False)
    r = client.post(
        "/api/analyze-upload",
        files={"file": ("t.wav", _tiny_wav(), "audio/wav")},
        data={"attested": "true"},
    )
    job_id = r.json()["job_id"]
    client.post("/api/engine/claim",
                json={"worker_id": "t", "device": "cpu", "wait_sec": 0},
                headers=engine_token)
    r = client.post(f"/api/engine/job/{job_id}/complete",
                    json={"duration_sec": 1.0},
                    headers=engine_token)
    assert r.status_code == 200
    assert tone_forge_api._JOBS.get(job_id).message == "Analysis complete"


# ---------------------------------------------------------------------------
# feedback endpoints
# ---------------------------------------------------------------------------

def test_feedback_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(fb, "FEEDBACK_PATH", tmp_path / "fb.jsonl")
    r = client.post("/api/debug/specialist-feedback", json={
        "verdict": "WORSE", "song_hash": "h", "target_family": "guitar",
        "tags": ["wrong_octave"], "note": "guitar off", "history_id": "x"})
    assert r.status_code == 200, r.text
    assert r.json()["record"]["verdict"] == "WORSE"
    g = client.get("/api/debug/specialist-feedback")
    assert g.status_code == 200
    assert len(g.json()["records"]) == 1


def test_feedback_rejects_invalid(tmp_path, monkeypatch):
    monkeypatch.setattr(fb, "FEEDBACK_PATH", tmp_path / "fb.jsonl")
    r = client.post("/api/debug/specialist-feedback",
                    json={"verdict": "8/10", "song_hash": "h",
                          "target_family": "guitar"})
    assert r.status_code == 422
    r = client.post("/api/debug/specialist-feedback",
                    json={"verdict": "BETTER", "tags": ["vibes"]})
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# provenance endpoint
# ---------------------------------------------------------------------------

def test_provenance_endpoint_shape(monkeypatch):
    fake = {"result": {
        "analysis_engine": "experimental_specialist",
        "specialist_provenance": {"routed": True, "transcriber": "riley_guitar"},
        "profiling": {"total_ms": 1.0},
        "midi_stems": {"guitar": {"method": "specialist:riley_guitar@x"}},
        "stems_paths": {"guitar": "/x", "bass": "/y"},
    }}
    monkeypatch.setattr(tone_forge_api, "_get_history_item",
                        lambda hid: fake if hid == "abc" else None)
    r = client.get("/api/debug/specialist-provenance/abc")
    assert r.status_code == 200
    body = r.json()
    assert body["analysis_engine"] == "experimental_specialist"
    assert body["specialist_provenance"]["transcriber"] == "riley_guitar"
    assert body["midi_methods"]["guitar"].startswith("specialist:")
    r = client.get("/api/debug/specialist-provenance/missing")
    assert r.status_code == 404
