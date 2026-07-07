"""Shared pytest setup for backend API tests.

The retention loop must never run inside the test process — it would
race the per-test monkeypatched history file. The env var has to be set
before ``tone_forge_api`` starts (startup hook reads it), so do it at
conftest import time, before any test module imports the app.
"""

import os

os.environ.setdefault("TONEFORGE_DISABLE_RETENTION", "1")
