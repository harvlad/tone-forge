"""PostgresAuthStore integration tests.

Run against a real Postgres set via TONEFORGE_TEST_DATABASE_URL, e.g.::

    TONEFORGE_TEST_DATABASE_URL=postgresql://localhost/toneforge_test \
        python3 -m pytest tests/test_auth_pg.py -q

Skipped entirely when the env var is unset (CI/dev default). Each test
gets a fresh schema: tables are dropped and migrations re-applied.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TEST_DB_URL = os.environ.get("TONEFORGE_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DB_URL, reason="TONEFORGE_TEST_DATABASE_URL not set"
)

if TEST_DB_URL:
    import asyncpg  # noqa: E402

    from tone_forge.auth.db import PostgresAuthStore, run_migrations  # noqa: E402
    from tone_forge.auth.tokens import hash_token, new_token  # noqa: E402

_DROP = """
DROP TABLE IF EXISTS
    devices, magic_link_tokens, sessions, auth_identities, users,
    schema_migrations
CASCADE
"""


@pytest_asyncio.fixture
async def store():
    pool = await asyncpg.create_pool(TEST_DB_URL, min_size=1, max_size=2)
    await pool.execute(_DROP)
    await run_migrations(pool)
    yield PostgresAuthStore(pool)
    await pool.close()


class TestPostgresAuthStore:
    @pytest.mark.asyncio
    async def test_upsert_creates_then_reuses_user(self, store):
        u1 = await store.upsert_user_by_identity("email", "a@b.co", email="a@b.co")
        u2 = await store.upsert_user_by_identity("email", "a@b.co", email="a@b.co")
        assert u1.id == u2.id
        assert u1.email == "a@b.co"

    @pytest.mark.asyncio
    async def test_identity_links_by_email(self, store):
        u1 = await store.upsert_user_by_identity("email", "a@b.co", email="a@b.co")
        u2 = await store.upsert_user_by_identity(
            "apple", "apple-sub-123", email="a@b.co"
        )
        assert u1.id == u2.id

    @pytest.mark.asyncio
    async def test_distinct_identities_distinct_users(self, store):
        u1 = await store.upsert_user_by_identity("email", "a@b.co", email="a@b.co")
        u2 = await store.upsert_user_by_identity("apple", "sub-x")  # no email
        assert u1.id != u2.id

    @pytest.mark.asyncio
    async def test_email_backfill_on_later_identity(self, store):
        u1 = await store.upsert_user_by_identity("apple", "sub-y")
        assert u1.email is None
        u2 = await store.upsert_user_by_identity("apple", "sub-y", email="y@z.co")
        assert u2.id == u1.id
        assert u2.email == "y@z.co"

    @pytest.mark.asyncio
    async def test_session_roundtrip_and_logout(self, store):
        u = await store.upsert_user_by_identity("email", "a@b.co", email="a@b.co")
        h = hash_token(new_token())
        await store.create_session(u.id, h, device_label="test")
        got = await store.session_user(h)
        assert got is not None and got.id == u.id
        await store.delete_session(h)
        assert await store.session_user(h) is None

    @pytest.mark.asyncio
    async def test_expired_session_is_dead(self, store):
        u = await store.upsert_user_by_identity("email", "a@b.co", email="a@b.co")
        h = hash_token(new_token())
        await store.create_session(u.id, h)
        await store._pool.execute(
            "UPDATE sessions SET expires_at = now() - interval '1 second'"
        )
        assert await store.session_user(h) is None
        # Row cleaned up
        n = await store._pool.fetchval("SELECT count(*) FROM sessions")
        assert n == 0

    @pytest.mark.asyncio
    async def test_sliding_renewal(self, store):
        u = await store.upsert_user_by_identity("email", "a@b.co", email="a@b.co")
        h = hash_token(new_token())
        await store.create_session(u.id, h)
        await store._pool.execute(
            "UPDATE sessions SET expires_at = now() + interval '1 day'"
        )
        assert await store.session_user(h) is not None
        remaining = await store._pool.fetchval(
            "SELECT expires_at - now() FROM sessions"
        )
        assert remaining.days > 80

    @pytest.mark.asyncio
    async def test_magic_link_single_use_and_expiry(self, store):
        h = hash_token(new_token())
        await store.create_magic_link("a@b.co", h, requester_ip="1.2.3.4")
        assert await store.consume_magic_link(h) == "a@b.co"
        assert await store.consume_magic_link(h) is None  # single use

        h2 = hash_token(new_token())
        await store.create_magic_link("c@d.co", h2)
        await store._pool.execute(
            "UPDATE magic_link_tokens SET expires_at = now() - interval '1 second' "
            "WHERE token_hash = $1",
            h2,
        )
        assert await store.consume_magic_link(h2) is None

    @pytest.mark.asyncio
    async def test_device_claim_and_reclaim(self, store):
        u1 = await store.upsert_user_by_identity("email", "a@b.co", email="a@b.co")
        u2 = await store.upsert_user_by_identity("email", "c@d.co", email="c@d.co")
        assert await store.device_owner("dev1") is None
        await store.claim_device("dev1", u1.id)
        assert await store.device_owner("dev1") == u1.id
        await store.claim_device("dev1", u2.id)  # reclaim moves ownership
        assert await store.device_owner("dev1") == u2.id

    @pytest.mark.asyncio
    async def test_migrations_idempotent(self, store):
        # Second run applies nothing and doesn't raise.
        await run_migrations(store._pool)
