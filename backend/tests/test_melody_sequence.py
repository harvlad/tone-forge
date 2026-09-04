"""Unit tests for ``analysis.melody_sequence`` + bundle wiring.

Pure-function tests — no audio, no models. Note dicts mirror the
pipeline wire shape ``{pitch, start, end, velocity[, role]}``.
"""
from __future__ import annotations

from tone_forge.analysis.melody_sequence import build_melody_sequence
from tone_forge.contracts import MelodySequence
from tone_forge.midi.melody_split import annotate_roles
from tone_forge.session import bundle as session_bundle


def _note(pitch, start, end, velocity=90, role=None):
    n = {"pitch": pitch, "start": start, "end": end, "velocity": velocity}
    if role is not None:
        n["role"] = role
    return n


def _vocal_line(n=8, start=0.0, dur=0.4, gap=0.1):
    notes = []
    t = start
    for i in range(n):
        notes.append(_note(60 + (i % 5), t, t + dur))
        t += dur + gap
    return notes


class TestBuildMelodySequence:
    def test_empty_input_returns_none(self):
        assert build_melody_sequence({}) is None
        assert build_melody_sequence({"vocals": {"notes": []}}) is None

    def test_below_min_note_count_returns_none(self):
        stems = {"vocals": {"notes": _vocal_line(3)}}
        assert build_melody_sequence(stems) is None

    def test_vocals_win_as_melody_source(self):
        stems = {
            "vocals": {"notes": _vocal_line(8), "overall_confidence": 0.8},
            "bass": {"notes": _vocal_line(20)},  # never a melody source
        }
        seq = build_melody_sequence(stems)
        assert isinstance(seq, MelodySequence)
        assert seq.source_stem == "vocals"
        assert seq.confidence == 0.8
        assert len(seq.notes) == 8

    def test_bass_and_drums_never_selected(self):
        stems = {
            "bass": {"notes": _vocal_line(30)},
            "drums": {"notes": _vocal_line(30)},
        }
        assert build_melody_sequence(stems) is None

    def test_polyphonic_stem_uses_melody_role_only(self):
        notes = [
            _note(72, i * 0.5, i * 0.5 + 0.4, role="melody") for i in range(6)
        ] + [
            _note(48, i * 0.5, i * 0.5 + 0.4, role="harmony") for i in range(6)
        ]
        seq = build_melody_sequence({"other": {"notes": notes}})
        assert seq is not None
        assert seq.source_stem == "other"
        assert all(n["pitch"] == 72 for n in seq.notes)

    def test_untagged_polyphonic_stem_needs_annotate(self):
        # No role tags, no annotate injected → stem unusable.
        notes = _vocal_line(10)
        assert build_melody_sequence({"other": {"notes": notes}}) is None
        # With the real splitter injected, singleton onsets become melody.
        seq = build_melody_sequence(
            {"other": {"notes": notes}}, annotate=annotate_roles
        )
        assert seq is not None
        assert seq.source_stem == "other"
        assert len(seq.notes) == 10

    def test_mono_trim_overlaps(self):
        stems = {
            "vocals": {
                "notes": [
                    _note(60, 0.0, 1.0),
                    _note(62, 0.5, 1.5),  # overlaps prev → prev trimmed
                    _note(64, 2.0, 2.5),
                    _note(65, 3.0, 3.5),
                ]
            }
        }
        seq = build_melody_sequence(stems)
        assert seq is not None
        first = seq.notes[0]
        assert first["pitch"] == 60
        assert first["end"] == 0.5  # trimmed at next onset
        starts = [n["start"] for n in seq.notes]
        assert starts == sorted(starts)
        for prev, cur in zip(seq.notes, seq.notes[1:]):
            assert prev["end"] <= cur["start"]

    def test_phrases_slice_by_section(self):
        stems = {"vocals": {"notes": _vocal_line(8, start=0.0, dur=0.4, gap=0.6)}}
        sections = [
            {"start_time": 0.0, "end_time": 2.0, "type": "verse"},
            {"start_s": 2.0, "end_s": 8.0, "label": "chorus"},
        ]
        seq = build_melody_sequence(stems, sections)
        assert seq is not None
        assert len(seq.phrases) == 2
        verse, chorus = seq.phrases
        assert verse.section_label == "verse"
        assert chorus.section_label == "chorus"
        assert all(0.0 <= n["start"] < 2.0 for n in verse.notes)
        assert all(2.0 <= n["start"] < 8.0 for n in chorus.notes)
        # Every note before 8.0 landed in exactly one phrase.
        assert len(verse.notes) + len(chorus.notes) == len(
            [n for n in seq.notes if n["start"] < 8.0]
        )

    def test_malformed_rows_dropped_not_fatal(self):
        stems = {
            "vocals": {
                "notes": _vocal_line(6)
                + [
                    {"pitch": "not-a-pitch", "start": 0, "end": 1},
                    {"start": 0.1, "end": 0.2},
                    {"pitch": 60, "start": 5.0, "end": 4.0},  # inverted
                    "garbage",
                ]
            }
        }
        seq = build_melody_sequence(stems)
        assert seq is not None
        assert len(seq.notes) == 6

    def test_confidence_clamped_and_defaulted(self):
        stems = {"vocals": {"notes": _vocal_line(6), "overall_confidence": 7}}
        seq = build_melody_sequence(stems)
        assert seq is not None and seq.confidence == 1.0
        stems = {"vocals": {"notes": _vocal_line(6)}}
        seq = build_melody_sequence(stems)
        assert seq is not None and seq.confidence == 0.5

    def test_duration_scoring_beats_note_count(self):
        # 6 long sung notes should outrank 8 short guitar melody notes.
        vocal = [_note(60, i * 2.0, i * 2.0 + 1.5) for i in range(6)]
        guitar = [
            _note(72, i * 0.3, i * 0.3 + 0.1, role="melody") for i in range(8)
        ]
        seq = build_melody_sequence(
            {"vocals": {"notes": vocal}, "guitar": {"notes": guitar}}
        )
        assert seq is not None
        assert seq.source_stem == "vocals"


class TestBundleMelodyWiring:
    def _result(self):
        return {
            "wav_path": "/tmp/x.wav",
            "duration_sec": 10.0,
            "sections": [
                {"start_time": 0.0, "end_time": 10.0, "type": "verse"},
            ],
        }

    def test_bundle_carries_injected_melody_and_note_highway(self):
        seq = build_melody_sequence(
            {"vocals": {"notes": _vocal_line(6)}},
            self._result()["sections"],
        )
        assert seq is not None
        bundle = session_bundle.build(self._result(), "sid", melody=seq)
        assert bundle.melody is seq
        assert bundle.guidance.note_highway == seq.notes
        payload = session_bundle.serialize(bundle)
        assert payload["melody"]["source_stem"] == "vocals"
        assert len(payload["melody"]["notes"]) == 6
        assert len(payload["melody"]["phrases"]) == 1
        assert payload["guidance"]["note_highway"] == payload["melody"]["notes"]

    def test_bundle_without_melody_stays_legacy(self):
        bundle = session_bundle.build(self._result(), "sid")
        assert bundle.melody is None
        assert bundle.guidance.note_highway == ()
        payload = session_bundle.serialize(bundle)
        assert payload["melody"] is None
