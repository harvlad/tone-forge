"""Animation demonstrator — step 1: interpolate the ACTUAL solved trajectories.

Reads the committed naive + trajopt trajectories for one_shift_scale and builds
a time-sampled sequence of poses by LINEAR interpolation in 30-DoF planner state
space between consecutive solved knots. Writes an mpfb_render input.

INTERPOLATION (identical for both planners, documented for the record):
  - piecewise LINEAR (LERP) in planner state space: state(t) = (1-a)*knot_i +
    a*knot_{i+1}, a = (t - t_i)/(t_{i+1} - t_i), for t in [t_i, t_{i+1}].
  - before the first / after the last knot: hold that knot (the still frames).
  - NO easing, anticipation, overshoot, secondary motion, or per-planner
    smoothing. The only transform is straight-line blending of the actual
    solved states, so the animation exposes exactly what each planner produced.
  - META (metacarpal) DoF zeroed for rendering (inert in V1; keeps render==solver).
  - fingers_mm = FK of the interpolated state, so the render==solver assertion
    is against the pose actually drawn.

Timeline: notes at t=0,0.5,1.0,1.5 s (90 bpm); phrase held to 2.0 s; 12 fps;
0.5 s still head + 0.5 s still tail. Both planners share this exact timeline.
"""
from __future__ import annotations
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
from adapters import SolverAdapter

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(HERE, "results")
DEMO = "one_shift_scale"
FPS = 12
HEAD_S = 0.5
TAIL_S = 0.5
PHRASE_END_S = 2.0          # hold the last note to here


def knots_of(path):
    tj = json.load(open(path))["trajectories"][DEMO]
    return [(k["t"], np.asarray(k["mpfb_state"], float),
             k["meta"].get("contacts", [])) for k in tj["knots"]]


def lerp_state(knots, t):
    ts = [k[0] for k in knots]
    if t <= ts[0]:
        return knots[0][1].copy()
    if t >= ts[-1]:
        return knots[-1][1].copy()
    for i in range(len(ts) - 1):
        if ts[i] <= t <= ts[i + 1]:
            a = (t - ts[i]) / (ts[i + 1] - ts[i])
            return (1 - a) * knots[i][1] + a * knots[i + 1][1]
    return knots[-1][1].copy()


def zero_meta(state):
    s = state.copy(); s[26:30] = 0.0; return s


if __name__ == "__main__":
    solver = SolverAdapter()
    srcs = {"naive": os.path.join(RES, "movement_report.json"),
            "trajopt": os.path.join(RES, "trajopt_report.json")}
    knots = {p: knots_of(path) for p, path in srcs.items()}
    contacts = knots["naive"][0][2]        # phrase contacts (same schedule both)

    # shared timeline (frame times)
    n_head = int(round(HEAD_S * FPS))
    n_phrase = int(round(PHRASE_END_S * FPS))
    n_tail = int(round(TAIL_S * FPS))
    times = ([0.0] * n_head
             + [i / FPS for i in range(n_phrase + 1)]
             + [PHRASE_END_S] * n_tail)

    out = {"__phrase_camera__": dict(ortho=0.36)}
    track = {p: [] for p in knots}          # hand-root x per frame, for diagnostic
    for p in knots:
        for fi, t in enumerate(times):
            st = zero_meta(lerp_state(knots[p], t))
            fingers = solver.fk(st.tolist())["fingers"]
            out[f"{p}__{fi:03d}"] = dict(state=st.tolist(), contacts=contacts,
                                         fingers_mm=fingers, t=round(t, 3), planner=p)
            track[p].append(float(st[0]))    # state[0] = root x (metres)

    json.dump(out, open(os.path.join(RES, "anim_states.json"), "w"))
    # sidecar: caption metrics (ACTUAL, from the reports) + track + timeline.
    # naive SCORE comes from the M5-scored baseline (movement_report.json holds
    # the pre-M5 stub score of 1.0); metrics are identical either way.
    nv = json.load(open(os.path.join(RES, "baseline_naive.json")))["phrases"][DEMO]
    tj = json.load(open(srcs["trajopt"]))["phrases"][DEMO]
    def m(rep, name, key): return rep["metrics"][name][key]
    meta = dict(fps=FPS, n_frames=len(times), n_head=n_head, n_tail=n_tail,
                times=[round(t, 3) for t in times], track=track,
                caption=dict(
                    naive=dict(shifts=m(nv, "shift_count", "count"),
                               root_travel_mm=m(nv, "root_travel", "total_mm"),
                               fingertip_travel_mm=m(nv, "fingertip_travel", "total_mm"),
                               movement_score=nv["score"]),
                    trajopt=dict(shifts=m(tj, "shift_count", "count"),
                                 root_travel_mm=m(tj, "root_travel", "total_mm"),
                                 fingertip_travel_mm=m(tj, "fingertip_travel", "total_mm"),
                                 movement_score=tj["score"])))
    json.dump(meta, open(os.path.join(RES, "anim_meta.json"), "w"), indent=1)
    print(f"wrote anim_states.json ({len(times)} frames/planner) + anim_meta.json")
    print("caption:", json.dumps(meta["caption"]))
