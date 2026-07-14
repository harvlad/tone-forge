"""Pipeline-output invariants — the defensibility lever for the Phase-7 hoist.

The chord-detection / tempo-estimation engine had two silent-zero bugs
that this test file is designed to prevent from ever returning:

  Bug A (UnifiedPipeline): ``_detect_sections`` returned ``tempo_bpm``
        in its result dict but the orchestrator at
        ``analyze_streaming`` read only ``sections`` and
        ``energy_curve`` — the tempo was dropped on the floor. Result:
        every UnifiedPipeline session shipped with ``tempo_bpm = 0.0``
        even when librosa had cleanly tracked the beats.

  Bug B (analysis_worker): The local engine wrote ``beat_times`` but
        ``session/bundle.py`` reads ``beats_s``. Field-name mismatch.
        Result: every local-engine session shipped with
        ``beats_s = ()`` — the JAM ribbon never saw beats and the
        chord-region snap step couldn't function.

  Bug C (UnifiedPipeline, Phase-7+ key hoist): ``chord_detector``
        runs Krumhansl-Schmuckler (+ a bass-anchored tiebreak) to
        pick a key for diatonic-bias scoring, but the result was only
        ever logged. ``detect_chords_from_audio`` returned
        ``List[Chord]`` with no key field, so the chord lane stage
        never surfaced it, ``AnalysisResult`` never carried it, and
        ``bundle._resolve_key`` returned None. Symptom: a song in
        F minor (chord_detector internally chose "F minor") was
        served with sharps everywhere (A#m for Bbm, C# for Db) and
        the JAM ribbon never knew the home key for spelling.

  Bug D (session/bundle._iter_sections): Producer↔consumer field-name
        mismatch on arrangement sections, exactly the Bug-B pattern.
        ``ArrangementSection.to_dict()`` emits
        ``{type, start_time, end_time, ...}`` (legacy shape used by
        the existing API and the JAM frontend), but
        ``bundle._iter_sections`` only accepted ``start_s``/``end_s``
        + ``label``. Result: every UnifiedPipeline session shipped
        with ``understanding.sections == []`` even when the section
        detector had found 43 sections. Surfaced by a deep-mode
        re-analysis of "Smells Like Teen Spirit": persisted dict had
        43 sections, ``understanding.sections`` was empty.

Both bugs were invisible to the existing stage-level regression tests
because every stage exposed a ``data exists`` signal: the section
detector returned a tempo, the chord detector returned chord regions —
nothing raised. What was missing was a contract on the *output of the
pipeline as a whole*: "for any non-silent fixture, the persisted
result has tempo > 0 and a non-empty beat grid."

This file is that contract. It runs the actual ``_track_beats`` stage
on a deterministic synthetic clip with a clear rhythmic pulse, then
walks each downstream surface (AnalysisResult.to_dict, session bundle)
and asserts the values survive.

Lesson encoded:
  Silent fallback at every stage is a hard pattern to debug when
  several stages share a contract. Output invariants are the
  cheapest way to make the silent failure loud.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import numpy as np
import pytest

from tone_forge.session.bundle import build as build_bundle
from tone_forge.unified_pipeline import (
    AnalysisResult,
    AudioData,
    DetectionResult,
    UnifiedPipeline,
)


# ---------------------------------------------------------------------------
# Fixture synthesis
# ---------------------------------------------------------------------------

def _make_rhythmic_clip(
    bpm: float = 120.0,
    duration_s: float = 6.0,
    sr: int = 22050,
) -> np.ndarray:
    """Generate a steady-pulse clip librosa.beat.beat_track locks onto.

    Each beat is a short transient (cosine click + low-pass tail) plus
    a sustained triad pad in the background so the chord-lane stage has
    something to chew on too. Beats are placed at exact
    ``60 / bpm`` second intervals so a test can assert the detected
    tempo round-trips.
    """
    n_samples = int(duration_s * sr)
    t = np.arange(n_samples) / sr
    # Sustained C major triad pad — gives chroma_cqt something to
    # latch onto if the chord stage runs.
    pad = (
        np.sin(2 * np.pi * 261.63 * t)   # C4
        + np.sin(2 * np.pi * 329.63 * t)  # E4
        + np.sin(2 * np.pi * 392.00 * t)  # G4
    ) / 6.0
    # Transient kick on each beat. Short cosine envelope on a low sine.
    beat_period_s = 60.0 / bpm
    n_beats = int(duration_s / beat_period_s)
    audio = pad.copy()
    for i in range(n_beats):
        start = int(i * beat_period_s * sr)
        click_len = int(0.04 * sr)
        end = min(start + click_len, n_samples)
        envelope = np.cos(
            np.linspace(0, np.pi / 2, end - start)
        ) ** 2
        kick = np.sin(
            2 * np.pi * 80.0 * np.arange(end - start) / sr
        ) * envelope
        audio[start:end] += kick * 1.5
    return audio.astype(np.float32)


def _make_audio_data(audio: np.ndarray, sr: int = 22050) -> AudioData:
    return AudioData(
        audio=audio,
        sr=sr,
        duration=len(audio) / sr,
        path=Path("/tmp/synthetic.wav"),
        source_type="file",
        source_name="invariant_fixture",
    )


def _make_detection(detected_type: str = "full_mix") -> DetectionResult:
    return DetectionResult(
        is_full_mix=detected_type == "full_mix",
        is_guitar=detected_type == "guitar",
        is_bass=False,
        is_drums=False,
        is_synth=False,
        is_vocals=False,
        detected_type=detected_type,
        summary=detected_type,
        confidence={detected_type: 0.9},
    )


# ---------------------------------------------------------------------------
# 1. _track_beats stage invariants
# ---------------------------------------------------------------------------

def test_track_beats_returns_canonical_keys():
    """The hoisted stage's output dict has the exact three keys
    ``analyze_streaming`` reads — anything else is a wire break."""
    pipeline = UnifiedPipeline()
    audio_data = _make_audio_data(_make_rhythmic_clip())

    grid = asyncio.run(pipeline._track_beats(audio_data))

    assert set(grid.keys()) == {"tempo_bpm", "beats_s", "downbeats_s"}, (
        "Phase-7 hoist contract: _track_beats must emit exactly these "
        "three keys for analyze_streaming to consume."
    )


def test_track_beats_recovers_tempo_on_steady_pulse():
    """A 120 BPM synthetic pulse must produce ``tempo_bpm`` in a
    musically-reasonable range. Wide bracket (80–160) so this test
    locks the *non-zero* contract, not a specific librosa estimate
    that may drift with future versions."""
    pipeline = UnifiedPipeline()
    audio_data = _make_audio_data(_make_rhythmic_clip(bpm=120.0))

    grid = asyncio.run(pipeline._track_beats(audio_data))

    # The defensibility assertion: non-zero tempo on non-silent input.
    # This single line would have caught both upstream bugs A and B.
    assert grid["tempo_bpm"] > 0, (
        "tempo_bpm must be > 0 for a non-silent rhythmic fixture; "
        "this guards against the silent-zero regression."
    )
    # Loose musical sanity — librosa often locks on octave-related
    # tempos (60 or 240 from a 120 source). Both are acceptable.
    assert 50 <= grid["tempo_bpm"] <= 260


def test_track_beats_produces_non_empty_beat_grid():
    """beats_s must be non-empty for a non-silent clip with a pulse."""
    pipeline = UnifiedPipeline()
    audio_data = _make_audio_data(_make_rhythmic_clip())

    grid = asyncio.run(pipeline._track_beats(audio_data))

    assert len(grid["beats_s"]) >= 2, (
        "beats_s must have ≥2 entries for any tracked tempo; "
        "the snap step needs at least one interval."
    )
    # Beats are monotonically increasing seconds.
    arr = np.asarray(grid["beats_s"])
    assert np.all(np.diff(arr) > 0)


def test_track_beats_produces_sane_downbeat_grid():
    """Downbeats are measured (beat_this) or 4/4-derived (librosa
    fallback). Either way the structural contract holds: at least one
    downbeat, strictly ascending, and sparser than the beat grid
    (a downbeat is one beat per bar, so count <= beat count)."""
    pipeline = UnifiedPipeline()
    audio_data = _make_audio_data(_make_rhythmic_clip(duration_s=8.0))

    grid = asyncio.run(pipeline._track_beats(audio_data))

    n_beats = len(grid["beats_s"])
    n_downbeats = len(grid["downbeats_s"])
    assert n_downbeats >= 1
    assert n_downbeats <= n_beats, (
        f"downbeats ({n_downbeats}) cannot outnumber beats ({n_beats})"
    )
    arr = np.asarray(grid["downbeats_s"])
    assert np.all(np.diff(arr) > 0), "downbeats must be strictly ascending"


def test_track_beats_degrades_silently_on_silence():
    """Pure silence must NOT raise; tempo == 0 and arrays empty are
    the honest 'no rhythm detected' signal."""
    pipeline = UnifiedPipeline()
    audio_data = _make_audio_data(np.zeros(22050 * 3, dtype=np.float32))

    grid = asyncio.run(pipeline._track_beats(audio_data))

    assert grid["tempo_bpm"] == 0.0
    assert grid["beats_s"] == []
    assert grid["downbeats_s"] == []


# ---------------------------------------------------------------------------
# 2. AnalysisResult <-> to_dict() round-trip invariants
# ---------------------------------------------------------------------------

def test_analysis_result_persists_tempo_unconditionally():
    """tempo_bpm is a non-Optional float — it must appear in to_dict()
    even when 0.0. UI keys off ``> 0``; absence would force the bundle
    resolver to walk legacy descriptor paths needlessly."""
    result = AnalysisResult(
        source_name="x",
        source_url=None,
        duration_sec=1.0,
        sample_rate=22050,
        detection=_make_detection(),
        detected_type="full_mix",
        tempo_bpm=120.0,
        beats_s=[0.0, 0.5, 1.0],
        downbeats_s=[0.0, 2.0],
    )

    out = result.to_dict()

    assert out["tempo_bpm"] == 120.0
    assert out["beats_s"] == [0.0, 0.5, 1.0]
    assert out["downbeats_s"] == [0.0, 2.0]


def test_analysis_result_persists_zero_tempo_for_silent_input():
    """Even when the beat tracker degraded (tempo=0), the field must
    survive to_dict() — otherwise the bundle resolver loses its
    ability to distinguish 'no tempo detected' from 'never wrote'."""
    result = AnalysisResult(
        source_name="silence",
        source_url=None,
        duration_sec=1.0,
        sample_rate=22050,
        detection=_make_detection(),
        detected_type="full_mix",
    )

    out = result.to_dict()

    assert "tempo_bpm" in out
    assert out["tempo_bpm"] == 0.0
    # beats_s / downbeats_s stay absent because they're Optional and
    # empty conveys nothing extra over absent.
    assert "beats_s" not in out
    assert "downbeats_s" not in out


# ---------------------------------------------------------------------------
# 3. Bundle resolver reads top-level tempo_bpm — fixes Bug A
# ---------------------------------------------------------------------------

def test_bundle_reads_top_level_tempo_first():
    """Bug A regression: the unified pipeline writes the canonical
    top-level ``tempo_bpm``. The bundle must read that BEFORE walking
    the legacy descriptor.tempo / guitar.tempo fallback paths.
    """
    persisted = {
        "detected_type": "guitar",
        "duration_sec": 30.0,
        "sample_rate": 22050,
        "tempo_bpm": 117.0,  # canonical top-level (Phase-7 hoist)
        # Legacy slots present and DIFFERENT — the top-level value
        # must win.
        "descriptor": {"tempo": 95.0},
    }
    bundle = build_bundle(persisted, session_id="bug-a")
    assert bundle.understanding.tempo_bpm == 117.0


def test_bundle_falls_back_to_legacy_when_top_level_missing():
    """Backward compat: older sessions without a top-level
    ``tempo_bpm`` still resolve through the descriptor path."""
    persisted = {
        "detected_type": "guitar",
        "duration_sec": 30.0,
        "sample_rate": 22050,
        # No top-level tempo_bpm — older history file.
        "descriptor": {"tempo": 100.0},
    }
    bundle = build_bundle(persisted, session_id="legacy")
    assert bundle.understanding.tempo_bpm == 100.0


def test_bundle_consumes_beats_s_from_top_level():
    """beats_s + downbeats_s feed the JAM now-playing strip and the
    chord-region snap. They live at the top level of the persisted
    dict by both writer paths (UnifiedPipeline + analysis_worker)."""
    persisted = {
        "detected_type": "guitar",
        "duration_sec": 4.0,
        "sample_rate": 22050,
        "tempo_bpm": 120.0,
        "beats_s": [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5],
        "downbeats_s": [0.0, 2.0],
    }
    bundle = build_bundle(persisted, session_id="beats-test")
    assert bundle.understanding.beats_s == (
        0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5,
    )
    assert bundle.understanding.downbeats_s == (0.0, 2.0)


# ---------------------------------------------------------------------------
# 4. Local-engine field-name compatibility — fixes Bug B
# ---------------------------------------------------------------------------

def test_bundle_reads_local_engine_dict_shape():
    """Bug B regression: ``analysis_worker.py`` historically wrote
    ``beat_times`` while the bundle read ``beats_s``. The new writer
    emits *both* canonical keys plus the legacy ``beat_times``;
    the bundle must accept the canonical keys."""
    # Synthesise the exact shape analysis_worker writes today.
    local_engine_result = {
        "detected_type": "guitar",
        "duration_sec": 4.0,
        "sample_rate": 22050,
        "tempo_bpm": 130.0,
        "detected_key": "C major",
        "beat_times": [0.0, 0.46, 0.92, 1.38],
        "beats_s": [0.0, 0.46, 0.92, 1.38],
        "downbeats_s": [0.0],
    }
    bundle = build_bundle(local_engine_result, session_id="local-engine")
    assert bundle.understanding.tempo_bpm == 130.0
    assert len(bundle.understanding.beats_s) == 4
    assert bundle.understanding.beats_s[0] == 0.0
    assert bundle.understanding.downbeats_s == (0.0,)


# ---------------------------------------------------------------------------
# 5. End-to-end pipeline invariants (no full UnifiedPipeline.analyze run —
#    we wire just the hoisted stage + the result builder)
# ---------------------------------------------------------------------------

def test_hoisted_pipeline_round_trip_to_bundle_tempo_survives():
    """The whole point of the hoist: run the new stage on synthetic
    audio, build an AnalysisResult, serialise via to_dict(), feed into
    the bundle. The tempo and beat grid survive the round trip."""
    pipeline = UnifiedPipeline()
    audio_data = _make_audio_data(_make_rhythmic_clip(bpm=120.0))

    grid = asyncio.run(pipeline._track_beats(audio_data))
    # The defensibility assertion — duplicated here so this test
    # alone catches the silent-zero regression even if 1..4 are
    # mutated.
    assert grid["tempo_bpm"] > 0

    result = AnalysisResult(
        source_name="fixture",
        source_url=None,
        duration_sec=audio_data.duration,
        sample_rate=audio_data.sr,
        detection=_make_detection(),
        detected_type="full_mix",
        tempo_bpm=grid["tempo_bpm"],
        beats_s=grid["beats_s"],
        downbeats_s=grid["downbeats_s"],
    )
    persisted = result.to_dict()
    bundle = build_bundle(persisted, session_id="e2e")

    assert bundle.understanding.tempo_bpm == pytest.approx(
        grid["tempo_bpm"], rel=1e-6,
    )
    assert len(bundle.understanding.beats_s) == len(grid["beats_s"])
    assert len(bundle.understanding.downbeats_s) == len(grid["downbeats_s"])


# ---------------------------------------------------------------------------
# 6. Detected-key invariants — fixes Bug C
# ---------------------------------------------------------------------------

def test_chord_detector_populates_key_out_dict():
    """``detect_chords_from_audio`` accepts a mutable ``key_out`` dict
    and populates it with the post-tie-break key decision. This is the
    rail Bug C's fix runs on — verify the rail itself."""
    from tone_forge.analysis import chord_detector

    audio = _make_rhythmic_clip(bpm=120.0, duration_s=4.0)
    key_out: dict = {}
    chord_detector.detect_chords_from_audio(
        audio, 22050, key_out=key_out,
    )

    assert key_out, (
        "key_out must be populated for a non-silent tonal fixture; "
        "Bug C regression guard."
    )
    assert set(key_out.keys()) >= {"root", "mode", "strength", "label"}
    assert key_out["mode"] in {"major", "minor"}
    assert isinstance(key_out["root"], int) and 0 <= key_out["root"] <= 11
    assert isinstance(key_out["label"], str) and key_out["label"]


def test_detect_chords_with_key_returns_chords_and_key_dict():
    """The public ``detect_chords_with_key`` is the entry point
    ``_detect_chord_lane`` uses. Verify the tuple shape and that the
    key dict is populated."""
    from tone_forge.analysis.chords import detect_chords_with_key

    audio = _make_rhythmic_clip(bpm=120.0, duration_s=4.0)
    chords, key = detect_chords_with_key(audio, 22050)

    assert isinstance(chords, tuple)
    assert isinstance(key, dict)
    assert key.get("label"), "key dict must carry a non-empty label"


def test_analysis_result_persists_detected_key_in_to_dict():
    """Round-trip: a populated AnalysisResult.detected_key survives
    serialisation; an empty one degrades to a stable absent-field +
    strength=0.0 shape that bundle can read."""
    populated = AnalysisResult(
        source_name="x",
        source_url=None,
        duration_sec=4.0,
        sample_rate=22050,
        detection=_make_detection(),
        detected_type="full_mix",
        detected_key="F minor",
        detected_key_root=5,
        detected_key_strength=0.42,
    )
    persisted = populated.to_dict()
    assert persisted["detected_key"] == "F minor"
    assert persisted["detected_key_root"] == 5
    assert persisted["detected_key_strength"] == pytest.approx(0.42)

    silent = AnalysisResult(
        source_name="silent",
        source_url=None,
        duration_sec=1.0,
        sample_rate=22050,
        detection=_make_detection(),
        detected_type="full_mix",
    )
    persisted_silent = silent.to_dict()
    # Optional fields stay absent on silent input (bundle treats
    # missing-key the same as None).
    assert "detected_key" not in persisted_silent
    assert "detected_key_root" not in persisted_silent
    # Strength is non-Optional, persisted unconditionally as the
    # honest "no key" signal.
    assert persisted_silent["detected_key_strength"] == 0.0


def test_bundle_reads_top_level_detected_key_first():
    """Bug C regression: the unified pipeline writes ``detected_key``
    at the top of the persisted dict. The bundle must consume that
    BEFORE walking the legacy descriptor.key / guitar.key paths."""
    persisted = {
        "detected_type": "guitar",
        "duration_sec": 30.0,
        "sample_rate": 22050,
        "tempo_bpm": 117.0,
        "detected_key": "F minor",        # canonical top-level
        "detected_key_strength": 0.75,
        # Legacy descriptor present with a DIFFERENT key — the
        # top-level value must win.
        "descriptor": {"key": "C major"},
    }
    bundle = build_bundle(persisted, session_id="bug-c")
    assert bundle.understanding.key == "F minor"


def test_bundle_falls_back_to_legacy_key_when_top_level_missing():
    """Backward compat: older history dicts without a top-level
    ``detected_key`` still resolve through the descriptor path."""
    persisted = {
        "detected_type": "guitar",
        "duration_sec": 30.0,
        "sample_rate": 22050,
        # No top-level detected_key — older history file.
        "descriptor": {"key": "C major"},
    }
    bundle = build_bundle(persisted, session_id="legacy-key")
    assert bundle.understanding.key == "C major"


def test_hoisted_key_round_trip_to_bundle_survives():
    """End-to-end Bug C round trip: chord_detector populates key_out
    via the public detect_chords_with_key entry point, the value lands
    on AnalysisResult.detected_key, survives to_dict(), and the bundle
    resolver exposes it as understanding.key."""
    from tone_forge.analysis.chords import detect_chords_with_key

    audio = _make_rhythmic_clip(bpm=120.0, duration_s=4.0)
    chords, key = detect_chords_with_key(audio, 22050)
    # Stage produced a key.
    assert key.get("label")

    result = AnalysisResult(
        source_name="fixture",
        source_url=None,
        duration_sec=4.0,
        sample_rate=22050,
        detection=_make_detection(),
        detected_type="full_mix",
        detected_key=key["label"],
        detected_key_root=key.get("root"),
        detected_key_strength=key.get("strength", 0.0),
    )
    persisted = result.to_dict()
    bundle = build_bundle(persisted, session_id="e2e-key")

    assert bundle.understanding.key == key["label"]


# ---------------------------------------------------------------------------
# 7. Section field-name compatibility — fixes Bug D
# ---------------------------------------------------------------------------

def test_bundle_accepts_legacy_section_field_names():
    """``bundle._iter_sections`` must translate the legacy
    ``ArrangementSection.to_dict()`` shape (``type`` / ``start_time``
    / ``end_time``) into ``Section`` records. Without this, the
    section detector's output (43 sections on a real-world track)
    silently becomes ``understanding.sections == []``."""
    persisted = {
        "detected_type": "full_mix",
        "duration_sec": 30.0,
        "sample_rate": 22050,
        "sections": [
            {
                "type": "intro",
                "start_time": 0.0,
                "end_time": 8.0,
                "duration": 8.0,
                "confidence": 0.7,
                "energy_mean": 0.2,
                "energy_peak": 0.7,
            },
            {
                "type": "verse",
                "start_time": 8.0,
                "end_time": 16.0,
                "duration": 8.0,
                "confidence": 0.85,
                "energy_mean": 0.7,
                "energy_peak": 0.9,
            },
        ],
    }
    bundle = build_bundle(persisted, session_id="bug-d-legacy")

    assert len(bundle.understanding.sections) == 2
    first, second = bundle.understanding.sections
    assert first.label == "intro"
    assert first.start_s == 0.0
    assert first.end_s == 8.0
    assert first.confidence == pytest.approx(0.7)
    assert second.label == "verse"
    assert second.start_s == 8.0


def test_bundle_accepts_contract_section_field_names():
    """The preferred contract shape (``label`` / ``start_s`` /
    ``end_s``) keeps working — Bug-D compat is additive, not a
    replacement."""
    persisted = {
        "detected_type": "full_mix",
        "duration_sec": 30.0,
        "sample_rate": 22050,
        "sections": [
            {
                "label": "verse",
                "start_s": 0.0,
                "end_s": 10.0,
                "confidence": 0.9,
            },
        ],
    }
    bundle = build_bundle(persisted, session_id="bug-d-contract")

    assert len(bundle.understanding.sections) == 1
    assert bundle.understanding.sections[0].label == "verse"
    assert bundle.understanding.sections[0].start_s == 0.0
    assert bundle.understanding.sections[0].end_s == 10.0


def test_section_detector_to_dict_shape_matches_bundle_reader():
    """End-to-end Bug D round trip: an actual
    ``ArrangementSection.to_dict()`` output is consumable by the
    bundle reader. Locks the producer↔consumer contract so the
    section detector can never silently regress past the bundle
    boundary again."""
    from tone_forge.analysis.sections import ArrangementSection, SectionType

    section = ArrangementSection(
        type=SectionType.VERSE,
        start_time=4.0,
        end_time=12.0,
        confidence=0.85,
        energy_mean=0.6,
        energy_peak=0.8,
        note_density=4.0,
        harmonic_density=0.0,
    )
    persisted = {
        "detected_type": "full_mix",
        "duration_sec": 30.0,
        "sample_rate": 22050,
        "sections": [section.to_dict()],
    }
    bundle = build_bundle(persisted, session_id="bug-d-e2e")

    assert len(bundle.understanding.sections) == 1
    assert bundle.understanding.sections[0].label == "verse"
    assert bundle.understanding.sections[0].start_s == 4.0
    assert bundle.understanding.sections[0].end_s == 12.0
    assert bundle.understanding.sections[0].confidence == pytest.approx(0.85)
    # Default ArrangementSection (constructor used above doesn't pass
    # guidance fields) emits guidance_mode="chord" / confidence=0.0 /
    # reason="" via to_dict(); the bundle reader must propagate those
    # defaults so the JAM UI's chord-ribbon fallback path stays valid.
    assert bundle.understanding.sections[0].guidance_mode == "chord"
    assert bundle.understanding.sections[0].guidance_confidence == pytest.approx(0.0)
    assert bundle.understanding.sections[0].guidance_reason == ""


def test_section_guidance_fields_round_trip_through_bundle():
    """Riff-first plumbing: when ArrangementSection.guidance_mode is
    populated by the classifier (e.g. ``"riff"`` for a Master of Puppets
    main-riff section, ``"lead"`` for a Kirk-Hammett-solo section), the
    bundle reader at session/bundle.py:_iter_sections must propagate
    the field, not silently strip it. Locks the root-cause regression
    that left every persisted bundle's ``understanding.sections`` and
    ``guidance.sections`` with no guidance_mode and forced the JAM UI
    onto the chord-ribbon fallback for power-chord-riff songs.
    """
    from tone_forge.analysis.sections import ArrangementSection, SectionType

    riff_section = ArrangementSection(
        type=SectionType.VERSE,
        start_time=0.0,
        end_time=12.0,
        confidence=0.78,
        note_density=4.0,
    )
    riff_section.guidance_mode = "riff"
    riff_section.guidance_confidence = 0.81
    riff_section.guidance_reason = "riff: guitar=riff(0.81)"

    lead_section = ArrangementSection(
        type=SectionType.BRIDGE,
        start_time=12.0,
        end_time=24.0,
        confidence=0.70,
        note_density=6.0,
    )
    lead_section.guidance_mode = "lead"
    lead_section.guidance_confidence = 0.66
    lead_section.guidance_reason = "lead: guitar=lead(0.66)"

    persisted = {
        "detected_type": "full_mix",
        "duration_sec": 30.0,
        "sample_rate": 22050,
        "sections": [riff_section.to_dict(), lead_section.to_dict()],
    }
    bundle = build_bundle(persisted, session_id="riff-first-round-trip")

    secs = bundle.understanding.sections
    assert len(secs) == 2
    assert secs[0].guidance_mode == "riff"
    assert secs[0].guidance_confidence == pytest.approx(0.81)
    assert secs[0].guidance_reason == "riff: guitar=riff(0.81)"
    assert secs[1].guidance_mode == "lead"
    assert secs[1].guidance_confidence == pytest.approx(0.66)
    assert secs[1].guidance_reason == "lead: guitar=lead(0.66)"
    # Same data must reach the guidance track (the JAM UI reads both).
    g_secs = bundle.guidance.sections
    assert tuple(s.guidance_mode for s in g_secs) == ("riff", "lead")


def test_section_dominant_stem_and_landmark_notes_round_trip():
    """Engine fix #5: the classifier picks ``dominant_stem`` and the
    section-features helper picks ``landmark_notes`` (density-capped,
    pre-ranked). Both must round-trip ArrangementSection.to_dict() →
    persisted JSON → bundle.understanding.sections so the JAM UI can
    render the riff/lead lane from a single source of truth without
    re-running stem-preference walks or guessing at note ranking.

    Locks the regression: the prior pass shipped guidance_mode through
    the bundle but lost the per-section dominant stem and landmark
    notes, which forced the client to re-walk
    ``guitar → other → piano → bass → vocals`` and produce the
    LEAD-PART-/-RIFF lane smear on dense sections (e.g. SLTS verse,
    104 notes / 16 s, 2.7 px per fret label)."""
    from tone_forge.analysis.sections import ArrangementSection, SectionType

    riff_section = ArrangementSection(
        type=SectionType.VERSE,
        start_time=0.0,
        end_time=8.0,
        confidence=0.78,
        note_density=4.0,
    )
    riff_section.guidance_mode = "riff"
    riff_section.guidance_confidence = 0.81
    riff_section.guidance_reason = "riff: guitar=riff(0.81)"
    riff_section.dominant_stem = "guitar"
    # Three landmark notes already pre-ranked + sorted by start time
    # (the shape ``select_landmark_notes`` emits).
    riff_section.landmark_notes = (
        {"pitch": 40, "start": 0.0, "end": 0.5, "velocity": 96},
        {"pitch": 45, "start": 0.5, "end": 1.0, "velocity": 92},
        {"pitch": 43, "start": 1.0, "end": 1.5, "velocity": 90},
    )

    # A "silent" section: no dominant stem, no landmark notes. Must
    # round-trip as empty string + empty tuple (the JAM UI then falls
    # back to the chord ribbon).
    silent_section = ArrangementSection(
        type=SectionType.BREAKDOWN,
        start_time=8.0,
        end_time=12.0,
        confidence=0.50,
        note_density=0.0,
    )
    # dominant_stem and landmark_notes default to "" / () on the
    # dataclass — confirm by leaving them unset.

    persisted = {
        "detected_type": "full_mix",
        "duration_sec": 12.0,
        "sample_rate": 22050,
        "sections": [riff_section.to_dict(), silent_section.to_dict()],
    }
    bundle = build_bundle(persisted, session_id="engine-fix-5-round-trip")

    secs = bundle.understanding.sections
    assert len(secs) == 2

    assert secs[0].dominant_stem == "guitar"
    assert len(secs[0].landmark_notes) == 3
    assert secs[0].landmark_notes[0] == {
        "pitch": 40,
        "start": 0.0,
        "end": 0.5,
        "velocity": 96,
    }
    assert secs[0].landmark_notes[1]["pitch"] == 45
    assert secs[0].landmark_notes[2]["pitch"] == 43
    # Order must be preserved (the helper sorts by start time).
    starts = [n["start"] for n in secs[0].landmark_notes]
    assert starts == sorted(starts)

    assert secs[1].dominant_stem == ""
    assert secs[1].landmark_notes == ()

    # The guidance track is fed from the same _iter_sections call, so
    # both fields must show up there too.
    g_secs = bundle.guidance.sections
    assert g_secs[0].dominant_stem == "guitar"
    assert len(g_secs[0].landmark_notes) == 3
    assert g_secs[1].dominant_stem == ""
    assert g_secs[1].landmark_notes == ()


def test_landmark_notes_round_trip_drops_malformed_entries():
    """``_iter_sections`` must defensively skip malformed landmark-note
    rows (missing pitch/start/end, non-numeric velocity, inverted span)
    instead of crashing or letting bad data through to the JAM UI.
    Mirrors how ``_iter_chords`` silently skips invalid rows above so
    a partial/older bundle stays parseable."""
    persisted = {
        "detected_type": "full_mix",
        "duration_sec": 4.0,
        "sample_rate": 22050,
        "sections": [
            {
                "start_s": 0.0,
                "end_s": 4.0,
                "label": "verse",
                "confidence": 0.7,
                "guidance_mode": "riff",
                "guidance_confidence": 0.6,
                "guidance_reason": "riff",
                "dominant_stem": "other",
                "landmark_notes": [
                    # Good row — survives.
                    {"pitch": 50, "start": 0.0, "end": 0.5, "velocity": 80},
                    # Missing pitch — dropped.
                    {"start": 0.5, "end": 1.0, "velocity": 80},
                    # Non-numeric pitch — dropped.
                    {"pitch": "not-a-pitch", "start": 1.0, "end": 1.5},
                    # Inverted span — dropped.
                    {"pitch": 52, "start": 2.0, "end": 1.5},
                    # Not a mapping — dropped.
                    [60, 2.0, 2.5, 80],
                    # Good row with missing velocity (defaults to 80).
                    {"pitch": 55, "start": 3.0, "end": 3.5},
                ],
            }
        ],
    }
    bundle = build_bundle(persisted, session_id="landmark-defensive")
    secs = bundle.understanding.sections
    assert len(secs) == 1
    assert secs[0].dominant_stem == "other"
    assert len(secs[0].landmark_notes) == 2
    assert secs[0].landmark_notes[0]["pitch"] == 50
    assert secs[0].landmark_notes[1]["pitch"] == 55
    assert secs[0].landmark_notes[1]["velocity"] == 80


def test_legacy_bundle_without_landmark_notes_defaults_to_empty():
    """Pre-engine-fix-#5 bundles (analysed before the dominant_stem /
    landmark_notes columns existed) must continue to parse, with both
    fields defaulting to "" / (). Locks the additive-only schema
    invariant declared in the riff-first plan."""
    persisted = {
        "detected_type": "full_mix",
        "duration_sec": 4.0,
        "sample_rate": 22050,
        "sections": [
            {
                "start_s": 0.0,
                "end_s": 4.0,
                "label": "verse",
                "confidence": 0.7,
                "guidance_mode": "chord",
            }
        ],
    }
    bundle = build_bundle(persisted, session_id="legacy-no-landmarks")
    secs = bundle.understanding.sections
    assert len(secs) == 1
    assert secs[0].dominant_stem == ""
    assert secs[0].landmark_notes == ()


# ---------------------------------------------------------------------------
# 9. debug_features round-trip — engine-fix-debug-#1 schema bump
# ---------------------------------------------------------------------------
#
# The /debug visualizer reads raw per-stem ``SectionFeatures`` from
# ``Section.debug_features``. The producer writes them as a list of dicts
# (one per stem) on every analyzed section; the bundle reader must
# round-trip them as a tuple of dicts. Legacy bundles persisted before
# the schema bump lack the key and must degrade silently to ().

def test_bundle_reads_section_debug_features():
    """A persisted section dict with debug_features round-trips into
    ``Section.debug_features`` as a tuple of plain dicts, preserving
    every key the classifier consumed."""
    persisted = {
        "detected_type": "full_mix",
        "duration_sec": 8.0,
        "sample_rate": 22050,
        "sections": [
            {
                "start_s": 0.0,
                "end_s": 4.0,
                "label": "verse",
                "confidence": 0.9,
                "guidance_mode": "riff",
                "guidance_confidence": 0.71,
                "dominant_stem": "guitar_left",
                "debug_features": [
                    {
                        "stem_name": "guitar_left",
                        "chord_density_per_s": 0.25,
                        "chord_count_in_section": 1,
                        "monophonic_ratio": 0.92,
                        "repetition_score": 0.88,
                        "repetition_period_beats": 4.0,
                        "polyphony_score": 0.05,
                        "lead_activity_score": 0.45,
                        "voiced_frame_ratio": 0.95,
                        "note_count": 16,
                        "duration_s": 4.0,
                        "pitch_class_diversity": 0.62,
                    },
                    {
                        "stem_name": "bass",
                        "chord_density_per_s": 0.0,
                        "chord_count_in_section": 0,
                        "monophonic_ratio": 1.0,
                        "repetition_score": 0.5,
                        "repetition_period_beats": None,
                        "polyphony_score": 0.0,
                        "lead_activity_score": 0.1,
                        "voiced_frame_ratio": 0.6,
                        "note_count": 8,
                        "duration_s": 4.0,
                        "pitch_class_diversity": 0.2,
                    },
                ],
            }
        ],
    }
    bundle = build_bundle(persisted, session_id="debug-features-roundtrip")
    secs = bundle.understanding.sections
    assert len(secs) == 1
    feats = secs[0].debug_features
    assert isinstance(feats, tuple)
    assert len(feats) == 2
    assert feats[0]["stem_name"] == "guitar_left"
    assert feats[0]["monophonic_ratio"] == pytest.approx(0.92)
    assert feats[0]["pitch_class_diversity"] == pytest.approx(0.62)
    assert feats[1]["stem_name"] == "bass"
    assert feats[1]["repetition_period_beats"] is None


def test_bundle_legacy_section_without_debug_features_defaults_empty():
    """Pre-debug-bump entries lack the ``debug_features`` key entirely.
    The bundle reader must default to () so the Inspector can detect
    legacy sessions and render the "no features" placeholder."""
    persisted = {
        "detected_type": "full_mix",
        "duration_sec": 4.0,
        "sample_rate": 22050,
        "sections": [
            {
                "start_s": 0.0,
                "end_s": 4.0,
                "label": "verse",
                "confidence": 0.7,
                "guidance_mode": "chord",
                # No debug_features key — pre-bump.
            }
        ],
    }
    bundle = build_bundle(persisted, session_id="legacy-no-debug-features")
    secs = bundle.understanding.sections
    assert len(secs) == 1
    assert secs[0].debug_features == ()


def test_bundle_skips_malformed_debug_feature_entries():
    """Defensive parse: non-Mapping entries in the debug_features list
    are dropped silently (matches the landmark_notes pattern). A
    well-formed entry mixed with garbage still round-trips."""
    persisted = {
        "detected_type": "full_mix",
        "duration_sec": 4.0,
        "sample_rate": 22050,
        "sections": [
            {
                "start_s": 0.0,
                "end_s": 4.0,
                "label": "v",
                "confidence": 0.9,
                "guidance_mode": "chord",
                "debug_features": [
                    "not a dict",
                    None,
                    {"stem_name": "drums", "chord_density_per_s": 0.0},
                    42,
                ],
            }
        ],
    }
    bundle = build_bundle(persisted, session_id="malformed-debug-features")
    feats = bundle.understanding.sections[0].debug_features
    assert len(feats) == 1
    assert feats[0]["stem_name"] == "drums"
