# M2 findings — first vertical slice (1 metric → report → PASS)

**1. What did we build?**
`evaluate.py` — metric engine with ONE metric, the `contact_fidelity` hard gate
(multiplicative {0,1}: every required contact must be reproduced by `fk(stored_state)`
within 6 mm). `report.py` — the single end-to-end command: load → naive solve → evaluate →
`movement_report.json` with full repro metadata + a `config_hash`.

**2. What did we learn?**
The pipeline works end-to-end and is reproducible. One command:
```
phrase=single  hard_gate=PASS  score=1.0  worst_contact=0.001mm  config_hash=5671935c112eb6f5
BYTE_IDENTICAL_ON_RERUN
```
Byte-identity holds because (a) the solver is deterministic — fixed restart perturbations,
no RNG — and (b) volatile fields (timestamp, runtime) are printed to stdout, never written
to the report. Metadata captured: benchmark_version, metric_version, planner+solver commit,
config_hash, seed, deps (numpy/scipy/python), machine, CC0 asset id.

**3. Assumptions confirmed.**
- The end-to-end slice is real, not mocked: phrase JSON in, verdict + report out.
- Determinism is free — the frozen solver has no hidden randomness, so regression
  comparison (M5) can rely on byte-identity within a (config_hash, machine).
- The metric-as-pure-function shape (`fn(traj, phrase, ref, solver)`) composes; the registry
  dict is enough until M3.

**4. Assumptions disproved.**
None. One deliberate deviation from the harness metadata sketch: `timestamp` and `runtime_s`
are NOT in the report file (they would break the done-when's byte-identity). They belong in
a run-log sidecar if ever needed — recorded here so it is a documented choice, not drift.

**5. Change before M3?**
Nothing. `config_hash` + deterministic states give M5 its regression baseline for free.
Render deps (blender/mpfb) are correctly marked render-only and deferred to M8. Proceed to
M3 (planner plugin interface + registry; the naive loop becomes the first registered plugin).
