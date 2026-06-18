"""``python -m bench.consensus`` — Phase 3 CLI.

Subcommands:

    build        Walk the evidence store, append one consensus record
                 per ``(song_id, section_id)`` that has at least one
                 reference source. Returns counts.

    show         Print the latest consensus record per section as
                 JSONL. Useful for spot-checking the agreement scoring.

    inspect      Print the per-key vote tally + agreement for one
                 ``(song_id, section_id)`` so a curator can see exactly
                 why confidence landed where it did.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..evidence.schema import _record_to_jsonable
from ..evidence.store import EvidenceStore
from .builder import (
    ConsensusBuilderConfig,
    build_consensus_for_section,
    build_consensus_for_store,
)


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------


def _cmd_build(args: argparse.Namespace) -> int:
    store = EvidenceStore(root=args.store_root)
    config = ConsensusBuilderConfig(
        tempo_bucket_bpm=args.tempo_bucket_bpm,
    )
    n = build_consensus_for_store(store, config=config)
    print(f"wrote {n} consensus records into {store.root}")
    return 0


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


def _cmd_show(args: argparse.Namespace) -> int:
    store = EvidenceStore(root=args.store_root)
    seen: set[tuple[str, str]] = set()
    latest: dict[tuple[str, str], dict] = {}
    for rec in store.iter_records():
        if rec.consensus_output is None:
            continue
        if args.song_id and rec.song_id != args.song_id:
            continue
        if args.section_id and rec.section_id != args.section_id:
            continue
        key = (rec.song_id, rec.section_id)
        existing = latest.get(key)
        if existing is None or rec.timestamp_utc > existing["timestamp_utc"]:
            latest[key] = _record_to_jsonable(rec)
        seen.add(key)

    if not latest:
        print("(no consensus records found)", file=sys.stderr)
        return 1
    for key in sorted(latest.keys()):
        rec = latest[key]
        if args.confidence_min is not None:
            conf = rec.get("consensus_output", {}).get("confidence", 0.0)
            if conf < args.confidence_min:
                continue
        print(json.dumps(rec, sort_keys=False, separators=(",", ":")))
    return 0


# ---------------------------------------------------------------------------
# inspect
# ---------------------------------------------------------------------------


def _cmd_inspect(args: argparse.Namespace) -> int:
    store = EvidenceStore(root=args.store_root)
    matching = [
        r for r in store.iter_records()
        if r.song_id == args.song_id and r.section_id == args.section_id
    ]
    if not matching:
        print("(no records for that section)", file=sys.stderr)
        return 1

    n_refs = sum(len(r.reference_sources) for r in matching)
    consensus = build_consensus_for_section(matching)

    print(f"song_id:           {args.song_id}")
    print(f"section_id:        {args.section_id}")
    print(f"n_records:         {len(matching)}")
    print(f"n_reference_srcs:  {n_refs}")
    if consensus is None:
        print("(no reference sources -> no consensus)")
        return 0
    print(f"consensus_mode:    {consensus.guidance_mode}")
    print(f"chord_sequence:    {consensus.chord_sequence}")
    print(f"confidence:        {consensus.confidence:.3f}")
    print("agreement:")
    for k, v in consensus.agreement.items():
        print(f"  {k:18s} {v:.3f}")
    print("votes:")
    for k, tally in consensus.votes.items():
        if not tally:
            print(f"  {k:18s} (no data)")
            continue
        formatted = ", ".join(f"{val!r}={cnt}" for val, cnt in tally.items())
        print(f"  {k:18s} {formatted}")
    return 0


# ---------------------------------------------------------------------------
# argparse wiring
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m bench.consensus",
        description="JAM Learning System — Consensus Builder (Phase 3).",
    )
    parser.add_argument(
        "--store-root", type=Path, default=None,
        help="Override the default evidence store root.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_build = sub.add_parser("build", help="Build consensus records for every section.")
    p_build.add_argument("--tempo-bucket-bpm", type=float, default=1.0,
                         help="BPM granularity for tempo voting (default 1.0).")
    p_build.set_defaults(func=_cmd_build)

    p_show = sub.add_parser("show", help="Print the latest consensus per section.")
    p_show.add_argument("--song-id", type=str, default=None)
    p_show.add_argument("--section-id", type=str, default=None)
    p_show.add_argument("--confidence-min", type=float, default=None,
                        help="Filter out consensus with confidence < this.")
    p_show.set_defaults(func=_cmd_show)

    p_insp = sub.add_parser("inspect", help="Dump vote tally for one section.")
    p_insp.add_argument("--song-id", required=True)
    p_insp.add_argument("--section-id", required=True)
    p_insp.set_defaults(func=_cmd_inspect)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
