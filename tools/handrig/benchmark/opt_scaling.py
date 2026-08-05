"""Phase 0 — state scaling (mixed-unit conditioning fix).

Self-contained, verified reparameterization. NOT wired into the frozen planner
(that is Phase 3, backend replacement); this module only provides + proves the
transform so later phases can adopt it. Rollback = delete this file; nothing
depends on it yet.

Problem (measured, see HANDRIG_OPTIMIZER_AUDIT.md): the 30-D per-knot state mixes
root translation in METRES (indices 0:3) with rotations/joint angles in RADIANS
(indices 3:30). In the trajopt objective the root-translation directions carry
1e4-1e5x the curvature of the joint directions (diag-Hessian block ratio up to
2.8e5), which is the unit-mismatch component of the ~1e13 conditioning.

Fix: optimize in scaled coordinates y = x / w, where w scales only the translation
block by a characteristic length L so a translation of L metres has the same
numeric size as a 1-radian joint change. Diagonal, exactly invertible, objective-
preserving: the map is a pure change of variables, so it changes the solver's
landscape conditioning but NOT the objective, the feasible set, or the optimum.

Per the design review's corrected gate: this addresses the UNIT-MISMATCH part of
the conditioning only. The residual structural null-space near-singularity is NOT
a scaling problem and is left for LM damping (Phase 3). Success gate here is the
root-vs-joint block ratio -> O(1), NOT total cond -> 1e6.
"""
from __future__ import annotations
import numpy as np

STATE_N = 30
RLOC = slice(0, 3)          # root translation, metres (the only scaled block)
# characteristic length: chosen so the trajopt-objective diagonal-Hessian
# root-block ~= joint-block (geometric mean of the per-phrase equalising L from
# the audit blocks: sustained 1.9mm, one_shift 5.3mm, string_crossing 14mm ~= 5mm).
# Use a POWER OF TWO so x/w*w is bit-exact (preserves byte-reproducibility when
# this scaling is later wired into the deterministic solver). 2**-8 m = 3.90625 mm.
DEFAULT_L = 2.0 ** -8      # metres = 0.00390625


def scale_weights(n_knots: int, L: float = DEFAULT_L) -> np.ndarray:
    """Diagonal weight vector w for a flattened (n_knots x 30) state.
    y = x / w  <=>  x = y * w. Translation dims get w=L (so 1 scaled unit = L m);
    all rotation/joint dims get w=1 (already O(1) radians)."""
    w1 = np.ones(STATE_N)
    w1[RLOC] = L
    return np.tile(w1, n_knots)


def to_scaled(x: np.ndarray, w: np.ndarray) -> np.ndarray:
    return np.asarray(x, float) / w


def from_scaled(y: np.ndarray, w: np.ndarray) -> np.ndarray:
    return np.asarray(y, float) * w


def scaled_bounds(lo: np.ndarray, hi: np.ndarray, w: np.ndarray):
    """Box bounds transform identically (w>0): [lo,hi] in x -> [lo/w, hi/w] in y."""
    return np.asarray(lo, float) / w, np.asarray(hi, float) / w


def make_scaled_objective(obj_x, w):
    """Wrap an objective defined on x so it can be called on scaled y.
    obj_y(y) = obj_x(from_scaled(y, w)). Value-identical by construction."""
    def obj_y(y):
        return obj_x(from_scaled(y, w))
    return obj_y


def _selftest():
    """Mathematical identity checks (no solver, no planner)."""
    rng = np.random.default_rng(0)
    for N in (1, 2, 3, 4):
        w = scale_weights(N)
        x = rng.standard_normal(N * STATE_N)
        # round-trip exact
        assert np.allclose(from_scaled(to_scaled(x, w), w), x, atol=0, rtol=0), "roundtrip"
        # objective value preserved under the wrap for an arbitrary nonlinear obj
        obj_x = lambda z: float(np.sum(np.sin(z) ** 2) + 0.3 * np.sum(z ** 4))
        obj_y = make_scaled_objective(obj_x, w)
        y = to_scaled(x, w)
        assert abs(obj_y(y) - obj_x(x)) < 1e-15, "objective preserved"
        # bounds transform preserves membership
        lo = x - 0.1; hi = x + 0.1
        loy, hiy = scaled_bounds(lo, hi, w)
        assert np.all(loy <= y) and np.all(y <= hiy), "bounds membership"
    print("opt_scaling selftest: PASS (roundtrip exact, objective preserved, bounds consistent)")


if __name__ == "__main__":
    _selftest()
