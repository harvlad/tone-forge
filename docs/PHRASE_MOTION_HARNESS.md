# Benchmark Harness V1 — engineering architecture

Infrastructure design only. No code, no planner, no benchmark data, no metric/scoring
redesign. Goal: the moment a planner exists, we can evaluate it and get a report. Prefer
the simplest architecture that still scales to the long-term vision.

The design carries forward the accepted benchmark v2 + the review's four fixes (acceptance-
region references, de-biased/orthogonal metrics, statistical fields, a timing axis) as
DATA and CONFIG, so the harness need not change when those land.

---

## 1. Guiding principle

The harness computes numbers and renders reports. It contains NO planner logic and NO
biomechanics — it orchestrates a planner plugin and the frozen solver, then measures. Two
load-bearing decisions make it future-proof: (a) stable plugin interfaces, (b) versioned
data + one report schema. Everything else is plain functions and can stay simple.

## 2. Scope (V1, exactly)

load phrases → load references → run a planner plugin → validate/measure each knot via the
solver's FK → compute metrics → classify failures → score → render → write JSON + HTML →
compare to a baseline. Nothing more.

## 3. Data model (the contracts that must be stable)

**Trajectory** (planner output, the key interface object): ordered knots
`[{t, mpfb_state[30], meta}]` plus the phrase's contact schedule it was solved for. Dense
enough that metrics resample it (guards the "Jerk-Hider" from the review — metrics
resample, not sample at solver knots).

**Pose/state**: the existing 30-DoF MPFB state (root xform + finger/thumb/metacarpal
eulers). The solver adapter turns a state into world joints via the validated FK, so
solver-space == render-space stays true by construction.

## 4. Modules — proposed, then CHALLENGED and collapsed

The brief lists 11 modules. For V1 that is over-engineered. Collapse to **five** files;
keep the 11 as *conceptual roles*, not separate subsystems:

| V1 module | absorbs (conceptual roles) | why merge |
|---|---|---|
| `loader.py` | Benchmark Loader + Reference Loader | both just read versioned JSON; one loader, two schemas |
| `adapters.py` | Planner Adapter + Solver Adapter | two thin interfaces; belong together |
| `evaluate.py` | Metric Engine + Failure Classifier + Scoring Engine | metrics→failures→score is one pass over a trajectory; splitting adds plumbing, not value. Metrics live in a registry (below) so this file stays small |
| `report.py` | Report Generator + Regression Comparator + Artifact Writer | JSON + HTML + diff-vs-baseline are one output step |
| (reuse) `mpfb_render.py` | Renderer | ALREADY EXISTS and is validated; the harness calls it, never reimplements |

So V1 = 4 new small files + reuse of the existing render/solver code. The 11-module split
is the V2/V3 shape if any of these grow; do not build it now.

## 5. Plugin interfaces (the only tight contracts)

Keep these stable forever; everything else can churn.

```
# Planner plugin — swap planners without touching the harness.
Planner.plan(phrase, references, solver, config) -> Trajectory
    phrase:     contacts + times + FIXED fingering (V1 invariant)
    references: {A: mechanical, B: acceptance-region/behavioural}
    solver:     the SolverAdapter (planner may call it per-knot)
    returns:    Trajectory (dense knots of mpfb_state)

# Solver adapter — abstracts the frozen MPFB solver + FK.
Solver.solve_pose(contacts, prev_state, config) -> (state, feasible, per_contact_mm)
Solver.fk(state) -> {finger: joints_mm, thumb, root}      # validated FK
Solver.version() -> commit/hash
```

The NAIVE BASELINE planner (per-note independent solve) is itself a Planner plugin —
implemented later as the first plugin, pinned as the regression baseline. The harness knows
nothing about how a planner decides; it only receives a Trajectory.

Registries (dict name→callable) for planners AND metrics, so adding a planner or a new
metric is a registration, not a harness edit — satisfies "multiple planners / new metrics
without architectural change."

## 6. Benchmark data format (versioned, reproducible for years)

```
benchmark/
  suite.json            # benchmark_version, phrase list, metric set + weights,
                        #   instrument defaults, style axis (future)
  phrases/<id>.json     # events: [{t, string, fret, finger, dur, articulation}],
                        #   instrument (n_strings, scale_len, spacing), tempo
  references/<id>.json  # A: mechanical spec; B: acceptance region = behavioural
                        #   constraints + acceptable ranges (review fix #1); style tag
  styles/               # (future) style-conditioned reference variants; empty in V1
  metadata/             # labeller ids, inter-rater notes (review fix #3), provenance
  results/<run_id>/     # movement_report.json, report.html, renders/, trajectories/
```

Everything the metrics need is DATA (contacts, fixed fingering, references, weights,
instrument params). Instrument params live IN the phrase (n_strings, spacing) so bass/uke/
7-string need no code change (review §6). A `phrase` never keys on pitch — always
(string,fret) — so tuning/capo are geometry, not schema. Contact `articulation` +
optional time-varying target field is reserved now so slides/bends don't break the format
later (review + v2 §11).

## 7. movement_report.json (the machine record)

```
{
  benchmark_version, planner_version(commit), solver_version(commit),
  metric_version, config_hash, seed, timestamp, runtime_s, machine{os,cpu,py},
  deps{ scipy, numpy, blender, mpfb, cc0_asset_ids }, blender_version,
  suite_pass_fail,
  aggregate{ movement_score, tier1, tier2, tier3, A_B_gap_mean },
  phrases: [ { id, style, hard_gates{...pass/fail}, tier2{metrics}, tier3{metrics},
              score, failures:[TAXONOMY...], vs_reference_A, vs_reference_B } ],
  regressions: [ ...vs baseline... ]
}
```

Statistical fields (review fix #3) are first-class: per-metric distributions across the
suite + CIs, and slots for inter-rater agreement on the human-labelled references. The
harness computes CIs across phrases even in V1 (cheap); the human-study protocol is data it
ingests, not code it runs.

## 8. HTML report (the standard artifact)

Self-contained single file (inline CSS/JS, embedded PNGs as data-URIs — matches our
Artifact rules): executive summary (score + Δ vs baseline, pass/fail), phrase table
(score, tier breakdown, failures, A↔B gap), regression highlights (red rows), metric-trend
sparklines across historical runs, embedded renders (fixtures + the fixed demonstration
phrase), trajectory plots (tip/root path, velocity, jerk), and the contact-sheet per phrase.
This becomes the review artifact for every planner change.

## 9. Reproducibility (must exactly reproduce a run)

In every report: planner commit, solver commit, benchmark_version, metric_version,
config_hash (hash of the full resolved config incl. weights), seed (solver is
deterministic, but multi-start order/seed pinned), runtime, machine info, dependency
versions (scipy/numpy/matplotlib), Blender version + MPFB extension version (renders are
part of the artifact), and the CC0 asset id/version (the MPFB base mesh). Plus: the exact
phrase + reference file hashes used. Anything render-affecting (Camera A params, render
language) is captured as a `render_config` block so a render is reproducible too. A run is
reproducible = same commits + same benchmark files + same config_hash → identical
movement_report.json (renders identical given same Blender build).

## 10. CI integration

```
planner change → PR → CI: run FAST tier (deterministic metrics, no Blender renders,
                         subset or full suite) → movement_report.json →
                         compare to pinned baseline → PASS/FAIL comment + JSON artifact
nightly / label 'full-bench' → full suite WITH renders → HTML artifact
```

**Blocks a merge (hard):** any Tier-1 hard-gate regression (a phrase that was feasible
becomes infeasible; contacts/ROM/collision fail; an impossible phrase gets "solved");
Movement Score drop beyond a pinned tolerance; any NEW `CONTACT_FAILURE`/`ROM_FAILURE`/
`COLLISION`. **Warns, does not block:** Tier-3 subscore dips, smoothness wobble, single-
phrase regressions within tolerance (guards against flakiness blocking merges). Renders are
too slow/nondeterministic-across-machines for PR gating → renders run nightly/on-label and
produce artifacts, not merge gates. The human perceptual gate is never automated — it's a
periodic manual review of the HTML, not a CI blocker.

## 11. Future compatibility (no architecture change required)

- **Multiple planners**: planner registry; report records which plugin + commit.
- **Multiple solver versions**: SolverAdapter.version() pinned in every report; a new solver
  is a new adapter binding, benchmark unchanged.
- **New benchmark versions**: benchmark_version in data + report; old results stay
  comparable within a version, cross-version diffs flagged.
- **New metrics**: metric registry — register a function `metric(trajectory, phrase, refs)
  -> value`; add its weight to suite.json. `evaluate.py` untouched.
- **New instruments**: instrument params in the phrase; n_strings parameterized (no 6
  hard-code). Fretless is the one exception (needs a continuous-pitch contact primitive) —
  documented as a future benchmark, not a V1 obstacle.
- **Style-conditioned eval**: `style` tag on phrases + references, `styles/` dir; Tier-3
  targets read the style's Reference B. Empty in V1, wired in the schema.
- **Right-hand benchmark**: a SEPARATE suite dir + its own metrics; reuses loader/adapters/
  report unchanged.

## 12. Out of scope (V1)

No planner, no benchmark content authoring (phrases/references are a separate data task),
no metric/scoring redesign, no perceptual-study software, no cross-machine render
determinism guarantees, no distributed/parallel execution.

## 13. Critical self-review — where THIS is over-engineered

- **11→5 module collapse is right; even 5 may be 4.** `report.py` and `evaluate.py` could
  merge for V1 (score-then-render is one flow). Keep them split only because HTML/regression
  logic genuinely grows; if it doesn't, merge.
- **Metric registry / plugin indirection is the one piece of "future" I'd KEEP** — it's
  cheap (a dict) and directly serves "new metrics without architectural change." Everything
  else future-facing should be DATA (schemas with reserved fields), not code.
- **Postpone to V2:** metric-trend sparklines / historical DB (V1 just diffs two runs — one
  baseline, one candidate; a trends DB is premature until there are many runs); style
  conditioning (schema slot now, no logic); CI full integration (start with a local
  `run_benchmark.py` + a JSON diff; wire GitHub Actions only once it's stable); inter-rater
  tooling (ingest labels as data; don't build a labelling app); confidence intervals can be
  a simple across-phrase std in V1, not a bootstrap.
- **Biggest over-engineering risk:** building the HTML report before there's data worth
  looking at. V1 should ship JSON first + a MINIMAL HTML (summary + phrase table + embedded
  demo render), and grow the HTML only when a real planner produces deltas worth
  visualizing. Renders reuse the existing pipeline — do NOT build a new renderer.
- **Simplest version that still scales:** 4 files (loader, adapters, evaluate, report) +
  reuse of mpfb_render/mpfb_solver + versioned `benchmark/` data + two frozen interfaces
  (Planner.plan, Solver.solve_pose/fk) + registries for planners & metrics. That is enough
  to evaluate the first planner and to absorb every listed future need without rework.

## 14. Recommendation

Build harness V1 as the 4 files + data format + two interfaces + registries above; ship
JSON report first, minimal HTML second, reusing the existing render/solver. Author the
benchmark phrase/reference DATA as a separate task (it carries the review's acceptance-
region + style + timing fixes). Then implement the naive baseline planner as the first
plugin to establish the regression baseline — and only then the real trajectory planner.
No harness code until you approve this architecture.
