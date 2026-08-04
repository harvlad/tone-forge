"""Factory Scale Validation: the CorpusValidator whole-corpus quality gate.

Proves the gate (a) passes a clean manufactured corpus and (b) automatically
REJECTs the failure signatures that matter — silent target, collapsed pair
(mixture==target), and duplicate pair — marking assets, not just reporting.

Self-contained: synthesizes its own guitar stems + uses SyntheticBackingProvider,
so it runs anywhere an audio env (soundfile) is present.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _write_guitarish(path: Path, seed: int, secs: float = 3.0, sr: int = 44100) -> None:
    """Bright, plucked, broadband tone that clears the auditor's guitar-label + rolloff
    gate (rich harmonics to ~5kHz + per-note pick transients)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    n = int(secs * sr)
    t = np.arange(n) / sr
    f0 = float(rng.uniform(150, 260))
    y = sum(np.sin(2 * np.pi * f0 * k * t) * (0.85 ** k) for k in range(1, 20))  # to ~5kHz
    y *= np.exp(-t / secs) + 0.3
    for g in range(0, n, int(0.5 * sr)):                 # pick transients (broadband)
        L = min(int(0.02 * sr), n - g)
        y[g:g + L] += rng.standard_normal(L) * np.exp(-np.arange(L) / (0.004 * sr)) * 0.5
    y = (y / (np.max(np.abs(y)) + 1e-9) * 0.5).astype(np.float32)
    sf.write(str(path), y, sr)


def run() -> None:
    from lab.factory import (AssetCatalog, PipelineRunner, GuitarSetSource, VirtualStudio,
                             SyntheticBackingProvider, all_scenarios, Kind, Status,
                             CorpusValidator, validate_manifest, build_supervised_manifest)

    tmp = Path(tempfile.mkdtemp(prefix="corpusval_"))
    os.environ.setdefault("TONEFORGE_LAB_DATA", str(tmp / "labdata"))
    gdir = tmp / "guitarset"
    for i in range(4):
        _write_guitarish(gdir / f"g_{i:02d}.wav", seed=10 + i)

    cat = AssetCatalog(path=tmp / "studio.jsonl")
    runner = PipelineRunner(cat)
    ing = runner.ingest(GuitarSetSource(gdir))
    guitars = ing.admitted + ing.review          # synthetic tones may land in REVIEW; both usable
    assert guitars, f"no guitars admitted: {ing.counts()}"

    studio = VirtualStudio(cat, runner, out_dir=tmp / "out", segment_seconds=2.0)
    provider = SyntheticBackingProvider()
    scenarios = all_scenarios()[:4]
    rep = studio.generate_dataset(guitars, scenarios, provider)
    print("generated:", rep.counts())
    assert len(rep.pairs) > 0

    # (a) clean corpus passes; manifest builds + independently verifies
    v = CorpusValidator(cat).validate()
    print("clean report:", {k: c.to_dict() for k, c in v.checks.items() if not c.ok})
    assert v.marked_reject == 0, f"clean corpus wrongly rejected {v.marked_reject}"
    assert v.n_mixtures == v.n_targets, "mixture/target count mismatch (collapse!)"
    mpath = build_supervised_manifest(cat, "corpusval", tmp / "manifests")
    mv = validate_manifest(mpath)
    assert mv["sha_ok"] and mv["license_ok"] and mv["n_tracks"] == len(rep.pairs), mv

    # (b) inject failures into a fresh catalog copy and confirm the gate REJECTs them
    bad = AssetCatalog(path=tmp / "bad.jsonl")
    mix0, tgt0 = rep.pairs[0]
    mix1, tgt1 = rep.pairs[1]
    # silent target
    sil = tmp / "out" / "silent_tgt.wav"
    sf.write(str(sil), np.zeros(int(2.0 * 44100), dtype=np.float32), 44100)
    silent = tgt0.derive(sil, stage="corrupt:silence", kind=Kind.STEM)
    silent = silent.evolve(stage="tag", metadata={**dict(tgt0.metadata), "pair_id": "BAD_SILENT"})
    bad.add(silent)
    bad.add(mix0.evolve(stage="tag", metadata={**dict(mix0.metadata), "pair_id": "BAD_SILENT"}))
    # duplicate pair (same content on a second pair_id)
    bad.add(mix1.evolve(stage="tag", metadata={**dict(mix1.metadata), "pair_id": "DUP"}))
    bad.add(tgt1.evolve(stage="tag", metadata={**dict(tgt1.metadata), "pair_id": "DUP"}))
    bad.add(mix1); bad.add(tgt1)   # the original pair_id too -> DUP is a duplicate of it

    bv = CorpusValidator(bad).validate()
    fired = {k for k, c in bv.checks.items() if not c.ok}
    print("bad corpus fired:", fired, "| rejects:", bv.marked_reject, "reviews:", bv.marked_review)
    assert "silent_target" in fired, "silent target not caught"
    assert bv.marked_reject >= 1, "no rejects applied"
    # the injected silent target must now be REJECT in the catalog
    assert any(a.audit_status == Status.REJECT for a in bad.all()
               if a.metadata.get("pair_id") == "BAD_SILENT"), "silent not marked REJECT"

    print("OK — CorpusValidator passes clean corpora and auto-REJECTs silent/collapsed/dup pairs; "
          "manifest sha+license independently verified.")


def test_corpus_validator():
    run()


if __name__ == "__main__":
    run()
