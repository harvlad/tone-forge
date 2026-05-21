"""
Tests for the template-based ALS generation system.

These tests validate:
1. MIDI note extraction from base64 content
2. ALS creation from analysis data
3. XML structure integrity
4. Round-trip compression/decompression
"""

import gzip
import base64
import xml.etree.ElementTree as ET
import pytest
from typing import List

from tone_forge.als_template import (
    MidiNote,
    create_als_from_analysis,
    create_als_from_analysis_base64,
    _extract_notes_from_midi_content,
)


class TestMidiNoteExtraction:
    """Test MIDI note extraction from base64 content."""

    def test_extract_notes_from_valid_midi(self):
        """Verify notes can be extracted from valid MIDI content."""
        # Create a simple MIDI file with pretty_midi
        try:
            import pretty_midi
            import io

            pm = pretty_midi.PrettyMIDI()
            inst = pretty_midi.Instrument(program=0)
            inst.notes.append(pretty_midi.Note(
                velocity=100, pitch=60, start=0.0, end=0.5
            ))
            inst.notes.append(pretty_midi.Note(
                velocity=90, pitch=64, start=0.5, end=1.0
            ))
            pm.instruments.append(inst)

            # Write to bytes
            midi_bytes = io.BytesIO()
            pm.write(midi_bytes)
            midi_b64 = base64.b64encode(midi_bytes.getvalue()).decode('ascii')

            # Extract notes
            notes = _extract_notes_from_midi_content(midi_b64)

            assert len(notes) == 2
            assert notes[0][0] == 60  # pitch
            assert notes[0][3] == 100  # velocity
            assert notes[1][0] == 64
            assert notes[1][3] == 90

        except ImportError:
            pytest.skip("pretty_midi not available")

    def test_extract_notes_invalid_base64(self):
        """Verify invalid base64 returns empty list."""
        notes = _extract_notes_from_midi_content("not-valid-base64!!!")
        assert notes == []

    def test_extract_notes_invalid_midi(self):
        """Verify invalid MIDI data returns empty list."""
        # Valid base64 but not MIDI data
        invalid_midi = base64.b64encode(b"not midi data").decode('ascii')
        notes = _extract_notes_from_midi_content(invalid_midi)
        assert notes == []


class TestCreateFromAnalysis:
    """Test the high-level create_als_from_analysis function."""

    def test_create_from_midi_stems_with_notes(self):
        """Verify creating ALS from analysis midi_stems with notes field."""
        midi_stems = {
            "bass": {
                "label": "Bass",
                "notes": [
                    (36, 0.0, 0.5, 100),  # pitch, start_sec, end_sec, velocity
                    (36, 0.5, 1.0, 100),
                ],
            },
            "drums": {
                "label": "Drums",
                "notes": [
                    (36, 0.0, 0.1, 127),  # Kick
                    (38, 0.5, 0.6, 100),  # Snare
                ],
            },
        }

        als_bytes, filename = create_als_from_analysis(
            name="Test Song",
            tempo_bpm=120.0,
            key_root=0,
            key_scale="major",
            midi_stems=midi_stems,
        )

        # Decompress and verify
        xml_str = gzip.decompress(als_bytes).decode('utf-8')
        assert "Bass MIDI" in xml_str
        assert "Drums MIDI" in xml_str

    def test_create_from_midi_stems_with_content(self):
        """Verify creating ALS from midi_stems with base64 MIDI content."""
        try:
            import pretty_midi
            import io

            # Create MIDI for bass stem
            pm = pretty_midi.PrettyMIDI()
            inst = pretty_midi.Instrument(program=0)
            inst.notes.append(pretty_midi.Note(
                velocity=100, pitch=36, start=0.0, end=0.5
            ))
            pm.instruments.append(inst)

            midi_bytes = io.BytesIO()
            pm.write(midi_bytes)
            bass_b64 = base64.b64encode(midi_bytes.getvalue()).decode('ascii')

            midi_stems = {
                "bass": {
                    "label": "Bass",
                    "content": bass_b64,  # Using content instead of notes
                },
            }

            als_bytes, filename = create_als_from_analysis(
                name="Content Test",
                tempo_bpm=120.0,
                key_root=0,
                key_scale="major",
                midi_stems=midi_stems,
            )

            xml_str = gzip.decompress(als_bytes).decode('utf-8')
            assert "Bass MIDI" in xml_str
            # Should have extracted the note
            assert "MidiNoteEvent" in xml_str

        except ImportError:
            pytest.skip("pretty_midi not available")

    def test_stem_order_preserved(self):
        """Verify stems are added in logical order."""
        midi_stems = {
            "vocals": {"label": "Vocals", "notes": [(60, 0, 1, 100)]},
            "bass": {"label": "Bass", "notes": [(36, 0, 1, 100)]},
            "drums": {"label": "Drums", "notes": [(36, 0, 0.1, 127)]},
        }

        als_bytes, _ = create_als_from_analysis(
            name="Order Test",
            tempo_bpm=120.0,
            key_root=0,
            key_scale="major",
            midi_stems=midi_stems,
        )

        xml_str = gzip.decompress(als_bytes).decode('utf-8')

        # Drums should appear before bass, bass before vocals
        drums_pos = xml_str.find("Drums MIDI")
        bass_pos = xml_str.find("Bass MIDI")
        vocals_pos = xml_str.find("Vocals MIDI")

        assert drums_pos < bass_pos < vocals_pos

    def test_empty_stems_skipped(self):
        """Verify stems with no notes are skipped."""
        midi_stems = {
            "bass": {"label": "Bass", "notes": [(36, 0, 1, 100)]},
            "guitar": {"label": "Guitar", "notes": []},  # Empty
        }

        als_bytes, _ = create_als_from_analysis(
            name="Skip Empty",
            tempo_bpm=120.0,
            key_root=0,
            key_scale="major",
            midi_stems=midi_stems,
        )

        xml_str = gzip.decompress(als_bytes).decode('utf-8')
        assert "Bass MIDI" in xml_str
        assert "Guitar MIDI" not in xml_str

    def test_tempo_applied_to_notes(self):
        """Verify tempo is correctly applied when converting note times."""
        midi_stems = {
            "bass": {
                "label": "Bass",
                "notes": [
                    (36, 0.0, 0.5, 100),  # 0.5 sec at 120 BPM = 1 beat
                ],
            },
        }

        als_bytes, _ = create_als_from_analysis(
            name="Tempo Test",
            tempo_bpm=120.0,  # 0.5 sec = 1 beat
            key_root=0,
            key_scale="major",
            midi_stems=midi_stems,
        )

        xml_str = gzip.decompress(als_bytes).decode('utf-8')

        # Find the note duration - should be 1 beat (0.5 sec at 120 BPM)
        assert 'Duration="1.0' in xml_str or 'Duration="1.' in xml_str

    def test_no_stems_produces_empty_als(self):
        """Verify creating ALS with no stems still works."""
        als_bytes, filename = create_als_from_analysis(
            name="Empty Project",
            tempo_bpm=120.0,
            key_root=0,
            key_scale="major",
            midi_stems={},
        )

        xml_str = gzip.decompress(als_bytes).decode('utf-8')
        assert '<Ableton' in xml_str
        assert '<LiveSet>' in xml_str
        # Should have no MIDI tracks
        assert 'MidiTrack' not in xml_str or xml_str.count('MidiTrack') == 0


class TestBase64Export:
    """Test base64 export functionality."""

    def test_export_base64_works(self):
        """Verify base64 export returns valid base64."""
        midi_stems = {
            "bass": {"label": "Bass", "notes": [(36, 0, 1, 100)]},
        }

        b64_content, filename = create_als_from_analysis_base64(
            name="B64 Test",
            tempo_bpm=120.0,
            key_root=0,
            key_scale="major",
            midi_stems=midi_stems,
        )

        # Verify it's valid base64
        decoded = base64.b64decode(b64_content)
        assert decoded[:2] == b'\x1f\x8b'  # Gzip magic bytes

    def test_export_filename_sanitization(self):
        """Verify filename is properly sanitized."""
        midi_stems = {
            "bass": {"label": "Bass", "notes": [(36, 0, 1, 100)]},
        }

        _, filename = create_als_from_analysis(
            name="Test/Project:With*Bad<Chars>",
            tempo_bpm=120.0,
            key_root=0,
            key_scale="major",
            midi_stems=midi_stems,
        )

        assert "/" not in filename
        assert ":" not in filename
        assert "*" not in filename
        assert filename.endswith(".als")


class TestXMLStructureIntegrity:
    """Test that generated XML maintains Ableton-compatible structure."""

    def test_produces_gzipped_xml(self):
        """Verify export produces valid gzipped XML."""
        midi_stems = {
            "bass": {"label": "Bass", "notes": [(36, 0, 1, 100)]},
        }

        als_bytes, _ = create_als_from_analysis(
            name="Gzip Test",
            tempo_bpm=120.0,
            key_root=0,
            key_scale="major",
            midi_stems=midi_stems,
        )

        # Verify it's gzipped
        assert als_bytes[:2] == b'\x1f\x8b'  # Gzip magic bytes

        # Verify decompression works
        xml_str = gzip.decompress(als_bytes).decode('utf-8')
        assert '<?xml version' in xml_str
        assert '<Ableton' in xml_str

    def test_round_trip(self):
        """Verify exported ALS can be re-parsed."""
        midi_stems = {
            "bass": {"label": "Bass", "notes": [(36, 0, 1, 100)]},
        }

        als_bytes, _ = create_als_from_analysis(
            name="RoundTrip",
            tempo_bpm=135.0,
            key_root=0,
            key_scale="major",
            midi_stems=midi_stems,
        )

        # Decompress and re-parse
        xml_str = gzip.decompress(als_bytes).decode('utf-8')
        # Remove XML declaration for parsing
        xml_content = xml_str.split('?>', 1)[1] if '?>' in xml_str else xml_str
        root = ET.fromstring(xml_content)

        # Verify structure preserved
        assert root.tag == "Ableton"
        assert root.find(".//MidiTrack") is not None

    def test_track_ids_are_unique(self):
        """Verify track IDs are unique."""
        midi_stems = {
            "drums": {"label": "Drums", "notes": [(36, 0, 0.1, 127)]},
            "bass": {"label": "Bass", "notes": [(36, 0, 1, 100)]},
            "guitar": {"label": "Guitar", "notes": [(60, 0, 1, 100)]},
        }

        als_bytes, _ = create_als_from_analysis(
            name="ID Test",
            tempo_bpm=120.0,
            key_root=0,
            key_scale="major",
            midi_stems=midi_stems,
        )

        xml_str = gzip.decompress(als_bytes).decode('utf-8')
        xml_content = xml_str.split('?>', 1)[1] if '?>' in xml_str else xml_str
        root = ET.fromstring(xml_content)

        tracks = root.findall(".//MidiTrack")
        track_ids = [t.get("Id") for t in tracks]
        assert len(track_ids) == len(set(track_ids)), "Duplicate track IDs"

    def test_required_ableton_attributes(self):
        """Verify root element has required Ableton attributes."""
        midi_stems = {
            "bass": {"label": "Bass", "notes": [(36, 0, 1, 100)]},
        }

        als_bytes, _ = create_als_from_analysis(
            name="Attr Test",
            tempo_bpm=120.0,
            key_root=0,
            key_scale="major",
            midi_stems=midi_stems,
        )

        xml_str = gzip.decompress(als_bytes).decode('utf-8')
        xml_content = xml_str.split('?>', 1)[1] if '?>' in xml_str else xml_str
        root = ET.fromstring(xml_content)

        assert root.get("MajorVersion") is not None
        assert root.get("MinorVersion") is not None
        assert root.get("Creator") is not None

    def test_midi_clip_structure(self):
        """Verify MIDI clip has required structure."""
        midi_stems = {
            "bass": {"label": "Bass", "notes": [(36, 0, 1, 100)]},
        }

        als_bytes, _ = create_als_from_analysis(
            name="Clip Test",
            tempo_bpm=120.0,
            key_root=0,
            key_scale="major",
            midi_stems=midi_stems,
        )

        xml_str = gzip.decompress(als_bytes).decode('utf-8')
        xml_content = xml_str.split('?>', 1)[1] if '?>' in xml_str else xml_str
        root = ET.fromstring(xml_content)

        clip = root.find(".//MidiClip")
        assert clip is not None

        # Required clip elements
        assert clip.find("Name") is not None
        assert clip.find("Loop") is not None
        assert clip.find("Notes") is not None
        assert clip.find("Grid") is not None

    def test_tempo_set_correctly(self):
        """Verify tempo is set in the master track."""
        midi_stems = {
            "bass": {"label": "Bass", "notes": [(36, 0, 1, 100)]},
        }

        als_bytes, _ = create_als_from_analysis(
            name="Tempo Check",
            tempo_bpm=145.5,
            key_root=0,
            key_scale="major",
            midi_stems=midi_stems,
        )

        xml_str = gzip.decompress(als_bytes).decode('utf-8')
        xml_content = xml_str.split('?>', 1)[1] if '?>' in xml_str else xml_str
        root = ET.fromstring(xml_content)

        tempo = root.find(".//MasterTrack//Tempo//Manual")
        assert tempo is not None
        assert float(tempo.get("Value")) == 145.5

    def test_key_set_correctly(self):
        """Verify key and scale are set."""
        midi_stems = {
            "bass": {"label": "Bass", "notes": [(36, 0, 1, 100)]},
        }

        als_bytes, _ = create_als_from_analysis(
            name="Key Check",
            tempo_bpm=120.0,
            key_root=9,  # A
            key_scale="minor",
            midi_stems=midi_stems,
        )

        xml_str = gzip.decompress(als_bytes).decode('utf-8')
        xml_content = xml_str.split('?>', 1)[1] if '?>' in xml_str else xml_str
        root = ET.fromstring(xml_content)

        scale = root.find(".//Scale")
        assert scale is not None

        root_note = scale.find("RootNote")
        assert root_note.get("Value") == "9"

        name = scale.find("Name")
        assert name.get("Value") == "Minor"


class TestChordLocators:
    """Test chord marker locators in ALS."""

    def test_chords_create_locators(self):
        """Verify chords are added as locators."""
        midi_stems = {
            "bass": {"label": "Bass", "notes": [(36, 0, 4, 100)]},
        }

        # Chord data (dict format from API)
        chords = [
            {"name": "Am", "start_time": 0.0, "end_time": 2.0},
            {"name": "F", "start_time": 2.0, "end_time": 4.0},
            {"name": "C", "start_time": 4.0, "end_time": 6.0},
            {"name": "G", "start_time": 6.0, "end_time": 8.0},
        ]

        als_bytes, _ = create_als_from_analysis(
            name="Chord Test",
            tempo_bpm=120.0,
            key_root=9,
            key_scale="minor",
            midi_stems=midi_stems,
            chords=chords,
        )

        xml_str = gzip.decompress(als_bytes).decode('utf-8')

        # Should have locators
        assert "<Locators>" in xml_str
        assert "Am" in xml_str
        assert "F" in xml_str
        assert "C" in xml_str
        assert "G" in xml_str

    def test_no_chords_empty_locators(self):
        """Verify no chords produces empty locators."""
        midi_stems = {
            "bass": {"label": "Bass", "notes": [(36, 0, 1, 100)]},
        }

        als_bytes, _ = create_als_from_analysis(
            name="No Chords",
            tempo_bpm=120.0,
            key_root=0,
            key_scale="major",
            midi_stems=midi_stems,
            chords=None,
        )

        xml_str = gzip.decompress(als_bytes).decode('utf-8')
        assert "<Locators/>" in xml_str


class TestMidiNoteDataclass:
    """Test the MidiNote dataclass."""

    def test_midi_note_creation(self):
        """Verify MidiNote dataclass works."""
        note = MidiNote(
            pitch=60,
            start_beats=0.0,
            duration_beats=1.0,
            velocity=100,
        )
        assert note.pitch == 60
        assert note.start_beats == 0.0
        assert note.duration_beats == 1.0
        assert note.velocity == 100

    def test_midi_note_default_velocity(self):
        """Verify default velocity is 100."""
        note = MidiNote(pitch=60, start_beats=0.0, duration_beats=1.0)
        assert note.velocity == 100


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
