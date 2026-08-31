"""Materialize a song's stems as local files for server-side DSP.

Server-local stem paths win (analysis box / dev). On an R2-only serving
box the (already re-presigned) ``stems_paths`` URLs are fetched once each
into the caller's scratch directory. Used by the performance-graph
backfill (``performance.serve.ensure_graph``) and the Ableton kit
exporter — anything that must touch stem audio where only URLs persist.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)


def _safe(name: str, limit: int = 24) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "_", name)
    return cleaned[:limit] or "stem"


def _cache_dir() -> Optional[Path]:
    """Disk cache for fetched stems. Presigned R2 URLs change per
    request, so the key is the URL *path* (bucket object), not the full
    URL. Re-exports of the same song then skip the multi-30MB downloads
    entirely — the dominant cost of /ableton-kit. Set
    TONEFORGE_STEM_CACHE=0 to disable (tests set a tmp dir)."""
    raw = os.environ.get("TONEFORGE_STEM_CACHE")
    if raw == "0":
        return None
    path = Path(raw) if raw else Path.home() / ".toneforge" / "stem_cache"
    try:
        path.mkdir(parents=True, exist_ok=True)
        return path
    except Exception:
        return None


def materialize_stems(
    result: Dict,
    scratch: Path,
    roles: Optional[List[str]] = None,
) -> Dict[str, Path]:
    """Local file per stem role. ``roles=None`` = every role that has a
    source. Missing/failed roles are dropped, never raised."""
    from tone_forge.performance.builder import _stem_paths_of

    local = _stem_paths_of(result) or {}
    urls = result.get("stems_paths")
    urls = urls if isinstance(urls, dict) else {}

    wanted = roles if roles is not None else sorted(set(local) | set(urls))

    out: Dict[str, Path] = {}
    for role in wanted:
        lp = local.get(role)
        if lp and Path(lp).exists():
            out[role] = Path(lp)
            continue
        url = urls.get(role)
        if not isinstance(url, str) or not url.startswith("http"):
            logger.warning("[stem-fetch] no audio source for stem %r", role)
            continue

        cache = _cache_dir()
        cached: Optional[Path] = None
        if cache is not None:
            key = hashlib.sha1(urlsplit(url).path.encode()).hexdigest()
            cached = cache / f"{key}.audio"
            if cached.exists() and cached.stat().st_size > 0:
                out[role] = cached
                continue

        dest = cached if cached is not None else scratch / f"stem_{_safe(role)}"
        tmp = dest.with_suffix(dest.suffix + ".part")
        try:
            with urllib.request.urlopen(url, timeout=120) as resp, open(tmp, "wb") as f:
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
            tmp.rename(dest)
            out[role] = dest
        except Exception as exc:
            tmp.unlink(missing_ok=True)
            logger.warning("[stem-fetch] fetch failed for %r: %s", role, exc)
    return out
