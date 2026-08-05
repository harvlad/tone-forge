"""Milestone 2 end-to-end: one GuitarSet DI travels
    DI -> Gain -> EQ -> NAM -> Catalog
as immutable, fully-traceable, reproducible, cached transformed Assets.

Runs under the audio-capable env (soundfile+scipy+numpy). Executable directly or via pytest.
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

# reuse the M1 fixture generator
from test_data_factory import _write_guitarish  # noqa: E402


def run() -> None:
    from lab.factory import (Asset, RawAsset, AssetCatalog, TransformEngine,
                             GainTransform, EQTransform, NAMTransform, Kind, Role, Status)
    from lab.factory.transforms import TransformProvider

    tmp = Path(tempfile.mkdtemp(prefix="xform_test_"))
    os.environ.setdefault("TONEFORGE_LAB_DATA", str(tmp / "labdata"))
    di_path = tmp / "di.wav"
    _write_guitarish(di_path, seed=7)

    catalog = AssetCatalog(path=tmp / "catalog.jsonl")
    engine = TransformEngine(catalog, store_dir=tmp / "xform",
                             cache_index=tmp / "cache.jsonl")

    # a clean DI asset (as if ingested from GuitarSet)
    di = Asset.ingest(RawAsset(str(di_path), kind=Kind.DI, role=Role.GUITAR,
                               source_tags={"guitar_type": "clean", "synthetic_real": "real"}),
                      source_id="guitarset", dataset_key="guitarset")
    catalog.add(di)

    gain, eq, nam = GainTransform(), EQTransform(), NAMTransform()
    assert all(isinstance(t, TransformProvider) for t in (gain, eq, nam)), "Protocol conformance"

    steps = [
        (gain, {"gain_db": 3.0}),
        (eq, {"mid_db": 2.0, "high_db": -3.0}),
        (nam, {"model": "builtin:tanh_v1", "drive_db": 15.0}),
    ]
    final = engine.run_chain(di, steps)

    # --- immutability: parent untouched, every stage a distinct content id ---
    assert di.path == str(di_path) and di.audit_status == Status.PENDING
    ids = [s.parent_asset_id for s in final.lineage]
    all_ids = [di.asset_id] + [a.asset_id for a in catalog.all()]
    assert len(set(a.asset_id for a in catalog.all())) == len(catalog.all()), "no id collisions"
    assert final.asset_id != di.asset_id

    # --- lineage: ingest -> gain -> eq -> nam, each linking to its parent ---
    stages = [s.stage for s in final.lineage]
    assert stages == ["ingest", "transform:gain", "transform:eq", "transform:nam"], stages
    # every transform step records name, version, params, seed, software, timestamp
    nam_step = final.lineage[-1]
    assert nam_step.params["transform"] == "nam"
    assert nam_step.params["transform_version"] == "v1"
    assert nam_step.params["params"]["drive_db"] == 15.0
    assert nam_step.params["params"]["model_id"] == "builtin:tanh_v1"
    assert nam_step.params["software"] and nam_step.at
    assert nam_step.parent_asset_id is not None  # links to the EQ output

    # --- provenance preserved: license + descriptive tags inherited through the chain ---
    # (license is stamped at ingest-in-pipeline; here di has none, but tags must carry)
    assert final.metadata.get("guitar_type") == "clean"
    assert final.provenance["derived_from"] == final.lineage[-1].parent_asset_id
    # walk lineage back to the original DI
    assert final.lineage[0].stage == "ingest" and final.lineage[0].parent_asset_id is None

    # --- reproducibility: replay the identical chain -> identical final content id ---
    engine2 = TransformEngine(AssetCatalog(path=tmp / "catalog2.jsonl"),
                              store_dir=tmp / "xform2", cache_index=tmp / "cache2.jsonl")
    final2 = engine2.run_chain(di, steps)
    assert final2.content_hash == final.content_hash, "replay must reproduce identical audio"
    assert final2.asset_id == final.asset_id

    # --- cache: re-running the same chain on the SAME engine recomputes nothing ---
    computed_before = engine.stats.computed
    again = engine.run_chain(di, steps)
    assert again.asset_id == final.asset_id
    assert engine.stats.computed == computed_before, "cache must prevent duplicate work"
    assert engine.stats.cache_hits >= 3, engine.stats

    # --- a different param -> different cache key -> new asset (not a false cache hit) ---
    other = engine.run(di, gain, {"gain_db": 6.0})
    assert other.asset_id != catalog.by_source("guitarset")[0].asset_id
    assert engine.stats.computed == computed_before + 1

    # --- catalog holds every intermediate + final with full lineage ---
    assert catalog.get(final.asset_id) is not None
    print(f"OK — DI -> gain -> eq -> nam through the engine. "
          f"final={final.asset_id[:8]} stages={stages} "
          f"computed={engine.stats.computed} cache_hits={engine.stats.cache_hits}; "
          f"replay reproduced identical audio; cache prevented recompute.")


def test_transform_engine_end_to_end():
    run()


if __name__ == "__main__":
    run()
