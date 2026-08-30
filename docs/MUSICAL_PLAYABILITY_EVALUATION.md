# Musical Playability Evaluation Framework

Status: **Design only. No implementation, no Lab change, no inference, no thresholds, no composite score.**
Companion to `INTENT_DRIVEN_ANALYSIS_ARCHITECTURE.md` (accepted baseline) and `SONG_UNDERSTANDING_CAPABILITY_MAP.md`. Consumes Specialist Discovery output; does not direct it.

**Evidence tags used throughout:** `[KNOWN]` grounded in code/architecture or basic music fact · `[HYP]` reasoned hypothesis, unproven · `[NV]` needs validation against real musician judgment before we rely on it.

> Guard rails honored: no arbitrary percentages are assigned; no fake composite "JAM Score" is created. Thresholds are specified as *things to learn*, not *things to declare*.

---

## 0. TL;DR

Engineering metrics (recall/precision/F1/octave/onset) measure *transcription fidelity*. JAM sells *musical usability*, which is a different quantity. This document defines the bridge:

1. **Playability is mode- and instrument-specific.** There is no one metric. Jam/Perform are driven by chords+grid+sections+stems (**not** notes — grounded in the `SongBundle` contract); Practice is the only mode where per-note fidelity matters, and even then only once note-aware UI ships.
2. **Errors are not equal.** A hierarchy (pitch/timing/rhythm/duration/omission/hallucination/harmonic/structural/register) with severity that depends on *musical position* (strong beat vs ornament, chord root vs passing tone) and *instrument*.
3. **Aggregate recall hides the experience.** Phrase-level, bar-level, and **contiguous-playable-run** measures matter more than a global number. **Systematic errors (e.g. the whole bass −12 semitones) are cheaply correctable; random errors are not** — and standard F1 can't tell them apart.
4. **The founder-scale method is forced-choice blind A/B** on short clips, used to discover *which automated proxies predict musician acceptance*, from which evidence-based thresholds are later learned. Not a 500-person study.

The single most important claim: **note F1 is a weak, sometimes actively misleading, proxy for playability** (§Final-Q1/Q2).

---

## 1. What "musical usability" means for JAM `[KNOWN]`

Grounded in the product: a song is *usable* when the musician can do their intended thing (jam / practice / perform) against it without errors that break flow, groove, or trust. Usability is defined **per experience**, not per song, mirroring the intent-driven architecture's mode split.

Operationally, three felt properties:
- **Trust** — nothing obviously, distractingly wrong (a false note on beat 1; the bass an octave off).
- **Flow** — the musician can play *continuously*; errors don't force a stop.
- **Groove** — timing feels locked to the beat they hear.

None of these is "% of notes correct."

## 2. Why conventional transcription metrics are insufficient `[KNOWN]` + `[HYP]`

- **They're position-blind.** `[KNOWN]` A missed ornament and a missed downbeat root count identically in recall. Musically they are worlds apart.
- **They're distribution-blind.** `[KNOWN]` 70% recall scattered vs 70% recall with whole phrases clean are the same number, opposite experiences (§7, §9).
- **They penalize correctable errors as if fatal.** `[KNOWN]` A uniform −12-semitone bass (the real `riley_bass_p12` register situation — a systematic shift, trivially transposable) can score terribly on strict-pitch recall yet be one `+12` away from perfect. Random 15%-of-notes octave jitter scores similarly but is *not* correctable (§11, §12).
- **The Lab matcher already forgives octaves separately.** `[KNOWN]` per `transcription-lab-infra`: recall is scored strict-pitch with octave errors bucketed separately (`oct` ≈ .163 for BP). So the headline recall *understates* pitch-class usefulness — a musician following a bass line often only needs the pitch class + register consistency, not the exact octave.
- **They ignore what the product actually renders.** `[KNOWN]` Jam/Perform never read notes at all (§3) — so note-F1 is the *wrong axis entirely* for two of three modes.

## 3. Mode-specific quality requirements

### JAM `[KNOWN — grounded in SongBundle + JamView]`
Jam renders from `timeline.chords[symbol,start,end]`, `sections`, `beats`/`downbeats`, `meta.key`, and `stems` — pads/suggestions are theory-derived (`ChordVoicing`, `DiatonicChords`, `ChordSuggestions`). **Per-note transcription is not on the Jam path.** So Jam playability is governed by:

| Signal | Damage if wrong | Severity `[HYP]` |
|---|---|---|
| **Beat grid / downbeats** | everything is grid-anchored; drift destroys pad timing + loops | **CRITICAL** |
| **Chord symbols + timing** | wrong chord under the player = wrong suggested notes, sour jam | **MAJOR–CRITICAL** |
| **Sections** | wrong loop/section boundaries; NEXT wrong | MAJOR |
| **Key** | scale pads wrong | MAJOR |
| **Target/backing stem quality** | bleed/artifacts audible while jamming | NOTICEABLE–MAJOR |
| per-note transcription | *not used* | N/A |

**Jam's quality gates are chord/beat/section/stem gates, not transcription gates.**

### PRACTICE / LEARN `[KNOWN current + HYP future]`
**Current** Learn is *also* symbol/voicing-derived (`GuitarVoicing.shape(symbol:)`, `RomanNumeral`) — it does not consume the backend `SessionBundle.user_midi`. So today Practice inherits Jam's gates.
**Future** note-aware Practice (follow-the-part, slow-down, loop-a-phrase, show-notes, compare-my-playing, learn-riffs) is where per-note fidelity becomes load-bearing. For each capability the tolerance differs:

| Practice capability | What must be right `[HYP]` | Tolerant to |
|---|---|---|
| follow the part visually | contour + rhythm + register consistency | isolated missed ornaments |
| slow-down / loop a phrase | the *phrase* must be internally correct (§9) | errors outside the looped phrase |
| show exact notes (tab) | strong-beat pitch + onset placement | duration sloppiness |
| compare user performance | onset timing + pitch class; needs stable reference | absolute octave if consistent |
| learn a riff | the *riff bars* specifically must be clean | rest-of-song errors |

### PERFORM `[KNOWN — grounded]`
Architecture: no transcription. Gates are: **backing integrity** (no target bleed, no lost musical material), **grid/tempo stability** (predictable, no drift), **scene/section boundaries**, **playback determinism**. These are separation- and timing-quality gates, wholly different from transcription accuracy.

## 4. Instrument-specific requirements

Do not force one model across families.

- **Bass** `[HYP]`: **root accuracy on strong beats is paramount**; **octave consistency** highly audible (bass octave slips are obvious); **groove/timing critical**; ornamental omission often acceptable. The `+12` register situation is the canonical bass failure *and* the canonical cheap fix (§11).
- **Guitar** `[HYP]` — split by regime:
  - *Lead/melodic*: contour + onset timing + strong-beat pitch; single-line, monophonic-ish.
  - *Rhythm/chordal*: chord-tone coverage + strum rhythm > exact voicing; polyphonic (§14). Getting 4/6 chord tones with the right root ≫ 6 random pitches.
- **Piano / Keys** `[HYP]`: **polyphony + chord correctness** dominate raw note recall; voice-leading and root/bass note matter; simultaneity (notes that should sound together) matters more than isolated recall. High smoke recall (e.g. the kong piano result) is promising *but* recall alone won't tell us if chords are voiced right (§14).
- **Drums** `[KNOWN — pitch metrics inappropriate]`: evaluate **events** (kick/snare/hat/tom/cymbal), **velocity/accents**, **beat-position accuracy**, **groove/microtiming**, **fills**. Reuse the on-device role vocabulary (`BeatOnsetExtractor`, `BeatClassifier.mlmodelc`) as the event taxonomy. No pitch F1.
- **Vocals** `[HYP]`: **pitch contour** fidelity, **phrase timing/boundaries**, **range**, later **lyric alignment**. Continuous-contour error (cents deviation over voiced regions), not note-onset recall, is the natural unit.
- **Synth lead / other melodic** `[HYP]`: treat like guitar-lead (melodic contour), with the caveat that pitch-tracker confusion rises on heavily processed timbres.

## 5. Error taxonomy `[KNOWN structure]`

Nine families (as enumerated in the brief), kept as the canonical taxonomy:

PITCH (semitone · scale-tone · octave · unrelated) · TIMING (onset dev · beat-relative dev · early/late · quantization) · RHYTHMIC (missed beat · missed repeated note · wrong subdivision · groove displacement) · DURATION (premature cutoff · over-sustain · overlap) · OMISSION (structural · ornamental · repeated) · HALLUCINATION (false on strong beat · false passing · persistent false · isolated false) · HARMONIC (wrong chord tone · non-chord tone · root error · quality error) · STRUCTURAL (wrong phrase · missing riff · wrong section · repeat mismatch) · REGISTER (right class wrong octave · consistent displacement · sporadic displacement).

## 6. Error-severity framework `[HYP — all severity claims need §17 validation]`

Severity is **not intrinsic to the error type** — it's `f(error_type, musical_position, instrument, mode, consistency)`. First-pass severity ranking (explicitly a hypothesis to be tested, not a decision):

| Likely CRITICAL | Likely MAJOR | Likely NOTICEABLE | Likely MINOR/NEGLIGIBLE |
|---|---|---|---|
| beat-grid drift (any mode) | wrong chord root under player | 30–80 ms onset dev | <30 ms onset dev |
| false note on downbeat | sporadic random octave slips | non-chord passing tone | missed ornament |
| wrong chord *quality* on held chord | missing main riff (structural) | premature cutoff | over-sustain by a little |
| bass octave *inconsistency* | 100–200 ms onset (groove-breaking) | one missed repeated 8th | isolated far-offbeat false note |
| whole-part wrong section | persistent low-level false notes | slight duration sloppiness | quiet ornamental omission |

The `[KNOWN]` anchors inside this `[HYP]` table: onset thresholds of *perceptual* groove-breaking are well-established in the psychoacoustics literature at roughly the ~50 ms "flam" region and ~100 ms+ "clearly late" region — but the exact JAM tolerance is `[NV]`.

## 7. Phrase-level evaluation `[KNOWN concept, HYP units]`

Aggregate song recall is the wrong altitude. Evaluate at **section** and **phrase** granularity using artifacts we already have: `sections` and `beats/downbeats` from the bundle define natural windows.
- Report a per-section accuracy vector, not one song number. `Verse .95 / Chorus .92 / Solo .20` tells the real story a single `.70` hides.
- **Riff/hook awareness**: weight the sections a musician actually cares about. The main riff being wrong while filler is right is worse than the inverse — global recall inverts this.

## 8. Bar-level evaluation `[KNOWN — derivable]`

Bars are the musician's working unit for looping/practice. With the beat grid + downbeats (in bundle; derivable from Slakh MIDI tempo), score **per-bar correctness** (some threshold of pitch+timing correctness within the bar, threshold to be *learned* §27, not declared). Output = a per-bar boolean/score stream. This feeds §9.

## 9. Contiguous-playability `[KNOWN concept — the key novel metric]`

**Playable-Run-Length (PRL): the distribution of consecutive correct bars before a major (CRITICAL/MAJOR) error.** This directly captures "can I play along without being thrown."
- Report the *distribution* (median run, longest run, #runs ≥ N bars), never a single scalar, and never a hard threshold yet.
- Worked contrast `[HYP]`: Model A (70% recall, errors scattered → short runs everywhere) vs Model B (65% recall, errors clustered in one solo → long clean runs elsewhere). **B is more practice-useful.** F1 says A wins; PRL says B. This is the framework's headline argument.
- Derivable **$0** from (GT notes, pred notes, beat grid) — no audio, no inference (§23).

## 10. Salience weighting `[HYP — derivable signals KNOWN]`

Weight notes by musical importance; a missed downbeat root ≫ a missed ornament. Salience signals and their availability:

| Salience signal | Source | Availability |
|---|---|---|
| on strong beat / downbeat | beat grid + downbeats (bundle) | `[KNOWN]` derivable now |
| chord root / chord tone | `timeline.chords` symbols | `[KNOWN]` derivable now |
| note duration | GT/pred note length | `[KNOWN]` derivable now |
| phrase boundary | sections + onset gaps | `[KNOWN]` derivable now |
| repeated motif / structural repetition | self-similarity over sections | `[HYP]` derivable, needs impl |
| velocity / prominence | GT velocity; pred velocity if present | `[KNOWN]` from GT; pred varies |
| bass root movement | bass GT + chords | `[HYP]` derivable |

**Do not bake salience weights as constants** — learn them from §17 (which errors musicians actually flag).

## 11. Systematic vs random errors `[KNOWN — grounded in real fix]`

The single most decision-relevant distinction, and standard F1 is blind to it.
- **Systematic / correctable**: a consistent transform explains the error — whole-part −12 semitones (the real bass register case, fixed by a `+12` transpose), constant onset latency, uniform octave shift. **Cheap to detect, cheap to fix, low true severity.**
- **Local / unpredictable**: random octave slips on 15% of notes, scattered false notes, jitter with no pattern. **Not correctable; high true severity.**

Proposed measurement (design, not built): fit the *best single global correction* (transpose, octave-fold, constant time-offset) and report **residual error after correction**. If a `+k`-semitone or `+t`-ms shift collapses the error, it was systematic; if residual stays high, it was random. Two models at equal raw F1 can sit at opposite ends of this axis. Register-consistency (§12) is a special case.

## 12. Register-error treatment `[KNOWN — matcher already separates this]`

The Lab already buckets octave errors out of strict recall (`oct` metric). Extend that thinking:
- Report **pitch-class recall** (octave-agnostic) *and* **octave-consistency** (is the displacement uniform?) as **separate axes** from strict-pitch recall.
- For **bass**, octave *inconsistency* is worse than octave *offset*: a uniformly-low bass is transposable; a bass that jumps octaves randomly is unplayable to follow. So `[HYP]` bass should be scored primarily on pitch-class + octave-consistency, with absolute octave a correctable secondary.
- This is where "high recall but obviously wrong" (whole bass an octave high) gets correctly *demoted* by consistency reporting and *rescued* by systematic-correction (§11).

## 13. Timing / groove evaluation `[KNOWN feasible]`
- **Beat-relative onset error**, not absolute: express each onset deviation as a fraction of the local beat/subdivision (needs the grid). 40 ms at 80 BPM ≠ 40 ms at 160 BPM in groove terms.
- Report **median and 95th-percentile** onset deviation separately — the tail is what breaks groove; the median is what sets feel.
- **Systematic latency** (constant offset) is correctable (§11) → separate it from jitter.
- Drums/rhythm: microtiming vs grid + accent placement.

## 14. Polyphonic / chord evaluation `[HYP]`
For keys/rhythm-guitar, recall-per-note misses the point. Evaluate **simultaneities**:
- **Chord-tone coverage**: of the notes sounding together, what fraction are correct chord tones; is the **root/bass** present.
- **Voicing plausibility** `[NV]`: is the predicted chord a reasonable voicing of the intended harmony (7/8 chord tones ≫ 3 unrelated pitches — the brief's example).
- **Spurious-note-in-chord** rate (hallucinated non-chord tones inside a held chord — more damaging than between chords).
- Reference against `timeline.chords` where GT notes are unavailable.

## 15. Separation-quality evaluation `[HYP proxies, KNOWN failure modes]`
User-facing failure modes → candidate proxies (all `[NV]` until correlated with listening):

| Failure | User experience | Candidate proxy | Needs |
|---|---|---|---|
| **Target bleed** | other instruments in your stem | SIR / target-to-other energy | audio + ref stems |
| **Target loss** | your part thins/vanishes | SDR drop / energy loss vs ref | audio + ref |
| **Backing damage** | muting your family removes needed material | backing-vs-ref-minus-target error | audio + ref |
| **Same-family collision** | can't isolate lead vs rhythm | (only measurable if ground-truth sources exist) | source-instance GT |
| **Artifacting** | warble/phase smear | spectral distortion / SAR | audio |

Slakh provides reference stems → SDR/SIR/SAR are computable offline (`[KNOWN feasible]`, requires audio). But **objective SDR correlates only loosely with perceived quality** `[KNOWN from source-sep literature]` → gate the important ones on listening (§17).

## 16. Same-family separation implications `[KNOWN — ties to architecture gap]`
From the architecture: same-instrument source separation is a **capability gap**. Evaluation consequence: **the "Practice Lead Guitar, keep Rhythm in backing" experience cannot be quality-gated yet** because we cannot produce the isolated sources to measure. The §15 "same-family collision" row is *unmeasurable* without source-instance ground truth. This framework should therefore, for now, **evaluate at family granularity** and flag per-part backing as blocked-on-Lab (consistent with the accepted ADR). When the Lab reports on source separation, the same-family oracle (§ Specialist handoff E) becomes the measurement.

## 17. Human listening / playability test design `[KNOWN method]`
The ground truth. Design principles:
- **Short clips**: 15–30 s, chosen to include a *contrasting* section pair (easy verse + hard solo) so PRL and phrase effects show.
- **Blind A/B forced-choice** beats 1–10 ratings `[KNOWN — forced-choice is more reliable than absolute Likert for small-N; less scale drift]`. Ask *comparative* questions:
  - "Which would you rather practice with?"
  - "Which preserves the groove?"
  - "Which has an error that immediately distracts you?" (can be "neither/both")
- **Task-anchored** questions, not aesthetic ones: "Could you learn this part from this?" (yes/no) · "Would you enjoy jamming against this backing?" (yes/no).
- Capture **free-text on the single most distracting error** — this is how we discover which taxonomy entries actually matter (feeds §10/§6 weights).

## 18. Minimum viable human test for a solo founder `[KNOWN — practical]`
The smallest credible protocol:
- **One expert listener (the founder) + occasional 2–3 musician friends.** Not a study; a calibration instrument.
- **~10–15 curated clips** spanning instruments/difficulty, fixed set reused over time (a personal "golden ears" set).
- **Blind pairwise** (A/B) between candidate models / config, order randomized, labels hidden.
- **Log**: winner + the one-line "worst error." ~20 min per sitting.
- Purpose is **not** statistical significance — it's to *label* enough examples that we can check which automated proxy (§19) agrees with the human winner. Even ~30–50 labeled pairs start revealing proxy correlation `[NV]`.

## 19. Automated proxy candidates `[HYP — to be validated §20]`
Candidates that *might* predict "usable": phrase-recall · strong-beat pitch accuracy · octave-consistency · false-notes-per-bar · median onset error · 95th-pct onset error · **contiguous correct bars (PRL)** · root-note accuracy · chord-tone coverage · section coverage. **Do not assume any of these is the answer.** They are hypotheses for §20.

## 20. How proxies get validated `[KNOWN method]`
```
human forced-choice labels (§18)  →  for each proxy, does it rank the pair the same way?
                                   →  keep proxies with high agreement; discard the rest
                                   →  combine only if a simple, interpretable rule emerges
```
- Measure **rank agreement** (does proxy prefer the human-preferred clip) across the labeled pairs.
- Prefer **few, interpretable** proxies over a fitted black-box score.
- A proxy earns trust only when it agrees with musician choice across enough diverse pairs — and it can *lose* trust on new material. Revalidate when the catalog or models shift.

## 21–26. Proposed future Lab metrics + implementation difficulty

Proposed additions to Lab reporting (**LATER, once validated — do not change the Lab now**), tagged by cost class:

| Metric | Class | Notes |
|---|---|---|
| pitch-class recall (octave-agnostic) | **DERIVABLE FROM EXISTING GT/PRED** | reuse matcher, relax octave |
| octave-consistency / systematic-shift residual (§11) | **DERIVABLE FROM GT/PRED** | fit best global transpose |
| strong-beat / downbeat pitch accuracy | **DERIVABLE (needs beat grid)** | grid from Slakh MIDI tempo |
| false-notes-per-bar | **DERIVABLE (needs grid)** | |
| median & p95 beat-relative onset error | **DERIVABLE (needs grid)** | |
| **contiguous playable-run-length (PRL)** | **DERIVABLE (needs grid + severity rule)** | the headline new metric |
| per-section / phrase recall vector | **DERIVABLE** | sections from GT/structure |
| root-note & chord-tone coverage | **DERIVABLE (needs chords)** | chords from GT or `analysis/chords` |
| drum event/groove metrics | **REQUIRES NEW FEATURES** | event taxonomy, not pitch |
| vocal contour (cents) / phrase timing | **REQUIRES NEW FEATURES + AUDIO** | f0 extraction on GT+pred |
| separation SDR/SIR/SAR, bleed, artifacting | **REQUIRES AUDIO (+ ref stems)** | Slakh has refs |
| perceived separation quality | **REQUIRES HUMAN VALIDATION** | SDR ≠ perception |
| salience weights, playability thresholds | **REQUIRES HUMAN VALIDATION** | learned, not declared |

### 23. What we can derive **immediately from existing caches for $0** `[KNOWN — with one caveat]`
From `(GT MIDI, cached per-note predictions, beat grid derived from GT tempo)`, **no audio, no inference, no GPU**: pitch-class recall, systematic-shift residual, per-section/phrase recall, strong-beat accuracy, false-notes/bar, onset percentiles, root/chord-tone coverage, and **PRL**.
**Caveat `[KNOWN]`:** per `transcription-lab-infra`, the original validated100 run stored **aggregates only** (`comparison_results.json`) — no per-note predictions. So $0-now applies to any model the Lab has cached **per-note** under the new immutable cache; the historical aggregate run must be **re-served from cache** (predictions already computed, keyed by hash — still $0 compute if cached) to expose per-note. Confirm which cached runs carry per-note before promising $0.

### 24. Requires new analysis (no new inference): drum-event scoring, structural/motif salience, self-similarity weighting.
### 25. Requires audio: all separation metrics, vocal f0 contour, any perceptual spectral measure.
### 26. Requires human validation: severity weights, proxy correlations, all thresholds, perceived separation quality.

## 27. Establishing evidence-based thresholds `[KNOWN method — no numbers now]`
```
NOT USABLE / EXPERIMENTAL / USABLE / GOOD / EXCELLENT
```
are **learned, not declared**:
1. Collect human forced-choice + task-anchored yes/no labels (§18) across many clips.
2. Find the region in validated-proxy space (§20) where musicians *consistently* say "yes, I could learn/jam with this."
3. Set band boundaries at those empirical transitions, **per instrument and per mode** (bass-practice threshold ≠ piano-practice threshold).
4. Re-estimate as data grows. Publish the boundaries *with* the evidence behind them.
Until then, report raw proxy distributions and human labels — **no band labels on unvalidated numbers.**

## 28. What "good enough" must NOT mean `[KNOWN — guard rails]`
- Must **not** mean a single composite score crossing an invented line.
- Must **not** mean high global recall (§2, §9).
- Must **not** be defined once and frozen — it drifts with catalog and models.
- Must **not** ignore mode (a great transcription doesn't help Perform; a great backing doesn't need transcription).
- Must **not** average away catastrophic-but-localized errors (main riff wrong) into a passing number.
- Must **not** treat systematic and random errors as equivalent (§11).

## 29. Recommended evaluation roadmap
1. **Now, $0**: implement the derivable-from-cache proxies (§23) as *reporting only* (pitch-class recall, systematic residual, phrase vector, PRL, strong-beat, onset percentiles) — outside the Lab, read-only over cached predictions. No thresholds.
2. **Build the golden-ears clip set** (§18) and start logging forced-choice labels.
3. **Correlate** proxies ↔ labels (§20); keep the winners.
4. **Add audio/separation + drum/vocal metrics** when those experiences approach.
5. **Learn thresholds** (§27) per instrument×mode once labels are sufficient.
6. **Propose validated metrics to the Lab** as additions (§30) — never before validation.

## 30. Handoff recommendations for Specialist Discovery `[advisory — do not change Lab now]`
- Keep recall/precision/F1/octave as the **discovery filter** — fine for scouting.
- **Before crowning a production winner**, add (once validated here): **systematic-shift residual** (catch/repair the `+12`-style cases automatically — a low-recall model that's uniformly transposed may be the real winner after correction), **octave-consistency**, **phrase-recall vector**, and **PRL**. A model with lower F1 but longer playable runs and a correctable global shift may beat a higher-F1 scattered-error model.
- **Record each candidate's input assumption** (full mix / family stem / clean / mono / poly) — clean-Slakh F1 overstates production performance under real stem bleed.
- **Flag separation as a possible upstream ceiling** (per the Specialist Discovery handoff already sent): improving the separator may raise downstream playability more than swapping transcription nets.
- **No new GPU spend** driven by this framework; proxy work is $0 over existing caches.

---

## Final questions — explicit answers

1. **Is note F1 a useful playability proxy?** `[HYP, leaning KNOWN]` **Weakly, and only for note-aware Practice.** It's position-, distribution-, and correction-blind, and irrelevant to Jam/Perform (which don't use notes). Useful as a coarse discovery filter; poor as a usability gate.
2. **Most misleading existing metric?** `[HYP]` **Global note recall** — it hides phrase distribution (§9), rewards scattered mediocrity over clustered excellence, and conflates a correctable −12 bass with random octave chaos (§11). Runner-up: any octave-*tolerant* recall for **bass**, which can call an obviously-wrong-octave line "good."
3. **Most damaging Bass errors?** `[HYP]` **Sporadic octave inconsistency**, **wrong/late root on strong beats**, **groove-breaking onset jitter (>~100 ms)**. Uniform octave offset and missed ornaments are minor/correctable.
4. **Most damaging Guitar errors?** `[HYP]` *Lead*: wrong strong-beat pitch, broken contour, groove displacement. *Rhythm*: wrong chord root/quality and hallucinated non-chord tones inside held chords; exact voicing matters less.
5. **Most damaging Piano/Keys errors?** `[HYP]` **Chord-tone/root errors and hallucinated notes within simultaneities** (§14) — voicing correctness over raw recall. Isolated inner-voice omissions are tolerable.
6. **Phrase-level > global recall?** `[HYP, strong]` **Yes.** Musicians experience sections and bars, not song averages; the main-riff-wrong case proves the global number lies (§7, §9).
7. **Weight strong beats/downbeats differently?** `[HYP, strong]` **Yes** — downbeat/strong-beat errors and downbeat hallucinations are far more damaging; grid-derived, cheap to weight (§10). Exact weights are `[NV]`.
8. **Treat octave errors how?** `[KNOWN approach]` Score **pitch-class recall** and **octave-consistency** as separate axes; apply **systematic correction** before judging; for bass, prioritize consistency over absolute octave (§12).
9. **Systematic vs random?** `[KNOWN]` Fit the best global correction (transpose/offset) and report **residual**; systematic = low residual = low true severity + auto-fixable; random = high residual = high severity (§11).
10. **Smallest credible human test?** `[KNOWN]` Founder + a few musicians, ~10–15 fixed golden-ears clips, **blind pairwise forced-choice**, log winner + worst-error, ~20 min/session. Goal: label examples to validate proxies, not statistical power (§18).
11. **What can we derive for $0 now?** `[KNOWN, with caveat]` Over cached per-note predictions + GT + grid: pitch-class recall, systematic residual, phrase/section vector, strong-beat accuracy, false-notes/bar, onset percentiles, root/chord-tone coverage, **PRL** — no audio, no inference. Caveat: the historical validated100 run is aggregate-only; needs per-note re-served from cache first (§23).
12. **New metrics the Lab should eventually adopt?** `[HYP]` Systematic-shift residual, octave-consistency, phrase-recall vector, PRL, and per-candidate input-assumption tagging — **after** validation (§30).
13. **Evidence to say "good enough for JAM Practice" (per instrument)?** `[KNOWN method]` When, on the golden-ears set, musicians in blind forced-choice *consistently* answer "yes, I could learn this part from this" **and** that acceptance aligns with a validated proxy region (e.g. high phrase-recall + long PRL + corrected register) for that instrument×mode — thresholds learned from that transition, not declared (§27). Not a single F1 number.
14. **What NOT to build yet?** `[KNOWN]` No composite JAM score; no thresholds; no Lab changes; no new inference/GPU; no separation metrics before the experience needs them; no salience-weight constants before human labels; no same-family collision metric while source-instance GT is unavailable (§16).

---

*Design only. No code, no Lab change, no inference, no thresholds, no composite score. Grounded where possible; hypotheses and validation-needs tagged distinctly. Stop.*
