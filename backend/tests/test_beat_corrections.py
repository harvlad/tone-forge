"""POST /api/beat-corrections — auth, schema validation, roundtrip.

Covers the Beat Capture (D-024) correction ingest: signed-in users push
batches of drum-role corrections (7 analysis features + role labels, no
audio) into the server-side training corpus.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tone_forge_api as api  # noqa: E402
from tone_forge import beat_corpus  # noqa: E402
from tone_forge.auth.deps import SESSION_COOKIE  # noqa: E402
from tone_forge.auth.tokens import hash_token, new_token  # noqa: E402


@pytest.fixture
def client():
    with TestClient(api.app) as c:
        yield c


@pytest.fixture(autouse=True)
def isolate_corpus(tmp_path, monkeypatch):
    """Point the corpus at a tmp dir and force the local (non-R2) path."""
    monkeypatch.setattr(beat_corpus, "_LOCAL_DIR", tmp_path / "beat_corrections")
    monkeypatch.setattr(beat_corpus.r2_storage, "is_configured", lambda: False)
    yield


async def _signed_in_token(email="beat@x.co"):
    store = api.app.state.auth_store
    user = await store.upsert_user_by_identity("email", email, email=email)
    token = new_token()
    await store.create_session(user.id, hash_token(token))
    return token, user


@pytest.fixture
def session_token(client):
    import asyncio

    token, _user = asyncio.run(_signed_in_token())
    return token


def _row(original="kick", corrected="snare", **overrides):
    features = {name: 0.5 for name in beat_corpus.FEATURE_NAMES}
    features.update(overrides.pop("features", {}))
    row = {
        "features": features,
        "original": original,
        "corrected": corrected,
        "ts": "2026-07-14T00:00:00Z",
    }
    row.update(overrides)
    return row


class TestBeatCorrections:
    def test_requires_auth(self, client):
        resp = client.post(
            "/api/beat-corrections", json={"corrections": [_row()]}
        )
        assert resp.status_code == 401

    def test_accepts_valid_batch(self, client, session_token):
        client.cookies.set(SESSION_COOKIE, session_token)
        resp = client.post(
            "/api/beat-corrections",
            json={"corrections": [_row(), _row(original="clap", corrected="rim")]},
            headers={"X-Device-Id": "dev-123"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"accepted": 2}

    def test_roundtrip_stores_rows(self, client, session_token):
        client.cookies.set(SESSION_COOKIE, session_token)
        client.post(
            "/api/beat-corrections",
            json={"corrections": [_row()]},
            headers={"X-Device-Id": "dev-xyz"},
        )
        stored = beat_corpus.read_all()
        assert len(stored) == 1
        assert stored[0]["original"] == "kick"
        assert stored[0]["corrected"] == "snare"
        assert stored[0]["device_id"] == "dev-xyz"
        assert stored[0]["owner_id"]  # stamped with the signed-in account

    def test_rejects_unknown_role(self, client, session_token):
        client.cookies.set(SESSION_COOKIE, session_token)
        resp = client.post(
            "/api/beat-corrections",
            json={"corrections": [_row(corrected="cowbell")]},
        )
        assert resp.status_code == 400

    def test_rejects_missing_feature(self, client, session_token):
        client.cookies.set(SESSION_COOKIE, session_token)
        bad = _row()
        del bad["features"]["zcr"]
        resp = client.post(
            "/api/beat-corrections", json={"corrections": [bad]}
        )
        assert resp.status_code == 400
        # Nothing persisted on a rejected batch.
        assert beat_corpus.read_all() == []

    def test_empty_batch_accepts_zero(self, client, session_token):
        client.cookies.set(SESSION_COOKIE, session_token)
        resp = client.post(
            "/api/beat-corrections", json={"corrections": []}
        )
        assert resp.status_code == 200
        assert resp.json() == {"accepted": 0}


class TestBeatCorrectionsExport:
    def test_export_requires_engine_token(self, client, monkeypatch):
        monkeypatch.setenv("TONEFORGE_ENGINE_TOKEN", "s3cret")
        # No token header → guarded 404 (surface not enumerable).
        resp = client.get("/api/beat-corrections/export")
        assert resp.status_code == 404

    def test_export_empty_returns_header_only(self, client, monkeypatch):
        monkeypatch.setenv("TONEFORGE_ENGINE_TOKEN", "s3cret")
        resp = client.get(
            "/api/beat-corrections/export",
            headers={"X-Engine-Token": "s3cret"},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/csv")
        lines = resp.text.strip().splitlines()
        assert lines[0] == ",".join(
            list(beat_corpus.FEATURE_NAMES) + ["original", "corrected"]
        )
        assert len(lines) == 1  # header only, no rows

    def test_export_roundtrips_rows(self, client, session_token, monkeypatch):
        client.cookies.set(SESSION_COOKIE, session_token)
        client.post(
            "/api/beat-corrections",
            json={"corrections": [_row(original="kick", corrected="snare")]},
        )
        monkeypatch.setenv("TONEFORGE_ENGINE_TOKEN", "s3cret")
        resp = client.get(
            "/api/beat-corrections/export",
            headers={"X-Engine-Token": "s3cret"},
        )
        assert resp.status_code == 200
        lines = resp.text.strip().splitlines()
        assert len(lines) == 2  # header + 1 row
        cols = lines[1].split(",")
        # 7 features + original + corrected
        assert len(cols) == len(beat_corpus.FEATURE_NAMES) + 2
        assert cols[-2:] == ["kick", "snare"]
