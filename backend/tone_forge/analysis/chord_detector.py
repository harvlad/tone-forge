"""
Chord detection and harmonic analysis.

Detects chords from audio or MIDI data, identifies progressions,
and can group scattered notes into coherent chord voicings.
"""

import numpy as np
import logging
from typing import Any, List, Tuple, Optional, Dict
from dataclasses import dataclass

from .detector_config import DetectorConfig

logger = logging.getLogger(__name__)

# Chord templates (intervals from root).
#
# The full extended set is kept here because under L2 cosine similarity
# the richer templates (9-chords especially) score above the 0.70
# cutoff on heavily-overdriven sources where the bare-triad template
# would fall to ~0.55-0.65. Stripping the extensions from the matching
# pool caused Pub Feed to collapse back to ~1 region (most windows
# failing the 0.70 floor).
#
# What the user sees in the ribbon is *not* the raw quality from this
# table — it's the collapsed display label from `_collapse_quality()`,
# which maps extensions back to the underlying triad family ("min9" ->
# "m", "maj9" -> "", "maj7" -> "", "dom7" -> "7"). The 9-chord variants
# are doing scoring work, not labelling work.
CHORD_TEMPLATES = {
    'maj': [0, 4, 7],
    'min': [0, 3, 7],
    'dim': [0, 3, 6],
    'aug': [0, 4, 8],
    'maj7': [0, 4, 7, 11],
    'min7': [0, 3, 7, 10],
    'dom7': [0, 4, 7, 10],
    'dim7': [0, 3, 6, 9],
    'sus2': [0, 2, 7],
    'sus4': [0, 5, 7],
    'add9': [0, 4, 7, 14],
    'min9': [0, 3, 7, 10, 14],
    'maj9': [0, 4, 7, 11, 14],
    # Power chord: root + perfect 5th only, no 3rd. Phase 3 of the
    # chord-detector rebuild. Overdriven guitar idioms (punk, garage,
    # hard rock — Pub Feed's vocabulary) voice chords as root + 5th
    # with the 3rd suppressed by saturation. Without this template
    # the matcher has no clean target for a power-chord chroma
    # ({root: high, 5th: high, 3rd: ~0}) and cosine ties scatter to
    # whichever triad happens to overlap the {root, 5th} dyad —
    # commonly the relative minor (which shares two notes with the
    # power chord's "implied" major). Pub Feed Phase 1 baseline:
    # A5 -> A correct only 15s of 145s; A5 -> B/C#m/F#m dominant.
    '5': [0, 7],
}


def _hpcp(y: np.ndarray, sr: int, hop_length: int = 512) -> np.ndarray:
    """36 -> 12 max-pool HPCP chroma with harmonic-5th suppression.

    Replacement for ``librosa.feature.chroma_cqt(n_chroma=12)`` in the
    chord-detector pipeline. See the rationale block above the call
    site in ``detect_chords_from_audio`` for *why*; this is the *how*.

    Steps:
      1. Compute 36-bin chroma_cqt (3 bins per semitone). The extra
         resolution absorbs detuning, vibrato, and pitched-instrument
         pitch glides without spreading mass across two semitones.
      2. L2-normalise each column (per-frame) so subsequent arithmetic
         is comparable across loud/quiet sections.
      3. Max-pool the 3 bins per semitone down to 12 bins. Max-pool
         (vs sum) prefers the single closest bin and suppresses
         leakage from a neighbouring semitone's tails.
      4. Subtract 0.3 * (perfect-5th-below neighbour) from each bin
         and clip at 0. The 3rd harmonic of pitch class p lands at
         (p + 7) % 12; this step removes the predictable overdrive-
         guitar overtone contribution before the template matcher
         sees the chroma vector.

    Args:
        y: mono audio samples
        sr: sample rate
        hop_length: hop length (frames per chroma column)

    Returns:
        chroma matrix shape (12, n_frames), values in [0, ~1].
    """
    import librosa

    # 36-bin CQT chroma: 3 bins per semitone.
    chroma_hr = librosa.feature.chroma_cqt(
        y=y, sr=sr, hop_length=hop_length, n_chroma=36, n_octaves=7,
    )

    # Per-frame L2 normalise (axis=0 == over the 36 bins).
    norms = np.linalg.norm(chroma_hr, axis=0, keepdims=True) + 1e-9
    chroma_hr = chroma_hr / norms

    # Max-pool 36 -> 12 bins. Reshape into (12 semitones, 3 sub-bins,
    # n_frames) and reduce over the sub-bin axis.
    n_frames = chroma_hr.shape[1]
    chroma_12 = chroma_hr.reshape(12, 3, n_frames).max(axis=1)

    # Harmonic-5th suppression DISABLED after empirical regression.
    #
    # Both naive (always-on) and conditional (q < 0.5 * (q-7)) variants
    # were measured against Pub Feed:
    #     Phase 1 (raw 12-bin CQT, no HPCP):   WCSR 0.1636
    #     HPCP + always-on 0.3 suppression:     WCSR 0.0940
    #     HPCP + conditional 0.3 suppression:   WCSR 0.0752
    #     HPCP, suppression disabled:           [this code path]
    #
    # The dominant error mode on Pub Feed is structural — there is no
    # power-chord ("5") template in the matcher, so A5 (= {A, E},
    # no C#) has no good triad to match and cosine ties scatter to
    # B, C#m, F#m, E. That's a Phase 3 problem, not a Phase 2 one.
    # Subtracting the 5th overtone makes the situation worse: it
    # strips real chord 5ths out of major triads and pushes A
    # toward the {A, C#} dyad that matches F#m more strongly.
    #
    # We keep the 36-bin -> 12-bin max-pool step (above) which
    # absorbs tuning drift cleanly; the overtone suppression is left
    # here as a no-op so it's visible in the diff and easy to
    # re-enable later (e.g. after Phase 3 introduces power-chord
    # templates, the suppression may be re-evaluated against the
    # power-chord-aware matcher's confusion matrix).

    return chroma_12


def _collapse_quality(quality: str) -> str:
    """Map an extended chord quality to its display label.

    Cosine similarity over harmonic-spread chroma reliably identifies
    the *root* and *family* (major-ish vs minor-ish), but the choice
    of extension within that family is noisy — an A major triad with
    overdriven harmonics scores about the same against Amaj, Amaj7
    and Amaj9, with the 9-chord winning by a small margin due to
    chroma spread. Reading "Amaj9" off the ribbon when the underlying
    chord is just "A" is misleading; collapse extensions to their
    triad form for display.
    """
    # Drop the major-family extensions entirely. "Amaj7" -> "A" etc.
    if quality in ('maj', 'maj7', 'maj9', 'add9'):
        return 'maj'
    # Keep dom7 as a "7" — it's harmonically distinct from a major
    # triad (the b7 is a real chord tone in V7/blues idioms). Mapping
    # it down to maj would lose useful information.
    if quality == 'dom7':
        return 'dom7'
    # Minor-family extensions collapse to bare minor.
    if quality in ('min', 'min7', 'min9'):
        return 'min'
    # Diminished family collapses to dim (dim7 is a real chord but
    # rare enough that the false-positive risk from over-triggering
    # the 4-note template outweighs the labeling precision).
    if quality in ('dim', 'dim7'):
        return 'dim'
    # Power chord — Phase 3 of the rebuild. Already minimal; pass
    # through unchanged so the display label reads as "A5" rather
    # than "A".
    if quality == '5':
        return '5'
    # sus2/sus4/aug are already minimal; pass through.
    return quality

NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']


# Phase 4 of the chord-detector rebuild (see plan
# `effervescent-twirling-neumann`): detection is routed through an
# HMM/Viterbi sequence model over the (root, collapsed_quality) state
# space plus a no-chord state, with diatonic-biased emissions and a
# self-loop transition prior. The Viterbi-decoded state sequence is the
# detector's output; there is no per-window-argmax fallback.
#
# The legacy heuristic post-processing stack (mode-filter label
# smoothing, gap-bridging, drop-short-regions) has been removed: the
# Viterbi self-loop term produces a temporally coherent label sequence
# natively, without destroying real short regions. See
# `_compute_emission_scores`, `_build_transition_matrix`,
# `_viterbi_decode`, and `_viterbi_states_to_chords`.
#
# Phase-progression on Pub Feed triad-relaxed WCSR:
#   pre-strip (heuristics ON, per-window argmax)  : 0.0707
#   Phase 1   (heuristics stripped)               : 0.1636
#   Phase 3   (+ power-chord '5' templates)       : 0.1636
#   Phase 4   (Viterbi)                           : 0.1933
#   Phase 5   (+ bass-routed emission bias)       : 0.2347
# Phase 5 finding: The Chats' bass guitar plays F# (and a moving line
# F# / B / A) under what the tab transcriber labelled "A5". The
# bass-routed multiplier resolved the relative-minor ambiguity toward
# F#m on those windows — musically correct against the recording,
# possibly out-of-sync with the guitar-only ground truth fixture. The
# net WCSR still climbed because the bias also surfaced correct A
# windows that were previously decoded as C#m / B (which dropped 11s
# and 7s respectively in the confusion matrix).
#
# Phase 6   (+ beat-synchronous chroma aggregation): land chord-region
# boundaries on musical beats rather than the arbitrary 0.5s grid by
# routing beats from ``librosa.beat.beat_track`` into
# ``detect_chords_from_audio``. The Viterbi state sequence advances
# one step per beat, so chord changes are structurally constrained to
# beat boundaries. Both production call sites
# (``local_engine/analysis_worker.py`` and
# ``tone_forge/unified_pipeline.py``) wire this through; missing or
# out-of-range tempos degrade gracefully to the fixed-window path.
# WCSR remeasurement on Pub Feed deferred until source audio is
# re-imported; Phase 7 multi-fixture regression continues.


# Krumhansl-Schmuckler tonal hierarchy profiles.
#
# 12-element vectors expressing the relative perceived "weight" of each
# pitch class within a major or minor key, derived from Krumhansl &
# Kessler's 1982 probe-tone experiments. Aggregated chroma from a real
# tonal song correlates most strongly with the rotated profile of the
# song's actual key. This is the standard music-IR approach to key
# detection — simple, well-cited, and robust against single-window
# noise (it operates on the full-song chroma sum, not per-window).
_KS_MAJOR = np.array(
    [6.35, 2.23, 3.48, 2.33, 4.38, 4.09,
     2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
)
_KS_MINOR = np.array(
    [6.33, 2.68, 3.52, 5.38, 2.60, 3.53,
     2.54, 4.75, 3.98, 2.69, 3.34, 3.17]
)


# Diatonic scale-degree harmonisations for major and minor modes.
#
# Each list element is (semitones-from-tonic, family) for one degree of
# the standard 7-degree harmonisation. These define which (root, family)
# pairs count as "in key" for the diatonic biasing in chord scoring.
# Covers basic-triad harmony only; secondary dominants, modal mixture,
# and chromatic chords are intentionally absent (they would defeat the
# bias's purpose of disambiguating template ties).
_MAJOR_DEGREES = [
    (0,  'maj'),  # I
    (2,  'min'),  # ii
    (4,  'min'),  # iii
    (5,  'maj'),  # IV
    (7,  'maj'),  # V
    (9,  'min'),  # vi
    (11, 'dim'),  # vii°
]
_MINOR_DEGREES = [
    (0,  'min'),  # i
    (2,  'dim'),  # ii°
    (3,  'maj'),  # III
    (5,  'min'),  # iv
    (7,  'min'),  # v   (natural minor; harmonic-minor V is covered by V degree of relative major)
    (8,  'maj'),  # VI
    (10, 'maj'),  # VII
]


def _quality_family(quality: str) -> str:
    """Collapse an extended chord quality to its diatonic family.

    Used by the diatonic biasing in `_match_chord_template` to decide
    whether a candidate's quality is "in family" for the diatonic
    degree at that root. Major-family extensions (maj7, maj9, add9)
    and dominant 7th (which functions as major-family on the V degree)
    all collapse to 'maj'. Minor extensions collapse to 'min'.
    Diminished pass through. sus2/sus4/aug stay distinct — they are
    tonally ambiguous and biasing them either way could fight the
    matcher.
    """
    if quality in ('maj', 'maj7', 'maj9', 'add9', 'dom7'):
        return 'maj'
    if quality in ('min', 'min7', 'min9'):
        return 'min'
    if quality in ('dim', 'dim7'):
        return 'dim'
    return quality


def _diatonic_chord_set(key_root: int, key_mode: str) -> set:
    """Build the set of (root, family) pairs diatonic to a given key.

    Example: _diatonic_chord_set(4, 'major') (= E major) returns
      {(4,'maj'), (6,'min'), (8,'min'), (9,'maj'), (11,'maj'),
       (1,'min'), (3,'dim')}
    which expresses E major's I-ii-iii-IV-V-vi-vii° = E, F#m, G#m, A,
    B, C#m, D#dim.
    """
    degrees = _MAJOR_DEGREES if key_mode == 'major' else _MINOR_DEGREES
    return {((key_root + d) % 12, f) for d, f in degrees}


# Stage-1.2 (heuristic ladder): normaliser for the Krumhansl best /
# second-best correlation margin used by Stage 1.2's relative-major/
# minor key tie-breaker. Calibrated empirically against the fixture
# corpus, where margins span ~0.0004 (essentially-tied) to ~0.006
# (clearly-dominant best key). At 0.01 the strength signal saturates
# above the "clearly dominant" range and stays in [0, 1) for genuinely
# ambiguous songs. Used only as an *input* to the key-tie-break
# decision; NOT used to scale the diatonic bias (see the reverted
# Stage 1.1 note at the use-site in detect_chords).
_KEY_STRENGTH_MARGIN_NORM = 0.01


def _rank_keys_by_krumhansl(
    chroma: np.ndarray,
) -> List[Tuple[float, int, str]]:
    """Rank all 24 (root, mode) keys by Krumhansl cosine correlation.

    Returns a list of ``(score, root, mode)`` tuples sorted in
    descending score order. Internal helper consumed by both
    ``_detect_key_from_chroma`` (top-1) and Stage 1.2's relative-pair
    tie-break path in ``detect_chords`` (needs top-2 with scores).
    """
    chroma_sum = np.mean(chroma, axis=1)
    chroma_sum = chroma_sum / (np.linalg.norm(chroma_sum) + 1e-9)
    scored: List[Tuple[float, int, str]] = []
    for root in range(12):
        for mode, profile in (('major', _KS_MAJOR), ('minor', _KS_MINOR)):
            rotated = np.roll(profile, root)
            rotated = rotated / (np.linalg.norm(rotated) + 1e-9)
            corr = float(np.dot(chroma_sum, rotated))
            scored.append((corr, root, mode))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored


def _detect_key_from_chroma(chroma: np.ndarray) -> Tuple[int, str, float]:
    """Estimate (key_root, key_mode, key_strength) from a chroma matrix.

    Aggregates chroma across time, L2-normalises both the result and
    the Krumhansl-Schmuckler key profile (rotated for each of 12 roots
    × 2 modes), and returns the (root, mode) pair with maximum cosine
    correlation plus a ``key_strength`` ∈ [0, 1] describing how
    confident that pick is.

    ``key_strength`` = ``clip(best_score - second_best_score, 0,
    _KEY_STRENGTH_MARGIN_NORM) / _KEY_STRENGTH_MARGIN_NORM``. Values
    near 1.0 mean the best key clearly dominates; values near 0 mean
    the top two candidates are essentially tied and Stage 1.2's
    relative-pair tie-break may swap the pick.

    This is the seed for diatonic-preference bias in chord scoring.
    The bias resolves template ambiguities that the per-window cosine
    matcher cannot — e.g. a B5 power chord in an E-major song matches
    multiple triad templates with near-equal raw scores, but only B
    major is diatonic in E major; biasing the score reliably picks
    the diatonic option without changing the chord-vs-noise gate.

    Returns ('C', 'major', 0.0) as the fallback for empty or all-zero
    chroma.
    """
    candidates = _rank_keys_by_krumhansl(chroma)
    if not candidates:
        return 0, 'major', 0.0

    best_score, best_root, best_mode = candidates[0]
    second_best_score = candidates[1][0] if len(candidates) > 1 else -np.inf

    if second_best_score == -np.inf:
        key_strength = 0.0
    else:
        margin = best_score - second_best_score
        key_strength = max(0.0, min(margin, _KEY_STRENGTH_MARGIN_NORM))
        key_strength /= _KEY_STRENGTH_MARGIN_NORM
    return best_root, best_mode, key_strength


# Stage 1.2 (heuristic ladder): tie-break threshold for the
# relative-major/minor key disambiguation. When the top-2 Krumhansl
# candidates are a relative-major/minor pair (e.g. C# minor and its
# relative major E) within this many correlation points of each other,
# the chroma alone cannot tell them apart (relative pairs share all
# seven diatonic pitch classes), and the bass line's tonic preference
# is the disambiguator. Calibrated to 0.02 because measured real-audio
# relative-pair margins are ≤ 0.01 (pub_feed: C#m vs E maj at 0.006);
# 0.02 covers that case with headroom while staying tight enough that
# clearly-distinct keys (margin > 0.02) never trigger the swap.
_KEY_TIE_MARGIN = 0.02


def _is_relative_major_minor_pair(
    root1: int, mode1: str, root2: int, mode2: str,
) -> bool:
    """Test whether two (root, mode) keys form a relative-major/minor pair.

    A major key with tonic ``M`` has its relative minor at
    ``(M + 9) % 12`` (equivalently, three semitones below ``M``).
    Examples: C major ↔ A minor, E major ↔ C# minor, G major ↔ E minor.
    Same-mode pairs are never relative; same-root pairs are *parallel*
    not relative and also return False.
    """
    if mode1 == mode2:
        return False
    if mode1 == 'major':
        maj_root, min_root = root1, root2
    else:
        maj_root, min_root = root2, root1
    return min_root == (maj_root + 9) % 12


def _bass_pitch_class_dist(
    bass_y: Optional[np.ndarray],
    sr: int,
    max_seconds: float = 30.0,
) -> Optional[np.ndarray]:
    """Compute a normalised pitch-class histogram of voiced bass pitches.

    Used by Stage 1.2 to break the relative-major/minor key tie: the
    bass-line's tonic preference is the dominant disambiguating signal
    when chroma alone is ambiguous. The histogram is normalised to
    sum to 1 across the 12 pitch classes; an unvoiced or unavailable
    bass returns ``None`` and the tie-break path falls back to the
    Krumhansl pick.

    The window is the first ``max_seconds`` of the bass stem. Most
    songs establish their tonal center in the intro/first verse, and
    later sections may move through modulations that bias the
    histogram away from the home key. 30 s is enough to cover the
    intro of a typical pop/rock arrangement.
    """
    if bass_y is None or len(bass_y) == 0:
        return None
    import librosa
    n_samples = min(len(bass_y), int(max_seconds * sr))
    y_window = bass_y[:n_samples]
    try:
        f0, voiced_flag, _ = librosa.pyin(
            y_window,
            fmin=40.0,
            fmax=250.0,
            sr=sr,
        )
    except Exception:
        return None
    with np.errstate(invalid='ignore', divide='ignore'):
        midi = 69.0 + 12.0 * np.log2(f0 / 440.0)
    voiced_midi = midi[(voiced_flag == True) & (~np.isnan(midi))]  # noqa: E712
    if voiced_midi.size == 0:
        return None
    pcs = (np.round(voiced_midi).astype(np.int64)) % 12
    histogram = np.bincount(pcs, minlength=12).astype(np.float64)
    total = histogram.sum()
    if total <= 0:
        return None
    return histogram / total


def _maybe_relative_pair_tiebreak(
    chroma_smooth: np.ndarray,
    key_root: int,
    key_mode: str,
    bass_y: Optional[np.ndarray],
    sr: int,
) -> Optional[Tuple[int, str, str]]:
    """Apply Stage 1.2 relative-major/minor tie-break if the conditions hold.

    Returns ``(new_root, new_mode, reason)`` on a swap, or ``None`` to
    leave the original Krumhansl pick in place. The ``reason`` field
    is a short string used for the log line at the call site.
    """
    if bass_y is None:
        return None
    candidates = _rank_keys_by_krumhansl(chroma_smooth)
    if len(candidates) < 2:
        return None
    best_score, best_root, best_mode = candidates[0]
    second_score, second_root, second_mode = candidates[1]
    # Sanity: top-1 should match what _detect_key_from_chroma returned.
    # If a future caller passes the *wrong* smoothed chroma here, we
    # bail rather than silently rewrite the key.
    if (best_root, best_mode) != (key_root, key_mode):
        return None
    margin = best_score - second_score
    if margin >= _KEY_TIE_MARGIN:
        return None
    if not _is_relative_major_minor_pair(
        best_root, best_mode, second_root, second_mode,
    ):
        return None
    bass_pc = _bass_pitch_class_dist(bass_y, sr)
    if bass_pc is None:
        return None
    best_mass = float(bass_pc[best_root])
    runner_mass = float(bass_pc[second_root])
    # Demand a clear bass preference for the runner-up tonic. Equal
    # mass = no signal; the original Krumhansl pick stands.
    if runner_mass <= best_mass:
        return None
    reason = (
        f"margin={margin:.4f}<{_KEY_TIE_MARGIN}; "
        f"bass[{NOTE_NAMES[second_root]}]={runner_mass:.3f} > "
        f"bass[{NOTE_NAMES[best_root]}]={best_mass:.3f}"
    )
    return second_root, second_mode, reason


@dataclass
class Chord:
    """Represents a detected chord."""
    root: int  # 0-11 (C=0, C#=1, etc.)
    quality: str  # 'maj', 'min', 'dom7', etc.
    start_time: float
    end_time: float
    confidence: float
    bass_note: Optional[int] = None  # For slash chords

    @property
    def name(self) -> str:
        """Get chord name like 'Cmaj7' or 'F#min'."""
        root_name = NOTE_NAMES[self.root]
        if self.quality == 'maj':
            return root_name
        elif self.quality == 'min':
            return f"{root_name}m"
        else:
            return f"{root_name}{self.quality}"

    @property
    def notes(self) -> List[int]:
        """Get pitch classes in this chord."""
        template = CHORD_TEMPLATES.get(self.quality, [0, 4, 7])
        return [(self.root + interval) % 12 for interval in template]


@dataclass
class ChordProgression:
    """A sequence of chords with timing."""
    chords: List[Chord]
    key_root: int
    key_quality: str  # 'major' or 'minor'
    tempo_bpm: float

    def __str__(self) -> str:
        chord_names = [c.name for c in self.chords]
        return f"{NOTE_NAMES[self.key_root]} {self.key_quality}: {' | '.join(chord_names)}"


def detect_chords_from_audio(
    y: np.ndarray,
    sr: int,
    hop_length: int = 512,
    min_chord_duration: float = 0.5,
    bass_y: Optional[np.ndarray] = None,
    beats_s: Optional[np.ndarray] = None,
    config: Optional[DetectorConfig] = None,
    key_out: Optional[Dict[str, Any]] = None,
) -> List[Chord]:
    """
    Detect chords from audio using chroma features.

    Args:
        y: Audio signal
        sr: Sample rate
        hop_length: Hop length for analysis
        min_chord_duration: Minimum chord duration in seconds
        bass_y: Optional bass-stem audio at the same sample rate. When
            provided, pyin extracts a per-window dominant bass pitch
            class which is used to bias the Viterbi emission scores
            toward templates whose root matches the heard bass note
            (Phase 5). Disambiguates relative-major/minor pairs that
            chroma alone cannot separate. When omitted, the detector
            runs without bass bias and the output matches Phase 4
            behaviour.
        beats_s: Optional beat-timestamp array in seconds, typically
            from ``librosa.beat.beat_track``. When provided, chroma is
            aggregated per beat and the Viterbi decoder runs over
            beat-indexed observations, so chord-change boundaries snap
            to musical beats rather than the arbitrary 0.5s grid
            (Phase 6). When omitted, falls back to fixed
            ``min_chord_duration`` windows.

    Returns:
        List of detected Chord objects
    """
    import librosa

    # Resolve numeric levers from the optional DetectorConfig. When
    # the caller omits ``config`` (or passes None) we substitute the
    # default-constructed instance, whose field values are by design
    # identical to the inline constants this function used to hold.
    # The local-variable names (DIATONIC_BIAS, COS_CUTOFF,
    # BASS_ROOT_BIAS) are retained verbatim so the downstream code
    # in this function did not need to change.
    _cfg = config if config is not None else DetectorConfig()

    # Chroma source: raw 12-bin chroma_cqt.
    #
    # Phase 2 of the rebuild explored an HPCP refinement (36-bin
    # CQT -> per-frame L2 -> 12-bin max-pool, optional harmonic-5th
    # suppression) and measured all variants against the Pub Feed
    # fixture:
    #
    #     Phase 1 raw 12-bin chroma_cqt:                WCSR 0.1636
    #     HPCP + always-on 0.3 5th suppression:         WCSR 0.0940
    #     HPCP + conditional 0.3 5th suppression:       WCSR 0.0752
    #     HPCP, suppression disabled (max-pool only):   WCSR 0.0818
    #
    # All HPCP variants regressed. Two contributing factors:
    #
    #   (a) 36 -> 12 max-pool preserves the strongest sub-bin per
    #       semitone but discards mass from the other two. Triad
    #       template cosine matching depends on cumulative mass
    #       across the three chord tones; max-pool punishes that.
    #
    #   (b) Harmonic-5th suppression treats the 5th of a real major
    #       triad as if it were a 3rd-harmonic overtone. A major
    #       ({A, C#, E}) loses E and starts to look like the {A, C#}
    #       dyad of F#m, doubling A->F#m confusion.
    #
    # The HPCP helper (``_hpcp``) is left in the module as documented
    # future infrastructure — when Phase 3 (power-chord templates)
    # closes the structural gap, the HPCP path can be re-evaluated
    # against the new confusion matrix. For now the production code
    # uses raw chroma_cqt, which empirically beats HPCP under the
    # current matcher.
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=hop_length)

    # Smooth chroma to reduce noise
    chroma_smooth = librosa.decompose.nn_filter(
        chroma, aggregate=np.median, metric='cosine'
    )

    # Time array
    times = librosa.times_like(chroma, sr=sr, hop_length=hop_length)

    # Detect the song's key from the full-song aggregated chroma.
    #
    # Per-window template matching cannot disambiguate chord pairs that
    # share most of their pitch classes — F#m and F# major (differ only
    # on the 3rd), B power chord and F#m (a B5 is just B+F#, which
    # overlaps F#m's F# bin), E major and Em (differ on the 3rd). The
    # cosine matcher picks the wrong member half the time depending on
    # which other pitch classes happen to bleed into the chroma vector
    # for that window. Cross-checking against a known tab of Pub Feed:
    # the song is in E major and uses I-vi-IV-V (E, F#m, A, B); the raw
    # detector was surfacing F# major (non-diatonic A#) and Em
    # (non-diatonic G) instead of the correct F#m and E. Both wrong
    # picks share a structural property: they are *not* diatonic in
    # E major.
    #
    # Diatonic biasing applies a small (DIATONIC_BIAS = 0.10)
    # multiplicative bonus to the cosine score of candidates whose
    # (root, family) sits inside the detected key's diatonic-chord set.
    # The bonus is applied only for argmax tie-breaking — the *returned*
    # confidence is the raw cosine, so the COS_CUTOFF gate still
    # measures genuine chord-vs-noise fit, not bias-inflated score.
    #
    # 0.10 is calibrated so that the bias overrides the typical 1-3%
    # cosine gap between sibling templates (e.g. F#m vs F# major on
    # overdriven chroma) but cannot override the larger gaps that
    # separate genuinely different chords (10%+).
    key_root, key_mode, key_strength = _detect_key_from_chroma(chroma_smooth)
    # Stage 1.2: relative-major/minor tie-break using bass tonic
    # preference. Fires when Krumhansl's top-2 candidates form a
    # relative-major/minor pair within _KEY_TIE_MARGIN and bass mass
    # supports the runner-up tonic. Pub Feed empirical: best=(C# minor
    # 0.978) vs runner=(E major 0.972), margin=0.006, relative pair,
    # bass-on-E (0.131) > bass-on-C# (0.000) -> swap to E major.
    #
    # *** Important caveat (Stage 1.2 finding) *** Relative pairs share
    # all seven diatonic triads by construction (E major and C# minor
    # both have diatonic set {E, F#m, G#m, A, B, C#m, D#dim}). Since
    # ``_diatonic_chord_set`` is the only downstream consumer of
    # (key_root, key_mode) inside chord detection, this tie-break has
    # *zero effect on chord output* — measured: pub_feed WCSR is
    # 0.2257 with and without the swap. The infrastructure is kept
    # because it produces an honest log line and the helper functions
    # are reusable (display labeling for key/section UI, future work
    # that respects parallel-major/minor distinctions where diatonic
    # sets DO differ, etc.). For chord-detection WCSR, the real lever
    # is Stage 1.3 (bass-anchored ROOT prior in Viterbi transitions),
    # which directly targets the per-window relative-pair confusion.
    tiebreak = _maybe_relative_pair_tiebreak(
        chroma_smooth, key_root, key_mode, bass_y, sr,
    )
    if tiebreak is not None:
        new_root, new_mode, reason = tiebreak
        logger.info(
            f"Key tie-break: {NOTE_NAMES[key_root]} {key_mode} -> "
            f"{NOTE_NAMES[new_root]} {new_mode} ({reason}) "
            f"[no chord-output effect; relative pairs share diatonic set]"
        )
        key_root, key_mode = new_root, new_mode
    # Phase-7+ key surfacing: if the caller passed an out-dict, populate
    # it with the final (post tie-break) key decision so the pipeline
    # can hoist it to AnalysisResult.detected_key without re-running
    # chroma + Krumhansl. Mirrors the tempo/beats hoist: useful state
    # computed inside a stage was previously logged then dropped on
    # the floor, leaving downstream (re-spelling, key-aware UI) blind.
    if key_out is not None:
        mode_word = 'major' if key_mode == 'major' else 'minor'
        key_out["root"] = int(key_root)
        key_out["mode"] = mode_word
        key_out["strength"] = float(key_strength)
        key_out["label"] = f"{NOTE_NAMES[key_root]} {mode_word}"
    diatonic = _diatonic_chord_set(key_root, key_mode)
    logger.info(
        f"Detected key: {NOTE_NAMES[key_root]} {key_mode} "
        f"(strength={key_strength:.3f}); "
        f"diatonic chord set has {len(diatonic)} (root, family) pairs"
    )
    # Stage 1.1 attempted to scale DIATONIC_BIAS by ``key_strength`` so
    # tonally ambiguous songs would relax the bias. Reverted: empirical
    # Krumhansl margins on real-audio fixtures are 0.0004-0.0060, well
    # below any normaliser that would distinguish "strong" vs "weak"
    # key. Scaling by strength drives the effective bias to ~0 on
    # human-clearly-in-key songs (jump_and_die margin = 0.0004 despite
    # being unambiguously G minor) and the detector regresses into the
    # relative-minor confusion the bias was put in place to fix. The
    # strength signal is still surfaced by ``_detect_key_from_chroma``
    # because Stage 1.2 needs it for the relative-major/minor
    # tie-break on the *key* decision itself (where a normaliser
    # calibrated to actual margins is meaningful).
    DIATONIC_BIAS = _cfg.diatonic_bias

    # Tile the song with fixed-size analysis windows.
    #
    # The previous implementation peak-picked chroma_diff for boundaries
    # (`mean + std` threshold over the first-difference sum). That only
    # fires on bright transient chord changes and silently swallows
    # songs with steady chord textures — overdriven rock, drone, slow
    # transitions, anything where the chroma vector evolves smoothly
    # rather than stepping. For those (the majority of real songs), the
    # peak-picker found one or two boundaries across the entire track
    # and the detector emitted ~1 chord region in 3 minutes of audio.
    # The Pub Feed import reproduced this in production: the full mix
    # surfaced exactly one C#sus4 region (139.88s–145.84s) while the
    # actual song is a recognisable 2-chord vamp the whole way through.
    #
    # Lowering the *confidence* cutoff (9cc11c6) didn't help: when the
    # segmenter only emits one segment, there's only one confidence
    # value to test. The bottleneck was upstream of the cutoff.
    #
    # Fixed windows give O(song_dur / window_dur) candidates that each
    # template-match independently; _merge_consecutive_chords (called
    # below) then compacts adjacent same-chord windows back into chord
    # regions, so long stretches of one chord still surface as one
    # pill — but a genuine change between two windows becomes a
    # boundary "for free", no transient required.
    #
    # Window size = min_chord_duration so we never emit a region shorter
    # than the caller-requested minimum. Step = window (no overlap):
    # overlap would inflate the candidate count without improving
    # boundary resolution (the boundary still lands on a window edge
    # after merging).
    #
    # Phase 6 (hybrid-grid revision): chroma aggregation always uses
    # the fixed ``min_chord_duration``-sized grid. Beats — when the
    # caller provides them — are used downstream, after the Viterbi
    # decoder has emitted its chord regions, to snap region
    # boundaries to the nearest beat. See ``_snap_regions_to_beats``
    # below.
    #
    # The original Phase 6 design drove chroma aggregation off the
    # beat grid directly, with one Viterbi window per beat. That
    # regressed Pub Feed WCSR from 0.2347 (Phase 5 fixed-window) to
    # 0.1739 (bass + beats) and from 0.1900 to 0.1643 (no bass +
    # beats). Diagnosis: at ~95 BPM, beat windows of ~0.63s average
    # ~14 chroma frames per window vs ~11 for 0.5s fixed windows.
    # The longer-averaged per-window chroma is smoother and closer
    # to the song's *modal* (key-vocabulary) distribution; the
    # cosine matcher's discriminative gap between competing
    # templates collapses, and Viterbi commits to whichever
    # modal-key chord (F#m, the diatonic vi on E major Pub Feed)
    # has a marginal emission advantage and self-loops there. The
    # confusion matrix showed A5->F#m mass jumping from 17.8s to
    # 25.5s under beat-aggregation.
    #
    # Conserving the Phase 4/5 emission discriminability while
    # still getting the beat-aligned visual output is what the
    # hybrid grid does. The Viterbi runs over the same 0.5s windows
    # as Phase 4/5 (bit-for-bit unchanged when ``beats_s is None``);
    # the beats only affect the *final* region timestamps.
    frames_per_window = max(1, int(min_chord_duration * sr / hop_length))
    boundaries = list(range(0, chroma.shape[1], frames_per_window))
    if not boundaries or boundaries[-1] != chroma.shape[1]:
        boundaries.append(chroma.shape[1])
    logger.info(
        f"Window grid: fixed {min_chord_duration:.2f}s windows "
        f"({len(boundaries) - 1} windows over "
        f"{chroma.shape[1]} chroma frames); "
        f"beat-snap={'on' if beats_s is not None else 'off'}"
    )

    # Minimum cosine-similarity cutoff for a window to count as a chord.
    #
    # Cosine similarity between a polyphonic chroma vector and a binary
    # chord template has a measured noise floor of ~0.66 (silence and
    # white noise both project onto chroma uniformly enough to score
    # ~0.66 against any 3-note template). Real chord-bearing windows
    # score 0.70+ across the full range of source material:
    #
    #   * Clean synthetic triads:       ~0.99
    #   * Realistic harmonic mix:        0.90–0.99
    #   * Overdriven rock (Pub Feed):    0.69–0.81  (mean ~0.73)
    #   * Single sustained sine:        ~0.58       (not a chord)
    #
    # COS_CUTOFF = 0.70 sits above the silence/noise floor (0.66) and
    # below the lowest-end chord-bearing regime (0.69 for heavily
    # overdriven sources). This is genuinely a "is there a chord here"
    # gate — not a confidence cutoff against template ambiguity. The
    # template-ambiguity case (two adjacent windows match different
    # templates with similar scores) is handled by
    # _merge_consecutive_chords downstream.
    #
    # An earlier draft used an adaptive per-song cutoff
    # `max(0.50, median + 0.3*std)`. That broke on songs with bimodal
    # confidence distributions (alternating strong/weak chord regions)
    # because the median landed between the two modes, and the cutoff
    # filtered out the weaker-but-real chord matches. The fixed floor
    # admits both modes; merging then compacts adjacent same-template
    # windows back into regions.
    COS_CUTOFF = _cfg.cos_cutoff

    # Phase 4: HMM/Viterbi sequence model.
    #
    # The Viterbi decode replaces what used to be a per-window argmax
    # plus a mode-filter / gap-bridge / drop-short heuristic stack. The
    # state space mirrors the user-visible (root, collapsed_quality)
    # alphabet plus a no-chord state. See `_compute_emission_scores`,
    # `_build_transition_matrix`, `_viterbi_decode`, and
    # `_viterbi_states_to_chords` for the math.
    #
    # Feed RAW chroma (not the nn_filter median-smoothed chroma) to the
    # emission step. The nn_filter is a per-frame non-local-means
    # filter: it picks similar-looking frames from anywhere in the song
    # and averages them in. On a song with stable harmonic vocabulary
    # (typical for pop/rock), this pushes every frame toward the song's
    # *modal* chroma — i.e. the key-scale distribution — and erases the
    # per-frame chord signature that the matcher needs to make a chord
    # decision. On Pub Feed (E maj / C# min relative pair), the
    # nn_filter collapsed A5 frames to the key-vocabulary distribution
    # {E, F#, A#, B, C#} where A's peak (0.58) sat *below* A# (0.89),
    # E (0.82), and B (0.77). The Viterbi self-loop bonus already
    # provides the per-frame smoothing the nn_filter was substituting
    # for, so the filter is redundant and actively harmful here.
    # Phase 5: bass-routed disambiguation.
    #
    # The Phase 4 detector still emits ~70s of A5 ground-truth windows
    # as B / C#m / F#m on Pub Feed — relative-major/minor pairs that
    # share two of three pitch classes. Chroma alone cannot break that
    # tie. The bass guitar, however, plays the root note of each chord
    # unambiguously: A under A5, F# under F#m. Routing the bass stem
    # through pyin gives us a per-window pitch class that names the
    # correct root; the emission step then multiplies the cosine score
    # of templates whose root matches by (1 + BASS_ROOT_BIAS).
    #
    # BASS_ROOT_BIAS calibration. The bass-root-track is voiced on
    # ~99% of Pub Feed windows (297 of 300), so the multiplier fires
    # on essentially every window. The right magnitude was found by
    # sweep against the Pub Feed fixture:
    #
    #     BASS_ROOT_BIAS = 0.000  -> WCSR 0.1933 (Phase 4 baseline)
    #     BASS_ROOT_BIAS = 0.025  -> WCSR 0.2166
    #     BASS_ROOT_BIAS = 0.040  -> WCSR 0.2331
    #     BASS_ROOT_BIAS = 0.045  -> WCSR 0.2347  <- plateau low
    #     BASS_ROOT_BIAS = 0.050  -> WCSR 0.2347  <- plateau high (chosen)
    #     BASS_ROOT_BIAS = 0.055  -> WCSR 0.2314
    #     BASS_ROOT_BIAS = 0.075  -> WCSR 0.2085
    #     BASS_ROOT_BIAS = 0.100  -> WCSR 0.2012
    #     BASS_ROOT_BIAS = 0.200  -> WCSR 0.1951
    #
    # 0.05 is the production value: smaller than DIATONIC_BIAS (0.10)
    # so the bass-root multiplier is a tiebreaker on truly ambiguous
    # windows, not a winner-take-all override. Above 0.07 the bass
    # pulls regions into F#m wherever the bass plays F# (which on Pub
    # Feed is the majority of the song's run-time — The Chats'
    # bassist plays F# under bars the guitar transcriber labelled as
    # A5 power chord, a structural relative-minor ambiguity the bass
    # actually resolves *toward* F#m). The 0.05 calibration trusts
    # the chroma signal as the primary classifier and uses the bass
    # only to break ties that chroma can't.
    #
    # When the bass stem is not provided (`bass_y is None`), the
    # emission step skips the bass multiplier and the detector falls
    # back to Phase 4 behaviour bit-for-bit.
    BASS_ROOT_BIAS = _cfg.bass_root_bias
    # Stage 1.3 (REVERTED). A per-transition bias on non-self edges
    # into states whose root matches the bass-root track was hypothesised
    # to help Viterbi "unstick" from relative-pair confusions. Empirical
    # corpus sweep (b ∈ {0.005, 0.010, 0.015, 0.020, 0.025}) showed every
    # value regressed every fixture, with corpus_wcsr decreasing
    # monotonically from 0.7897 (b=0) to 0.7309 (b=0.025). The reward-
    # for-switching-to-bass-root direction over-segments songs whose
    # bass is steady (demolition_warning, jump_and_die) and *reinforces*
    # the F#m mistake on pub_feed because the actual bass on that song
    # tracks F#, not the ground-truth A5 root. Bass disambiguation on
    # pub_feed needs Stage 1.4's power-chord prior (third-absence), not
    # a louder bass signal. The transition-bias infrastructure (helper
    # + optional `transition_in_bias` param on `_viterbi_decode`) was
    # removed since no caller remained; if Stage 3.1 (HCDF-anchored
    # boundaries) wants per-transition priors, it can re-add a focused
    # version sized to that signal.
    bass_track = None
    if bass_y is not None:
        try:
            bass_track = _bass_root_track(
                bass_y, sr, boundaries, hop_length,
            )
            voiced = int((bass_track >= 0).sum())
            logger.info(
                f"Bass-root track: {voiced}/{len(bass_track)} "
                f"windows voiced; pitch-class bias = {BASS_ROOT_BIAS}"
            )
        except Exception as e:
            # Bass-routing is a refinement, not a contract: if pyin
            # explodes (e.g. zero-length stem, sample-rate mismatch
            # the user can't fix) we degrade to no-bass-bias rather
            # than failing chord detection entirely.
            logger.warning(
                f"Bass-root track failed ({e}); falling back to no bass bias"
            )
            bass_track = None

    # Stage 1.4 (REVERTED). The "demote maj/min when third is absent"
    # prior was implemented and swept (ratio ∈ {0.20, 0.30, 0.35, 0.50}
    # x penalty ∈ {0.02, 0.05}). No configuration improved pub_feed
    # and every non-trivial configuration regressed demolition_warning.
    # Root-cause analysis: pub_feed's relative-pair confusion (F#m vs
    # A5) is NOT caused by 5-vs-min template ranking at root=A. The
    # mechanism is that F#m's *minor third* is A, which is the
    # *strongest* bin on a power-chord-A chroma — so F#'s third-absence
    # test fails (the third is the high-mass note) and the F#m
    # emission is never penalised. Demoting A's maj/min while F#m
    # continues to dominate doesn't change the Viterbi path. On
    # demolition_warning (real D minor) the penalty fires on roots
    # where the minor third happens to be momentarily quiet, demoting
    # genuine minor triads.
    #
    # The third-absence helper code is retained as opt-in machinery
    # (defaults to disabled via ``power_chord_*=0``) for future work
    # that wants to combine it with other signals — e.g. an ML
    # harmony LM that has already decided the relative-pair direction.
    # The call site passes zeros, so behaviour is bit-for-bit
    # identical to pre-S1.4.
    # Stage 1.4.1 power-chord prior plumbing. Three numeric levers
    # plus an optional key-conditioning gate live on DetectorConfig.
    # When power_chord_minor_key_only=True, the levers are zeroed
    # for songs whose detected key is NOT minor with high strength
    # (>= 0.7) — the rock idiom this prior targets. For songs that
    # pass the gate (or when the flag is off), the levers flow
    # through to the emission scorer unchanged. With default config
    # (all zeros + False) this branch is a no-op and behaviour
    # matches pre-Stage 1.4.1 exactly.
    _pc_ratio = float(_cfg.power_chord_third_ratio)
    _pc_penalty = float(_cfg.power_chord_penalty)
    _pc_streak = int(_cfg.power_chord_third_min_streak)
    if _cfg.power_chord_minor_key_only:
        if not (key_mode == 'minor' and key_strength >= 0.7):
            _pc_ratio = 0.0
            _pc_penalty = 0.0
            _pc_streak = 0
    emissions = _compute_emission_scores(
        chroma, boundaries,
        diatonic=diatonic, bias=DIATONIC_BIAS,
        no_chord_floor=COS_CUTOFF,
        bass_root_track=bass_track,
        bass_bias=BASS_ROOT_BIAS if bass_track is not None else 0.0,
        power_chord_third_ratio=_pc_ratio,
        power_chord_penalty=_pc_penalty,
        power_chord_third_min_streak=_pc_streak,
    )
    transitions = _build_transition_matrix(diatonic=diatonic, config=_cfg)
    states = _viterbi_decode(emissions, transitions)
    chords = _viterbi_states_to_chords(
        states, boundaries, times, emissions,
    )
    # Stage 1.4.2 — post-Viterbi power-chord substitution. Complements
    # the Stage 1.4.1 emission-side penalty: 1.4.1 demotes maj/min
    # cells during per-window argmax, but the dyad/triad mass
    # asymmetry means the triad templates still usually win after the
    # Viterbi argmax bakes in the transition bonuses. This pass
    # re-scores each emitted region's raw cosine against
    # region-averaged chroma and substitutes the quality to '5' when
    # the region is dyad-like. Same minor+strength>=0.7 key gate as
    # 1.4.1; bench corpus stays bit-exact because the default config
    # leaves both fields at 0.0.
    _pv_ratio = float(_cfg.power_chord_post_viterbi_third_ratio)
    _pv_margin = float(_cfg.power_chord_post_viterbi_margin)
    _pv_shape = float(getattr(_cfg, 'power_chord_shape_ratio_min', 0.0))
    if _cfg.power_chord_minor_key_only:
        if not (key_mode == 'minor' and key_strength >= 0.7):
            _pv_ratio = 0.0
            _pv_margin = 0.0
            _pv_shape = 0.0
    chords = _substitute_power_chords_on_dyads(
        chords, chroma, times,
        third_ratio_max=_pv_ratio,
        margin=_pv_margin,
        shape_ratio_min=_pv_shape,
    )
    # Merge adjacent identical-label regions defensively. The
    # state-to-chord step already collapses adjacent identical *states*,
    # but it cannot merge two different states that collapse to the
    # same display label after Phase 5+ adds a bass-bias layer that may
    # produce e.g. distinct A5 / A min states with the same visible
    # 'A'. Running through the merger keeps the output contract stable.
    chords = _merge_consecutive_chords(chords)
    # Stage 3.1 HCDF-snapped boundaries: REVERTED.
    #
    # Implemented as a post-merge boundary-time refinement step that
    # pulled adjacent chord boundaries to the nearest local maximum of
    # harmonic change (cosine distance between consecutive chroma
    # frames) inside a ±N-frame window. Corpus sweep (r ∈ {1,2,3,4})
    # regressed every fixture monotonically; corpus_wcsr dropped from
    # 0.7897 (r=0) to 0.7590 (r=4). pub_feed in particular fell from
    # 0.2257 to 0.2123 at r=2 (>1pt threshold violation). Reason: the
    # local HCDF peak inside ±2 chroma frames (~46 ms) does not, on
    # these fixtures, align with the ground-truth chord onset. Real
    # chord changes are smeared across a multi-frame attack envelope,
    # and the Viterbi grid edge already approximates the change point
    # better than a single argmax inside a tiny window. Pulling the
    # boundary ±N frames moves ~20-90 ms of timeline mass to whichever
    # label dominates the local chroma — usually the wrong direction.
    # See _compute_hcdf / _snap_chords_to_hcdf in the source history if
    # a future Stage 3.x revisit wants the helpers; both were removed
    # since no caller remained.
    if beats_s is not None:
        song_dur_s = float(times[-1]) if len(times) else 0.0
        chords = _snap_regions_to_beats(chords, beats_s, song_dur_s)
        chords = _merge_consecutive_chords(chords)
    logger.info(
        f"Viterbi: emitted {len(chords)} chord regions from "
        f"{emissions.shape[0]} windows over {emissions.shape[1]} states"
    )
    return chords


def detect_chords_from_midi(
    notes: List[Tuple[int, float, float, int]],
    min_chord_duration: float = 0.25,
    min_notes_for_chord: int = 2,
) -> List[Chord]:
    """
    Detect chords from MIDI notes.

    Args:
        notes: List of (pitch, start, end, velocity) tuples
        min_chord_duration: Minimum duration for a chord
        min_notes_for_chord: Minimum simultaneous notes to form a chord

    Returns:
        List of detected Chord objects
    """
    if not notes:
        return []

    # Sort by start time
    sorted_notes = sorted(notes, key=lambda x: x[1])

    # Find time segments where notes overlap
    # Create a list of all note on/off events
    events = []
    for pitch, start, end, vel in sorted_notes:
        events.append((start, 'on', pitch, vel))
        events.append((end, 'off', pitch, vel))
    events.sort(key=lambda x: (x[0], x[1] == 'on'))  # Sort by time, offs before ons

    # Track active notes and detect chords
    active_notes = {}  # pitch -> velocity
    chords = []
    segment_start = 0.0
    last_pitches = set()

    for time, event_type, pitch, vel in events:
        if event_type == 'on':
            active_notes[pitch] = vel
        else:
            if pitch in active_notes:
                del active_notes[pitch]

        current_pitches = set(active_notes.keys())

        # Check if chord changed
        if current_pitches != last_pitches:
            # Save previous chord if valid
            if len(last_pitches) >= min_notes_for_chord and (time - segment_start) >= min_chord_duration:
                pitch_classes = [p % 12 for p in last_pitches]
                root, quality, confidence = _identify_chord_from_pitches(list(last_pitches))

                if confidence > 0.3:
                    # Find bass note (lowest pitch)
                    bass = min(last_pitches) % 12 if last_pitches else None

                    chord = Chord(
                        root=root,
                        quality=quality,
                        start_time=segment_start,
                        end_time=time,
                        confidence=confidence,
                        bass_note=bass if bass != root else None,
                    )
                    chords.append(chord)

            segment_start = time
            last_pitches = current_pitches

    # Merge consecutive identical chords
    chords = _merge_consecutive_chords(chords)

    logger.info(f"Detected {len(chords)} chords from MIDI")
    return chords


def group_notes_into_chords(
    notes: List[Tuple[int, float, float, int]],
    chords: List[Chord],
) -> List[Tuple[int, float, float, int]]:
    """
    Group scattered notes into coherent chord voicings.

    Takes messy extracted notes and aligns them to detected chords,
    removing spurious notes and ensuring clean chord voicings.

    Args:
        notes: Original extracted notes (pitch, start, end, velocity)
        chords: Detected chord progression

    Returns:
        Cleaned up notes aligned to chords
    """
    if not notes or not chords:
        return notes

    cleaned_notes = []

    for chord in chords:
        # Find notes that fall within this chord's time range
        chord_notes = [
            n for n in notes
            if n[1] < chord.end_time and n[2] > chord.start_time
        ]

        if not chord_notes:
            continue

        # Get the chord's pitch classes
        chord_pcs = set(chord.notes)

        # Filter to notes that match the chord
        matching_notes = []
        for pitch, start, end, vel in chord_notes:
            pc = pitch % 12
            if pc in chord_pcs:
                # Clip note to chord boundaries
                new_start = max(start, chord.start_time)
                new_end = min(end, chord.end_time)
                if new_end > new_start:
                    matching_notes.append((pitch, new_start, new_end, vel))

        # If we have matching notes, use them
        # Otherwise, generate chord tones from the chord
        if matching_notes:
            cleaned_notes.extend(matching_notes)
        else:
            # Generate basic chord voicing
            avg_vel = int(np.mean([n[3] for n in chord_notes])) if chord_notes else 80
            base_octave = 4  # Middle octave

            for interval in CHORD_TEMPLATES.get(chord.quality, [0, 4, 7])[:3]:  # Limit to triad
                pitch = (chord.root + interval) + (base_octave * 12)
                cleaned_notes.append((pitch, chord.start_time, chord.end_time, avg_vel))

    # Sort by start time
    cleaned_notes.sort(key=lambda x: (x[1], x[0]))

    logger.info(f"Grouped {len(notes)} notes into {len(cleaned_notes)} chord-aligned notes")
    return cleaned_notes


# ---------------------------------------------------------------------------
# Phase 4: HMM / Viterbi sequence model
# ---------------------------------------------------------------------------
#
# The per-window cosine matcher (`_match_chord_template`) decides each
# window's chord label independently. Even with diatonic biasing, real
# audio's chroma is noisy enough that sibling templates (relative
# minors, IV/V neighbours, sus/triad variants) trade the argmax frame
# to frame. The Phase 1/3 baseline on Pub Feed (WCSR 0.1636) reflects
# that noise: the matcher knows the rough harmonic neighbourhood but
# scatters across ~6-8 plausible roots per second.
#
# Phase 4 replaces the per-window argmax with a single Viterbi decode
# over the entire song. State = (root, collapsed_quality). Emissions =
# biased cosine scores from the existing template machinery. Transitions
# = additive bonuses in the cosine scale: a small per-frame bonus for
# staying in the same chord (self-loop), a small bonus for moving to a
# diatonic neighbour, a penalty for off-diatonic moves and for entering
# the no-chord state. The result is a globally-coherent label sequence
# that natively replicates what the deleted `_smooth_chord_labels` and
# `_drop_short_regions` heuristics were trying to bandaid.
#
# All weights are calibrated in the cosine scale (emissions in [0, ~1.15]
# after diatonic bias) so the relative scale of bonuses against
# emissions is interpretable: SELF_LOOP_BONUS = 0.10 is exactly the
# typical between-sibling-template cosine gap, so a single-frame
# distractor cannot break out of a real chord but a sustained 0.15-
# cosine-lead chord change will.

# Collapsed-quality vocabulary used for the Viterbi state space. These
# are the labels `_collapse_quality` produces; the Viterbi alphabet
# must match the user-visible alphabet so the decoded sequence renders
# directly without further collapsing.
_VITERBI_QUALITIES = ('maj', 'min', '7', 'dim', 'aug', 'sus2', 'sus4', '5')


def _quality_collapse_map() -> Dict[str, str]:
    """Map raw CHORD_TEMPLATES quality names to collapsed Viterbi qualities.

    Mirrors `_collapse_quality` but as an explicit dict so the Viterbi
    emission step can route raw-template cosine scores to the right
    state-space cell in a single lookup. Drift between this map and
    `_collapse_quality` is a bug — both must agree on the display
    alphabet.
    """
    return {
        'maj': 'maj', 'maj7': 'maj', 'maj9': 'maj', 'add9': 'maj',
        'min': 'min', 'min7': 'min', 'min9': 'min',
        'dom7': '7',
        'dim': 'dim', 'dim7': 'dim',
        'aug': 'aug',
        'sus2': 'sus2', 'sus4': 'sus4',
        '5': '5',
    }


def _is_diatonic_state(
    root: int,
    quality: str,
    diatonic: Optional[set],
) -> bool:
    """Return True if (root, quality) sits inside the diatonic chord set.

    Power-chord states ('5') are voicing-agnostic — a root+5th dyad
    carries no information about whether the underlying chord is major
    or minor — so they pattern as "diatonic at root R" if EITHER
    (R, 'maj') OR (R, 'min') is in the diatonic set. Without this
    equivalence the Viterbi state space structurally double-penalises
    every power-chord state (no emission bias + off-diatonic transition
    penalty) and decoded paths collapse to triad states instead, which
    is exactly what's wrong with the Phase 1 detector output on
    overdriven-rock fixtures.
    """
    if diatonic is None:
        return False
    if quality == '5':
        return (root, 'maj') in diatonic or (root, 'min') in diatonic
    family = _quality_family(quality)
    return (root, family) in diatonic


def _build_beat_boundaries(
    beats_s: Optional[np.ndarray],
    n_chroma_frames: int,
    sr: int,
    hop_length: int,
) -> Optional[List[int]]:
    """Translate beat timestamps (seconds) into chroma-frame boundaries.

    Returns a sorted list of chroma-frame indices ``[b0, b1, b2, ...,
    bN]`` such that window ``t`` covers chroma frames
    ``[b[t], b[t+1])``. The list always starts at 0 and ends at
    ``n_chroma_frames`` so the boundary array covers the full song; the
    interior boundaries come from beat times.

    Phase 6 rationale: ``librosa.beat.beat_track`` returns timestamps
    that *correspond to musical beats*. Aggregating chroma per beat and
    decoding the Viterbi state sequence over beat-indexed observations
    means a chord change can only occur on a beat boundary, which is
    where chord changes actually happen in real music. The previous
    fixed-0.5s grid forced boundaries onto the wrong subdivisions on
    songs whose beat doesn't divide 0.5s cleanly (e.g. 97 BPM Pub Feed
    has a beat of 0.619s, ~24% out of phase with a 0.5s grid).

    Returns ``None`` when:
      * ``beats_s`` is None (the caller didn't supply beat data), or
      * fewer than 2 beats survived (too-short song, or beat-track
        failure), or
      * after frame conversion fewer than 2 unique boundary frames
        survive (degenerate timing — e.g. all beats fell in the same
        chroma frame, which would happen only on sub-millisecond
        beats).

    In all None-return cases, the caller's fixed-window fallback path
    runs unchanged.

    Args:
        beats_s: Beat timestamps in seconds, monotonically increasing.
        n_chroma_frames: Total number of frames in the chroma matrix.
        sr: Sample rate the chroma was computed at.
        hop_length: Hop length the chroma was computed at.

    Returns:
        Sorted list of int frame indices, or None to signal fallback.
    """
    if beats_s is None:
        return None
    beats_arr = np.asarray(beats_s, dtype=np.float64)
    if beats_arr.ndim != 1 or beats_arr.size < 2:
        return None

    # Convert beat seconds to chroma frame indices.
    # frame_idx = round(beat_s * sr / hop_length); clamp to chroma range.
    frame_idx = np.round(beats_arr * sr / hop_length).astype(np.int64)
    frame_idx = np.clip(frame_idx, 0, n_chroma_frames)
    # Drop duplicates (multiple beats falling in the same chroma frame —
    # only happens at extreme tempo + coarse hop, but be defensive).
    frame_idx = np.unique(frame_idx)

    # Ensure 0 and n_chroma_frames are present so the boundary list
    # covers the full song without dropping leading silence or
    # trailing audio.
    if frame_idx[0] != 0:
        frame_idx = np.concatenate(([0], frame_idx))
    if frame_idx[-1] != n_chroma_frames:
        frame_idx = np.concatenate((frame_idx, [n_chroma_frames]))

    if frame_idx.size < 2:
        return None
    return frame_idx.tolist()


def _snap_regions_to_beats(
    chords: List[Chord],
    beats_s: np.ndarray,
    song_dur_s: float,
) -> List[Chord]:
    """Snap each chord region's start/end time to the nearest beat.

    Phase 6 (hybrid-grid revision). Inputs are the Viterbi-emitted +
    consecutive-merged chord regions and the beat track from
    ``librosa.beat.beat_track``. Output is the same region sequence
    with each boundary moved to the nearest beat (or song start /
    song end, whichever is closer to the original boundary).

    The chroma aggregation upstream already happened on the fixed
    0.5s grid, so the chord *labels* are exactly what the Phase 4/5
    detector would have emitted. This pass only tightens the
    visible boundary timestamps onto musical-beat gridpoints,
    matching the rhythm at which real chord changes actually occur.

    Invariants preserved:
      * Region order is preserved.
      * No region is dropped (even if its snapped duration collapses
        to zero; the next call to ``_merge_consecutive_chords``
        cleans those up if they collapsed into a neighbour).
      * First region starts at the song's first sample (or the
        earliest snap target ≤ original start_time).
      * Last region ends at song_dur_s (or the latest snap target ≥
        original end_time).
      * Consecutive regions are contiguous (region[i].end_time ==
        region[i+1].start_time) — after snapping, each region's
        start is forced equal to the previous region's snapped end
        to prevent the snap rounding from producing gaps or
        overlaps.

    No-op cases (returns ``chords`` unchanged):
      * ``beats_s`` is None.
      * Fewer than 2 beats.
      * ``chords`` is empty or singleton.
    """
    if beats_s is None:
        return chords
    beats_arr = np.asarray(beats_s, dtype=np.float64)
    if beats_arr.ndim != 1 or beats_arr.size < 2:
        return chords
    if len(chords) < 2:
        return chords

    # Snap targets include the song's start and end so boundaries
    # near silence at edges have somewhere to land.
    snap_targets = np.unique(np.concatenate((
        [0.0], beats_arr, [float(song_dur_s)],
    )))

    def _snap(t: float) -> float:
        # Argmin over absolute time difference. Ties resolve toward
        # the earlier target (np.argmin's default behaviour) which
        # is the convention musicians use for boundaries.
        return float(snap_targets[int(np.argmin(np.abs(snap_targets - t)))])

    snapped: List[Chord] = []
    for c in chords:
        snapped.append(Chord(
            root=c.root,
            quality=c.quality,
            start_time=_snap(c.start_time),
            end_time=_snap(c.end_time),
            confidence=c.confidence,
            bass_note=c.bass_note,
        ))

    # Force contiguity: a region's start must equal the previous
    # region's end. Snap rounding can introduce ±half-beat slop
    # otherwise, leaving visible gaps in the ribbon.
    snapped[0].start_time = chords[0].start_time
    snapped[-1].end_time = chords[-1].end_time
    for i in range(1, len(snapped)):
        snapped[i].start_time = snapped[i - 1].end_time

    return snapped


def _bass_root_track(
    bass_y: np.ndarray,
    sr: int,
    boundaries: List[int],
    hop_length: int,
    min_voiced_ratio: float = 0.4,
) -> np.ndarray:
    """Aggregate bass-stem f0 into a per-window dominant pitch class.

    Runs pyin over the bass stem to extract a fundamental-frequency
    track, then for each Viterbi window (one cell in `boundaries`)
    picks the most common pitch class across the voiced frames in that
    window.

    Phase 5 rationale: cosine matching on chroma cannot separate
    relative-major/minor pairs (A vs F#m, C vs Am, D vs Bm) — they
    share two of three pitch classes, and which one wins per window is
    chroma-noise. The Songsterr tab for Pub Feed has A5 dominating the
    intro/verse, but the Phase 4 detector calls those windows F#m or
    C#m roughly 50% of the time because the chroma is ambiguous. The
    bass *root note*, on the other hand, is unambiguous: it's A under
    A5 and F# under F#m. Routing the bass stem through pyin and biasing
    the emission score in favour of templates whose root matches the
    detected bass pitch class is the principled fix.

    Args:
        bass_y: Bass-stem audio at sample rate ``sr``.
        sr: Sample rate of ``bass_y`` (must match the chroma audio sr
            so the resulting f0 frames line up with chroma frames).
        boundaries: Window-boundary frame indices, as built by
            ``detect_chords_from_audio``.
        hop_length: Hop length used for chroma analysis. pyin uses the
            same hop so f0 frames align 1:1 with chroma frames.
        min_voiced_ratio: A window must have at least this fraction of
            voiced f0 frames before we assign it a bass root; otherwise
            the window's track entry is -1 (unvoiced — usually a rest,
            a drum-only fill, or a transient between notes). Defaults
            to 0.4: bass notes typically sustain across most of a beat
            but pyin's voicing detector occasionally drops frames at
            attack/decay edges.

    Returns:
        ``np.ndarray`` of dtype int64 with shape ``(len(boundaries) -
        1,)``. Each entry is a pitch class 0-11 (C=0, C#=1, ..., B=11)
        or -1 when the window is unvoiced.
    """
    import librosa

    # pyin against a tight bass-instrument frequency band (E1 = 41.2 Hz
    # to roughly B3 = 247 Hz). Tighter than librosa's defaults so
    # octave-error misreads in the mid-range guitar register can't
    # contaminate the bass-root signal.
    f0, voiced_flag, _voiced_prob = librosa.pyin(
        bass_y,
        fmin=40.0,
        fmax=250.0,
        sr=sr,
        hop_length=hop_length,
    )
    # MIDI = 69 + 12 * log2(f0 / 440). Nan-preserving so unvoiced
    # frames propagate as NaN through the pitch-class calculation and
    # are skipped during the per-window mode count.
    with np.errstate(invalid='ignore', divide='ignore'):
        midi = 69.0 + 12.0 * np.log2(f0 / 440.0)

    T = len(boundaries) - 1
    track = np.full(T, -1, dtype=np.int64)
    n_frames = midi.shape[0]
    for t in range(T):
        s = min(int(boundaries[t]), n_frames)
        e = min(int(boundaries[t + 1]), n_frames)
        if e <= s:
            continue
        win = midi[s:e]
        voiced = win[~np.isnan(win)]
        # Need a minimum number of voiced frames to trust the window's
        # dominant pitch class. A bass-rest or transient-only window
        # stays -1 and the emission bias is skipped for that window.
        if len(voiced) < max(1, int(min_voiced_ratio * (e - s))):
            continue
        pcs = (np.round(voiced).astype(np.int64)) % 12
        counts = np.bincount(pcs, minlength=12)
        track[t] = int(np.argmax(counts))
    return track


def _compute_emission_scores(
    chroma: np.ndarray,
    boundaries: List[int],
    diatonic: Optional[set] = None,
    bias: float = 0.0,
    no_chord_floor: float = 0.70,
    bass_root_track: Optional[np.ndarray] = None,
    bass_bias: float = 0.0,
    power_chord_third_ratio: float = 0.0,
    power_chord_penalty: float = 0.0,
    power_chord_third_min_streak: int = 0,
) -> np.ndarray:
    """Compute the (T, S) emission score matrix for Viterbi decoding.

    T = len(boundaries) - 1; S = 12 * |_VITERBI_QUALITIES| + 1 (the
    last column is the no-chord absorbing state).

    For each window, the score at (root, collapsed_quality) is the
    maximum biased cosine over raw CHORD_TEMPLATES entries that
    collapse to that quality. Biased = raw cosine * (1 + bias) when
    (root, family) is diatonic, else raw cosine — same formula as
    `_match_chord_template` uses for argmax tie-breaking, so the
    Viterbi emission and the per-window matcher cannot disagree on
    template ranking; they only disagree on how to combine ranks
    across time.

    The no-chord state emits a flat `no_chord_floor`, equal to the
    legacy COS_CUTOFF (0.70). When every real-chord candidate scores
    below this floor (silence, white-noise window, between-chord
    transient), no-chord wins on emission; the heuristic stack's
    confidence gate is therefore preserved structurally inside the
    state space.

    Stage 1.4 — power-chord prior (third absence). When the per-
    window chroma shows strong root+5th mass on candidate root R but
    both the major-third bin ((R+4)%12) and the minor-third bin
    ((R+3)%12) sit below ``power_chord_third_ratio`` of the root+5th
    average, the maj/min triad templates still score high (cosine
    rewards aligned mass on the shared root+5th notes) and on noisy
    overdriven guitar can edge out the `5` template by a few hundredths
    of a cosine. The power-chord prior subtracts
    ``power_chord_penalty`` from the maj and min collapsed-state
    emissions for that root, letting `5` win the slot. Zero by
    default; gated on ``power_chord_penalty > 0`` and
    ``power_chord_third_ratio > 0``.

    Stage 1.4.1 — persistence streak gate. The original Stage-1.4
    failed corpus regression because the per-window third-absence
    test fires on real triads during attack envelopes, demoting
    them. ``power_chord_third_min_streak`` >= 1 requires the
    third-absent flag to be True for N consecutive windows ending at
    t before the penalty applies — a power-chord voicing has the
    third absent for its full duration, but a triad's third only
    drops out for one or two windows during transients. Per-root
    streak counters reset to 0 whenever a window's third-absent flag
    is False, so the gate is sharp on the leading edge of a real
    power-chord region (no penalty on the first (N-1) frames) and
    resets cleanly on the trailing edge.
    """
    n_q = len(_VITERBI_QUALITIES)
    n_chord_states = 12 * n_q
    n_states = n_chord_states + 1
    no_chord_idx = n_chord_states  # last index
    T = len(boundaries) - 1
    emissions = np.full((T, n_states), -np.inf, dtype=np.float64)

    collapse = _quality_collapse_map()
    # Precompute L2-normalised template vectors once, indexed by
    # (root, raw_quality). Inner-loop dot-product against the chroma
    # window then collapses to O(12 * n_raw_templates) per window.
    raw_templates = []
    for root in range(12):
        for q_raw, intervals in CHORD_TEMPLATES.items():
            tmpl = np.zeros(12)
            for interval in intervals:
                tmpl[(root + interval) % 12] = 1.0
            tmpl /= (np.linalg.norm(tmpl) + 1e-9)
            raw_templates.append((root, q_raw, tmpl))

    pc_enabled = (
        power_chord_penalty > 0.0 and power_chord_third_ratio > 0.0
    )
    pc_min_streak = max(1, int(power_chord_third_min_streak))
    # Per-root running streak of consecutive third-absent windows.
    # Resets to zero whenever the third-absent flag drops to False.
    # The penalty is gated on streak >= pc_min_streak so a single
    # transient frame can't demote a real triad (the failure mode
    # that caused the original Stage 1.4 to be reverted).
    pc_streak = np.zeros(12, dtype=np.int64)

    for t in range(T):
        s_frame, e_frame = boundaries[t], boundaries[t + 1]
        segment = np.mean(chroma[:, s_frame:e_frame], axis=1)
        chroma_norm = segment / (np.linalg.norm(segment) + 1e-9)

        # Phase 5 bass-root bias: pulled out of the inner template loop
        # so we lookup once per window. A value of -1 means the bass
        # stem was unvoiced at this window (rest, transient, etc.) and
        # no template gets a bass-root multiplier — emission falls back
        # to pure diatonic-bias-aided cosine.
        bass_root = -1
        if bass_root_track is not None and bass_bias > 0:
            br = int(bass_root_track[t])
            if br >= 0:
                bass_root = br

        # Stage 1.4: per-window, per-root third-absence detection.
        # ``third_absent[r]`` is True when root r shows strong root+5th
        # mass but both third bins are below the threshold ratio — the
        # spectral signature of an overdriven-guitar power chord. The
        # maj/min emissions at that root get the power-chord penalty.
        third_absent = np.zeros(12, dtype=bool)
        if pc_enabled:
            for r in range(12):
                root_mass = float(chroma_norm[r])
                fifth_mass = float(chroma_norm[(r + 7) % 12])
                root_fifth_avg = 0.5 * (root_mass + fifth_mass)
                if root_fifth_avg <= 0.0:
                    continue
                thirds = max(
                    float(chroma_norm[(r + 4) % 12]),
                    float(chroma_norm[(r + 3) % 12]),
                )
                if thirds < power_chord_third_ratio * root_fifth_avg:
                    third_absent[r] = True

        # Stage 1.4.1: update per-root persistence streak. After
        # this update, ``pc_streak[r]`` counts consecutive
        # third-absent windows ending at t (inclusive). The penalty
        # at this window only fires when pc_streak[r] >= pc_min_streak,
        # ruling out single-frame transients on real triads.
        if pc_enabled:
            for r in range(12):
                if third_absent[r]:
                    pc_streak[r] += 1
                else:
                    pc_streak[r] = 0

        for root, q_raw, tmpl in raw_templates:
            raw = float(np.dot(chroma_norm, tmpl))
            biased = raw
            if bias > 0 and _is_diatonic_state(root, q_raw, diatonic):
                biased = raw * (1.0 + bias)
            # Multiplicative bass-root bias. Stacks on top of the
            # diatonic bias when both apply (root is diatonic AND
            # matches the bass), which is the correct compound bonus
            # for "diatonic chord rooted on the heard bass note" —
            # nearly always the correct decode.
            if bass_root >= 0 and root == bass_root:
                biased = biased * (1.0 + bass_bias)
            q_coll = collapse[q_raw]
            # Stage 1.4 third-absence penalty applied to maj/min
            # collapsed cells only. Subtractive (cosine-units) so it
            # composes additively with the Viterbi transition scores.
            # Stage 1.4.1 gates the penalty on the persistence streak
            # so single-frame transients on real triads don't trip it.
            if (
                pc_enabled
                and pc_streak[root] >= pc_min_streak
                and (q_coll == 'maj' or q_coll == 'min')
            ):
                biased = biased - power_chord_penalty
            q_idx = _VITERBI_QUALITIES.index(q_coll)
            state_idx = root * n_q + q_idx
            if biased > emissions[t, state_idx]:
                emissions[t, state_idx] = biased

        emissions[t, no_chord_idx] = no_chord_floor

    # Any (root, collapsed_quality) cell that didn't receive a raw
    # template (none exists for that quality) defaults to the no-chord
    # floor so the cell can't "win" by being -inf elsewhere.
    np.maximum(emissions, no_chord_floor - 1.0, out=emissions)
    return emissions


def _build_transition_matrix(
    diatonic: Optional[set],
    config: Optional[DetectorConfig] = None,
) -> np.ndarray:
    """Build the (S, S) additive transition bonus matrix.

    All entries are in the same scale as emissions (cosine units),
    so Viterbi compares emission deltas and transition bonuses on a
    common axis. Tunable constants (calibrated against the typical
    0.01-0.05 cosine gap between sibling templates):

      SELF_LOOP_BONUS = 0.10
        Per-frame bonus for staying in the same state. Strong enough
        that a single-frame distractor cannot break out of a real
        chord, weak enough that a sustained 2-3-frame run of a real
        chord change overwhelms it.

      SAME_ROOT_QUALITY_BONUS = 0.05
        Bonus for moving between two chord states sharing a root
        (A maj <-> A5, A maj <-> A7). Models the common idiom of a
        single root with shifting voicing across a bar.

      DIATONIC_TRANSITION_BONUS = 0.03
        Bonus for moving to a chord state whose (root, family) is
        diatonic in the detected key. Small because the diatonic
        bias is also already applied at the emission step; this
        compounds it to push the decoded path toward the diatonic
        progression vocabulary.

      NON_DIATONIC_PENALTY = -0.05
        Penalty for moving to a non-diatonic chord state. Off-key
        chords are rare in pop/rock vocabulary; this discourages
        the decoder from inventing chromatic chords from noise.

      NO_CHORD_PENALTY = -0.10
        Penalty for entering the no-chord state from a chord state.
        The no-chord state's emission floor (0.70) is already a high
        bar; this penalty further suppresses transient drops into
        no-chord during noisy frames. The no-chord -> chord direction
        carries no transition penalty (we don't want to fight the
        natural onset of a real chord after silence).
    """
    # Empirical retuning after Pub Feed measurement. Initial values
    # (SELF_LOOP=0.10, DIATONIC_BONUS=0.03, NON_DIATONIC_PENALTY=-0.05)
    # caused Viterbi to commit to whichever diatonic state happened to
    # win the cumulative early-frame emission contest (C#min on Pub
    # Feed) and stay there for ~30s stretches, because per-frame
    # emission deltas between sibling diatonic states on noisy chroma
    # are ~0.02-0.04 — below SELF_LOOP. Lowered SELF_LOOP and removed
    # the redundant DIATONIC_TRANSITION_BONUS (already applied at the
    # emission step) and the NON_DIATONIC_PENALTY (let emission decide).
    #
    # Sweep on Pub Feed triad-relaxed WCSR:
    #   SELF_LOOP=0.10 -> 0.1078
    #   SELF_LOOP=0.04 -> 0.1800
    #   SELF_LOOP=0.02 -> 0.1867
    #   SELF_LOOP=0.01 -> 0.1933   <- chosen
    #   SELF_LOOP=0.005-> 0.1933
    #   SELF_LOOP=0.00 -> 0.1900
    # 0.01 is the smallest value at the plateau, preferred over 0.005
    # for marginal robustness to longer-held chords on songs with less
    # noisy chroma than Pub Feed. Remaining loss is genuine chroma
    # ambiguity (A5 vs C#m / F#m relative-minor pair) that Phase 5
    # bass-routing is intended to disambiguate.
    # Resolve numeric levers from the optional DetectorConfig. When
    # ``config`` is None (the legacy call signature), we substitute
    # the default-constructed instance, whose field values reproduce
    # the prior inline constants bit-for-bit. The local-variable
    # names below (SELF_LOOP_BONUS, SAME_ROOT_QUALITY_BONUS,
    # NO_CHORD_PENALTY) are retained verbatim so the transition-matrix
    # construction below does not need to change.
    _cfg = config if config is not None else DetectorConfig()
    SELF_LOOP_BONUS = _cfg.self_loop_bonus
    SAME_ROOT_QUALITY_BONUS = _cfg.same_root_quality_bonus
    DIATONIC_TRANSITION_BONUS = 0.0  # subsumed by emission bias
    NON_DIATONIC_PENALTY = 0.0       # let emission decide; no transition penalty
    NO_CHORD_PENALTY = _cfg.no_chord_penalty
    # Stage 2.1 (REVERTED). A QUALITY_SWITCH_PENALTY (subtractive bonus
    # on same-root quality changes) was implemented and swept across
    # penalty ∈ {0.005, 0.010, 0.015, 0.020, 0.030}. Every non-zero
    # value regressed at least one fixture; corpus_wcsr decreased
    # monotonically from 0.7897 (p=0) to 0.7796 (p=0.030). The
    # existing balance between SELF_LOOP_BONUS and SAME_ROOT_QUALITY_
    # BONUS (both 0.01, i.e. zero hysteresis) appears to be at a
    # local optimum for this corpus. Tightening hysteresis blocks
    # legitimate quality transitions (Cmaj → C7 in real progressions)
    # without measurable benefit to flicker reduction. Reverted to
    # legacy values.

    n_q = len(_VITERBI_QUALITIES)
    n_chord_states = 12 * n_q
    n_states = n_chord_states + 1
    no_chord_idx = n_chord_states

    transitions = np.zeros((n_states, n_states), dtype=np.float64)

    def _decode(idx):
        if idx == no_chord_idx:
            return None, None
        return idx // n_q, _VITERBI_QUALITIES[idx % n_q]

    for s_prev in range(n_states):
        r_prev, q_prev = _decode(s_prev)
        for s_curr in range(n_states):
            r_curr, q_curr = _decode(s_curr)
            if s_prev == s_curr:
                transitions[s_prev, s_curr] = SELF_LOOP_BONUS
                continue
            if s_curr == no_chord_idx:
                transitions[s_prev, s_curr] = NO_CHORD_PENALTY
                continue
            if s_prev == no_chord_idx:
                # No-chord -> any chord: neutral. Onsets are fine.
                transitions[s_prev, s_curr] = 0.0
                continue
            if r_prev == r_curr:
                transitions[s_prev, s_curr] = SAME_ROOT_QUALITY_BONUS
                continue
            if _is_diatonic_state(r_curr, q_curr, diatonic):
                transitions[s_prev, s_curr] = DIATONIC_TRANSITION_BONUS
            else:
                transitions[s_prev, s_curr] = NON_DIATONIC_PENALTY

    return transitions


def _viterbi_decode(
    emissions: np.ndarray,
    transitions: np.ndarray,
) -> np.ndarray:
    """Pure-numpy Viterbi MAP decoder.

    emissions: (T, S) per-frame emission scores.
    transitions: (S, S) additive transition bonuses.

    Score recurrence:
        score[t, s] = max_{s'} (score[t-1, s'] + transitions[s', s])
                     + emissions[t, s]

    Returns int array of length T containing the most-likely state
    sequence. Backtracking uses a (T, S) int32 backpointer table.

    Complexity: O(T * S^2). With S = 97 and T ~ 280 (Pub Feed at 0.5s
    windows on 145s) this is ~2.6M float ops; negligible vs the
    upstream chroma extraction.
    """
    T, S = emissions.shape
    assert transitions.shape == (S, S), "transitions/emissions shape mismatch"

    score = emissions[0].astype(np.float64).copy()
    backpointers = np.zeros((T, S), dtype=np.int32)

    for t in range(1, T):
        # candidates[s_prev, s_curr] = score[t-1, s_prev] + transitions[s_prev, s_curr]
        candidates = score[:, None] + transitions
        best_prev = np.argmax(candidates, axis=0)
        # Take best score per s_curr, then add this frame's emission.
        score = candidates[best_prev, np.arange(S)] + emissions[t]
        backpointers[t] = best_prev

    states = np.zeros(T, dtype=np.int32)
    states[T - 1] = int(np.argmax(score))
    for t in range(T - 1, 0, -1):
        states[t - 1] = backpointers[t, states[t]]
    return states


def _viterbi_states_to_chords(
    states: np.ndarray,
    boundaries: List[int],
    times: np.ndarray,
    emissions: np.ndarray,
) -> List["Chord"]:
    """Convert a Viterbi state sequence to a list of Chord regions.

    Adjacent identical states collapse into one Chord; the no-chord
    state drops out entirely (emits no region). Confidence is the
    mean emission score over the constituent frames — directly
    comparable to the legacy per-window cosine because emissions in
    this implementation *are* biased cosines.
    """
    n_q = len(_VITERBI_QUALITIES)
    no_chord_idx = 12 * n_q
    chords: List[Chord] = []

    if len(states) == 0:
        return chords

    run_start = 0
    for t in range(1, len(states) + 1):
        if t == len(states) or states[t] != states[run_start]:
            s = int(states[run_start])
            if s != no_chord_idx:
                root = s // n_q
                quality = _VITERBI_QUALITIES[s % n_q]
                start_frame = boundaries[run_start]
                end_frame = boundaries[t]
                t_start = float(times[start_frame])
                t_end = float(times[min(end_frame, len(times) - 1)])
                # Clip to [0, 1] — biased emissions can exceed 1.0
                # when a near-perfect cosine match is on a diatonic
                # state (raw * (1.0 + DIATONIC_BIAS) > 1.0), but the
                # downstream Chord contract requires confidence in
                # [0, 1].
                conf = float(np.clip(np.mean(emissions[run_start:t, s]), 0.0, 1.0))
                chords.append(Chord(
                    root=root,
                    quality=quality,
                    start_time=t_start,
                    end_time=t_end,
                    confidence=conf,
                ))
            run_start = t
    return chords


def _substitute_power_chords_on_dyads(
    chords: List["Chord"],
    chroma: np.ndarray,
    times: np.ndarray,
    *,
    third_ratio_max: float,
    margin: float,
    shape_ratio_min: float = 0.0,
) -> List["Chord"]:
    """Stage 1.4.2 — post-Viterbi power-chord substitution on dyads.

    Walks each emitted maj/min region, aggregates the chroma over the
    region's frame range, and substitutes the region's quality to
    ``'5'`` when the region looks dyad-like. Two independent geometric
    gates can be enabled (either one, both, or neither):

      1. Raw third-bin ratio (Stage 1.4.2, legacy): third_bin /
         root_bin < ``third_ratio_max``. A magnitude test on a single
         chroma bin. Kept for backward compat.

      2. Spectral-shape ratio (Round-2 Fix 1):
         ``(root_bin + fifth_bin) / (third_bin + seventh_bin + eps)
         >= shape_ratio_min``. A geometric property of the four
         diatonic bins that is invariant under harmonic-distortion
         overtone inflation because both numerator and denominator
         inflate together under a diatonic tone stack. Fires on real
         distorted power chords that the raw third-ratio misses.

    A region must pass *every configured* gate (gates with a threshold
    ``<= 0`` are treated as disabled). In addition, the raw-cosine
    margin criterion must always hold:

      3. The ``'5'`` template's raw cosine against the region-averaged
         chroma is within ``margin`` of the winning maj/min raw cosine
         (computed against the same region-averaged chroma — NOT the
         per-window emission score, which was already biased by
         Stage-1.4.1 / DIATONIC_BIAS).

    When all active conditions hold, the region is rewritten with
    ``quality='5'`` and ``confidence`` updated to the power-template
    raw cosine. start_time/end_time/root unchanged.

    Caller is responsible for the key gate (Stage 1.4.1's
    minor+strength>=0.7 condition). This function performs no
    key-context inspection so it can be unit-tested against synthetic
    chroma without a full pipeline.

    No-op if ``margin <= 0`` OR (``third_ratio_max <= 0`` AND
    ``shape_ratio_min <= 0``) OR ``len(chords) == 0`` OR ``chroma`` is
    empty. These short-circuits keep production callers (default
    DetectorConfig → zeros) bit-exact identical to pre-Stage 1.4.2.

    Args:
        chords: Output of ``_viterbi_states_to_chords``, modified
            functionally — the returned list is a fresh list of fresh
            Chord records; the input is not mutated.
        chroma: Per-frame chroma matrix shape (12, n_frames). Same
            array fed to ``_compute_emission_scores``.
        times: Per-frame timestamps in seconds.
        third_ratio_max: Third-bin ratio threshold. The third bin must
            be at most this fraction of the root bin for the
            substitution to fire. Set to 0.0 to disable this gate.
        margin: Raw-cosine gap: ``power_raw >= maj_raw - margin``.
        shape_ratio_min: Spectral-shape ratio floor. Region's
            ``(root+5th) / (3rd+7th + eps)`` must be at least this
            value for the substitution to fire. Set to 0.0 to disable
            this gate.
    """
    if margin <= 0.0:
        return chords
    if third_ratio_max <= 0.0 and shape_ratio_min <= 0.0:
        return chords
    if not chords or chroma.size == 0 or times.size == 0:
        return chords

    n_frames = chroma.shape[1]
    if n_frames == 0:
        return chords

    out: List[Chord] = []
    for c in chords:
        if c.quality not in ('maj', 'min'):
            out.append(c)
            continue

        # Map region timestamps back to chroma frame indices via
        # np.searchsorted on the per-frame ``times`` array. Same
        # mapping convention as ``_viterbi_states_to_chords`` which
        # indexes ``times[boundaries[run_start]]``.
        start_frame = int(np.searchsorted(times, c.start_time, side='left'))
        end_frame = int(np.searchsorted(times, c.end_time, side='right'))
        start_frame = max(0, min(start_frame, n_frames - 1))
        end_frame = max(start_frame + 1, min(end_frame, n_frames))
        region_chroma = np.mean(chroma[:, start_frame:end_frame], axis=1)

        root = c.root
        third_interval = 4 if c.quality == 'maj' else 3
        # Minor-7th interval is 10 semitones from root; major-7th
        # would be 11. We use the minor-7th convention because power
        # chord idiom sits inside minor-key rock; the shape ratio
        # is nearly identical for the alternate choice on realistic
        # chroma (a real major-7th triad has energy in both bins).
        seventh_interval = 10
        fifth_interval = 7
        root_bin = float(region_chroma[root])
        third_bin = float(region_chroma[(root + third_interval) % 12])
        fifth_bin = float(region_chroma[(root + fifth_interval) % 12])
        seventh_bin = float(region_chroma[(root + seventh_interval) % 12])
        if root_bin <= 1e-9:
            out.append(c)
            continue

        # Gate 1: raw third-bin ratio (legacy).
        if third_ratio_max > 0.0 and third_bin >= third_ratio_max * root_bin:
            # Third is present at expected mass — region really is a
            # triad. Skip substitution.
            out.append(c)
            continue

        # Gate 2: spectral-shape ratio (Round-2 Fix 1). Genre-neutral
        # geometric signature of a power chord: root+5th dominate,
        # 3rd/7th are only intermodulation residue.
        if shape_ratio_min > 0.0:
            harmonic_mass = root_bin + fifth_bin
            melodic_mass = third_bin + seventh_bin
            shape_ratio = harmonic_mass / (melodic_mass + 1e-9)
            if shape_ratio < shape_ratio_min:
                out.append(c)
                continue

        # Recompute raw cosines on the region-averaged chroma for the
        # winning triad and the power-5 template. Cosine math mirrors
        # ``_match_chord_template`` exactly so behaviour aligns.
        chroma_norm = region_chroma / (np.linalg.norm(region_chroma) + 1e-9)

        triad_intervals = (0, 4, 7) if c.quality == 'maj' else (0, 3, 7)
        triad_template = np.zeros(12)
        for iv in triad_intervals:
            triad_template[(root + iv) % 12] = 1.0
        triad_template /= (np.linalg.norm(triad_template) + 1e-9)
        triad_raw = float(np.dot(chroma_norm, triad_template))

        power_template = np.zeros(12)
        power_template[root] = 1.0
        power_template[(root + 7) % 12] = 1.0
        power_template /= (np.linalg.norm(power_template) + 1e-9)
        power_raw = float(np.dot(chroma_norm, power_template))

        if power_raw >= triad_raw - margin:
            out.append(Chord(
                root=c.root,
                quality='5',
                start_time=c.start_time,
                end_time=c.end_time,
                confidence=float(np.clip(power_raw, 0.0, 1.0)),
            ))
        else:
            out.append(c)
    return out


# ---------------------------------------------------------------------------
# End Phase 4 Viterbi block
# ---------------------------------------------------------------------------


def _match_chord_template(
    chroma: np.ndarray,
    diatonic: Optional[set] = None,
    bias: float = 0.0,
) -> Tuple[int, str, float]:
    """
    Match a chroma vector to chord templates via cosine similarity.

    Returns (root, quality, confidence) where confidence is the raw
    (unbiased) cosine similarity in [0, 1] between the L2-normalized
    chroma vector and the L2-normalized binary chord template.

    If `diatonic` (a set of (root, family) pairs from
    `_diatonic_chord_set`) and `bias` > 0 are provided, candidates
    whose (root, family) sit inside `diatonic` get a multiplicative
    bonus of (1 + bias) applied to their score for argmax tie-breaking
    only. The returned confidence is always the *raw* cosine — the
    bias is internal to the comparison, never inflated into the value
    that COS_CUTOFF downstream gates on. This separation is important:
    a window with a weak chord match should still be rejected by the
    cutoff even if its weak winner happens to be diatonic.

    Why cosine and not L1 + dot-product:
    The previous implementation L1-normalized both vectors and took
    their dot product. For a binary triad template that scoring scheme
    is bounded above by 1/(triad-size) ≈ 0.333 — only reached when the
    chroma energy is concentrated *entirely* on the chord notes. On
    real polyphonic audio (overtones, transients, melody, bleed) the
    energy is spread, and dot-product scores collapse into a narrow
    0.10–0.18 band where every cutoff choice is either too tight (Jam
    ribbon stays empty) or too loose (every window passes and the
    detector emits one giant smeared region).

    Cosine similarity uses L2 normalization, so the *direction* of the
    chroma vector is compared to the template direction; magnitude
    differences cancel. Empirically on the Pub Feed full mix the score
    range moves from 0.10–0.28 (dot-product) to 0.69–0.81 (cosine),
    with a clear separation between chord segments and noise floor
    (silence/white-noise ~0.66, single sine ~0.58, clean triad ~0.99).
    Adaptive cutoffs become trivial in cosine space — see
    detect_chords_from_audio for the per-song gating formula.
    """
    best_root, best_quality = 0, 'maj'
    best_raw = 0.0
    best_biased = -np.inf

    # L2-normalize chroma for cosine similarity.
    chroma_norm = chroma / (np.linalg.norm(chroma) + 1e-9)

    for root in range(12):
        for quality, intervals in CHORD_TEMPLATES.items():
            # Create template chroma
            template = np.zeros(12)
            for interval in intervals:
                template[(root + interval) % 12] = 1.0
            template /= (np.linalg.norm(template) + 1e-9)

            # Cosine similarity (both vectors L2-normalized).
            similarity = float(np.dot(chroma_norm, template))

            # Diatonic bias for tie-breaking. The biased score is used
            # for argmax only; the raw cosine is recorded for return.
            biased = similarity
            if diatonic is not None and bias > 0:
                family = _quality_family(quality)
                if (root, family) in diatonic:
                    biased = similarity * (1.0 + bias)

            if biased > best_biased:
                best_biased = biased
                best_raw = similarity
                best_root = root
                best_quality = quality

    # Phase 3 power-chord disambiguation tie-break.
    #
    # When the winning template is a major or minor triad whose root's
    # 3rd bin is structurally absent (the 3rd of the major triad — or
    # the b3 for the minor — carries < THIRD_BIN_RATIO of the root
    # bin's mass), and the "5" template at the same root is within
    # POWER_CHORD_GAP of the winning raw cosine, switch to "5".
    # This is the "no 3rd present" test that distinguishes overdriven-
    # guitar power chords (A5 = {A, E}) from voiced triads (A =
    # {A, C#, E}).
    #
    # Rationale: a clean A5 chroma {A: high, E: high, C#: ~0} cosine-
    # matches the {root, 5th} "5" template at 1.0 but scores 0.816
    # against the {root, 3rd, 5th} triad template. So under noise-
    # free conditions the triad template never wins on a power chord
    # in the first place. On real noisy chroma the gap narrows — the
    # triad template can edge ahead by chroma leak into the 3rd bin
    # from overtones or bleed. The 3rd-bin gating recovers the
    # correct answer in that regime without disturbing clean-triad
    # cases (where the 3rd is genuinely present and the test fails).
    #
    # THIRD_BIN_RATIO = 0.40: empirically tuned on the Pub Feed
    # fixture. At 0.25 the gating fires too rarely on real noisy
    # chroma (Pub Feed: 3 of 179 windows). At 0.40 it fires often
    # enough to surface meaningful "5" populations while still
    # leaving real triads (3rd >= 50% of root) untouched. The
    # synthetic gating tests pin 0.20 < threshold < 0.50 so this
    # window is preserved.
    #
    # Phase 3 empirical finding: this tie-break only fires when the
    # main-loop argmax already picked the correct root. On Pub Feed
    # only ~16% of per-window predictions have the right root (most
    # scatter to B/C#m/F#m from chroma noise + diatonic bias on
    # F# minor key). So Phase 3 in isolation lifts strict WCSR by
    # at most that 16% — it's a necessary but not sufficient piece.
    # Phase 4 (HMM/Viterbi) corrects root-level identification with
    # temporal context; THEN the "5" gating becomes broadly active.
    THIRD_BIN_RATIO = 0.40
    if best_quality in ('maj', 'min'):
        # 3rd interval: +4 semitones above root for major, +3 for minor.
        third_interval = 4 if best_quality == 'maj' else 3
        root_bin = chroma[best_root]
        third_bin = chroma[(best_root + third_interval) % 12]
        if root_bin > 1e-9 and third_bin < THIRD_BIN_RATIO * root_bin:
            # 3rd is structurally absent. Look up the "5" raw cosine
            # at this same root to see if it's competitive.
            power_template = np.zeros(12)
            power_template[best_root] = 1.0
            power_template[(best_root + 7) % 12] = 1.0
            power_template /= (np.linalg.norm(power_template) + 1e-9)
            power_raw = float(np.dot(chroma_norm, power_template))

            POWER_CHORD_GAP = 0.05
            if power_raw >= best_raw - POWER_CHORD_GAP:
                best_quality = '5'
                best_raw = power_raw

    return (best_root, best_quality, best_raw)


def _identify_chord_from_pitches(pitches: List[int]) -> Tuple[int, str, float]:
    """
    Identify chord from a set of MIDI pitches.

    Returns (root, quality, confidence)
    """
    if not pitches:
        return (0, 'maj', 0.0)

    # Get pitch classes
    pitch_classes = set(p % 12 for p in pitches)

    best_match = (0, 'maj', 0.0)

    for root in range(12):
        for quality, intervals in CHORD_TEMPLATES.items():
            template_pcs = set((root + i) % 12 for i in intervals)

            # Calculate overlap
            overlap = len(pitch_classes & template_pcs)
            total = len(pitch_classes | template_pcs)

            if total > 0:
                similarity = overlap / total

                # Bonus for matching all template notes
                if template_pcs <= pitch_classes:
                    similarity += 0.2

                if similarity > best_match[2]:
                    best_match = (root, quality, min(similarity, 1.0))

    return best_match


def _merge_consecutive_chords(chords: List[Chord]) -> List[Chord]:
    """Merge consecutive chords with same root and quality."""
    if len(chords) < 2:
        return chords

    merged = [chords[0]]

    for chord in chords[1:]:
        last = merged[-1]
        if chord.root == last.root and chord.quality == last.quality:
            # Merge: extend the previous chord
            merged[-1] = Chord(
                root=last.root,
                quality=last.quality,
                start_time=last.start_time,
                end_time=chord.end_time,
                confidence=(last.confidence + chord.confidence) / 2,
                bass_note=last.bass_note,
            )
        else:
            merged.append(chord)

    return merged


def analyze_chord_progression(
    chords: List[Chord],
    key_root: int,
    key_quality: str = 'major',
) -> Dict:
    """
    Analyze a chord progression for music theory insights.

    Returns analysis including:
    - Roman numeral analysis
    - Common progressions detected
    - Harmonic rhythm
    """
    if not chords:
        return {'roman_numerals': [], 'progression_type': 'unknown'}

    # Convert chords to roman numerals
    roman_numerals = []
    scale = [0, 2, 4, 5, 7, 9, 11] if key_quality == 'major' else [0, 2, 3, 5, 7, 8, 10]

    for chord in chords:
        interval = (chord.root - key_root) % 12

        # Find closest scale degree
        closest_degree = min(range(7), key=lambda i: abs(scale[i] - interval))

        # Roman numeral based on quality
        numeral = ['I', 'II', 'III', 'IV', 'V', 'VI', 'VII'][closest_degree]
        if chord.quality in ['min', 'min7', 'min9']:
            numeral = numeral.lower()
        elif chord.quality in ['dim', 'dim7']:
            numeral = numeral.lower() + '°'
        elif chord.quality in ['dom7']:
            numeral += '7'
        elif chord.quality in ['maj7']:
            numeral += 'maj7'

        roman_numerals.append(numeral)

    # Detect common progressions
    progression_str = '-'.join(roman_numerals[:4])  # First 4 chords

    common_progressions = {
        'I-V-vi-IV': 'Pop/Rock (Axis)',
        'I-IV-V-I': 'Classic Rock',
        'ii-V-I': 'Jazz',
        'i-VI-III-VII': 'Epic/Cinematic',
        'I-vi-IV-V': '50s Doo-wop',
        'vi-IV-I-V': 'Emotional Pop',
    }

    progression_type = common_progressions.get(progression_str, 'Custom')

    # Calculate harmonic rhythm (average chord duration)
    if chords:
        durations = [c.end_time - c.start_time for c in chords]
        harmonic_rhythm = np.mean(durations)
    else:
        harmonic_rhythm = 0

    return {
        'roman_numerals': roman_numerals,
        'progression_type': progression_type,
        'harmonic_rhythm_sec': harmonic_rhythm,
        'chord_count': len(chords),
    }
