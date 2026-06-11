#!/usr/bin/env python3
"""Catalog Integrity Gate — final pre-retrieval check.

Refuses to declare a preset catalog ready for retrieval / embedding /
ranking work unless ALL six criteria from ``RENDER_PIPELINE_RCA.md``
section 7 pass.

Criteria:
    1. Decoded-audio SHA-1 unique count >= 95 / 99.
    2. No heterogeneous decoded-SHA-1 buckets — any bucket of size >= 2
       must be single-``sound_type`` AND single-``category``.
    3. (Generate-time invariant; re-checked here at the catalog level by
       confirming every row has an ``als_path`` recorded.)
    4. Every catalog row carries the seven provenance fields.
    5. No two rows share ``decoded_audio_sha1`` from different ``preset_id``s
       (subsumed by 1+2 but reported separately for diagnosis).
    6. >= 20 random pairs from the catalog have pairwise cosine distance
       > 0.0005 on a baseline mel-spectrum feature recomputed inline. This
       threshold is calibrated to catch the collapse-to-default-patch
       signature (which produces cosine distance == 0.0 because the renders
       are byte-identical) while not false-positive flagging legitimate
       factory similarity within sonic families. Sibling bass patches that
       differ in ~70 parameter values can still collapse to cosine distance
       ~= 0.001 on a mean-pooled mel feature because the discriminating
       params (vibrato depth, filter envelope shape, glide) operate in
       time dimensions the mean obliterates — so 0.0005 is the safe floor.

Exit:
    0  - all six criteria pass; GREEN summary.
    1  - any criterion fails; RED summary itemising failures.

Writes ``<output-dir>/retrieval/catalog_integrity_gate.json``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

try:
    import librosa
except ImportError:  # pragma: no cover
    print("ERROR: librosa is required", file=sys.stderr)
    sys.exit(2)


PROVENANCE_FIELDS = (
    "preset_path",
    "adv_sha1",
    "als_path",
    "als_sha1",
    "wav_sha1",
    "decoded_audio_sha1",
    "test_sequence_name",
)

# Default thresholds — match plan and RCA section 7.
DEFAULT_UNIQUE_DECODED_MIN = 95
DEFAULT_TOTAL_PRESETS = 99
DEFAULT_PAIR_SAMPLES = 20
DEFAULT_PAIR_COSINE_DIST_MIN = 0.0005


def _mean_log_mel(y: np.ndarray, sr: int) -> np.ndarray:
    S = librosa.feature.melspectrogram(
        y=y, sr=sr, n_mels=64, fmin=20, fmax=20000
    )
    log_S = librosa.power_to_db(S + 1e-12)
    return log_S.mean(axis=1).astype(np.float32)


def _cosine_distance(u: np.ndarray, v: np.ndarray) -> float:
    nu = float(np.linalg.norm(u))
    nv = float(np.linalg.norm(v))
    if nu < 1e-12 or nv < 1e-12:
        return 1.0
    return 1.0 - float(np.dot(u, v) / (nu * nv))


def gate_1_unique_decoded(rows: List[Dict], expected_total: int, threshold: int) -> Tuple[bool, Dict]:
    """Decoded-audio SHA-1 unique count >= threshold."""
    hashes = [r["provenance"]["decoded_audio_sha1"] for r in rows
              if r["provenance"]["decoded_audio_sha1"]]
    unique = set(hashes)
    passed = len(unique) >= threshold and len(rows) >= expected_total - 4
    return passed, {
        "criterion": "decoded_audio_sha1 unique count",
        "total_rows": len(rows),
        "rows_with_hash": len(hashes),
        "unique_count": len(unique),
        "threshold": threshold,
        "passed": passed,
    }


def gate_2_no_heterogeneous_buckets(rows: List[Dict]) -> Tuple[bool, Dict]:
    """Any decoded-SHA-1 bucket of size >= 2 must be single sound_type AND category."""
    buckets: Dict[str, List[Dict]] = defaultdict(list)
    for r in rows:
        h = r["provenance"]["decoded_audio_sha1"]
        if h:
            buckets[h].append(r)

    heterogeneous = []
    for h, members in buckets.items():
        if len(members) < 2:
            continue
        sound_types = {m.get("sound_type") for m in members}
        categories = {m.get("category") for m in members}
        if len(sound_types) > 1 or len(categories) > 1:
            heterogeneous.append({
                "decoded_audio_sha1": h,
                "size": len(members),
                "sound_types": sorted(s for s in sound_types if s),
                "categories": sorted(c for c in categories if c),
                "preset_ids": [m["preset_id"] for m in members[:6]],
            })

    passed = len(heterogeneous) == 0
    return passed, {
        "criterion": "no heterogeneous decoded-SHA-1 buckets (size>=2)",
        "n_buckets_with_dupes": sum(1 for v in buckets.values() if len(v) >= 2),
        "n_heterogeneous": len(heterogeneous),
        "examples": heterogeneous[:5],
        "passed": passed,
    }


def gate_3_als_path_recorded(rows: List[Dict]) -> Tuple[bool, Dict]:
    """Every row records an als_path (proxy for 'ALS structurally validated')."""
    missing = [r["preset_id"] for r in rows
               if not r.get("provenance", {}).get("als_path")]
    passed = len(missing) == 0
    return passed, {
        "criterion": "every row has provenance.als_path",
        "missing_count": len(missing),
        "examples": missing[:5],
        "passed": passed,
    }


def gate_4_provenance_complete(rows: List[Dict]) -> Tuple[bool, Dict]:
    """Every catalog row carries the seven provenance fields (non-null)."""
    issues: List[Dict] = []
    for r in rows:
        prov = r.get("provenance", {}) or {}
        missing = [f for f in PROVENANCE_FIELDS if not prov.get(f)]
        if missing:
            issues.append({"preset_id": r["preset_id"], "missing": missing})
    passed = len(issues) == 0
    return passed, {
        "criterion": "all 7 provenance fields populated on every row",
        "rows_with_gaps": len(issues),
        "examples": issues[:5],
        "passed": passed,
    }


def gate_5_no_duplicate_decoded_hashes(rows: List[Dict]) -> Tuple[bool, Dict]:
    """No two distinct preset_ids share a decoded_audio_sha1.

    Note: subsumed by gate 1 in the strict sense (size-1 buckets => no
    duplicates), but reported separately because it isolates the specific
    failure mode that produced the previous catalog collapse.
    """
    by_hash: Dict[str, List[str]] = defaultdict(list)
    for r in rows:
        h = r["provenance"]["decoded_audio_sha1"]
        if h:
            by_hash[h].append(r["preset_id"])
    dupes = {h: ids for h, ids in by_hash.items() if len(set(ids)) > 1}
    passed = len(dupes) == 0
    return passed, {
        "criterion": "no two preset_ids share a decoded_audio_sha1",
        "n_duplicate_hashes": len(dupes),
        "examples": [
            {"decoded_audio_sha1": h, "preset_ids": sorted(set(ids))[:6]}
            for h, ids in list(dupes.items())[:5]
        ],
        "passed": passed,
    }


def gate_6_pair_cosine_diversity(
    rows: List[Dict],
    audio_root: Path,
    n_pairs: int,
    min_distance: float,
    rng_seed: int = 0,
) -> Tuple[bool, Dict]:
    """At least n_pairs random row-pairs have mel-spectrum cosine distance
    > min_distance. Recomputed inline from WAVs to be independent of
    the stored fingerprint features."""
    valid = [r for r in rows if r.get("audio_path")]
    if len(valid) < 2:
        return False, {
            "criterion": "pairwise mel-cosine diversity",
            "passed": False,
            "error": "fewer than 2 rows with audio_path",
        }
    rng = random.Random(rng_seed)
    samples = rng.sample(valid, min(40, len(valid)))
    feats: Dict[str, np.ndarray] = {}
    failed_loads: List[str] = []
    for r in samples:
        path = audio_root / Path(r["audio_path"]).name
        if not path.exists():
            path = Path(r["audio_path"])
        if not path.exists():
            failed_loads.append(r["preset_id"])
            continue
        try:
            y, sr = librosa.load(str(path), sr=22050, mono=True)
            feats[r["preset_id"]] = _mean_log_mel(y, sr)
        except Exception:
            failed_loads.append(r["preset_id"])

    ids = list(feats.keys())
    pairs_checked = 0
    pairs_passed = 0
    examples_fail: List[Dict] = []
    for _ in range(n_pairs * 4):
        if pairs_checked >= n_pairs or len(ids) < 2:
            break
        a, b = rng.sample(ids, 2)
        d = _cosine_distance(feats[a], feats[b])
        pairs_checked += 1
        if d > min_distance:
            pairs_passed += 1
        elif len(examples_fail) < 5:
            examples_fail.append(
                {"preset_a": a, "preset_b": b, "cosine_distance": d}
            )

    passed = pairs_checked >= n_pairs and pairs_passed == pairs_checked
    return passed, {
        "criterion": "pairwise mel-cosine diversity",
        "n_pairs_checked": pairs_checked,
        "n_pairs_passed": pairs_passed,
        "min_distance": min_distance,
        "failed_loads": failed_loads[:5],
        "examples_below_threshold": examples_fail,
        "passed": passed,
    }


def run_gate(
    catalog_path: Path,
    audio_root: Path,
    expected_total: int,
    unique_threshold: int,
    n_pairs: int,
    min_pair_distance: float,
) -> Tuple[bool, Dict]:
    data = json.loads(catalog_path.read_text())
    rows: List[Dict] = data.get("presets", [])

    # Normalise rows so legacy catalogs (no provenance block) still
    # pass through the gates — they'll simply fail criteria 1/2/4/5
    # cleanly instead of raising KeyError.
    for r in rows:
        if "provenance" not in r or r["provenance"] is None:
            r["provenance"] = {f: None for f in PROVENANCE_FIELDS}
        else:
            for f in PROVENANCE_FIELDS:
                r["provenance"].setdefault(f, None)

    results = []
    results.append(gate_1_unique_decoded(rows, expected_total, unique_threshold))
    results.append(gate_2_no_heterogeneous_buckets(rows))
    results.append(gate_3_als_path_recorded(rows))
    results.append(gate_4_provenance_complete(rows))
    results.append(gate_5_no_duplicate_decoded_hashes(rows))
    results.append(
        gate_6_pair_cosine_diversity(
            rows, audio_root, n_pairs, min_pair_distance
        )
    )

    passed = all(p for p, _ in results)
    report = {
        "catalog_path": str(catalog_path),
        "audio_root": str(audio_root),
        "total_rows": len(rows),
        "passed": passed,
        "criteria": [detail for _, detail in results],
    }
    return passed, report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--catalog",
        type=Path,
        default=Path("preset_catalog_output/catalog/catalog_analog.json"),
        help="path to catalog JSON",
    )
    parser.add_argument(
        "--audio-root",
        type=Path,
        default=Path("preset_catalog_output/audio"),
        help="directory containing rendered WAV files",
    )
    parser.add_argument(
        "--expected-total",
        type=int,
        default=DEFAULT_TOTAL_PRESETS,
    )
    parser.add_argument(
        "--unique-threshold",
        type=int,
        default=DEFAULT_UNIQUE_DECODED_MIN,
    )
    parser.add_argument(
        "--n-pairs",
        type=int,
        default=DEFAULT_PAIR_SAMPLES,
    )
    parser.add_argument(
        "--min-pair-distance",
        type=float,
        default=DEFAULT_PAIR_COSINE_DIST_MIN,
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="JSON output path (default: <catalog parent>/../retrieval/catalog_integrity_gate.json)",
    )
    args = parser.parse_args()

    if not args.catalog.exists():
        print(f"ERROR: catalog not found: {args.catalog}", file=sys.stderr)
        return 2

    passed, report = run_gate(
        args.catalog,
        args.audio_root,
        args.expected_total,
        args.unique_threshold,
        args.n_pairs,
        args.min_pair_distance,
    )

    # Console summary.
    print(f"Catalog: {args.catalog}")
    print(f"Rows:    {report['total_rows']}")
    print()
    for c in report["criteria"]:
        tag = "PASS" if c["passed"] else "FAIL"
        print(f"  [{tag}]  {c['criterion']}")
        # Surface key counts inline.
        for k in (
            "unique_count",
            "threshold",
            "n_heterogeneous",
            "missing_count",
            "rows_with_gaps",
            "n_duplicate_hashes",
            "n_pairs_checked",
            "n_pairs_passed",
        ):
            if k in c:
                print(f"            {k} = {c[k]}")

    print()
    if passed:
        print("CATALOG INTEGRITY GATE: PASS")
    else:
        print("CATALOG INTEGRITY GATE: FAIL — catalog is not ready for retrieval.")

    report_path = args.report or (
        args.catalog.parent.parent / "retrieval" / "catalog_integrity_gate.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2))
    print(f"Report -> {report_path}")

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
