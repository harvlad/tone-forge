"""Opaque token generation + hashing.

Tokens are returned to the client exactly once; only the SHA-256 hash
is stored. A leaked database therefore never leaks usable sessions.
"""
from __future__ import annotations

import hashlib
import secrets

# 32 bytes of entropy -> 43-char urlsafe string.
_TOKEN_BYTES = 32


def new_token() -> str:
    """Generate an opaque bearer token (session or magic-link)."""
    return secrets.token_urlsafe(_TOKEN_BYTES)


def hash_token(token: str) -> bytes:
    """SHA-256 digest of a token, as stored in the database."""
    return hashlib.sha256(token.encode("utf-8")).digest()
