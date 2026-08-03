"""Manufacture — run DI assets through DatasetRecipes with automatic quality gates,
then emit a training manifest. Pure orchestration over M1 (auditor, catalog, license)
and M2 (engine, transforms) + recipes. No new infrastructure.

Gates every manufactured asset must pass (no manual intervention):
  1. license verification  — inherited license present + (for commercial) eligible
  2. provenance verification — ingest root + derived_from chain intact
  3. lineage verification    — the recipe step is recorded
  4. Dataset Auditor         — verdict != REJECT
Assets failing any gate are rejected and never enter the manifest.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .. import training_data
from .asset import Asset, Status
from .catalog import AssetCatalog
from .engine import TransformEngine
from .pipeline import PipelineRunner
from .recipes import DatasetRecipe, apply_recipe


@dataclass
class ManufactureReport:
    admitted: list = field(default_factory=list)
    rejected: list = field(default_factory=list)   # (asset, reasons)
    def counts(self) -> dict:
        return {"admitted": len(self.admitted), "rejected": len(self.rejected)}
    def by_recipe(self) -> dict:
        out: dict = {}
        for a in self.admitted:
            out.setdefault(a.metadata.get("recipe", "?"), 0)
            out[a.metadata["recipe"]] += 1
        return out


def _quality_gates(asset: Asset, *, require_commercial: bool) -> tuple[bool, list]:
    reasons = []
    # 1. license
    if asset.license is None:
        reasons.append("no license stamp")
    elif require_commercial and not asset.license.commercial_training_allowed:
        reasons.append(f"license {asset.license.license} not commercial-eligible")
    # 2. provenance
    if not asset.content_hash:
        reasons.append("no content hash")
    if not (asset.lineage and asset.lineage[0].stage == "ingest"):
        reasons.append("lineage does not root at ingest")
    if "derived_from" not in asset.provenance:
        reasons.append("no derived_from provenance (not a manufactured asset)")
    # 3. lineage records the recipe
    if not any(s.stage.startswith("recipe:") for s in asset.lineage):
        reasons.append("no recipe step in lineage")
    # 4. auditor
    if asset.audit_status == Status.REJECT:
        reasons.append(f"auditor REJECT: {asset.audit.get('reasons') if asset.audit else '?'}")
    return (len(reasons) == 0, reasons)


def manufacture(engine: TransformEngine, runner: PipelineRunner, catalog: AssetCatalog,
                di_assets: list, recipes: list, *, require_commercial: bool = True) -> ManufactureReport:
    """For every (DI asset x recipe): apply the recipe, audit, gate, catalog."""
    report = ManufactureReport()
    for di in di_assets:
        for recipe in recipes:
            made = apply_recipe(engine, di, recipe)   # deterministic; cache-reproducible
            made = runner.audit(made)                  # Dataset Auditor stage
            ok, reasons = _quality_gates(made, require_commercial=require_commercial)
            if not ok:
                report.rejected.append((made, reasons))
                continue
            report.admitted.append(catalog.add(made))
    return report


def build_manufactured_manifest(catalog: AssetCatalog, name: str, out_dir: Path, *,
                                intended_use: str = "commercial_eligible") -> Path:
    """Emit a training manifest from all catalog assets that carry a recipe tag and
    passed audit. Reuses training_data.build_manifest (the license gate).

    NOTE: these are guitar TARGET assets — `mixture_path` is null because no mixture
    exists yet. A separation trainer additionally needs mixtures (the Mix Generator,
    a later milestone). The manifest is emitted so the target side is training-ready
    and the mixture requirement is explicit, not silently missing.
    """
    tracks = []
    for a in catalog.all():
        if a.metadata.get("recipe") and a.audit_status in (Status.PASS, Status.REVIEW):
            tracks.append({
                "dataset": a.dataset_key,
                "track_id": a.asset_id,
                "target_stem": "guitar",
                "target_path": a.path,
                "mixture_path": None,           # <- requires Mix Generator (later milestone)
                "recipe": a.metadata["recipe"],
                "recipe_version": a.metadata.get("recipe_version"),
                "content_hash": a.content_hash,
                "quality_score": a.quality_score,
            })
    return training_data.build_manifest(name, tracks, intended_use=intended_use, out_dir=out_dir)
