"""In-memory sliding-window rate limiter for magic-link requests.

Single-process deploy, so in-memory is fine. Limits: 3 requests per
15 min per email, 10 per 15 min per IP. State is two dicts of
timestamp lists, pruned on access.
"""
from __future__ import annotations

import time
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

WINDOW_SEC = 15 * 60
EMAIL_LIMIT = 3
IP_LIMIT = 10

_email_hits: Dict[str, List[float]] = defaultdict(list)
_ip_hits: Dict[str, List[float]] = defaultdict(list)


def _prune(hits: List[float], now: float) -> None:
    cutoff = now - WINDOW_SEC
    while hits and hits[0] <= cutoff:
        hits.pop(0)


def _check(
    hits: Dict[str, List[float]], key: str, limit: int, now: float
) -> Optional[int]:
    """Retry-After seconds when over limit, else None (and records hit)."""
    bucket = hits[key]
    _prune(bucket, now)
    if len(bucket) >= limit:
        return max(1, int(bucket[0] + WINDOW_SEC - now) + 1)
    bucket.append(now)
    return None


def check_magic_link(
    email: str, ip: Optional[str]
) -> Tuple[bool, Optional[int]]:
    """(allowed, retry_after_seconds). Records the attempt when allowed."""
    now = time.time()
    retry = _check(_email_hits, email.lower(), EMAIL_LIMIT, now)
    if retry is not None:
        return False, retry
    if ip:
        retry = _check(_ip_hits, ip, IP_LIMIT, now)
        if retry is not None:
            return False, retry
    return True, None


def reset() -> None:
    """Test hook."""
    _email_hits.clear()
    _ip_hits.clear()
