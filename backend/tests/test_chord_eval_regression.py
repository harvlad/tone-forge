"""Phase 7 — multi-fixture chord-detector regression suite.

This module pins the chord detector's WCSR against a curated set of
fixtures and fails CI when a code change regresses any of them. It runs
in two layers:

  (A) Synthetic fixtures: deterministic sine-wave chord progressions
      with exact ground truth. No disk audio dependency. These exercise
      Phase 3 (power-chord templates), Phase 4 (Viterbi sequence
      smoothing), Phase 5 (bass-routed disambiguation when a synthetic
      bass line is provided), and Phase 6 (beat-synchronous aggregation
      when synthetic beats are supplied). The WCSR floors here are
      pinned conservatively from the implementation's current behaviour
      so the suite catches regressions without becoming brittle to
      small numerical drift.

  (B) Real-audio fixtures: iterates every JSON fixture in
      ``backend/tests/fixtures/chord_groundtruth/`` and looks for a
      ``source_audio`` (or ``source_audio_other_stem`` /
      ``source_audio_bass_stem``) field in each. If the referenced
      audio is present on disk, runs the detector against it and
      asserts WCSR ≥ the fixture's ``regression_floor_triad_relaxed``
      value. If the audio isn't on disk (developer machine without
      the source material), the fixture is skipped cleanly via
      ``pytest.skip``. CI machines with the source material populated
      will exercise the full regression.

Phase 7 acceptance: at least one fixture (the synthetic suite) runs
green on every machine; the Pub Feed fixture runs green wherever the
source audio is available; the suite is structured so adding a new
fixture is a one-file JSON drop.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
import pytest

from tone_forge.analysis import detect_chords
from tone_forge.analysis.chord_eval import (
    triad_relaxed_wcsr,
    root_only_wcsr,
)


# ---------------------------------------------------------------------------
# Synthetic audio helpers
# ---------------------------------------------------------------------------


# Pitch class -> frequency at MIDI 60 octave (C4=261.63). For the
# regression we build chords in a fixed octave around A3/A4 so the
# overdriven-guitar overtone profile from real recordings isn't a
# variable — pure sines give a clean test of the matcher's core path.
_PC_FREQ_C4 = np.array([
    261.63, 277.18, 293.66, 311.13, 329.63, 349.23,
    369.99, 392.00, 415.30, 440.00, 466.16, 493.88,
], dtype=np.float64)


# Pitch-class indices for chord qualities (semitone offsets from root).
_QUALITY_INTERVALS = {
    'maj': (0, 4, 7),
    'min': (0, 3, 7),
    '5':   (0, 7),
}


def _label_to_root_pc(label: str) -> int:
    """Return pitch-class 0-11 for a chord label's root (A=9, C=0, ...)."""
    if len(label) >= 2 and label[1] in ('#', 'b'):
        head = label[:2]
    else:
        head = label[:1]
    pc_map = {
        'C': 0, 'C#': 1, 'Db': 1, 'D': 2, 'D#': 3, 'Eb': 3,
        'E': 4, 'F': 5, 'F#': 6, 'Gb': 6, 'G': 7, 'G#': 8,
        'Ab': 8, 'A': 9, 'A#': 10, 'Bb': 10, 'B': 11,
    }
    return pc_map[head]


def _label_to_quality(label: str) -> str:
    """Return 'maj' / 'min' / '5' for the chord label."""
    if label.endswith('5'):
        return '5'
    if label.endswith('m') and not label.endswith('dim'):
        return 'min'
    return 'maj'


def _synth_chord(
    label: str, duration_s: float, sr: int, octave_offset: int = 0,
) -> np.ndarray:
    """Synthesise a single chord as the sum of pure sines, one per
    chord tone, in the octave around C4 plus ``octave_offset`` octaves.

    Pure sines keep the chroma vector exactly matched to the template
    so the matcher's intrinsic accuracy can be measured without HPCP /
    overtone confounds.
    """
    root_pc = _label_to_root_pc(label)
    quality = _label_to_quality(label)
    intervals = _QUALITY_INTERVALS[quality]
    n = int(duration_s * sr)
    t = np.arange(n) / sr
    y = np.zeros(n, dtype=np.float64)
    octave_scale = 2.0 ** octave_offset
    for iv in intervals:
        pc = (root_pc + iv) % 12
        # Wrap into the same octave to keep all chord tones close together
        # — emulates a tight guitar voicing.
        freq = _PC_FREQ_C4[pc] * octave_scale
        if pc < root_pc:
            freq *= 2.0  # keep tones above the root
        y += np.sin(2 * np.pi * freq * t)
    y = y / max(1.0, len(intervals))
    return (y * 0.3).astype(np.float32)


def _synth_progression(
    regions: List[Tuple[float, float, str]],
    sr: int = 22050,
    octave_offset: int = 0,
) -> np.ndarray:
    """Concatenate per-region chord syntheses into one audio buffer."""
    parts = []
    for (start_s, end_s, label) in regions:
        parts.append(_synth_chord(label, end_s - start_s, sr, octave_offset))
    return np.concatenate(parts).astype(np.float32)


def _synth_bass_track(
    regions: List[Tuple[float, float, str]],
    sr: int = 22050,
) -> np.ndarray:
    """Single sine at each region's root, two octaves below the
    chord's reference octave. Emulates a bass guitar playing root
    notes — what the Phase 5 bass-routed disambiguation expects.
    """
    parts = []
    for (start_s, end_s, label) in regions:
        n = int((end_s - start_s) * sr)
        t = np.arange(n) / sr
        root_pc = _label_to_root_pc(label)
        freq = _PC_FREQ_C4[root_pc] / 4.0  # two octaves down (~65-130 Hz)
        parts.append(np.sin(2 * np.pi * freq * t).astype(np.float32) * 0.3)
    return np.concatenate(parts)


def _evenly_spaced_beats(duration_s: float, bpm: float) -> np.ndarray:
    """Generate a regular beat grid at ``bpm`` covering ``duration_s``."""
    beat_period = 60.0 / bpm
    n_beats = int(np.floor(duration_s / beat_period)) + 1
    return np.arange(n_beats, dtype=np.float64) * beat_period


# ---------------------------------------------------------------------------
# (A) Synthetic regression suite
# ---------------------------------------------------------------------------


# Synthetic fixtures + WCSR floors.
#
# Measured scores at Phase 6/7 (sine-wave audio, synthetic bass + beats):
#   E_major_I_V_vi_IV_vamp        strict=0.9961  triad=0.9961  root=0.9961
#   A_major_power_chord_vamp      strict=0.9961  triad=0.9961  root=0.9961
#   C_major_triad_progression     strict=0.9965  triad=0.9965  root=0.9965
#   F_sharp_minor_modal_vamp                     triad=0.9985  root=0.9985
#   B_flat_major_I_IV_V_I                        triad=0.9985  root=0.9985
#   mixed_quality_C_Am_F5_G                      triad=0.9985  root=0.9985
#   tight_changes_D_G_A_D                        triad=0.9985  root=0.9985
#
# Floors pinned at 0.95 — catches any single-region (~25% of audio
# duration) misclassification while leaving ~0.05 headroom for
# numerical drift in chroma / beat / Viterbi numerics. A score below
# 0.95 on these synthetic fixtures means a real regression: the
# matcher's clean-signal accuracy has dropped by more than one region.
SYNTHETIC_SUITE = [
    # name, regions [(start, end, label), ...], bpm, triad_floor, root_floor
    (
        "E_major_I_V_vi_IV_vamp",
        # Classic pop progression in E major. Triad qualities exercise
        # diatonic bias + Viterbi self-loop. Each region 2s @ 120 BPM.
        [(0.0, 2.0, "E"), (2.0, 4.0, "B"),
         (4.0, 6.0, "C#m"), (6.0, 8.0, "A")],
        120.0,
        0.95,  # triad-relaxed floor
        0.95,  # root-only floor
    ),
    (
        "A_major_power_chord_vamp",
        # Phase 3 power-chord templates: A5/D5/E5 progression. The
        # synthesised audio has no thirds, so the detector must pick
        # "5" quality over major/minor on the cosine score.
        [(0.0, 2.0, "A5"), (2.0, 4.0, "D5"),
         (4.0, 6.0, "E5"), (6.0, 8.0, "A5")],
        120.0,
        0.95,
        0.95,
    ),
    (
        "C_major_triad_progression",
        # Different key, longer regions to test stability under
        # Viterbi self-loop. C - F - G - C.
        [(0.0, 3.0, "C"), (3.0, 6.0, "F"),
         (6.0, 9.0, "G"), (9.0, 12.0, "C")],
        100.0,
        0.95,
        0.95,
    ),
    (
        "F_sharp_minor_modal_vamp",
        # Sharp-key modal vamp: vi - IV - I - V in A major (but
        # voiced starting on F#m for a minor-mode feel). Exercises
        # the root-PC table on the F#/A#/C# half of the wheel and
        # the Phase 5 bass routing on a minor-tonic progression.
        [(0.0, 2.0, "F#m"), (2.0, 4.0, "D"),
         (4.0, 6.0, "A"), (6.0, 8.0, "E")],
        120.0,
        0.95,
        0.95,
    ),
    (
        "B_flat_major_I_IV_V_I",
        # Flat-key authentic cadence: Bb - Eb - F - Bb. Triad-relaxed
        # scoring credits the enharmonic A#/D# emission as Bb/Eb
        # (same pitch classes), which is the right behaviour for a
        # detector that doesn't disambiguate key signature.
        [(0.0, 2.5, "Bb"), (2.5, 5.0, "Eb"),
         (5.0, 7.5, "F"), (7.5, 10.0, "Bb")],
        96.0,
        0.95,
        0.95,
    ),
    (
        "mixed_quality_C_Am_F5_G",
        # Heterogeneous qualities in one progression: major, minor,
        # power-chord, major. Exercises template-quality switching
        # within a single Viterbi run — the sequence model must not
        # collapse the minor or power-chord regions toward the
        # surrounding majors via self-loop dominance.
        [(0.0, 2.0, "C"), (2.0, 4.0, "Am"),
         (4.0, 6.0, "F5"), (6.0, 8.0, "G")],
        120.0,
        0.95,
        0.95,
    ),
    (
        "tight_changes_D_G_A_D",
        # 1s regions — twice the change rate of the other fixtures.
        # Exercises Viterbi behaviour at fast transitions: the
        # self-loop bias must not steamroll genuine 1-second chord
        # changes (the failure mode that motivated the sequence
        # model in the first place). Floor 0.90 (lower than other
        # fixtures) to allow for boundary timing variance on
        # fast-transition synthetic audio.
        [(0.0, 1.0, "D"), (1.0, 2.0, "G"),
         (2.0, 3.0, "A"), (3.0, 4.0, "D"),
         (4.0, 5.0, "G"), (5.0, 6.0, "A"),
         (6.0, 7.0, "D"), (7.0, 8.0, "G")],
        120.0,
        0.90,
        0.90,
    ),
]


@pytest.mark.parametrize(
    "name,regions,bpm,triad_floor,root_floor",
    SYNTHETIC_SUITE,
    ids=[s[0] for s in SYNTHETIC_SUITE],
)
def test_synthetic_progression_meets_wcsr_floors(
    name: str,
    regions: List[Tuple[float, float, str]],
    bpm: float,
    triad_floor: float,
    root_floor: float,
) -> None:
    """Detector against a synthetic chord progression must meet
    triad-relaxed and root-only WCSR floors.

    Floors are conservative: they're set well below the empirically
    observed score so small numerical drift doesn't trip the suite,
    but well above the pre-Phase-1 baseline so genuine regressions
    surface immediately.
    """
    sr = 22050
    duration_s = regions[-1][1]
    y = _synth_progression(regions, sr=sr)
    bass = _synth_bass_track(regions, sr=sr)
    beats = _evenly_spaced_beats(duration_s, bpm=bpm)

    predicted = detect_chords(
        y, sr,
        bass_audio=bass,
        beats_s=beats,
    )

    triad = triad_relaxed_wcsr(predicted, regions, duration_s)
    root = root_only_wcsr(predicted, regions, duration_s)

    assert triad >= triad_floor, (
        f"{name}: triad-relaxed WCSR {triad:.4f} below floor "
        f"{triad_floor:.4f}; predicted={[(c.symbol, round(c.start_s, 2), round(c.end_s, 2)) for c in predicted]}"
    )
    assert root >= root_floor, (
        f"{name}: root-only WCSR {root:.4f} below floor "
        f"{root_floor:.4f}; predicted={[(c.symbol, round(c.start_s, 2), round(c.end_s, 2)) for c in predicted]}"
    )


# ---------------------------------------------------------------------------
# (B) Real-audio fixture iteration
# ---------------------------------------------------------------------------


_FIXTURE_DIR = (
    Path(__file__).resolve().parent / "fixtures" / "chord_groundtruth"
)


def _discover_fixtures() -> List[Path]:
    if not _FIXTURE_DIR.exists():
        return []
    return sorted(_FIXTURE_DIR.glob("*.json"))


def _load_fixture(path: Path) -> dict:
    with open(path, "r") as f:
        return json.load(f)


def _resolve_audio_path(fixture: dict, key: str) -> Optional[Path]:
    """Return Path if the fixture names an audio file under ``key`` and
    it exists on disk, otherwise None."""
    raw = fixture.get(key)
    if not raw:
        return None
    p = Path(raw)
    if not p.is_absolute():
        # Resolve relative to the backend directory (sibling of tests/).
        p = (_FIXTURE_DIR.parent.parent.parent / p).resolve()
    return p if p.exists() else None


@pytest.mark.parametrize(
    "fixture_path",
    _discover_fixtures(),
    ids=lambda p: p.stem,
)
def test_real_audio_fixture_meets_wcsr_floor(fixture_path: Path) -> None:
    """For every JSON fixture in ``chord_groundtruth/`` that names a
    present-on-disk ``source_audio`` (or ``source_audio_other_stem``),
    run the detector and assert WCSR meets the fixture's pinned floor.

    Fixture schema additions for Phase 7:

      "source_audio_other_stem": "<path>",   # preferred (chroma source)
      "source_audio_bass_stem":  "<path>",   # optional (Phase 5 bias)
      "regression_floor_triad_relaxed": 0.20 # required when audio runs

    If the audio isn't present (typical on developer machines without
    re-imported source material), the test ``pytest.skip``s cleanly.
    """
    fixture = _load_fixture(fixture_path)

    audio_path = (
        _resolve_audio_path(fixture, "source_audio_other_stem")
        or _resolve_audio_path(fixture, "source_audio")
    )
    if audio_path is None:
        pytest.skip(
            f"source audio for {fixture_path.stem} not on disk; "
            f"re-import or populate fixture['source_audio_other_stem']"
        )

    floor = fixture.get("regression_floor_triad_relaxed")
    if floor is None:
        pytest.skip(
            f"{fixture_path.stem} has no regression_floor_triad_relaxed "
            f"pinned yet; run chord_eval_runner and add the floor"
        )

    bass_path = _resolve_audio_path(fixture, "source_audio_bass_stem")

    import librosa  # local import: the synthetic suite doesn't need it
    y, sr = librosa.load(str(audio_path), sr=22050, mono=True)
    bass_y = None
    if bass_path is not None:
        bass_y, _ = librosa.load(str(bass_path), sr=sr, mono=True)

    # Phase 6: compute beats for the detector; degrade gracefully on
    # out-of-range tempo (matches the production call-site logic).
    beats_s = None
    try:
        tempo_raw, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
        tempo_val = float(np.asarray(tempo_raw).item())
        if 40 <= tempo_val <= 240 and len(beat_frames) >= 2:
            beats_s = librosa.frames_to_time(beat_frames, sr=sr)
    except Exception:
        beats_s = None

    predicted = detect_chords(
        y, sr, bass_audio=bass_y, beats_s=beats_s,
    )

    truth_regions = fixture["regions"]
    truth_duration = float(fixture["duration_s"])
    triad = triad_relaxed_wcsr(predicted, truth_regions, truth_duration)

    assert triad >= float(floor), (
        f"{fixture_path.stem}: triad-relaxed WCSR {triad:.4f} "
        f"below pinned floor {float(floor):.4f}"
    )


# ---------------------------------------------------------------------------
# (C) Corpus aggregate (Stage 0 of detector-accuracy ladder)
# ---------------------------------------------------------------------------
#
# Stage 0 of the chord-detector heuristic-tuning ladder adds a single
# headline number — ``corpus_wcsr`` = unweighted mean of per-fixture
# triad-relaxed WCSR across all real-audio fixtures with audio present
# on this machine. Stages 1–3 of the ladder are gated on this number
# strictly increasing relative to the prior stage. The floor here is
# only a safety net (catastrophic-regression catch); the actual
# stage-gating happens against the saved Stage-0 baseline captured
# during ladder execution.


_CORPUS_FLOOR_TRIAD_RELAXED = 0.50


def _run_fixture_wcsr(fixture_path: Path) -> Optional[Tuple[str, float]]:
    """Run detect_chords against one fixture and return (slug, wcsr).

    Returns None when the fixture lacks audio on disk or hasn't pinned a
    regression floor — same skip semantics as the per-fixture test.
    """
    fixture = _load_fixture(fixture_path)
    audio_path = (
        _resolve_audio_path(fixture, "source_audio_other_stem")
        or _resolve_audio_path(fixture, "source_audio")
    )
    if audio_path is None:
        return None
    if fixture.get("regression_floor_triad_relaxed") is None:
        return None

    bass_path = _resolve_audio_path(fixture, "source_audio_bass_stem")

    import librosa
    y, sr = librosa.load(str(audio_path), sr=22050, mono=True)
    bass_y = None
    if bass_path is not None:
        bass_y, _ = librosa.load(str(bass_path), sr=sr, mono=True)

    beats_s = None
    try:
        tempo_raw, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
        tempo_val = float(np.asarray(tempo_raw).item())
        if 40 <= tempo_val <= 240 and len(beat_frames) >= 2:
            beats_s = librosa.frames_to_time(beat_frames, sr=sr)
    except Exception:
        beats_s = None

    predicted = detect_chords(y, sr, bass_audio=bass_y, beats_s=beats_s)
    truth_regions = fixture["regions"]
    truth_duration = float(fixture["duration_s"])
    triad = triad_relaxed_wcsr(predicted, truth_regions, truth_duration)
    return (fixture_path.stem, float(triad))


def test_corpus_wcsr_aggregate() -> None:
    """Aggregate corpus_wcsr across all real-audio fixtures present on
    disk. Skipped when no fixture has audio available locally.

    Prints per-fixture WCSR + corpus_wcsr so detector tuning can read
    the per-song breakdown straight off the test output. The numeric
    assertion is a catastrophic-regression floor; tighter gating
    (monotone improvement vs prior ladder stage) is the operator's job
    when shipping a Stage 1/2/3 sub-move.
    """
    fixtures = _discover_fixtures()
    if not fixtures:
        pytest.skip("no real-audio fixtures discovered")

    results: List[Tuple[str, float]] = []
    for fp in fixtures:
        out = _run_fixture_wcsr(fp)
        if out is not None:
            results.append(out)

    if not results:
        pytest.skip(
            "no real-audio fixtures have both audio + a regression floor "
            "available on this machine"
        )

    per_fixture = "\n".join(f"  {slug}: {wcsr:.4f}" for slug, wcsr in results)
    corpus_wcsr = sum(w for _, w in results) / float(len(results))
    print(
        "\nCorpus triad-relaxed WCSR breakdown:\n"
        f"{per_fixture}\n"
        f"  corpus_wcsr (mean over {len(results)} fixtures): {corpus_wcsr:.4f}"
    )

    assert corpus_wcsr >= _CORPUS_FLOOR_TRIAD_RELAXED, (
        f"corpus_wcsr {corpus_wcsr:.4f} below catastrophic-regression "
        f"floor {_CORPUS_FLOOR_TRIAD_RELAXED:.4f}; per-fixture:\n"
        f"{per_fixture}"
    )
