#!/usr/bin/env python3
"""Blind A/B audit pack for stems.enhance (lossy-rip bandwidth extension).

Question under audit: does SBR-style bandwidth extension make a
YouTube-rip stem (and the remixed stem sum) sound *better* — more air,
no added hiss/artifacts — than the untouched audio? Promotion is
blind-gated on ears (house rule); the cutoff detector's own spectral
measurements are derivation tools and would be a circular gate.

Per clip the pack contains:

  X1.wav / X2.wav   original vs bandwidth-extended excerpt, shuffled
                    per clip (seeded), loudness-matched

Clips are cut from each detected-lossy stem plus the full stem sum
(optionally + separation residual when --mix is given, mixed at
--residual-db). Stems with no detected codec cliff are skipped — the
treatment is a no-op there by construction.

With ``--kit-json`` (an /api/song/{id}/kit manifest), clips are the
EXACT pad slices the app plays: enhancement runs on the parent stem
(the shipping architecture — a stem is enhanced once, pads slice it),
then each pad's [startSec, endSec] window becomes one A/B clip. Pads
whose parent stem has no detected cliff are skipped.

Protocol (same as lab_data/tuner_audit_*): fill VERDICTS.md completely
before opening ANSWER_KEY.json.

Usage:
    .venv/bin/python scripts/make_stem_enhance_audit.py \
        --stems-dir lab_data/stem_enhance_audit_v1/source \
        [--mix lab_data/stem_enhance_audit_v1/source/mix.wav] \
        [--out-dir lab_data/stem_enhance_audit_v1] \
        [--clips-per-source 4] [--clip-seconds 12] \
        [--amount 1.0] [--residual-db -12] [--seed 20260906]
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

import numpy as np

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

from tone_forge.stems.enhance import (  # noqa: E402
    detect_lossy_cutoff,
    extend_bandwidth,
    separation_residual,
)


def _load(path: Path) -> tuple[np.ndarray, int]:
    import soundfile as sf
    data, sr = sf.read(str(path), dtype="float64", always_2d=True)
    return data.mean(axis=1), int(sr)


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt((x ** 2).mean()) + 1e-12)


def _write(path: Path, y: np.ndarray, sr: int) -> None:
    import soundfile as sf
    peak = np.abs(y).max()
    if peak > 0.98:
        y = y * (0.98 / peak)
    sf.write(str(path), y.astype(np.float32), sr)


def _pick_offsets(n_samples: int, sr: int, clip_s: float, count: int,
                  rng: random.Random) -> list[int]:
    """Spread offsets across the song, skipping the first/last 5%."""
    clip_n = int(clip_s * sr)
    lo, hi = int(n_samples * 0.05), int(n_samples * 0.95) - clip_n
    if hi <= lo:
        return [0]
    return sorted(rng.randrange(lo, hi) for _ in range(count))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stems-dir", type=Path, required=True)
    ap.add_argument("--mix", type=Path, default=None)
    ap.add_argument("--kit-json", type=Path, default=None,
                    help="kit manifest: clip per pad stemSlice instead of random offsets")
    ap.add_argument("--out-dir", type=Path,
                    default=_REPO_ROOT / "lab_data" / "stem_enhance_audit_v1")
    ap.add_argument("--clips-per-source", type=int, default=4)
    ap.add_argument("--clip-seconds", type=float, default=12.0)
    ap.add_argument("--amount", type=float, default=1.0)
    ap.add_argument("--residual-db", type=float, default=-12.0)
    ap.add_argument("--seed", type=int, default=20260906)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)

    stem_paths = sorted(
        p for p in args.stems_dir.iterdir()
        if p.suffix.lower() in (".wav", ".flac")
        and (args.mix is None or p.resolve() != args.mix.resolve())
    )
    if not stem_paths:
        print(f"no stems in {args.stems_dir}", file=sys.stderr)
        return 1

    # Load + per-stem enhancement (full-length once; clips slice it).
    sources: list[tuple[str, np.ndarray, np.ndarray, int, dict]] = []
    stems_raw: dict[str, np.ndarray] = {}
    sr_common = None
    for p in stem_paths:
        y, sr = _load(p)
        sr_common = sr_common or sr
        if sr != sr_common:
            print(f"skip {p.name}: sr {sr} != {sr_common}", file=sys.stderr)
            continue
        stems_raw[p.stem] = y
        cut = detect_lossy_cutoff(y, sr)
        if cut is None:
            print(f"  {p.stem}: no codec cliff — skipped (treatment is a no-op)")
            continue
        enh = extend_bandwidth(y, sr, cut, amount=args.amount)
        print(f"  {p.stem}: cliff {cut.cutoff_hz:.0f} Hz "
              f"(drop {cut.drop_db:.1f} dB, slope {cut.slope_db_per_octave:+.1f} dB/oct)")
        sources.append((p.stem, y, enh, sr, {
            "cutoff_hz": cut.cutoff_hz, "drop_db": cut.drop_db,
            "slope_db_per_octave": cut.slope_db_per_octave,
        }))

    # Stem-sum source (the thing users actually hear in the app) —
    # enhanced sum = sum of enhanced stems (+ optional residual glue).
    if len(stems_raw) >= 2 and sources:
        n = min(len(v) for v in stems_raw.values())
        base_sum = np.zeros(n)
        enh_sum = np.zeros(n)
        enh_by_name = {name: e for name, _y, e, _sr, _m in sources}
        for name, y in stems_raw.items():
            base_sum += y[:n]
            enh_sum += enh_by_name.get(name, y)[:n]
        meta: dict = {"kind": "stem_sum"}
        if args.mix is not None and args.mix.exists():
            mix_y, mix_sr = _load(args.mix)
            if mix_sr == sr_common:
                res = separation_residual(mix_y, stems_raw)
                g = 10.0 ** (args.residual_db / 20.0)
                m = min(len(res), n)
                enh_sum[:m] += g * res[:m]
                meta["residual_db"] = args.residual_db
        sources.append(("stem_sum", base_sum, enh_sum, sr_common, meta))

    if not sources:
        print("nothing to audit: no source had a detectable codec cliff",
              file=sys.stderr)
        return 1

    key: dict = {"seed": args.seed, "amount": args.amount, "clips": {}}
    verdict_lines = [
        "# Stem-enhance blind audit — VERDICTS",
        "",
        "For each clip: listen to X1 and X2 (loudness-matched). Note",
        "which sounds better (more air / detail WITHOUT added hiss,",
        "crunch, or metallic edge) or 'no difference'. Fill EVERY row",
        "before opening ANSWER_KEY.json.",
        "",
        "| clip | better (X1/X2/none) | notes |",
        "|------|--------------------:|-------|",
    ]

    # One job = one A/B clip: (label, orig, enh, sr, meta, start, stop).
    jobs: list[tuple] = []
    if args.kit_json is not None:
        kit = json.loads(args.kit_json.read_text())
        by_name = {name: (y, e, sr, m) for name, y, e, sr, m in sources
                   if name != "stem_sum"}
        for pad in kit.get("pads", []):
            sl_meta = pad.get("stemSlice") or {}
            role = sl_meta.get("stemRole")
            if role not in by_name:
                continue  # parent stem had no cliff — treatment is a no-op
            y, e, sr, m = by_name[role]
            start = int(float(sl_meta["startSec"]) * sr)
            stop = int(float(sl_meta["endSec"]) * sr)
            if stop <= start or start >= len(y):
                continue
            label = f"{role}:pad{pad.get('padIdx')}"
            jobs.append((label, y, e, sr,
                         {**m, "pad_name": pad.get("name", "")}, start, stop))
        if not jobs:
            print("kit mode: no pad's parent stem had a detectable cliff",
                  file=sys.stderr)
            return 1
    else:
        for name, y, enh, sr, meta in sources:
            for off in _pick_offsets(len(y), sr, args.clip_seconds,
                                     args.clips_per_source, rng):
                jobs.append((name, y, enh, sr, meta,
                             off, off + int(args.clip_seconds * sr)))

    clip_idx = 0
    for name, y, enh, sr, meta, start, stop in jobs:
            off = start
            clip_idx += 1
            cid = f"clip{clip_idx:02d}"
            cdir = out / cid
            cdir.mkdir(exist_ok=True)
            sl = slice(start, stop)
            a, b = y[sl].copy(), enh[sl].copy()
            # Loudness-match so the (slightly hotter) extended clip
            # can't win on level alone.
            b *= _rms(a) / _rms(b)
            enhanced_is_x1 = rng.random() < 0.5
            x1, x2 = (b, a) if enhanced_is_x1 else (a, b)
            _write(cdir / "X1.wav", x1, sr)
            _write(cdir / "X2.wav", x2, sr)
            key["clips"][cid] = {
                "source": name,
                "offset_s": round(off / sr, 2),
                "enhanced": "X1" if enhanced_is_x1 else "X2",
                **meta,
            }
            verdict_lines.append(f"| {cid} ({name}) |  |  |")

    (out / "ANSWER_KEY.json").write_text(json.dumps(key, indent=2))
    (out / "VERDICTS.md").write_text("\n".join(verdict_lines) + "\n")
    print(f"\n{clip_idx} clips → {out}")
    print("Protocol: fill VERDICTS.md fully BEFORE opening ANSWER_KEY.json.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
