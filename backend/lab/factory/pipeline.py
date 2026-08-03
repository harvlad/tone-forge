"""PipelineRunner — the first production pipeline (Milestone 1).

    Source  ->  Asset  ->  License verification  ->  Dataset Auditor  ->  Catalog

Each stage produces a NEW immutable Asset (lineage appended). Reuses the existing
gates rather than reimplementing them:
  - license verification  -> lab.training_data (registry is the source of truth)
  - audit + metadata      -> lab.dataset_auditor

Augmentation / mix generation / coverage / commissioning / GPU are NOT here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .. import training_data
from ..dataset_auditor import audit_track, audit_to_dict, AuditThresholds
from .asset import Asset, LicenseStamp, Role, Status, _utcnow
from .catalog import AssetCatalog
from .sources import SourceProvider

# Descriptive regime tags kept in Asset.metadata (disjoint from audit metrics).
_METADATA_TAGS = ("genre", "guitar_type", "gain", "pickup", "tempo", "key",
                  "vocal_density", "masking_score", "difficulty",
                  "recording_type", "synthetic_real")
# Objective quality metrics kept in Asset.audit.
_AUDIT_METRICS = ("verdict", "reasons", "guitar_rms", "occupancy", "clipping_frac",
                  "silence_frac", "dynamic_range_db", "leakage", "hf_ratio",
                  "lf_ratio", "rolloff_hz", "label_ok", "is_duplicate", "dup_of",
                  "estimated_fields")


@dataclass
class IngestResult:
    admitted: list        # audit PASS
    review: list          # audit REVIEW
    rejected: list        # license or audit REJECT
    def counts(self) -> dict:
        return {"admitted": len(self.admitted), "review": len(self.review),
                "rejected": len(self.rejected)}


def _quality_status(score: Optional[float]) -> str:
    if score is None:
        return Status.PENDING
    if score >= 0.8:
        return Status.GOOD
    if score >= 0.5:
        return Status.ACCEPTABLE
    return Status.POOR


class PipelineRunner:
    def __init__(self, catalog: AssetCatalog, thresholds: AuditThresholds = AuditThresholds()):
        self.catalog = catalog
        self.th = thresholds

    # ---- stage 2: license verification ----
    def verify_license(self, asset: Asset) -> Asset:
        try:
            facts = training_data.dataset_facts(asset.dataset_key)
        except training_data.ProvenanceError:
            # unregistered dataset -> hard reject; nothing unverified proceeds
            return asset.evolve(stage="license:unregistered", audit_status=Status.REJECT,
                                params={"reason": "dataset not in provenance registry"})
        stamp = LicenseStamp(
            dataset_key=asset.dataset_key, license=facts["license"],
            commercial_training_allowed=bool(facts["commercial_training_allowed"]),
            redistribution_allowed=bool(facts.get("redistribution_allowed", False)),
            verified_at=_utcnow())
        return asset.evolve(stage="license", license=stamp,
                            params={"license": stamp.license,
                                    "commercial": stamp.commercial_training_allowed})

    # ---- stage 3+4+5: audit -> metadata + quality ----
    def audit(self, asset: Asset) -> Asset:
        tags = asset.metadata  # source-provided tags override audio proxies where present
        try:
            ta = audit_track(
                asset.path, dataset=asset.dataset_key, track_id=asset.asset_id,
                genre=tags.get("genre", "unknown"), pickup=tags.get("pickup", "unknown"),
                recording_type=tags.get("recording_type", "unknown"),
                synthetic_real=tags.get("synthetic_real", "unknown"),
                guitar_type=tags.get("guitar_type"), gain=tags.get("gain"),
                expect_guitar=(asset.role == Role.GUITAR),   # backing isn't guitar-shaped
                th=self.th)
        except Exception as e:
            # a single unreadable/corrupt file must never kill a batch (worker discipline)
            return asset.evolve(stage="audit:error", audit_status=Status.REJECT,
                                audit={"reasons": [f"audit error: {type(e).__name__}: {e}"]},
                                params={"error": str(e)[:200]})
        d = audit_to_dict(ta)
        audit_metrics = {k: d[k] for k in _AUDIT_METRICS if k in d}
        metadata = dict(asset.metadata)
        metadata.update({k: d[k] for k in _METADATA_TAGS if k in d})
        return asset.evolve(
            stage="audit", audit=audit_metrics, metadata=metadata,
            audit_status=ta.verdict, quality_score=ta.quality_score,
            quality_status=_quality_status(ta.quality_score),
            params={"verdict": ta.verdict, "quality_score": ta.quality_score})

    # ---- run one asset all the way through ----
    def process(self, asset: Asset) -> Asset:
        asset = self.verify_license(asset)
        if asset.audit_status == Status.REJECT:            # license rejected -> catalog + stop
            return self.catalog.add(asset.evolve(stage="catalog"))
        asset = self.audit(asset)
        return self.catalog.add(asset.evolve(stage="catalog"))

    # ---- run a whole source ----
    def ingest(self, provider: SourceProvider) -> IngestResult:
        if not provider.health():
            raise RuntimeError(f"source '{provider.id}' unhealthy (root missing?)")
        res = IngestResult([], [], [])
        for raw in provider.iter_assets():
            asset = Asset.ingest(raw, source_id=provider.id, dataset_key=provider.dataset_key)
            stored = self.process(asset)
            if stored.audit_status == Status.PASS:
                res.admitted.append(stored)
            elif stored.audit_status == Status.REVIEW:
                res.review.append(stored)
            else:
                res.rejected.append(stored)
        return res
