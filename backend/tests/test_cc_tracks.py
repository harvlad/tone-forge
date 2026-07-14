"""Curated CC0/CC-BY demo-track catalog + one-tap import (D-024).

GET /api/cc-tracks serves the catalog minus the server-side ``file``
field; POST /api/cc-tracks/{id}/import queues a pre-attested engine job
carrying the catalog's license metadata (attestation_source="curated").
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

import tone_forge_api as api
from tone_forge.analysis_jobs import JobRegistry

client = TestClient(api.app)

_TRACK = {
    "id": "night-drive",
    "title": "Night Drive",
    "artist": "Some Artist",
    "license": "CC-BY",
    "licenseUrl": "https://creativecommons.org/licenses/by/4.0/",
    "sourceUrl": "https://example.org/night-drive",
    "attribution": "“Night Drive” by Some Artist (CC BY), https://example.org/night-drive",
    "file": "night-drive.wav",
    "durationSec": 12.3,
    "description": "moody synthwave",
}


@pytest.fixture()
def cc_env(tmp_path, monkeypatch):
    """Isolated cc-tracks dir with one real track + fresh registry."""
    cc_dir = tmp_path / "cc-tracks"
    (cc_dir / "audio").mkdir(parents=True)
    (cc_dir / "audio" / "night-drive.wav").write_bytes(b"RIFFccaudio")
    (cc_dir / "catalog.json").write_text(
        json.dumps({"catalogVersion": 1, "tracks": [_TRACK]})
    )
    registry = JobRegistry(tmp_path / "jobs")
    monkeypatch.setattr(api, "_CC_TRACKS_DIR", cc_dir)
    monkeypatch.setattr(api, "_JOBS", registry)
    monkeypatch.setattr(api, "_UPLOADS_DIR", tmp_path / "uploads")
    monkeypatch.setitem(api._ENGINE_PRESENCE, "last_seen", 0.0)
    monkeypatch.delenv("TONEFORGE_ENGINE_TOKEN", raising=False)
    return {"cc_dir": cc_dir, "registry": registry, "uploads": tmp_path / "uploads"}


# ---------------------------------------------------------------------------
# GET /api/cc-tracks
# ---------------------------------------------------------------------------

def test_catalog_omits_file_field(cc_env):
    resp = client.get("/api/cc-tracks")
    assert resp.status_code == 200
    tracks = resp.json()["tracks"]
    assert len(tracks) == 1
    assert tracks[0]["id"] == "night-drive"
    assert tracks[0]["license"] == "CC-BY"
    assert tracks[0]["attribution"] == _TRACK["attribution"]
    assert "file" not in tracks[0]


def test_catalog_missing_file_returns_empty(cc_env, monkeypatch):
    monkeypatch.setattr(api, "_CC_TRACKS_DIR", cc_env["cc_dir"] / "nope")
    assert client.get("/api/cc-tracks").json() == {"tracks": []}


def test_catalog_skips_malformed_entries(cc_env):
    (cc_env["cc_dir"] / "catalog.json").write_text(json.dumps({
        "tracks": [_TRACK, "junk", {"no": "id"}],
    }))
    tracks = client.get("/api/cc-tracks").json()["tracks"]
    assert [t["id"] for t in tracks] == ["night-drive"]


# ---------------------------------------------------------------------------
# POST /api/cc-tracks/{id}/import
# ---------------------------------------------------------------------------

def test_import_unknown_id_404(cc_env):
    assert client.post("/api/cc-tracks/nope/import").status_code == 404


def test_import_missing_audio_410(cc_env):
    (cc_env["cc_dir"] / "audio" / "night-drive.wav").unlink()
    assert client.post("/api/cc-tracks/night-drive/import").status_code == 410


def test_import_path_traversal_410(cc_env, tmp_path):
    evil = dict(_TRACK, id="evil", file="../../catalog.json")
    (cc_env["cc_dir"] / "catalog.json").write_text(
        json.dumps({"tracks": [_TRACK, evil]})
    )
    assert client.post("/api/cc-tracks/evil/import").status_code == 410


def test_import_creates_attested_curated_engine_job(cc_env):
    resp = client.post(
        "/api/cc-tracks/night-drive/import",
        headers={"X-Device-Id": "device-abc"},
    )
    assert resp.status_code == 200
    body = resp.json()
    job = cc_env["registry"].get(body["job_id"])
    assert job is not None
    assert job.kind == "engine"
    assert job.attested is True
    assert job.device_id == "device-abc"
    assert job.payload["attestation_source"] == "curated"
    # license metadata rides the job for the history writer
    assert job.meta["title"] == "Night Drive"
    assert job.meta["license"] == "CC-BY"
    assert job.meta["license_url"] == _TRACK["licenseUrl"]
    assert job.meta["source_url"] == _TRACK["sourceUrl"]
    assert job.meta["attribution"] == _TRACK["attribution"]
    # the source was copied under uploads/ named by job id
    stored = Path(job.payload["upload_path"])
    assert stored.parent == cc_env["uploads"]
    assert stored.read_bytes() == b"RIFFccaudio"


def test_imported_job_flows_through_engine_claim_and_complete(cc_env, monkeypatch):
    history_calls: list[dict] = []

    def fake_add_to_history(entry, full_result=None, device_id=None, owner_id=None):
        history_calls.append({"entry": entry})
        return {"id": "hist-cc-1"}

    monkeypatch.setattr(api, "_add_to_history", fake_add_to_history)
    job_id = client.post("/api/cc-tracks/night-drive/import").json()["job_id"]

    claim = client.post(
        "/api/engine/claim",
        json={"worker_id": "w1", "device": "mps", "wait_sec": 0.1},
    )
    assert claim.status_code == 200
    assert claim.json()["job_id"] == job_id
    done = client.post(
        f"/api/engine/job/{job_id}/complete",
        json={"detected_type": "guitar", "duration_sec": 12.3},
    )
    assert done.status_code == 200
    entry = history_calls[0]["entry"]
    assert entry["name"] == "Night Drive"
    assert entry["artist"] == "Some Artist"
    assert entry["license"] == "CC-BY"
    assert entry["attribution"] == _TRACK["attribution"]
    assert entry["attested"] is True
    assert entry["attestation_source"] == "curated"


# ---------------------------------------------------------------------------
# fetch script gate
# ---------------------------------------------------------------------------

def test_fetch_script_rejects_incomplete_cc_by(tmp_path, monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "fetch_cc_tracks",
        Path(__file__).resolve().parent.parent / "scripts" / "fetch_cc_tracks.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "CC_TRACKS_DIR", tmp_path)
    monkeypatch.setattr(mod, "CATALOG_PATH", tmp_path / "catalog.json")
    monkeypatch.setattr(mod, "AUDIO_DIR", tmp_path / "audio")

    src = tmp_path / "song.wav"
    src.write_bytes(b"RIFF")
    args = type("A", (), {
        "file": str(src), "id": "x-track", "title": "X", "artist": "",
        "license": "CC-BY", "license_url": "", "source_url": "",
        "attribution": "", "description": "",
    })()
    with pytest.raises(SystemExit) as exc:
        mod.cmd_add(args)
    msg = str(exc.value)
    assert "--artist" in msg and "--source-url" in msg and "--license-url" in msg
