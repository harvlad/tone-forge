"""Beat Capture (D-024) model distribution endpoints.

A model is a compiled ``.mlmodelc`` directory published as a zip. The
server explodes it into per-file objects + a manifest.json (each member
with sha256). Covers publish (engine-token gated), latest pointer,
manifest fetch, per-file fetch + sha256 roundtrip, bad-archive reject,
and not-yet-published / unknown-member 404s.
"""

from __future__ import annotations

import hashlib
import io
import sys
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tone_forge_api as api  # noqa: E402
from tone_forge import beat_model_store  # noqa: E402


# A tiny fake .mlmodelc: two member files under a top-level wrapper dir
# (mirrors how `coremlc compile` lays out a compiled model).
_MEMBERS = {
    "model.mlmodel": b"\x00\x01COREML\xff",
    "metadata.json": b'{"role":"drum"}',
}


def _make_zip(members: dict[str, bytes] = _MEMBERS) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(f"BeatClassifier.mlmodelc/{name}", data)
    return buf.getvalue()


@pytest.fixture
def client():
    with TestClient(api.app) as c:
        yield c


@pytest.fixture(autouse=True)
def isolate_store(tmp_path, monkeypatch):
    """Local (non-R2) store rooted in a tmp dir; loopback engine auth."""
    monkeypatch.setattr(beat_model_store, "_LOCAL_DIR", tmp_path / "beat_model")
    monkeypatch.setattr(
        beat_model_store.r2_storage, "is_configured", lambda: False
    )
    # No engine token set → loopback (TestClient) is authorized.
    monkeypatch.delenv("TONEFORGE_ENGINE_TOKEN", raising=False)
    yield


def _publish(client, version="v1", zip_bytes=None):
    body = zip_bytes if zip_bytes is not None else _make_zip()
    return client.post(
        "/api/beat-model",
        data={"version": version},
        files={
            "file": ("model.mlmodelc.zip", io.BytesIO(body), "application/zip")
        },
    )


class TestBeatModelApi:
    def test_latest_404_before_publish(self, client):
        assert client.get("/api/beat-model/latest").status_code == 404

    def test_publish_then_latest(self, client):
        resp = _publish(client, version="2026.07.14")
        assert resp.status_code == 200
        pointer = resp.json()
        assert pointer["version"] == "2026.07.14"
        assert pointer["files"] == len(_MEMBERS)
        assert len(pointer["sha256"]) == 64

        latest = client.get("/api/beat-model/latest").json()
        assert latest["version"] == "2026.07.14"
        assert latest["files"] == len(_MEMBERS)
        assert latest["url"] == "/api/beat-model/2026.07.14/manifest"
        assert latest["sha256"] == pointer["sha256"]

    def test_manifest_lists_members_with_sha256(self, client):
        _publish(client, version="v7")
        manifest = client.get("/api/beat-model/v7/manifest").json()
        assert manifest["version"] == "v7"
        by_path = {f["path"]: f for f in manifest["files"]}
        assert set(by_path) == set(_MEMBERS)
        for name, data in _MEMBERS.items():
            entry = by_path[name]
            assert entry["size"] == len(data)
            assert entry["sha256"] == hashlib.sha256(data).hexdigest()

    def test_fetch_file_roundtrips_bytes(self, client):
        _publish(client, version="v7")
        resp = client.get("/api/beat-model/v7/file/model.mlmodel")
        assert resp.status_code == 200
        assert resp.content == _MEMBERS["model.mlmodel"]
        assert resp.headers["content-type"] == "application/octet-stream"

    def test_fetch_unknown_version_manifest_404(self, client):
        assert (
            client.get("/api/beat-model/nope/manifest").status_code == 404
        )

    def test_fetch_unknown_member_404(self, client):
        _publish(client, version="v7")
        assert (
            client.get("/api/beat-model/v7/file/missing.bin").status_code
            == 404
        )

    def test_publish_rejects_bad_version(self, client):
        resp = _publish(client, version="bad/../slash")
        assert resp.status_code == 400

    def test_publish_rejects_non_zip(self, client):
        resp = _publish(client, version="v2", zip_bytes=b"not a zip")
        assert resp.status_code == 400

    def test_publish_engine_token_required(self, client, monkeypatch):
        # With a token configured, an unauthenticated publish is hidden
        # behind a 404 (engine-auth guard shape).
        monkeypatch.setenv("TONEFORGE_ENGINE_TOKEN", "secret-xyz")
        resp = _publish(client, version="v2")
        assert resp.status_code == 404

    def test_publish_with_engine_token(self, client, monkeypatch):
        monkeypatch.setenv("TONEFORGE_ENGINE_TOKEN", "secret-xyz")
        resp = client.post(
            "/api/beat-model",
            data={"version": "v3"},
            files={
                "file": (
                    "m.zip",
                    io.BytesIO(_make_zip()),
                    "application/zip",
                )
            },
            headers={"X-Engine-Token": "secret-xyz"},
        )
        assert resp.status_code == 200
        assert resp.json()["version"] == "v3"

    def test_latest_declared_before_version_route(self, client):
        # "latest" must resolve to the pointer route, not be treated as a
        # version id.
        _publish(client, version="v9")
        assert client.get("/api/beat-model/latest").json()["version"] == "v9"
