# Phrase-Motion — Implementation Roadmap

Execution plan, not architecture. Smallest independently-verifiable milestones, vertical
slices, shortest feedback loop. Goal: reach the FIRST TRUSTWORTHY PLANNER EVALUATION with
the least work. Each milestone ships something executable, produces measurable output, and
answers one "what new fact do we now know?" Solver stays frozen.

Effort is "focused days"; anything > 3d is split.

---

## Milestones

### M0 — Data format smoke (author 1 phrase + 1 reference, trivial loader)
- **Objective:** prove the benchmark data format is authorable and loadable.
- **Deliverables:** one `phrases/single.json` (contacts+times+fixed fingering) + one
  `references/single.json` (acceptance-region: behavioural constraints + ranges), hand-
  authored; a ~30-line loader.
- **Dependencies:** none (schema from the harness doc).
- **Outputs:** loader prints the parsed phrase + reference.
- **Done:** loader reads both files into typed objects; a unit assertion on field presence
  passes. Binary.
- **Fails if:** the schema can't express the phrase/reference cleanly (missing field,
  awkward encoding).
- **Effort:** 0.5d. **Risk:** low — but authoring the *reference* reveals whether
  acceptance-region labels are practical (the real fact). **Rollback:** delete files;
  schema is data-only.
- **Fact learned:** is the data format usable, and are acceptance-region labels authorable?

### M1 — Solver adapter → Trajectory (naive per-note, no plugin yet)
- **Objective:** produce a Trajectory end-to-end from the frozen solver on one phrase.
- **Deliverables:** `SolverAdapter` wrapping `mpfb_solver` (solve_pose, fk, version); a
  straight loop solving each note → a `Trajectory` (dense knots of mpfb_state).
- **Dependencies:** M0.
- **Outputs:** a `trajectory.json` (knots) for the phrase; per-note feasible flags.
- **Done:** trajectory has one feasible knot per note; FK on each knot reproduces solver
  contact (reuses the validated 0.000 mm invariant). Binary.
- **Fails if:** the adapter interface can't express what the solver needs, or FK/solver
  disagree.
- **Effort:** 1d. **Risk:** low (solver validated). **Rollback:** adapter is a thin wrapper;
  revert file.
- **Fact:** is the SolverAdapter interface sufficient; can we emit a Trajectory?

### M2 — First vertical slice: 1 metric → movement_report.json → PASS
- **Objective:** the illustrative end-to-end slice — phrase→solve→ONE metric→JSON→verdict.
- **Deliverables:** `evaluate.py` with ONE metric (contact-fidelity hard gate — cheapest,
  reuses render==solver); `report.py` writing `movement_report.json` with full repro
  metadata.
- **Dependencies:** M1.
- **Outputs:** `movement_report.json` (metadata + 1 phrase + hard-gate pass + score stub).
- **Done:** running one command on `single` yields a JSON with correct metadata (commits,
  config_hash, deps) and hard-gate = PASS; re-run is byte-identical. Binary.
- **Fails if:** JSON not reproducible, or metadata incomplete.
- **Effort:** 1d. **Risk:** low. **Rollback:** revert 2 files.
- **Fact:** the end-to-end pipeline works and is reproducible.

### M3 — Planner plugin interface + registry (naive planner becomes a plugin)
- **Objective:** decouple the planner; make it swappable.
- **Deliverables:** frozen `Planner.plan(phrase, refs, solver, config) -> Trajectory`; the
  M1 loop refactored into a registered `naive` plugin; a planner registry (dict).
- **Dependencies:** M2.
- **Outputs:** same report, now via `--planner naive`.
- **Done:** harness selects the planner by name; report records planner name+commit;
  identical output to M2. Binary.
- **Fails if:** the interface can't carry what a real planner will need (look-ahead over
  the whole phrase) — caught here, cheaply, before a real planner exists.
- **Effort:** 0.5d. **Risk:** MEDIUM — this interface must anticipate the trajectory
  planner's needs (whole-phrase input, references). Get it wrong and M10 forces a refactor.
- **Rollback:** revert; M2 loop still works.
- **Fact:** is the planner interface sufficient for BOTH naive and future trajectory planners?

### M4 — Core deterministic metrics + a few phrases
- **Objective:** the cheap, deterministic soft metrics behaving on real phrases.
- **Deliverables:** shift-count, fingertip-travel, root-travel, finger-reuse in `evaluate.py`
  (registered in the metric registry); author 4 more phrases + references (repeated-note,
  string-crossing, one-shift scale, sustained-anchor — the highest-signal categories).
- **Dependencies:** M3.
- **Outputs:** report with per-metric values for 5 phrases, naive planner.
- **Done:** each metric returns a sane value hand-checkable against the phrase (e.g.
  repeated-note reuse ≈ 1.0; one-shift scale shift-count = 1). Binary per metric.
- **Fails if:** a metric is nonsensical or non-deterministic.
- **Effort:** 2d (1d metrics + 1d authoring). **Risk:** MEDIUM — authoring references is
  slower/more subjective than it looks. **Rollback:** metric registry — remove a metric;
  drop a phrase.
- **Fact:** do the economy/reuse metrics behave sensibly; how costly is reference authoring?

### M5 — Scoring + failure taxonomy + baseline pin + regression diff
- **Objective:** one comparable Movement Score + regression comparison.
- **Deliverables:** tiered Movement Score (hard-gate multiplier × weighted soft), failure
  classifier (taxonomy tags), `report.py` diff vs a pinned baseline report.
- **Dependencies:** M4.
- **Outputs:** report with aggregate + tier scores + failures; a diff of two runs.
- **Done:** naive baseline pinned; a deliberately-worse run scores lower and the diff flags
  it; failures tag correctly on a hand-constructed bad trajectory. Binary.
- **Fails if:** score doesn't move monotonically with obvious quality changes.
- **Effort:** 1.5d. **Risk:** LOW-MED (weighting is data; review's coupling caveat noted —
  weights provisional). **Rollback:** revert scoring; metrics still print.
- **Fact:** does the scoring+diff produce a trustworthy comparable number?

### M6 — Timing metric (the review's missing first-order axis)
- **Objective:** score on-beat arrival + release — the highest-uncertainty NEW metric.
- **Deliverables:** a timing metric (fingertip at contact within a tolerance window of the
  note onset; release near note end) registered in the engine.
- **Dependencies:** M4 (needs trajectory timing).
- **Outputs:** timing sub-score per phrase.
- **Done:** on a hand-built early/late trajectory the metric penalizes correctly; on the
  naive (arrive-when-solved) it reports a defined value. Binary.
- **Fails if:** timing metric is unstable or its tolerance is arbitrary/ungrounded.
- **Effort:** 1.5d. **Risk:** HIGH — the review flagged "do timing metrics behave sensibly"
  as a genuine unknown; the naive planner has no real timing model, so this metric may have
  nothing meaningful to grade until the real planner exists. **Rollback:** deregister the
  metric (registry); rest of the suite unaffected.
- **Fact:** do timing metrics behave sensibly, and is timing even measurable pre-planner?

### M7 — First REAL planner (kinematic trajectory-opt MVP) + FIRST TRUSTWORTHY EVAL
- **Objective:** the payoff — a real planner scored against the naive baseline on the suite.
- **Deliverables:** a `trajopt` planner plugin (scipy direct-collocation MVP: effort +
  smoothness + per-knot feasibility over the whole phrase); run the benchmark; compare.
- **Dependencies:** M3 (interface), M5 (scoring/diff), M4 (metrics), M6 (timing, optional).
- **Outputs:** movement_report.json + regression diff: trajopt vs naive.
- **Done:** trajopt beats naive on ≥ the pre-registered V1 gates (shift economy, reuse,
  travel) with NO hard-gate regression, or clearly does not — either way, a trustworthy
  measured verdict. Binary against pre-registered thresholds.
- **Fails if:** solve doesn't converge at phrase scale, or beats naive on soft metrics while
  regressing contacts.
- **Effort:** 3d (split if the NLP fights: 3a kinematic MVP on 2-note; 3b full phrase; 3c
  behavioural terms). **Risk:** HIGH — convergence + solve-time at ~1900 vars (the design
  doc's estimate), and the interface (M3) being sufficient. **Rollback:** it's a plugin;
  naive remains the shipping baseline; delete the plugin.
- **Fact:** does whole-phrase trajectory optimization measurably beat memoryless solving?
  (The core empirical question of the whole track.)

### M8 (parallel track) — Rendering + minimal HTML (the visual gate)
- **Objective:** turn solved trajectories into the human believability artifact.
- **Deliverables:** wire the existing `mpfb_render` to render a trajectory's knots (contact
  sheet) for the demo phrase + fixtures; a MINIMAL self-contained HTML (summary + phrase
  table + embedded demo contact sheet).
- **Dependencies:** M2 (trajectory), can start after M2 in PARALLEL with M4-M7.
- **Outputs:** `report.html` + per-phrase contact sheets.
- **Done:** HTML opens standalone, shows the demo phrase render + the phrase-score table.
  Binary.
- **Fails if:** renders don't integrate or HTML isn't self-contained.
- **Effort:** 1.5d (reuses the validated renderer). **Risk:** LOW (renderer exists).
  **Rollback:** JSON report still stands without HTML.
- **Fact:** do renders integrate as artifacts; is the HTML worth having yet?

### V2 / deferred (NOT scheduled): CI wiring, trend DB, style conditioning, inter-rater
tooling, bootstrap CIs, fancy HTML, full 20+ phrase suite, right-hand suite. Each deferred
because none reduces the risk of the core question (does the planner work); all are cheap
to add later via the data/registry seams.

## Sequencing justification

- **Harness before data? No — together, tiny.** M0 authors one phrase WHILE building the
  loader, so the schema is proven by use immediately.
- **Naive planner before real planner: yes.** It's the baseline; without it "better" is
  meaningless. It's also the cheapest exercise of the plugin interface (M3) — de-risking the
  interface before the expensive real planner (M7).
- **Metrics/scoring before the real planner: yes.** Benchmark-first discipline — you cannot
  trust a planner result without the scoring it's measured by (M4-M6 before M7).
- **Timing (M6) before M7: soft-yes.** Author it before, but it may only become meaningful
  WITH the real planner — hence its high-uncertainty flag; do not block M7 on it.
- **Rendering/HTML (M8): PARALLEL, after M2, not on the critical path** to the first NUMERIC
  eval. It IS required before the final *believability* judgment, but the first trustworthy
  numeric comparison (hard + economy metrics) doesn't need pictures. Slot it alongside M4-M7.
- **CI/stats/style/trends: last.** They serve scale/automation, not the first fact.

## Critical path & dependency graph

```
M0 ─ M1 ─ M2 ─ M3 ─ M4 ─ M5 ─ (M6) ─ M7   ← CRITICAL PATH to first trustworthy eval
                     └───────────────┘
                          M8 (render/HTML) branches off M2, runs in PARALLEL
```
- **Blocks everything:** M0→M1→M2 (the pipeline) and M3 (interface) and M5 (scoring).
- **Parallelizable:** reference/phrase AUTHORING (M0/M4/M7 data) can proceed alongside
  harness code by a different effort stream; M8 render/HTML runs parallel after M2.
- **The one true gate on the whole track:** M7 — does whole-phrase optimization beat naive.

Critical-path effort ≈ 0.5+1+1+0.5+2+1.5+1.5+3 = **11 focused days** to first trustworthy
eval (M8 parallel adds ~1.5d wall-clock if not overlapped).

## Self-critique

- **Most-underestimated effort: reference/phrase AUTHORING.** Acceptance-region labels are
  subjective, need guitarist judgment, and the review demands multiple acceptable
  solutions — I've budgeted ~1d/batch; realistically each phrase's Reference B is fiddly.
  This is the quiet schedule risk, not the code.
- **Highest technical uncertainty: M7** (does trajopt converge at phrase scale and actually
  beat naive) and **M6** (does timing even mean anything before a planner with a timing
  model). M7 is THE empirical unknown the whole project exists to resolve; everything before
  it is de-risking scaffolding.
- **Interface risk at M3** — if the naive-planner interface doesn't anticipate the trajectory
  planner's whole-phrase/look-ahead needs, M7 forces a refactor. Mitigation: design M3's
  interface signature reading the WHOLE phrase + references even though naive ignores the
  look-ahead — cheap insurance.
- **30% cut (keep ~90% value):** drop **M8 (render/HTML)** from the first-eval path (defer
  to right after M7 — the numeric verdict is trustworthy without pictures, and the visual
  gate becomes the *next* step); fold **M6 timing** into a single lightweight metric rather
  than a full milestone (or defer until M7 exists to grade it); trim M4's authoring from 5
  phrases to **3**. That yields M0-M1-M2-M3-M4(3 phrases)-M5-M7 ≈ **7.5 days** to the first
  trustworthy numeric eval, with rendering + timing + suite-expansion as immediate
  follow-ups. Keeps the core empirical answer; drops only visualization and breadth.

## Recommendation

Execute the critical path M0→M7 in order; run authoring and M8 in parallel streams. Freeze
scope at M7 (first trustworthy eval) and STOP to read the result before deciding whether the
trajectory planner is worth deepening (behavioural terms, CasADi/Jacobians, full suite). No
code until you approve starting M0.
