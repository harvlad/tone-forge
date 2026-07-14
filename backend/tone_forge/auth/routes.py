"""Auth routes: magic link, session, logout.

Mounted at /api/auth. All handlers reach the store via
``request.app.state.auth_store`` (Postgres in prod, memory in dev/tests).
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from . import email_sender, rate_limit
from .deps import SESSION_COOKIE, current_user
from .store import SESSION_TTL, AuthStore
from .tokens import hash_token, new_token

logger = logging.getLogger("toneforge.auth")

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _store(request: Request) -> AuthStore:
    return request.app.state.auth_store


def _client_ip(request: Request) -> Optional[str]:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


def _is_https(request: Request) -> bool:
    return (
        request.url.scheme == "https"
        or request.headers.get("x-forwarded-proto") == "https"
    )


def _set_session_cookie(
    response: Response, request: Request, token: str
) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,
        secure=_is_https(request),
        samesite="lax",
    )


class MagicLinkRequest(BaseModel):
    email: str


@router.post("/magic-link", status_code=202)
async def request_magic_link(body: MagicLinkRequest, request: Request):
    """Always 202 — no account enumeration."""
    email = body.email.strip().lower()
    if "@" not in email or "." not in email.split("@")[-1] or len(email) > 254:
        return JSONResponse(
            {"detail": "Invalid email"}, status_code=422
        )
    allowed, retry_after = rate_limit.check_magic_link(
        email, _client_ip(request)
    )
    if not allowed:
        return JSONResponse(
            {"detail": "Too many requests"},
            status_code=429,
            headers={"Retry-After": str(retry_after)},
        )
    token = new_token()
    await _store(request).create_magic_link(
        email, hash_token(token), requester_ip=_client_ip(request)
    )
    try:
        await email_sender.send_magic_link(email, token)
    except Exception:  # noqa: BLE001 — don't leak delivery status
        logger.exception("auth: magic link send failed for %s", email)
    return {"status": "sent"}


@router.get("/verify")
async def verify_magic_link(token: str, request: Request):
    store = _store(request)
    email = await store.consume_magic_link(hash_token(token))
    if email is None:
        return RedirectResponse("/?auth_error=expired", status_code=303)
    user = await store.upsert_user_by_identity("email", email, email=email)
    session_token = new_token()
    await store.create_session(
        user.id, hash_token(session_token), device_label="web"
    )
    resp = RedirectResponse("/", status_code=303)
    _set_session_cookie(resp, request, session_token)
    return resp


class AppleSignInRequest(BaseModel):
    identity_token: str
    nonce: Optional[str] = None
    device_id: Optional[str] = None
    full_name: Optional[str] = None


@router.post("/apple")
async def apple_sign_in(body: AppleSignInRequest, request: Request):
    from fastapi import HTTPException

    from .apple import AppleVerifyError, verify_identity_token

    try:
        identity = verify_identity_token(body.identity_token, body.nonce)
    except AppleVerifyError as exc:
        logger.warning("auth: apple verify failed: %s", exc)
        raise HTTPException(status_code=401, detail="Invalid identity token")

    store = _store(request)
    user = await store.upsert_user_by_identity(
        "apple",
        identity.subject,
        email=identity.email.lower() if identity.email else None,
        display_name=body.full_name,
    )
    token = new_token()
    await store.create_session(
        user.id, hash_token(token), device_label="ios"
    )
    if body.device_id:
        await store.claim_device(body.device_id, user.id)
    return {
        "token": token,
        "user": {
            "id": user.id,
            "email": user.email,
            "display_name": user.display_name,
        },
    }


@router.get("/session")
async def whoami(request: Request):
    user = await current_user(request)
    if user is None:
        return {"user": None}
    return {
        "user": {
            "id": user.id,
            "email": user.email,
            "display_name": user.display_name,
        }
    }


@router.post("/logout")
async def logout(request: Request):
    from .deps import _session_token

    token = _session_token(request)
    if token:
        await _store(request).delete_session(hash_token(token))
    resp = JSONResponse({"status": "signed_out"})
    resp.delete_cookie(SESSION_COOKIE)
    return resp
