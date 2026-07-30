# Optimizer Migration — Phase 4 investigation report (behaviour classification)

Purpose: use the validated GN/LM optimizer (Phase 3, unchanged, untuned) as an experimental
instrument to determine which previously-observed planner behaviours were OPTIMIZER artefacts
(A), OBJECTIVE artefacts (B), or OPEN (C). No planner/optimizer/objective/benchmark change.
`phase4_investigate.py` re-solves every frozen moment with GN/LM (warm-started from the
committed naive pose, same contacts + bounds) and scores the result with the FROZEN metrics.

## Headline result
GN/LM converges the frozen objective BETTER than the naive L-BFGS-B backend (lower per-moment
cost; worst-contact improves everywhere: one_shift 3.73→1.36 mm, string_crossing 0.012→0.002,
sustained 0.010→0.001) — and in doing so ROOT TRAVEL RISES and benchmark scores FALL:
```
phrase           root_travel (mm)   fingertip (mm)   movement_score   shift_count
                 LBFGSB -> GN/LM    LBFGSB -> GN/LM  LBFGSB -> GN/LM  LBFGSB -> GN/LM
single             0.00 ->  0.00      0.0 ->   0.0     1.000 -> 1.000    0 -> 0
repeated_note      0.01 ->  4.30     14.3 ->  25.7     1.000 -> 0.994    0 -> 0
string_crossing    0.52 -> 16.07    253.4 -> 260.8     0.782 -> 0.752    0 -> 0
one_shift_scale   91.62 -> 98.57    889.3 -> 859.9     0.661 -> 0.661    2 -> 2
sustained_anchor   0.29 -> 18.85     52.1 ->  93.3     1.000 -> 0.936    0 -> 0
```
**The naive baseline's "quiet hand" (near-zero root travel) was largely an OPTIMIZER ARTEFACT.**
L-BFGS-B under-converged and stayed near its warm-start (the previous knot), incidentally
producing low inter-moment root motion. When the objective is TRULY minimized, its per-moment
minima are more root-displaced, so the hand moves more and the metrics penalize it.

## Classification of every previously-investigated behaviour
| # | behaviour | class | evidence |
|---|---|---|---|
| 1 | **naive "quiet hand" / low root-travel** | **A (optimizer artefact)** | GN/LM (better convergence, lower cost) raises root travel on 3/5 phrases (0.01→4.3, 0.52→16.1, 0.29→18.9 mm). The objective does NOT prefer the quiet hand; under-convergence produced it. |
| 2 | **sustained_anchor root wander** | **B (objective artefact)** | A BETTER optimizer makes it WORSE (0.29→18.85 mm) at LOWER cost — the frozen objective's true minimum genuinely sits ~19 mm displaced. Confirms the comfort-model study (comfort basin offset from the anchor; no task relevance) independently of the optimizer. |
| 3 | **one_shift_scale extra shift (2 not 1)** | **B (objective artefact)** | shift_count unchanged 2→2 under GN/LM. Better optimization does not remove it; the frozen per-moment objective wants the hand re-centred per note. (Only the whole-phrase resist PLANNER cut it to 1 — a planner-structure effect, not an optimizer one.) |
| 4 | **string_crossing large fingertip travel** | **B (objective artefact)** | essentially unchanged 253→261 mm under GN/LM — intrinsic 4-finger-reach geometry, not an optimizer effect. (The M4 reference band being tight is a separate reference-authoring matter.) |
| 5 | **one_shift_scale#2 poor high-fret pose (worst-contact 3.7 mm)** | **C (open / both-limited)** | GN/LM improves contact 3.73→1.36 mm but STALLS (cost 18.74, proj-grad 1.7e4, xtol); L-BFGS-B also non-stationary there. Near the feasibility limit and kink-dominated; neither optimizer solves it cleanly. Cannot be cleanly attributed. |
| 6 | **local minima / non-convergence (falsification)** | **B+A (confirmed)** | The falsification's core claim — L-BFGS-B was non-converged — is confirmed: from the same start GN/LM reaches strictly lower cost on 10/16 moments. But the resulting behaviour (more motion) is the objective's true preference (B), not a numerical fix that "cleans up" the hand. |
| 7 | **trajopt vs naive narrow win (2→1 shift)** | **B (planner-structure, not optimizer)** | The win came from the whole-phrase resist TERM, not the optimizer. Per-moment optimization (any backend) keeps shift=2. Consistent with the V2 conclusion that the fix is structural, not numerical. |

## The deep finding
The frozen objective and the benchmark's notion of "good motion" DISAGREE. Truly minimizing the
objective (GN/LM) LOWERS benchmark movement scores (repeated 1.000→0.994, string 0.782→0.752,
sustained 1.000→0.936) because the objective — which has no task relevance and lets comfort
chase the fingers (comfort-model study) — genuinely prefers a MORE MOBILE hand than the metrics
reward. The naive baseline scored well partly because its optimizer failed to reach that
preference. This is not an optimizer defect; it is the objective's true character surfacing.

## Which behaviours were numerical vs architectural
- **Numerical (A):** the quiet hand / low root travel of the naive baseline.
- **Architectural (B):** sustained_anchor wander, the one_shift extra shift, string_crossing
  travel — the frozen objective genuinely prefers these; a faithful optimizer confirms it.
- **Open (C):** one_shift_scale#2 high-fret feasibility — objective-hard AND optimizer-hard;
  both backends stall.

## Confidence
High (~90%) for classes A and B: the direction is unambiguous — a strictly-lower-cost optimizer
increases root travel and lowers benchmark scores, which is only possible if the objective's
true minima are more mobile than L-BFGS-B revealed. Determinism holds (Phase 3: 0/16
nondeterministic). Lower for item 5 (~open by construction) and any claim about magnitude on the
stalled moments, where GN/LM does not reach stationarity.

## Recommendation — did the migration isolate planner behaviour?
YES. The optimizer migration has successfully SEPARATED numerical behaviour from planner
behaviour. It overturned one headline behaviour (the quiet hand was numerical, not intended by
the objective) and independently CONFIRMED that the flagged pathologies are architectural —
directly corroborating the Planner V2 conclusion (the objective lacks task relevance and prefers
comfort-chasing motion) under a faithful optimizer, not merely at the frozen weights. The
benchmark/objective disagreement is now a measured, defensible fact rather than a suspicion.

No planner or optimizer change was made. The one open item (one_shift_scale#2) is
optimizer-and-objective-hard; a trust-region strategy is the natural OPTIMIZER lever, but that
is out of this investigation's scope.

## Rollback
Delete `phase4_investigate.py` and this report. Nothing else changed.

## GO / NO-GO request
Requesting explicit GO / NO-GO for Phase 5 (optional contact handling, only if evidence
justifies it). Not proceeding automatically.
