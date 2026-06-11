"""Endpoint contract test for ``POST /api/connect/restart``.

This endpoint is the server side of the §3D "Try restarting Connect"
browser CTA: the supervisor exhausts its bounded auto-restart budget
(``_MAX_RESTART_ATTEMPTS = 4``), the browser surfaces a button, and
clicking the button POSTs here to re-arm the supervisor and respawn
the helper.

The supervisor behaviour itself (stop / start / auto-restart /
budget reset) is already pinned by ``test_connect_bridge_lifecycle.py``.
This file pins the **endpoint contract**: shape, status code, and that
``restart()`` is the supervisor entry point we actually call.

What we pin
-----------

1. ``POST /api/connect/restart`` exists and returns 200.
2. The response body is a status-shaped dict with the keys the browser
   CTA reads — ``running``, ``last_error`` — so a future endpoint
   refactor that drops those fields fails here, not in the field.
3. The endpoint goes through ``get_supervisor().restart()`` (single
   entry point, no inline ``stop()`` + ``start()`` divergence) so that
   the supervisor's restart semantics — pending-restart cancel, budget
   reset, intent flip — apply uniformly.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from local_engine.server import app  # noqa: E402


def test_connect_restart_endpoint_returns_status_dict() -> None:
    """``POST /api/connect/restart`` returns 200 with a status dict
    that carries the keys the browser CTA branches on (``running``,
    ``last_error``). Drift-gate against an endpoint refactor that
    removes or renames those fields.
    """
    client = TestClient(app)
    # We don't actually want to spawn a real Connect helper in the
    # test process — patch the supervisor methods to no-ops so the
    # endpoint exercises its wiring without side effects.
    with patch("local_engine.server._get_connect_supervisor") as get_sup:
        sup = get_sup.return_value
        # restart() is the contract entry point we're pinning.
        sup.restart.return_value = None
        # _connect_status_dict() reads status() on the same supervisor.
        sup.status.return_value.running = False
        sup.status.return_value.pid = None
        sup.status.return_value.session_id = "default"
        sup.status.return_value.binary = None
        sup.status.return_value.last_error = None
        sup.status.return_value.log_path = "/tmp/connect-bridge.log"

        resp = client.post("/api/connect/restart")

    assert resp.status_code == 200
    data = resp.json()
    # Browser CTA branches on these two keys; missing either would
    # silently break the field-level UX.
    assert "running" in data, (
        "endpoint must surface `running` so the CTA can confirm "
        "whether the supervisor actually got the child up"
    )
    assert "last_error" in data, (
        "endpoint must surface `last_error` so the CTA can show "
        "the user a reason when the restart didn't take"
    )


def test_connect_restart_endpoint_calls_supervisor_restart() -> None:
    """Endpoint goes through ``ConnectSupervisor.restart()`` — not an
    inline ``stop()`` + ``start()`` pair. The supervisor's restart()
    is what cancels pending auto-restart timers, resets the attempt
    budget, and flips intent in one atomic-ish operation. Future
    refactors that bypass it would break the §3D contract silently.
    """
    client = TestClient(app)
    with patch("local_engine.server._get_connect_supervisor") as get_sup:
        sup = get_sup.return_value
        sup.restart.return_value = None
        sup.status.return_value.running = True
        sup.status.return_value.pid = 12345
        sup.status.return_value.session_id = "default"
        sup.status.return_value.binary = "/Applications/Connect.app/Contents/MacOS/Connect"
        sup.status.return_value.last_error = None
        sup.status.return_value.log_path = "/tmp/connect-bridge.log"

        resp = client.post("/api/connect/restart")

        assert resp.status_code == 200
        sup.restart.assert_called_once()
