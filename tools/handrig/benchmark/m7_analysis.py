"""M7 deep analysis: per-event shift table, note-2 classification, failure-mode falsification.
Reads frozen reports; uses SolverAdapter for FK root/wrist/fingertip positions.
Does NOT modify anything."""
import json, sys, math
sys.path.insert(0,"/Users/mattharvey/Sites/tone-forge/tools/handrig/benchmark")
from adapters import SolverAdapter
import numpy as np

A=SolverAdapter()
BM="/Users/mattharvey/Sites/tone-forge/tools/handrig/benchmark"
naive=json.load(open(f"{BM}/results/movement_report.json"))
traj=json.load(open(f"{BM}/results/trajopt_report.json"))
PHRASES=["single","repeated_note","string_crossing","one_shift_scale","sustained_anchor"]

def root(state):
    r=A.root_of(np.array(state)); return np.array(r)
def dist(a,b): return float(np.linalg.norm(np.array(a)-np.array(b))*1000)  # m->mm

print("="*70); print("ITEM 3+4: PER-EVENT SHIFT ANALYSIS (one_shift_scale)"); print("="*70)
for plan,rep in (("NAIVE",naive),("TRAJOPT",traj)):
    t=rep["trajectories"]["one_shift_scale"]; knots=t["knots"]; sched=t["contact_schedule"]
    pm=rep["phrases"]["one_shift_scale"]["metrics"]["contact_fidelity"]["per_moment"]
    print(f"\n--- {plan} ---")
    prev_r=None
    for i,(k,c) in enumerate(zip(knots,sched)):
        r=root(k["mpfb_state"])
        disp=dist(prev_r,r) if prev_r is not None else 0.0
        shift="SHIFT" if disp>20 else "-"
        cm=pm[i]["contact_mm"]
        print(f"  ev{i} s{c['string']}f{c['fret']:>2} {c['finger']:<6} rootΔ={disp:6.2f}mm {shift:5} contact={cm:.3f}mm")
        prev_r=r
    m=rep["phrases"]["one_shift_scale"]["metrics"]
    print(f"  => shift_count={m['shift_count']['count']} deltas={m['shift_count']['deltas_mm']} "
          f"root_travel={m['root_travel']['total_mm']:.1f}mm tip_travel={m['fingertip_travel']['total_mm']:.1f}mm")

print("\n"+"="*70); print("ITEM 4: NOTE-2 (event index 2, s3f9) CONTACT ERROR"); print("="*70)
for plan,rep in (("NAIVE",naive),("TRAJOPT",traj)):
    pm=rep["phrases"]["one_shift_scale"]["metrics"]["contact_fidelity"]["per_moment"]
    print(f"  {plan}: note2 contact={pm[2]['contact_mm']:.3f}mm  (all: {[x['contact_mm'] for x in pm]})")

print("\n"+"="*70); print("ITEM 6: PHRASE-BY-PHRASE (naive vs trajopt)"); print("="*70)
for ph in PHRASES:
    ns=naive["phrases"][ph]["score"]; ts=traj["phrases"][ph]["score"]
    nsc=ns["movement_score"] if isinstance(ns,dict) else ns
    tsc=ts["movement_score"] if isinstance(ts,dict) else ts
    d=tsc-nsc
    verdict="TRAJOPT BETTER" if d>0.001 else ("WORSE" if d<-0.001 else "SAME")
    nf=[f["tag"] for f in naive["phrases"][ph]["failures"]]
    tf=[f["tag"] for f in traj["phrases"][ph]["failures"]]
    print(f"  {ph:16} naive={nsc:.3f} trajopt={tsc:.3f} Δ={d:+.3f} {verdict:15} naiveFail={nf} trajFail={tf}")

print("\n"+"="*70); print("ITEM 7: FAILURE-MODE FALSIFICATION (try to break trajopt)"); print("="*70)
for ph in PHRASES:
    tt=traj["trajectories"][ph]; knots=tt["knots"]
    roots=[root(k["mpfb_state"]) for k in knots]
    deltas=[dist(roots[i],roots[i+1]) for i in range(len(roots)-1)]
    m=traj["phrases"][ph]["metrics"]
    tip=m["fingertip_travel"]["total_mm"]; rt=m["root_travel"]["total_mm"]
    # churn: sum of direction reversals along neck
    print(f"  {ph:16} rootΔ/event={[round(d,1) for d in deltas]} tip={tip:.0f} root={rt:.1f} shifts={m['shift_count']['count']}")
