"""Validation suite — increment 2. Tests 1-14 + regressions + timing.

Generalized contacts (PointContact/LineContact), synergy-manifold solver.
NO chord names, NO barre special case, NO manual poses. The 8 approved
chords remain regression fixtures.

Run:  PYTHONPATH=. python3 run_tests.py <out_dir>
"""

from __future__ import annotations
import os, sys, json
import numpy as np
import anatomy as A
import contacts as CN
import hand_solver as S
import diagnostics as D

OUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/solver2"
os.makedirs(OUT, exist_ok=True)
model = A.HandModel.default()


def P(s, fr, fg):
    return CN.PointContact(string=s, fret=fr, finger=fg)


def L(fret, lo, hi, fg="index"):
    return CN.LineContact(fret=fret, lo_string=lo, hi_string=hi, finger=fg)


# Contacts from GuitarVoicing (same numbers the fixtures used).
FIXTURES = {
    "G":  [P(4, 2, "index"), P(5, 3, "middle"), P(0, 3, "ring")],
    "C":  [P(4, 1, "index"), P(3, 2, "middle"), P(1, 3, "ring")],
    "D":  [P(3, 2, "index"), P(5, 2, "middle"), P(4, 3, "ring")],
    "Em": [P(1, 2, "middle"), P(2, 2, "ring")],
    "A":  [P(2, 2, "index"), P(3, 2, "middle"), P(4, 2, "ring")],
    "Am": [P(4, 1, "index"), P(2, 2, "middle"), P(3, 2, "ring")],
    "E":  [P(3, 1, "index"), P(1, 2, "middle"), P(2, 2, "ring")],
    "Dm": [P(5, 1, "index"), P(3, 2, "middle"), P(4, 3, "ring")],
}

report = {}
times = []


def record(name, result, contact_list):
    D.render(result, contact_list, name, f"{OUT}/{name}.png")
    times.append(result.solve_time_s)
    report[name] = dict(
        status=result.status,
        max_contact_mm=result.max_contact_mm,
        contact_accuracy=result.contact_accuracy_mm,
        joint_limit_violations=result.joint_limit_violations,
        collision_penetration_mm=result.collision_penetration_mm,
        board_penetration_mm=result.board_penetration_mm,
        thumb_behind_neck=bool(result.thumb_behind_neck),
        naturalness=result.naturalness,
        manifold_cost=round(result.costs.manifold_cost, 3),
        solve_time_s=result.solve_time_s,
        iterations=result.iterations,
        reasons=result.reasons,
    )
    r = report[name]
    print(f"[{name}] {r['status']} contact<={r['max_contact_mm']}mm "
          f"coll={r['collision_penetration_mm']} thumb={r['thumb_behind_neck']} "
          f"nat={r['naturalness']} t={r['solve_time_s']}s")
    if r["reasons"]:
        print("       ", r["reasons"])


# --- Regressions (tests 1-8 equivalents) ---
record("test1_single", S.solve(model, [P(3, 2, "middle")]), [P(3, 2, "middle")])
two = [P(2, 2, "index"), P(3, 2, "middle")]
record("test2_two", S.solve(model, two), two)
for name in ("G", "C", "D", "Em", "A", "Am", "E", "Dm"):
    record(f"fixture_{name}", S.solve(model, FIXTURES[name]), FIXTURES[name])

# prev-pose (G→C)
gres = S.solve(model, FIXTURES["G"])
cres = S.solve(model, FIXTURES["C"], prev_state=gres.state)
record("test6_G_then_C", cres, FIXTURES["C"])

# phrase 8 notes (prev-chained)
PHRASE = [(5, 3, "index"), (5, 5, "ring"), (4, 2, "index"), (4, 4, "ring"),
          (3, 2, "index"), (3, 4, "ring"), (2, 3, "middle"), (1, 3, "middle")]
prev = None
pok = 0
for i, (s, fr, fg) in enumerate(PHRASE):
    ct = [P(s, fr, fg)]
    res = S.solve(model, ct, prev_state=prev)
    record(f"test7_phrase_{i}", res, ct)
    pok += res.status == "OK"
    prev = res.state
report["test7_summary"] = dict(notes=8, ok=pok)

# impossible point contacts
imp = [P(0, 1, "pinky"), P(5, 12, "index")]
record("test8_impossible", S.solve(model, imp), imp)

# --- NEW increment-2 tests ---
# TEST 9 — F# E-shape barre (line contact index)
fsharp = [L(2, 0, 5, "index"), P(3, 3, "middle"), P(1, 4, "ring"), P(2, 4, "pinky")]
record("test9_Fsharp_barre", S.solve(model, fsharp), fsharp)

# TEST 10 — TRANSPOSED barre: same structural pattern, moved to fret 5 (A).
transp = [L(5, 0, 5, "index"), P(3, 6, "middle"), P(1, 7, "ring"), P(2, 7, "pinky")]
record("test10_transposed_barre", S.solve(model, transp), transp)

# TEST 11 — DIFFERENT barre family: A-shape (index barre fret 2, ring
# barres/points strings 1-3 at fret 4). Structurally different.
ashape = [L(2, 1, 4, "index"), P(3, 4, "ring"), P(2, 4, "ring"), P(1, 4, "middle")]
# express as index line + fingers on frets 4; distinct from E-shape.
ashape = [L(2, 1, 5, "index"), P(1, 4, "middle"), P(2, 4, "ring"), P(3, 4, "pinky")]
record("test11_Ashape_barre", S.solve(model, ashape), ashape)

# TEST 12 — PARTIAL barre: index across only the top 3 strings at fret 5.
partial = [L(5, 3, 5, "index"), P(2, 7, "ring"), P(1, 7, "middle")]
record("test12_partial_barre", S.solve(model, partial), partial)

# TEST 14 — IMPOSSIBLE line contact: index must barre ALL strings at
# fret 2 AND fret 9 at once (physically impossible span).
imp_line = [L(2, 0, 5, "index"), L(9, 0, 5, "middle")]
record("test14_impossible_line", S.solve(model, imp_line), imp_line)

# timing distribution
ts = sorted(times)
report["timing"] = dict(
    n=len(ts), median=round(ts[len(ts)//2], 2),
    p95=round(ts[int(len(ts)*0.95)], 2), worst=round(ts[-1], 2),
    total=round(sum(ts), 1))

with open(f"{OUT}/report.json", "w") as f:
    json.dump(report, f, indent=2)
ok = sum(1 for v in report.values() if isinstance(v, dict) and v.get("status") == "OK")
inf = [k for k, v in report.items() if isinstance(v, dict) and v.get("status") == "INFEASIBLE"]
print(f"\nOK {ok}  INFEASIBLE {inf}")
print("timing", report["timing"])
print("WROTE", f"{OUT}/report.json")
