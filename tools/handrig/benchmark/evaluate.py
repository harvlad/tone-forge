"""M2 — metric engine (one metric for the first vertical slice).

Metrics are pure functions (trajectory, phrase, reference, solver) -> result.
A metric is either a HARD GATE (multiplicative {0,1} — correctness) or a SOFT
metric (continuous, tiered scoring). M2 ships exactly ONE: the contact-fidelity
hard gate, the cheapest possible correctness check, reusing the validated
render==solver invariant (fk(state) reproduces the pressed contact).

The registry is a plain dict now; M3 formalizes plugin registration. Keeping
metrics as {name: fn} here so evaluate.py never grows a dispatch tree.
"""
from __future__ import annotations

# Feasibility tolerance mirrors the solver's own gate (mpfb_solver.CONTACT_TOL_MM).
CONTACT_TOL_MM = 6.0


def contact_fidelity(trajectory, phrase, reference, solver):
    """HARD GATE. Every required contact must be reproduced by fk(stored_state)
    within tolerance. This is the single non-negotiable: a pose that does not
    actually press the note is worthless regardless of how graceful it looks."""
    worst = 0.0
    per_note = []
    events = phrase.events
    for knot, e in zip(trajectory.knots, events):
        miss = solver.contact_error_mm(knot.mpfb_state, [e])
        per_note.append(dict(t=e.t, string=e.string, fret=e.fret,
                             finger=e.finger, contact_mm=round(miss, 3),
                             within_tol=bool(miss <= CONTACT_TOL_MM)))
        worst = max(worst, miss)
    passed = all(p["within_tol"] for p in per_note) and len(per_note) == len(events)
    return dict(name="contact_fidelity", kind="hard_gate",
                tol_mm=CONTACT_TOL_MM, worst_contact_mm=round(worst, 3),
                per_note=per_note, **{"pass": bool(passed)})


# name -> (fn, kind). M2 registry = one entry.
METRICS = {
    "contact_fidelity": (contact_fidelity, "hard_gate"),
}


def evaluate_trajectory(trajectory, phrase, reference, solver, metric_names=None):
    """Run the selected metrics. Hard gates multiply into a {0,1} correctness
    factor; soft metrics (none yet) will feed the tiered score. Returns a
    deterministic dict (sorted, no wall-clock)."""
    names = metric_names or list(METRICS)
    results = {}
    hard_gate_pass = True
    for n in names:
        fn, kind = METRICS[n]
        r = fn(trajectory, phrase, reference, solver)
        results[n] = r
        if kind == "hard_gate":
            hard_gate_pass = hard_gate_pass and r["pass"]
    # Score stub: correctness factor only until soft metrics land (M4/M5).
    score = 1.0 if hard_gate_pass else 0.0
    return dict(phrase_id=phrase.id, hard_gate_pass=bool(hard_gate_pass),
                score=score, metrics=results)
