"""M2 — report generator + the single end-to-end command.

Runs the vertical slice: load phrase + reference -> naive trajectory (solve) ->
evaluate (one hard gate) -> write movement_report.json with full repro metadata.

Reproducibility contract: the written JSON is BYTE-IDENTICAL across re-runs.
The solver is deterministic (fixed restart perturbations, no RNG), so states
are stable. Volatile fields (wall-clock timestamp, runtime) are printed to
stdout, NEVER written to the report — that is what keeps re-runs identical.
config_hash pins the inputs that DO affect the result.

Run: PYTHONPATH=... python3 report.py [phrase_id]   # default: single
"""
from __future__ import annotations
import os, sys, json, hashlib, platform

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from loader import load_suite, load_phrase, load_reference
from adapters import SolverAdapter, _traj_json
from planners import get_planner
import evaluate as EV


def _dep_versions():
    v = {}
    for mod in ("numpy", "scipy"):
        try:
            v[mod] = __import__(mod).__version__
        except Exception:
            v[mod] = "n/a"
    v["python"] = platform.python_version()
    # Render deps are exercised only in M8; recorded as not-invoked here.
    v["blender"] = "render-only (M8)"
    v["mpfb"] = "render-only (M8)"
    return v


def _config_hash(config, benchmark_version, metric_version, solver_version, planner):
    """Hash only the inputs that change the result. Excludes wall-clock.
    Planner is a first-class input (different planners => different result)."""
    canon = json.dumps(dict(config=config, benchmark_version=benchmark_version,
                            metric_version=metric_version,
                            solver_version=solver_version, planner=planner),
                       sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode()).hexdigest()[:16]


def build_report(phrase_id, planner_name="naive", config=None):
    config = config or {"restarts": 5}
    suite = load_suite(HERE)
    phrase = load_phrase(HERE, phrase_id)
    reference = load_reference(HERE, phrase_id)
    solver = SolverAdapter()

    planner = get_planner(planner_name)
    traj = planner.plan(phrase, reference, solver, config)
    result = EV.evaluate_trajectory(traj, phrase, reference, solver,
                                    weights=suite.weights)

    meta = dict(
        benchmark_version=suite.version,
        metric_version=suite.metric_version,
        planner=planner_name,
        planner_version=solver.version(),   # planners are in-repo; == repo commit
        solver_version=solver.version(),
        seed=0,
        deps=_dep_versions(),
        machine=dict(os=platform.system(), release=platform.release(),
                     cpu=platform.machine(), py=platform.python_version()),
        config=config,
        cc0_assets=dict(mpfb_rig="unification/mpfb_rig.json"),
    )
    meta["config_hash"] = _config_hash(config, suite.version,
                                       suite.metric_version, solver.version(),
                                       planner_name)

    return dict(metadata=meta, phrases={phrase_id: result},
                trajectories={phrase_id: _traj_json(traj)})


def build_suite_report(planner_name="naive", config=None):
    """Run every phrase in the suite under one planner into one report."""
    suite = load_suite(HERE)
    merged = None
    for pid in suite.phrases:
        rep = build_report(pid, planner_name=planner_name, config=config)
        if merged is None:
            merged = rep
        else:
            merged["phrases"].update(rep["phrases"])
            merged["trajectories"].update(rep["trajectories"])
    return merged


def diff_reports(baseline, current):
    """Regression comparator: per-phrase Movement Score delta vs a pinned
    baseline. A phrase REGRESSED if its score dropped (beyond a tiny epsilon)."""
    EPS = 1e-6
    rows = []
    for pid, cur in current["phrases"].items():
        base = baseline["phrases"].get(pid)
        b = base["score"] if base else None
        c = cur["score"]
        delta = None if b is None else round(c - b, 6)
        rows.append(dict(phrase=pid, baseline=b, current=c, delta=delta,
                         regressed=bool(delta is not None and delta < -EPS),
                         improved=bool(delta is not None and delta > EPS)))
    any_reg = any(r["regressed"] for r in rows)
    return dict(rows=rows, any_regressed=any_reg,
                baseline_planner=baseline["metadata"]["planner"],
                current_planner=current["metadata"]["planner"])


def write_report(report, path):
    # sort_keys + fixed separators => byte-identical for identical content.
    with open(path, "w") as f:
        json.dump(report, f, indent=1, sort_keys=True)
        f.write("\n")


def print_report(rep):
    for pid, r in rep["phrases"].items():
        gate = "PASS" if r["hard_gate_pass"] else "FAIL"
        tags = ",".join(f["tag"] for f in r["failures"]) or "-"
        print(f"{pid:16s} gate={gate} score={r['score']:.3f} "
              f"(T2={r['scoring']['tier2_score']} T3={r['scoring']['tier3_score']}) "
              f"failures=[{tags}]")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("phrase", nargs="?", default="ALL",
                    help="phrase id, or ALL for the whole suite (default)")
    ap.add_argument("--planner", default="naive")
    ap.add_argument("--out", default="movement_report.json")
    ap.add_argument("--pin", metavar="PATH",
                    help="also write the report to PATH as a pinned baseline")
    ap.add_argument("--diff", metavar="BASELINE",
                    help="diff this run's scores against a pinned baseline report")
    a = ap.parse_args()
    if a.phrase == "ALL":
        rep = build_suite_report(planner_name=a.planner)
    else:
        rep = build_report(a.phrase, planner_name=a.planner)
    os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
    out = os.path.join(HERE, "results", a.out)
    write_report(rep, out)
    print_report(rep)
    print(f"config_hash={rep['metadata']['config_hash']}  wrote {out}")
    if a.pin:
        write_report(rep, a.pin)
        print(f"pinned baseline -> {a.pin}")
    if a.diff:
        base = json.load(open(a.diff))
        d = diff_reports(base, rep)
        print(f"\nDIFF {d['current_planner']} vs baseline {d['baseline_planner']}:")
        for r in d["rows"]:
            flag = "REGRESSED" if r["regressed"] else ("improved" if r["improved"] else "same")
            print(f"  {r['phrase']:16s} {r['baseline']} -> {r['current']} "
                  f"(delta {r['delta']}) {flag}")
        print(f"any_regressed={d['any_regressed']}")
