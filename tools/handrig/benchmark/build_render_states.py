"""M8 — assemble render-states for the visual gate.

Extracts the demo phrase's knot states from the naive and trajopt reports and
writes a states.json the frozen `mpfb_render` consumes ({name: {"state":[30]}}).

The META (metacarpal cupping) DoF is ZEROED: the committed rig has no metacarpal
bones so the solver treats cupping as inert (M1), but the Blender renderer WOULD
apply it to real MPFB metacarpal bones — zeroing keeps render==solver faithful.
"""
from __future__ import annotations
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from adapters import SolverAdapter

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(HERE, "results")
DEMO = "one_shift_scale"


def knots_of(report_path, pid):
    rep = json.load(open(report_path))
    return rep["trajectories"][pid]["knots"]


def zero_meta(state):
    s = list(state)
    s[26:30] = [0.0, 0.0, 0.0, 0.0]      # inert cupping (M1)
    return s


if __name__ == "__main__":
    solver = SolverAdapter()
    out = {}
    srcs = {"naive": os.path.join(RES, "movement_report.json"),
            "trajopt": os.path.join(RES, "trajopt_report.json")}
    for planner, path in srcs.items():
        for i, k in enumerate(knots_of(path, DEMO)):
            state = zero_meta(k["mpfb_state"])
            # fingers_mm = solver FK of the (meta-zeroed) state, for the
            # renderer's render==solver assertion.
            fingers_mm = solver.fk(state)["fingers"]
            out[f"{DEMO}_{planner}_{i}"] = dict(state=state,
                                               contacts=k["meta"].get("contacts", []),
                                               fingers_mm=fingers_mm,
                                               t=k["t"], planner=planner)
    # Fixed phrase camera so the hand's neck position is comparable across
    # frames (the shift is only visible with a static viewpoint).
    out["__phrase_camera__"] = dict(ortho=0.36)
    dst = os.path.join(RES, "render_states.json")
    json.dump(out, open(dst, "w"), indent=1)
    print(f"wrote {dst}  ({len(out)} states: {sorted(out)})")
