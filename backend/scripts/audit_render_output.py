#!/usr/bin/env python3
"""Post-render output audit.

Cross-references the generated ``als/*.als`` files against the rendered
``audio/*.wav`` files and asserts:

  * Every ALS has a corresponding WAV at the expected basename.
  * Every WAV is non-empty and contains audible content (RMS above floor).
  * Every WAV has a plausible duration (>= ``MIN_DURATION_SEC``).
  * No "drift" — WAVs that ended up in an unexpected sibling directory
    (e.g. ``equivalence/`` from a previous session). When ``--auto-fix``
    is passed, drifted WAVs (and their ``.asd`` / ``.mp3`` siblings) are
    moved back into ``audio/``.

This catches the exact failure mode hit during Preset Rendering Pipeline v2:
Ableton's export dialog defaulted to a sibling folder (``equivalence/``)
instead of the intended ``audio/``, so 37 renders landed in the wrong place
with no automated signal. Gate 1 of the Catalog Integrity Gate caught it
later, but only after we'd already wasted an `auto_export_presets.py` run.
Running this audit between render and fingerprint gives a fast failure.

Exit codes:
  0 — every ALS has a healthy WAV at the expected location.
  1 — one or more problems detected (missing / silent / drifted / short).
  2 — usage error.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np

try:
    import librosa
except ImportError:  # pragma: no cover
    print("ERROR: librosa is required (pip install librosa)", file=sys.stderr)
    sys.exit(2)


# Audit thresholds.
MIN_DURATION_SEC = 1.0          # below this is suspicious (truncated render)
MIN_RMS = 0.001                  # below this is effectively silent
SIBLING_DRIFT_DIRS = ("equivalence",)  # folders to scan for drifted WAVs
DRIFT_COMPANION_EXTS = (".wav", ".wav.asd", ".mp3", ".mp3.asd")


def _load_audio_stats(path: Path) -> Tuple[float, float]:
    """Return (duration_sec, rms) for a WAV file."""
    y, sr = librosa.load(str(path), sr=None, mono=True)
    duration = len(y) / sr if sr > 0 else 0.0
    rms = float(np.sqrt(np.mean(np.square(y)) + 1e-20))
    return duration, rms


def audit(
    als_dir: Path,
    audio_dir: Path,
    *,
    auto_fix: bool = False,
    sibling_drift_root: Path | None = None,
) -> Tuple[bool, Dict]:
    """Run the audit. Returns (passed, report)."""
    als_files = sorted(als_dir.glob("*.als"))
    if not als_files:
        return False, {"error": f"no .als files in {als_dir}"}

    expected = {p.stem: p for p in als_files}
    report: Dict = {
        "als_dir": str(als_dir),
        "audio_dir": str(audio_dir),
        "auto_fix": auto_fix,
        "n_als": len(expected),
        "results": [],
        "drift_recovered": [],
        "summary": {},
    }

    # Pre-pass: scan sibling-drift dirs for WAVs whose basename matches an
    # expected ALS but is in the wrong location.
    drift_root = sibling_drift_root or als_dir.parent
    drifted: Dict[str, Path] = {}
    for sib in SIBLING_DRIFT_DIRS:
        sib_dir = drift_root / sib
        if not sib_dir.is_dir():
            continue
        for wav in sib_dir.glob("*.wav"):
            if wav.stem in expected:
                drifted[wav.stem] = wav

    if drifted and auto_fix:
        audio_dir.mkdir(parents=True, exist_ok=True)
        for stem, wav in drifted.items():
            # Move WAV and any companion files (.wav.asd, matching .mp3, etc.)
            moved = []
            for ext in DRIFT_COMPANION_EXTS:
                src = wav.with_name(stem + ext)
                if src.exists():
                    dst = audio_dir / src.name
                    shutil.move(str(src), str(dst))
                    moved.append(str(dst))
            report["drift_recovered"].append({"preset_id": stem, "moved": moved})

    n_pass = 0
    n_missing = 0
    n_silent = 0
    n_short = 0
    n_drifted_unfixed = 0
    n_zero_byte = 0

    for stem, als_path in expected.items():
        entry: Dict = {"preset_id": stem, "als": str(als_path)}
        wav_path = audio_dir / f"{stem}.wav"

        if not wav_path.exists():
            # Look for drift even if auto_fix was off.
            if stem in drifted and not auto_fix:
                entry["status"] = "drifted"
                entry["found_at"] = str(drifted[stem])
                n_drifted_unfixed += 1
            else:
                entry["status"] = "missing"
                n_missing += 1
            report["results"].append(entry)
            continue

        if wav_path.stat().st_size == 0:
            entry["status"] = "zero_byte"
            n_zero_byte += 1
            report["results"].append(entry)
            continue

        try:
            duration, rms = _load_audio_stats(wav_path)
        except Exception as exc:
            entry["status"] = "load_error"
            entry["error"] = str(exc)
            n_zero_byte += 1
            report["results"].append(entry)
            continue

        entry["duration_sec"] = duration
        entry["rms"] = rms

        if rms < MIN_RMS:
            entry["status"] = "silent"
            n_silent += 1
        elif duration < MIN_DURATION_SEC:
            entry["status"] = "short"
            n_short += 1
        else:
            entry["status"] = "ok"
            n_pass += 1

        report["results"].append(entry)

    report["summary"] = {
        "n_als": len(expected),
        "n_pass": n_pass,
        "n_missing": n_missing,
        "n_drifted_unfixed": n_drifted_unfixed,
        "n_zero_byte": n_zero_byte,
        "n_silent": n_silent,
        "n_short": n_short,
        "n_drift_recovered": len(report["drift_recovered"]),
        "min_duration_sec": MIN_DURATION_SEC,
        "min_rms": MIN_RMS,
    }

    passed = (
        n_missing == 0
        and n_drifted_unfixed == 0
        and n_zero_byte == 0
        and n_silent == 0
        and n_short == 0
    )
    return passed, report


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--als-dir",
        type=Path,
        default=Path("preset_catalog_output/als"),
        help="directory containing generated *.als files",
    )
    p.add_argument(
        "--audio-dir",
        type=Path,
        default=Path("preset_catalog_output/audio"),
        help="directory expected to contain rendered *.wav files",
    )
    p.add_argument(
        "--auto-fix",
        action="store_true",
        help="move drifted WAVs from sibling dirs (e.g. equivalence/) into "
             "audio/ before judging the result",
    )
    p.add_argument(
        "--report",
        type=Path,
        default=None,
        help="optional JSON report path (default: "
             "<als-dir>/../retrieval/render_output_audit.json)",
    )
    args = p.parse_args()

    if not args.als_dir.is_dir():
        print(f"ERROR: not a directory: {args.als_dir}", file=sys.stderr)
        return 2

    passed, report = audit(
        args.als_dir,
        args.audio_dir,
        auto_fix=args.auto_fix,
    )

    s = report.get("summary", {})
    print(f"ALS dir:   {args.als_dir}")
    print(f"Audio dir: {args.audio_dir}")
    if report.get("drift_recovered"):
        print(f"  drift recovered: {len(report['drift_recovered'])} WAVs moved into audio/")
    print(f"  n_als           = {s.get('n_als', 0)}")
    print(f"  PASS            = {s.get('n_pass', 0)}")
    print(f"  missing         = {s.get('n_missing', 0)}")
    print(f"  drifted (unfix) = {s.get('n_drifted_unfixed', 0)}")
    print(f"  zero-byte       = {s.get('n_zero_byte', 0)}")
    print(f"  silent          = {s.get('n_silent', 0)}")
    print(f"  short           = {s.get('n_short', 0)}")

    # Print failure examples
    if not passed:
        bad = [r for r in report["results"] if r.get("status") != "ok"]
        for r in bad[:10]:
            extra = ""
            if r.get("status") == "drifted":
                extra = f" (found at {r['found_at']})"
            elif "rms" in r:
                extra = f" (rms={r['rms']:.4f}, duration={r['duration_sec']:.2f}s)"
            print(f"    FAIL  [{r['status']}]  {r['preset_id']}{extra}", file=sys.stderr)

    report_path = args.report or (
        args.als_dir.parent / "retrieval" / "render_output_audit.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2))
    print(f"  Report -> {report_path}")

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
