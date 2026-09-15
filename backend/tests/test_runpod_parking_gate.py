"""Idle scale-down: park (stop) vs terminate.

Parking stops one pod for a fast resume, but a *stopped* RunPod pod still
bills for its 40+60 GB disk 24/7 -- a standing ~$4.76/day "regardless of
analysis" that drained the account. RUNPOD_PARK=0 opts into pure
pay-per-analysis: idle scale-down terminates everything, $0 while idle.

These pin that the gate actually changes the scale-down action.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from local_engine import runpod_autoscaler as autoscale


@pytest.fixture()
def idle_single_pod(monkeypatch):
    """Enabled autoscaler, one RUNNING pod, past the idle window, floor 0."""
    monkeypatch.setenv("RUNPOD_AUTOSCALE", "1")
    monkeypatch.setenv("RUNPOD_API_KEY", "rpa_test")
    monkeypatch.setenv("RUNPOD_IDLE_MINUTES", "0")   # idle immediately
    monkeypatch.delenv("RUNPOD_MIN_WARM", raising=False)  # floor 0
    monkeypatch.setattr(autoscale, "_last_active_ts", 0.0)  # long idle
    monkeypatch.setattr(autoscale, "list_worker_pods",
                        lambda: [{"id": "pod1", "desiredStatus": "RUNNING"}])
    monkeypatch.setattr(autoscale, "_load_parked", lambda: [])

    calls = {"park": [], "delete": []}
    monkeypatch.setattr(autoscale, "_park_pod",
                        lambda pid: calls["park"].append(pid) or True)

    class _Resp:
        status_code = 200
        text = ""

    def _fake_delete(url, *a, **k):
        calls["delete"].append(url)
        return _Resp()

    monkeypatch.setattr(autoscale.requests, "delete", _fake_delete)
    return calls


def test_default_parks_the_last_pod(idle_single_pod, monkeypatch):
    monkeypatch.delenv("RUNPOD_PARK", raising=False)  # default -> park
    autoscale.scale_down_if_idle(has_pending_or_running=False)
    assert idle_single_pod["park"] == ["pod1"]
    assert idle_single_pod["delete"] == []          # not terminated


def test_park_disabled_terminates_the_last_pod(idle_single_pod, monkeypatch):
    monkeypatch.setenv("RUNPOD_PARK", "0")           # pay-per-analysis
    autoscale.scale_down_if_idle(has_pending_or_running=False)
    assert idle_single_pod["park"] == []             # never parked
    assert any("pod1" in u for u in idle_single_pod["delete"])  # terminated


def test_park_enabled_reads_env(monkeypatch):
    monkeypatch.delenv("RUNPOD_PARK", raising=False)
    assert autoscale._park_enabled() is True
    monkeypatch.setenv("RUNPOD_PARK", "0")
    assert autoscale._park_enabled() is False


def test_stall_grace_covers_a_cold_boot():
    """The reaper grace must exceed a real cold boot (image pull + model
    download can pass 5 min) or booting pods get reaped and jobs deadlock
    in a reap->replace loop — observed live 2026-09-14 at the old 300s."""
    import tone_forge_api as api

    assert api._WORKER_STALL_GRACE_SEC >= 900.0


def test_volume_pinned_create_falls_back_to_any_datacenter(monkeypatch):
    """A region-locked network volume pins pods to one DC; when that DC has
    no capacity the create 500s and, without the fallback, every retry fails
    and jobs strand (EU-RO-1 had no A40 the day the volume shipped). The
    second attempt must drop the volume/DC pin and restore the ephemeral
    pod-local volume."""
    monkeypatch.setenv("RUNPOD_AUTOSCALE", "1")
    monkeypatch.setenv("RUNPOD_API_KEY", "rpa_test")
    monkeypatch.setenv("RUNPOD_NETWORK_VOLUME_ID", "vol123")
    monkeypatch.setenv("RUNPOD_DATACENTER", "EU-RO-1")
    monkeypatch.delenv("RUNPOD_MIN_WARM", raising=False)
    # Neutralize live-API touchpoints + create guardrails.
    monkeypatch.setattr(autoscale, "list_worker_pods", lambda: [])
    monkeypatch.setattr(autoscale, "_reap_exited_pods", lambda: None)
    monkeypatch.setattr(autoscale, "_load_parked", lambda: [])
    monkeypatch.setattr(autoscale, "_last_create_ts", 0.0)
    monkeypatch.setattr(autoscale, "_create_history", [])

    bodies = []

    class _Resp:
        def __init__(self, code, payload=None):
            self.status_code = code
            self._payload = payload or {}
            self.text = ""

        def json(self):
            return self._payload

    def _fake_post(url, headers=None, json=None, timeout=None):
        bodies.append(json)
        # First (volume-pinned) create fails on capacity; retry succeeds.
        if len(bodies) == 1:
            return _Resp(500)
        return _Resp(201, {"id": "pod-fallback"})

    monkeypatch.setattr(autoscale.requests, "post", _fake_post)

    assert autoscale.ensure_worker(1) == "pod-fallback"
    assert len(bodies) == 2
    assert bodies[0].get("networkVolumeId") == "vol123"
    assert bodies[0].get("dataCenterIds") == ["EU-RO-1"]
    assert "networkVolumeId" not in bodies[1]
    assert "dataCenterIds" not in bodies[1]
    assert bodies[1].get("volumeInGb") == 60  # ephemeral volume restored
