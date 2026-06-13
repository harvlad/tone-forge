"""Locks the chord-lane wireup on the local-engine path.

The Jam UI's chord ribbon reads ``result.chords``. There are two
independent backends that produce ``result``:

* ``tone_forge.unified_pipeline.UnifiedPipeline.analyze_streaming``
  (direct path, used when ``use_local_engine=False``).
* ``local_engine.analysis_worker.run_file_analysis``
  (subprocess path, used when ``use_local_engine=True``).

The unified path is already locked by
``tests/test_chord_lane_wireup.py``. This file locks the same
contract on the local-engine path: the worker must invoke
``tone_forge.analysis.detect_chords`` and surface the records into
the result dict under the key ``chords`` with the four-tuple shape
``(start_s, end_s, symbol, confidence)``.

We do NOT exercise ``run_file_analysis`` end-to-end. That function
spawns a multiprocessing subprocess, loads librosa, runs stem
separation, runs MIDI extraction, and depends on the local engine
runtime. Running it in a unit test would be either flaky or expensive
to make hermetic. The wireup is the regression surface; the detector
itself is already algorithmically tested elsewhere.

Test approach: source inspection. We read the module text and assert
that the chord step is present at the expected seam. This is brittle
to rename refactors — that is intentional. A rename that breaks the
ribbon should trip CI rather than silently ship.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_WORKER_PATH = (
    Path(__file__).resolve().parents[1]
    / "local_engine"
    / "analysis_worker.py"
)


@pytest.fixture(scope="module")
def worker_source() -> str:
    """Read the analysis_worker.py source once per module."""
    assert _WORKER_PATH.exists(), (
        f"local-engine worker missing at {_WORKER_PATH}; the chord-lane "
        "wireup test cannot run if the worker has moved"
    )
    return _WORKER_PATH.read_text()


# ---------------------------------------------------------------------------
# 1. The detect_chords symbol must be imported (otherwise no detection ran).
# ---------------------------------------------------------------------------


def test_worker_imports_detect_chords(worker_source: str) -> None:
    """The worker must import ``detect_chords`` from the analysis
    package. We accept either a top-of-file import or a deferred
    import inside the chord step; the latter is the pattern used
    by other stages in the file to keep cold-start cheap.
    """
    pattern = re.compile(
        r"from\s+tone_forge\.analysis\s+import\s+detect_chords"
    )
    assert pattern.search(worker_source), (
        "local-engine worker no longer imports detect_chords; "
        "chord ribbon will stay hidden on every local-engine run"
    )


# ---------------------------------------------------------------------------
# 2. The detector must actually be CALLED.
# ---------------------------------------------------------------------------


def test_worker_invokes_detect_chords(worker_source: str) -> None:
    """An import without a call is the silent regression we want to
    catch. Pin the call site.
    """
    assert re.search(r"detect_chords\s*\(", worker_source), (
        "detect_chords is imported but never called in the local-engine "
        "worker; the chord lane will not be populated"
    )


# ---------------------------------------------------------------------------
# 3. The result dict must carry the field the Jam UI reads.
# ---------------------------------------------------------------------------


def test_worker_result_dict_has_chords_key(worker_source: str) -> None:
    """The Jam UI reads ``result.chords`` (see backend/static/jam.js
    `buildChordRibbon(result.chords || [])`). Other naming would mean
    chords are detected but the ribbon stays empty.
    """
    # Match either "chords": (string-keyed) form. The worker uses
    # dict-literal construction.
    assert re.search(r'["\']chords["\']\s*:', worker_source), (
        "result dict in analysis_worker.py has no 'chords' key; "
        "Jam UI's `result.chords || []` will fall back to empty"
    )


# ---------------------------------------------------------------------------
# 4. A progress event for the chord stage must be emitted.
# ---------------------------------------------------------------------------


def test_worker_emits_chord_progress_event(worker_source: str) -> None:
    """Operators rely on SSE progress events to observe the pipeline.
    An absent ``chords`` progress event was the leading symptom that
    led to discovering the chord-lane gap on this path. Lock the
    event so the diagnostic surface stays visible.
    """
    # Match the send_progress call with the "chords" stage. We don't
    # pin percent or message — those are tunable.
    pattern = re.compile(
        r'send_progress\([^)]*["\']chords["\']', re.DOTALL
    )
    assert pattern.search(worker_source), (
        "no send_progress(..., 'chords', ...) call in the local-engine "
        "worker; the SSE 'Detecting chord lane...' event will not appear"
    )


# ---------------------------------------------------------------------------
# 5. Round-trip the four-tuple shape detect_chords emits, so a
#    future change to contracts.Chord can't drift away from what the
#    worker serializes.
# ---------------------------------------------------------------------------


def test_chord_dict_shape_matches_persisted_contract() -> None:
    """The persisted shape consumed by ``session.bundle._iter_chords``
    is ``{start_s, end_s, symbol, confidence}``. Verify
    ``detect_chords`` emits records with exactly those attributes;
    if a future refactor renames a field, the worker's dict
    comprehension will start emitting ``None`` and the bundle adapter
    will silently drop pills.
    """
    import numpy as np

    from tone_forge.analysis import detect_chords

    sr = 22050
    duration = 1.5
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    # C major triad — same fixture as the unified-pipeline test.
    triad = (
        np.sin(2 * np.pi * 261.63 * t)
        + np.sin(2 * np.pi * 329.63 * t)
        + np.sin(2 * np.pi * 392.00 * t)
    ).astype(np.float32) / 3.0

    chords = detect_chords(triad, sr, min_chord_duration_s=0.3)
    assert chords, "detect_chords returned nothing on a synthetic C triad"
    for c in chords:
        # The four attributes the worker reads on every record.
        assert hasattr(c, "start_s")
        assert hasattr(c, "end_s")
        assert hasattr(c, "symbol")
        assert hasattr(c, "confidence")
        assert isinstance(c.symbol, str) and c.symbol
        assert 0.0 <= float(c.confidence) <= 1.0
        assert float(c.end_s) > float(c.start_s)
