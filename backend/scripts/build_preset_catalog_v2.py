#!/usr/bin/env python3
"""Build V2 preset catalogs from the Live-rendered V2 audio corpus.

Inputs:
- discover_presets() for each engine (Analog, Drift, Collision, Electric)
- WAV files under preset_catalog_output/audio_v2/<safe_filename>.wav
- ALS files under preset_catalog_output/als_v2/<safe_filename> Project/<safe_filename>.als

Outputs (under preset_catalog_output/catalog/):
- catalog_<engine>_v2.json : per-engine V2 catalog (one per engine)
- catalog_v2.json          : union catalog across all engines

Each output uses the same schema as catalog_analog.json (V1) so existing
loaders (PresetCatalog.load) and retrieval scripts can consume V2 catalogs
without changes.

Usage:
    python3 scripts/build_preset_catalog_v2.py \\
        --instruments Analog Drift Collision Electric \\
        --audio-dir preset_catalog_output/audio_v2 \\
        --als-dir preset_catalog_output/als_v2 \\
        --catalog-dir preset_catalog_output/catalog
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Dict, List, Optional

# Add backend/ to sys.path so `tone_forge` imports resolve when invoked
# from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tone_forge.preset_catalog.catalog_builder import (
    PresetCatalog,
    extract_preset_fingerprint,
)
from tone_forge.preset_catalog.preset_discovery import (
    discover_presets,
    safe_filename,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def _find_als_path(als_root: Path, stem: str) -> Optional[Path]:
    """Locate the per-preset ALS produced by build_per_preset_als.py.

    build_per_preset_als writes each ALS into its own Project folder
    (Live 12 requires this for dirty Sets). Layout:
        <als_root>/<stem> Project/<stem>.als
    """
    candidate = als_root / f"{stem} Project" / f"{stem}.als"
    return candidate if candidate.exists() else None


def build_engine_catalog(
    engine: str,
    audio_dir: Path,
    als_dir: Path,
) -> PresetCatalog:
    """Build a single engine's V2 catalog by fingerprinting V2 WAVs.

    Skips presets whose WAV is missing (and logs them). Provenance fields
    (preset_path, adv_sha1, als_path) are captured via extract_preset_fingerprint;
    the WAV/decoded SHA-1s come from the same path.
    """
    presets = discover_presets([engine])
    logger.info("[%s] discovered %d presets", engine, len(presets))

    catalog = PresetCatalog()
    missing = 0
    for preset in presets:
        stem = safe_filename(preset.preset_id)
        wav_path = audio_dir / f"{stem}.wav"
        if not wav_path.exists():
            logger.warning("[%s] WAV missing for %s: %s", engine, preset.preset_id, wav_path)
            missing += 1
            continue

        als_path = _find_als_path(als_dir, stem)
        try:
            fp = extract_preset_fingerprint(
                wav_path,
                preset,
                als_path=als_path,
            )
            catalog.add(fp)
        except Exception as exc:
            logger.error(
                "[%s] fingerprint FAILED for %s: %s: %s",
                engine, preset.preset_id, type(exc).__name__, exc,
            )

    logger.info(
        "[%s] catalog built: %d fingerprints, %d missing WAVs, %d errors",
        engine, len(catalog.presets), missing,
        len(presets) - len(catalog.presets) - missing,
    )
    return catalog


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--instruments",
        nargs="+",
        default=["Analog", "Drift", "Collision", "Electric"],
        help="Engines to include in V2 catalog (default: %(default)s)",
    )
    p.add_argument(
        "--audio-dir",
        type=Path,
        default=Path("preset_catalog_output/audio_v2"),
    )
    p.add_argument(
        "--als-dir",
        type=Path,
        default=Path("preset_catalog_output/als_v2"),
    )
    p.add_argument(
        "--catalog-dir",
        type=Path,
        default=Path("preset_catalog_output/catalog"),
    )
    args = p.parse_args()

    args.catalog_dir.mkdir(parents=True, exist_ok=True)

    per_engine: Dict[str, PresetCatalog] = {}
    for engine in args.instruments:
        cat = build_engine_catalog(engine, args.audio_dir, args.als_dir)
        per_engine[engine] = cat
        out_path = args.catalog_dir / f"catalog_{engine.lower()}_v2.json"
        cat.save(out_path)
        logger.info("[%s] wrote %s (%d presets)", engine, out_path, len(cat.presets))

    # Combined union catalog.
    union = PresetCatalog()
    for engine, cat in per_engine.items():
        for fp in cat.presets:
            union.add(fp)
    union_path = args.catalog_dir / "catalog_v2.json"
    union.save(union_path)
    logger.info("Wrote union catalog %s (%d presets)", union_path, len(union.presets))

    # Summary
    print("\n=== V2 catalog summary ===")
    for engine, cat in per_engine.items():
        print(f"  {engine:10s}: {len(cat.presets):4d} fingerprints")
    print(f"  {'TOTAL':10s}: {len(union.presets):4d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
