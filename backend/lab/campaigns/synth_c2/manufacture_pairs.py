"""riley_synth_c2 P1 — manufacture residual-domain training pairs.

Runs ON THE POD (htdemucs on GPU; ~1-2 s/track there vs ~40 s CPU).

Per slakh_synth track ({mix,synth,guitar}.flac; rest = mix - synth holds to
-61 dB, verified 2026-09-16) and per augmentation variant:

    synth' = width_aug(gain(synth))            # stats-driven, deterministic
    mix'   = rest + synth'
    input  = residual(htdemucs_6s(mix'))       # the deployment-domain input
    t_syn  = input - residual(htdemucs_6s(rest))   # paired-mix difference
    t_rest = residual(htdemucs_6s(rest))

The paired-difference target places supervision in the TRUE deployment
domain (separator artifacts included) — the Riley/kong domain-matching
principle. Augmentation ranges come from measured real-library residuals
(2026-09-16): mid level -57..-18 dBFS (median -25) and BIMODAL stereo
width — most residuals near-mono (side-mid ~ -50 dB) but wide-pad songs
(M83) sit at side-mid ~ -1.4 dB. Slakh stems are MONO, so the width aug
is what buys the wide-pad regime at all.

Deterministic: every random draw is seeded by (track, variant) so re-runs
and resumes bit-match (corpus_hash discipline).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

SR = 44100

# Variant grid (stats-driven). gain_db: where the synth sits relative to its
# Slakh level; width_mode: near-mono vs wide (Haas + decorrelation), matching
# the measured bimodal deployment distribution, weighted toward wide — the
# hard, motivating regime.
VARIANTS = [
    {"id": "v0", "gain_db": 0.0,   "width": "mono"},
    {"id": "v1", "gain_db": 0.0,   "width": "wide"},
    {"id": "v2", "gain_db": -8.0,  "width": "wide"},
    {"id": "v3", "gain_db": +6.0,  "width": "wide"},
    {"id": "v4", "gain_db": -16.0, "width": "mono"},   # buried-synth regime
    {"id": "v5", "gain_db": 0.0,   "width": "extreme"},  # M83-class full-wide
]


def _seed(track: str, variant: str) -> int:
    return int(hashlib.sha1(f"{track}:{variant}".encode()).hexdigest()[:8], 16)


def _to_stereo(y: np.ndarray) -> np.ndarray:
    if y.ndim == 1:
        return np.stack([y, y], axis=1)
    return y


def width_aug(y: np.ndarray, mode: str, rng: np.random.Generator) -> np.ndarray:
    """Pseudo-stereo width for a mono synth stem.

    mono    — leave as dual-mono (the near-mono deployment mode).
    wide    — Haas delay (5-25 ms) on one side + gentle side gain.
    extreme — Haas + decorrelating short random-phase FIR on the right,
              side level ~= mid (the measured M83 regime, side-mid -1.4 dB).
    """
    y = _to_stereo(np.asarray(y, dtype=np.float32))
    if mode == "mono":
        return y
    L = y[:, 0].copy()
    R = y[:, 1].copy()
    delay = int(rng.uniform(0.005, 0.025) * SR)
    R = np.concatenate([np.zeros(delay, dtype=R.dtype), R[:-delay]]) if delay else R
    if mode == "extreme":
        # 32-tap random allpass-ish FIR decorrelator on R.
        taps = rng.uniform(-1, 1, 32).astype(np.float32)
        taps /= np.sqrt((taps**2).sum()) + 1e-9
        R = np.convolve(R, taps, mode="same").astype(np.float32)
    out = np.stack([L, R], axis=1)
    # Renormalize to the input RMS so gain_db stays the only level knob.
    in_rms = float(np.sqrt((y**2).mean())) + 1e-12
    out_rms = float(np.sqrt((out**2).mean())) + 1e-12
    return (out * (in_rms / out_rms)).astype(np.float32)


def load_separator():
    import torch
    from demucs.apply import apply_model
    from demucs.pretrained import get_model

    model = get_model("htdemucs_6s")
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model.to(device)

    def residual(audio: np.ndarray) -> np.ndarray:
        wav = torch.tensor(audio.T, dtype=torch.float32)[None].to(device)
        with torch.no_grad():
            out = apply_model(model, wav, shifts=0, overlap=0.1)[0]
        idx = model.sources.index("other")
        return out[idx].cpu().numpy().T

    return residual


def process_track(tdir: Path, out_root: Path, residual, variants) -> int:
    mix_p = tdir / "mixture.flac"
    if not mix_p.exists():
        mix_p = tdir / "mix.flac"
    mix, _ = sf.read(mix_p, dtype="float32")
    syn, _ = sf.read(tdir / "synth.flac", dtype="float32")
    n = min(len(mix), len(syn))
    mix, syn = _to_stereo(mix[:n]), _to_stereo(syn[:n])
    rest = mix - syn
    # residual(rest) is variant-independent — compute once per track.
    res_rest = residual(rest)
    made = 0
    for v in variants:
        out_dir = out_root / tdir.name / v["id"]
        done = out_dir / ".done"
        if done.exists():
            continue
        rng = np.random.default_rng(_seed(tdir.name, v["id"]))
        syn_v = width_aug(syn.mean(axis=1), v["width"], rng) * (10 ** (v["gain_db"] / 20))
        mix_v = rest + syn_v
        res_mix = residual(mix_v)
        m = min(len(res_mix), len(res_rest))
        out_dir.mkdir(parents=True, exist_ok=True)
        sf.write(out_dir / "mixture.flac", res_mix[:m], SR)
        sf.write(out_dir / "synth.flac", (res_mix[:m] - res_rest[:m]), SR)
        sf.write(out_dir / "rest.flac", res_rest[:m], SR)
        done.write_text(json.dumps(v))
        made += 1
    return made


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="/workspace/slakh_synth/train")
    ap.add_argument("--out", default="/workspace/pairs/train")
    ap.add_argument("--limit", type=int, default=0, help="track cap (P1 tiny)")
    ap.add_argument("--variants", default="v0,v1,v2,v3,v4,v5")
    args = ap.parse_args()
    want = {v.strip() for v in args.variants.split(",")}
    variants = [v for v in VARIANTS if v["id"] in want]
    residual = load_separator()
    tracks = sorted(p for p in Path(args.src).iterdir()
                    if (p / "mixture.flac").exists() or (p / "mix.flac").exists())
    if args.limit:
        tracks = tracks[: args.limit]
    total = 0
    for i, t in enumerate(tracks):
        try:
            total += process_track(t, Path(args.out), residual, variants)
        except Exception as e:  # noqa: BLE001 — one bad track never kills the run
            print(f"SKIP {t.name}: {e}", flush=True)
        if i % 10 == 0:
            print(f"[{i}/{len(tracks)}] pairs made: {total}", flush=True)
    print(f"DONE tracks={len(tracks)} pairs={total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
