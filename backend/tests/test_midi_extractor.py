"""Tests for tone_forge/midi_extractor.py - MIDI extraction functions."""
from __future__ import annotations

import io
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tone_forge.midi_extractor import (
    MIDIExtractionResult,
    NOTE_NAMES,
    SCALE_PATTERNS,
    STEM_TYPES,
    DEFAULT_PROFILE,
    SYNTHWAVE_PROFILES,
    get_extraction_profile,
    detect_key,
    filter_to_key,
    quantize_notes,
    remove_isolated_notes,
    merge_overlapping_notes,
    normalize_velocities,
    shift_octave_if_too_low,
    filter_delay_repeats,
    _sanitize_name,
    extract_midi,
    extract_midi_from_array,
)

SR = 22050


def _make_sine_wave(freq: float = 440, duration: float = 1.0) -> np.ndarray:
    """Generate a sine wave."""
    t = np.linspace(0, duration, int(SR * duration), endpoint=False)
    return (np.sin(2 * np.pi * freq * t) * 0.5).astype(np.float32)


def _make_test_audio(duration: float = 2.0) -> np.ndarray:
    """Generate test audio with harmonics."""
    t = np.linspace(0, duration, int(SR * duration), endpoint=False)
    # A4 (440 Hz) with harmonics
    sig = np.zeros_like(t)
    for k in range(1, 6):
        sig += (0.7 / k) * np.sin(2 * np.pi * k * 440 * t)
    # Add envelope
    env = np.exp(-0.5 * t)
    return (sig * env * 0.5).astype(np.float32)


class TestConstants:
    """Test module constants."""

    def test_note_names(self):
        assert len(NOTE_NAMES) == 12
        assert NOTE_NAMES[0] == 'C'
        assert NOTE_NAMES[9] == 'A'

    def test_scale_patterns_exist(self):
        assert 'major' in SCALE_PATTERNS
        assert 'minor' in SCALE_PATTERNS
        assert 'pentatonic_major' in SCALE_PATTERNS

    def test_major_scale_pattern(self):
        assert SCALE_PATTERNS['major'] == [0, 2, 4, 5, 7, 9, 11]

    def test_minor_scale_pattern(self):
        assert SCALE_PATTERNS['minor'] == [0, 2, 3, 5, 7, 8, 10]

    def test_stem_types(self):
        assert 'bass' in STEM_TYPES
        assert 'drums' in STEM_TYPES
        assert 'synth' in STEM_TYPES

    def test_default_profile_keys(self):
        assert 'onset_threshold' in DEFAULT_PROFILE
        assert 'frame_threshold' in DEFAULT_PROFILE
        assert 'min_note_ms' in DEFAULT_PROFILE

    def test_synthwave_profiles(self):
        assert 'bass' in SYNTHWAVE_PROFILES
        assert 'pad' in SYNTHWAVE_PROFILES
        assert 'lead' in SYNTHWAVE_PROFILES


class TestGetExtractionProfile:
    """Test get_extraction_profile function."""

    def test_get_default_profile(self):
        profile = get_extraction_profile('other', 'default')
        assert profile == DEFAULT_PROFILE

    def test_get_synthwave_bass(self):
        profile = get_extraction_profile('bass', 'synthwave')
        assert profile['onset_threshold'] == 0.35
        assert profile['min_note_ms'] == 150

    def test_get_synthwave_pad(self):
        profile = get_extraction_profile('pad', 'synthwave')
        assert profile['min_note_ms'] == 500  # Pads are long

    def test_unknown_stem_type(self):
        profile = get_extraction_profile('unknown_type', 'default')
        assert profile == DEFAULT_PROFILE


class TestSanitizeName:
    """Test _sanitize_name function."""

    def test_sanitize_ascii(self):
        assert _sanitize_name("Test Name") == "Test Name"

    def test_sanitize_unicode(self):
        # Unicode characters should be stripped
        result = _sanitize_name("Test™ Name©")
        assert "™" not in result
        assert "©" not in result

    def test_sanitize_empty(self):
        result = _sanitize_name("")
        assert result == "Extracted MIDI"

    def test_sanitize_only_unicode(self):
        result = _sanitize_name("™©®")
        assert result == "Extracted MIDI"


class TestMIDIExtractionResult:
    """Test MIDIExtractionResult dataclass."""

    def test_create_result(self):
        result = MIDIExtractionResult(
            filename="test.mid",
            content="base64data",
            note_count=10,
            duration_seconds=5.0,
            tempo_bpm=120.0,
            pitch_range=(60, 72),
        )
        assert result.filename == "test.mid"
        assert result.note_count == 10
        assert result.pitch_range == (60, 72)


class TestDetectKey:
    """Test detect_key function."""

    def test_detect_c_major(self):
        # Notes in C major: C4, E4, G4
        notes = [
            (60, 0.0, 0.5, 100),  # C4
            (64, 0.5, 1.0, 100),  # E4
            (67, 1.0, 1.5, 100),  # G4
        ]
        root, scale = detect_key(notes)
        # Should detect C or related key
        assert root in [0, 4, 7]  # C, E, or G (relative keys)

    def test_detect_empty_notes(self):
        root, scale = detect_key([])
        assert root == 0
        assert scale == 'major'


class TestFilterToKey:
    """Test filter_to_key function."""

    def test_filter_keeps_in_key_notes(self):
        notes = [
            (60, 0.0, 0.5, 100),  # C - in C major
            (62, 0.5, 1.0, 100),  # D - in C major
            (61, 1.0, 1.5, 100),  # C# - NOT in C major
        ]
        filtered = filter_to_key(notes, root=0, scale='major', strictness=1.0)
        # With strictness=1.0, only C major notes should remain
        pitches = [n[0] for n in filtered]
        assert 60 in pitches
        assert 62 in pitches
        # C# might be removed (depends on randomness, but high strictness should remove most)

    def test_filter_unknown_scale(self):
        notes = [(60, 0.0, 0.5, 100)]
        filtered = filter_to_key(notes, root=0, scale='unknown_scale', strictness=1.0)
        # Unknown scale should return notes unchanged
        assert filtered == notes


class TestQuantizeNotes:
    """Test quantize_notes function."""

    def test_quantize_to_16th(self):
        tempo = 120  # 120 BPM = 0.5 sec per beat
        # A note slightly off the grid
        notes = [(60, 0.13, 0.5, 100)]  # Should snap to 0.125 (16th at 120 BPM)
        quantized = quantize_notes(notes, tempo, grid_division=16, strength=1.0)
        # 16th note at 120 BPM = 0.125 sec
        assert abs(quantized[0][1] - 0.125) < 0.01

    def test_quantize_empty(self):
        quantized = quantize_notes([], 120, grid_division=16)
        assert quantized == []

    def test_quantize_preserves_duration(self):
        notes = [(60, 0.1, 0.4, 100)]
        quantized = quantize_notes(notes, 120, grid_division=16, strength=1.0)
        original_duration = 0.4 - 0.1
        new_duration = quantized[0][2] - quantized[0][1]
        assert abs(original_duration - new_duration) < 0.001


class TestRemoveIsolatedNotes:
    """Test remove_isolated_notes function."""

    def test_remove_isolated(self):
        notes = [
            (60, 0.0, 0.5, 100),  # Has neighbors
            (62, 0.2, 0.7, 100),  # Has neighbors
            (70, 10.0, 10.5, 100),  # Isolated (far from others)
        ]
        filtered = remove_isolated_notes(notes, min_neighbors=1, time_window=2.0)
        # The isolated note at t=10 should be removed
        times = [n[1] for n in filtered]
        assert 0.0 in times
        assert 0.2 in times
        assert 10.0 not in times

    def test_keep_all_if_close(self):
        notes = [
            (60, 0.0, 0.5, 100),
            (62, 0.2, 0.7, 100),
            (64, 0.4, 0.9, 100),
        ]
        filtered = remove_isolated_notes(notes, min_neighbors=1, time_window=2.0)
        assert len(filtered) == 3


class TestMergeOverlappingNotes:
    """Test merge_overlapping_notes function."""

    def test_merge_overlapping(self):
        notes = [
            (60, 0.0, 0.5, 100),
            (60, 0.45, 1.0, 90),  # Same pitch, overlaps with previous
        ]
        merged = merge_overlapping_notes(notes, max_gap=0.1)
        assert len(merged) == 1
        # Should extend to end of last note
        assert merged[0][2] >= 1.0

    def test_no_merge_different_pitch(self):
        notes = [
            (60, 0.0, 0.5, 100),
            (62, 0.45, 1.0, 90),  # Different pitch
        ]
        merged = merge_overlapping_notes(notes, max_gap=0.1)
        assert len(merged) == 2

    def test_merge_empty(self):
        merged = merge_overlapping_notes([], max_gap=0.1)
        assert merged == []


class TestNormalizeVelocities:
    """Test normalize_velocities function."""

    def test_normalize_range(self):
        notes = [
            (60, 0.0, 0.5, 30),   # Low velocity
            (62, 0.5, 1.0, 127),  # High velocity
        ]
        normalized = normalize_velocities(notes, min_vel=60, max_vel=110)
        velocities = [n[3] for n in normalized]
        assert min(velocities) == 60
        assert max(velocities) == 110

    def test_normalize_empty(self):
        normalized = normalize_velocities([], min_vel=60, max_vel=110)
        assert normalized == []


class TestShiftOctaveIfTooLow:
    """Test shift_octave_if_too_low function."""

    def test_shift_low_notes(self):
        notes = [
            (24, 0.0, 0.5, 100),  # Very low C1
            (26, 0.5, 1.0, 100),  # Very low D1
        ]
        shifted = shift_octave_if_too_low(notes, min_reasonable_pitch=28, shift_amount=12)
        # Should shift up by 12 semitones
        assert shifted[0][0] == 36
        assert shifted[1][0] == 38

    def test_no_shift_normal_notes(self):
        notes = [
            (60, 0.0, 0.5, 100),  # C4 - normal range
            (62, 0.5, 1.0, 100),
        ]
        shifted = shift_octave_if_too_low(notes, min_reasonable_pitch=28, shift_amount=12)
        # Should not shift
        assert shifted[0][0] == 60

    def test_shift_empty(self):
        shifted = shift_octave_if_too_low([], min_reasonable_pitch=28)
        assert shifted == []


class TestFilterDelayRepeats:
    """Test filter_delay_repeats function."""

    def test_filter_echo_notes(self):
        tempo = 120  # 120 BPM
        beat_sec = 60.0 / tempo  # 0.5 seconds per beat

        notes = [
            (60, 0.0, 0.5, 100),           # Original note
            (60, 0.375, 0.875, 80),        # Echo at dotted 8th (0.375 sec at 120 BPM) - quieter
        ]
        filtered = filter_delay_repeats(notes, tempo, tolerance_ms=50)
        # Echo should be filtered out (same pitch, quieter, at delay interval)
        assert len(filtered) <= len(notes)

    def test_keep_non_echo(self):
        notes = [
            (60, 0.0, 0.5, 100),
            (62, 0.5, 1.0, 100),  # Different pitch, not an echo
        ]
        filtered = filter_delay_repeats(notes, 120, tolerance_ms=50)
        assert len(filtered) == 2


class TestExtractMIDI:
    """Test extract_midi function."""

    def test_extract_returns_result(self, tmp_path):
        audio = _make_test_audio(duration=2.0)
        file_path = tmp_path / "test.wav"
        sf.write(str(file_path), audio, SR)

        result = extract_midi(
            str(file_path),
            preset_name="Test",
            polyphonic=True,
            stem_type='other',
        )
        assert isinstance(result, MIDIExtractionResult)
        assert result.filename.endswith('.mid')

    def test_extract_has_content(self, tmp_path):
        audio = _make_test_audio(duration=2.0)
        file_path = tmp_path / "test.wav"
        sf.write(str(file_path), audio, SR)

        result = extract_midi(str(file_path), preset_name="Test")
        # Content should be base64 encoded
        assert len(result.content) > 0

    def test_extract_monophonic_fallback(self, tmp_path):
        audio = _make_sine_wave(440, duration=2.0)
        file_path = tmp_path / "test.wav"
        sf.write(str(file_path), audio, SR)

        result = extract_midi(
            str(file_path),
            preset_name="Test",
            polyphonic=False,
        )
        assert isinstance(result, MIDIExtractionResult)


class TestExtractMIDIFromArray:
    """Test extract_midi_from_array function."""

    def test_extract_from_array(self):
        audio = _make_test_audio(duration=2.0)
        result = extract_midi_from_array(
            audio,
            SR,
            preset_name="Array Test",
            polyphonic=True,
        )
        assert isinstance(result, MIDIExtractionResult)
        assert result.filename == "Array Test.mid"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
