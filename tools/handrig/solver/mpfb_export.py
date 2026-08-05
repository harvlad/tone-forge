"""Solve the fixture matrix with the UNIFIED MPFB solver; write states +
render-space metrics to JSON. State is MPFB joint angles + root xform, so
the render reproduces it exactly (FK validated 0.0002 mm).

Run: PYTHONPATH=.:.. python3 mpfb_export.py <rig.json> <out.json>
"""
import sys, json
import mpfb_solver as M
import contacts as CN

RIG = sys.argv[1]
OUT = sys.argv[2]
s = M.MPFBSolver(RIG)


def P(st, fr, fg):
    return CN.PointContact(string=st, fret=fr, finger=fg)


FIX = {
    "single": [P(3, 2, "middle")],
    "two":    [P(2, 2, "index"), P(3, 3, "middle")],
    "D":      [P(3, 2, "index"), P(5, 2, "middle"), P(4, 3, "ring")],
    "G":      [P(4, 2, "index"), P(5, 3, "middle"), P(0, 3, "ring")],
    "C":      [P(4, 1, "index"), P(3, 2, "middle"), P(1, 3, "ring")],
    "three":  [P(1, 5, "index"), P(2, 6, "middle"), P(3, 7, "ring")],
    "wide":   [P(0, 2, "index"), P(4, 5, "pinky")],
    "impossible": [P(0, 1, "pinky"), P(5, 12, "index")],
}
PHRASE = [(5, 3, "index"), (5, 5, "ring"), (4, 2, "index"), (4, 4, "ring"),
          (3, 2, "index"), (3, 4, "ring"), (2, 3, "middle"), (1, 3, "middle")]

out = {}
for name, ct in FIX.items():
    r = s.solve(ct)
    r["contacts"] = [dict(string=c.string, fret=c.fret, finger=c.finger) for c in ct]
    out[name] = r
    print(f"[{name}] {r['status']} c={r['max_contact_mm']}mm coll={r['collision_mm']} "
          f"thumb={r['thumb_behind']} t={r['solve_s']}s")

prev = None
import numpy as np
for i, (st, fr, fg) in enumerate(PHRASE):
    ct = [P(st, fr, fg)]
    pv = np.array(prev) if prev is not None else None
    r = s.solve(ct, prev=pv)
    r["contacts"] = [dict(string=st, fret=fr, finger=fg)]
    out[f"phrase_{i}"] = r
    prev = r["state"]
    print(f"[phrase_{i}] {r['status']} c={r['max_contact_mm']}mm")

json.dump(out, open(OUT, "w"), indent=1)
print("WROTE", OUT)
