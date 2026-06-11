"""Interactive CLI harness for the Reconstruction Trial v1.

See ``backend/RECONSTRUCTION_TRIAL_PLAN.md`` for the experimental design.

This is the canonical runner. It supersedes the earlier
``scripts/reconstruction_trial.py`` by adding a separate
``t_selected`` capture for the retrieval arm so the harness can
decompose time-to-acceptable into "audition" (start to selection) and
"tweak" (selection to acceptable) costs.

Commands
--------
::

    # 1. List the workhorse PASS targets from V3 (run once)
    python3 scripts/reconstruction_trial_runner.py list-targets

    # 2. Run one trial (alternate arms by index per the plan)
    python3 scripts/reconstruction_trial_runner.py run \\
        --arm retrieval \\
        --target synth_essentials_analog_dalmation_bass

    python3 scripts/reconstruction_trial_runner.py run \\
        --arm control \\
        --target synth_essentials_analog_saw_filter_bass

Each run:

1. Prints the reference WAV path and (retrieval arm) the top-5 with
   preset names + audio paths.
2. Prompts the operator to press ENTER to mark
   ``t_start`` -> begin auditioning.
3. (retrieval arm) Prompts again at ``t_selected`` once the operator
   has picked which top-5 candidate to start from, and records its
   rank (1-5).
4. Prompts at ``t_acceptable`` (or ``cap`` if the 15-min cap was hit).
5. Prompts at ``t_export`` after the file is bounced.
6. Collects: ``tweaks_estimate`` (bucket), ``success``, ``satisfaction``
   (1-5), free-text ``notes``, optional ``exported_als_path``.
7. Atomically writes ``trial_<id>.json`` into the trials directory.

The JSON schema is consumed by ``score_reconstruction_trials.py``;
``t_selected_iso`` and ``time_to_selection_sec`` are new informational
fields that the scorer reads if present and ignores otherwise.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# --- Paths --------------------------------------------------------------------

# All paths are relative to backend/ — the script expects to be invoked from
# backend/ (CWD) or with absolute working directory.
V3_FILE = Path("preset_catalog_output/retrieval/v3_top5_usefulness_rating.json")
TRIALS_DIR = Path("preset_catalog_output/reconstruction_trials")

# --- Constants ----------------------------------------------------------------

TWEAK_BUCKETS = ["0-5", "6-15", "16-30", "30+"]
WORKHORSE = {"bass", "lead", "pad", "other"}
TIME_CAP_SEC = 15 * 60
HARNESS_VERSION = "runner-v1"


# --- Helpers ------------------------------------------------------------------

def _git_sha() -> Optional[str]:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
            cwd=str(Path(__file__).resolve().parents[1]),
        )
        return out.decode().strip()
    except Exception:
        return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _atomic_write(target: Path, payload: object) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    os.replace(tmp, target)


def _load_v3_queries() -> list[dict]:
    if not V3_FILE.exists():
        sys.exit(f"ERROR: V3 file not found: {V3_FILE}")
    return json.loads(V3_FILE.read_text())["queries"]


def _workhorse_pass_targets() -> list[dict]:
    out: list[dict] = []
    for q in _load_v3_queries():
        if q["query_sound_type"] not in WORKHORSE:
            continue
        if q["query_summary"].get("any_top5_usable") is True:
            out.append(q)
    return out


def _prompt(text: str, default: Optional[str] = None) -> str:
    suffix = f" [{default}]" if default else ""
    val = input(f"{text}{suffix}: ").strip()
    if not val and default is not None:
        return default
    return val


def _prompt_choice(text: str, choices: list[str]) -> str:
    s = "/".join(choices)
    while True:
        val = input(f"{text} ({s}): ").strip().lower()
        if val in choices:
            return val
        print(f"  pick one of {choices}")


def _prompt_int(text: str, lo: int, hi: int) -> int:
    while True:
        raw = input(f"{text} ({lo}-{hi}): ").strip()
        try:
            v = int(raw)
            if lo <= v <= hi:
                return v
        except ValueError:
            pass
        print(f"  enter an integer in [{lo}, {hi}]")


def _press_enter(text: str) -> float:
    input(f"{text} — press ENTER ")
    return time.time()


# --- list-targets -------------------------------------------------------------

def cmd_list_targets() -> int:
    rows = _workhorse_pass_targets()
    by_st: dict[str, list[dict]] = {}
    for r in rows:
        by_st.setdefault(r["query_sound_type"], []).append(r)
    print(f"\nWorkhorse V3 PASS targets ({len(rows)} total):\n")
    for st in ("bass", "lead", "pad", "other"):
        items = by_st.get(st, [])
        print(f"  {st} ({len(items)}):")
        for it in items:
            print(f"    {it['query_preset_id']:55s}  ({it['query_name']})")
        print()
    print("Plan quota (10 per arm): 3 bass, 3 lead, 2 pad, 2 other.")
    print("Alternate arms by trial index. Time cap: 15 min per trial.")
    return 0


# --- run ----------------------------------------------------------------------

def cmd_run(args: argparse.Namespace) -> int:
    arm = args.arm
    if arm not in ("retrieval", "control"):
        sys.exit("--arm must be retrieval or control")

    queries = {q["query_preset_id"]: q for q in _load_v3_queries()}
    if args.target not in queries:
        sys.exit(
            f"ERROR: --target {args.target!r} not in V3 query set. "
            f"Run `reconstruction_trial_runner.py list-targets` for candidates."
        )
    q = queries[args.target]
    sound_type = q["query_sound_type"]
    if sound_type not in WORKHORSE:
        print(
            f"WARNING: target sound_type={sound_type} is outside the "
            f"workhorse cohort; this trial will be tagged but excluded "
            f"from aggregate H1/H2/H3 verdicts."
        )

    trial_id = (
        datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        + f"_{arm}_{sound_type}"
    )

    # --- Brief operator ------------------------------------------------------
    print("\n" + "=" * 70)
    print(f"  Reconstruction trial  id={trial_id}")
    print("=" * 70)
    print(f"  arm:        {arm}")
    print(f"  target id:  {q['query_preset_id']}")
    print(f"  target:     {q['query_name']}")
    print(f"  sound_type: {sound_type}")
    print(f"  category:   {q['query_category']}")
    print(f"  reference:  {q['query_audio']}")
    print()
    if arm == "retrieval":
        print("  Top-5 retrieved (rank | name | sound_type | cos | audio):")
        for nb in q["top5"]:
            print(
                f"    #{nb['rank']}  {nb['name']:35s}  "
                f"{nb['sound_type']:5s}  cos={nb['cosine_sim']:.3f}"
            )
            print(f"        {nb['audio_path']}")
        print()
    else:
        print(
            "  CONTROL ARM: no presets shown. Start from a default Analog "
            "patch in Live."
        )
        print()
    print(f"  Time cap: {TIME_CAP_SEC // 60} min wall-clock from t_start.")
    print("  Acceptable-sound criterion is your pre-committed rule.")
    print()

    confirm = _prompt_choice("Ready to begin?", ["y", "n"])
    if confirm != "y":
        print("Aborted before t_start.")
        return 1

    # --- Run timeline --------------------------------------------------------
    # t_start: operator begins auditioning candidates (retrieval) or opens a
    # blank patch (control).
    t_start = _press_enter(">>> START: begin auditioning / open Live")
    t_start_iso = _now_iso()
    print(f"    t_start captured at {t_start_iso}.")
    print()

    t_selected: Optional[float] = None
    t_selected_iso: Optional[str] = None
    selected_rank: Optional[int] = None

    # Retrieval-arm only: capture the moment the operator commits to a starting
    # preset, before they begin tweaking it in Live.
    if arm == "retrieval":
        _press_enter(
            ">>> SELECTED: one of the top-5 picked as starting preset"
        )
        t_selected = time.time()
        t_selected_iso = _now_iso()
        elapsed_sel = t_selected - t_start
        print(
            f"    t_selected captured. Audition time: {elapsed_sel:.0f}s "
            f"({elapsed_sel / 60:.1f} min)."
        )
        selected_rank = _prompt_int("    Selected rank (1-5)", 1, 5)
        print()

    # t_acceptable: operator decides the reconstruction is committable; or
    # types 'cap' if 15 minutes elapsed without reaching that bar.
    t_acceptable: Optional[float] = None
    t_acceptable_iso: Optional[str] = None
    t_export: Optional[float] = None
    t_export_iso: Optional[str] = None

    while True:
        sentinel = input(
            "    Press ENTER when ACCEPTABLE reached, or type 'cap' "
            "if the 15-min cap was hit: "
        ).strip().lower()
        if sentinel in ("", "cap"):
            break

    if sentinel == "cap":
        print("    Trial marked as cap-hit (success will be recorded false).")
    else:
        t_acceptable = time.time()
        t_acceptable_iso = _now_iso()
        elapsed = t_acceptable - t_start
        print(
            f"    t_acceptable captured. Elapsed since t_start: "
            f"{elapsed:.0f}s ({elapsed / 60:.1f} min)."
        )
        print()
        input(
            "    Press ENTER when EXPORTED (after Live finishes rendering): "
        )
        t_export = time.time()
        t_export_iso = _now_iso()
        elapsed = t_export - t_start
        print(
            f"    t_export captured. Total elapsed: {elapsed:.0f}s "
            f"({elapsed / 60:.1f} min)."
        )

    print()

    # --- Post-trial prompts --------------------------------------------------
    tweaks = _prompt_choice(
        "Tweaks estimate bucket", [b.lower() for b in TWEAK_BUCKETS]
    )
    tweaks = TWEAK_BUCKETS[[b.lower() for b in TWEAK_BUCKETS].index(tweaks)]

    success_raw = _prompt_choice(
        "Success? (reached acceptable within cap)", ["y", "n"]
    )
    success = success_raw == "y"

    satisfaction = _prompt_int(
        "Satisfaction (1=hated it, 5=great)", 1, 5
    )

    notes = _prompt("Notes (free text, optional)", default="")
    exported_als_path = _prompt(
        "Exported ALS path (optional, for future param-diff)", default=""
    )

    # --- Derived metrics -----------------------------------------------------
    time_to_selection_sec = (
        None if t_selected is None else round(t_selected - t_start, 1)
    )
    time_to_acceptable_sec = (
        None if t_acceptable is None else round(t_acceptable - t_start, 1)
    )
    time_to_export_sec = (
        None if t_export is None else round(t_export - t_start, 1)
    )

    record = {
        "trial_id": trial_id,
        "harness_version": HARNESS_VERSION,
        "git_sha": _git_sha(),
        "arm": arm,
        "target_preset_id": q["query_preset_id"],
        "target_name": q["query_name"],
        "target_sound_type": sound_type,
        "target_category": q["query_category"],
        "reference_wav": q["query_audio"],
        "top5_offered": (
            [
                {
                    "rank": nb["rank"],
                    "preset_id": nb["preset_id"],
                    "name": nb["name"],
                    "sound_type": nb["sound_type"],
                    "cosine_sim": nb["cosine_sim"],
                    "audio_path": nb["audio_path"],
                }
                for nb in q["top5"]
            ]
            if arm == "retrieval"
            else []
        ),
        "t_start_iso": t_start_iso,
        "t_selected_iso": t_selected_iso,
        "t_acceptable_iso": t_acceptable_iso,
        "t_export_iso": t_export_iso,
        "time_to_selection_sec": time_to_selection_sec,
        "time_to_acceptable_sec": time_to_acceptable_sec,
        "time_to_export_sec": time_to_export_sec,
        "selected_rank": selected_rank,
        "tweaks_estimate": tweaks,
        "success": success,
        "satisfaction": satisfaction,
        "notes": notes,
        "exported_als_path": exported_als_path or None,
    }
    out = TRIALS_DIR / f"trial_{trial_id}.json"
    _atomic_write(out, record)
    print()
    print(f"Wrote {out}")
    print("Done.")
    return 0


# --- Main ---------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list-targets", help="Print workhorse PASS targets from V3")
    run_p = sub.add_parser("run", help="Run a single trial")
    run_p.add_argument(
        "--arm", required=True, choices=("retrieval", "control")
    )
    run_p.add_argument(
        "--target", required=True, help="preset_id from V3 query set"
    )
    args = ap.parse_args()
    if args.cmd == "list-targets":
        return cmd_list_targets()
    return cmd_run(args)


if __name__ == "__main__":
    sys.exit(main())
