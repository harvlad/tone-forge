#!/usr/bin/env python3
"""Vinyl-Crate ingestion CLI — run on the VPS to pull, analyze, and register a
curated CC-BY/CC0 donor track.

This is the ONLY place that wires the real, machine-specific pieces the crate
ingestion needs: the HTTP download and the real analysis engine
(``unified_pipeline``). ``tone_forge.crate.ingest`` stays a pure orchestrator
over contracts + borrow (so the subsystem boundary check holds); this script
injects ``analyzer`` + ``downloader`` into ``ingest_track``.

    DATA-OPS RUN ON THE VPS. This build ships no live pull. Running this
    against a manifest downloads audio, runs GPU analysis, and self-hosts
    stems — do that on the box with the GPU worker, never on a laptop.

Usage:
    python scripts/ingest_crate.py tracks.json          # ingest a batch
    python scripts/ingest_crate.py tracks.json --dry-run # validate only

``tracks.json`` is a list of ingest-metadata dicts (see the crate seed
fixtures for the shape): id, title, artist, source, source_track_id,
source_url, license, license_url, attribution, genre, tags, mood, …
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlsplit

# ccMixter (and some CDNs) 403 the default Python-urllib User-Agent as a bot.
# A browser UA + a same-origin Referer returns 206 audio/mpeg from ccMixter
# (verified); Jamendo/incompetech serve fine with a plain fetch but the browser
# UA is harmless there, so send it unconditionally.
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

# Make ``tone_forge`` importable when run from backend/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tone_forge.crate import ingest as crate_ingest  # noqa: E402


def _download(url: str, staging_dir: Path) -> Path:
    """Fetch a CC-licensed file to the staging dir. Self-hosting is authorized
    by the CC license on the track, not by any platform API."""
    staging_dir.mkdir(parents=True, exist_ok=True)
    name = url.split("/")[-1].split("?")[0] or "crate_track"
    dest = staging_dir / name
    parts = urlsplit(url)
    referer = f"{parts.scheme}://{parts.netloc}/" if parts.scheme else ""
    req = urllib.request.Request(url, headers={
        "User-Agent": _BROWSER_UA,
        "Referer": referer,
        "Accept": "audio/mpeg,audio/*;q=0.9,*/*;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as fh:
        fh.write(resp.read())
    return dest


def _gpu_preflight() -> None:
    """Log the torch device once at startup and — when the operator asserted a
    GPU with ``TONEFORGE_EXPECT_GPU=1`` — HARD-EXIT if CUDA isn't actually
    present. The first fleet run's 17 min/track came partly from demucs (the
    one CUDA-accelerated stage) silently falling back to CPU on a pod whose
    driver/arch lottery left CUDA unusable: it looked like it was working, just
    ~10× too slow. Fail loud and fast instead of burning a pod-hour on CPU at
    GPU prices. Imports torch lazily so the dry-run + tests never need it."""
    expect = os.environ.get("TONEFORGE_EXPECT_GPU", "").strip() in ("1", "true", "yes")
    try:
        import torch
        cuda = bool(torch.cuda.is_available())
        name = torch.cuda.get_device_name(0) if cuda else None
    except Exception as exc:  # noqa: BLE001
        cuda, name = False, None
        if expect:
            raise SystemExit(
                f"TONEFORGE_EXPECT_GPU=1 but torch/CUDA failed to import: {exc}")
    print(f"[gpu] torch.cuda.is_available()={cuda} device={name or 'cpu'} "
          f"expect_gpu={expect}")
    if expect and not cuda:
        raise SystemExit(
            "!!! TONEFORGE_EXPECT_GPU=1 but CUDA is NOT available — refusing to "
            "run: demucs would fall back to CPU (~10× slower, the failure mode "
            "that blew the first fleet's 1-hour watchdog). Rent a pod with a "
            "working GPU or unset TONEFORGE_EXPECT_GPU to allow CPU.")


def _make_analyzer(config_factory=None):
    """Build the analyzer closure: run the SAME engine the app uses, then
    (a) attach the performance_graph (serve.derive_and_attach — the prod box
    is GPU-less and can only render from a stored graph) and (b) materialize
    the melody lane onto result["melody"] (the default to_dict does not
    persist it, and the crate match's melody term needs it — when MIDI is on).

    ``config_factory`` selects the analysis depth; it defaults to
    ``PipelineConfig.crate`` — full-capture: stems + graph + tempo/key/chords/
    sections/energy AND the per-stem MIDI/melody ensemble (extract_midi=True,
    use_ensemble=True). The ensemble is GPU-accelerated on a CUDA pod (torchcrepe
    on CUDA + basic_pitch ONNX-GPU), so it is no longer the CPU wall it was
    before 2026-09-07. ``PipelineConfig.deep`` is the same analysis without the
    crate stem-serve base."""
    from tone_forge.analysis.melody_sequence import build_melody_sequence
    from tone_forge.midi.melody_split import annotate_roles
    from tone_forge.performance import serve as perf_serve
    from tone_forge.unified_pipeline import PipelineConfig, get_pipeline

    make_config = config_factory or PipelineConfig.crate

    def analyze(path: Path) -> Dict:
        pipeline = get_pipeline()
        analysis = asyncio.run(pipeline.analyze(path, make_config()))
        result = analysis.to_dict()
        entry_id = result.get("content_hash") or path.stem
        # Persist the graph (+ drum hits) so prod serves pads without stems.
        perf_serve.derive_and_attach(entry_id, result)
        # Materialize the melody lane so the match's MELODY term is live.
        try:
            midi_stems = result.get("midi_stems") or {}
            melody_stems = {
                stem: {"notes": (blob or {}).get("notes"),
                       "overall_confidence": (blob or {}).get("overall_confidence")}
                for stem, blob in midi_stems.items()
                if isinstance(blob, dict) and blob.get("notes")
            }
            mel = build_melody_sequence(
                melody_stems, result.get("sections"), annotate=annotate_roles)
            if mel is not None:
                result["melody"] = {
                    "source_stem": mel.source_stem,
                    "notes": list(mel.notes),
                    "confidence": mel.confidence,
                }
        except Exception as exc:  # noqa: BLE001
            print(f"  [warn] melody materialization failed: {exc}")
        return result

    return analyze


def _is_cuda_oom(exc: BaseException) -> bool:
    """A CUDA out-of-memory error from running too many demucs at once. Detected
    by shape (name/message) so the script never has to import torch (it stays
    importable for the dry-run + tests without the analysis deps)."""
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    return ("outofmemory" in name or "out of memory" in msg
            or ("cuda" in msg and "memory" in msg))


def _select_shard(metas: List[Dict], shard: Optional[str]) -> List[Dict]:
    """Round-robin shard selector: ``--shard I/N`` keeps every track whose
    position ≡ I (mod N). Round-robin (not contiguous) so sources/genres —
    which are grouped in the manifest — spread evenly across pods, balancing
    GPU load and not concentrating one source's failures on one pod."""
    if not shard:
        return metas
    i_str, _, n_str = shard.partition("/")
    i, n = int(i_str), int(n_str)
    if not (0 <= i < n):
        raise SystemExit(f"--shard {shard!r}: need 0 <= I < N")
    return [m for idx, m in enumerate(metas) if idx % n == i]


def run_batch(metas: List[Dict], *, analyzer, downloader, concurrency: int = 3,
              crate_dir: Optional[Path] = None,
              blind_gate=None) -> Tuple[int, int]:
    """Download+analyze+register a batch, up to ``concurrency`` tracks at once.

    A ThreadPoolExecutor over ``ingest_track`` is the safe primitive: each
    track's ``pipeline.analyze`` runs under its own ``asyncio.run`` event loop
    (asyncio.run is per-thread) and torch releases the GIL during GPU work, so
    threads genuinely overlap the demucs passes. The shared registry.json write
    is serialized inside ``ingest_track`` (a lock there), so concurrent
    persistence is safe.

    Per-track isolation is preserved: one track raising never sinks the batch.
    A CUDA-OOM (too many concurrent demucs for the card) is not a real reject —
    those tracks are collected and RETRIED sequentially after the pool drains,
    so an over-eager --concurrency degrades to correctness rather than data loss.

    Returns (admitted, rejected). Only the MAIN thread mutates the counters
    (results are consumed via as_completed), so no counter lock is needed.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    kw = {} if blind_gate is None else {"blind_gate": blind_gate}

    def _one(meta: Dict):
        track = crate_ingest.ingest_track(
            meta, analyzer=analyzer, downloader=downloader,
            crate_dir=crate_dir, **kw)
        return track

    def _report_ok(tid, track):
        print(f"  [ok] {tid} → {track.features.tempo_bpm:.0f} BPM "
              f"{track.features.detected_key} ({track.license.license_id.value})")

    ok = fail = 0
    oom_retry: List[Dict] = []
    workers = max(1, int(concurrency))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_one, m): m for m in metas}
        for fut in as_completed(futs):
            meta = futs[fut]
            tid = meta.get("id")
            try:
                track = fut.result()
                _report_ok(tid, track)
                ok += 1
            except Exception as exc:  # noqa: BLE001
                if _is_cuda_oom(exc):
                    print(f"  [oom] {tid}: {exc} — deferring to sequential retry")
                    oom_retry.append(meta)
                else:
                    print(f"  [reject] {tid}: {exc}")
                    fail += 1

    # Sequential, isolated retry for anything that OOM'd under concurrency.
    for meta in oom_retry:
        tid = meta.get("id")
        try:
            track = _one(meta)
            _report_ok(tid, track)
            ok += 1
        except Exception as exc:  # noqa: BLE001
            print(f"  [reject] {tid}: {exc} (after OOM retry)")
            fail += 1
    return ok, fail


def main() -> int:
    ap = argparse.ArgumentParser(description="Ingest CC-BY/CC0 tracks into the Vinyl Crate.")
    ap.add_argument("manifest", help="JSON manifest: a list of ingest dicts, or "
                    "{\"_meta\":..., \"tracks\":[...]}")
    ap.add_argument("--dry-run", action="store_true",
                    help="validate admission + license only; no download/analysis")
    ap.add_argument("--crate-dir", default=None,
                    help="override TONEFORGE_CRATE_DIR for this run")
    ap.add_argument("--concurrency", type=int, default=3,
                    help="analyze up to N tracks at once (one A40 fits ~3-4 "
                         "demucs). 1 = strict sequential. CUDA-OOM tracks are "
                         "auto-retried sequentially.")
    ap.add_argument("--shard", default=None,
                    help="process only shard I of N, round-robin: --shard 0/4. "
                         "For fanning the manifest across parallel GPU pods.")
    ap.add_argument("--full", action="store_true",
                    help="use PipelineConfig.deep() instead of the crate config. "
                         "Both run the full per-stem MIDI/melody ensemble; the "
                         "crate config just adds the stem-serve base. Kept for "
                         "parity checks against the plain deep() path.")
    args = ap.parse_args()

    metas = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    if isinstance(metas, dict):
        metas = metas.get("tracks", [])
    metas = _select_shard(metas, args.shard)
    crate_dir = Path(args.crate_dir) if args.crate_dir else None

    if args.shard:
        print(f"shard {args.shard}: {len(metas)} tracks")

    if args.dry_run:
        ok = fail = 0
        for meta in metas:
            tid = meta.get("id")
            try:
                crate_ingest.validate_admission(meta)
                print(f"  [ok/dry] {tid} admissible")
                ok += 1
            except Exception as exc:  # noqa: BLE001
                print(f"  [reject] {tid}: {exc}")
                fail += 1
        print(f"done: {ok} admitted, {fail} rejected")
        return 0 if fail == 0 else 1

    # Fail loud + fast if a GPU was asserted but isn't really there.
    _gpu_preflight()

    from tone_forge.unified_pipeline import PipelineConfig
    config_factory = PipelineConfig.deep if args.full else PipelineConfig.crate
    print(f"[config] analysis={'deep' if args.full else 'crate'} "
          f"(midi/melody ON — GPU ensemble on a CUDA pod)")
    analyzer = _make_analyzer(config_factory)
    ok, fail = run_batch(metas, analyzer=analyzer, downloader=_download,
                         concurrency=args.concurrency, crate_dir=crate_dir)
    print(f"done: {ok} admitted, {fail} rejected")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
