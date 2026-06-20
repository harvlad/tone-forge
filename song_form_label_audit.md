# Song-Form Label Audit

**Date:** 2026-06-20
**Scope:** Measurement + architecture only. No code modified.
**Question:** Does the ToneForge pipeline actually understand song form
(intro / verse / chorus / bridge / outro), or are those labels heuristic
placeholders that the UI presents as evidence of musical understanding?

**Verdict (executive):** **Option C — labels are largely heuristic
placeholders and should not be presented as detected musical structure.**

Quantitative headline:
* **0 of 215 sections** across **100 history sessions** have ever been
  classified `chorus`.
* **0 of 215 sections** have ever been classified `bridge`.
* `verse` accounts for **47%** of all sections; `unknown` (rendered as
  the placeholder string `"section"` by the bundle layer) accounts for
  **31%**.
* `intro` and `outro` together account for **6.5%** — i.e. essentially
  one each per song.

Detail follows.

---

## A. Architecture trace

### A.1 Call chain (top → bottom)

| Step | File:line | Symbol |
|---|---|---|
| 1 | `backend/tone_forge/unified_pipeline.py:1643` | `UnifiedPipeline._detect_sections()` |
| 2 | `backend/tone_forge/analysis/sections.py:201` | `SectionDetector.detect_sections()` |
| 3 | `backend/tone_forge/analysis/sections.py:241` | `_detect_boundaries()` (energy-curve novelty) |
| 4 | `backend/tone_forge/analysis/sections.py:398` | `_classify_sections()` (per-section feature extract) |
| 5 | `backend/tone_forge/analysis/sections.py:507` | `_classify_section_type()` — **label assignment** |
| 6 | `backend/tone_forge/analysis/sections.py:656` | `_refine_intro_outro()` — **post-hoc label overwrite** |
| 7 | `backend/tone_forge/analysis/sections.py:93`  | `ArrangementSection.to_dict()` — emits `"type"` |
| 8 | `backend/tone_forge/session/bundle.py:506-509` | `_iter_sections()` — maps `"type"` → contract `label` |
| 9 | `backend/tone_forge/contracts.py:205-220` | `Section.label: str` — UI-facing field |
| 10 | `backend/static/jam.js:2281` | `const label = s.type || s.name || s.label || 'Section';` |
| 10b | `backend/static/debug.js:205,262,759` | `sec.label || 'section'` / `sec.label || '—'` |

### A.2 Inputs to the label classifier

The only signals consulted by `_classify_section_type()` (sections.py:507-564):

* `index` — position in section list (integer)
* `num_sections` — total section count
* `energy_mean`, `energy_peak`, `energy_std` — RMS-based per section
* `global_energy_mean` — track-wide RMS mean
* `spectral_centroid` — accepted as a parameter but **never read** in the
  function body (dead input)
* `note_density` — `librosa.onset.onset_detect()` count ÷ duration (NOT
  polyphonic density; just onset events per second)
* `duration` — seconds

Signals that exist elsewhere in the codebase but are **NOT** consulted
by the label classifier:

* `SectionFeatures.repetition_score`
  (`backend/tone_forge/analysis/section_features.py`)
* `SectionFeatures.chord_density_per_s`
* `SectionFeatures.monophonic_ratio`
* `SectionFeatures.polyphony_score`
* Chord-lane similarity / chord-progression matching
* Self-similarity matrix (no SSM is ever constructed)
* Vocal stem activity (would distinguish verse from chorus on many songs)
* MIDI feature aggregation
* Beat / downbeat grid (only consulted by `_detect_boundaries` to snap
  boundary times, not by the labeller)

### A.3 Boundary detection (for completeness)

`_detect_boundaries()` (sections.py:285) finds local maxima in the
derivative of the smoothed RMS curve, optionally snaps to the nearest
tracked beat (sections.py:241 + Probe-5 design in
`backend/segmenter_followup_probes.md`). That part is grounded in a real
audio signal. The **boundaries** are evidence-based; the **labels** are
not.

---

## B. Label frequency statistics

Source: `backend/data/history.json` (100 entries, 215 total sections,
all analyses since the history file was created).

### B.1 Aggregate distribution

| Label | Count | Percent | Detector rule (sections.py) |
|---|---:|---:|---|
| `verse` | 101 | **47.0 %** | medium-energy + density ≥ 2 (the default mid-song bucket) |
| `unknown` | 67 | **31.2 %** | no rule matched; bundle layer rewrites to `"section"` |
| `buildup` | 27 | 12.6 % | medium-energy + energy_std > 0.2 |
| `intro` | 8 | 3.7 % | positional + low-energy refinement |
| `outro` | 6 | 2.8 % | positional + low-energy refinement |
| `drop` | 6 | 2.8 % | high-energy + peak > 0.9 + std < 0.15 |
| **`chorus`** | **0** | **0.0 %** | high-energy + density > 3 — **never fires** |
| **`bridge`** | **0** | **0.0 %** | medium-energy + middle position — **never fires** |
| `breakdown` | 0 | 0.0 % | low-energy + density < 2 — never observed on guitar mixes |

### B.2 Per-session breakdown (sessions with ≥ 3 sections)

| Session | Song | # sections | Labels emitted |
|---|---|---:|---|
| `22ba2ff1` | Sex On Fire (Run B, post-fix) | 20 | verse=18, intro=1, outro=1 |
| `b640c78a` | Sex On Fire (Run A, post-fix) | 20 | verse=18, intro=1, outro=1 |
| `fcc38d5c` | Sex On Fire (post-fix) | 20 | verse=18, intro=1, outro=1 |
| `07e61e2a` | Sex On Fire (pre-fix) | 22 | verse=20, intro=1, outro=1 |
| `126c9515` | Sex On Fire (pre-fix) | 22 | verse=20, intro=1, outro=1 |
| `6a32035b` | (Drum-machine-style track) | 8 | verse=3, drop=3, intro=1, buildup=1 |
| `29b31695` | (Same track, different mix) | 8 | verse=3, drop=3, intro=1, buildup=1 |
| `fcbb84bf` | (Short clip) | 3 | intro=1, verse=1, outro=1 |

### B.3 Trial corpus status

`backend/tests/fixtures/song_trial_corpus.json` (5 songs):

| Song | Has ground truth? | Has analysis bundle? |
|---|---|---|
| Stairway to Heaven | yes (4 sections) | not yet analyzed |
| Hotel California | yes (4 sections) | not yet analyzed |
| Wish You Were Here | yes (4 sections) | not yet analyzed |
| Romance de Amor | yes (2 sections) | not yet analyzed |
| Sex On Fire | yes (5 sections, added today) | yes — `b640c78a`, `22ba2ff1`, `fcc38d5c` |

`JAD` and `LMIP` are not present in the repository or in
`history.json` — no analyzed bundle exists for those songs. The
calibration evidence base is currently Sex On Fire plus the drum-
machine session, total ~50 sections of meaningful coverage.

---

## C. Whether labels are evidence-based

For each label, what genuinely supports the choice:

| Label | Real signal? | What actually drives it |
|---|---|---|
| `intro` | partial | First-section position + `energy_mean < 0.7 × next_section.energy_mean`. Refinement at `sections.py:670-684` can overwrite the initial label. No melodic, harmonic, or instrumentational analysis. |
| `outro` | partial | Last-section position + `energy_mean < 0.7 × prev_section.energy_mean`. Refinement at `sections.py:691-705`. Same caveat as `intro`. |
| `verse` | no | Fallback bucket for any mid-song section whose energy ratio is between 0.6× and 1.2× of the track mean and whose onset rate is ≥ 2/s. Carries no semantic content beyond "neither very loud nor very quiet". |
| `chorus` | **no — and empirically never fires** | Requires `energy_ratio > 1.2` AND `note_density > 3.0` simultaneously. On 100 production runs the rule has fired **0 times**. Operationally this label does not exist in the product. |
| `bridge` | **no — and empirically never fires** | Requires medium energy + not in the first or last 20% of sections. Same empirical truth: **0 hits in 100 runs**. |
| `drop` | partial | High energy + peak > 0.9 + low std. Fires on drum-machine / electronic content but never on the guitar-rock corpus we are calibrating against. |
| `buildup` | partial | Medium energy + std > 0.2. Detected only on the K7l5ZeVVoCA drum-machine session. |
| `breakdown` | no | Low energy + sparse onsets. Has fired 0 times in this dataset. |
| `unknown` | n/a | No rule matched. Bundle layer (`session/bundle.py:506-509`) replaces with the placeholder string `"section"` before it reaches the UI. |

**Does a dedicated chorus detector exist?** **No.** The codebase
contains exactly one place where the string `chorus` is produced
(`sections.py:553`). That site is one branch of an `if/elif` ladder
whose condition is `is_high_energy and note_density > 3.0`. There is:

* no self-similarity matrix
* no chord-progression similarity check
* no comparison between sections (sections are classified independently)
* no vocal-stem or vocal-energy check
* no repetition/loudness-contrast detection
* no harmonic/lyrical anchor

The `repetition_score` signal that the riff-first milestone added in
`section_features.py` is computed per stem per section but is
**never consulted** by `_classify_section_type()`. It feeds only the
guidance-mode classifier.

### C.1 Real example: Sex On Fire `b640c78a`

Engine ground-truth comparison (corpus + tab):

| Engine | t_start | Engine label | Engine guidance | Ground-truth song form | Ground-truth mode |
|---:|---:|---|---|---|---|
| 0 | 0.00 | intro | chord 1.00 | Intro (anthem chord wash) | chord ✓ |
| 1 | 9.47 | verse | chord 1.00 | Intro continued | chord ✓ |
| 2 | 15.79 | verse | chord 1.00 | Intro continued | chord ✓ |
| 3 | 23.24 | verse | chord 1.00 | Intro tail | chord ✓ |
| 4 | 28.70 | verse | chord 1.00 | Intro→Verse 1 transition | chord ✓ |
| 5 | 32.76 | verse | chord 1.00 | **Verse 1 riff** | **riff** ✗ |
| 6 | 39.33 | verse | chord 0.68 | **Verse 1 riff** | **riff** ✗ |
| 7 | 44.81 | verse | chord 0.76 | Verse 1 tail / Chorus 1 | chord ✓ (partial) |
| 8 | 67.59 | verse | chord 0.66 | Chorus 1 → V2 riff onset | mixed |
| 9 | 74.07 | verse | chord 0.52 | **Verse 2 riff** | **riff** ✗ |
| 10 | 109.57 | verse | chord 0.76 | Chorus 2 | chord ✓ |
| 11 | 117.47 | verse | chord 0.52 | Chorus 2 | chord ✓ |
| 12 | 123.74 | verse | chord 0.62 | Chorus 2 | chord ✓ |
| 13 | 128.31 | verse | chord 0.49 | Bridge | **(no `bridge` label emitted)** |
| 14 | 139.09 | verse | chord 1.00 | Bridge / Chorus return | chord ✓ |
| 15 | 154.88 | verse | chord 1.00 | Chorus return | chord ✓ |
| 16 | 161.89 | verse | chord 0.73 | Chorus return | chord ✓ |
| 17 | 169.83 | verse | chord 1.00 | Outro chorus | chord ✓ |
| 18 | 176.10 | verse | **lead 0.77** | Outro chorus | chord ✗ (FP lead) |
| 19 | 204.75 | outro | chord 0.65 | Final tag | chord ✓ |

Observations:

1. **18 of 20 sections are labelled `verse`** despite the song
   containing intro, two verses, two choruses, a bridge, and an outro.
2. **The two `chorus` regions** (~110-140 s and ~155-200 s) are labelled
   `verse`. The chorus detector did not fire.
3. **The bridge region** (~128-139 s) is labelled `verse`. No
   `bridge` was emitted.
4. **The `intro` and `outro` labels** at sections 0 and 19 reflect
   positional + energy heuristics, not a musical detector. They happen
   to be correct here because the song really does start quiet and end
   on a tag, but the same code would label the first section of a
   cold-open song as `intro` regardless of whether it actually is one.
5. The guidance-mode classification (right-hand columns) is correct on
   ~14/20 segments. The label classification (left-hand columns) is
   correct on ~3/20 segments under any generous interpretation
   (sections 0, 19, and arguably the post-intro warm-ups).

### C.2 Real example: across all post-fix Sex On Fire runs

Three independent analyses of the same source (`b640c78a`,
`22ba2ff1`, `fcc38d5c`) emit identical label distributions:
`verse=18, intro=1, outro=1`. The labeller is deterministic but
flat — it produces effectively the same low-information output for
every guitar-rock song.

### C.3 Bonus: key detection has a similar failure mode

While auditing, the operator confirmed via Google + Hooktheory that
the song is in **E major**, but the engine consistently detects
**G# minor** (its relative minor, same key signature). This is the
classic chroma-template ambiguity between a major key and its
relative minor; the engine is not weighing tonic emphasis, so the
template-correlation lands on either parallel with no tiebreaker.

This is out of scope for the song-form audit, but it's tracked in
`song_trial_corpus.json:97` (`engine_detected_key_note`) as another
example of the UI surfacing a confident answer that the underlying
detector arrived at without sufficient signal.

---

## D. UI accuracy assessment

### D.1 What the UI presents

* **JAM section chip strip (`backend/static/jam.js:2281`)**: each chip
  shows `s.type || s.name || s.label || 'Section'` and the start time.
  For a Sex On Fire bundle the user sees 22 chips reading "intro 0:00",
  "verse 0:09", "verse 0:15", … "outro 3:24".
* **Debug ML Decision Inspector (`backend/static/debug.js:205, 262,
  759`)**: each timeline cell renders `sec.label`, the radar panel
  displays `label: <value>`, and the corpus-comparison tab joins on
  `label`.

### D.2 What that wording implies vs what the backend actually does

| UI affordance | Implies | Actually backed by |
|---|---|---|
| Chip labelled `chorus` | A chorus detector identified this region | Never emitted (0 of 215 in production) |
| Chip labelled `verse` | A verse detector identified this region | "Mid-song section whose energy is between 0.6× and 1.2× the track mean and whose onset rate ≥ 2/s" |
| Chip labelled `intro` | An intro detector identified this region | First section in the list + energy < 70% of section 2 |
| Chip labelled `outro` | An outro detector identified this region | Last section in the list + energy < 70% of section N-1 |
| Chip labelled `bridge` | A bridge detector identified this region | Never emitted |
| `unknown` → rendered as `section` | A real section that couldn't be classified | Identical: bundle layer (`session/bundle.py:508`) substitutes the placeholder string `"section"` so the user never sees the honest `unknown` |

### D.3 Failure mode this creates

A user sees 20 chips on Sex On Fire saying `intro / verse × 18 /
outro` and reasonably concludes that the engine "thinks the whole song
is one big verse with a short intro and outro tag". That conclusion is
not what the engine thinks — the engine simply has no `chorus`/`bridge`
classifier that fires on real guitar audio, and `verse` is its
fallback bucket for everything that isn't first, last, or a drum-
machine drop.

### D.4 Comparison: when does the UI *not* overstate?

The same UI also shows guidance-mode (chord / riff / lead) — and for
that label the backend does have a real per-stem signal pipeline,
real thresholds, and per-section confidence numbers that respond to
audio content. The mode label is honest. The song-form label is not.

---

## E. Test coverage

Search of `backend/tests/` for assertions on label values:

| Test file | What it asserts | What it does NOT assert |
|---|---|---|
| `test_pipeline_output_invariants.py:549-590` | Legacy `"type":"intro"` round-trips to `section.label == "intro"` through the bundle | Whether `intro` was the correct classification |
| `test_pipeline_output_invariants.py:617-655` | `ArrangementSection(type=SectionType.VERSE)` → bundle → `section.label == "verse"` | Whether the detector should have picked VERSE |
| `test_pipeline_output_invariants.py:657-710` | guidance_mode round-trip | label correctness |
| `test_section_boundary_snap_to_beats.py` | Boundaries snap to beats | Anything about labels |
| `test_section_boundary_bar_grid_fallback.py` | Bar-grid fallback | Anything about labels |
| `test_section_features.py` | Per-stem feature numerics | Whether features feed into labelling (they don't) |

**No test in the repo asserts on the logic inside
`_classify_section_type()`.** If the function were swapped for
`return SectionType.VERSE` on every call, the only tests that would
fail are the field-name compat tests — and only because they
specifically construct INTRO/VERSE instances and round-trip them.

---

## F. Comparison and recommendation

### F.1 Qualitative quality assessment

| Capability | Rating | Evidence |
|---|---|---|
| Guidance-mode classification (chord / riff / lead) | **Medium** | Real per-stem signals (`monophonic_ratio`, `repetition_score`, `chord_density_per_s`, `polyphony_score`, `lead_activity_score`); calibrated thresholds in `GuidanceThresholds`; per-section confidence reflects real signal margin; ~14/20 correct on Sex On Fire with known failure modes (palm-mute attenuation, stem dilution). Improvable, but it's measuring something. |
| Section boundary detection | **Medium** | Real energy-novelty + beat-snap path. Stable boundaries across deterministic runs (verified post-Demucs fix: 20/20 boundaries match A vs B). |
| Song-form labelling (intro / verse / chorus / bridge / outro) | **Low** | `chorus` and `bridge` have **never** fired in 100 runs (0/215 sections). `verse` is the fallback bucket for 47% of all sections; `unknown` covers 31% but is silently relabelled `section` by the bundle layer. `intro`/`outro` are positional + energy-ratio heuristics, not musical detectors. No self-similarity, no harmonic comparison, no vocal/instrumentational analysis. No test exercises the classifier logic. |

### F.2 Recommendation

**Option C — song-form labels are largely heuristic placeholders and
should not be presented as detected musical structure.**

Rationale:

1. The empirical evidence is unambiguous. Two of the five label values
   (`chorus`, `bridge`) are operationally dead. A third (`verse`) is
   a residual bucket, not a positive classification. Together those
   three account for >75% of every song's chips today.
2. The honesty gap is concrete. The bundle layer already swallows the
   most honest output the classifier can produce (`unknown` → `section`)
   so the UI never has to admit that no rule fired. That suppression
   is itself a signal that the labels are presentation-only.
3. Guidance-mode is the real artefact. The riff-first milestone built
   a calibrated per-stem signal pipeline. The song-form labeller did
   not get an equivalent build. They should not be presented at the
   same visual weight when their evidentiary backing differs by
   roughly an order of magnitude.

### F.3 What this audit does NOT recommend

Out of scope, per the user's directive:

* Do not modify the classifier
* Do not modify thresholds
* Do not modify section generation
* Do not modify the UI

This document records findings only. Any UI de-emphasis, replacement
of the song-form labeller with a real SSM-based detector, or label-
suppression in the JAM chip strip is a follow-up decision for the
operator.

### F.4 Suggested follow-up tracks (for the operator to schedule)

Roughly in increasing order of effort, none implemented here:

1. **Visual de-emphasis** — render `verse`/`section` chips in a muted
   colour while keeping `intro`/`outro`/`drop`/`buildup` at full
   weight. Communicates the actual confidence asymmetry without
   removing information.
2. **Surface `unknown` honestly** — stop the `unknown → section`
   rewrite in `session/bundle.py:508` so the UI can render an
   honest "unclassified" state.
3. **Real chorus detector** — add an SSM-based or chord-progression-
   similarity-based section classifier that wires `repetition_score`
   (already computed) into the label decision. Would also unblock
   real `bridge` detection.
4. **Vocal-stem signal** — chorus on most guitar-rock tracks tracks
   the vocal stem's energy + harmony shape. Cheap signal, large
   return.
5. **Calibration corpus expansion** — analyse the four trial-corpus
   songs that have ground truth but no bundle (Stairway, Hotel
   California, Wish You Were Here, Romance de Amor). Will confirm
   whether the 0/215 chorus count generalises beyond Sex On Fire.

---

## Appendix: provenance

* `backend/data/history.json` snapshot read at 2026-06-20.
* Source code reads against the current working tree
  (branch ahead of `origin/main` by 15 commits including
  `ad25b0d` JAM section-chip fix and `d59a6ea` Demucs determinism
  fix landed earlier today).
* Ground-truth labels for Sex On Fire: Songsterr lead-guitar tab
  + Google/Hooktheory key confirmation, recorded in
  `backend/tests/fixtures/song_trial_corpus.json:86-110`.
* All `file:line` references verified against the working tree.
