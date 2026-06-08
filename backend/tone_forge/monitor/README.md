# Monitor Chain Bank

**Purpose:** Curated `MonitorChain` bank — one chain per family. Connect builds the AVAudioEngine graph from these specs and pipes the user's input through them when retrieval lands on a LOW / UNKNOWN tier (or when the user explicitly chooses a chain from the fallback list).

**Owner:** Founder (curation), Platform & Engine (engineering scaffold).

**Status:** Engineering scaffold landed (loader + 5 placeholder YAMLs + WS plumbing). Hand-tuning of the parameter values is a separate, ear-driven workstream that proceeds out-of-band. See *Curation Process* below.

---

## Layout

```
monitor/
├── __init__.py              # public API: load_chain, load_all, list_chain_ids
├── loader.py                # YAML parser + validation
├── README.md                # this file
└── chains/
    ├── tfc.clean_strat.yaml
    ├── tfc.edge_of_breakup.yaml
    ├── tfc.classic_rock.yaml
    ├── tfc.modern_gain.yaml
    ├── tfc.ambient.yaml
    └── preview/             # founder-recorded A/B clips (gitignored binaries)
```

Filenames must equal the chain `id`: `tfc.clean_strat.yaml` ↔ `id: tfc.clean_strat`. The loader rejects mismatches.

---

## Chain YAML schema

Every chain ships **all** required keys. The loader is strict — a missing section is a CI failure, not a runtime fallback.

```yaml
id: tfc.<family>              # required, must equal filename stem
family: <family>              # required, must be a MonitorChainFamily value
display_name: "..."           # required, human label for UI
description: "..."            # required, may be empty string
parameters:                   # required mapping
  input:                      # required section
    gain_db: <number>
    high_pass_hz: <number>
  gain_stage:                 # required section
    type: <string>            # tube_clean / tube_break / tube_overdrive / tube_high_gain
    drive: <0.0..1.0>
    bias: <0.0..1.0>
  eq:                         # required section
    bass_db: <number>
    mid_db: <number>
    treble_db: <number>
    presence_db: <number>
  comp:                       # required section
    enabled: <bool>
    ratio: <number>
    threshold_db: <number>
    attack_ms: <number>
    release_ms: <number>
  reverb:                     # required section
    type: <string>            # room / plate / spring / hall / small_hall
    size: <0.0..1.0>
    mix: <0.0..1.0>
  output:                     # required section
    trim_db: <number>
preview_audio: "preview/<id>.mp3"   # optional, UI A/B clip
```

The loader enforces *structural* validity (keys present, types correct). The numeric ranges above are conventions for the curator, not bounds the loader checks — Connect normalizes / clamps where its DSP needs it.

### `MonitorChainFamily` values

| family | Used when (Plan §7) |
|---|---|
| `clean` | LOW on clean/jangle |
| `edge_of_breakup` | LOW on bluesy/indie (also the safe-default fall-through) |
| `classic_rock` | LOW on rock/punk |
| `modern_gain` | LOW on metal/hard rock |
| `ambient` | LOW on shoegaze / post-rock / slow material |

These are pinned in `tone_forge.contracts.MonitorChainFamily`. Adding a family is a contracts change + a corresponding chain file + a `tone.policy.FAMILY_TO_CHAIN_ID` entry.

---

## Curation Process

Engineering can validate that a chain *loads*; only the founder's ear can validate that it *sounds right*. The two workstreams overlap only at acceptance:

1. **Listen to the reference recording** on a known interface + headphones. Catalogue the recording in this README under *Reference Songs by Family*.
2. **Plug a Strat (or the user-role-appropriate instrument)** through the same chain; play along to the reference at monitor level.
3. **Adjust** `parameters` until the player feels they "belong" in the mix. Re-edit the YAML in place; the loader picks up the new values on next process start.
4. **A/B against the original recording** for tonal sit. Record the A/B as `chains/preview/<id>.mp3` for UX consumption.
5. **Lock the chain.** Commit the YAML and the preview MP3 together. The commit message must reference the reference song(s) used.
6. **Update the chain's `description`** to reflect the dialed-in vibe (drop the "pre-listening baseline" suffix).

Until step 5 lands for a given family, the chain ships as a structurally-valid placeholder — Connect builds a graph and audio comes out, but the experience is not curated.

### Reference Songs by Family

*(Founder fills this in as chains are dialed.)*

| Family | Reference song(s) | Listening setup | Sign-off date |
|---|---|---|---|
| `clean` | — | — | placeholder |
| `edge_of_breakup` | — | — | placeholder |
| `classic_rock` | — | — | placeholder |
| `modern_gain` | — | — | placeholder |
| `ambient` | — | — | placeholder |

---

## Acceptance Gate (per chain)

A chain passes its listening engagement if it:

* Sounds usable on **at least 3 reference songs** in its target family.
* Sits at a comparable monitor level to the original recording (no normalization required to be hearable in the mix).
* Doesn't clip with input peaking at −6 dBFS.
* Round-trip latency ≤ 10 ms on M-series hardware (measured by `connect latency`).

Document the result in the commit message that finalizes the chain. CI does not gate on listening criteria — that's the founder's call by definition.

---

## Programmatic access

```python
from tone_forge.monitor import load_chain, load_all, list_chain_ids

# Single chain
chain = load_chain("tfc.classic_rock")
chain.id              # "tfc.classic_rock"
chain.family          # MonitorChainFamily.CLASSIC_ROCK
chain.parameters      # nested dict forwarded to Connect as-is

# Whole bank
bank = load_all()     # {chain_id: MonitorChain}

# Just the ids (no parsing)
list_chain_ids()      # ["tfc.ambient", "tfc.classic_rock", ...]
```

Errors raised:

* `ChainNotFoundError` — no file for the requested id.
* `ChainSpecError` — file exists but failed validation (missing keys, bad family, filename ↔ id mismatch).

---

## Phase 2 (NOT in MVP)

* Per-pickup variants (single-coil vs humbucker within each family).
* Per-amp character (Fender / Marshall / Mesa / Vox archetypes).
* Bass chains (depends on bass user role landing).
* Downloadable chain banks (community / artist packs) — would extend the loader's `chains_root` argument.
