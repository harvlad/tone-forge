"""Standardized benchmark reporting + early-termination guidance."""
from __future__ import annotations

from typing import Optional

from . import config


def print_eval(model_id: str, tier: str, inst_class: Optional[str], result: dict) -> None:
    g = result["global"]
    print(f"\n== {model_id} | tier={tier} | class={inst_class or 'all'} | "
          f"tol={result['onset_tolerance']*1000:.0f}ms ==")
    print(f"stems: {result['n_stems_evaluated']} evaluated, "
          f"{len(result['missing'])} missing predictions")
    print(f"GLOBAL  n_gt={g['n_gt']}  recall={g['recall']:.3f}  "
          f"precision={g['precision']:.3f}  f1={g['f1']:.3f}  "
          f"oct_err={g['octave_error_rate']:.3f}")
    for cls, c in result["per_class"].items():
        print(f"  {cls:<22} n_gt={c['n_gt']:<7} recall={c['recall']:.3f}  "
              f"precision={c['precision']:.3f}  f1={c['f1']:.3f}  "
              f"oct_err={c['octave_error_rate']:.3f}")


def early_termination_hint(result: dict, incumbent_recall: float,
                           tier: str) -> Optional[str]:
    """Research-level early-termination advice (advisory, with reasons —
    never a blind gate)."""
    g = result["global"]
    if result["n_stems_evaluated"] == 0:
        return None
    if g["n_pred"] == 0:
        return ("STOP CANDIDATE: model produced zero notes across the tier — "
                "broken adapter or unsupported input.")
    if tier in ("smoke", "scout") and incumbent_recall > 0:
        if g["recall"] < incumbent_recall * 0.25:
            return (f"STOP CANDIDATE: recall {g['recall']:.3f} is <25% of incumbent "
                    f"({incumbent_recall:.3f}) after {tier}; recovery implausible. "
                    "Partial predictions stay cached.")
        if g["recall"] < incumbent_recall * 0.6:
            return (f"CAUTION: recall {g['recall']:.3f} well below incumbent "
                    f"({incumbent_recall:.3f}). Consider stopping unless it wins "
                    "a target class.")
    return None


def decision_options() -> str:
    return "REJECT | PROMOTE | SPECIALIST_CANDIDATE | GENERALIST_CANDIDATE | NEEDS_INVESTIGATION"


def incumbent_recall() -> float:
    return config.VALIDATED_REFERENCE["basic_pitch"]["recall"]
