"""Content hashing + canonical config hashing for cache identity."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def file_sha256(path: Path, chunk_size: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def canonical_json(obj) -> str:
    """Deterministic JSON: sorted keys, no whitespace variance, floats via
    repr through json default handling."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def config_hash(obj) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def short(h: str, n: int = 20) -> str:
    return h[:n]
