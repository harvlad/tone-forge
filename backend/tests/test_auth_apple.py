"""Sign in with Apple: verification unit tests + route flow.

Signs identity tokens with a local RSA keypair and monkeypatches the
JWKS client so no network is involved.
"""

from __future__ import annotations

import hashlib
import sys
import time
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tone_forge_api as api  # noqa: E402
from tone_forge.auth import apple  # noqa: E402
from tone_forge.auth.apple import (  # noqa: E402
    AppleVerifyError,
    verify_identity_token,
)

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_BUNDLE = "com.harvlad.toneforge.mobile"


def _make_token(**overrides):
    claims = {
        "iss": "https://appleid.apple.com",
        "aud": _BUNDLE,
        "exp": int(time.time()) + 600,
        "iat": int(time.time()),
        "sub": "apple-sub-001",
        "email": "relay@privaterelay.appleid.com",
    }
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, _KEY, algorithm="RS256")


class _FakeSigningKey:
    key = _KEY.public_key()


class _FakeJWKClient:
    def get_signing_key_from_jwt(self, token):
        return _FakeSigningKey()


@pytest.fixture(autouse=True)
def fake_jwks(monkeypatch):
    monkeypatch.setattr(apple, "_get_jwk_client", lambda: _FakeJWKClient())


class TestVerify:
    def test_valid_token(self):
        ident = verify_identity_token(_make_token())
        assert ident.subject == "apple-sub-001"
        assert ident.email == "relay@privaterelay.appleid.com"

    def test_no_email_ok(self):
        ident = verify_identity_token(_make_token(email=None))
        assert ident.email is None

    def test_bad_audience(self):
        with pytest.raises(AppleVerifyError):
            verify_identity_token(_make_token(aud="com.evil.app"))

    def test_bad_issuer(self):
        with pytest.raises(AppleVerifyError):
            verify_identity_token(_make_token(iss="https://evil.example"))

    def test_expired(self):
        with pytest.raises(AppleVerifyError):
            verify_identity_token(_make_token(exp=int(time.time()) - 10))

    def test_garbage_token(self):
        with pytest.raises(AppleVerifyError):
            verify_identity_token("not.a.jwt")

    def test_nonce_hashed_match(self):
        raw = "raw-nonce-123"
        tok = _make_token(nonce=hashlib.sha256(raw.encode()).hexdigest())
        assert verify_identity_token(tok, nonce=raw).subject == "apple-sub-001"

    def test_nonce_mismatch(self):
        tok = _make_token(nonce="something-else")
        with pytest.raises(AppleVerifyError):
            verify_identity_token(tok, nonce="raw-nonce-123")

    def test_missing_sub(self):
        with pytest.raises(AppleVerifyError):
            verify_identity_token(_make_token(sub=None))


@pytest.fixture
def client():
    with TestClient(api.app) as c:
        yield c


class TestAppleRoute:
    def test_sign_in_returns_token_and_user(self, client):
        resp = client.post(
            "/api/auth/apple",
            json={
                "identity_token": _make_token(),
                "device_id": "dev-abc",
                "full_name": "Matt H",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["token"]) >= 40
        assert body["user"]["email"] == "relay@privaterelay.appleid.com"
        assert body["user"]["display_name"] == "Matt H"

        # Bearer works for whoami
        who = client.get(
            "/api/auth/session",
            headers={"Authorization": f"Bearer {body['token']}"},
        )
        assert who.json()["user"]["id"] == body["user"]["id"]

    def test_invalid_token_401(self, client):
        resp = client.post(
            "/api/auth/apple", json={"identity_token": "junk"}
        )
        assert resp.status_code == 401

    def test_device_claimed(self, client):
        import asyncio

        resp = client.post(
            "/api/auth/apple",
            json={"identity_token": _make_token(), "device_id": "dev-xyz"},
        )
        user_id = resp.json()["user"]["id"]
        store = api.app.state.auth_store
        owner = asyncio.run(store.device_owner("dev-xyz"))
        assert owner == user_id

    def test_links_to_email_account(self, client):
        # Existing email user + apple sign-in with same email = same account
        import asyncio

        store = api.app.state.auth_store
        u = asyncio.run(
            store.upsert_user_by_identity(
                "email", "same@x.co", email="same@x.co"
            )
        )
        resp = client.post(
            "/api/auth/apple",
            json={"identity_token": _make_token(email="same@x.co")},
        )
        assert resp.json()["user"]["id"] == u.id
