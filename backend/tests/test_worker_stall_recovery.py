"""Stalled-worker recovery: the 0%-forever failure mode.

A queued engine job depends on a worker claiming it. When the worker
never arrives — a RunPod pod that boots into nothing, a Mac worker
that stays off — the job used to sit ``queued`` indefinitely and the
client showed "Waking up an analysis worker" at 0% with no timeout.

Two defences are covered here:
  * ``_fail_stranded_engine_jobs`` turns a job nothing ever claimed into
    a real error, but ONLY when no worker checked in for the whole wait.
  * ``reap_stalled_workers`` replaces a pod RunPod still calls RUNNING
    that no worker ever booted inside — while sparing one still booting.
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tone_forge_api as api
from local_engine import runpod_autoscaler as autoscale
from tone_forge.analysis_jobs import JobRegistry


@pytest.fixture()
def jobs(tmp_path, monkeypatch):
    registry = JobRegistry(tmp_path / "jobs")
    monkeypatch.setattr(api, "_JOBS", registry)
    monkeypatch.setitem(api._ENGINE_PRESENCE, "last_seen", 0.0)
    monkeypatch.delenv("TONEFORGE_STRANDED_JOB_MIN", raising=False)
    return registry


def _queued(registry, age_sec: float):
    job = registry.create_engine_job(filename="song.wav", payload={})
    job.created_at = time.time() - age_sec
    return job


# --- stranded queued jobs ------------------------------------------------

def test_job_queued_past_timeout_with_no_worker_contact_errors(jobs):
    job = _queued(jobs, 16 * 60)
    assert asyncio.run(api._fail_stranded_engine_jobs()) == 1
    assert jobs.get(job.id).status == "error"
    assert "No analysis worker" in (jobs.get(job.id).error or "")


def test_job_inside_the_timeout_is_left_alone(jobs):
    job = _queued(jobs, 5 * 60)
    assert asyncio.run(api._fail_stranded_engine_jobs()) == 0
    assert jobs.get(job.id).status == "queued"


def test_real_queue_behind_a_live_worker_never_fails(jobs, monkeypatch):
    """A worker that checked in after the job was created means the job
    is behind other work — however long that takes, it is not stranded."""
    job = _queued(jobs, 60 * 60)
    monkeypatch.setitem(api._ENGINE_PRESENCE, "last_seen", time.time())
    assert asyncio.run(api._fail_stranded_engine_jobs()) == 0
    assert jobs.get(job.id).status == "queued"


def test_stranded_timeout_can_be_disabled(jobs, monkeypatch):
    monkeypatch.setenv("TONEFORGE_STRANDED_JOB_MIN", "0")
    job = _queued(jobs, 24 * 60 * 60)
    assert asyncio.run(api._fail_stranded_engine_jobs()) == 0
    assert jobs.get(job.id).status == "queued"


def test_running_jobs_are_not_swept(jobs):
    job = _queued(jobs, 60 * 60)
    job.status = "running"
    assert asyncio.run(api._fail_stranded_engine_jobs()) == 0


# --- /api/jobs wait visibility -------------------------------------------

def test_jobs_endpoint_reports_wait_and_worker_presence(jobs):
    from fastapi.testclient import TestClient

    _queued(jobs, 120).device_id = "dev-1"
    client = TestClient(api.app)
    rows = client.get("/api/jobs", headers={"X-Device-Id": "dev-1"}).json()["jobs"]
    assert len(rows) == 1
    assert rows[0]["queue_position"] == 1
    assert rows[0]["queued_for_s"] >= 120
    assert rows[0]["worker_online"] is False


def test_debug_engine_reports_the_stall_facts(jobs):
    from fastapi.testclient import TestClient

    _queued(jobs, 600)
    body = TestClient(api.app).get("/api/debug/engine").json()
    assert body["engine"]["online"] is False
    assert body["engine"]["silent_for_s"] > 0
    assert body["jobs"]["queued"] == 1
    assert body["jobs"]["oldest_queued_age_s"] >= 600
    assert body["autoscale"]["enabled"] is False


# --- zombie pod reap ------------------------------------------------------

class _FakeRequests:
    def __init__(self, pods):
        self._pods = pods
        self.deleted: list[str] = []

    def get(self, url, **kw):
        pods = self._pods

        class R:
            status_code = 200

            @staticmethod
            def json():
                return pods

        return R()

    def delete(self, url, **kw):
        self.deleted.append(url.rsplit("/", 1)[-1])

        class R:
            status_code = 200

        return R()


@pytest.fixture()
def runpod(monkeypatch):
    monkeypatch.setenv("RUNPOD_AUTOSCALE", "1")
    monkeypatch.setenv("RUNPOD_API_KEY", "rpa_test")
    monkeypatch.setattr(autoscale, "_pod_first_seen", {})
    monkeypatch.setattr(autoscale, "_last_create_ts", time.time())
    return monkeypatch


def _pod(pid, age_sec, status="RUNNING"):
    from datetime import datetime, timedelta, timezone

    created = datetime.now(timezone.utc) - timedelta(seconds=age_sec)
    return {
        "id": pid,
        "name": autoscale._POD_NAME,
        "desiredStatus": status,
        "createdAt": created.isoformat().replace("+00:00", "Z"),
    }


def test_reap_replaces_a_pod_that_never_claimed(runpod, monkeypatch):
    fake = _FakeRequests([_pod("zombie", 900)])
    monkeypatch.setattr(autoscale, "requests", fake)
    assert autoscale.reap_stalled_workers(300) == 1
    assert fake.deleted == ["zombie"]
    # A reap proves the last create produced nothing — the cooldown must
    # not then block the replacement for another five minutes.
    assert autoscale._last_create_ts == 0.0


def test_reap_spares_a_pod_that_is_still_booting(runpod, monkeypatch):
    fake = _FakeRequests([_pod("booting", 90)])
    monkeypatch.setattr(autoscale, "requests", fake)
    assert autoscale.reap_stalled_workers(300) == 0
    assert fake.deleted == []


def test_pod_age_falls_back_to_first_seen_when_timestamp_is_unusable(
    runpod, monkeypatch
):
    """An un-ageable pod is exactly where a zombie would hide, so a
    missing/garbage createdAt must still age from first sight."""
    pod = {"id": "no-stamp", "name": autoscale._POD_NAME,
           "desiredStatus": "RUNNING", "createdAt": "not-a-date"}
    fake = _FakeRequests([pod])
    monkeypatch.setattr(autoscale, "requests", fake)
    autoscale.list_worker_pods()  # first sight starts the clock
    autoscale._pod_first_seen["no-stamp"] = time.time() - 900
    assert autoscale.reap_stalled_workers(300) == 1


def test_reap_is_inert_when_autoscale_is_off(monkeypatch):
    monkeypatch.delenv("RUNPOD_AUTOSCALE", raising=False)
    assert autoscale.reap_stalled_workers(300) == 0
