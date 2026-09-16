# riley_synth_c2 — synth-separation self-train

Campaign scripts for the masking residual refiner (input: htdemucs_6s
residual `other`; output: `{synth, rest}` with `synth + rest ≡ input`).
Design, corpus verdicts, budget gates and the c1 post-mortem live in the
Phase-0 record (memory: synth-separation-ceiling; counsel items:
OUTSTANDING.md). Total cap **$60**: P1 ≤$15, P2 ≤$40, P3 ≤$5.

## P1 — recipe validation (this directory)

| File | Role |
|---|---|
| `manufacture_pairs.py` | rest = mix − synth (verified −61 dB), stats-driven gain/width augmentation, dual htdemucs pass → residual-domain `{mixture, synth, rest}` pairs |
| `arm_config.py` | derives the 3 arm configs from the **validated c5 recipe** (loss/audio blocks verbatim) |
| `merge_model_blocks.py` | model/audio blocks copied from MSST's own example configs — no invented hyperparameters |
| `smoke_eval.py` | SI-SDR vs identity/half baselines — **kill + ranking only, never promotion** |
| `pod_entry_synth_c2_p1.sh` | preflight → fetch → manufacture → 3-arm race (2 h caps) → inference → smoke eval → ship artifacts to the volume |

Arms: `a_htdemucs`, `b_melroformer`, `c_scnet`, all from scratch.
(Deviation from Phase-0: the htdemucs *fine-tune* arm was dropped — the
pretrained 4/6-source heads don't survive a 2-source swap, so a partial
load validates nothing at P1 scale.)

## Ignition (operator steps)

1. Stage the P1 subset + code on the volume, served via `/factory`:
   `slakh_synth_p1` subset tarball (≤48 train + 8 valid tracks),
   `synth_c2_code.tgz` (this directory), `c5_bundle.tgz` (the surviving validated recipe; c6 was lost).
2. Preflight checklist (memory: gpu-launch-preflight) — disk <85 %, nginx
   log alive, transfer hashes, image CUDA vs host driver, heartbeats.
3. Create pod named `riley_synth_c2_p1` (A40 preferred, ~$0.40/h; the
   analysis autoscaler must never touch `riley_*` pods) with env
   `SYNTH_RETURN_KEY`, `JAMN_FACTORY=https://jamn.app/factory`.
4. Watch `http://<pod>-8888.proxy.runpod.net/p1.log` + `hb_*` files.
5. Kill criteria (from the plan): difference-targets audibly incoherent,
   no arm beats `identity` on smoke metrics, or the 6-clip pre-read says
   the split hurts. Kill cost ≤$15.

## Doctrine reminders

- Metrics are smoke; promotion is a ≥24-clip **blind** ear pack vs the raw
  residual (P3), through the specialist-registry license gate.
- Data ops on the VPS/volume, never the Mac. Checkpoints to volume + R2.
- Cambridge-MT is **permanently banned** for training (see OUTSTANDING.md).
