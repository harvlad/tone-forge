"""``python -m bench.corpus_consensus`` — Phase 5 CLI.

Subcommands:

    stats   Walk the evidence store, count consensus-corpus entries
            grouped by guidance_mode, with min/mean confidence and a
            jam-output coverage breakdown.

    list    Stream every corpus entry as a one-liner (song_id /
            section_id / guidance_mode / confidence). Supports
            ``--json`` for downstream tooling.

    export  Dump the corpus as a single JSON file (array of entries)
            so Phase 6's sweep gate can mmap a snapshot rather than
            re-iterating the JSONL store on every run.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable

from ..evidence.store import EvidenceStore
from .loader import (
    ConsensusCorpusConfig,
    ConsensusCorpusEntry,
    iter_consensus_corpus,
    summarise_consensus_corpus,
)


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------


def _entry_to_jsonable(entry: ConsensusCorpusEntry) -> dict:
    return {
        "song_id": entry.song_id,
        "section_id": entry.section_id,
        "ref_guidance_mode": entry.ref_guidance_mode,
        "ref_chord_sequence": (
            list(entry.ref_chord_sequence)
            if entry.ref_chord_sequence is not None else None
        ),
        "ref_confidence": entry.ref_confidence,
        "ref_agreement": dict(entry.ref_agreement),
        "latest_jam_output": (
            dict(entry.latest_jam_output)
            if entry.latest_jam_output is not None else None
        ),
        "consensus_timestamp_utc": entry.consensus_timestamp_utc,
        "jam_timestamp_utc": entry.jam_timestamp_utc,
    }


def _sorted_entries(it: Iterable[ConsensusCorpusEntry]) -> list[ConsensusCorpusEntry]:
    return sorted(it, key=lambda e: (e.song_id, e.section_id))


def _config_from_args(args: argparse.Namespace) -> ConsensusCorpusConfig:
    return ConsensusCorpusConfig(
        min_confidence=args.min_confidence,
        require_jam_output=args.require_jam_output,
        song_id=args.song_id,
    )


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------


def _cmd_stats(args: argparse.Namespace) -> int:
    store = EvidenceStore(root=args.store_root)
    summary = summarise_consensus_corpus(store, config=_config_from_args(args))
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=False))
        return 0
    print(f"Evidence store: {store.root}")
    print(f"  corpus entries:        {summary['n_entries']}")
    print(f"  unique songs:          {summary['n_unique_songs']}")
    print(f"  w/ jam_output:         {summary['n_with_jam_output']}")
    print(f"  without jam_output:    {summary['n_without_jam_output']}")
    print(f"  min confidence:        {summary['min_confidence']:.3f}")
    print(f"  mean confidence:       {summary['mean_confidence']:.3f}")
    print("  by guidance_mode:")
    for mode, n in sorted(summary["by_guidance_mode"].items()):
        print(f"    {mode:20s} {n}")
    return 0


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def _cmd_list(args: argparse.Namespace) -> int:
    store = EvidenceStore(root=args.store_root)
    entries = _sorted_entries(
        iter_consensus_corpus(store, config=_config_from_args(args))
    )
    if args.json:
        payload = {
            "n_entries": len(entries),
            "entries": [_entry_to_jsonable(e) for e in entries],
        }
        print(json.dumps(payload, indent=2, sort_keys=False))
        return 0
    if not entries:
        print("(consensus corpus is empty at the current threshold)")
        return 0
    for e in entries:
        mode = e.ref_guidance_mode or "<undecided>"
        jam_tag = "jam" if e.latest_jam_output is not None else "no-jam"
        print(
            f"  {e.section_id}  mode={mode:6s}  "
            f"conf={e.ref_confidence:.2f}  [{jam_tag}]"
        )
    return 0


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


def _cmd_export(args: argparse.Namespace) -> int:
    store = EvidenceStore(root=args.store_root)
    entries = _sorted_entries(
        iter_consensus_corpus(store, config=_config_from_args(args))
    )
    payload: dict[str, Any] = {
        "n_entries": len(entries),
        "min_confidence": args.min_confidence,
        "entries": [_entry_to_jsonable(e) for e in entries],
    }
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(payload, indent=2, sort_keys=False), encoding="utf-8"
    )
    sys.stderr.write(
        f"wrote {len(entries)} entries -> {out_path}\n"
    )
    return 0


# ---------------------------------------------------------------------------
# argparse wiring
# ---------------------------------------------------------------------------


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--min-confidence", type=float, default=0.8,
        help="Skip consensus entries below this confidence (default 0.8).",
    )
    parser.add_argument(
        "--require-jam-output", action="store_true",
        help="Only include sections that also have a cached jam_output.",
    )
    parser.add_argument(
        "--song-id", default=None,
        help="Restrict the corpus to a single song_id.",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m bench.corpus_consensus",
        description="JAM Learning System — Consensus-derived corpus loader (Phase 5).",
    )
    parser.add_argument(
        "--store-root", type=Path, default=None,
        help="Override the default evidence store root.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_stats = sub.add_parser("stats", help="Corpus counts + confidence summary.")
    _add_common(p_stats)
    p_stats.add_argument("--json", action="store_true")
    p_stats.set_defaults(func=_cmd_stats)

    p_list = sub.add_parser("list", help="Per-entry one-line dump.")
    _add_common(p_list)
    p_list.add_argument("--json", action="store_true")
    p_list.set_defaults(func=_cmd_list)

    p_export = sub.add_parser("export", help="Write corpus snapshot as JSON.")
    _add_common(p_export)
    p_export.add_argument(
        "--output", required=True,
        help="Output JSON path (parents created if missing).",
    )
    p_export.set_defaults(func=_cmd_export)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
