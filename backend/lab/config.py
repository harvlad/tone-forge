"""Central paths, constants and validated reference numbers for the lab."""
from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Roots
# ---------------------------------------------------------------------------
BACKEND_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_ROOT.parent

# All lab state lives here.  Override with TONEFORGE_LAB_DATA for tests.
LAB_DATA = Path(os.environ.get("TONEFORGE_LAB_DATA", BACKEND_ROOT / "lab_data"))

CORPUS_DIR = LAB_DATA / "corpus"
GT_DIR = LAB_DATA / "gt"
PREDICTIONS_DIR = LAB_DATA / "predictions"
MATCHES_DIR = LAB_DATA / "matches"
FEATURES_DIR = LAB_DATA / "features"
EXPERIMENTS_DIR = LAB_DATA / "experiments"
MODELS_DIR = LAB_DATA / "models"
TIERS_DIR = LAB_DATA / "tiers"
JOBS_DIR = LAB_DATA / "jobs"
LEGACY_DIR = LAB_DATA / "legacy"
REPORTS_DIR = LAB_DATA / "reports"

MANIFEST_PATH = CORPUS_DIR / "manifest.parquet"
EXPERIMENTS_LOG = EXPERIMENTS_DIR / "experiments.jsonl"
MODEL_REGISTRY_PATH = MODELS_DIR / "registry.json"
THROUGHPUT_PATH = MODELS_DIR / "throughput.json"

# ---------------------------------------------------------------------------
# Datasets
# ---------------------------------------------------------------------------
# dataset name -> root dir.  Stem identity never embeds these absolute paths.
DATASETS = {
    "slakh2100": REPO_ROOT / "datasets" / "slakh2100_flac_redux",
    "babyslakh": REPO_ROOT / "datasets" / "babyslakh_16k",
}

# Slakh native splits.  Convention:
#   train      -> training only
#   validation -> development set (all tuning happens here)
#   test       -> FROZEN HELD-OUT (never tune against it)
SLAKH_SPLITS = ("train", "validation", "test")

# ---------------------------------------------------------------------------
# Instrument classes — GM program mapping used by the VALIDATED benchmark
# (mt3_vs_basic_pitch.py).  Do not edit: changing this silently changes
# which stems belong to the validated 100-stem eval set.
# ---------------------------------------------------------------------------
GM_CLASSES = {
    (1, 8): "Piano", (9, 16): "Chromatic Percussion",
    (17, 24): "Organ", (25, 32): "Guitar", (33, 40): "Bass",
    (41, 48): "Strings", (49, 56): "Ensemble",
    (57, 64): "Brass", (65, 72): "Reed", (73, 80): "Pipe",
    (81, 88): "Synth Lead", (89, 96): "Synth Pad",
}


def get_inst_class(program: int) -> str:
    """GM program number (0-based) -> class name.  Verbatim port of the
    validated benchmark's mapping."""
    for (lo, hi), name in GM_CLASSES.items():
        if lo <= program + 1 <= hi:
            return name
    return "Unknown"


# ---------------------------------------------------------------------------
# Dataset tiers (stems per instrument class; None = no cap)
# ---------------------------------------------------------------------------
TIER_SIZES = {
    "sanity": 3,
    "smoke": 8,
    "scout": 40,
    "benchmark": 150,
    "full": None,
}
TIER_ORDER = ["sanity", "smoke", "scout", "benchmark", "full", "heldout"]
DEFAULT_TIER_SEED = 42

# ---------------------------------------------------------------------------
# Validated reference numbers (100-stem validation set, 100ms onset
# tolerance, official Basic Pitch predict(), MT3 mt3_pytorch).
# Used by `lab validate` to verify the harness reproduces history.
# ---------------------------------------------------------------------------
VALIDATED_REFERENCE = {
    "onset_tolerance": 0.1,
    "n_stems": 100,
    "basic_pitch": {"f1": 0.313, "recall": 0.423, "precision": 0.249,
                    "octave_error_rate": 0.163},
    "mt3": {"f1": 0.132, "recall": 0.110, "precision": 0.165,
            "octave_error_rate": 0.035},
    "oracle_recall": 0.472,
    "per_class": {
        "Bass": {"basic_pitch": {"recall": 0.188, "octave_error_rate": 0.425},
                 "mt3": {"recall": 0.427, "octave_error_rate": 0.025}},
        "Synth Lead": {"basic_pitch": {"recall": 0.009}, "mt3": {"recall": 0.105}},
        "Piano": {"basic_pitch": {"recall": 0.577}, "mt3": {"recall": 0.097}},
        "Guitar": {"basic_pitch": {"recall": 0.501}, "mt3": {"recall": 0.081}},
        "Reed": {"basic_pitch": {"recall": 0.568}, "mt3": {"recall": 0.010}},
        "Brass": {"basic_pitch": {"recall": 0.342}, "mt3": {"recall": 0.057}},
    },
}
# Stems in validated100 that the ORIGINAL run silently dropped (its script
# `continue`d on per-stem inference/GT errors).  Identified by exact
# subset-sum against the historical per-class n_gt table: without these
# three, per-class GT note counts match comparison_results.json exactly
# (34,803 notes).  `lab validate` excludes them when comparing to the
# reference; regular benchmarks include them.
VALIDATED_EXCLUDED_STEMS = [
    "slakh2100:Track01529:S02",  # Guitar, 3852 GT notes
    "slakh2100:Track01594:S06",  # Brass, 70
    "slakh2100:Track01576:S10",  # Strings, 30
]

# Reproduction tolerance (absolute) before `lab validate` fails hard.
VALIDATE_ABS_TOLERANCE = 0.005


def ensure_dirs() -> None:
    for d in (CORPUS_DIR, GT_DIR, PREDICTIONS_DIR, MATCHES_DIR, FEATURES_DIR,
              EXPERIMENTS_DIR, MODELS_DIR, TIERS_DIR, JOBS_DIR, LEGACY_DIR,
              REPORTS_DIR):
        d.mkdir(parents=True, exist_ok=True)
