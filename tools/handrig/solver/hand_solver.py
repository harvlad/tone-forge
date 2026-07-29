"""Phase 3 — Static whole-hand biomechanical solver.

Input:  required contacts (string/fret/finger), neck geometry, the
        anatomical hand model, and an optional previous pose.
Output: a solved whole-hand HandState, plus a per-term cost breakdown
        and a feasibility verdict — or INFEASIBLE.

The solver does NOT solve fingers independently: one 26-DoF state is
optimized jointly under shared anatomical + collision costs. Contact
error is only ONE term; a pose that hits the targets but violates
anatomy/collision is rejected. There are NO chord-specific or
per-finger special cases.
"""

from __future__ import annotations
from dataclasses import dataclass, field
import numpy as np
from scipy.optimize import minimize

import anatomy as A
import guitar as G


# Feasibility thresholds.
CONTACT_TOL_MM = 6.0        # a required contact must land within this
PENETRATION_TOL_MM = 3.0    # allowed board/neck penetration
COLLISION_TOL_MM = 2.0      # allowed finger/finger overlap beyond radii
# Fingertip pad half-width. Adjacent strings sit ~8.7 mm apart, and a
# real guitarist DOES place fingers on adjacent strings at one fret
# nearly touching — so the collision radius must be smaller than the
# string gap or legitimate chords (Em, A) read as penetration.
FINGER_RADIUS_MM = 4.5


@dataclass
class CostBreakdown:
    contact_error: float = 0.0
    joint_limit_penalty: float = 0.0
    collision_penalty: float = 0.0
    board_penetration: float = 0.0
    strain_cost: float = 0.0
    splay_cost: float = 0.0
    coupling_cost: float = 0.0
    thumb_support_cost: float = 0.0
    wrist_cost: float = 0.0
    crossing_cost: float = 0.0
    previous_pose_movement: float = 0.0
    total: float = 0.0

    def as_dict(self):
        return self.__dict__.copy()


@dataclass
class SolveResult:
    status: str                       # "OK" | "INFEASIBLE"
    state: A.HandState | None
    costs: CostBreakdown
    # Separated validation metrics (never collapsed into one score):
    contact_accuracy_mm: dict         # per-contact fingertip error
    max_contact_mm: float
    joint_limit_violations: list
    collision_penetration_mm: float
    board_penetration_mm: float
    thumb_behind_neck: bool
    naturalness: float                # strain+splay+coupling (lower better)
    fk: dict = field(default_factory=dict)


# Soft-cost weights. Contact is weighted high to pull the solve, but
# feasibility is judged SEPARATELY (not by these weights).
W = dict(
    contact=6.0,      # weighted high to pull the solve; feasibility judged
                      # separately (not by this weight)
    board=40.0,
    collision=30.0,
    strain=0.35,
    splay=0.9,
    coupling=1.5,
    thumb=2.0,
    wrist=0.6,
    crossing=20.0,
    move=0.5,
)


def _contacts_targets(contacts, barre):
    """Flatten contacts + barre into (finger, target) pairs."""
    pairs = []
    for c in contacts:
        pairs.append((c.finger, c.target(), c.required))
    if barre is not None:
        # Barre = several colinear targets the barre finger must span.
        # We attach them to the barre finger; the FK tip solves the
        # LOW string, the straightness cost handles the rest (below).
        ts = barre.targets()
        pairs.append((barre.finger, ts[0], True))  # tip → top string
        pairs.append((("_barre_line", barre.finger), ts, True))
    return pairs


def _finger_capsule_penalty(fingers):
    """Finger/finger collision: min distance between the mid segments of
    adjacent fingers vs 2·radius. Penalize overlap."""
    pen = 0.0
    order = A.FINGERS
    def seg_pts(fk):
        return [fk.pip, fk.dip, fk.tip]
    for i in range(len(order) - 1):
        a = seg_pts(fingers[order[i]])
        b = seg_pts(fingers[order[i + 1]])
        dmin = 1e9
        for pa in a:
            for pb in b:
                dmin = min(dmin, np.linalg.norm(pa - pb))
        overlap = 2 * FINGER_RADIUS_MM - dmin
        if overlap > 0:
            pen += overlap ** 2
    return pen


def _crossing_penalty(fingers):
    """Anti-tangle. Fingers may legitimately sit at the SAME x (three
    fingers across strings at one fret) — they then separate in z. So we
    do NOT force x-separation. We penalize only a genuine ORDER swap:
    when a radially-inner finger's tip crosses to the −x side of a
    radially-outer one AND they are also close in z (i.e. actually
    tangled, not merely stacked across strings)."""
    order = A.FINGERS
    tips = [fingers[f].tip for f in order]
    pen = 0.0
    for i in range(len(tips) - 1):
        dx = tips[i][0] - tips[i + 1][0]     # want ≥ 0 (index +x of middle)
        dz = abs(tips[i][2] - tips[i + 1][2])
        if dx < 0:
            # Out of x-order; weight by how close in z (tangle severity).
            closeness = max(0.0, 1.0 - dz / 20.0)
            pen += (dx * dx) * closeness
    return pen


def _board_penetration(fingers, thumb):
    pen = 0.0
    for f in A.FINGERS:
        for p in fingers[f].joints():
            pen += G.penetrates_board(p) ** 2
    return pen


def _thumb_support_cost(thumb, fingers):
    """Thumb should sit BEHIND the neck (y≈NECK_THICK), roughly opposing
    the middle-finger MCP in x/z. Penalize thumb in FRONT of the board."""
    tip = thumb.tip
    cost = 0.0
    # Behind the neck target: rear face, at the middle MCP's x, mid board.
    mid_x = fingers["middle"].mcp[0]
    target = np.array([mid_x, G.NECK_THICK, 0.0])
    cost += float(np.sum((tip - target) ** 2)) * 0.01
    # Mild aversion to the thumb ending in FRONT of the neck's rear
    # face. NOTE: reliably placing the thumb behind needs a neck-WRAP
    # posture (palm-side to the board, thumb curling to the rear), which
    # the current rigid-palm model only partially affords — a stronger
    # penalty here just drags the palm and breaks finger contacts. See
    # the report's known-gaps section.
    shortfall = G.NECK_THICK - tip[1]
    if shortfall > 0:
        cost += (shortfall / 10.0) ** 2 * 1.0
    return cost


def _wrist_cost(state: A.HandState):
    """Penalize extreme wrist rotation (unnatural palm orientation).
    Neutral rotation is the guitar-hand base orientation supplied via
    the initial guess; here we just discourage large roll/pitch."""
    rx, ry, rz = state.wrist_rot
    # Discourage excessive deviation from the seeded orientation is
    # handled by `move`; here penalize gross twist (rz) only.
    return rz * rz * 0.5


def evaluate(model, state, pairs, prev_state) -> CostBreakdown:
    fingers, thumb, Rw = A.forward_kinematics(model, state)
    cb = CostBreakdown()

    # Contact error (soft term).
    ce = 0.0
    for entry in pairs:
        if entry[0] == "_barre_line":
            continue
        finger, target, required = entry if len(entry) == 3 else (*entry, True)
        if isinstance(finger, tuple):
            continue
        tip = fingers[finger].tip
        ce += float(np.sum((tip - target) ** 2))
    cb.contact_error = ce

    # Barre straightness: the barre finger must be near-straight to lie
    # flat, and its extent should cover the barre targets.
    for entry in pairs:
        if entry[0] == "_barre_line":
            _, finger = entry[0], entry[1]
            fk = fingers[finger]
            # Straightness: PIP and DIP flexion near 0.
            _, _, pip, dip = state.finger[finger]
            cb.contact_error += (pip ** 2 + dip ** 2) * 20.0

    cb.board_penetration = _board_penetration(fingers, thumb)
    cb.collision_penalty = _finger_capsule_penalty(fingers)
    cb.crossing_cost = _crossing_penalty(fingers)
    cb.strain_cost = A.strain_penalty(state)
    cb.splay_cost = A.splay_penalty(state)
    cb.coupling_cost = A.coupling_penalty(state)
    cb.thumb_support_cost = _thumb_support_cost(thumb, fingers)
    cb.wrist_cost = _wrist_cost(state)
    if prev_state is not None:
        d = state.to_vector() - prev_state.to_vector()
        cb.previous_pose_movement = float(np.sum(d * d))

    cb.total = (
        W["contact"] * cb.contact_error
        + W["board"] * cb.board_penetration
        + W["collision"] * cb.collision_penalty
        + W["crossing"] * cb.crossing_cost
        + W["strain"] * cb.strain_cost
        + W["splay"] * cb.splay_cost
        + W["coupling"] * cb.coupling_cost
        + W["thumb"] * cb.thumb_support_cost
        + W["wrist"] * cb.wrist_cost
        + W["move"] * cb.previous_pose_movement
    )
    return cb


def _initial_state(model, contacts, barre, prev_state):
    """Seed: place the wrist below the board, centred on the pressed
    cluster, palm facing the board; fingers in a neutral curl. This is a
    GEOMETRIC seed, not a chord pose."""
    targets = [c.target() for c in contacts]
    if barre is not None:
        targets += barre.targets()
    cx = float(np.mean([t[0] for t in targets]))
    cz = float(np.mean([t[2] for t in targets]))
    # Wrist sits below (−z) and in front (−y) of the board so fingers
    # reach UP and press IN. Orientation: palm toward +y (board),
    # fingers toward +z (up the strings). In the hand's local frame
    # fingers extend +y and palm normal +z, so rotate so local +y→world
    # +z (up strings) and local +z→world +y? We rotate the wrist frame:
    #   Rx = -90° maps local +y → world +z, local +z → world -y.
    # We want palm normal (+z local) to face the board (-y world here,
    # since the hand is in front pressing back), i.e. local +z → +y.
    # Use Rx = +90°: local +y→ -z? Let's use euler that sends fingers up
    # and palm into board. Solve empirically below with a base guess.
    wrist_pos = np.array([cx, -14.0, cz - 58.0])
    wrist_rot = np.array([-np.pi / 2, 0.0, 0.0])  # fingers point +z (up)
    if prev_state is not None:
        finger = {f: prev_state.finger[f].copy() for f in A.FINGERS}
        thumb = prev_state.thumb.copy()
    else:
        neutral = np.array([15.0, 0.0, 25.0, 16.0]) * A.D2R
        finger = {f: neutral.copy() for f in A.FINGERS}
        thumb = np.array([35.0, 20.0, 10.0, 10.0]) * A.D2R
    return A.HandState(wrist_pos, wrist_rot, finger, thumb)


def solve(model, contacts, barre=None, prev_state=None, restarts=4):
    pairs = _contacts_targets(contacts, barre)
    seed = _initial_state(model, contacts, barre, prev_state)
    lo, hi = A.bounds(model, seed.wrist_pos, reach=120.0)

    def obj(v):
        st = A.HandState.from_vector(v)
        return evaluate(model, st, pairs, prev_state).total

    best = None
    best_cost = np.inf
    # Deterministic multi-start: vary wrist pitch/roll and overall finger
    # curl so a stuck local minimum on one seed is escaped by another.
    wrist_perturb = [
        (0.0, 0.0, 0.0), (0.15, 0.0, 0.0), (-0.15, 0.0, 0.0),
        (0.0, 0.12, 0.0), (0.0, -0.12, 0.0), (0.25, 0.0, 0.05),
        (-0.2, 0.1, 0.0), (0.1, -0.1, 0.0),
    ]
    curl_perturb = [0.0, 0.15, -0.1, 0.2, -0.15, 0.3, 0.1, -0.05]
    for r in range(restarts):
        v0 = seed.to_vector().copy()
        wp = wrist_perturb[r % len(wrist_perturb)]
        v0[3] += wp[0]; v0[4] += wp[1]; v0[5] += wp[2]
        cp = curl_perturb[r % len(curl_perturb)]
        for k in range(6, A.STATE_SIZE):
            v0[k] += cp
        v0 = np.clip(v0, lo, hi)
        res = minimize(obj, v0, method="L-BFGS-B",
                       bounds=list(zip(lo, hi)),
                       options=dict(maxiter=500, ftol=1e-10))
        if res.fun < best_cost:
            best_cost = res.fun
            best = res.x

    state = A.HandState.from_vector(best)
    cb = evaluate(model, state, pairs, prev_state)
    fingers, thumb, _ = A.forward_kinematics(model, state)

    # Separated validation metrics.
    acc = {}
    max_c = 0.0
    for c in contacts:
        e = float(np.linalg.norm(fingers[c.finger].tip - c.target()))
        acc[f"{c.finger}:{c.string}/{c.fret}"] = e
        if c.required:
            max_c = max(max_c, e)
    if barre is not None:
        # Barre error = worst distance from the index axis to each target.
        fk = fingers[barre.finger]
        axis0, axis1 = fk.mcp, fk.tip
        for t in barre.targets():
            d = _point_line_dist(t, axis0, axis1)
            acc[f"barre:{t[2]:.0f}"] = d
            max_c = max(max_c, d)

    limit_viol = _limit_violations(model, state)
    coll = np.sqrt(cb.collision_penalty) if cb.collision_penalty > 0 else 0.0
    boardpen = np.sqrt(cb.board_penetration) if cb.board_penetration > 0 else 0.0
    thumb_ok = G.behind_neck(thumb.tip)

    status = "OK"
    reasons = []
    if max_c > CONTACT_TOL_MM:
        status = "INFEASIBLE"; reasons.append(f"contact {max_c:.1f}mm > {CONTACT_TOL_MM}")
    if boardpen > PENETRATION_TOL_MM:
        status = "INFEASIBLE"; reasons.append(f"board penetration {boardpen:.1f}mm")
    if coll > COLLISION_TOL_MM:
        status = "INFEASIBLE"; reasons.append(f"finger collision {coll:.1f}mm")
    if limit_viol:
        status = "INFEASIBLE"; reasons.append(f"joint limits: {limit_viol}")

    result = SolveResult(
        status=status,
        state=state,
        costs=cb,
        contact_accuracy_mm=acc,
        max_contact_mm=max_c,
        joint_limit_violations=limit_viol,
        collision_penetration_mm=coll,
        board_penetration_mm=boardpen,
        thumb_behind_neck=thumb_ok,
        naturalness=cb.strain_cost + cb.splay_cost + cb.coupling_cost,
        fk=dict(fingers=fingers, thumb=thumb),
    )
    result.reasons = reasons
    return result


def _point_line_dist(p, a, b):
    ab = b - a
    t = np.clip(np.dot(p - a, ab) / (np.dot(ab, ab) + 1e-9), 0, 1)
    return float(np.linalg.norm(p - (a + ab * t)))


def _limit_violations(model, state, tol_deg=0.5):
    """Report joints AT or beyond their physiological limit (bounds
    should prevent this, but we verify separately)."""
    viol = []
    tol = tol_deg * A.D2R
    for f in A.FINGERS:
        lims = model.finger_limits[f]
        for j, dof in enumerate(A.FINGER_DOF):
            val = state.finger[f][j]
            L = lims[dof]
            if val < L.lo - tol or val > L.hi + tol:
                viol.append(f"{f}.{dof}={val/A.D2R:.0f}°")
    tl = model.thumb_limits
    for j, dof in enumerate(A.THUMB_DOF):
        val = state.thumb[j]
        L = tl[dof]
        if val < L.lo - tol or val > L.hi + tol:
            viol.append(f"thumb.{dof}={val/A.D2R:.0f}°")
    return viol
