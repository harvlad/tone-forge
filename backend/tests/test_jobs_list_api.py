"""GET /api/jobs — caller-scoped job list for the desktop analysis queue.

Scoping mirrors /api/history?scope=mine: device-id header match OR
owner-id match when signed in. Anonymous callers with no device id get
an empty list (200, never 401) and never see other callers' jobs.
"""
from __future__ import annotations

import io
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

import tone_forge_api as api
from tone_forge.analysis_jobs import JobRegistry

client = TestClient(api.app)


@pytest.fixture()
def jobs_env(tmp_path, monkeypatch):
    """Fresh registry + isolated uploads dir."""
    registry = JobRegistry(tmp_path / "jobs")
    monkeypatch.setattr(api, "_JOBS", registry)
    monkeypatch.setattr(api, "_UPLOADS_DIR", tmp_path / "uploads")
    return registry


def _upload(device_id: str | None, filename="song.wav"):
    headers = {"X-Device-Id": device_id} if device_id else {}
    return client.post(
        "/api/analyze-upload",
        data={"attested": "true", "extract_midi": "true"},
        files={"file": (filename, io.BytesIO(b"RIFFfake"), "audio/wav")},
        headers=headers,
    )


# ---------------------------------------------------------------------------
# registry accessors
# ---------------------------------------------------------------------------

def test_registry_all_and_positions(tmp_path):
    reg = JobRegistry(tmp_path / "jobs")
    server = reg.create(filename="server.wav")
    first = reg.create_engine_job(filename="a.wav")
    second = reg.create_engine_job(filename="b.wav")
    first.created_at = 100.0
    second.created_at = 200.0

    assert {j.id for j in reg.all()} == {server.id, first.id, second.id}

    positions = reg.queued_engine_positions()
    # server-kind job never appears; FIFO by created_at
    assert positions == {first.id: 1, second.id: 2}

    # running engine job drops out of the queue count
    first.status = "running"
    assert reg.queued_engine_positions() == {second.id: 1}


# ---------------------------------------------------------------------------
# endpoint scoping
# ---------------------------------------------------------------------------

def test_jobs_empty_without_identity(jobs_env):
    resp = client.get("/api/jobs")
    assert resp.status_code == 200
    assert resp.json() == {"jobs": []}


def test_jobs_scoped_by_device_id(jobs_env):
    _upload("dev-A", "a1.wav")
    _upload("dev-A", "a2.wav")
    _upload("dev-B", "b1.wav")

    resp = client.get("/api/jobs", headers={"X-Device-Id": "dev-A"})
    assert resp.status_code == 200
    rows = resp.json()["jobs"]
    assert len(rows) == 2
    assert {r["filename"] for r in rows} == {"a1.wav", "a2.wav"}


def test_jobs_newest_first_and_limit(jobs_env):
    registry = jobs_env
    ids = []
    for i, ts in enumerate((100.0, 200.0, 300.0)):
        job = registry.create_engine_job(filename=f"f{i}.wav", device_id="dev-A")
        job.created_at = ts
        ids.append(job.id)

    resp = client.get("/api/jobs", headers={"X-Device-Id": "dev-A"})
    rows = resp.json()["jobs"]
    assert [r["job_id"] for r in rows] == [ids[2], ids[1], ids[0]]

    resp = client.get("/api/jobs?limit=2", headers={"X-Device-Id": "dev-A"})
    assert len(resp.json()["jobs"]) == 2


def test_jobs_queue_position_on_queued_rows_only(jobs_env):
    registry = jobs_env
    first = registry.create_engine_job(filename="one.wav", device_id="dev-A")
    second = registry.create_engine_job(filename="two.wav", device_id="dev-A")
    first.created_at = 100.0
    second.created_at = 200.0
    done = registry.create_engine_job(filename="done.wav", device_id="dev-A")
    done.status = "done"
    done.history_id = "hist-1"

    resp = client.get("/api/jobs", headers={"X-Device-Id": "dev-A"})
    rows = {r["job_id"]: r for r in resp.json()["jobs"]}
    assert rows[first.id]["queue_position"] == 1
    assert rows[second.id]["queue_position"] == 2
    assert "queue_position" not in rows[done.id]


def test_jobs_owner_scope(jobs_env, monkeypatch):
    registry = jobs_env
    registry.create_engine_job(filename="mine.wav", owner_id="u1")
    registry.create_engine_job(filename="theirs.wav", owner_id="u2")

    class _User:
        id = "u1"

    async def fake_current_user(request):
        return _User()

    import tone_forge.auth.deps as deps
    monkeypatch.setattr(deps, "current_user", fake_current_user)

    resp = client.get("/api/jobs")  # no device header — pure owner scope
    rows = resp.json()["jobs"]
    assert len(rows) == 1
    assert rows[0]["filename"] == "mine.wav"


def test_jobs_shape_no_private_fields(jobs_env):
    _upload("dev-A")
    resp = client.get("/api/jobs", headers={"X-Device-Id": "dev-A"})
    row = resp.json()["jobs"][0]
    for private in ("payload", "device_id", "owner_id", "meta", "kind",
                    "device_token"):
        assert private not in row
    for public in ("job_id", "status", "percent", "message", "filename",
                   "created_at", "updated_at"):
        assert public in row
