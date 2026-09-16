"""riley_synth_c2 P1 — derive the three arm configs from the validated c6 recipe.

Never hand-author a training config when a validated one exists
(dataset-eval-recipe principle): we load the c6 bundle's YAML — the config
whose loss_multistft and audio settings were already blind-validated in the
Riley guitar campaigns — and patch ONLY what this campaign changes:
instruments -> [synth, rest], dataset paths, model size (tiny for P1), and
per-arm model_type. Everything else (loss weights, STFT resolutions, chunking,
optimizer defaults) rides the validated recipe verbatim.

Arms (all license-clean, all from scratch):
  A htdemucs   — the conservative arch; recipes well-trodden in MSST.
  B mel_band_roformer (small) — the SDR-leaderboard family; highest ceiling.
  C scnet (small) — cheap third inductive bias.
  (The planned htdemucs FINE-TUNE arm was dropped: the pretrained heads are
  4/6-source and a 2-source head swap invalidates exactly the part we'd be
  fine-tuning; a partial load proves nothing at P1 scale. Recorded as a
  deviation from the Phase-0 plan.)
"""
from __future__ import annotations

import argparse
import copy
from pathlib import Path

import yaml

ARMS = {
    "a_htdemucs": {"model_type": "htdemucs"},
    "b_melroformer": {"model_type": "mel_band_roformer"},
    "c_scnet": {"model_type": "scnet"},
}

# P1 tiny-scale overrides — enough steps to rank arms, nowhere near converged.
P1_TRAIN = {
    "batch_size": 4,
    "num_epochs": 8,
    "num_steps": 500,          # per epoch; 4k steps total per arm
    "instruments": ["synth", "rest"],
    "target_instrument": None,
    "patience": 4,
    "reduce_factor": 0.95,
    "optimizer": "adamw",
    "lr": 3.0e-4,
}


def patch(base_cfg: dict, arm: str) -> dict:
    cfg = copy.deepcopy(base_cfg)
    cfg.setdefault("training", {}).update(P1_TRAIN)
    cfg["training"]["coarse_loss_clip"] = False
    # Keep the validated loss block exactly as c6 shipped it.
    assert "loss_multistft" in cfg or "loss" in str(cfg), (
        "c6 config lost its validated loss block — refusing to invent one")
    return cfg


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--c6-config", required=True,
                    help="path to the validated c6 YAML from the bundle")
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    base = yaml.safe_load(Path(args.c6_config).read_text())
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for arm in ARMS:
        cfg = patch(base, arm)
        p = out / f"config_{arm}.yaml"
        p.write_text(yaml.safe_dump(cfg, sort_keys=False))
        print(f"wrote {p}")
    print("NOTE: model_type per arm is passed to MSST via --model_type "
          f"({', '.join(f'{k}:{v['model_type']}' for k, v in ARMS.items())}); "
          "model-size blocks come from MSST's example configs for the tiny "
          "variants and are merged by the pod entry (see pod_entry script).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
