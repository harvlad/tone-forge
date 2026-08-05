"""M4 real-backing path: StemPoolBackingProvider draws real backing stems from a
pool (same ScenarioProvider Protocol), and the license gate correctly refuses a
commercial manifest when the backing pool is non-commercial.

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


def _noise_stem(path: Path, seed: int, sr: int = 44100, secs: float = 6.0):
    rng = np.random.default_rng(seed)
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), (rng.standard_normal(int(sr * secs)) * 0.2).astype("float32"), sr)


def run() -> None:
    from lab.factory import (AssetCatalog, PipelineRunner, TransformEngine, GuitarSetSource,
                             all_recipes, manufacture, VirtualStudio, StemPoolBackingProvider,
                             all_scenarios, build_supervised_manifest)
    from lab.factory.backing import ScenarioProvider
    from lab import training_data

    tmp = Path(tempfile.mkdtemp(prefix="stempool_test_"))
    os.environ.setdefault("TONEFORGE_LAB_DATA", str(tmp / "labdata"))

    # guitar
    gdir = tmp / "guitarset"
    for i in range(2):
        _write_guitarish(gdir / f"di_{i}.wav", seed=300 + i)
    # real (fixture) backing pool — vocals/drums/bass present; synth/keys absent (tests fallback)
    pool_paths = {}
    for role, s in (("vocals", 1), ("drums", 2), ("bass", 3)):
        p = tmp / "pool" / role / "a.wav"
        _noise_stem(p, seed=s)
        pool_paths[role] = [str(p)]

    catalog = AssetCatalog(path=tmp / "catalog.jsonl")
    runner = PipelineRunner(catalog)
    engine = TransformEngine(catalog, store_dir=tmp / "xf", cache_index=tmp / "c.jsonl")

    di = runner.ingest(GuitarSetSource(gdir)).admitted
    guitars = manufacture(engine, runner, catalog, di, all_recipes()[:1]).admitted
    assert len(guitars) == 2

    provider = StemPoolBackingProvider(pool_paths, dataset_key="rnd_backing")
    assert isinstance(provider, ScenarioProvider)
    assert provider.health()

    studio = VirtualStudio(catalog, runner, out_dir=tmp / "studio")
    scenarios = all_scenarios()[:2]  # classic_rock, acoustic_vocal
    report = studio.generate_dataset(guitars, scenarios, provider)
    print("stem-pool studio:", report.counts())
    assert len(report.pairs) == 2 * 2

    mix0, _ = report.pairs[0]
    step = [s for s in mix0.lineage if s.stage.startswith("scenario:")][0].params
    assert step["backing_dataset"] == "rnd_backing"
    assert any(b["backing_sha256"] for b in step["backings"])       # real stems hashed
    # determinism
    c2 = AssetCatalog(path=tmp / "c2.jsonl")
    s2 = VirtualStudio(c2, PipelineRunner(c2), out_dir=tmp / "studio2")
    a, _ = s2.generate(guitars[0], scenarios[0], provider)
    b, _ = s2.generate(guitars[0], scenarios[0], provider)
    assert a.content_hash == b.content_hash == mix0.content_hash

    # --- license gate: rnd_backing is NON-commercial -> commercial manifest REFUSED ---
    raised = False
    try:
        build_supervised_manifest(catalog, "should_fail", tmp / "m", intended_use="commercial_eligible")
    except training_data.ProvenanceError as e:
        raised = True
        assert "rnd_backing" in str(e) and "commercial" in str(e)
    assert raised, "commercial manifest must be refused when backing is non-commercial"

    # --- research_only manifest succeeds (pairs are valid R&D data) ---
    mpath = build_supervised_manifest(catalog, "riley_rnd_pairs", tmp / "m", intended_use="research_only")
    import json
    manifest = json.loads(mpath.read_text())
    assert manifest["intended_use"] == "research_only"
    assert manifest["n_tracks"] == len(report.pairs)
    assert all(t["mixture_path"] and t["target_path"] for t in manifest["tracks"])

    print(f"OK — StemPoolBackingProvider produced {len(report.pairs)} real-backing pairs "
          f"(deterministic); commercial manifest correctly REFUSED (backing NC); "
          f"research_only manifest built. Backing-license gate hole closed.")


def test_stem_pool_backing():
    run()


if __name__ == "__main__":
    run()
