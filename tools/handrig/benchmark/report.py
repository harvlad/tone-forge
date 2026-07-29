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
from adapters import SolverAdapter, naive_trajectory, _traj_json
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


def _config_hash(config, benchmark_version, metric_version, solver_version):
    """Hash only the inputs that change the result. Excludes wall-clock."""
    canon = json.dumps(dict(config=config, benchmark_version=benchmark_version,
                            metric_version=metric_version,
                            solver_version=solver_version),
                       sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode()).hexdigest()[:16]


def build_report(phrase_id, config=None):
    config = config or {"restarts": 5, "planner": "naive"}
    suite = load_suite(HERE)
    phrase = load_phrase(HERE, phrase_id)
    reference = load_reference(HERE, phrase_id)
    solver = SolverAdapter()

    traj = naive_trajectory(phrase, solver, config)
    result = EV.evaluate_trajectory(traj, phrase, reference, solver)

    meta = dict(
        benchmark_version=suite.version,
        metric_version=suite.metric_version,
        planner="naive",
        planner_version=solver.version(),   # planner is inline; == repo commit
        solver_version=solver.version(),
        seed=0,
        deps=_dep_versions(),
        machine=dict(os=platform.system(), release=platform.release(),
                     cpu=platform.machine(), py=platform.python_version()),
        config=config,
        cc0_assets=dict(mpfb_rig="unification/mpfb_rig.json"),
    )
    meta["config_hash"] = _config_hash(config, suite.version,
                                       suite.metric_version, solver.version())

    return dict(metadata=meta, phrases={phrase_id: result},
                trajectories={phrase_id: _traj_json(traj)})


def write_report(report, path):
    # sort_keys + fixed separators => byte-identical for identical content.
    with open(path, "w") as f:
        json.dump(report, f, indent=1, sort_keys=True)
        f.write("\n")


if __name__ == "__main__":
    pid = sys.argv[1] if len(sys.argv) > 1 else "single"
    rep = build_report(pid)
    out = os.path.join(HERE, "results", "movement_report.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    write_report(rep, out)
    r = rep["phrases"][pid]
    gate = "PASS" if r["hard_gate_pass"] else "FAIL"
    cf = r["metrics"]["contact_fidelity"]
    print(f"phrase={pid}  hard_gate={gate}  score={r['score']}  "
          f"worst_contact={cf['worst_contact_mm']}mm  config_hash={rep['metadata']['config_hash']}")
    print(f"wrote {out}")
