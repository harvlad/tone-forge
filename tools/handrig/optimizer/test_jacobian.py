"""Phase 2 Proof B — analytic-Jacobian validation (permanent).

For every frozen phrase/knot, compare the JAX autodiff Jacobian of the residual
vector against high-quality central differences, PER residual family. Reports
max-abs, max-rel, mean-abs error and the worst-case sample (not only aggregates),
and checks determinism. Read-only; no planner/objective/benchmark change.

Note on non-smoothness: several families are hinges (contact miss, board,
collision, splay, coupling, thumb-short, conform). At the frozen solved states
these are interior (inactive → 0, or smooth quadratic), so autodiff and central
difference agree. A residual element sitting exactly on a hinge boundary would
show a one-sided vs central mismatch; such cases are reported explicitly, not
hidden.
"""
from __future__ import annotations
import os, sys, json
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "benchmark"))
sys.path.insert(0, HERE)
from adapters import SolverAdapter
from jax_forward import JaxForward
import jax
import jax.numpy as jnp

RES = os.path.join(os.path.dirname(HERE), "benchmark", "results")
PHRASES = ["single", "repeated_note", "string_crossing", "one_shift_scale", "sustained_anchor"]
EPS = 1e-6
GATE_ABS = 1e-4          # max abs Jacobian error allowed
GATE_REL = 1e-4          # max rel error on entries with meaningful magnitude


def central_diff(fn, v, m):
    n = v.shape[0]
    J = np.zeros((m, n))
    for j in range(n):
        vp = v.copy(); vp[j] += EPS
        vm = v.copy(); vm[j] -= EPS
        J[:, j] = (np.array(fn(vp)) - np.array(fn(vm))) / (2 * EPS)
    return J


def block_slices(jf, v):
    b = jf.blocks(v)
    sl = {}; i = 0
    for k in jf.ORDER:
        n = int(np.array(b[k]).shape[0]); sl[k] = slice(i, i + n); i += n
    return sl


def main():
    solver = SolverAdapter()
    rep = json.load(open(os.path.join(RES, "movement_report.json")))
    fam = {}   # family -> dict(maxabs, maxrel, sabs, cnt, worst)
    nondet = 0; n_knots = 0
    for pid in PHRASES:
        for ki, knot in enumerate(rep["trajectories"][pid]["knots"]):
            v = np.array(knot["mpfb_state"], float)
            specs = knot["meta"]["contacts"]
            jf = JaxForward(solver, specs)
            r0 = np.array(jf.residual(v)); m = r0.shape[0]
            Ja = np.array(jax.jacfwd(jf.residual)(v))
            Jfd = central_diff(jf.residual, v, m)
            # determinism: recompute analytic
            if not np.array_equal(np.array(jax.jacfwd(jf.residual)(v)), Ja):
                nondet += 1
            n_knots += 1
            sl = block_slices(jf, v)
            for k, s in sl.items():
                a = Ja[s]; fd = Jfd[s]
                if a.size == 0:
                    continue
                err = np.abs(a - fd)
                rel = err / (np.abs(fd) + 1e-8)
                d = fam.setdefault(k, dict(maxabs=0.0, maxrel=0.0, sabs=0.0, cnt=0, worst=None))
                d["sabs"] += err.sum(); d["cnt"] += err.size
                if err.max() > d["maxabs"]:
                    d["maxabs"] = float(err.max())
                    idx = np.unravel_index(np.argmax(err), err.shape)
                    d["worst"] = (f"{pid}#{ki}", int(idx[0]), int(idx[1]),
                                  float(a[idx]), float(fd[idx]))
                # relative error only where fd is not ~0 (avoid 0/0 on inactive hinges)
                mask = np.abs(fd) > 1e-6
                if mask.any():
                    d["maxrel"] = max(d["maxrel"], float(rel[mask].max()))

    print(f"knots checked: {n_knots}   nondeterministic Jacobians: {nondet}")
    print(f"{'family':11s} {'max_abs':>11s} {'max_rel':>11s} {'mean_abs':>11s}  worst (analytic vs fd)")
    ok = True
    for k in JaxForward.ORDER:
        if k not in fam:
            continue
        d = fam[k]; mean = d["sabs"] / max(1, d["cnt"])
        w = d["worst"]
        wtxt = f"{w[0]} r{w[1]}/v{w[2]} {w[3]:.4e} vs {w[4]:.4e}" if w else "-"
        flag = "" if (d["maxabs"] <= GATE_ABS and d["maxrel"] <= GATE_REL) else "  <-- CHECK"
        print(f"{k:11s} {d['maxabs']:11.3e} {d['maxrel']:11.3e} {mean:11.3e}  {wtxt}{flag}")
        if d["maxabs"] > GATE_ABS and d["maxrel"] > GATE_REL:
            ok = False
    print(f"gates: max_abs<={GATE_ABS:.0e} OR max_rel<={GATE_REL:.0e} per family; determinism exact")
    print("JACOBIAN_PASS" if (ok and nondet == 0) else "JACOBIAN_FAIL")


if __name__ == "__main__":
    main()
