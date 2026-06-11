"""Representation comparison harness for preset retrieval.

Re-extracts multiple representations from the 99 Analog preset audio files
in a single pass, then runs identical retrieval metrics on each so we can
compare them apples-to-apples.

Representations evaluated:
  R1  current_embedding      — backend StemFingerprint.embedding (128-d)
  R2  interpretable_only     — backend StemFingerprint.to_vector() (~24-d)
  R3  active_dims_only       — current_embedding restricted to dims with
                                 any non-zero across the catalog
  R4  high_variance_dims     — current_embedding restricted to top-K dims
                                 by variance (K=26 from prior diagnostics)
  R5  pca_16/32/64           — PCA reduction of current_embedding
  R6  zscore_then_l2         — per-dim z-score on current_embedding, re-norm
  R7  pca_whiten             — PCA with whitening (decorrelated, unit-var)
  R8  spectral_encoder_128   — backend ml/embeddings encoder.py spectral
                                 fallback (mel + MFCC + chroma + stats)
  R9  concat_int_plus_emb    — z-scored interpretable ⊕ current_embedding
  R10 spectral_pca_whiten    — PCA-whitened spectral_encoder_128

Metrics per representation:
  - top1_sound_type_accuracy   (leave-one-out, full catalog)
  - top1_category_accuracy     (leave-one-out, full catalog)
  - top5_sound_type_hit_rate   (avg over full catalog)
  - top5_category_hit_rate     (avg over full catalog)
  - intra_cross_margin         (mean intra cosine − mean cross cosine)
  - intra_cross_margin_in_std  (margin / within-class std)
  - hubness_n10_skew           (skewness of in-degree at k=10) — high = bad
  - hubness_top10_in_degree    (max in-degree at k=10) — high = bad

Distance metric also varied:
  - cosine (L2-normalized rows, dot product)
  - euclidean
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Tuple

import numpy as np

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from tone_forge.fingerprint.stem_fingerprint import FingerprintExtractor  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("repr_experiments")

CATALOG_PATH = (
    BACKEND_DIR / "preset_catalog_output" / "catalog" / "catalog_analog.json"
)
OUT_DIR = BACKEND_DIR / "preset_catalog_output" / "retrieval"


# ---------------------------------------------------------------------------
# 1) Audio pass: extract all base representations once
# ---------------------------------------------------------------------------
def extract_all(
    presets: List[dict],
    sr: int = 22050,
    max_seconds: float = 8.0,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, List[dict]]:
    """Return (current_emb, interpretable, spectral_emb, kept_presets)."""
    import librosa

    extractor = FingerprintExtractor()
    cur, intr, spec, kept = [], [], [], []
    t0 = time.time()
    for i, p in enumerate(presets):
        ap = p.get("audio_path")
        if not ap or not Path(ap).exists():
            log.warning("missing audio for %s", p.get("preset_id"))
            continue
        try:
            y, _ = librosa.load(ap, sr=sr, mono=True, duration=max_seconds)
            if y.size == 0:
                continue
            fp = extractor.extract(
                y, sr,
                stem_id=p["preset_id"],
                stem_type=p.get("sound_type", "unknown"),
            )
            cur.append(np.asarray(fp.embedding, dtype=np.float32))
            intr.append(np.asarray(fp.to_vector(), dtype=np.float32))
            spec.append(_spectral_encoder_128(y, sr))
            kept.append(p)
            if (i + 1) % 10 == 0:
                log.info("extracted %d/%d (%.1fs)",
                         i + 1, len(presets), time.time() - t0)
        except Exception as e:
            log.warning("failed %s: %s", p.get("preset_id"), e)
    if not kept:
        raise RuntimeError("nothing extracted")
    log.info("extraction took %.1fs", time.time() - t0)
    return (
        np.stack(cur, axis=0),
        np.stack(intr, axis=0),
        np.stack(spec, axis=0),
        kept,
    )


def _spectral_encoder_128(audio: np.ndarray, sr: int) -> np.ndarray:
    """Replica of backend/tone_forge/ml/embeddings/encoder.py::_encode_spectral.

    64 mel-mean + 20 spectral stats + 32 MFCC mean/std + 12 chroma = 128-d.
    L2-normalized.
    """
    import librosa

    n_fft = 2048
    hop_length = 512
    spec = np.abs(librosa.stft(audio, n_fft=n_fft, hop_length=hop_length))

    mel = librosa.feature.melspectrogram(S=spec ** 2, sr=sr, n_mels=64)
    mel_db = librosa.power_to_db(mel, ref=np.max)
    mel_mean = np.mean(mel_db, axis=1)

    centroid = librosa.feature.spectral_centroid(S=spec, sr=sr)[0]
    bandwidth = librosa.feature.spectral_bandwidth(S=spec, sr=sr)[0]
    rolloff = librosa.feature.spectral_rolloff(S=spec, sr=sr)[0]
    flatness = librosa.feature.spectral_flatness(S=spec)[0]
    spectral_stats = np.array([
        np.mean(centroid), np.std(centroid),
        np.percentile(centroid, 25), np.percentile(centroid, 75),
        np.mean(bandwidth), np.std(bandwidth),
        np.percentile(bandwidth, 25), np.percentile(bandwidth, 75),
        np.mean(rolloff), np.std(rolloff),
        np.percentile(rolloff, 25), np.percentile(rolloff, 75),
        np.mean(flatness), np.std(flatness),
        np.percentile(flatness, 25), np.percentile(flatness, 75),
        np.max(centroid), np.min(centroid),
        np.max(flatness), np.min(flatness),
    ])

    mfcc = librosa.feature.mfcc(y=audio, sr=sr, n_mfcc=16, hop_length=hop_length)
    mfcc_features = np.concatenate([np.mean(mfcc, axis=1), np.std(mfcc, axis=1)])

    chroma = librosa.feature.chroma_stft(S=spec, sr=sr)
    chroma_mean = np.mean(chroma, axis=1)

    embedding = np.concatenate([mel_mean, spectral_stats, mfcc_features, chroma_mean])
    n = np.linalg.norm(embedding)
    if n > 0:
        embedding = embedding / n
    return embedding.astype(np.float32)


# ---------------------------------------------------------------------------
# 2) Representation transforms (each returns a finite-dim feature matrix)
# ---------------------------------------------------------------------------
def l2_normalize(x: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(x, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return x / norms


def zscore(x: np.ndarray) -> np.ndarray:
    mu = x.mean(axis=0, keepdims=True)
    sd = x.std(axis=0, keepdims=True)
    sd[sd < 1e-8] = 1.0
    return (x - mu) / sd


def pca_reduce(x: np.ndarray, k: int, whiten: bool = False) -> np.ndarray:
    from sklearn.decomposition import PCA
    k = min(k, x.shape[1], x.shape[0])
    return PCA(n_components=k, whiten=whiten, random_state=0).fit_transform(x)


def select_active_dims(x: np.ndarray) -> np.ndarray:
    keep = (np.abs(x) > 1e-6).any(axis=0)
    return x[:, keep]


def select_high_variance(x: np.ndarray, top_k: int = 26) -> np.ndarray:
    var = x.var(axis=0)
    idx = np.argsort(-var)[:top_k]
    return x[:, idx]


# ---------------------------------------------------------------------------
# 3) Metrics
# ---------------------------------------------------------------------------
def pairwise_sim_or_dist(x: np.ndarray, metric: str) -> np.ndarray:
    """Higher = more similar. For euclidean, returns -distance."""
    if metric == "cosine":
        xn = l2_normalize(x)
        return xn @ xn.T
    elif metric == "euclidean":
        # negate so larger = closer (matches argmax convention)
        from sklearn.metrics import pairwise_distances
        return -pairwise_distances(x, metric="euclidean")
    else:
        raise ValueError(metric)


def evaluate(
    x: np.ndarray,
    categories: List[str],
    sound_types: List[str],
    metric: str = "cosine",
    k_top: int = 5,
    k_hub: int = 10,
) -> Dict[str, float]:
    n = x.shape[0]
    sim = pairwise_sim_or_dist(x, metric)
    np.fill_diagonal(sim, -np.inf)
    nn1 = sim.argmax(axis=1)

    # Top-1 accuracy
    top1_cat = float(np.mean([categories[i] == categories[nn1[i]] for i in range(n)]))
    top1_st = float(np.mean([sound_types[i] == sound_types[nn1[i]] for i in range(n)]))

    # Top-k hit rate
    order = np.argpartition(-sim, kth=min(k_top, n - 1), axis=1)[:, :k_top]
    top5_cat_hits, top5_st_hits = [], []
    for i in range(n):
        ids = order[i]
        # sort the k_top by sim within
        ids = ids[np.argsort(-sim[i, ids])]
        cat_hits = [categories[j] == categories[i] for j in ids]
        st_hits = [sound_types[j] == sound_types[i] for j in ids]
        top5_cat_hits.append(np.mean(cat_hits))
        top5_st_hits.append(np.mean(st_hits))

    # Intra/cross margin (cosine geometry — use cosine even when metric=euclidean for comparability)
    xn = l2_normalize(x)
    cos = xn @ xn.T
    iu = np.triu_indices(n, k=1)
    intra_c, cross_c = [], []
    intra_s, cross_s = [], []
    for i, j in zip(*iu):
        if categories[i] == categories[j]:
            intra_c.append(cos[i, j])
        else:
            cross_c.append(cos[i, j])
        if sound_types[i] == sound_types[j]:
            intra_s.append(cos[i, j])
        else:
            cross_s.append(cos[i, j])
    intra_c = np.asarray(intra_c)
    cross_c = np.asarray(cross_c)
    intra_s = np.asarray(intra_s)
    cross_s = np.asarray(cross_s)
    cat_margin = float(intra_c.mean() - cross_c.mean())
    st_margin = float(intra_s.mean() - cross_s.mean())
    cat_margin_in_std = cat_margin / (float(intra_c.std()) + 1e-9)
    st_margin_in_std = st_margin / (float(intra_s.std()) + 1e-9)

    # Hubness at k_hub: in-degree distribution
    k_hub = min(k_hub, n - 1)
    nn_k = np.argpartition(-sim, kth=k_hub, axis=1)[:, :k_hub]
    in_degree = np.zeros(n, dtype=np.int64)
    for row in nn_k:
        for j in row:
            in_degree[j] += 1
    mean_d = in_degree.mean()
    std_d = in_degree.std()
    if std_d > 0:
        # Pearson skewness ~ Nk-skewness from Radovanović et al.
        n10_skew = float(((in_degree - mean_d) ** 3).mean() / (std_d ** 3))
    else:
        n10_skew = 0.0
    top10_in = int(in_degree.max())
    top5_share = float(np.sort(in_degree)[-5:].sum() / max(1, in_degree.sum()))

    return {
        "n": n,
        "dim": int(x.shape[1]),
        "metric": metric,
        "top1_sound_type_accuracy": top1_st,
        "top1_category_accuracy": top1_cat,
        "top5_sound_type_hit_rate": float(np.mean(top5_st_hits)),
        "top5_category_hit_rate": float(np.mean(top5_cat_hits)),
        "intra_cross_margin_category": cat_margin,
        "intra_cross_margin_sound_type": st_margin,
        "category_margin_over_intra_std": cat_margin_in_std,
        "sound_type_margin_over_intra_std": st_margin_in_std,
        "hubness_n10_skew": n10_skew,
        "hubness_top10_max_in_degree": top10_in,
        "hubness_top5_in_degree_share": top5_share,
    }


# ---------------------------------------------------------------------------
# 4) Driver
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=CATALOG_PATH)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    parser.add_argument("--max-seconds", type=float, default=8.0)
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    presets = json.loads(args.catalog.read_text())["presets"]
    log.info("loaded %d presets", len(presets))

    # Re-extract everything in one audio pass
    cur, intr, spec, kept = extract_all(presets, max_seconds=args.max_seconds)
    np.savez(
        args.out_dir / "representations.npz",
        current_embedding=cur,
        interpretable=intr,
        spectral_encoder=spec,
        preset_ids=np.array([p["preset_id"] for p in kept]),
    )
    log.info("wrote representations.npz")

    categories = [p.get("category", "Unknown") for p in kept]
    sound_types = [p.get("sound_type", "unknown") for p in kept]

    # Build all variants
    variants: Dict[str, Tuple[np.ndarray, str]] = {}
    variants["R1_current_embedding_cosine"] = (cur, "cosine")
    variants["R1b_current_embedding_euclidean"] = (cur, "euclidean")
    variants["R2_interpretable_only"] = (intr, "cosine")
    variants["R2b_interpretable_only_zscore"] = (zscore(intr), "cosine")
    variants["R2c_interpretable_only_zscore_euclid"] = (zscore(intr), "euclidean")
    variants["R3_active_dims_only"] = (select_active_dims(cur), "cosine")
    variants["R4_high_variance_top26"] = (select_high_variance(cur, 26), "cosine")
    variants["R5a_pca16"] = (pca_reduce(cur, 16, whiten=False), "cosine")
    variants["R5b_pca32"] = (pca_reduce(cur, 32, whiten=False), "cosine")
    variants["R5c_pca64"] = (pca_reduce(cur, 64, whiten=False), "cosine")
    variants["R6_zscore_then_l2"] = (l2_normalize(zscore(cur)), "cosine")
    variants["R7a_pca_whiten16"] = (pca_reduce(cur, 16, whiten=True), "cosine")
    variants["R7b_pca_whiten32"] = (pca_reduce(cur, 32, whiten=True), "cosine")
    variants["R7c_pca_whiten32_euclid"] = (
        pca_reduce(cur, 32, whiten=True), "euclidean")
    variants["R8_spectral_encoder_128"] = (spec, "cosine")
    variants["R8b_spectral_zscore_l2"] = (l2_normalize(zscore(spec)), "cosine")
    variants["R8c_spectral_pca_whiten32"] = (
        pca_reduce(spec, 32, whiten=True), "cosine")
    variants["R8d_spectral_pca_whiten32_euclid"] = (
        pca_reduce(spec, 32, whiten=True), "euclidean")
    # Concatenate z-scored interpretable + current embedding
    concat = np.concatenate([zscore(intr), cur], axis=1)
    variants["R9_concat_zint_plus_emb"] = (l2_normalize(concat), "cosine")
    # Concatenate z-scored interpretable + spectral encoder
    concat2 = np.concatenate([zscore(intr), spec], axis=1)
    variants["R9b_concat_zint_plus_spec"] = (l2_normalize(concat2), "cosine")
    variants["R10_spectral_pca_whiten64"] = (
        pca_reduce(spec, 64, whiten=True), "cosine")

    results: List[Dict] = []
    for name, (x, metric) in variants.items():
        r = evaluate(x, categories, sound_types, metric=metric)
        r["name"] = name
        results.append(r)
        log.info(
            "%-42s d=%3d %-9s top1_st=%.3f top1_cat=%.3f "
            "margin_st=%.4f st_margin/std=%.2f hub_skew=%.2f hub_max=%d",
            name, r["dim"], r["metric"],
            r["top1_sound_type_accuracy"], r["top1_category_accuracy"],
            r["intra_cross_margin_sound_type"],
            r["sound_type_margin_over_intra_std"],
            r["hubness_n10_skew"], r["hubness_top10_max_in_degree"],
        )

    results.sort(key=lambda r: -r["top1_sound_type_accuracy"])
    (args.out_dir / "representation_experiments.json").write_text(
        json.dumps({"results": results}, indent=2)
    )

    # Markdown summary
    lines = ["# Representation experiments", ""]
    lines.append(f"- Presets: {len(kept)}")
    lines.append("- Distance: cosine on L2-normed rows unless suffixed `_euclidean`.")
    lines.append("- Hubness: in-degree at k=10. Higher skew / max-in-degree = worse.")
    lines.append("")
    lines.append("| Variant | dim | metric | top1_st | top1_cat | top5_st | st_margin | st_margin/std | hub_skew | hub_max |")
    lines.append("|---|---:|---|---:|---:|---:|---:|---:|---:|---:|")
    for r in results:
        lines.append(
            f"| {r['name']} | {r['dim']} | {r['metric']} | "
            f"{r['top1_sound_type_accuracy']:.3f} | "
            f"{r['top1_category_accuracy']:.3f} | "
            f"{r['top5_sound_type_hit_rate']:.3f} | "
            f"{r['intra_cross_margin_sound_type']:.4f} | "
            f"{r['sound_type_margin_over_intra_std']:.2f} | "
            f"{r['hubness_n10_skew']:.2f} | "
            f"{r['hubness_top10_max_in_degree']} |"
        )
    (args.out_dir / "representation_experiments.md").write_text("\n".join(lines))

    print(json.dumps(
        [{"name": r["name"],
          "top1_st": round(r["top1_sound_type_accuracy"], 3),
          "top1_cat": round(r["top1_category_accuracy"], 3),
          "st_margin/std": round(r["sound_type_margin_over_intra_std"], 2),
          "hub_skew": round(r["hubness_n10_skew"], 2),
          "hub_max": r["hubness_top10_max_in_degree"],
          "dim": r["dim"],
          "metric": r["metric"]}
         for r in results[:12]],
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
