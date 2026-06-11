#!/usr/bin/env python3
"""Founder Validation Corpus harness — runs the pipeline on every entry and
diffs against the founder-validated expected outputs.

Usage:
  python backend/scripts/run_founder_validation.py
  python backend/scripts/run_founder_validation.py --tier smoke
  python backend/scripts/run_founder_validation.py --tier full
  python backend/scripts/run_founder_validation.py --report-path /tmp/run.md
  python backend/scripts/run_founder_validation.py --manifest /path/to/manifest.yaml

Exit codes:
  0  — all hard-gated fields passed (soft warns are OK)
  1  — at least one hard-gated field failed
  2  — harness error (manifest unparseable, no audio for an entry, etc.)

By default writes the Markdown report to
  backend/founder_corpus/reports/latest.md
and also to a timestamped sibling
  backend/founder_corpus/reports/YYYY-MM-DDTHH-MM-SSZ.md

The timestamped file is gitignored; `latest.md` is the only tracked
report and stays current with the most recent run.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import List, Optional, Tuple

# Make 'backend' importable when run as a plain script (not via -m).
HERE = Path(__file__).resolve()
BACKEND_ROOT = HERE.parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from tone_forge.evaluation.founder_corpus import (  # noqa: E402
    CorpusEntry,
    EXIT_HARD_FAIL,
    EXIT_HARNESS_ERROR,
    EXIT_OK,
    FieldResult,
    compare,
    compute_exit_code,
    format_markdown_report,
    load_expected,
    load_manifest,
)


DEFAULT_MANIFEST = BACKEND_ROOT / "founder_corpus" / "manifest.yaml"
DEFAULT_REPORTS_DIR = BACKEND_ROOT / "founder_corpus" / "reports"


def _git_short_sha() -> str:
    """Return the current git short SHA, or '(no-git)' on failure."""
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(BACKEND_ROOT),
            stderr=subprocess.DEVNULL,
            timeout=2.0,
        ).decode().strip()
        return out or "(no-git)"
    except Exception:
        return "(no-git)"


async def _analyze_one(entry: CorpusEntry) -> dict:
    """Run the full pipeline on one corpus entry and return AnalysisResult.to_dict()."""
    # Local import: the pipeline import is heavy. Keeping it inside the
    # function lets `--help` and schema-only operations stay fast.
    from tone_forge.unified_pipeline import PipelineConfig, UnifiedPipeline  # noqa: WPS433

    pipeline = UnifiedPipeline()
    config = PipelineConfig.standard()
    config.source_name = entry.id
    result = await pipeline.analyze(str(entry.audio_path), config)
    return result.to_dict()


def _run_entry(entry: CorpusEntry) -> Tuple[List[FieldResult], Optional[str]]:
    """Run + compare one entry. Returns (field_results, error_message_or_None)."""
    if not entry.audio_path.exists():
        return [], f"audio not found: {entry.audio_path}"
    if not entry.expected_path.exists():
        return [], f"expected not found: {entry.expected_path}"
    try:
        expected = load_expected(entry.expected_path)
    except Exception as exc:
        return [], f"failed to load expected: {exc}"
    try:
        actual = asyncio.run(_analyze_one(entry))
    except Exception as exc:  # noqa: BLE001
        tb = "".join(traceback.format_exception_only(type(exc), exc)).strip()
        return [], f"pipeline raised: {tb}"
    try:
        results = compare(expected, actual)
    except Exception as exc:  # noqa: BLE001
        return [], f"comparator raised: {exc}"
    return results, None


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST,
                        help=f"path to manifest.yaml (default: {DEFAULT_MANIFEST})")
    parser.add_argument("--tier", choices=["smoke", "full", "all"], default="all",
                        help="which entries to run (default: all)")
    parser.add_argument("--report-path", type=Path, default=None,
                        help="explicit report path; default writes both latest.md and a timestamped file")
    parser.add_argument("--quiet", action="store_true",
                        help="suppress per-entry progress to stderr")
    args = parser.parse_args(argv)

    try:
        manifest = load_manifest(args.manifest)
    except Exception as exc:  # noqa: BLE001
        print(f"[harness-error] failed to load manifest: {exc}", file=sys.stderr)
        return EXIT_HARNESS_ERROR

    entries = manifest.filter_by_tier(args.tier)
    if not entries:
        print(f"[harness-error] manifest has no entries matching tier={args.tier!r}", file=sys.stderr)
        return EXIT_HARNESS_ERROR

    if not args.quiet:
        print(f"[harness] running {len(entries)} entries (tier={args.tier})", file=sys.stderr)

    started = time.time()
    all_results: List[Tuple[CorpusEntry, List[FieldResult]]] = []
    errors: List[Tuple[str, str]] = []

    for entry in entries:
        entry_start = time.time()
        if not args.quiet:
            print(f"[harness]   {entry.id} ...", end="", file=sys.stderr, flush=True)
        results, error = _run_entry(entry)
        if error is not None:
            errors.append((entry.id, error))
            all_results.append((entry, []))
            if not args.quiet:
                print(f" ERROR ({error}) [{time.time() - entry_start:.1f}s]", file=sys.stderr)
            continue
        all_results.append((entry, results))
        if not args.quiet:
            n_fail = sum(1 for r in results if not r.passed and r.gate == "hard")
            n_warn = sum(1 for r in results if not r.passed and r.gate == "soft")
            status = "PASS" if n_fail == 0 and n_warn == 0 else ("FAIL" if n_fail else "WARN")
            print(f" {status} [{time.time() - entry_start:.1f}s]", file=sys.stderr)

    runtime = time.time() - started

    report = format_markdown_report(
        all_results,
        run_iso=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        tier_filter=args.tier,
        pipeline_version=_git_short_sha(),
        runtime_s=runtime,
        errors=errors,
    )

    if args.report_path is not None:
        args.report_path.parent.mkdir(parents=True, exist_ok=True)
        args.report_path.write_text(report, encoding="utf-8")
        if not args.quiet:
            print(f"[harness] report -> {args.report_path}", file=sys.stderr)
    else:
        DEFAULT_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        latest = DEFAULT_REPORTS_DIR / "latest.md"
        latest.write_text(report, encoding="utf-8")
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
        timestamped = DEFAULT_REPORTS_DIR / f"{stamp}.md"
        timestamped.write_text(report, encoding="utf-8")
        if not args.quiet:
            print(f"[harness] report -> {latest}", file=sys.stderr)
            print(f"[harness] report -> {timestamped}", file=sys.stderr)

    exit_code = compute_exit_code(all_results)
    if errors:
        # Harness-level errors are worse than a clean run but should not be
        # silently swallowed; they take precedence over EXIT_OK.
        if exit_code == EXIT_OK:
            exit_code = EXIT_HARNESS_ERROR

    if not args.quiet:
        n_fail = sum(1 for _, rs in all_results for r in rs if not r.passed and r.gate == "hard")
        n_warn = sum(1 for _, rs in all_results for r in rs if not r.passed and r.gate == "soft")
        print(f"[harness] done in {runtime:.1f}s "
              f"(hard-fail={n_fail} warn={n_warn} errors={len(errors)} exit={exit_code})",
              file=sys.stderr)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
