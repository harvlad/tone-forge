"""``python -m bench.evidence`` — Phase 1 CLI.

Subcommands:

    stats         summarise the evidence store (record count, songs,
                  sections, disk usage, schema versions seen).

    replay        ingest ``backend/data/history.json`` into the
                  evidence store. Idempotent in the sense that
                  re-running appends *new* records with the current
                  timestamp; older records remain in place. Use
                  ``--dry-run`` to print what would be written.

    show          dump one or all records matching a filter
                  (song_id / section_id / date_prefix). For human
                  inspection; not intended as a programmatic API.

This CLI lives alongside the existing ``python -m bench.corpus`` /
``python -m bench.benchmark`` / ``python -m bench.sweep`` runners.
The top-level ``python -m bench`` dispatcher is updated separately
to route the ``evidence`` subcommand.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from .schema import _record_to_jsonable
from .store import EvidenceStore
from .writer import from_analysis_result


# Default location of the API's rolling history log.
_DEFAULT_HISTORY = Path(__file__).resolve().parents[2] / "data" / "history.json"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------


def _cmd_stats(args: argparse.Namespace) -> int:
    store = EvidenceStore(root=args.store_root)
    n_records = 0
    songs: set[str] = set()
    sections: set[tuple[str, str]] = set()
    schema_versions: dict[int, int] = {}
    with_consensus = 0
    with_references = 0
    with_corrections = 0
    for record in store.iter_records():
        n_records += 1
        songs.add(record.song_id)
        sections.add((record.song_id, record.section_id))
        schema_versions[record.schema_version] = (
            schema_versions.get(record.schema_version, 0) + 1
        )
        if record.consensus_output is not None:
            with_consensus += 1
        if record.reference_sources:
            with_references += 1
        if record.corrections:
            with_corrections += 1

    summary: dict[str, object] = {
        "store_root": str(store.root),
        "files": [p.name for p in store.file_paths()],
        "n_files": len(store.file_paths()),
        "n_records": n_records,
        "n_unique_songs": len(songs),
        "n_unique_sections": len(sections),
        "n_with_reference_sources": with_references,
        "n_with_consensus": with_consensus,
        "n_with_corrections": with_corrections,
        "schema_versions": schema_versions,
        "total_bytes": store.total_bytes(),
    }
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=False))
    else:
        print(f"Evidence store: {summary['store_root']}")
        print(f"  files:               {summary['n_files']}")
        print(f"  records:             {summary['n_records']}")
        print(f"  unique songs:        {summary['n_unique_songs']}")
        print(f"  unique sections:     {summary['n_unique_sections']}")
        print(f"  w/ reference src:    {summary['n_with_reference_sources']}")
        print(f"  w/ consensus:        {summary['n_with_consensus']}")
        print(f"  w/ corrections:      {summary['n_with_corrections']}")
        print(f"  schema versions:     {summary['schema_versions']}")
        print(f"  total bytes:         {summary['total_bytes']:,}")
    return 0


# ---------------------------------------------------------------------------
# replay
# ---------------------------------------------------------------------------


def _load_history(path: Path) -> list[Mapping[str, object]]:
    if not path.exists():
        print(f"history file not found: {path}", file=sys.stderr)
        return []
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "entries" in data:
        return list(data["entries"])
    print(
        f"unrecognised history.json shape (got {type(data).__name__}); "
        "expected list of analysis results",
        file=sys.stderr,
    )
    return []


def _cmd_replay(args: argparse.Namespace) -> int:
    history_path = Path(args.history) if args.history else _DEFAULT_HISTORY
    entries = _load_history(history_path)
    if not entries:
        return 1

    # Use one timestamp for the whole replay so all generated records
    # group cleanly into one ingest event in time-window queries.
    ts = _utc_now_iso()
    store = EvidenceStore(root=args.store_root)

    n_songs = 0
    n_records = 0
    n_skipped = 0
    for entry in entries:
        if not isinstance(entry, Mapping):
            n_skipped += 1
            continue
        # backend/data/history.json wraps the AnalysisResult payload
        # inside ``entry["result"]`` (alongside id/timestamp/summary
        # metadata). The writer expects the flat AnalysisResult dict,
        # so unwrap here. Fall back to the envelope itself if no
        # ``result`` key is present (older log shapes / direct
        # AnalysisResult dumps).
        payload: Mapping[str, object]
        nested = entry.get("result")
        if isinstance(nested, Mapping) and "sections" in nested:
            payload = nested
        else:
            payload = entry
        records = from_analysis_result(payload, timestamp_utc=ts)
        if not records:
            n_skipped += 1
            continue
        n_songs += 1
        n_records += len(records)
        if not args.dry_run:
            store.extend(records)

    if args.dry_run:
        print(f"[dry-run] would replay {n_songs} songs / {n_records} records "
              f"({n_skipped} entries skipped) into {store.root}")
    else:
        print(f"replayed {n_songs} songs / {n_records} records "
              f"({n_skipped} entries skipped) into {store.root}")
    return 0


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


def _cmd_show(args: argparse.Namespace) -> int:
    store = EvidenceStore(root=args.store_root)
    n = 0
    for record in store.iter_records(date_prefix=args.date_prefix):
        if args.song_id and record.song_id != args.song_id:
            continue
        if args.section_id and record.section_id != args.section_id:
            continue
        print(json.dumps(_record_to_jsonable(record), sort_keys=False, separators=(",", ":")))
        n += 1
        if args.limit > 0 and n >= args.limit:
            break
    if n == 0:
        print("(no matching records)", file=sys.stderr)
        return 1
    return 0


# ---------------------------------------------------------------------------
# argparse wiring
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m bench.evidence",
        description="JAM Learning System — Evidence Store (Phase 1).",
    )
    parser.add_argument(
        "--store-root",
        type=Path,
        default=None,
        help="Override the default store root (backend/data/evidence/).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_stats = sub.add_parser("stats", help="Summarise the evidence store.")
    p_stats.add_argument("--json", action="store_true", help="Emit JSON.")
    p_stats.set_defaults(func=_cmd_stats)

    p_replay = sub.add_parser(
        "replay",
        help="Replay backend/data/history.json into the evidence store.",
    )
    p_replay.add_argument(
        "--history", type=Path, default=None,
        help="Path to history.json (default: backend/data/history.json).",
    )
    p_replay.add_argument(
        "--dry-run", action="store_true",
        help="Don't write; print counts only.",
    )
    p_replay.set_defaults(func=_cmd_replay)

    p_show = sub.add_parser("show", help="Print records as JSONL.")
    p_show.add_argument("--song-id", type=str, default=None)
    p_show.add_argument("--section-id", type=str, default=None)
    p_show.add_argument("--date-prefix", type=str, default=None,
                        help="Filter by daily filename prefix, e.g. 2026-06.")
    p_show.add_argument("--limit", type=int, default=0,
                        help="Max records to print (0 = unlimited).")
    p_show.set_defaults(func=_cmd_show)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
