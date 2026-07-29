# M4 findings — core deterministic metrics + 5 phrases

**1. What did we build?**
Four soft metrics in `evaluate.py` (registered): `shift_count`, `fingertip_travel`,
`root_travel` (T2 economy), `finger_reuse` (T3). Four new phrases + acceptance references:
`repeated_note`, `string_crossing`, `one_shift_scale`, `sustained_anchor`. Generalized the
naive planner to solve per-MOMENT (events sharing a timestamp) so multi-contact chords /
held anchors are expressible; `Planner.plan` interface unchanged. `report.py` runs the whole
suite into one report.

**2. What did we learn? (naive planner, all gates PASS)**
```
single           gate=PASS shift=0 reuse=1.0 tip=0mm     root=0mm     worst=0.001mm
repeated_note    gate=PASS shift=0 reuse=1.0 tip=14.3mm  root=0.01mm  worst=0.001mm
string_crossing  gate=PASS shift=0 reuse=0.0 tip=253.4mm root=0.52mm  worst=0.012mm
one_shift_scale  gate=PASS shift=2 reuse=0.0 tip=889.3mm root=91.6mm  worst=3.734mm
sustained_anchor gate=PASS shift=0 reuse=0.5 tip=52.1mm  root=0.29mm  worst=0.01mm
```
Every metric is hand-checkable and behaves:
- `finger_reuse`: repeated_note=1.0 (one finger), string_crossing=0.0 (four distinct),
  sustained_anchor=0.5 (index held, upper voice changes). Exactly as designed.
- `shift_count` deltas expose real behaviour, not just a scalar (see below).
- economy metrics separate cheap (repeated 14mm) from expensive (one_shift 889mm) phrases.

**3. Assumptions confirmed.**
- Soft metrics compute deterministically from `fk(state)` — same space the renderer draws.
- Moment-grouping makes chords/anchors first-class with no interface change.
- Metrics are pure functions of deterministic solver states ⇒ deterministic (M2 proved
  byte-identity; nothing here adds randomness).

**4. Assumption DISPROVED / honest deviation from pre-registration.**
The frozen roadmap pre-registered `one_shift_scale` shift_count = **1**. Naive scored **2**.
This is NOT a metric bug — the deltas are `[0.02, 23.26, 30.81] mm` (threshold 20):
- f2→f4 (0.02 mm): naive HELD position I via warm-start — correct, no shift counted.
- f4→f9 (23 mm): the one intended shift.
- f9→f11 (31 mm): naive BROKE position — it recentred the whole hand on fret 11 instead of
  reaching with the pinky from an anchored position VIII. A disciplined player does not move
  here.
So the "extra" shift is naive's real positional-commitment failure, faithfully measured. The
pre-registered "=1" was the IDEAL (human) value; the naive baseline legitimately scores
worse. **I did not tune the 20 mm threshold to force the pre-registered number** — that would
be hacking the metric to a value that assumed a better planner. Same signal in
`string_crossing`: tip travel 253 mm vs an ideal ~90 mm because naive re-solves every finger
each moment instead of holding the hand.

This is the benchmark working as intended: on its first real run it already quantifies where
naive is wasteful (excess shifts, excess travel) — the exact gap the phrase planner (M7) must
close. That gap is the first evidence toward the overarching question.

**How costly was reference authoring?** (the M4 fact-to-learn) Moderate and subjective, as
flagged. The mechanical Reference A (economy bounds) is quick and defensible. Reference B's
behavioural bands (`economy_band_*`, `finger_reuse_band`, `anchor_holds`) require judgment —
I set them from the pre-registered ideal, and one turned out looser/tighter than naive
reality (shift, string-cross travel). That is fine (references are the target, not the
baseline) but confirms the roadmap's MEDIUM risk: reference numbers need a second labeller
before they gate anything (deferred to V2, noted in every file's `labeller`).

**5. Change before M5?**
No code change. One thing to CARRY into M5, not fix now: `shift_count`'s 20 mm threshold and
the economy bands are provisional; M5's scoring must treat naive's over-shift/over-travel as
LOW SCORE (not a gate failure), and the metric-coupling/threshold review flagged earlier
should be revisited with a correlation study, not by hand-tuning. Proceed to M5 (scoring +
failure taxonomy + baseline pin + regression diff).
