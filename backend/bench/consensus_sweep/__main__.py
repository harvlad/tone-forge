"""``python -m bench.consensus_sweep`` — Phase 6 CLI.

Subcommands:

    score    Walk the evidence store, compute consensus-corpus
             agreement metrics, write a ``ConsensusCorpusScore``
             to JSON.

    compare  Compare two score files (candidate, baseline) and
             emit an acceptance verdict. Exit code 0 if accepted,
             1 if rejected.

    show     Pretty-print one score file (counts + per-field
             agreement). Useful for quick visibility without
             diff-ing.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..evidence.store import EvidenceStore
from .gate import (
    ConsensusAcceptanceConfig,
    evaluate_consensus_acceptance,
)
from .scorer import (
    ConsensusScoreConfig,
    dump_consensus_score,
    load_consensus_score,
    score_consensus_corpus,
)


# ---------------------------------------------------------------------------
# score
# ---------------------------------------------------------------------------


def _cmd_score(args: argparse.Namespace) -> int:
    store = EvidenceStore(root=args.store_root)
    config = ConsensusScoreConfig(
        min_confidence=args.min_confidence,
        require_jam_output=args.require_jam_output,
        song_id=args.song_id,
    )
    score = score_consensus_corpus(store, config=config)
    if args.output is not None:
        dump_consensus_score(score, args.output)
        sys.stderr.write(f"wrote score -> {args.output}\n")
    if args.json or args.output is None:
        # When no --output is given, default to JSON stdout so the
        # score can be piped into compare without an intermediate file.
        from .scorer import _score_to_jsonable
        print(json.dumps(_score_to_jsonable(score), indent=2, sort_keys=False))
    else:
        _print_score_summary(score)
    return 0


def _print_score_summary(score) -> None:
    print(f"  entries:                   {score.n_entries}")
    print(f"  entries w/ jam_output:     {score.n_entries_with_jam}")
    print(f"  guidance evaluated:        {score.n_guidance_evaluated}")
    print(f"  chord_seq evaluated:       {score.n_chord_sequence_evaluated}")
    print(f"  guidance_mode_match_rate:  {score.guidance_mode_match_rate:.4f}")
    print(f"  chord_sequence_match_rate: {score.chord_sequence_match_rate:.4f}")
    print(f"  chord_seq mean jaccard:    {score.chord_sequence_mean_jaccard:.4f}")
    print(f"  combined_match_rate:       {score.combined_match_rate:.4f}")
    print(f"  score wall seconds:        {score.score_wall_seconds:.3f}")


# ---------------------------------------------------------------------------
# compare
# ---------------------------------------------------------------------------


def _cmd_compare(args: argparse.Namespace) -> int:
    candidate = load_consensus_score(args.candidate)
    baseline = load_consensus_score(args.baseline)
    rules = ConsensusAcceptanceConfig(
        corpus_must_strictly_improve=not args.allow_neutral,
        max_combined_regression_pp=args.max_combined_regression_pp,
        max_section_regressions=args.max_section_regressions,
        max_runtime_factor=args.max_runtime_factor,
    )
    verdict = evaluate_consensus_acceptance(candidate, baseline, rules)

    if args.json:
        payload = {
            "accepted": verdict.accepted,
            "combined_delta": verdict.combined_delta,
            "rejection_reason": verdict.rejection_reason,
            "per_field_deltas": [
                {"field": f, "delta": d} for f, d in verdict.per_field_deltas
            ],
            "regressing_sections": list(verdict.regressing_sections),
            "candidate": {
                "combined_match_rate": candidate.combined_match_rate,
                "score_wall_seconds": candidate.score_wall_seconds,
            },
            "baseline": {
                "combined_match_rate": baseline.combined_match_rate,
                "score_wall_seconds": baseline.score_wall_seconds,
            },
        }
        print(json.dumps(payload, indent=2, sort_keys=False))
    else:
        status = "ACCEPT" if verdict.accepted else "REJECT"
        print(f"[{status}] combined_match_rate delta = {verdict.combined_delta:+.4f}")
        if verdict.rejection_reason:
            print(f"  reason: {verdict.rejection_reason}")
        print("  per-field deltas:")
        for f, d in verdict.per_field_deltas:
            print(f"    {f:30s} {d:+.4f}")
        if verdict.regressing_sections:
            print(f"  regressing sections ({len(verdict.regressing_sections)}):")
            for s in verdict.regressing_sections:
                print(f"    {s}")
    return 0 if verdict.accepted else 1


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


def _cmd_show(args: argparse.Namespace) -> int:
    score = load_consensus_score(args.path)
    if args.json:
        from .scorer import _score_to_jsonable
        print(json.dumps(_score_to_jsonable(score), indent=2, sort_keys=False))
    else:
        print(f"Score file: {args.path}")
        _print_score_summary(score)
    return 0


# ---------------------------------------------------------------------------
# argparse wiring
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m bench.consensus_sweep",
        description="JAM Learning System — Consensus-corpus regression gate (Phase 6).",
    )
    parser.add_argument(
        "--store-root", type=Path, default=None,
        help="Override the default evidence store root.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_score = sub.add_parser("score", help="Compute corpus score.")
    p_score.add_argument("--min-confidence", type=float, default=0.8)
    p_score.add_argument("--require-jam-output", action="store_true")
    p_score.add_argument("--song-id", default=None)
    p_score.add_argument("--output", type=Path, default=None,
                         help="Write the score to this JSON file.")
    p_score.add_argument("--json", action="store_true",
                         help="Also emit JSON to stdout (default when no --output).")
    p_score.set_defaults(func=_cmd_score)

    p_cmp = sub.add_parser("compare", help="Run the acceptance gate.")
    p_cmp.add_argument("--candidate", type=Path, required=True,
                       help="Candidate score JSON.")
    p_cmp.add_argument("--baseline", type=Path, required=True,
                       help="Baseline score JSON.")
    p_cmp.add_argument("--allow-neutral", action="store_true",
                       help="Allow no-op (delta == 0) changes through.")
    p_cmp.add_argument("--max-combined-regression-pp", type=float, default=1.0,
                       help="Reject if combined_match_rate drops by more than this (pp).")
    p_cmp.add_argument("--max-section-regressions", type=int, default=0,
                       help="Reject if more than N sections regress from 1.0 to 0.0.")
    p_cmp.add_argument("--max-runtime-factor", type=float, default=2.0,
                       help="Reject if candidate scoring runtime > N x baseline.")
    p_cmp.add_argument("--json", action="store_true")
    p_cmp.set_defaults(func=_cmd_compare)

    p_show = sub.add_parser("show", help="Pretty-print one score file.")
    p_show.add_argument("path", type=Path)
    p_show.add_argument("--json", action="store_true")
    p_show.set_defaults(func=_cmd_show)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
