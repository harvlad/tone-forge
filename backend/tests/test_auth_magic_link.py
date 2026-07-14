"""Magic-link flow through the FastAPI app (MemoryAuthStore, dev email log)."""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tone_forge_api as api  # noqa: E402
from tone_forge.auth import email_sender, rate_limit  # noqa: E402
from tone_forge.auth import store as auth_store  # noqa: E402
from tone_forge.auth.deps import SESSION_COOKIE  # noqa: E402
from tone_forge.auth.tokens import hash_token  # noqa: E402


@pytest.fixture
def client():
    rate_limit.reset()
    email_sender.sent_emails.clear()
    with TestClient(api.app) as c:
        yield c


def _request_link(client, email="user@example.com"):
    resp = client.post("/api/auth/magic-link", json={"email": email})
    assert resp.status_code == 202
    assert len(email_sender.sent_emails) >= 1
    link = email_sender.sent_emails[-1]["link"]
    return parse_qs(urlparse(link).query)["token"][0]


class TestMagicLink:
    def test_request_logs_link_in_dev(self, client):
        token = _request_link(client)
        assert len(token) >= 40
        assert email_sender.sent_emails[-1]["to"] == "user@example.com"

    def test_email_is_normalized(self, client):
        _request_link(client, "  User@Example.COM ")
        assert email_sender.sent_emails[-1]["to"] == "user@example.com"

    def test_invalid_email_rejected(self, client):
        for bad in ("nope", "a@b", "a" * 255 + "@x.co"):
            resp = client.post("/api/auth/magic-link", json={"email": bad})
            assert resp.status_code == 422, bad
        assert email_sender.sent_emails == []

    def test_verify_signs_in_and_sets_cookie(self, client):
        token = _request_link(client)
        resp = client.get(
            f"/api/auth/verify?token={token}", follow_redirects=False
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == "/"
        set_cookie = resp.headers["set-cookie"]
        assert SESSION_COOKIE in set_cookie
        assert "HttpOnly" in set_cookie
        assert "SameSite=lax" in set_cookie
        # TestClient is http, so Secure must be off (https detection)
        assert "Secure" not in set_cookie

        who = client.get("/api/auth/session")
        assert who.json()["user"]["email"] == "user@example.com"

    def test_verify_secure_cookie_behind_https_proxy(self, client):
        token = _request_link(client)
        resp = client.get(
            f"/api/auth/verify?token={token}",
            headers={"X-Forwarded-Proto": "https"},
            follow_redirects=False,
        )
        assert "Secure" in resp.headers["set-cookie"]

    def test_verify_is_single_use(self, client):
        token = _request_link(client)
        assert (
            client.get(
                f"/api/auth/verify?token={token}", follow_redirects=False
            ).status_code
            == 303
        )
        resp = client.get(
            f"/api/auth/verify?token={token}", follow_redirects=False
        )
        assert resp.headers["location"] == "/?auth_error=expired"

    def test_verify_expired_token(self, client):
        token = _request_link(client)
        store = api.app.state.auth_store
        store._magic_links[hash_token(token)].expires_at = (
            auth_store._now() - timedelta(seconds=1)
        )
        resp = client.get(
            f"/api/auth/verify?token={token}", follow_redirects=False
        )
        assert resp.headers["location"] == "/?auth_error=expired"

    def test_verify_garbage_token(self, client):
        resp = client.get(
            "/api/auth/verify?token=not-a-token", follow_redirects=False
        )
        assert resp.headers["location"] == "/?auth_error=expired"

    def test_repeat_sign_in_same_user(self, client):
        t1 = _request_link(client)
        client.get(f"/api/auth/verify?token={t1}", follow_redirects=False)
        u1 = client.get("/api/auth/session").json()["user"]["id"]

        rate_limit.reset()
        t2 = _request_link(client)
        client.get(f"/api/auth/verify?token={t2}", follow_redirects=False)
        u2 = client.get("/api/auth/session").json()["user"]["id"]
        assert u1 == u2
