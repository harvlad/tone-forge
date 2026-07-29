"""Validation suite — Tests 1-8 from the increment brief.

NO chord-specific logic anywhere. Chords are just sets of contacts. The
approved fixtures are REGRESSION targets, not training truth. Metrics are
reported separately (never one collapsed score).

Run:  PYTHONPATH=. python3 run_tests.py <out_dir>
"""

from __future__ import annotations
import os, sys, json
import numpy as np
import anatomy as A
import guitar as G
import hand_solver as S
import diagnostics as D

OUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/solver_tests"
os.makedirs(OUT, exist_ok=True)
model = A.HandModel.default()


def C(string, fret, finger):
    return G.Contact(string=string, fret=fret, finger=finger)


# Contacts from the app's GuitarVoicing export (chords.json). These are
# the SAME numbers the approved MPFB fixtures used — the solver gets only
# string/fret/finger, never the chord name or a pose.
FIXTURES = {
    "G":  [C(4, 2, "index"), C(5, 3, "middle"), C(0, 3, "ring")],
    "C":  [C(4, 1, "index"), C(3, 2, "middle"), C(1, 3, "ring")],
    "D":  [C(3, 2, "index"), C(5, 2, "middle"), C(4, 3, "ring")],
    "Em": [C(1, 2, "middle"), C(2, 2, "ring")],
    "A":  [C(2, 2, "index"), C(3, 2, "middle"), C(4, 2, "ring")],
    "Am": [C(4, 1, "index"), C(2, 2, "middle"), C(3, 2, "ring")],
    "E":  [C(3, 1, "index"), C(1, 2, "middle"), C(2, 2, "ring")],
    "Dm": [C(5, 1, "index"), C(3, 2, "middle"), C(4, 3, "ring")],
}

report = {}


def record(name, result, contacts, barre=None):
    D.render(result, contacts, barre, name, f"{OUT}/{name}.png")
    report[name] = dict(
        status=result.status,
        max_contact_mm=round(result.max_contact_mm, 2),
        contact_accuracy=({k: round(v, 2)
                           for k, v in result.contact_accuracy_mm.items()}),
        joint_limit_violations=result.joint_limit_violations,
        collision_penetration_mm=round(result.collision_penetration_mm, 2),
        board_penetration_mm=round(result.board_penetration_mm, 2),
        thumb_behind_neck=bool(result.thumb_behind_neck),
        naturalness=round(result.naturalness, 3),
        costs={k: round(v, 3) for k, v in result.costs.as_dict().items()},
        reasons=getattr(result, "reasons", []),
    )
    r = report[name]
    print(f"[{name}] {r['status']}  contact≤{r['max_contact_mm']}mm  "
          f"coll={r['collision_penetration_mm']}  board={r['board_penetration_mm']}  "
          f"thumb_behind={r['thumb_behind_neck']}  nat={r['naturalness']}")
    if r["reasons"]:
        print("        reasons:", r["reasons"])


# TEST 1 — single note
record("test1_single", S.solve(model, [C(3, 2, "middle")]),
       [C(3, 2, "middle")])

# TEST 2 — two-finger shape
two = [C(2, 2, "index"), C(3, 2, "middle")]
record("test2_two", S.solve(model, two), two)

# TEST 3/4 + all fixtures — open chords, NO chord logic
for name in ("G", "C", "D", "Em", "A", "Am", "E", "Dm"):
    record(f"fixture_{name}", S.solve(model, FIXTURES[name]), FIXTURES[name])

# TEST 5 — F# barre (canonical E-shape), NO barre special case beyond
# expressing the physical LINE contact of the index.
fsharp = [C(3, 3, "middle"), C(1, 4, "ring"), C(2, 4, "pinky")]
barre = G.BarreContact(fret=2, lo_string=0, hi_string=5, finger="index")
record("test5_Fsharp_barre", S.solve(model, fsharp, barre=barre),
       fsharp, barre)

# TEST 6 — previous-pose influence: solve C given G as previous.
gres = S.solve(model, FIXTURES["G"])
cres = S.solve(model, FIXTURES["C"], prev_state=gres.state)
record("test6_G_then_C", cres, FIXTURES["C"])
report["test6_G_then_C"]["movement_from_prev"] = round(
    cres.costs.previous_pose_movement, 3)
cres_cold = S.solve(model, FIXTURES["C"])
report["test6_G_then_C"]["movement_cold_baseline"] = round(
    float(np.sum((cres_cold.state.to_vector() - gres.state.to_vector()) ** 2)),
    3)

# TEST 7 — arbitrary 8-note phrase (NO chord names). time/pitch given for
# provenance; solver sees only string/fret/finger per note.
PHRASE = [
    # (t, string, fret, finger)
    (0.0, 5, 3, "index"),
    (0.5, 5, 5, "ring"),
    (1.0, 4, 2, "index"),
    (1.5, 4, 4, "ring"),
    (2.0, 3, 2, "index"),
    (2.5, 3, 4, "ring"),
    (3.0, 2, 3, "middle"),
    (3.5, 1, 3, "middle"),
]
prev = None
phrase_ok = 0
for i, (t, s, fr, fg) in enumerate(PHRASE):
    ct = [C(s, fr, fg)]
    res = S.solve(model, ct, prev_state=prev)
    record(f"test7_phrase_{i}_{fg}_{s}-{fr}", res, ct)
    if res.status == "OK":
        phrase_ok += 1
    prev = res.state
report["test7_summary"] = dict(notes=len(PHRASE), ok=phrase_ok)

# TEST 8 — impossible fingering: pinky on fret 1 low-E AND index on
# fret 12 high-e simultaneously (span far beyond a hand). Expect
# INFEASIBLE, NOT a contorted pose.
impossible = [C(0, 1, "pinky"), C(5, 12, "index")]
ires = S.solve(model, impossible)
record("test8_impossible", ires, impossible)
report["test8_impossible"]["EXPECTED"] = "INFEASIBLE"

with open(f"{OUT}/report.json", "w") as f:
    json.dump(report, f, indent=2)
print("\nWROTE", f"{OUT}/report.json")
