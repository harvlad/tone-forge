#!/usr/bin/env bash
# riley_synth_c2 — P1 recipe-validation pod entry (budget arm: <= $15).
#
# Pattern: pod_entry_synth1.sh lineage + the GPU-launch preflight checklist.
# Stages: preflight -> fetch P1 subset -> manufacture residual pairs (GPU
# htdemucs) -> derive arm configs from the validated c6 recipe -> train the
# three tiny arms -> smoke eval vs the IDENTITY MASK baseline -> ship
# checkpoints + samples + logs back to the volume (artifact-durability rule).
#
# Heartbeats: every stage writes hb_* files under /workspace, served on :8888
# (http.server) so progress is scrapeable without SSH — the nginx-log-freeze
# lesson says never trust the serving box's logs as the ground truth.
#
# Required env: SYNTH_RETURN_KEY (base64 ssh key, scp back to the volume),
#               JAMN_FACTORY=https://jamn.app/factory
set -uo pipefail
cd /workspace
echo "== riley_synth_c2 P1 $(date -u +%FT%TZ) =="
( python3 -m http.server 8888 >/dev/null 2>&1 & ) || true
exec > >(tee -a /workspace/p1.log) 2>&1

hb() { echo "$(date -u +%FT%TZ) $2" > "/workspace/hb_$1"; }

# ---- preflight (checklist items that can fail a rented GPU) ----
hb preflight start
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader || { echo "FATAL no GPU"; exit 3; }
python3 - <<'PY' || { echo "FATAL cuda compute broken"; exit 3; }
import torch; a=torch.randn(1024,1024,device="cuda"); print("matmul ok", float((a@a).sum()))
PY
df -h /workspace | tail -1
echo "$SYNTH_RETURN_KEY" | base64 -d > /tmp/ret_key && chmod 600 /tmp/ret_key
hb preflight ok

# ---- fetch P1 bundle (subset tarball staged by the ignition script) ----
hb fetch start
curl -fsSL -o p1_subset.tgz "$JAMN_FACTORY/synth_c2_p1_subset.tgz" || { echo "FATAL subset fetch"; exit 3; }
tar xzf p1_subset.tgz && rm p1_subset.tgz          # -> slakh_synth_p1/{train,valid}
curl -fsSL -o c6_bundle.tgz "$JAMN_FACTORY/c6_bundle.tgz" && tar xzf c6_bundle.tgz && rm c6_bundle.tgz
curl -fsSL -o campaign.tgz "$JAMN_FACTORY/synth_c2_code.tgz" && tar xzf campaign.tgz && rm campaign.tgz
pip install -q soundfile pyyaml demucs 2>&1 | tail -1
git clone -q https://github.com/ZFTurbo/Music-Source-Separation-Training msst \
  && cd msst && git checkout -q "${MSST_REF:-master}" && pip install -q -r requirements.txt 2>&1 | tail -1 && cd ..
hb fetch ok

# ---- stage 1: manufacture pairs (GPU htdemucs, deterministic) ----
hb manufacture start
python3 synth_c2/manufacture_pairs.py --src slakh_synth_p1/train --out pairs/train --limit "${P1_TRACKS:-40}"
python3 synth_c2/manufacture_pairs.py --src slakh_synth_p1/valid --out pairs/valid --limit 8
hb manufacture ok

# ---- stage 2: derive arm configs from the validated c6 recipe ----
C6_CFG=$(ls c6*/config*.yaml c6*/*.yaml 2>/dev/null | head -1)
python3 synth_c2/arm_config.py --c6-config "$C6_CFG" --out-dir arm_configs
# Merge tiny model blocks from MSST's own example configs (small variants).
python3 synth_c2/merge_model_blocks.py --msst msst --configs arm_configs

# ---- stage 3: the arm race (time-capped; num_epochs runaway lesson) ----
for ARM in a_htdemucs b_melroformer c_scnet; do
  hb "train_$ARM" start
  MT=$(python3 -c "import yaml;print({'a_htdemucs':'htdemucs','b_melroformer':'mel_band_roformer','c_scnet':'scnet'}['$ARM'])")
  timeout 7200 python3 msst/train.py \
      --model_type "$MT" \
      --config_path "arm_configs/config_${ARM}.yaml" \
      --results_path "results/$ARM" \
      --data_path pairs/train \
      --valid_path pairs/valid \
      --num_workers 6 --device_ids 0 \
    || echo "ARM $ARM exited $? (timeout=2h cap)"
  hb "train_$ARM" ok
done

# ---- stage 3b: inference on valid pairs per arm (feeds smoke eval) ----
for ARM in a_htdemucs b_melroformer c_scnet; do
  CKPT=$(ls -t results/$ARM/*.ckpt 2>/dev/null | head -1)
  [ -z "$CKPT" ] && { echo "no ckpt for $ARM — skipping inference"; continue; }
  MT=$(python3 -c "import yaml;print({'a_htdemucs':'htdemucs','b_melroformer':'mel_band_roformer','c_scnet':'scnet'}['$ARM'])")
  python3 msst/inference.py --model_type "$MT" \
      --config_path "arm_configs/config_${ARM}.yaml" \
      --start_check_point "$CKPT" \
      --input_folder pairs/valid --store_dir "results/$ARM/pred" \
    || echo "inference $ARM failed"
done

# ---- stage 4: smoke eval vs identity mask (metrics = smoke only) ----
hb eval start
python3 synth_c2/smoke_eval.py --pairs pairs/valid --results results --out eval_p1.json
cat eval_p1.json
hb eval ok

# ---- stage 5: ship artifacts (durability rule: volume + hashes) ----
hb ship start
tar czf p1_artifacts.tgz results eval_p1.json arm_configs p1.log
sha256sum p1_artifacts.tgz | tee p1_artifacts.sha
scp -o StrictHostKeyChecking=no -i /tmp/ret_key p1_artifacts.tgz p1_artifacts.sha \
    root@jamn.app:/mnt/HC_Volume_106533567/factory/synth_c2/ && hb ship ok || hb ship FAILED
echo "== P1 DONE $(date -u +%FT%TZ) =="
