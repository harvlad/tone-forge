"""Phase 3.2 Coverage Planner: decision layer over the pool. Validates gap
detection, ROI/priority ranking, benchmark-boost, and report generation.
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
                             all_recipes, manufacture, planner)

    tmp = Path(tempfile.mkdtemp(prefix="planner_test_"))
    os.environ.setdefault("TONEFORGE_LAB_DATA", str(tmp / "labdata"))
    gdir = tmp / "guitarset"
    for i in range(4):
        _write_guitarish(gdir / f"di_{i}.wav", seed=500 + i)
    catalog = AssetCatalog(path=tmp / "catalog.jsonl")
    runner = PipelineRunner(catalog)
    engine = TransformEngine(catalog, store_dir=tmp / "xf", cache_index=tmp / "c.jsonl")
    di = runner.ingest(GuitarSetSource(gdir)).admitted
    manufacture(engine, runner, catalog, di, all_recipes())   # acoustic + clean + distorted guitar

    p = planner.plan(catalog)
    gaps = {(g.dimension, g.value) for g in p.gaps}

    # --- validation: the planner surfaces the gaps we already know are real ---
    assert ("tuning", "7-string") in gaps and ("tuning", "baritone") in gaps
    assert ("string_count", "7") in gaps
    assert ("playing_style", "metal") in gaps
    assert ("masking_level", "high") in gaps           # no mixtures yet -> masking gap
    # ranked by priority; high-impact tunings rank near the top
    top_dims = [g.dimension for g in p.top(6)]
    assert "tuning" in top_dims or "string_count" in top_dims, top_dims
    # every gap carries an explanation + strategy
    for g in p.gaps:
        assert g.strategy in ("commission", "license", "green", "virtual_studio")
        assert g.source_note and g.impact > 0

    # --- benchmark integration: a failing regime boosts that gap's priority ---
    base = {(g.dimension, g.value): g.priority for g in p.gaps}
    p2 = planner.plan(catalog, benchmark_failures={"tuning": {"7-string": 1.0}})
    boosted = {(g.dimension, g.value): (g.priority, g.benchmark_boost) for g in p2.gaps}
    prio, boost = boosted[("tuning", "7-string")]
    assert boost > 1.0 and prio > base[("tuning", "7-string")], (prio, base[("tuning", "7-string")])

    # --- reports render (the 9 outputs) ---
    rep = planner.full_report(p, catalog)
    for section in ("Coverage Dashboard", "Coverage Heatmap", "Gap Report",
                    "Acquisition Priority List", "ROI Report", "Commissioning Briefs",
                    "Licensing Opportunities", "Historical Coverage Trend",
                    "Recommended Next Actions"):
        assert section in rep, f"missing report section: {section}"
    assert "Need ~" in rep   # a brief was generated

    print("OK — planner: gaps detected match known blind spots (tunings/7-string/metal/masking); "
          f"benchmark-boost works; 9 reports render; {len(p.gaps)} ranked gaps.")


def test_planner():
    run()


if __name__ == "__main__":
    run()
