"""Tests for tone_forge.midi.melody_split (pure, no audio)."""
import pytest

from tone_forge.midi.melody_split import (
    annotate_roles,
    split_melody_accompaniment,
)


def _note(pitch, start, dur=0.4, velocity=90):
    return {
        "pitch": pitch,
        "start": start,
        "end": start + dur,
        "velocity": velocity,
    }


def _chord(pitches, start, dur=1.0):
    return [_note(p, start, dur) for p in pitches]


class TestSplit:
    def test_empty(self):
        assert split_melody_accompaniment([]) == ([], [])

    def test_scale_over_block_chords(self):
        """Classic texture: strummed triads + scale on top. Chords go
        to harmony, every scale note stays melody."""
        chords = (
            _chord([48, 52, 55], 0.0, 2.0)   # C3 triad
            + _chord([45, 48, 52], 2.0, 2.0)  # Am triad
        )
        scale = [_note(72 + i, 0.3 + i * 0.45) for i in range(8)]
        melody, harmony = split_melody_accompaniment(chords + scale)
        assert sorted(n["pitch"] for n in melody) == sorted(
            n["pitch"] for n in scale
        )
        assert len(harmony) == 6

    def test_double_stop_keeps_top_voice(self):
        """Dyads: top note carries the line, lower note is harmony."""
        dyads = _chord([55, 62], 0.0) + _chord([53, 60], 0.5)
        melody, harmony = split_melody_accompaniment(dyads)
        assert [n["pitch"] for n in melody] == [62, 60]
        assert [n["pitch"] for n in harmony] == [55, 53]

    def test_slow_arpeggio_stays_melody(self):
        """Onsets wider than the window are singletons, not a chord."""
        arp = [_note(60 + p, i * 0.3) for i, p in enumerate([0, 4, 7, 4])]
        melody, harmony = split_melody_accompaniment(arp)
        assert len(melody) == 4
        assert harmony == []

    def test_strum_within_window_is_one_chord(self):
        """A 30 ms strum spread still counts as one 3-note cluster."""
        strum = [
            _note(48, 0.000), _note(52, 0.015), _note(55, 0.030),
        ]
        melody, harmony = split_melody_accompaniment(strum)
        assert melody == []
        assert len(harmony) == 3

    def test_register_gate_reassigns_low_fills(self):
        """Singleton bass fills an octave+ under the tune move to
        harmony via the register gate."""
        tune = [_note(74 + (i % 3), i * 0.5) for i in range(8)]
        low_fill = [_note(40, 1.1), _note(38, 2.6)]
        melody, harmony = split_melody_accompaniment(tune + low_fill)
        assert {n["pitch"] for n in harmony} == {40, 38}
        assert len(melody) == 8

    def test_register_gate_is_local(self):
        """A genuinely low melody passage far (in time) from a high
        passage survives: the median window is local, not global."""
        high = [_note(80, i * 0.5) for i in range(4)]          # 0.0-1.5s
        low = [_note(50 + i, 20.0 + i * 0.5) for i in range(4)]  # 20s+
        melody, _harmony = split_melody_accompaniment(high + low)
        assert len(melody) == 8


class TestAnnotate:
    def test_additive_and_partition(self):
        notes = _chord([48, 52, 55], 0.0) + [_note(72, 0.3)]
        tagged = annotate_roles(notes)
        assert len(tagged) == 4
        roles = {n["pitch"]: n["role"] for n in tagged}
        assert roles[72] == "melody"
        assert roles[48] == roles[52] == roles[55] == "harmony"
        # Original keys preserved.
        for n in tagged:
            assert {"pitch", "start", "end", "velocity", "role"} <= set(n)

    def test_originals_not_mutated(self):
        notes = [_note(60, 0.0)]
        annotate_roles(notes)
        assert "role" not in notes[0]

    def test_empty(self):
        assert annotate_roles([]) == []
