"""Postgres-backed auth store (asyncpg, no ORM).

Pool lifecycle is owned by the app lifespan. Migrations are plain SQL
files in ``migrations/``, applied in filename order at startup and
recorded in ``schema_migrations`` — fine for a single-process deploy.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import asyncpg

from .store import (
    SESSION_RENEW_BELOW,
    SESSION_TTL,
    MAGIC_LINK_TTL,
    AuthUser,
)

logger = logging.getLogger("toneforge.auth")

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"

_pool: Optional[asyncpg.Pool] = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def init_pool() -> Optional[asyncpg.Pool]:
    """Create the pool and apply migrations. None when DATABASE_URL unset."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        return None
    global _pool
    _pool = await asyncpg.create_pool(url, min_size=1, max_size=5)
    await run_migrations(_pool)
    logger.info("auth: postgres pool ready")
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def run_migrations(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version    text PRIMARY KEY,
                applied_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
        applied = {
            r["version"]
            for r in await conn.fetch("SELECT version FROM schema_migrations")
        }
        for path in sorted(_MIGRATIONS_DIR.glob("*.sql")):
            if path.name in applied:
                continue
            async with conn.transaction():
                await conn.execute(path.read_text())
                await conn.execute(
                    "INSERT INTO schema_migrations (version) VALUES ($1)",
                    path.name,
                )
            logger.info("auth: applied migration %s", path.name)


class PostgresAuthStore:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def upsert_user_by_identity(
        self,
        provider: str,
        subject: str,
        email: Optional[str] = None,
        display_name: Optional[str] = None,
    ) -> AuthUser:
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    SELECT u.id, u.email, u.display_name
                    FROM auth_identities i JOIN users u ON u.id = i.user_id
                    WHERE i.provider = $1 AND i.subject = $2
                    """,
                    provider, subject,
                )
                if row is None and email:
                    row = await conn.fetchrow(
                        "SELECT id, email, display_name FROM users WHERE email = $1",
                        email,
                    )
                    if row is not None:
                        await conn.execute(
                            """
                            INSERT INTO auth_identities (user_id, provider, subject)
                            VALUES ($1, $2, $3) ON CONFLICT DO NOTHING
                            """,
                            row["id"], provider, subject,
                        )
                if row is None:
                    row = await conn.fetchrow(
                        """
                        INSERT INTO users (email, display_name)
                        VALUES ($1, $2) RETURNING id, email, display_name
                        """,
                        email, display_name,
                    )
                    await conn.execute(
                        """
                        INSERT INTO auth_identities (user_id, provider, subject)
                        VALUES ($1, $2, $3)
                        """,
                        row["id"], provider, subject,
                    )
                user_id, cur_email, cur_name = (
                    row["id"], row["email"], row["display_name"]
                )
                if email and not cur_email:
                    await conn.execute(
                        "UPDATE users SET email = $2 WHERE id = $1",
                        user_id, email,
                    )
                    cur_email = email
                if display_name and not cur_name:
                    await conn.execute(
                        "UPDATE users SET display_name = $2 WHERE id = $1",
                        user_id, display_name,
                    )
                    cur_name = display_name
        return AuthUser(id=str(user_id), email=cur_email, display_name=cur_name)

    async def create_session(
        self,
        user_id: str,
        token_hash: bytes,
        device_label: Optional[str] = None,
    ) -> None:
        await self._pool.execute(
            """
            INSERT INTO sessions (user_id, token_hash, device_label, expires_at)
            VALUES ($1, $2, $3, $4)
            """,
            user_id, token_hash, device_label, _now() + SESSION_TTL,
        )

    async def session_user(self, token_hash: bytes) -> Optional[AuthUser]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT s.id AS session_id, s.expires_at,
                       u.id, u.email, u.display_name
                FROM sessions s JOIN users u ON u.id = s.user_id
                WHERE s.token_hash = $1
                """,
                token_hash,
            )
            if row is None:
                return None
            now = _now()
            if row["expires_at"] <= now:
                await conn.execute(
                    "DELETE FROM sessions WHERE id = $1", row["session_id"]
                )
                return None
            if row["expires_at"] - now < SESSION_RENEW_BELOW:
                await conn.execute(
                    """
                    UPDATE sessions SET expires_at = $2, last_seen_at = $3
                    WHERE id = $1
                    """,
                    row["session_id"], now + SESSION_TTL, now,
                )
        return AuthUser(
            id=str(row["id"]),
            email=row["email"],
            display_name=row["display_name"],
        )

    async def delete_session(self, token_hash: bytes) -> None:
        await self._pool.execute(
            "DELETE FROM sessions WHERE token_hash = $1", token_hash
        )

    async def create_magic_link(
        self,
        email: str,
        token_hash: bytes,
        requester_ip: Optional[str] = None,
    ) -> None:
        await self._pool.execute(
            """
            INSERT INTO magic_link_tokens (email, token_hash, requester_ip, expires_at)
            VALUES ($1, $2, $3, $4)
            """,
            email, token_hash, requester_ip, _now() + MAGIC_LINK_TTL,
        )

    async def consume_magic_link(self, token_hash: bytes) -> Optional[str]:
        row = await self._pool.fetchrow(
            """
            UPDATE magic_link_tokens
            SET consumed_at = now()
            WHERE token_hash = $1 AND consumed_at IS NULL AND expires_at > now()
            RETURNING email
            """,
            token_hash,
        )
        return row["email"] if row else None

    async def claim_device(self, device_id: str, user_id: str) -> None:
        await self._pool.execute(
            """
            INSERT INTO devices (device_id, user_id, claimed_at)
            VALUES ($1, $2, now())
            ON CONFLICT (device_id)
            DO UPDATE SET user_id = EXCLUDED.user_id, claimed_at = now()
            """,
            device_id, user_id,
        )

    async def device_owner(self, device_id: str) -> Optional[str]:
        row = await self._pool.fetchrow(
            "SELECT user_id FROM devices WHERE device_id = $1", device_id
        )
        return str(row["user_id"]) if row and row["user_id"] else None
