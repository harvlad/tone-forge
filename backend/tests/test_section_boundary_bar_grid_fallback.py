"""Bar-grid fallback in SectionDetector when no beat grid is supplied.

The Probe-5 wire-up made snap-to-beats the preferred quantizer, but the
legacy ``(60/tempo)*4`` bar grid is preserved as a fallback for:

  - the module-level ``detect_sections(audio)`` convenience function
    (no beat grid in its signature),
  - test fixtures and callers that construct a SectionDetector
    directly without running the beat-tracker stage,
  - the pipeline's belt-and-braces path when ``_track_beats`` fails
    (degraded ``beats_s`` with <2 entries).

This file pins three behaviours of that fallback:

1. **No-beats-supplied path:** ``detect_sections(audio)`` and
   ``_detect_boundaries(energy, duration, tempo)`` continue to return
   the same bar-aligned boundaries they did before Probe-5. Interior
   boundaries are integer multiples of ``(60/tempo)*4``.

2. **Degraded beat-grid path:** an empty list, ``None``, or a
   single-entry beat array all route to the bar-grid fallback rather
   than crashing. (A single beat can't define an interval.)

3. **Tempo-sensitivity of the fallback (sanity, not a regression):**
   the fallback path still consults ``tempo`` — confirming the path
   actually ran rather than silently emptying the boundary list.
"""
from __future__ import annotations

import numpy as np
import pytest

from tone_forge.analysis.sections import SectionDetector


def _square_energy_curve(
    duration_s: float = 60.0,
    resolution_s: float = 0.1,
    drop_times_s=(15.3, 30.7, 45.1),
) -> tuple[np.ndarray, float]:
    """Same step-pattern fixture as the snap-to-beats tests.

    Energy alternates between high (0.8) and low (0.1) on each drop
    time so that after smoothing in ``_detect_boundaries`` each
    transition produces a novelty peak strong enough to clear the
    ``mean + std`` threshold.
    """
    time_per_sample = resolution_s / 2
    n_samples = int(duration_s / time_per_sample)
    energy = np.empty(n_samples, dtype=np.float32)
    level = 0.8
    cursor = 0
    for t in drop_times_s:
        idx = int(t / time_per_sample)
        if idx <= cursor:
            continue
        if idx >= n_samples:
            idx = n_samples
        energy[cursor:idx] = level
        cursor = idx
        level = 0.1 if level > 0.5 else 0.8
    energy[cursor:] = level
    # Tiny deterministic ripple to break smoothed-novelty plateaus
    # (see ``test_section_boundary_snap_to_beats._square_energy_curve``
    # for the rationale).
    rng = np.random.default_rng(seed=0)
    energy = energy + rng.normal(0.0, 0.001, size=n_samples).astype(np.float32)
    return energy, duration_s


def _make_detector() -> SectionDetector:
    return SectionDetector(
        sr=22050,
        min_section_duration=4.0,
        max_section_duration=64.0,
        energy_resolution=0.1,
    )


def test_fallback_when_beats_s_is_none():
    """No beat grid → bar-grid quantization runs as before Probe-5."""
    detector = _make_detector()
    energy, duration = _square_energy_curve()
    tempo = 120.0
    bar_duration = (60.0 / tempo) * 4  # 2.0s

    boundaries = detector._detect_boundaries(
        energy, duration, tempo=tempo, beats_s=None,
    )

    # All interior boundaries land on the bar grid.
    for b in boundaries[1:-1]:
        bars = b / bar_duration
        assert abs(bars - round(bars)) < 1e-6, (
            f"interior boundary {b} not on the bar grid (bar_duration={bar_duration})"
        )


@pytest.mark.parametrize("beats", [[], [10.0]])
def test_fallback_when_beats_s_is_degraded(beats):
    """Empty list or single-entry list → bar-grid fallback (not a crash)."""
    detector = _make_detector()
    energy, duration = _square_energy_curve()
    tempo = 120.0
    bar_duration = (60.0 / tempo) * 4

    boundaries = detector._detect_boundaries(
        energy, duration, tempo=tempo, beats_s=np.asarray(beats),
    )

    # Same invariant as the None case.
    assert boundaries[0] == 0.0
    assert boundaries[-1] == duration
    for b in boundaries[1:-1]:
        bars = b / bar_duration
        assert abs(bars - round(bars)) < 1e-6


def test_fallback_path_is_tempo_sensitive():
    """Sanity: the fallback actually consults ``tempo``.

    Not a regression — it's the property we're moving *away* from. The
    test ensures we haven't accidentally short-circuited the bar-grid
    path to ignore tempo. (If this fires, the fallback isn't running.)
    """
    detector = _make_detector()
    energy, duration = _square_energy_curve()

    # 120 BPM → 2.0s bars → boundary at 16.0
    # 200 BPM → 1.2s bars → boundary at 15.6
    boundaries_120 = detector._detect_boundaries(
        energy, duration, tempo=120.0, beats_s=None,
    )
    boundaries_200 = detector._detect_boundaries(
        energy, duration, tempo=200.0, beats_s=None,
    )

    interior_120 = [b for b in boundaries_120 if 0 < b < duration]
    interior_200 = [b for b in boundaries_200 if 0 < b < duration]

    # The bar-grid path doesn't have to find the same number of
    # boundaries at every tempo (consolidation cascade interacts with
    # bar_duration), but the actual boundary times must differ — proving
    # the tempo argument propagated.
    assert interior_120 != interior_200, (
        "bar-grid fallback didn't consult tempo; the snap-to-beats "
        "branch may have swallowed the path."
    )


def test_module_level_detect_sections_still_runs_without_beats():
    """The ``detect_sections(audio)`` convenience function path.

    No ``beats_s`` parameter exists at the module level. The wrapper
    must route through SectionDetector.detect_sections with
    ``beats_s=None`` (i.e. the default), exercising the bar-grid
    fallback end-to-end.
    """
    from tone_forge.analysis.sections import detect_sections

    sr = 22050
    duration_s = 8.0
    # Trivial harmonic + percussive synthetic — enough for librosa
    # beat_track to produce a tempo and for the energy curve to be
    # populated without depending on a real file.
    t = np.arange(int(duration_s * sr)) / sr
    audio = 0.3 * np.sin(2 * np.pi * 220.0 * t).astype(np.float32)
    # Add periodic transients so the energy curve has structure.
    for i in range(int(duration_s * 2)):
        idx = int(i * 0.5 * sr)
        click_end = min(idx + int(0.02 * sr), len(audio))
        audio[idx:click_end] += 0.5

    result = detect_sections(audio, sr=sr, tempo=120.0)

    # Boundary contract: anchored ends + non-negative duration.
    assert result.duration > 0
    assert len(result.sections) >= 1
    assert result.sections[0].start_time == 0.0
    assert abs(result.sections[-1].end_time - result.duration) < 1e-6
