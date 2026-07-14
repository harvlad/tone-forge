"""Session endpoints: whoami via cookie and bearer, logout, expiry."""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tone_forge_api as api  # noqa: E402
from tone_forge.auth import store as auth_store  # noqa: E402
from tone_forge.auth.deps import SESSION_COOKIE  # noqa: E402
from tone_forge.auth.tokens import hash_token, new_token  # noqa: E402


@pytest.fixture
def client():
    with TestClient(api.app) as c:
        yield c


async def _signed_in_token(email="s@x.co"):
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


class TestSessions:
    def test_anonymous_session_is_null(self, client):
        resp = client.get("/api/auth/session")
        assert resp.status_code == 200
        assert resp.json() == {"user": None}

    def test_whoami_via_cookie(self, client, session_token):
        client.cookies.set(SESSION_COOKIE, session_token)
        assert client.get("/api/auth/session").json()["user"]["email"] == "s@x.co"

    def test_whoami_via_bearer(self, client, session_token):
        resp = client.get(
            "/api/auth/session",
            headers={"Authorization": f"Bearer {session_token}"},
        )
        assert resp.json()["user"]["email"] == "s@x.co"

    def test_garbage_token_is_null(self, client):
        client.cookies.set(SESSION_COOKIE, "garbage")
        assert client.get("/api/auth/session").json() == {"user": None}

    def test_expired_session_is_null(self, client, session_token):
        store = api.app.state.auth_store
        store._sessions[hash_token(session_token)].expires_at = (
            auth_store._now() - timedelta(seconds=1)
        )
        client.cookies.set(SESSION_COOKIE, session_token)
        assert client.get("/api/auth/session").json() == {"user": None}

    def test_logout_revokes_and_clears_cookie(self, client, session_token):
        client.cookies.set(SESSION_COOKIE, session_token)
        resp = client.post("/api/auth/logout")
        assert resp.status_code == 200
        assert f'{SESSION_COOKIE}=""' in resp.headers["set-cookie"]
        # Token revoked server-side, not just cookie cleared
        client.cookies.set(SESSION_COOKIE, session_token)
        assert client.get("/api/auth/session").json() == {"user": None}

    def test_logout_via_bearer(self, client, session_token):
        resp = client.post(
            "/api/auth/logout",
            headers={"Authorization": f"Bearer {session_token}"},
        )
        assert resp.status_code == 200
        assert (
            client.get(
                "/api/auth/session",
                headers={"Authorization": f"Bearer {session_token}"},
            ).json()["user"]
            is None
        )

    def test_logout_anonymous_ok(self, client):
        assert client.post("/api/auth/logout").status_code == 200
