"""TransformEngine — runs TransformProviders over Assets, with lineage + cache.

Owns everything an Asset needs so transforms stay pure DSP:
  - canonicalizes params (stable cache key)
  - content-addressable cache: (parent content_hash + transform id + version +
    canonical params + seed) -> output. Identical work is never recomputed.
  - writes the output to an input-addressed path, hashes it, and derives a NEW
    immutable child Asset (Asset.derive) with a full lineage step.
  - catalogs the child.

Reuses the M1 Catalog and Asset; no metadata or storage duplicated.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .. import config
from .asset import Asset
from .catalog import AssetCatalog
from .transforms import TransformProvider

_SOFTWARE_VERSION = "factory-engine/1"


def _cache_key(parent_hash: str, tid: str, tver: str, cparams: dict, seed: Optional[int]) -> str:
    payload = json.dumps({"p": parent_hash, "t": tid, "v": tver, "params": cparams, "seed": seed},
                         sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


@dataclass
class EngineStats:
    computed: int = 0        # transforms actually run
    cache_hits: int = 0      # transforms served from cache


class TransformEngine:
    def __init__(self, catalog: AssetCatalog,
                 store_dir: str | Path | None = None,
                 cache_index: str | Path | None = None):
        self.catalog = catalog
        self.store_dir = Path(store_dir) if store_dir else (config.FACTORY_DIR / "transformed")
        self.cache_index_path = Path(cache_index) if cache_index else (config.FACTORY_DIR / "transform_cache.jsonl")
        self.stats = EngineStats()
        self._cache: dict[str, str] = {}       # cache_key -> output asset_id
        self._load_cache()

    def _load_cache(self) -> None:
        if self.cache_index_path.exists():
            for line in self.cache_index_path.read_text().splitlines():
                if line.strip():
                    rec = json.loads(line)
                    self._cache[rec["key"]] = rec["asset_id"]

    def _record_cache(self, key: str, asset_id: str, out_path: Path) -> None:
        self._cache[key] = asset_id
        self.cache_index_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.cache_index_path, "a") as f:
            f.write(json.dumps({"key": key, "asset_id": asset_id, "path": str(out_path)}) + "\n")

    def run(self, asset: Asset, transform: TransformProvider,
            params: Optional[dict] = None, seed: Optional[int] = None) -> Asset:
        cparams = transform.canonical_params(params or {})
        key = _cache_key(asset.content_hash, transform.id, transform.version, cparams, seed)

        # --- cache hit: reuse without recomputing ---
        cached_id = self._cache.get(key)
        if cached_id is not None:
            existing = self.catalog.get(cached_id)
            if existing is not None and Path(existing.path).exists():
                self.stats.cache_hits += 1
                return existing

        # --- compute ---
        out_path = self.store_dir / key[:2] / f"{key}.wav"
        extra = transform.apply(Path(asset.path), out_path, cparams, seed)
        self.stats.computed += 1

        lineage_params = {
            "transform": transform.id,
            "transform_version": transform.version,
            "params": cparams,
            "seed": seed,
            "software": _SOFTWARE_VERSION,
            "extra": extra,
        }
        child = asset.derive(out_path, stage=f"transform:{transform.id}", params=lineage_params)
        stored = self.catalog.add(child)
        self._record_cache(key, stored.asset_id, out_path)
        return stored

    def run_chain(self, asset: Asset, steps: list) -> Asset:
        """Apply an ordered list of (transform, params[, seed]) tuples, cataloging
        each intermediate. Returns the final Asset."""
        current = asset
        for step in steps:
            transform = step[0]
            params = step[1] if len(step) > 1 else None
            seed = step[2] if len(step) > 2 else None
            current = self.run(current, transform, params, seed)
        return current
