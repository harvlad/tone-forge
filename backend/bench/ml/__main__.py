"""``python -m bench.ml`` — Phase 9 CLI.

Subcommands:

    validate    Walk the evidence store and confirm every record
                parses cleanly at the current ``SCHEMA_VERSION``.
                Exits non-zero on the first mismatch so CI can
                catch a writer/reader skew before training jobs.

    stats       Print the ML view (counts, label distribution,
                mean confidence) so an operator can answer "how
                much labelled data do we have?".

    dump        Stream the ML examples as JSONL on stdout — the
                contract a future ML pipeline would consume. No
                training, no normalisation, just the dataset.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..evidence.store import EvidenceStore
from .dataset import (
    MLDatasetConfig,
    SchemaValidationError,
    compute_dataset_stats,
    iter_ml_examples,
    validate_store_schema,
)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def _cmd_validate(args: argparse.Namespace) -> int:
    store = EvidenceStore(root=args.store_root)
    try:
        validate_store_schema(store)
    except SchemaValidationError as e:
        sys.stderr.write(f"schema validation failed: {e}\n")
        return 1
    n = store.count()
    if args.json:
        print(json.dumps({"ok": True, "n_records": n}, indent=2))
    else:
        print(f"OK ({n} records, schema_version stable)")
    return 0


def _cmd_stats(args: argparse.Namespace) -> int:
    store = EvidenceStore(root=args.store_root)
    config = MLDatasetConfig(
        min_label_confidence=args.min_label_confidence,
        include_semisupervised=args.include_semisupervised,
        song_id=args.song_id,
        date_prefix=args.date_prefix,
    )
    stats = compute_dataset_stats(store, config=config)
    if args.json:
        print(json.dumps(stats.to_dict(), indent=2, sort_keys=False))
        return 0
    print("ML dataset stats")
    print("=" * 50)
    print(f"records (total):           {stats.n_records_total}")
    print(f"supervised examples:       {stats.n_supervised_examples}")
    print(f"semisupervised examples:   {stats.n_semisupervised_examples}")
    print(f"unique songs:              {stats.n_unique_songs}")
    print(f"unique sections:           {stats.n_unique_sections}")
    print(f"mean label confidence:     {stats.mean_label_confidence:.3f}")
    if stats.guidance_mode_label_counts:
        print("guidance_mode label distribution:")
        for label, n in sorted(stats.guidance_mode_label_counts.items()):
            print(f"  {label:20s} {n}")
    if stats.chord_sequence_length_histogram:
        print("chord_sequence length histogram:")
        for length, n in sorted(stats.chord_sequence_length_histogram.items()):
            print(f"  len={length:<4d} {n}")
    return 0


def _cmd_dump(args: argparse.Namespace) -> int:
    store = EvidenceStore(root=args.store_root)
    config = MLDatasetConfig(
        min_label_confidence=args.min_label_confidence,
        include_semisupervised=args.include_semisupervised,
        song_id=args.song_id,
        date_prefix=args.date_prefix,
    )
    out = sys.stdout if args.output is None else args.output.open("w", encoding="utf-8")
    n = 0
    try:
        for ex in iter_ml_examples(store, config=config):
            line = json.dumps(ex.to_dict(), sort_keys=False, separators=(",", ":"))
            out.write(line)
            out.write("\n")
            n += 1
    finally:
        if args.output is not None:
            out.close()
    if args.output is not None:
        sys.stderr.write(f"wrote {n} examples to {args.output}\n")
    return 0


# ---------------------------------------------------------------------------
# argparse wiring
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m bench.ml",
        description=(
            "JAM Learning System — future-ML compatibility surface (Phase 9). "
            "Read-only view over the evidence store; never writes."
        ),
    )
    parser.add_argument(
        "--store-root", type=Path, default=None,
        help="Override the default evidence store root.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_val = sub.add_parser("validate", help="Schema sanity check.")
    p_val.add_argument("--json", action="store_true")
    p_val.set_defaults(func=_cmd_validate)

    p_stats = sub.add_parser("stats", help="ML-view dataset stats.")
    p_stats.add_argument("--min-label-confidence", type=float, default=0.8)
    p_stats.add_argument("--include-semisupervised", action="store_true")
    p_stats.add_argument("--song-id", default=None)
    p_stats.add_argument("--date-prefix", default=None)
    p_stats.add_argument("--json", action="store_true")
    p_stats.set_defaults(func=_cmd_stats)

    p_dump = sub.add_parser("dump", help="Stream ML examples as JSONL.")
    p_dump.add_argument("--min-label-confidence", type=float, default=0.8)
    p_dump.add_argument("--include-semisupervised", action="store_true")
    p_dump.add_argument("--song-id", default=None)
    p_dump.add_argument("--date-prefix", default=None)
    p_dump.add_argument("--output", type=Path, default=None,
                        help="Write JSONL to this path; default stdout.")
    p_dump.set_defaults(func=_cmd_dump)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
