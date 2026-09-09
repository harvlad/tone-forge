"""Run the web UI's ``*.test.mjs`` suites as part of the backend gate.

``backend/static/`` ships nine hand-rolled node test suites (stage, kit,
lpview, sequencer, padengine, artwork, chopedit, jam-mixer, audio-context).
Until this file existed, nothing ran them: CI is ``ruff + pytest`` and the
suites are JavaScript, so they only ever ran when a developer remembered
to type ``node backend/static/foo.test.mjs``. Two web audio regressions
shipped past them.

Discovery is by glob, so a new ``*.test.mjs`` is picked up with no edit
here. Each suite becomes its own pytest case, and node's own output is
attached to the failure so the assertion message survives the hop.

The suites are deliberately dependency-free (plain ``node``, no npm, no
browser) precisely so this wrapper can stay this thin.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

STATIC_ROOT = Path(__file__).resolve().parents[1] / "static"
SUITES = sorted(STATIC_ROOT.glob("*.test.mjs"))

# A missing node must not silently pass. It skips (a Python dev without a
# node toolchain still gets a green backend run), but the count check
# below fails loudly if the suites themselves go missing or get renamed
# out of the glob.
NODE = shutil.which("node")

# Floor, not an exact count — adding a suite should not have to edit this
# file, but deleting them all should not quietly disable the gate.
MIN_EXPECTED_SUITES = 9


def test_static_js_suites_are_discovered() -> None:
    assert len(SUITES) >= MIN_EXPECTED_SUITES, (
        f"expected at least {MIN_EXPECTED_SUITES} *.test.mjs suites under "
        f"{STATIC_ROOT}, found {[p.name for p in SUITES]} — if a suite was "
        f"intentionally removed, lower MIN_EXPECTED_SUITES in the same commit"
    )


@pytest.mark.skipif(NODE is None, reason="node not installed")
@pytest.mark.parametrize("suite", SUITES, ids=lambda p: p.name)
def test_static_js_suite(suite: Path) -> None:
    proc = subprocess.run(
        [NODE, str(suite)],
        capture_output=True,
        text=True,
        cwd=STATIC_ROOT,
        timeout=120,
    )
    if proc.returncode != 0:
        pytest.fail(
            f"{suite.name} failed (exit {proc.returncode})\n"
            f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}",
            pytrace=False,
        )
