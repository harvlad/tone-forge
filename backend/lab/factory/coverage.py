"""Coverage Analysis — measure the Stem Pool's coverage of MUSICAL SPACE, not song
count. Read-only over the catalog; highlights sparse regions so the data strategy
becomes self-directing (coverage gap -> commissioning brief).

Dimensions (Phase 3): genre, gain, guitar_type, pickup, tempo, key, masking_level,
recording_style, acoustic_vs_electric, difficulty, synthetic_vs_real, license_class.

Synth dimensions read the same vocabulary ``SynthDescriptor`` produces
(synth_analyzer), so a manufactured synth asset can be stamped straight from a
real analysis rather than from hand-typed tags.
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
    # --- synth coverage -------------------------------------------------
    # Five dimensions, chosen because each one predicts a DIFFERENT failure
    # mode for a synth separator, not because they describe a patch well:
    #
    #   synth_role     where it sits in the arrangement. A lead competes
    #                  with vocals, a pad with guitar; a separator good at
    #                  one is routinely bad at the other.
    #   oscillator     spectral identity. A saw is harmonically dense like
    #                  a distorted guitar; a sine is nearly a pure tone
    #                  hiding under a bass. Opposite confusions.
    #   brightness     how far up the spectrum it lives, i.e. what it
    #                  collides with — cymbals up top, bass down low.
    #   movement       filter sweeps and LFO. Non-stationary timbre is the
    #                  hard case: a separator trained on static pads
    #                  smears anything that moves.
    #   stereo_width   wide detuned pads defeat pan-based cues, which is
    #                  exactly what the existing multi-guitar split leans
    #                  on. A corpus of mono synths would hide that.
    "synth_role": lambda a: a.metadata.get("synth_role", _UNK),
    "oscillator": lambda a: a.metadata.get("oscillator", _UNK),
    "brightness": lambda a: _bucket01(a.metadata.get("brightness")),
    "movement": lambda a: _bucket01(a.metadata.get("movement")),
    "stereo_width": lambda a: _bucket01(a.metadata.get("stereo_width"),
                                        lo="mono", mid="narrow", hi="wide"),
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
