"""Milestone 3, Steps 1-4: manufacture the first dataset through the factory.

12 GuitarSet-style DI recordings x 4 versioned DatasetRecipes -> audited, gated,
reproducible manufactured assets + a training manifest. (Step 5 training needs
mixtures = the Mix Generator, a later milestone — see the report at the end.)

Runs under the audio-capable env. Executable directly or via pytest.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_data_factory import _write_guitarish  # noqa: E402


def run() -> None:
    from lab.factory import (AssetCatalog, PipelineRunner, TransformEngine,
                             GuitarSetSource, all_recipes, manufacture,
                             build_manufactured_manifest, Status)

    tmp = Path(tempfile.mkdtemp(prefix="mfg_test_"))
    os.environ.setdefault("TONEFORGE_LAB_DATA", str(tmp / "labdata"))
    di_dir = tmp / "guitarset"
    N_DI = 12
    for i in range(N_DI):
        _write_guitarish(di_dir / f"di_{i:02d}.wav", seed=100 + i)

    catalog = AssetCatalog(path=tmp / "catalog.jsonl")
    runner = PipelineRunner(catalog)
    engine = TransformEngine(catalog, store_dir=tmp / "xform", cache_index=tmp / "cache.jsonl")

    # --- ingest DI through the M1 pipeline (license + audit) ---
    di_res = runner.ingest(GuitarSetSource(di_dir))
    di_assets = di_res.admitted + di_res.review
    assert len(di_assets) == N_DI, f"expected {N_DI} DI ingested, got {di_res.counts()}"
    for d in di_assets:
        assert d.license and d.license.commercial_training_allowed  # GuitarSet is green-tier

    # --- manufacture: 12 DI x 4 recipes, gated ---
    recipes = all_recipes()
    report = manufacture(engine, runner, catalog, di_assets, recipes, require_commercial=True)
    print("manufacture:", report.counts(), "| by_recipe:", report.by_recipe())
    assert len(report.admitted) == N_DI * len(recipes), report.counts()
    assert len(report.rejected) == 0, [r for _, r in report.rejected][:3]

    # --- every manufactured asset passes all four gates ---
    for a in report.admitted:
        assert a.license and a.license.commercial_training_allowed          # license
        assert a.provenance.get("derived_from") and a.content_hash          # provenance
        assert any(s.stage.startswith("recipe:") for s in a.lineage)        # lineage records recipe
        assert a.audit_status in (Status.PASS, Status.REVIEW)               # auditor
        assert a.metadata.get("recipe") and a.metadata.get("recipe_version")

    # --- controlled diversity: all 4 recipes represented ---
    assert set(report.by_recipe()) == {"clean_twin", "british_crunch", "modern_metal", "acoustic_natural"}
    gtypes = {a.metadata.get("guitar_type") for a in report.admitted}
    assert {"clean", "distorted", "acoustic"} <= gtypes, gtypes

    # --- reproducibility: re-manufacture -> engine cache serves it, no new catalog assets ---
    n_assets_before = len(catalog)
    computed_before = engine.stats.computed
    ids_before = {a.asset_id for a in report.admitted}
    report2 = manufacture(engine, runner, catalog, di_assets, recipes)
    assert engine.stats.computed == computed_before, "re-manufacture must hit the transform cache"
    assert {a.asset_id for a in report2.admitted} == ids_before, "identical recipes -> identical asset ids"
    assert len(catalog) == n_assets_before, "no duplicate assets on replay"

    # --- Step 4: training manifest via the existing (license-gated) builder ---
    manifest_path = build_manufactured_manifest(catalog, "riley_mfg_v1", tmp / "manifests",
                                                intended_use="commercial_eligible")
    import json
    manifest = json.loads(manifest_path.read_text())
    assert manifest["intended_use"] == "commercial_eligible"
    assert manifest["n_tracks"] == N_DI * len(recipes)
    assert manifest["dataset_lineage"]["guitarset"]["commercial_training_allowed"] is True
    assert all(t["mixture_path"] is None for t in manifest["tracks"])   # honest: mixtures not yet generated
    assert manifest.get("manifest_sha256")

    print(f"OK — manufactured {len(report.admitted)} audited/gated assets from {N_DI} DI "
          f"across {len(recipes)} versioned recipes; reproducible (cache-replayed); "
          f"commercial manifest built ({manifest['n_tracks']} target tracks). "
          f"Mixtures pending (Mix Generator, next milestone).")


def test_manufacture_steps_1_to_4():
    run()


if __name__ == "__main__":
    run()
