"""Attribution metadata pipeline (D-024).

Covers:
  * tone_forge.media_tags — best-effort tag extraction + sanitizing
  * _resolve_attribution_meta precedence: form field > file tags >
    filename (title only)
  * _stamp_attribution — history-entry merge rules
  * /api/analyze-upload — tags extracted from the stored original,
    form fields win, job.meta rides the engine job
  * /api/engine/job/{id}/complete — the second history writer stamps
    job.meta into the entry
  * bundle meta + history list projection emit the new fields with
    empty-string / absent fallbacks for old entries
"""
from __future__ import annotations

import asyncio
import io
import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

import tone_forge_api as api
from tone_forge.analysis_jobs import JobRegistry
from tone_forge.media_tags import read_tags, sanitize_tag

client = TestClient(api.app)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def _tagged_flac(path: Path, title="Blue in Green", artist="Testy McTest") -> Path:
    """Small FLAC with real tags — soundfile writes it, mutagen tags it."""
    soundfile.write(str(path), np.zeros(4800, dtype="float32"), 48000)
    from mutagen.flac import FLAC

    f = FLAC(str(path))
    if title is not None:
        f["title"] = title
    if artist is not None:
        f["artist"] = artist
    f.save()
    return path


@pytest.fixture()
def engine_env(tmp_path, monkeypatch):
    """Fresh registry + isolated uploads dir + captured history calls."""
    registry = JobRegistry(tmp_path / "jobs")
    monkeypatch.setattr(api, "_JOBS", registry)
    monkeypatch.setattr(api, "_UPLOADS_DIR", tmp_path / "uploads")
    monkeypatch.setattr(api, "_ENGINE_STEMS_ROOT", tmp_path / "stems")
    monkeypatch.setitem(api._ENGINE_PRESENCE, "last_seen", 0.0)
    monkeypatch.delenv("TONEFORGE_ENGINE_TOKEN", raising=False)
    history_calls: list[dict] = []

    def fake_add_to_history(entry, full_result=None, device_id=None, owner_id=None):
        history_calls.append({"entry": entry, "full_result": full_result})
        return {"id": "hist-test-1"}

    monkeypatch.setattr(api, "_add_to_history", fake_add_to_history)
    return {"registry": registry, "history_calls": history_calls}


def _upload(filename="song.flac", content=b"", data=None):
    payload = {"attested": "true", "extract_midi": "true"}
    payload.update(data or {})
    return client.post(
        "/api/analyze-upload",
        data=payload,
        files={"file": (filename, io.BytesIO(content), "audio/flac")},
    )


# ---------------------------------------------------------------------------
# media_tags
# ---------------------------------------------------------------------------

def test_read_tags_returns_title_and_artist(tmp_path):
    path = _tagged_flac(tmp_path / "t.flac")
    assert read_tags(path) == {"title": "Blue in Green", "artist": "Testy McTest"}


def test_read_tags_untagged_file_returns_empty(tmp_path):
    path = tmp_path / "plain.wav"
    soundfile.write(str(path), np.zeros(480, dtype="float32"), 48000)
    assert read_tags(path) == {}


def test_read_tags_corrupt_and_missing_files_return_empty(tmp_path):
    corrupt = tmp_path / "corrupt.mp3"
    corrupt.write_bytes(b"\x00\x01not audio at all")
    assert read_tags(corrupt) == {}
    assert read_tags(tmp_path / "nope.mp3") == {}


def test_sanitize_tag_strips_controls_and_caps_length():
    assert sanitize_tag("  hello\x00\x1fworld  ") == "helloworld"
    assert len(sanitize_tag("x" * 500)) == 200
    assert sanitize_tag(None) == ""
    assert sanitize_tag(123) == ""


# ---------------------------------------------------------------------------
# _resolve_attribution_meta precedence
# ---------------------------------------------------------------------------

def test_resolve_form_beats_tags_beats_filename(tmp_path):
    path = _tagged_flac(tmp_path / "t.flac", title="Tag Title", artist="Tag Artist")
    meta = api._resolve_attribution_meta(
        {"title": "Form Title"}, audio_path=path, filename="fallback.flac"
    )
    assert meta["title"] == "Form Title"   # form wins
    assert meta["artist"] == "Tag Artist"  # tag fills the gap
    assert meta["license"] == ""


def test_resolve_falls_back_to_filename_stem(tmp_path):
    path = tmp_path / "untitled.wav"
    soundfile.write(str(path), np.zeros(480, dtype="float32"), 48000)
    meta = api._resolve_attribution_meta(
        {}, audio_path=path, filename="My Demo Track.wav"
    )
    assert meta["title"] == "My Demo Track"
    assert meta["artist"] == ""


def test_resolve_sanitizes_form_fields():
    meta = api._resolve_attribution_meta({"artist": "  Bad\x00Actor  " + "y" * 500})
    assert meta["artist"].startswith("BadActor")
    assert len(meta["artist"]) <= 200


# ---------------------------------------------------------------------------
# _stamp_attribution
# ---------------------------------------------------------------------------

def test_stamp_title_upgrades_name_and_skips_empties():
    entry = {"name": "track07.mp3"}
    api._stamp_attribution(entry, {
        "title": "Real Title", "artist": "Someone",
        "license": "", "license_url": "", "source_url": "", "attribution": "",
    })
    assert entry["name"] == "Real Title"
    assert entry["artist"] == "Someone"
    assert "license" not in entry  # empty fields never stamp


def test_stamp_none_meta_is_noop():
    entry = {"name": "x"}
    api._stamp_attribution(entry, None)
    assert entry == {"name": "x"}


# ---------------------------------------------------------------------------
# /api/analyze-upload → job.meta
# ---------------------------------------------------------------------------

def test_upload_extracts_tags_from_stored_original(engine_env, tmp_path):
    content = _tagged_flac(tmp_path / "src.flac").read_bytes()
    job_id = _upload(content=content).json()["job_id"]
    job = engine_env["registry"].get(job_id)
    assert job.meta["title"] == "Blue in Green"
    assert job.meta["artist"] == "Testy McTest"
    assert job.meta["license"] == ""


def test_upload_form_fields_override_tags(engine_env, tmp_path):
    content = _tagged_flac(tmp_path / "src.flac").read_bytes()
    job_id = _upload(content=content, data={
        "title": "Curated Title",
        "artist": "Curated Artist",
        "license": "CC-BY",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "attribution": "“Curated Title” by Curated Artist (CC BY 4.0)",
    }).json()["job_id"]
    job = engine_env["registry"].get(job_id)
    assert job.meta["title"] == "Curated Title"
    assert job.meta["artist"] == "Curated Artist"
    assert job.meta["license"] == "CC-BY"
    # meta never leaks through the public snapshot
    assert "meta" not in job.public_dict()


def test_upload_untagged_falls_back_to_filename(engine_env):
    job_id = _upload(filename="Cool Jam.flac", content=b"notaflac").json()["job_id"]
    job = engine_env["registry"].get(job_id)
    assert job.meta["title"] == "Cool Jam"
    assert job.meta["artist"] == ""


# ---------------------------------------------------------------------------
# engine complete — second history writer stamps job.meta
# ---------------------------------------------------------------------------

def test_engine_complete_stamps_attribution_into_history(engine_env, tmp_path):
    content = _tagged_flac(tmp_path / "src.flac").read_bytes()
    job_id = _upload(content=content, data={"license": "CC0"}).json()["job_id"]
    claim = client.post(
        "/api/engine/claim",
        json={"worker_id": "w1", "device": "mps", "wait_sec": 0.1},
    )
    assert claim.status_code == 200
    done = client.post(
        f"/api/engine/job/{job_id}/complete",
        json={"detected_type": "guitar", "duration_sec": 3.0},
    )
    assert done.status_code == 200
    entry = engine_env["history_calls"][0]["entry"]
    assert entry["name"] == "Blue in Green"
    assert entry["artist"] == "Testy McTest"
    assert entry["license"] == "CC0"


# ---------------------------------------------------------------------------
# bundle meta + history list projection
# ---------------------------------------------------------------------------

def _bundle_meta_for(entry, monkeypatch):
    monkeypatch.setattr(api, "_get_history_item", lambda _id: entry)
    monkeypatch.setattr(api, "_maybe_upload_stems_to_r2", lambda *_a, **_k: False)
    monkeypatch.setattr(api, "_refresh_r2_stem_urls", lambda *_a, **_k: None)
    resp = client.get("/api/song/test-entry/bundle")
    assert resp.status_code == 200
    return resp.json()["meta"]


def test_bundle_meta_emits_attribution_fields(monkeypatch):
    meta = _bundle_meta_for({
        "id": "test-entry",
        "name": "Curated Title",
        "artist": "Curated Artist",
        "license": "CC-BY",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "attribution": "“Curated Title” by Curated Artist (CC BY 4.0)",
        "result": {"duration_sec": 3.0},
    }, monkeypatch)
    assert meta["artist"] == "Curated Artist"
    assert meta["license"] == "CC-BY"
    assert meta["licenseUrl"] == "https://creativecommons.org/licenses/by/4.0/"
    assert meta["attribution"] == "“Curated Title” by Curated Artist (CC BY 4.0)"


def test_bundle_meta_old_entries_fall_back_to_empty(monkeypatch):
    meta = _bundle_meta_for({
        "id": "test-entry",
        "name": "Old Song",
        "result": {"duration_sec": 3.0},
    }, monkeypatch)
    assert meta["license"] == ""
    assert meta["licenseUrl"] == ""
    assert meta["attribution"] == ""


def test_history_list_row_includes_attribution_when_present():
    row = api._history_list_row({
        "id": "x", "name": "n", "artist": "A", "license": "CC0",
    })
    assert row["artist"] == "A"
    assert row["license"] == "CC0"
    # old entries project the same rows as before
    old = api._history_list_row({"id": "y", "name": "old"})
    assert "artist" not in old
    assert "license" not in old
