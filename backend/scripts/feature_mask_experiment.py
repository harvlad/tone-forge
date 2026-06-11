"""Phase 2 feature-mask validation experiment (controlled, offline).

Hypothesis under test
---------------------
Three of the eight catalog features (`attack_ms`, `decay_ms`, `pitch_stability`)
are unreliable on polyphonic content. They are dominating z-norm distance
on real-song query stems and burying the discriminating features.

Method
------
1.  Read the production catalog fingerprints from disk and compute
    (mean, std) the same way ``guitar_catalog._get_catalog`` does.
2.  For each test query, compute:
      - raw z-norm L2 distance to every chain (production math)
      - masked z-norm L2 distance (3 axes zeroed out)
    plus the per-feature squared contribution so we can see WHY.
3.  Confidence = ``exp(-distance / 14)``  (DISTANCE_TAU)
    Margin     = ``(d_second - d_top) / d_top``

This script READS production code constants but DOES NOT modify any
production file. It writes a report to PHASE2_FEATURE_MASK_REPORT.md.
"""
from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np

# Production constants — mirrored from tone_forge/tone/guitar_catalog.py
# so this experiment uses identical math without importing the module
# (avoids triggering caches / side-effects in a running server).
FEATURE_KEYS: Tuple[str, ...] = (
    "brightness",
    "warmth",
    "air",
    "attack_ms",
    "decay_ms",
    "sustain_ratio",
    "harmonic_ratio",
    "pitch_stability",
)
DISTANCE_TAU: float = 14.0
STD_FLOOR: float = 1e-3

# Mask under test: zero out these three axes in (query - catalog) / std
MASKED_FEATURES: Tuple[str, ...] = (
    "attack_ms",
    "decay_ms",
    "pitch_stability",
)
MASK_INDICES = [FEATURE_KEYS.index(k) for k in MASKED_FEATURES]

CHAINS_ROOT = Path(__file__).resolve().parent.parent / "tone_forge" / "monitor" / "chains"
REPO_ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = REPO_ROOT / "PHASE2_FEATURE_MASK_REPORT.md"


# ---------------------------------------------------------------------------
# Catalog loading (mirrors guitar_catalog._get_catalog)
# ---------------------------------------------------------------------------

def load_catalog() -> Tuple[List[str], np.ndarray, np.ndarray, np.ndarray]:
    """Return (chain_ids, vectors NxD, mean D, std D)."""
    entries: List[Tuple[str, np.ndarray]] = []
    for fp in sorted(CHAINS_ROOT.glob("tfc.*.fingerprint.json")):
        chain_id = fp.name.replace(".fingerprint.json", "")
        data = json.loads(fp.read_text())
        feats = data["features"]
        vec = np.array([float(feats[k]) for k in FEATURE_KEYS], dtype=np.float64)
        entries.append((chain_id, vec))
    if not entries:
        raise RuntimeError(f"No fingerprints found under {CHAINS_ROOT}")
    chain_ids = [e[0] for e in entries]
    vectors = np.stack([e[1] for e in entries], axis=0)
    mean = vectors.mean(axis=0)
    std = vectors.std(axis=0)
    std = np.where(std < STD_FLOOR, STD_FLOOR, std)
    return chain_ids, vectors, mean, std


# ---------------------------------------------------------------------------
# Distance / ranking
# ---------------------------------------------------------------------------

def rank_query(
    query: np.ndarray,
    chain_ids: List[str],
    catalog: np.ndarray,
    std: np.ndarray,
    *,
    mask_indices: List[int] = (),
) -> Dict:
    """Compute ranking + per-feature contribution.

    Returns dict with:
      ranking: list of (chain_id, distance, per_feature_contrib dict)
      top_chain, top_distance, second_distance
      confidence, margin
    """
    mask = np.ones_like(std)
    for i in mask_indices:
        mask[i] = 0.0

    ranking = []
    for i, cid in enumerate(chain_ids):
        delta = (query - catalog[i]) / std
        delta = delta * mask
        contrib_sq = delta ** 2  # per-axis squared z-distance
        d = float(np.linalg.norm(delta))
        contrib = {k: float(contrib_sq[j]) for j, k in enumerate(FEATURE_KEYS)}
        ranking.append((cid, d, contrib))

    ranking.sort(key=lambda r: r[1])
    distances = [r[1] for r in ranking]

    top = ranking[0]
    second = ranking[1] if len(ranking) > 1 else None

    conf = math.exp(-top[1] / DISTANCE_TAU) if math.isfinite(top[1]) else 0.0
    conf = max(0.0, min(1.0, conf))
    margin = None
    if second is not None and top[1] > 0:
        margin = (second[1] - top[1]) / top[1]

    return {
        "ranking": ranking,
        "top_chain": top[0],
        "top_distance": top[1],
        "second_distance": second[1] if second else None,
        "confidence": conf,
        "margin": margin,
    }


# ---------------------------------------------------------------------------
# Test queries
# ---------------------------------------------------------------------------

def extract_query_from_wav(wav_path: Path) -> np.ndarray:
    """Run the production extractor on a WAV file to get an 8-feature vector.

    Imports the production extractor read-only (no state mutation).
    """
    # Append repo root so we can import the package
    repo_backend = Path(__file__).resolve().parent.parent
    if str(repo_backend) not in sys.path:
        sys.path.insert(0, str(repo_backend))
    from tone_forge.tone import guitar_catalog as gc
    result = gc._extract_query_fingerprint(wav_path)
    if result is None:
        raise RuntimeError(f"extractor returned None for {wav_path}")
    # Production now returns (vector, validity); experiment harness
    # ignores the validity for the unmasked baseline since masking is
    # applied externally here.
    vec, _ = result
    return np.asarray(vec, dtype=np.float64)


def load_query_from_history(history_id: str) -> Tuple[str, np.ndarray]:
    """Pull (title, query_vector) from a saved history result file in /tmp."""
    p = Path(f"/tmp/hist_{history_id}.json")
    if not p.is_file():
        raise FileNotFoundError(p)
    d = json.loads(p.read_text())
    title = d.get("name", history_id)
    qv = d["result"]["tone"]["debug"]["query_vector"]
    return title, np.asarray(qv, dtype=np.float64)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def format_ranking_line(rank_idx: int, cid: str, d: float, conf: float = None) -> str:
    if conf is not None:
        return f"  {rank_idx}. {cid:<25}  d={d:8.4f}  conf={conf:.3f}"
    return f"  {rank_idx}. {cid:<25}  d={d:8.4f}"


def format_contrib_table(contrib: Dict[str, float]) -> str:
    total = sum(contrib.values())
    lines = []
    lines.append(f"      {'feature':<18} {'sq-z':>10}  {'% of d²':>8}")
    for k in FEATURE_KEYS:
        v = contrib[k]
        pct = (v / total * 100.0) if total > 0 else 0.0
        marker = " *" if k in MASKED_FEATURES else "  "
        lines.append(f"     {marker}{k:<18} {v:>10.4f}  {pct:>7.1f}%")
    return "\n".join(lines)


def run_one(label: str, query: np.ndarray, chain_ids, catalog, std, expected: str = None) -> List[str]:
    out = []
    out.append(f"\n### {label}")
    if expected:
        out.append(f"_Expected top-1_: **{expected}**")
    out.append("")
    out.append(f"```")
    out.append(f"query_vector = {np.array2string(query, precision=4, suppress_small=True)}")
    out.append(f"```")

    full = rank_query(query, chain_ids, catalog, std)
    masked = rank_query(query, chain_ids, catalog, std, mask_indices=MASK_INDICES)

    def block(title, res, mode):
        lines = []
        lines.append(f"\n**{title}** (mode={mode})")
        lines.append("```")
        for i, (cid, d, _contrib) in enumerate(res["ranking"]):
            conf_i = math.exp(-d / DISTANCE_TAU)
            lines.append(format_ranking_line(i + 1, cid, d, conf=conf_i))
        m = res["margin"]
        lines.append(f"  → confidence={res['confidence']:.4f}  margin={'n/a' if m is None else f'{m:.4f}'}")
        lines.append("```")
        # show top contribution breakdown
        top_contrib = res["ranking"][0][2]
        lines.append("\n_Per-feature contribution to top-1 d² (* = masked):_")
        lines.append("```")
        lines.append(format_contrib_table(top_contrib))
        lines.append("```")
        return lines

    out.extend(block("BEFORE (full 8 features)", full, "production"))
    out.extend(block(f"AFTER  (masked {len(MASKED_FEATURES)} features)", masked, "experiment"))

    # quick diff summary
    out.append("\n_Δ summary_:")
    out.append("```")
    bf_top, af_top = full["top_chain"], masked["top_chain"]
    out.append(f"  top-1: {bf_top}  →  {af_top}  {'(CHANGED)' if bf_top != af_top else ''}")
    out.append(f"  conf:  {full['confidence']:.4f}  →  {masked['confidence']:.4f}")
    out.append(f"  margin: {full['margin']}  →  {masked['margin']}")
    if expected:
        bf_rank = [r[0] for r in full["ranking"]].index(expected) + 1
        af_rank = [r[0] for r in masked["ranking"]].index(expected) + 1
        out.append(f"  expected ({expected}): rank {bf_rank} → rank {af_rank}")
    out.append("```")
    return out, full, masked


def main():
    chain_ids, catalog, mean, std = load_catalog()
    print(f"Loaded catalog: {chain_ids}")
    print(f"  mean: {mean}")
    print(f"  std : {std}")

    lines: List[str] = []
    lines.append("# Phase 2 Feature-Mask Validation Experiment")
    lines.append("")
    lines.append("**Hypothesis under test**: `attack_ms`, `decay_ms`, and `pitch_stability`")
    lines.append("are unreliable on polyphonic content and are dominating z-norm distance,")
    lines.append("burying the discriminating features (brightness, warmth, air, sustain_ratio,")
    lines.append("harmonic_ratio).")
    lines.append("")
    lines.append(f"**Method**: re-rank known queries with these 3 features zeroed out in the")
    lines.append(f"z-norm vector. Production code unchanged.")
    lines.append("")
    lines.append(f"**Catalog**: {len(chain_ids)} chains from `tone_forge/monitor/chains/`")
    lines.append(f"```")
    for cid, vec in zip(chain_ids, catalog):
        lines.append(f"  {cid:<25} {np.array2string(vec, precision=4, suppress_small=True)}")
    lines.append(f"  mean  {' ' * 25} {np.array2string(mean, precision=4, suppress_small=True)}")
    lines.append(f"  std   {' ' * 25} {np.array2string(std, precision=4, suppress_small=True)}")
    lines.append(f"```")
    lines.append("")
    lines.append(f"**Masked features**: {', '.join(MASKED_FEATURES)}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Test queries")

    summary_rows = []

    # ---- IDENTITY SANITY CHECKS ----
    # Each chain queried against the catalog should rank itself #1.
    lines.append("\n### Group A — Identity sanity checks (catalog → itself)")
    lines.append("")
    lines.append("Each WAV used to build a fingerprint is re-extracted and ranked. ")
    lines.append("These should ALL rank themselves #1 in both modes. If they don't, ")
    lines.append("the extractor is non-deterministic or the catalog is corrupt.")

    for cid in chain_ids:
        wav = CHAINS_ROOT / f"{cid}.wav"
        if not wav.is_file():
            continue
        try:
            qv = extract_query_from_wav(wav)
        except Exception as exc:
            lines.append(f"\n  _{cid}_: extraction FAILED ({exc})")
            continue
        block, full, masked = run_one(f"A. Identity: {cid}", qv, chain_ids, catalog, std, expected=cid)
        lines.extend(block)
        summary_rows.append((
            f"identity:{cid}",
            cid,
            full["top_chain"],
            masked["top_chain"],
            full["confidence"],
            masked["confidence"],
        ))

    # ---- REAL-SONG QUERIES (saturated ADSR) ----
    lines.append("\n### Group B — Real songs (polyphonic queries)")
    real_cases = [
        ("8031817b", "ALCEST — Flamme Jumelle", "tfc.ambient"),
        ("17e04231", "ALCEST — Kodama",          "tfc.ambient"),
        ("5f2fc178", "ALCEST — Flamme Jumelle (early run)", "tfc.ambient"),
    ]
    for hid, label, expected in real_cases:
        try:
            title, qv = load_query_from_history(hid)
        except FileNotFoundError:
            lines.append(f"\n  _{label} ({hid})_: no history file in /tmp, skipping")
            continue
        block, full, masked = run_one(f"B. {label}", qv, chain_ids, catalog, std, expected=expected)
        lines.extend(block)
        summary_rows.append((
            label,
            expected,
            full["top_chain"],
            masked["top_chain"],
            full["confidence"],
            masked["confidence"],
        ))

    # ---- CLEAN-DI CONTROL ----
    lines.append("\n### Group C — Clean DI control (Dry_Guitar_sustain.wav)")
    lines.append("")
    lines.append("This is the source DI used to render the catalog. It contains no amp/cab ")
    lines.append("processing. Closest chain should be `tfc.clean_strat` (the least-processed ")
    lines.append("entry in the catalog).")
    di_path = Path("/Users/mattharvey/Desktop/Dry_Guitar_sustain.wav")
    if di_path.is_file():
        try:
            qv = extract_query_from_wav(di_path)
            block, full, masked = run_one("C. Dry DI (Dry_Guitar_sustain.wav)", qv, chain_ids, catalog, std, expected="tfc.clean_strat")
            lines.extend(block)
            summary_rows.append((
                "dry_di",
                "tfc.clean_strat",
                full["top_chain"],
                masked["top_chain"],
                full["confidence"],
                masked["confidence"],
            ))
        except Exception as exc:
            lines.append(f"\n  Dry DI extraction FAILED ({exc})")
    else:
        lines.append(f"\n  Dry DI file not found at {di_path}")

    # ---- SUMMARY ----
    lines.append("\n---\n")
    lines.append("## Summary table")
    lines.append("")
    lines.append("| case | expected | full top-1 | masked top-1 | full conf | masked conf | change |")
    lines.append("|---|---|---|---|---|---|---|")
    for label, expected, bf, af, bc, ac in summary_rows:
        change_marks = []
        if af == expected and bf != expected:
            change_marks.append("✓ now correct")
        elif bf == expected and af != expected:
            change_marks.append("✗ regression")
        elif af != bf:
            change_marks.append("changed")
        if ac > bc * 1.5 and bc < 0.2:
            change_marks.append("conf rose")
        elif ac < bc * 0.5 and ac < 0.1:
            change_marks.append("conf fell")
        change_str = ", ".join(change_marks) if change_marks else "—"
        lines.append(f"| {label} | {expected} | {bf} | {af} | {bc:.3f} | {ac:.3f} | {change_str} |")

    # ---- VERDICT ----
    lines.append("\n## Verdict")
    lines.append("")
    pass_criteria = []
    fail_criteria = []
    # Pass: identity checks all OK in BOTH modes
    identity_ok = all(
        bf == ex and af == ex
        for (label, ex, bf, af, _, _) in summary_rows
        if label.startswith("identity:")
    )
    pass_criteria.append(("Identity checks pass in both modes", identity_ok))

    # Pass: ambient rises on Alcest cases after masking
    ambient_rose = any(
        af == "tfc.ambient" and bf != "tfc.ambient"
        for (label, ex, bf, af, _, _) in summary_rows
        if "Alcest" in label or "ALCEST" in label
    )
    pass_criteria.append(("Ambient becomes top-1 on at least one shoegaze case", ambient_rose))

    # Pass: confidence on real-song queries materially improves
    real_conf_rose = any(
        ac >= 0.10 and bc < 0.05
        for (label, ex, bf, af, bc, ac) in summary_rows
        if "Alcest" in label or "ALCEST" in label
    )
    pass_criteria.append(("Real-song confidence rises out of the 0.00 floor", real_conf_rose))

    # Fail: clean DI baseline broke
    clean_di_row = next((row for row in summary_rows if row[0] == "dry_di"), None)
    if clean_di_row is not None:
        clean_kept = clean_di_row[3] == clean_di_row[1]
        pass_criteria.append(("Clean DI still matches clean_strat after masking", clean_kept))

    lines.append("**Pass criteria**:")
    for desc, ok in pass_criteria:
        lines.append(f"- [{'x' if ok else ' '}] {desc}")
    lines.append("")
    passes = sum(1 for _, ok in pass_criteria if ok)
    total = len(pass_criteria)
    lines.append(f"**Result**: {passes}/{total} pass criteria met.")
    if passes == total:
        lines.append("")
        lines.append("→ **HYPOTHESIS SUPPORTED**: feature reliability is the dominant issue. ")
        lines.append("Recommend follow-up to (1) repair the broken features in the extractor or ")
        lines.append("(2) drop them from the production distance computation.")
    elif passes >= total - 1:
        lines.append("")
        lines.append("→ **HYPOTHESIS PARTIALLY SUPPORTED**: masking helps but is not sufficient. ")
        lines.append("There may be additional issues (catalog content mismatch, calibration, etc.).")
    else:
        lines.append("")
        lines.append("→ **HYPOTHESIS REJECTED**: masking the 3 features does not produce ")
        lines.append("meaningfully better ranking. The extractor ceiling is real and broader ")
        lines.append("than just these 3 features. Stop and document.")

    REPORT_PATH.write_text("\n".join(lines))
    print(f"\nWrote report to {REPORT_PATH}")
    print(f"\nSummary ({passes}/{total} criteria met):")
    for desc, ok in pass_criteria:
        print(f"  [{'x' if ok else ' '}] {desc}")


if __name__ == "__main__":
    main()
