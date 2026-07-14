"""Magic-link email delivery via Resend.

Dev fallback: when RESEND_API_KEY is unset the link is logged instead
of sent, so local sign-in works with zero setup (click the logged URL).
Tests can also read ``sent_emails`` (populated in dev mode).
"""
from __future__ import annotations

import logging
import os
from typing import Dict, List

import httpx

logger = logging.getLogger("toneforge.auth")

# Dev-mode capture: list of {"to": ..., "link": ...} dicts.
sent_emails: List[Dict[str, str]] = []

_RESEND_URL = "https://api.resend.com/emails"


def public_base_url() -> str:
    return os.environ.get(
        "TONEFORGE_PUBLIC_BASE_URL", "http://localhost:8000"
    ).rstrip("/")


def magic_link_url(token: str) -> str:
    return f"{public_base_url()}/api/auth/verify?token={token}"


async def send_magic_link(email: str, token: str) -> None:
    """Send (or log, in dev) the sign-in link. Raises on send failure."""
    link = magic_link_url(token)
    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        sent_emails.append({"to": email, "link": link})
        logger.info("auth: dev magic link for %s: %s", email, link)
        return

    mail_from = os.environ.get("TONEFORGE_MAIL_FROM", "signin@jamn.app")
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            _RESEND_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "from": f"ToneForge <{mail_from}>",
                "to": [email],
                "subject": "Sign in to ToneForge",
                "html": (
                    "<p>Click to sign in to ToneForge:</p>"
                    f'<p><a href="{link}">Sign in</a></p>'
                    "<p>This link expires in 15 minutes. If you didn't "
                    "request it, you can ignore this email.</p>"
                ),
            },
        )
        resp.raise_for_status()
    logger.info("auth: magic link sent to %s", email)
