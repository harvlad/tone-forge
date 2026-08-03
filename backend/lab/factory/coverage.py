"""Coverage Analysis — measure the Stem Pool's coverage of MUSICAL SPACE, not song
count. Read-only over the catalog; highlights sparse regions so the data strategy
becomes self-directing (coverage gap -> commissioning brief).

Dimensions (Phase 3): genre, gain, guitar_type, pickup, tempo, key, masking_level,
recording_style, acoustic_vs_electric, difficulty, synthetic_vs_real, license_class.
"""
from __future__ import annotations

from collections import Counter
from typing import Callable, Optional

from .asset import Asset, Kind
from .catalog import AssetCatalog

_UNK = "unknown"


def _bucket_tempo(v) -> str:
    try:
        t = float(v)
    except (TypeError, ValueError):
        return _UNK
    return "<90" if t < 90 else "90-120" if t < 120 else "120-150" if t < 150 else ">=150"


def _bucket01(v, lo="low", mid="med", hi="high") -> str:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return _UNK
    return lo if x < 0.33 else mid if x < 0.66 else hi


def _acoustic_electric(a: Asset) -> str:
    gt = a.metadata.get("guitar_type", _UNK)
    if gt == "acoustic":
        return "acoustic"
    if gt in ("clean", "distorted", "electric", "electric_clean"):
        return "electric"
    return _UNK


def _license_class(a: Asset) -> str:
    if a.license is None:
        return "unverified"
    return "commercial" if a.license.commercial_training_allowed else "research_only"


# dim name -> value extractor
DIMENSIONS: dict[str, Callable[[Asset], str]] = {
    "genre": lambda a: a.metadata.get("genre", _UNK),
    "gain": lambda a: _bucket01(a.metadata.get("gain")),
    "guitar_type": lambda a: a.metadata.get("guitar_type", _UNK),
    "pickup": lambda a: a.metadata.get("pickup", _UNK),
    "tempo": lambda a: _bucket_tempo(a.metadata.get("tempo")),
    "key": lambda a: a.metadata.get("key", _UNK),
    "masking_level": lambda a: _bucket01(a.metadata.get("masking_score")) if a.kind == Kind.MIXTURE else "none",
    "recording_style": lambda a: a.metadata.get("recording_type", _UNK),
    "acoustic_vs_electric": _acoustic_electric,
    "difficulty": lambda a: _bucket01(a.metadata.get("difficulty")),
    "synthetic_vs_real": lambda a: a.metadata.get("synthetic_real", _UNK),
    "license_class": _license_class,
}


def coverage_report(catalog: AssetCatalog, *, sparse_threshold: int = 5,
                    kinds: Optional[set] = None) -> dict:
    """Histogram every dimension over pool assets; flag sparse cells."""
    kinds = kinds or {Kind.STEM, Kind.DI, Kind.MIXTURE}
    assets = [a for a in catalog.all() if a.kind in kinds]
    dims = {name: dict(Counter(fn(a) for a in assets)) for name, fn in DIMENSIONS.items()}
    sparse = []
    for name, hist in dims.items():
        for value, count in hist.items():
            if value != _UNK and count < sparse_threshold:
                sparse.append({"dimension": name, "value": value, "count": count})
    sparse.sort(key=lambda s: s["count"])
    return {"n_assets": len(assets), "dimensions": dims,
            "sparse": sparse, "sparse_threshold": sparse_threshold}


def render_report(report: dict, bar_width: int = 24) -> str:
    lines = [f"# Stem Pool Coverage  (n={report['n_assets']} assets, "
             f"sparse < {report['sparse_threshold']})", ""]
    for name, hist in report["dimensions"].items():
        if not hist:
            continue
        lines.append(f"## {name}")
        mx = max(hist.values()) or 1
        for value, count in sorted(hist.items(), key=lambda kv: -kv[1]):
            bar = "█" * max(1, int(bar_width * count / mx))
            flag = "  <- SPARSE" if value != _UNK and count < report["sparse_threshold"] else ""
            lines.append(f"  {str(value):18} {bar} {count}{flag}")
        lines.append("")
    if report["sparse"]:
        lines.append("## GAPS (drive commissioning)")
        for s in report["sparse"][:20]:
            lines.append(f"  {s['dimension']}={s['value']} (only {s['count']})")
    return "\n".join(lines)
