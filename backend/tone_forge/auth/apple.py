"""Sign in with Apple: identity-token verification.

Verifies the RS256 identity token from ASAuthorization against Apple's
JWKS. Audience must be one of our bundle ids
(TONEFORGE_APPLE_BUNDLE_IDS, comma-separated). Nonce binding: client
sends the raw nonce alongside the token; the token carries
sha256(raw nonce) hex.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Optional

import jwt
from jwt import PyJWKClient

APPLE_JWKS_URL = "https://appleid.apple.com/auth/keys"
APPLE_ISSUER = "https://appleid.apple.com"

_DEFAULT_BUNDLE_IDS = "com.harvlad.toneforge.mobile"

# Lazily-created, cached JWKS client (keeps Apple's keys warm).
_jwk_client: Optional[PyJWKClient] = None


class AppleVerifyError(Exception):
    """Any verification failure — caller maps to 401."""


@dataclass(frozen=True)
class AppleIdentity:
    subject: str  # Apple's stable `sub`
    email: Optional[str]  # may be private-relay address, may be absent


def _bundle_ids() -> list[str]:
    raw = os.environ.get("TONEFORGE_APPLE_BUNDLE_IDS", _DEFAULT_BUNDLE_IDS)
    return [b.strip() for b in raw.split(",") if b.strip()]


def _get_jwk_client() -> PyJWKClient:
    global _jwk_client
    if _jwk_client is None:
        _jwk_client = PyJWKClient(APPLE_JWKS_URL)
    return _jwk_client


def verify_identity_token(
    identity_token: str, nonce: Optional[str] = None
) -> AppleIdentity:
    """Verify signature, issuer, audience, expiry, and (optional) nonce."""
    try:
        signing_key = _get_jwk_client().get_signing_key_from_jwt(
            identity_token
        )
        claims = jwt.decode(
            identity_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=_bundle_ids(),
            issuer=APPLE_ISSUER,
        )
    except Exception as exc:  # noqa: BLE001 — collapse all jwt errors
        raise AppleVerifyError(str(exc)) from exc

    if nonce is not None:
        expected = hashlib.sha256(nonce.encode("utf-8")).hexdigest()
        token_nonce = claims.get("nonce")
        if token_nonce != expected and token_nonce != nonce:
            raise AppleVerifyError("nonce mismatch")

    sub = claims.get("sub")
    if not sub:
        raise AppleVerifyError("missing sub")
    return AppleIdentity(subject=sub, email=claims.get("email"))
