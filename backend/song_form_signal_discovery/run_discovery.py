"""
Song-Form Phase 0B — Similarity Signal Discovery.

Read-only analysis. Does NOT modify production code, the classifier,
SongFormThresholds, clustering logic, UI, or API.

Generates six independent section-by-section similarity matrices per song:
  A. canonicalised chord similarity   (bundle chord lane → root+quality)
  B. beat-aligned chroma similarity   (stem audio → librosa chroma)
  C. section audio embedding (MFCC)   (stem audio → MFCC means+stds)
  D. vocal RMS similarity              (vocals stem → RMS envelope per section)
  E. instrumentation similarity        (per-stem RMS → activity vector)
  F. drum density similarity           (drums stem → onset-rate per section)

For every song writes:
  - <signal>_ssm__<slug>.png + <signal>_ssm__<slug>.json
  - per_song_signal_summary.json

For the corpus writes:
  - cross_song_ranking.csv
  - corpus_coverage.json

No thresholds. No clustering. No labels. No classifier.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import librosa
import matplotlib.pyplot as plt
import numpy as np
import soundfile as sf

REPO_ROOT = Path(__file__).resolve().parents[2]
HISTORY = REPO_ROOT / "backend" / "data" / "history.json"
OUT_DIR = REPO_ROOT / "backend" / "song_form_signal_discovery"

# Targets — same as Phase 0. Each entry maps the session to its on-disk stem dir.
TARGETS = [
    {
        "session_id": "b640c78a",
        "slug": "sex_on_fire",
        "title": "Sex On Fire (Kings of Leon) — canonical post-fix Run A",
        "stem_dir": "/var/folders/t9/s8pg2yfx3g73nt0lzf6p40xc0000gn/T/toneforge_stems_1xsffrxl",
        "stem_files": {
            "drums": "Kings Of Leon - Sex on Fire (Official Video)_drums.wav",
            "bass": "Kings Of Leon - Sex on Fire (Official Video)_bass.wav",
            "other": "Kings Of Leon - Sex on Fire (Official Video)_other.wav",
            "vocals": "Kings Of Leon - Sex on Fire (Official Video)_vocals.wav",
            "guitar": "Kings Of Leon - Sex on Fire (Official Video)_guitar.wav",
            "piano": "Kings Of Leon - Sex on Fire (Official Video)_piano.wav",
        },
    },
    {
        "session_id": "29b31695",
        "slug": "whats_my_age_again",
        "title": "What's My Age Again? (blink-182)",
        "stem_dir": "/var/folders/t9/s8pg2yfx3g73nt0lzf6p40xc0000gn/T/toneforge_stems_uoyxgz2v",
        "stem_files": {
            "drums": "tmp70ztsn5t_drums.wav",
            "bass": "tmp70ztsn5t_bass.wav",
            "vocals": "tmp70ztsn5t_vocals.wav",
            "other": "tmp70ztsn5t_other.wav",
        },
    },
]

SR_TARGET = 22050  # Match bundle sample rate. Resample on load if needed.


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def load_history() -> list[dict[str, Any]]:
    with HISTORY.open() as f:
        return json.load(f)


def find_session(history: list[dict[str, Any]], session_id: str) -> dict[str, Any] | None:
    for s in history:
        if s.get("id") == session_id:
            return s
    return None


def load_stem(path: Path) -> tuple[np.ndarray, int]:
    y, sr = sf.read(str(path), always_2d=False)
    if y.ndim > 1:
        y = y.mean(axis=1)
    if sr != SR_TARGET:
        y = librosa.resample(y, orig_sr=sr, target_sr=SR_TARGET)
        sr = SR_TARGET
    return y.astype(np.float32), sr


def slice_section(y: np.ndarray, sr: int, t0: float, t1: float) -> np.ndarray:
    i0 = max(0, int(round(t0 * sr)))
    i1 = min(len(y), int(round(t1 * sr)))
    return y[i0:i1] if i1 > i0 else np.zeros(1, dtype=y.dtype)


# -----------------------------------------------------------------------------
# Signal A — canonicalised chord similarity
# -----------------------------------------------------------------------------
def canonicalize_chord(symbol: str) -> str:
    """root + minor-or-major only. Drops power-chord suffix, drops sevenths,
    drops slash bass."""
    s = symbol.strip()
    if not s:
        return ""
    # drop slash bass
    if "/" in s:
        s = s.split("/", 1)[0]
    # extract root (1 or 2 chars with optional # / b)
    if len(s) >= 2 and s[1] in ("#", "b"):
        root, rest = s[:2], s[2:]
    else:
        root, rest = s[:1], s[1:]
    # Minor detection
    is_minor = rest.startswith("m") and not rest.startswith("maj")
    return f"{root}{'m' if is_minor else ''}"


def chord_seq_in(section: dict[str, Any], chords: list[dict[str, Any]]) -> list[str]:
    s0, s1 = section["start_time"], section["end_time"]
    out: list[str] = []
    for c in chords:
        mid = 0.5 * (c["start_s"] + c["end_s"])
        if s0 <= mid < s1:
            sym = canonicalize_chord(c["symbol"])
            if sym and (not out or out[-1] != sym):
                out.append(sym)
    return out


def chord_similarity(a: list[str], b: list[str]) -> float:
    """Jaccard + bigram-Jaccard average over canonicalised chord sequences."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    jacc = len(sa & sb) / len(sa | sb)

    def bigrams(seq: list[str]) -> set[tuple[str, str]]:
        return {(seq[i], seq[i + 1]) for i in range(len(seq) - 1)}

    ba, bb = bigrams(a), bigrams(b)
    if ba or bb:
        bi = len(ba & bb) / max(len(ba | bb), 1)
    else:
        bi = 1.0 if a == b else 0.0
    return float((jacc + bi) / 2.0)


# -----------------------------------------------------------------------------
# Audio-derived per-section features
# -----------------------------------------------------------------------------
def per_section_chroma(y_full: np.ndarray, sr: int, sections: list[dict[str, Any]]) -> list[np.ndarray]:
    """Mean chroma vector per section, computed on a beat-tracked full-song chromagram."""
    hop_length = 512
    chroma = librosa.feature.chroma_cqt(y=y_full, sr=sr, hop_length=hop_length)
    # Normalize per frame so loudness doesn't dominate
    norms = np.linalg.norm(chroma, axis=0, keepdims=True) + 1e-8
    chroma = chroma / norms
    frame_times = librosa.frames_to_time(np.arange(chroma.shape[1]), sr=sr, hop_length=hop_length)
    out: list[np.ndarray] = []
    for s in sections:
        mask = (frame_times >= s["start_time"]) & (frame_times < s["end_time"])
        if mask.any():
            out.append(chroma[:, mask].mean(axis=1))
        else:
            out.append(np.zeros(12, dtype=np.float32))
    return out


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    na = np.linalg.norm(a) + 1e-12
    nb = np.linalg.norm(b) + 1e-12
    return float(np.clip(np.dot(a, b) / (na * nb), 0.0, 1.0))


def per_section_mfcc(y_full: np.ndarray, sr: int, sections: list[dict[str, Any]]) -> list[np.ndarray]:
    """Per-section MFCC summary: mean + std of 13 MFCC coefficients (26-d embedding)."""
    hop_length = 512
    mfcc = librosa.feature.mfcc(y=y_full, sr=sr, n_mfcc=13, hop_length=hop_length)
    frame_times = librosa.frames_to_time(np.arange(mfcc.shape[1]), sr=sr, hop_length=hop_length)
    out: list[np.ndarray] = []
    for s in sections:
        mask = (frame_times >= s["start_time"]) & (frame_times < s["end_time"])
        if mask.any():
            seg = mfcc[:, mask]
            emb = np.concatenate([seg.mean(axis=1), seg.std(axis=1)])
        else:
            emb = np.zeros(26, dtype=np.float32)
        out.append(emb.astype(np.float32))
    return out


def per_section_rms(y_full: np.ndarray, sr: int, sections: list[dict[str, Any]]) -> list[dict[str, float]]:
    """Per-section RMS summary statistics from any single stem."""
    hop = 512
    rms = librosa.feature.rms(y=y_full, hop_length=hop)[0]
    frame_times = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=hop)
    out: list[dict[str, float]] = []
    for s in sections:
        mask = (frame_times >= s["start_time"]) & (frame_times < s["end_time"])
        if mask.any():
            v = rms[mask]
            out.append({
                "rms_mean": float(v.mean()),
                "rms_p95": float(np.percentile(v, 95)),
                "rms_std": float(v.std()),
                "dyn_range_db": float(20.0 * np.log10((np.percentile(v, 95) + 1e-9) / (np.percentile(v, 5) + 1e-9))),
            })
        else:
            out.append({"rms_mean": 0.0, "rms_p95": 0.0, "rms_std": 0.0, "dyn_range_db": 0.0})
    return out


def per_section_onset_rate(y_full: np.ndarray, sr: int, sections: list[dict[str, Any]]) -> list[float]:
    """Onset count per second within each section, from any single stem."""
    onset_frames = librosa.onset.onset_detect(y=y_full, sr=sr, units="time", backtrack=False)
    out: list[float] = []
    for s in sections:
        t0, t1 = s["start_time"], s["end_time"]
        n = int(((onset_frames >= t0) & (onset_frames < t1)).sum())
        dur = max(t1 - t0, 1e-3)
        out.append(n / dur)
    return out


# -----------------------------------------------------------------------------
# Pairwise score functions (operate on per-section feature blobs)
# -----------------------------------------------------------------------------
def rms_sim(a: dict[str, float], b: dict[str, float]) -> float:
    """Normalised L1 over (rms_mean, rms_p95, dyn_range_db). rms_mean &
    rms_p95 are normalised by the larger of the two; dyn_range_db divided by 30."""
    am, bm = a["rms_mean"], b["rms_mean"]
    ap, bp = a["rms_p95"], b["rms_p95"]
    ad, bd = a["dyn_range_db"], b["dyn_range_db"]
    den_m = max(am, bm, 1e-6)
    den_p = max(ap, bp, 1e-6)
    diff = np.mean([
        abs(am - bm) / den_m,
        abs(ap - bp) / den_p,
        abs(ad - bd) / 30.0,
    ])
    return float(max(0.0, 1.0 - diff))


def density_sim(a: float, b: float) -> float:
    den = max(a, b, 1e-6)
    return float(max(0.0, 1.0 - abs(a - b) / max(den, 1.0)))


# -----------------------------------------------------------------------------
# Matrix construction + plotting
# -----------------------------------------------------------------------------
def build_matrix(items: list[Any], score_fn) -> np.ndarray:
    n = len(items)
    m = np.zeros((n, n), dtype=np.float64)
    for i in range(n):
        for j in range(n):
            m[i, j] = score_fn(items[i], items[j])
    return m


def heatmap(matrix: np.ndarray, labels: list[str], title: str, outfile: Path) -> None:
    n = matrix.shape[0]
    fig_w = max(6.0, 0.55 * n + 3.0)
    fig, ax = plt.subplots(figsize=(fig_w, fig_w * 0.95))
    im = ax.imshow(matrix, cmap="viridis", vmin=0.0, vmax=1.0, aspect="equal")
    ax.set_xticks(range(n))
    ax.set_yticks(range(n))
    ax.set_xticklabels(labels, rotation=60, ha="right", fontsize=8)
    ax.set_yticklabels(labels, fontsize=8)
    for i in range(n):
        for j in range(n):
            v = matrix[i, j]
            ax.text(j, i, f"{v:.2f}", ha="center", va="center",
                    color=("white" if v < 0.55 else "black"),
                    fontsize=7)
    ax.set_title(title, fontsize=11)
    fig.colorbar(im, ax=ax, shrink=0.7, label="similarity (0=different, 1=same)")
    fig.tight_layout()
    fig.savefig(outfile, dpi=120, bbox_inches="tight")
    plt.close(fig)


def off_diag_stats(m: np.ndarray) -> dict[str, float]:
    n = m.shape[0]
    if n < 2:
        return {"mean": 0.0, "std": 0.0, "p10": 0.0, "p50": 0.0, "p90": 0.0,
                "min": 0.0, "max": 0.0, "pairs_ge_0_6": 0, "pairs_ge_0_8": 0,
                "separability": 0.0}
    mask = ~np.eye(n, dtype=bool)
    v = m[mask]
    p10 = float(np.percentile(v, 10))
    p90 = float(np.percentile(v, 90))
    # Separability score: how spread the off-diagonal distribution is.
    # 0 ⇒ everything identical (saturated), 1 ⇒ maximally separated.
    separability = float(v.std() * 2.0)  # rough — std caps at 0.5 → scaled to ~1.0
    return {
        "mean": float(v.mean()),
        "std": float(v.std()),
        "p10": p10,
        "p50": float(np.percentile(v, 50)),
        "p90": p90,
        "min": float(v.min()),
        "max": float(v.max()),
        "pairs_ge_0_6": int((v >= 0.6).sum() // 2),
        "pairs_ge_0_8": int((v >= 0.8).sum() // 2),
        "separability": separability,
    }


# -----------------------------------------------------------------------------
# Per-song pipeline
# -----------------------------------------------------------------------------
def analyse_song(
    session: dict[str, Any],
    target: dict[str, Any],
    out_dir: Path,
) -> dict[str, Any]:
    slug = target["slug"]
    title = target["title"]
    print(f"\n[analyse] {slug} — {title}")

    result = session["result"]
    sections = result.get("sections", [])
    chords = result.get("chords", [])
    n = len(sections)
    labels = [f"{i+1}.{s.get('type','?')[:6]}" for i, s in enumerate(sections)]

    # ---- Load stems we need
    stem_dir = Path(target["stem_dir"])
    stem_files = target["stem_files"]
    stems_loaded: dict[str, np.ndarray] = {}
    sr_used = SR_TARGET
    for name, fname in stem_files.items():
        path = stem_dir / fname
        if not path.exists():
            print(f"  [warn] missing stem {name} at {path}")
            continue
        print(f"  [load] {name} ({fname})")
        y, sr_used = load_stem(path)
        stems_loaded[name] = y
    if not stems_loaded:
        raise RuntimeError(f"No stems loaded for {slug}")

    # Build a synthetic "full mix" by summing stems (for chroma + MFCC).
    full = None
    for y in stems_loaded.values():
        if full is None:
            full = y.copy()
        else:
            L = min(len(full), len(y))
            full = full[:L] + y[:L]
    # Normalise to peak 1.0
    peak = float(np.max(np.abs(full)) + 1e-9)
    full = full / peak

    # ---- Signal A — canonicalised chord
    chord_seqs_canon = [chord_seq_in(s, chords) for s in sections]
    A = build_matrix(chord_seqs_canon, chord_similarity)

    # ---- Signal B — beat-aligned chroma (from synthetic full mix)
    chroma_vecs = per_section_chroma(full, sr_used, sections)
    B = build_matrix(chroma_vecs, cosine_similarity)

    # ---- Signal C — MFCC embedding (from full mix)
    mfcc_embs = per_section_mfcc(full, sr_used, sections)
    # Normalize each embedding to unit norm before cosine
    mfcc_embs_n = [v / (np.linalg.norm(v) + 1e-9) for v in mfcc_embs]
    C = build_matrix(mfcc_embs_n, cosine_similarity)

    # ---- Signal D — vocal RMS
    if "vocals" in stems_loaded:
        voc_rms = per_section_rms(stems_loaded["vocals"], sr_used, sections)
    else:
        voc_rms = [{"rms_mean": 0.0, "rms_p95": 0.0, "rms_std": 0.0, "dyn_range_db": 0.0}] * n
    D = build_matrix(voc_rms, rms_sim)

    # ---- Signal E — instrumentation activity vector per section
    # Per-stem mean RMS within section, then cosine across stems.
    stem_names = [name for name in ("drums", "bass", "vocals", "guitar", "other", "piano") if name in stems_loaded]
    stem_rms_per_section: list[np.ndarray] = []
    per_stem_rms_summary = {sn: per_section_rms(stems_loaded[sn], sr_used, sections) for sn in stem_names}
    for i in range(n):
        v = np.array([per_stem_rms_summary[sn][i]["rms_mean"] for sn in stem_names])
        stem_rms_per_section.append(v)
    # Normalise each section's vector to unit so cosine measures *relative*
    # instrumentation balance, not absolute loudness.
    inst_vecs = [v / (np.linalg.norm(v) + 1e-12) for v in stem_rms_per_section]
    E = build_matrix(inst_vecs, cosine_similarity)

    # ---- Signal F — drum onset rate
    if "drums" in stems_loaded:
        drum_rate = per_section_onset_rate(stems_loaded["drums"], sr_used, sections)
    else:
        drum_rate = [0.0] * n
    F = build_matrix(drum_rate, density_sim)

    # ---- Stats
    stats = {
        "A_chord_canonical": off_diag_stats(A),
        "B_chroma": off_diag_stats(B),
        "C_mfcc": off_diag_stats(C),
        "D_vocal_rms": off_diag_stats(D),
        "E_instrumentation": off_diag_stats(E),
        "F_drum_density": off_diag_stats(F),
    }

    # ---- Heatmaps
    heatmap(A, labels, f"A. Canonical Chord SSM — {title}", out_dir / f"A_chord_canonical__{slug}.png")
    heatmap(B, labels, f"B. Beat-Aligned Chroma SSM — {title}", out_dir / f"B_chroma__{slug}.png")
    heatmap(C, labels, f"C. MFCC Embedding SSM — {title}", out_dir / f"C_mfcc__{slug}.png")
    heatmap(D, labels, f"D. Vocal RMS SSM — {title}", out_dir / f"D_vocal_rms__{slug}.png")
    heatmap(E, labels, f"E. Instrumentation SSM — {title}", out_dir / f"E_instrumentation__{slug}.png")
    heatmap(F, labels, f"F. Drum Density SSM — {title}", out_dir / f"F_drum_density__{slug}.png")

    # ---- Raw JSON dumps
    section_meta = {
        "section_labels": labels,
        "section_types": [s.get("type") for s in sections],
        "section_start_s": [s.get("start_time") for s in sections],
        "section_end_s": [s.get("end_time") for s in sections],
    }
    for kind, mat in (
        ("A_chord_canonical", A),
        ("B_chroma", B),
        ("C_mfcc", C),
        ("D_vocal_rms", D),
        ("E_instrumentation", E),
        ("F_drum_density", F),
    ):
        with (out_dir / f"{kind}__{slug}.json").open("w") as f:
            json.dump({
                "session_id": session.get("id"),
                "slug": slug,
                "title": title,
                **section_meta,
                "matrix": mat.tolist(),
            }, f, indent=2)

    return {
        "session_id": session.get("id"),
        "slug": slug,
        "title": title,
        "section_count": n,
        "section_types": [s.get("type") for s in sections],
        "stats": stats,
        "stem_names_loaded": list(stems_loaded.keys()),
    }


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------
def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    history = load_history()
    per_song: list[dict[str, Any]] = []
    missing_targets: list[str] = []
    missing_stem_dirs: list[str] = []

    for target in TARGETS:
        sess = find_session(history, target["session_id"])
        if sess is None:
            missing_targets.append(target["slug"])
            continue
        if not Path(target["stem_dir"]).exists():
            missing_stem_dirs.append(target["slug"])
            continue
        per_song.append(analyse_song(sess, target, OUT_DIR))

    coverage = {
        "analysed_songs": [
            {"slug": p["slug"], "title": p["title"], "session_id": p["session_id"],
             "section_count": p["section_count"], "stems_loaded": p["stem_names_loaded"]}
            for p in per_song
        ],
        "missing_targets_no_session": missing_targets,
        "missing_targets_no_stem_dir": missing_stem_dirs,
        "named_trial_corpus_with_no_bundle": [
            "stairway_to_heaven", "hotel_california",
            "wish_you_were_here", "romance_de_amor",
        ],
        "non_corpus_targets": ["disco_of_doom", "simulated_life"],
        "notes": (
            "Phase 0B is constrained by the same corpus bottleneck as Phase 0. "
            "Only Sex On Fire and What's My Age Again? have analyzed bundles "
            "and live stem directories on disk. Stem directories are in /var/folders "
            "(macOS tempdir), which can be evicted; this study captures findings "
            "before any eviction. If a future run finds the stem dirs missing, "
            "the songs need to be re-analysed."
        ),
    }
    with (OUT_DIR / "corpus_coverage.json").open("w") as f:
        json.dump(coverage, f, indent=2)

    # Cross-song ranking CSV.
    signals = ["A_chord_canonical", "B_chroma", "C_mfcc",
               "D_vocal_rms", "E_instrumentation", "F_drum_density"]
    with (OUT_DIR / "per_song_signal_summary.json").open("w") as f:
        json.dump(per_song, f, indent=2)

    with (OUT_DIR / "cross_song_ranking.csv").open("w", newline="") as f:
        w = csv.writer(f)
        header = ["signal"]
        for p in per_song:
            header += [f"{p['slug']}__mean", f"{p['slug']}__std",
                       f"{p['slug']}__p10_p90_gap", f"{p['slug']}__separability",
                       f"{p['slug']}__pairs_ge_0_6", f"{p['slug']}__pairs_ge_0_8"]
        w.writerow(header)
        for sig in signals:
            row = [sig]
            for p in per_song:
                st = p["stats"][sig]
                row += [
                    f"{st['mean']:.3f}", f"{st['std']:.3f}",
                    f"{st['p90'] - st['p10']:.3f}",
                    f"{st['separability']:.3f}",
                    st["pairs_ge_0_6"], st["pairs_ge_0_8"],
                ]
            w.writerow(row)

    # Console summary
    print()
    print("=" * 78)
    print("Phase 0B — Similarity Signal Discovery (summary)")
    print("=" * 78)
    for p in per_song:
        print()
        print(f"  ▶ {p['title']} ({p['session_id']}, {p['section_count']} sections)")
        for sig in signals:
            st = p["stats"][sig]
            print(f"    {sig:24s} mean={st['mean']:.2f} std={st['std']:.2f} "
                  f"p10/p50/p90={st['p10']:.2f}/{st['p50']:.2f}/{st['p90']:.2f} "
                  f"pairs≥0.6={st['pairs_ge_0_6']:3d} pairs≥0.8={st['pairs_ge_0_8']:3d}")

    print()
    print(f"Outputs written to: {OUT_DIR}")


if __name__ == "__main__":
    main()
