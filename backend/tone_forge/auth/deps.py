"""FastAPI dependencies for session auth.

Per-route dependencies (not middleware) so the existing admin/engine
guards are untouched and anonymous access keeps working everywhere a
route opts in with ``Depends(current_user)``.
"""
from __future__ import annotations

from typing import Optional

from fastapi import Depends, HTTPException, Request

from .store import AuthUser
from .tokens import hash_token

SESSION_COOKIE = "toneforge_session"


def _bearer_token(request: Request) -> Optional[str]:
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return header[len("Bearer "):].strip() or None
    return None


def _session_token(request: Request) -> Optional[str]:
    return request.cookies.get(SESSION_COOKIE) or _bearer_token(request)


async def current_user(request: Request) -> Optional[AuthUser]:
    """The signed-in user, or None. Never raises."""
    token = _session_token(request)
    if not token:
        return None
    store = getattr(request.app.state, "auth_store", None)
    if store is None:
        return None
    try:
        return await store.session_user(hash_token(token))
    except Exception:  # noqa: BLE001 — auth must never take a route down
        return None


async def require_user(
    user: Optional[AuthUser] = Depends(current_user),
) -> AuthUser:
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in required")
    return user
