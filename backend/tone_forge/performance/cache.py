"""Content-addressed cache for the MusicalGraph.

Mirrors the lab/cache identity model (content_hash + module_version + config
hash) but stores JSON (the graph is small structured data, not tensors). An
identical input yields an identical ``graph_hash`` → we never regenerate. Writes
are atomic and carry a provenance sidecar; nothing is silently deleted.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Optional

from .graph import MODULE_ID, MODULE_VERSION, MusicalGraph

_DEFAULT_ROOT = os.environ.get(
    "TONEFORGE_PERF_CACHE",
    str(Path(__file__).resolve().parents[2] / "data" / "performance_cache"),
)


def cache_key(content_hash: str, module_version: str, config_hash: str) -> str:
    from .graph import short  # reuse hashing.short

    return short(config_hash_str := __import__("hashlib").sha256(
        f"{content_hash}:{module_version}:{config_hash}".encode()
    ).hexdigest())


class GraphCache:
    def __init__(self, root: Optional[str] = None):
        self.root = Path(root or _DEFAULT_ROOT)

    def _path(self, key: str) -> Path:
        return self.root / f"{key}.json"

    def load(self, content_hash: str, config_hash: str) -> Optional[dict]:
        key = cache_key(content_hash, MODULE_VERSION, config_hash)
        p = self._path(key)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text())
        except Exception:
            return None

    def store(self, graph: MusicalGraph) -> str:
        key = cache_key(graph.content_hash, graph.module_version, graph.config_hash)
        self.root.mkdir(parents=True, exist_ok=True)
        payload = {
            "provenance": {
                "module_id": MODULE_ID,
                "module_version": graph.module_version,
                "content_hash": graph.content_hash,
                "config_hash": graph.config_hash,
                "graph_hash": graph.graph_hash,
            },
            "graph": graph.to_dict(),
        }
        p = self._path(key)
        # atomic write
        fd, tmp = tempfile.mkstemp(dir=str(self.root), suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(payload, f, separators=(",", ":"))
            os.replace(tmp, p)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        return key
