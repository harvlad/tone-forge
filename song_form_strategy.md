# Song-Form Capability Review

**Date:** 2026-06-20
**Companion to:** `song_form_label_audit.md` (`e26aed2`)
**Scope:** Capability assessment + product-path comparison only.
No code changes. No classifier tuning. No UI changes.

**Question this document answers:** given that song-form labels today
are heuristic placeholders (audit Option C), should JAM evolve a real
song-form classifier, remove the labels, or de-emphasize them?

**Recommendation (executive):** **Phased Path B → Path C.** Land a
small UI de-emphasis commit immediately to close the honesty gap
(Path B), then schedule a single-milestone Path C effort that wires
the existing `repetition_score` + chord-lane similarity into a real
classifier, gated on first expanding the calibration corpus from
1 analyzed song to ~6 analyzed songs. Skipping straight to Path C
without the corpus produces another set of un-validated heuristics.
Skipping straight to Path A throws away the parts of today's
labeller that actually work (intro / outro / drop on a quiet start /
loud end). Rationale in §6.

---

## 1. Information that could support song-form detection

### 1.1 Catalogue of plausible signals

| Signal | Definition | Why it would help song form |
|---|---|---|
| Repetition score | Self-similarity of pitch sequences over a section | Chorus = "thing the song does multiple times"; bridge = "thing the song does once" |
| Section-to-section similarity | Pairwise distance over per-section feature vectors | Verses and choruses cluster; bridges sit alone |
| Chord-progression similarity | Edit-distance / alignment between chord sequences of two sections | Two sections with the same chord loop are the same song-form role |
| Energy profile | RMS shape over the section | Choruses are loud; verses moderate; intros/outros quiet at one end |
| Instrumentation changes | Stem activity on/off across sections | Drums-in transition is often "chorus enters"; vocals-out is often "intro/outro/instrumental" |
| Stem activity patterns | Per-stem energy / note rate per section | Vocal stem energy peak ≈ chorus on most pop/rock |
| Spectral centroid / brightness | High-freq energy distribution | Choruses often brighter (cymbals open, lead enters) |
| Vocal phrase grid | Vocal phrase onsets, length, repetition | Lyrical hook lines correlate with chorus boundaries |
| Position in song | Index, percent-of-song | Intro=start, outro=end, bridge=middle and unique |
| Section duration | Length of segment | Bridges are often shorter than verses/choruses |
| Downbeat phase alignment | How boundaries align to bars | Real song-form boundaries land on 4/8/16-bar grids |
| Lyrical content | Repeated lyric lines | Chorus = literally the same words twice |

### 1.2 Which signals would be needed for each label

| Label | Minimum-viable signal set | Nice-to-have |
|---|---|---|
| Intro | Position (first) + low energy + vocals tacit | Drums tacit; rising spectral centroid into section 2 |
| Outro | Position (last) + energy fade + vocals tacit | Drums tacit; section duration short |
| Verse | Vocals active + chord-progression repeat across multiple sections + medium energy | Lyrical phrase pattern |
| Chorus | Vocals active (loudest) + chord-progression match to ≥1 other section + high energy + brighter spectrum | Drum density up; repeated lyric line |
| Bridge | Mid-song position + chord progression NOT matching verse or chorus + duration shorter than verse | Vocals optional; harmonic detour |
| Drop / breakdown | Already adequately handled by today's energy heuristics | — |
| Buildup | Already adequately handled by today's energy heuristics | — |

The minimum-viable set for the four song-form labels that matter
(intro/outro/verse/chorus) is the union of:

* **section-to-section chord-progression similarity** (the single
  most discriminating signal for verse vs chorus on guitar-rock)
* **vocal stem energy / activity per section**
* **position + duration**

Bridge detection further requires the similarity matrix to be present
so we can identify the one section that is "not like the others".

---

## 2. Signals already computed today

Inventory of what each stage of the pipeline already produces and
whether the song-form labeller currently consults it.

| Signal | Where it's computed | Currently consulted by song-form labeller? |
|---|---|---|
| `repetition_score` (per stem per section) | `backend/tone_forge/analysis/section_features.py:61` | **No.** Used only by guidance-mode. |
| `chord_density_per_s` (per section) | `section_features.py:54` | **No.** Used only by guidance-mode. |
| `monophonic_ratio` | `section_features.py:58` | **No.** |
| `polyphony_score` | `section_features.py:65` | **No.** |
| `lead_activity_score` | `section_features.py:68` | **No.** |
| `pitch_class_diversity` (per section per stem) | `section_features.py:89` | **No.** |
| Per-stem MIDI inside section | upstream of `section_features` | **No.** |
| Chord lane (full song) | `backend/tone_forge/analysis/chords.py` + `chord_detector.py` | **No.** The lane is computed and reaches the bundle, but `_classify_section_type` does not read it. |
| `energy_mean`, `energy_peak`, `energy_std` per section | `backend/tone_forge/analysis/sections.py:428-449` | Yes — sole positive input. |
| `note_density` (onset count / second) | `sections.py:444-446` | Yes — sole positive input besides energy. |
| `spectral_centroid` per section | `sections.py:415` | Passed to `_classify_section_type()` but **never read** in the function body. Dead input today. |
| Beats / downbeats grid | `unified_pipeline.py` (via librosa or beat-tracker) | Consulted by `_detect_boundaries` for boundary snapping; not by the labeller. |
| Per-stem audio (Demucs output) | `backend/tone_forge/stem_separator.py` | Not consumed by the labeller. |
| Per-stem energy / activity profile | **Not pre-computed at section level.** Would have to be aggregated from the existing stem audio + section boundaries. | n/a |
| Self-similarity matrix (SSM) | **Not computed anywhere.** | n/a |
| Vocal phrase segmentation | **Not computed anywhere.** | n/a |
| Lyrical content | **Not computed anywhere.** | n/a |
| Tempo / time signature | Computed | Available but not used by the labeller. |

**Key observation.** The riff-first milestone already built the
infrastructure that would be needed for chorus / bridge detection
(`repetition_score`, `chord_density_per_s`, per-section per-stem
feature vectors). The labeller predates that infrastructure and
never picked it up.

---

## 3. Additional signals required for robust detection

What we would need to add to today's pipeline for each label.

### 3.1 Intro / outro detection

Already partially honest today (positional + energy ratio). Robustness
gap is small:

* **Vocal-stem tacit check.** Compute mean energy of the vocal stem
  during the section; if section 0's vocal energy is < 30% of section
  1's, that's hard evidence of an intro. Same for the closing section.
* **Drum-stem activity check.** Intros and outros often have drums
  off or sparse; drum-stem onset count per section is a cheap
  additional signal.
* **Estimated effort:** **Low.** Both signals can be derived from
  the existing per-stem audio in O(stem_length) and added as fields
  to `SectionFeatures`.

### 3.2 Verse detection

Verse is currently a fallback bucket. Making it a positive
classification requires:

* **Cross-section chord-progression similarity matrix.** Build an
  N×N matrix where entry (i,j) = normalised edit distance between
  the chord sequence inside sections i and j. The chord lane is
  already available — this is a straightforward aggregation +
  alignment step.
* **Vocal-stem activity per section.** Verses on most rock/pop
  songs have vocals active but at moderate energy.
* **Estimated effort:** **Medium.** The SSM is the main piece;
  vocal aggregation is trivial once that scaffold is up.

### 3.3 Chorus detection

The hardest of the four if done right.

* **Same chord-progression SSM** as verse detection — chorus is
  "the high-energy cluster that repeats". Reuse, don't rebuild.
* **Vocal-stem energy peak per section.** Most genres put the
  loudest vocal in the chorus.
* **Spectral brightness uplift.** Cymbals open; lead enters. The
  `spectral_centroid` we already compute but never read becomes
  the input here.
* **Hook-line repetition (lyrics).** Optional — out of scope until
  a vocal/lyric model is added, which is a much bigger lift.
* **Estimated effort:** **Medium.** All signals listed are
  available in the current stem catalogue; the dependency is the
  SSM from §3.2.

### 3.4 Bridge detection

Definitionally "the section that is not like the others".

* **Requires** the chord-progression SSM from §3.2/§3.3 — without
  a similarity matrix, "not like the others" is unmeasurable.
* **Position prior:** middle-of-song.
* **Duration prior:** typically shorter than the surrounding
  verses/choruses.
* **Estimated effort:** **Medium-High** if the SSM exists;
  **High** without it (would be re-implementing the SSM solely
  for this detector).

### 3.5 Drop / buildup / breakdown

Already work reasonably well on the one electronic-style session
in `history.json`. No change needed.

---

## 4. Complexity estimate per detector

Effort buckets calibrated against past work on the riff-first
milestone (`section_features.py` + `guidance_mode.py` were ~3
commits of focused work — that's the "medium" reference point).

| Detector | Effort | Signals required | Notes |
|---|---|---|---|
| Intro / Outro (improved) | **Low** | Vocal-stem energy aggregate, drum-stem onset count | Add two fields to `SectionFeatures`; rewrite `_refine_intro_outro`; reuse existing per-stem audio. |
| Drop / Buildup / Breakdown | **Low (no work)** | Already handled | Today's energy heuristics are adequate for the genres these labels target. |
| Chord-progression SSM | **Medium** | Existing chord lane + new pairwise alignment helper | New module `analysis/section_similarity.py`; ~200 lines + tests. Reusable by chorus, verse, bridge. |
| Verse vs Chorus discriminator | **Medium** | SSM + vocal-stem energy + spectral centroid | After SSM exists, this is a clustering + threshold step; ~150 lines + tests. |
| Bridge detector | **Medium-High** | SSM + position prior + duration prior + chord-novelty | Sits on top of SSM. Conservative confidence (won't fire often) is appropriate. |
| Calibration corpus expansion | **Medium** | Run pipeline on Stairway, Hotel California, Wish You Were Here, Romance de Amor; label ground truth from existing tab PDFs | Each run is ~25 min on this hardware. Labeling per song is similar effort to Sex On Fire today. |
| Vocal-phrase / lyrical hook detection | **High** | Vocal stem + new phrase segmenter or speech-to-text + alignment | Significant new dependency. Defer. |

---

## 5. Three product paths

### Path A — Remove song-form labels entirely

Hide the label string in JAM and Debug UIs. Sections keep their
boundaries and guidance_mode but no human-readable label.

* Pros:
  * Eliminates the honesty gap completely.
  * Smallest engineering footprint.
  * Forces downstream features (recommend, search, share) to lean
    on guidance_mode, which is the honest signal.
* Cons:
  * Throws away the cases where labels are correct (intro on
    quiet starts, outro on energy fade, drop on EDM-style track).
  * Users lose a familiar UX affordance ("jump to chorus") that
    they may not realise was unreliable to start with.
  * Doesn't preserve the option to bring labels back if a real
    detector lands later — the UI plumbing would need to be re-
    added.

### Path B — Keep labels but de-emphasize them visually

Render `verse` and `section`-placeholder chips in a muted style;
keep `intro`, `outro`, `drop`, `buildup` at full visual weight.
Optionally surface the underlying `unknown` honestly instead of
the bundle layer's rewrite to `section`.

* Pros:
  * Communicates the asymmetry between guidance-mode (real) and
    song-form (heuristic) without removing information.
  * Tiny diff: 2 CSS rules + 1 JS class toggle + 1 line removed
    from `session/bundle.py:508`.
  * Reversible: when a real detector ships, drop the muted class
    and the chips fire at full weight again.
  * Preserves the correct-by-construction labels (intro/outro on
    quiet-start/quiet-end songs).
* Cons:
  * Doesn't actually improve labelling quality — only its
    presentation.
  * Operators may forget the muting and miss that the labels are
    still inaccurate.

### Path C — Build a genuine song-form classifier

New module `analysis/section_similarity.py` (chord-lane SSM), wire
into a new `analysis/song_form_classifier.py` that consumes the
SSM + per-stem energy + per-stem activity from `SectionFeatures`,
emits a real {intro, verse, chorus, bridge, outro} label per
section with a calibrated confidence. Calibrated against an
expanded corpus (5 → ~6 analyzed songs minimum to start).

* Pros:
  * Honest labels at full visual weight — what the UI says is
    what the engine knows.
  * Reuses the riff-first playbook (per-section feature vectors +
    calibrated decision rule + frozen thresholds dataclass) that
    has already shown it works for guidance-mode.
  * Unlocks downstream features that need real form labels
    (e.g. "loop the chorus", "skip to the bridge", chorus-only
    practice mode).
* Cons:
  * Largest engineering footprint. Multi-commit milestone (rough
    sketch: SSM + verse/chorus + bridge + corpus expansion +
    JAM gating ≈ 5-7 commits at the granularity we've been
    shipping).
  * Calibration risk: with only Sex On Fire analyzed today, any
    thresholds chosen are over-fit to one song. Must expand the
    corpus first.
  * Vocal-stem dependency: Demucs vocal isolation quality varies
    by song; chorus detector that depends on it may inherit
    those failure modes.

---

## 6. Per-path estimates

### 6.1 Engineering effort

| Path | Estimate | Breakdown |
|---|---|---|
| A | **Very Low** | 1 commit: hide chip text in `jam.js` + `debug.js`; remove from bundle output (optional). |
| B | **Low** | 1-2 commits: CSS rule + JS class toggle + optional `unknown` honesty in `session/bundle.py`. |
| C | **High** | 5-7 commits: corpus expansion (1), SSM module (1), verse/chorus discriminator (1), bridge detector (1), pipeline wiring (1), JAM gating (1), regression smoke (1). |

### 6.2 Expected user value

| Path | Estimate | Justification |
|---|---|---|
| A | **Low** | Removes information that today is sometimes correct; no replacement. Negative-value possible if users relied on the existing "jump to intro/outro" affordance. |
| B | **Low-Medium** | Doesn't add capability; aligns UI claims with engine reality. Users get a clearer mental model of what the engine actually knows. Value compounds when the team later ships Path C — there's a place to "un-mute" the labels back to full weight. |
| C | **High** | New capability: section-aware practice (loop the chorus, drill the bridge), section-aware sharing (deep-link to the chorus), section-aware analytics. Path B's framing also remains valid (full-weight chips after C lands). |

### 6.3 Technical risk

| Path | Risk | Notes |
|---|---|---|
| A | **Very Low** | UI-only deletion. No model changes, no calibration. Hardest part is preserving the API contract for older clients. |
| B | **Very Low** | CSS + class toggle. Reversible. No backend changes required (the `unknown` honesty flip is one optional line). |
| C | **Medium-High** | Calibration risk is real: choosing thresholds against one song (Sex On Fire) will not generalise. Mitigation is corpus expansion *before* threshold freezing — same gating discipline the riff-first milestone used. SSM correctness is testable on synthetic fixtures (the same playbook as `test_section_features.py`). Vocal-stem dependency adds a Demucs-quality coupling that may regress in surprising ways. |

### 6.4 Sensitivity to corpus state

Critical operational fact: the calibration corpus today has
**1 analyzed song** (Sex On Fire) with ground truth. Stairway,
Hotel California, Wish You Were Here, and Romance de Amor have
ground-truth labels but no analyzed bundle to score against.

* Path A: insensitive — no calibration required.
* Path B: insensitive — no calibration required.
* Path C: **highly sensitive.** Cannot responsibly land thresholds
  for chorus/bridge until at least 5-6 songs are analyzed.
  Romance de Amor in particular (polyphonic classical guitar, no
  stems split useful) is a known stress test that should be
  included before thresholds are frozen.

---

## 7. Recommendation

### 7.1 Recommended path: Phased B → C

**Phase 1 (immediate, low effort): Path B.**

* 1 small commit that mutes `verse` and `section`-placeholder chips
  visually (e.g. 40% opacity, no border).
* Optional follow-up commit that flips the
  `session/bundle.py:508` `unknown → section` rewrite back to
  honest `unknown`. Tested with an additive bundle-schema test in
  `test_pipeline_output_invariants.py` to ensure older clients
  don't break.
* Keep `intro`, `outro`, `drop`, `buildup` at full visual weight
  — those labels are correct often enough to be valuable.
* Total effort: 1-2 commits.
* Total user value: closes the honesty gap *now* while preserving
  the correct-by-construction labels.

**Phase 2 (scheduled, gated): Path C.**

Sequenced as separate, independently-green commits, modelled on
the riff-first milestone:

| # | Commit | Gate to proceed |
|---|---|---|
| 1 | Analyze the four remaining corpus songs (Stairway, Hotel California, Wish You Were Here, Romance de Amor) | All four runs return HTTP 200; bundles persist |
| 2 | `analysis: section_similarity.py` — chord-lane SSM | Synthetic-fixture tests for similar/dissimilar sections |
| 3 | `analysis: vocal/drum-stem per-section energy fields` (extend `SectionFeatures`) | Existing tests stay green; additive schema |
| 4 | `analysis: song_form_classifier.py` consuming SSM + stem energies | Synthetic-fixture tests for each label; ≥ 4 of 5 corpus songs labelled correctly |
| 5 | `pipeline: wire song_form_classifier into _classify_sections` | Round-trip test for new label field; old bundle compatibility |
| 6 | `jam: un-mute song-form chips now that labels are honest` | Phase-1 muting class removed |
| 7 | Regression: corpus-wide assertion that no song is 100% verse-labelled | Lock the failure mode that drove this audit |

Phase 2 is gated on Phase 1 (operator can ship Phase 1 today; Phase
2 lands as effort permits) and on the calibration corpus reaching
analyzed status (Commit 1 above is the gate for everything else).

### 7.2 Why not Path A alone?

Removes information that today is sometimes correct. Specifically
the `intro` and `outro` heuristics get the right answer on most
guitar-rock songs (Sex On Fire's 0.0s `intro` and 204.7s `outro`
are both correct in `b640c78a`). Path A throws those wins away
without compensation.

### 7.3 Why not Path B alone?

It doesn't fix the underlying capability gap. Users will still
ask "why doesn't JAM know where the chorus is?" — the answer
becomes "because we muted the label", not "because we built the
detector". Acceptable as a stopping point only if the team
deprioritises song-aware practice features entirely.

### 7.4 Why not Path C alone?

Calibration risk. With only one analyzed corpus song today, any
thresholds chosen are over-fit. Riff-first milestone discipline
was "expand the synthetic fixture set first, then freeze
`GuidanceThresholds`"; the equivalent here is "expand the
analyzed corpus first, then freeze `SongFormThresholds`". Path B
ships a real product improvement while that expansion is in
flight, instead of blocking on Phase 2 entirely.

### 7.5 Risks the recommended path explicitly accepts

* The Phase 1 muted-chip state will live for some time before
  Phase 2 lands. That's a feature, not a bug — it sets honest
  expectations and gives the team room to expand the corpus
  without UI pressure.
* The `unknown → section` honesty flip in Phase 1 is technically
  a UI-visible string change. Acceptable per the audit's premise
  that UI honesty is the goal.
* Phase 2's vocal-stem dependency couples song-form quality to
  Demucs vocal-isolation quality. Mitigation: keep the labeller
  conservative — emit `unknown` rather than guessing when the
  vocal stem is below a confidence floor.

---

## 8. Out of scope

This document explicitly does NOT:

* propose specific threshold values for any new classifier
* modify classifier code
* modify UI code
* commit to a Phase 2 timeline
* recommend abandoning guidance-mode

This document records the capability assessment that the operator
asked for. Any implementation work begins with a separate plan
under the appropriate phase header above.

---

## Appendix: signal-to-effort matrix

Single-table summary for fast reference.

| Signal | Computed today? | Used by song-form labeller? | Effort to wire in |
|---|---|---|---|
| Energy mean / peak / std | Yes | Yes | n/a |
| Note density (onsets/s) | Yes | Yes | n/a |
| Spectral centroid | Yes (passed) | No (dead input) | Low — already passed, just consult it |
| Position (index / first / last) | Yes | Yes | n/a |
| Duration | Yes | Yes (intro/outro only) | n/a |
| Repetition score (per stem) | Yes | No | Low — wire into chorus condition |
| Chord density per second | Yes | No | Low |
| Monophonic / polyphony ratio | Yes | No | Low |
| Pitch-class diversity | Yes | No | Low |
| Chord lane (full song) | Yes | No | Medium — needs SSM aggregation |
| Beats / downbeats | Yes | Indirectly (boundary snap) | Low — for duration-in-bars |
| Per-stem audio | Yes (Demucs output) | No | Medium — need per-section aggregation |
| Per-stem energy / activity per section | **No** | n/a | Low-Medium — new `SectionFeatures` field, derive from existing stem audio |
| Self-similarity matrix | **No** | n/a | Medium — new module, ~200 LOC + tests |
| Vocal phrase / lyric content | **No** | n/a | High — new dependency |

The headline pattern: most of the signals a real classifier needs
**are already computed and persisted today**. The gap is plumbing,
not perception.
