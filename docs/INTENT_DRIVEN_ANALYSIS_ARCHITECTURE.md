# Intent-Driven Song Analysis — Architecture Proposal

Status: **Accepted baseline (design). No implementation authorized.**
Version: **v2 — Target-Part amendment.** (v1 = target-first + progressive background; v2 adds instrument-family-vs-part.)
Branch context: `feat/ui-refactor`. Complements `SONG_UNDERSTANDING_CAPABILITY_MAP.md` and `JAM_PRODUCT_ROADMAP.md`.
Scope boundary: does **not** touch the Transcription Lab (`backend/lab/`) or specialist-model research. Consumes their output as a black box (see §8, §19, §26).

---

## 0. TL;DR

**Recommendation stands: Option C — target-first + progressive background analysis.** The v2 amendment does not invalidate it; it refines *what* "the target" is.

The v2 change: **an instrument family is not one musical part.** "Guitar" may be lead + rhythm + acoustic + a double. The architecture now models **Family → Subfamily → Part**, targets a *part*, and — critically — is honest that **the production separator cannot currently isolate two guitars from one mix** (§3). That is a documented capability gap, not a solved problem.

Load-bearing decisions:
- **One import question stays**: "What are you playing?" (family). A second question — "Which part?" — appears **only when multiple credible candidates exist**, and is answered by **audition** (isolated previews), not by trusting a machine "lead/rhythm" label.
- **Persistent identity is `part_id` + soft descriptors**, never a hard `LEAD`/`RHYTHM` enum.
- **Artifact key gains a typed `scope`** (`SONG_GLOBAL` | `FAMILY` | `PART`) instead of a nullable `instrument` field.
- **MVP ships family-level targeting.** Same-instrument source-instance separation is post-MVP, gated on the Lab discovering a separator that does it.
- **Jam needs stems + grid + sections + harmony (chords/key) — but NOT target note transcription.** Grounded: the `SongBundle` the client decodes has no note fields. Even Learn is symbol-derived today; transcription is *built-but-unconsumed* and should be gated behind a future note-practice feature, not run speculatively (§14–16).

The one principle to hold: **optimize Time-To-Playable, never time-to-everything-analyzed.**

---

## ADR — Accepted decisions (baseline)

These are **accepted** as of this revision. Change them only via a new ADR entry, not silently.

1. **Architecture: Option C** — target-first + progressive background analysis.
2. **One explicit import question: "What are you playing?"** (instrument family).
3. **Mode is inferred from the tab** (Learn/Jam/Perform) — never asked.
4. **Optimize Time-To-Playable**, not time-to-full-analysis.
5. **Family-level targeting is the MVP.** Part-level targeting is **additive**, not a prerequisite.
6. **Ask "Which part?" only when ≥2 credible candidates exist**; default is silent single-part.
7. **Prefer musician audition** over brittle semantic (lead/rhythm) classification.
8. **Internal model: Family → Subfamily → Part.** Persistent identity = `part_id` + soft descriptors, never a hard LEAD/RHYTHM enum.
9. **Product UI never exposes GM taxonomy.**
10. **Planner requests capabilities/artifacts, never model names** ("high-quality bass notes", not "run MT3").
11. **Specialist registry is human-promoted from Lab results**; production pins immutable versions.
12. **Research never silently changes production routing.**
13. **Background analysis is demand-driven** (TARGET-CRITICAL / OPPORTUNISTIC / ON-DEMAND / NEVER-BY-DEFAULT) — not "analyze everything eventually."
14. **Current Jam requires stems + grid + harmony + sections — NOT note transcription.**
15. **Current Perform requires stems + grid + harmony + sections + performance scene — NOT note transcription.**
16. **Current note transcription has no iOS consumer and stays OFF the production critical path.** Current Learn/Practice is symbol/voicing-derived (`GuitarVoicing`), not transcription-driven. High-quality transcription is **R&D for future note-aware Practice/Learn**, gated behind its consuming UI — never run merely because the backend can produce it.
17. **Same-instrument source-instance separation is a known capability gap** (family-level Demucs + a stereo-pan heuristic only).
18. **No dynamic execution scheduler yet** (interface only; constant cloud-GPU policy).
19. **No iOS specialist-model porting yet.**
20. **No paid GPU compute without explicit approval.**

**Unresolved assumptions (NOT decisions — do not mistake for accepted):** see §29. Chiefly: does same-instrument separation exist (Lab, §26); is `produceAnalysisWAV` byte-stable (§20); is characterization reliable enough for role hints (§13); the transcription-consumer product decision (§15/§29); backing-suppression threshold (§17); part re-match confidence (§12-identity).

---

# PART A — Core architecture (v1, preserved + updated)

## 1. Product rationale

A JAM user arrives with an **intent**, not a request for full multitrack transcription: "play bass on this," "learn the guitar part," "sing this," "perform keys." The current pipeline spends GPU-seconds producing artifacts nobody looks at (piano MIDI for a bassist, six stems when one-plus-backing suffices). For a solo operator this scales cost with *songs × instruments* instead of *songs × requested-part*.

The insight: **intent is a query over a dependency graph.** Given the query, run only the required subgraph, reuse everything cached, defer or skip the rest.

## 2. Target UX — "What are you playing?"

Single mandatory question, musical language, one tap:

> **What are you playing?** Guitar · Bass · Keys · Drums · Vocals · *(Other)*

This selects an **instrument family**, a first-class, editable song property (§28) — not a one-time modal gate. "Other" = general polyphonic transcription + full backing (safe superset). Returning users get their usual instrument pre-highlighted; if the song's part is already resolved, the question is skipped entirely.

## 3. Mode is the tab, not a question

The IA refactor already made mode navigational: **Learn → Practice**, **Jam → Jam**, **Perform → Perform**, **Mixer → mix**. The tab *is* the second answer. Asking "Jam/Practice/Perform?" is redundant UI — do not add it. Moving Learn→Perform re-plans and computes only the *delta* artifacts (§28); it never re-imports. **Decision: instrument explicit (one question), mode implicit (the tab).**

## 4. Analysis planner

Pure function + executor; knows nothing about iPhone/Mac/cloud (that is §9's job).

```
plan(target: ExperienceTarget, store) -> AnalysisPlan {
  required, background, reused, readiness_stages
}
```
Walk the DAG (§12) top-down; for each artifact query the store; reuse `ready` artifacts at acceptable tier and prune their subtree; emit `ArtifactRequest`s for the rest; partition into `required` (hard-path to target) vs `background` (soft). Cheap, idempotent, re-run on import / tab change / target change / part change.

## 5. Specialist router — the Lab seam

Planner requests **capabilities**, never model names: `"high-quality bass notes"`, not `"run MT3"`. A versioned router table maps need → `ModelBinding` (model/version/checkpoint/adapter/locations/expected-quality). Reuses the Lab's **official-inference adapters verbatim** (the `transcription-lab-infra` memory records custom windowing caused a 1.21× timing-stretch bug — never reimplement inference). New Lab winner = versioned table edit ⇒ targeted invalidation (§18). Interface expanded for parts in §12-router.

## 6. Execution-location abstraction

Tasks declare *requirements*; a scheduler picks *where* (`ON_DEVICE_ANE|GPU|DSP`, `LOCAL_MAC`, `CLOUD_CPU|GPU`). **Phase-1 policy is a constant**: heavy → `CLOUD_GPU` (today's `run_file_analysis` worker), cheap DSP → on device. Build the interface, not the scheduler.

## 7. Cache / artifact store

Production **ArtifactStore** built on the existing JSON+R2 substrate (`analysis_jobs.py:JobRegistry` in `backend/data/`, `history.json`, `r2_storage.py`), not a new DB. Device side: extend `BundleStore` to accept **partial** bundles that fill in as artifacts land. Provenance via `provenance.py`. Inference identity is kept separate from consumption/eval params (Lab discipline) — re-rendering notes must never invalidate the notes. **Key schema updated in §10.**

## 8. Progressive readiness

Replace `IMPORT → 0–100% → READY` with musical stages that flip on as artifacts land: *Preparing song → Mapping the beat → Finding your part → Building your part → Ready.* Each = a real usable capability. Guard rail: never mutate an artifact the user is actively performing against; upgrades apply at safe boundaries (§17-tier).

## 9. Time-To-Playable (base definition)

**TTP = wall-clock from import to the first moment the user can do their chosen thing in their chosen mode.** Mode-dependent (§14–16). Primary optimization target. "Time to full analysis" is explicitly *not* optimized. Extended for ambiguity in §22–23.

---

# PART B — Target-Part amendment (v2, new)

## 10. Instrument family vs target part; Family → Subfamily → Part

Real songs contain multiple parts per family: lead+rhythm guitar, acoustic+electric, doubled guitars, lead+backing vocals, piano+EP, lead synth+pad. The v1 assumption "Guitar → guitar stem → guitar analysis" is insufficient.

**Internal three-level model** (the user sees only the top level):

```
USER FAMILY (visible)     MODEL SUBFAMILY (internal, GM-derived)   SOURCE PART (internal)
Keys                      ├─ piano                                 piano_part_01
                          ├─ electric_piano                        ep_part_01
                          ├─ organ                                 organ_part_01
                          └─ synth (lead/pad)                      synth_part_01, synth_part_02
Guitar                    ├─ electric_guitar                       gtr_part_01 (lead-like)
                          └─ acoustic_guitar                       gtr_part_02 (rhythm-like)
Vocals                    ├─ lead_vocal                            vox_part_01
                          └─ backing_vocal                         vox_part_02
```

This abstraction cleanly solves the GM↔product mapping (§20): the user says "Keys," internal discovery finds which subfamilies/parts are actually present, and any "Which part?" prompt uses musician-friendly labels ("Piano", "Organ") derived from subfamily — GM taxonomy never leaks to the UI.

**Do not** force reality into `LEAD`/`RHYTHM`. Persistent identity is:
```
instrument_family = guitar
part_id           = gtr_part_02
descriptors (optional, soft): { role_hint: "rhythm-like", role_conf, register, activity, character: "chordal" }
```
A song can hold two rhythm guitars, harmonized leads, or roles that change per section — descriptors are hints, not the primary key.

## 11. Instrument separation vs source-instance separation — the capability gap (GROUNDED)

Two different technical problems:

**A. Family/stem separation** — `Song → {vocals, drums, bass, guitar, piano, other}`.
**Production HAS this.** `stem_separator.py:182 separate_all_stems` runs Demucs (`htdemucs_6s`), iterating `model.sources` (`:250`) to emit one WAV per family. Family-level, one stem per family.

**B. Same-instrument source-instance separation** — `Guitar mixture → {lead, rhythm, acoustic}`.
**Production does NOT have this.** The only mechanism that surfaces >1 candidate from one family is `stem_separator.py:449 decompose_stem_pan` — a **mid/side center/sides split** on the stereo `other` stem. Its own docstring is explicit about the limits:
- fires only when hard-panned double-tracked parts exist (side-energy ratio > threshold **and** L/R correlation < threshold);
- *"cannot recover content that both L and R contain (correlated stereo). For that you'd need a time-frequency masking approach (pan-angle classification per TF bin). That's a follow-up."*

So: **true same-instrument source separation is a capability gap.** Today = family-level Demucs + a crude stereo-pan proxy that catches only hard-panned doubles. Any design that promises "pick lead vs rhythm from any song" is currently unfunded by the models we have. This is the single most important honesty in v2, and it gates the multi-part UX (§13) and per-part backing (§17).

**What already ships (grounded).** The pan-split is not hypothetical — its output already reaches the client. The backend `StemRole` enum (`stem_model.py:34`) encodes `{drums, bass, vocals, harmonic, lead, rhythm, texture, keys, unknown}`; the session bundle maps the pan-split center/sides into fixed wire slots `guitar_left→LEAD` and `guitar_right→RHYTHM` (`session/bundle.py:150`), with additional same-family stems (`guitar_lead`, `guitar_rhythm`, `guitar_texture`, `piano`, `keys`) spilling into `StemSet.extras`. So a **crude two-candidate guitar split is already productionized** — but as arbitrary role *names*, not a first-class, auditioned part choice, and only when the pan heuristic fires. v2's job is to turn that latent capability into an honest, musician-driven selection (§13) and to fix the identity model beneath it (§12-identity).

## 12. Dependency DAG (updated for parts)

```
UserIntent (family + mode)
      │
      ▼
InstrumentFamilyTarget
      │
      ▼
FamilyDiscovery ── produces ──> [CandidatePart...]     (cheap; see §13)
      │
      ├── exactly 1 credible candidate ──> TargetPart          (auto, no question)
      │
      └── ≥2 credible candidates ──> UserSelection ──> TargetPart
                                     (durable product state,
                                      NOT a neural artifact — §12-identity)
      ▼
   TargetPart
      ├──> TargetSourceAudio     (family stem, or part audio if B available)
      ├──> BackingMix            (family-suppressed; per-part suppression needs B — §17)
      ├──> TargetTranscription   (Practice only: NoteTranscription | DrumTranscription | VocalPitch)
      └──> Mode artifacts        (DifficultyMap / PerformanceScene)

Song-global (no part): GlobalAnalysis → BeatGrid → SongStructure; KeyEstimate → ChordProgression
```

Edges are hard dependencies only. `NoteTranscription(gtr_part_02)` depends on its source audio + a specialist model; **not** on chords. `UserSelection` is durable provenance, not analysis output.

### 12-identity. Target-part identity across analysis versions

Problem: separator v1 emits `gtr_part_01/02`; v2 may reorder or resolve differently. A user who chose "the rhythm part" must not silently receive the lead after an upgrade.

**Current model is brittle (grounded).** Today multiplicity is expressed only as **role strings** (`StemRole`), the wire `SongBundle.stems` is a `[BundleStem{role}]` array (`SongBundle.swift:145`), and the iOS client **caches each stem by its role string as the filename** (`BundleStore.swift:124` → `"<role>.<ext>"`). Two stems sharing a role would **collide in the cache** — so the client silently assumes one stem per role. A stable `part_id` (below) is precisely what replaces role-string-as-cache-key.

Strategy (document now, don't overbuild):
- Bind `UserSelection` to a **fingerprint of the chosen candidate's audio** captured at selection time (cheap: activity-pattern vector over sections + coarse spectral signature; later: an embedding).
- On separator upgrade, **re-match** new candidates to the stored fingerprint by audio/activity similarity. High confidence ⇒ silently rebind `part_id`. Low confidence ⇒ **re-ask the musician** (surface both, let them re-audition). Never silent swap under threshold.
- Store `producer_id`/`producer_version` on every part so a rebind is explainable.

### 12-router. Specialist-router interface (expanded)

Add routing dimensions as a **typed key**, not metadata soup:
```
RoutingKey { artifact, family, subfamily?, part_id?, regime?, quality }
   regime ∈ { monophonic, polyphonic/chordal, percussive, pitch-contour }
resolve(RoutingKey, constraints) -> ModelBinding
```
Lets the Lab later route `lead guitar → Model X`, `polyphonic guitar → Model Y`, `vocals → pitch model` without the interface becoming arbitrary. Routes hardcoded from Lab findings initially; empty dims fall back to family-level defaults.

## 13. Candidate-part discovery & the "I play Guitar" strategy

Evaluated strategies A–F. **Recommendation: D+E** — *cheap family discovery first, ask only on ambiguity, resolve by audition* — bounded by the §11 capability gap.

| Strategy | Verdict |
|---|---|
| A. Assume one target | MVP default. Correct when the family has one credible source; wrong for multi-part songs. |
| B. Discover all candidates, then ask | Requires source-instance separation we lack (§11). Post-MVP. |
| C. Ask Lead/Rhythm up front | Rejected — asks before we know parts exist; forces brittle labels. |
| **D. Cheap family analysis, ask only when ambiguous** | **Chosen.** No question in the common single-source case. |
| **E. Separate candidates + let user audition** | **Chosen, where candidates exist** — musician is ground truth (§13-audition). |
| F. Other | Not needed. D+E generalize. |

Flow:
```
"What are you playing?" [Guitar]
        │
   FamilyDiscovery (cheap): is there a guitar family stem with real energy?
        │
   How many CREDIBLE candidates?
        ├─ 1  → continue silently (no second question)      ← common case, MVP
        └─ ≥2 → "Which part are you playing?" + auditions    ← needs candidates to exist
```

**Honest limit:** with only Demucs + `decompose_stem_pan`, "≥2 candidates" can only arise from **hard-panned double-tracked parts**. Most single-guitar and center-panned-lead songs will (correctly) show one candidate. Rich multi-part discovery waits on a Lab separator (§26). So MVP is effectively strategy A with a D+E hook that lights up wherever the pan-split legitimately fires.

### 13-audition. Audition as ground truth

Do not trust machine "lead/rhythm" labels; let the musician listen.
- **Preview region selection**: highest-activity windows (RMS/onset density) within contrasting sections (verse + chorus). Cheap — computed on the candidate audio, **no transcription needed**.
- **Multiple regions** useful when a part's role changes across sections.
- **Duration**: ~5–8 s per preview.
- **Cost**: requires the candidate *audio* (family stem, cloud today), **not** full-song transcription. Region picking is on-device-capable DSP.
- **Heavy overlap**: if only the family stem exists (no B), previews are of the whole family — the musician still hears "the guitars" but can't isolate lead vs rhythm. That is the gap, surfaced honestly.

Principle: **when machine certainty is low, use the musician.** Don't build an expensive classifier to avoid two 5-second previews.

### 13-characterization. Cheap part characterization

Given candidate audio, describe it without transcription. Classification of each feature:

| Feature | Status |
|---|---|
| activity ratio, silence ratio | **KNOWN AVAILABLE** — RMS/onset; `BeatOnsetExtractor` already does this on device |
| onset density | **CHEAP** — DSP |
| sustained vs transient energy | **CHEAP** — spectral flux / envelope |
| register / spectral centroid distribution | **CHEAP** — FFT/vDSP |
| section coverage, prominence | **CHEAP** — once SongStructure exists |
| harmonicity | **CHEAP-ish** — spectral peak structure |
| monophonic vs polyphonic estimate | **REQUIRES VALIDATION** — heuristic unreliable |
| melodic movement vs chordal | **REQUIRES VALIDATION / light pitch tracking** |
| repetition | **REQUIRES VALIDATION** — self-similarity, moderate cost |
| true role (lead/rhythm) semantic | **REQUIRES ML / not currently available** |

Prefer DSP where adequate. Characterization operates on whatever the separator produced: for a single family stem it describes the *whole* part (useful for a role *hint* and preview selection), not for splitting. **Characterization does not create candidates — separation does.**

## 14. Jam minimum artifact set (RESOLVED — is stems+grid enough?)

Grounded answer: **stems + grid alone is NOT quite enough; Jam also needs harmony + sections — but NOT target note transcription.** Confirmed against the actual wire contract: **`SongBundle` (`SongBundle.swift`) — the only analysis model the iOS client decodes — carries no per-note MIDI, no pitch contours, no "suggested notes" arrays.** It carries `meta{tempoBpm, detectedKey}`, `timeline{chords[symbol,start,end], sections, beats, downbeats}`, `stems[role,url]`, and `presets/chops`. Every note the Jam surface plays is *synthesized* from a chord symbol or scale math: `suggestedChords` ← `ChordSuggestions.suggestions(after:in:)`, degree pads ← `DiatonicChords.triads(key:)`, pad audio ← `ChordVoicing.midiNotes(symbol:)`, `JamPadGrid12` ← `key.root` + `ScaleIntervals`. None read a transcription.

| Artifact | Jam |
|---|---|
| waveform, duration | REQUIRED |
| tempo | REQUIRED |
| beat + downbeat grid | REQUIRED |
| sections | REQUIRED |
| chords / harmony | **REQUIRED** (drives ChordContext + chord pads) |
| key | **REQUIRED** (scale/pads) |
| target family stem | REQUIRED |
| backing mix | REQUIRED |
| lightweight target pitch/harmony info | OPTIONAL |
| **target note transcription** | **NOT NEEDED** |
| pad/suggestion generation | derived from chords+key (no transcription) |

**Jam TTP = time to `{stems, grid, sections, chords, key}` ready.** Notably harmony is promoted from "soft" (v1) to "required" (v2) — that is the corrected answer to open risk #3. Transcription stays out of Jam's hard path, preserving the big cost win.

## 15. Practice minimum artifact set

All of Jam's set **+ high-quality target-part transcription** (`NoteTranscription` for melodic parts, `DrumTranscription` for drums, `VocalPitch` for vocals) + optional `DifficultyMap` — **as a forward-looking requirement, not a current one.**

**Grounded correction (important).** The *current* Learn surface does **not** consume note transcription either. `LearnView` / `ChordCard` / `FretboardDiagram` derive everything from **chord symbols + key**: the fretboard comes from `GuitarVoicing.shape(symbol:)` (a symbol→fret-window algorithm, `Theory/GuitarVoicing.swift`), roman numerals from `RomanNumeral.label(symbol:key:)`. Per-note MIDI *is* produced backend-side — `SessionBundle.user_midi: InstrumentMIDI` with per-note arrays and section `landmark_notes` (`session/bundle.py:_build_user_midi`, `contracts.py:633`) — but the iOS client has **no Swift model for `SessionBundle`/`user_midi` and never decodes or renders it.** Transcription is therefore a **built-but-unconsumed** artifact today.

Consequence for MVP: **transcription is a latent Practice dependency that activates only when Learn adds true note-by-note practice of the user's own part** (tab/lick/exact-notes mode). Until that feature exists, even Practice can run on `{stems, grid, sections, chords, key}`. This makes the target-first cost win larger than v1 claimed — we are currently paying for MIDI extraction whose output no client renders — and raises a real MVP question (§29): *do we run target transcription at all before the consuming UI ships?* Recommended: **gate transcription behind the Learn note-practice feature**, not behind mode selection alone.

## 16. Perform minimum artifact set

Backing mix + grid + sections + `PerformanceScene`; **chords/harmony REQUIRED** (ChordContext appears on Perform too, per `ui-refactor-decisions`); target stem SOFT (only if live monitoring/scoring is added); **transcription NOT needed**. Perform inherits the instrument/state built in Jam (Perform = play+manipulate, no construction). Perform TTP ≈ Jam TTP (both need harmony+sections+backing).

### 16-matrix. Experience × artifact requirements (with part resolution)

`H` hard / `S` soft / `—` n/a. New row: **candidate-part resolution**, which is mode-dependent.

| Artifact | Jam·Gtr | Practice·Gtr | Perform·Gtr | Practice·Drums | Practice·Vox |
|---|---|---|---|---|---|
| grid + tempo + sections | H | H | H | H | H |
| chords + key | H | H | H | S | H |
| target family stem | H | H | S | H | H |
| backing mix (family-suppressed) | H | S | H | S | S |
| **candidate-part resolution** | **S** (family ok) | **H** (need the exact part) | **S** (suppress family) | S (usually 1 kit) | H (lead vs backing) |
| target transcription | — | H (Notes) | — | H (DrumEvents) | H (VocalPitch) |
| performance scene | — | — | H | — | — |

Reading it: **Practice is where part precision (and therefore §11's gap) bites hardest**; Jam/Perform tolerate family-level targeting.

## 17. Backing-mix bleed (RESOLVED — open risk #1)

The strongest concrete argument for source-instance separation.

Scenario: **Practice Lead Guitar.** The backing should exclude the *lead* but keep the *rhythm*. With only family separation, muting "guitar" removes **both** → wrong backing. Options:
1. **Family-level mute** — removes all guitar. Correct only when the user plays the whole/only guitar part. **MVP behavior.**
2. **Mid/side pan suppression** (`decompose_stem_pan`) — suppress hard-panned doubles; partial, fails on center-panned or correlated parts.
3. **True source-instance suppression / TF-bin masking** — keeps rhythm, removes lead. **Needs capability B (§11); does not exist yet.**

Quality threshold: acceptable when residual target bleed in the backing is low enough not to confuse the player against their own live playing — define a target-to-backing suppression ratio metric during implementation, don't guess now.

**Conclusion:** MVP ships **family-level mute**; per-part backing is post-MVP, **gated on the Lab delivering B**. Document as a known limitation on the Practice·Lead experience, not a silent degradation.

## 18. Invalidation rules (unchanged core, part-scoped)

Follows DAG edges + identity keys; nothing global.
- New bass transcription model ⇒ mark `NoteTranscription(bass_part_*)` **stale** only. Waveform/grid/stems/chords/guitar untouched.
- New separator ⇒ `StemSeparation` + child stems/backing stale ⇒ transitively any part transcription that hard-depends on them; grid/key/chords derived from the *original mix* untouched. **Also triggers part re-identification (§12-identity).**
- New eval/render config ⇒ **no inference invalidation**.
- Stale ≠ deleted; still serviceable (flagged outdated), recomputed lazily. User never blocked by an upgrade.

## 19. Quality tiers (unchanged, part-aware)

`FAST` (on-device/cheap) → `STANDARD` (Lab-crowned specialist) → `HIGH` (best/slow/ensemble). Transparent upgrades for **timing/grid**; **gated** upgrades for **note-content** (never change notes under a performing user — offer opt-in "improved version available"). Same rule applies per part.

---

# PART C — Resolved open risks & cross-cutting policy

## 20. Song identity / hash strategy (RESOLVED — open risk #2)

Separate **cache correctness** from **cross-import dedup**:
- **Cache key = `content_hash` = sha256 of the normalized *decoded PCM*** that `ImportCoordinator.produceAnalysisWAV` already emits as the canonical analysis input. Same audio through the same pipeline → same hash → correct reuse. This is the only key artifacts are stored under.
- **Cross-encoding dedup** (same recording as MP3 vs AAC vs WAV): decoded PCM differs sample-wise across lossy codecs, so `content_hash` will (correctly) differ — they are different inputs. True "same song" dedup needs an **acoustic fingerprint** (chromaprint-style) as a *separate, optional* `acoustic_id`, **not** the cache key.
- **Recommendation: dual identity.** `content_hash` (correctness, MVP) + optional `acoustic_id` (dedup hint, post-MVP). **Do NOT build a fingerprint service for MVP.**
- **Unresolved:** is `produceAnalysisWAV` byte-stable across app versions? If resample/normalize params change, hashes shift and cache misses. Must verify + pin the normalization config into the hash (§27).

## 21. Router-table ownership & promotion (RESOLVED — open risk #4)

```
Lab result  →  human review  →  production SpecialistRegistry (versioned, immutable)  →  planner pins a version
```
- The Lab **never** writes the production registry. Promotion = an explicit commit editing a checked-in `specialist_registry.json` (git-versioned), reviewed by the one human.
- Each entry records provenance: Lab run id + validated metrics (e.g. Bass MT3 recall .427). Artifacts store the registry **version** they were produced under.
- **Rollback** = pin the previous registry version. **Research experiments cannot silently alter production** because production reads only pinned, committed versions.
- Simple for a one-person company: one JSON file in git, referenced by version.

## 22. Lab GM classes ↔ product taxonomy (RESOLVED — open risk #5)

Solved by the Family→Subfamily→Part model (§10). Mapping layer:
- Product family (visible) → set of GM/model subfamilies (internal). `Keys → {piano, electric_piano, organ, synth_lead, synth_pad}`; `Guitar → {electric, acoustic}`; `Vocals → {lead, backing}`.
- User says "Keys"; discovery finds present subfamilies; multiple credible → "Which part?" with **subfamily-derived musician labels** ("Piano"/"Organ"/"Synth"), auditionable. **GM taxonomy never reaches the UI.** Router consumes the internal `RoutingKey` (§12-router); finer internal routing than the visible taxonomy is explicitly supported.
- **Favor simple UX:** user picks a coarse family, not a subfamily, unless auditions demand disambiguation.

## 23. Background-analysis policy (Option C ≠ "analyze everything")

Explicit priority classes — Option C does **not** mean "target first, then auto-analyze the rest":

| Class | Meaning | Trigger |
|---|---|---|
| **TARGET-CRITICAL** | H artifacts for current (family, mode) | immediately, user waiting |
| **OPPORTUNISTIC** | cheap + high-reuse (chords/key/structure once stems exist) | device idle + charging + wifi |
| **ON-DEMAND** | triggered by explicit action | target/part change, Mixer open, "analyze all" |
| **NEVER-BY-DEFAULT** | transcribing non-selected parts/instruments | only explicit user request |

Never burn cloud/GPU simply because an artifact is missing. Background = demand-driven or clear-benefit only, cost-gated, cancellable.

## 24. Time-To-Playable update + Time-To-Part-Choice

Ambiguity adds latency metrics:
- **TTP-single-part**: family → 1 candidate → playable. (= §14–16 TTP.)
- **TTP-multi-part**: family → candidates discovered → **Time-To-Part-Choice** → user picks → selected-part analysis → playable.
- **Time-To-Part-Choice (new)**: import → candidate previews ready for the user to audition. A real UX latency metric; exists only in the multi-part case. Minimizing it means making FamilyDiscovery + preview-region selection cheap (§13-audition) and, ideally, on-device.

Instrument all separately (extends v1's Time-To-First-Playback / Time-To-Beat / Time-To-Target-Stem / Time-To-Basic-Jam / Time-To-Full-Analysis).

## 25. On-device opportunities (part discovery)

| Capability | Feasibility |
|---|---|
| activity/onset/RMS, register/centroid, pan mid-side split, preview-region pick | **ON DEVICE NOW** — vDSP/Accelerate; `BeatOnsetExtractor` already computes onsets+RMS |
| mono/poly estimate, melodic-vs-chordal | **ON DEVICE AFTER MODEL CONVERSION / UNKNOWN — BENCHMARK REQUIRED** |
| true source-instance separation | **SERVER REQUIRED** — and the model does not exist yet (§11, §26) |
| specialist transcription | **SERVER REQUIRED** initially — model discovery precedes any iOS port |

Gating reality: on-device *part discovery* still needs the **family stem**, which is cloud Demucs today. So device-side characterization/preview is unlocked only once the stem is local. Not implementing — just mapping.

## 26. Requirements for the Specialist Model Discovery session

Formulated as research requirements (do **not** benchmark here):

1. **Same-instrument source-instance separation** — does any model split one family stem into individual sources?
2. **Guitar lead/rhythm separation** specifically.
3. **Vocal lead/backing separation.**
4. **Keys subfamily separation** — piano vs organ vs synth from a mixed keys bucket.
5. Do candidate **transcription models assume already-isolated monophonic sources**? (Determines whether separation must precede transcription.)
6. Does **separation quality cap transcription accuracy more than the transcription model itself**? (Where to spend the quality budget.)
7. Do **specialist separators exist per family** (a bass-specific, vocal-specific separator)?
8. Should **source separation itself become a specialist routing tree** (family → separator model), parallel to the transcription router?

These directly gate: multi-part UX (§13), per-part backing (§17), and whether MVP can ever exceed family-level targeting.

## 27. Revised implementation phases

**Phase 0 — store + planner + registry (backend, invisible).** ArtifactStore (extend `JobRegistry`/history/R2) with **scoped keys** (§10-schema), DAG+planner behind existing `run_file_analysis`, checked-in `specialist_registry.json` (§21). Pin normalization config into `content_hash` (§20). No UX change.

**Phase 1 — target-first, family-level.** Split `run_file_analysis` into per-artifact jobs; queue target family stem + backing + grid + harmony first; transcription conditional on Practice. Router wired from current Lab findings. Cloud-GPU-only. **Ships strategy A (one part per family).**

**Phase 2 — target UX + progressive readiness.** "What are you playing?", readiness stages, partial `BundleStore`. Users first feel the TTP win.

**Phase 3 — candidate parts (where cheaply possible).** FamilyDiscovery + `decompose_stem_pan`-backed candidates, "Which part?" + audition previews (§13). Only lights up on hard-panned doubles until a Lab separator lands. Part-identity binding (§12-identity).

**Phase 4 — on-device FAST tier.** Provisional grid, on-device VocalPitch/DrumTranscription, on-device characterization/preview selection, offline floor.

**Phase 5 — real source-instance separation (Lab-gated) + execution scheduler + opportunistic background.** Per-part backing (§17 option 3), full multi-part discovery, location-selecting scheduler.

Phases 0–2 deliver most of the cost + TTP win at family granularity. Parts are additive, not a rewrite.

### 27-schema. Updated artifact key (typed scope, no nullable soup)

```python
Scope = SONG_GLOBAL | FAMILY(family) | PART(family, part_id)

ArtifactKey = (content_hash, artifact_type, scope, producer_id, producer_version, config_hash)

# SONG_GLOBAL: GlobalAnalysis, BeatGrid, SongStructure, KeyEstimate, ChordProgression
# FAMILY:      StemSeparation (producer) → Stem(family), BackingMix
# PART:        NoteTranscription / DrumTranscription / VocalPitch, DifficultyMap, PerformanceScene
```
`scope` is a typed union, so song-global artifacts carry no dangling `part_id`, and part artifacts require one — no nullable-field chaos.

### 27-discovery. Separation-discovery interface

```
discoverParts(song, family) -> [CandidatePart]
CandidatePart {
  part_id, family, subfamily?, audio_ref,
  descriptors { role_hint?, register, activity_summary, character }, confidence,
  preview_regions[], producer, producer_version
}
```
Architectural interface only. For MVP, `discoverParts` returns a single family-level candidate for all families except where `decompose_stem_pan` legitimately splits.

## 28. Changing target / part; multiple instruments

- **Family change** (Bass→Guitar): re-plan; reuse global + stems already `ready`; request only the guitar delta; cancel unneeded background bass work; UI shows only "Building your guitar part."
- **Part change** (within Guitar): rebind `UserSelection`; reuse family stem; request the new part's transcription only.
- **Multiple instruments**: lazy triggers only — user requests another (§28), Mixer opened (⇒ expand to full stem taxonomy), or opportunistic background (idle+charging+wifi, opt-in). "Analyze everything" is an explicit button, never the default.
- **Mixer** stays a pure consumer of `StemSeparation` (family stems + faders); opening it expands separation to the full family taxonomy.

---

# PART D — Final review

## Answers to the 18 review questions

1. **Does Option C remain recommended?** Yes. v2 refines the target definition; the target-first + progressive-background core is unchanged and still correct.
2. **Is "What are you playing?" still the only import question?** Yes — the only *mandatory* one. "Which part?" is conditional (§13) and appears only on genuine ambiguity.
3. **When should JAM ask "Which part?"** Only when FamilyDiscovery yields ≥2 credible candidates — today, only hard-panned double-tracked parts qualify (§11, §13).
4. **Can we identify lead/rhythm cheaply without full transcription?** Partially. Cheap DSP gives *hints* (activity/register/onset-density — §13-characterization), not reliable semantic labels. Treat as hints; let audition decide.
5. **When should the musician just audition?** Whenever candidates exist and machine confidence in labels is low — i.e. essentially always for lead/rhythm. Musician is ground truth (§13-audition).
6. **Does current separation provide those candidates?** No, beyond family level. Only `decompose_stem_pan` surfaces extra candidates, and only for hard-panned doubles (§11).
7. **If not, what capability is missing?** True same-instrument source-instance separation (TF-bin pan-angle masking or a specialist per-family separator). Flagged to the Lab (§26).
8. **Same-instrument separation required for MVP?** **No.** MVP ships family-level targeting. Multi-part is post-MVP, Lab-gated (§27 Phases 3/5).
9. **Is stems + grid enough for Jam?** Not quite — Jam also needs **harmony (chords/key) + sections**. But **not** target transcription (§14).
10. **Minimum Jam set:** `{waveform, tempo, beat/downbeats, sections, chords, key, target family stem, backing}`. No transcription.
11. **Minimum Practice set:** Jam set **+ target-part transcription** (Notes/DrumEvents/VocalPitch) — but only *once Learn ships note-by-note practice*. **Today Learn is symbol-derived** (`GuitarVoicing`), and backend `SessionBundle.user_midi` is produced yet never decoded by the client. So current Practice = Jam set; transcription should be **gated behind the consuming feature**, not run speculatively (§15).
12. **Minimum Perform set:** `{backing, grid, sections, chords/harmony, performance scene}`; target stem soft; no transcription.
13. **Identical songs, different encodings?** Cache key = decoded-PCM `content_hash` (correctness). Cross-encoding dedup = optional acoustic fingerprint, post-MVP. Dual identity; no fingerprint service for MVP (§20).
14. **Who owns/promotes the routing table?** Human-reviewed promotion of Lab results into a git-versioned `specialist_registry.json`; production pins immutable versions; Lab never writes it (§21).
15. **GM ↔ product mapping?** Family→Subfamily→Part; product shows coarse families + audition labels; GM stays internal in the router (§22).
16. **New requirements for Specialist Discovery?** The 8 in §26 — chiefly: does same-instrument source separation exist, and does separation quality cap transcription.
17. **Still-unvalidated assumptions?** See §29.
18. **What NOT to build yet?** See §30.

## 29. Assumptions still unvalidated (risk register v2)

- **Same-instrument separation is unsolved** in our stack — all multi-part UX depends on a Lab result that may not exist.
- **`produceAnalysisWAV` byte-stability** across app versions — unverified; cache correctness depends on it (§20).
- **Cheap characterization reliability** — activity/register hints are plausible but unproven as role indicators (§13-characterization: REQUIRES VALIDATION rows).
- **`decompose_stem_pan` real-world hit rate** — how often it fires correctly vs mis-splits on real catalogs is unmeasured.
- **Jam-needs-harmony-not-transcription** — grounded in current UI (`ChordContext`/chord pads derive from chords+key), but validate no Jam feature secretly wants the user's transcribed part.
- **Backing suppression threshold** — the acceptable target-to-backing ratio is undefined (§17).
- **Part-identity re-matching confidence** — the rebind-vs-reask threshold (§12-identity) needs empirical calibration.
- **Store scaling** — JSON+R2 at per-artifact × per-part granularity; define the trigger to move to a real DB.
- **Transcription is currently built-but-unconsumed** — production runs MIDI extraction into `SessionBundle.user_midi`, but no iOS surface decodes it (§15). Either a note-practice feature is imminent (then keep it, gated) or this is pure wasted compute today (then stop running it until the UI exists). Product must decide.
- **Role-string cache collisions** — the client keys stems by role-as-filename (`BundleStore.swift:124`); shipping >1 stem per role today would silently collide. The `part_id`/scoped-key migration (§10, §12-identity) must land before real multi-part stems are emitted.

## 30. What NOT to build yet

- Same-instrument source-instance separation / TF-bin masking (wait for Lab §26).
- The cost-optimizing execution scheduler (interface only).
- On-device ports of Demucs / Basic Pitch / MT3.
- An acoustic-fingerprint service (§20).
- Speculative multi-instrument background enrichment.
- A relational artifact DB (extend JSON+R2 first).
- The explicit Jam/Practice/Perform selector (mode = the tab).
- **Speculative target transcription** — do not keep running MIDI extraction into `SessionBundle.user_midi` while no client renders it (§15); gate it behind the Learn note-practice feature.
- Any change to the Transcription Lab or specialist research.

## 31. Files that would eventually change — **DO NOT MODIFY YET**

Backend: `backend/local_engine/analysis_worker.py` (`run_file_analysis:308`, split into per-artifact jobs) · `backend/tone_forge/unified_pipeline.py` (presets → planner-driven) · `backend/tone_forge/analysis_jobs.py` (`JobRegistry:97` → scoped ArtifactStore) · `backend/tone_forge_api.py` (`analyze_upload_endpoint:2176`, engine claim/complete `:2359–2478`; add plan + per-artifact + discover-parts endpoints) · `backend/tone_forge/r2_storage.py`, `provenance.py` · **new** `backend/tone_forge/artifacts/` (store, planner, DAG, SpecialistRouter, registry, discoverParts). Read-only deps: `stem_separator.py` (family sep + `decompose_stem_pan`), `midi/gpu_extractor.py`, `midi/coreml_extractor.py`, `beat_tracking.py`, `analysis/chords.py`, `analysis/structure.py`, Lab adapters.

iOS: `Import/ImportCoordinator.swift` (family selection + readiness) · `ToneForgeEngine/JobClient.swift`, `AnalyzeClient.swift` (plan + per-artifact + candidate previews) · `ToneForgeEngine/BundleStore.swift` (partial bundles) · **new** "What are you playing?" + "Which part?" audition view + readiness indicator · `Views/Tabs/*`, `Navigation/AppTab.swift` (inferred mode). Reuse read-only: `BeatCapture/*`, `ToneForgeML/CoreMLBeatClassifier.swift`, `DSP/*` (FAST tier + on-device characterization).

---

## FINAL DECISION

**Option C — target-first + progressive background — remains the recommendation, at family granularity for MVP, with a clean path to parts.**

Rationale for a one-person company: preserves musical quality (specialist models, applied only where needed); default work drops from ~6 instrument-analyses/song to ~1; reuses the existing worker protocol, JSON+R2 store, and on-device DSP; TTP is the explicit target so the user plays early; every "better model discovered" (including a future source separator) is a versioned registry edit + targeted invalidation, not a redesign.

The v2 amendment's job was honesty: **we do not yet have same-instrument source separation.** MVP therefore targets the *family*, ships the multi-part UX as a hook that lights up where the cheap pan-split legitimately fires, and sends the real research need to the Lab. No musical reality is forced into a `LEAD`/`RHYTHM` enum; the musician auditions when the machine is unsure.

## Should implementation start? **No — still design-only.**

Wait on: (a) Lab answer to §26 Q1/Q5/Q6 (does source separation exist; does it cap transcription) — it determines whether Phase 3/5 is even fundable; (b) verification that `produceAnalysisWAV` is byte-stable (§20). Phases 0–2 (family-level, the bulk of the value) are unblocked and could start on approval, but the instruction is design-only — so this returns for review.

*End of proposal v2. No code changed.*
