"""Preset Retrieval Validation.

Generates 128-dim StemFingerprint embeddings for every preset in
``preset_catalog_output/catalog/catalog_analog.json``, then runs leave-one-out
top-5 nearest-neighbor retrieval over a random sample of presets.

Outputs:
    backend/preset_catalog_output/retrieval/embeddings.npz
    backend/preset_catalog_output/retrieval/retrieval_top5.json
    backend/preset_catalog_output/retrieval/retrieval_report.md

The objective is to answer one question: does the existing 128-dim
fingerprint space cluster perceptually similar Analog presets close
together, or does it not?
"""
from __future__ import annotations

import argparse
import json
import logging
import random
import sys
import time
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np

# Make backend/ importable when run from repo root or from backend/
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from tone_forge.fingerprint.stem_fingerprint import FingerprintExtractor  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("preset_retrieval")

CATALOG_PATH = (
    BACKEND_DIR / "preset_catalog_output" / "catalog" / "catalog_analog.json"
)
OUT_DIR = BACKEND_DIR / "preset_catalog_output" / "retrieval"


def load_catalog(path: Path) -> List[dict]:
    with open(path, "r") as f:
        data = json.load(f)
    return data["presets"]


def extract_embeddings(
    presets: List[dict],
    sr: int = 22050,
    max_seconds: float = 8.0,
) -> Tuple[np.ndarray, List[dict]]:
    """Compute 128-dim embeddings for every preset whose audio exists.

    Returns (embeddings [N x 128], kept_presets [N]).
    """
    import librosa

    extractor = FingerprintExtractor()
    embeddings: List[np.ndarray] = []
    kept: List[dict] = []

    t0 = time.time()
    for i, preset in enumerate(presets):
        audio_path = preset.get("audio_path")
        if not audio_path or not Path(audio_path).exists():
            log.warning("missing audio for %s", preset.get("preset_id"))
            continue
        try:
            y, _ = librosa.load(audio_path, sr=sr, mono=True, duration=max_seconds)
            if y.size == 0:
                log.warning("empty audio for %s", preset.get("preset_id"))
                continue
            fp = extractor.extract(
                y, sr,
                stem_id=preset["preset_id"],
                stem_type=preset.get("sound_type", "unknown"),
            )
            emb = np.asarray(fp.embedding, dtype=np.float32)
            if emb.shape != (128,):
                log.warning(
                    "bad embedding shape %s for %s",
                    emb.shape, preset.get("preset_id"),
                )
                continue
            # L2 normalize (defensive; extractor already does this)
            n = np.linalg.norm(emb)
            if n > 0:
                emb = emb / n
            embeddings.append(emb)
            kept.append(preset)
            if (i + 1) % 10 == 0:
                log.info("fingerprinted %d/%d (%.1fs elapsed)",
                         i + 1, len(presets), time.time() - t0)
        except Exception as e:
            log.warning("fingerprint failed for %s: %s",
                        preset.get("preset_id"), e)

    if not embeddings:
        raise RuntimeError("No embeddings produced; check audio paths")

    log.info("fingerprinted %d presets in %.1fs", len(kept), time.time() - t0)
    return np.stack(embeddings, axis=0), kept


def top_k(
    embeddings: np.ndarray,
    query_idx: int,
    k: int = 5,
) -> List[Tuple[int, float]]:
    """Return top-k neighbors for `query_idx` by cosine similarity (excludes self)."""
    sims = embeddings @ embeddings[query_idx]   # cosine since rows are L2-normed
    sims[query_idx] = -np.inf
    order = np.argsort(-sims)[:k]
    return [(int(i), float(sims[i])) for i in order]


def run_retrieval(
    embeddings: np.ndarray,
    presets: List[dict],
    n_queries: int = 20,
    k: int = 5,
    seed: int = 42,
) -> dict:
    rng = random.Random(seed)
    n = len(presets)
    query_indices = sorted(rng.sample(range(n), min(n_queries, n)))

    results = []
    for q in query_indices:
        neighbors = top_k(embeddings, q, k=k)
        q_preset = presets[q]
        entry = {
            "query": {
                "preset_id": q_preset["preset_id"],
                "preset_name": q_preset.get("preset_name", ""),
                "category": q_preset.get("category", ""),
                "sound_type": q_preset.get("sound_type", ""),
            },
            "neighbors": [
                {
                    "rank": rank + 1,
                    "preset_id": presets[i]["preset_id"],
                    "preset_name": presets[i].get("preset_name", ""),
                    "category": presets[i].get("category", ""),
                    "sound_type": presets[i].get("sound_type", ""),
                    "cosine_similarity": sim,
                }
                for rank, (i, sim) in enumerate(neighbors)
            ],
        }
        results.append(entry)
    return {"k": k, "n_queries": len(query_indices), "queries": results}


def summarize(
    retrieval: dict,
    presets: List[dict],
) -> dict:
    """Compute retrieval health metrics.

    Metric definitions:
    - category_hit@k: fraction of neighbors that share the query's category.
    - sound_type_hit@k: fraction of neighbors that share the query's sound_type.
    - top1_category_match: 1 if rank-1 neighbor has same category as query.
    - top1_sound_type_match: same for sound_type.
    """
    cat_hits, st_hits, top1_cat, top1_st = [], [], [], []
    per_query = []
    for q in retrieval["queries"]:
        q_cat = q["query"]["category"]
        q_st = q["query"]["sound_type"]
        neighbors = q["neighbors"]
        cat_match = [n["category"] == q_cat for n in neighbors]
        st_match = [n["sound_type"] == q_st for n in neighbors]
        cat_hits.append(np.mean(cat_match) if cat_match else 0.0)
        st_hits.append(np.mean(st_match) if st_match else 0.0)
        top1_cat.append(float(cat_match[0]) if cat_match else 0.0)
        top1_st.append(float(st_match[0]) if st_match else 0.0)
        per_query.append({
            "preset_id": q["query"]["preset_id"],
            "category": q_cat,
            "sound_type": q_st,
            "category_hit_rate": float(np.mean(cat_match) if cat_match else 0.0),
            "sound_type_hit_rate": float(np.mean(st_match) if st_match else 0.0),
            "top1_category_match": bool(cat_match[0]) if cat_match else False,
            "top1_sound_type_match": bool(st_match[0]) if st_match else False,
        })

    return {
        "n_queries": len(retrieval["queries"]),
        "k": retrieval["k"],
        "avg_category_hit_at_k": float(np.mean(cat_hits) if cat_hits else 0.0),
        "avg_sound_type_hit_at_k": float(np.mean(st_hits) if st_hits else 0.0),
        "top1_category_accuracy": float(np.mean(top1_cat) if top1_cat else 0.0),
        "top1_sound_type_accuracy": float(np.mean(top1_st) if top1_st else 0.0),
        "category_distribution": _category_distribution(presets),
        "per_query": per_query,
    }


def _category_distribution(presets: List[dict]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for p in presets:
        c = p.get("category", "Unknown")
        counts[c] = counts.get(c, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


def write_markdown_report(
    retrieval: dict,
    summary: dict,
    out_path: Path,
) -> None:
    lines: List[str] = []
    lines.append("# Preset Retrieval Validation Report")
    lines.append("")
    lines.append(f"- Queries: **{summary['n_queries']}**")
    lines.append(f"- k: **{summary['k']}**")
    lines.append(f"- Embedding dim: **128**")
    lines.append(f"- Distance metric: **cosine similarity** "
                 "(rows are L2-normalized; sim = dot product)")
    lines.append("")
    lines.append("## Aggregate metrics")
    lines.append("")
    lines.append(f"- avg category hit@{summary['k']}: "
                 f"**{summary['avg_category_hit_at_k']:.3f}**")
    lines.append(f"- avg sound_type hit@{summary['k']}: "
                 f"**{summary['avg_sound_type_hit_at_k']:.3f}**")
    lines.append(f"- top-1 category accuracy: "
                 f"**{summary['top1_category_accuracy']:.3f}**")
    lines.append(f"- top-1 sound_type accuracy: "
                 f"**{summary['top1_sound_type_accuracy']:.3f}**")
    lines.append("")
    lines.append("## Catalog category distribution")
    lines.append("")
    lines.append("| Category | Count |")
    lines.append("|---|---:|")
    for cat, n in summary["category_distribution"].items():
        lines.append(f"| {cat} | {n} |")
    lines.append("")
    lines.append("## Per-query top-5 neighbors")
    lines.append("")
    for q in retrieval["queries"]:
        lines.append(f"### {q['query']['preset_name']} "
                     f"(`{q['query']['preset_id']}`)")
        lines.append(
            f"- query category: **{q['query']['category']}**, "
            f"sound_type: **{q['query']['sound_type']}**"
        )
        lines.append("")
        lines.append("| Rank | Preset | Category | Sound type | Cosine sim |")
        lines.append("|---:|---|---|---|---:|")
        for n in q["neighbors"]:
            same_cat = "✓" if n["category"] == q["query"]["category"] else "·"
            lines.append(
                f"| {n['rank']} | {n['preset_name']} | "
                f"{n['category']} {same_cat} | {n['sound_type']} | "
                f"{n['cosine_similarity']:.4f} |"
            )
        lines.append("")
    out_path.write_text("\n".join(lines))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=CATALOG_PATH)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    parser.add_argument("--n-queries", type=int, default=20)
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-seconds", type=float, default=8.0,
                        help="Truncate audio at this duration before fingerprinting.")
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)

    presets = load_catalog(args.catalog)
    log.info("loaded %d presets from %s", len(presets), args.catalog)

    embeddings, kept = extract_embeddings(presets, max_seconds=args.max_seconds)

    # Persist embeddings + ids
    np.savez(
        args.out_dir / "embeddings.npz",
        embeddings=embeddings,
        preset_ids=np.array([p["preset_id"] for p in kept]),
    )
    log.info("wrote embeddings to %s", args.out_dir / "embeddings.npz")

    retrieval = run_retrieval(
        embeddings, kept, n_queries=args.n_queries, k=args.k, seed=args.seed,
    )
    (args.out_dir / "retrieval_top5.json").write_text(
        json.dumps(retrieval, indent=2)
    )
    log.info("wrote retrieval_top5.json")

    summary = summarize(retrieval, kept)
    (args.out_dir / "retrieval_summary.json").write_text(
        json.dumps(summary, indent=2)
    )
    log.info("wrote retrieval_summary.json")

    write_markdown_report(retrieval, summary, args.out_dir / "retrieval_report.md")
    log.info("wrote retrieval_report.md")

    print(json.dumps({
        "n_presets_fingerprinted": len(kept),
        "n_queries": summary["n_queries"],
        "k": summary["k"],
        "avg_category_hit_at_k": summary["avg_category_hit_at_k"],
        "avg_sound_type_hit_at_k": summary["avg_sound_type_hit_at_k"],
        "top1_category_accuracy": summary["top1_category_accuracy"],
        "top1_sound_type_accuracy": summary["top1_sound_type_accuracy"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
