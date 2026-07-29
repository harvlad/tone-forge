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


# timing tolerances are GROUNDED in perception + tempo, not arbitrary:
#   PERFECT_MS  perceptual onset-asynchrony tolerance (~30 ms) -> quality 1
#   decay end   one 16th-note at the phrase tempo -> quality 0 (clearly off-beat)
TIMING_PERFECT_MS = 30.0


def timing(trajectory, phrase, reference, solver):
    """On-beat ARRIVAL + hold. For each note the responsible fingertip must be
    in its contact region BY the onset (early/held is fine — a ready finger);
    LATE arrival is the defect (buzzed/muted attack). Also checks the finger
    holds the region until the note end. Grounded window: perfect within
    ~30 ms, zero one 16th-note late.

    Naive places every pose exactly at its onset, so arrival==onset and this is
    VACUOUSLY perfect — reported honestly as 1.0 (nothing to grade until a
    planner produces approach dynamics). The metric scans whatever knots exist,
    so it upgrades automatically when M7 emits dense trajectories."""
    bpm = phrase.tempo_bpm or 90
    beat_ms = 60000.0 / bpm
    decay_ms = max(beat_ms / 4.0, TIMING_PERFECT_MS * 2)      # a 16th-note
    knots = sorted(trajectory.knots, key=lambda k: k.t)
    per_note, qualities = [], []
    for e in phrase.events:
        spec = dict(string=e.string, fret=e.fret, finger=e.finger)
        in_region = [k.t for k in knots
                     if solver.contact_error_mm(k.mpfb_state, [spec]) <= CONTACT_TOL_MM]
        if not in_region:
            per_note.append(dict(t=e.t, finger=e.finger, arrival=None,
                                 late_ms=None, quality=0.0, note="never in region"))
            qualities.append(0.0)
            continue
        arrival = min(in_region)
        departure = max(in_region)
        late_ms = max(0.0, (arrival - e.t) * 1000.0)
        end = e.t + e.dur
        early_release_ms = max(0.0, (end - departure) * 1000.0)
        # onset lateness dominates; a short-held note is a lesser fault.
        # Score on onset LATENESS only. Release/hold needs a DENSE trajectory to
        # measure (a missing knot at note-end is not a release); it is reported
        # informationally and folded in only once M7 emits approach dynamics.
        q = round(_low_is_good(late_ms, TIMING_PERFECT_MS,
                               decay_ms - TIMING_PERFECT_MS), 3)
        qualities.append(q)
        per_note.append(dict(t=e.t, finger=e.finger, arrival=round(arrival, 4),
                             late_ms=round(late_ms, 1),
                             early_release_ms=round(early_release_ms, 1), quality=q))
    score = round(float(np.mean(qualities)), 3) if qualities else 1.0
    return dict(name="timing", kind="soft", tier=3, timing_score=score,
                perfect_ms=TIMING_PERFECT_MS, decay_ms=round(decay_ms, 1),
                per_note=per_note)


METRICS = {
    "contact_fidelity": (contact_fidelity, "hard_gate"),
    "shift_count": (shift_count, "soft"),
    "fingertip_travel": (fingertip_travel, "soft"),
    "root_travel": (root_travel, "soft"),
    "finger_reuse": (finger_reuse, "soft"),
    "timing": (timing, "soft"),
}


# ---------------- scoring (M5) ----------------
# A metric's raw value becomes a QUALITY in [0,1] measured against the phrase's
# reference (economy is meaningless in the absolute — only vs the acceptance
# region). Weights are DATA (suite.json) and provisional per the review; the
# scorer only guarantees monotonicity, not a calibrated absolute number.

def _clamp01(x):
    return max(0.0, min(1.0, x))


def _low_is_good(value, ideal, tol):
    """Quality for 'lower is better': 1 at/below ideal, 0 at ideal+tol."""
    return _clamp01(1.0 - max(0.0, value - ideal) / max(tol, 1e-9))


def _band(value, lo, hi, soft):
    """Quality for a two-sided acceptance band: 1 inside [lo,hi], decaying to
    0 `soft` units outside either edge."""
    if value < lo:
        return _clamp01(1.0 - (lo - value) / max(soft, 1e-9))
    if value > hi:
        return _clamp01(1.0 - (value - hi) / max(soft, 1e-9))
    return 1.0


def _quality(name, results, reference):
    """Map one soft metric to [0,1] against the reference. Missing reference
    fields fall back to lenient defaults so unlabelled phrases still score."""
    A = reference.A.get("expected", {})
    C = reference.B.get("constraints", {})
    if name == "shift_count":
        ideal = A.get("shift_count", 0)
        return _low_is_good(results["shift_count"]["count"], ideal, tol=3.0)
    if name == "fingertip_travel":
        hi = C.get("economy_band_fingertip_mm", [0, 120])[1]
        return _band(results["fingertip_travel"]["total_mm"], 0, hi, soft=hi)
    if name == "root_travel":
        lo, hi = C.get("economy_band_root_mm", [0, 12])
        return _band(results["root_travel"]["total_mm"], lo, hi, soft=max(hi - lo, 20.0))
    if name == "finger_reuse":
        lo, hi = C.get("finger_reuse_band", [0.0, 1.0])
        return _band(results["finger_reuse"]["reuse"], lo, hi, soft=0.3)
    if name == "timing":
        return results["timing"]["timing_score"]     # already [0,1]
    return 1.0


def movement_score(results, hard_gate_pass, reference, weights):
    """Movement Score = hard-gate multiplier {0,1} × weighted soft quality.
    Tiers reported separately. Only IMPLEMENTED soft metrics contribute; future
    metrics in the weight table are ignored until they exist."""
    gate = 1.0 if hard_gate_pass else 0.0
    tiers = {"tier2": weights.get("tier2", {}), "tier3": weights.get("tier3", {})}
    per_q, tier_scores = {}, {}
    all_wq, all_w = 0.0, 0.0
    for tier, wmap in tiers.items():
        wq = w = 0.0
        for name, wt in wmap.items():
            if name not in results or wt <= 0:
                continue
            q = _quality(name, results, reference)
            per_q[name] = round(q, 3)
            wq += wt * q; w += wt
        tier_scores[tier] = round(wq / w, 3) if w > 0 else None
        all_wq += wq; all_w += w
    soft = (all_wq / all_w) if all_w > 0 else 0.0
    return dict(movement_score=round(gate * soft, 3),
                hard_gate_multiplier=gate, soft_score=round(soft, 3),
                tier2_score=tier_scores["tier2"], tier3_score=tier_scores["tier3"],
                per_metric_quality=per_q)


# ---------------- failure taxonomy (M5) ----------------
def classify_failures(results, hard_gate_pass, reference):
    """Emit taxonomy tags. Each tag is a named, checkable defect — never a
    vague 'looks bad'. Order = severity (gate failure first)."""
    tags = []
    A = reference.A.get("expected", {})
    C = reference.B.get("constraints", {})
    if not hard_gate_pass:
        cf = results.get("contact_fidelity", {})
        tags.append(dict(tag="infeasible_contact", severity="hard",
                         detail=f"worst {cf.get('worst_contact_mm')}mm > tol {cf.get('tol_mm')}mm"))
    sc = results.get("shift_count", {})
    if sc and sc["count"] > A.get("shift_count", 0) + 1:
        tags.append(dict(tag="excess_shifts", severity="soft",
                         detail=f"{sc['count']} shifts vs ideal {A.get('shift_count', 0)}"))
    ft = results.get("fingertip_travel", {})
    hi_ft = C.get("economy_band_fingertip_mm", [0, 1e9])[1]
    if ft and ft["total_mm"] > hi_ft:
        tags.append(dict(tag="over_travel", severity="soft",
                         detail=f"tip {ft['total_mm']}mm > band {hi_ft}mm"))
    rt = results.get("root_travel", {})
    if rt:
        lo_rt, hi_rt = C.get("economy_band_root_mm", [0, 1e9])
        if rt["total_mm"] > hi_rt:
            tags.append(dict(tag="position_wander", severity="soft",
                             detail=f"root {rt['total_mm']}mm > band {hi_rt}mm"))
        elif lo_rt > 0 and rt["total_mm"] < lo_rt:
            tags.append(dict(tag="missed_shift", severity="soft",
                             detail=f"root {rt['total_mm']}mm < required {lo_rt}mm"))
    fr = results.get("finger_reuse", {})
    if fr and "reuse" in fr:
        lo_r, hi_r = C.get("finger_reuse_band", [0.0, 1.0])
        anchored = bool(C.get("anchor_holds"))
        if fr["reuse"] < lo_r:
            tags.append(dict(tag="anchor_released" if anchored else "under_reuse",
                             severity="soft",
                             detail=f"reuse {fr['reuse']} < band {lo_r}"))
    return tags


def evaluate_trajectory(trajectory, phrase, reference, solver, metric_names=None,
                        weights=None):
    """Run metrics -> hard gate -> Movement Score + failure tags. Deterministic."""
    names = metric_names or list(METRICS)
    results, hard_gate_pass = {}, True
    for n in names:
        fn, kind = METRICS[n]
        r = fn(trajectory, phrase, reference, solver)
        results[n] = r
        if kind == "hard_gate":
            hard_gate_pass = hard_gate_pass and r["pass"]
    scoring = movement_score(results, hard_gate_pass, reference, weights or {})
    failures = classify_failures(results, hard_gate_pass, reference)
    return dict(phrase_id=phrase.id, hard_gate_pass=bool(hard_gate_pass),
                score=scoring["movement_score"], scoring=scoring,
                failures=failures, metrics=results)
