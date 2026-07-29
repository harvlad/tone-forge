"""Phase 3 (increment 2b) — free-DoF whole-hand solver with a SOFT
synergy-manifold attraction and GENERALIZED contacts.

Lesson from 2a: reparameterizing fingers as mean+Sz+residual over-
constrains them and regresses exact contact accuracy. Instead we keep
the finger joints FREE (exact contacts) and add a SOFT cost = distance
from the natural synergy manifold. Fingers hit their targets; the
manifold penalty biases the REST of the hand toward coordinated,
natural poses (the brief's "allow residual freedom with a penalty").

Contacts are generalized primitives (PointContact / LineContact); the
solver reads only geometry, never a chord name. NO chord/barre hacks.
"""

from __future__ import annotations
from dataclasses import dataclass, field
import time
import numpy as np
from scipy.optimize import minimize

import anatomy as A
import guitar as G
import contacts as CN
from synergy import SynergyBasis

BASIS = SynergyBasis()

CONTACT_TOL_MM = 6.0
PENETRATION_TOL_MM = 3.0
COLLISION_TOL_MM = 2.0
FINGER_RADIUS_MM = 4.5


@dataclass
class CostBreakdown:
    contact_error: float = 0.0
    collision_penalty: float = 0.0
    board_penetration: float = 0.0
    strain_cost: float = 0.0
    splay_cost: float = 0.0
    coupling_cost: float = 0.0
    thumb_support_cost: float = 0.0
    wrist_cost: float = 0.0
    crossing_cost: float = 0.0
    manifold_cost: float = 0.0
    previous_pose_movement: float = 0.0
    total: float = 0.0

    def as_dict(self):
        return self.__dict__.copy()


@dataclass
class SolveResult:
    status: str
    state: object
    costs: CostBreakdown
    contact_accuracy_mm: dict
    max_contact_mm: float
    joint_limit_violations: list
    collision_penetration_mm: float
    board_penetration_mm: float
    thumb_behind_neck: bool
    naturalness: float
    solve_time_s: float = 0.0
    iterations: int = 0
    fk: dict = field(default_factory=dict)
    reasons: list = field(default_factory=list)


# Contact is now a REGION penalty (0 inside the valid fret area), so a
# high contact weight only pulls the tip INTO the region; once there it
# costs nothing and the naturalness terms (strain toward a fretting-ready
# curl, synergy manifold, splay, coupling) shape the whole hand freely.
W = dict(
    contact=8.0, board=40.0, collision=30.0, strain=2.6, splay=2.0,
    coupling=2.0, thumb=2.5, wrist=0.6, crossing=20.0, manifold=3.0, move=0.5,
)


def _finger_vector(state):
    return np.concatenate([state.finger[f] for f in A.FINGERS])


def _manifold_penalty(state):
    """Distance from the natural synergy manifold: project the 16-D
    finger vector onto the basis and penalize the off-manifold residual.
    Pulls the hand toward coordinated, natural coupled poses without
    preventing exact contacts (free DoF pays the penalty when needed)."""
    q = _finger_vector(state)
    z = BASIS.components @ (q - BASIS.mean)
    q_proj = BASIS.mean + BASIS.components.T @ z
    d = q - q_proj
    return float(np.sum(d * d))


def _finger_capsule_penalty(fingers):
    pen = 0.0
    order = A.FINGERS
    for i in range(len(order) - 1):
        a = [fingers[order[i]].pip, fingers[order[i]].dip, fingers[order[i]].tip]
        b = [fingers[order[i+1]].pip, fingers[order[i+1]].dip, fingers[order[i+1]].tip]
        dmin = min(np.linalg.norm(pa - pb) for pa in a for pb in b)
        overlap = 2 * FINGER_RADIUS_MM - dmin
        if overlap > 0:
            pen += overlap ** 2
    return pen


def _crossing_penalty(fingers):
    order = A.FINGERS
    tips = [fingers[f].tip for f in order]
    pen = 0.0
    for i in range(len(tips) - 1):
        dx = tips[i][0] - tips[i + 1][0]
        dz = abs(tips[i][2] - tips[i + 1][2])
        if dx < 0:
            pen += (dx * dx) * max(0.0, 1.0 - dz / 20.0)
    return pen


def _board_penetration(fingers):
    pen = 0.0
    for f in A.FINGERS:
        for p in fingers[f].joints():
            pen += G.penetrates_board(p) ** 2
    return pen


def _thumb_support_cost(thumb, fingers):
    tip = thumb.tip
    mid_x = fingers["middle"].mcp[0]
    target = np.array([mid_x, G.NECK_THICK, 0.0])
    cost = float(np.sum((tip - target) ** 2)) * 0.01
    shortfall = G.NECK_THICK - tip[1]
    if shortfall > 0:
        cost += (shortfall / 10.0) ** 2 * 1.5
    return cost


def evaluate(model, state, contact_list, prev_state) -> CostBreakdown:
    fingers, thumb, _ = A.forward_kinematics(model, state)
    cb = CostBreakdown()
    cb.contact_error = sum(
        (2.2 if c.kind == "line" else 1.0) * c.residual(fingers[c.finger])
        for c in contact_list)
    cb.board_penetration = _board_penetration(fingers)
    cb.collision_penalty = _finger_capsule_penalty(fingers)
    cb.crossing_cost = _crossing_penalty(fingers)
    cb.strain_cost = A.strain_penalty(state)
    cb.splay_cost = A.splay_penalty(state)
    cb.coupling_cost = A.coupling_penalty(state)
    cb.thumb_support_cost = _thumb_support_cost(thumb, fingers)
    cb.wrist_cost = state.wrist_rot[2] ** 2 * 0.5
    cb.manifold_cost = _manifold_penalty(state)
    if prev_state is not None:
        d = state.to_vector() - prev_state.to_vector()
        cb.previous_pose_movement = float(np.sum(d * d))
    cb.total = (
        W["contact"] * cb.contact_error + W["board"] * cb.board_penetration
        + W["collision"] * cb.collision_penalty + W["crossing"] * cb.crossing_cost
        + W["strain"] * cb.strain_cost + W["splay"] * cb.splay_cost
        + W["coupling"] * cb.coupling_cost + W["thumb"] * cb.thumb_support_cost
        + W["wrist"] * cb.wrist_cost + W["manifold"] * cb.manifold_cost
        + W["move"] * cb.previous_pose_movement)
    return cb


def _initial_state(model, contact_list, prev_state):
    all_targets = []
    for c in contact_list:
        all_targets += c.targets()
    cx = float(np.mean([t[0] for t in all_targets]))
    cz = float(np.mean([t[2] for t in all_targets]))
    wrist_pos = np.array([cx, -14.0, cz - 58.0])
    wrist_rot = np.array([-np.pi / 2, 0.0, 0.0])
    if prev_state is not None:
        finger = {f: prev_state.finger[f].copy() for f in A.FINGERS}
        thumb = prev_state.thumb.copy()
    else:
        neutral = np.array([15.0, 0.0, 25.0, 16.0]) * A.D2R
        finger = {f: neutral.copy() for f in A.FINGERS}
        thumb = np.array([35.0, 20.0, 10.0, 10.0]) * A.D2R
    st = A.HandState(wrist_pos, wrist_rot, finger, thumb)
    # LINE-contact-aware seed (contact TYPE, not chord): flatten the
    # barre finger and drop the palm to the low end of the span so the
    # flat finger can sweep the strings. Generalizes to any barre.
    line = next((c for c in contact_list if c.kind == "line"), None)
    if line is not None:
        straight = np.array([10.0, 0.0, 8.0, 5.0]) * A.D2R
        st.finger[line.finger] = straight
        span = line.targets()
        st.wrist_pos[2] = min(t[2] for t in span) - 40
    return st


def solve(model, contact_list, prev_state=None, restarts=5):
    t0 = time.time()
    seed = _initial_state(model, contact_list, prev_state)
    lo, hi = A.bounds(model, seed.wrist_pos, reach=120.0)

    def obj(v):
        st = A.HandState.from_vector(v)
        return evaluate(model, st, contact_list, prev_state).total

    wrist_perturb = [(0, 0, 0), (0.15, 0, 0), (-0.15, 0, 0),
                     (0, 0.12, 0), (0.25, 0, 0.05), (0.1, 0, 0.35),
                     (0.1, 0, -0.35)]
    curl = [0.0, 0.15, -0.1, 0.2, -0.15, 0.1, -0.05]
    zpos = [0, 0, 0, 0, -22, 22, -16]
    best, best_cost, best_iter = None, np.inf, 0
    for r in range(restarts):
        v0 = seed.to_vector().copy()
        wp = wrist_perturb[r % len(wrist_perturb)]
        v0[3] += wp[0]; v0[4] += wp[1]; v0[5] += wp[2]
        for k in range(6, A.STATE_SIZE):
            v0[k] += curl[r % len(curl)]
        v0[2] += zpos[r % len(zpos)]
        v0 = np.clip(v0, lo, hi)
        res = minimize(obj, v0, method="L-BFGS-B", bounds=list(zip(lo, hi)),
                       options=dict(maxiter=300, ftol=1e-9))
        if res.fun < best_cost:
            best_cost, best, best_iter = res.fun, res.x, res.nit

    state = A.HandState.from_vector(best)
    cb = evaluate(model, state, contact_list, prev_state)
    fingers, thumb, _ = A.forward_kinematics(model, state)

    acc, max_c = {}, 0.0
    for c in contact_list:
        e = c.error_mm(fingers[c.finger])
        acc[f"{c.kind}:{c.finger}"] = round(e, 2)
        if c.required:
            max_c = max(max_c, e)

    viol = _limit_violations(model, state)
    coll = float(np.sqrt(cb.collision_penalty)) if cb.collision_penalty > 0 else 0.0
    boardpen = float(np.sqrt(cb.board_penetration)) if cb.board_penetration > 0 else 0.0
    thumb_ok = G.behind_neck(thumb.tip)

    status, reasons = "OK", []
    if max_c > CONTACT_TOL_MM:
        status = "INFEASIBLE"; reasons.append(f"contact {max_c:.1f}mm")
    if boardpen > PENETRATION_TOL_MM:
        status = "INFEASIBLE"; reasons.append(f"board {boardpen:.1f}mm")
    if coll > COLLISION_TOL_MM:
        status = "INFEASIBLE"; reasons.append(f"collision {coll:.1f}mm")
    if viol:
        status = "INFEASIBLE"; reasons.append(f"limits {viol}")

    return SolveResult(
        status=status, state=state, costs=cb, contact_accuracy_mm=acc,
        max_contact_mm=round(max_c, 2), joint_limit_violations=viol,
        collision_penetration_mm=round(coll, 2),
        board_penetration_mm=round(boardpen, 2),
        thumb_behind_neck=bool(thumb_ok),
        naturalness=round(cb.strain_cost + cb.splay_cost + cb.coupling_cost, 3),
        solve_time_s=round(time.time() - t0, 3), iterations=int(best_iter),
        fk=dict(fingers=fingers, thumb=thumb), reasons=reasons)


def _limit_violations(model, state, tol_deg=0.5):
    viol = []
    tol = tol_deg * A.D2R
    for f in A.FINGERS:
        lims = model.finger_limits[f]
        for j, dof in enumerate(A.FINGER_DOF):
            val = state.finger[f][j]
            L = lims[dof]
            if val < L.lo - tol or val > L.hi + tol:
                viol.append(f"{f}.{dof}={val/A.D2R:.0f}")
    return viol
