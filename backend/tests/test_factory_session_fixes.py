"""Regression tests for the Campaign-1/2 factory fixes:
  - catalog batched persistence (extend/flush/autoflush) — the O(n^2) fix
  - backing RNG cross-process-stable hash + canonical order — the replay fix
  - backing_for resilience to an unreadable file — the "one bad file" fix
  - VirtualStudio deterministic segmentation + cache-skip

Self-contained (synthesises its own audio); runs under the audio env (soundfile).
Executable directly or via pytest.
"""
from __future__ import annotations
import os, sys, tempfile
from pathlib import Path
import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _guitarish(path: Path, seed: int, secs: float = 3.0, sr: int = 44100) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    n = int(secs * sr); t = np.arange(n) / sr
    f0 = float(rng.uniform(150, 260))
    y = sum(np.sin(2 * np.pi * f0 * k * t) * (0.85 ** k) for k in range(1, 20))
    y *= np.exp(-t / secs) + 0.3
    for g in range(0, n, int(0.5 * sr)):
        L = min(int(0.02 * sr), n - g)
        y[g:g + L] += rng.standard_normal(L) * np.exp(-np.arange(L) / (0.004 * sr)) * 0.5
    sf.write(str(path), (y / (np.max(np.abs(y)) + 1e-9) * 0.5).astype(np.float32), sr)


# ---------------------------------------------------------------- catalog batch
def test_catalog_batched_persistence():
    from lab.factory import AssetCatalog, GuitarSetSource, PipelineRunner
    tmp = Path(tempfile.mkdtemp(prefix="cat_"))
    os.environ.setdefault("TONEFORGE_LAB_DATA", str(tmp / "labdata"))
    gdir = tmp / "g"
    for i in range(6):
        _guitarish(gdir / f"g{i}.wav", seed=i)
    src = GuitarSetSource(gdir)
    assets = [a for a in __import__("itertools").islice(_ingest_raw(src), 6)]

    # autoflush=False + extend -> single write, all present, reload round-trips
    cat = AssetCatalog(path=tmp / "c.jsonl", autoflush=False)
    n = cat.extend(assets)
    assert n == len(assets)
    cat.flush()
    reloaded = AssetCatalog(path=tmp / "c.jsonl")
    assert len(reloaded.all()) == len(assets), "extend/flush lost assets"
    # dedup by content id: re-extending identical assets does not grow the catalog
    cat.extend(assets); cat.flush()
    assert len(AssetCatalog(path=tmp / "c.jsonl").all()) == len(assets), "content-id dedup broken"
    # add(flush=False) defers; explicit flush persists
    cat2 = AssetCatalog(path=tmp / "c2.jsonl")
    cat2.add(assets[0], flush=False)
    assert len(AssetCatalog(path=tmp / "c2.jsonl").all()) == 0, "add(flush=False) wrote prematurely"
    cat2.flush()
    assert len(AssetCatalog(path=tmp / "c2.jsonl").all()) == 1
    print("OK catalog: extend/flush O(n), content dedup, deferred add")


def _ingest_raw(src):
    # yield ingested Assets (license+audit) via a throwaway runner
    from lab.factory import AssetCatalog, PipelineRunner
    import tempfile as _t
    rc = AssetCatalog(path=Path(_t.mkdtemp()) / "raw.jsonl")
    res = PipelineRunner(rc).ingest(src)
    for a in res.admitted + res.review:
        yield a


# ------------------------------------------------------- backing determinism
def test_backing_cross_process_stable_and_sorted():
    from lab.factory.backing import StemPoolBackingProvider, _shash, _rng
    from lab.factory.scenario import all_scenarios, RoleSpec
    tmp = Path(tempfile.mkdtemp(prefix="bk_"))
    # stable hash: pure function of the string (this is the PYTHONHASHSEED fix)
    assert _shash("drums") == _shash("drums")
    assert _shash("drums:male") != _shash("bass:male")
    # build a small real backing pool
    paths = []
    for i in range(5):
        p = tmp / f"d{i}.wav"; _guitarish(p, seed=100 + i, secs=2.0); paths.append(str(p))
    scen = all_scenarios()[0]; rs = RoleSpec("drums", -6.0)
    # two providers built from DIFFERENTLY-ORDERED path lists must pick the same file
    p1 = StemPoolBackingProvider({"drums": paths}, "rnd_backing")
    p2 = StemPoolBackingProvider({"drums": list(reversed(paths))}, "rnd_backing")
    a = p1.backing_for(rs, scen, seed=12345, n=44100)
    b = p2.backing_for(rs, scen, seed=12345, n=44100)
    assert np.array_equal(a, b), "backing not order-invariant (canonical sort broken)"
    print("OK backing: stable hash + canonical-order selection")


def test_backing_survives_unreadable_file():
    from lab.factory.backing import StemPoolBackingProvider
    from lab.factory.scenario import all_scenarios, RoleSpec
    tmp = Path(tempfile.mkdtemp(prefix="bkbad_"))
    good = tmp / "good.wav"; _guitarish(good, seed=7, secs=2.0)
    bad = tmp / "bad.wav"; bad.write_bytes(b"not audio at all")
    missing = tmp / "gone.wav"
    scen = all_scenarios()[0]; rs = RoleSpec("drums", -6.0)
    prov = StemPoolBackingProvider({"drums": [str(bad), str(missing), str(good)]}, "rnd_backing")
    out = prov.backing_for(rs, scen, seed=1, n=44100)          # must not raise
    assert out.shape[0] == 44100
    # all-bad pool -> silence, still no crash
    prov2 = StemPoolBackingProvider({"drums": [str(bad), str(missing)]}, "rnd_backing")
    out2 = prov2.backing_for(rs, scen, seed=1, n=1000)
    assert out2.shape[0] == 1000 and float(np.max(np.abs(out2))) == 0.0
    print("OK backing: one bad/missing file never kills the mix")


# ------------------------------------------------------- studio segment + cache
def test_studio_segment_and_cache_skip():
    from lab.factory import (AssetCatalog, PipelineRunner, GuitarSetSource,
                             VirtualStudio, SyntheticBackingProvider, all_scenarios, Kind)
    tmp = Path(tempfile.mkdtemp(prefix="st_"))
    os.environ.setdefault("TONEFORGE_LAB_DATA", str(tmp / "labdata"))
    gdir = tmp / "g"
    for i in range(3):
        _guitarish(gdir / f"g{i}.wav", seed=200 + i, secs=8.0)
    cat = AssetCatalog(path=tmp / "s.jsonl")
    runner = PipelineRunner(cat)
    ing = runner.ingest(GuitarSetSource(gdir)); guitars = ing.admitted + ing.review
    assert guitars
    studio = VirtualStudio(cat, runner, out_dir=tmp / "out", segment_seconds=4.0)
    prov = SyntheticBackingProvider(); scen = all_scenarios()[:2]
    r1 = studio.generate_dataset(guitars, scen, prov)
    assert len(r1.pairs) > 0 and len(r1.cached) == 0
    # segmentation: target audio is ~4s not 8s
    mix0, tgt0 = r1.pairs[0]
    y, sr = sf.read(tgt0.path); assert abs(len(y) / sr - 4.0) < 0.2, "segment_seconds not applied"
    # cache-skip: re-run identical -> everything cached, no new pairs generated
    r2 = studio.generate_dataset(guitars, scen, prov)
    c = r2.counts()
    assert c["new"] == 0 and c["cached"] == len(r2.pairs), f"cache-skip failed: {c}"
    print("OK studio: deterministic 4s segment + 100% cache-skip on replay")


if __name__ == "__main__":
    test_catalog_batched_persistence()
    test_backing_cross_process_stable_and_sorted()
    test_backing_survives_unreadable_file()
    test_studio_segment_and_cache_skip()
    print("\nALL SESSION-FIX TESTS PASSED")
