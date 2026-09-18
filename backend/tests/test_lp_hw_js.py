"""Run the lp-hw.js function-button suite under Node.

lp-hw.js carries the WEB port of the desktop Launchpad Pro MK3
function-button contract (jam-desktop D-036, LaunchpadControlSurface):
the CC -> function assignment table and the control-LED state frame.
Desktop pins its half with LaunchpadControlSurfaceTests; this wrapper
runs the committed node script so the web half is pinned in the same CI
(parity rule 4 - identical buttons, identical semantics - rots silently
without it).

Skips cleanly when node isn't installed (GitHub ubuntu runners ship node).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_TEST_FILE = Path(__file__).resolve().parent.parent / "static" / "lp-hw.test.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_lp_hw_js_function_button_suite():
    proc = subprocess.run(
        ["node", str(_TEST_FILE)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, (
        f"lp-hw.js function-button suite failed:\n{proc.stdout}\n{proc.stderr}"
    )
