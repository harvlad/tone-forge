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
import sys
import urllib.request
from pathlib import Path
from typing import Dict

# Make ``tone_forge`` importable when run from backend/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tone_forge.crate import ingest as crate_ingest  # noqa: E402


def _download(url: str, staging_dir: Path) -> Path:
    """Fetch a CC-licensed file to the staging dir. Self-hosting is authorized
    by the CC license on the track, not by any platform API."""
    staging_dir.mkdir(parents=True, exist_ok=True)
    name = url.split("/")[-1].split("?")[0] or "crate_track"
    dest = staging_dir / name
    with urllib.request.urlopen(url, timeout=120) as resp, open(dest, "wb") as fh:
        fh.write(resp.read())
    return dest


def _make_analyzer():
    """Build the analyzer closure: run the SAME engine the app uses, then
    (a) attach the performance_graph (serve.derive_and_attach — the prod box
    is GPU-less and can only render from a stored graph) and (b) materialize
    the melody lane onto result["melody"] (the default to_dict does not
    persist it, and the crate match's melody term needs it)."""
    from tone_forge.analysis.melody_sequence import build_melody_sequence
    from tone_forge.midi.melody_split import annotate_roles
    from tone_forge.performance import serve as perf_serve
    from tone_forge.unified_pipeline import PipelineConfig, get_pipeline

    def analyze(path: Path) -> Dict:
        pipeline = get_pipeline()
        analysis = asyncio.run(pipeline.analyze(path, PipelineConfig.deep()))
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


def main() -> int:
    ap = argparse.ArgumentParser(description="Ingest CC-BY/CC0 tracks into the Vinyl Crate.")
    ap.add_argument("manifest", help="JSON list of ingest-metadata dicts")
    ap.add_argument("--dry-run", action="store_true",
                    help="validate admission + license only; no download/analysis")
    ap.add_argument("--crate-dir", default=None,
                    help="override TONEFORGE_CRATE_DIR for this run")
    args = ap.parse_args()

    metas = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    if isinstance(metas, dict):
        metas = metas.get("tracks", [])
    crate_dir = Path(args.crate_dir) if args.crate_dir else None

    analyzer = None if args.dry_run else _make_analyzer()
    ok = fail = 0
    for meta in metas:
        tid = meta.get("id")
        try:
            crate_ingest.validate_admission(meta)
            if args.dry_run:
                print(f"  [ok/dry] {tid} admissible")
                ok += 1
                continue
            track = crate_ingest.ingest_track(
                meta, analyzer=analyzer, downloader=_download, crate_dir=crate_dir)
            print(f"  [ok] {tid} → {track.features.tempo_bpm:.0f} BPM "
                  f"{track.features.detected_key} ({track.license.license_id.value})")
            ok += 1
        except Exception as exc:  # noqa: BLE001
            print(f"  [reject] {tid}: {exc}")
            fail += 1
    print(f"done: {ok} admitted, {fail} rejected")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
