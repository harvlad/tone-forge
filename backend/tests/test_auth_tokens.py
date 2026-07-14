"""Token generation + hashing, and MemoryAuthStore behaviour."""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tone_forge.auth import store as auth_store  # noqa: E402
from tone_forge.auth.store import MemoryAuthStore  # noqa: E402
from tone_forge.auth.tokens import hash_token, new_token  # noqa: E402


class TestTokens:
    def test_tokens_are_unique_and_long(self):
        tokens = {new_token() for _ in range(100)}
        assert len(tokens) == 100
        assert all(len(t) >= 40 for t in tokens)

    def test_hash_is_sha256_deterministic(self):
        t = new_token()
        assert hash_token(t) == hash_token(t)
        assert len(hash_token(t)) == 32
        assert hash_token(t) != hash_token(new_token())


class TestMemoryAuthStore:
    @pytest.mark.asyncio
    async def test_upsert_creates_then_reuses_user(self):
        s = MemoryAuthStore()
        u1 = await s.upsert_user_by_identity("email", "a@b.co", email="a@b.co")
        u2 = await s.upsert_user_by_identity("email", "a@b.co", email="a@b.co")
        assert u1.id == u2.id
        assert u1.email == "a@b.co"

    @pytest.mark.asyncio
    async def test_identity_links_by_email(self):
        # Existing email user + new apple identity with same verified
        # email must resolve to one account (cross-device pickup).
        s = MemoryAuthStore()
        u1 = await s.upsert_user_by_identity("email", "a@b.co", email="a@b.co")
        u2 = await s.upsert_user_by_identity(
            "apple", "apple-sub-123", email="a@b.co"
        )
        assert u1.id == u2.id

    @pytest.mark.asyncio
    async def test_distinct_identities_distinct_users(self):
        s = MemoryAuthStore()
        u1 = await s.upsert_user_by_identity("email", "a@b.co", email="a@b.co")
        u2 = await s.upsert_user_by_identity("apple", "sub-x")  # no email
        assert u1.id != u2.id

    @pytest.mark.asyncio
    async def test_session_roundtrip_and_logout(self):
        s = MemoryAuthStore()
        u = await s.upsert_user_by_identity("email", "a@b.co", email="a@b.co")
        h = hash_token(new_token())
        await s.create_session(u.id, h)
        assert (await s.session_user(h)).id == u.id
        await s.delete_session(h)
        assert await s.session_user(h) is None

    @pytest.mark.asyncio
    async def test_expired_session_is_dead(self):
        s = MemoryAuthStore()
        u = await s.upsert_user_by_identity("email", "a@b.co", email="a@b.co")
        h = hash_token(new_token())
        await s.create_session(u.id, h)
        s._sessions[h].expires_at = auth_store._now() - timedelta(seconds=1)
        assert await s.session_user(h) is None
        assert h not in s._sessions  # cleaned up

    @pytest.mark.asyncio
    async def test_sliding_renewal(self):
        s = MemoryAuthStore()
        u = await s.upsert_user_by_identity("email", "a@b.co", email="a@b.co")
        h = hash_token(new_token())
        await s.create_session(u.id, h)
        # Push expiry to just inside the renewal window.
        s._sessions[h].expires_at = auth_store._now() + timedelta(days=1)
        await s.session_user(h)
        remaining = s._sessions[h].expires_at - auth_store._now()
        assert remaining > timedelta(days=80)

    @pytest.mark.asyncio
    async def test_magic_link_single_use_and_expiry(self):
        s = MemoryAuthStore()
        h = hash_token(new_token())
        await s.create_magic_link("a@b.co", h)
        assert await s.consume_magic_link(h) == "a@b.co"
        assert await s.consume_magic_link(h) is None  # single use

        h2 = hash_token(new_token())
        await s.create_magic_link("c@d.co", h2)
        s._magic_links[h2].expires_at = auth_store._now() - timedelta(seconds=1)
        assert await s.consume_magic_link(h2) is None

    @pytest.mark.asyncio
    async def test_device_claim(self):
        s = MemoryAuthStore()
        u = await s.upsert_user_by_identity("email", "a@b.co", email="a@b.co")
        assert await s.device_owner("dev1") is None
        await s.claim_device("dev1", u.id)
        assert await s.device_owner("dev1") == u.id
