"""Validation harness: reproduce the known BP/MT3 benchmark from cache.

If reproduced numbers differ materially from VALIDATED_REFERENCE, this
FAILS LOUDLY.  Do not explain discrepancies away — a broken harness cost
weeks once already.
"""
from __future__ import annotations

from typing import List, Tuple

from . import analysis, config, corpus, tiers


def validate_model(model_id: str, onset_tolerance: float = 0.1) -> dict:
    manifest = corpus.load_manifest()
    stems = tiers.validated100_stems(manifest)
    # exclude the stems the original run dropped (see config for evidence)
    stems = stems[~stems["stem_id"].isin(config.VALIDATED_EXCLUDED_STEMS)]
    result = analysis.evaluate_model(model_id, stems, onset_tolerance=onset_tolerance)
    ref = config.VALIDATED_REFERENCE.get(model_id)
    checks: List[Tuple[str, float, float, bool]] = []
    if ref:
        for metric, expected in ref.items():
            got = result["global"].get(metric)
            ok = got is not None and abs(got - expected) <= config.VALIDATE_ABS_TOLERANCE
            checks.append((f"global.{metric}", expected, got, ok))
        for cls, models in config.VALIDATED_REFERENCE["per_class"].items():
            for metric, expected in models.get(model_id, {}).items():
                got = result["per_class"].get(cls, {}).get(metric)
                ok = got is not None and abs(got - expected) <= config.VALIDATE_ABS_TOLERANCE
                checks.append((f"{cls}.{metric}", expected, got, ok))
    result["checks"] = checks
    result["all_passed"] = all(c[3] for c in checks) if checks else None
    result["n_missing"] = len(result["missing"])
    return result


def print_validation(model_id: str, result: dict) -> bool:
    print(f"\n== VALIDATION: {model_id} (validated100, "
          f"tol={result['onset_tolerance']*1000:.0f}ms) ==")
    print(f"stems evaluated: {result['n_stems_evaluated']}  "
          f"missing predictions: {result['n_missing']}")
    if result["n_missing"]:
        print("  -> cannot fully validate until missing predictions are "
              "computed/imported (lab benchmark ... --tier validated100)")
    for name, expected, got, ok in result["checks"]:
        mark = "PASS" if ok else "FAIL"
        got_s = f"{got:.4f}" if got is not None else "n/a"
        print(f"  [{mark}] {name}: expected {expected:.4f}, got {got_s}")
    if result["all_passed"] is False and not result["n_missing"]:
        print("\n*** VALIDATION FAILED — STOP. Investigate the harness, do not "
              "explain the discrepancy away. ***")
    return bool(result["all_passed"])
