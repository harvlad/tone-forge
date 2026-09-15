"""Run the padengine.js timing regression tests under Node.

padengine.js is the WEB reference implementation of the pad launch/phase-lock
algorithm (the native SampleScheduler/ChopPlayer are its ports, covered by the
Swift suites). Its logic had zero committed tests — the phase-lock saga was
verified with ad-hoc local sims that never reached CI. This wrapper runs the
committed node:test file so the web reference is pinned alongside the ports.

Skips cleanly when node isn't installed (GitHub ubuntu runners ship node).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_TEST_FILE = Path(__file__).resolve().parent.parent / "static" / "padengine.test.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_padengine_js_timing_suite():
    proc = subprocess.run(
        ["node", "--test", str(_TEST_FILE)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, (
        f"padengine.js regression suite failed:\n{proc.stdout}\n{proc.stderr}"
    )
