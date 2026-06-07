"""Subsystem boundary enforcement.

Each new subsystem package under ``backend/tone_forge/`` may import only
from ``tone_forge.contracts`` (and the legacy ``tone_forge.stem_model``
re-export the contracts depend on), plus its own internal modules and
standard / third-party libraries.

This test fails CI if any subsystem reaches into another subsystem's
internals. The composition layer is ``tone_forge_api`` — that is the
*only* place that may import multiple subsystems at once.

Implementation: AST walk over each subsystem package; collect every
``from tone_forge.X import ...`` / ``import tone_forge.X`` reference;
assert ``X`` is on the allow-list for that subsystem.

See ``/EXECUTION_PLAN.md`` §1 (Boundary enforcement) and §2 (Package
structure) for the policy this test encodes.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path
from typing import Iterable, Set

import pytest

# Repository layout:
#   <repo>/backend/tests/test_subsystem_boundaries.py  ← this file
#   <repo>/backend/tone_forge/                         ← package root
TONE_FORGE_ROOT = Path(__file__).resolve().parents[1] / "tone_forge"

# The subsystem packages introduced by the boundary freeze.
# Each maps to the additional ``tone_forge.*`` modules it is allowed to
# import from beyond the universal allow-list below.
SUBSYSTEMS: dict[str, Set[str]] = {
    "acquisition": set(),
    "analysis": set(),
    "stems": set(),
    "tone": set(),
    "monitor": set(),
    "devices": set(),
    "session": set(),
    "guidance": set(),
    "notation": set(),
}

# Every subsystem may import these directly.
#   - ``contracts``: the typed cross-boundary surface.
#   - ``stem_model``: ``contracts.StemSet`` composes its ``Stem`` record;
#     subsystems will need the same type when they consume StemSets.
UNIVERSAL_ALLOW: Set[str] = {
    "tone_forge.contracts",
    "tone_forge.stem_model",
}


def _iter_python_files(pkg_root: Path) -> Iterable[Path]:
    for dirpath, _dirnames, filenames in os.walk(pkg_root):
        for name in filenames:
            if name.endswith(".py"):
                yield Path(dirpath) / name


def _collect_tone_forge_imports(path: Path) -> Set[str]:
    """Return the set of ``tone_forge.*`` module references imported by ``path``.

    Captures both ``import tone_forge.X`` and ``from tone_forge.X import Y``.
    Only the top-level subpath under ``tone_forge`` is recorded
    (``tone_forge.tone.calibration`` → ``tone_forge.tone``) because
    boundaries are at the subsystem level, not the module level.
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    refs: Set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mod = alias.name
                if mod == "tone_forge" or mod.startswith("tone_forge."):
                    refs.add(_truncate_to_subsystem(mod))
        elif isinstance(node, ast.ImportFrom):
            # Skip relative imports — they cannot cross packages.
            if node.level and node.level > 0:
                continue
            mod = node.module or ""
            if mod == "tone_forge" or mod.startswith("tone_forge."):
                refs.add(_truncate_to_subsystem(mod))

    return refs


def _truncate_to_subsystem(dotted: str) -> str:
    """``tone_forge.tone.calibration.foo`` → ``tone_forge.tone``."""
    parts = dotted.split(".")
    if len(parts) <= 2:
        return dotted
    return ".".join(parts[:2])


@pytest.mark.parametrize("subsystem", sorted(SUBSYSTEMS.keys()))
def test_subsystem_imports_are_within_allowlist(subsystem: str) -> None:
    pkg_root = TONE_FORGE_ROOT / subsystem
    assert pkg_root.is_dir(), f"Missing subsystem package: {pkg_root}"

    own_module = f"tone_forge.{subsystem}"
    allow = UNIVERSAL_ALLOW | {own_module} | {
        f"tone_forge.{name}" for name in SUBSYSTEMS[subsystem]
    }

    violations: list[tuple[Path, str]] = []
    for py in _iter_python_files(pkg_root):
        for ref in _collect_tone_forge_imports(py):
            # Imports of the subsystem's own package are allowed.
            if ref == own_module or ref.startswith(own_module + "."):
                continue
            if ref in allow:
                continue
            violations.append((py.relative_to(TONE_FORGE_ROOT.parent), ref))

    assert not violations, (
        f"Subsystem '{subsystem}' has out-of-boundary imports.\n"
        + "\n".join(f"  {path}: {ref}" for path, ref in violations)
        + "\nCross-subsystem types must travel through tone_forge.contracts; "
        + "composition belongs in tone_forge_api."
    )


def test_all_declared_subsystems_exist() -> None:
    """Catch typos in SUBSYSTEMS: every entry must be a real package."""
    for name in SUBSYSTEMS:
        pkg = TONE_FORGE_ROOT / name / "__init__.py"
        assert pkg.is_file(), f"Declared subsystem has no __init__.py: {pkg}"
