"""Score the FX label audit worksheet.

Reads ``preset_catalog_output/retrieval/fx_label_audit_worksheet.json``
after the operator has filled in ``operator_label`` and (optionally)
``operator_note`` for each item.

Decision rule encoded here:
    relabel rate >= 0.40  -> taxonomy is the dominant problem
                             (clean labels before any FX representation
                             work)
    0.20 <= rate < 0.40   -> mixed; cleanup AND representation work
                             likely both needed
    rate < 0.20           -> representation is the dominant problem
                             (most fx labels are correct; embedding
                             does not cluster them)

Outputs:
- ``preset_catalog_output/retrieval/fx_label_audit_report.json`` (structured)
- ``preset_catalog_output/retrieval/fx_label_audit_report.md``   (human report)

Exit codes:
- 0 on success (file fully rated)
- 1 if any item is unrated
- 2 on malformed input
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

WORKSHEET = Path(
    "preset_catalog_output/retrieval/fx_label_audit_worksheet.json"
)
OUT_JSON = Path(
    "preset_catalog_output/retrieval/fx_label_audit_report.json"
)
OUT_MD = Path(
    "preset_catalog_output/retrieval/fx_label_audit_report.md"
)

VALID_LABELS = {"fx", "bass", "lead", "pad", "keys", "percussion", "other"}


def _classify_rate(rate: float) -> tuple[str, str]:
    if rate >= 0.40:
        return ("taxonomy", "Taxonomy cleanup must precede FX representation work.")
    if rate >= 0.20:
        return ("mixed",
                "Both label cleanup and representation work likely needed; "
                "rerun this audit after cleanup before committing to feature work.")
    return ("representation",
            "Most fx labels are correct; the embedding does not cluster them. "
            "FX feature engineering is justified.")


def _md(items, n, n_rated, n_unrated, relabel_count, relabel_rate,
        verdict, recommendation, label_dist) -> str:
    lines = [
        "# FX Label Audit Report",
        "",
        f"**Worksheet:** `{WORKSHEET}`",
        f"**Items:** {n} ({n_rated} rated, {n_unrated} unrated)",
        f"**Relabeled away from fx:** {relabel_count} / {n_rated}"
        f"  = **{relabel_rate * 100:.1f}%**" if n_rated else "",
        "",
        "**Verdict:** "
        f"{verdict.upper()} — {recommendation}",
        "",
        "## Operator label distribution",
    ]
    for k, v in sorted(label_dist.items()):
        lines.append(f"- `{k}`: {v}")
    lines += ["", "## Per-item detail"]
    for it in items:
        cur = it["current_label_sound_type"]
        op = it.get("operator_label") or "(unrated)"
        marker = "  RELABEL" if op != "(unrated)" and op != cur else ""
        note = (it.get("operator_note") or "").strip()
        line = (f"- **{it['name']}** — current=`{cur}`, "
                f"operator=`{op}`{marker}")
        if note:
            line += f"  \n  note: {note}"
        lines.append(line)
    return "\n".join(lines) + "\n"


def main() -> int:
    if not WORKSHEET.exists():
        print(f"ERROR: worksheet not found: {WORKSHEET}", file=sys.stderr)
        return 2
    data = json.loads(WORKSHEET.read_text())
    items = data.get("items")
    if not isinstance(items, list):
        print("ERROR: worksheet missing 'items' list", file=sys.stderr)
        return 2

    n = len(items)
    n_rated = 0
    n_unrated = 0
    relabel_count = 0
    invalid = []
    label_dist: Counter = Counter()
    for it in items:
        op = it.get("operator_label")
        if op in (None, ""):
            n_unrated += 1
            continue
        n_rated += 1
        if op not in VALID_LABELS:
            invalid.append((it.get("name"), op))
            continue
        label_dist[op] += 1
        if op != it["current_label_sound_type"]:
            relabel_count += 1

    if invalid:
        print(
            "ERROR: invalid operator_label values: " + str(invalid),
            file=sys.stderr,
        )
        return 2

    relabel_rate = relabel_count / n_rated if n_rated else 0.0
    verdict, recommendation = _classify_rate(relabel_rate)

    report = {
        "n": n,
        "n_rated": n_rated,
        "n_unrated": n_unrated,
        "relabel_count": relabel_count,
        "relabel_rate": relabel_rate,
        "label_distribution": dict(label_dist),
        "verdict": verdict,
        "recommendation": recommendation,
        "items": [
            {
                "preset_id": it["preset_id"],
                "name": it["name"],
                "current_label_sound_type": it["current_label_sound_type"],
                "operator_label": it.get("operator_label"),
                "operator_note": it.get("operator_note", ""),
            }
            for it in items
        ],
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(report, indent=2))
    OUT_MD.write_text(_md(
        items, n, n_rated, n_unrated, relabel_count, relabel_rate,
        verdict, recommendation, label_dist,
    ))
    print(f"Wrote {OUT_JSON}")
    print(f"Wrote {OUT_MD}")
    print(
        f"n={n}  rated={n_rated}  unrated={n_unrated}  "
        f"relabel={relabel_count}/{n_rated} "
        f"({relabel_rate * 100:.1f}%)  verdict={verdict}"
    )
    return 0 if n_unrated == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
