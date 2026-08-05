"""AssetCatalog — Riley's source of truth for admitted assets.

A durable, queryable store keyed by the content-based `asset_id` (so re-ingesting
identical audio updates in place rather than duplicating). Persisted as JSONL,
mirroring the discipline of experiments.jsonl. The catalog stores Asset RECORDS
(which reference `dataset_key` for license and a `path` for audio) — it never
copies the license registry or the audio itself.

Lookups required by Milestone 1: by id, source, license, quality, tags; plus
lineage inspection.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Optional

from .. import config
from .asset import Asset


class AssetCatalog:
    def __init__(self, path: str | Path | None = None, *, autoflush: bool = True):
        self.path = Path(path) if path is not None else (config.FACTORY_DIR / "catalog.jsonl")
        self._by_id: dict[str, Asset] = {}
        # autoflush=True keeps the crash-safe per-add persistence used incrementally;
        # set False (or use extend()) for O(n) bulk loads — one flush instead of n.
        self._autoflush = autoflush
        self._load()

    # ---- persistence ----
    def _load(self) -> None:
        if self.path.exists():
            for line in self.path.read_text().splitlines():
                line = line.strip()
                if line:
                    a = Asset.from_dict(json.loads(line))
                    self._by_id[a.asset_id] = a

    def _flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with open(tmp, "w") as f:
            for a in self._by_id.values():
                f.write(json.dumps(a.to_dict(), default=str) + "\n")
        tmp.replace(self.path)   # atomic

    # ---- write ----
    def add(self, asset: Asset, *, flush: Optional[bool] = None) -> Asset:
        """Insert or replace by content id (natural dedup). Returns the stored asset.
        Rewriting the whole JSONL per add is O(n²) at scale — pass flush=False (or use
        extend()/flush()) for bulk loads."""
        self._by_id[asset.asset_id] = asset
        if self._autoflush if flush is None else flush:
            self._flush()
        return asset

    def extend(self, assets: Iterable[Asset]) -> int:
        """Bulk insert/replace with a SINGLE flush — O(n) instead of O(n²)."""
        n = 0
        for a in assets:
            self._by_id[a.asset_id] = a
            n += 1
        self._flush()
        return n

    def flush(self) -> None:
        """Persist now (for use with add(flush=False) / autoflush=False loops)."""
        self._flush()

    # ---- lookups ----
    def get(self, asset_id: str) -> Optional[Asset]:
        return self._by_id.get(asset_id)

    def all(self) -> list:
        return list(self._by_id.values())

    def by_source(self, source_id: str) -> list:
        return [a for a in self._by_id.values() if a.source_id == source_id]

    def by_license(self, *, commercial_only: Optional[bool] = None,
                   license: Optional[str] = None) -> list:
        out = []
        for a in self._by_id.values():
            st = a.license
            if st is None:
                continue
            if commercial_only is not None and st.commercial_training_allowed != commercial_only:
                continue
            if license is not None and st.license != license:
                continue
            out.append(a)
        return out

    def by_quality(self, *, status: Optional[str] = None,
                   min_score: Optional[float] = None) -> list:
        out = []
        for a in self._by_id.values():
            if status is not None and a.quality_status != status:
                continue
            if min_score is not None and not (a.quality_score is not None and a.quality_score >= min_score):
                continue
            out.append(a)
        return out

    def by_audit_status(self, status: str) -> list:
        return [a for a in self._by_id.values() if a.audit_status == status]

    def by_tag(self, key: str, value) -> list:
        return [a for a in self._by_id.values() if a.metadata.get(key) == value]

    def lineage(self, asset_id: str) -> list:
        """The ordered transform chain for an asset (ingest -> ... )."""
        a = self._by_id.get(asset_id)
        return list(a.lineage) if a else []

    def __len__(self) -> int:
        return len(self._by_id)
