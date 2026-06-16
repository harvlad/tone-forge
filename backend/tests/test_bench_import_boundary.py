"""Enforce the one-way ``bench`` -> ``tone_forge`` dependency.

Invariant 3 in the M1 plan: ``bench`` may import from ``tone_forge``
(read-only), but ``tone_forge`` must NEVER import from ``bench``.
This test statically scans every ``tone_forge`` source file and
fails loudly if any of them references ``bench``.

The scan is intentionally textual (regex over file contents) rather
than AST-based: AST parsing would miss ``__import__('bench')`` or
``importlib.import_module('bench')`` shenanigans, while the textual
check catches both forms with one rule.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


_BACKEND = Path(__file__).resolve().parents[1]
_TONE_FORGE = _BACKEND / "tone_forge"

# Match the literal substring "bench" only when it appears as a
# top-level module name in an import / dotted access. Substrings
# inside other identifiers (``benchmark_data``, ``benchmark.py``) are
# legitimate and NOT matched by these patterns.
_IMPORT_PATTERNS = [
    re.compile(r"^\s*import\s+bench(\s|$|\.|,)", re.MULTILINE),
    re.compile(r"^\s*from\s+bench(\s|\.)", re.MULTILINE),
    # Dynamic import via importlib.
    re.compile(r"""importlib\.import_module\(\s*['"]bench['"]""", re.MULTILINE),
    # Dynamic import via __import__.
    re.compile(r"""__import__\(\s*['"]bench['"]""", re.MULTILINE),
]


def _iter_tone_forge_py_files() -> list[Path]:
    return sorted(_TONE_FORGE.rglob("*.py"))


def test_tone_forge_does_not_import_bench() -> None:
    py_files = _iter_tone_forge_py_files()
    assert py_files, "no tone_forge/*.py files found; path discovery is broken"

    offenders: list[tuple[Path, str]] = []
    for path in py_files:
        text = path.read_text(encoding="utf-8")
        for pat in _IMPORT_PATTERNS:
            m = pat.search(text)
            if m:
                offenders.append((path, m.group(0).strip()))
                break

    assert not offenders, (
        "tone_forge.* must not import from bench.* "
        "(bench depends on tone_forge, not the reverse). Offenders:\n"
        + "\n".join(f"  {p.relative_to(_BACKEND)}: {snippet}"
                    for p, snippet in offenders)
    )


def test_bench_can_import_tone_forge() -> None:
    # Sanity: confirm the one-way direction is actually exercised.
    # We do a controlled import to ensure both packages co-exist on
    # the path.
    import bench  # noqa: F401  -- import for side-effect
    import tone_forge.analysis.detector_config  # noqa: F401


def test_pattern_does_not_match_substring_of_identifier(tmp_path: Path) -> None:
    # Defensive: a file containing ``benchmark`` (no leading dot,
    # different identifier) must not trigger the import-boundary
    # check. This guards against false positives in future
    # tone_forge code that legitimately mentions benchmarking.
    sample = (
        "# This module mentions benchmark and benchmarks.\n"
        "BENCHMARK_TAG = 'bench-test'\n"
        "def benchmarked(): pass\n"
    )
    for pat in _IMPORT_PATTERNS:
        assert pat.search(sample) is None, pat.pattern
