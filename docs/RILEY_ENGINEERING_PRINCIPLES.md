# Riley Engineering Principles

Permanent, load-bearing principles distilled from Riley's campaigns. Additive — append,
don't rewrite.

## P1 — Never evaluate a dataset with an unvalidated training recipe
**Origin:** Campaigns 1→2 (2026-08).
Campaign 1 evaluated Riley's corpus with htdemucs trained **from random init**
(pretrained checkpoint accidentally omitted; and its config was non-standard so stock
weights could not have loaded anyway). It underestimated the corpus: +1.46 dB advantage,
both models at negative (broken) absolute SI-SDR. Campaign 2 changed ONLY the init
(validated fine-tune from stock htdemucs, 100% backbone transfer) → Riley +3.56 dB,
naive −2.96, **+6.52 dB advantage, 12/12 held-out wins**.

**Principle:** a dataset A/B must use an **already-validated training recipe** so the
experiment measures *dataset quality*, not optimizer/initialization differences. The
correct recipe *amplified* the corpus advantage (~4.5×), proving it did not create it —
but the wrong recipe nearly buried the signal. Validate the recipe first; then vary the
data.

**Corollary:** when fine-tuning from a pretrained checkpoint, the model config MUST match
the pretrained architecture exactly (Campaign 1's `bottom_channels 0` vs standard 512 →
only 35% weight fit → silent non-transfer). Always verify transfer coverage before spend.

## P2 — Promotion is evidence-based and blind-gated (never metric-only)
Objective metrics (SI-SDR/F1) are necessary, not sufficient. A separator is promoted
only when blind listening AGREES with the metrics; disagreement is documented and
investigated, never overridden. (Origin: AudioShake/B1 false positives — a 2-song or
metric win ≠ a promotion.)

## P3 — Provenance-sensitive artifacts are durable-first
Checkpoints, eval sets, frozen corpora → persistent storage + committed hashes the
moment they exist; never leave the only copy in cleanable scratch. (Origin: the lost T2
eval set.) See memory `artifact-durability-rule`.

## P4 — Factory data-ops run on Hetzner, not the Mac
Large data stays datacenter-side; git holds indexes + hashes only. (Origin:
home-uplink bottleneck.)
