"""Remote GPU engine job flow: upload -> claim -> stems -> complete.

Covers the JobRegistry engine-job additions and the /api/analyze-upload
+ /api/engine/* endpoints. TestClient's request host is "testclient",
which is in _LOOPBACK_HOSTS, so engine auth passes without a token;
token enforcement is exercised explicitly via monkeypatched env.
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

import tone_forge_api as api
from tone_forge.analysis_jobs import JobRegistry

client = TestClient(api.app)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def engine_env(tmp_path, monkeypatch):
    """Fresh registry + isolated uploads/stems dirs + fake history."""
    registry = JobRegistry(tmp_path / "jobs")
    uploads = tmp_path / "uploads"
    stems_root = tmp_path / "stems"
    monkeypatch.setattr(api, "_JOBS", registry)
    monkeypatch.setattr(api, "_UPLOADS_DIR", uploads)
    monkeypatch.setattr(api, "_ENGINE_STEMS_ROOT", stems_root)
    monkeypatch.setitem(api._ENGINE_PRESENCE, "last_seen", 0.0)
    monkeypatch.setitem(api._ENGINE_PRESENCE, "device", "")
    history_calls: list[dict] = []

    def fake_add_to_history(entry, full_result=None, device_id=None, owner_id=None):
        history_calls.append({"entry": entry, "full_result": full_result})
        return {"id": "hist-test-1"}

    monkeypatch.setattr(api, "_add_to_history", fake_add_to_history)
    monkeypatch.delenv("TONEFORGE_ENGINE_TOKEN", raising=False)
    return {
        "registry": registry,
        "uploads": uploads,
        "stems_root": stems_root,
        "history_calls": history_calls,
    }


def _upload(filename="song.wav", attested="true", content=b"RIFFfake"):
    return client.post(
        "/api/analyze-upload",
        data={"attested": attested, "extract_midi": "true"},
        files={"file": (filename, io.BytesIO(content), "audio/wav")},
    )


def _claim(wait_sec=0.1, headers=None):
    return client.post(
        "/api/engine/claim",
        json={"worker_id": "w1", "device": "mps", "wait_sec": wait_sec},
        headers=headers or {},
    )


# ---------------------------------------------------------------------------
# registry: engine-job additions
# ---------------------------------------------------------------------------

def test_create_engine_job_kind_and_payload_persist(tmp_path):
    reg = JobRegistry(tmp_path / "jobs")
    job = reg.create_engine_job(
        filename="s.wav", attested=True, payload={"upload_path": "/x"}
    )
    assert job.kind == "engine"
    on_disk = json.loads((tmp_path / "jobs" / f"{job.id}.json").read_text())
    assert on_disk["kind"] == "engine"
    assert on_disk["payload"] == {"upload_path": "/x"}
    # payload never leaks to clients
    assert "payload" not in job.public_dict()
    assert "kind" not in job.public_dict()


def test_next_queued_engine_job_skips_server_jobs(tmp_path):
    reg = JobRegistry(tmp_path / "jobs")
    reg.create(filename="server.wav")  # kind=server, status=queued
    engine = reg.create_engine_job(filename="engine.wav")
    picked = reg.next_queued_engine_job()
    assert picked is not None
    assert picked.id == engine.id


def test_next_queued_engine_job_requeues_stale_running(tmp_path):
    reg = JobRegistry(tmp_path / "jobs")
    job = reg.create_engine_job(filename="s.wav")
    asyncio.run(reg.update(job.id, status="running"))
    job.updated_at = time.time() - 999  # worker went silent

    picked = reg.next_queued_engine_job(stale_after_sec=180)
    assert picked is not None
    assert picked.id == job.id
    assert picked.status == "queued"


def test_next_queued_engine_job_leaves_live_running_alone(tmp_path):
    reg = JobRegistry(tmp_path / "jobs")
    job = reg.create_engine_job(filename="s.wav")
    asyncio.run(reg.update(job.id, status="running"))
    assert reg.next_queued_engine_job(stale_after_sec=180) is None
    assert job.status == "running"


def test_recover_requeues_engine_jobs_but_errors_server_jobs(tmp_path):
    reg = JobRegistry(tmp_path / "jobs")
    server_job = reg.create()
    engine_job = reg.create_engine_job(filename="s.wav")
    asyncio.run(reg.update(server_job.id, status="running"))
    asyncio.run(reg.update(engine_job.id, status="running"))

    fresh = JobRegistry(tmp_path / "jobs")
    fresh.recover()
    assert fresh.get(server_job.id).status == "error"
    assert fresh.get(engine_job.id).status == "queued"


# ---------------------------------------------------------------------------
# /api/analyze-upload
# ---------------------------------------------------------------------------

def test_upload_rejects_unsupported_suffix(engine_env):
    resp = _upload(filename="notes.txt")
    assert resp.status_code == 400
    assert "Unsupported file type" in resp.text


def test_upload_requires_attestation(engine_env):
    resp = _upload(attested="false")
    assert resp.status_code == 400
    assert "attestation" in resp.text.lower()


def test_upload_creates_engine_job_and_stores_file(engine_env):
    resp = _upload(content=b"RIFF1234")
    assert resp.status_code == 200
    body = resp.json()
    job = engine_env["registry"].get(body["job_id"])
    assert job is not None
    assert job.kind == "engine"
    assert job.attested is True
    stored = Path(job.payload["upload_path"])
    assert stored.parent == engine_env["uploads"]
    assert stored.read_bytes() == b"RIFF1234"
    assert body["engine_online"] is False


# ---------------------------------------------------------------------------
# /api/engine/status + claim (presence)
# ---------------------------------------------------------------------------

def test_engine_status_offline_by_default(engine_env):
    resp = client.get("/api/engine/status")
    assert resp.status_code == 200
    assert resp.json()["online"] is False


def test_claim_empty_queue_returns_204_and_marks_presence(engine_env):
    resp = _claim()
    assert resp.status_code == 204
    status = client.get("/api/engine/status").json()
    assert status["online"] is True
    assert status["device"] == "mps"


def test_claim_requires_token_when_configured(engine_env, monkeypatch):
    monkeypatch.setenv("TONEFORGE_ENGINE_TOKEN", "sekret")
    assert _claim().status_code == 404  # no token -> non-enumerable 404
    assert _claim(headers={"X-Engine-Token": "wrong"}).status_code == 404
    assert _claim(headers={"X-Engine-Token": "sekret"}).status_code == 204
    # Bearer form accepted too.
    assert _claim(headers={"Authorization": "Bearer sekret"}).status_code == 204


# ---------------------------------------------------------------------------
# full worker flow
# ---------------------------------------------------------------------------

def test_full_engine_job_flow(engine_env):
    job_id = _upload(content=b"RIFFdata").json()["job_id"]

    # claim
    claim = _claim()
    assert claim.status_code == 200
    claimed = claim.json()
    assert claimed["job_id"] == job_id
    assert claimed["filename"] == "song.wav"
    assert claimed["extract_midi"] is True
    assert engine_env["registry"].get(job_id).status == "running"

    # source download
    src = client.get(f"/api/engine/job/{job_id}/file")
    assert src.status_code == 200
    assert src.content == b"RIFFdata"

    # progress
    prog = client.post(
        f"/api/engine/job/{job_id}/progress",
        json={"percent": 42.5, "message": "Separating stems"},
    )
    assert prog.status_code == 200
    snap = client.get(f"/api/job/{job_id}").json()
    assert snap["percent"] == 42.5
    assert snap["message"] == "Separating stems"

    # stem upload
    stem = client.post(
        f"/api/engine/job/{job_id}/stem",
        data={"role": "drums"},
        files={"file": ("drums.wav", io.BytesIO(b"stemdata"), "audio/wav")},
    )
    assert stem.status_code == 200
    stem_path = engine_env["stems_root"] / job_id / "drums.wav"
    assert stem_path.read_bytes() == b"stemdata"

    # complete: result stems_paths rewritten to the uploaded copies
    done = client.post(
        f"/api/engine/job/{job_id}/complete",
        json={
            "detected_type": "guitar",
            "duration_sec": 12.0,
            "stems_paths": {"drums": "http://127.0.0.1:7777/api/serve-file?path=/x"},
        },
    )
    assert done.status_code == 200
    assert done.json()["history_id"] == "hist-test-1"
    snap = client.get(f"/api/job/{job_id}").json()
    assert snap["status"] == "done"
    assert snap["history_id"] == "hist-test-1"

    saved = engine_env["history_calls"][0]["full_result"]
    assert saved["stems_paths"] == {
        "drums": f"/api/admin/serve-file?path={stem_path}"
    }
    assert saved["filename"] == "song.wav"
    assert engine_env["history_calls"][0]["entry"]["attested"] is True
    assert engine_env["history_calls"][0]["entry"]["deep_analysis"] is True


def test_stem_rejects_bad_role_and_suffix(engine_env):
    job_id = _upload().json()["job_id"]
    bad_role = client.post(
        f"/api/engine/job/{job_id}/stem",
        data={"role": "../evil"},
        files={"file": ("drums.wav", io.BytesIO(b"x"), "audio/wav")},
    )
    assert bad_role.status_code == 400
    bad_suffix = client.post(
        f"/api/engine/job/{job_id}/stem",
        data={"role": "drums"},
        files={"file": ("drums.exe", io.BytesIO(b"x"), "audio/wav")},
    )
    assert bad_suffix.status_code == 400


def test_fail_marks_job_errored(engine_env):
    job_id = _upload().json()["job_id"]
    _claim()
    resp = client.post(
        f"/api/engine/job/{job_id}/fail", json={"error": "demucs exploded"}
    )
    assert resp.status_code == 200
    snap = client.get(f"/api/job/{job_id}").json()
    assert snap["status"] == "error"
    assert snap["error"] == "demucs exploded"


def test_engine_endpoints_404_for_server_jobs(engine_env):
    job = engine_env["registry"].create(filename="server.wav")
    assert client.get(f"/api/engine/job/{job.id}/file").status_code == 404
    assert client.post(
        f"/api/engine/job/{job.id}/progress", json={"percent": 1}
    ).status_code == 404


def test_file_endpoint_410_when_source_gone(engine_env):
    job_id = _upload().json()["job_id"]
    job = engine_env["registry"].get(job_id)
    Path(job.payload["upload_path"]).unlink()
    assert client.get(f"/api/engine/job/{job_id}/file").status_code == 410


# ---------------------------------------------------------------------------
# retention sweep
# ---------------------------------------------------------------------------

def test_sweep_expired_uploads_removes_only_old_files(engine_env):
    uploads = engine_env["uploads"]
    uploads.mkdir(parents=True, exist_ok=True)
    old = uploads / "old.wav"
    fresh = uploads / "fresh.wav"
    old.write_bytes(b"x")
    fresh.write_bytes(b"y")
    stale_mtime = time.time() - api._UPLOAD_RETENTION_SEC - 60
    os.utime(old, (stale_mtime, stale_mtime))

    removed = api._sweep_expired_uploads()

    assert removed == 1
    assert not old.exists()
    assert fresh.exists()
