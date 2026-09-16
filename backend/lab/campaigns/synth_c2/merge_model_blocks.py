"""Merge tiny model-size blocks from MSST's own example configs into the arm
configs. We never invent architecture hyperparameters: each arm copies the
`model` block from an MSST-shipped config for that arch (smallest variant
found), so every knob has an upstream-validated origin. P1 ranks arms; sizes
scale in P2 only for the winner."""
from __future__ import annotations

import argparse
from pathlib import Path

import yaml

# Preferred source config per arch, smallest-first globs.
SOURCES = {
    "a_htdemucs": ["configs/config_musdb18_htdemucs.yaml", "configs/*htdemucs*.yaml"],
    "b_melroformer": ["configs/config_musdb18_mel_band_roformer.yaml",
                      "configs/*mel_band_roformer*.yaml"],
    "c_scnet": ["configs/config_musdb18_scnet.yaml", "configs/*scnet*.yaml"],
}


def find_source(msst: Path, arm: str) -> Path:
    for pat in SOURCES[arm]:
        hits = sorted(msst.glob(pat))
        if hits:
            return hits[0]
    raise SystemExit(f"no MSST example config found for {arm} — refusing to invent one")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--msst", required=True)
    ap.add_argument("--configs", required=True)
    args = ap.parse_args()
    msst = Path(args.msst)
    for arm in SOURCES:
        cfg_p = Path(args.configs) / f"config_{arm}.yaml"
        cfg = yaml.safe_load(cfg_p.read_text())
        src_p = find_source(msst, arm)
        src = yaml.safe_load(src_p.read_text())
        if "model" in src:
            cfg["model"] = src["model"]
        # Audio block must match the arch's expectations (chunk sizes differ
        # per family) — take the upstream one, then re-pin our sample rate.
        if "audio" in src:
            cfg["audio"] = src["audio"]
            cfg["audio"]["sample_rate"] = 44100
        cfg_p.write_text(yaml.safe_dump(cfg, sort_keys=False))
        print(f"{arm}: model+audio blocks from {src_p.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
