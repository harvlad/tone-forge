"""Regression: every ``apply_model`` call in stem_separator must pin ``shifts=0``.

Demucs' default ``shifts=1`` applies a random temporal shift to each
chunk and averages predictions. The shift is drawn from PyTorch RNG
with no seed pinned. That made stem separation non-deterministic
across pipeline runs on the same source audio:

  * piano stem differed by 57% RMS
  * other  stem differed by 39% RMS
  * guitar/bass/drums/vocals each differed by 4-8% RMS

Downstream, that drift propagated into the chord detector (via
bass-pitch biasing of chord templates → chord_density_per_s in
guidance-mode signal A) and into per-stem MIDI features (signals
B/C/D/E). Result: 20 of 22 sections on Sex On Fire reclassified
between two consecutive analysis runs, which silently broke the
calibration loop (we were labelling sections against a moving
classifier output).

The fix: pass ``shifts=0`` everywhere. This bypasses the random-shift
averaging trick, giving bit-exact stems across runs. Quality loss is
imperceptible for the downstream analysis pipeline (we don't ship
stems as final-mix audio).

This file pins the fix so a future refactor can't silently drop the
kwarg and re-introduce 20-of-22-section classifier drift.

The test is static (text-grep over stem_separator.py) because it
must work without a Demucs install in CI — Demucs is a heavy
optional dep and importing it just to lint a kwarg would be the
wrong trade-off.
"""
from __future__ import annotations

import re
from pathlib import Path

STEM_SEPARATOR = (
    Path(__file__).resolve().parent.parent
    / "tone_forge"
    / "stem_separator.py"
)

# Match: ``apply_model(model, wav, device=device, shifts=0)``
# Tolerate whitespace and trailing args; require ``shifts=0`` to
# appear before the closing paren.
_OK_PATTERN = re.compile(
    r"apply_model\s*\([^)]*\bshifts\s*=\s*0\b[^)]*\)",
    re.DOTALL,
)
# Match: any ``apply_model(...)`` call so we can count totals.
_ANY_PATTERN = re.compile(r"apply_model\s*\([^)]*\)", re.DOTALL)


def _read_source() -> str:
    return STEM_SEPARATOR.read_text(encoding="utf-8")


def test_all_apply_model_calls_pin_shifts_zero():
    src = _read_source()
    all_calls = _ANY_PATTERN.findall(src)
    ok_calls = _OK_PATTERN.findall(src)
    assert len(all_calls) >= 1, (
        "stem_separator.py should call apply_model at least once; "
        "if Demucs was removed, this test is obsolete and should be deleted."
    )
    assert len(ok_calls) == len(all_calls), (
        f"Every apply_model() call must pin shifts=0 for determinism. "
        f"Found {len(all_calls)} call(s), only {len(ok_calls)} pin shifts=0. "
        f"All calls:\n  " + "\n  ".join(all_calls)
    )


def test_no_apply_model_call_uses_shifts_one():
    """Defensive: if a future commit explicitly sets ``shifts=1``
    inside an ``apply_model`` call we fail loudly. The 'silent
    default 1' was the original bug; an explicit 1 would be a
    deliberate regression and deserves a test failure so the
    author has to justify it.

    Scope intentionally narrow: docstrings and comments may freely
    mention ``shifts=1`` (the rationale-bearing comment that explains
    the fix does exactly this). Only ``apply_model(... shifts=1 ...)``
    calls are forbidden.
    """
    src = _read_source()
    bad_pattern = re.compile(
        r"apply_model\s*\([^)]*\bshifts\s*=\s*1\b[^)]*\)",
        re.DOTALL,
    )
    matches = bad_pattern.findall(src)
    assert not matches, (
        "stem_separator.py has apply_model() calls with shifts=1. "
        "That's the non-deterministic default — pin shifts=0 instead. "
        f"Offending calls:\n  " + "\n  ".join(matches)
    )
