"""``python -m bench.roadmap`` — Phase 8 CLI.

Subcommands:

    build        Build a roadmap from the evidence store and emit
                 either a human-readable report or JSON. Pass
                 ``--output PATH`` to write JSON to disk.

    show         Pretty-print a previously-saved roadmap JSON
                 file. Useful in CI when an earlier job dumped
                 the roadmap and a later one wants to render it.

The CLI is intentionally thin — it parses argparse, calls
``build_roadmap``, and either renders or persists. All the
real logic lives in ``ranker.py`` so the same code path drives
tests and the CLI.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..evidence.store import EvidenceStore
from .ranker import (
    RoadmapConfig,
    RoadmapReport,
    build_roadmap,
    dump_roadmap,
    load_roadmap,
)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _render_text(report: RoadmapReport) -> str:
    lines: list[str] = []
    lines.append("Disagreement-Driven Roadmap")
    lines.append("=" * 50)
    lines.append(f"areas with evidence:   {report.n_areas_total}")
    lines.append(
        f"consensus failures:    {report.n_consensus_failures_total} "
        f"(min_conf={report.config.get('min_consensus_confidence')})"
    )
    lines.append(f"user corrections:      {report.n_user_corrections_total}")
    lines.append(f"top items shown:       {len(report.items)}")
    song_filter = report.config.get("song_id")
    if song_filter is not None:
        lines.append(f"song filter:           {song_filter}")
    lines.append("")
    if not report.items:
        lines.append("(no engine areas with evidence yet)")
        return "\n".join(lines)
    for i, it in enumerate(report.items, start=1):
        lines.append(f"{i}. {it.area}   score={it.score:.3f}")
        lines.append(
            f"   consensus_failures={it.n_consensus_failures} "
            f"(mean_conf={it.mean_consensus_confidence:.2f})  "
            f"user_corrections={it.n_user_corrections}"
        )
        if it.failure_types:
            lines.append(f"   failure_types: {', '.join(it.failure_types)}")
        if it.correction_types:
            lines.append(f"   correction_types: {', '.join(it.correction_types)}")
        if it.example_sections:
            lines.append("   examples:")
            for song_id, section_id, kind in it.example_sections:
                lines.append(f"     [{kind}] {section_id}")
        if it.representative_diffs:
            lines.append("   diff samples:")
            for d in it.representative_diffs:
                lines.append(
                    f"     [{d.get('failure_type')}] "
                    f"jam={d.get('jam_value')!r} "
                    f"cons={d.get('consensus_value')!r}"
                )
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def _cmd_build(args: argparse.Namespace) -> int:
    store = EvidenceStore(root=args.store_root)
    config = RoadmapConfig(
        min_consensus_confidence=args.min_consensus_confidence,
        top_n=args.top_n,
        examples_per_item=args.examples_per_item,
        consensus_weight=args.consensus_weight,
        correction_weight=args.correction_weight,
        song_id=args.song_id,
    )
    report = build_roadmap(store, config=config)

    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(dump_roadmap(report), indent=2, sort_keys=False),
            encoding="utf-8",
        )
        sys.stderr.write(f"wrote {args.output}\n")
        if args.json:
            sys.stdout.write(
                json.dumps(dump_roadmap(report), indent=2, sort_keys=False)
            )
            sys.stdout.write("\n")
        return 0

    if args.json:
        print(json.dumps(dump_roadmap(report), indent=2, sort_keys=False))
        return 0

    print(_render_text(report))
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    data = json.loads(args.path.read_text(encoding="utf-8"))
    report = load_roadmap(data)
    if args.json:
        print(json.dumps(dump_roadmap(report), indent=2, sort_keys=False))
    else:
        print(_render_text(report))
    return 0


# ---------------------------------------------------------------------------
# argparse wiring
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m bench.roadmap",
        description=(
            "JAM Learning System — Disagreement-Driven Roadmap (Phase 8). "
            "Aggregates Phase 4 failures + Phase 7 corrections into a "
            "ranked list of engine areas to fix."
        ),
    )
    parser.add_argument(
        "--store-root", type=Path, default=None,
        help="Override the default evidence store root.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_build = sub.add_parser(
        "build",
        help="Build a roadmap from the evidence store.",
    )
    p_build.add_argument("--min-consensus-confidence", type=float, default=0.8)
    p_build.add_argument("--top-n", type=int, default=10)
    p_build.add_argument("--examples-per-item", type=int, default=5)
    p_build.add_argument("--consensus-weight", type=float, default=1.0)
    p_build.add_argument("--correction-weight", type=float, default=1.0)
    p_build.add_argument("--song-id", default=None,
                         help="Limit roadmap to one song_id.")
    p_build.add_argument("--output", type=Path, default=None,
                         help="Write the report JSON to this path.")
    p_build.add_argument("--json", action="store_true",
                         help="Emit JSON to stdout (also works alongside --output).")
    p_build.set_defaults(func=_cmd_build)

    p_show = sub.add_parser(
        "show",
        help="Pretty-print a previously-saved roadmap JSON.",
    )
    p_show.add_argument("path", type=Path)
    p_show.add_argument("--json", action="store_true",
                        help="Re-emit JSON instead of pretty text.")
    p_show.set_defaults(func=_cmd_show)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
