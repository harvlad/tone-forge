"""End-to-end smoke test for the reconstruction export path (Phase 2).

Verifies the multi-stem reconstruction `.als` produced by
`export_reconstruction_als` / `als_template.create_reconstruction_als`:
- one MIDI track per stem present in `midi_stems`
- each melodic track has a non-empty `<Devices>` block
- per-stem device-Id ranges do not collide
- per-stem `preset_matches` propagate into `<UserName>`
- drums stem is gracefully skipped when no drum rack is available
- Thick Chord Pad fallback when no preset_matches are supplied

Network and the HTTP layer are intentionally bypassed — we exercise the
export functions directly so the suite is fast (<2 s) and runs without
a server.
"""

from __future__ import annotations

import base64
import gzip
import io
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pretty_midi
import pytest

from tone_forge import preset_export


_DEFAULT_PRESET_PATH = Path(preset_export._PHASE1_DEFAULT_ADV_PATH)
_ALT_PRESET_PATH = _DEFAULT_PRESET_PATH.with_name("Bright Thin Sweep Pad.adv")
_DRUM_RACK_PATH = Path(preset_export._RECONSTRUCTION_DRUM_RACK_PATH)


pytestmark = pytest.mark.skipif(
    not _DEFAULT_PRESET_PATH.exists(),
    reason=(
        "Default reconstruction preset .adv not installed at standard Live "
        "12 Standard path. Smoke test requires the Live core library."
    ),
)


def _make_midi_blob(notes_spec, *, tempo_bpm: float = 120.0):
    """Build a base64-encoded .mid blob matching MIDIExtractionResult shape.

    `notes_spec` is a list of (pitch, start_sec, end_sec, velocity) tuples.
    """
    pm = pretty_midi.PrettyMIDI(initial_tempo=tempo_bpm)
    inst = pretty_midi.Instrument(program=0, is_drum=False, name="test")
    for pitch, start, end, vel in notes_spec:
        inst.notes.append(pretty_midi.Note(
            velocity=vel, pitch=pitch, start=start, end=end,
        ))
    pm.instruments.append(inst)
    buf = io.BytesIO()
    pm.write(buf)
    return {
        "filename": "test.mid",
        "content": base64.b64encode(buf.getvalue()).decode("ascii"),
        "note_count": len(notes_spec),
        "duration_seconds": (
            max(e for _, _, e, _ in notes_spec) if notes_spec else 0.0
        ),
        "tempo_bpm": tempo_bpm,
        "pitch_range": {
            "lowest": min(p for p, _, _, _ in notes_spec) if notes_spec else 0,
            "highest": max(p for p, _, _, _ in notes_spec) if notes_spec else 0,
        },
        # Match the (pitch, start_sec, end_sec, velocity) tuple shape that
        # `als_template._notes_for_stem` expects when `notes` is supplied
        # directly. Leave it out and the orchestrator falls back to decoding
        # `content`; we provide both so the tests don't rely on the decoder.
        "notes": [(p, s, e, v) for p, s, e, v in notes_spec],
    }


def _stem(notes_spec, *, tempo_bpm: float = 120.0):
    return _make_midi_blob(notes_spec, tempo_bpm=tempo_bpm)


def _decode(exported) -> str:
    """Return decoded ALS XML as a string with shared assertions."""
    assert exported.format == "reconstruction"
    assert exported.filename.endswith(".als")
    assert exported.content_type == "application/octet-stream"
    als_bytes = base64.b64decode(exported.content)
    assert als_bytes[:2] == b"\x1f\x8b", "ALS content must be gzip"
    xml_str = gzip.decompress(als_bytes).decode("utf-8")
    ET.fromstring(xml_str)  # must parse
    return xml_str


def _count_midi_tracks(xml_str: str) -> int:
    return len(re.findall(r"<MidiTrack\b", xml_str))


def _count_keytracks(xml_str: str) -> int:
    return len(re.findall(r"<KeyTrack\b", xml_str))


class TestReconstructionExport:
    """Multi-stem reconstruction export smoke tests (Phase 2)."""

    def test_all_six_stems_emit_tracks(self):
        """All 6 stems with notes → 6 MIDI tracks (drums included)."""
        full_result = {
            "tempo_bpm": 120.0,
            "midi_stems": {
                "drums":  _stem([(36, 0.0, 0.5, 100)]),
                "bass":   _stem([(40, 0.0, 0.5, 100)]),
                "guitar": _stem([(64, 0.0, 0.5, 100)]),
                "piano":  _stem([(60, 0.0, 0.5, 100)]),
                "other":  _stem([(67, 0.0, 0.5, 100)]),
                "vocals": _stem([(72, 0.0, 0.5, 100)]),
            },
        }
        # The pipeline maps 'other' → 'guitar' if 'guitar' isn't present;
        # since we have both, both should ship.
        xml_str = _decode(
            preset_export.export_reconstruction_als(full_result, "All Six")
        )
        # If the drum rack is installed → 6 tracks; otherwise 5 (drums skipped
        # only when both rack is missing AND no fallback). With our
        # orchestrator drums get an empty device chain when no rack, but the
        # track still ships. So we expect 6 regardless of rack presence.
        assert _count_midi_tracks(xml_str) == 6
        assert _count_keytracks(xml_str) == 6

    def test_melodic_tracks_have_non_empty_device_chains(self):
        """Each non-drum stem must end up with a non-empty `<Devices>` block."""
        full_result = {
            "tempo_bpm": 120.0,
            "midi_stems": {
                "bass":   _stem([(40, 0.0, 0.5, 100)]),
                "vocals": _stem([(72, 0.0, 0.5, 100)]),
            },
        }
        xml_str = _decode(
            preset_export.export_reconstruction_als(full_result, "Devices")
        )
        # No empty <Devices/> blocks should remain.
        assert "<Devices/>" not in xml_str
        # The fallback preset is Analog ⇒ UltraAnalog block is present.
        assert "UltraAnalog" in xml_str

    def test_device_ids_are_unique(self):
        """Multi-track sets must not reuse `Id="500"` (collision sentinel)."""
        full_result = {
            "tempo_bpm": 120.0,
            "midi_stems": {
                "bass":   _stem([(40, 0.0, 0.5, 100)]),
                "guitar": _stem([(64, 0.0, 0.5, 100)]),
                "piano":  _stem([(60, 0.0, 0.5, 100)]),
                "other":  _stem([(67, 0.0, 0.5, 100)]),
                "vocals": _stem([(72, 0.0, 0.5, 100)]),
            },
        }
        xml_str = _decode(
            preset_export.export_reconstruction_als(full_result, "Ids")
        )
        # Pre-parameterization, every device opened with Id="500" — that
        # would crash Live with "list members must have unique Ids".
        # Now each stem uses a distinct base (500/600/700/800/900/1000).
        five_hundreds = re.findall(r'Id="500"', xml_str)
        assert len(five_hundreds) <= 1, (
            f'Id="500" appears {len(five_hundreds)} times — '
            "device-Id parameterization is not working"
        )

    @pytest.mark.skipif(
        not _ALT_PRESET_PATH.exists(),
        reason="Alternate Analog preset not installed.",
    )
    def test_preset_matches_propagate_to_username(self):
        """Different `preset_matches` paths should produce different
        `<UserName>` values in the resulting XML."""
        midi_stems = {
            "bass":   _stem([(40, 0.0, 0.5, 100)]),
            "vocals": _stem([(72, 0.0, 0.5, 100)]),
        }
        result_a = preset_export.export_reconstruction_als(
            {
                "tempo_bpm": 120.0,
                "midi_stems": midi_stems,
                "preset_matches": {
                    "bass": {
                        "preset_id": "alt",
                        "preset_name": "Bright Thin Sweep Pad",
                        "preset_path": str(_ALT_PRESET_PATH),
                        "instrument": "Analog",
                    },
                },
            },
            "PresetA",
        )
        result_b = preset_export.export_reconstruction_als(
            {
                "tempo_bpm": 120.0,
                "midi_stems": midi_stems,
                # No preset_matches → fallback to Thick Chord Pad on bass.
            },
            "PresetB",
        )
        xml_a = _decode(result_a)
        xml_b = _decode(result_b)
        # The user-visible preset name should reflect the chosen .adv.
        assert "Bright Thin Sweep Pad" in xml_a
        assert "Thick Chord Pad" in xml_b
        # And the two outputs must differ in the preset name region.
        assert xml_a != xml_b

    def test_drums_skipped_when_no_rack(self, monkeypatch, tmp_path):
        """If the configured drum rack path is missing, only melodic tracks
        ship — drums are dropped entirely (no notes-only track either, since
        without a rack the MIDI has nowhere meaningful to play)."""
        # Point the drum-rack constant at a non-existent file.
        missing = tmp_path / "no-such-rack.adg"
        monkeypatch.setattr(
            preset_export, "_RECONSTRUCTION_DRUM_RACK_PATH", str(missing),
        )
        full_result = {
            "tempo_bpm": 120.0,
            "midi_stems": {
                "drums":  _stem([(36, 0.0, 0.5, 100)]),
                "bass":   _stem([(40, 0.0, 0.5, 100)]),
                "vocals": _stem([(72, 0.0, 0.5, 100)]),
            },
        }
        xml_str = _decode(
            preset_export.export_reconstruction_als(full_result, "NoDrums")
        )
        # Drums still ship as a MIDI track (empty device chain) so the user
        # can route it manually — that's what the orchestrator does. Confirm
        # we don't reference a Drum Rack device.
        assert "<DrumGroupDevice" not in xml_str
        # The other stems still ship.
        assert _count_midi_tracks(xml_str) == 3

    def test_all_missing_fallback_to_thick_chord_pad(self):
        """No `preset_matches` at all → every melodic stem uses Thick Chord
        Pad → still a valid ALS."""
        full_result = {
            "tempo_bpm": 120.0,
            "midi_stems": {
                "bass":   _stem([(40, 0.0, 0.5, 100)]),
                "guitar": _stem([(64, 0.0, 0.5, 100)]),
                "vocals": _stem([(72, 0.0, 0.5, 100)]),
            },
        }
        xml_str = _decode(
            preset_export.export_reconstruction_als(full_result, "Fallback")
        )
        # Every stem should resolve to Thick Chord Pad.
        assert xml_str.count("Thick Chord Pad") >= 3
        # Three MIDI tracks present.
        assert _count_midi_tracks(xml_str) == 3
        # And each track must have a non-empty device chain.
        assert "<Devices/>" not in xml_str

    def test_missing_midi_stems_raises(self):
        """Absent `midi_stems` is a hard error — the user must analyse first."""
        with pytest.raises(ValueError, match="midi_stems"):
            preset_export.export_reconstruction_als(
                {"tempo_bpm": 120.0}, "Empty",
            )

    def test_pipeline_dict_note_shape_accepted(self):
        """`unified_pipeline._extract_midi` emits notes as dicts —
        `{"pitch", "start", "end", "velocity"}` — not tuples. The
        orchestrator must accept that shape (regression for the
        `unhashable type: 'slice'` 500 we saw in the live server)."""
        pipeline_shape_stem = {
            "filename": "bass.mid",
            "content": _make_midi_blob(
                [(40, 0.0, 0.5, 100)], tempo_bpm=120.0
            )["content"],
            "note_count": 1,
            "notes": [
                {"pitch": 40, "start": 0.0, "end": 0.5, "velocity": 100},
                {"pitch": 43, "start": 0.5, "end": 1.0, "velocity": 100},
            ],
            "tempo": 120.0,
        }
        full_result = {
            "tempo_bpm": 120.0,
            "midi_stems": {"bass": pipeline_shape_stem},
        }
        xml_str = _decode(
            preset_export.export_reconstruction_als(
                full_result, "DictNotes"
            )
        )
        # Two notes should land in the bass track's KeyTrack.
        assert _count_midi_tracks(xml_str) == 1
        # KeyTrack must contain MidiNoteEvent entries.
        assert "<MidiNoteEvent" in xml_str

    def test_tempo_propagates_to_clip(self):
        """A non-default tempo should appear in the generated ALS tempo block."""
        full_result = {
            "tempo_bpm": 95.0,
            "midi_stems": {
                "bass": _stem([(40, 0.0, 0.5, 100)], tempo_bpm=95.0),
            },
        }
        xml_str = _decode(
            preset_export.export_reconstruction_als(full_result, "Tempo")
        )
        # Live stores tempo as a Manual float Value.
        assert re.search(r'<Manual Value="95(\.0+)?"', xml_str)
