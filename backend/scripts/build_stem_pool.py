#!/usr/bin/env python3
"""Build / grow the permanent Stem Pool by ingesting a source through the factory
pipeline, then emit a coverage report. Thin driver over the existing pipeline —
no new infrastructure.

Canonical audio lives on durable infra (Hetzner/R2); the committed catalog is the
INDEX. Each asset is audited from a local working copy, but its stored `path` is
rewritten to the durable canonical URI (content_hash preserves identity for
re-fetch). Nothing bypasses license/audit/provenance/catalog.

Usage:
  build_stem_pool.py --source guitarset --audio-dir <dir> \
      --canonical-prefix "hetzner:/root/stempool/src/guitarset/audio_mono-mic" \
      --catalog backend/lab_data/factory/stempool.jsonl \
      --coverage backend/lab_data/factory/coverage.txt [--limit N]
"""
from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lab.factory import (AssetCatalog, PipelineRunner, GuitarSetSource, GuitarTechsSource,  # noqa: E402
                         EGFxSetSource, Status, coverage_report, render_report)

SOURCES = {"guitarset": GuitarSetSource, "guitar_techs": GuitarTechsSource, "egfxset": EGFxSetSource}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, choices=list(SOURCES))
    ap.add_argument("--audio-dir", required=True)
    ap.add_argument("--canonical-prefix", default="", help="durable URI prefix for stored paths")
    ap.add_argument("--catalog", required=True)
    ap.add_argument("--coverage", default="")
    ap.add_argument("--limit", type=int, default=0, help="0 = all")
    args = ap.parse_args()

    catalog = AssetCatalog(path=args.catalog)
    runner = PipelineRunner(catalog)
    provider = SOURCES[args.source](args.audio_dir)
    audio_root = Path(args.audio_dir)

    res = runner.ingest(provider)   # Source -> License -> Auditor -> Catalog
    admitted = res.admitted + res.review

    # rewrite each admitted asset's path to the durable canonical URI (audit already ran).
    # preserve the relative path under audio-dir so nested layouts stay unique + re-parseable.
    if args.canonical_prefix:
        for a in list(admitted):
            try:
                rel = Path(a.path).resolve().relative_to(audio_root.resolve())
            except ValueError:
                rel = Path(a.path).name
            durable = f"{args.canonical_prefix.rstrip('/')}/{rel}"
            catalog.add(a.evolve(stage="canonicalize",
                                 params={"canonical_uri": durable, "audited_local": a.path},
                                 path=durable, provenance={**dict(a.provenance),
                                                           "canonical_uri": durable}))

    counts = res.counts()
    print(f"ingested {args.source}: {counts}")
    rep = coverage_report(catalog)
    if args.coverage:
        Path(args.coverage).parent.mkdir(parents=True, exist_ok=True)
        Path(args.coverage).write_text(render_report(rep))
        Path(args.coverage).with_suffix(".json").write_text(json.dumps(rep, indent=1, default=str))
    print(render_report(rep))
    print(f"\npool size: {len(catalog)} assets | catalog: {args.catalog}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
