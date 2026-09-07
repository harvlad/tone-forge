"""TONEFORGE_FEATURED_QUERY pins a curated song to the front of /api/history.

Env-gated dev/TestFlight behavior: unset must be byte-for-byte today's
list (no ``featured`` key anywhere), and scope=mine is never reordered.
"""

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

ENV = "TONEFORGE_FEATURED_QUERY"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "_HISTORY_FILE", tmp_path / "history.json")
    monkeypatch.setattr(r2_storage, "is_configured", lambda: False)
    monkeypatch.delenv(ENV, raising=False)
    with TestClient(api.app) as c:
        yield c


def _seed(client):
    # _add_to_history inserts at the front, so list order is d, c
    # (Doomsday), b, a — the featured match sits mid-list.
    api._add_to_history({"name": "a-first"})
    api._add_to_history({"name": "b-second"})
    api._add_to_history({"name": "Doomsday - MF DOOM"})
    api._add_to_history({"name": "d-newest"})


class TestFeatured:
    def test_featured_moves_to_front_with_flag(self, client, monkeypatch):
        _seed(client)
        monkeypatch.setenv(ENV, "doomsday")  # case-insensitive contains
        rows = client.get("/api/history").json()["history"]
        assert [r["name"] for r in rows] == [
            "Doomsday - MF DOOM", "d-newest", "b-second", "a-first",
        ]
        assert rows[0]["featured"] is True
        assert all("featured" not in r for r in rows[1:])

    def test_newest_match_wins(self, client, monkeypatch):
        api._add_to_history({"name": "Doomsday (old)"})
        api._add_to_history({"name": "Doomsday (new)"})
        monkeypatch.setenv(ENV, "Doomsday")
        rows = client.get("/api/history").json()["history"]
        assert rows[0]["name"] == "Doomsday (new)"
        assert rows[0]["featured"] is True
        assert "featured" not in rows[1]

    def test_unset_env_changes_nothing(self, client):
        _seed(client)
        rows = client.get("/api/history").json()["history"]
        assert [r["name"] for r in rows] == [
            "d-newest", "Doomsday - MF DOOM", "b-second", "a-first",
        ]
        assert all("featured" not in r for r in rows)

    def test_no_match_changes_nothing(self, client, monkeypatch):
        _seed(client)
        monkeypatch.setenv(ENV, "no-such-song")
        rows = client.get("/api/history").json()["history"]
        assert [r["name"] for r in rows] == [
            "d-newest", "Doomsday - MF DOOM", "b-second", "a-first",
        ]
        assert all("featured" not in r for r in rows)

    def test_scope_mine_untouched(self, client, monkeypatch):
        async def go():
            store = api.app.state.auth_store
            user = await store.upsert_user_by_identity(
                "email", "feat@x.co", email="feat@x.co"
            )
            token = new_token()
            await store.create_session(user.id, hash_token(token))
            return token, user

        token, user = asyncio.run(go())
        api._add_to_history({"name": "Doomsday - MF DOOM"}, owner_id=user.id)
        api._add_to_history({"name": "mine-newest"}, owner_id=user.id)

        monkeypatch.setenv(ENV, "Doomsday")
        client.cookies.set(SESSION_COOKIE, token)
        rows = client.get("/api/history?scope=mine").json()["history"]
        assert [r["name"] for r in rows] == [
            "mine-newest", "Doomsday - MF DOOM",
        ]
        assert all("featured" not in r for r in rows)
