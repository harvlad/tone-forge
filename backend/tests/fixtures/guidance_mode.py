"""Synthetic fixtures for the guidance-mode classifier.

These fixtures encode the failure modes observed in the
``/tmp/jam_calibration_diag.py`` run on real songs. They do NOT add
new audio; they synthesise MIDI-note dicts in the shape
``compute_section_features`` consumes (``pitch``/``start``/``end``).

The point of these fixtures is to lock observed feature-quality
problems into regression tests *before* any classifier scoring or
threshold change. Each fixture builds a list of per-stem note
streams plus a chord lane; tests run the streams through the real
``compute_section_features`` → ``classify_section`` path so the
test exercises the actual extractors, not hand-rolled feature
vectors.

Fixtures:

* :func:`fixture_monophonic_midi_chord_block` — chord progression
  delivered as monophonic per-stem streams (mimics CoreML MIDI
  extractor output where every stem comes back as a single voice).
  Expected: ``chord``.

* :func:`fixture_drums_pitched_artifacts` — drums stem with many
  spurious pitched notes alongside a genuine multi-stem chord
  progression. Expected: ``chord`` (drums must not dominate).

* :func:`fixture_riff_minority_vote` — one stem with a clear
  repeating riff, remaining stems near-silent / baseline noise.
  Reproduces the Let's Make It Pain failure pattern. Expected:
  ``riff``.

* :func:`fixture_low_chord_density_no_riff` — sparse notes, low
  repetition, low chord density across all stems. Expected:
  ``lead``.

* :func:`fixture_sparse_lead_phrase` — single-note melodic
  phrase, low repetition, high pitch-class diversity, very low
  chord density. Expected: ``lead``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence


NoteDict = dict
"""``{"pitch": int, "start": float, "end": float, "velocity": int}``."""


@dataclass(frozen=True)
class GuidanceFixture:
    """Bundle of synthetic per-stem MIDI + chord lane for one section."""

    name: str
    expected_mode: str  # "chord" | "riff" | "lead"
    section_start_s: float
    section_end_s: float
    stems: dict[str, list[NoteDict]]
    chord_regions: list[dict]
    note: str = ""  # human-readable description of what this fixture exposes


def _note(pitch: int, start: float, dur: float, vel: int = 80) -> NoteDict:
    return {
        "pitch": int(pitch),
        "start": float(start),
        "end": float(start + dur),
        "velocity": int(vel),
    }


def _chord_region(start: float, end: float, symbol: str = "C") -> dict:
    return {"start_s": float(start), "end_s": float(end), "symbol": symbol}


# ---------------------------------------------------------------------------
# Fixture 1 — monophonic_midi_chord_block
# ---------------------------------------------------------------------------

def fixture_monophonic_midi_chord_block() -> GuidanceFixture:
    """Four chords (C, G, Am, F) over 8s, every stem monophonic.

    Each chord is split across three stems: ``root_stem`` plays the
    root, ``third_stem`` plays the third, ``fifth_stem`` plays the
    fifth. No stem ever plays two notes simultaneously. The chord
    lane carries four chord regions (one per chord, 2s each →
    ``chord_density = 0.5/s``).

    Why this matters: this is the failure mode where the CoreML
    extractor returns monophonic streams per stem even though the
    actual mix contains chord blocks. ``polyphony_score`` measured
    per-stem will be exactly the constant floor (``1/6 ≈ 0.17``)
    rather than reflecting genuine polyphony.

    Expected: ``chord``. Even with a pinned polyphony signal, the
    chord-density signal alone should carry the decision.
    """
    chords = [
        # symbol  root  third  fifth   start
        ("C",     60,   64,    67,     0.0),
        ("G",     67,   71,    74,     2.0),
        ("Am",    69,   72,    76,     4.0),
        ("F",     65,   69,    72,     6.0),
    ]
    root_stream: list[NoteDict] = []
    third_stream: list[NoteDict] = []
    fifth_stream: list[NoteDict] = []
    chord_regions: list[dict] = []
    for symbol, r, t3, t5, s in chords:
        # Each stem holds its pitch for the full 2s window (held chord).
        root_stream.append(_note(r, s, 2.0))
        third_stream.append(_note(t3, s, 2.0))
        fifth_stream.append(_note(t5, s, 2.0))
        chord_regions.append(_chord_region(s, s + 2.0, symbol))
    return GuidanceFixture(
        name="monophonic_midi_chord_block",
        expected_mode="chord",
        section_start_s=0.0,
        section_end_s=8.0,
        stems={
            "root_stem": root_stream,
            "third_stem": third_stream,
            "fifth_stem": fifth_stream,
        },
        chord_regions=chord_regions,
        note=(
            "Mimics CoreML monophonic-per-stem extraction over a real "
            "chord progression. polyphony_score will be the pinned ~0.17 "
            "floor; chord_density_per_s = 0.5; classifier must still "
            "land on chord."
        ),
    )


# ---------------------------------------------------------------------------
# Fixture 2 — drums_pitched_artifacts
# ---------------------------------------------------------------------------

def fixture_drums_pitched_artifacts() -> GuidanceFixture:
    """Genuine chord progression with a drums stem emitting spurious
    pitched notes.

    The chord progression looks like the canonical I-V-vi-IV across
    8s on two harmonic stems (``other`` plays a held chord pad,
    ``bass`` plays the chord roots). The chord lane carries four
    chord regions. A ``drums`` stem emits 20 short pitched notes
    scattered across the section at varied pitches — this mimics
    the CoreML extractor assigning artifactual pitches to drum
    hits (observed on Jump and Die: drums voted lead(0.60) on one
    section).

    Expected: ``chord``. Drums should not flip the outcome.
    """
    section_end = 8.0
    chord_regions = [
        _chord_region(0.0, 2.0, "C"),
        _chord_region(2.0, 4.0, "G"),
        _chord_region(4.0, 6.0, "Am"),
        _chord_region(6.0, 8.0, "F"),
    ]
    # Two harmonic stems: chord pad + bass roots.
    pad: list[NoteDict] = []
    bass: list[NoteDict] = []
    for sym, root, third, fifth, s in (
        ("C", 60, 64, 67, 0.0),
        ("G", 67, 71, 74, 2.0),
        ("Am", 69, 72, 76, 4.0),
        ("F", 65, 69, 72, 6.0),
    ):
        # Pad plays the triad as a held block (true polyphony).
        pad.append(_note(root, s, 2.0))
        pad.append(_note(third, s, 2.0))
        pad.append(_note(fifth, s, 2.0))
        # Bass holds the root one octave down.
        bass.append(_note(root - 12, s, 2.0))

    # Drums: 20 short spurious pitched notes scattered across [0, 8].
    drums: list[NoteDict] = []
    # Pitches walk through ~7 distinct pitch classes so pcDiv ends up
    # in the same band observed on real drums (0.4-0.7).
    drum_pitches = [
        36, 38, 41, 43, 45, 48, 50,
        36, 41, 38, 45, 43, 50, 48,
        36, 38, 41, 45, 48, 50,
    ]
    for i, p in enumerate(drum_pitches):
        start = (i + 0.5) * (section_end / (len(drum_pitches) + 1))
        drums.append(_note(p, start, 0.12, vel=72))

    return GuidanceFixture(
        name="drums_pitched_artifacts",
        expected_mode="chord",
        section_start_s=0.0,
        section_end_s=section_end,
        stems={
            "other": pad,
            "bass": bass,
            "drums": drums,
        },
        chord_regions=chord_regions,
        note=(
            "Drums stem emits 20 spurious pitched notes with pcDiv ~0.6, "
            "same band observed on real drums in the Jump and Die diag. "
            "Genuine chord progression on two harmonic stems must still "
            "win the vote."
        ),
    )


# ---------------------------------------------------------------------------
# Fixture 3 — riff_minority_vote
# ---------------------------------------------------------------------------

def fixture_riff_minority_vote() -> GuidanceFixture:
    """One stem plays a clear 4-note repeating riff; five other stems
    are voiced (0.5–0.9) but contribute only baseline chord-floor
    noise.

    Numerically mirrors the Let's Make It Pain section 1 shape
    observed in the calibration diagnostic:

    * ``other``  → 16-note riff loop (E4-G4-A4-D4 × 4), monophonic
    * ``drums`` / ``bass`` / ``vocals`` / ``guitar`` / ``piano``  →
      each voiced ~50–90% with sparse non-repeating notes, producing
      per-stem chord-floor scores around 0.26 (i.e. the
      ``poly=0.17 + 0.5×cdens`` floor when ``cdens ≈ 0.18``).

    A single chord region is placed in the lane so chord_density per
    section ≈ 0.13/s — small but nonzero, exactly the LMIP regime
    where the chord detector still emits *some* regions even though
    the song is riff-driven.

    Pre-fix aggregator math:
      riff vote ≈ voi(other) × dur × conf(other)        ≈ 0.88 × 8 × 1.0 = 7.0
      chord vote ≈ Σ voi_i × dur × conf_chord_i (5 stems) ≈ 5 × 0.7 × 8 × 0.26 ≈ 7.3
    → chord wins by ~5% margin. This is the failure mode we want to
    lock as a regression.

    Expected: ``riff``.
    """
    section_end = 8.0
    # One chord region (1 chord / 8s → 0.125 chords/s); mimics LMIP s1
    # where the chord detector still emits sparse regions even on
    # riff-driven sections.
    chord_regions = [_chord_region(2.0, 4.0, "Em")]

    riff_pitches = [64, 67, 69, 74]  # E4 G4 A4 D5
    riff: list[NoteDict] = []
    note_dur = 8.0 / (4 * 4)  # 4 loops × 4 notes
    for loop in range(4):
        for i, p in enumerate(riff_pitches):
            start = (loop * 4 + i) * note_dur
            riff.append(_note(p, start, note_dur * 0.9))

    # Five baseline stems voiced 50-90% with sparse non-repeating notes.
    # Each stem fires 4-6 notes over the section at varied pitches so
    # voiced_frame_ratio lands above the floor but per-stem scores
    # collapse to the chord baseline (no repetition, no lead activity
    # high enough to win, no chord density-derived lift beyond the
    # section-wide ~0.13/s).
    def _spread(pitches: list[int], start_offsets: list[float],
                dur: float = 0.5) -> list[NoteDict]:
        return [_note(p, s, dur) for p, s in zip(pitches, start_offsets)]

    baseline_drums = _spread(
        [36, 38, 41, 43, 45, 47],
        [0.2, 1.5, 2.8, 4.2, 5.4, 6.6],
        dur=0.5,
    )
    baseline_bass = _spread(
        [40, 43, 45, 47],
        [0.4, 2.1, 4.5, 6.8],
        dur=0.9,
    )
    baseline_vocals = _spread(
        [60, 62, 64, 65],
        [1.0, 3.0, 5.0, 7.0],
        dur=0.7,
    )
    baseline_guitar = _spread(
        [55, 57, 60],
        [0.6, 3.3, 6.2],
        dur=0.6,
    )
    baseline_piano = _spread(
        [48, 50, 52, 53, 55],
        [0.5, 2.2, 3.8, 5.4, 7.1],
        dur=0.5,
    )

    return GuidanceFixture(
        name="riff_minority_vote",
        expected_mode="riff",
        section_start_s=0.0,
        section_end_s=section_end,
        stems={
            "other": riff,
            "drums": baseline_drums,
            "bass": baseline_bass,
            "vocals": baseline_vocals,
            "guitar": baseline_guitar,
            "piano": baseline_piano,
        },
        chord_regions=chord_regions,
        note=(
            "Numerically mirrors LMIP s1: 1 riff-confident stem + 5 "
            "stems above the voiced floor with sparse non-repeating "
            "notes; cdens ≈ 0.13. Five baseline chord-floor votes can "
            "outweigh the single riff vote in the aggregator — this "
            "fixture locks that failure mode."
        ),
    )


# ---------------------------------------------------------------------------
# Fixture 4 — low_chord_density_no_riff
# ---------------------------------------------------------------------------

def fixture_low_chord_density_no_riff() -> GuidanceFixture:
    """Sparse notes, no repetition, no chord regions.

    A single ``other`` stem plays six widely-spaced notes across 8s
    (note rate 0.75/s) at varied pitches. No n-gram of length 3+
    repeats. No chord regions are placed in the lane
    (``chord_density_per_s = 0``).

    Why this matters: without a riff signal and without chord
    density, the classifier's default-to-chord behaviour fires —
    the directive says this *should* land on lead instead.

    Expected: ``lead``.
    """
    section_end = 8.0
    # 6 widely-spaced notes with varied pitches, no n-gram repeats.
    spec = [
        (60, 0.5, 0.6),   # C4
        (67, 1.8, 0.5),   # G4
        (72, 3.0, 0.7),   # C5
        (65, 4.4, 0.5),   # F4
        (74, 5.8, 0.6),   # D5
        (69, 7.0, 0.5),   # A4
    ]
    melody = [_note(p, s, d) for p, s, d in spec]
    return GuidanceFixture(
        name="low_chord_density_no_riff",
        expected_mode="lead",
        section_start_s=0.0,
        section_end_s=section_end,
        stems={"other": melody},
        chord_regions=[],
        note=(
            "Sparse single-stem melody, no repetition, no chord regions. "
            "Without a chord-density signal and without riff repetition, "
            "the default-to-chord behaviour should yield to lead."
        ),
    )


# ---------------------------------------------------------------------------
# Fixture 5 — sparse_lead_phrase
# ---------------------------------------------------------------------------

def fixture_sparse_lead_phrase() -> GuidanceFixture:
    """Single-note melody, high pitch-class diversity, low repetition,
    very low chord density.

    A single ``vocals`` stem plays a 9-note phrase across 8s walking
    through ~9 distinct pitch classes (chromatic-leaning), so
    pitch_class_diversity is near 1.0. Wide melodic intervals (≥4
    semitones average) drive lead_activity_score high. No n-gram
    repeats. Chord lane is empty.

    Why this matters: this is the canonical "real lead" shape. If
    fixture 4 lands on chord, this one is the test that fails
    harder — it leaves the classifier with no excuse to choose
    chord.

    Expected: ``lead``.
    """
    section_end = 8.0
    # 9 notes, ~9 distinct pitch classes, wide intervals.
    spec = [
        (60, 0.2, 0.4),   # C4
        (66, 1.0, 0.4),   # F#4
        (62, 1.9, 0.4),   # D4
        (70, 2.9, 0.4),   # A#4
        (64, 3.9, 0.4),   # E4
        (73, 4.9, 0.4),   # C#5
        (68, 5.9, 0.4),   # G#4
        (75, 6.7, 0.4),   # D#5
        (61, 7.4, 0.4),   # C#4 / Db4
    ]
    melody = [_note(p, s, d) for p, s, d in spec]
    return GuidanceFixture(
        name="sparse_lead_phrase",
        expected_mode="lead",
        section_start_s=0.0,
        section_end_s=section_end,
        stems={"vocals": melody},
        chord_regions=[],
        note=(
            "Canonical lead-phrase shape: 9 notes across 9 pitch classes, "
            "wide intervals, no repeats, no chord regions. The classifier "
            "has no excuse to choose chord here."
        ),
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

ALL_FIXTURES: tuple = (
    fixture_monophonic_midi_chord_block,
    fixture_drums_pitched_artifacts,
    fixture_riff_minority_vote,
    fixture_low_chord_density_no_riff,
    fixture_sparse_lead_phrase,
)


__all__ = [
    "GuidanceFixture",
    "fixture_monophonic_midi_chord_block",
    "fixture_drums_pitched_artifacts",
    "fixture_riff_minority_vote",
    "fixture_low_chord_density_no_riff",
    "fixture_sparse_lead_phrase",
    "ALL_FIXTURES",
]
