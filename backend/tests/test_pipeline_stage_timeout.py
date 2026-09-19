"""Per-stage timeout guard + onnx CUDA fail-fast (crate pipeline hang fix).

RCA these pin: a crate canary (A40) froze for 27+ min with the GPU at 0% and
the CPU idle — a heavy analysis stage BLOCKED (a C-level onnxruntime CUDA
provider init / lock / subprocess wait), which a try/except can't catch. The
fix wraps each heavy stage in a wall-clock timeout so a hung stage is dropped,
the TRACK continues, and the pod never wedges — and fails the onnx CUDA
provider fast to CPU so MIDI degrades-but-completes instead of hanging.

These tests assert the guard's contract WITHOUT any real audio/GPU:
  - happy path returns the result, no timeout fires;
  - a genuinely-hung stage raises StageTimeout and the pipeline stays usable;
  - StageTimeout is an Exception subclass so the existing per-stage
    `except Exception` drops just the one stage;
  - the config carries generous, env-tunable timeouts;
  - the onnx guard is memoized/best-effort and its CPU-forcing patch works.
"""
from __future__ import annotations

import asyncio
import threading
import time

import pytest

from tone_forge import unified_pipeline as up
from tone_forge.unified_pipeline import (
    PipelineConfig,
    StageTimeout,
    ensure_onnx_provider_ready,
    run_stage_with_timeout,
)


# ---------------------------------------------------------------------------
# run_stage_with_timeout — happy path is unchanged
# ---------------------------------------------------------------------------

def test_happy_path_returns_result_no_timeout():
    """A fast stage returns its value and never trips the timeout."""
    def work():
        return {"note_count": 7}

    out = asyncio.run(
        run_stage_with_timeout(work, stage="midi_ensemble", stem="other", timeout_s=5.0)
    )
    assert out == {"note_count": 7}


def test_real_exception_propagates_not_swallowed():
    """A stage that RAISES surfaces its real error (so callers log the cause),
    and it is NOT a StageTimeout."""
    def boom():
        raise ValueError("kaboom")

    with pytest.raises(ValueError, match="kaboom"):
        asyncio.run(
            run_stage_with_timeout(boom, stage="quality", stem="bass", timeout_s=5.0)
        )


# ---------------------------------------------------------------------------
# run_stage_with_timeout — a HANG is bounded and the pipeline stays usable
# ---------------------------------------------------------------------------

def test_hung_stage_raises_stage_timeout_and_pipeline_recovers():
    """A stage that blocks past its budget raises StageTimeout quickly, and a
    subsequent stage still runs — i.e. one hung stage can't wedge the pod."""
    release = threading.Event()

    def hang():
        # Simulates a blocked C-level call (onnx CUDA init / lock / subprocess).
        release.wait(30)
        return "should-never-be-used"

    async def scenario():
        t0 = time.time()
        with pytest.raises(StageTimeout):
            await run_stage_with_timeout(
                hang, stage="midi_ensemble", stem="guitar", timeout_s=0.3
            )
        elapsed = time.time() - t0
        # Bounded by the budget, not by the 30s hang.
        assert elapsed < 5.0
        # The event loop is still alive and can run the next stage.
        return await run_stage_with_timeout(
            lambda: "next-track-ok", stage="quality", stem="drums", timeout_s=5.0
        )

    try:
        assert asyncio.run(scenario()) == "next-track-ok"
    finally:
        release.set()  # let the leaked daemon thread exit


def test_stage_timeout_is_exception_subclass():
    """Existing per-stage `except Exception:` guards must catch a timeout with
    no new branch — so the stage is dropped and the track proceeds."""
    assert issubclass(StageTimeout, Exception)


def test_zero_timeout_disables_the_guard():
    """timeout_s<=0 falls straight through to run_in_thread (no daemon/guard)."""
    out = asyncio.run(
        run_stage_with_timeout(lambda: 42, stage="quality", timeout_s=0)
    )
    assert out == 42


# ---------------------------------------------------------------------------
# PipelineConfig — generous, env-tunable timeouts on every factory
# ---------------------------------------------------------------------------

def test_config_carries_generous_timeouts():
    for cfg in (PipelineConfig.crate(), PipelineConfig.deep(), PipelineConfig.standard()):
        assert cfg.stage_timeout_s >= 300
        assert cfg.midi_timeout_s >= 600


def test_timeout_defaults_are_env_tunable(monkeypatch):
    """The module reads TONEFORGE_*_TIMEOUT_S at import; reloading with the env
    set proves a canary can shrink the budgets without a code edit."""
    import importlib

    monkeypatch.setenv("TONEFORGE_STAGE_TIMEOUT_S", "12")
    monkeypatch.setenv("TONEFORGE_MIDI_TIMEOUT_S", "34")
    mod = importlib.reload(up)
    try:
        cfg = mod.PipelineConfig.deep()
        assert cfg.stage_timeout_s == 12
        assert cfg.midi_timeout_s == 34
    finally:
        # Restore module-level defaults for the rest of the suite.
        monkeypatch.delenv("TONEFORGE_STAGE_TIMEOUT_S", raising=False)
        monkeypatch.delenv("TONEFORGE_MIDI_TIMEOUT_S", raising=False)
        importlib.reload(up)


# ---------------------------------------------------------------------------
# ONNX CUDA fail-fast guard
# ---------------------------------------------------------------------------

def test_onnx_guard_is_memoized_and_best_effort():
    """Best-effort: never raises, and runs the probe at most once per process."""
    first = ensure_onnx_provider_ready(probe_timeout_s=1.0)
    second = ensure_onnx_provider_ready(probe_timeout_s=1.0)
    assert isinstance(first, str) and first == second


def test_force_onnx_cpu_coerces_providers_to_cpu():
    """The fail-fast patch must rewrite any provider request to CPU-only, so a
    broken CUDA EP can't be re-selected by basic_pitch after the guard fires."""
    captured = {}

    class FakeSession:
        def __init__(self, path_or_bytes, sess_options=None,
                     providers=None, provider_options=None, **kw):
            captured["providers"] = providers

    class FakeOrt:
        InferenceSession = FakeSession

    up._force_onnx_cpu(FakeOrt)
    FakeOrt.InferenceSession("model.onnx",
                             providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
    assert captured["providers"] == ["CPUExecutionProvider"]
    # Idempotent: patching twice must not double-wrap / recurse.
    up._force_onnx_cpu(FakeOrt)
    assert getattr(FakeOrt.InferenceSession.__init__, "_jamn_cpu_forced", False)
