"""Ownership stamping, scope=mine, and device claim backfill."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tone_forge_api as api  # noqa: E402
from tone_forge import r2_storage  # noqa: E402
from tone_forge.auth.deps import SESSION_COOKIE  # noqa: E402
from tone_forge.auth.tokens import hash_token, new_token  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "_HISTORY_FILE", tmp_path / "history.json")
    monkeypatch.setattr(r2_storage, "is_configured", lambda: False)
    with TestClient(api.app) as c:
        yield c


def _sign_in(email="own@x.co"):
    async def go():
        store = api.app.state.auth_store
        user = await store.upsert_user_by_identity("email", email, email=email)
        token = new_token()
        await store.create_session(user.id, hash_token(token))
        return token, user

    return asyncio.run(go())


class TestStamping:
    def test_add_to_history_stamps(self, client):
        entry = api._add_to_history(
            {"name": "song"}, device_id="dev-1", owner_id="user-1"
        )
        assert entry["device_id"] == "dev-1"
        assert entry["owner_id"] == "user-1"

    def test_add_to_history_omits_when_absent(self, client):
        entry = api._add_to_history({"name": "song"})
        assert "device_id" not in entry
        assert "owner_id" not in entry

    def test_default_list_shape_unchanged(self, client):
        # iOS regression guard: stamped fields never leak into the
        # lightweight list rows.
        api._add_to_history(
            {"name": "song"}, device_id="dev-1", owner_id="user-1"
        )
        rows = client.get("/api/history").json()["history"]
        assert len(rows) == 1
        assert "device_id" not in rows[0]
        assert "owner_id" not in rows[0]


class TestRequestOwnership:
    def test_signed_in_user_wins(self, client):
        token, user = _sign_in()

        async def go():
            scope = {
                "type": "http",
                "headers": [
                    (b"x-device-id", b"dev-9"),
                    (b"authorization", f"Bearer {token}".encode()),
                ],
                "app": api.app,
            }
            from fastapi import Request

            return await api._request_ownership(Request(scope))

        device_id, owner_id = asyncio.run(go())
        assert device_id == "dev-9"
        assert owner_id == user.id

    def test_claimed_device_attaches_owner(self, client):
        _token, user = _sign_in()
        asyncio.run(api.app.state.auth_store.claim_device("dev-c", user.id))

        async def go():
            from fastapi import Request

            scope = {
                "type": "http",
                "headers": [(b"x-device-id", b"dev-c")],
                "app": api.app,
            }
            return await api._request_ownership(Request(scope))

        device_id, owner_id = asyncio.run(go())
        assert device_id == "dev-c"
        assert owner_id == user.id

    def test_anonymous_unclaimed(self, client):
        async def go():
            from fastapi import Request

            scope = {
                "type": "http",
                "headers": [(b"x-device-id", b"dev-anon")],
                "app": api.app,
            }
            return await api._request_ownership(Request(scope))

        device_id, owner_id = asyncio.run(go())
        assert device_id == "dev-anon"
        assert owner_id is None


class TestScopeMine:
    def test_anonymous_401(self, client):
        assert client.get("/api/history?scope=mine").status_code == 401

    def test_filters_to_owner_and_device(self, client):
        token, user = _sign_in()
        api._add_to_history({"name": "mine-owned"}, owner_id=user.id)
        api._add_to_history({"name": "mine-device"}, device_id="dev-m")
        api._add_to_history({"name": "not-mine"}, owner_id="someone-else")
        api._add_to_history({"name": "anon"})

        client.cookies.set(SESSION_COOKIE, token)
        rows = client.get(
            "/api/history?scope=mine", headers={"X-Device-Id": "dev-m"}
        ).json()["history"]
        names = {r["name"] for r in rows}
        assert names == {"mine-owned", "mine-device"}

    def test_default_scope_returns_everything(self, client):
        api._add_to_history({"name": "a"}, owner_id="u1")
        api._add_to_history({"name": "b"})
        rows = client.get("/api/history").json()["history"]
        assert len(rows) == 2


class TestClaim:
    def test_claim_requires_sign_in(self, client):
        resp = client.post("/api/auth/claim", json={"device_id": "d"})
        assert resp.status_code == 401

    def test_claim_requires_device_id(self, client):
        token, _user = _sign_in()
        client.cookies.set(SESSION_COOKIE, token)
        assert client.post("/api/auth/claim", json={}).status_code == 400

    def test_claim_backfills_only_unowned_matching(self, client):
        token, user = _sign_in()
        api._add_to_history({"name": "unowned-match"}, device_id="dev-x")
        api._add_to_history(
            {"name": "owned-match"}, device_id="dev-x", owner_id="other"
        )
        api._add_to_history({"name": "other-device"}, device_id="dev-y")

        client.cookies.set(SESSION_COOKIE, token)
        resp = client.post("/api/auth/claim", json={"device_id": "dev-x"})
        assert resp.status_code == 200
        assert resp.json() == {"claimed": 1}

        history = api._load_history()
        by_name = {e["name"]: e for e in history}
        assert by_name["unowned-match"]["owner_id"] == user.id
        assert by_name["owned-match"]["owner_id"] == "other"
        assert "owner_id" not in by_name["other-device"]

    def test_claim_idempotent(self, client):
        token, _user = _sign_in()
        api._add_to_history({"name": "e"}, device_id="dev-x")
        client.cookies.set(SESSION_COOKIE, token)
        assert client.post(
            "/api/auth/claim", json={"device_id": "dev-x"}
        ).json() == {"claimed": 1}
        assert client.post(
            "/api/auth/claim", json={"device_id": "dev-x"}
        ).json() == {"claimed": 0}

    def test_claim_binds_device_for_future_jobs(self, client):
        token, user = _sign_in()
        client.cookies.set(SESSION_COOKIE, token)
        client.post("/api/auth/claim", json={"device_id": "dev-f"})
        owner = asyncio.run(
            api.app.state.auth_store.device_owner("dev-f")
        )
        assert owner == user.id
