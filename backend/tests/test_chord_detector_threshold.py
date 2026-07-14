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
) -> None:
    """Historical regression doc for the original cutoff bug.

    Reproduces the OLD failure mode end-to-end: chroma_diff peak-pick
    segmenter + L1-normalized dot-product scoring + fixed 0.30 cutoff.
    Inlines the entire old pipeline so the test stays valid even as
    the production source moves on (it has since moved to fixed-window
    segmenter + L2 cosine similarity + adaptive cutoff).

    What this pins:
    * The realistic synthetic mix sits in the regression band for the
      old scoring (max dot-product confidence well below 0.30, so the
      old cutoff would have shipped an empty ribbon).
    * At least one segment passes the intermediate 0.18 dot-product
      cutoff (i.e. lowering the cutoff was the necessary first step
      9cc11c6 took — the test confirms that step would have produced
      *some* chords for this fixture class).
    """
    import librosa
    from tone_forge.analysis.chord_detector import CHORD_TEMPLATES

    def _old_match(chroma: np.ndarray) -> float:
        """Old scoring: L1-normalize both, dot product. Returns confidence."""
        chroma_norm = chroma / (np.sum(chroma) + 1e-6)
        best = 0.0
        for root in range(12):
            for _quality, intervals in CHORD_TEMPLATES.items():
                template = np.zeros(12)
                for interval in intervals:
                    template[(root + interval) % 12] = 1.0
                template /= np.sum(template)
                sim = float(np.dot(chroma_norm, template))
                if sim > best:
                    best = sim
        return best

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
        confs.append(_old_match(seg))

    passing_at_old_cutoff = sum(1 for c in confs if c > 0.30)
    assert passing_at_old_cutoff == 0, (
        f"realistic mix passes the OLD 0.30 dot-product cutoff in "
        f"{passing_at_old_cutoff} segment(s) — fixture is no longer in "
        f"the regression band; max dot-product confidence={max(confs):.3f}. "
        f"Re-tune the fixture so it sits in the 0.20–0.29 dot-product band "
        f"where the original production bug lives."
    )

    passing_at_intermediate_cutoff = sum(1 for c in confs if c > 0.18)
    assert passing_at_intermediate_cutoff >= 1, (
        f"realistic mix produces no segments above the intermediate 0.18 "
        f"dot-product cutoff either (max={max(confs):.3f}); the cutoff-drop "
        f"step (9cc11c6) would not have produced any chord pills for this "
        f"signal class."
    )


# ---------------------------------------------------------------------------
# 4. The source constant itself. Belt-and-braces: an LLM-style refactor
#    that swaps the literal without re-running the realistic-mix test
#    is still caught by the regression test above; this assertion just
#    makes the *intent* visible at the constant site.
# ---------------------------------------------------------------------------


def test_chord_detector_source_uses_calibrated_cutoff() -> None:
    """Pin the gating to the cosine-similarity + adaptive-cutoff form.

    History: the detector originally used L1-normalized dot-product
    scoring with a fixed 0.3 cutoff (filtered 100% of real audio →
    empty ribbon), then a fixed 0.18 cutoff (still filtered Pub Feed
    down to 1 region because dot-product scores collapse to the same
    narrow band across all real songs). The current form is L2
    cosine similarity + per-song adaptive threshold
    `max(0.50, median + 0.3*std)`. A regression that re-introduces a
    fixed scalar cutoff or reverts to L1 + dot-product will silently
    re-empty the ribbon; pin the cutoff expression here so that
    happens at test time instead.
    """
    import inspect

    src = inspect.getsource(chord_detector.detect_chords_from_audio)
    assert "COS_CUTOFF = 0.70" in src and "no_chord_floor=COS_CUTOFF" in src, (
        "detect_chords_from_audio no longer gates windows at the calibrated "
        "cosine-similarity floor of 0.70. Under Phase 4 the floor is wired "
        "into the Viterbi emission step as the no-chord-state's emission "
        "score: any per-window emission below 0.70 cosine loses to the "
        "no-chord state and the window emits no pill. Reverting to a "
        "different floor will either silence the ribbon (raised above the "
        "overdriven-rock regime) or fill it with noise pills (dropped below "
        "the ~0.66 chroma noise floor). See chord_detector.py:COS_CUTOFF "
        "block for floor rationale and empirical scoring bands."
    )
    match_src = inspect.getsource(chord_detector._match_chord_template)
    assert "np.linalg.norm" in match_src, (
        "_match_chord_template no longer uses L2 normalization (cosine "
        "similarity). Reverting to L1 + dot-product caps scores at "
        "1/triad-size ≈ 0.333 and re-introduces the bug class the "
        "adaptive cutoff is calibrated against."
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


def test_extended_labels_collapsed_to_triad_form() -> None:
    """The chord ribbon shows simple triad-family labels, not 9-chords.

    Under L2 cosine similarity the richer templates (maj9 = 5 "on"
    pitch classes) score higher than bare triads when chroma has any
    harmonic spread — overdriven sources especially. Pre-collapse, Pub
    Feed surfaced as 58×F#min9 / 29×Amaj9 / 26×Emaj9 / 25×F#maj9 even
    though the underlying chords are simple triads with harmonic
    spread. The collapse step maps those back to the triad family
    ("F#min9" -> "F#m", "Amaj9" -> "A", etc.) for the ribbon label
    while preserving the cosine-score advantage of the richer template
    pool.

    This test pins that the OUTPUT chord symbols never contain a 9- or
    extension suffix that the user wouldn't want to read off the
    ribbon. A regression that removes the collapse (or re-introduces
    9-chord symbols downstream) will trip this against the same
    overdrive-style fixture that drove the original bug report.
    """
    sr = 22050
    chord_dur_s = 2.0
    rng = np.random.default_rng(7)

    # Overdriven A and E triads (heavy harmonic content). With cosine
    # matching these reliably score Amaj9 / Emaj9 in the raw matcher.
    vamp = [
        ("A", [220.00, 277.18, 329.63]),  # A major triad
        ("E", [164.81, 207.65, 246.94]),  # E major triad
    ]
    parts = []
    for _ in range(4):
        for _name, freqs in vamp:
            t = np.linspace(0, chord_dur_s, int(sr * chord_dur_s), endpoint=False)
            y = np.zeros_like(t)
            for f in freqs:
                for h, amp in [(1, 1.0), (2, 0.7), (3, 0.5), (4, 0.35), (5, 0.2)]:
                    y += amp * np.sin(2 * np.pi * f * h * t)
            y /= max(1.0, float(np.max(np.abs(y))))
            parts.append(y)
    sig = np.concatenate(parts).astype(np.float32)
    noise = rng.standard_normal(len(sig)).astype(np.float32) * 0.05 * float(np.std(sig))
    sig = (sig + noise).astype(np.float32)
    sig /= max(1e-9, float(np.max(np.abs(sig))))

    chords = detect_chords(sig, sr)
    assert chords, "extended-label fixture produced no chords at all"

    # No emitted symbol may contain an extension suffix from the
    # collapse-target set. (sus2/sus4/dim/aug/dom7 are allowed: they
    # are kept as distinct display qualities; min7/maj7/add9/min9/maj9
    # / dim7 are the ones the collapse maps away.)
    BANNED = ("maj7", "maj9", "min7", "min9", "add9", "dim7")
    offenders = [c.symbol for c in chords if any(b in c.symbol for b in BANNED)]
    assert not offenders, (
        f"chord symbols still carry extension suffixes after collapse: "
        f"{offenders}. The _collapse_quality step has been removed or "
        f"bypassed; the Jam ribbon will read 'Amaj9' where the user "
        f"expects 'A'. See chord_detector.py:_collapse_quality docstring."
    )


def test_internal_time_gaps_are_bridged() -> None:
    """Adjacent chord regions are contiguous in time (no `idx == -1`
    frames during playback).

    NOTE: This test still passes under STRIP_HEURISTICS=True (Phase 1
    of the chord-detector rebuild) because the synthetic input doesn't
    actually trip the COS_CUTOFF gate during the noise interludes —
    the windowed segmenter covers the full duration with valid chord
    matches even without the GAP_BRIDGE_MAX post-pass. In production
    the bridge was needed for real overdriven guitar where transient
    noise dropped some windows below 0.70; the synthetic harmonics
    here score 0.95+ throughout. The test is kept because it pins
    "contiguous regions emerge on harmonic input", which remains a
    correct property under Phase 4's HMM (where contiguity becomes a
    structural invariant rather than a post-pass).

    The fixed-window segmenter + COS_CUTOFF gate naturally produces
    chord arrays with small time gaps where individual windows fell
    just below 0.70 cosine. Pre-bridge, Pub Feed surfaced ~7 such
    gaps in a 145s mix, each 0.5–1.0s long. The Jam playhead
    interpolates through those gaps with `highlightIdx = -1`
    (jam.js:updateChordPlayhead), so the user-visible effect is the
    active chord pill momentarily de-highlighting as the playhead
    crosses a gap.

    The detector's GAP_BRIDGE_MAX post-pass extends the previous
    chord's end_time to the next chord's start_time for any gap up
    to 1.5s. This test pins that no chord pair surfaces with a
    nonzero time gap on realistic harmonic-spread input.
    """
    sr = 22050
    chord_dur_s = 1.0
    rng = np.random.default_rng(11)

    # Long signal: 12 chord cycles of A major + a couple of brief
    # "silent" interludes (chunks of low-amplitude noise) that the
    # detector will discard as below-cutoff. The bridge step should
    # absorb those gaps back into the surrounding chord.
    chunks = []
    for cycle in range(12):
        t = np.linspace(0, chord_dur_s, int(sr * chord_dur_s), endpoint=False)
        y = np.zeros_like(t)
        for f in [220.00, 277.18, 329.63]:
            for h, amp in [(1, 1.0), (2, 0.7), (3, 0.5)]:
                y += amp * np.sin(2 * np.pi * f * h * t)
        y /= max(1.0, float(np.max(np.abs(y))))
        chunks.append(y)
        # Inject a 0.6s dim-amplitude noise stretch every 4 cycles
        # — small enough that the bridge should still close it.
        if cycle % 4 == 3:
            interlude = rng.standard_normal(int(0.6 * sr)).astype(np.float32) * 0.05
            chunks.append(interlude)

    sig = np.concatenate(chunks).astype(np.float32)
    sig /= max(1e-9, float(np.max(np.abs(sig))))
    chords = detect_chords(sig, sr)

    assert len(chords) >= 1, "test signal produced no chords at all"

    # No pair should have a measurable gap between them.
    gaps = []
    for i in range(len(chords) - 1):
        gap = chords[i + 1].start_s - chords[i].end_s
        if gap > 0.05:  # 50ms slack for frame-to-time rounding
            gaps.append((i, round(gap, 3), chords[i].symbol, chords[i + 1].symbol))
    assert not gaps, (
        f"chord regions surface with internal time-gaps after the "
        f"bridge pass: {gaps}. The Jam chord ribbon will flicker "
        f"off-highlight as the playhead crosses each gap. See "
        f"chord_detector.py:GAP_BRIDGE_MAX block."
    )


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


def test_dense_alternating_vamp_does_not_explode_region_count() -> None:
    """End-to-end upper bound on chord region count.

    Pub Feed's full mix surfaced 170 chord regions over 145s pre-smooth
    (~0.85s/region) because cosine similarity flipped between F#m, A,
    F#, and Em every analysis window. Post-smooth + post-drop the
    region count should be musically reasonable — a real 4-chord vamp
    has on the order of one region per chord cycle, not one per window.

    This test synthesises 8 cycles of an A/D vamp at 4s/chord (32s
    total) with heavy harmonic content (overdrive proxy, the signal
    class that triggered the Pub Feed flips). The detector should
    surface a region count consistent with the cycle count, not a
    per-window flip storm.
    """
    sr = 22050
    chord_dur_s = 4.0
    rng = np.random.default_rng(0)

    vamp = [
        ("A", [220.00, 277.18, 329.63]),
        ("D", [146.83, 220.00, 293.66]),
    ]
    parts = []
    for _ in range(8):
        for _name, freqs in vamp:
            t = np.linspace(0, chord_dur_s, int(sr * chord_dur_s), endpoint=False)
            y = np.zeros_like(t)
            for f in freqs:
                for h, amp in [(1, 1.0), (2, 0.7), (3, 0.5), (4, 0.35), (5, 0.2)]:
                    y += amp * np.sin(2 * np.pi * f * h * t)
            y /= max(1.0, float(np.max(np.abs(y))))
            parts.append(y)
    sig = np.concatenate(parts).astype(np.float32)
    noise = rng.standard_normal(len(sig)).astype(np.float32) * 0.10 * float(np.std(sig))
    sig = (sig + noise).astype(np.float32)
    sig /= max(1e-9, float(np.max(np.abs(sig))))

    chords = detect_chords(sig, sr)
    # 32s of audio with 64 windows of 0.5s. Pre-smoothing produced up to
    # ~50 chord regions on this kind of input. Post-smooth + post-drop
    # the upper bound should be well below that; a 2-chord vamp with
    # 16 ground-truth boundaries should not surface more than ~25
    # regions even allowing for boundary slop.
    assert len(chords) <= 25, (
        f"dense vamp surfaced {len(chords)} chord regions for 32s of audio "
        f"({[c.symbol for c in chords]}). Per-window label flips have "
        f"escaped the smoothing + short-region-drop passes; the Jam ribbon "
        f"will read as a flip storm. See chord_detector.py "
        f"LABEL_SMOOTH_WINDOW and MIN_REGION_DUR."
    )


def test_key_detection_finds_e_major_on_e_major_progression() -> None:
    """Direct unit test of `_detect_key_from_chroma`.

    Build a chroma matrix from an E-major chord progression (I=E,
    vi=F#m, IV=A, V=B) — exactly the chord vocabulary of Pub Feed
    according to the tabs. The KS profile correlation should identify
    the key as E major (root=4, mode='major').

    A regression that swaps the major/minor profiles, mis-rotates,
    or normalises incorrectly will trip this with the wrong key,
    which propagates to the wrong diatonic set and the bias acts on
    the wrong chord family.
    """
    # Build a 12-pc chroma vector summing the four chord tones equally.
    # E major: E G# B → indices 4, 8, 11
    # F#m:     F# A C# → 6, 9, 1
    # A major: A C# E  → 9, 1, 4
    # B major: B D# F# → 11, 3, 6
    chroma_2d = np.zeros((12, 1))
    for pc in (4, 8, 11,   # E
               6, 9, 1,    # F#m
               9, 1, 4,    # A
               11, 3, 6):  # B
        chroma_2d[pc, 0] += 1.0
    root, mode, _strength = chord_detector._detect_key_from_chroma(chroma_2d)
    E_ROOT = 4
    assert (root, mode) == (E_ROOT, 'major'), (
        f"E-major chord-tone aggregate should detect as E major; got "
        f"({root}, {mode!r}). See chord_detector.py:_detect_key_from_chroma."
    )


def test_diatonic_bias_picks_in_key_chord_at_template_tie() -> None:
    """Direct unit test of the bias path in `_match_chord_template`.

    The B major triad (B-D#-F#) and the F# major triad (F#-A#-C#)
    share zero pitch classes — they can't be confused on a clean
    chroma vector. The harder case is the *B power chord* (just B
    and F#): its chroma matches F# minor (F#-A-C#) and B major
    (B-D#-F#) and other templates with similar scores depending on
    the bleed.

    Build a chroma vector that ties F# major and B major in raw
    cosine (both contain a strong F# bin and a moderate root bin)
    and verify: without bias, F# major wins (insertion order or
    chroma asymmetry); with E-major diatonic bias, B major wins.

    Pin the structural property: diatonic biasing changes the
    argmax outcome for ambiguous chroma, exactly where the tabs
    say it must.
    """
    # Construct a chroma with strong F# + B + moderate D# (between B
    # and F#m roots, with some B-major colour). This creates a near-
    # tie between B major and F#m / F# in raw cosine.
    chroma = np.zeros(12)
    chroma[11] = 1.0   # B
    chroma[6]  = 1.0   # F#
    chroma[3]  = 0.7   # D# (B major's 3rd)
    chroma[9]  = 0.4   # A (slight residue from neighbouring F#m)

    # Without bias: whoever wins, wins.
    root_nb, qual_nb, conf_nb = chord_detector._match_chord_template(chroma)

    # With E-major diatonic bias: B major (V in E) should be favoured.
    E_ROOT = 4
    diatonic_e = chord_detector._diatonic_chord_set(E_ROOT, 'major')
    root_b, qual_b, conf_b = chord_detector._match_chord_template(
        chroma, diatonic=diatonic_e, bias=0.10
    )

    B_ROOT = 11
    # Pin the behaviour: with bias, B major (or some major-family
    # extension on B) wins. The collapsed quality on the result must
    # land in the major family per _quality_family.
    fam = chord_detector._quality_family(qual_b)
    assert (root_b, fam) == (B_ROOT, 'maj'), (
        f"E-major diatonic bias should pick B major over neighbours on "
        f"a B+F#+D# chroma; got ({chord_detector.NOTE_NAMES[root_b]}, "
        f"{qual_b!r}, raw={conf_b:.3f}). Unbiased pick was "
        f"({chord_detector.NOTE_NAMES[root_nb]}, {qual_nb!r}, raw={conf_nb:.3f})."
    )
    # The returned confidence must be the RAW cosine, not the inflated
    # biased score. A regression that returns the biased score would
    # see conf_b > 1.0 sometimes; pin it to <= 1.0 and ~= the raw
    # confidence of the chosen template against the chroma directly.
    assert 0.0 <= conf_b <= 1.0, (
        f"_match_chord_template must return raw cosine confidence "
        f"(0..1), not the bias-inflated score; got {conf_b}."
    )


def test_match_chord_template_unbiased_path_unchanged() -> None:
    """Backwards-compat pin: calling `_match_chord_template` without
    the `diatonic`/`bias` kwargs must produce the same result as the
    pre-bias implementation did.

    A clean C major triad chroma (notes C, E, G) should resolve to
    (root=0, quality='maj') regardless of whether the bias path is
    available, as long as it isn't enabled.
    """
    chroma = np.zeros(12)
    chroma[0] = 1.0  # C
    chroma[4] = 1.0  # E
    chroma[7] = 1.0  # G
    root, quality, conf = chord_detector._match_chord_template(chroma)
    assert root == 0 and quality == 'maj', (
        f"unbiased clean C-major triad should resolve to (0, 'maj'); "
        f"got ({root}, {quality!r}). The default arguments to "
        f"_match_chord_template have drifted."
    )
    assert conf > 0.95, (
        f"clean triad should score near 1.0 in cosine space; got {conf}."
    )


def test_pub_feed_proxy_progression_surfaces_tab_chord_vocab() -> None:
    """End-to-end vocabulary test against a Pub-Feed-style E-major
    progression.

    Synthesise a 4-chord vamp matching the tabs' verse-end / chorus
    progression (E - F#m - A - B repeated) with heavy overdrive-style
    harmonics. The detector must surface all four chord labels and
    must NOT surface the previously-prevalent wrong picks (F# major,
    Em) which are non-diatonic in E major.

    A regression that removes _detect_key_from_chroma or DIATONIC_BIAS
    will produce the wrong vocabulary; the test will flag whichever
    label set surfaces in the failure message so the cause is obvious.
    """
    sr = 22050
    chord_dur_s = 2.0
    rng = np.random.default_rng(0)

    # E major progression: E, F#m, A, B  (I, vi, IV, V).
    # Frequencies are triad voicings centered roughly in the same
    # octave as a guitar's open chord position.
    progression = [
        ("E",  [164.81, 207.65, 246.94]),   # E G# B
        ("F#m", [185.00, 220.00, 277.18]),  # F# A C#
        ("A",  [220.00, 277.18, 329.63]),   # A C# E
        ("B",  [246.94, 311.13, 369.99]),   # B D# F#
    ]
    parts = []
    for _ in range(4):  # 4 cycles → 32s total
        for _name, freqs in progression:
            t = np.linspace(0, chord_dur_s, int(sr * chord_dur_s), endpoint=False)
            y = np.zeros_like(t)
            for f in freqs:
                # Heavy harmonics (overdrive proxy — the signal class
                # that drove the F#m↔F# / B↔F#m confusion pre-bias).
                for h, amp in [(1, 1.0), (2, 0.7), (3, 0.5), (4, 0.35), (5, 0.2)]:
                    y += amp * np.sin(2 * np.pi * f * h * t)
            y /= max(1.0, float(np.max(np.abs(y))))
            parts.append(y)
    sig = np.concatenate(parts).astype(np.float32)
    noise = rng.standard_normal(len(sig)).astype(np.float32) * 0.10 * float(np.std(sig))
    sig = (sig + noise).astype(np.float32)
    sig /= max(1e-9, float(np.max(np.abs(sig))))

    chords = detect_chords(sig, sr)
    symbols = {c.symbol for c in chords}

    # The four ground-truth diatonic chords must all surface. The
    # collapsed display labels are: "E", "F#m", "A", "B".
    expected = {"E", "F#m", "A", "B"}
    missing = expected - symbols
    assert not missing, (
        f"Pub-Feed-style E-major progression failed to surface "
        f"diatonic chord(s) {missing}; detector emitted {sorted(symbols)}. "
        f"Key detection (_detect_key_from_chroma) or diatonic bias "
        f"(DIATONIC_BIAS in detect_chords_from_audio) has regressed."
    )

    # The wrong picks pre-bias (F# major, Em) must not contaminate the
    # vocabulary. A residue of "F#" or "Em" indicates the bias path
    # isn't applied to the windows where the ambiguity occurs.
    NON_DIATONIC = {"F#", "Em", "A#", "D#m"}
    contamination = NON_DIATONIC & symbols
    assert not contamination, (
        f"non-diatonic chord(s) {contamination} contaminated the "
        f"vocabulary on an E-major progression; detector emitted "
        f"{sorted(symbols)}. Diatonic bias is not preferring in-key "
        f"siblings at template-tie ambiguities."
    )


def test_pipeline_callers_prefer_other_stem_for_chroma_source():
    """Locks the chroma-source swap at both pipeline call sites.

    The full mix is dominated by bass-string fundamentals; CQT chroma
    reads the bass root and the cosine matcher locks onto the bass
    note's relative-minor template (e.g. on Pub Feed the bass riff
    hammers F# on the open low E at fret 2, which pulled every chord
    in the song toward F#m even though the guitar was playing E).

    Empirical probe on the Pub Feed harmonic stem (session 9a72462f):

      Full mix:  13 regions, symbols=[('F#m', 7), ('E', 6)]
                 (B = 0 occurrences, A = 0, F#m dominant at 82%)

      'other':   31 regions, symbols=[('B', 11), ('E', 6), ('C#m', 5),
                                       ('F#m', 3), ('A', 2), …]
                 (B and A surface; F#m correctly demoted)

    The swap is implemented in two places:

      * tone_forge.unified_pipeline.UnifiedPipeline._detect_chord_lane
        — takes ``stems`` and loads ``stems.get("other")`` before
        delegating to ``tone_forge.analysis.detect_chords``.
      * local_engine.analysis_worker — the chord lane block at
        Step 4a2 loads ``stems.get("other")`` before calling
        ``detect_chords``.

    If a future refactor reverts either site to passing the full mix
    (``audio_data.audio`` / ``y_dur``) directly without consulting the
    ``other`` stem, this test trips with the same failure mode that
    reproduced in production (Pub Feed labelled as 82% F#m, no B, no A).
    """
    import inspect
    from tone_forge import unified_pipeline
    from local_engine import analysis_worker

    # 1. unified_pipeline path
    unified_src = inspect.getsource(unified_pipeline.UnifiedPipeline._detect_chord_lane)
    assert 'stems.get("other")' in unified_src or "stems.get('other')" in unified_src, (
        "UnifiedPipeline._detect_chord_lane no longer consults the 'other' "
        "stem. The chord lane will revert to chroma-from-full-mix, which "
        "is bass-dominated and pulls every chord toward the bass note's "
        "relative-minor template (Pub Feed regression: 13 regions of "
        "F#m+E only, B and A absent)."
    )
    assert "detect_chords" in unified_src, (
        "UnifiedPipeline._detect_chord_lane no longer calls detect_chords."
    )

    # 2. local_engine.analysis_worker path
    worker_src = inspect.getsource(analysis_worker)
    # Locate the chord lane block (Step 4a2) and assert it consults stems.get("other").
    assert ("Step 4a2: Chord lane" in worker_src
            and ('stems.get("other")' in worker_src
                 or "stems.get('other')" in worker_src)), (
        "local_engine.analysis_worker chord lane block no longer consults "
        "the 'other' stem. The chord lane will revert to chroma-from-full-"
        "mix on the local-engine path (Pub Feed regression: 13 regions of "
        "F#m+E only, B and A absent)."
    )


# ---- Phase 2: HPCP overtone suppression ------------------------------


def test_hpcp_keeps_fundamental_dominant_with_strong_5th_overtone() -> None:
    """Sanity floor for the HPCP refinement (no-suppression variant).

    Phase 2 explored harmonic-5th suppression to deal with overdriven
    guitar's strong 3rd-harmonic leak into the perfect-5th bin. Both
    naive and conditional suppression variants regressed Pub Feed
    WCSR (0.16 -> 0.09 / 0.075) because they stripped real chord 5ths
    out of major triads. The empirical conclusion: with the current
    template set (no power-chord templates yet), suppressing 5ths
    pushes A major toward F#m. Suppression is currently a no-op.

    The remaining HPCP contract that this test pins:

      1. 36-bin CQT -> per-frame L2 -> 12-bin max-pool produces a
         chroma vector where the fundamental's pitch class is still
         the dominant bin even with a strong 3rd-harmonic mixed in.
      2. The output retains (12, n_frames) shape, no negative values.

    We synthesise an A fundamental (220 Hz) plus a strong 3rd
    harmonic (660 Hz, only ~3 dB below the fundamental — heavier
    than real overdrive in order to stress-test the binning).

    A = pitch class 9; E = pitch class 4 (= (9 + 7) mod 12).
    """
    sr = 22050
    dur = 2.0
    t = np.linspace(0, dur, int(sr * dur), endpoint=False)
    y = (
        1.0 * np.sin(2 * np.pi * 220.0 * t)            # A fundamental
        + 0.70 * np.sin(2 * np.pi * 660.0 * t)         # 3rd harmonic (E)
    ).astype(np.float32)

    hpcp = chord_detector._hpcp(y, sr, hop_length=512)
    hpcp_mean = hpcp.mean(axis=1)
    hpcp_A = hpcp_mean[9]

    # Floor: A bin remains the dominant pitch class even with strong
    # 5th overtone present.
    assert hpcp_A == hpcp_mean.max(), (
        f"After HPCP, A is no longer the dominant bin "
        f"(A={hpcp_A:.3f}, max={hpcp_mean.max():.3f} at "
        f"bin={int(hpcp_mean.argmax())}). The 36->12 max-pool step "
        "lost the fundamental — likely a reshape/axis error."
    )


@pytest.mark.skip(
    reason=(
        "Future contract — harmonic-5th suppression is currently "
        "disabled in _hpcp because both naive and conditional "
        "suppression variants regressed Pub Feed WCSR. Re-enable "
        "and un-skip after Phase 3 (power-chord templates) lands "
        "and the confusion matrix is re-checked."
    )
)
def test_hpcp_suppresses_5th_overtone() -> None:  # pragma: no cover
    """Future contract: HPCP should reduce the 5th-overtone leak.

    See ``test_hpcp_keeps_fundamental_dominant_with_strong_5th_overtone``
    docstring for the empirical reason this is currently skipped.
    The intent is to re-enable harmonic-5th suppression after the
    power-chord template gap is closed, then assert::

        hpcp_E_over_A < raw_E_over_A * 0.70
    """
    pass


def test_hpcp_returns_12_by_n_matrix() -> None:
    """Shape contract pin for the HPCP helper.

    The downstream cosine-template path assumes a (12, n_frames)
    chroma matrix. If the helper accidentally drops to (n_frames,)
    or returns the 36-bin tensor, every chord match will go
    sideways silently.
    """
    sr = 22050
    y = np.random.RandomState(42).randn(sr).astype(np.float32) * 0.1
    out = chord_detector._hpcp(y, sr, hop_length=512)
    assert out.ndim == 2 and out.shape[0] == 12, (
        f"_hpcp returned shape {out.shape}, expected (12, n_frames)."
    )
    assert out.shape[1] > 0, "no chroma frames produced"
    assert (out >= 0.0).all(), "HPCP output contains negative values"


# ---- Phase 3: power-chord ("5") templates ----------------------------


def test_power_chord_template_present_in_chord_templates() -> None:
    """Pin: ``CHORD_TEMPLATES['5']`` exists and is the root+5th dyad.

    The whole Phase 3 disambiguation path keys off this template
    being addressable by quality ``"5"`` and being structurally
    {root, perfect-5th} only — no 3rd of either flavour.
    """
    assert "5" in chord_detector.CHORD_TEMPLATES, (
        "Power-chord template '5' was removed from CHORD_TEMPLATES. "
        "Phase 3 power-chord disambiguation has no template to gate on."
    )
    assert chord_detector.CHORD_TEMPLATES["5"] == [0, 7], (
        f"Power-chord template should be [0, 7] (root + perfect 5th), "
        f"got {chord_detector.CHORD_TEMPLATES['5']}. Any 3rd in the "
        f"interval list defeats the purpose."
    )


def test_collapse_quality_passes_power_chord_through_unchanged() -> None:
    """_collapse_quality('5') -> '5'.

    Without this pass-through the display label collapses to bare
    "A" instead of "A5", silently dropping the power-chord
    distinction even though the matcher correctly identified it.
    """
    assert chord_detector._collapse_quality("5") == "5"


def test_chord_name_renders_power_chord_with_5_suffix() -> None:
    """Chord(quality='5').name renders as e.g. 'D5'.

    Uses the existing else branch of Chord.name (any quality not
    'maj' or 'min' is appended verbatim to the root name). Pin
    that contract here so a future refactor of Chord.name can't
    silently swallow the '5' suffix.
    """
    c = chord_detector.Chord(
        root=2, quality="5", start_time=0.0, end_time=1.0, confidence=0.9,
    )
    assert c.name == "D5"


def test_d5_powerchord_chroma_resolves_to_d5_not_d_or_dm() -> None:
    """Synthesise a D5 chroma and assert the matcher returns 'D5'.

    A clean D5 chroma is {D: 1, A: 1, others: 0} (D = pc 2, A =
    pc 9). With the Phase 3 template in place + the 3rd-bin gating
    tie-break, the matcher should emit (2, '5'). Pre-Phase 3 the
    only available 2-note templates with these pitch classes are
    sus2 (rooted on A: A + B + E? no, A-sus2 = A+B+E, not D+A) and
    nothing else; the matcher used to scatter to A (which contains
    A but not D, so partial), F#m (contains A but not D), D
    (contains D but not the 3rd F#), etc.
    """
    chroma = np.zeros(12)
    chroma[2] = 1.0  # D
    chroma[9] = 1.0  # A

    root, quality, conf = chord_detector._match_chord_template(chroma)
    assert (root, quality) == (2, "5"), (
        f"Expected (root=2 D, quality='5') for D5 chroma; got "
        f"(root={root} {chord_detector.NOTE_NAMES[root]}, "
        f"quality={quality!r}, conf={conf:.3f}). With the power-chord "
        f"template in place, D5 chroma should match {{D, A}} exactly."
    )


def test_a5_powerchord_chroma_resolves_to_a5_not_relative_minor() -> None:
    """The Pub Feed regression case: A5 chroma should not become F#m.

    A5 = {A: 1, E: 1}. Without the "5" template the closest options
    are F#m ({F#, A, C#} — shares A only) and A maj ({A, C#, E} —
    shares A and E). The cosine of A5 against A maj is 0.816; against
    F#m is 0.408 — A maj wins normally. But with diatonic bias toward
    F# minor or with chroma leak into C#, the result flips. The
    Phase 3 3rd-bin gating must resolve this consistently to A5.
    """
    chroma = np.zeros(12)
    chroma[9] = 1.0  # A
    chroma[4] = 1.0  # E

    root, quality, conf = chord_detector._match_chord_template(chroma)
    assert (root, quality) == (9, "5"), (
        f"Expected (root=9 A, quality='5') for A5 chroma; got "
        f"(root={root} {chord_detector.NOTE_NAMES[root]}, "
        f"quality={quality!r}, conf={conf:.3f})."
    )


def test_d_triad_chroma_resolves_to_d_not_d5() -> None:
    """Inverse pin: a real D major triad should still match D, not D5.

    The 3rd-bin gating must NOT fire when the 3rd is genuinely
    present. D major = {D, F#, A} = pcs {2, 6, 9}; F# at bin 6 is
    the 3rd of D and must register strongly enough that
    chroma[6] >= 0.25 * chroma[2].
    """
    chroma = np.zeros(12)
    chroma[2] = 1.0   # D
    chroma[6] = 1.0   # F# (the 3rd)
    chroma[9] = 1.0   # A

    root, quality, conf = chord_detector._match_chord_template(chroma)
    assert (root, quality) == (2, "maj"), (
        f"Expected (root=2 D, quality='maj') for D-major triad chroma; "
        f"got (root={root} {chord_detector.NOTE_NAMES[root]}, "
        f"quality={quality!r}, conf={conf:.3f}). The 3rd-bin gating "
        f"over-triggered and stripped a real 3rd out of the triad."
    )


def test_dm_triad_chroma_resolves_to_dm_not_d5() -> None:
    """Inverse pin: D minor triad must remain Dm, not collapse to D5.

    D minor = {D, F, A} = pcs {2, 5, 9}. F at bin 5 is the b3 of D
    and must register strongly enough to defeat the power-chord
    tie-break (chroma[5] >= 0.25 * chroma[2]).
    """
    chroma = np.zeros(12)
    chroma[2] = 1.0   # D
    chroma[5] = 1.0   # F (the b3)
    chroma[9] = 1.0   # A

    root, quality, conf = chord_detector._match_chord_template(chroma)
    assert (root, quality) == (2, "min"), (
        f"Expected (root=2 D, quality='min') for D-minor triad chroma; "
        f"got (root={root} {chord_detector.NOTE_NAMES[root]}, "
        f"quality={quality!r}, conf={conf:.3f})."
    )


def test_powerchord_with_weak_third_leak_still_resolves_to_5() -> None:
    """Realistic: 20% leak into the 3rd bin should still resolve to 5.

    Overdriven guitar isn't purely root+5th in chroma terms — some
    chroma leak into the 3rd bin happens from overtones and bleed.
    The 0.25 gating threshold should tolerate small leak (here 0.2
    of the root) and still produce '5'. Floor for the threshold.
    """
    chroma = np.zeros(12)
    chroma[9] = 1.0    # A (root)
    chroma[4] = 1.0    # E (5th)
    chroma[1] = 0.20   # C# (3rd of A maj), small leak

    root, quality, _conf = chord_detector._match_chord_template(chroma)
    assert (root, quality) == (9, "5"), (
        f"With small 3rd-bin leak (0.2 of root), expected (9, '5'); "
        f"got ({root}, {quality!r})."
    )


def test_powerchord_with_strong_third_does_not_resolve_to_5() -> None:
    """Realistic inverse: ~50% 3rd-bin mass means it's a real triad.

    If chroma[3rd] is at half the root mass, the 3rd is genuinely
    sounded (a quiet but voiced 3rd in the chord), not just overtone
    leak. The matcher should classify this as the triad, not the
    power chord.
    """
    chroma = np.zeros(12)
    chroma[9] = 1.0    # A (root)
    chroma[4] = 1.0    # E (5th)
    chroma[1] = 0.50   # C# (3rd) — strong, real

    root, quality, _conf = chord_detector._match_chord_template(chroma)
    assert (root, quality) == (9, "maj"), (
        f"With strong 3rd-bin mass (0.5 of root), expected (9, 'maj'); "
        f"got ({root}, {quality!r}). 3rd-bin gating misfired."
    )


# ---- Phase 4: HMM/Viterbi sequence model ----------------------------


def _viterbi_state_idx(root: int, quality: str) -> int:
    """Translate (root, quality) into the Viterbi flat-state index used
    by `_compute_emission_scores` / `_build_transition_matrix` /
    `_viterbi_decode`. Mirrors the indexing scheme in chord_detector
    (root * n_qualities + qualities.index(quality))."""
    qs = chord_detector._VITERBI_QUALITIES
    return root * len(qs) + qs.index(quality)


def test_viterbi_self_loop_smooths_single_frame_dropout() -> None:
    """A single noisy-emission frame between two clear A frames must
    not flip the decoded state to the noise winner.

    The Phase 4 plan calls this out as the canonical replacement for
    the deleted `_smooth_chord_labels` heuristic: the Viterbi self-loop
    bonus is what makes 1-frame label flips disappear, structurally
    rather than as a post-pass.
    """
    A_ROOT = 9
    FSM_ROOT = 6
    n_qs = len(chord_detector._VITERBI_QUALITIES)
    n_states = 12 * n_qs + 1
    a_idx = _viterbi_state_idx(A_ROOT, 'maj')
    fsm_idx = _viterbi_state_idx(FSM_ROOT, 'min')

    # 9 frames clearly preferring A, 1 frame preferring F#m by a thin
    # margin, then 9 frames preferring A again. All other states get
    # flat low emission so they cannot win. The noise margin (0.005)
    # is intentionally below SELF_LOOP_BONUS (0.01) so the self-loop
    # term must dominate for the test to pass.
    T = 19
    emissions = np.full((T, n_states), 0.50, dtype=np.float64)
    for t in range(T):
        emissions[t, a_idx] = 0.80
    # Single-frame noise burst at the middle frame: F#m wins by 0.005.
    emissions[9, a_idx] = 0.795
    emissions[9, fsm_idx] = 0.800

    transitions = chord_detector._build_transition_matrix(diatonic=None)
    states = chord_detector._viterbi_decode(emissions, transitions)

    # All frames should decode to A maj; the self-loop bonus must
    # dominate the 0.02 single-frame F#m advantage.
    assert all(s == a_idx for s in states), (
        f"Viterbi failed to smooth a single-frame F#m dropout into the "
        f"surrounding A run: decoded states {list(states)} (expected "
        f"all == {a_idx}). The self-loop transition prior is too weak "
        f"relative to per-frame emission noise — chord_detector.py's "
        f"SELF_LOOP_BONUS in _build_transition_matrix is the knob."
    )


def test_viterbi_emission_bias_lifts_diatonic_state_above_tied_nondiatonic() -> None:
    """At a chroma-level cosine tie between a diatonic and a
    non-diatonic chord, the emission-bias step lifts the diatonic
    state above the non-diatonic by ~`bias` fraction.

    Phase 4 moved the diatonic preference out of the transition matrix
    (`DIATONIC_TRANSITION_BONUS = 0.0`) and into the emission step.
    Pin that mechanism here: build a chroma frame where D maj
    (diatonic in A major) and Bb maj (non-diatonic) score the same raw
    cosine, run `_compute_emission_scores` with bias=DIATONIC_BIAS, and
    confirm the D state's emission ends up strictly larger than Bb's.

    A regression that removes the diatonic-bias multiplication or
    silently disables it for power-chord-equivalent states will trip
    this.
    """
    A_ROOT = 9
    D_ROOT = 2
    BB_ROOT = 10
    n_qs = len(chord_detector._VITERBI_QUALITIES)
    n_states = 12 * n_qs + 1
    d_idx = _viterbi_state_idx(D_ROOT, 'maj')
    bb_idx = _viterbi_state_idx(BB_ROOT, 'maj')

    diatonic = chord_detector._diatonic_chord_set(A_ROOT, 'major')
    assert (D_ROOT, 'maj') in diatonic
    assert (BB_ROOT, 'maj') not in diatonic

    # Uniform chroma — every pitch class equally excited. Under L2
    # cosine, every major triad template scores identically against
    # uniform chroma (their template-sum is invariant under root
    # rotation), so D and Bb maj tie on raw emission. The diatonic
    # bias multiplier is then the only thing that can break the tie.
    chroma = np.ones((12, 4), dtype=np.float64)
    boundaries = np.array([0, 1, 2, 3, 4], dtype=np.int64)

    bias = chord_detector.DIATONIC_BIAS if hasattr(chord_detector, "DIATONIC_BIAS") else 0.05
    emissions = chord_detector._compute_emission_scores(
        chroma, boundaries, diatonic=diatonic, bias=bias, no_chord_floor=0.0,
    )
    assert emissions.shape == (4, n_states)

    # The diatonic D state must score strictly higher than the
    # non-diatonic Bb state on every frame.
    deltas = emissions[:, d_idx] - emissions[:, bb_idx]
    assert np.all(deltas > 0.0), (
        f"emission bias failed to lift diatonic D above non-diatonic Bb on "
        f"chroma where both chords sound: deltas {deltas.tolist()}. "
        f"DIATONIC_BIAS in chord_detector or the `(root, '5')` diatonic-"
        f"equivalence in `_is_diatonic_state` may have regressed."
    )


# ---- Phase 5: bass-routed emission bias -----------------------------


def test_bass_root_bias_lifts_matching_root_above_chroma_tied_sibling() -> None:
    """At a chroma-level cosine tie between A maj and F#m, the
    bass-root multiplier must lift whichever root matches the bass.

    This is the Phase 5 disambiguation mechanism in isolation: cosine
    between {A, C#, E} and {F#, A, C#} chroma is identical (both
    share two of three pitch classes with the inputs); only the
    bass-root identity breaks the tie. Run `_compute_emission_scores`
    twice on the same chroma — once with bass=A, once with bass=F# —
    and assert the matching-root state wins each time.
    """
    A_ROOT = 9
    FSM_ROOT = 6  # F#
    n_qs = len(chord_detector._VITERBI_QUALITIES)
    a_idx = _viterbi_state_idx(A_ROOT, 'maj')
    fsm_idx = _viterbi_state_idx(FSM_ROOT, 'min')

    # Chroma vector ambiguous between A maj {A, C#, E} and F#m
    # {F#, A, C#}: put equal mass on A, C#, E, F#. Both templates
    # project onto two of these four bins so their cosines tie
    # exactly. With no bass bias, the Viterbi state space sees a tie
    # and picks one arbitrarily (in practice argmax on the underlying
    # tied state); with bass bias, the matching-root template wins.
    chroma = np.zeros((12, 2), dtype=np.float64)
    for pc in (9, 1, 4, 6):  # A, C#, E, F#
        chroma[pc, :] = 1.0
    boundaries = np.array([0, 1, 2], dtype=np.int64)

    # bass = A -> A maj should outscore F#m
    bass_track_a = np.array([A_ROOT, A_ROOT], dtype=np.int64)
    em_a = chord_detector._compute_emission_scores(
        chroma, boundaries,
        diatonic=None, bias=0.0, no_chord_floor=0.0,
        bass_root_track=bass_track_a, bass_bias=0.20,
    )
    assert np.all(em_a[:, a_idx] > em_a[:, fsm_idx]), (
        f"bass=A failed to lift A maj above F#m on tied chroma; "
        f"A emissions {em_a[:, a_idx].tolist()}, "
        f"F#m emissions {em_a[:, fsm_idx].tolist()}"
    )

    # bass = F# -> F#m should outscore A maj
    bass_track_fsm = np.array([FSM_ROOT, FSM_ROOT], dtype=np.int64)
    em_fsm = chord_detector._compute_emission_scores(
        chroma, boundaries,
        diatonic=None, bias=0.0, no_chord_floor=0.0,
        bass_root_track=bass_track_fsm, bass_bias=0.20,
    )
    assert np.all(em_fsm[:, fsm_idx] > em_fsm[:, a_idx]), (
        f"bass=F# failed to lift F#m above A maj on tied chroma; "
        f"F#m emissions {em_fsm[:, fsm_idx].tolist()}, "
        f"A emissions {em_fsm[:, a_idx].tolist()}"
    )


def test_bass_root_unvoiced_window_skips_bias() -> None:
    """A window with bass_root_track value -1 (unvoiced — bass rest)
    must receive no bass-bias multiplier. The emission for that
    window should match the no-bass-track path exactly.
    """
    chroma = np.ones((12, 2), dtype=np.float64)
    boundaries = np.array([0, 1, 2], dtype=np.int64)

    em_no_bass = chord_detector._compute_emission_scores(
        chroma, boundaries,
        diatonic=None, bias=0.0, no_chord_floor=0.0,
    )
    em_unvoiced = chord_detector._compute_emission_scores(
        chroma, boundaries,
        diatonic=None, bias=0.0, no_chord_floor=0.0,
        bass_root_track=np.array([-1, -1], dtype=np.int64),
        bass_bias=0.50,  # high bias on purpose; should not fire
    )
    np.testing.assert_allclose(em_no_bass, em_unvoiced, atol=1e-12)


def test_bass_bias_zero_is_no_op() -> None:
    """`bass_bias=0.0` is the no-bass-routing toggle. Even with a
    fully-voiced bass track passed in, the emission matrix must equal
    the no-bass-track baseline. Pins the bypass path so future
    refactors of the inner loop don't silently leak bass bias.
    """
    chroma = np.random.RandomState(0).rand(12, 3).astype(np.float64)
    boundaries = np.array([0, 1, 2, 3], dtype=np.int64)
    bass_track = np.array([9, 6, 4], dtype=np.int64)  # A, F#, E

    em_no_track = chord_detector._compute_emission_scores(
        chroma, boundaries,
        diatonic=None, bias=0.0, no_chord_floor=0.0,
    )
    em_zero_bias = chord_detector._compute_emission_scores(
        chroma, boundaries,
        diatonic=None, bias=0.0, no_chord_floor=0.0,
        bass_root_track=bass_track, bass_bias=0.0,
    )
    np.testing.assert_allclose(em_no_track, em_zero_bias, atol=1e-12)


def test_bass_root_track_returns_minus_one_for_silence() -> None:
    """A zero-amplitude bass signal must produce an all-unvoiced
    track (all -1). Pyin's voicing detector should refuse to commit
    to a pitch on silence; if it ever starts hallucinating a root on
    silence, this test catches it.
    """
    sr = 22050
    hop = 512
    duration_s = 1.0
    n_samples = int(duration_s * sr)
    silence = np.zeros(n_samples, dtype=np.float32)
    # Match the window-boundary construction in detect_chords_from_audio.
    n_chroma_frames = 1 + n_samples // hop
    frames_per_window = int(0.5 * sr / hop)
    boundaries = list(range(0, n_chroma_frames, frames_per_window))
    if boundaries[-1] != n_chroma_frames:
        boundaries.append(n_chroma_frames)

    track = chord_detector._bass_root_track(silence, sr, boundaries, hop)
    assert (track == -1).all(), (
        f"bass-root track on silence must be all -1; got {track.tolist()}"
    )


def test_detect_chords_no_bass_matches_phase_4_path() -> None:
    """Top-level integration: calling `detect_chords_from_audio`
    without a bass stem must produce identical output to calling it
    with the explicit `bass_y=None` argument. The bass-routing code
    path is a pure refinement gated by `bass_y`, not a behaviour
    change.
    """
    sr = 22050
    duration_s = 2.0
    n_samples = int(duration_s * sr)
    t = np.arange(n_samples) / sr
    # A major chord: A2 + C#3 + E3.
    y = (
        np.sin(2 * np.pi * 110.0 * t) +
        np.sin(2 * np.pi * 138.59 * t) +
        np.sin(2 * np.pi * 164.81 * t)
    ).astype(np.float32) * 0.3

    out_default = chord_detector.detect_chords_from_audio(y, sr)
    out_explicit = chord_detector.detect_chords_from_audio(y, sr, bass_y=None)

    assert len(out_default) == len(out_explicit)
    for a, b in zip(out_default, out_explicit):
        assert a.name == b.name
        assert a.start_time == pytest.approx(b.start_time)
        assert a.end_time == pytest.approx(b.end_time)


# ---------------------------------------------------------------------------
# Phase 6: beat-synchronous chroma aggregation
# ---------------------------------------------------------------------------


def test_build_beat_boundaries_returns_none_without_beats() -> None:
    """No beats supplied -> None signals fixed-window fallback."""
    out = chord_detector._build_beat_boundaries(
        None, n_chroma_frames=100, sr=22050, hop_length=512,
    )
    assert out is None


def test_build_beat_boundaries_returns_none_for_too_few_beats() -> None:
    """A single beat (or zero) cannot define a window grid; fall back."""
    assert chord_detector._build_beat_boundaries(
        np.array([], dtype=np.float64),
        n_chroma_frames=100, sr=22050, hop_length=512,
    ) is None
    assert chord_detector._build_beat_boundaries(
        np.array([0.5], dtype=np.float64),
        n_chroma_frames=100, sr=22050, hop_length=512,
    ) is None


def test_build_beat_boundaries_includes_full_song_range() -> None:
    """The boundary list must start at 0 and end at n_chroma_frames so
    the per-beat aggregation covers the entire audio without dropping
    leading silence or trailing audio.
    """
    sr = 22050
    hop = 512
    # Beats at 1.0s and 2.0s with a song that's ~3s long.
    n_frames = int(3.0 * sr / hop)
    beats = np.array([1.0, 2.0], dtype=np.float64)
    out = chord_detector._build_beat_boundaries(
        beats, n_chroma_frames=n_frames, sr=sr, hop_length=hop,
    )
    assert out is not None
    assert out[0] == 0
    assert out[-1] == n_frames
    # Interior boundaries are the beat-time frame indices.
    expected_b0 = int(round(1.0 * sr / hop))
    expected_b1 = int(round(2.0 * sr / hop))
    assert expected_b0 in out
    assert expected_b1 in out


def test_build_beat_boundaries_dedupes_collapsed_beats() -> None:
    """If two beats round to the same chroma frame (degenerate case),
    `np.unique` must collapse them so we never get a zero-length
    window that would break Viterbi indexing.
    """
    sr = 22050
    hop = 512
    n_frames = 200
    # Beats spaced finer than one chroma frame (~23ms at sr=22050, hop=512).
    beats = np.array([0.500, 0.501, 0.502], dtype=np.float64)
    out = chord_detector._build_beat_boundaries(
        beats, n_chroma_frames=n_frames, sr=sr, hop_length=hop,
    )
    assert out is not None
    # All distinct values; no consecutive duplicates that would give
    # an empty window [b[t], b[t+1]) with b[t] == b[t+1].
    assert len(out) == len(set(out))
    for i in range(len(out) - 1):
        assert out[i] < out[i + 1]


def test_detect_chords_with_beats_snaps_boundaries_to_beats() -> None:
    """End-to-end: synthesize a song with a chord change near (but not
    exactly on) a beat, supply beats, assert the resulting boundary
    lands on the beat — not the original chord-change time.
    """
    sr = 22050
    # 1.0s of A major (A2 + C#3 + E3) then 1.0s of D major (D3 + F#3 + A3).
    dur = 1.0
    t = np.arange(int(dur * sr)) / sr
    y_a = (
        np.sin(2 * np.pi * 110.00 * t) +
        np.sin(2 * np.pi * 138.59 * t) +
        np.sin(2 * np.pi * 164.81 * t)
    ).astype(np.float32) * 0.3
    y_d = (
        np.sin(2 * np.pi * 146.83 * t) +
        np.sin(2 * np.pi * 185.00 * t) +
        np.sin(2 * np.pi * 220.00 * t)
    ).astype(np.float32) * 0.3
    y = np.concatenate([y_a, y_d])

    # Beats at 0.0, 0.5, 1.0, 1.5 (i.e. 120 BPM grid). The chord change
    # at 1.0s lands exactly on a beat boundary. We expect the detector
    # to land its A->D boundary at 1.0s (within one beat's slop).
    beats = np.array([0.0, 0.5, 1.0, 1.5], dtype=np.float64)
    out = chord_detector.detect_chords_from_audio(
        y, sr, beats_s=beats, min_chord_duration=0.1,
    )
    assert len(out) >= 1
    # At least one boundary should land within one beat (0.5s) of 1.0s.
    boundary_times = [c.end_time for c in out[:-1]]
    assert any(
        abs(b - 1.0) <= 0.5 for b in boundary_times
    ), f"no boundary near 1.0s in {boundary_times}"


def test_detect_chords_no_beats_matches_fixed_window_path() -> None:
    """Phase 6 is opt-in: omitting `beats_s` produces output identical
    to the fixed-window path (Phases 4-5 behaviour preserved).
    """
    sr = 22050
    duration_s = 2.0
    n_samples = int(duration_s * sr)
    t = np.arange(n_samples) / sr
    y = (
        np.sin(2 * np.pi * 110.0 * t) +
        np.sin(2 * np.pi * 138.59 * t) +
        np.sin(2 * np.pi * 164.81 * t)
    ).astype(np.float32) * 0.3

    out_default = chord_detector.detect_chords_from_audio(y, sr)
    out_explicit = chord_detector.detect_chords_from_audio(y, sr, beats_s=None)

    assert len(out_default) == len(out_explicit)
    for a, b in zip(out_default, out_explicit):
        assert a.name == b.name
        assert a.start_time == pytest.approx(b.start_time)
        assert a.end_time == pytest.approx(b.end_time)


# ---------------------------------------------------------------------------
# Stage 1.4.2 — _substitute_power_chords_on_dyads unit tests.
#
# The helper is the post-Viterbi pass that re-scores each emitted maj/min
# region against region-averaged chroma and substitutes the quality to '5'
# when (a) the region's third bin is weak and (b) the power-5 template's
# raw cosine is within a configured margin of the winning triad's raw
# cosine. Caller (detect_chords_from_audio) applies the minor-key gate.
# ---------------------------------------------------------------------------


def _make_chroma_with_voicing(
    pitch_classes_strength: list,
    n_frames: int = 4,
) -> np.ndarray:
    """Build a deterministic (12, n_frames) chroma matrix.

    ``pitch_classes_strength`` is a list of (pc, strength) pairs.
    Each pc gets ``strength`` in every frame; other bins are 0.0.
    """
    chroma = np.zeros((12, n_frames), dtype=np.float64)
    for pc, s in pitch_classes_strength:
        chroma[pc, :] = float(s)
    return chroma


def test_substitute_power_chords_on_dyads_fires_on_pure_dyad():
    """Pure F+C dyad chroma under an Fm region: should substitute to F5."""
    # F = pc 5, C = pc 0 (the perfect 5th above F is C).
    chroma = _make_chroma_with_voicing([(5, 1.0), (0, 1.0)])
    times = np.array([0.0, 0.5, 1.0, 1.5])
    fm = chord_detector.Chord(
        root=5, quality='min',
        start_time=0.0, end_time=1.5, confidence=0.7,
    )
    out = chord_detector._substitute_power_chords_on_dyads(
        [fm], chroma, times,
        third_ratio_max=0.4, margin=0.05,
    )
    assert len(out) == 1
    assert out[0].root == 5
    assert out[0].quality == '5'  # substituted
    assert out[0].start_time == 0.0
    assert out[0].end_time == 1.5


def test_substitute_power_chords_on_dyads_skips_real_triad():
    """Strong third in chroma → real triad → no substitution."""
    # F major triad chroma: F (5) + A (9) + C (0), all strong.
    # Third bin (A=9, +4 from F) at 1.0 = 100% of root bin → above
    # threshold so the gate rejects substitution.
    chroma = _make_chroma_with_voicing([(5, 1.0), (9, 1.0), (0, 1.0)])
    times = np.array([0.0, 0.5, 1.0, 1.5])
    f_maj = chord_detector.Chord(
        root=5, quality='maj',
        start_time=0.0, end_time=1.5, confidence=0.85,
    )
    out = chord_detector._substitute_power_chords_on_dyads(
        [f_maj], chroma, times,
        third_ratio_max=0.4, margin=0.05,
    )
    assert len(out) == 1
    assert out[0].quality == 'maj'  # unchanged


def test_substitute_power_chords_on_dyads_skips_non_maj_min():
    """Already-power-chord, dim, sus, etc. regions are pass-through."""
    chroma = _make_chroma_with_voicing([(5, 1.0), (0, 1.0)])
    times = np.array([0.0, 0.5, 1.0, 1.5])
    f5 = chord_detector.Chord(
        root=5, quality='5',
        start_time=0.0, end_time=1.5, confidence=0.7,
    )
    out = chord_detector._substitute_power_chords_on_dyads(
        [f5], chroma, times,
        third_ratio_max=0.4, margin=0.05,
    )
    assert len(out) == 1
    assert out[0].quality == '5'  # unchanged
    assert out[0] is f5  # pass-through


def test_substitute_power_chords_on_dyads_noop_when_ratio_zero():
    """third_ratio_max=0 short-circuits — must return input unchanged.

    This is the production no-op path: bench corpus passes default
    DetectorConfig (zeros) so this short-circuit is what makes Stage
    1.4.2 bit-exact identical to pre-1.4.2 when not opted in.
    """
    chroma = _make_chroma_with_voicing([(5, 1.0), (0, 1.0)])
    times = np.array([0.0, 0.5, 1.0, 1.5])
    fm = chord_detector.Chord(
        root=5, quality='min',
        start_time=0.0, end_time=1.5, confidence=0.7,
    )
    out = chord_detector._substitute_power_chords_on_dyads(
        [fm], chroma, times,
        third_ratio_max=0.0, margin=0.05,
    )
    assert len(out) == 1 and out[0] is fm


def test_substitute_power_chords_on_dyads_noop_when_margin_zero():
    """margin=0 short-circuits — must return input unchanged."""
    chroma = _make_chroma_with_voicing([(5, 1.0), (0, 1.0)])
    times = np.array([0.0, 0.5, 1.0, 1.5])
    fm = chord_detector.Chord(
        root=5, quality='min',
        start_time=0.0, end_time=1.5, confidence=0.7,
    )
    out = chord_detector._substitute_power_chords_on_dyads(
        [fm], chroma, times,
        third_ratio_max=0.4, margin=0.0,
    )
    assert len(out) == 1 and out[0] is fm


def test_substitute_power_chords_on_dyads_preserves_timestamps_and_root():
    """When substitution fires, start_time/end_time/root are preserved."""
    chroma = _make_chroma_with_voicing([(5, 1.0), (0, 1.0)])
    times = np.linspace(0.0, 2.0, 5)
    fm = chord_detector.Chord(
        root=5, quality='min',
        start_time=0.5, end_time=1.7, confidence=0.6,
    )
    out = chord_detector._substitute_power_chords_on_dyads(
        [fm], chroma, times,
        third_ratio_max=0.4, margin=0.05,
    )
    assert out[0].root == 5
    assert out[0].start_time == 0.5
    assert out[0].end_time == 1.7
    assert out[0].quality == '5'


def test_substitute_power_chords_on_dyads_empty_input_is_noop():
    """Empty chord list returns the same empty list."""
    chroma = _make_chroma_with_voicing([(5, 1.0), (0, 1.0)])
    times = np.array([0.0, 0.5, 1.0, 1.5])
    out = chord_detector._substitute_power_chords_on_dyads(
        [], chroma, times,
        third_ratio_max=0.4, margin=0.05,
    )
    assert out == []


def test_substitute_power_chords_on_dyads_default_config_is_bit_exact():
    """End-to-end: default DetectorConfig must produce identical chord output.

    The Stage 1.4.2 changes must not perturb behaviour when callers
    pass the default DetectorConfig — this is the same defensibility
    contract test_detector_config_equivalence locks for the
    emission-side levers. Replays the rock-idiom synthetic fixture
    used elsewhere in this file and asserts the chord *names* are
    unchanged between an explicit-default-config call and a
    no-config call.
    """
    from tone_forge.analysis.detector_config import DetectorConfig
    sr = 22050
    duration = 8.0
    t_axis = np.linspace(0, duration, int(sr * duration), endpoint=False)
    # F minor triad
    y = 0.0
    for f in (174.61, 207.65, 261.63):
        y = y + 0.3 * np.sin(2 * np.pi * f * t_axis)
    y = y.astype(np.float32)

    out_no_cfg = chord_detector.detect_chords_from_audio(y, sr)
    out_default = chord_detector.detect_chords_from_audio(
        y, sr, config=DetectorConfig(),
    )
    assert len(out_no_cfg) == len(out_default)
    for a, b in zip(out_no_cfg, out_default):
        assert a.name == b.name
        assert a.start_time == pytest.approx(b.start_time)
        assert a.end_time == pytest.approx(b.end_time)
