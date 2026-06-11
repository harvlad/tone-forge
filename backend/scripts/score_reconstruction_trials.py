"""Aggregate reconstruction-trial JSONs into the project report.

Reads every ``trial_*.json`` under
``preset_catalog_output/reconstruction_trials/``. Computes per-arm
aggregates, the cross-arm deltas, and applies the H1/H2/H3 thresholds
from ``RECONSTRUCTION_TRIAL_PLAN.md``.

Outputs
-------
- ``preset_catalog_output/reconstruction_trials/reconstruction_trial_report.json``
- ``preset_catalog_output/reconstruction_trials/reconstruction_trial_report.md``

Exit codes
----------
- 0  overall verdict STRONG PASS or WEAK PASS
- 1  overall verdict FAIL
- 2  insufficient sample (n_per_arm < 5) or malformed input
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import Counter
from pathlib import Path

TRIALS_DIR = Path("preset_catalog_output/reconstruction_trials")
OUT_JSON = TRIALS_DIR / "reconstruction_trial_report.json"
OUT_MD = TRIALS_DIR / "reconstruction_trial_report.md"

MIN_PER_ARM = 5
WORKHORSE = {"bass", "lead", "pad", "other"}

# Material-effect thresholds (locked by the plan)
H1_TIME_REDUCTION = 0.30   # >= 30% drop in median time-to-acceptable
H2_SUCCESS_LIFT = 0.15     # >= 15 pp lift in success rate
H3_SATISFACTION_LIFT = 1.0 # >= 1.0 Likert lift


def _load_trials() -> list[dict]:
    if not TRIALS_DIR.exists():
        return []
    out: list[dict] = []
    for p in sorted(TRIALS_DIR.glob("trial_*.json")):
        try:
            out.append(json.loads(p.read_text()))
        except Exception as exc:  # noqa: BLE001
            print(f"WARN: skipping malformed {p.name}: {exc}", file=sys.stderr)
    return out


def _aggregate_arm(trials: list[dict]) -> dict:
    if not trials:
        return {"n": 0}
    n = len(trials)
    n_success = sum(1 for t in trials if t["success"])
    succ = [t for t in trials if t["success"]]
    times_acc = [t["time_to_acceptable_sec"] for t in succ
                 if t["time_to_acceptable_sec"] is not None]
    times_exp = [t["time_to_export_sec"] for t in succ
                 if t["time_to_export_sec"] is not None]
    sats = [t["satisfaction"] for t in succ]
    tweaks = Counter(t["tweaks_estimate"] for t in trials)
    rank_dist = Counter(t["selected_rank"] for t in trials
                        if t["selected_rank"] is not None)
    return {
        "n": n,
        "n_success": n_success,
        "success_rate": n_success / n,
        "median_time_to_acceptable_sec":
            statistics.median(times_acc) if times_acc else None,
        "median_time_to_export_sec":
            statistics.median(times_exp) if times_exp else None,
        "mean_satisfaction": (
            statistics.mean(sats) if sats else None
        ),
        "tweaks_distribution": dict(tweaks),
        "selected_rank_distribution": dict(rank_dist),
        "n_workhorse": sum(1 for t in trials
                           if t["target_sound_type"] in WORKHORSE),
    }


def _eval_hypotheses(retrieval: dict, control: dict) -> dict:
    def _pct(x: float | None) -> str:
        return "n/a" if x is None else f"{x * 100:.1f}%"

    # H1
    r_med = retrieval.get("median_time_to_acceptable_sec")
    c_med = control.get("median_time_to_acceptable_sec")
    if r_med is None or c_med is None or c_med == 0:
        h1_pass = False
        h1_delta = None
        h1_reason = "missing median time(s)"
    else:
        h1_delta = (c_med - r_med) / c_med
        h1_pass = h1_delta >= H1_TIME_REDUCTION
        h1_reason = (f"control_med={c_med:.0f}s, retrieval_med={r_med:.0f}s, "
                     f"reduction={_pct(h1_delta)} "
                     f"vs threshold {_pct(H1_TIME_REDUCTION)}")

    # H2
    r_succ = retrieval.get("success_rate")
    c_succ = control.get("success_rate")
    if r_succ is None or c_succ is None:
        h2_pass = False; h2_delta = None
        h2_reason = "missing success rate"
    else:
        h2_delta = r_succ - c_succ
        h2_pass = h2_delta >= H2_SUCCESS_LIFT
        h2_reason = (f"control_succ={_pct(c_succ)}, "
                     f"retrieval_succ={_pct(r_succ)}, "
                     f"lift={h2_delta * 100:+.1f}pp "
                     f"vs threshold +{H2_SUCCESS_LIFT * 100:.0f}pp")

    # H3
    r_sat = retrieval.get("mean_satisfaction")
    c_sat = control.get("mean_satisfaction")
    if r_sat is None or c_sat is None:
        h3_pass = False; h3_delta = None
        h3_reason = "missing satisfaction"
    else:
        h3_delta = r_sat - c_sat
        h3_pass = h3_delta >= H3_SATISFACTION_LIFT
        h3_reason = (f"control_sat={c_sat:.2f}, retrieval_sat={r_sat:.2f}, "
                     f"lift={h3_delta:+.2f} "
                     f"vs threshold +{H3_SATISFACTION_LIFT:.1f}")

    # Overall
    if h1_pass and (h2_pass or h3_pass):
        verdict = "STRONG PASS"
    elif h1_pass or (h2_pass and h3_pass):
        verdict = "WEAK PASS"
    else:
        verdict = "FAIL"

    return {
        "H1_time_to_acceptable_reduction": {
            "pass": h1_pass, "delta": h1_delta,
            "threshold": H1_TIME_REDUCTION, "reason": h1_reason,
        },
        "H2_success_rate_lift": {
            "pass": h2_pass, "delta": h2_delta,
            "threshold": H2_SUCCESS_LIFT, "reason": h2_reason,
        },
        "H3_satisfaction_lift": {
            "pass": h3_pass, "delta": h3_delta,
            "threshold": H3_SATISFACTION_LIFT, "reason": h3_reason,
        },
        "overall_verdict": verdict,
    }


def _markdown(retrieval, control, hypotheses, n_total) -> str:
    def _fmt_sec(s):
        return "n/a" if s is None else f"{s:.0f}s ({s / 60:.1f} min)"

    def _fmt_pct(p):
        return "n/a" if p is None else f"{p * 100:.1f}%"

    lines = [
        "# Reconstruction Trial Report",
        "",
        f"**Trials loaded:** {n_total}",
        f"**Retrieval arm:** n={retrieval.get('n', 0)}, "
        f"workhorse={retrieval.get('n_workhorse', 0)}",
        f"**Control arm:**   n={control.get('n', 0)}, "
        f"workhorse={control.get('n_workhorse', 0)}",
        "",
        f"## Overall verdict: **{hypotheses['overall_verdict']}**",
        "",
        "| Hypothesis | Result | Detail |",
        "|---|---|---|",
    ]
    for key, label in [
        ("H1_time_to_acceptable_reduction", "H1 time-to-acceptable lift"),
        ("H2_success_rate_lift", "H2 success-rate lift"),
        ("H3_satisfaction_lift", "H3 satisfaction lift"),
    ]:
        h = hypotheses[key]
        lines.append(
            f"| {label} | "
            f"{'PASS' if h['pass'] else 'FAIL'} | {h['reason']} |"
        )

    for arm_name, arm in [("Retrieval", retrieval), ("Control", control)]:
        if arm.get("n", 0) == 0:
            continue
        lines += [
            "",
            f"## {arm_name} arm (n={arm['n']})",
            f"- success rate: {arm['n_success']}/{arm['n']} "
            f"= {_fmt_pct(arm['success_rate'])}",
            f"- median time-to-acceptable: "
            f"{_fmt_sec(arm['median_time_to_acceptable_sec'])}",
            f"- median time-to-export: "
            f"{_fmt_sec(arm['median_time_to_export_sec'])}",
            f"- mean satisfaction: "
            f"{arm['mean_satisfaction']:.2f}" if arm['mean_satisfaction']
            is not None else "- mean satisfaction: n/a",
            f"- tweaks distribution: {arm['tweaks_distribution']}",
        ]
        if arm["selected_rank_distribution"]:
            lines.append(
                f"- selected rank distribution: "
                f"{arm['selected_rank_distribution']}"
            )
    return "\n".join(lines) + "\n"


def main() -> int:
    trials = _load_trials()
    if not trials:
        print("No trials found. Run scripts/reconstruction_trial.py run ...")
        return 2

    retrieval = [t for t in trials if t["arm"] == "retrieval"]
    control = [t for t in trials if t["arm"] == "control"]
    agg_r = _aggregate_arm(retrieval)
    agg_c = _aggregate_arm(control)

    too_small = (
        agg_r.get("n", 0) < MIN_PER_ARM
        or agg_c.get("n", 0) < MIN_PER_ARM
    )

    if too_small:
        print(f"INSUFFICIENT SAMPLE: need >= {MIN_PER_ARM} trials per arm; "
              f"have retrieval={agg_r.get('n', 0)}, "
              f"control={agg_c.get('n', 0)}.")
        # Still write a partial report so progress is visible.
        partial = {
            "trials_total": len(trials),
            "retrieval": agg_r,
            "control": agg_c,
            "verdict": "INSUFFICIENT_SAMPLE",
        }
        OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
        OUT_JSON.write_text(json.dumps(partial, indent=2))
        OUT_MD.write_text(
            f"# Reconstruction Trial Report (incomplete)\n\n"
            f"Need >= {MIN_PER_ARM} trials per arm; have "
            f"retrieval={agg_r.get('n', 0)}, control={agg_c.get('n', 0)}.\n"
        )
        return 2

    hyp = _eval_hypotheses(agg_r, agg_c)
    report = {
        "trials_total": len(trials),
        "retrieval": agg_r,
        "control": agg_c,
        "hypotheses": hyp,
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(report, indent=2))
    OUT_MD.write_text(_markdown(agg_r, agg_c, hyp, len(trials)))
    print(f"Wrote {OUT_JSON}")
    print(f"Wrote {OUT_MD}")
    print(f"\nOverall verdict: {hyp['overall_verdict']}")
    for key in ("H1_time_to_acceptable_reduction",
                "H2_success_rate_lift", "H3_satisfaction_lift"):
        h = hyp[key]
        print(f"  {'PASS' if h['pass'] else 'FAIL'}  {key}: {h['reason']}")
    return 0 if hyp["overall_verdict"] != "FAIL" else 1


if __name__ == "__main__":
    sys.exit(main())
