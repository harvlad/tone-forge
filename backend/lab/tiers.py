"""Deterministic dataset tiers + the frozen validated-100 eval set.

Selection is reproducible from (tier, class, seed): filter manifest,
sort by stem_id, seeded shuffle, take N.  Every selection is persisted
to lab_data/tiers/ as a frozen record of exactly which stems a result
was derived from.

Split policy:
    validation -> development (tiers sanity..full)
    test       -> FROZEN HELD-OUT ("heldout" tier only; never tune on it)
"""
from __future__ import annotations

import json
import random
from typing import Optional

import pandas as pd

from . import config, corpus

DEV_SPLIT = "validation"
HELDOUT_SPLIT = "test"


def validated100_stems(manifest: pd.DataFrame) -> pd.DataFrame:
    """The EXACT 100-stem set of the validated BP/MT3 benchmark:
    first 100 stems of the sorted validation split (slakh2100), any
    GM class except Unknown, in track order."""
    df = manifest[(manifest["dataset"] == "slakh2100")
                  & (manifest["split"] == "validation")
                  & (manifest["inst_class_gm"] != "Unknown")]
    df = df.sort_values(["track_id", "stem_name"]).head(config.VALIDATED_REFERENCE["n_stems"])
    return df.reset_index(drop=True)


def select_tier(manifest: pd.DataFrame, tier: str, inst_class: Optional[str] = None,
                dataset: str = "slakh2100", seed: int = config.DEFAULT_TIER_SEED,
                persist: bool = True) -> pd.DataFrame:
    """Deterministic stem selection for a tier."""
    if tier == "validated100":
        return validated100_stems(manifest)
    if tier not in config.TIER_ORDER:
        raise ValueError(f"Unknown tier '{tier}'. Known: {config.TIER_ORDER} + validated100")
    split = HELDOUT_SPLIT if tier == "heldout" else DEV_SPLIT
    df = corpus.select_stems(manifest, dataset=dataset, split=split,
                             inst_class=inst_class)
    n = config.TIER_SIZES.get(tier)
    if tier == "heldout":
        n = None
    if n is not None and len(df) > n:
        ids = sorted(df["stem_id"])
        rng = random.Random(f"{seed}:{dataset}:{split}:{inst_class or 'all'}:{tier}")
        rng.shuffle(ids)
        chosen = set(ids[:n])
        df = df[df["stem_id"].isin(chosen)].sort_values("stem_id").reset_index(drop=True)
    if persist:
        _persist_selection(tier, inst_class, dataset, seed, df)
    return df


def _persist_selection(tier, inst_class, dataset, seed, df) -> None:
    config.TIERS_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{dataset}_{tier}_{(inst_class or 'all').replace(' ', '')}_seed{seed}.json"
    path = config.TIERS_DIR / name
    record = {
        "tier": tier, "inst_class": inst_class, "dataset": dataset, "seed": seed,
        "n_stems": len(df), "stem_ids": sorted(df["stem_id"].tolist()),
    }
    existing = None
    if path.exists():
        try:
            existing = json.loads(path.read_text())
        except Exception:
            existing = None
    if existing and existing.get("stem_ids") != record["stem_ids"]:
        # Corpus changed under a frozen selection — keep old record, flag loudly.
        path.with_suffix(".CHANGED.json").write_text(json.dumps(record, indent=1))
        raise RuntimeError(
            f"Tier selection {name} changed vs frozen record (corpus drift?). "
            "Inspect lab_data/tiers/ before proceeding.")
    if not existing:
        path.write_text(json.dumps(record, indent=1))
