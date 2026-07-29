"""M2/M4 — metric engine.

Metrics are pure functions (trajectory, phrase, reference, solver) -> result.
A metric is either a HARD GATE (multiplicative {0,1} — correctness) or a SOFT
metric (continuous; tiered scoring lands in M5).

A trajectory is a sequence of KNOTS, one per MOMENT (simultaneous contacts).
World positions come from the validated `solver.fk(state)`, so every metric
is measured in the same space the renderer will draw — no separate geometry.

M4 metrics:
  contact_fidelity  hard gate  — every contact reproduced within tol
  shift_count       soft (T2)  — deliberate hand relocations along the neck
  fingertip_travel  soft (T2)  — total fingertip path length (economy)
  root_travel       soft (T2)  — total hand-base path length (economy)
  finger_reuse      soft (T3)  — fraction of a moment's fingers held from the last
"""
from __future__ import annotations
import numpy as np

CONTACT_TOL_MM = 6.0      # mirrors mpfb_solver.CONTACT_TOL_MM
SHIFT_MM = 20.0           # along-neck root move that counts as a position shift
M2MM = 1000.0


def _fk_cache(trajectory, solver):
    """fk() for every knot, once. Returns list of {fingers, thumb, root}."""
    return [solver.fk(k.mpfb_state) for k in trajectory.knots]


def _fingers_in(knot):
    return {c["finger"] for c in knot.meta.get("contacts", [])}


# ---------------- hard gate ----------------
def contact_fidelity(trajectory, phrase, reference, solver):
    """HARD GATE. Every contact in every moment must be reproduced by
    fk(stored_state) within tolerance. A pose that does not press the note is
    worthless regardless of grace."""
    worst = 0.0
    per_moment = []
    for k in trajectory.knots:
        specs = k.meta.get("contacts", [])
        miss = solver.contact_error_mm(k.mpfb_state, specs) if specs else 0.0
        per_moment.append(dict(t=k.t, n_contacts=len(specs),
                               contact_mm=round(miss, 3),
                               within_tol=bool(miss <= CONTACT_TOL_MM)))
        worst = max(worst, miss)
    passed = bool(per_moment) and all(p["within_tol"] for p in per_moment)
    return dict(name="contact_fidelity", kind="hard_gate", tier=1,
                tol_mm=CONTACT_TOL_MM, worst_contact_mm=round(worst, 3),
                per_moment=per_moment, **{"pass": bool(passed)})


# ---------------- soft: economy ----------------
def root_travel(trajectory, phrase, reference, solver):
    """Total hand-base path length (mm). Low = economical, no wandering."""
    fks = _fk_cache(trajectory, solver)
    roots = [np.array(f["root"]) * M2MM for f in fks]   # root stored in metres
    total = sum(float(np.linalg.norm(roots[i] - roots[i - 1]))
                for i in range(1, len(roots)))
    return dict(name="root_travel", kind="soft", tier=2,
                total_mm=round(total, 2), n_knots=len(roots))


def fingertip_travel(trajectory, phrase, reference, solver):
    """Total fingertip path length summed over the four fingers (mm)."""
    fks = _fk_cache(trajectory, solver)
    per_finger = {}
    for f in ("index", "middle", "ring", "pinky"):
        tips = [np.array(fk["fingers"][f][-1]) for fk in fks]   # already mm
        per_finger[f] = round(sum(float(np.linalg.norm(tips[i] - tips[i - 1]))
                                  for i in range(1, len(tips))), 2)
    return dict(name="fingertip_travel", kind="soft", tier=2,
                total_mm=round(sum(per_finger.values()), 2),
                per_finger_mm=per_finger)


def shift_count(trajectory, phrase, reference, solver):
    """Deliberate hand relocations along the neck: count moment-to-moment
    root moves along the neck axis (x) exceeding SHIFT_MM."""
    fks = _fk_cache(trajectory, solver)
    xs = [f["root"][0] * M2MM for f in fks]
    shifts, deltas = 0, []
    for i in range(1, len(xs)):
        dx = abs(xs[i] - xs[i - 1])
        deltas.append(round(dx, 2))
        if dx > SHIFT_MM:
            shifts += 1
    return dict(name="shift_count", kind="soft", tier=2,
                count=shifts, threshold_mm=SHIFT_MM, deltas_mm=deltas)


# ---------------- soft: musicianship ----------------
def finger_reuse(trajectory, phrase, reference, solver):
    """Fraction of a moment's fingers that were already active in the previous
    moment (guide/held fingers). Repeated notes with one finger -> 1.0; all
    distinct fingers -> 0.0. Averaged over moment transitions."""
    ks = trajectory.knots
    if len(ks) < 2:
        return dict(name="finger_reuse", kind="soft", tier=3,
                    reuse=1.0, note="single moment; trivially 1.0")
    ratios = []
    for i in range(1, len(ks)):
        cur, prev = _fingers_in(ks[i]), _fingers_in(ks[i - 1])
        if cur:
            ratios.append(len(cur & prev) / len(cur))
    reuse = float(np.mean(ratios)) if ratios else 0.0
    return dict(name="finger_reuse", kind="soft", tier=3,
                reuse=round(reuse, 3), per_transition=[round(r, 3) for r in ratios])


METRICS = {
    "contact_fidelity": (contact_fidelity, "hard_gate"),
    "shift_count": (shift_count, "soft"),
    "fingertip_travel": (fingertip_travel, "soft"),
    "root_travel": (root_travel, "soft"),
    "finger_reuse": (finger_reuse, "soft"),
}


def evaluate_trajectory(trajectory, phrase, reference, solver, metric_names=None):
    """Run the selected metrics. Hard gates multiply into a {0,1} correctness
    factor; soft metrics are reported (tiered scoring is M5). Deterministic."""
    names = metric_names or list(METRICS)
    results, hard_gate_pass = {}, True
    for n in names:
        fn, kind = METRICS[n]
        r = fn(trajectory, phrase, reference, solver)
        results[n] = r
        if kind == "hard_gate":
            hard_gate_pass = hard_gate_pass and r["pass"]
    score = 1.0 if hard_gate_pass else 0.0     # score stub until M5
    return dict(phrase_id=phrase.id, hard_gate_pass=bool(hard_gate_pass),
                score=score, metrics=results)
