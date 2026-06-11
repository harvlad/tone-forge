# Jam Product Roadmap — Post-MVP

**Lens.** Guitarist-product. Not signal-centric.

Companion to `SONG_UNDERSTANDING_INVESTIGATION.md` (contract audit)
and `SONG_UNDERSTANDING_CAPABILITY_MAP.md` (signal-to-feature map).
This doc explicitly **disagrees** with the capability map in several
places — flagged in §6. Where it disagrees, it's because the
question is different: not "what can we build with the signals we
have" but "what makes a guitarist feel like they joined the band
faster."

No code changes accompany this commit.

## 1. North-star and operating assumptions

**The question.** *Does this help the user feel like they joined the
band faster?*

Concretely: a guitarist who plugs in, pastes a YouTube URL, and
within minutes is playing along — sounding right, knowing what to
play, knowing where they are in the song, and knowing what to
practice next.

**Stipulated:**

- Connect is the product delivery layer.
- Jam is the primary product surface.
- Song Understanding is the long-term moat.
- Studio is maintenance-only.
- Retrieval is structurally sound; tone matching is shipped.
- Guitar is the sole focus.

**Implication.** Every feature below is evaluated against whether
it serves a guitarist learning a specific song at home. Vocals,
karaoke, DJ tools, "music discovery for general listeners" — all
ignored. If a feature serves the guitarist on song N, it's in
scope. If it serves a meta-customer (label, teacher, content
creator), it's out.

**Second implication.** "Sounds smart" is not a product axis.
Several features in the capability map sound impressive in a
signal report but disappear in front of a real user — flagged in
§3 (engineering traps).

## 2. Per-feature evaluation

### Notation

- **User value (1–10).** The guitarist's perceived benefit on
  song N. Not abstract sophistication — does it move a needle the
  user can name? "I tuned my guitar in one click" is a 9. "The
  song has a self-similar bridge" is a 3.
- **Engineering effort (1–10).** Wall-clock complexity. Higher
  is worse. Calibration data costs count.
- **Strategic differentiation.** Four competitors. For each:
  - ✗ — they don't have it
  - ◐ — they have a partial / curated version
  - ✓ — they have it (parity)
  - **★** — they have it and do it well (we'd be playing catch-up)
- **Category.** Core Jam (must-ship to feel complete) / Song
  Understanding Moat (compounding intelligence) / Practice Layer
  (helps the user improve) / Nice-to-have (interesting, optional).
- **Phase.** P1 (next ship after MVP) / P2 (12-month horizon) /
  P3 (compounding moat) / Future (out of current planning window).

---

### 2.1 Riff extraction

| | |
|---|---|
| **User value** | 10 — the canonical guitarist ask: "what's the riff?" |
| **Effort** | 6 — sub-sequence matching on guitar-stem MIDI; inputs exist |
| **Moises / Yousician / UG / Rocksmith** | ✗ / ◐ (tabbed only) / ◐ (tabbed only) / ◐ (their catalog only) |
| **Category** | Core Jam + Song Understanding Moat |
| **Phase** | **P1** |

This is the product hook. Nobody else extracts riffs from arbitrary
audio. The capability map called this "highest-leverage"; the
product lens calls it **the headline feature of the post-MVP
release.** Without it, Jam is a competent "play along with a
song" tool. With it, Jam is "the tool that finds the riff for
me." That's a positioning sentence a guitarist repeats to a
friend.

### 2.2 Difficulty heatmaps

| | |
|---|---|
| **User value** | 7 — concrete "start here, the chorus is harder" |
| **Effort** | 4 — composite rubric over signals we have |
| **M / Y / UG / R** | ✗ / ◐ (per-lesson, not per-section) / ✗ / ★ (dynamic difficulty) |
| **Category** | Practice Layer |
| **Phase** | **P2** |

Per-section is the right unit; per-song is too coarse to be
actionable. Yousician and Rocksmith dose difficulty by
*simplifying* the score the user plays; we'd be doing it by
*annotating* the source. Different mechanism. The output the
user sees: a coloured timeline strip under the song that says
"start with verse 1, save the bridge for last."

### 2.3 Practice guidance (orchestration layer)

| | |
|---|---|
| **User value** | 9 — when it works |
| **Effort** | 9 — orchestrates 4–6 component features |
| **M / Y / UG / R** | ◐ / ★ / ✗ / ★ |
| **Category** | Song Understanding Moat |
| **Phase** | **P3** |

The capability map calls this the strategic anchor. Agreed — *as
the destination*. As a *near-term feature*, it's a trap (see §3.1).
Practice guidance is the *composition* of riff extraction,
difficulty heatmap, chord-change drills, and tempo curriculum.
Built before its components, it's vapor. Built after, it's the
moat.

### 2.4 Stem-mix recipes

| | |
|---|---|
| **User value** | 9 — instant, table-stakes |
| **Effort** | 2 — UI on existing stems |
| **M / Y / UG / R** | **★** (their core product) / ◐ / ✗ / ✗ |
| **Category** | Core Jam |
| **Phase** | **P1 (urgent)** |

The capability map listed this as an "adjacent opportunity."
Wrong category. This is **table-stakes parity with Moises.** Its
absence is a churn risk: a guitarist who can't say "let me play
along with just drums and bass" will go to Moises and not come
back. The good news: stems already exist. This is 2 days of UI
and a preset list. *This should have shipped before the
investigation work.*

### 2.5 Tone-to-rig translation

| | |
|---|---|
| **User value** | 8 — bridge from "Jam picks a tone" to "I can replicate it on my Marshall" |
| **Effort** | 3 — render existing monitor chain spec as text |
| **M / Y / UG / R** | ✗ / ✗ / ◐ (curated tone tabs) / ◐ (curated tone notes) |
| **Category** | Core Jam |
| **Phase** | **P1** |

The most underrated feature in the inventory. Tone matching is
shipped — it returns a `MonitorChain` with concrete parameter
values. Surfacing those as "Tube Screamer @ 11 o'clock, JCM800
channel 2, gain 7, reverb mix 25%" makes the tone *portable* to
the user's physical rig. Most guitarists own pedals and amps.
Most don't own a $500 modeler. Telling them "this is what to set
on the gear you already have" is a feature competitors can't
easily copy — their tone systems are curated tabs or learned
profiles, not portable rig recipes.

### 2.6 Chord-change drills

| | |
|---|---|
| **User value** | 6 — useful, niche |
| **Effort** | 3 — chord lane already there |
| **M / Y / UG / R** | ✗ / **★** / ✗ / ★ |
| **Category** | Practice Layer |
| **Phase** | **P2** |

Yousician and Rocksmith both do this well. We're at parity
risk, not differentiation. Bundling it into the difficulty-
driven practice loop ("the song flagged this change as hard;
here's a drill") is more interesting than shipping it standalone.

### 2.7 Section similarity

| | |
|---|---|
| **User value** | 3 — informational, not actionable |
| **Effort** | 2 — pairwise distance on existing per-section features |
| **M / Y / UG / R** | ✗ / ✗ / ✗ / ✗ |
| **Category** | Nice-to-have |
| **Phase** | **Future / kill** |

A guitarist learning a song does not care that chorus 1 ≈ chorus 2.
They already heard the chorus repeat. What's actionable: knowing
which chorus is *different* (key change, extra layer, harder
ending). That's an input to difficulty heatmap §2.2, not a
standalone feature. **Kill as a user-facing capability; keep as a
sub-routine.**

### 2.8 Motif detection

| | |
|---|---|
| **User value** | 3 (standalone), 8 (as substrate for riff extraction) |
| **Effort** | 3 — `librosa.segment.recurrence_matrix` wrapper |
| **M / Y / UG / R** | ✗ / ✗ / ✗ / ✗ |
| **Category** | Subsumed by Riff Extraction |
| **Phase** | **Future / merge into 2.1** |

The capability map treated motif as a peer of riff extraction.
Product-wise, motif detection is what you'd ship if you didn't
have stem MIDI. We *do* have stem MIDI, so motif detection is
the inferior version of riff extraction. **Merge as an
implementation detail of 2.1; do not ship as a separate user
surface.**

### 2.9 Tuning detection

| | |
|---|---|
| **User value** | 9 — first 30 seconds matter |
| **Effort** | 5 — conservative 3-state version |
| **M / Y / UG / R** | ◐ (basic) / ✓ / ✗ (tab declares) / ✗ (tab declares) |
| **Category** | Core Jam |
| **Phase** | **P1** |

The capability map ranked this Medium-High value. Product lens
says: this is a **30-second UX-impact feature**. A guitarist
trying a Drop-D song without knowing it spends five minutes
confused about why their chords sound wrong. A clear "tune to
Drop D" prompt in the first frame of Jam saves that frustration
and is the kind of moment users repeat in word-of-mouth. Ship
conservative (Standard / Half-step-down / Drop-D / Unknown);
expand later if labels arrive.

### 2.10 Capo detection

| | |
|---|---|
| **User value** | 3 — niche, declared faster by user |
| **Effort** | 8 — cascade on tuning + fret-position estimation we don't have |
| **M / Y / UG / R** | ✗ / ✗ (tab declares) / ✗ (tab declares) / ✗ (tab declares) |
| **Category** | Kill |
| **Phase** | **Kill — ship UX dropdown** |

The capability map said "defer indefinitely." Product lens says
**kill the algorithmic version outright.** A one-question
dropdown ("are you using a capo? at fret ___") solves this in 30
seconds with 100% accuracy. Engineering effort to algorithmically
match what the user can declare in one tap is pure waste. The
"defer" framing leaves the door open; close it.

### 2.11 Tempo curriculum

| | |
|---|---|
| **User value** | 7 — expected feature |
| **Effort** | 1 — UI presets on existing tempo control |
| **M / Y / UG / R** | ◐ (slider, not curriculum) / **★** (per-lesson) / ✗ / ★ |
| **Category** | Core Jam |
| **Phase** | **P1** |

Not differentiating, but expected. Three preset buttons
(70% / 85% / 100%) is a half-day's UI work. Ship for parity.

### 2.12 Note-density heatmap

| | |
|---|---|
| **User value** | 3 — pretty, not actionable |
| **Effort** | 2 |
| **M / Y / UG / R** | ✗ / ✗ / ✗ / ✗ |
| **Category** | Nice-to-have |
| **Phase** | **Future / kill** |

What does a guitarist do with "this section has more notes"?
Either they can play it or they can't; the density number doesn't
change their action. The difficulty heatmap (§2.2) is the
*useful* version of this. **Kill as a separate feature.**

### 2.13 Vibrato / bend mimicry drills

| | |
|---|---|
| **User value** | 7 (intermediate+), 2 (beginner) |
| **Effort** | 5 — pitch_stability already detects, but drill UX is real work |
| **M / Y / UG / R** | ✗ / ◐ (partial) / ✗ / ★ (technique challenges) |
| **Category** | Practice Layer |
| **Phase** | **P2 (for ambitious users) or P3** |

This is the feature that *retains* an advanced player six months
in. Beginners won't notice; intermediates will love it. The
signal is unusually clean (`pitch_stability.vibrato_rate_hz` and
`vibrato_depth_cents` per note). Differentiated.

### 2.14 Cross-song progression library

| | |
|---|---|
| **User value** | 6 — musical-education value |
| **Effort** | 5 — hash design + catalog scale |
| **M / Y / UG / R** | ✗ / ◐ / ◐ (search by chord) / ✗ |
| **Category** | Song Understanding Moat |
| **Phase** | **P3** |

Powerful at catalog scale, weak before that. Hooktheory does this
well in an adjacent space; UG does a coarse search-by-chord
version. Requires either a large analyzed catalog (we don't have
it yet) or willingness to chew through a fixed reference set.
Defer until P3 when there's enough analyzed library to make it
useful.

### 2.15 Reverb / delay matching

| | |
|---|---|
| **User value** | 6 — extension of tone-to-rig |
| **Effort** | 3 — `ProductionStyle` already exists |
| **M / Y / UG / R** | ✗ / ✗ / ◐ (tone tab) / ◐ |
| **Category** | Core Jam (as part of tone-to-rig) |
| **Phase** | **P2 (extends 2.5)** |

Should not be a separate feature. Pull `ProductionStyle.reverb`
and `.delay` into the tone-to-rig translation card. "Tube
Screamer + JCM800 + Spring Reverb @ 25%, Eighth-note delay @
35% feedback" — a complete monitor-rig recipe.

### 2.16 Right-hand technique tagging

| | |
|---|---|
| **User value** | 4 — guitarist who can hear knows already |
| **Effort** | 7 — calibration labels expensive |
| **M / Y / UG / R** | ✗ / ◐ / ◐ (tab notation) / ★ (tagged) |
| **Category** | Nice-to-have |
| **Phase** | **P3 or Future** |

Sounds smart in a feature list, weak in practice. A guitarist
hearing palm-muting already labels it; a guitarist who can't
hear it isn't going to benefit from a tag without the underlying
technique training. Defer until labels exist for a reason
(e.g., as input to a Rocksmith-style technique challenge), not
because tagging is intrinsically valuable.

## 3. The five meta-questions

### 3.1 Which 5 features most improve Jam in the next year?

Ranked by likely impact on a new user's first session:

1. **Stem-mix recipes (§2.4)** — table-stakes parity. Absence is a churn risk.
2. **Riff extraction (§2.1)** — the headline feature; the "wow."
3. **Tuning detection (§2.9)** — fixes the 30-second confusion moment.
4. **Tone-to-rig translation (§2.5)** — bridges Jam's tone matching to physical gear.
5. **Tempo curriculum (§2.11)** — expected, half a day, ship it.

All five are P1. All five are low-to-medium engineering effort.
Three are parity, two are differentiated. The combination ships
Jam at "feels complete, plus has one thing nobody else does."

### 3.2 Which 5 features create the strongest long-term moat?

Ranked by how hard a competitor would have to work to match it:

1. **Riff extraction (§2.1)** — uniquely composable from MIDI + sections.
2. **Practice guidance composition (§2.3)** — the orchestration is the moat. Built only after components.
3. **Tone-to-rig translation (§2.5)** — leverages tone matching in a way curated competitors can't replicate at scale.
4. **Difficulty heatmaps (§2.2)** — per-section rubric on full audio + MIDI is a richer signal than any competitor uses.
5. **Vibrato / bend drills (§2.13)** — `pitch_stability` data is rare; surgical drills are rarer.

The moat is **specialist depth**, not feature breadth. Five
deep, defensible features beat fifteen shallow ones.

### 3.3 Engineering traps that sound smarter than they are

Brutally:

1. **Practice guidance as a standalone P1/P2 feature.** It's an
   integration of components that don't exist yet. Built early,
   it's a wireframe with placeholder data. Build the components
   first; let practice guidance emerge as the natural composition.
   The capability map's call to make this the "strategic anchor"
   is correct as the destination but **dangerous as near-term
   prioritization.**

2. **Capo detection.** Cascades on tuning + on fret-position
   estimation we cannot do. UX dropdown is strictly better. Kill,
   don't defer.

3. **Right-hand technique tagging.** Sounds impressive; needs
   expensive labels; benefits a user segment (beginners learning
   palm-muting) that doesn't read tags. The advanced user already
   knows.

4. **Section similarity** *as a user surface*. Meta-information
   about song structure. Guitarists don't act on it. Useful as a
   sub-routine for difficulty heatmap; not a feature.

5. **Motif detection** *as a peer of riff extraction*. It's the
   inferior version because we already have stem MIDI. Ship
   riffs; don't ship motifs as a separate product surface.

6. **Cross-song progression library** *before catalog scale*.
   At 50 songs it's a curiosity; at 5,000 it's powerful. Don't
   build the index before there's a corpus.

7. **Note-density heatmap.** Subsumed by difficulty heatmap.
   Kill the parallel feature.

### 3.4 Which features would a guitarist notice in 30 seconds?

The "first-frame" features. Each is something the user
*observes* during their first interaction without being told to
look for it:

1. **Tuning detection** — a clear "tune to Drop D" prompt
   appears before play. *They notice they didn't have to
   diagnose it themselves.*
2. **Stem-mix recipes** — preset chips ("Drums only," "No
   guitar") in the transport bar. *They notice they have one-tap
   isolation.*
3. **Tone-to-rig translation** — a card under the tone match
   that says "Set your Tube Screamer to 11 o'clock." *They
   notice it told them what to do, not just what it did.*
4. **Tempo curriculum** — three buttons (70/85/100). *They
   notice the expected feature is there.*
5. **Riff extraction** — a "loop the riff" button on the song
   timeline. *They notice the product found the part they were
   going to look for anyway.*

Note the overlap with §3.1. The first-30-seconds-features and the
high-near-term-impact features are nearly the same set — because
guitarists *judge the product on the first session*. The shape of
that session is the product strategy.

### 3.5 Which features increase willingness-to-pay?

Different question from "improve UX." WTP is driven by
replacement value — what does the user have to *otherwise pay
for*?

1. **Riff extraction (§2.1)** — replaces $20/song on Songsterr
   Plus / paid tabs, multiplied by every song. The user's
   alternative is buying tabs or transcribing themselves. Highest
   replacement value per song.
2. **Tone-to-rig translation (§2.5)** — replaces $300–$2000 in
   pedals/modelers the user would otherwise buy chasing a tone.
   Even a partial recipe defers gear purchase.
3. **Practice guidance (§2.3)** — replaces $60/hour in lessons.
   At full composition, the strongest single WTP driver. Without
   composition, vapor.
4. **Difficulty heatmaps (§2.2)** — replaces the *time* the user
   would spend bouncing around the song figuring out where to
   start. Time is the constraint, not money, for hobbyist
   guitarists.
5. **Vibrato/bend drills (§2.13)** — replaces technique-coach
   feedback at the intermediate-to-advanced level. Specific WTP
   from a smaller cohort; useful for retention pricing.

Stem-mix recipes and tuning detection do not appear here. They
drive *adoption* (without them users leave); they don't drive WTP
(users wouldn't pay extra for them).

## 4. Opinions and contrarian takes

These are the calls where the product lens diverges from the
signal-centric capability map. Each is opinionated; each can be
argued against — but the arguments must be stated.

### 4.1 Stem-mix recipes belong in P1, not "adjacent"

The capability map listed stem-mix recipes as "Adjacent
Opportunity #2." That's a signal-centric framing — *of course*
they're adjacent, the analysis is done. The product framing:
they're **the absence-as-churn-risk feature** because Moises
already does them. Ship them in P1, before the headline features,
to neutralize the obvious comparison.

### 4.2 Tuning detection is P1, not P8.5

Capability map sequenced tuning at P8.5 — last in the
recommended sequence because labels are needed. Product lens
says: the conservative 3-state version (Standard / Half-step /
Drop-D / Unknown) requires no labels. It's a heuristic, not a
model. Ship it now; expand later.

### 4.3 Kill capo detection. Don't defer.

The capability map says "defer indefinitely." The product call is
stronger: **delete it from the roadmap.** Algorithmic capo
detection is engineering effort spent solving a problem a UX
dropdown solves in 30 seconds with 100% accuracy. "Defer" leaves
the option open; "kill" frees the engineering attention.

### 4.4 Practice guidance is the destination, not the next ship

The capability map ranked practice guidance the highest-leverage
feature. Product lens agrees — **as the destination**. As a
near-term build, it's an orchestration of components that don't
exist. Building it early is wireframe-with-mock-data territory.
Build riffs, difficulty heatmap, drills, tone-to-rig first; let
practice guidance be the natural composition. Sequencing it
first inverts the dependency.

### 4.5 Tone-to-rig is undersold

Capability map composite score: 9 ("Adjacent #1"). Product lens
score: this is the single most defensible Phase 1 feature
because tone matching is already shipped and competitors don't
have the underlying tone retrieval to translate from. Rendering
the monitor chain as a portable rig recipe is days of work and
weeks of competitive lead time.

### 4.6 Riff extraction is the headline, not "one of seven"

The capability map listed riff extraction as one of seven target
features. Product lens: it's *the* feature. It's what someone
tells their friend about Jam. It's the demo that fits in a
GIF. Every other feature on the P1 list is either parity or
infrastructure; riff extraction is the moment.

### 4.7 The roadmap should kill features, not just sequence them

The capability map deferred almost nothing. The product lens
**explicitly kills**:

- Capo detection (UX dropdown wins).
- Section similarity (as a user surface; keep as sub-routine).
- Note-density heatmap (subsumed by difficulty).
- Motif detection (as a separate feature; subsumed by riffs).

Carrying these on the roadmap is engineering attention drift.
Cut them; the roadmap gets sharper.

### 4.8 Five deep features beat fifteen shallow ones

The strategic logic: a guitarist deciding between Moises +
Yousician + Ultimate Guitar + Rocksmith + ToneForge does not
benchmark feature counts. They benchmark *the experience of
learning one specific song.* That experience is dominated by 4–6
moments. Optimize those moments. Don't carpet-bomb the roadmap.

## 5. Revised Jam-centric roadmap

### Phase 1 — "Feels complete, plus one wow"

**Goal.** A guitarist trying Jam for the first time does not
notice anything missing compared to Moises, and within the first
session has one "this is the tool I'll use" moment. Five
features. Roughly half a quarter of focused engineering.

| Order | Feature | Why first |
|---|---|---|
| 1 | **Stem-mix recipes** | Table-stakes; absence is churn risk |
| 2 | **Tempo curriculum** | Half-day; expected |
| 3 | **Tuning detection (conservative)** | 30-second UX moment; heuristic, no labels |
| 4 | **Tone-to-rig translation** | Underrated; leverages existing tone retrieval |
| 5 | **Riff extraction (MVP)** | The headline; the demo moment |

**P1 explicitly does not include.** Difficulty heatmap, practice
guidance, chord drills, vibrato drills, motif detection, capo
detection, section similarity, anything cross-catalog.

**P1 success condition.** Riff extraction works on ≥80% of a
sample 50-song test set, with timestamps within ±0.5s of human
annotation. Tone-to-rig text is human-validated on the five
shipped monitor chains. Stem-mix preset chips have one-tap
parity with Moises's stem isolation.

### Phase 2 — "Get better at the song"

**Goal.** Convert weekend players into daily players by giving
them concrete things to practice. Four features, building on P1.

| Order | Feature | Depends on |
|---|---|---|
| 1 | **Difficulty heatmap (per-section)** | P1 riff extraction (for section-internal scoring) |
| 2 | **Chord-change drills** | Existing chord lane |
| 3 | **Reverb/delay extension to tone-to-rig** | P1 tone-to-rig card |
| 4 | **Vibrato / bend drills** | `pitch_stability` (existing) |

**P2 success condition.** A user can identify, on a given song,
*the* hardest 30-second segment to focus practice on, and *the*
hardest two-chord change to drill. The vibrato drill works for
the canonical "Comfortably Numb" reference recording.

### Phase 3 — "The moat composes"

**Goal.** The components from P1 + P2 orchestrate into the
practice-guidance product. The cross-song moat opens.

| Order | Feature | Why now |
|---|---|---|
| 1 | **Practice guidance composition** | All components exist by end of P2 |
| 2 | **Cross-song progression library** | Requires catalog scale that accrues over P1+P2 |
| 3 | **Right-hand technique tagging** | Only worth labels once practice-guidance loop consumes them |

**P3 success condition.** The user gets a personalized
"practice card" on every song: tune, start-here pointer, riff
loop, hard-change drill, tone-to-rig recipe — composed
automatically without manual setup.

### Future / explicit kill list

**Killed (not just deferred):**

- Capo detection (UX dropdown wins).
- Section similarity *as user surface* (keep as internal helper
  for difficulty heatmap).
- Note-density heatmap (subsumed by difficulty heatmap).
- Motif detection *as separate feature* (subsumed by riff
  extraction).

**Deferred to Future (specific blocker stated, not just
"someday"):**

- Tablature transcription — needs fret-position estimation we
  don't have. Not viable on processed audio.
- Multi-instrument practice (bass, keys) — outside guitarist
  focus.
- Multi-user collaboration / leaderboards — outside guitarist
  focus.
- Mobile clients — desktop-first per Connect strategy.

## 6. What this changes relative to the capability map

Direct diffs against `SONG_UNDERSTANDING_CAPABILITY_MAP.md`
sequencing.

| Item | Capability map | Product roadmap | Why |
|---|---|---|---|
| Stem-mix recipes | "Adjacent #2" | **P1, second to ship** | Table-stakes parity; absence churns users |
| Tuning detection | P8.5 (last) | **P1, third to ship** | Conservative version is heuristic; 30-second UX moment |
| Tone-to-rig | "Adjacent #1" | **P1, fourth to ship** | Most underrated leverage of existing tone retrieval |
| Riff extraction | P8.2 | **P1, fifth — the headline** | Same sequencing, sharpened framing |
| Capo detection | "Defer indefinitely" | **Killed** | UX dropdown solves it; carrying it drifts attention |
| Practice guidance | P8.6 (last) | **P3** (after components) | Agree on destination, disagree on sequencing |
| Section similarity | P8.1 | **Internal helper only, not user-facing** | Not actionable for guitarists |
| Motif detection | P8.4 | **Subsume into riff extraction** | Inferior because stem MIDI exists |
| Difficulty heatmap | P8.3 | **P2** | Agree on importance, sequence after P1 wow |

The capability map is correct on the destination and on the
signal feasibility. The product roadmap is opinionated on what
to **kill, merge, and resequence** so the user-facing arc is
sharper.

## 7. Open product questions

These need product input before P1 starts. Recording them so
implementation doesn't begin in ambiguity.

1. **Tone-to-rig text format.** Free-text instruction card, or
   structured pedal-by-pedal table? Affects which Phase 1 UX
   bucket it lands in.
2. **Riff extraction UX.** Auto-loop on the highest-scoring
   riff? Click-to-loop with a "best riff" badge? Surface multiple
   riffs per song?
3. **Stem-mix recipe preset list.** Fixed three presets
   (drums-only, drums+bass, no-guitar), or per-stem toggles, or
   both?
4. **Tuning detection failure mode.** If the detector returns
   `UNKNOWN`, silently omit, or prompt the user to declare?
5. **Difficulty heatmap calibration corpus.** Who labels the
   30-song calibration set? Founder, contractor, community?
6. **Vibrato drill cohort.** Default-on for everyone, or opt-in
   from "advanced" user setting?

## 8. Closing call

If ToneForge ships Phase 1 as defined here, Jam becomes:

> The tool that tunes your guitar, builds you a stem mix, tells
> you what to set on your amp, and finds the riff — all from a
> YouTube URL, in under 90 seconds.

That positioning sentence is what should drive every Phase 1
decision. If a feature doesn't move that sentence, it doesn't
ship in Phase 1.

If ToneForge ships Phase 2, Jam becomes the tool you *practice
with*. If it ships Phase 3, Jam becomes the tool you *cannot
replace*. But none of that matters if Phase 1 doesn't make a
guitarist say "this is mine now" in their first session.

---

This document is product strategy, not engineering design. Each
P1 / P2 / P3 feature requires its own implementation pass
(contracts, producers, consumers, tests). The roadmap is a
sequencing artifact, not a commitment to specific code shapes.
