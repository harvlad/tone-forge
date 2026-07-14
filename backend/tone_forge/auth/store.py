"""Auth storage: protocol + in-memory fallback.

``PostgresAuthStore`` (tone_forge.auth.db) is used when DATABASE_URL is
set; ``MemoryAuthStore`` otherwise. Both implement ``AuthStore`` so the
routes never know which backend they run on. The memory store is also
what the default test tier exercises — no Postgres required.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional, Protocol

SESSION_TTL = timedelta(days=90)
# Sliding renewal: extend when less than this much life remains.
SESSION_RENEW_BELOW = timedelta(days=45)
MAGIC_LINK_TTL = timedelta(minutes=15)


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class AuthUser:
    id: str
    email: Optional[str]
    display_name: Optional[str]


class AuthStore(Protocol):
    async def upsert_user_by_identity(
        self,
        provider: str,
        subject: str,
        email: Optional[str] = None,
        display_name: Optional[str] = None,
    ) -> AuthUser:
        """Find-or-create the user behind an identity.

        Resolution order: existing (provider, subject) identity wins;
        otherwise an existing user with the same (verified) email is
        linked; otherwise a new user is created.
        """
        ...

    async def create_session(
        self,
        user_id: str,
        token_hash: bytes,
        device_label: Optional[str] = None,
    ) -> None: ...

    async def session_user(self, token_hash: bytes) -> Optional[AuthUser]:
        """Resolve a session token hash to its user.

        Expired sessions resolve to None. Implementations apply the
        sliding renewal (extend expiry when close to it).
        """
        ...

    async def delete_session(self, token_hash: bytes) -> None: ...

    async def create_magic_link(
        self,
        email: str,
        token_hash: bytes,
        requester_ip: Optional[str] = None,
    ) -> None: ...

    async def consume_magic_link(self, token_hash: bytes) -> Optional[str]:
        """Single-use: returns the email and marks the token consumed.

        Returns None for unknown, expired, or already-consumed tokens.
        """
        ...

    async def claim_device(self, device_id: str, user_id: str) -> None: ...

    async def device_owner(self, device_id: str) -> Optional[str]:
        """User id a device was claimed by, or None."""
        ...


# ---------------------------------------------------------------------------
# In-memory implementation (dev / default test tier)


@dataclass
class _MemUser:
    id: str
    email: Optional[str] = None
    display_name: Optional[str] = None


@dataclass
class _MemSession:
    user_id: str
    device_label: Optional[str]
    created_at: datetime = field(default_factory=_now)
    expires_at: datetime = field(default_factory=lambda: _now() + SESSION_TTL)


@dataclass
class _MemMagicLink:
    email: str
    requester_ip: Optional[str]
    expires_at: datetime = field(default_factory=lambda: _now() + MAGIC_LINK_TTL)
    consumed: bool = False


class MemoryAuthStore:
    """Single-process, event-loop-confined — no locking needed."""

    def __init__(self) -> None:
        self._users: dict[str, _MemUser] = {}
        self._identities: dict[tuple[str, str], str] = {}  # (provider, subject) -> user_id
        self._sessions: dict[bytes, _MemSession] = {}
        self._magic_links: dict[bytes, _MemMagicLink] = {}
        self._devices: dict[str, Optional[str]] = {}  # device_id -> user_id

    async def upsert_user_by_identity(
        self,
        provider: str,
        subject: str,
        email: Optional[str] = None,
        display_name: Optional[str] = None,
    ) -> AuthUser:
        user_id = self._identities.get((provider, subject))
        if user_id is None and email:
            for uid, u in self._users.items():
                if u.email == email:
                    user_id = uid
                    break
        if user_id is None:
            user_id = str(uuid.uuid4())
            self._users[user_id] = _MemUser(id=user_id)
        self._identities[(provider, subject)] = user_id
        user = self._users[user_id]
        if email and not user.email:
            user.email = email
        if display_name and not user.display_name:
            user.display_name = display_name
        return AuthUser(id=user.id, email=user.email, display_name=user.display_name)

    async def create_session(
        self,
        user_id: str,
        token_hash: bytes,
        device_label: Optional[str] = None,
    ) -> None:
        self._sessions[token_hash] = _MemSession(
            user_id=user_id, device_label=device_label
        )

    async def session_user(self, token_hash: bytes) -> Optional[AuthUser]:
        sess = self._sessions.get(token_hash)
        if sess is None:
            return None
        now = _now()
        if sess.expires_at <= now:
            del self._sessions[token_hash]
            return None
        if sess.expires_at - now < SESSION_RENEW_BELOW:
            sess.expires_at = now + SESSION_TTL
        user = self._users.get(sess.user_id)
        if user is None:
            return None
        return AuthUser(id=user.id, email=user.email, display_name=user.display_name)

    async def delete_session(self, token_hash: bytes) -> None:
        self._sessions.pop(token_hash, None)

    async def create_magic_link(
        self,
        email: str,
        token_hash: bytes,
        requester_ip: Optional[str] = None,
    ) -> None:
        self._magic_links[token_hash] = _MemMagicLink(
            email=email, requester_ip=requester_ip
        )

    async def consume_magic_link(self, token_hash: bytes) -> Optional[str]:
        link = self._magic_links.get(token_hash)
        if link is None or link.consumed or link.expires_at <= _now():
            return None
        link.consumed = True
        return link.email

    async def claim_device(self, device_id: str, user_id: str) -> None:
        self._devices[device_id] = user_id

    async def device_owner(self, device_id: str) -> Optional[str]:
        return self._devices.get(device_id)
