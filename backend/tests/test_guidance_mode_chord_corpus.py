"""Chord-fixture regression smoke for the guidance-mode classifier.

This is the "don't break the chord ribbon" canary the milestone plan
calls out. We feed the classifier per-section features built from the
five canonical chord progressions:

    I-V-vi-IV (pop)
    I-vi-IV-V (50s)
    vi-IV-I-V (anthem)
    I-IV-V7-I
    ii-V-I    (jazz cadence)

and assert each one classifies as guidance_mode == "chord" with
confidence ≥ 0.6. If a future tuning pass to GuidanceThresholds drops
any of these below 0.6 or flips them to riff/lead this test fires and
forces the change to be re-justified.

We don't run the full ``unified_pipeline`` here — running stem
separation + MIDI extraction on synthesised audio adds tens of seconds
to the suite and would only test detector noise, not the classifier
itself. The classifier ingests SectionFeatures regardless of how those
features were produced, so feeding it hand-built features from
ground-truth chord MIDI exercises the same seam ``unified_pipeline``
exercises in production (commit ``bc4f678`` wires this same
composition).
"""
from __future__ import annotations

from typing import List, Tuple

from tone_forge.analysis.guidance_mode import classify_section
from tone_forge.analysis.section_features import compute_section_features


def _triad_pitches(root_midi: int, quality: str = "maj") -> Tuple[int, int, int]:
    """Return three MIDI pitches for a triad rooted at ``root_midi``.

    Qualities: ``maj`` (0, 4, 7), ``min`` (0, 3, 7), ``dom7`` adds the
    flat 7 (0, 4, 7, 10) — represented here as the upper triad
    (4, 7, 10) so the helper stays 3-tuple. The detector / chord lane
    treats either as "polyphonic on this chord", which is all the
    classifier cares about.
    """
    if quality == "min":
        return (root_midi, root_midi + 3, root_midi + 7)
    if quality == "dom7":
        return (root_midi, root_midi + 4, root_midi + 7)
    return (root_midi, root_midi + 4, root_midi + 7)


# Pitch-class → root MIDI in the C4-ish band the chord_spike fixtures use.
_PC = {
    "C": 60, "D": 62, "E": 64, "F": 65, "G": 67, "A": 69, "B": 71,
}


def _build_chord_block(
    progression: List[Tuple[str, str]],
    bar_s: float = 2.0,
) -> Tuple[list[dict], tuple[dict, ...]]:
    """For a list of ``(root_label, quality)`` chords, return:

    1. A MIDI note list (triad per bar, held for the full bar) suitable
       for the ``other``/guitar stem.
    2. The matching chord_regions tuple the chord detector would emit
       so the classifier sees ``chord_density_per_s ≈ 1/bar``.
    """
    notes: list[dict] = []
    regions: list[dict] = []
    for i, (root_label, quality) in enumerate(progression):
        t0 = i * bar_s
        t1 = t0 + bar_s
        root_midi = _PC[root_label]
        for pitch in _triad_pitches(root_midi, quality):
            notes.append(
                {"pitch": pitch, "start": t0, "end": t1, "velocity": 90}
            )
        regions.append(
            {"start_s": t0, "end_s": t1, "symbol": f"{root_label}{quality}"}
        )
    return notes, tuple(regions)


# Five canonical chord progressions, in the same labelling style the
# chord-recognition substrate's eval regression uses. Each is one bar
# per chord, four chords per progression.
_PROGRESSIONS: List[Tuple[str, List[Tuple[str, str]]]] = [
    ("I-V-vi-IV (pop)",       [("C", "maj"), ("G", "maj"), ("A", "min"), ("F", "maj")]),
    ("I-vi-IV-V (50s)",       [("C", "maj"), ("A", "min"), ("F", "maj"), ("G", "maj")]),
    ("vi-IV-I-V (anthem)",    [("A", "min"), ("F", "maj"), ("C", "maj"), ("G", "maj")]),
    ("I-IV-V7-I",             [("C", "maj"), ("F", "maj"), ("G", "dom7"), ("C", "maj")]),
    ("ii-V-I (jazz cadence)", [("D", "min"), ("G", "maj"), ("C", "maj"), ("C", "maj")]),
]


def test_canonical_chord_progressions_stay_classified_as_chord() -> None:
    """The plan's canary: every chord progression must vote chord
    with confidence ≥ 0.6. If this fires, the thresholds in
    ``GuidanceThresholds`` have drifted and chord songs are being
    misclassified — which means the JAM chord ribbon is being muted
    on songs where it's still the honest surface.
    """
    failures: list[str] = []
    for name, prog in _PROGRESSIONS:
        notes, regions = _build_chord_block(prog)
        sf = compute_section_features(
            stem_name="other",
            stem_midi=notes,
            chord_regions=regions,
            section_start_s=0.0,
            section_end_s=len(prog) * 2.0,
        )
        d = classify_section([sf])
        if d.mode != "chord" or d.confidence < 0.6:
            failures.append(
                f"{name}: mode={d.mode!r} conf={d.confidence:.3f} "
                f"(reason: {d.reason})"
            )
    assert not failures, (
        "Chord-corpus regression: the classifier dropped progression(s) "
        "below the chord ≥ 0.6 floor:\n  " + "\n  ".join(failures)
    )


def test_chord_progression_with_bass_root_voicing_still_votes_chord() -> None:
    """Multi-stem version: chord pad on ``other`` + monophonic bass on
    ``bass`` playing the root only. The bass alone would score riff-ish
    (mono ≈ 1.0); the aggregator's vote-weighting should still come
    out chord because the polyphonic pad outvotes the silent-most-of-
    the-time root pattern. Mirrors the real-song shape — a guitar
    chord progression with bass roots underneath."""
    prog = _PROGRESSIONS[0][1]  # I-V-vi-IV
    pad_notes, regions = _build_chord_block(prog)

    bass_notes: list[dict] = []
    for i, (root_label, _q) in enumerate(prog):
        t0 = i * 2.0
        # Plucked root on the downbeat, held 1 bar. Two octaves down.
        bass_notes.append(
            {
                "pitch": _PC[root_label] - 24,
                "start": t0,
                "end": t0 + 2.0,
                "velocity": 110,
            }
        )

    pad_sf = compute_section_features(
        stem_name="other",
        stem_midi=pad_notes,
        chord_regions=regions,
        section_start_s=0.0,
        section_end_s=8.0,
    )
    bass_sf = compute_section_features(
        stem_name="bass",
        stem_midi=bass_notes,
        chord_regions=regions,
        section_start_s=0.0,
        section_end_s=8.0,
    )
    decision = classify_section([pad_sf, bass_sf])
    assert decision.mode == "chord", (
        f"bass-routed pop progression should still vote chord; "
        f"got {decision.mode!r} (reason: {decision.reason})"
    )
    assert decision.confidence >= 0.6, (
        f"confidence too low for canonical chord progression: "
        f"{decision.confidence:.3f}"
    )
