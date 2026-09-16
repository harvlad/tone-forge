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
tar --no-same-owner -xzf p1_subset.tgz && rm p1_subset.tgz          # -> slakh_synth_p1/{train,valid}
curl -fsSL -o c5_bundle.tgz "$JAMN_FACTORY/c5_bundle.tgz" && tar --no-same-owner -xzf c5_bundle.tgz && rm c5_bundle.tgz
curl -fsSL -o campaign.tgz "$JAMN_FACTORY/synth_c2_code.tgz" && tar --no-same-owner -xzf campaign.tgz && rm campaign.tgz
pip install -q soundfile pyyaml demucs 2>&1 | tail -1
# Subshell so a failed checkout can never leak cwd into later stages (the
# first P1 pod died exactly that way: MSST's default branch is main, the
# checkout of "master" failed, cd .. never ran, every path resolved under
# msst/). No MSST_REF -> ride default HEAD, but always log the commit.
git clone -q https://github.com/ZFTurbo/Music-Source-Separation-Training msst || { echo "FATAL msst clone"; exit 3; }
( cd msst \
  && { [ -z "${MSST_REF:-}" ] || git checkout -q "$MSST_REF"; } \
  && echo "msst @ $(git rev-parse --short HEAD)" \
  && grep -viE "wxpython|pyaudio" requirements.txt > req_train.txt \
  && pip install -q -r req_train.txt 2>&1 | tail -1 ) || { echo "FATAL msst setup"; exit 3; }
# wxpython/pyaudio are MSST's GUI extras — they need system libs the pod
# lacks and training never imports them.
[ -f msst/train.py ] || { echo "FATAL msst/train.py missing"; exit 3; }
hb fetch ok

# ---- stage 1: manufacture pairs (GPU htdemucs, deterministic) ----
# PAIRS_URL short-circuits the ~40 min GPU manufacture with a prebuilt
# tarball (salvaged/shipped from a previous run) — durability rule both ways.
hb manufacture start
if [ -n "${PAIRS_URL:-}" ]; then
  curl -fsSL -o pairs.tgz "$PAIRS_URL" && tar --no-same-owner -xzf pairs.tgz && rm pairs.tgz \
    || { echo "FATAL pairs fetch"; hb manufacture FAILED; exit 3; }
else
  python3 synth_c2/manufacture_pairs.py --src slakh_synth_p1/train --out pairs/train --limit "${P1_TRACKS:-40}" \
    || { echo "FATAL manufacture train"; hb manufacture FAILED; exit 3; }
  python3 synth_c2/manufacture_pairs.py --src slakh_synth_p1/valid --out pairs/valid --limit 8 \
    || { echo "FATAL manufacture valid"; hb manufacture FAILED; exit 3; }
  # Ship pairs immediately — a later-stage failure must never cost the
  # manufacture GPU time again.
  tar czf pairs_ship.tgz pairs && scp -o StrictHostKeyChecking=no -i /tmp/ret_key pairs_ship.tgz \
      root@jamn.app:/mnt/HC_Volume_106533567/factory/synth_c2_p1_pairs.tgz \
    && rm pairs_ship.tgz || echo "WARN pairs ship failed (continuing)"
fi
[ -n "$(ls pairs/train 2>/dev/null)" ] || { echo "FATAL no pairs produced"; hb manufacture FAILED; exit 3; }
# MSST dataset_type 1 wants a FLAT dir of track folders each holding
# <instrument>.flac; our pairs are nested track/variant. Symlink-flatten for
# training, and give inference a flat dir of mixture files (its default
# template writes store_dir/<file_name>/<instr>.flac). valid.py rglobs
# mixture.flac so the nested tree is fine as valid_path.
rm -rf flat_train infer_in && mkdir -p flat_train infer_in
for t in pairs/train/*/; do tn=$(basename "$t")
  for v in "$t"*/; do vn=$(basename "$v")
    ln -s "$(readlink -f "$v")" "flat_train/${tn}__${vn}"
  done
done
for t in pairs/valid/*/; do tn=$(basename "$t")
  for v in "$t"*/; do vn=$(basename "$v")
    ln -s "$(readlink -f "$v")/mixture.flac" "infer_in/${tn}__${vn}.flac"
  done
done
echo "flat_train: $(ls flat_train | wc -l) dirs, infer_in: $(ls infer_in | wc -l) mixtures"
hb manufacture ok

# ---- stage 2: derive arm configs from the validated c6 recipe ----
# c6 bundle was lost to a self-loop symlink; c5 is the surviving
# blind-validated recipe from the same campaign lineage.
C6_CFG=$(ls configs/c5*.yaml 2>/dev/null | head -1)
[ -n "$C6_CFG" ] || { echo "FATAL c5 recipe missing"; exit 3; }
python3 synth_c2/arm_config.py --c6-config "$C6_CFG" --out-dir arm_configs || { echo "FATAL arm_config"; exit 3; }
# Merge tiny model blocks from MSST's own example configs (small variants).
python3 synth_c2/merge_model_blocks.py --msst msst --configs arm_configs || { echo "FATAL merge blocks"; exit 3; }

# ---- stage 3: the arm race (time-capped; num_epochs runaway lesson) ----
for ARM in a_htdemucs b_melroformer c_scnet; do
  hb "train_$ARM" start
  MT=$(python3 -c "import yaml;print({'a_htdemucs':'htdemucs','b_melroformer':'mel_band_roformer','c_scnet':'scnet'}['$ARM'])")
  timeout 7200 python3 msst/train.py \
      --model_type "$MT" \
      --config_path "arm_configs/config_${ARM}.yaml" \
      --results_path "results/$ARM" \
      --data_path flat_train \
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
      --input_folder infer_in --store_dir "results/$ARM/pred" \
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
