"""Locks the chord-detector confidence cutoff against silent regression.

The Jam chord ribbon stayed empty on every real-song session in
production because ``chord_detector.detect_chords_from_audio`` was
configured with a confidence cutoff of ``> 0.3``. The scoring
function is ``dot(chroma_norm, template_norm)`` with both vectors
L1-normalised, and for a 3-note triad template the *mathematical*
ceiling is 1/3 ≈ 0.333 — only reached when chroma energy is
concentrated entirely on the chord notes, which never happens for
real polyphonic audio.

Empirically, the Pub Feed full-mix capped at max confidence 0.249;
the isolated `other` stem at 0.219. With a 0.3 cutoff the ribbon
was 100% empty on every real song while still passing the
synthetic C-triad test in ``test_local_engine_chord_wireup.py``.

This file locks the calibration so a future refactor that nudges
the cutoff back above ~0.25 trips CI rather than silently shipping
an empty chord lane again.
"""
from __future__ import annotations

import numpy as np
import pytest

from tone_forge.analysis import detect_chords
from tone_forge.analysis import chord_detector


# ---------------------------------------------------------------------------
# Realistic synthetic mix fixture.
#
# Four-chord I-vi-IV-V progression in C (C / Am / F / G), each 2 seconds.
# Per chord we sum three triad voices and overlay the 2nd + 3rd harmonics
# of each at decaying amplitude — this mimics the chroma-flattening
# overtone structure of real instrument timbres. We then add 10% RMS
# broadband noise so the chroma distribution looks like a real polyphonic
# mix rather than a pristine sine bath.
#
# This fixture's per-segment confidence sits at ≈ 0.28 — comfortably
# above the new 0.18 cutoff and comfortably below the old 0.30 cutoff,
# i.e. it is in the realistic band the threshold change targets.
# ---------------------------------------------------------------------------

_SR = 22050
_CHORD_DUR_S = 2.0

_PROGRESSION = [
    ("C",  [261.63, 329.63, 392.00]),
    ("Am", [220.00, 261.63, 329.63]),
    ("F",  [174.61, 220.00, 261.63]),
    ("G",  [196.00, 246.94, 293.66]),
]


@pytest.fixture(scope="module")
def realistic_mix() -> np.ndarray:
    rng = np.random.default_rng(42)
    parts = []
    for _name, freqs in _PROGRESSION:
        t = np.linspace(0, _CHORD_DUR_S, int(_SR * _CHORD_DUR_S), endpoint=False)
        y = np.zeros_like(t)
        for f in freqs:
            # Fundamental + 2 harmonics (decaying amplitudes) per voice.
            y += np.sin(2 * np.pi * f * t)
            y += 0.5 * np.sin(2 * np.pi * 2 * f * t)
            y += 0.25 * np.sin(2 * np.pi * 3 * f * t)
        y /= len(freqs)
        parts.append(y)
    sig = np.concatenate(parts).astype(np.float32)
    noise = rng.standard_normal(len(sig)).astype(np.float32) * 0.10 * float(np.std(sig))
    sig = (sig + noise).astype(np.float32)
    sig /= float(np.max(np.abs(sig)) + 1e-9)
    return sig


# ---------------------------------------------------------------------------
# 1. The user-visible bug: detect_chords must return ≥1 chord on a
#    realistic mix. This is what the Jam ribbon depends on; if zero
#    pills come out, the ribbon stays hidden and the user sees nothing.
# ---------------------------------------------------------------------------


def test_realistic_mix_yields_at_least_one_chord(realistic_mix: np.ndarray) -> None:
    chords = detect_chords(realistic_mix, _SR)
    assert len(chords) >= 1, (
        "detect_chords returned no chords on a realistic synthetic mix; "
        "the Jam chord ribbon will be empty for every real song again. "
        "See chord_detector.py docstring for cutoff rationale."
    )


# ---------------------------------------------------------------------------
# 2. The full I-vi-IV-V should not collapse to one giant segment — we
#    expect a pill per chord region. Allow some slack (≥3) for boundary
#    detection jitter without locking the exact count.
# ---------------------------------------------------------------------------


def test_realistic_mix_yields_distinct_chord_regions(realistic_mix: np.ndarray) -> None:
    chords = detect_chords(realistic_mix, _SR)
    assert len(chords) >= 3, (
        f"expected the four-chord progression to surface as ≥3 distinct "
        f"pills (one per region, with some slack for boundary jitter), "
        f"got {len(chords)}: {[c.symbol for c in chords]}"
    )


# ---------------------------------------------------------------------------
# 3. Lock the calibration direction. The realistic mix scores in the
#    0.25–0.30 band; if a future refactor re-raises the cutoff above
#    that band the user-visible bug returns. We re-run the internal
#    routine with the old cutoff to demonstrate the regression surface
#    rather than just asserting a literal constant in source.
# ---------------------------------------------------------------------------


def test_old_threshold_would_have_filtered_everything(
    realistic_mix: np.ndarray,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the cutoff is moved back to 0.30, the realistic mix must
    produce zero chords. This is the failure mode we are guarding
    against — a "tighten the threshold" refactor that looks safe
    against the synthetic C-triad fixture but reintroduces an empty
    ribbon in production.
    """
    import librosa

    from tone_forge.analysis.chord_detector import _match_chord_template

    hop_length = 512
    chroma = librosa.feature.chroma_cqt(y=realistic_mix, sr=_SR, hop_length=hop_length)
    chroma_smooth = librosa.decompose.nn_filter(
        chroma, aggregate=np.median, metric="cosine"
    )
    chroma_diff = np.sum(np.abs(np.diff(chroma_smooth, axis=1)), axis=0)
    boundary_thr = float(np.mean(chroma_diff) + np.std(chroma_diff))
    min_frames = int(0.5 * _SR / hop_length)

    boundaries = [0]
    for i in range(1, len(chroma_diff)):
        if chroma_diff[i - 1] > boundary_thr and (i - boundaries[-1]) >= min_frames:
            boundaries.append(i)
    boundaries.append(chroma.shape[1])

    confs = []
    for i in range(len(boundaries) - 1):
        seg = np.mean(chroma_smooth[:, boundaries[i] : boundaries[i + 1]], axis=1)
        _root, _qual, conf = _match_chord_template(seg)
        confs.append(conf)

    passing_at_old_cutoff = sum(1 for c in confs if c > 0.30)
    assert passing_at_old_cutoff == 0, (
        f"realistic mix passes the OLD 0.30 cutoff in {passing_at_old_cutoff} "
        f"segment(s) — fixture is no longer in the regression band; max "
        f"confidence={max(confs):.3f}. Re-tune the fixture so it sits in the "
        f"0.20–0.29 confidence band where the production bug actually lives."
    )

    passing_at_new_cutoff = sum(1 for c in confs if c > 0.18)
    assert passing_at_new_cutoff >= 1, (
        f"realistic mix produces no segments above the new 0.18 cutoff "
        f"either (max confidence={max(confs):.3f}); the cutoff change "
        f"would not have fixed the user-visible bug for this signal class."
    )


# ---------------------------------------------------------------------------
# 4. The source constant itself. Belt-and-braces: an LLM-style refactor
#    that swaps the literal without re-running the realistic-mix test
#    is still caught by the regression test above; this assertion just
#    makes the *intent* visible at the constant site.
# ---------------------------------------------------------------------------


def test_chord_detector_source_uses_calibrated_cutoff() -> None:
    """The cutoff lives as a literal in chord_detector.py rather than
    as a named module constant (legacy code). Pin the literal so a
    'cleanup' that bumps it back to 0.3 trips here even if the
    realistic-mix test is skipped or marked xfail.
    """
    import inspect

    src = inspect.getsource(chord_detector.detect_chords_from_audio)
    assert "if confidence > 0.18" in src, (
        "detect_chords_from_audio no longer gates segments at the calibrated "
        "0.18 cutoff; the Jam chord ribbon will stay empty on real songs if "
        "the cutoff has been re-raised. See chord_detector.py:134 docstring."
    )


# ---------------------------------------------------------------------------
# 5. Segmenter density on steady-vamp audio.
#
# The Pub Feed bug class: songs whose chroma evolves smoothly (overdriven
# rock, slow transitions, drone) where the OLD chroma_diff peak-pick
# segmenter found ~1 boundary across the entire track and emitted ~1
# chord region. The 0.18 confidence cutoff was necessary but not
# sufficient — when the segmenter only emits one segment there's only
# one confidence to test.
#
# This fixture synthesises an 8-cycle A/D vamp (16 chord regions
# expected, 4s each, 64s total) with overdrive-style harmonics. A
# segmenter that bottlenecks on chroma-change peaks would collapse
# this to ~1 region; the fixed-window segmenter recovers all 16 cleanly.
#
# Lower bound: ≥6 regions (slack for boundary jitter / merge collapse).
# A regression that re-introduces the peak-pick segmenter will fail
# this with the same ~1-region output that reproduced in production.
# ---------------------------------------------------------------------------


def test_steady_vamp_yields_multiple_chord_regions() -> None:
    sr = 22050
    chord_dur_s = 4.0
    rng = np.random.default_rng(0)

    vamp = [
        ("A",  [220.00, 277.18, 329.63]),
        ("D",  [146.83, 220.00, 293.66]),
    ]

    parts = []
    for _ in range(8):
        for _name, freqs in vamp:
            t = np.linspace(0, chord_dur_s, int(sr * chord_dur_s), endpoint=False)
            y = np.zeros_like(t)
            for f in freqs:
                # Heavy harmonic content (overdrive proxy).
                for h, amp in [(1, 1.0), (2, 0.7), (3, 0.5), (4, 0.35), (5, 0.2)]:
                    y += amp * np.sin(2 * np.pi * f * h * t)
            y /= max(1.0, float(np.max(np.abs(y))))
            parts.append(y)
    sig = np.concatenate(parts).astype(np.float32)
    noise = rng.standard_normal(len(sig)).astype(np.float32) * 0.10 * float(np.std(sig))
    sig = (sig + noise).astype(np.float32)
    sig /= max(1e-9, float(np.max(np.abs(sig))))

    chords = detect_chords(sig, sr)

    assert len(chords) >= 6, (
        f"steady 8-cycle A/D vamp (16 ground-truth regions) collapsed to "
        f"{len(chords)} chord region(s): {[c.symbol for c in chords]}. "
        f"The chroma-diff peak-pick segmenter has been re-introduced; the "
        f"Jam chord ribbon will only show ~1 pill for any song with smooth "
        f"chord transitions (overdriven rock, drone, slow changes). See "
        f"chord_detector.py:108 docstring for the windowed-segmenter rationale."
    )
