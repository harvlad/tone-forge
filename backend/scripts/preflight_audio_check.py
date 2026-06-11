"""Pre-flight audio check for V3 ratings + FX label audit.

Verifies every audio file referenced by the listening worksheets is:
- present on disk
- nonzero duration
- not silent (RMS > 1e-3, well above the 1e-10 regression threshold)
- a sane sample rate (8 kHz <= sr <= 192 kHz)

Writes ``preset_catalog_output/retrieval/preflight_audio_check.json`` and
prints a per-file PASS/FAIL summary. Exits 0 if everything passes, 1 if
anything fails.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

RMS_THRESHOLD = 1e-3
SR_MIN, SR_MAX = 8000, 192000

V3_FILE = Path(
    "preset_catalog_output/retrieval/v3_top5_usefulness_rating.json"
)
FX_FILE = Path(
    "preset_catalog_output/retrieval/fx_label_audit_worksheet.json"
)
OUT_FILE = Path(
    "preset_catalog_output/retrieval/preflight_audio_check.json"
)


def _check_one(path_str: str) -> dict:
    p = Path(path_str)
    info = {
        "path": str(p),
        "exists": p.exists(),
        "duration_sec": None,
        "samplerate": None,
        "rms": None,
        "ok": False,
        "reason": None,
    }
    if not p.exists():
        info["reason"] = "file does not exist"
        return info
    try:
        meta = sf.info(str(p))
        info["duration_sec"] = float(meta.duration)
        info["samplerate"] = int(meta.samplerate)
        if meta.duration <= 0:
            info["reason"] = "zero duration"
            return info
        if not (SR_MIN <= meta.samplerate <= SR_MAX):
            info["reason"] = f"samplerate {meta.samplerate} out of range"
            return info
        data, _ = sf.read(str(p), dtype="float32", always_2d=False)
        if data.ndim > 1:
            data = data.mean(axis=1)
        rms = float(np.sqrt(np.mean(data * data)))
        info["rms"] = rms
        if rms < RMS_THRESHOLD:
            info["reason"] = f"rms {rms:.2e} below threshold {RMS_THRESHOLD:.0e}"
            return info
    except Exception as exc:  # noqa: BLE001
        info["reason"] = f"read error: {exc!r}"
        return info
    info["ok"] = True
    return info


def _collect_v3_paths() -> list[str]:
    if not V3_FILE.exists():
        return []
    d = json.loads(V3_FILE.read_text())
    paths: list[str] = []
    for q in d.get("queries", []):
        paths.append(q["query_audio"])
        for nb in q["top5"]:
            paths.append(nb["audio_path"])
    return paths


def _collect_fx_paths() -> list[str]:
    if not FX_FILE.exists():
        return []
    d = json.loads(FX_FILE.read_text())
    return [item["query_audio"] for item in d.get("items", [])]


def main() -> int:
    v3 = _collect_v3_paths()
    fx = _collect_fx_paths()
    unique = sorted(set(v3 + fx))
    print(f"V3 references: {len(v3)} audio paths "
          f"({len(set(v3))} unique)")
    print(f"FX audit references: {len(fx)} audio paths "
          f"({len(set(fx))} unique)")
    print(f"Unique files to check: {len(unique)}\n")

    results = [_check_one(p) for p in unique]
    failed = [r for r in results if not r["ok"]]

    for r in results:
        flag = "PASS" if r["ok"] else "FAIL"
        rms = "n/a" if r["rms"] is None else f"{r['rms']:.3f}"
        dur = "n/a" if r["duration_sec"] is None else f"{r['duration_sec']:.2f}s"
        name = Path(r["path"]).name
        if r["ok"]:
            print(f"  [{flag}] {name:60s}  dur={dur:>7s}  rms={rms}")
        else:
            print(f"  [{flag}] {name:60s}  {r['reason']}")

    print(f"\nSummary: {len(results) - len(failed)}/{len(results)} pass, "
          f"{len(failed)} fail")
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps({
        "n_checked": len(results),
        "n_pass": len(results) - len(failed),
        "n_fail": len(failed),
        "rms_threshold": RMS_THRESHOLD,
        "results": results,
    }, indent=2))
    print(f"Wrote {OUT_FILE}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
