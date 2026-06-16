"""Operator CLI for the song-validation subsystem.

This module exposes the public Python surface (ingestion, pipeline,
reports, corpus, queue) as argparse subcommands so operators can run
the subsystem from a shell without writing Python glue. The CLI is
strictly a thin presentation layer; all real work happens in the
underlying modules.

Invocation::

    python -m song_validation <subcommand> [options]

Subcommands:

- ``drain <queue_dir>``                  drain the file-queue inbox.
- ``enqueue-bundle <queue_dir> <file>``  enqueue an analysis bundle.
- ``enqueue-tab <queue_dir> <file>``     enqueue a tab source.
- ``validate <song_id> [<song_id> ...]`` run the validation pipeline.
- ``report jam-wrong``                   "Where is JAM wrong?" ranking.
- ``report tabs-wrong``                  rows flagged LIKELY_TAB_ERROR.
- ``report dominant``                    dominant failure class.
- ``report engine-diff <v1> <v2>``       score-card delta.
- ``corpus stats``                       sizing of the training corpus.

Global option ``--db PATH`` overrides the default ~/.toneforge DB
location. Output is JSON on stdout by default; ``--pretty`` switches
to indent=2.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from .disagreement import (
    LIKELY_TAB_ERROR_CONF_THRESHOLD,
    confidence_calibration_report,
    reclassify_all_alignments,
    reclassify_song,
)
from .ingestion import ingest_analysis_bundle, ingest_tab_source
from .maintenance import list_songs, purge_song, vacuum_store
from .pipeline import PipelineError, validate_song, validate_songs
from .queue import drain_queue, enqueue_bundle, enqueue_tab
from .reports import (
    aligner_diff_report,
    disagreement_trends_over_time,
    dominant_failure_class,
    engine_version_diff,
    engine_version_song_diff,
    ingestion_trends_over_time,
    inspect_song,
    where_are_tabs_wrong,
    where_is_jam_wrong,
)
from .store import Store
from .training import corpus_stats, export_corpus


def _emit(payload: Any, *, pretty: bool, out=None) -> None:
    """Write ``payload`` as JSON to ``out`` (default ``sys.stdout``)."""
    stream = out if out is not None else sys.stdout
    if pretty:
        text = json.dumps(payload, indent=2, sort_keys=True, default=str)
    else:
        text = json.dumps(payload, sort_keys=True, default=str)
    stream.write(text + "\n")


def _load_json_file(path: Path) -> Mapping[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, Mapping):
        raise SystemExit(f"file {path} is not a JSON object")
    return data


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="song_validation",
        description=(
            "Operator CLI for the offline song-validation subsystem."
        ),
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help=(
            "Override the sqlite DB path (defaults to "
            "~/.toneforge/song_validation.db)."
        ),
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output (indent=2).",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # drain
    p_drain = sub.add_parser(
        "drain",
        help="Drain pending envelopes from the queue inbox.",
    )
    p_drain.add_argument("queue_dir", type=Path)
    p_drain.add_argument(
        "--auto-validate",
        action="store_true",
        help=(
            "After ingestion, run validate_song for songs that now "
            "have both an analysis and a tab row."
        ),
    )
    p_drain.add_argument(
        "--max-items",
        type=int,
        default=None,
        help="Cap on envelopes processed this pass.",
    )

    # enqueue-bundle
    p_eb = sub.add_parser(
        "enqueue-bundle",
        help="Read an analysis-bundle JSON file and enqueue it.",
    )
    p_eb.add_argument("queue_dir", type=Path)
    p_eb.add_argument("payload_file", type=Path)

    # enqueue-tab
    p_et = sub.add_parser(
        "enqueue-tab",
        help="Read a tab-source JSON file and enqueue it.",
    )
    p_et.add_argument("queue_dir", type=Path)
    p_et.add_argument("payload_file", type=Path)

    # validate
    p_val = sub.add_parser(
        "validate",
        help="Run validate_song on one or more song ids.",
    )
    p_val.add_argument("song_ids", nargs="+")

    # report
    p_rep = sub.add_parser("report", help="Pre-baked report queries.")
    rep_sub = p_rep.add_subparsers(dest="report_command", required=True)

    rep_jam = rep_sub.add_parser(
        "jam-wrong",
        help="Top failure classes (Where is JAM wrong?).",
    )
    rep_jam.add_argument("--top-n", type=int, default=6)

    rep_tab = rep_sub.add_parser(
        "tabs-wrong",
        help="Rows flagged LIKELY_TAB_ERROR.",
    )
    rep_tab.add_argument("--limit", type=int, default=500)

    rep_sub.add_parser(
        "dominant",
        help="Dominant failure class across all disagreements.",
    )

    rep_diff = rep_sub.add_parser(
        "engine-diff",
        help="Per-metric delta between two engine versions.",
    )
    rep_diff.add_argument("version_a")
    rep_diff.add_argument("version_b")

    rep_song_diff = rep_sub.add_parser(
        "engine-song-diff",
        help=(
            "Per-song delta between two engine versions: which "
            "individual songs improved or regressed."
        ),
    )
    rep_song_diff.add_argument("version_a")
    rep_song_diff.add_argument("version_b")
    rep_song_diff.add_argument(
        "--limit",
        type=int,
        default=None,
        help=(
            "Cap on the detail rows returned (improvements/"
            "regressions/unchanged lists are always complete)."
        ),
    )

    rep_aligner_diff = rep_sub.add_parser(
        "aligner-diff",
        help=(
            "Per-song delta between two aligner kinds (e.g. grid "
            "vs dtw): which individual songs improved or regressed."
        ),
    )
    rep_aligner_diff.add_argument("aligner_a")
    rep_aligner_diff.add_argument("aligner_b")
    rep_aligner_diff.add_argument(
        "--limit",
        type=int,
        default=None,
        help=(
            "Cap on the detail rows returned (improvements/"
            "regressions/unchanged lists are always complete)."
        ),
    )

    rep_inspect = rep_sub.add_parser(
        "inspect",
        help="Per-song drilldown: every artifact for one song.",
    )
    rep_inspect.add_argument("song_id")

    rep_trends_dis = rep_sub.add_parser(
        "trends-disagreements",
        help=(
            "Time-bucketed disagreement counts by classification, "
            "keyed off analysis ingestion time."
        ),
    )
    rep_trends_dis.add_argument(
        "--bucket",
        choices=["hour", "day", "week", "month"],
        default="day",
    )
    rep_trends_dis.add_argument(
        "--since",
        default=None,
        help="Inclusive ISO-8601 lower bound (e.g. 2025-01-01).",
    )
    rep_trends_dis.add_argument(
        "--until",
        default=None,
        help="Exclusive ISO-8601 upper bound.",
    )

    rep_trends_ing = rep_sub.add_parser(
        "trends-ingestion",
        help=(
            "Time-bucketed analysis ingestion volume + per-engine-"
            "version split."
        ),
    )
    rep_trends_ing.add_argument(
        "--bucket",
        choices=["hour", "day", "week", "month"],
        default="day",
    )
    rep_trends_ing.add_argument(
        "--since",
        default=None,
        help="Inclusive ISO-8601 lower bound.",
    )
    rep_trends_ing.add_argument(
        "--until",
        default=None,
        help="Exclusive ISO-8601 upper bound.",
    )

    rep_cal = rep_sub.add_parser(
        "calibrate",
        help=(
            "Profile the LIKELY_TAB_ERROR threshold against the "
            "UNKNOWN+LIKELY_TAB_ERROR slice (pure diagnostic)."
        ),
    )
    rep_cal.add_argument(
        "--candidate-thresholds",
        type=str,
        default=None,
        help=(
            "Comma-separated list of thresholds to project, e.g. "
            "'0.3,0.4,0.5'. Default: 0.0..1.0 step 0.1."
        ),
    )

    # reclassify
    p_rc = sub.add_parser(
        "reclassify",
        help=(
            "Re-run the classifier over existing alignments without "
            "re-aligning. Use after rule/threshold changes."
        ),
    )
    p_rc.add_argument(
        "--song-id",
        default=None,
        help="Limit reclassification to one song (default: whole store).",
    )
    p_rc.add_argument(
        "--likely-tab-error-threshold",
        type=float,
        default=LIKELY_TAB_ERROR_CONF_THRESHOLD,
        help=(
            "Override the LIKELY_TAB_ERROR confidence cutoff for this "
            f"pass (default: {LIKELY_TAB_ERROR_CONF_THRESHOLD})."
        ),
    )
    p_rc.add_argument(
        "--no-reaggregate",
        action="store_true",
        help=(
            "Skip the engine_metrics re-aggregation step "
            "(faster preview, but score cards stay stale)."
        ),
    )

    # corpus
    p_corp = sub.add_parser("corpus", help="Training corpus queries.")
    corp_sub = p_corp.add_subparsers(dest="corpus_command", required=True)
    corp_stats = corp_sub.add_parser(
        "stats", help="Aggregate sizing for the high-confidence subset."
    )
    corp_stats.add_argument("--min-alignment-score", type=float, default=0.8)
    corp_stats.add_argument("--min-tab-confidence", type=float, default=0.7)
    corp_stats.add_argument(
        "--min-chord-confidence", type=float, default=None
    )

    corp_export = corp_sub.add_parser(
        "export",
        help=(
            "Snapshot the high-confidence corpus to a JSONL file "
            "for offline ML training."
        ),
    )
    corp_export.add_argument("output_path", type=Path)
    corp_export.add_argument(
        "--format", choices=["jsonl"], default="jsonl"
    )
    corp_export.add_argument(
        "--min-alignment-score", type=float, default=0.8
    )
    corp_export.add_argument(
        "--min-tab-confidence", type=float, default=0.7
    )
    corp_export.add_argument(
        "--min-chord-confidence", type=float, default=None
    )

    # store maintenance
    p_store = sub.add_parser(
        "store",
        help="Operator housekeeping on the validation DB.",
    )
    store_sub = p_store.add_subparsers(
        dest="store_command", required=True
    )

    store_list = store_sub.add_parser(
        "list-songs", help="Enumerate songs with per-song child counts."
    )
    store_list.add_argument("--limit", type=int, default=None)

    store_purge = store_sub.add_parser(
        "purge-song",
        help=(
            "Delete one song and every row that references it. "
            "Does NOT re-aggregate metrics; run the reclassify "
            "subcommand if score cards must stay live."
        ),
    )
    store_purge.add_argument("song_id")

    store_sub.add_parser(
        "vacuum",
        help=(
            "Run sqlite VACUUM and report byte-size delta. "
            "Useful after a big purge."
        ),
    )

    return parser


def _store_for_args(args: argparse.Namespace) -> Store:
    return Store(db_path=args.db) if args.db is not None else Store()


def _run_drain(args: argparse.Namespace, store: Store) -> Mapping[str, Any]:
    return drain_queue(
        args.queue_dir,
        store,
        auto_validate=args.auto_validate,
        max_items=args.max_items,
    )


def _run_enqueue_bundle(
    args: argparse.Namespace, store: Store
) -> Mapping[str, Any]:
    payload = _load_json_file(args.payload_file)
    path = enqueue_bundle(payload, args.queue_dir)
    return {"enqueued": str(path)}


def _run_enqueue_tab(
    args: argparse.Namespace, store: Store
) -> Mapping[str, Any]:
    payload = _load_json_file(args.payload_file)
    path = enqueue_tab(payload, args.queue_dir)
    return {"enqueued": str(path)}


def _run_validate(
    args: argparse.Namespace, store: Store
) -> Mapping[str, Any]:
    # Batch wrapper collects per-song errors instead of halting on one.
    return {"results": validate_songs(args.song_ids, store)}


def _run_report(
    args: argparse.Namespace, store: Store
) -> Mapping[str, Any]:
    cmd = args.report_command
    if cmd == "jam-wrong":
        return where_is_jam_wrong(store, top_n=args.top_n)
    if cmd == "tabs-wrong":
        return where_are_tabs_wrong(store, limit=args.limit)
    if cmd == "dominant":
        return {"dominant_failure_class": dominant_failure_class(store)}
    if cmd == "engine-diff":
        return engine_version_diff(args.version_a, args.version_b, store)
    if cmd == "engine-song-diff":
        return engine_version_song_diff(
            args.version_a, args.version_b, store, limit=args.limit
        )
    if cmd == "aligner-diff":
        return aligner_diff_report(
            args.aligner_a, args.aligner_b, store, limit=args.limit
        )
    if cmd == "inspect":
        return inspect_song(args.song_id, store)
    if cmd == "trends-disagreements":
        return disagreement_trends_over_time(
            store,
            bucket=args.bucket,
            since=args.since,
            until=args.until,
        )
    if cmd == "trends-ingestion":
        return ingestion_trends_over_time(
            store,
            bucket=args.bucket,
            since=args.since,
            until=args.until,
        )
    if cmd == "calibrate":
        cts = None
        if args.candidate_thresholds is not None:
            cts = [
                float(t.strip())
                for t in args.candidate_thresholds.split(",")
                if t.strip()
            ]
        return confidence_calibration_report(
            store, candidate_thresholds=cts
        )
    raise SystemExit(f"unknown report command: {cmd!r}")


def _run_reclassify(
    args: argparse.Namespace, store: Store
) -> Mapping[str, Any]:
    kwargs = {
        "likely_tab_error_threshold": args.likely_tab_error_threshold,
        "reaggregate_metrics": not args.no_reaggregate,
    }
    if args.song_id is not None:
        return reclassify_song(args.song_id, store, **kwargs)
    return reclassify_all_alignments(store, **kwargs)


def _run_corpus(
    args: argparse.Namespace, store: Store
) -> Mapping[str, Any]:
    if args.corpus_command == "stats":
        return corpus_stats(
            store,
            min_alignment_score=args.min_alignment_score,
            min_tab_confidence=args.min_tab_confidence,
            min_chord_confidence=args.min_chord_confidence,
        )
    if args.corpus_command == "export":
        return export_corpus(
            store,
            args.output_path,
            format=args.format,
            min_alignment_score=args.min_alignment_score,
            min_tab_confidence=args.min_tab_confidence,
            min_chord_confidence=args.min_chord_confidence,
        )
    raise SystemExit(f"unknown corpus command: {args.corpus_command!r}")


def _run_store(
    args: argparse.Namespace, store: Store
) -> Mapping[str, Any]:
    cmd = args.store_command
    if cmd == "list-songs":
        return {"songs": list_songs(store, limit=args.limit)}
    if cmd == "purge-song":
        return purge_song(args.song_id, store)
    if cmd == "vacuum":
        return vacuum_store(store)
    raise SystemExit(f"unknown store command: {cmd!r}")


_DISPATCH = {
    "drain": _run_drain,
    "enqueue-bundle": _run_enqueue_bundle,
    "enqueue-tab": _run_enqueue_tab,
    "validate": _run_validate,
    "report": _run_report,
    "reclassify": _run_reclassify,
    "corpus": _run_corpus,
    "store": _run_store,
}


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    out=None,
) -> int:
    """CLI entry point.

    Returns a POSIX-style exit code so callers (and ``__main__.py``)
    can propagate it. Pipeline errors are caught and surfaced as
    exit code 2 with the message on stderr — this keeps shell scripts
    able to branch on failure without parsing JSON.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)
    handler = _DISPATCH.get(args.command)
    if handler is None:
        parser.error(f"unknown command: {args.command!r}")
    store = _store_for_args(args)
    try:
        result = handler(args, store)
    except PipelineError as exc:
        print(f"pipeline error: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"file not found: {exc}", file=sys.stderr)
        return 2
    _emit(result, pretty=args.pretty, out=out)
    return 0
