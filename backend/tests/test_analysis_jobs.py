"""JobRegistry lifecycle: create/update, crash recovery, TTL sweep."""
import asyncio
import json
import time

from tone_forge.analysis_jobs import JobRegistry, JobState


def _registry(tmp_path, ttl=3600.0):
    return JobRegistry(tmp_path / "jobs", ttl_sec=ttl)


def test_create_persists_to_disk(tmp_path):
    reg = _registry(tmp_path)
    job = reg.create(filename="song.wav")
    on_disk = json.loads((tmp_path / "jobs" / f"{job.id}.json").read_text())
    assert on_disk["id"] == job.id
    assert on_disk["status"] == "queued"
    assert on_disk["filename"] == "song.wav"


def test_update_bumps_version_and_persists(tmp_path):
    reg = _registry(tmp_path)
    job = reg.create()
    asyncio.run(reg.update(job.id, status="running", percent=50.0))
    assert job.version == 1
    on_disk = json.loads((tmp_path / "jobs" / f"{job.id}.json").read_text())
    assert on_disk["status"] == "running"
    assert on_disk["percent"] == 50.0


def test_recover_marks_interrupted_jobs_errored(tmp_path):
    reg = _registry(tmp_path)
    job = reg.create()
    asyncio.run(reg.update(job.id, status="running"))

    fresh = _registry(tmp_path)
    fresh.recover()
    recovered = fresh.get(job.id)
    assert recovered is not None
    assert recovered.status == "error"
    assert "interrupted" in (recovered.error or "")


def test_recover_drops_expired_files(tmp_path):
    reg = _registry(tmp_path, ttl=100.0)
    job = reg.create()
    # Age the job past the TTL on disk.
    path = tmp_path / "jobs" / f"{job.id}.json"
    data = json.loads(path.read_text())
    data["created_at"] = time.time() - 200
    path.write_text(json.dumps(data))

    fresh = _registry(tmp_path, ttl=100.0)
    fresh.recover()
    assert fresh.get(job.id) is None
    assert not path.exists()


def test_sweep_removes_only_expired_terminal_jobs(tmp_path):
    reg = _registry(tmp_path, ttl=100.0)
    old_done = reg.create()
    old_running = reg.create()
    fresh_done = reg.create()
    asyncio.run(reg.update(old_done.id, status="done"))
    asyncio.run(reg.update(old_running.id, status="running"))
    asyncio.run(reg.update(fresh_done.id, status="done"))
    # Age two of them past the TTL in memory.
    old_done.created_at = time.time() - 200
    old_running.created_at = time.time() - 200

    removed = reg.sweep()

    assert removed == 1
    assert reg.get(old_done.id) is None
    assert not (tmp_path / "jobs" / f"{old_done.id}.json").exists()
    # In-flight job survives even past TTL; fresh terminal job survives.
    assert reg.get(old_running.id) is not None
    assert reg.get(fresh_done.id) is not None


def test_sweep_noop_when_nothing_expired(tmp_path):
    reg = _registry(tmp_path)
    job = reg.create()
    asyncio.run(reg.update(job.id, status="done"))
    assert reg.sweep() == 0
    assert reg.get(job.id) is not None


def test_public_dict_hides_device_token(tmp_path):
    job = JobState(id="abc", device_token="secret")
    public = job.public_dict()
    assert "device_token" not in public
    assert public["job_id"] == "abc"


def test_attestation_recorded_and_persisted(tmp_path):
    reg = _registry(tmp_path)
    job = reg.create(filename="song.wav", attested=True)
    assert job.attested is True
    assert job.public_dict()["attested"] is True
    on_disk = json.loads((tmp_path / "jobs" / f"{job.id}.json").read_text())
    assert on_disk["attested"] is True

    # Survives crash recovery.
    fresh = _registry(tmp_path)
    fresh.recover()
    recovered = fresh.get(job.id)
    assert recovered is not None
    assert recovered.attested is True


def test_attestation_defaults_false(tmp_path):
    reg = _registry(tmp_path)
    job = reg.create(filename="song.wav")
    assert job.attested is False
    assert job.public_dict()["attested"] is False
