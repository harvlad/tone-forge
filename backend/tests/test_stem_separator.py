"""Tests for tone_forge/stem_separator.py - Stem separation functions."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import numpy as np
import soundfile as sf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tone_forge.stem_separator import (
    is_available,
    _check_demucs,
    _get_torch_device,
    separate_guitar,
    separate_bass,
    separate_drums,
    separate_all_stems,
)

SR = 44100


def _make_test_audio(duration: float = 2.0) -> np.ndarray:
    """Generate test audio with multiple frequencies."""
    t = np.linspace(0, duration, int(SR * duration), endpoint=False)
    # Mix of frequencies to simulate a real audio file
    sig = np.zeros((len(t), 2))  # Stereo
    # Low frequency (bass-like)
    sig[:, 0] += 0.3 * np.sin(2 * np.pi * 60 * t)
    sig[:, 1] += 0.3 * np.sin(2 * np.pi * 60 * t)
    # Mid frequency (guitar-like)
    sig[:, 0] += 0.4 * np.sin(2 * np.pi * 440 * t)
    sig[:, 1] += 0.4 * np.sin(2 * np.pi * 440 * t)
    # High frequency (hihat-like noise)
    noise = np.random.randn(len(t)) * 0.1
    sig[:, 0] += noise
    sig[:, 1] += noise
    return sig.astype(np.float32)


class TestIsAvailable:
    """Test is_available function."""

    def test_is_available_returns_bool(self):
        result = is_available()
        assert isinstance(result, bool)


class TestCheckDemucs:
    """Test _check_demucs function."""

    def test_check_demucs_returns_bool(self):
        result = _check_demucs()
        assert isinstance(result, bool)

    def test_check_demucs_cached(self):
        # Second call should use cached value
        result1 = _check_demucs()
        result2 = _check_demucs()
        assert result1 == result2


class TestGetTorchDevice:
    """Test _get_torch_device function."""

    @pytest.mark.skipif(not is_available(), reason="Demucs not available")
    def test_get_torch_device_returns_device(self):
        import torch
        device = _get_torch_device()
        assert isinstance(device, torch.device)

    @pytest.mark.skipif(not is_available(), reason="Demucs not available")
    def test_get_torch_device_valid_type(self):
        import torch
        device = _get_torch_device()
        # Should be one of: cuda, mps, cpu
        assert device.type in ['cuda', 'mps', 'cpu']


class TestSeparateGuitarErrors:
    """Test error handling in separate_guitar."""

    def test_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            separate_guitar(tmp_path / "nonexistent.wav")

    @pytest.mark.skipif(is_available(), reason="Skip when Demucs is available")
    def test_import_error_when_not_available(self, tmp_path):
        # Create a test file
        audio = _make_test_audio()
        file_path = tmp_path / "test.wav"
        sf.write(str(file_path), audio, SR)

        # Should raise ImportError when Demucs is not installed
        with pytest.raises(ImportError, match="Demucs is not installed"):
            separate_guitar(str(file_path))


class TestSeparateBassErrors:
    """Test error handling in separate_bass."""

    def test_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            separate_bass(tmp_path / "nonexistent.wav")


class TestSeparateDrumsErrors:
    """Test error handling in separate_drums."""

    def test_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            separate_drums(tmp_path / "nonexistent.wav")


class TestSeparateAllStemsErrors:
    """Test error handling in separate_all_stems."""

    def test_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            separate_all_stems(tmp_path / "nonexistent.wav")


# Integration tests - only run if Demucs is available
@pytest.mark.skipif(not is_available(), reason="Demucs not available")
class TestStemSeparationIntegration:
    """Integration tests for stem separation (requires Demucs)."""

    def test_separate_guitar_creates_file(self, tmp_path):
        audio = _make_test_audio(duration=1.0)
        file_path = tmp_path / "test.wav"
        sf.write(str(file_path), audio, SR)

        output_path = separate_guitar(str(file_path), output_dir=tmp_path)
        assert output_path.exists()
        assert output_path.suffix == '.wav'

    def test_separate_bass_creates_file(self, tmp_path):
        audio = _make_test_audio(duration=1.0)
        file_path = tmp_path / "test.wav"
        sf.write(str(file_path), audio, SR)

        output_path = separate_bass(str(file_path), output_dir=tmp_path)
        assert output_path.exists()
        assert 'bass' in output_path.name

    def test_separate_drums_creates_file(self, tmp_path):
        audio = _make_test_audio(duration=1.0)
        file_path = tmp_path / "test.wav"
        sf.write(str(file_path), audio, SR)

        output_path = separate_drums(str(file_path), output_dir=tmp_path)
        assert output_path.exists()
        assert 'drums' in output_path.name

    def test_separate_all_stems_returns_dict(self, tmp_path):
        audio = _make_test_audio(duration=1.0)
        file_path = tmp_path / "test.wav"
        sf.write(str(file_path), audio, SR)

        result = separate_all_stems(str(file_path), output_dir=tmp_path)
        assert isinstance(result, dict)
        assert len(result) > 0
        # Should have standard stems
        assert 'drums' in result or 'bass' in result

    def test_separate_guitar_default_output_dir(self, tmp_path):
        audio = _make_test_audio(duration=1.0)
        file_path = tmp_path / "test.wav"
        sf.write(str(file_path), audio, SR)

        # Don't specify output_dir - should use temp dir
        output_path = separate_guitar(str(file_path))
        assert output_path.exists()

    def test_separate_guitar_mono_input(self, tmp_path):
        # Create mono audio
        t = np.linspace(0, 1.0, int(SR * 1.0), endpoint=False)
        audio_mono = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
        file_path = tmp_path / "mono.wav"
        sf.write(str(file_path), audio_mono, SR)

        output_path = separate_guitar(str(file_path), output_dir=tmp_path)
        assert output_path.exists()


@pytest.mark.skipif(not is_available(), reason="Demucs not available")
class TestOutputQuality:
    """Test output quality of separated stems."""

    def test_output_is_valid_audio(self, tmp_path):
        audio = _make_test_audio(duration=1.0)
        file_path = tmp_path / "test.wav"
        sf.write(str(file_path), audio, SR)

        output_path = separate_guitar(str(file_path), output_dir=tmp_path)

        # Read the output file
        output_audio, output_sr = sf.read(str(output_path))
        assert len(output_audio) > 0
        assert output_sr > 0
        # Should not contain NaN or Inf
        assert np.all(np.isfinite(output_audio))

    def test_output_sample_rate(self, tmp_path):
        audio = _make_test_audio(duration=1.0)
        file_path = tmp_path / "test.wav"
        sf.write(str(file_path), audio, SR)

        output_path = separate_guitar(str(file_path), output_dir=tmp_path)
        _, output_sr = sf.read(str(output_path))
        # Demucs outputs at 44.1kHz
        assert output_sr == 44100


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
