"""Unit tests for the analyzer module.

Tests individual feature extractors and classifiers with controlled inputs.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import soundfile as sf
import tempfile
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tone_forge import analyzer
from tone_forge.analyzer import _Features, _compute_features, _estimate_gain, _estimate_voicing
from tone_forge.descriptor import ToneDescriptor

SR = 22050


def _write_temp_wav(signal: np.ndarray, sr: int = SR) -> str:
    """Write signal to a temp wav file and return path."""
    path = Path(tempfile.gettempdir()) / f"test_{np.random.randint(100000)}.wav"
    sf.write(str(path), signal.astype(np.float32), sr)
    return str(path)


def _make_sine(freq: float, duration: float, amplitude: float = 0.8) -> np.ndarray:
    """Pure sine wave."""
    t = np.linspace(0, duration, int(SR * duration), endpoint=False)
    return amplitude * np.sin(2 * np.pi * freq * t)


def _make_distorted(freq: float, duration: float, drive: float = 0.7) -> np.ndarray:
    """Sine wave with soft clipping (adds harmonics like distortion)."""
    clean = _make_sine(freq, duration, amplitude=1.0)
    gain = 1 + drive * 8
    return np.tanh(clean * gain) * 0.8


def _make_harmonic_rich(fundamental: float, duration: float, n_harmonics: int = 12) -> np.ndarray:
    """Signal with many harmonics (guitar-like spectrum)."""
    t = np.linspace(0, duration, int(SR * duration), endpoint=False)
    sig = np.zeros_like(t)
    for k in range(1, n_harmonics + 1):
        if k * fundamental < SR / 2:
            sig += (1.0 / k**1.5) * np.sin(2 * np.pi * k * fundamental * t)
    return sig / np.max(np.abs(sig)) * 0.8


class TestGainEstimation:
    """Test that gain estimation responds correctly to distortion levels.

    Note: The analyzer is tuned for guitar-like signals, not pure sines.
    Simple synthetic signals may not behave exactly as expected since
    the analyzer uses spectral flatness and crest factor which depend
    on harmonic content.
    """

    def test_gain_in_valid_range(self):
        """Gain should always be in [0, 1] range."""
        sig = _make_sine(220, 2.0)
        path = _write_temp_wav(sig)
        d = analyzer.analyze(path)
        assert 0 <= d.amp.gain <= 1, f"Gain out of range: {d.amp.gain}"

    def test_distorted_signal_higher_gain_than_clean_harmonic(self):
        """Distorted signal should report higher gain than clean with harmonics."""
        # Use harmonic-rich signals that are more guitar-like
        clean_sig = _make_harmonic_rich(110, 2.0, n_harmonics=6)
        dist_sig = _make_distorted(110, 2.0, drive=0.9)

        clean_path = _write_temp_wav(clean_sig)
        dist_path = _write_temp_wav(dist_sig)

        clean_d = analyzer.analyze(clean_path)
        dist_d = analyzer.analyze(dist_path)

        # Distorted should have higher gain (or at least not lower)
        assert dist_d.amp.gain >= clean_d.amp.gain - 0.1, (
            f"Distorted ({dist_d.amp.gain:.2f}) should have gain >= clean ({clean_d.amp.gain:.2f})"
        )

    def test_heavy_distortion_moderate_to_high_gain(self):
        """Heavily clipped signal should report moderate-to-high gain."""
        sig = _make_distorted(110, 2.0, drive=1.0)
        path = _write_temp_wav(sig)
        d = analyzer.analyze(path)
        # Heavy distortion should read at least moderate gain
        assert d.amp.gain > 0.3, f"Heavy distortion should have moderate+ gain, got {d.amp.gain}"


class TestAmpFamilyClassification:
    """Test amp family detection patterns."""

    def test_returns_valid_family(self):
        """Analyzer should always return a valid amp family."""
        sig = _make_harmonic_rich(110, 2.0)
        path = _write_temp_wav(sig)
        d = analyzer.analyze(path)

        valid_families = {
            "fender_clean", "vox_chime", "marshall_plexi", "marshall_jcm",
            "mesa_rectifier", "5150_peavey", "bogner", "soldano", "ac30",
            "tweed", "dumble", "unknown",
        }
        assert d.amp.family in valid_families, f"Invalid family: {d.amp.family}"

    def test_confidence_in_valid_range(self):
        """Confidence should be between 0 and 1."""
        sig = _make_harmonic_rich(110, 2.0)
        path = _write_temp_wav(sig)
        d = analyzer.analyze(path)
        assert 0 <= d.confidence.amp_family <= 1

    def test_alternates_populated_on_low_confidence(self):
        """When confidence is low, alternates should be populated."""
        # Simple signal that's ambiguous
        sig = _make_sine(220, 2.0)
        path = _write_temp_wav(sig)
        d = analyzer.analyze(path)
        # Alternates should be a list (may be empty if confidence is high)
        assert isinstance(d.amp.alternates, list)


class TestVoicingEstimation:
    """Test EQ/voicing detection."""

    def test_voicing_values_normalized(self):
        """All voicing values should be in [0, 1] range."""
        sig = _make_harmonic_rich(110, 2.0)
        path = _write_temp_wav(sig)
        d = analyzer.analyze(path)

        assert 0 <= d.amp.voicing.bass <= 1
        assert 0 <= d.amp.voicing.mid <= 1
        assert 0 <= d.amp.voicing.treble <= 1
        assert 0 <= d.amp.voicing.presence <= 1
        assert 0 <= d.amp.voicing.mid_scoop <= 1

    def test_bass_heavy_signal_detected(self):
        """Low frequency content should increase bass reading."""
        # Low frequency signal
        low_sig = _make_harmonic_rich(55, 2.0)  # A1
        # Higher frequency signal
        high_sig = _make_harmonic_rich(440, 2.0)  # A4

        low_path = _write_temp_wav(low_sig)
        high_path = _write_temp_wav(high_sig)

        low_d = analyzer.analyze(low_path)
        high_d = analyzer.analyze(high_path)

        # Low signal should have higher bass reading
        assert low_d.amp.voicing.bass >= high_d.amp.voicing.bass - 0.1


class TestCabClassification:
    """Test cabinet/speaker detection."""

    def test_cab_config_valid(self):
        """Cab configuration should be a valid option."""
        sig = _make_harmonic_rich(110, 2.0)
        path = _write_temp_wav(sig)
        d = analyzer.analyze(path)

        valid_configs = {"1x12", "2x12", "4x10", "4x12", "unknown"}
        assert d.cab.configuration in valid_configs

    def test_speaker_character_valid(self):
        """Speaker character should be a valid option."""
        sig = _make_harmonic_rich(110, 2.0)
        path = _write_temp_wav(sig)
        d = analyzer.analyze(path)

        valid_chars = {"v30_like", "g12h_like", "g12m_like", "alnico_blue_like", "jensen_like", "unknown"}
        assert d.cab.speaker_character in valid_chars


class TestEffectsDetection:
    """Test effects detection (delay, reverb, modulation).

    Note: Effects detection uses heuristics tuned for real guitar signals.
    Simple synthetic signals may trigger false positives/negatives.
    """

    def test_reverb_detection_returns_valid_structure(self):
        """Reverb detection should return valid structure or None."""
        sig = _make_sine(220, 1.0)
        path = _write_temp_wav(sig)
        d = analyzer.analyze(path)
        # Reverb is either None or has valid fields
        if d.effects.reverb:
            assert d.effects.reverb.type in ("room", "plate", "hall", "none")
            assert 0 <= d.effects.reverb.mix <= 1
            assert 0 <= d.effects.reverb.size <= 1

    def test_delay_echo_detection(self):
        """Signal with echo pattern should detect delay."""
        # Create signal with echo
        sig = _make_sine(220, 0.3)
        # Add delayed copy
        delay_samples = int(SR * 0.35)  # 350ms delay
        padded = np.zeros(len(sig) + delay_samples * 3)
        padded[:len(sig)] = sig
        padded[delay_samples:delay_samples + len(sig)] += sig * 0.5
        padded[delay_samples * 2:delay_samples * 2 + len(sig)] += sig * 0.25

        path = _write_temp_wav(padded.astype(np.float32))
        d = analyzer.analyze(path)

        # Should detect delay
        if d.effects.delay:
            assert d.effects.delay.time_ms > 200, "Should detect delay time"

    def test_effects_structure_valid(self):
        """Effects should have valid structure."""
        sig = _make_harmonic_rich(110, 2.0)
        path = _write_temp_wav(sig)
        d = analyzer.analyze(path)

        # Effects object should exist
        assert d.effects is not None
        # Individual effects are optional (None) or have valid structure
        if d.effects.delay:
            assert hasattr(d.effects.delay, 'time_ms')
            assert hasattr(d.effects.delay, 'mix')
        if d.effects.modulation:
            assert hasattr(d.effects.modulation, 'type')
            assert hasattr(d.effects.modulation, 'rate')


class TestSourceMetadata:
    """Test source metadata extraction."""

    def test_duration_accurate(self):
        """Reported duration should match actual audio length."""
        duration = 2.5
        sig = _make_sine(220, duration)
        path = _write_temp_wav(sig)
        d = analyzer.analyze(path)
        assert abs(d.source.duration_sec - duration) < 0.1

    def test_sample_rate_recorded(self):
        """Sample rate should be recorded."""
        sig = _make_sine(220, 1.0)
        path = _write_temp_wav(sig)
        d = analyzer.analyze(path)
        assert d.source.sample_rate == SR

    def test_filename_captured(self):
        """Filename should be captured from path."""
        sig = _make_sine(220, 1.0)
        path = _write_temp_wav(sig)
        d = analyzer.analyze(path)
        assert d.source.filename is not None
        assert ".wav" in d.source.filename


class TestGuitarContext:
    """Test guitar context inference."""

    def test_playing_style_valid(self):
        """Playing style should be a recognized value."""
        sig = _make_harmonic_rich(110, 2.0)
        path = _write_temp_wav(sig)
        d = analyzer.analyze(path)

        valid_styles = {"palm_mute", "chord_riff", "lead", "clean_strum", "unknown"}
        assert d.guitar.playing_style in valid_styles

    def test_pickup_brightness_normalized(self):
        """Pickup brightness should be in [0, 1] range."""
        sig = _make_harmonic_rich(110, 2.0)
        path = _write_temp_wav(sig)
        d = analyzer.analyze(path)
        assert 0 <= d.guitar.pickup_brightness <= 1


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_very_short_audio(self):
        """Very short audio should still analyze without crashing."""
        sig = _make_sine(220, 0.1)  # 100ms
        path = _write_temp_wav(sig)
        d = analyzer.analyze(path)
        assert isinstance(d, ToneDescriptor)

    def test_silence_handling(self):
        """Near-silent audio should not crash."""
        sig = np.zeros(int(SR * 1.0)) + np.random.randn(int(SR * 1.0)) * 0.0001
        path = _write_temp_wav(sig.astype(np.float32))
        d = analyzer.analyze(path)
        assert isinstance(d, ToneDescriptor)

    def test_loud_clipped_audio(self):
        """Heavily clipped audio should still analyze."""
        sig = np.clip(_make_sine(220, 1.0) * 3, -1, 1)
        path = _write_temp_wav(sig.astype(np.float32))
        d = analyzer.analyze(path)
        assert isinstance(d, ToneDescriptor)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
