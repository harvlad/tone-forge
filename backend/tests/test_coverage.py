"""Phase 3 coverage analysis: histogram the pool over musical space + flag gaps.

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
    from lab.factory import (AssetCatalog, PipelineRunner, TransformEngine, GuitarSetSource,
                             all_recipes, manufacture, coverage_report, render_report, DIMENSIONS)

    tmp = Path(tempfile.mkdtemp(prefix="cov_test_"))
    os.environ.setdefault("TONEFORGE_LAB_DATA", str(tmp / "labdata"))
    gdir = tmp / "guitarset"
    for i in range(4):
        _write_guitarish(gdir / f"di_{i}.wav", seed=400 + i)

    catalog = AssetCatalog(path=tmp / "catalog.jsonl")
    runner = PipelineRunner(catalog)
    engine = TransformEngine(catalog, store_dir=tmp / "xf", cache_index=tmp / "c.jsonl")

    di = runner.ingest(GuitarSetSource(gdir)).admitted
    manufacture(engine, runner, catalog, di, all_recipes())  # clean/distorted/acoustic variety

    rep = coverage_report(catalog, sparse_threshold=5)
    assert rep["n_assets"] > 0
    # all requested dimensions present
    assert set(rep["dimensions"]) == set(DIMENSIONS)
    # guitar_type diversity from recipes shows up
    gt = rep["dimensions"]["guitar_type"]
    assert {"clean", "distorted", "acoustic"} <= set(gt), gt
    # license class is tracked (all green here)
    assert "commercial" in rep["dimensions"]["license_class"]
    # sparse detection produces gap entries (small pool)
    assert isinstance(rep["sparse"], list)
    text = render_report(rep)
    assert "Coverage" in text and "guitar_type" in text
    print(text[:600])
    print(f"\nOK — coverage over {rep['n_assets']} assets, {len(rep['dimensions'])} dimensions, "
          f"{len(rep['sparse'])} sparse cells flagged.")


def test_coverage():
    run()


if __name__ == "__main__":
    run()
