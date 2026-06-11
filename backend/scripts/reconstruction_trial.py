"""Interactive CLI harness for a single reconstruction trial.

See ``backend/RECONSTRUCTION_TRIAL_PLAN.md`` for the experimental design.

Usage
-----
::

    # one-time: list candidate workhorse PASS targets from V3
    python3 scripts/reconstruction_trial.py list-targets

    # per-trial
    python3 scripts/reconstruction_trial.py run --arm retrieval \\
        --target synth_essentials_analog_dalmation_bass

    python3 scripts/reconstruction_trial.py run --arm control \\
        --target synth_essentials_analog_saw_filter_bass

The harness:

1. Prints the reference WAV path and, for the retrieval arm, the
   pre-computed top-5 (with audio paths).
2. Prompts the operator to press ENTER at ``t_start``,
   ``t_acceptable``, and ``t_export``.
3. Collects post-trial fields: ``selected_rank`` (retrieval arm only),
   ``tweaks_estimate``, ``success``, ``satisfaction``, ``notes``, and
   optional ``exported_als_path``.
4. Atomically writes ``trial_<id>.json`` into the trials directory.

Trial JSON schema is consumed by ``score_reconstruction_trials.py``.
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

V3_FILE = Path(
    "preset_catalog_output/retrieval/v3_top5_usefulness_rating.json"
)
V3_REPORT = Path("preset_catalog_output/retrieval/v3_score_report.json")
TRIALS_DIR = Path("preset_catalog_output/reconstruction_trials")

TWEAK_BUCKETS = ["0-5", "6-15", "16-30", "30+"]
WORKHORSE = {"bass", "lead", "pad", "other"}


# ---------------------------------------------------------------------------
def _git_sha() -> Optional[str]:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
            cwd=str(Path(__file__).resolve().parents[1]),
        )
        return out.decode().strip()
    except Exception:  # noqa: BLE001
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
    """Workhorse queries from V3 that the operator marked usable."""
    out = []
    for q in _load_v3_queries():
        if q["query_sound_type"] not in WORKHORSE:
            continue
        if q["query_summary"].get("any_top5_usable") is True:
            out.append(q)
    return out


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
            print(f"    {it['query_preset_id']:55s}  "
                  f"({it['query_name']})")
        print()
    print("Plan recommends quota: 4 bass, 4 lead, 2 pad. "
          "Alternate arms by trial index.")
    return 0


# ---------------------------------------------------------------------------
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


def cmd_run(args: argparse.Namespace) -> int:
    arm = args.arm
    if arm not in ("retrieval", "control"):
        sys.exit("--arm must be retrieval or control")

    queries = {q["query_preset_id"]: q for q in _load_v3_queries()}
    if args.target not in queries:
        sys.exit(
            f"ERROR: --target {args.target!r} not in V3 query set. "
            f"Run `reconstruction_trial.py list-targets` for candidates."
        )
    q = queries[args.target]
    sound_type = q["query_sound_type"]
    if sound_type not in WORKHORSE:
        print(f"WARNING: target sound_type={sound_type} is outside the "
              f"workhorse cohort; this trial will be tagged but excluded "
              f"from aggregate H1/H2/H3 verdicts.")

    trial_id = (
        datetime.now(timezone.utc)
        .strftime("%Y%m%d_%H%M%S")
        + f"_{arm}_{sound_type}"
    )

    # --- Brief operator -----------------------------------------------------
    print("\n" + "=" * 60)
    print(f"  Reconstruction trial  id={trial_id}")
    print("=" * 60)
    print(f"  arm:        {arm}")
    print(f"  target id:  {q['query_preset_id']}")
    print(f"  sound_type: {sound_type}")
    print(f"  category:   {q['query_category']}")
    print(f"  reference:  {q['query_audio']}")
    print()
    if arm == "retrieval":
        print("  Top-5 retrieved (rank | name | sound_type | cos | audio):")
        for nb in q["top5"]:
            print(f"    #{nb['rank']}  {nb['name']:35s}  "
                  f"{nb['sound_type']:5s}  cos={nb['cosine_sim']:.3f}")
            print(f"        {nb['audio_path']}")
        print()
    else:
        print("  CONTROL ARM: no presets shown. Start from a default "
              "Analog patch in Live.")
        print()
    print("  Time cap: 15 min wall-clock from t_start.")
    print("  Acceptable-sound criterion is your pre-committed rule.")
    print()

    confirm = _prompt_choice("Ready to begin?", ["y", "n"])
    if confirm != "y":
        print("Aborted before t_start.")
        return 1

    # --- Run ----------------------------------------------------------------
    t_start = _press_enter(">>> START: open Live and begin")
    t_start_iso = _now_iso()
    print(f"    t_start captured at {t_start_iso}.")
    print()

    t_acceptable: Optional[float] = None
    t_acceptable_iso: Optional[str] = None
    t_export: Optional[float] = None
    t_export_iso: Optional[str] = None

    # Acceptable
    while True:
        sentinel = input(
            "    Press ENTER when ACCEPTABLE reached, or type 'cap' "
            "if the 15-min cap was hit: "
        ).strip().lower()
        if sentinel in ("", "cap"):
            break
    if sentinel == "cap":
        print("    Trial marked as cap-hit (success=false).")
    else:
        t_acceptable = time.time()
        t_acceptable_iso = _now_iso()
        elapsed = t_acceptable - t_start
        print(f"    t_acceptable captured. Elapsed: {elapsed:.0f}s "
              f"({elapsed / 60:.1f} min).")
        print()
        input("    Press ENTER when EXPORTED (after Live finishes rendering): ")
        t_export = time.time()
        t_export_iso = _now_iso()
        elapsed = t_export - t_start
        print(f"    t_export captured. Total elapsed: {elapsed:.0f}s "
              f"({elapsed / 60:.1f} min).")

    print()
    # --- Post-trial prompts -------------------------------------------------
    if arm == "retrieval" and t_acceptable is not None:
        selected_rank = _prompt_int("Selected rank (1-5)", 1, 5)
    else:
        selected_rank = None
    tweaks = _prompt_choice("Tweaks estimate bucket",
                            [b.lower() for b in TWEAK_BUCKETS])
    tweaks = TWEAK_BUCKETS[
        [b.lower() for b in TWEAK_BUCKETS].index(tweaks)
    ]
    success_raw = _prompt_choice("Success? (reached acceptable within cap)",
                                 ["y", "n"])
    success = success_raw == "y"
    satisfaction = _prompt_int("Satisfaction (1=hated it, 5=great)", 1, 5)
    notes = _prompt("Notes (free text, optional)", default="")
    exported_als_path = _prompt(
        "Exported ALS path (optional, for future param-diff)", default=""
    )

    record = {
        "trial_id": trial_id,
        "git_sha": _git_sha(),
        "arm": arm,
        "target_preset_id": q["query_preset_id"],
        "target_name": q["query_name"],
        "target_sound_type": sound_type,
        "target_category": q["query_category"],
        "reference_wav": q["query_audio"],
        "top5_offered": (
            [{"rank": nb["rank"], "preset_id": nb["preset_id"],
              "name": nb["name"], "sound_type": nb["sound_type"],
              "cosine_sim": nb["cosine_sim"],
              "audio_path": nb["audio_path"]}
             for nb in q["top5"]]
            if arm == "retrieval" else []
        ),
        "t_start_iso": t_start_iso,
        "t_acceptable_iso": t_acceptable_iso,
        "t_export_iso": t_export_iso,
        "time_to_acceptable_sec": (
            None if t_acceptable is None else round(t_acceptable - t_start, 1)
        ),
        "time_to_export_sec": (
            None if t_export is None else round(t_export - t_start, 1)
        ),
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


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list-targets",
                   help="Print workhorse PASS targets from V3")
    run_p = sub.add_parser("run", help="Run a single trial")
    run_p.add_argument("--arm", required=True,
                       choices=("retrieval", "control"))
    run_p.add_argument("--target", required=True,
                       help="preset_id from V3 query set")
    args = ap.parse_args()
    if args.cmd == "list-targets":
        return cmd_list_targets()
    return cmd_run(args)


if __name__ == "__main__":
    sys.exit(main())
