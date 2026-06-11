#!/usr/bin/env python3
"""V2 preset retrieval evaluation (leave-one-out).

Two hypotheses are exercised against the V2 catalogs produced by
``build_preset_catalog_v2.py``:

V2-H1  Within-engine LOO retrieval
    For each preset P in engine E, query that engine's catalog (E itself
    auto-excludes P via PresetCatalog.find_similar's self-skip). Score:

        - top-1 hit:  rank-1 result shares sound_type with P
        - top-5 hit:  any of the top-5 results shares sound_type with P

    Acceptance threshold (from RECONSTRUCTION_TRIAL_PLAN / EXTRACTION_ROADMAP):
    top-5 hit-rate >= 60% per engine.

V2-H2  Cross-engine LOO retrieval
    For each preset P, query the *union* catalog (auto-excluding P). Score:

        - same_engine_bias: fraction of top-5 results sharing P's engine
        - cross_engine_topk:  fraction of queries whose top-5 contains at
          least one preset from a DIFFERENT engine sharing P's sound_type

    Acceptance: same_engine_bias <= 70% AND cross_engine_topk >= 50%.

Outputs (under report-dir, default preset_catalog_output/catalog/):
    - v2_retrieval_eval.json   (machine-readable per-engine / per-preset)
    - v2_retrieval_eval.md     (human report with summary tables)

Usage:
    python3 scripts/retrieval_eval_v2.py \\
        --catalog-dir preset_catalog_output/catalog \\
        --report-dir preset_catalog_output/catalog
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tone_forge.preset_catalog.catalog_builder import PresetCatalog, PresetFingerprint

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


# Acceptance thresholds (mirrors RECONSTRUCTION_TRIAL_PLAN.md V2-H1/H2).
V2_H1_TOPK_HIT_RATE_MIN = 0.60   # top-5 sound_type hit rate per engine
V2_H2_SAME_ENGINE_BIAS_MAX = 0.70
V2_H2_CROSS_ENGINE_TOPK_MIN = 0.50

TOP_K = 5


# --- Data structures ----------------------------------------------------------

@dataclass
class QueryResult:
    """Per-preset LOO retrieval record."""
    preset_id: str
    instrument: str
    sound_type: str
    top_neighbors: List[Tuple[str, str, str, float]] = field(default_factory=list)
    #                          ^id    ^engine ^sound_type ^distance
    top1_same_sound_type: bool = False
    topk_same_sound_type: bool = False
    same_engine_in_topk: int = 0
    cross_engine_same_sound_type_in_topk: int = 0


@dataclass
class EngineEval:
    engine: str
    n: int = 0
    top1_hits: int = 0
    topk_hits: int = 0
    per_sound_type_hits: Dict[str, Tuple[int, int]] = field(default_factory=dict)
    #                                       ^topk_hits  ^n
    queries: List[QueryResult] = field(default_factory=list)

    @property
    def top1_rate(self) -> float:
        return self.top1_hits / self.n if self.n else 0.0

    @property
    def topk_rate(self) -> float:
        return self.topk_hits / self.n if self.n else 0.0


@dataclass
class CrossEval:
    n: int = 0
    same_engine_bias_sum: float = 0.0  # mean fraction same-engine in topk
    cross_engine_topk_hits: int = 0
    per_engine: Dict[str, "CrossEval"] = field(default_factory=dict)
    queries: List[QueryResult] = field(default_factory=list)


# --- Within-engine evaluation -------------------------------------------------

def within_engine_eval(
    engine: str,
    catalog: PresetCatalog,
) -> EngineEval:
    """V2-H1: LOO retrieval within a single engine catalog."""
    rep = EngineEval(engine=engine, n=len(catalog.presets))
    sound_type_counters: Dict[str, List[int]] = defaultdict(lambda: [0, 0])

    for query in catalog.presets:
        results = catalog.find_similar(query, k=TOP_K)
        qr = QueryResult(
            preset_id=query.preset_id,
            instrument=query.instrument,
            sound_type=query.sound_type,
            top_neighbors=[
                (p.preset_id, p.instrument, p.sound_type, dist)
                for p, dist in results
            ],
        )

        if results:
            top1 = results[0][0]
            qr.top1_same_sound_type = (top1.sound_type == query.sound_type)
            qr.topk_same_sound_type = any(
                p.sound_type == query.sound_type for p, _ in results
            )

        if qr.top1_same_sound_type:
            rep.top1_hits += 1
        if qr.topk_same_sound_type:
            rep.topk_hits += 1

        # Per-sound_type breakdown
        sound_type_counters[query.sound_type][1] += 1
        if qr.topk_same_sound_type:
            sound_type_counters[query.sound_type][0] += 1

        rep.queries.append(qr)

    rep.per_sound_type_hits = {
        st: (hits, total) for st, (hits, total) in sound_type_counters.items()
    }
    return rep


# --- Cross-engine evaluation --------------------------------------------------

def cross_engine_eval(union: PresetCatalog) -> CrossEval:
    """V2-H2: LOO retrieval against the full union catalog."""
    rep = CrossEval(n=len(union.presets))
    per_engine_sums: Dict[str, List[float]] = defaultdict(list)
    per_engine_cross_hits: Dict[str, List[int]] = defaultdict(list)

    for query in union.presets:
        results = union.find_similar(query, k=TOP_K)
        if not results:
            continue
        same_engine_in_topk = sum(
            1 for p, _ in results if p.instrument == query.instrument
        )
        cross_engine_st_in_topk = sum(
            1 for p, _ in results
            if p.instrument != query.instrument and p.sound_type == query.sound_type
        )
        qr = QueryResult(
            preset_id=query.preset_id,
            instrument=query.instrument,
            sound_type=query.sound_type,
            top_neighbors=[
                (p.preset_id, p.instrument, p.sound_type, dist)
                for p, dist in results
            ],
            same_engine_in_topk=same_engine_in_topk,
            cross_engine_same_sound_type_in_topk=cross_engine_st_in_topk,
        )
        # Same-engine bias: fraction of top-k from the same engine.
        bias = same_engine_in_topk / len(results)
        rep.same_engine_bias_sum += bias
        per_engine_sums[query.instrument].append(bias)

        if cross_engine_st_in_topk > 0:
            rep.cross_engine_topk_hits += 1
            per_engine_cross_hits[query.instrument].append(1)
        else:
            per_engine_cross_hits[query.instrument].append(0)

        rep.queries.append(qr)

    # Build per-engine breakdown
    for eng, biases in per_engine_sums.items():
        sub = CrossEval(n=len(biases))
        sub.same_engine_bias_sum = sum(biases)
        sub.cross_engine_topk_hits = sum(per_engine_cross_hits[eng])
        rep.per_engine[eng] = sub

    return rep


# --- Report rendering ---------------------------------------------------------

def render_json(
    within: Dict[str, EngineEval],
    cross: CrossEval,
    output_path: Path,
) -> None:
    payload: Dict = {
        "config": {
            "top_k": TOP_K,
            "v2_h1_topk_hit_rate_min": V2_H1_TOPK_HIT_RATE_MIN,
            "v2_h2_same_engine_bias_max": V2_H2_SAME_ENGINE_BIAS_MAX,
            "v2_h2_cross_engine_topk_min": V2_H2_CROSS_ENGINE_TOPK_MIN,
        },
        "v2_h1_within_engine": {},
        "v2_h2_cross_engine": {
            "n": cross.n,
            "mean_same_engine_bias": (
                cross.same_engine_bias_sum / cross.n if cross.n else 0.0
            ),
            "cross_engine_topk_hit_rate": (
                cross.cross_engine_topk_hits / cross.n if cross.n else 0.0
            ),
            "per_engine": {},
        },
    }

    for engine, rep in within.items():
        payload["v2_h1_within_engine"][engine] = {
            "n": rep.n,
            "top1_hit_rate": rep.top1_rate,
            "topk_hit_rate": rep.topk_rate,
            "passes_v2_h1": rep.topk_rate >= V2_H1_TOPK_HIT_RATE_MIN,
            "per_sound_type": {
                st: {
                    "n": total,
                    "topk_hits": hits,
                    "topk_hit_rate": hits / total if total else 0.0,
                }
                for st, (hits, total) in rep.per_sound_type_hits.items()
            },
            "queries": [
                {
                    "preset_id": q.preset_id,
                    "sound_type": q.sound_type,
                    "top1_same_sound_type": q.top1_same_sound_type,
                    "topk_same_sound_type": q.topk_same_sound_type,
                    "top_neighbors": [
                        {
                            "preset_id": pid,
                            "instrument": eng,
                            "sound_type": st,
                            "distance": dist,
                        }
                        for pid, eng, st, dist in q.top_neighbors
                    ],
                }
                for q in rep.queries
            ],
        }

    for engine, sub in cross.per_engine.items():
        payload["v2_h2_cross_engine"]["per_engine"][engine] = {
            "n": sub.n,
            "mean_same_engine_bias": (
                sub.same_engine_bias_sum / sub.n if sub.n else 0.0
            ),
            "cross_engine_topk_hit_rate": (
                sub.cross_engine_topk_hits / sub.n if sub.n else 0.0
            ),
        }

    payload["v2_h2_cross_engine"]["passes_v2_h2"] = (
        payload["v2_h2_cross_engine"]["mean_same_engine_bias"] <= V2_H2_SAME_ENGINE_BIAS_MAX
        and payload["v2_h2_cross_engine"]["cross_engine_topk_hit_rate"] >= V2_H2_CROSS_ENGINE_TOPK_MIN
    )

    output_path.write_text(json.dumps(payload, indent=2))
    logger.info("Wrote JSON report: %s", output_path)


def render_markdown(
    within: Dict[str, EngineEval],
    cross: CrossEval,
    output_path: Path,
) -> None:
    lines: List[str] = []
    lines.append("# V2 Retrieval Evaluation\n")
    lines.append(
        f"Top-K = **{TOP_K}**. Acceptance: V2-H1 top-{TOP_K} hit rate "
        f">= {V2_H1_TOPK_HIT_RATE_MIN:.0%}; V2-H2 same-engine bias "
        f"<= {V2_H2_SAME_ENGINE_BIAS_MAX:.0%} AND cross-engine top-{TOP_K} "
        f"hit rate >= {V2_H2_CROSS_ENGINE_TOPK_MIN:.0%}.\n"
    )

    lines.append("## V2-H1 Within-engine LOO\n")
    lines.append("| Engine | N | Top-1 hit | Top-K hit | Pass V2-H1 |")
    lines.append("|---|---:|---:|---:|:---:|")
    for engine, rep in within.items():
        passes = "PASS" if rep.topk_rate >= V2_H1_TOPK_HIT_RATE_MIN else "FAIL"
        lines.append(
            f"| {engine} | {rep.n} | {rep.top1_rate:.1%} | "
            f"{rep.topk_rate:.1%} | {passes} |"
        )

    lines.append("\n### Sound-type breakdown (top-K hit rate)\n")
    all_types = sorted({
        st for rep in within.values() for st in rep.per_sound_type_hits
    })
    lines.append("| Engine | " + " | ".join(all_types) + " |")
    lines.append("|---|" + "---:|" * len(all_types))
    for engine, rep in within.items():
        cells = [engine]
        for st in all_types:
            hits, total = rep.per_sound_type_hits.get(st, (0, 0))
            if total == 0:
                cells.append("-")
            else:
                cells.append(f"{hits}/{total} ({hits/total:.0%})")
        lines.append("| " + " | ".join(cells) + " |")

    lines.append("\n## V2-H2 Cross-engine LOO\n")
    mean_bias = cross.same_engine_bias_sum / cross.n if cross.n else 0.0
    cross_hit = cross.cross_engine_topk_hits / cross.n if cross.n else 0.0
    overall_pass = (
        mean_bias <= V2_H2_SAME_ENGINE_BIAS_MAX
        and cross_hit >= V2_H2_CROSS_ENGINE_TOPK_MIN
    )
    lines.append(f"- N queries: **{cross.n}**")
    lines.append(f"- Mean same-engine bias (top-{TOP_K}): **{mean_bias:.1%}**")
    lines.append(f"- Cross-engine top-{TOP_K} hit rate: **{cross_hit:.1%}**")
    lines.append(f"- Overall V2-H2: **{'PASS' if overall_pass else 'FAIL'}**\n")

    lines.append("### Per-engine breakdown\n")
    lines.append("| Engine | N | Same-eng bias | Cross-eng top-K hit |")
    lines.append("|---|---:|---:|---:|")
    for engine, sub in cross.per_engine.items():
        sub_bias = sub.same_engine_bias_sum / sub.n if sub.n else 0.0
        sub_hit = sub.cross_engine_topk_hits / sub.n if sub.n else 0.0
        lines.append(
            f"| {engine} | {sub.n} | {sub_bias:.1%} | {sub_hit:.1%} |"
        )

    output_path.write_text("\n".join(lines))
    logger.info("Wrote markdown report: %s", output_path)


# --- Main ---------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--instruments",
        nargs="+",
        default=["Analog", "Drift", "Collision", "Electric"],
    )
    p.add_argument(
        "--catalog-dir",
        type=Path,
        default=Path("preset_catalog_output/catalog"),
    )
    p.add_argument(
        "--report-dir",
        type=Path,
        default=Path("preset_catalog_output/catalog"),
    )
    args = p.parse_args()
    args.report_dir.mkdir(parents=True, exist_ok=True)

    # Load per-engine catalogs.
    within: Dict[str, EngineEval] = {}
    union = PresetCatalog()
    for engine in args.instruments:
        cat_path = args.catalog_dir / f"catalog_{engine.lower()}_v2.json"
        if not cat_path.exists():
            logger.error("Missing catalog: %s", cat_path)
            continue
        cat = PresetCatalog.load(cat_path)
        logger.info("Loaded %s catalog (%d presets)", engine, len(cat.presets))
        within[engine] = within_engine_eval(engine, cat)
        for fp in cat.presets:
            union.add(fp)

    # V2-H2 cross-engine
    cross = cross_engine_eval(union)

    # Reports
    render_json(within, cross, args.report_dir / "v2_retrieval_eval.json")
    render_markdown(within, cross, args.report_dir / "v2_retrieval_eval.md")

    # Console summary
    print("\n=== V2-H1 Within-engine LOO ===")
    print(f"{'engine':10s} {'n':>5s} {'top-1':>7s} {'top-5':>7s}  pass?")
    for engine, rep in within.items():
        passes = "PASS" if rep.topk_rate >= V2_H1_TOPK_HIT_RATE_MIN else "FAIL"
        print(
            f"{engine:10s} {rep.n:5d} {rep.top1_rate:7.1%} "
            f"{rep.topk_rate:7.1%}  {passes}"
        )

    print("\n=== V2-H2 Cross-engine LOO ===")
    mean_bias = cross.same_engine_bias_sum / cross.n if cross.n else 0.0
    cross_hit = cross.cross_engine_topk_hits / cross.n if cross.n else 0.0
    overall_pass = (
        mean_bias <= V2_H2_SAME_ENGINE_BIAS_MAX
        and cross_hit >= V2_H2_CROSS_ENGINE_TOPK_MIN
    )
    print(f"N = {cross.n}")
    print(f"mean same-engine bias: {mean_bias:.1%}  (max {V2_H2_SAME_ENGINE_BIAS_MAX:.0%})")
    print(f"cross-engine top-{TOP_K} hit rate: {cross_hit:.1%}  (min {V2_H2_CROSS_ENGINE_TOPK_MIN:.0%})")
    print(f"V2-H2: {'PASS' if overall_pass else 'FAIL'}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
