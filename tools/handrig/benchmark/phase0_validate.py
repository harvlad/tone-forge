"""Phase 0 validation: math identity + block-conditioning before/after scaling.
Read-only: reconstructs the FROZEN trajopt objective (as opt_audit did), does NOT
modify the planner. Measures the root-vs-joint diagonal-Hessian block ratio in
ORIGINAL vs SCALED coordinates on all 5 frozen phrases."""
import sys, numpy as np
sys.path.insert(0,"/Users/mattharvey/Sites/tone-forge/tools/handrig/benchmark")
from loader import load_phrase, load_suite
from adapters import SolverAdapter
from planners import NaivePlanner, TrajoptPlanner, group_moments
import opt_scaling as OS
HERE="/Users/mattharvey/Sites/tone-forge/tools/handrig/benchmark"
solver=SolverAdapter(); W_ROOT,W_SMOOTH=TrajoptPlanner.W_ROOT,TrajoptPlanner.W_SMOOTH
S=OS.STATE_N

def build(pid):
    ph=load_phrase(HERE,pid); mo=group_moments(ph.events)
    specs=[[dict(string=e.string,fret=e.fret,finger=e.finger,articulation=e.articulation) for e in evs] for _,evs in mo]
    N=len(mo); naive=NaivePlanner().plan(ph,{},solver,{})
    init=[np.asarray(k.mpfb_state,float) for k in naive.knots]
    centre=np.mean([solver.root_of(s) for s in init],axis=0)
    los,his=zip(*[solver.knot_bounds(centre,root_pad=0.15) for _ in range(N)])
    lo=np.concatenate(los);hi=np.concatenate(his);x0=np.clip(np.concatenate(init),lo,hi)
    def obj(x):
        st=[x[i*S:(i+1)*S] for i in range(N)]
        t=sum(solver.evaluate_state(st[i],specs[i]) for i in range(N))
        for i in range(1,N):
            dr=st[i][:3]-st[i-1][:3];dq=st[i][6:]-st[i-1][6:]
            t+=W_ROOT*float(dr@dr)+W_SMOOTH*float(dq@dq)
        return t
    return obj,x0,lo,hi,N

def diag_hess(obj,x,h=1e-5):
    n=len(x);f0=obj(x);d=np.zeros(n)
    for i in range(n):
        e=np.zeros(n);e[i]=h
        d[i]=(obj(x+e)-2*f0+obj(x-e))/(h*h)
    return d

def block_ratio(diag,N):
    root=np.concatenate([[i*S,i*S+1,i*S+2] for i in range(N)]).astype(int)
    joint=np.array([k for k in range(len(diag)) if k not in set(root.tolist())])
    return np.median(np.abs(diag[root])), np.median(np.abs(diag[joint]))

print("=== math identity ==="); OS._selftest()
print("\n=== objective preserved on real trajopt objective (random x) ===")
for pid in ["one_shift_scale"]:
    obj,x0,lo,hi,N=build(pid); w=OS.scale_weights(N)
    rng=np.random.default_rng(1)
    for _ in range(3):
        x=np.clip(x0+0.02*rng.standard_normal(len(x0)),lo,hi)
        oy=OS.make_scaled_objective(obj,w); y=OS.to_scaled(x,w)
        print(f"  {pid}: |obj_scaled(y) - obj(x)| = {abs(oy(y)-obj(x)):.2e}")

print("\n=== block-conditioning before/after scaling (root-trans vs joint diag-Hessian) ===")
print(f"{'phrase':16} {'L(mm)':>6} {'root/joint ORIG':>18} {'root/joint SCALED':>18}")
for pid in ["single","repeated_note","string_crossing","one_shift_scale","sustained_anchor"]:
    obj,x0,lo,hi,N=build(pid)
    if N<2:  # single: no cross-knot; still measure
        pass
    w=OS.scale_weights(N)
    # original diag Hessian at x0
    dO=diag_hess(obj,x0); rO,jO=block_ratio(dO,N); ratO=rO/(jO+1e-12)
    # scaled: objective on y, diag Hessian at y0=to_scaled(x0)
    oy=OS.make_scaled_objective(obj,w); y0=OS.to_scaled(x0,w)
    dS=diag_hess(oy,y0); rS,jS=block_ratio(dS,N); ratS=rS/(jS+1e-12)
    print(f"{pid:16} {OS.DEFAULT_L*1000:6.1f} {ratO:18.2e} {ratS:18.2e}")
