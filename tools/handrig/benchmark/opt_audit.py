"""OPTIMIZATION HEALTH AUDIT (read-only). Reconstructs the FROZEN TrajoptPlanner
objective exactly (does not modify any frozen file) and measures gradient quality,
conditioning, convergence, reproducibility, null-space. Focus: sustained_anchor
(the unfixed multi-contact regression) + string_crossing (fixed by resist) as contrast."""
import sys, numpy as np, time
sys.path.insert(0,"/Users/mattharvey/Sites/tone-forge/tools/handrig/benchmark")
from loader import load_phrase, load_reference, load_suite
from adapters import SolverAdapter
from planners import NaivePlanner, TrajoptPlanner, group_moments
from scipy.optimize import minimize, approx_fprime
np.set_printoptions(precision=4, suppress=True)
HERE="/Users/mattharvey/Sites/tone-forge/tools/handrig/benchmark"
solver=SolverAdapter()
W_ROOT, W_SMOOTH = TrajoptPlanner.W_ROOT, TrajoptPlanner.W_SMOOTH

def build(pid):
    ph=load_phrase(HERE,pid); moments=group_moments(ph.events)
    specs=[[dict(string=e.string,fret=e.fret,finger=e.finger,articulation=e.articulation) for e in evs] for _,evs in moments]
    N=len(moments)
    naive=NaivePlanner().plan(ph,{}, solver,{})
    init=[np.asarray(k.mpfb_state,float) for k in naive.knots]; S=init[0].shape[0]
    centre=np.mean([solver.root_of(s) for s in init],axis=0)
    los,his=zip(*[solver.knot_bounds(centre,root_pad=0.15) for _ in range(N)])
    lo=np.concatenate(los); hi=np.concatenate(his)
    x0=np.clip(np.concatenate(init),lo,hi)
    def obj(x):
        st=[x[i*S:(i+1)*S] for i in range(N)]
        tot=sum(solver.evaluate_state(st[i],specs[i]) for i in range(N))
        for i in range(1,N):
            dr=st[i][:3]-st[i-1][:3]; dq=st[i][6:]-st[i-1][6:]
            tot+=W_ROOT*float(dr@dr)+W_SMOOTH*float(dq@dq)
        return tot
    return dict(pid=pid,obj=obj,x0=x0,lo=lo,hi=hi,S=S,N=N,specs=specs,init=init)

def solve(P, maxiter=600, ftol=1e-9):
    return minimize(P["obj"],P["x0"],method="L-BFGS-B",bounds=list(zip(P["lo"],P["hi"])),
                    options=dict(maxiter=maxiter,ftol=ftol))

for pid in ["sustained_anchor","string_crossing","one_shift_scale"]:
    print("="*70); print(f"PHRASE: {pid}"); print("="*70)
    P=build(pid); S,N=P["S"],P["N"]
    t0=time.time(); res=solve(P); dt=time.time()-t0
    x=res.x
    print(f"[convergence] status={res.status} '{res.message}' nit={res.nit} nfev={res.nfev} f={res.fun:.6f} {dt:.1f}s")
    # tighter/longer -> did it stop prematurely?
    res2=solve(P,maxiter=5000,ftol=1e-12)
    dfun=res.fun-res2.fun; dx=np.linalg.norm(res.x-res2.x)
    print(f"[premature?] longer run: f {res.fun:.6f}->{res2.fun:.6f} (Δf={dfun:.6e}) ||Δx||={dx:.6e}")
    # gradient quality: scipy default FD step vs central-diff, at solution
    g_fwd=approx_fprime(x, P["obj"], 1.49e-8)              # scipy L-BFGS-B default 2-point step
    eps=1e-6
    g_cen=np.array([ (P["obj"](x+eps*ei)-P["obj"](x-eps*ei))/(2*eps) for ei in np.eye(len(x)) ])
    gerr=np.linalg.norm(g_fwd-g_cen)/(np.linalg.norm(g_cen)+1e-12)
    print(f"[gradient] ||g_fwd - g_central||/||g|| = {gerr:.3e}  (FD step mismatch; large => noisy/non-smooth)")
    print(f"           ||g_central|| at 'solution' = {np.linalg.norm(g_cen):.3e} (should be ~0 if truly converged)")
    # conditioning: finite-diff Hessian, split root-translation vs joint blocks
    n=len(x); H=np.zeros((n,n)); h=1e-5; f0=P["obj"](x)
    # cheap diagonal + a few cross terms is enough for scale mismatch; do full for small n
    if n<=120:
        for i in range(n):
            for j in range(i,n):
                fpp=P["obj"](x+h*(np.eye(n)[i]+np.eye(n)[j]))
                fp_i=P["obj"](x+h*np.eye(n)[i]); fp_j=P["obj"](x+h*np.eye(n)[j])
                H[i,j]=H[j,i]=(fpp-fp_i-fp_j+f0)/(h*h)
        w=np.linalg.eigvalsh(H); w=w[np.abs(w)>1e-9]
        cond=abs(w).max()/abs(w).min() if len(w) else float('inf')
        # scale mismatch: diagonal Hessian magnitude, root-translation dims vs joint dims
        diag=np.diag(H); root_idx=np.concatenate([[i*S,i*S+1,i*S+2] for i in range(N)])
        joint_idx=np.array([k for k in range(n) if k not in set(root_idx)])
        print(f"[conditioning] cond(H)~{cond:.2e}  eig range [{w.min():.3e},{w.max():.3e}]")
        print(f"[scale] |diagH| root-trans dims median={np.median(np.abs(diag[root_idx])):.3e}  "
              f"joint dims median={np.median(np.abs(diag[joint_idx])):.3e}  "
              f"ratio={np.median(np.abs(diag[root_idx]))/(np.median(np.abs(diag[joint_idx]))+1e-12):.2e}")
    # null-space probe: perturb root along-neck (x-dim) of the moving knot, re-eval objective
    x_pert=x.copy(); base=P["obj"](x)
    curve=[]
    for d in [-0.010,-0.005,-0.002,0.0,0.002,0.005,0.010]:  # meters
        xp=x.copy()
        for i in range(N): xp[i*S]=x[i*S]+d   # shift ALL knot roots along-neck together
        curve.append((d*1000, P["obj"](xp)-base))
    print("[null-space] rigid along-neck shift of whole trajectory (mm -> Δobj):")
    print("            "+"  ".join(f"{mm:+.0f}:{dv:+.3f}" for mm,dv in curve))
    # reproducibility
    r_a=solve(P); r_b=solve(P)
    print(f"[reproducible] identical re-solve: ||Δx||={np.linalg.norm(r_a.x-r_b.x):.2e} Δf={abs(r_a.fun-r_b.fun):.2e}")
    print()
