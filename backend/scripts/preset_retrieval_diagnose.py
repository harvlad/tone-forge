"""Diagnose the 128-dim preset embedding space.

Reads the embeddings produced by ``preset_retrieval_validation.py`` and
quantifies:
    - Embedding magnitude / sparsity / dimension utilization
    - Pairwise cosine similarity distribution (min, mean, percentiles)
    - Intra-category vs cross-category similarity
    - Top-1 retrieval accuracy across the FULL catalog (not just 20 queries)

This is analysis-only; it does not modify the fingerprint code.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np

BACKEND_DIR = Path(__file__).resolve().parents[1]


def load(retrieval_dir: Path) -> Tuple[np.ndarray, List[str], Dict[str, dict]]:
    npz = np.load(retrieval_dir / "embeddings.npz", allow_pickle=False)
    embeddings: np.ndarray = npz["embeddings"]
    preset_ids: List[str] = [str(s) for s in npz["preset_ids"]]
    catalog = json.loads(
        (BACKEND_DIR / "preset_catalog_output" / "catalog" / "catalog_analog.json")
        .read_text()
    )
    by_id = {p["preset_id"]: p for p in catalog["presets"]}
    return embeddings, preset_ids, by_id


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--retrieval-dir",
        type=Path,
        default=BACKEND_DIR / "preset_catalog_output" / "retrieval",
    )
    args = parser.parse_args()

    emb, ids, by_id = load(args.retrieval_dir)
    n, d = emb.shape

    # --- Dimension utilization ---
    abs_emb = np.abs(emb)
    dim_active = (abs_emb > 1e-6).any(axis=0)              # used by at least one preset
    dim_var = emb.var(axis=0)
    n_active = int(dim_active.sum())
    n_high_var = int((dim_var > 1e-4).sum())

    # --- Magnitude (should be ~1 since rows are L2-normed) ---
    row_norms = np.linalg.norm(emb, axis=1)

    # --- Pairwise cosine similarity (off-diagonal) ---
    sims = emb @ emb.T
    iu = np.triu_indices(n, k=1)
    off_diag = sims[iu]

    # --- Intra-category vs cross-category similarity ---
    categories = [by_id[pid].get("category", "Unknown") for pid in ids]
    sound_types = [by_id[pid].get("sound_type", "unknown") for pid in ids]

    def intra_cross(labels: List[str]) -> Tuple[float, float, float, float]:
        intra, cross = [], []
        for i, j in zip(*iu):
            if labels[i] == labels[j]:
                intra.append(sims[i, j])
            else:
                cross.append(sims[i, j])
        intra = np.asarray(intra)
        cross = np.asarray(cross)
        return (
            float(intra.mean()) if intra.size else float("nan"),
            float(cross.mean()) if cross.size else float("nan"),
            float(intra.std()) if intra.size else float("nan"),
            float(cross.std()) if cross.size else float("nan"),
        )

    cat_intra_mu, cat_cross_mu, cat_intra_sd, cat_cross_sd = intra_cross(categories)
    st_intra_mu, st_cross_mu, st_intra_sd, st_cross_sd = intra_cross(sound_types)

    # --- Full-catalog top-1 accuracy (leave-one-out) ---
    sims_self_masked = sims.copy()
    np.fill_diagonal(sims_self_masked, -np.inf)
    nn_idx = sims_self_masked.argmax(axis=1)
    top1_category_match = float(np.mean(
        [categories[i] == categories[nn_idx[i]] for i in range(n)]
    ))
    top1_sound_type_match = float(np.mean(
        [sound_types[i] == sound_types[nn_idx[i]] for i in range(n)]
    ))

    # --- Where in the embedding does the variance live? ---
    # Hand-crafted features occupy ~first 21 dims, mel summary 22..86, then zero-pad.
    head_var = float(emb[:, :21].var(axis=0).sum())
    mel_var = float(emb[:, 21:86].var(axis=0).sum())
    tail_var = float(emb[:, 86:].var(axis=0).sum())

    summary = {
        "n_presets": int(n),
        "embedding_dim": int(d),
        "dimension_utilization": {
            "active_dims": n_active,
            "high_variance_dims": n_high_var,
            "head_block_variance_0_21": head_var,
            "mel_block_variance_21_86": mel_var,
            "tail_block_variance_86_128": tail_var,
        },
        "row_norm": {
            "min": float(row_norms.min()),
            "mean": float(row_norms.mean()),
            "max": float(row_norms.max()),
        },
        "pairwise_cosine": {
            "min": float(off_diag.min()),
            "p05": float(np.percentile(off_diag, 5)),
            "p25": float(np.percentile(off_diag, 25)),
            "median": float(np.median(off_diag)),
            "p75": float(np.percentile(off_diag, 75)),
            "p95": float(np.percentile(off_diag, 95)),
            "max": float(off_diag.max()),
            "mean": float(off_diag.mean()),
            "std": float(off_diag.std()),
        },
        "category_separation": {
            "intra_mean": cat_intra_mu,
            "cross_mean": cat_cross_mu,
            "intra_std": cat_intra_sd,
            "cross_std": cat_cross_sd,
            "margin_intra_minus_cross": cat_intra_mu - cat_cross_mu,
        },
        "sound_type_separation": {
            "intra_mean": st_intra_mu,
            "cross_mean": st_cross_mu,
            "intra_std": st_intra_sd,
            "cross_std": st_cross_sd,
            "margin_intra_minus_cross": st_intra_mu - st_cross_mu,
        },
        "leave_one_out_full_catalog": {
            "top1_category_accuracy": top1_category_match,
            "top1_sound_type_accuracy": top1_sound_type_match,
        },
    }

    out_path = args.retrieval_dir / "retrieval_diagnostics.json"
    out_path.write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
