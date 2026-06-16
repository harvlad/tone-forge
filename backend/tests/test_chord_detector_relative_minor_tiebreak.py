"""Unit tests for Stage 1.2 — relative-major/minor key tie-breaker.

Pins the predicates and helper behaviour. The tie-break itself has no
effect on chord output for relative pairs (they share the diatonic
set), but the helper contracts are still worth pinning for future
reuse — display labeling, parallel-pair work, etc.
"""

import numpy as np

from tone_forge.analysis import chord_detector


# ---------------------------------------------------------------------------
# _is_relative_major_minor_pair
# ---------------------------------------------------------------------------

def test_is_relative_pair_c_major_a_minor() -> None:
    """C major and A minor are the canonical relative-major/minor pair.

    A minor's tonic A sits at pitch class 9, which is (C + 9) % 12.
    """
    assert chord_detector._is_relative_major_minor_pair(0, 'major', 9, 'minor')
    assert chord_detector._is_relative_major_minor_pair(9, 'minor', 0, 'major')


def test_is_relative_pair_e_major_csharp_minor() -> None:
    """E major and C# minor — the pub_feed case."""
    E_ROOT, C_SHARP_ROOT = 4, 1
    assert chord_detector._is_relative_major_minor_pair(
        E_ROOT, 'major', C_SHARP_ROOT, 'minor'
    )


def test_is_relative_pair_rejects_parallel_pairs() -> None:
    """Same-root pairs (parallel major/minor) are NOT relative pairs."""
    assert not chord_detector._is_relative_major_minor_pair(
        2, 'major', 2, 'minor'
    ), "D major and D minor are parallel, not relative"


def test_is_relative_pair_rejects_same_mode() -> None:
    """Two majors or two minors can never be a relative pair."""
    assert not chord_detector._is_relative_major_minor_pair(0, 'major', 7, 'major')
    assert not chord_detector._is_relative_major_minor_pair(0, 'minor', 7, 'minor')


def test_is_relative_pair_rejects_unrelated_pairs() -> None:
    """A major / D minor share no tonal-center relationship."""
    assert not chord_detector._is_relative_major_minor_pair(
        9, 'major', 2, 'minor'
    )


# ---------------------------------------------------------------------------
# _rank_keys_by_krumhansl
# ---------------------------------------------------------------------------

def test_rank_keys_returns_all_24_candidates_sorted() -> None:
    """The ranker returns one entry per (root, mode) pair, sorted desc."""
    chroma = np.zeros((12, 1))
    for pc in (0, 4, 7):  # C major triad
        chroma[pc, 0] = 1.0
    ranked = chord_detector._rank_keys_by_krumhansl(chroma)
    assert len(ranked) == 24
    scores = [s for s, _, _ in ranked]
    assert scores == sorted(scores, reverse=True)


def test_rank_keys_top_pick_matches_detect_key_from_chroma() -> None:
    """The ranker's top-1 must match what `_detect_key_from_chroma` returns.

    They share an implementation under the hood; this pins that contract
    so a future refactor cannot silently let them disagree.
    """
    rng = np.random.default_rng(seed=20251201)
    for _ in range(10):
        chroma = np.clip(rng.normal(loc=0.5, scale=0.3, size=(12, 5)), 0.0, None)
        ranked = chord_detector._rank_keys_by_krumhansl(chroma)
        top_score, top_root, top_mode = ranked[0]
        detect_root, detect_mode, _strength = chord_detector._detect_key_from_chroma(chroma)
        assert (detect_root, detect_mode) == (top_root, top_mode)


# ---------------------------------------------------------------------------
# _bass_pitch_class_dist
# ---------------------------------------------------------------------------

def test_bass_pitch_class_dist_returns_none_for_empty_input() -> None:
    assert chord_detector._bass_pitch_class_dist(None, 22050) is None
    assert chord_detector._bass_pitch_class_dist(np.zeros(0), 22050) is None


def test_bass_pitch_class_dist_returns_none_for_silence() -> None:
    """Pyin on pure silence yields no voiced frames → None."""
    silence = np.zeros(22050)  # 1s of silence
    assert chord_detector._bass_pitch_class_dist(silence, 22050) is None


def test_bass_pitch_class_dist_sums_to_one_when_voiced() -> None:
    """A synthetic 110 Hz sine (A2) yields a normalised PC histogram."""
    sr = 22050
    duration_s = 2.0
    t = np.arange(int(sr * duration_s)) / sr
    y = 0.5 * np.sin(2 * np.pi * 110.0 * t).astype(np.float32)
    hist = chord_detector._bass_pitch_class_dist(y, sr)
    assert hist is not None
    assert hist.shape == (12,)
    assert abs(float(hist.sum()) - 1.0) < 1e-6
    # A is pitch class 9; the sine should put most mass there.
    assert int(np.argmax(hist)) == 9


# ---------------------------------------------------------------------------
# _maybe_relative_pair_tiebreak
# ---------------------------------------------------------------------------

def test_tiebreak_returns_none_without_bass() -> None:
    """No bass input → tie-break can't fire."""
    chroma = np.ones((12, 1)) * 0.5
    result = chord_detector._maybe_relative_pair_tiebreak(
        chroma, 0, 'major', None, 22050
    )
    assert result is None


def test_tiebreak_returns_none_when_margin_exceeds_threshold() -> None:
    """A clearly-dominant best key should not trigger the swap."""
    sr = 22050
    chroma = np.zeros((12, 1))
    for pc in (0, 4, 7):  # clean C major
        chroma[pc, 0] = 1.0
    bass = np.sin(2 * np.pi * 65.41 * np.arange(sr) / sr).astype(np.float32)  # C2
    # With a clean triad the Krumhansl margin between best and runner-up
    # is well above _KEY_TIE_MARGIN; the tie-break must not fire.
    result = chord_detector._maybe_relative_pair_tiebreak(
        chroma, 0, 'major', bass, sr,
    )
    assert result is None


def test_key_tie_margin_constant_pinned() -> None:
    """Pin _KEY_TIE_MARGIN so future tuning is explicit."""
    assert chord_detector._KEY_TIE_MARGIN == 0.02
