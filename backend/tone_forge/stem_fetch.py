"""Materialize a song's stems as local files for server-side DSP.

Server-local stem paths win (analysis box / dev). On an R2-only serving
box the (already re-presigned) ``stems_paths`` URLs are fetched once each
into the caller's scratch directory. Used by the performance-graph
backfill (``performance.serve.ensure_graph``) and the Ableton kit
exporter — anything that must touch stem audio where only URLs persist.
"""

from __future__ import annotations

import logging
import re
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


def _safe(name: str, limit: int = 24) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "_", name)
    return cleaned[:limit] or "stem"


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
        dest = scratch / f"stem_{_safe(role)}"
        try:
            with urllib.request.urlopen(url, timeout=120) as resp, open(dest, "wb") as f:
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
            out[role] = dest
        except Exception as exc:
            logger.warning("[stem-fetch] fetch failed for %r: %s", role, exc)
    return out
