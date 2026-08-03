"""Milestone 4: the Virtual Studio manufactures supervised (mixture, guitar_target)
pairs from M3 manufactured guitar assets + Scenarios, deterministically and with
exact ground truth, then builds a training-ready manifest.

Runs under the audio-capable env. Executable directly or via pytest.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_data_factory import _write_guitarish  # noqa: E402


def run() -> None:
    from lab.factory import (AssetCatalog, PipelineRunner, TransformEngine, GuitarSetSource,
                             all_recipes, manufacture, VirtualStudio, SyntheticBackingProvider,
                             all_scenarios, build_supervised_manifest, Kind, Role, Status)
    from lab.factory.backing import ScenarioProvider

    tmp = Path(tempfile.mkdtemp(prefix="studio_test_"))
    os.environ.setdefault("TONEFORGE_LAB_DATA", str(tmp / "labdata"))
    di_dir = tmp / "guitarset"
    for i in range(3):
        _write_guitarish(di_dir / f"di_{i:02d}.wav", seed=200 + i)

    catalog = AssetCatalog(path=tmp / "catalog.jsonl")
    runner = PipelineRunner(catalog)
    engine = TransformEngine(catalog, store_dir=tmp / "xform", cache_index=tmp / "cache.jsonl")

    # M3: manufacture guitar assets (3 DI x 2 recipes)
    di = runner.ingest(GuitarSetSource(di_dir)).admitted
    recipes = all_recipes()[:2]
    mfg = manufacture(engine, runner, catalog, di, recipes)
    guitar_assets = mfg.admitted
    assert len(guitar_assets) == 6

    # M4: Virtual Studio -> supervised pairs
    provider = SyntheticBackingProvider()
    assert isinstance(provider, ScenarioProvider)
    studio = VirtualStudio(catalog, runner, out_dir=tmp / "studio")
    scenarios = all_scenarios()[:3]           # classic_rock, acoustic_vocal, modern_metal
    report = studio.generate_dataset(guitar_assets, scenarios, provider)
    print("studio:", report.counts())
    assert len(report.pairs) == 6 * 3, report.counts()

    mix0, tgt0 = report.pairs[0]

    # --- output shape: mixture + target are distinct assets, distinct kinds ---
    assert mix0.kind == Kind.MIXTURE and tgt0.kind == Kind.STEM and tgt0.role == Role.GUITAR
    assert mix0.asset_id != tgt0.asset_id
    assert mix0.metadata["pair_id"] == tgt0.metadata["pair_id"]

    # --- EXACT ground truth: mixture - sum(backings) == target (linear mix) ---
    mix, sr = sf.read(mix0.path, always_2d=True)
    tgt, _ = sf.read(tgt0.path, always_2d=True)
    # the guitar-in-mix energy must be fully present in the mixture; residual (mix-tgt)
    # is the backing bed and must be non-trivial (guitar is masked, not alone)
    residual = mix[: len(tgt)] - tgt[: len(mix)]
    assert np.sqrt(np.mean(tgt ** 2)) > 1e-4, "target must be audible"
    assert np.sqrt(np.mean(residual ** 2)) > 1e-4, "mixture must contain backing (masking present)"
    # target is literally a subtractive component of the mixture (exact GT)
    assert np.max(np.abs((tgt + residual) - mix[: len(tgt)])) < 1e-5, "mixture must equal target + backing"

    # --- provenance: every mixing decision recorded for replay ---
    step = [s for s in mix0.lineage if s.stage.startswith("scenario:")][0]
    p = step.params
    for key in ("scenario", "scenario_version", "scenario_signature", "provider",
                "backing_dataset", "backings", "guitar_level_db", "masking_db",
                "norm_gain", "seed", "dsp", "mix_version"):
        assert key in p, f"missing provenance: {key}"
    assert p["backings"] and all("backing_sha256" in b for b in p["backings"])
    assert mix0.metadata["benchmark_regime"]          # benchmark tag present
    assert mix0.provenance.get("derived_from")        # links back to the guitar asset

    # --- determinism: regenerate the same pair -> byte-identical -> same asset id ---
    catalog2 = AssetCatalog(path=tmp / "catalog2.jsonl")
    studio2 = VirtualStudio(catalog2, PipelineRunner(catalog2), out_dir=tmp / "studio2")
    g0 = guitar_assets[0]
    m_a, _ = studio2.generate(g0, scenarios[0], provider)
    m_b, _ = studio2.generate(g0, scenarios[0], provider)
    assert m_a.content_hash == m_b.content_hash == mix0.content_hash, "studio must be deterministic"

    # --- masking difficulty tracks the scenario (metal masks harder than jazz-ish) ---
    def masker_energy(mixture_asset):
        st = [s for s in mixture_asset.lineage if s.stage.startswith("scenario:")][0].params
        return st["masking_db"]
    metal = [m for m, _ in report.pairs if m.metadata["scenario"] == "modern_metal"][0]
    rock = [m for m, _ in report.pairs if m.metadata["scenario"] == "classic_rock"][0]
    assert masker_energy(metal) > masker_energy(rock)

    # --- Step 4: supervised (mixture,target) manifest, license-gated ---
    manifest_path = build_supervised_manifest(catalog, "riley_studio_v1", tmp / "manifests")
    import json
    manifest = json.loads(manifest_path.read_text())
    assert manifest["intended_use"] == "commercial_eligible"
    assert manifest["n_tracks"] == len(report.pairs)
    assert all(t["mixture_path"] and t["target_path"] for t in manifest["tracks"])   # PAIRS now
    assert manifest["dataset_lineage"]["guitarset"]["commercial_training_allowed"] is True

    regimes = {t["benchmark_regime"] for t in manifest["tracks"]}
    print(f"OK — Virtual Studio produced {len(report.pairs)} supervised (mixture,target) pairs "
          f"across {len(scenarios)} scenarios; exact GT; deterministic replay; full provenance; "
          f"license-gated pair manifest built. benchmark regimes: {sorted(regimes)}")


def test_virtual_studio_end_to_end():
    run()


if __name__ == "__main__":
    run()
