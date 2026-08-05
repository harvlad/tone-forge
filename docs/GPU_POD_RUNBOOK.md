# GPU Pod Runbook — RunPod + ZFTurbo MSST training

Operational findings from the T0/T1 recipe experiment + T2 full-corpus run
(2026-07-28/29). Written so the next GPU job costs a fraction of the learning tax
paid here. Pairs with `SEPARATOR_TIER2_PROPOSAL.md`. T2 addendum below §0.

---

## 0. TL;DR — the rules that would have saved ~$5

1. **Dry-run the FULL train loop locally first ($0).** Every recipe bug (deps,
   stereo, EMA, config) is reproducible on CPU with 2 steps. Never debug on rented GPU.
2. **Hard-cap `num_epochs` in the config, not just `num_steps`.** The trainer runs
   `num_epochs × num_steps` batches. Patching only `num_steps` inherits the config's
   `num_epochs: 1000` → 14h runaway. This one bug cost ~$6.
3. **Ship artifacts per-phase, incrementally.** A single end-of-run archive is a
   hostage. One truncated download + a chained `rm` destroyed a 14h run's output.
4. **Verify (md5) BEFORE any delete.** Never chain `rm remote` after an unverified `scp`.
5. **Put the kill-switch on an always-on host (the VPS), not this Mac.** Local
   background watchdogs get killed with the session; a VPS cron does not.
6. **Ship a setup HEARTBEAT.** Without it you're blind until phase 1 (~1-2h). A tiny
   marker file scp'd at each setup stage (bundle_ready, deps_ready) confirms progress
   in ~2min AND is how you tell a working pod from a dead host.
7. **A pod with negative/zero `uptimeInSeconds` + 0% GPU/CPU for >10min is a DEAD HOST.**
   RunPod-side container-init failure, not your bug (nginx will still show the bundle
   fetched). Kill and relaunch — it lands on a different host. Do not wait it out.

---

## Data integrity audit (across the whole T0→T2 effort)

**One permanent loss, fully recovered; nothing else lost.**
- **Lost:** the v1 T0/T1 run's artifacts (checkpoints, loss curves, separated stems)
  — destroyed by the retrieval race (stale `rm remote` fired on a truncated download,
  killing the pristine VPS copy; only a corrupt checkpoint survived). **Superseded by
  the v2 rerun** ($0.34), which reproduced everything. Net: ~$6 GPU + time, **zero
  knowledge/capability lost**.
- **Never lost:** source data (Slakh 2100), lab prediction cache, derived mixes,
  code, configs, provenance registry, experiment records, memory, docs, all prior
  research (discovery waves, reconciliation, tax scorecards). None were ever at risk —
  they live in the repo / lab_data, not in the ephemeral pod/VPS scratch path.
- **T2 artifacts:** redundantly stored — full checkpoint bundles + log + separated
  stems on the VPS AND best checkpoints local + md5-verified.
- **Rule going forward:** trained checkpoints are provenance-sensitive assets →
  archive to R2 (durable, versioned) immediately after retrieval, don't leave them
  only in VPS scratch. Verify (md5) before any delete; never chain `rm` to an
  unverified transfer.

## T2 addendum (2026-07-29, full-corpus run)

- **Dead-host failure mode:** first T2 pod (A40 secure) reported `uptimeInSeconds: -11`
  and 0% GPU/CPU/mem for 10+ min while `desiredStatus: RUNNING`. nginx confirmed the
  bundle was fetched, so the entrypoint started — but the container never truly ran.
  Negative uptime is the tell. Terminated + relaunched → fresh host trained fine
  (GPU 100%, uptime positive, heartbeat in 2min). Cost of the dead host: ~$0.20.
- **Heartbeat pattern (added to pod_entry + run script):** `HB(){ touch /tmp/hb_$1; scp
  ... :/root/<job>_artifacts/; }` called at `setup_start`, `deps_ready`, `bundle_ready`.
  Markers appear on the VPS within minutes → instant health signal, no pod SSH needed.
- **Update the runner without re-uploading the big bundle:** have `pod_entry.sh` curl
  `run_<job>.sh` from the VPS static dir AFTER `tar xzf bundle.tgz` (overrides the
  bundled copy). Bundle (dataset+configs, GBs) stays put; iterate the ~3KB runner freely.
- **Home-uplink is the real staging bottleneck:** 1GB Mac→VPS took ~50min at ~0.3MB/s.
  GPU is NOT running during upload ($0), so background it and launch on completion.
  For bigger T3 datasets, stage via R2 (already configured, bucket `toneforge`) instead
  of routing through the home Mac, OR build smaller: FLAC + a per-track length cap
  (90s) took the 160-track set to **1.0GB** (vs ~24GB for full-length WAV).
- **A40 48GB SECURE $0.44/hr remained the only reliably-available card** across all
  T2 relaunches (community A40 and all 4090s: SUPPLY_CONSTRAINT). Batch 8 fits easily.
- **T2 measured:** heartbeat→GPU-100% within ~2min of a healthy host; setup (pip + demucs
  + 1GB bundle pull VPS→pod) ~2min (datacenter link fast, unlike home uplink).

---

---

## 1. Provisioning (RunPod GraphQL API)

- Auth: `POST https://api.runpod.io/graphql?api_key=$KEY` with
  `Content-Type: application/json`. **The `?api_key=` query-param form works;
  `Authorization: Bearer` returned 403 (cloudflare 1010).** Use the query param.
- Balance/pods: `query { myself { clientBalance pods { id desiredStatus runtime { uptimeInSeconds } } } }`
- Deploy: `mutation { podFindAndDeployOnDemand(input: {...}) { id costPerHr } }`
- Terminate: `mutation { podTerminate(input: {podId: "..."}) }`

### Availability is the real constraint, not price
Live stock survey (2026-07-29) via `gpuTypes { lowestPrice(input:{gpuCount:1}) { stockStatus } }`:
most consumer cards showed `stockStatus: Low`; **only A40 (48GB) was `High`.**
RTX 4090 community repeatedly failed: `"no longer any instances available"`
(SUPPLY_CONSTRAINT) and `"machine does not have the resources"`. **Cascade across
GPU types AND cloudType (COMMUNITY→SECURE) programmatically** — don't pin one card.
We landed on **A40 SECURE $0.44/hr** every time; community A40 was itself unavailable.

### Prices seen (community / secure)
A40 48GB $0.35/$0.44 · RTX 4090 24GB $0.34/$0.69 · RTX 3090 $0.22 · L40S 48GB $0.79/$0.99 ·
A100 80GB $1.19/$1.39 · H100 $1.99/$2.89. Per-second billing. **A40 48GB is the value
pick for this workload** — runs stock 48GB configs, reliably in stock, cheap.

### Image
`runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04` worked. `containerDiskInGb: 25`,
`volumeInGb: 0` (no persistent volume — see §3 caveat). Smaller disk requests did NOT
help the 4090 supply error (that was host capacity, not disk).

### Headless bootstrap pattern (no inbound SSH)
Pods here had no registered SSH key. Bootstrap via `dockerArgs` entrypoint that pulls
its own script over HTTPS:
```
dockerArgs: "bash -c 'mkdir -p /workspace && cd /workspace &&
  curl -fsSL -o pod_entry.sh https://jamn.app/static/pod_entry.sh && bash pod_entry.sh'"
```
Job bundle (dataset+configs+scripts) staged as a static file on the VPS
(`/opt/toneforge/backend/static/`, served by nginx). Pod curls it. Clean, no
interactive login.

### Artifact return (no inbound SSH to pod)
Pod pushes OUT to the VPS via a **restricted scp-only** deploy key:
`command="scp -t /root/t0t1_artifacts/",restrict <pubkey>` in the VPS
`authorized_keys`. Pass the private key to the pod as a base64 env var
(`T0T1_RETURN_KEY`), decode to `/tmp/ret_key` in the entrypoint. Ephemeral —
remove the authorized_keys line on cleanup.

---

## 2. ZFTurbo MSST trainer specifics

- Repo: `github.com/ZFTurbo/Music-Source-Separation-Training` (MIT code).
- **`requirements.txt` pulls GUI deps that fail on headless** (pyaudio → needs
  portaudio.h; wxpython → needs GTK). Filter them: `grep -vE 'pyaudio|wxpython'`.
- **Config YAML uses `!!python/tuple` tags** → `yaml.safe_load` fails. Edit configs
  with regex/sed line-substitution, NOT round-trip yaml load/dump. `FullLoader`
  works if you must load.
- Model built from config: `MelBandRoformer(**dict(config.model))` — `stereo: true`
  REQUIRES stereo (2-channel) audio in **every** training AND validation file.
  Slakh stems/mixes are often mono → assertion crash. **Force stereo at dataset build**
  (`np.repeat(mono, 2, axis=1)`), verify with `sf.info(f).channels == 2` on all files.
- **EMA (`ema_momentum > 0`) crashed** with `AveragedModel` shape mismatch on this
  torch/model combo. Set `ema_momentum: 0` for short runs (it's a final-polish feature).
- Training loop: `for epoch in range(num_epochs): <num_steps batches>`. **Validation
  runs every epoch** = full separation over all held-out tracks (the slow part).
  Minimize epochs to minimize validation cost: prefer few epochs × many steps.
- Fine-tune from checkpoint: `--start_check_point <path>`. htdemucs base:
  `demucs.pretrained.get_model("htdemucs_6s")` caches a `.th` file, pass its path.
- Flags used: `--dataset_type 1` (folder-per-track: `mixture.wav`+stems),
  `--use_standard_loss --num_workers 4 --seed 42`.
- Missing-at-runtime deps beyond requirements: `ml_collections auraloss
  pytorch_optimizer torch_log_wmse audiomentations` (+ wandb; set `WANDB_MODE=disabled`).

### Measured throughput (A40, 2026-07-29)
- **T0 both candidates (200 steps + 1 validation each): ~10 min total.** From-scratch
  mel-roformer step ~12s/it early; htdemucs ~6.5s/it (2-batch, small chunks).
- Model sizes confirmed: mel-roformer small (dim128/depth4) = **32.04M params / 122MB**;
  htdemucs 6s standard.
- T0 held-out SDR (200 steps, sanity only): B2 guitar 0.74; B1 guitar 1.28 / avg 3.99.

---

## 3. Failure log (what actually went wrong, in order)

| # | Failure | Root cause | Fix |
|---|---------|-----------|-----|
| R1 | crash ~4min | pyaudio/wxpython wheels fail headless | filter requirements |
| R2 | crash ~4min | `stereo=true` vs mono Slakh audio | force-stereo dataset build |
| local | 5 iterations | missing deps, YAML tuple tags, EMA shape crash, mono mixes | all fixed on CPU at $0 |
| **R3** | **14h runaway, $6** | **`num_epochs:1000` inherited; only `num_steps` was patched** | **cap `num_epochs` per phase** |
| R3 retrieval | **artifacts lost** | stale bg cmd chained `rm remote` after truncated scp; race with re-download | per-phase ship + md5-before-delete |
| watchdog | local loops killed | Mac bg jobs die with session | move cron to VPS |
| watchdog v2 | script no-op ~15min | heredoc quote-escaping broke `term()` (`unexpected EOF`) | write script as a file, `scp` it, `bash -n` check |

### The expensive lesson (R3)
`num_epochs × num_steps` is total work. The `run()` helper patched `num_steps` via sed
but left `num_epochs` at the config default (1000, from the htdemucs 6-stem config).
On 5-track overfit data, validation SDR micro-improves forever → no early stop → ran
to the wall-clock we happened to allow. **Always set BOTH keys explicitly per phase,
and add `timeout <sec>` around the train call as a hard backstop.**

---

## 4. Cost ledger (this experiment)

| Run | GPU | Wall | Spend | Result |
|-----|-----|------|-------|--------|
| R1 | A40 secure | ~4min | ~$0.03 | dep crash |
| R2 | A40 secure | ~4min | ~$0.03 | stereo crash |
| R3 | A40 secure | ~14h | **~$6.5** | ran, artifacts lost to retrieval race |
| v2 | A40 secure | in progress | ~$0.15 so far | T0 PASS, capped |

RunPod balance path: $10 → ~$3.6. **~$6.8 spent to retire recipe+ops uncertainty**
that a proper local dry-run + epoch cap would have retired for <$2. Documented so the
next job (T2) is the cheap one this was supposed to be.

---

## 5. Reusable assets (in scratchpad `t0t1/`)
- `build_t0t1_dataset.py` — Slakh→dataset_type-1, force-stereo, corrupt-flac skip,
  provenance manifest via `lab/training_data.py`.
- `run_t0t1.sh` (v2) — epoch-capped, per-phase incremental ship, `timeout` backstop.
- `pod_entry.sh` (v2) — headless bootstrap + return-key decode + done_marker.
- VPS watchdog (`/root/t0t1_watchdog.sh`) — cron `*/3`, terminates on done_marker or
  uptime cap. **Deploy by scp'ing a real file, never inline heredoc.**

## 6. Checklist for the next GPU job
- [ ] Local CPU dry-run of the full loop, 2 steps, green
- [ ] Both `num_epochs` and `num_steps` set explicitly; `timeout` wraps train call
- [ ] Dataset provenance manifest built; all audio channel-count verified
- [ ] Per-phase artifact ship wired; VPS watchdog scp'd as a file + `bash -n` passed
- [ ] Watchdog `POD=` id confirmed correct after launch (force one tick, read log)
- [ ] Spend cap = min(time cap, balance); balance is the final backstop
- [ ] Cleanup: terminate pod, remove ephemeral authorized_keys line, drop static bundle
