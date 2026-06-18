"""``python -m bench.failures`` — Phase 4 CLI.

Subcommands:

    report       Walk the evidence store, run ``mine_failures``,
                 print a per-section disagreement list and aggregate
                 counts by ``failure_type``.

    summary      Same as report, just the aggregate (no per-row
                 dump) so it's pipeable for dashboards.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from ..evidence.store import EvidenceStore
from .miner import FailureMiningConfig, mine_failures


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------


def _row_to_jsonable(row) -> dict:
    return {
        "song_id": row.song_id,
        "section_id": row.section_id,
        "failure_type": row.failure_type,
        "jam_value": _to_jsonable(row.jam_value),
        "consensus_value": _to_jsonable(row.consensus_value),
        "consensus_confidence": row.consensus_confidence,
        "reason": row.reason,
    }


def _to_jsonable(v):
    if isinstance(v, tuple):
        return list(v)
    return v


def _cmd_report(args: argparse.Namespace) -> int:
    store = EvidenceStore(root=args.store_root)
    config = FailureMiningConfig(
        min_consensus_confidence=args.min_consensus_confidence,
    )
    rows = mine_failures(store, config=config)
    rows.sort(key=lambda r: (r.failure_type, r.song_id, r.section_id))

    if args.json:
        payload = {
            "n_failures": len(rows),
            "by_failure_type": dict(Counter(r.failure_type for r in rows)),
            "rows": [_row_to_jsonable(r) for r in rows],
        }
        print(json.dumps(payload, indent=2, sort_keys=False))
        return 0

    print(f"Evidence store: {store.root}")
    print(f"  failures:           {len(rows)}")
    if not rows:
        print("  (no engine-vs-consensus disagreements)")
        return 0
    counts = Counter(r.failure_type for r in rows)
    print("  by failure_type:")
    for ftype, n in sorted(counts.items()):
        print(f"    {ftype:30s} {n}")
    print()
    print("Per-section failures:")
    for r in rows:
        print(f"  [{r.failure_type}] {r.song_id}:{r.section_id} "
              f"conf={r.consensus_confidence:.2f}")
        print(f"    {r.reason}")
    return 0


# ---------------------------------------------------------------------------
# summary
# ---------------------------------------------------------------------------


def _cmd_summary(args: argparse.Namespace) -> int:
    store = EvidenceStore(root=args.store_root)
    config = FailureMiningConfig(
        min_consensus_confidence=args.min_consensus_confidence,
    )
    rows = mine_failures(store, config=config)
    counts = Counter(r.failure_type for r in rows)
    summary = {
        "n_failures": len(rows),
        "by_failure_type": dict(counts),
        "n_unique_songs_failing": len({r.song_id for r in rows}),
        "n_unique_sections_failing": len(
            {(r.song_id, r.section_id) for r in rows}
        ),
    }
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=False))
    else:
        print(f"failures:                  {summary['n_failures']}")
        print(f"unique songs failing:      {summary['n_unique_songs_failing']}")
        print(f"unique sections failing:   {summary['n_unique_sections_failing']}")
        print("by failure_type:")
        for ftype, n in sorted(counts.items()):
            print(f"  {ftype:30s} {n}")
    return 0


# ---------------------------------------------------------------------------
# argparse wiring
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m bench.failures",
        description="JAM Learning System — Automatic Failure Mining (Phase 4).",
    )
    parser.add_argument(
        "--store-root", type=Path, default=None,
        help="Override the default evidence store root.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_report = sub.add_parser("report", help="Per-section failure dump + summary.")
    p_report.add_argument("--min-consensus-confidence", type=float, default=0.8,
                          help="Skip sections whose consensus < this (default 0.8).")
    p_report.add_argument("--json", action="store_true",
                          help="Emit JSON (rows + counts).")
    p_report.set_defaults(func=_cmd_report)

    p_sum = sub.add_parser("summary", help="Aggregate failure counts only.")
    p_sum.add_argument("--min-consensus-confidence", type=float, default=0.8)
    p_sum.add_argument("--json", action="store_true")
    p_sum.set_defaults(func=_cmd_summary)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
