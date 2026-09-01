"""Pad usage store — the kit's feedback loop.

Clients (jamn Kit plugin first) report per-asset play/skip events;
the kit builder folds them into ranking so kits learn what the user
actually reaches for. Storage is a small JSON file per analysis under
~/.toneforge/pad_usage/ (fast local read on the serving path; nothing
here belongs in the R2 history object).

Event kinds:
  play — the pad was launched and held long enough to matter
  skip — launched but killed almost immediately (didn't want it)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_KINDS = ("play", "skip")


def _store_dir() -> Path:
    raw = os.environ.get("TONEFORGE_PAD_USAGE")
    path = Path(raw) if raw else Path.home() / ".toneforge" / "pad_usage"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _path_for(entry_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", entry_id)[:64]
    return _store_dir() / f"{safe}.json"


def load(entry_id: str) -> Dict[str, Dict[str, int]]:
    """{assetId: {"play": n, "skip": n}} — empty dict when no history."""
    try:
        with open(_path_for(entry_id)) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:
        logger.warning("[pad-usage] unreadable store for %s", entry_id)
        return {}


def record(entry_id: str, events: List[Dict]) -> int:
    """Fold client events into the store. Returns events accepted.
    Event shape: {"assetId": str, "kind": "play"|"skip"}."""
    accepted = 0
    with _LOCK:
        usage = load(entry_id)
        for event in events or []:
            if not isinstance(event, dict):
                continue
            asset_id = event.get("assetId")
            kind = event.get("kind")
            if not isinstance(asset_id, str) or not asset_id \
                    or kind not in _KINDS:
                continue
            slot = usage.setdefault(asset_id, {k: 0 for k in _KINDS})
            slot[kind] = int(slot.get(kind, 0)) + 1
            accepted += 1
        if accepted:
            path = _path_for(entry_id)
            tmp = path.with_suffix(".part")
            tmp.write_text(json.dumps(usage))
            tmp.rename(path)
    return accepted


def digest(usage: Optional[Dict]) -> str:
    """Short stable hash of a usage dict — folded into the kit's
    provenance so a usage change re-ranks AND busts the export
    zip cache."""
    if not usage:
        return "0"
    return hashlib.sha1(
        json.dumps(usage, sort_keys=True).encode()).hexdigest()[:8]
