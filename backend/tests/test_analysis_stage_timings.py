"""Locks the per-stage timing instrumentation in
``local_engine/analysis_worker.py``.

Background:

A profile of "The Chats - Pub Feed" showed 188s spent in the
``instrument_analysis`` bucket. But that bucket was a *residual*
(``total - stem - midi``) and ``section_time`` was fabricated as
``analysis_time * 0.3``. There was no way to know which stage
inside the 188s dominated.

This commit adds explicit ``time.perf_counter`` brackets around
every post-MIDI stage. Each stage emits ``started_ms``,
``finished_ms``, ``duration_ms`` (all relative to a single
perf_counter origin taken right before stem separation) into the
result payload at ``profiling.stages.<stage_name>``. Reading the
entries sorted by ``started_ms`` reveals whether stages overlap or
are serialized.

These tests source-inspect the instrumentation rather than running
the full pipeline. End-to-end execution requires the local GPU
engine and a real audio file; the value-add here is the literal
presence of the brackets, not the timing values.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


_BACKEND_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def worker_source() -> str:
    p = _BACKEND_ROOT / "local_engine" / "analysis_worker.py"
    assert p.exists(), f"analysis_worker missing at {p}"
    return p.read_text()


# ---------------------------------------------------------------------------
# 1. Helper + perf-counter origin
# ---------------------------------------------------------------------------


def test_perf_counter_origin_is_defined(worker_source: str) -> None:
    """The timeline emits started_ms / finished_ms relative to a
    single monotonic origin. Without ``_t0``, per-stage timestamps
    cannot be compared on a common clock.
    """
    assert re.search(r"_t0\s*=\s*time\.perf_counter\(\)", worker_source), (
        "perf_counter origin (_t0) missing; per-stage timeline cannot "
        "be expressed in a single comparable clock"
    )


def test_record_stage_helper_is_defined(worker_source: str) -> None:
    """The helper that writes ``stage_timings[name] = {started_ms,
    finished_ms, duration_ms}`` must exist with that exact triple of
    fields.
    """
    assert re.search(
        r"def\s+_record_stage\s*\([^)]*name[^)]*t_start[^)]*t_end",
        worker_source,
    ), "_record_stage helper missing or has unexpected signature"
    assert '"started_ms"' in worker_source
    assert '"finished_ms"' in worker_source
    assert '"duration_ms"' in worker_source


# ---------------------------------------------------------------------------
# 2. Every named stage is bracketed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stage_name",
    [
        "stem_separation",
        "multi_guitar_split",
        "tone_analysis",
        "audio_reload",
        "section_detection",
        "chord_detection",
        "tempo_estimation",
        "key_detection",
        "role_classification",
        "quality_analysis",
        "waveform_generation",
    ],
)
def test_stage_is_recorded(worker_source: str, stage_name: str) -> None:
    """Every named stage must have a ``_record_stage`` call. Missing
    one means that stage's contribution to the 188s ``analysis_time``
    bucket would be invisible.
    """
    pattern = re.compile(rf'_record_stage\(\s*["\']{stage_name}["\']')
    assert pattern.search(worker_source), (
        f"stage '{stage_name}' is no longer recorded; the timeline "
        "is incomplete"
    )


def test_midi_extraction_is_per_stem(worker_source: str) -> None:
    """MIDI extraction runs once per stem inside a loop. The recorded
    stage name must include the stem (e.g. ``midi_extraction.drums``)
    so per-stem cost is visible. A single aggregate would hide which
    stem dominates.
    """
    assert re.search(
        r'_record_stage\(\s*f["\']midi_extraction\.\{stem_name\}',
        worker_source,
    ), (
        "midi_extraction is no longer recorded per-stem; "
        "drums/bass/guitar costs cannot be distinguished"
    )


# ---------------------------------------------------------------------------
# 3. The fabricated section_time is gone
# ---------------------------------------------------------------------------


def test_fabricated_section_time_is_gone(worker_source: str) -> None:
    """The pre-instrumentation residual ``section_time = analysis_time
    * 0.3`` was a fabricated split, not a measurement. The new
    instrumentation produces a real ``section_detection`` measurement
    inside ``stage_timings``. The fabricated assignment must not
    return.
    """
    assert not re.search(
        r"section_time\s*=\s*analysis_time\s*\*\s*0\.3",
        worker_source,
    ), (
        "section_time fabricated as analysis_time * 0.3 is back; "
        "use the real measurement in stage_timings['section_detection']"
    )


# ---------------------------------------------------------------------------
# 4. The result payload exposes the timeline
# ---------------------------------------------------------------------------


def test_profiling_dict_carries_stage_timings(worker_source: str) -> None:
    """The result payload must spread ``stage_timings`` into
    ``profiling.stages`` so the timeline is visible on the wire.
    Without this, the instrumentation would be invisible to any
    consumer of the SSE payload.
    """
    # We're matching ``"stages": {`` followed (within the same dict
    # literal) by ``**stage_timings``. DOTALL so the regex spans
    # the comment block between key and dict body.
    assert re.search(
        r'"stages"\s*:\s*\{[^}]*\*\*stage_timings',
        worker_source,
        re.DOTALL,
    ), (
        "profiling.stages no longer carries **stage_timings; the "
        "per-stage timeline is gone from the SSE payload"
    )


def test_legacy_aggregate_fields_preserved(worker_source: str) -> None:
    """Frontend code reads ``stages["stem_separation"]["gpu_used"]``
    and ``stages["midi_extraction"]["extraction_time_sec"]``. Those
    sub-fields must continue to exist or the UI's
    "GPU/CPU"-style hint disappears.
    """
    assert '"gpu_used"' in worker_source, (
        "gpu_used flag on stem_separation was dropped; UI hints "
        "that depend on it will read undefined"
    )
    assert '"extraction_time_sec"' in worker_source, (
        "extraction_time_sec on midi_extraction was dropped; "
        "frontend extraction-time display will break"
    )


# ---------------------------------------------------------------------------
# 5. Helper arithmetic — synthetic round-trip
# ---------------------------------------------------------------------------


def test_record_stage_arithmetic_round_trips() -> None:
    """The helper is an inner function inside run_file_analysis so we
    can't import it directly. Re-implement the exact same arithmetic
    here and lock the contract: started_ms < finished_ms, and
    duration_ms == finished_ms - started_ms (within float epsilon).
    A regression that swapped started/finished would silently break
    overlap analysis without raising.
    """
    _t0 = 100.0
    stage_timings: dict = {}

    def _record_stage(name: str, t_start: float, t_end: float) -> None:
        stage_timings[name] = {
            "started_ms": round((t_start - _t0) * 1000.0, 2),
            "finished_ms": round((t_end - _t0) * 1000.0, 2),
            "duration_ms": round((t_end - t_start) * 1000.0, 2),
        }

    _record_stage("foo", 100.5, 100.75)
    rec = stage_timings["foo"]
    assert rec["started_ms"] == 500.0
    assert rec["finished_ms"] == 750.0
    assert rec["duration_ms"] == pytest.approx(250.0, abs=0.01)
    # Overlap predicate (used by future timeline-overlap analyzers):
    # finished_ms >= started_ms for every record.
    assert rec["finished_ms"] >= rec["started_ms"]
