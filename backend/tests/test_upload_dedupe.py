"""Content-hash upload dedupe — /api/analyze-upload + _find_duplicate_analysis.

The same audio uploaded twice used to manufacture two independent
analyses: duplicate Band Room rows (the triple "BANKS - Change") and,
when no worker was around, two jobs that strand independently. The upload
path now hashes the bytes, persists that fingerprint onto the completed
history entry, and reuses an existing analysis (in-flight job OR completed
row) before enqueuing.

Invariants locked here:
  * same bytes, same owner → REUSE (in-flight job id, then completed
    history id once it finishes) — never a duplicate job;
  * different bytes → two jobs;
  * dedupe is PER-OWNER (another device's identical file is separate);
  * an anonymous caller (no owner/device) can't be scoped and is never
    deduped;
  * the content hash is actually persisted onto history at completion.
"""
from __future__ import annotations

import io
import sys
import wave
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tone_forge_api as api  # noqa: E402
from tone_forge import r2_storage  # noqa: E402
from tone_forge.analysis_jobs import JobRegistry  # noqa: E402

ENGINE = {"X-Engine-Token": "tkn"}
DEV = {"X-Device-Id": "dev-1"}


def _wav(freq: int = 220, sr: int = 8000, dur_s: float = 0.2) -> bytes:
    """Deterministic tiny mono WAV — identical args → identical bytes
    (so the sha256 matches on re-upload)."""
    buf = io.BytesIO()
    t = np.arange(int(sr * dur_s)) / sr
    samples = (np.sin(2 * np.pi * freq * t) * 0.4 * 32767).astype(np.int16)
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(samples.tobytes())
    return buf.getvalue()


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Isolate history + jobs + uploads under tmp so nothing touches real
    # data. Engine token gates claim/complete; R2 stays off.
    monkeypatch.setattr(api, "_HISTORY_FILE", tmp_path / "history.json")
    monkeypatch.setattr(api, "_JOBS", JobRegistry(tmp_path / "jobs"))
    monkeypatch.setattr(api, "_UPLOADS_DIR", tmp_path / "uploads")
    monkeypatch.setattr(r2_storage, "is_configured", lambda: False)
    monkeypatch.setenv("TONEFORGE_ENGINE_TOKEN", "tkn")
    with TestClient(api.app) as c:
        yield c


def _upload(client, audio: bytes, headers=DEV, name="song.wav"):
    return client.post(
        "/api/analyze-upload",
        files={"file": (name, audio, "audio/wav")},
        data={"attested": "true"},
        headers=headers,
    )


def _engine_job_count() -> int:
    return len([j for j in api._JOBS.all() if j.kind == "engine"])


# ---------------------------------------------------------------------------
# endpoint dedupe
# ---------------------------------------------------------------------------

def test_same_file_twice_dedupes_to_inflight_job(client):
    audio = _wav()
    r1 = _upload(client, audio)
    assert r1.status_code == 200, r1.text
    j1 = r1.json()["job_id"]
    assert j1

    r2 = _upload(client, audio)
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["duplicate"] is True
    assert body["job_id"] == j1  # follows the job already running
    assert _engine_job_count() == 1, "the second upload must not enqueue a job"


def test_different_files_are_separate(client):
    j1 = _upload(client, _wav(220)).json()["job_id"]
    body = _upload(client, _wav(440)).json()
    assert not body.get("duplicate")
    assert body["job_id"] and body["job_id"] != j1
    assert _engine_job_count() == 2


def test_dedupe_is_per_device(client):
    audio = _wav()
    j_a = _upload(client, audio, headers={"X-Device-Id": "dev-a"}).json()["job_id"]
    body = _upload(client, audio, headers={"X-Device-Id": "dev-b"}).json()
    assert not body.get("duplicate"), "another device's identical file is separate"
    assert body["job_id"] and body["job_id"] != j_a
    assert _engine_job_count() == 2


def test_anonymous_uploads_are_not_deduped(client):
    # No device id + no session → cannot be scoped to an owner, so the
    # dedupe deliberately falls through (never dedupes across the shared
    # anonymous bucket).
    audio = _wav()
    j1 = _upload(client, audio, headers={}).json()["job_id"]
    body = _upload(client, audio, headers={}).json()
    assert not body.get("duplicate")
    assert body["job_id"] and body["job_id"] != j1
    assert _engine_job_count() == 2


def test_completed_analysis_dedupes_to_history(client, monkeypatch):
    monkeypatch.setattr(api, "_maybe_upload_stems_to_r2",
                        lambda eid, entry: False)
    audio = _wav()
    j1 = _upload(client, audio).json()["job_id"]
    # claim (→ running) then complete (→ persists content_hash to history)
    client.post("/api/engine/claim",
                json={"worker_id": "w", "device": "cpu", "wait_sec": 0},
                headers=ENGINE)
    r = client.post(f"/api/engine/job/{j1}/complete",
                    json={"duration_sec": 1.0, "detected_type": "guitar"},
                    headers=ENGINE)
    assert r.status_code == 200, r.text
    hid = r.json()["history_id"]

    entry = api._get_history_item(hid)
    assert entry.get("content_hash"), "completion must persist the fingerprint"

    body = _upload(client, audio).json()
    assert body["duplicate"] is True
    assert body["history_id"] == hid  # reuse the finished row
    assert body["job_id"] is None
    # only the original job exists (now done) — no fresh enqueue
    assert _engine_job_count() == 1


# ---------------------------------------------------------------------------
# _find_duplicate_analysis unit behavior
# ---------------------------------------------------------------------------

def test_find_duplicate_blank_hash(client):
    assert api._find_duplicate_analysis("", "d1", "u1") == (None, None)


def test_find_duplicate_inflight_job(client):
    job = api._JOBS.create_engine_job(
        filename="x.wav", device_id="d1", payload={"content_hash": "Z"})
    assert api._find_duplicate_analysis("Z", "d1", None) == (None, job.id)


def test_find_duplicate_prefers_completed_history_over_job(client):
    entry = api._add_to_history(
        {"name": "done", "content_hash": "H"}, owner_id="u1")
    api._JOBS.create_engine_job(
        filename="x.wav", owner_id="u1", payload={"content_hash": "H"})
    hid, jid = api._find_duplicate_analysis("H", None, "u1")
    assert hid == entry["id"]
    assert jid is None


def test_find_duplicate_scopes_to_owner(client):
    api._add_to_history({"name": "u1-song", "content_hash": "S"}, owner_id="u1")
    # a different owner asking for the same hash gets nothing
    assert api._find_duplicate_analysis("S", None, "u2") == (None, None)


def test_find_duplicate_legacy_entry_without_hash(client):
    api._add_to_history({"name": "legacy"}, owner_id="u1")  # no content_hash
    assert api._find_duplicate_analysis("anything", None, "u1") == (None, None)
