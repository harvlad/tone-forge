"""
Song-Form Phase 0 — Similarity Feasibility Study.

Read-only analysis script. Does NOT modify production code, the classifier,
SongFormThresholds, clustering logic, UI, or API. Produces:

  - chord_ssm__<slug>.png + chord_ssm__<slug>.json
  - repetition_ssm__<slug>.png + repetition_ssm__<slug>.json
  - vocal_ssm__<slug>.png + vocal_ssm__<slug>.json
  - composite_ssm__<slug>.png + composite_ssm__<slug>.json
  - per_song_summary.csv
  - corpus_coverage.json

Input: backend/data/history.json (existing analyzed bundles).
Output: backend/song_form_feasibility/.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
HISTORY = REPO_ROOT / "backend" / "data" / "history.json"
OUT_DIR = REPO_ROOT / "backend" / "song_form_feasibility"

# Sessions selected by hand from the inventory step.
# Format: (session_id, slug, friendly_title)
TARGETS = [
    ("b640c78a", "sex_on_fire",          "Sex On Fire (Kings of Leon) — canonical post-fix Run A"),
    ("29b31695", "whats_my_age_again",   "What's My Age Again? (blink-182)"),
]

# Corpus songs documented in song_trial_corpus.json. Used to print explicit
# "no bundle yet" notes when they are missing from history.json.
CORPUS_EXPECTED = [
    "stairway_to_heaven",
    "hotel_california",
    "wish_you_were_here",
    "romance_de_amor",
    "sex_on_fire",
    "disco_of_doom",
    "simulated_life",
]


# -----------------------------------------------------------------------------
# Bundle access
# -----------------------------------------------------------------------------
def load_history() -> list[dict[str, Any]]:
    with HISTORY.open() as f:
        return json.load(f)


def find_session(history: list[dict[str, Any]], session_id: str) -> dict[str, Any] | None:
    for s in history:
        if s.get("id") == session_id:
            return s
    return None


# -----------------------------------------------------------------------------
# Per-section feature extraction
# -----------------------------------------------------------------------------
def chord_sequence_in(section: dict[str, Any], chords: list[dict[str, Any]]) -> list[str]:
    """Return the chord symbols whose midpoint falls inside [start_time, end_time)."""
    s0 = section["start_time"]
    s1 = section["end_time"]
    out: list[str] = []
    for c in chords:
        mid = 0.5 * (c["start_s"] + c["end_s"])
        if s0 <= mid < s1:
            out.append(c["symbol"])
    return out


def stem_feature(section: dict[str, Any], stem_name: str, field: str) -> float | None:
    for sf in section.get("debug_features", []):
        if sf.get("stem_name") == stem_name:
            return sf.get(field)
    return None


def per_section_vocal_activity(section: dict[str, Any]) -> dict[str, float]:
    """Three vocal-activity signals per section, derived from debug_features.

    Falls back to 0.0 when the vocal stem is missing.
    """
    vfr = stem_feature(section, "vocals", "voiced_frame_ratio") or 0.0
    notes = stem_feature(section, "vocals", "note_count") or 0
    dur = stem_feature(section, "vocals", "duration_s") or section.get("duration") or 1.0
    note_density = notes / max(dur, 1e-6)
    lead_act = stem_feature(section, "vocals", "lead_activity_score") or 0.0
    return {
        "voiced_frame_ratio": float(vfr),
        "note_density": float(note_density),
        "lead_activity_score": float(lead_act),
    }


def per_section_repetition_signature(section: dict[str, Any]) -> dict[str, float]:
    """Stem-keyed repetition_score map for cross-section pairwise similarity."""
    out: dict[str, float] = {}
    for sf in section.get("debug_features", []):
        name = sf.get("stem_name")
        if name:
            out[name] = float(sf.get("repetition_score") or 0.0)
    return out


# -----------------------------------------------------------------------------
# Similarity primitives
# -----------------------------------------------------------------------------
def chord_seq_similarity(a: list[str], b: list[str]) -> float:
    """Symmetric similarity in [0, 1] combining:

      - set Jaccard over chord symbols (progression overlap)
      - normalised edit distance over the symbol sequence
      - shared-bigram fraction (repeated progression score)

    Returns mean of the three sub-scores. Empty sequences return 0.0 unless
    both are empty, in which case they are by-convention treated as the same
    (1.0) so silent sections cluster.
    """
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0

    # 1. Jaccard
    sa, sb = set(a), set(b)
    jacc = len(sa & sb) / len(sa | sb)

    # 2. Normalised Levenshtein distance over the symbol sequence
    m, n = len(a), len(b)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(m + 1):
        dp[i][0] = i
    for j in range(n + 1):
        dp[0][j] = j
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            dp[i][j] = min(
                dp[i - 1][j] + 1,
                dp[i][j - 1] + 1,
                dp[i - 1][j - 1] + cost,
            )
    edit_norm = 1.0 - dp[m][n] / max(m, n)

    # 3. Shared bigrams (repeated progression score)
    def bigrams(seq: list[str]) -> set[tuple[str, str]]:
        return {(seq[i], seq[i + 1]) for i in range(len(seq) - 1)}

    ba, bb = bigrams(a), bigrams(b)
    if ba or bb:
        bigram_jacc = len(ba & bb) / max(len(ba | bb), 1)
    else:
        bigram_jacc = 1.0 if a == b else 0.0

    return float(np.mean([jacc, max(edit_norm, 0.0), bigram_jacc]))


def repetition_similarity(a: dict[str, float], b: dict[str, float]) -> float:
    """1 - L1 distance over per-stem repetition_score vectors, clipped to [0, 1].

    Sections whose stems all repeat similarly will sit near 1.0; sections where
    one is highly repetitive and the other is not will sit near 0.0.
    """
    stems = sorted(set(a) | set(b))
    if not stems:
        return 1.0
    diffs = [abs(a.get(s, 0.0) - b.get(s, 0.0)) for s in stems]
    return float(max(0.0, 1.0 - np.mean(diffs)))


def vocal_similarity(a: dict[str, float], b: dict[str, float]) -> float:
    """Component-wise inverse-L1 over the three vocal signals, normalised."""
    keys = ("voiced_frame_ratio", "note_density", "lead_activity_score")
    # Note density needs scaling — clip / normalise per-song would be cleanest,
    # but for SSM purposes we use a soft cap at 10 notes/s.
    def _norm(d: dict[str, float]) -> np.ndarray:
        return np.array([
            float(d.get("voiced_frame_ratio", 0.0)),
            min(float(d.get("note_density", 0.0)) / 10.0, 1.0),
            float(d.get("lead_activity_score", 0.0)),
        ])
    diff = np.abs(_norm(a) - _norm(b))
    return float(max(0.0, 1.0 - diff.mean()))


# -----------------------------------------------------------------------------
# Matrix construction
# -----------------------------------------------------------------------------
def build_matrix(sections: list[Any], score_fn) -> np.ndarray:
    n = len(sections)
    m = np.zeros((n, n))
    for i in range(n):
        for j in range(n):
            m[i, j] = score_fn(sections[i], sections[j])
    return m


def heatmap(
    matrix: np.ndarray,
    labels: list[str],
    title: str,
    outfile: Path,
) -> None:
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


# -----------------------------------------------------------------------------
# Per-song pipeline
# -----------------------------------------------------------------------------
def analyse_song(
    session: dict[str, Any],
    slug: str,
    title: str,
    out_dir: Path,
) -> dict[str, Any]:
    result = session["result"]
    sections = result.get("sections", [])
    chords = result.get("chords", [])
    n = len(sections)

    # Per-section feature blobs
    chord_seqs = [chord_sequence_in(s, chords) for s in sections]
    rep_sigs = [per_section_repetition_signature(s) for s in sections]
    vocal_sigs = [per_section_vocal_activity(s) for s in sections]
    labels = [
        f"{i+1}.{s.get('type','?')[:6]}"
        for i, s in enumerate(sections)
    ]

    # Matrices
    chord_m = build_matrix(chord_seqs, chord_seq_similarity)
    rep_m = build_matrix(rep_sigs, repetition_similarity)
    voc_m = build_matrix(vocal_sigs, vocal_similarity)
    composite = (chord_m + rep_m + voc_m) / 3.0

    # Off-diagonal stats (diagonal is always 1.0 by construction)
    def off_diag_stats(m: np.ndarray) -> dict[str, float]:
        if n < 2:
            return {"mean": 0.0, "p95": 0.0, "max": 0.0, "count_ge_0_8": 0}
        mask = ~np.eye(n, dtype=bool)
        v = m[mask]
        return {
            "mean": float(v.mean()),
            "p95": float(np.percentile(v, 95)),
            "max": float(v.max()),
            "count_ge_0_8": int((v >= 0.8).sum() // 2),  # symmetric → halve
            "count_ge_0_6": int((v >= 0.6).sum() // 2),
        }

    stats = {
        "chord": off_diag_stats(chord_m),
        "repetition": off_diag_stats(rep_m),
        "vocal": off_diag_stats(voc_m),
        "composite": off_diag_stats(composite),
    }

    # Heatmaps
    heatmap(chord_m, labels, f"Chord SSM — {title}", out_dir / f"chord_ssm__{slug}.png")
    heatmap(rep_m, labels, f"Repetition SSM — {title}", out_dir / f"repetition_ssm__{slug}.png")
    heatmap(voc_m, labels, f"Vocal Activity SSM — {title}", out_dir / f"vocal_ssm__{slug}.png")
    heatmap(composite, labels, f"Composite SSM — {title}", out_dir / f"composite_ssm__{slug}.png")

    # Raw JSON dumps
    def dump(matrix: np.ndarray, kind: str) -> None:
        with (out_dir / f"{kind}_ssm__{slug}.json").open("w") as f:
            json.dump({
                "session_id": session.get("id"),
                "slug": slug,
                "title": title,
                "section_labels": labels,
                "section_types": [s.get("type") for s in sections],
                "section_start_s": [s.get("start_time") for s in sections],
                "section_end_s": [s.get("end_time") for s in sections],
                "matrix": matrix.tolist(),
            }, f, indent=2)

    dump(chord_m, "chord")
    dump(rep_m, "repetition")
    dump(voc_m, "vocal")
    dump(composite, "composite")

    # Find sections that have at least one strong off-diagonal match
    def strongly_paired(m: np.ndarray, threshold: float) -> list[int]:
        out: list[int] = []
        for i in range(n):
            for j in range(n):
                if i != j and m[i, j] >= threshold:
                    out.append(i)
                    break
        return out

    pair_indices_chord = strongly_paired(chord_m, 0.8)
    pair_indices_composite = strongly_paired(composite, 0.7)

    # Find single-outlier sections (no off-diagonal entry above 0.5)
    def outliers(m: np.ndarray, threshold: float) -> list[int]:
        out: list[int] = []
        for i in range(n):
            row_max = 0.0
            for j in range(n):
                if i != j:
                    row_max = max(row_max, m[i, j])
            if row_max < threshold:
                out.append(i)
        return out

    outlier_indices_chord = outliers(chord_m, 0.5)
    outlier_indices_composite = outliers(composite, 0.5)

    return {
        "session_id": session.get("id"),
        "slug": slug,
        "title": title,
        "section_count": n,
        "section_types": [s.get("type") for s in sections],
        "stats": stats,
        "strong_chord_pairs_section_indices": pair_indices_chord,
        "strong_composite_pairs_section_indices": pair_indices_composite,
        "chord_outlier_section_indices": outlier_indices_chord,
        "composite_outlier_section_indices": outlier_indices_composite,
        "chord_sequences": chord_seqs,
    }


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------
def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    history = load_history()

    per_song: list[dict[str, Any]] = []
    missing: list[str] = []

    for session_id, slug, title in TARGETS:
        sess = find_session(history, session_id)
        if sess is None:
            missing.append(f"{slug}: session_id={session_id} not in history.json")
            continue
        print(f"[analyse] {slug} ({session_id}) — {title}")
        per_song.append(analyse_song(sess, slug, title, OUT_DIR))

    # Coverage doc: explicit list of expected-but-absent corpus songs.
    analysed_slugs = {p["slug"] for p in per_song}
    coverage = {
        "expected_corpus_songs": CORPUS_EXPECTED,
        "analysed_songs": [
            {
                "slug": p["slug"],
                "title": p["title"],
                "session_id": p["session_id"],
                "section_count": p["section_count"],
            }
            for p in per_song
        ],
        "missing_from_history_json": [
            c for c in CORPUS_EXPECTED if c not in analysed_slugs
        ],
        "missing_session_targets": missing,
        "notes": (
            "Only Sex On Fire from the named trial corpus has an analyzed "
            "bundle. The other corpus songs (Stairway, Hotel California, "
            "Wish You Were Here, Romance de Amor, Disco of Doom, Simulated "
            "Life) have not yet been analyzed through the pipeline. The "
            "blink-182 bundle (29b31695, 'What's My Age Again?') is included "
            "as a second non-corpus song to give Phase 0 a multi-song read."
        ),
    }
    with (OUT_DIR / "corpus_coverage.json").open("w") as f:
        json.dump(coverage, f, indent=2)

    # Per-song summary CSV
    with (OUT_DIR / "per_song_summary.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "slug", "title", "session_id", "section_count",
            "chord_mean", "chord_p95", "chord_count_pairs_ge_0_8",
            "repetition_mean", "repetition_p95",
            "vocal_mean", "vocal_p95",
            "composite_mean", "composite_p95", "composite_count_pairs_ge_0_7",
            "chord_outliers", "composite_outliers",
        ])
        for p in per_song:
            st = p["stats"]
            w.writerow([
                p["slug"], p["title"], p["session_id"], p["section_count"],
                f"{st['chord']['mean']:.3f}",
                f"{st['chord']['p95']:.3f}",
                st["chord"]["count_ge_0_8"],
                f"{st['repetition']['mean']:.3f}",
                f"{st['repetition']['p95']:.3f}",
                f"{st['vocal']['mean']:.3f}",
                f"{st['vocal']['p95']:.3f}",
                f"{st['composite']['mean']:.3f}",
                f"{st['composite']['p95']:.3f}",
                st["composite"]["count_ge_0_8"],  # at the lower 0.6 bucket too
                len(p["chord_outlier_section_indices"]),
                len(p["composite_outlier_section_indices"]),
            ])

    # Full per-song dump for the markdown report to consume
    with (OUT_DIR / "per_song_findings.json").open("w") as f:
        # Drop chord_sequences from the on-disk dump to keep it compact;
        # they're already inside the *_ssm__*.json files.
        compact = []
        for p in per_song:
            q = dict(p)
            q["chord_sequences_short"] = [
                " ".join(seq[:8]) + ("…" if len(seq) > 8 else "")
                for seq in q.pop("chord_sequences", [])
            ]
            compact.append(q)
        json.dump(compact, f, indent=2)

    # Console summary
    print()
    print("=" * 78)
    print("Phase 0 — Similarity Feasibility Study (summary)")
    print("=" * 78)
    for p in per_song:
        print()
        print(f"  ▶ {p['title']}  ({p['session_id']}, {p['section_count']} sections)")
        st = p["stats"]
        print(f"    chord SSM       mean={st['chord']['mean']:.2f} "
              f"p95={st['chord']['p95']:.2f} "
              f"pairs ≥0.8: {st['chord']['count_ge_0_8']} "
              f"pairs ≥0.6: {st['chord']['count_ge_0_6']}")
        print(f"    repetition SSM  mean={st['repetition']['mean']:.2f} "
              f"p95={st['repetition']['p95']:.2f} "
              f"pairs ≥0.8: {st['repetition']['count_ge_0_8']}")
        print(f"    vocal SSM       mean={st['vocal']['mean']:.2f} "
              f"p95={st['vocal']['p95']:.2f} "
              f"pairs ≥0.8: {st['vocal']['count_ge_0_8']}")
        print(f"    composite SSM   mean={st['composite']['mean']:.2f} "
              f"p95={st['composite']['p95']:.2f} "
              f"pairs ≥0.7: {st['composite']['count_ge_0_8']}")
        print(f"    chord outliers     (row max < 0.5): {p['chord_outlier_section_indices']}")
        print(f"    composite outliers (row max < 0.5): {p['composite_outlier_section_indices']}")

    print()
    print(f"  Missing from corpus (no analyzed bundle): "
          f"{coverage['missing_from_history_json']}")
    print()
    print(f"  Outputs written to: {OUT_DIR}")


if __name__ == "__main__":
    main()
