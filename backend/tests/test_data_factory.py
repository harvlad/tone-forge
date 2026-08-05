"""Milestone 1 end-to-end: one Slakh asset + one GuitarSet asset travel
Source -> Asset -> License -> Auditor -> Catalog with full provenance.

Runs under the audio-capable env (soundfile+librosa). Executable directly:
    TONEFORGE_LAB_DATA=/tmp/x <sepenv>/bin/python backend/tests/test_data_factory.py
or via pytest where those deps are installed.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _write_guitarish(path: Path, seed: int, sr: int = 44100) -> None:
    """A few plucked notes: harmonic stack + decay + pick-attack transient + gaps.
    Tuned to pass the auditor (real occupancy, high crest, guitar-range rolloff)."""
    rng = np.random.default_rng(seed)
    notes = [82.41, 110.0, 146.83, 196.0, 246.94, 329.63]  # E2 A2 D3 G3 B3 E4
    note_s, gap_s = 0.7, 0.25
    y = []
    for f0 in notes:
        n = int(note_s * sr)
        t = np.arange(n) / sr
        env = np.exp(-t / 0.28)
        sig = np.zeros(n)
        for k in range(1, 25):                       # bright harmonic stack (1/k^0.3) up to ~6kHz
            if k * f0 < 0.45 * sr:
                sig += (1.0 / (k ** 0.3)) * np.sin(2 * np.pi * k * f0 * t)
        sig *= env
        attack = np.zeros(n)                          # 15ms broadband pick transient -> HF/rolloff
        a = int(0.015 * sr)
        attack[:a] = rng.standard_normal(a) * np.linspace(1, 0, a)
        note = 0.6 * sig + 0.4 * attack
        y.append(note)
        y.append(np.zeros(int(gap_s * sr)))          # silence gap -> occupancy < 1, higher crest
    out = np.concatenate(y).astype(np.float32)
    out /= (np.max(np.abs(out)) + 1e-9)
    out *= 0.6                                        # headroom, no clipping
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), out, sr)


def _fixtures(root: Path):
    slakh = root / "slakh"; gset = root / "guitarset"
    _write_guitarish(slakh / "Track00001" / "guitar.wav", seed=1)
    _write_guitarish(gset / "excerpt_01_mic.wav", seed=2)
    return slakh, gset


def run() -> None:
    from lab.factory import (Asset, AssetCatalog, PipelineRunner,
                             SlakhSource, GuitarSetSource, Status)
    from lab.factory.sources import SourceProvider

    tmp = Path(tempfile.mkdtemp(prefix="factory_test_"))
    os.environ.setdefault("TONEFORGE_LAB_DATA", str(tmp / "labdata"))
    slakh_root, gset_root = _fixtures(tmp)

    catalog = AssetCatalog(path=tmp / "catalog.jsonl")
    runner = PipelineRunner(catalog)

    slakh = SlakhSource(slakh_root, emit_mixture=False)
    gset = GuitarSetSource(gset_root)
    assert isinstance(slakh, SourceProvider) and isinstance(gset, SourceProvider), "Protocol conformance"

    r1 = runner.ingest(slakh)
    r2 = runner.ingest(gset)
    print("slakh:", r1.counts(), "| guitarset:", r2.counts())

    # --- both assets present in catalog ---
    assert len(catalog) == 2, f"expected 2 assets, got {len(catalog)}"
    slk = catalog.by_source("slakh2100"); gst = catalog.by_source("guitarset")
    assert len(slk) == 1 and len(gst) == 1
    a_slk, a_gst = slk[0], gst[0]

    # --- content-based identity ---
    from lab.factory.asset import asset_id_for
    assert a_slk.content_hash and a_slk.asset_id == asset_id_for(a_slk.content_hash)
    assert a_slk.asset_id != a_gst.asset_id

    # --- license stamped from the registry (both green-tier commercial-OK) ---
    assert a_slk.license and a_slk.license.commercial_training_allowed is True
    assert a_gst.license and a_gst.license.license == "CC-BY-4.0"

    # --- audited, not rejected; metadata + audit present and DISJOINT ---
    for a in (a_slk, a_gst):
        assert a.audit_status in (Status.PASS, Status.REVIEW), f"{a.source_id} -> {a.audit_status} {a.audit['reasons']}"
        assert a.audit and "guitar_rms" in a.audit and "verdict" in a.audit
        assert a.metadata and "guitar_type" in a.metadata and "key" in a.metadata
        assert a.quality_score is not None and a.quality_status in (Status.GOOD, Status.ACCEPTABLE, Status.POOR)
        # no duplicated fields across audit vs metadata
        assert set(a.audit) & set(a.metadata) == set(), "audit and metadata must be disjoint"
        # license lives only in the stamp, not copied into metadata
        assert "license" not in a.metadata

    # --- source tags flowed through (GuitarSet knows it's acoustic -> not an estimate) ---
    assert a_gst.metadata["guitar_type"] == "acoustic"
    assert a_gst.metadata["synthetic_real"] == "real"
    assert a_slk.metadata["synthetic_real"] == "synthetic"

    # --- full lineage: ingest -> license -> audit -> catalog ---
    stages = [s.stage for s in catalog.lineage(a_slk.asset_id)]
    assert stages == ["ingest", "license", "audit", "catalog"], stages
    assert catalog.lineage(a_slk.asset_id)[0].parent_asset_id is None  # ingest is the root

    # --- lookups ---
    assert len(catalog.by_license(commercial_only=True)) == 2
    assert len(catalog.by_license(commercial_only=False)) == 0
    assert len(catalog.by_quality(min_score=0.0)) == 2
    assert catalog.by_tag("synthetic_real", "real") == [a_gst]
    assert catalog.get(a_slk.asset_id) is a_slk or catalog.get(a_slk.asset_id).asset_id == a_slk.asset_id

    # --- immutability ---
    import dataclasses
    try:
        object.__setattr__  # sanity
        a_slk.__setattr__("path", "/hacked")  # frozen -> should raise
        raise AssertionError("Asset must be immutable")
    except dataclasses.FrozenInstanceError:
        pass

    # --- content dedup: re-ingest identical audio -> same id, catalog unchanged ---
    n_before = len(catalog)
    runner.ingest(SlakhSource(slakh_root, emit_mixture=False))
    assert len(catalog) == n_before, "re-ingesting identical content must not duplicate"

    # --- persistence round-trip ---
    reloaded = AssetCatalog(path=tmp / "catalog.jsonl")
    assert len(reloaded) == 2
    ra = reloaded.get(a_slk.asset_id)
    assert ra and ra.content_hash == a_slk.content_hash and [s.stage for s in ra.lineage] == stages

    print("OK — 2 assets through Source->License->Audit->Catalog with full provenance; "
          "dedup + persistence + immutability verified.")


def test_data_factory_end_to_end():
    run()


if __name__ == "__main__":
    run()
