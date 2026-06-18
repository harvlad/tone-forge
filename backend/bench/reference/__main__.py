"""``python -m bench.reference`` — Phase 2 CLI.

Subcommands:

    ingest        Read a normalized reference JSON file and append
                  per-section evidence records.

    list          Walk the references directory and summarise what's
                  been curated (song_id + source + section count).

    template      Print a stub reference JSON for a given song so a
                  curator can fill it in by hand.

The default references directory is ``backend/data/references/``.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..evidence.store import EvidenceStore
from .ingest import ingest_reference_file, reference_file_to_records
from .schema import load_reference_file, RawReferenceFile, RawReferenceSection


# Default location for curator-supplied reference files.
_DEFAULT_REFERENCES = Path(__file__).resolve().parents[2] / "data" / "references"


# ---------------------------------------------------------------------------
# ingest
# ---------------------------------------------------------------------------


def _cmd_ingest(args: argparse.Namespace) -> int:
    target = Path(args.path)
    if not target.exists():
        print(f"reference file not found: {target}", file=sys.stderr)
        return 1
    ref = load_reference_file(target)

    if args.dry_run:
        records = reference_file_to_records(ref)
        print(
            f"[dry-run] would append {len(records)} records "
            f"from {ref.source}@{ref.version} for song {ref.song_id}"
        )
        return 0

    store = EvidenceStore(root=args.store_root)
    n = ingest_reference_file(ref, store)
    print(
        f"appended {n} records from {ref.source}@{ref.version} "
        f"for song {ref.song_id} into {store.root}"
    )
    return 0


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def _cmd_list(args: argparse.Namespace) -> int:
    root = Path(args.references_root) if args.references_root else _DEFAULT_REFERENCES
    if not root.exists():
        print(f"references directory does not exist: {root}", file=sys.stderr)
        return 1

    rows: list[dict] = []
    for path in sorted(root.glob("*.json")):
        try:
            ref = load_reference_file(path)
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            print(f"  ! {path.name}: {exc}", file=sys.stderr)
            continue
        rows.append({
            "file": path.name,
            "song_id": ref.song_id,
            "source": ref.source,
            "version": ref.version,
            "n_sections": len(ref.sections),
            "fetched_at_utc": ref.fetched_at_utc,
        })

    if args.json:
        print(json.dumps(rows, indent=2, sort_keys=False))
    else:
        print(f"references root: {root}")
        if not rows:
            print("  (no reference files)")
            return 0
        for row in rows:
            print(
                f"  {row['file']:40s}  song={row['song_id']}  "
                f"source={row['source']}@{row['version']}  "
                f"sections={row['n_sections']}"
            )
    return 0


# ---------------------------------------------------------------------------
# template
# ---------------------------------------------------------------------------


def _cmd_template(args: argparse.Namespace) -> int:
    """Emit a stub reference file. Curator fills in the labels."""
    sections = tuple(
        RawReferenceSection(
            section_id=f"{args.song_id}:{i:04d}",
            labels={
                "guidance_mode": None,
                "chord_sequence": [],
            },
        )
        for i in range(args.n_sections)
    )
    ref = RawReferenceFile(
        song_id=args.song_id,
        source=args.source,
        version=args.version or "manual-v1",
        fetched_at_utc="REPLACE_WITH_ISO_UTC_TIMESTAMP",
        sections=sections,
        source_url=args.source_url,
    )
    payload = {
        "song_id": ref.song_id,
        "source": ref.source,
        "version": ref.version,
        "fetched_at_utc": ref.fetched_at_utc,
        "source_url": ref.source_url,
        "sections": [
            {"section_id": s.section_id, "labels": dict(s.labels)}
            for s in ref.sections
        ],
    }
    print(json.dumps(payload, indent=2, sort_keys=False))
    return 0


# ---------------------------------------------------------------------------
# argparse wiring
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m bench.reference",
        description="JAM Learning System — Reference Import (Phase 2).",
    )
    parser.add_argument(
        "--store-root", type=Path, default=None,
        help="Override the default evidence store root.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_ingest = sub.add_parser("ingest", help="Append a reference file to the store.")
    p_ingest.add_argument("path", type=Path,
                          help="Path to a normalized reference JSON file.")
    p_ingest.add_argument("--dry-run", action="store_true",
                          help="Don't write; print count only.")
    p_ingest.set_defaults(func=_cmd_ingest)

    p_list = sub.add_parser("list", help="Summarise curated reference files.")
    p_list.add_argument("--references-root", type=Path, default=None,
                        help="Override default backend/data/references/.")
    p_list.add_argument("--json", action="store_true", help="Emit JSON rows.")
    p_list.set_defaults(func=_cmd_list)

    p_tpl = sub.add_parser("template", help="Print a stub reference JSON to stdout.")
    p_tpl.add_argument("--song-id", required=True,
                       help="The 16-hex song id (look up in evidence store first).")
    p_tpl.add_argument("--source", required=True,
                       choices=["songsterr", "ultimate_guitar", "chordify", "manual"],
                       help="Provider tag.")
    p_tpl.add_argument("--version", default=None,
                       help="Provider revision string (default: manual-v1).")
    p_tpl.add_argument("--source-url", default=None,
                       help="Provider URL for human re-fetch.")
    p_tpl.add_argument("--n-sections", type=int, default=1,
                       help="Number of section stubs to emit.")
    p_tpl.set_defaults(func=_cmd_template)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
