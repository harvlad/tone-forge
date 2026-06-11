"""Phase 2 validation benchmark runner.

Three subcommands, each appending to the same Markdown report so the
full validation lands in one file. Designed to be re-run from a clean
checkout — every output section starts with a unique fence so reruns
overwrite their previous output without disturbing the operator-filled
sections.

Subcommands
-----------
* ``delta-report``  — Step 2. Reads the frozen placeholder snapshot
  and the now-overwritten ``tfc.*.fingerprint.json`` files; emits the
  per-chain delta table and z-norm shifts.
* ``alcest``        — Step 3. Runs ``guitar_catalog.recommend`` against
  the Phase-1 Alcest stem and prints the comparison table + full wire
  dict.
* ``validate``      — Steps 4–7. Walks the corpus JSON, runs each
  song through the recommender, builds the ranking table, runs the
  failure analysis on misses, and issues the Go / No-Go verdict.

Anti-goals
----------
* Does not refit calibration.
* Does not modify catalog YAMLs or fingerprint JSONs.
* Does not run yt-dlp or the local engine: each corpus row must point
  at a guitar stem on disk (``stem_path``) so this script stays small
  and reproducible. Stem extraction is delegated to the Jam UI / local
  engine (the operator runs that out-of-band).

Report contract
---------------
Every section emitted by this script is wrapped between two markers:
::

  <!-- phase2:auto-start <section> -->
  ...
  <!-- phase2:auto-end <section> -->

A re-run replaces only the content between the matching markers,
leaving operator-filled sections (e.g. §0 rendering provenance,
§4 corpus) untouched.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import logging
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

# Ensure backend/ is on sys.path so this script can be invoked from anywhere.
_HERE = Path(__file__).resolve()
_BACKEND_ROOT = _HERE.parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from tone_forge.tone import guitar_catalog as gc  # noqa: E402

logger = logging.getLogger("run_phase2_benchmark")

# ---------------------------------------------------------------------------
# Section-marker plumbing
# ---------------------------------------------------------------------------

_MARKER_FMT = "<!-- phase2:auto-{kind} {section} -->"


def _replace_section(report_path: Path, section: str, body: str) -> None:
    """Replace the content between phase2:auto-start/end markers.

    If the section doesn't exist yet (first run), appends it. Idempotent
    on re-runs so partial validation passes are safe.
    """
    start = _MARKER_FMT.format(kind="start", section=section)
    end = _MARKER_FMT.format(kind="end", section=section)
    block = f"{start}\n{body.rstrip()}\n{end}"

    existing = report_path.read_text(encoding="utf-8") if report_path.exists() else ""
    pattern = re.compile(
        re.escape(start) + r"[\s\S]*?" + re.escape(end), re.MULTILINE,
    )
    if pattern.search(existing):
        updated = pattern.sub(block, existing)
    else:
        # Append at the bottom under a clear heading.
        sep = "\n\n" if existing and not existing.endswith("\n\n") else ""
        updated = existing + sep + f"<!-- phase2 section: {section} -->\n" + block + "\n"
    report_path.write_text(updated, encoding="utf-8")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _load_measured_fingerprints() -> Dict[str, Dict[str, float]]:
    """Return ``{chain_id: features_dict}`` for the *current* catalog state."""
    chains_dir = _BACKEND_ROOT / "tone_forge" / "monitor" / "chains"
    out: Dict[str, Dict[str, float]] = {}
    for fp in sorted(chains_dir.glob("tfc.*.fingerprint.json")):
        chain_id = fp.stem.replace(".fingerprint", "")
        data = json.loads(fp.read_text(encoding="utf-8"))
        out[chain_id] = {
            "_source": data.get("source", "unknown"),
            "_rendered_from": data.get("rendered_from"),
            **{k: float(v) for k, v in (data.get("features") or {}).items()},
        }
    return out


def _per_feature_std() -> Dict[str, float]:
    """Return per-feature std-dev from the live catalog (post-overwrite)."""
    cat = gc._get_catalog()
    return {k: float(cat.feature_std[i]) for i, k in enumerate(gc._FEATURE_KEYS)}


def _znorm_distance(
    a: Dict[str, float], b: Dict[str, float], stds: Dict[str, float]
) -> float:
    """Per-spec z-normalised L2 between two feature dicts."""
    sq = 0.0
    for k in gc._FEATURE_KEYS:
        sd = max(stds.get(k, 1e-3), 1e-3)
        sq += ((a[k] - b[k]) / sd) ** 2
    return float(np.sqrt(sq))


def _now() -> str:
    return _dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


# ---------------------------------------------------------------------------
# delta-report (Step 2)
# ---------------------------------------------------------------------------


def cmd_delta_report(*, placeholders_path: Path, out_path: Path) -> int:
    """Emit the per-chain placeholder→measured delta table."""
    snap = json.loads(placeholders_path.read_text(encoding="utf-8"))
    placeholders = snap["placeholders"]
    measured = _load_measured_fingerprints()
    stds = _per_feature_std()

    # Refuse to run if any chain still carries the placeholder source —
    # the delta would be 0 by construction and the report would lie.
    still_placeholder = [
        cid for cid, feats in measured.items()
        if feats.get("_source") == "hand_authored_estimate"
    ]
    if still_placeholder:
        msg = (
            f"Refusing to emit delta report. The following chains still carry "
            f"hand_authored_estimate source: {still_placeholder}. "
            f"Render the missing WAVs and re-run render_chain_references.py first."
        )
        logger.error(msg)
        _replace_section(out_path, "delta-report", f"**BLOCKED:** {msg}")
        return 1

    lines: List[str] = []
    lines.append(f"_Generated at {_now()}._")
    lines.append("")

    total_shift = 0.0
    per_chain_shift: List[Tuple[str, float]] = []
    rank_preserved = 0

    for chain_id in sorted(set(placeholders) | set(measured)):
        ph = placeholders[chain_id]["features"]
        ms = {k: measured[chain_id][k] for k in gc._FEATURE_KEYS}
        d = _znorm_distance(ph, ms, stds)
        total_shift += d
        per_chain_shift.append((chain_id, d))

        # Rank-preservation: top-2 strongest features by absolute value.
        ph_top2 = {k for k, _ in sorted(ph.items(), key=lambda x: -abs(x[1]))[:2]}
        ms_top2 = {k for k, _ in sorted(ms.items(), key=lambda x: -abs(x[1]))[:2]}
        if ph_top2 == ms_top2:
            rank_preserved += 1

        lines.append(f"### `{chain_id}`")
        lines.append("")
        lines.append("| feature | placeholder | measured | delta |")
        lines.append("|---|---|---|---|")
        for k in gc._FEATURE_KEYS:
            v_ph, v_ms = float(ph[k]), float(ms[k])
            delta = v_ms - v_ph
            lines.append(f"| {k} | {v_ph:.4g} | {v_ms:.4g} | {delta:+.4g} |")
        lines.append("")
        lines.append(f"**z-norm distance (placeholder → measured): `{d:.4f}`**")
        lines.append("")

    n = len(per_chain_shift)
    largest = max(per_chain_shift, key=lambda x: x[1]) if per_chain_shift else ("-", 0)
    smallest = min(per_chain_shift, key=lambda x: x[1]) if per_chain_shift else ("-", 0)
    avg = total_shift / max(n, 1)

    lines.append("### Aggregate")
    lines.append("")
    lines.append("| Stat | Value |")
    lines.append("|---|---|")
    lines.append(f"| Total z-norm shift (sum across {n} chains) | {total_shift:.4f} |")
    lines.append(f"| Mean shift per chain | {avg:.4f} |")
    lines.append(f"| Largest shift | `{largest[0]}` ({largest[1]:.4f}) |")
    lines.append(f"| Smallest shift | `{smallest[0]}` ({smallest[1]:.4f}) |")
    lines.append(f"| Chains with top-2 features rank-preserved | {rank_preserved} / {n} |")
    lines.append("")

    # Heuristic for "directionally accurate":
    #   - Per-chain shift < 5.0 z-units AND rank-preserved → "directionally accurate"
    #   - shift ≥ 5.0 OR rank flipped → "directionally wrong"
    accurate_chains = sum(
        1 for cid, shift in per_chain_shift
        if shift < 5.0
    )
    accurate_and_rank_preserved = rank_preserved

    lines.append("**Q: Were the original estimates directionally accurate?**")
    lines.append("")
    if accurate_chains == n and rank_preserved == n:
        verdict = "Yes — every chain's measured fingerprint lands within 5 z-units of its placeholder AND top-2 feature ranking preserved."
    elif accurate_chains >= int(0.6 * n):
        verdict = f"Mixed — {accurate_chains}/{n} chains within 5 z-units; {rank_preserved}/{n} preserved their top-2 ranking. See per-chain rows above."
    else:
        verdict = f"No — only {accurate_chains}/{n} chains landed within 5 z-units and {rank_preserved}/{n} preserved their top-2 ranking. Placeholders were poor estimates."
    lines.append(verdict)

    _replace_section(out_path, "delta-report", "\n".join(lines))
    return 0


# ---------------------------------------------------------------------------
# alcest (Step 3)
# ---------------------------------------------------------------------------

# Frozen Phase-1 numbers for the comparison column. If the Phase-1 result
# is regenerated, update these — they are the "before" of the delta.
_PHASE1_ALCEST = {
    "top_match_raw": "tfc.ambient",
    "distance_to_top": 30.26,
    "distance_to_runner_up": 33.04,
    "margin": 0.08,
    "confidence": 0.12,
    "tier": "low",
    "apply": "tfc.classic_rock (fallback)",
}


def cmd_alcest(*, stem_path: Path, tempo: float, key: str, out_path: Path) -> int:
    """Run the recommender on the Alcest stem and emit the §3 comparison."""
    if not stem_path.is_file():
        msg = f"Alcest stem not found at {stem_path}. Re-extract via the Jam UI."
        logger.error(msg)
        _replace_section(out_path, "alcest", f"**BLOCKED:** {msg}")
        return 1

    rec = gc.recommend_from_tempo_key(stem_path, tempo_bpm=tempo, key=key)
    wire = gc.to_wire_dict(rec)
    ranking = wire["debug"]["ranking"]
    top = ranking[0]
    runner = ranking[1] if len(ranking) > 1 else {"chain_id": "-", "distance": float("nan")}

    measured_tier = wire["tier"]
    measured_conf = (wire.get("match") or {}).get("confidence")
    measured_margin = wire["debug"].get("margin")
    measured_apply = wire["apply"]["chain_id"]

    def _fmt(v: Any) -> str:
        if v is None:
            return "—"
        if isinstance(v, float):
            return f"{v:.4f}"
        return str(v)

    lines: List[str] = []
    lines.append(f"_Generated at {_now()}._")
    lines.append("")
    lines.append(f"Stem: `{stem_path}`  ·  Tempo: `{tempo}`  ·  Key: `{key}`")
    lines.append("")
    lines.append("### Comparison table")
    lines.append("")
    lines.append("| Field | Phase 1 (placeholder) | Phase 2 (measured) | Δ |")
    lines.append("|---|---|---|---|")
    lines.append(
        f"| Top match (raw) | `{_PHASE1_ALCEST['top_match_raw']}` | "
        f"`{top['chain_id']}` | "
        f"{'same' if top['chain_id'] == _PHASE1_ALCEST['top_match_raw'] else 'CHANGED'} |"
    )
    lines.append(
        f"| Distance to top | {_PHASE1_ALCEST['distance_to_top']:.2f} | "
        f"{top['distance']:.4f} | "
        f"{top['distance'] - _PHASE1_ALCEST['distance_to_top']:+.4f} |"
    )
    lines.append(
        f"| Distance to runner-up | {_PHASE1_ALCEST['distance_to_runner_up']:.2f} | "
        f"{runner['distance']:.4f} | "
        f"{runner['distance'] - _PHASE1_ALCEST['distance_to_runner_up']:+.4f} |"
    )
    lines.append(
        f"| Margin | {_PHASE1_ALCEST['margin']:.4f} | {_fmt(measured_margin)} | — |"
    )
    lines.append(
        f"| Confidence | {_PHASE1_ALCEST['confidence']:.4f} | {_fmt(measured_conf)} | — |"
    )
    lines.append(
        f"| Tier | `{_PHASE1_ALCEST['tier']}` | `{measured_tier}` | — |"
    )
    lines.append(
        f"| Apply | `{_PHASE1_ALCEST['apply']}` | `{measured_apply}` | — |"
    )
    lines.append("")

    # Answers
    ambient_top1 = top["chain_id"] == "tfc.ambient"
    distance_drop = _PHASE1_ALCEST["distance_to_top"] - top["distance"]
    conf_improved = (measured_conf or 0.0) > _PHASE1_ALCEST["confidence"]
    tier_order = {"unknown": 0, "low": 1, "medium": 2, "high": 3}
    tier_improved = tier_order.get(measured_tier, 0) > tier_order[_PHASE1_ALCEST["tier"]]

    lines.append("### Answers to the 5 brief questions")
    lines.append("")
    lines.append(f"1. **Does Ambient remain the top recommendation?** {'Y' if ambient_top1 else 'N'}")
    lines.append(
        f"2. **Did the distance collapse from ~30 toward the expected range?** "
        f"from {_PHASE1_ALCEST['distance_to_top']:.2f} → {top['distance']:.4f} "
        f"({distance_drop:+.4f})"
    )
    lines.append(
        f"3. **Did confidence improve?** "
        f"from {_PHASE1_ALCEST['confidence']:.4f} → {_fmt(measured_conf)} "
        f"({'Y' if conf_improved else 'N'})"
    )
    lines.append(
        f"4. **Did tier improve?** {_PHASE1_ALCEST['tier']} → {measured_tier} "
        f"({'Y' if tier_improved else 'N'})"
    )
    rationale = (
        "Ambient won top-1 and the measured fingerprint pulled toward the query."
        if ambient_top1
        else f"Top-1 is now {top['chain_id']} (not ambient). Likely cause: "
             f"the chain whose fingerprint is closest to a real shoegaze tone "
             f"is no longer ambient; see §6 for diagnosis."
    )
    lines.append(f"5. **Is the recommendation still directionally correct?** {'Y' if ambient_top1 else 'N'} — {rationale}")
    lines.append("")

    lines.append("### Full recommendation object (measured)")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(wire, indent=2))
    lines.append("```")

    _replace_section(out_path, "alcest", "\n".join(lines))
    return 0 if ambient_top1 else 2  # exit code 2 = directional miss; user decides


# ---------------------------------------------------------------------------
# validate (Steps 4-7)
# ---------------------------------------------------------------------------

_GENRE_TO_EXPECTED = {
    "shoegaze": {"tfc.ambient"},
    "post-rock": {"tfc.ambient"},
    "ambient": {"tfc.ambient"},
    "classic_rock": {"tfc.classic_rock"},
    "blues": {"tfc.classic_rock", "tfc.edge_of_breakup"},
    "indie": {"tfc.edge_of_breakup", "tfc.clean_strat"},
    "modern_gain": {"tfc.modern_gain"},
    "djent": {"tfc.modern_gain"},
    "metal": {"tfc.classic_rock", "tfc.modern_gain"},
    "clean": {"tfc.clean_strat"},
    "jangle": {"tfc.clean_strat"},
    "edge_of_breakup": {"tfc.edge_of_breakup"},
}


def _failure_class(query_vec: Dict[str, float], expected: str, actual: str,
                   ranking: List[Dict[str, Any]], stem_path: Optional[Path]) -> str:
    """Heuristic classification of a misranking.

    Order of checks (return on first match):
      stem_selection  — stem file missing or zero-size
      catalog         — expected chain ranked last, suggesting it's nowhere
                        near the query in feature space
      ranking         — expected chain ranked 2nd or 3rd; close miss
      feature         — expected chain ranked 4th-5th but margin is tight
      calibration     — top-1 correct but tier was demoted (caller handles)
    """
    if stem_path is None or not Path(stem_path).is_file():
        return "stem_selection"

    expected_rank = next(
        (i for i, r in enumerate(ranking) if r["chain_id"] == expected), -1
    )
    if expected_rank < 0:
        return "catalog"
    if expected_rank == len(ranking) - 1:
        return "catalog"
    if expected_rank in (1, 2):
        return "ranking"
    # 3rd or 4th
    top_d = ranking[0]["distance"]
    exp_d = ranking[expected_rank]["distance"]
    if abs(exp_d - top_d) < 1.0:
        return "ranking"
    return "feature"


def cmd_validate(*, corpus_path: Path, out_path: Path,
                 results_jsonl: Optional[Path] = None) -> int:
    """Run the ranking validation, failure analysis, and verdict."""
    if not corpus_path.is_file():
        msg = (
            f"Benchmark corpus not found at {corpus_path}. "
            "Operator must create PHASE2_BENCHMARK_CORPUS.json with 10-20 rows."
        )
        logger.error(msg)
        _replace_section(out_path, "validate", f"**BLOCKED:** {msg}")
        return 1

    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    if not isinstance(corpus, list) or not corpus:
        _replace_section(out_path, "validate", "**BLOCKED:** corpus is empty or not a list.")
        return 1

    results: List[Dict[str, Any]] = []
    n_pass1 = n_pass2 = n_pass3 = 0
    per_genre: Dict[str, Dict[str, int]] = defaultdict(
        lambda: {"n": 0, "pass1": 0, "pass3": 0}
    )

    for row in corpus:
        artist = row.get("artist", "?")
        song = row.get("song", "?")
        expected = row.get("expected_chain") or row.get("expected")
        genre = row.get("genre", "")
        stem_path = row.get("stem_path")
        tempo = float(row.get("tempo_bpm") or 120.0)
        key = row.get("key") or "C Major"

        if not expected:
            logger.warning("Row missing expected_chain: %s — %s. Skipping.", artist, song)
            continue

        wire = None
        ranking: List[Dict[str, Any]] = []
        if stem_path and Path(stem_path).is_file():
            try:
                rec = gc.recommend_from_tempo_key(Path(stem_path), tempo_bpm=tempo, key=key)
                wire = gc.to_wire_dict(rec)
                ranking = wire["debug"]["ranking"]
            except Exception as exc:
                logger.warning("recommend() failed for %s: %s", song, exc)
        else:
            logger.warning(
                "No stem_path on disk for %s — %s (got %r). "
                "Operator must extract via Jam UI and update the corpus row.",
                artist, song, stem_path,
            )

        top1 = ranking[0]["chain_id"] if ranking else None
        top2 = ranking[1]["chain_id"] if len(ranking) > 1 else None
        top3 = ranking[2]["chain_id"] if len(ranking) > 2 else None
        top1_dist = ranking[0]["distance"] if ranking else None
        top1_conf = (wire.get("match") if wire else {} or {}).get("confidence") if wire else None
        tier = wire["tier"] if wire else "unknown"

        pass1 = (top1 == expected)
        pass3 = expected in {top1, top2, top3}
        if pass1:
            n_pass1 += 1
        if expected in {top1, top2}:
            n_pass2 += 1
        if pass3:
            n_pass3 += 1

        per_genre[genre]["n"] += 1
        per_genre[genre]["pass1"] += int(pass1)
        per_genre[genre]["pass3"] += int(pass3)

        results.append({
            "artist": artist,
            "song": song,
            "genre": genre,
            "expected": expected,
            "stem_path": stem_path,
            "top1": top1, "top2": top2, "top3": top3,
            "top1_distance": top1_dist,
            "top1_confidence": top1_conf,
            "margin": (wire["debug"].get("margin") if wire else None),
            "tier": tier,
            "ranking": ranking,
            "query_vector": (wire["debug"].get("query_vector") if wire else None),
            "pass1": pass1, "pass3": pass3,
        })

    if results_jsonl is not None:
        with results_jsonl.open("w", encoding="utf-8") as fh:
            for r in results:
                fh.write(json.dumps(r) + "\n")

    n = max(len(results), 1)

    # ---- §5 ranking table ----
    lines: List[str] = []
    lines.append(f"_Generated at {_now()}._")
    lines.append("")
    lines.append("| # | Artist | Song | Expected | Top 1 (d, conf) | Top 2 (d) | Top 3 (d) | Tier | Pass? |")
    lines.append("|---|--------|------|----------|-----------------|-----------|-----------|------|-------|")
    for i, r in enumerate(results, 1):
        ranking = r["ranking"]
        def _cell(rank_idx: int) -> str:
            if rank_idx >= len(ranking):
                return "—"
            chain = ranking[rank_idx]["chain_id"]
            d = ranking[rank_idx]["distance"]
            if rank_idx == 0 and r["top1_confidence"] is not None:
                return f"`{chain}` ({d:.2f}, conf={r['top1_confidence']:.2f})"
            return f"`{chain}` ({d:.2f})"
        pass_mark = "✓" if r["pass3"] else "✗"
        lines.append(
            f"| {i} | {r['artist']} | {r['song']} | `{r['expected']}` | "
            f"{_cell(0)} | {_cell(1)} | {_cell(2)} | {r['tier']} | {pass_mark} |"
        )

    lines.append("")
    lines.append("**Aggregate accuracy:**")
    lines.append("")
    lines.append("| Metric | Value | N |")
    lines.append("|---|---|---|")
    lines.append(f"| Top-1 accuracy | {100*n_pass1/n:.1f}% | {n_pass1} / {n} |")
    lines.append(f"| Top-2 accuracy | {100*n_pass2/n:.1f}% | {n_pass2} / {n} |")
    lines.append(f"| Top-3 accuracy | {100*n_pass3/n:.1f}% | {n_pass3} / {n} |")
    lines.append("")

    if per_genre:
        lines.append("**Per-genre breakdown:**")
        lines.append("")
        lines.append("| Genre | N | Top-1 | Top-3 |")
        lines.append("|---|---|---|---|")
        for g, agg in sorted(per_genre.items()):
            if not g:
                g_disp = "(unlabeled)"
            else:
                g_disp = g
            lines.append(
                f"| {g_disp} | {agg['n']} | "
                f"{agg['pass1']} / {agg['n']} | {agg['pass3']} / {agg['n']} |"
            )

    _replace_section(out_path, "validate", "\n".join(lines))

    # ---- §6 failure analysis ----
    fail_lines: List[str] = []
    fail_lines.append(f"_Generated at {_now()}._")
    fail_lines.append("")
    class_counts: Counter = Counter()
    for r in results:
        if r["pass3"]:
            continue
        cls = _failure_class(
            r.get("query_vector") or {}, r["expected"], r["top1"] or "",
            r["ranking"], Path(r["stem_path"]) if r.get("stem_path") else None,
        )
        class_counts[cls] += 1
        fail_lines.append(f"### {r['artist']} — {r['song']}")
        fail_lines.append("")
        fail_lines.append("| Field | Value |")
        fail_lines.append("|---|---|")
        fail_lines.append(f"| Expected | `{r['expected']}` |")
        fail_lines.append(f"| Actual top-1 | `{r['top1']}` (d={r['top1_distance']}, conf={r['top1_confidence']}) |")
        fail_lines.append(f"| Margin | {r['margin']} |")
        fail_lines.append(f"| Tier | {r['tier']} |")
        fail_lines.append("")
        if r.get("query_vector"):
            qv = r["query_vector"]
            qv_named = dict(zip(gc._FEATURE_KEYS, qv))
            fail_lines.append("**Query feature vector:**")
            fail_lines.append("")
            fail_lines.append("```")
            fail_lines.append(
                "  ".join(f"{k}={v:.4g}" for k, v in qv_named.items())
            )
            fail_lines.append("```")
            fail_lines.append("")
        fail_lines.append("**Full distance ranking:**")
        fail_lines.append("")
        fail_lines.append("```")
        for i, rk in enumerate(r["ranking"], 1):
            fail_lines.append(f"  {i}. {rk['chain_id']}  d={rk['distance']:.4f}")
        fail_lines.append("```")
        fail_lines.append("")
        fail_lines.append(f"**Failure class:** `{cls}`")
        fail_lines.append("")
        fail_lines.append("**Probable cause:** _<engineering judgement to be added by Claude on review>_")
        fail_lines.append("")

    if not class_counts:
        fail_lines.append("_No failures — all corpus rows passed top-3._")
    else:
        fail_lines.append("### Failure-class summary")
        fail_lines.append("")
        fail_lines.append("| Class | Count |")
        fail_lines.append("|---|---|")
        for cls, ct in class_counts.most_common():
            fail_lines.append(f"| `{cls}` | {ct} |")

    _replace_section(out_path, "failure-analysis", "\n".join(fail_lines))

    # ---- §7 verdict ----
    top1_acc = n_pass1 / n
    top3_acc = n_pass3 / n

    # Override: ambient must be top-1 for at least one shoegaze/post-rock row
    sg_rows = [
        r for r in results
        if (r.get("genre", "").lower() in {"shoegaze", "post-rock", "ambient"})
    ]
    sg_ambient_top1 = any(r["top1"] == "tfc.ambient" for r in sg_rows)

    if top1_acc >= 0.70 and top3_acc >= 0.90 and (not sg_rows or sg_ambient_top1):
        verdict = "GO"
        rationale = (
            "Top-1 ≥ 70% AND Top-3 ≥ 90%; ambient top-1 on at least one shoegaze/post-rock row. "
            "The current 5-chain system produces directionally correct rankings and is ready for broader testing."
        )
    elif top1_acc >= 0.50 and top3_acc >= 0.80 and (not sg_rows or sg_ambient_top1):
        verdict = "CONDITIONAL GO"
        rationale = (
            "Top-1 50-70% / Top-3 80-90%. Works for dominant genres but the failing categories in §6 must be triaged before broader rollout."
        )
    else:
        verdict = "NO-GO"
        if sg_rows and not sg_ambient_top1:
            rationale = (
                "Ambient is never top-1 for shoegaze/post-rock. The Phase 1 thesis "
                "(ambient is the most distinctive archetype) failed. "
                "Architectural change needed before broader testing."
            )
        else:
            rationale = (
                f"Top-1 {100*top1_acc:.0f}% / Top-3 {100*top3_acc:.0f}% below threshold. "
                "Architectural change needed before broader testing."
            )

    verd_lines = [
        f"_Generated at {_now()}._",
        "",
        "| Metric | Threshold | Observed |",
        "|---|---|---|",
        f"| Top-1 accuracy | ≥ 70% (GO) · ≥ 50% (Conditional) | {100*top1_acc:.1f}% |",
        f"| Top-3 accuracy | ≥ 90% (GO) · ≥ 80% (Conditional) | {100*top3_acc:.1f}% |",
        f"| Ambient → top-1 for shoegaze/post-rock | required for GO | {'Y' if (not sg_rows or sg_ambient_top1) else 'N'} |",
        "",
        f"**Verdict:** **{verdict}**",
        "",
        f"**Rationale:** {rationale}",
        "",
        "**Failure-class summary:**",
        "",
    ]
    if class_counts:
        verd_lines.append("| Class | Count |")
        verd_lines.append("|---|---|")
        for cls, ct in class_counts.most_common():
            verd_lines.append(f"| `{cls}` | {ct} |")
    else:
        verd_lines.append("_No failures._")

    _replace_section(out_path, "verdict", "\n".join(verd_lines))

    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="run_phase2_benchmark")
    p.add_argument("--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_delta = sub.add_parser("delta-report", help="Step 2 — placeholder vs measured deltas.")
    p_delta.add_argument(
        "--placeholders", type=Path,
        default=_BACKEND_ROOT / "PHASE2_PLACEHOLDER_FINGERPRINTS.json",
    )
    p_delta.add_argument(
        "--out", type=Path,
        default=_BACKEND_ROOT / "PHASE2_VALIDATION_REPORT.md",
    )

    p_alc = sub.add_parser("alcest", help="Step 3 — re-test Alcest under measured fingerprints.")
    p_alc.add_argument("--stem", type=Path, required=True)
    p_alc.add_argument("--tempo", type=float, default=104.0)
    p_alc.add_argument("--key", type=str, default="E Minor")
    p_alc.add_argument(
        "--out", type=Path,
        default=_BACKEND_ROOT / "PHASE2_VALIDATION_REPORT.md",
    )

    p_val = sub.add_parser("validate", help="Steps 4-7 — corpus, ranking, failures, verdict.")
    p_val.add_argument(
        "--corpus", type=Path,
        default=_BACKEND_ROOT / "PHASE2_BENCHMARK_CORPUS.json",
    )
    p_val.add_argument(
        "--out", type=Path,
        default=_BACKEND_ROOT / "PHASE2_VALIDATION_REPORT.md",
    )
    p_val.add_argument(
        "--results-jsonl", type=Path,
        default=_BACKEND_ROOT / "PHASE2_BENCHMARK_RESULTS.jsonl",
    )

    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    gc._reset_catalog_cache()

    if args.cmd == "delta-report":
        return cmd_delta_report(
            placeholders_path=args.placeholders, out_path=args.out,
        )
    if args.cmd == "alcest":
        return cmd_alcest(
            stem_path=args.stem, tempo=args.tempo, key=args.key, out_path=args.out,
        )
    if args.cmd == "validate":
        return cmd_validate(
            corpus_path=args.corpus, out_path=args.out,
            results_jsonl=args.results_jsonl,
        )
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
