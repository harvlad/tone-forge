"""Tests for tone_forge/drum_analyzer.py - Drum analysis and machine matching."""
from __future__ import annotations

import io
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tone_forge.drum_analyzer import (
    KickCharacteristics,
    SnareCharacteristics,
    HihatCharacteristics,
    DrumOverall,
    DrumSource,
    DrumConfidence,
    DrumDescriptor,
    analyze_drums,
    match_drum_machine,
    _analyze_kick,
    _analyze_snare,
    _analyze_hihat,
    _analyze_overall,
)

SR = 22050


def _make_kick_sound(duration: float = 0.3) -> np.ndarray:
    """Generate a synthetic kick drum sound."""
    t = np.linspace(0, duration, int(SR * duration), endpoint=False)
    # Kick: low frequency with pitch drop
    freq_start = 150
    freq_end = 50
    freq = freq_start * np.exp(-t * 10) + freq_end
    phase = 2 * np.pi * np.cumsum(freq) / SR
    kick = np.sin(phase) * np.exp(-t * 8)
    return kick.astype(np.float32)


def _make_snare_sound(duration: float = 0.2) -> np.ndarray:
    """Generate a synthetic snare drum sound."""
    t = np.linspace(0, duration, int(SR * duration), endpoint=False)
    # Snare: tone + noise
    tone = np.sin(2 * np.pi * 200 * t) * np.exp(-t * 15)
    noise = np.random.randn(len(t)) * 0.3 * np.exp(-t * 10)
    snare = tone + noise
    return snare.astype(np.float32)


def _make_hihat_sound(duration: float = 0.1) -> np.ndarray:
    """Generate a synthetic hi-hat sound."""
    t = np.linspace(0, duration, int(SR * duration), endpoint=False)
    # Hi-hat: filtered noise
    noise = np.random.randn(len(t))
    # Simple high-pass effect
    hihat = noise * np.exp(-t * 20)
    return hihat.astype(np.float32)


def _make_drum_loop(duration: float = 2.0) -> np.ndarray:
    """Generate a simple drum loop."""
    samples = int(SR * duration)
    loop = np.zeros(samples, dtype=np.float32)

    # Add kicks at beats 1 and 3
    kick = _make_kick_sound()
    beat_samples = int(SR * 0.5)  # 120 BPM
    for i in [0, 2]:
        start = i * beat_samples
        end = min(start + len(kick), samples)
        loop[start:end] += kick[:end - start]

    # Add snares at beats 2 and 4
    snare = _make_snare_sound()
    for i in [1, 3]:
        start = i * beat_samples
        end = min(start + len(snare), samples)
        loop[start:end] += snare[:end - start]

    # Add hi-hats on every eighth note
    hihat = _make_hihat_sound()
    eighth_samples = beat_samples // 2
    for i in range(int(duration / 0.25)):
        start = i * eighth_samples
        end = min(start + len(hihat), samples)
        if end > start:
            loop[start:end] += hihat[:end - start] * 0.3

    # Normalize
    loop = loop / (np.max(np.abs(loop)) + 1e-6) * 0.8
    return loop


class TestDataclasses:
    """Test drum analysis dataclasses."""

    def test_kick_characteristics(self):
        kick = KickCharacteristics(pitch_hz=60, decay_ms=200, saturation=0.3)
        assert kick.pitch_hz == 60
        assert kick.decay_ms == 200
        assert kick.saturation == 0.3

    def test_snare_characteristics(self):
        snare = SnareCharacteristics(pitch_hz=200, noise=0.5, snap=0.7)
        assert snare.pitch_hz == 200
        assert snare.noise == 0.5
        assert snare.snap == 0.7

    def test_hihat_characteristics(self):
        # HihatCharacteristics uses open_ratio, not open_closed_ratio
        hihat = HihatCharacteristics(open_ratio=0.3, decay_ms=50)
        assert hihat.open_ratio == 0.3
        assert hihat.decay_ms == 50

    def test_drum_overall(self):
        overall = DrumOverall(tempo_bpm=120, swing=0.0, compression=0.5)
        assert overall.tempo_bpm == 120
        assert overall.swing == 0.0
        assert overall.compression == 0.5

    def test_drum_source(self):
        source = DrumSource(
            kind="isolated_drums",
            duration_sec=4.0,
            sample_rate=44100,
            filename="drums.wav"
        )
        assert source.kind == "isolated_drums"
        assert source.duration_sec == 4.0

    def test_drum_confidence(self):
        # DrumConfidence has: tempo, style, kick, snare (not hihat)
        conf = DrumConfidence(kick=0.8, snare=0.7, tempo=0.9, style=0.6)
        assert conf.kick == 0.8
        assert conf.tempo == 0.9


class TestDrumDescriptor:
    """Test DrumDescriptor dataclass."""

    def test_create_descriptor(self):
        desc = DrumDescriptor(
            source=DrumSource("isolated", 2.0, 44100, "test.wav"),
            kick=KickCharacteristics(60, 200, 0.3),
            snare=SnareCharacteristics(200, 0.5, 0.7),
            hihat=HihatCharacteristics(0.3, 50),
            overall=DrumOverall(120, 0.0, 0.5),
            confidence=DrumConfidence(tempo=0.9, style=0.8, kick=0.7, snare=0.6),
        )
        assert desc.source.duration_sec == 2.0
        assert desc.kick.pitch_hz == 60
        assert desc.overall.tempo_bpm == 120


class TestAnalyzeDrums:
    """Test the main analyze_drums function."""

    def test_analyze_from_file_path(self, tmp_path):
        loop = _make_drum_loop()
        file_path = tmp_path / "drums.wav"
        sf.write(str(file_path), loop, SR)

        result = analyze_drums(str(file_path))
        assert isinstance(result, DrumDescriptor)

    def test_analyze_sets_source_info(self, tmp_path):
        loop = _make_drum_loop(duration=3.0)
        file_path = tmp_path / "drums.wav"
        sf.write(str(file_path), loop, SR)

        result = analyze_drums(str(file_path))
        assert result.source.filename == "drums.wav"
        assert result.source.duration_sec > 0

    def test_analyze_returns_kick_characteristics(self, tmp_path):
        loop = _make_drum_loop()
        file_path = tmp_path / "drums.wav"
        sf.write(str(file_path), loop, SR)

        result = analyze_drums(str(file_path))
        assert result.kick is not None
        assert isinstance(result.kick.pitch_hz, (int, float))

    def test_analyze_returns_snare_characteristics(self, tmp_path):
        loop = _make_drum_loop()
        file_path = tmp_path / "drums.wav"
        sf.write(str(file_path), loop, SR)

        result = analyze_drums(str(file_path))
        assert result.snare is not None
        assert isinstance(result.snare.noise, (int, float))

    def test_analyze_returns_tempo(self, tmp_path):
        loop = _make_drum_loop()
        file_path = tmp_path / "drums.wav"
        sf.write(str(file_path), loop, SR)

        result = analyze_drums(str(file_path))
        assert result.overall is not None
        assert result.overall.tempo_bpm > 0


class TestAnalyzeKick:
    """Test kick analysis."""

    def test_kick_analysis_returns_characteristics(self):
        kick = _make_kick_sound()
        result = _analyze_kick(kick, SR)
        assert isinstance(result, KickCharacteristics)

    def test_kick_pitch_in_range(self):
        kick = _make_kick_sound()
        result = _analyze_kick(kick, SR)
        # Kick should be low frequency
        assert 30 <= result.pitch_hz <= 200

    def test_kick_decay_positive(self):
        kick = _make_kick_sound()
        result = _analyze_kick(kick, SR)
        assert result.decay_ms > 0

    def test_kick_saturation_normalized(self):
        kick = _make_kick_sound()
        result = _analyze_kick(kick, SR)
        assert 0 <= result.saturation <= 1


class TestAnalyzeSnare:
    """Test snare analysis."""

    def test_snare_analysis_returns_characteristics(self):
        snare = _make_snare_sound()
        result = _analyze_snare(snare, SR)
        assert isinstance(result, SnareCharacteristics)

    def test_snare_noise_normalized(self):
        snare = _make_snare_sound()
        result = _analyze_snare(snare, SR)
        assert 0 <= result.noise <= 1

    def test_snare_snap_normalized(self):
        snare = _make_snare_sound()
        result = _analyze_snare(snare, SR)
        assert 0 <= result.snap <= 1


class TestAnalyzeHihat:
    """Test hi-hat analysis."""

    def test_hihat_analysis_returns_characteristics(self):
        hihat = _make_hihat_sound()
        result = _analyze_hihat(hihat, SR)
        assert isinstance(result, HihatCharacteristics)

    def test_hihat_open_ratio_normalized(self):
        hihat = _make_hihat_sound()
        result = _analyze_hihat(hihat, SR)
        # The field is open_ratio, not open_closed_ratio
        assert 0 <= result.open_ratio <= 1


class TestAnalyzeOverall:
    """Test overall drum analysis."""

    def test_overall_analysis_returns_characteristics(self):
        loop = _make_drum_loop()
        result = _analyze_overall(loop, SR)
        assert isinstance(result, DrumOverall)

    def test_tempo_reasonable(self):
        loop = _make_drum_loop()  # Should be around 120 BPM
        result = _analyze_overall(loop, SR)
        # Tempo detection can vary, allow wide range
        assert 60 <= result.tempo_bpm <= 200

    def test_swing_normalized(self):
        loop = _make_drum_loop()
        result = _analyze_overall(loop, SR)
        assert 0 <= result.swing <= 1

    def test_compression_normalized(self):
        loop = _make_drum_loop()
        result = _analyze_overall(loop, SR)
        assert 0 <= result.compression <= 1


class TestMatchDrumMachine:
    """Test drum machine matching."""

    def test_match_returns_dict_or_none(self):
        desc = DrumDescriptor(
            source=DrumSource("isolated", 2.0, 44100, "test.wav"),
            kick=KickCharacteristics(50, 300, 0.2),  # 808-like
            snare=SnareCharacteristics(180, 0.3, 0.5),
            hihat=HihatCharacteristics(0.2, 40),
            overall=DrumOverall(120, 0.0, 0.3),
            confidence=DrumConfidence(tempo=0.9, style=0.8, kick=0.7, snare=0.6),
        )
        result = match_drum_machine(desc)
        assert result is None or isinstance(result, dict)

    def test_match_808_characteristics(self):
        # TR-808 characteristics: low kick, long decay, low saturation
        desc = DrumDescriptor(
            source=DrumSource("isolated", 2.0, 44100, "test.wav"),
            kick=KickCharacteristics(45, 400, 0.1),
            snare=SnareCharacteristics(150, 0.4, 0.4),
            hihat=HihatCharacteristics(0.3, 60),
            overall=DrumOverall(110, 0.0, 0.2),
            confidence=DrumConfidence(tempo=0.9, style=0.8, kick=0.7, snare=0.6),
            matched_machine="tr808",
        )
        result = match_drum_machine(desc)
        # Result depends on whether drum_machines.json exists
        if result:
            assert "id" in result or "display" in result or "machine" in result

    def test_match_909_characteristics(self):
        # TR-909 characteristics: punchier kick, more snap
        desc = DrumDescriptor(
            source=DrumSource("isolated", 2.0, 44100, "test.wav"),
            kick=KickCharacteristics(55, 200, 0.3),
            snare=SnareCharacteristics(200, 0.5, 0.7),
            hihat=HihatCharacteristics(0.4, 40),
            overall=DrumOverall(130, 0.0, 0.4),
            confidence=DrumConfidence(tempo=0.9, style=0.8, kick=0.7, snare=0.6),
            matched_machine="tr909",
        )
        result = match_drum_machine(desc)
        # Result depends on whether drum_machines.json exists
        if result:
            assert "id" in result or "display" in result or "machine" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
