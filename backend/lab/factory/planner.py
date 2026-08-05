"""Coverage Planner — the Data Factory's decision layer.

A pure-analysis layer ON TOP of the existing coverage report + catalog (no new
pipeline infra, reads metadata only — no audio). It compares the pool against
declared coverage TARGETS (the regimes a complete guitar corpus must cover, each
weighted by separation impact), detects gaps, and answers the only question that
matters now: *what acquisition gives Riley the biggest improvement per dollar?*

Optimize COVERAGE, not volume. Everything is extensible: add a dimension by adding
an extractor + a target; add a regime by extending TARGETS. Nothing is hardcoded as
an assumption beyond the declared, editable targets.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Optional

from .asset import Asset, Kind, Status
from .catalog import AssetCatalog
from . import coverage as _cov

_UNK = "unknown"


# --------------------------------------------------------------------------
# Extended dimension extractors (reuse coverage.DIMENSIONS; add the planner's)
# --------------------------------------------------------------------------
def _md(key, default=_UNK):
    return lambda a: a.metadata.get(key, default)


def _quality_bucket(a: Asset) -> str:
    q = a.quality_score
    if q is None:
        return _UNK
    return "high" if q >= 0.8 else "med" if q >= 0.5 else "low"


PLANNER_DIMENSIONS: dict[str, Callable[[Asset], str]] = {
    **_cov.DIMENSIONS,
    "amp_family": lambda a: a.metadata.get("effect") or a.metadata.get("amp_voicing", _UNK),
    "recording_method": _md("recording_type"),
    "tuning": _md("tuning"),               # not yet tagged -> surfaced as a blind spot
    "string_count": _md("string_count"),
    "capo": _md("capo"),
    "playing_style": lambda a: a.metadata.get("performance_style") or a.metadata.get("genre", _UNK),
    "articulation": _md("performance_style"),
    "player_identity": _md("player"),
    "recording_quality": _quality_bucket,
    "provenance_confidence": lambda a: ("estimated" if (a.audit or {}).get("estimated_fields") else "measured"),
}


# --------------------------------------------------------------------------
# TARGETS — the regimes a complete corpus must cover, weighted by SEPARATION
# IMPACT (higher = harder/more-valuable regime to own). Editable; not exhaustive.
# --------------------------------------------------------------------------
TARGETS: dict[str, dict[str, float]] = {
    "acoustic_vs_electric": {"acoustic": 1.0, "electric": 1.5},
    "guitar_type": {"acoustic": 1.0, "clean": 1.0, "distorted": 1.6},
    "pickup": {"neck": 1.0, "middle": 1.0, "bridge": 1.2},
    "gain": {"low": 1.0, "med": 1.6, "high": 1.4},           # med/crunch is the empty middle
    "tuning": {"standard": 1.0, "drop_d": 1.6, "drop_c": 2.2, "dadgad": 1.4,
               "open_g": 1.4, "baritone": 2.4, "7-string": 2.4, "8-string": 2.2},
    "string_count": {"6": 1.0, "7": 2.2, "8": 2.2, "12": 1.6},
    "amp_family": {"clean": 1.0, "fender_clean": 1.0, "british_crunch": 1.4,
                   "high_gain": 1.6, "blues driver": 1.2, "tube screamer": 1.2, "distortion": 1.4},
    "recording_method": {"di": 1.8, "amp_mic": 1.2, "di_processed": 1.2, "acoustic": 1.0},
    "playing_style": {"rock": 1.0, "jazz": 1.2, "funk": 1.4, "bossa_nova": 1.2,
                      "singer_songwriter": 1.0, "metal": 1.8, "blues": 1.2, "ambient": 1.4},
    "articulation": {"PalmMute": 1.6, "Bendings": 1.2, "Harmonics": 1.4, "PinchHarmonics": 1.8,
                     "Vibrato": 1.2, "Slides": 1.2, "Tapping": 1.6, "fingerstyle": 1.4},
    "player_identity": {},   # diversity target handled specially (want MANY players)
    "masking_level": {"none": 0.5, "low": 1.2, "med": 1.6, "high": 2.0},
}

# how to acquire each dimension's gaps (strategy + cost/effort bands + concrete source)
_STRATEGY = {
    "tuning":        ("commission", "high", "high",  "no dataset has diverse tunings — record DI to spec"),
    "string_count":  ("commission", "high", "high",  "7/8-string absent everywhere — commission"),
    "gain":          ("commission", "med",  "med",   "green tier is bimodal (clean vs high); record edge/crunch DI"),
    "amp_family":    ("green",       "low",  "low",   "mine remaining EGFxSet effects / more recipes (free, on Hetzner)"),
    "articulation":  ("green",       "low",  "med",   "more of Guitar-TECHS techniques (free) + commission gaps"),
    "playing_style": ("license",     "med",  "med",   "MoisesDB / SourceAudio genres; or commission metal"),
    "player_identity":("commission", "med",  "high",  "diversity: many players via AirGigs/SoundBetter DI"),
    "masking_level": ("virtual_studio", "low", "low", "Virtual Studio scenarios (needs REAL backing pool)"),
    "recording_method":("green",     "low",  "low",   "Guitar-TECHS DI + more green"),
}
_COST_USD = {"low": 500, "med": 3000, "high": 12000}   # order-of-magnitude bands for ROI math


@dataclass
class DimensionScore:
    dimension: str
    n: int
    coverage_pct: float          # fraction of target regimes with >= floor assets
    asset_count: int
    quality_weighted: float      # sum(quality_score) over the dimension's assets
    diversity: int               # distinct non-unknown values present
    confidence: float            # 0..1 (share of assets characterized, not "unknown")


@dataclass
class Gap:
    dimension: str
    value: str
    impact: float                # separation-impact weight of the regime
    have: int                    # quality-weighted assets present
    deficit: float               # 0..1 how far below target
    strategy: str
    cost_band: str
    effort_band: str
    source_note: str
    priority: float = 0.0        # impact * deficit * benchmark_boost
    roi: float = 0.0             # (coverage_gain * impact) / cost
    benchmark_boost: float = 1.0

    def brief(self, n: int = 20) -> str:
        parts = [f"{self.value}"]
        return (f"Need ~{n} performances: {self.dimension}={self.value}"
                + (" (DI, bridge humbucker, 90-140 BPM)" if self.dimension in ("tuning", "string_count") else "")
                + f".  [{self.strategy}, cost~{self.cost_band}, impact~{self.impact:.1f}]")


@dataclass
class CoveragePlan:
    n_assets: int
    dimensions: list                 # [DimensionScore]
    gaps: list                       # [Gap] ranked by priority
    def top(self, k=5):
        return self.gaps[:k]


# --------------------------------------------------------------------------
def _pool(catalog: AssetCatalog):
    return [a for a in catalog.all() if a.kind in (Kind.STEM, Kind.DI, Kind.MIXTURE)]


def score_dimension(assets: list, dim: str, floor: int = 5) -> DimensionScore:
    fn = PLANNER_DIMENSIONS[dim]
    vals = [fn(a) for a in assets]
    hist = Counter(vals)
    target = TARGETS.get(dim, {})
    covered = sum(1 for v in target if hist.get(v, 0) >= floor)
    cov_pct = (covered / len(target)) if target else float(hist and 1.0)
    characterized = sum(c for v, c in hist.items() if v != _UNK)
    qw = sum(a.quality_score or 0.0 for a in assets if fn(a) != _UNK)
    diversity = len([v for v in hist if v != _UNK])
    return DimensionScore(dim, len(assets), round(cov_pct, 3),
                          characterized, round(qw, 1), diversity,
                          round(characterized / max(1, len(assets)), 3))


def detect_gaps(assets: list, *, floor: int = 5,
                benchmark_failures: Optional[dict] = None) -> list:
    """A gap = a TARGET regime under-covered (quality-weighted). Ranked by priority."""
    bench = benchmark_failures or {}
    gaps = []
    for dim, regimes in TARGETS.items():
        if not regimes:
            continue
        fn = PLANNER_DIMENSIONS[dim]
        # quality-weighted counts per value
        qcount: dict = {}
        for a in assets:
            v = fn(a)
            qcount[v] = qcount.get(v, 0.0) + (a.quality_score or 0.5)
        for value, impact in regimes.items():
            have = qcount.get(value, 0.0)
            if have >= floor:
                continue
            deficit = 1.0 - min(1.0, have / floor)
            strat, cost, effort, note = _STRATEGY.get(
                dim, ("commission", "med", "med", "record/license to spec"))
            boost = 1.0 + float(bench.get(dim, {}).get(value, 0.0)) * 2.0 if isinstance(bench.get(dim), dict) \
                else (1.0 + float(bench.get(f"{dim}:{value}", 0.0)) * 2.0)
            g = Gap(dim, value, impact, int(have), round(deficit, 2), strat, cost, effort, note,
                    benchmark_boost=round(boost, 2))
            g.priority = round(impact * deficit * boost, 3)
            gain = deficit                                  # coverage gain if filled
            g.roi = round((gain * impact * boost) / (_COST_USD[cost] / 1000.0), 3)
            gaps.append(g)
    gaps.sort(key=lambda g: (-g.priority, -g.roi))
    return gaps


def plan(catalog: AssetCatalog, *, floor: int = 5,
         benchmark_failures: Optional[dict] = None) -> CoveragePlan:
    assets = _pool(catalog)
    dims = [score_dimension(assets, d, floor) for d in PLANNER_DIMENSIONS]
    gaps = detect_gaps(assets, floor=floor, benchmark_failures=benchmark_failures)
    return CoveragePlan(len(assets), dims, gaps)


# ==========================================================================
# Reports (the 9 planner outputs) — markdown/text over the plan.
# ==========================================================================
def _bar(frac: float, w: int = 12) -> str:
    n = int(round(frac * w))
    return "█" * n + "." * (w - n)


def render_dashboard(plan: CoveragePlan) -> str:
    L = [f"# Coverage Dashboard  (pool = {plan.n_assets} assets)", ""]
    L.append(f"{'dimension':22} {'coverage':10} {'assets':7} {'diversity':9} {'confidence'}")
    for d in plan.dimensions:
        L.append(f"{d.dimension:22} {_bar(d.coverage_pct)} {d.asset_count:5}   {d.diversity:5}     {d.confidence:.2f}")
    return "\n".join(L)


def render_heatmap(plan: CoveragePlan, catalog: AssetCatalog, floor: int = 5) -> str:
    """Target-regime coverage heatmap per dimension (actual vs target)."""
    assets = _pool(catalog)
    L = ["# Coverage Heatmap (target regimes; . = empty)", ""]
    for dim, regimes in TARGETS.items():
        if not regimes:
            continue
        fn = PLANNER_DIMENSIONS[dim]
        hist = Counter(fn(a) for a in assets)
        mx = max([hist.get(v, 0) for v in regimes] + [1])
        L.append(f"## {dim}")
        for v, impact in sorted(regimes.items(), key=lambda kv: -kv[1]):
            c = hist.get(v, 0)
            flag = "  GAP" if c < floor else ""
            L.append(f"  {v:14} {_bar(c / mx, 10)} {c:4}  (impact {impact}){flag}")
        L.append("")
    return "\n".join(L)


def render_priority(plan: CoveragePlan, k: int = 12) -> str:
    L = ["# Acquisition Priority List (ranked)", "",
         f"{'#':2} {'gap':34} {'strat':13} {'cost':5} {'impact':6} {'roi':6} {'priority'}"]
    for i, g in enumerate(plan.gaps[:k], 1):
        L.append(f"{i:2} {g.dimension+'='+g.value:34} {g.strategy:13} {g.cost_band:5} "
                 f"{g.impact:6.1f} {g.roi:6.2f} {g.priority:.2f}"
                 + ("  [BENCH+]" if g.benchmark_boost > 1.0 else ""))
    return "\n".join(L)


def render_gaps(plan: CoveragePlan) -> str:
    L = ["# Gap Report", ""]
    for g in plan.gaps:
        L.append(f"- {g.dimension}={g.value}: have {g.have} (deficit {g.deficit:.0%}), "
                 f"impact {g.impact:.1f} -> {g.strategy}. {g.source_note}")
    return "\n".join(L)


def render_briefs(plan: CoveragePlan, k: int = 6) -> str:
    L = ["# Commissioning Briefs (auto-generated from gaps)", ""]
    commissionable = [g for g in plan.gaps if g.strategy == "commission"][:k]
    for g in commissionable:
        L.append("> " + g.brief())
    if not commissionable:
        L.append("(no commission-strategy gaps in the top set)")
    return "\n".join(L)


def render_licensing(plan: CoveragePlan) -> str:
    L = ["# Licensing Opportunities", ""]
    lic = [g for g in plan.gaps if g.strategy in ("license", "green")]
    for g in lic:
        L.append(f"- {g.dimension}={g.value}: {g.strategy} — {g.source_note} "
                 f"(cost~{g.cost_band}, impact {g.impact:.1f})")
    if not lic:
        L.append("(no license/green-solvable gaps in the top set)")
    return "\n".join(L)


def render_roi(plan: CoveragePlan, k: int = 10) -> str:
    L = ["# ROI Report (coverage-gain x impact / cost)", ""]
    for g in sorted(plan.gaps, key=lambda g: -g.roi)[:k]:
        L.append(f"  ROI {g.roi:6.2f}  {g.dimension}={g.value:14} "
                 f"(impact {g.impact:.1f}, cost {g.cost_band}, {g.strategy})")
    return "\n".join(L)


def render_trend(plan: CoveragePlan, prev: Optional[dict]) -> str:
    """Historical coverage trend vs a prior coverage.json (dimension histograms)."""
    L = ["# Historical Coverage Trend", ""]
    if not prev:
        L.append("(no prior snapshot — this is the baseline)")
        return "\n".join(L)
    prev_dims = prev.get("dimensions", {})
    for d in plan.dimensions:
        pv = prev_dims.get(d.dimension, {})
        prev_div = len([v for v in pv if v != _UNK])
        delta = d.diversity - prev_div
        if delta:
            L.append(f"  {d.dimension:22} diversity {prev_div} -> {d.diversity}  ({'+' if delta>0 else ''}{delta})")
    return "\n".join(L)


_VERB = {"commission": "COMMISSION", "license": "LICENSE", "green": "INGEST (free green)",
         "virtual_studio": "GENERATE (Virtual Studio)"}


def render_next_actions(plan: CoveragePlan, k: int = 5) -> str:
    """Budget-allocator logic: exhaust FREE/cheap coverage first (best $-ROI), THEN
    spend on the high-impact strategic gaps only acquisition can fill."""
    free = [g for g in plan.gaps if g.strategy in ("green", "virtual_studio")]
    free.sort(key=lambda g: -g.roi)
    spend = [g for g in plan.gaps if g.strategy in ("commission", "license")]
    spend.sort(key=lambda g: -g.priority)
    L = ["# Recommended Next Actions", "",
         f"## Do first — free / cheap wins (highest $-ROI, exhaust before spending)"]
    for i, g in enumerate(free[:k], 1):
        L.append(f"{i}. {_VERB[g.strategy]}: {g.dimension}={g.value} "
                 f"— ROI {g.roi:.2f}, impact {g.impact:.1f}. {g.source_note}")
    L.append("")
    L.append(f"## Then invest — highest-impact gaps only $ can fill (rank by strategic priority)")
    for i, g in enumerate(spend[:k], 1):
        L.append(f"{i}. {_VERB[g.strategy]}: {g.dimension}={g.value} "
                 f"— impact {g.impact:.1f}, priority {g.priority:.2f}, cost~{g.cost_band}. {g.source_note}")
    return "\n".join(L)


def full_report(plan: CoveragePlan, catalog: AssetCatalog, prev: Optional[dict] = None) -> str:
    return "\n\n".join([
        render_dashboard(plan), render_heatmap(plan, catalog), render_gaps(plan),
        render_priority(plan), render_roi(plan), render_briefs(plan),
        render_licensing(plan), render_trend(plan, prev), render_next_actions(plan),
    ])
