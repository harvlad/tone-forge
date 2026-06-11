# Phase 2 Validation — Execution Kit

**Status:** Awaiting 5 rendered reference WAVs from operator (Matt).

**Purpose:** Everything needed to (a) produce real measured fingerprints for the
5 monitor chains, (b) re-run Alcest, (c) benchmark 10–20 songs, (d) reach a
Go / No-Go decision on the existing 5-chain recommender.

**Anti-goal:** No new architecture. No new chains. No new features. No
calibration refit. We are answering one question and one question only:

> Does the current 5-chain recommendation system produce sensible rankings on
> real music after replacing placeholder fingerprints with measured fingerprints?

---

## 0. Pre-flight checks (already done)

| Check | Status | Evidence |
|---|---|---|
| `render_chain_references.py` exists and parses | OK | `python3 -m scripts.render_chain_references --help` works |
| 5 chain YAMLs present | OK | `tfc.{ambient,classic_rock,clean_strat,edge_of_breakup,modern_gain}.yaml` |
| 5 placeholder fingerprints present | OK | All 5 carry `source: "hand_authored_estimate"` |
| Placeholders snapshotted | OK | `backend/PHASE2_PLACEHOLDER_FINGERPRINTS.json` (so the delta report survives the overwrite) |
| `guitar_catalog._reset_catalog_cache()` exists and is called by the script | OK | `render_chain_references.py:328` |
| Feature math identical for catalog and query path | OK | Script calls `gc._extract_query_fingerprint`, same function the runtime query uses |
| Query window | 8 s, mono, 22.05 kHz | `guitar_catalog._extract_query_fingerprint` |

---

## 1. The 5 chains and what they should sound like

These vibes are taken verbatim from the YAML headers. The acceptance criteria
in the right column are what Phase 2 will check via the measured fingerprint —
they are *qualitative* descriptions of what the rendered WAV should communicate
to a human listener; the numerical fingerprint is the downstream observable.

| Chain id | Display | Reference vibe (from YAML) | What the rendered WAV should sound like |
|---|---|---|---|
| `tfc.clean_strat` | Clean Strat | Twin Reverb clean, neck pickup. Bright, low-noise, light comp, small room reverb. | Pristine clean, clear pick attack, no breakup, short room tail, sparkly top. |
| `tfc.edge_of_breakup` | Edge of Breakup | Deluxe Reverb on 6. Minimal drive, hint of grit, plate reverb. | Mostly clean, breaks up only on transients, plate reverb adds smear, no fuzz. |
| `tfc.classic_rock` | Classic Rock | Plexi at 7 with a treble booster. Mid-heavy, punchy, bluesy/rock pocket. | Mids forward, push when you hit harder, light spring, no scoop. |
| `tfc.modern_gain` | Modern Gain | 5150-ish, tight low cut, scooped or flat mids, hot output. | Tight chunk, fast attack, palm-mute chug should sound surgical not woolly. |
| `tfc.ambient` | Ambient | Clean + dotted-eighth delay + hall reverb. Sit-inside-a-wash. | Notes hang. Delay repeats audible. Wash tail at least 1.5–2 s. |

---

## 2. Stage 1 — render reference WAVs (operator action)

### 2.1 What to record

A **single DI guitar phrase**, played once with each of the 5 chains applied.
The phrase must be identical across all 5 renders — otherwise the per-feature
deltas conflate "chain difference" with "performance difference" and the
validation is unsalvageable.

**Recommended phrase** (from the Phase 1 plan's "picking pattern B"):

- Hold **open D major** (open D string root, then play A2, D3, F#3, A3) and a
  **B minor** voicing (B2, F#3, B3, D4) for roughly 4 s each.
- Pick gently then crescendo into the last bar so the dynamic envelope is
  non-trivial.
- Let the last note ring out for at least 2 s so the decay/sustain features
  are well-defined.
- Total duration: **≥ 8 s**, ≤ 30 s. The fingerprint extractor only looks at
  8 s from the middle of the file (`QUERY_WINDOW_SECONDS = 8.0`), but a longer
  file gives room to centre on the meatiest part.

> If you prefer a different phrase, fine — the **only** constraint is that
> the exact same phrase is reused for all 5 renders. Use a metronome or just
> copy the same DI clip in 5 different tracks; do not re-perform per chain.

### 2.2 How to capture (Connect path)

Per `scripts/render_chain_references.py` lines 18–27, the rendering is a manual
operator step because both Connect and Live are platform-coupled. Use whichever
path you have working:

**Option A — Connect helper** (preferred if Connect can render to disk):

1. Open Connect on this machine.
2. For each chain id in the list below:
   1. Send `apply_chain` for that chain id.
   2. Route Connect's monitor output to a bouncer (Soundflower / BlackHole / Loopback / built-in record).
   3. Play the DI phrase through Connect (or play back a pre-recorded DI clip into Connect's input).
   4. Bounce to a WAV named exactly `<chain_id>.wav`.

**Option B — Ableton Live + M4L (the documented path):**

1. Build the M4L recorder device per `backend/scripts/render_via_m4l/README.md`
   (Stage 1, Steps 1–4). Or if you already have it from the previous PoC, reuse it.
2. Build 5 Ableton Live sets, one per chain, each containing:
   - A single audio track with the **same DI clip**.
   - The chain's amp-sim / FX matching the YAML parameters (gain, drive, EQ,
     comp, reverb — see §1 above and the YAML files for the exact parameter
     targets).
   - The M4L recorder on master.
3. Press Play on each set, let the clip play through, Stop.
4. Rename each captured WAV to `<chain_id>.wav`.

**Option C — Existing 3rd-party amp-sim** (Helix Native, NeuralDSP, AmpliTube):

If you trust a 3rd-party amp-sim to faithfully represent each archetype, render
the same DI through 5 patches that match the YAML vibes (§1 above) and save
as `<chain_id>.wav`. This is the fastest path but the rendered audio will
diverge from what Connect will eventually deliver. Document which patches
were used in `PHASE2_VALIDATION_REPORT.md` §0 ("Rendering provenance").

### 2.3 Sample rate / bit depth

Whatever you can produce. The extractor resamples to 22.05 kHz mono. WAV /
AIFF / FLAC accepted. Bit depth doesn't matter (16 or 24 both fine). Stereo
files get summed to mono inside librosa.

### 2.4 Exact filenames the script expects

Place all 5 files in a single directory. The script looks for `<chain_id>.wav`
(or `.aif`, `.aiff`, `.flac`):

```
<your-render-dir>/
├── tfc.ambient.wav
├── tfc.classic_rock.wav
├── tfc.clean_strat.wav
├── tfc.edge_of_breakup.wav
└── tfc.modern_gain.wav
```

Typos in chain ids cause the script to error and refuse to proceed
(`_resolve_targets` raises `SystemExit` on unknown ids).

---

## 3. Per-chain acceptance criteria

Once the 5 WAVs exist, **before** running the fingerprint script, gut-check the
audio. Each chain should pass its own basic acceptance test or the downstream
validation result will be uninterpretable.

| Chain | Sit-with-reference check (subjective) | Listenable failure modes |
|---|---|---|
| `tfc.clean_strat` | Sounds like a clean Strat through a small Fender. No gain. | If it sounds distorted → wrong drive. If it sounds dark → EQ inverted. |
| `tfc.edge_of_breakup` | Clean on soft picks, just-barely-breaks-up on harder picks. | If always clean → drive too low. If always distorted → drive too high. |
| `tfc.classic_rock` | Punchy mids, Plexi-on-7 character. | If scooped mids → wrong EQ. If high-gain modern chug → wrong gain stage. |
| `tfc.modern_gain` | Tight chunk, fast attack, scooped/flat mids. | If saggy bass → comp on / drive too low. If fuzzy → wrong tube model. |
| `tfc.ambient` | Wash with audible delay repeats, long reverb tail. | If dry → reverb/delay not in chain. If muddy → reverb too big or EQ. |

These are subjective — write a one-line confirmation per chain in
`PHASE2_VALIDATION_REPORT.md` §0 once you've listened.

---

## 4. Stage 2 — extract fingerprints (Claude action, runs after WAVs land)

### 4.1 Commands to run

```bash
cd /Users/mattharvey/Sites/tone-forge/backend

# 4.1.a — Dry run first. Print computed fingerprints without touching disk.
python3 scripts/render_chain_references.py \
    --audio-dir <your-render-dir> \
    --dry-run --verbose 2>&1 | tee /tmp/phase2_dryrun.log

# 4.1.b — Real run. Overwrites the 5 placeholder JSONs in place.
python3 scripts/render_chain_references.py \
    --audio-dir <your-render-dir> \
    --allow-placeholder-overwrite --verbose 2>&1 | tee /tmp/phase2_render.log

# 4.1.c — Sanity-check the new fingerprints were written.
python3 -c "
import json
from pathlib import Path
for fp in sorted(Path('tone_forge/monitor/chains').glob('tfc.*.fingerprint.json')):
    d = json.loads(fp.read_text())
    print(fp.name, '->', d.get('source'), d.get('rendered_from'))
"
```

Expected output of the third command:

```
tfc.ambient.fingerprint.json -> rendered_reference tfc.ambient.wav
tfc.classic_rock.fingerprint.json -> rendered_reference tfc.classic_rock.wav
tfc.clean_strat.fingerprint.json -> rendered_reference tfc.clean_strat.wav
tfc.edge_of_breakup.fingerprint.json -> rendered_reference tfc.edge_of_breakup.wav
tfc.modern_gain.fingerprint.json -> rendered_reference tfc.modern_gain.wav
```

All 5 must read `rendered_reference`. If any still say `hand_authored_estimate`,
that WAV was missing — re-render that one and re-run §4.1.b.

### 4.2 Per-chain delta report (Step 2 of the brief)

Once §4.1 succeeds, Claude runs the delta computation against
`backend/PHASE2_PLACEHOLDER_FINGERPRINTS.json` (the frozen snapshot). The
table format is fixed:

```
Chain: tfc.<id>
  feature           placeholder   measured   delta
  brightness        0.XX          0.XX       ±0.XX
  warmth            0.XX          0.XX       ±0.XX
  air               0.XX          0.XX       ±0.XX
  attack_ms         XXXX          XXXX       ±XXXX
  decay_ms          XXXX          XXXX       ±XXXX
  sustain_ratio     0.XX          0.XX       ±0.XX
  harmonic_ratio    0.XX          0.XX       ±0.XX
  pitch_stability   0.XX          0.XX       ±0.XX
  z-norm distance(placeholder→measured): X.XX
```

A per-chain z-norm distance > 5.0 means the placeholder was directionally
wrong; ≤ 2.0 means the placeholder was a good guess. The total
"placeholder-vs-measured" z-norm across all chains is the headline number
for "were our estimates directionally accurate?"

---

## 5. Stage 3 — re-test Alcest (Step 3 of the brief)

The exact Alcest stem used in Phase 1 is:

```
/var/folders/t9/s8pg2yfx3g73nt0lzf6p40xc0000gn/T/toneforge_stems_t7uls3zg/tmpzqz3k5um_other_center.wav
```

Tempo and key inputs to `recommend_from_tempo_key`:

- `tempo_bpm = 104.0`
- `key = "E Minor"`

Phase 1 measured result on placeholders (so we can quote the delta):

| Field | Placeholder result |
|---|---|
| Top match (raw) | `tfc.ambient` |
| Distance to ambient | 30.26 |
| Distance to runner-up (classic_rock) | 33.04 |
| Margin | 0.08 |
| Confidence | 0.12 |
| Tier | `low` |
| Apply | `tfc.classic_rock` (fallback) |

Phase 2 will quote the same six fields on the measured fingerprints, plus the
full `to_wire_dict` of the recommendation object.

Pass criteria for Step 3 (verbatim from the brief):

1. **Does Ambient remain the top recommendation?**  Y/N (it must — Alcest is ambient)
2. **Did the distance collapse from ~30 toward the expected range?**  Numeric answer
3. **Did confidence improve?**  Numeric answer
4. **Did tier improve?**  Categorical (low → medium / high)
5. **Is the recommendation still directionally correct?**  Y/N + rationale

If Y to (1) and (5), Steps 4–7 proceed. If N to either, jump straight to
Step 6 (failure analysis) and stop.

---

## 6. Stage 4 — benchmark corpus (your action)

Per your chosen path (Option A in the AskUserQuestion): you supply 10–20
`(artist, song, youtube_url, expected_chain, reason)` rows. Drop them into
`backend/PHASE2_BENCHMARK_CORPUS.json` using this schema:

```json
[
  {
    "artist": "Alcest",
    "song": "Kodama",
    "url": "https://www.youtube.com/watch?v=...",
    "expected_chain": "tfc.ambient",
    "reason": "Shoegaze/post-black metal; soaked in delay+reverb wash."
  },
  ...
]
```

**Genre coverage Claude will check is present** (raises a warning if any genre
is missing, but does not block — your judgment overrides):

| Genre | Expected chain |
|---|---|
| Shoegaze / post-rock | `tfc.ambient` |
| Ambient guitar | `tfc.ambient` |
| Classic rock | `tfc.classic_rock` |
| Blues | `tfc.classic_rock` or `tfc.edge_of_breakup` |
| Indie | `tfc.edge_of_breakup` or `tfc.clean_strat` |
| Modern gain / djent | `tfc.modern_gain` |
| Metal (trad / NWOBHM) | `tfc.classic_rock` or `tfc.modern_gain` |
| Clean / jangle | `tfc.clean_strat` |
| Edge-of-breakup | `tfc.edge_of_breakup` |

Total: 10–20 rows. Per-genre counts roughly balanced.

---

## 7. Stage 5 — ranking validation (Claude action)

Once `PHASE2_BENCHMARK_CORPUS.json` exists, Claude runs
`scripts/run_phase2_benchmark.py` against it. The script:

1. For each row, ensures stems exist on disk (extracts via the local engine
   if needed — that's the slow step; ~1 min per song first time).
2. Resolves the guitar stem path.
3. Calls `guitar_catalog.recommend_from_tempo_key(stem, tempo_bpm, key)`.
4. Writes the per-song row into the §5 table of the validation report.

The exact table format the report will produce:

```
| # | Artist | Song | Expected | Top 1 | Top 2 | Top 3 | Conf | Margin | Tier | Pass? |
|---|--------|------|----------|-------|-------|-------|------|--------|------|-------|
| 1 | Alcest | Kodama | ambient | ambient (d=X) | … | … | 0.XX | 0.XX | medium | ✓ |
| 2 | …      | …      | …       | …             | … | … | …    | …    | …      | … |
```

Pass rule (verbatim from the brief): if the expected chain appears in the
returned top 3 in a reasonable position, count it as a pass.

Aggregates Claude produces:

- Top-1 accuracy = `#songs where rank 1 == expected / N`
- Top-2 accuracy = `#songs where expected ∈ {rank1, rank2} / N`
- Top-3 accuracy = `#songs where expected ∈ {rank1, rank2, rank3} / N`

---

## 8. Stage 6 — failure analysis (Claude action)

For every row where Pass? == ✗, Claude produces:

| Field | Source |
|---|---|
| Expected chain | corpus |
| Actual top-1 | recommend() output |
| Feature breakdown (8-vector) | `wire.debug.query_vector` |
| Distance breakdown (5 chains) | `wire.debug.ranking` |
| Probable cause (narrative) | Engineering judgment |
| Failure class | One of: `catalog` / `feature` / `stem_selection` / `ranking` / `calibration` |

Failure-class definitions:

- **catalog** — measured fingerprints don't span the relevant region of feature
  space (e.g. no chain occupies "low-distortion shoegaze with wash")
- **feature** — the 8-feature DSP fingerprint cannot represent the perceived
  difference (e.g. delay/reverb identity collapses to similar centroid+envelope)
- **stem_selection** — the wrong stem fed into the recommender (e.g. `other`
  contained keyboards, not guitar)
- **ranking** — features and distances are sane, but the nearest-neighbour
  decision was wrong (e.g. z-norm scaling makes attack dominate brightness)
- **calibration** — directionally correct but tier classification rejected it

---

## 9. Stage 7 — Go / No-Go (Claude action)

Decision matrix:

| Top-1 accuracy | Top-3 accuracy | Verdict |
|---|---|---|
| ≥ 70% | ≥ 90% | **GO** — system is directionally correct |
| ≥ 50% | ≥ 80% | **CONDITIONAL GO** — works for the dominant genres; flag the failing categories |
| < 50% | < 80% | **NO-GO** — architectural change needed before broader testing |

Override: if `tfc.ambient` is *never* the top-1 for shoegaze/post-rock songs,
that's a NO-GO regardless of overall numbers (the Phase 1 thesis was that
ambient is the most distinctive archetype; failing it means the feature set
can't carry the rest).

The final report ends with the verdict, the supporting numbers, and the
single biggest issue identified.

---

## 10. Operator checklist

Tick these off in order. Once all 10 are ticked, hand back to Claude.

- [ ] **R1.** Decided on rendering option (A Connect / B Ableton+M4L / C 3rd-party). Documented choice in `PHASE2_VALIDATION_REPORT.md` §0.
- [ ] **R2.** Same DI phrase will be used for all 5 chains. (Or: the DI clip exists at `<path>` and will be re-applied per chain, not re-performed.)
- [ ] **R3.** All 5 chain renders captured to files named exactly `tfc.ambient.wav`, `tfc.classic_rock.wav`, `tfc.clean_strat.wav`, `tfc.edge_of_breakup.wav`, `tfc.modern_gain.wav`.
- [ ] **R4.** All 5 files dropped in one directory.
- [ ] **R5.** Listened to each — they pass the §3 sit-with-reference checks. (One-line confirmation per chain in the report.)
- [ ] **B1.** Wrote `PHASE2_BENCHMARK_CORPUS.json` with 10–20 labelled songs covering the genres in §6.
- [ ] **B2.** Confirmed each YouTube URL still resolves (open one in a browser).
- [ ] **B3.** Local engine is running (the pipeline needs it to extract stems). Or: pre-extracted stems exist and corpus rows point to them via a `stem_path` field instead of `url`.
- [ ] **R6.** Path to the render dir handed to Claude.
- [ ] **R7.** Path to the corpus JSON handed to Claude.

---

## 11. What Claude runs in one command sequence once handoff lands

```bash
cd /Users/mattharvey/Sites/tone-forge/backend

# A. Pre-flight (catalogue current state)
python3 scripts/render_chain_references.py --help                        # sanity
python3 -c "from tone_forge.tone import guitar_catalog as g; print(len(g._get_catalog().entries))"   # expect 5

# B. Step 2 — overwrite placeholders with measured fingerprints
python3 scripts/render_chain_references.py \
    --audio-dir <RENDER_DIR> \
    --allow-placeholder-overwrite --verbose 2>&1 | tee /tmp/phase2_render.log

# C. Step 2 — delta report against the placeholder snapshot
python3 scripts/run_phase2_benchmark.py delta-report \
    --placeholders backend/PHASE2_PLACEHOLDER_FINGERPRINTS.json \
    --out backend/PHASE2_VALIDATION_REPORT.md

# D. Step 3 — re-test Alcest (single song, captured in Phase 1)
python3 scripts/run_phase2_benchmark.py alcest \
    --stem '/var/folders/t9/s8pg2yfx3g73nt0lzf6p40xc0000gn/T/toneforge_stems_t7uls3zg/tmpzqz3k5um_other_center.wav' \
    --tempo 104 --key 'E Minor' \
    --out backend/PHASE2_VALIDATION_REPORT.md

# E. Steps 4–7 — benchmark + ranking + failure analysis + verdict
python3 scripts/run_phase2_benchmark.py validate \
    --corpus backend/PHASE2_BENCHMARK_CORPUS.json \
    --out backend/PHASE2_VALIDATION_REPORT.md
```

Each `run_phase2_benchmark.py` subcommand appends to the same report file
under a distinct section header so the full picture lands in one document.

---

## 12. What this kit deliberately does not do

- ❌ Refit confidence curves (Step 7 reads existing telemetry, doesn't rewrite calibration)
- ❌ Add chains, expand catalog, introduce CLAP/OpenL3/MERT, build preview clips
- ❌ Work on `preset_matches` (legacy synth path, untouched)
- ❌ Add new recommendation systems
- ❌ Compute or quote any validation metric on synthetic or placeholder data

The only acceptable output is a metric derived from real rendered references
and real labelled songs.
