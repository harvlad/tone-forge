"""Representation experiments v2: includes CLAP / OpenL3 learned embeddings.

Loads cached matrices:
    preset_catalog_output/retrieval/representations.npz
        current_embedding, interpretable, spectral_encoder
    preset_catalog_output/retrieval/learned_embeddings.npz
        embeddings (CLAP or OpenL3), preset_ids, encoder_name

Adds the learned-embedding variants (raw, z-score, PCA, PCA-whiten) to the
same comparison table used by ``representation_experiments.py`` and writes
``representation_experiments_v2.{json,md}`` alongside the existing reports.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np

BACKEND_DIR = Path(__file__).resolve().parents[1]
SCRIPT_DIR = BACKEND_DIR / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from representation_experiments import (  # noqa: E402
    evaluate,
    l2_normalize,
    zscore,
    pca_reduce,
    select_active_dims,
    select_high_variance,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("repr_v2")

CATALOG_PATH = (
    BACKEND_DIR / "preset_catalog_output" / "catalog" / "catalog_analog.json"
)
OUT_DIR = BACKEND_DIR / "preset_catalog_output" / "retrieval"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--catalog", type=Path, default=CATALOG_PATH)
    ap.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = ap.parse_args()

    # Load catalog (for category / sound_type)
    presets = json.loads(args.catalog.read_text())["presets"]
    by_id = {p["preset_id"]: p for p in presets}

    # Hand-crafted / spectral representations
    reps = np.load(args.out_dir / "representations.npz", allow_pickle=False)
    cur = reps["current_embedding"]
    intr = reps["interpretable"]
    spec = reps["spectral_encoder"]
    base_ids = [str(s) for s in reps["preset_ids"]]

    # Learned embeddings
    learned = np.load(args.out_dir / "learned_embeddings.npz", allow_pickle=False)
    learn = learned["embeddings"]
    learn_ids = [str(s) for s in learned["preset_ids"]]
    encoder_name = str(learned["encoder_name"])
    log.info("learned encoder: %s, dim=%d", encoder_name, learn.shape[1])

    # Align learned to the same row order as the cached representations
    pos = {pid: i for i, pid in enumerate(learn_ids)}
    keep_rows = [i for i, pid in enumerate(base_ids) if pid in pos]
    if len(keep_rows) != len(base_ids):
        log.warning(
            "id mismatch: base=%d learned=%d overlap=%d",
            len(base_ids), len(learn_ids), len(keep_rows),
        )
    cur = cur[keep_rows]
    intr = intr[keep_rows]
    spec = spec[keep_rows]
    base_ids = [base_ids[i] for i in keep_rows]
    learn = np.stack([learn[pos[pid]] for pid in base_ids], axis=0)

    categories = [by_id[pid].get("category", "Unknown") for pid in base_ids]
    sound_types = [by_id[pid].get("sound_type", "unknown") for pid in base_ids]

    variants: Dict[str, Tuple[np.ndarray, str]] = {}

    # Anchors from v1 (re-evaluated here for direct comparison)
    variants["R1_current_embedding (DSP 128-d)"] = (cur, "cosine")
    variants["R2_interpretable_only (24-d scalars)"] = (intr, "cosine")
    variants["R6_zscore_then_l2 (current)"] = (l2_normalize(zscore(cur)), "cosine")
    variants["R8_spectral_encoder_128 (DSP)"] = (spec, "cosine")

    # Learned variants
    variants[f"R11_{encoder_name}_raw (512-d)"] = (learn, "cosine")
    variants[f"R11b_{encoder_name}_zscore_l2"] = (
        l2_normalize(zscore(learn)), "cosine"
    )
    variants[f"R11c_{encoder_name}_zscore_l2_euclid"] = (
        l2_normalize(zscore(learn)), "euclidean"
    )
    variants[f"R11d_{encoder_name}_pca64"] = (pca_reduce(learn, 64), "cosine")
    variants[f"R11e_{encoder_name}_pca128"] = (pca_reduce(learn, 128), "cosine")
    variants[f"R11f_{encoder_name}_pca_whiten64"] = (
        pca_reduce(learn, 64, whiten=True), "cosine"
    )
    variants[f"R11g_{encoder_name}_pca_whiten128"] = (
        pca_reduce(learn, 128, whiten=True), "cosine"
    )

    # Concatenations
    variants[f"R12_{encoder_name}_plus_interpretable"] = (
        l2_normalize(np.concatenate([l2_normalize(learn), zscore(intr)], axis=1)),
        "cosine",
    )
    variants[f"R12b_{encoder_name}_plus_current"] = (
        l2_normalize(np.concatenate([l2_normalize(learn), cur], axis=1)),
        "cosine",
    )

    results: List[Dict] = []
    for name, (x, metric) in variants.items():
        r = evaluate(x, categories, sound_types, metric=metric)
        r["name"] = name
        results.append(r)
        log.info(
            "%-48s d=%4d %-9s top1_st=%.3f top1_cat=%.3f "
            "st_margin/std=%.2f hub_skew=%.2f hub_max=%d",
            name, r["dim"], r["metric"],
            r["top1_sound_type_accuracy"], r["top1_category_accuracy"],
            r["sound_type_margin_over_intra_std"],
            r["hubness_n10_skew"], r["hubness_top10_max_in_degree"],
        )

    results.sort(key=lambda r: -r["top1_sound_type_accuracy"])

    (args.out_dir / "representation_experiments_v2.json").write_text(
        json.dumps({"encoder": encoder_name, "results": results}, indent=2)
    )

    # Gate evaluation
    GATE_TOP1_ST = 0.80
    GATE_MARGIN = 3.0
    GATE_HUB_MAX = 15

    lines = ["# Representation experiments v2 (learned embeddings)", ""]
    lines.append(f"- Learned encoder: **{encoder_name}**")
    lines.append(f"- Presets aligned: {len(base_ids)}")
    lines.append(
        f"- Gate: top1_st ≥ {GATE_TOP1_ST}, "
        f"st_margin/std ≥ {GATE_MARGIN}, hub_max ≤ {GATE_HUB_MAX}"
    )
    lines.append("")
    lines.append(
        "| Variant | dim | metric | top1_st | top1_cat | top5_st | "
        "st_margin | st_margin/std | hub_skew | hub_max | gate |"
    )
    lines.append("|---|---:|---|---:|---:|---:|---:|---:|---:|---:|:---:|")
    for r in results:
        pass_all = (
            r["top1_sound_type_accuracy"] >= GATE_TOP1_ST
            and r["sound_type_margin_over_intra_std"] >= GATE_MARGIN
            and r["hubness_top10_max_in_degree"] <= GATE_HUB_MAX
        )
        gate = "✅" if pass_all else "—"
        lines.append(
            f"| {r['name']} | {r['dim']} | {r['metric']} | "
            f"{r['top1_sound_type_accuracy']:.3f} | "
            f"{r['top1_category_accuracy']:.3f} | "
            f"{r['top5_sound_type_hit_rate']:.3f} | "
            f"{r['intra_cross_margin_sound_type']:.4f} | "
            f"{r['sound_type_margin_over_intra_std']:.2f} | "
            f"{r['hubness_n10_skew']:.2f} | "
            f"{r['hubness_top10_max_in_degree']} | {gate} |"
        )
    (args.out_dir / "representation_experiments_v2.md").write_text(
        "\n".join(lines)
    )

    print(json.dumps({
        "encoder": encoder_name,
        "best_variant": results[0]["name"],
        "best_top1_sound_type": round(results[0]["top1_sound_type_accuracy"], 3),
        "best_top1_category": round(results[0]["top1_category_accuracy"], 3),
        "best_margin_over_std": round(
            results[0]["sound_type_margin_over_intra_std"], 2
        ),
        "best_hub_max": results[0]["hubness_top10_max_in_degree"],
        "gate_top1_st": GATE_TOP1_ST,
        "gate_margin": GATE_MARGIN,
        "gate_hub_max": GATE_HUB_MAX,
        "gate_passed_any": any(
            r["top1_sound_type_accuracy"] >= GATE_TOP1_ST
            and r["sound_type_margin_over_intra_std"] >= GATE_MARGIN
            and r["hubness_top10_max_in_degree"] <= GATE_HUB_MAX
            for r in results
        ),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
