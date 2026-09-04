#!/usr/bin/env python3
"""Blind A/B audit pack for the per-song chain tuner (monitor.tuner).

Question under audit: does the tuner's EQ re-voicing move a chain
*audibly closer* to the song's guitar tone than the untouched base
chain? Promotion is blind-gated on ears (>= 24 clips, house rule) —
never on the spectral objective the tuner itself optimizes, which
would be circular.

Per clip the pack contains:

  ref_target.wav   the song's guitar stem excerpt (the tone to approach)
  X1.wav / X2.wav  base chain reference render vs tuned render,
                   shuffled per clip (seeded), loudness-matched to ref

The tuned render is EXACT for V1, not a proxy: the tuner only touches
EQ band gains and the input high-pass — all linear — so applying those
filters to the base chain's reference render produces precisely what
Connect would output for the same input. (The moment the tuner learns
a nonlinear knob, this shortcut dies and the pack needs a DI + full
render path.)

Filter shapes approximate a guitar-amp tone stack: low shelf (bass),
peaking (mid, treble), high shelf (presence), biquads at RBJ cookbook
coefficients. Connect's exact filter Qs may differ; the audit measures
the *direction* of the re-voicing, and a verdict of "closer but the
mid Q feels off" is itself useful output.

Protocol (same as lab_data/tone_audit_v1): fill VERDICTS.md completely
before opening ANSWER_KEY.json.

Usage:
    .venv/bin/python scripts/make_tuner_audit_pack.py \
        [--clips-dir data/tone_calibration_clips] \
        [--out-dir lab_data/tuner_audit_v1] \
        [--count 24] [--seed 20260904]
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from pathlib import Path
from typing import List, Tuple

import numpy as np

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

_SR = 48_000


# ---------------------------------------------------------------------------
# RBJ biquads — enough EQ to realize the tuner's four band deltas.
# ---------------------------------------------------------------------------

def _biquad(b, a, x):
    from scipy.signal import lfilter
    return lfilter(np.asarray(b) / a[0], np.asarray(a) / a[0], x)


def _low_shelf(x, f0, gain_db, s=0.9):
    A = 10 ** (gain_db / 40)
    w0 = 2 * np.pi * f0 / _SR
    alpha = np.sin(w0) / 2 * np.sqrt((A + 1 / A) * (1 / s - 1) + 2)
    cosw, sq = np.cos(w0), 2 * np.sqrt(A) * (np.sin(w0) / 2) * np.sqrt((A + 1 / A) * (1 / s - 1) + 2)
    b = [A * ((A + 1) - (A - 1) * cosw + sq),
         2 * A * ((A - 1) - (A + 1) * cosw),
         A * ((A + 1) - (A - 1) * cosw - sq)]
    a = [(A + 1) + (A - 1) * cosw + sq,
         -2 * ((A - 1) + (A + 1) * cosw),
         (A + 1) + (A - 1) * cosw - sq]
    return _biquad(b, a, x)


def _high_shelf(x, f0, gain_db, s=0.9):
    A = 10 ** (gain_db / 40)
    w0 = 2 * np.pi * f0 / _SR
    sq = 2 * np.sqrt(A) * (np.sin(w0) / 2) * np.sqrt((A + 1 / A) * (1 / s - 1) + 2)
    cosw = np.cos(w0)
    b = [A * ((A + 1) + (A - 1) * cosw + sq),
         -2 * A * ((A - 1) + (A + 1) * cosw),
         A * ((A + 1) + (A - 1) * cosw - sq)]
    a = [(A + 1) - (A - 1) * cosw + sq,
         2 * ((A - 1) - (A + 1) * cosw),
         (A + 1) - (A - 1) * cosw - sq]
    return _biquad(b, a, x)


def _peaking(x, f0, gain_db, q=0.8):
    A = 10 ** (gain_db / 40)
    w0 = 2 * np.pi * f0 / _SR
    alpha = np.sin(w0) / (2 * q)
    cosw = np.cos(w0)
    b = [1 + alpha * A, -2 * cosw, 1 - alpha * A]
    a = [1 + alpha / A, -2 * cosw, 1 - alpha / A]
    return _biquad(b, a, x)


def _high_pass(x, f0, q=0.707):
    w0 = 2 * np.pi * f0 / _SR
    alpha = np.sin(w0) / (2 * q)
    cosw = np.cos(w0)
    b = [(1 + cosw) / 2, -(1 + cosw), (1 + cosw) / 2]
    a = [1 + alpha, -2 * cosw, 1 - alpha]
    return _biquad(b, a, x)


def _apply_tuning(
    x: np.ndarray,
    deltas: dict,
    hpf_delta: Tuple[float, float],
    base_params: dict,
    tuned_params: dict,
    seed: int,
) -> np.ndarray:
    """Realize the tuner's full-parameter move on the base render.

    EQ/HPF are exact (linear). Drive/reverb are additive-only proxies:
    we are transforming the chain's finished render, not a DI, so we
    can add saturation and space but not remove them — a negative
    delta is silently skipped (recorded in the answer key regardless).
    Comp is a simple feed-forward envelope follower.
    """
    y = x
    base_hpf, tuned_hpf = hpf_delta
    if abs(tuned_hpf - base_hpf) > 1.0:
        y = _high_pass(y, tuned_hpf)
    if abs(deltas.get("bass_db", 0.0)) > 0.05:
        y = _low_shelf(y, 250.0, deltas["bass_db"])
    if abs(deltas.get("mid_db", 0.0)) > 0.05:
        y = _peaking(y, 800.0, deltas["mid_db"])
    if abs(deltas.get("treble_db", 0.0)) > 0.05:
        y = _peaking(y, 3500.0, deltas["treble_db"], q=0.7)
    if abs(deltas.get("presence_db", 0.0)) > 0.05:
        y = _high_shelf(y, 6000.0, deltas["presence_db"])

    drive_delta = (float((tuned_params.get("gain_stage") or {}).get("drive", 0))
                   - float((base_params.get("gain_stage") or {}).get("drive", 0)))
    if drive_delta > 0.03:
        peak = float(np.max(np.abs(y))) or 1e-9
        k = 1.0 + drive_delta * 8.0
        y = np.tanh(y / peak * k) * (peak / np.tanh(k))

    comp = tuned_params.get("comp") or {}
    if comp.get("enabled") and float(comp.get("ratio", 1.0)) > 1.1:
        y = _compress(y, ratio=float(comp["ratio"]),
                      attack_ms=float(comp.get("attack_ms", 5) or 5),
                      release_ms=float(comp.get("release_ms", 100) or 100))

    mix_delta = (float((tuned_params.get("reverb") or {}).get("mix", 0))
                 - float((base_params.get("reverb") or {}).get("mix", 0)))
    if mix_delta > 0.02:
        from scipy.signal import fftconvolve
        rng = np.random.default_rng(seed)
        size = float((tuned_params.get("reverb") or {}).get("size", 0.3))
        rt = 0.4 + size * 1.6                        # seconds
        n = int(_SR * rt)
        ir = rng.standard_normal(n) * np.exp(-3.0 * np.arange(n) / n)
        ir /= float(np.sqrt(np.sum(ir**2))) or 1e-9
        wet = fftconvolve(y, ir)[: len(y)]
        y = (1.0 - mix_delta) * y + mix_delta * wet
    return y


def _compress(x: np.ndarray, ratio: float, attack_ms: float,
              release_ms: float, threshold_db: float = -18.0) -> np.ndarray:
    """Feed-forward RMS compressor, block envelope — audit proxy only."""
    atk = math.exp(-1.0 / (_SR * max(attack_ms, 0.1) / 1000.0))
    rel = math.exp(-1.0 / (_SR * max(release_ms, 1.0) / 1000.0))
    env = 0.0
    gains = np.ones(len(x))
    absx = np.abs(x)
    thr = 10 ** (threshold_db / 20)
    for i in range(len(x)):
        coeff = atk if absx[i] > env else rel
        env = coeff * env + (1 - coeff) * absx[i]
        if env > thr:
            over_db = 20 * math.log10(env / thr)
            gains[i] = 10 ** (-over_db * (1 - 1 / ratio) / 20)
    return x * gains


def _synth_chord_di(seed: int, bpm: float = 96.0) -> np.ndarray:
    """Strummed open-chord DI via Karplus-Strong — E, A, D, G, one bar
    each, quarter-note strums.

    The bundled mono DI is single-note picking; chords exercise the
    chain the way the reference stems actually play (voiced harmony,
    strum transients, string interaction under drive). Synthesis keeps
    the pack deterministic and self-contained — no multi-GB multitrack
    downloads hunting for a real DI take.
    """
    rng = np.random.default_rng(seed)

    def pluck(freq: float, seconds: float, level: float) -> np.ndarray:
        n = int(_SR * seconds)
        period = max(2, int(_SR / freq))
        buf = rng.uniform(-1, 1, period)
        out = np.empty(n)
        damp = 0.996
        for i in range(n):
            out[i] = buf[i % period]
            buf[i % period] = damp * 0.5 * (buf[i % period] + buf[(i + 1) % period])
        return out * level

    def strum(midi_notes, seconds):
        y = np.zeros(int(_SR * seconds))
        for k, m in enumerate(midi_notes):
            f = 440.0 * 2 ** ((m - 69) / 12)
            s = pluck(f, seconds, 0.9 ** k)
            offset = int(_SR * 0.012 * k)          # pick sweep
            y[offset:] += s[: len(y) - offset]
        return y

    chords = [
        (40, 47, 52, 56, 59, 64),   # E
        (45, 52, 57, 61, 64),       # A
        (50, 57, 62, 66),           # D
        (43, 47, 50, 55, 59, 67),   # G
    ]
    beat = 60.0 / bpm
    out = []
    for chord in chords:
        for _ in range(4):
            out.append(strum(chord, beat))
    y = np.concatenate(out)
    return (y / (np.max(np.abs(y)) or 1.0) * 0.35).astype(np.float64)


def _render_chain_proxy(di: np.ndarray, params: dict, seed: int) -> np.ndarray:
    """Render a clean DI through a numpy proxy of the full monitor chain.

    Absolute parameters, not deltas — with a DI as the source, drive
    *reduction* is as renderable as drive addition, which the v2
    render-transform approach could not do (its verdict: clean refs
    drew three distorted contenders). Approximate DSP, same spirit as
    the tone-audit proxy renderer: judges direction, not Connect's
    exact voicing.
    """
    inp = params.get("input") or {}
    gs = params.get("gain_stage") or {}
    eq = params.get("eq") or {}
    comp = params.get("comp") or {}
    rev = params.get("reverb") or {}
    out = params.get("output") or {}

    y = di * 10 ** (float(inp.get("gain_db", 0)) / 20)
    y = _high_pass(y, float(inp.get("high_pass_hz", 80)))

    drive = float(gs.get("drive", 0.0))
    if drive > 0.02:
        peak = float(np.percentile(np.abs(y), 99.9)) or 1e-9
        k = 1.0 + drive * 14.0
        y = np.tanh(y / peak * k) * (peak / math.tanh(k))

    if abs(float(eq.get("bass_db", 0))) > 0.05:
        y = _low_shelf(y, 250.0, float(eq["bass_db"]))
    if abs(float(eq.get("mid_db", 0))) > 0.05:
        y = _peaking(y, 800.0, float(eq["mid_db"]))
    if abs(float(eq.get("treble_db", 0))) > 0.05:
        y = _peaking(y, 3500.0, float(eq["treble_db"]), q=0.7)
    if abs(float(eq.get("presence_db", 0))) > 0.05:
        y = _high_shelf(y, 6000.0, float(eq["presence_db"]))

    if comp.get("enabled") and float(comp.get("ratio", 1)) > 1.1:
        y = _compress(y, ratio=float(comp["ratio"]),
                      attack_ms=float(comp.get("attack_ms", 5) or 5),
                      release_ms=float(comp.get("release_ms", 100) or 100))

    mix = float(rev.get("mix", 0.0))
    if mix > 0.02:
        from scipy.signal import fftconvolve
        rng = np.random.default_rng(seed)
        size = float(rev.get("size", 0.3))
        n = int(_SR * (0.4 + size * 1.6))
        ir = rng.standard_normal(n) * np.exp(-3.0 * np.arange(n) / n)
        ir /= float(np.sqrt(np.sum(ir**2))) or 1e-9
        y = (1.0 - mix) * y + mix * fftconvolve(y, ir)[: len(y)]

    return y * 10 ** (float(out.get("trim_db", 0)) / 20)


# Crest-gated base family. The 8-feature matcher measured at chance
# (AUC 0.506) — measured gain character is the one signal the blind
# labels didn't refute, so the audit's base chain comes from it.
_FAMILY_BY_DRIVE = (
    (0.25, "tfc.clean_strat"),
    (0.45, "tfc.edge_of_breakup"),
    (0.65, "tfc.classic_rock"),
    (9.99, "tfc.modern_gain"),
)


def _pick_base_chain(drive_est: float, reverb_est: float) -> str:
    if reverb_est > 0.30 and drive_est < 0.35:
        return "tfc.ambient"
    for ceiling, chain_id in _FAMILY_BY_DRIVE:
        if drive_est < ceiling:
            return chain_id
    return "tfc.edge_of_breakup"


def _rms_match(x: np.ndarray, target_rms: float) -> np.ndarray:
    rms = float(np.sqrt(np.mean(x**2))) or 1e-9
    y = x * (target_rms / rms)
    peak = float(np.max(np.abs(y))) or 1e-9
    if peak > 0.98:
        y *= 0.98 / peak
    return y


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--clips-dir", type=Path,
                        default=_REPO_ROOT / "data" / "tone_calibration_clips")
    parser.add_argument("--out-dir", type=Path,
                        default=_REPO_ROOT / "lab_data" / "tuner_audit_v1")
    parser.add_argument("--count", type=int, default=24)
    parser.add_argument("--seed", type=int, default=20260904)
    args = parser.parse_args()

    import soundfile as sf
    from tone_forge.ir_match import _load_mono_48k
    from tone_forge.monitor.tuner import derive_tuned_chain
    from tone_forge.tone import guitar_catalog as gc

    clips = sorted(
        p for p in args.clips_dir.iterdir()
        if p.suffix.lower() in {".wav", ".flac", ".mp3", ".aif", ".aiff"}
    )
    if len(clips) < args.count:
        raise SystemExit(f"need {args.count} clips, found {len(clips)}")
    # Even stride over the sorted corpus — deterministic diversity
    # (alphabetical order scatters genres) without cherry-picking.
    stride = len(clips) / args.count
    chosen = [clips[int(i * stride)] for i in range(args.count)]

    rng = random.Random(args.seed)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    key: List[dict] = []
    verdict_lines: List[str] = []

    from tone_forge.monitor.loader import load_chain
    from tone_forge.monitor.tuner import _estimate_drive, _estimate_reverb_mix

    # Per-clip source = pseudo-DI of the song's own stem (the Phase-0
    # rig's trick, make_tone_audit_pack._pseudo_di): the stem whitened
    # toward a DI spectrum with an inverse match IR. Every contender
    # then carries the song's actual performance — real dynamics, real
    # playing — which synthetic strums (v3) audibly could not fake.
    import make_tone_audit_pack as phase0
    di_ref = _load_mono_48k(_REPO_ROOT / "data" / "phase2_renders" / "_di_source.wav")

    for clip in chosen:
        name = clip.stem[:20]
        ref_clip = _load_mono_48k(clip)

        # Base is Clean Strat for every clip. Auto family gating is
        # dead until it has labels: crest factor, HF ratio and spectral
        # flatness all failed to separate clean/edge/driven on the six
        # ear-labeled anchor clips (2026-09) — the third refutation of
        # single-statistic per-clip estimators in this codebase. In the
        # product the *user* picks the family (the picker card); the
        # audit mirrors that by holding family fixed and judging only
        # what the tuner claims to do: spectral + space matching.
        base_id = "tfc.clean_strat"
        base_chain = load_chain(base_id)

        try:
            tuned = derive_tuned_chain(base_id, clip)
        except Exception as exc:
            print(f"skip {name}: {exc}")
            continue

        # Audit scope: EQ + reverb only. Drive/comp revert to the base
        # values — their estimators are refuted (same anchors) and
        # auditing a knob we know is broken wastes operator ears.
        tuned_params = dict(tuned.chain.parameters)
        tuned_params["gain_stage"] = base_chain.parameters["gain_stage"]
        tuned_params["comp"] = base_chain.parameters["comp"]

        # Source: this song's own performance, de-amped. All contenders
        # play the same part as the ref.
        src = phase0._pseudo_di(ref_clip, di_ref).astype(np.float64)

        base_audio = _render_chain_proxy(src, base_chain.parameters, args.seed)
        tuned_audio = _render_chain_proxy(src, tuned_params, args.seed)

        target_rms = float(np.sqrt(np.mean(ref_clip**2))) or 0.05

        # Ceiling contender, Phase-0 recipe: drive stage first, then
        # match-IR the *driven* signal onto the ref — the IR then
        # absorbs amp EQ + cab + room in one measured curve. Decides
        # whether the chain DSP earns an IR block.
        from scipy.signal import fftconvolve
        from tone_forge.ir_match import compute_match_ir
        driven = _render_chain_proxy(src, base_chain.parameters, args.seed)
        ir = compute_match_ir(driven, ref_clip).ir
        matched_audio = fftconvolve(driven, ir)[: len(driven)]

        variants = {
            "base": _rms_match(base_audio, target_rms),
            "knobs": _rms_match(tuned_audio, target_rms),
            "match_eq": _rms_match(matched_audio, target_rms),
        }
        order = list(variants.keys())
        rng.shuffle(order)

        clip_dir = args.out_dir / name
        clip_dir.mkdir(exist_ok=True)
        sf.write(str(clip_dir / "ref_target.wav"), ref_clip.astype(np.float32), _SR)
        for i, variant in enumerate(order, start=1):
            sf.write(
                str(clip_dir / f"X{i}.wav"),
                variants[variant].astype(np.float32), _SR,
            )

        key.append({
            "clip": name,
            "base_chain": base_id,
            **{f"X{i}": variant for i, variant in enumerate(order, start=1)},
            "band_deltas_db": {k: round(v, 2) for k, v in tuned.band_deltas_db.items()},
            "high_pass_hz": tuned.high_pass_hz,
            "tuned_gain_stage": tuned.chain.parameters.get("gain_stage"),
            "tuned_reverb": tuned.chain.parameters.get("reverb"),
            "tuned_comp": tuned.chain.parameters.get("comp"),
        })
        verdict_lines.append(f"- [ ] {name}: ")
        print(f"ok {name}: base={base_id} deltas="
              f"{ {k: round(v,1) for k,v in tuned.band_deltas_db.items()} }")

    (args.out_dir / "ANSWER_KEY.json").write_text(
        json.dumps(key, indent=2) + "\n", encoding="utf-8",
    )
    (args.out_dir / "VERDICTS.md").write_text(
        "# Tuner audit verdicts\n\n"
        "Protocol: per clip, X1/X2/X3 play the SAME PART as the ref "
        "(pseudo-DI of the song's own stem, re-rendered). Blind "
        "three-way: untouched chain, knob-tuned, full match-EQ — "
        "shuffled per clip. Pick the X closest to the ref's tone. "
        "Fill COMPLETELY before opening ANSWER_KEY.json.\n\n"
        + "\n".join(verdict_lines) + "\n",
        encoding="utf-8",
    )
    print(f"\npack: {args.out_dir} ({len(key)} clips)")
    print("fill VERDICTS.md before opening ANSWER_KEY.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
