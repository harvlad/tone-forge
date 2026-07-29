# M5 findings — Movement Score + failure taxonomy + baseline + regression diff

**1. What did we build?**
- Tiered **Movement Score** = hard-gate multiplier {0,1} × weighted soft quality. Each soft
  metric is mapped to a [0,1] QUALITY against the phrase's reference (economy is meaningless
  in the absolute — only vs the acceptance region): `shift_count` low-is-good vs Reference A
  ideal; `fingertip_travel`/`root_travel`/`finger_reuse` two-sided bands from Reference B.
  Weights come from `suite.json` (provisional, per the review). Tier2/Tier3 reported
  separately.
- **Failure classifier**: named, checkable taxonomy tags (`infeasible_contact`,
  `excess_shifts`, `over_travel`, `position_wander`, `missed_shift`, `anchor_released` /
  `under_reuse`) — never a vague "looks bad".
- **Regression diff** (`report.py diff_reports` + `--pin`/`--diff`): per-phrase score delta
  vs a pinned baseline, flags REGRESSED/improved.
- `check_m5.py`: the binary done-when harness.

**2. What did we learn? (the numbers)**
```
                 naive     recenter(worse)
single           1.000     1.000
repeated_note    1.000     1.000
string_crossing  0.782     0.273
one_shift_scale  0.661     0.523
sustained_anchor 1.000     0.703
aggregate        4.443  -> 3.499
diff: any_regressed=True  improved=[]  ->  MONOTONIC_WORSE=True
infeasible probe: score=0.0 gate=False tags=[infeasible_contact]  TAGGED_OK
M5_OK
```
The score is trustworthy in the only sense M5 claims: it moves the RIGHT DIRECTION with an
obvious quality change. The deliberately-worse `recenter` planner (no warm-start → recentres
the hand every note) scores ≤ naive on every phrase, strictly worse on the three that have
room to move, and never better. Feasible→infeasible drops the score to exactly 0 via the
hard-gate multiplier, and the defect is tagged.

**3. Assumptions confirmed.**
- Hard-gate-×-soft structure works: correctness gates absolutely, economy/musicianship shade
  continuously within the feasible set.
- Scoring against the reference (not absolute) is what makes cross-phrase scores comparable —
  string_crossing's 0.782 already encodes the M4 over-travel finding, with no special-casing.
- Re-scoring stored trajectories (FK-only, no re-solve) is fast — regression comparison does
  not pay the solver cost twice.

**4. Assumptions disproved.**
None. The M4 over-travel/over-shift observations flow into the score cleanly, which is the
consistency check I wanted.

**5. Change before M6?**
No code change. Two things carried forward, both already flagged, NOT hidden:
- **Weights are provisional** and the metrics are coupled (shift/travel co-move). The score is
  trustworthy for DIRECTION, not yet a calibrated absolute; a correlation study (post-M7) must
  set weights, not hand-tuning. `metric_version` stays 0.0.0 until then.
- The pinned baseline (`results/baseline_naive.json`) is the scored naive; it will be
  re-pinned when M6 adds the timing axis (new `metric_version`).
Proceed to M6 (timing metric — the review's missing first-order axis).
