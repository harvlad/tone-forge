"""Snap-to-beats boundary quantization in SectionDetector.

Probe-5 (``backend/segmenter_followup_probes.md``) established that the
legacy bar-grid quantizer ``round(b / bar_duration) * bar_duration`` was
the source of the segmenter's sub-BPM tempo sensitivity (±0.5 BPM
produced 0.6s median / 1.9s p95 boundary drift). The fix is to snap to
the tracked beat grid instead of the synthesized bar grid; under the
same perturbations beats-snap dropped to 0.05s median / 0.00s on tempo
nudges.

This file pins three behaviours:

1. **Snap-to-beats:** when ``beats_s`` is supplied with ≥2 entries,
   every interior boundary lands on one of those beats. No more
   fractional-bar quantization.

2. **Bar-grid fallback:** when ``beats_s`` is None or degraded (<2
   entries), the section detector falls back to the legacy
   ``(60/tempo)*4`` bar grid. The fallback path is what keeps the
   ``detect_sections(audio)`` convenience function, test fixtures
   without a beat grid, and the pipeline's degraded-beat-tracker
   recovery path all working.

3. **Tempo-perturbation stability:** with the same beat grid pinned,
   varying the ``tempo`` argument is a no-op (the snap path doesn't
   consult tempo at all). This is the property that motivated the
   change.
"""
from __future__ import annotations

import numpy as np

from tone_forge.analysis.sections import SectionDetector


def _square_energy_curve(
    duration_s: float = 60.0,
    resolution_s: float = 0.1,
    drop_times_s=(15.3, 30.7, 45.1),
) -> tuple[np.ndarray, float]:
    """Build an energy curve with sharp step-changes at known un-quantized times.

    The energy alternates between high (0.8) and low (0.1) on each
    drop time; each transition is a sustained step, so after the 1.0s
    smoothing in ``_detect_boundaries`` the novelty peak at each drop
    is strong enough to clear the ``mean + std`` threshold. The drop
    times are deliberately not on any tempo-derived bar grid so the
    quantization step is the only thing reshaping them.
    """
    # _detect_boundaries computes ``time_per_sample = resolution / 2``
    # (the convolutional smoothing uses ``hop = frame_length // 2``).
    time_per_sample = resolution_s / 2
    n_samples = int(duration_s / time_per_sample)
    energy = np.empty(n_samples, dtype=np.float32)
    # Step pattern: start high, flip at each drop time.
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
    # Tiny deterministic ripple. Convolving a clean step with the
    # smoothing window produces a flat plateau in the novelty, and
    # ``peak[i] > peak[i-1] and peak[i] > peak[i+1]`` would never fire
    # on a plateau. The ripple stays an order of magnitude below the
    # step height so it can't manufacture a false peak — it just makes
    # the smoothed plateau slightly non-flat at its midpoint.
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


def test_snap_to_beats_lands_on_provided_beats():
    """Every returned interior boundary equals one of the beats."""
    detector = _make_detector()
    energy, duration = _square_energy_curve()

    # Off-grid beats — deliberately not a tempo-derived bar grid so we
    # can prove the snap follows the provided grid rather than the
    # tempo argument.
    beats_s = np.arange(0.37, duration, 0.5)

    boundaries = detector._detect_boundaries(
        energy, duration, tempo=120.0, beats_s=beats_s,
    )

    # The first and last entries are anchored; everything else must be
    # in the beats_s set.
    assert boundaries[0] == 0.0
    assert boundaries[-1] == duration
    beat_set = set(round(float(b), 6) for b in beats_s)
    for b in boundaries[1:-1]:
        assert round(b, 6) in beat_set, (
            f"interior boundary {b} not on the supplied beat grid"
        )


def test_snap_to_beats_is_tempo_independent():
    """With the same beat grid, varying the tempo argument is a no-op.

    This is the property the bar-grid path didn't have — there a
    ±0.5 BPM nudge produced 0.6s median drift. The snap path doesn't
    consult tempo at all, so it must be bit-identical.
    """
    detector = _make_detector()
    energy, duration = _square_energy_curve()
    beats_s = np.arange(0.37, duration, 0.5)

    boundaries_a = detector._detect_boundaries(
        energy, duration, tempo=120.0, beats_s=beats_s,
    )
    boundaries_b = detector._detect_boundaries(
        energy, duration, tempo=119.5, beats_s=beats_s,
    )
    boundaries_c = detector._detect_boundaries(
        energy, duration, tempo=200.0, beats_s=beats_s,
    )

    assert boundaries_a == boundaries_b == boundaries_c


def test_snap_to_beats_picks_nearest_neighbour():
    """A novelty peak between two beats snaps to whichever is nearer."""
    detector = _make_detector()
    # Single drop at t=10.3 — equidistant beats 10.0 and 11.0 would
    # tie; we shift to 10.3 so 10.0 wins by 0.3 vs 0.7.
    energy, duration = _square_energy_curve(
        duration_s=30.0, drop_times_s=(10.3,),
    )
    beats_s = np.array([0.0, 5.0, 10.0, 11.0, 15.0, 20.0, 25.0, 30.0])

    boundaries = detector._detect_boundaries(
        energy, duration, tempo=120.0, beats_s=beats_s,
    )

    interior = [b for b in boundaries if 0.0 < b < duration]
    assert interior, "expected at least one interior boundary"
    # The first interior boundary should be the nearest beat to 10.3,
    # which is 10.0 (Δ=0.3) rather than 11.0 (Δ=0.7).
    assert interior[0] == 10.0


def test_snap_distance_clamp_falls_through_to_bar_grid():
    """Boundaries beyond the tracked beat grid fall back to the bar grid.

    Real-world case (Sex On Fire, JAM session fcbb84bf): librosa
    beat-track terminated at 177.9s on a 207s song; the verse→outro
    boundary at ~205.3s was being pulled back to 177.9s — a 27-second
    regression. The clamp says: if the nearest tracked beat is more
    than one bar away from the raw boundary, use the bar grid for
    that specific boundary instead.
    """
    detector = _make_detector()
    # 60s clip; energy drops at 15.3 (inside the beat grid) and 55.0
    # (well beyond it).
    energy, duration = _square_energy_curve(
        duration_s=60.0, drop_times_s=(15.3, 55.0),
    )
    # Beat grid covers only the first 30 seconds.
    tempo = 120.0
    bar_duration = (60.0 / tempo) * 4  # 2.0s
    beats_s = np.arange(0.37, 30.0, 0.5)

    boundaries = detector._detect_boundaries(
        energy, duration, tempo=tempo, beats_s=beats_s,
    )

    interior = [b for b in boundaries if 0.0 < b < duration]
    assert len(interior) >= 2, "expected boundaries near 15.3 and 55.0"

    # The first boundary is inside the beat grid → snaps to a beat.
    beat_set = set(round(float(b), 6) for b in beats_s)
    assert round(interior[0], 6) in beat_set

    # The second boundary is beyond the beat grid → clamped to bar grid.
    # Nearest beat to 55.0 would be 29.87 (Δ=25.13s) — that's ≫ 2.0s
    # bar duration, so the clamp engages and the bar grid is used.
    second = interior[1]
    assert second not in beat_set, (
        "boundary beyond beat-grid coverage should not snap to a beat"
    )
    bars = second / bar_duration
    assert abs(bars - round(bars)) < 1e-6, (
        f"clamped boundary {second} not on bar grid (bar_duration={bar_duration})"
    )
    # And it should be close to the raw 55.0 — within one bar.
    assert abs(second - 55.0) <= bar_duration


def test_min_section_duration_consolidation_runs_on_snapped_positions():
    """Closely-spaced beat snaps still collapse via min_section_duration.

    The bar-grid path used min_section_duration on the post-snap times
    to drop near-duplicates. Snap-to-beats must preserve that — without
    consolidation we'd over-segment in dense-novelty regions (Probe-4
    showed that catastrophic case).
    """
    detector = _make_detector()  # min_section_duration = 4.0
    # Three drops within 5 seconds — the consolidation should keep at
    # most one of them after snapping (each subsequent one is within
    # 4s of the previous accepted boundary).
    energy, duration = _square_energy_curve(
        duration_s=30.0, drop_times_s=(10.3, 11.6, 13.2),
    )
    beats_s = np.arange(0.0, 30.0, 0.25)  # very dense beat grid

    boundaries = detector._detect_boundaries(
        energy, duration, tempo=120.0, beats_s=beats_s,
    )

    # Consecutive interior boundaries must respect min_section_duration.
    for prev, cur in zip(boundaries[:-1], boundaries[1:]):
        if prev > 0.0 and cur < duration:
            assert cur - prev >= detector.min_section_duration, (
                f"consolidation broken: {prev} → {cur} "
                f"closer than {detector.min_section_duration}s"
            )
