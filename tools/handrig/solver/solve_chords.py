"""Solve guitar CHORD fingerings with the UNIFIED comfort/strain MPFB solver
(the natural-pose one), NOT the splayed Blender IK. Input = app-exported
chords.json {sym:{baseFret,notes:[{string,fret,finger}],barre?}}; string 0=lowE,
finger 1=index..4=pinky, absolute fret. Output = states JSON (mpfb_states2 schema).
Run: PYTHONPATH=.:.. python3 solve_chords.py <rig.json> <chords.json> <out.json> [sym,sym,...]
"""
import sys, json
import mpfb_solver as M
import contacts as CN

RIG, CHORDS, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
ONLY = set(sys.argv[4].split(",")) if len(sys.argv) > 4 else None
s = M.MPFBSolver(RIG)
FNAME = {1: "index", 2: "middle", 3: "ring", 4: "pinky"}
data = json.load(open(CHORDS))
out = {}
for sym, entry in data.items():
    if ONLY and sym not in ONLY:
        continue
    barre = entry.get("barre")
    cts, rc = [], []
    if barre:
        # LineContact isn't fully wired in this solver; pose the barre index as a
        # single point on the low string of the span (natural curl, not splayed).
        cts.append(CN.PointContact(string=barre["loString"], fret=barre["fret"], finger="index"))
        rc.append(dict(string=barre["loString"], fret=barre["fret"], finger="index"))
    for n in entry["notes"]:
        if barre and n["finger"] == 1:
            continue
        fg = FNAME[n["finger"]]
        cts.append(CN.PointContact(string=n["string"], fret=n["fret"], finger=fg))
        rc.append(dict(string=n["string"], fret=n["fret"], finger=fg))
    if not cts:
        print("skip", sym); continue
    r = s.solve(cts)
    r["contacts"] = rc
    out[sym] = r
    print(f"[{sym}] {r['status']} c={r.get('max_contact_mm')}mm coll={r.get('collision_mm')} t={r.get('solve_s')}s")
json.dump(out, open(OUT, "w"), indent=1)
print("WROTE", OUT, len(out))
