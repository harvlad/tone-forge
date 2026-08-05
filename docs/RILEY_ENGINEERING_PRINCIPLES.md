# Riley Engineering Principles

What Riley learned building a data-centric guitar-separation system — from separator
research through the Data Factory to the first promoted corpus (Campaign 2, 2026-08-05).
Permanent and additive: append, don't rewrite. Each principle is earned, with its origin.

---

## Evaluation & promotion

### P1 — Never evaluate a dataset with an unvalidated training recipe
Campaign 1 judged Riley's corpus with htdemucs trained **from random init** (pretrained
ckpt omitted; its config was also non-standard so stock weights couldn't load anyway).
It underestimated the corpus (+1.46 dB, both models broken/negative). Campaign 2 changed
**only** the init (validated fine-tune, 100% backbone transfer) → +6.52 dB, then a real-
song blind sweep. A dataset A/B must use an already-validated recipe so the experiment
measures *data*, not optimizer/init. **Corollary:** when fine-tuning from a pretrained
checkpoint, the model config must match that architecture exactly (C1's `bottom_channels
0` vs standard `512` → only 35% weight fit → silent non-transfer). Verify transfer
coverage before spending.

### P2 — Blind listening on REAL audio is the deciding gate; metrics are necessary, not sufficient
SI-SDR ranked Riley 12/12 on synthetic mixes — but the synthetic blind was a **wash
(7/12)**. Only the **real-song blind (15/15, P≈3e-5)** confirmed the win and earned the
promotion. Objective metrics gate entry; a blind majority on real, deployment-relevant
audio decides. Never promote on a metric alone.

### P3 — The evaluation distribution can lie; test on the deployment distribution
The synthetic eval's mixtures were Riley's *own* Virtual-Studio output, so SI-SDR
rewarded "matches my synthesis," not "cleaner guitar." The corpus advantage was
**inaudible on easy synthetic mixes and decisive on hard real songs** — so evaluate on
real multitrack mixtures (Benchmark v2.0), not on the same synthesis you trained toward.

### P4 — The promotion gate is an asset; a rejection is a result
The matured gate caught **three** metric false-positives (B1 fine-tune, AudioShake API,
Campaign 2 synthetic) before they shipped. Each rejection sharpened the method and, in
C2's case, forced building the real-song benchmark that produced the honest promotion.
Do not weaken the gate to force a win; document disagreements and investigate.

---

## Data as the product

### P5 — The Data Factory is the moat; corpus quality is a real, measurable lever
No off-the-shelf real+isolated-guitar+commercial dataset exists — so Riley *manufactures*
supervision (Virtual Studio: scenarios, masking, real backing, exact ground truth).
Campaign 2 proved manufactured data beats a naïve corpus from the *same* source, audibly,
on real songs. Improve the separator primarily via better **corpus versions**, not
repeated architecture experiments.

### P6 — Realistic supervision generalizes; naïve supervision doesn't
The two corpora shared guitars + backing; only the *mixing realism* differed (masking,
levels, compression, scenario diversity). That difference is what generalized to real
songs. Realism in the supervision is the payload.

### P7 — Scope claims to the evidence
Campaign 2 promoted the **corpus** (proven-better data), not a production-grade
separator — real-song absolute SI-SDR is still negative (models trained only on
GuitarSet+Freesound; real full-band is a domain gap). State what's proven and what isn't;
the next lever (real training data / Corpus v2) follows from the honest gap.

---

## Infrastructure & operations

### P8 — Provenance-sensitive artifacts are durable-first
Checkpoints, eval sets, frozen corpora, blind packages → persistent storage + committed
hashes the moment they exist; never leave the only copy in cleanable scratch. Origin: the
T2 real-song eval set was lost to scratch and had to be rebuilt. Every model ties to
corpus_hash ↔ checkpoint_sha ↔ campaign_id.

### P9 — Data-ops run in the datacenter, not over the home uplink
Big data stays on Hetzner (fast link); git holds indexes + hashes only. The 88 GB
MoisesDB was never downloaded — a **ZIP range-extract** (`remotezip`) pulled only the
13.5 GB of guitar songs directly to the volume. Availability, not price, is the usual
constraint; datacenter IPs get bot-blocked (Cambridge-MT, Zenodo) → fall back to a
user-assisted, signed-URL fetch (the Freesound/MoisesDB pattern).

### P10 — Dry-run every GPU job on CPU first; watchdog every rented pod
The full train loop reproduces on CPU with 2 steps at $0 — never debug on a rented GPU.
A VPS-cron kill-switch (not a local one) with a hard time cap terminates the pod on
completion or runaway. Campaigns 1+2 together cost ~$4 because of this discipline.

---

## The loop (now operational)
Benchmark v2.0 (real songs) → Coverage Planner → acquire/manufacture → Corpus version
(frozen + hashed) → validated fine-tune (GPU) → SI-SDR + **real-song blind** vs the
incumbent default → promote only on a blind win. Every model is tied to a frozen corpus;
every corpus is judged against the current default (`DEFAULT_CORPUS.json`).
