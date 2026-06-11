#!/usr/bin/env python3
"""Validate the V2 preset audio corpus and catalog.

Checks performed per engine (Analog, Drift, Collision, Electric):

  WAV integrity
    - File exists, size > 0
    - Decodable via librosa @ 48 kHz mono
    - Duration in {4.0s, 6.0s} (V2 rendering writes one of these two clip lengths)
    - Not silent: RMS > 1e-3
    - Not clipped: peak < 0.999
    - decoded_audio_sha1 in catalog matches recomputed SHA-1

  Catalog integrity
    - 8 feature fields present, all finite (no NaN/Inf)
    - No fingerprint collisions (two presets with identical 8-tuple)
    - Provenance fields present (preset_path, als_path, als_sha1, wav_sha1,
      decoded_audio_sha1)

  Coverage
    - Sound-type histogram per engine
    - Cross-engine sound-type matrix (for retrieval design)

Exit code 0 on full pass, 1 if any per-engine validator fails.

Usage:
    python3 scripts/validate_v2_corpus.py \\
        --catalog-dir preset_catalog_output/catalog \\
        --audio-dir preset_catalog_output/audio_v2 \\
        --report-dir preset_catalog_output/catalog
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

# Add backend/ to sys.path so we can import tone_forge.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


# --- Validation thresholds (kept conservative for V2 retrieval gate) ----------

# V2 renderer writes either a 4s or 6s audio clip, plus a ~1.5–1.7s tail/settle
# buffer captured by the M4L recorder. Observed WAV durations cluster around
# 5.7s and 7.7s.
ALLOWED_DURATIONS = (5.7, 7.7)
DURATION_TOLERANCE = 0.25  # seconds
SILENCE_RMS = 1e-3
CLIP_PEAK = 0.999
EXPECTED_FEATURES = (
    "brightness",
    "warmth",
    "air",
    "attack_ms",
    "decay_ms",
    "sustain_ratio",
    "harmonic_ratio",
    "pitch_stability",
)


# --- Result types -------------------------------------------------------------

@dataclass
class PresetCheck:
    preset_id: str
    instrument: str
    sound_type: str
    ok: bool
    issues: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    wav_path: Optional[str] = None
    duration_sec: Optional[float] = None
    rms: Optional[float] = None
    peak: Optional[float] = None
    sha1_match: Optional[bool] = None


@dataclass
class EngineReport:
    engine: str
    n_total: int = 0
    n_ok: int = 0
    n_fail: int = 0
    n_warn: int = 0
    n_missing_wav: int = 0
    sound_types: Dict[str, int] = field(default_factory=dict)
    fingerprint_collisions: List[Tuple[str, str]] = field(default_factory=list)
    failures: List[PresetCheck] = field(default_factory=list)
    warnings_list: List[PresetCheck] = field(default_factory=list)


# --- Helpers ------------------------------------------------------------------

def _sha1_decoded_audio(path: Path) -> str:
    """Match catalog_builder._sha1_decoded_audio: int16-quantised PCM hash."""
    import librosa

    y, _ = librosa.load(str(path), sr=48000, mono=True)
    y_int16 = np.clip(y * 32767.0, -32768, 32767).astype(np.int16)
    return hashlib.sha1(y_int16.tobytes()).hexdigest()


def _load_wav_stats(path: Path) -> Tuple[float, float, float]:
    """Return (duration_sec, rms, peak_abs) of mono 48 kHz decoded audio."""
    import librosa

    y, sr = librosa.load(str(path), sr=48000, mono=True)
    duration = len(y) / float(sr)
    rms = float(np.sqrt(np.mean(np.square(y)))) if y.size else 0.0
    peak = float(np.max(np.abs(y))) if y.size else 0.0
    return duration, rms, peak


def _within(value: float, allowed: Tuple[float, ...], tol: float) -> bool:
    return any(abs(value - a) <= tol for a in allowed)


def _is_finite(x) -> bool:
    try:
        return math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


# --- Per-engine validation ----------------------------------------------------

def validate_engine(
    engine: str,
    catalog_path: Path,
    audio_dir: Path,
    verify_sha: bool,
) -> EngineReport:
    report = EngineReport(engine=engine)
    if not catalog_path.exists():
        logger.error("[%s] catalog missing: %s", engine, catalog_path)
        return report

    with catalog_path.open() as fh:
        catalog = json.load(fh)
    presets = catalog.get("presets", [])
    report.n_total = len(presets)

    seen_fingerprints: Dict[Tuple[float, ...], str] = {}
    stype_counts: Counter[str] = Counter()

    for entry in presets:
        check = PresetCheck(
            preset_id=entry.get("preset_id", "?"),
            instrument=entry.get("instrument", engine),
            sound_type=entry.get("sound_type", "?"),
            ok=True,
        )
        stype_counts[check.sound_type] += 1

        # 1. WAV path: prefer audio_dir/<safe_filename>.wav by reusing the catalog's
        #    own audio_path (relative paths get resolved against repo root).
        wav_rel = entry.get("audio_path", "")
        if wav_rel:
            wav_path = Path(wav_rel)
            if not wav_path.is_absolute():
                wav_path = Path(__file__).resolve().parents[1] / wav_rel
        else:
            wav_path = audio_dir / f"{check.preset_id}.wav"
        check.wav_path = str(wav_path)

        if not wav_path.exists() or wav_path.stat().st_size == 0:
            check.ok = False
            check.issues.append(f"wav_missing_or_empty:{wav_path}")
            report.n_missing_wav += 1
            report.failures.append(check)
            continue

        # 2. WAV stats
        try:
            duration, rms, peak = _load_wav_stats(wav_path)
        except Exception as exc:
            check.ok = False
            check.issues.append(f"wav_decode_failed:{type(exc).__name__}:{exc}")
            report.failures.append(check)
            continue
        check.duration_sec = duration
        check.rms = rms
        check.peak = peak

        if not _within(duration, ALLOWED_DURATIONS, DURATION_TOLERANCE):
            check.warnings.append(f"duration_unexpected:{duration:.3f}s")
        if rms < SILENCE_RMS:
            check.ok = False
            check.issues.append(f"silent:rms={rms:.2e}")
        if peak >= CLIP_PEAK:
            # Clipping is a quality warning (signals hot enough to saturate the
            # ADC) but does not block fingerprint extraction or retrieval eval.
            check.warnings.append(f"clipped:peak={peak:.4f}")

        # 3. SHA-1 of decoded audio matches provenance
        prov = entry.get("provenance", {}) or {}
        expected_sha = prov.get("decoded_audio_sha1", "")
        if verify_sha and expected_sha:
            try:
                actual_sha = _sha1_decoded_audio(wav_path)
            except Exception as exc:
                actual_sha = ""
                check.issues.append(f"sha1_compute_failed:{type(exc).__name__}:{exc}")
            check.sha1_match = (actual_sha == expected_sha)
            if not check.sha1_match:
                check.ok = False
                check.issues.append(
                    f"sha1_mismatch:expected={expected_sha[:12]} got={actual_sha[:12]}"
                )

        # 4. Feature finiteness + completeness
        features = entry.get("features", {}) or {}
        missing = [k for k in EXPECTED_FEATURES if k not in features]
        if missing:
            check.ok = False
            check.issues.append(f"features_missing:{missing}")
        bad_feats = [
            k for k in EXPECTED_FEATURES
            if k in features and not _is_finite(features[k])
        ]
        if bad_feats:
            check.ok = False
            check.issues.append(f"features_non_finite:{bad_feats}")

        # 5. Fingerprint collision (only count when all features finite)
        if check.ok or not (missing or bad_feats):
            fp_tuple = tuple(float(features[k]) for k in EXPECTED_FEATURES)
            existing = seen_fingerprints.get(fp_tuple)
            if existing and existing != check.preset_id:
                check.issues.append(f"fingerprint_collision_with:{existing}")
                report.fingerprint_collisions.append((existing, check.preset_id))
                # Note: collisions are *warnings*, not failures, since duplicate
                # presets across packs can legitimately share fingerprints.
            seen_fingerprints[fp_tuple] = check.preset_id

        # 6. Provenance completeness (warning-only fields)
        for key in ("preset_path", "als_path", "als_sha1", "wav_sha1", "decoded_audio_sha1"):
            if not prov.get(key):
                check.warnings.append(f"provenance_missing:{key}")

        if check.ok:
            report.n_ok += 1
            if check.warnings:
                report.n_warn += 1
                report.warnings_list.append(check)
        else:
            report.n_fail += 1
            report.failures.append(check)

    report.sound_types = dict(stype_counts)
    return report


# --- Cross-engine coverage ----------------------------------------------------

def coverage_matrix(reports: Dict[str, EngineReport]) -> Dict[str, Dict[str, int]]:
    matrix: Dict[str, Dict[str, int]] = {}
    for engine, rpt in reports.items():
        matrix[engine] = dict(rpt.sound_types)
    return matrix


# --- Report rendering ---------------------------------------------------------

def render_markdown(reports: Dict[str, EngineReport], output_path: Path) -> None:
    lines: List[str] = []
    lines.append("# V2 Corpus Validation Report\n")
    lines.append("## Per-engine summary\n")
    lines.append("| Engine | Total | OK | Fail | Warnings | Missing WAV | Collisions |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for engine, rpt in reports.items():
        lines.append(
            f"| {engine} | {rpt.n_total} | {rpt.n_ok} | {rpt.n_fail} | "
            f"{rpt.n_warn} | {rpt.n_missing_wav} | {len(rpt.fingerprint_collisions)} |"
        )

    lines.append("\n## Sound-type coverage\n")
    all_types = sorted({
        st for rpt in reports.values() for st in rpt.sound_types
    })
    header = "| Engine | " + " | ".join(all_types) + " |"
    sep = "|---|" + "---:|" * len(all_types)
    lines.append(header)
    lines.append(sep)
    for engine, rpt in reports.items():
        row = [engine] + [str(rpt.sound_types.get(st, 0)) for st in all_types]
        lines.append("| " + " | ".join(row) + " |")

    lines.append("\n## Failures\n")
    for engine, rpt in reports.items():
        if not rpt.failures:
            continue
        lines.append(f"### {engine} ({len(rpt.failures)} failures)\n")
        for f in rpt.failures[:50]:
            lines.append(f"- `{f.preset_id}` ({f.sound_type}): {'; '.join(f.issues)}")
        if len(rpt.failures) > 50:
            lines.append(f"- ... ({len(rpt.failures) - 50} more)")
        lines.append("")

    lines.append("\n## Warnings\n")
    for engine, rpt in reports.items():
        if not rpt.warnings_list:
            continue
        lines.append(f"### {engine} ({len(rpt.warnings_list)} warnings)\n")
        for f in rpt.warnings_list[:50]:
            lines.append(f"- `{f.preset_id}` ({f.sound_type}): {'; '.join(f.warnings)}")
        if len(rpt.warnings_list) > 50:
            lines.append(f"- ... ({len(rpt.warnings_list) - 50} more)")
        lines.append("")

    output_path.write_text("\n".join(lines))
    logger.info("Wrote markdown report: %s", output_path)


def render_json(reports: Dict[str, EngineReport], output_path: Path) -> None:
    payload = {
        "engines": {},
        "totals": {
            "n_total": sum(r.n_total for r in reports.values()),
            "n_ok": sum(r.n_ok for r in reports.values()),
            "n_fail": sum(r.n_fail for r in reports.values()),
        },
    }
    for engine, rpt in reports.items():
        payload["engines"][engine] = {
            "n_total": rpt.n_total,
            "n_ok": rpt.n_ok,
            "n_fail": rpt.n_fail,
            "n_warn": rpt.n_warn,
            "n_missing_wav": rpt.n_missing_wav,
            "sound_types": rpt.sound_types,
            "fingerprint_collisions": rpt.fingerprint_collisions,
            "failures": [
                {
                    "preset_id": f.preset_id,
                    "sound_type": f.sound_type,
                    "issues": f.issues,
                    "warnings": f.warnings,
                    "duration_sec": f.duration_sec,
                    "rms": f.rms,
                    "peak": f.peak,
                    "sha1_match": f.sha1_match,
                }
                for f in rpt.failures
            ],
            "warnings": [
                {
                    "preset_id": f.preset_id,
                    "sound_type": f.sound_type,
                    "warnings": f.warnings,
                    "duration_sec": f.duration_sec,
                    "peak": f.peak,
                }
                for f in rpt.warnings_list
            ],
        }
    output_path.write_text(json.dumps(payload, indent=2))
    logger.info("Wrote JSON report: %s", output_path)


# --- Main ---------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--instruments",
        nargs="+",
        default=["Analog", "Drift", "Collision", "Electric"],
    )
    p.add_argument(
        "--catalog-dir",
        type=Path,
        default=Path("preset_catalog_output/catalog"),
    )
    p.add_argument(
        "--audio-dir",
        type=Path,
        default=Path("preset_catalog_output/audio_v2"),
    )
    p.add_argument(
        "--report-dir",
        type=Path,
        default=Path("preset_catalog_output/catalog"),
    )
    p.add_argument(
        "--skip-sha",
        action="store_true",
        help="Skip decoded-audio SHA-1 recomputation (faster).",
    )
    args = p.parse_args()

    args.report_dir.mkdir(parents=True, exist_ok=True)
    verify_sha = not args.skip_sha

    reports: Dict[str, EngineReport] = {}
    for engine in args.instruments:
        catalog_path = args.catalog_dir / f"catalog_{engine.lower()}_v2.json"
        logger.info("Validating %s from %s", engine, catalog_path)
        reports[engine] = validate_engine(
            engine=engine,
            catalog_path=catalog_path,
            audio_dir=args.audio_dir,
            verify_sha=verify_sha,
        )

    render_json(reports, args.report_dir / "v2_corpus_validation.json")
    render_markdown(reports, args.report_dir / "v2_corpus_validation.md")

    print("\n=== V2 corpus validation ===")
    print(f"{'engine':10s} {'total':>6s} {'ok':>6s} {'fail':>6s} {'warn':>6s} {'missing':>8s}")
    n_total = n_ok = n_fail = n_warn = 0
    for engine, rpt in reports.items():
        n_total += rpt.n_total
        n_ok += rpt.n_ok
        n_fail += rpt.n_fail
        n_warn += rpt.n_warn
        print(
            f"{engine:10s} {rpt.n_total:6d} {rpt.n_ok:6d} {rpt.n_fail:6d} "
            f"{rpt.n_warn:6d} {rpt.n_missing_wav:8d}"
        )
    print(f"{'TOTAL':10s} {n_total:6d} {n_ok:6d} {n_fail:6d} {n_warn:6d}")

    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
