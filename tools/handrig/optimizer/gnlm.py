"""Phase 3 — Gauss-Newton / Levenberg-Marquardt backend.

Minimizes 1/2 ||r(x)||^2 for the FROZEN objective, using the validated JAX
residual vector and analytic Jacobian (jax_forward). This is the OPTIMIZER only
— it introduces no new mathematics, does not touch the planner/objective/weights/
benchmark, and does not smooth any hinge. JAX subgradients at max(0,.) boundaries
are accepted as the linearisation (per the Phase 3 model assumption).

Levenberg-Marquardt with MARQUARDT diagonal scaling (damping = lam * diag(J^T J)):
this is scale-invariant and carries the Phase-0 state-scaling role, directly
addressing the root-vs-angle conditioning (~350:1) the audit measured. Box bounds
are enforced by projection (clip). Deterministic: no RNG anywhere.
"""
from __future__ import annotations
import time
import numpy as np
import jax
jax.config.update("jax_enable_x64", True)
import jax.numpy as jnp


def _proj_grad_norm(g, x, lo, hi, tol=1e-12):
    """Norm of the bound-projected gradient (KKT stationarity measure)."""
    pg = g.copy()
    at_lo = x <= lo + tol
    at_hi = x >= hi - tol
    pg[at_lo] = np.minimum(pg[at_lo], 0.0)
    pg[at_hi] = np.maximum(pg[at_hi], 0.0)
    return float(np.linalg.norm(pg))


def gauss_newton_lm(residual_fn, jac_fn, x0, lo, hi,
                    gtol=1e-6, xtol=1e-12, ftol=1e-14, maxiter=200,
                    lam0=1e-3, nu=3.0, lam_min=1e-12, lam_max=1e12,
                    max_inner=30):
    """residual_fn(x)->(m,), jac_fn(x)->(m,n); numpy in/out. Returns a diag dict."""
    t0 = time.time()
    x = np.clip(np.asarray(x0, float), lo, hi)
    r = np.asarray(residual_fn(x)); cost = 0.5 * float(r @ r)
    lam = lam0
    nfev = 1; njev = 0
    reason = "maxiter"
    lam_hist = [lam]
    accepts = 0; rejects = 0
    for it in range(maxiter):
        J = np.asarray(jac_fn(x)); njev += 1
        g = J.T @ r
        H = J.T @ J
        pgn = _proj_grad_norm(g, x, lo, hi)
        if pgn < gtol:
            reason = "gtol"; break
        diag = np.clip(np.diag(H), 1e-12, None)   # Marquardt scaling (state scaling)
        stepped = False
        for _ in range(max_inner):
            A = H + lam * np.diag(diag)
            try:
                delta = np.linalg.solve(A, -g)
            except np.linalg.LinAlgError:
                delta, *_ = np.linalg.lstsq(A, -g, rcond=None)
            x_try = np.clip(x + delta, lo, hi)
            r_try = np.asarray(residual_fn(x_try)); nfev += 1
            cost_try = 0.5 * float(r_try @ r_try)
            if cost_try < cost:            # accept
                dstep = float(np.linalg.norm(x_try - x))
                dcost = cost - cost_try
                x, r, cost = x_try, r_try, cost_try
                lam = max(lam / nu, lam_min); lam_hist.append(lam)
                accepts += 1; stepped = True
                if dstep < xtol:
                    reason = "xtol"
                if dcost < ftol * (1.0 + cost):
                    reason = "ftol"
                break
            else:                          # reject: increase damping, retry
                lam = min(lam * nu, lam_max); lam_hist.append(lam)
                rejects += 1
                if lam >= lam_max:
                    reason = "lam_max"; stepped = False; break
        if reason in ("xtol", "ftol", "lam_max"):
            break
        if not stepped and lam >= lam_max:
            break
    J = np.asarray(jac_fn(x)); njev += 1
    g = J.T @ r
    return dict(x=x, cost=cost, gradnorm=_proj_grad_norm(g, x, lo, hi),
                nit=it + 1, nfev=nfev, njev=njev, reason=reason, lam=lam,
                accepts=accepts, rejects=rejects, runtime_s=round(time.time() - t0, 4))


def make_callables(jf):
    """JIT-compiled residual + analytic Jacobian for a JaxForward moment."""
    res = jax.jit(jf.residual)
    jac = jax.jit(jax.jacfwd(jf.residual))
    return (lambda x: np.asarray(res(jnp.asarray(x))),
            lambda x: np.asarray(jac(jnp.asarray(x))))
