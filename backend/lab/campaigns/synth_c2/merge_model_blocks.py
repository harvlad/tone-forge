"""Merge tiny model-size blocks from MSST's own example configs into the arm
configs. We never invent architecture hyperparameters: each arm copies the
`model` block from an MSST-shipped config for that arch (smallest variant
found), so every knob has an upstream-validated origin. P1 ranks arms; sizes
scale in P2 only for the winner."""
from __future__ import annotations

import argparse
from pathlib import Path

import yaml


# MSST example configs carry `!!python/tuple` tags (their loader handles
# them); SafeLoader refuses. Round-trip them faithfully: construct as tuple,
# re-emit with the same tag so the merged config stays byte-equivalent for
# MSST's own loader.
class _MsstLoader(yaml.SafeLoader):
    pass


_MsstLoader.add_constructor(
    "tag:yaml.org,2002:python/tuple",
    lambda loader, node: tuple(loader.construct_sequence(node)))


class _MsstDumper(yaml.SafeDumper):
    pass


_MsstDumper.add_representer(
    tuple,
    lambda dumper, value: dumper.represent_sequence(
        "tag:yaml.org,2002:python/tuple", list(value)))


def _load(path: Path) -> dict:
    return yaml.load(path.read_text(), Loader=_MsstLoader)


def _dump(cfg: dict) -> str:
    return yaml.dump(cfg, Dumper=_MsstDumper, sort_keys=False)


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
        cfg = _load(cfg_p)
        src_p = find_source(msst, arm)
        src = _load(src_p)
        if "model" in src:
            cfg["model"] = src["model"]
            # The example blocks are musdb-shaped (4 sources, sometimes
            # mono) — P1 arms B/C died on shape errors from exactly this.
            # Re-pin only keys the block already carries; never invent.
            for key, val in (("stereo", True),
                             ("num_stems", 2),
                             ("sources", ["synth", "rest"]),
                             ("instruments", ["synth", "rest"])):
                if key in cfg["model"]:
                    cfg["model"][key] = val
        # Audio block must match the arch's expectations (chunk sizes differ
        # per family) — take the upstream one, then re-pin our sample rate.
        if "audio" in src:
            cfg["audio"] = src["audio"]
            cfg["audio"]["sample_rate"] = 44100
        cfg_p.write_text(_dump(cfg))
        print(f"{arm}: model+audio blocks from {src_p.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
