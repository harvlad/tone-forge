"""Score V3 top-5 usefulness ratings.

Reads ``preset_catalog_output/retrieval/v3_top5_usefulness_rating.json``
after the operator has filled in:

- ``top5[r].rating_would_start_from_this`` (bool / null)
- ``query_summary.any_top5_usable`` (bool / null)
- ``query_summary.best_rank`` (int 1-5 / null)

Computes the per-cohort rates and applies the retrieval gate.

Cohorts
-------
- Workhorse: bass, lead, pad, other  (drives the retrieval-gate decision)
- Minor:     keys, fx, percussion    (diagnostic only)

Gate
----
PASS if workhorse ``any_top5_usable >= 0.70``, else FAIL. The minor cohort
result is reported but does not gate.

Outputs
-------
- ``preset_catalog_output/retrieval/v3_score_report.json``  (structured)
- ``preset_catalog_output/retrieval/v3_score_report.md``    (human report)

Exit codes
----------
- 0  gate PASS
- 1  gate FAIL or ratings incomplete (any null in workhorse cohort)
- 2  malformed input
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean

WORKHORSE = {"bass", "lead", "pad", "other"}
MINOR = {"keys", "fx", "percussion"}
GATE_THRESHOLD = 0.70

RATING_FILE = Path(
    "preset_catalog_output/retrieval/v3_top5_usefulness_rating.json"
)
OUT_JSON = Path("preset_catalog_output/retrieval/v3_score_report.json")
OUT_MD = Path("preset_catalog_output/retrieval/v3_score_report.md")


def _cohort(sound_type: str) -> str:
    if sound_type in WORKHORSE:
        return "workhorse"
    if sound_type in MINOR:
        return "minor"
    return "unknown"


def _summarise(queries):
    """Return dict of per-cohort metrics."""
    buckets = defaultdict(list)
    for q in queries:
        buckets[_cohort(q["query_sound_type"])].append(q)

    out = {}
    for cohort, items in buckets.items():
        any_usable = [q["query_summary"]["any_top5_usable"] for q in items]
        best_rank = [q["query_summary"]["best_rank"] for q in items]
        n = len(items)
        n_rated = sum(1 for v in any_usable if v is not None)
        n_unrated = n - n_rated
        n_usable = sum(1 for v in any_usable if v is True)
        rate = n_usable / n_rated if n_rated else None

        # Best-rank distribution restricted to usable queries.
        rank_dist = Counter()
        for q, usable in zip(items, any_usable):
            if usable is True:
                br = q["query_summary"]["best_rank"]
                rank_dist[br if br is not None else "unspecified"] += 1
            elif usable is False:
                rank_dist["none"] += 1

        # Per-rank "would_start_from" hit rate across all items.
        per_rank = defaultdict(lambda: {"true": 0, "false": 0, "null": 0})
        for q in items:
            for nb in q["top5"]:
                v = nb.get("rating_would_start_from_this")
                key = "true" if v is True else "false" if v is False else "null"
                per_rank[nb["rank"]][key] += 1

        out[cohort] = {
            "n": n,
            "n_rated": n_rated,
            "n_unrated": n_unrated,
            "n_usable": n_usable,
            "any_top5_usable_rate": rate,
            "best_rank_distribution": dict(rank_dist),
            "per_rank_would_start_from": {
                int(r): dict(v) for r, v in sorted(per_rank.items())
            },
            "per_sound_type": dict(
                Counter(q["query_sound_type"] for q in items)
            ),
        }
    return out


def _format_pct(x):
    return "n/a" if x is None else f"{x * 100:.1f}%"


def _build_markdown(summary, gate_pass, gate_value, threshold, queries):
    lines = ["# V3 Top-5 Usefulness Report", ""]
    wh = summary.get("workhorse", {})
    mn = summary.get("minor", {})

    lines.append(f"**Gate:** workhorse any_top5_usable >= {threshold*100:.0f}%")
    lines.append(
        f"**Result:** "
        f"{'PASS' if gate_pass else 'FAIL'} "
        f"(workhorse rate = {_format_pct(gate_value)})"
    )
    lines.append("")

    for label, key in [("Workhorse (gate cohort)", "workhorse"),
                       ("Minor (diagnostic only)", "minor")]:
        s = summary.get(key)
        if not s:
            continue
        lines.append(f"## {label}")
        lines.append(f"- classes: `{s['per_sound_type']}`")
        lines.append(f"- n_total: {s['n']}, n_rated: {s['n_rated']}, "
                     f"n_unrated: {s['n_unrated']}")
        lines.append(
            f"- any_top5_usable: {s['n_usable']}/{s['n_rated']} "
            f"= {_format_pct(s['any_top5_usable_rate'])}"
        )
        lines.append(f"- best-rank distribution: {s['best_rank_distribution']}")
        lines.append("- per-rank 'would start from this' counts:")
        for r, d in s["per_rank_would_start_from"].items():
            total = d["true"] + d["false"]
            rate = d["true"] / total if total else None
            lines.append(
                f"    rank {r}: true={d['true']}, false={d['false']}, "
                f"null={d['null']}, hit_rate={_format_pct(rate)}"
            )
        lines.append("")

    lines.append("## Per-query detail")
    for q in queries:
        cohort = _cohort(q["query_sound_type"])
        s = q["query_summary"]
        usable = s["any_top5_usable"]
        rank = s["best_rank"]
        usable_str = (
            "PASS" if usable is True
            else "fail" if usable is False
            else "UNRATED"
        )
        lines.append(
            f"- [{cohort:9s}] [{q['query_sound_type']:10s}] "
            f"{q['query_name']!r:35s} -> {usable_str}, best_rank={rank}"
        )

    return "\n".join(lines) + "\n"


def main() -> int:
    if not RATING_FILE.exists():
        print(f"ERROR: rating file not found: {RATING_FILE}", file=sys.stderr)
        return 2
    data = json.loads(RATING_FILE.read_text())
    queries = data.get("queries")
    if not isinstance(queries, list):
        print("ERROR: rating file missing 'queries' list", file=sys.stderr)
        return 2

    summary = _summarise(queries)
    wh = summary.get("workhorse", {})
    wh_rate = wh.get("any_top5_usable_rate")
    wh_unrated = wh.get("n_unrated", 0)

    if wh_unrated > 0 or wh_rate is None:
        gate_pass = False
        gate_reason = (
            f"workhorse cohort has {wh_unrated} unrated queries; "
            f"gate cannot be evaluated"
        )
    else:
        gate_pass = wh_rate >= GATE_THRESHOLD
        gate_reason = (
            f"workhorse any_top5_usable={wh_rate:.3f} "
            f"{'>=' if gate_pass else '<'} {GATE_THRESHOLD}"
        )

    report = {
        "gate": {
            "threshold": GATE_THRESHOLD,
            "passed": bool(gate_pass),
            "workhorse_rate": wh_rate,
            "reason": gate_reason,
        },
        "cohorts": summary,
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(report, indent=2))
    OUT_MD.write_text(_build_markdown(
        summary, gate_pass, wh_rate, GATE_THRESHOLD, queries
    ))

    print(f"Wrote {OUT_JSON}")
    print(f"Wrote {OUT_MD}")
    print()
    print(f"Workhorse:  n={wh.get('n', 0)}, "
          f"rated={wh.get('n_rated', 0)}, "
          f"any_top5_usable={_format_pct(wh_rate)}")
    mn = summary.get("minor", {})
    print(f"Minor:      n={mn.get('n', 0)}, "
          f"rated={mn.get('n_rated', 0)}, "
          f"any_top5_usable={_format_pct(mn.get('any_top5_usable_rate'))}")
    print()
    print(f"GATE: {'PASS' if gate_pass else 'FAIL'}  ({gate_reason})")

    return 0 if gate_pass else 1


if __name__ == "__main__":
    sys.exit(main())
