"""Solve the fixture matrix with the biomechanical solver and export
joint angles + metrics to JSON for the Blender/MPFB bridge. NO chord
names reach the solver — only string/fret/finger contacts.

Run: PYTHONPATH=. python3 export_poses.py <out.json>
"""
import sys, json
import numpy as np
import anatomy as A
import contacts as CN
import hand_solver as S

OUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/poses.json"
model = A.HandModel.default()


def P(s, fr, fg):
    return CN.PointContact(string=s, fret=fr, finger=fg)


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

# 8-note phrase (prev-chained), no chord names.
PHRASE = [(5, 3, "index"), (5, 5, "ring"), (4, 2, "index"), (4, 4, "ring"),
          (3, 2, "index"), (3, 4, "ring"), (2, 3, "middle"), (1, 3, "middle")]


def pose_dict(r, contact_list):
    st = r.state
    return dict(
        status=r.status,
        contacts=[dict(string=c.string, fret=c.fret, finger=c.finger)
                  for c in contact_list],
        wrist_pos=[float(v) for v in st.wrist_pos],
        wrist_rot=[float(v) for v in st.wrist_rot],
        fingers={f: [float(v) for v in st.finger[f]] for f in A.FINGERS},
        thumb=[float(v) for v in st.thumb],
        metrics=dict(
            contact_mm=r.max_contact_mm,
            collision_mm=r.collision_penetration_mm,
            board_mm=r.board_penetration_mm,
            joint_viol=len(r.joint_limit_violations),
            thumb_behind=bool(r.thumb_behind_neck),
            naturalness=r.naturalness,
            manifold=round(r.costs.manifold_cost, 3),
            strain=round(r.costs.strain_cost, 3),
            splay=round(r.costs.splay_cost, 3),
            move=round(r.costs.previous_pose_movement, 3),
            solve_s=r.solve_time_s,
            mcp_deg={f: round(np.degrees(st.finger[f][0]), 1) for f in A.FINGERS},
            pip_deg={f: round(np.degrees(st.finger[f][2]), 1) for f in A.FINGERS},
            dip_deg={f: round(np.degrees(st.finger[f][3]), 1) for f in A.FINGERS},
            abd_deg={f: round(np.degrees(st.finger[f][1]), 1) for f in A.FINGERS},
        ),
    )


out = {}
for name, ct in FIX.items():
    r = S.solve(model, ct)
    out[name] = pose_dict(r, ct)
    m = out[name]["metrics"]
    print(f"[{name}] {r.status} c={m['contact_mm']} nat={m['naturalness']} "
          f"manifold={m['manifold']} abd={list(m['abd_deg'].values())}")

# phrase, prev-chained
prev = None
for i, (s, fr, fg) in enumerate(PHRASE):
    ct=[P(s, fr, fg)]
    r = S.solve(model, ct, prev_state=prev)
    out[f"phrase_{i}"] = pose_dict(r, ct)
    prev = r.state
    print(f"[phrase_{i}] {r.status} c={r.max_contact_mm} move={out[f'phrase_{i}']['metrics']['move']}")

with open(OUT, "w") as f:
    json.dump(out, f, indent=1)
print("WROTE", OUT)
