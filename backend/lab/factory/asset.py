"""The Asset — the factory's immutable atomic unit.

Identity is content-based: `content_hash` = sha256 of the audio bytes, and
`asset_id` = a deterministic UUID5 over that hash. Re-ingesting the same audio
yields the same id (natural dedup); nothing downstream references raw files
directly — always the Asset.

Assets are FROZEN. Every pipeline stage produces a NEW Asset via `evolve(...)`,
which appends a lineage record and never mutates the parent. The original is
always recoverable by walking `lineage` back to the ingest record.

Field responsibilities are disjoint to avoid duplicated metadata:
  - provenance  : where it came from (source, dataset, original path, when)
  - lineage     : the ordered transform chain (ingest, then future augment steps)
  - license     : the verification STAMP produced by the license stage
                  (facts live authoritatively in lab.training_data; this is the
                  timestamped result of checking against it, not a second copy)
  - audit       : objective quality metrics + verdict from the Dataset Auditor
  - metadata    : descriptive REGIME tags (genre, guitar_type, tempo, key, ...)
  - quality_*   : the admit signal derived from the audit's quality_score
Each concrete value appears in exactly one of these.
"""
from __future__ import annotations

import dataclasses
import datetime
import hashlib
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Mapping, Optional

# Stable namespace so asset ids are reproducible across machines/runs.
_ASSET_NS = uuid.UUID("d9f1a4e2-0b7c-5a3e-9c21-8f5e6d7c9a0b")


class Kind:
    STEM = "stem"          # isolated single instrument
    MIXTURE = "mixture"    # multi-instrument mix
    DI = "di"              # clean direct-input guitar
    IR = "ir"              # cabinet impulse response
    CAPTURE = "capture"    # amp/pedal capture (e.g. NAM)
    BACKING = "backing"    # accompaniment bed (no guitar)


class Role:
    GUITAR = "guitar"
    VOCALS = "vocals"
    DRUMS = "drums"
    BASS = "bass"
    KEYS = "keys"
    OTHER = "other"
    MIX = "mix"
    NONE = "none"


class Status:
    PENDING = "pending"
    # audit verdicts — MUST match lab.dataset_auditor's constants (uppercase)
    PASS = "PASS"
    REVIEW = "REVIEW"
    REJECT = "REJECT"
    # quality buckets (factory-defined)
    GOOD = "good"
    ACCEPTABLE = "acceptable"
    POOR = "poor"


def _utcnow() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def content_hash_file(path: str | Path, chunk: int = 1 << 20) -> str:
    """sha256 of a file's bytes — the content identity."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def asset_id_for(content_hash: str) -> str:
    """Deterministic, content-based UUID5. Same bytes -> same id."""
    return str(uuid.uuid5(_ASSET_NS, content_hash))


@dataclass(frozen=True)
class RawAsset:
    """What a SourceProvider yields — a file plus what the source knows about it,
    before any hashing, verification, or auditing."""
    audio_path: str
    kind: str = Kind.STEM
    role: str = Role.GUITAR
    source_tags: Mapping[str, object] = field(default_factory=dict)  # genre/tuning/etc if known


@dataclass(frozen=True)
class LicenseStamp:
    """Result of the license-verification stage. The authoritative facts live in
    lab.training_data.TRAINING_DATA_REGISTRY; this records the outcome + when."""
    dataset_key: str
    license: str
    commercial_training_allowed: bool
    redistribution_allowed: bool
    verified_at: str


@dataclass(frozen=True)
class LineageStep:
    stage: str                       # "ingest" | "license" | "audit" | "catalog" | (future) "augment:reamp" ...
    at: str                          # utc iso
    parent_asset_id: Optional[str] = None
    params: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class Asset:
    # ---- identity ----
    asset_id: str
    content_hash: str
    path: str
    kind: str
    role: str
    # ---- provenance (origin) ----
    source_id: str
    dataset_key: str
    provenance: Mapping[str, object]          # {source_id, dataset_key, original_path, ingested_at}
    lineage: tuple                            # tuple[LineageStep] — transform chain, [0] == ingest
    # ---- verification + analysis (populated by later stages) ----
    license: Optional[LicenseStamp] = None
    audit: Optional[Mapping[str, object]] = None      # objective metrics + verdict (no descriptive tags)
    metadata: Mapping[str, object] = field(default_factory=dict)  # descriptive regime tags
    audit_status: str = Status.PENDING
    quality_status: str = Status.PENDING
    quality_score: Optional[float] = None
    created_at: str = field(default_factory=_utcnow)
    updated_at: str = field(default_factory=_utcnow)

    # ---- construction ----
    @classmethod
    def ingest(cls, raw: RawAsset, *, source_id: str, dataset_key: str) -> "Asset":
        """Stage 1: hash the file, mint a content-based id, open the lineage."""
        ch = content_hash_file(raw.audio_path)
        aid = asset_id_for(ch)
        now = _utcnow()
        prov = {"source_id": source_id, "dataset_key": dataset_key,
                "original_path": str(raw.audio_path), "ingested_at": now}
        step = LineageStep(stage="ingest", at=now, parent_asset_id=None,
                           params={"source_id": source_id, "kind": raw.kind, "role": raw.role})
        return cls(
            asset_id=aid, content_hash=ch, path=str(raw.audio_path),
            kind=raw.kind, role=raw.role,
            source_id=source_id, dataset_key=dataset_key,
            provenance=prov, lineage=(step,),
            metadata=dict(raw.source_tags), created_at=now, updated_at=now,
        )

    def evolve(self, *, stage: str, params: Optional[dict] = None, **changes) -> "Asset":
        """Immutable in-place-identity update: append a lineage step and apply field
        changes while KEEPING the same audio (same content_hash/asset_id). Used by
        stages that annotate an asset (license, audit). Returns a NEW Asset."""
        now = _utcnow()
        step = LineageStep(stage=stage, at=now, parent_asset_id=self.asset_id, params=params or {})
        return replace(self, lineage=self.lineage + (step,), updated_at=now, **changes)

    def derive(self, new_path: str | Path, *, stage: str, params: Optional[dict] = None,
               kind: Optional[str] = None, role: Optional[str] = None) -> "Asset":
        """Create a CHILD asset from transformed audio at `new_path`.

        The child has a NEW content identity (hash of the new bytes), inherits the
        parent's license and descriptive metadata (a re-amp of a CC-BY DI is still a
        CC-BY derivative; guitar_type/genre carry forward), and RESETS audit/quality
        (new audio must be re-audited before it can enter a manifest). Lineage gains
        one step linking back to the parent, so the transform chain is fully
        replayable. The parent is untouched; the original is always recoverable."""
        ch = content_hash_file(new_path)
        aid = asset_id_for(ch)
        now = _utcnow()
        step = LineageStep(stage=stage, at=now, parent_asset_id=self.asset_id, params=params or {})
        prov = {**dict(self.provenance), "derived_from": self.asset_id, "derived_at": now}
        return replace(
            self, asset_id=aid, content_hash=ch, path=str(new_path),
            kind=kind or self.kind, role=role or self.role,
            provenance=prov, lineage=self.lineage + (step,),
            audit=None, audit_status=Status.PENDING,
            quality_status=Status.PENDING, quality_score=None,
            created_at=now, updated_at=now,
        )

    # ---- serialization (for the catalog) ----
    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["lineage"] = [dataclasses.asdict(s) for s in self.lineage]
        d["license"] = dataclasses.asdict(self.license) if self.license else None
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Asset":
        d = dict(d)
        d["lineage"] = tuple(LineageStep(**s) for s in d.get("lineage", []))
        d["license"] = LicenseStamp(**d["license"]) if d.get("license") else None
        return cls(**d)
