#!/usr/bin/env python3
"""Phase-0 blind audit pack for the guitar tone generator.

Question under test: does the ToneDescriptor → Helix preset path (plus
the match-EQ IR from ir_match.py) actually sound like the song's guitar?
The numbers can't answer that — same discipline as the specialist
listening packs: blind A/B, verdicts written before the answer key is
opened, promotion gated on ≥24 clips.

Per clip directory (``<name>/`` under --clips-dir):
    target_guitar.wav   the song's separated guitar stem (required)
    di.wav              a clean DI/neutral guitar take (optional)

Outputs per clip into the pack:
    generated.hlx       preset from the analyzed ToneDescriptor
    match_ir.wav        2048-tap match IR (when di.wav present) — import
                        into the Helix IR library; generated.hlx points
                        its IR block at --ir-index
    X1.wav / X2.wav     blind pair (target stem excerpt vs render),
                        shuffled per clip with --seed
    ANSWER_KEY.json     which X is which — open ONLY after VERDICTS.md

Renderers (--renderer):
    helix-native  render di.wav through Helix Native VST3 loading
                  generated.hlx (ground-truth render). Requires Helix
                  Native installed + `pip install pedalboard`.
    proxy         deterministic numpy stand-in: tanh drive staged from
                  the descriptor's gain + the match IR. NOT the real
                  amp — smoke-tests the harness and the IR, nothing
                  more. Requires di.wav.
    none          no renders: pack carries preset+IR only, judging
                  happens on hardware (load preset, play, compare to
                  the stem excerpt by ear).

Usage (from backend/):
    python3 scripts/make_tone_audit_pack.py \
        --clips-dir lab_data/tone_audit_src \
        --out lab_data/tone_audit_v1 \
        --renderer proxy --clip-seconds 20 --seed 20260831
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import asdict
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

HELIX_NATIVE_VST3 = "/Library/Audio/Plug-Ins/VST3/Helix Native.vst3"
SR = 48_000


def _load_mono(path: Path) -> np.ndarray:
    data, file_sr = sf.read(str(path), always_2d=True)
    mono = data.mean(axis=1)
    if file_sr != SR:
        from scipy.signal import resample_poly
        from math import gcd
        g = gcd(int(file_sr), SR)
        mono = resample_poly(mono, SR // g, int(file_sr) // g)
    return mono.astype(np.float32)


def _best_window(x: np.ndarray, clip_s: float) -> tuple[int, int]:
    n = int(clip_s * SR)
    if len(x) <= n:
        return 0, len(x)
    hop = SR
    energies = [(np.sum(x[i:i + n] ** 2), i) for i in range(0, len(x) - n, hop)]
    _, start = max(energies)
    return start, start + n


def _normalize(x: np.ndarray) -> np.ndarray:
    peak = np.abs(x).max()
    return x * (0.891 / peak) if peak > 0 else x


def _render_proxy(di: np.ndarray, descriptor: dict,
                  target: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Numpy stand-in render: staged tanh drive + match-EQ IR."""
    from scipy.signal import fftconvolve
    from tone_forge.ir_match import compute_match_ir

    gain = float((descriptor.get("amp") or {}).get("gain", 0.5) or 0.5)
    driven = np.tanh(di * (1.0 + gain * 12.0))
    result = compute_match_ir(driven, target)
    rendered = fftconvolve(driven, result.ir)[:len(driven)]
    return _normalize(rendered.astype(np.float32)), result.ir


def _render_helix_native(di: np.ndarray, hlx_path: Path) -> np.ndarray:
    try:
        from pedalboard import load_plugin
    except ImportError:
        raise SystemExit(
            "--renderer helix-native needs `pip install pedalboard`")
    if not Path(HELIX_NATIVE_VST3).exists():
        raise SystemExit(
            f"Helix Native not found at {HELIX_NATIVE_VST3}. Install it "
            "(free 15-day trial at line6.com) or use --renderer proxy/none.")
    plugin = load_plugin(HELIX_NATIVE_VST3)
    # pedalboard's load_preset takes .vstpreset, not .hlx — Helix
    # Native only imports .hlx through its own UI. Workflow: open the
    # plugin UI once, import the .hlx, save a sibling .vstpreset.
    vstpreset = hlx_path.with_suffix(".vstpreset")
    if not vstpreset.exists():
        raise SystemExit(
            f"{vstpreset} missing. Helix Native can't load .hlx "
            "headlessly: open the plugin once (show_editor), import "
            f"{hlx_path.name}, save the state as {vstpreset.name}, "
            "then re-run.")
    plugin.load_preset(str(vstpreset))
    return plugin(di, SR)


def build_clip(clip_dir: Path, out_dir: Path, clip_s: float,
               renderer: str, ir_index: int, rng: random.Random) -> dict:
    from tone_forge import analyzer, helix_translator
    from tone_forge.ir_match import MatchIRResult, write_ir_wav
    from tone_forge.preset_export import attach_match_ir, export_helix_preset

    target_path = clip_dir / "target_guitar.wav"
    di_path = clip_dir / "di.wav"
    out_dir.mkdir(parents=True, exist_ok=True)

    target = _load_mono(target_path)
    lo, hi = _best_window(target, clip_s)
    target_clip = target[lo:hi]
    sf.write(str(out_dir / "ref_target.wav"), _normalize(target_clip), SR)

    # Analyze → descriptor → .hlx
    descriptor = analyzer.analyze(str(target_path),
                                  source_kind="isolated_guitar")
    card = helix_translator.translate(descriptor)
    chain = [asdict(p) for p in card.picks]
    preset = export_helix_preset(chain, descriptor, preset_name=clip_dir.name)
    hlx_content = preset.content

    rendered = None
    if di_path.exists():
        di = _load_mono(di_path)
        di_clip = di[:len(target_clip)] if len(di) > len(target_clip) else di
        if renderer == "proxy":
            rendered, ir = _render_proxy(di_clip, descriptor.to_dict(),
                                         target_clip)
        else:
            from tone_forge.ir_match import compute_match_ir
            result = compute_match_ir(di_clip, target_clip)
            ir = result.ir
            if renderer == "helix-native":
                hlx_tmp = out_dir / "generated.hlx"
                hlx_tmp.write_text(hlx_content)
                rendered = _render_helix_native(di_clip, hlx_tmp)
        ir_result = MatchIRResult(ir=ir, sample_rate=SR,
                                  ratio_db_range=(0.0, 0.0),
                                  source_seconds=len(di_clip) / SR,
                                  target_seconds=len(target_clip) / SR)
        write_ir_wav(out_dir / "match_ir.wav", ir_result)
        hlx_content = attach_match_ir(hlx_content, ir_index=ir_index)

    (out_dir / "generated.hlx").write_text(hlx_content)

    entry = {
        "clip": clip_dir.name,
        "amp_family": descriptor.to_dict().get("amp", {}).get("family"),
        "renderer": renderer if rendered is not None else "none",
    }

    if rendered is not None:
        pair = [("target", _normalize(target_clip)), ("render", rendered)]
        rng.shuffle(pair)
        for i, (label, audio) in enumerate(pair, start=1):
            sf.write(str(out_dir / f"X{i}.wav"), audio, SR)
            entry[f"X{i}"] = label
    return entry


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clips-dir", required=True)
    ap.add_argument("--out", default="lab_data/tone_audit_v1")
    ap.add_argument("--renderer", default="none",
                    choices=["helix-native", "proxy", "none"])
    ap.add_argument("--clip-seconds", type=float, default=20.0)
    ap.add_argument("--ir-index", type=int, default=1)
    ap.add_argument("--seed", type=int, default=20260831)
    args = ap.parse_args()

    clips_dir = Path(args.clips_dir)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)

    clip_dirs = sorted(d for d in clips_dir.iterdir()
                       if d.is_dir() and (d / "target_guitar.wav").exists())
    if not clip_dirs:
        raise SystemExit(f"no clip dirs with target_guitar.wav in {clips_dir}")
    if len(clip_dirs) < 24:
        print(f"[warn] {len(clip_dirs)} clips < 24 — pack is exploratory, "
              "NOT a promotion gate (blind-gate rule).")

    key = []
    for clip_dir in clip_dirs:
        print(f"[clip] {clip_dir.name}")
        entry = build_clip(clip_dir, out / clip_dir.name, args.clip_seconds,
                           args.renderer, args.ir_index, rng)
        key.append(entry)

    (out / "ANSWER_KEY.json").write_text(json.dumps(key, indent=2))
    (out / "VERDICTS.md").write_text(
        "# Tone audit verdicts\n\n"
        "Protocol: per clip, listen to ref_target.wav then the X files "
        "(or play the generated.hlx + match_ir.wav on hardware against "
        "the ref). Note per clip: close / usable / wrong, and what's "
        "off (gain, EQ, ambience). Fill this file COMPLETELY before "
        "opening ANSWER_KEY.json.\n\n"
        + "\n".join(f"- [ ] {e['clip']}: " for e in key) + "\n")
    print(f"[done] {len(key)} clips → {out}")


if __name__ == "__main__":
    main()
