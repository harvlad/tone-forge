#!/usr/bin/env python3
"""Build preset catalog for Gate 1 validation.

This script orchestrates the full preset catalog pipeline:
1. Discover Ableton presets
2. Generate ALS files with test MIDI sequences
3. (Manual step) Render ALS files to audio in Ableton
4. Extract fingerprints from rendered audio
5. Build catalog with similarity index

Usage:
    # Step 1: Generate ALS files
    python scripts/build_preset_catalog.py generate --instrument Analog

    # Step 2: Manually render in Ableton (see generated instructions)

    # Step 3: Build catalog from rendered audio
    python scripts/build_preset_catalog.py fingerprint

    # Step 4: Run retrieval validation (Gate 1)
    python scripts/build_preset_catalog.py validate
"""
from __future__ import annotations

import argparse
import datetime as _dt
import logging
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from tone_forge.preset_catalog.preset_discovery import (
    discover_presets,
    safe_filename,
)
from tone_forge.preset_catalog.catalog_builder import (
    CatalogBuilder,
    PresetCatalog,
    extract_preset_fingerprint,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Default output directory
DEFAULT_OUTPUT_DIR = Path(__file__).parent.parent / "preset_catalog_output"

# Scripts that the pipeline invokes as subprocesses.
SCRIPTS_DIR = Path(__file__).parent
AUDIT_SCRIPT = SCRIPTS_DIR / "audit_als_param_content.py"
RENDER_AUDIT_SCRIPT = SCRIPTS_DIR / "audit_render_output.py"
INTEGRITY_GATE_SCRIPT = SCRIPTS_DIR / "catalog_integrity_gate.py"


def _backup_existing_outputs(output_dir: Path) -> Optional[Path]:
    """Rename existing als/audio/catalog subdirs to _backup_<UTC-ts>/.

    Preserves forensic evidence (so the RCA artifacts remain intact) before
    regeneration overwrites them. Returns the backup root, or None if there
    was nothing to back up.
    """
    subdirs = [output_dir / "als", output_dir / "audio", output_dir / "catalog"]
    has_content = any(d.exists() and any(d.iterdir()) for d in subdirs if d.exists())
    if not has_content:
        return None

    ts = _dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    backup_root = output_dir / f"_backup_{ts}"
    backup_root.mkdir(parents=True, exist_ok=False)
    for d in subdirs:
        if d.exists() and any(d.iterdir()):
            target = backup_root / d.name
            shutil.move(str(d), str(target))
            logger.info(f"  backed up {d.name} -> {target}")
    return backup_root


def cmd_generate(args):
    """Generate ALS files for rendering."""
    output_dir = Path(args.output_dir)
    instruments = args.instruments.split(",") if args.instruments else ["Analog"]

    logger.info(f"Generating ALS files for: {instruments}")
    logger.info(f"Output directory: {output_dir}")

    # Back up existing als/audio/catalog so a fresh generation does not
    # destroy forensic evidence from the previous (collapsed) catalog.
    if not args.no_backup:
        backup_root = _backup_existing_outputs(output_dir)
        if backup_root:
            logger.info(f"Preserved previous outputs at: {backup_root}")
        else:
            logger.info("No existing outputs to back up.")

    builder = CatalogBuilder(
        output_dir=output_dir,
        instruments=instruments,
        tempo=args.tempo,
    )

    # Discover presets
    presets = builder.discover_presets()

    if not presets:
        logger.error("No presets found!")
        return 1

    # Limit if specified
    if args.limit:
        presets = presets[:args.limit]
        logger.info(f"Limited to {len(presets)} presets")

    # Generate ALS files
    als_results = builder.generate_als_files(presets)

    # Generate rendering instructions
    builder.generate_manual_render_instructions(presets)

    # Pre-render structural audit — fails-loud if any generated ALS has
    # an empty/under-populated UltraAnalog device chain.
    if not args.skip_audit:
        logger.info("")
        logger.info("Running pre-render ALS structural audit...")
        result = subprocess.run(
            [sys.executable, str(AUDIT_SCRIPT), "--als-dir", str(builder.als_dir)],
            cwd=str(Path(__file__).parent.parent),
        )
        if result.returncode != 0:
            logger.error(
                "ALS structural audit FAILED — refusing to declare generation "
                "ready. Inspect the report and fix preset_als_generator.py."
            )
            return 1
        logger.info("ALS structural audit PASSED.")

    # Generate AppleScript (optional)
    if args.applescript:
        builder.generate_batch_render_script(presets)

    logger.info(f"\n{'='*60}")
    logger.info("ALS GENERATION COMPLETE")
    logger.info(f"{'='*60}")
    logger.info(f"Generated {len(als_results)} ALS files")
    logger.info(f"ALS directory: {builder.als_dir}")
    logger.info(f"Audio output directory: {builder.audio_dir}")
    logger.info("")
    logger.info("NEXT STEPS:")
    logger.info("")
    logger.info("Option A - Use AppleScript helper (recommended):")
    logger.info("  1. Open scripts/render_presets_fast.applescript in Script Editor")
    logger.info("  2. Click Run")
    logger.info("  3. Click 'Export' for each preset when prompted")
    logger.info("")
    logger.info("Option B - Manual:")
    logger.info("  1. Open each ALS file in Ableton Live")
    logger.info("  2. Export to WAV (Cmd+Shift+R)")
    logger.info(f"  3. Save to: {builder.audio_dir}")
    logger.info("")
    logger.info(f"Then run: python {__file__} fingerprint")
    logger.info("")
    logger.info(f"See: {output_dir}/RENDER_INSTRUCTIONS.md for details")

    return 0


def cmd_fingerprint(args):
    """Extract fingerprints from rendered audio."""
    output_dir = Path(args.output_dir)
    instruments = args.instruments.split(",") if args.instruments else ["Analog"]

    builder = CatalogBuilder(
        output_dir=output_dir,
        instruments=instruments,
    )

    # Check for audio files
    audio_files = list(builder.audio_dir.glob("*.wav"))
    if not audio_files:
        logger.error(f"No audio files found in {builder.audio_dir}")
        logger.error("Did you complete the rendering step?")
        return 1

    logger.info(f"Found {len(audio_files)} audio files")

    # Pre-fingerprint render-output audit — fails-loud if any expected WAV is
    # missing / silent / short / drifted to a sibling folder (e.g. equivalence/).
    # This catches the exact failure mode from the previous batch render where
    # the Ableton export dialog defaulted to the wrong folder.
    if not args.skip_render_audit:
        logger.info("")
        logger.info("Running pre-fingerprint render-output audit...")
        audit_cmd = [
            sys.executable,
            str(RENDER_AUDIT_SCRIPT),
            "--als-dir", str(builder.als_dir),
            "--audio-dir", str(builder.audio_dir),
        ]
        if args.auto_fix_drift:
            audit_cmd.append("--auto-fix")
        result = subprocess.run(audit_cmd, cwd=str(Path(__file__).parent.parent))
        if result.returncode != 0:
            logger.error(
                "RENDER-OUTPUT AUDIT FAILED — refusing to fingerprint a "
                "partial render. Inspect the report at "
                f"{output_dir}/retrieval/render_output_audit.json, "
                "re-render the failing presets, then re-run fingerprint."
            )
            return 1
        logger.info("Render-output audit PASSED.")
        # Re-scan after potential drift recovery so the fingerprint loop sees
        # any WAVs that were just moved back into audio/.
        audio_files = list(builder.audio_dir.glob("*.wav"))

    # Re-discover presets to get metadata
    presets = builder.discover_presets()
    preset_map = {safe_filename(p.preset_id): p for p in presets}

    # Build catalog
    catalog = PresetCatalog()

    for audio_path in audio_files:
        # Find matching preset
        stem = audio_path.stem
        preset_info = preset_map.get(stem)

        if not preset_info:
            logger.warning(f"No preset info for: {stem}")
            continue

        # Locate the matching ALS for provenance.
        als_path = builder.als_dir / f"{stem}.als"
        als_path_arg: Optional[Path] = als_path if als_path.exists() else None

        # The test sequence is selected by sound_type; use that as the
        # provenance label so the integrity gate can detect leakage of a
        # single sequence across multiple sound types.
        test_seq_name = f"sound_type:{preset_info.sound_type}"

        try:
            fingerprint = extract_preset_fingerprint(
                audio_path,
                preset_info,
                als_path=als_path_arg,
                test_sequence_name=test_seq_name,
            )
            catalog.add(fingerprint)
            logger.info(f"Fingerprinted: {preset_info.name}")
        except Exception as e:
            logger.error(f"Failed: {preset_info.name}: {e}")

    # Save catalog
    catalog_name = f"catalog_{instruments[0].lower()}"
    builder.save_catalog(catalog, catalog_name)

    logger.info(f"\n{'='*60}")
    logger.info("FINGERPRINT EXTRACTION COMPLETE")
    logger.info(f"{'='*60}")
    logger.info(f"Catalog: {len(catalog.presets)} presets")
    logger.info(f"Saved to: {builder.catalog_dir}/{catalog_name}.json")

    # Catalog Integrity Gate — refuses to declare the catalog ready unless
    # all six criteria (RENDER_PIPELINE_RCA.md §7) pass.
    if not args.skip_gate:
        logger.info("")
        logger.info("Running Catalog Integrity Gate...")
        catalog_path = builder.catalog_dir / f"{catalog_name}.json"
        result = subprocess.run(
            [
                sys.executable,
                str(INTEGRITY_GATE_SCRIPT),
                "--catalog", str(catalog_path),
                "--audio-root", str(builder.audio_dir),
            ],
            cwd=str(Path(__file__).parent.parent),
        )
        if result.returncode != 0:
            logger.error(
                "CATALOG INTEGRITY GATE: FAIL — catalog is NOT ready for "
                "retrieval. Investigate the failing criteria before any "
                "downstream embedding / ranking / reconstruction work."
            )
            return 1
        logger.info("CATALOG INTEGRITY GATE: PASS")
        logger.info("")
        logger.info("NEXT STEP:")
        logger.info(f"Run: python {__file__} validate")

    return 0


def cmd_integrity(args):
    """Run the Catalog Integrity Gate standalone on an existing catalog."""
    output_dir = Path(args.output_dir)
    catalog_path = output_dir / "catalog" / f"catalog_{args.instrument.lower()}.json"
    audio_root = output_dir / "audio"

    if not catalog_path.exists():
        logger.error(f"Catalog not found: {catalog_path}")
        return 1

    result = subprocess.run(
        [
            sys.executable,
            str(INTEGRITY_GATE_SCRIPT),
            "--catalog", str(catalog_path),
            "--audio-root", str(audio_root),
        ],
        cwd=str(Path(__file__).parent.parent),
    )
    return result.returncode


def cmd_validate(args):
    """Run Gate 1 retrieval validation."""
    output_dir = Path(args.output_dir)
    catalog_path = output_dir / "catalog" / f"catalog_{args.instrument.lower()}.json"

    if not catalog_path.exists():
        logger.error(f"Catalog not found: {catalog_path}")
        logger.error("Did you complete the fingerprint step?")
        return 1

    # Load catalog
    catalog = PresetCatalog.load(catalog_path)

    if len(catalog.presets) < 25:
        logger.error(f"Need at least 25 presets for validation, found {len(catalog.presets)}")
        return 1

    logger.info(f"Loaded catalog with {len(catalog.presets)} presets")

    # Select random query presets
    import random
    random.seed(42)  # Reproducible
    query_indices = random.sample(range(len(catalog.presets)), min(25, len(catalog.presets)))

    logger.info(f"\n{'='*60}")
    logger.info("GATE 1: RETRIEVAL VALIDATION")
    logger.info(f"{'='*60}")
    logger.info(f"Query presets: {len(query_indices)}")
    logger.info("")

    results = []

    for idx in query_indices:
        query = catalog.presets[idx]

        # Find top-5 similar
        similar = catalog.find_similar(query, k=5)

        logger.info(f"\nQuery: {query.preset_name} ({query.category})")
        logger.info(f"  Sound type: {query.sound_type}")
        logger.info("  Top-5 matches:")

        for i, (match, distance) in enumerate(similar, 1):
            logger.info(f"    {i}. {match.preset_name} ({match.category}) - dist={distance:.3f}")

        results.append({
            "query": query.preset_name,
            "query_category": query.category,
            "matches": [
                {
                    "name": m.preset_name,
                    "category": m.category,
                    "distance": d,
                }
                for m, d in similar
            ],
        })

    # Summary
    logger.info(f"\n{'='*60}")
    logger.info("VALIDATION SUMMARY")
    logger.info(f"{'='*60}")
    logger.info("")
    logger.info("Manual evaluation required:")
    logger.info("For each query, rate top-5 relevance (1-5):")
    logger.info("  5 = Perfect match")
    logger.info("  4 = Good match")
    logger.info("  3 = Acceptable")
    logger.info("  2 = Poor")
    logger.info("  1 = Unrelated")
    logger.info("")
    logger.info("Gate 1 passes if:")
    logger.info("  - Average top-5 relevance >= 3.5")
    logger.info("  - 80%+ queries have at least one match rated 4+")
    logger.info("  - <10% of queries have all matches rated <=2")

    # Save results
    import json
    results_path = output_dir / "validation_results.json"
    results_path.write_text(json.dumps(results, indent=2))
    logger.info(f"\nResults saved to: {results_path}")

    return 0


def cmd_cluster(args):
    """Run clustering analysis on catalog."""
    output_dir = Path(args.output_dir)
    catalog_path = output_dir / "catalog" / f"catalog_{args.instrument.lower()}.json"

    if not catalog_path.exists():
        logger.error(f"Catalog not found: {catalog_path}")
        return 1

    catalog = PresetCatalog.load(catalog_path)

    logger.info(f"Building similarity matrix for {len(catalog.presets)} presets")

    # Build similarity matrix
    matrix = catalog.build_similarity_matrix()

    # Hierarchical clustering
    try:
        from scipy.cluster.hierarchy import linkage, fcluster, dendrogram
        from scipy.spatial.distance import squareform
        import matplotlib.pyplot as plt

        # Convert to condensed form
        condensed = squareform(matrix)

        # Hierarchical clustering
        Z = linkage(condensed, method='ward')

        # Cut into clusters
        n_clusters = args.n_clusters or 8
        clusters = fcluster(Z, t=n_clusters, criterion='maxclust')

        # Analyze clusters
        logger.info(f"\n{'='*60}")
        logger.info(f"CLUSTERING RESULTS ({n_clusters} clusters)")
        logger.info(f"{'='*60}")

        for c in range(1, n_clusters + 1):
            cluster_presets = [
                catalog.presets[i]
                for i in range(len(catalog.presets))
                if clusters[i] == c
            ]

            if cluster_presets:
                categories = {}
                for p in cluster_presets:
                    categories[p.category] = categories.get(p.category, 0) + 1

                top_category = max(categories.items(), key=lambda x: x[1])[0]

                logger.info(f"\nCluster {c} ({len(cluster_presets)} presets)")
                logger.info(f"  Dominant category: {top_category}")
                logger.info(f"  Categories: {dict(sorted(categories.items(), key=lambda x: -x[1]))}")
                logger.info(f"  Examples: {[p.preset_name for p in cluster_presets[:5]]}")

        # Save dendrogram
        plt.figure(figsize=(12, 8))
        dendrogram(Z, labels=[p.preset_name[:15] for p in catalog.presets], leaf_rotation=90)
        plt.title("Preset Similarity Dendrogram")
        plt.tight_layout()

        dendrogram_path = output_dir / "clustering_dendrogram.png"
        plt.savefig(dendrogram_path, dpi=150)
        logger.info(f"\nDendrogram saved to: {dendrogram_path}")

    except ImportError as e:
        logger.error(f"Clustering requires scipy and matplotlib: {e}")
        return 1

    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Build preset catalog for ToneForge",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help=f"Output directory (default: {DEFAULT_OUTPUT_DIR})",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # Generate command
    gen_parser = subparsers.add_parser("generate", help="Generate ALS files")
    gen_parser.add_argument(
        "--instruments",
        default="Analog",
        help="Comma-separated list of instruments (default: Analog)",
    )
    gen_parser.add_argument(
        "--tempo",
        type=float,
        default=120.0,
        help="BPM for test sequences (default: 120)",
    )
    gen_parser.add_argument(
        "--limit",
        type=int,
        help="Limit number of presets (for testing)",
    )
    gen_parser.add_argument(
        "--applescript",
        action="store_true",
        help="Generate AppleScript for batch rendering",
    )
    gen_parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip backup of existing als/audio/catalog directories.",
    )
    gen_parser.add_argument(
        "--skip-audit",
        action="store_true",
        help="Skip the post-generation ALS structural audit (not recommended).",
    )

    # Fingerprint command
    fp_parser = subparsers.add_parser("fingerprint", help="Extract fingerprints")
    fp_parser.add_argument(
        "--instruments",
        default="Analog",
        help="Comma-separated list of instruments",
    )
    fp_parser.add_argument(
        "--skip-gate",
        action="store_true",
        help="Skip the Catalog Integrity Gate after fingerprinting (not recommended).",
    )
    fp_parser.add_argument(
        "--skip-render-audit",
        action="store_true",
        help="Skip the pre-fingerprint render-output audit (not recommended).",
    )
    fp_parser.add_argument(
        "--auto-fix-drift",
        action="store_true",
        help="Let the render audit move drifted WAVs (e.g. from equivalence/) "
             "back into audio/ before judging the result.",
    )

    # Integrity command (re-run the gate without re-fingerprinting)
    int_parser = subparsers.add_parser(
        "integrity",
        help="Run the Catalog Integrity Gate on an existing catalog.",
    )
    int_parser.add_argument(
        "--instrument",
        default="Analog",
        help="Instrument catalog to gate (default: Analog)",
    )

    # Validate command
    val_parser = subparsers.add_parser("validate", help="Run retrieval validation")
    val_parser.add_argument(
        "--instrument",
        default="Analog",
        help="Instrument to validate",
    )

    # Cluster command
    cluster_parser = subparsers.add_parser("cluster", help="Run clustering analysis")
    cluster_parser.add_argument(
        "--instrument",
        default="Analog",
        help="Instrument to analyze",
    )
    cluster_parser.add_argument(
        "--n-clusters",
        type=int,
        default=8,
        help="Number of clusters (default: 8)",
    )

    args = parser.parse_args()

    if args.command == "generate":
        return cmd_generate(args)
    elif args.command == "fingerprint":
        return cmd_fingerprint(args)
    elif args.command == "integrity":
        return cmd_integrity(args)
    elif args.command == "validate":
        return cmd_validate(args)
    elif args.command == "cluster":
        return cmd_cluster(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
