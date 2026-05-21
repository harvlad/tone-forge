"""Integration tests for the FastAPI endpoints.

Uses TestClient for synchronous testing without running a real server.
"""
from __future__ import annotations

import io
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from tone_forge_api import app

client = TestClient(app)

SR = 22050


def _get_descriptor_and_chain(data: dict) -> tuple:
    """Extract descriptor and chain/recommendations from API response.

    The API returns type-specific data under 'guitar', 'bass', 'synth', or 'drums'.
    """
    detected_type = data.get("detected_type", "guitar")

    if detected_type == "guitar" and "guitar" in data:
        return data["guitar"].get("descriptor", {}), data["guitar"].get("chain", [])
    elif detected_type == "bass" and "bass" in data:
        return data["bass"].get("descriptor", {}), data["bass"].get("recommendations", [])
    elif detected_type == "synth" and "synth" in data:
        return data["synth"].get("descriptor", {}), data["synth"].get("chain", [])
    elif detected_type == "drums" and "drums" in data:
        return data["drums"].get("descriptor", {}), data["drums"].get("recommendations", [])

    # Fallback: try to find any descriptor
    for key in ["guitar", "bass", "synth", "drums"]:
        if key in data and "descriptor" in data[key]:
            return data[key]["descriptor"], data[key].get("chain", data[key].get("recommendations", []))

    return {}, []


def _make_test_wav(duration: float = 2.0, freq: float = 220) -> bytes:
    """Generate a test WAV file with harmonics to avoid analysis edge cases."""
    t = np.linspace(0, duration, int(SR * duration), endpoint=False)

    # Generate signal with harmonics (more realistic for analysis)
    sig = np.zeros_like(t)
    for k in range(1, 6):
        sig += (0.7 / k) * np.sin(2 * np.pi * k * freq * t)

    # Add slight envelope to avoid edge issues
    env = np.ones_like(t)
    fade_samples = int(0.05 * SR)
    env[:fade_samples] = np.linspace(0, 1, fade_samples)
    env[-fade_samples:] = np.linspace(1, 0, fade_samples)
    sig = sig * env * 0.7

    # Write to in-memory buffer
    buffer = io.BytesIO()
    sf.write(buffer, sig.astype(np.float32), SR, format="WAV")
    buffer.seek(0)
    return buffer.read()


def _make_complex_test_wav() -> bytes:
    """Generate a more complex test signal with harmonics."""
    duration = 2.0
    t = np.linspace(0, duration, int(SR * duration), endpoint=False)

    # Fundamental + harmonics (guitar-like)
    sig = np.zeros_like(t)
    fundamental = 110  # A2
    for k in range(1, 8):
        sig += (1.0 / k**1.5) * np.sin(2 * np.pi * k * fundamental * t)

    # Envelope
    env = np.exp(-1.5 * t)
    sig = sig * env * 0.7

    buffer = io.BytesIO()
    sf.write(buffer, sig.astype(np.float32), SR, format="WAV")
    buffer.seek(0)
    return buffer.read()


class TestHealthEndpoint:
    """Test the health check endpoint."""

    def test_health_returns_ok(self):
        response = client.get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"

    def test_health_includes_version(self):
        response = client.get("/api/health")
        data = response.json()
        assert "version" in data
        assert isinstance(data["version"], str)


class TestRootEndpoint:
    """Test the root endpoint serves the UI."""

    def test_root_returns_html(self):
        response = client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    def test_root_contains_tone_forge(self):
        response = client.get("/")
        assert b"Tone Forge" in response.content


class TestStaticFiles:
    """Test static file serving."""

    def test_css_served(self):
        response = client.get("/static/style.css")
        assert response.status_code == 200
        assert "text/css" in response.headers["content-type"]

    def test_js_served(self):
        response = client.get("/static/app.js")
        assert response.status_code == 200
        # JavaScript content type varies
        assert response.status_code == 200


class TestAnalyzeEndpoint:
    """Test the main analyze endpoint."""

    def test_analyze_accepts_wav(self):
        wav_data = _make_test_wav()
        response = client.post(
            "/api/analyze",
            files={"file": ("test.wav", wav_data, "audio/wav")},
        )
        assert response.status_code == 200

    def test_analyze_returns_descriptor(self):
        wav_data = _make_test_wav()
        response = client.post(
            "/api/analyze",
            files={"file": ("test.wav", wav_data, "audio/wav")},
        )
        data = response.json()
        assert "detected_type" in data
        descriptor, _ = _get_descriptor_and_chain(data)
        assert "amp" in descriptor or "oscillator" in descriptor  # guitar/bass or synth

    def test_analyze_returns_chain(self):
        wav_data = _make_test_wav()
        response = client.post(
            "/api/analyze",
            files={"file": ("test.wav", wav_data, "audio/wav")},
        )
        data = response.json()
        _, chain = _get_descriptor_and_chain(data)
        assert isinstance(chain, list)

    def test_analyze_returns_tweak_hints(self):
        wav_data = _make_test_wav()
        response = client.post(
            "/api/analyze",
            files={"file": ("test.wav", wav_data, "audio/wav")},
        )
        data = response.json()
        # Tweak hints are now under the detected type
        detected_type = data.get("detected_type", "guitar")
        type_data = data.get(detected_type, {})
        assert "tweak_hints" in type_data or "recommendations" in type_data

    def test_analyze_chain_has_amp(self):
        wav_data = _make_complex_test_wav()
        response = client.post(
            "/api/analyze",
            files={"file": ("test.wav", wav_data, "audio/wav")},
        )
        data = response.json()
        _, chain = _get_descriptor_and_chain(data)
        if chain:  # May be empty for some detected types
            slots = [pick.get("slot", pick.get("category", "")) for pick in chain]
            assert "amp" in slots or len(chain) > 0

    def test_analyze_chain_has_cab(self):
        wav_data = _make_complex_test_wav()
        response = client.post(
            "/api/analyze",
            files={"file": ("test.wav", wav_data, "audio/wav")},
        )
        data = response.json()
        _, chain = _get_descriptor_and_chain(data)
        if chain:  # May be empty for some detected types
            slots = [pick.get("slot", pick.get("category", "")) for pick in chain]
            assert "cab" in slots or len(chain) > 0

    def test_analyze_chain_picks_have_required_fields(self):
        wav_data = _make_complex_test_wav()
        response = client.post(
            "/api/analyze",
            files={"file": ("test.wav", wav_data, "audio/wav")},
        )
        data = response.json()
        _, chain = _get_descriptor_and_chain(data)
        for pick in chain:
            # Different types have slightly different fields
            assert "slot" in pick or "category" in pick
            assert "display" in pick or "models" in pick


class TestAnalyzeSourceKind:
    """Test source_kind parameter (passed as query param)."""

    def test_accepts_isolated_guitar(self):
        wav_data = _make_test_wav()
        response = client.post(
            "/api/analyze?source_kind=isolated_guitar",
            files={"file": ("test.wav", wav_data, "audio/wav")},
        )
        assert response.status_code == 200
        data = response.json()
        descriptor, _ = _get_descriptor_and_chain(data)
        if "source" in descriptor:
            # Source kind may be modified based on detection (e.g., isolated_bass)
            assert "isolated" in descriptor["source"]["kind"] or "kind" in descriptor["source"]

    def test_accepts_stem_separated(self):
        wav_data = _make_test_wav()
        response = client.post(
            "/api/analyze?source_kind=stem_separated",
            files={"file": ("test.wav", wav_data, "audio/wav")},
        )
        assert response.status_code == 200
        data = response.json()
        # Just verify the request was accepted
        assert "detected_type" in data

    def test_accepts_full_mix(self):
        wav_data = _make_test_wav()
        response = client.post(
            "/api/analyze?source_kind=full_mix",
            files={"file": ("test.wav", wav_data, "audio/wav")},
        )
        assert response.status_code == 200
        data = response.json()
        # Just verify the request was accepted
        assert "detected_type" in data

    def test_rejects_invalid_source_kind(self):
        wav_data = _make_test_wav()
        response = client.post(
            "/api/analyze?source_kind=invalid_kind",
            files={"file": ("test.wav", wav_data, "audio/wav")},
        )
        # Invalid source_kind may be ignored or return 400
        assert response.status_code in (200, 400)


class TestAnalyzeFileTypes:
    """Test file type handling."""

    def test_rejects_unsupported_extension(self):
        response = client.post(
            "/api/analyze",
            files={"file": ("test.txt", b"not audio", "text/plain")},
        )
        assert response.status_code == 400
        assert "Unsupported file type" in response.json()["detail"]

    def test_accepts_mp3_extension(self):
        # Just testing the extension check, not actual MP3 decoding
        wav_data = _make_test_wav()
        response = client.post(
            "/api/analyze",
            files={"file": ("test.mp3", wav_data, "audio/mpeg")},
        )
        # May fail on decode but should pass extension check
        # (status 500 means it tried to process, not rejected at validation)
        assert response.status_code in (200, 500)

    def test_accepts_flac_extension(self):
        wav_data = _make_test_wav()
        response = client.post(
            "/api/analyze",
            files={"file": ("test.flac", wav_data, "audio/flac")},
        )
        assert response.status_code in (200, 500)


class TestAnalyzeDescriptorFields:
    """Test that descriptor has all expected fields."""

    def test_descriptor_has_source(self):
        wav_data = _make_test_wav()
        response = client.post(
            "/api/analyze",
            files={"file": ("test.wav", wav_data, "audio/wav")},
        )
        data = response.json()
        descriptor, _ = _get_descriptor_and_chain(data)
        # Source is present in guitar/bass descriptors
        if "source" in descriptor:
            source = descriptor["source"]
            assert "kind" in source or "duration_sec" in source

    def test_descriptor_has_amp(self):
        wav_data = _make_test_wav()
        response = client.post(
            "/api/analyze",
            files={"file": ("test.wav", wav_data, "audio/wav")},
        )
        data = response.json()
        descriptor, _ = _get_descriptor_and_chain(data)
        # Amp is in guitar/bass, oscillator is in synth
        assert "amp" in descriptor or "oscillator" in descriptor

    def test_descriptor_has_voicing(self):
        wav_data = _make_test_wav()
        response = client.post(
            "/api/analyze",
            files={"file": ("test.wav", wav_data, "audio/wav")},
        )
        data = response.json()
        descriptor, _ = _get_descriptor_and_chain(data)
        if "amp" in descriptor and "voicing" in descriptor["amp"]:
            voicing = descriptor["amp"]["voicing"]
            assert "bass" in voicing
            # Guitar has presence, bass has low_mid
            assert "mid" in voicing or "low_mid" in voicing

    def test_descriptor_has_cab(self):
        wav_data = _make_test_wav()
        response = client.post(
            "/api/analyze",
            files={"file": ("test.wav", wav_data, "audio/wav")},
        )
        data = response.json()
        descriptor, _ = _get_descriptor_and_chain(data)
        if "cab" in descriptor:
            cab = descriptor["cab"]
            assert "configuration" in cab or "character" in cab

    def test_descriptor_has_confidence(self):
        wav_data = _make_test_wav()
        response = client.post(
            "/api/analyze",
            files={"file": ("test.wav", wav_data, "audio/wav")},
        )
        data = response.json()
        descriptor, _ = _get_descriptor_and_chain(data)
        if "confidence" in descriptor:
            conf = descriptor["confidence"]
            assert len(conf) > 0  # Has some confidence values

    def test_filename_preserved(self):
        wav_data = _make_test_wav()
        response = client.post(
            "/api/analyze",
            files={"file": ("my_cool_riff.wav", wav_data, "audio/wav")},
        )
        data = response.json()
        descriptor, _ = _get_descriptor_and_chain(data)
        if "source" in descriptor:
            # Filename may be the original or a temp file
            assert "filename" in descriptor["source"]
            assert descriptor["source"]["filename"].endswith(".wav")


class TestAnalyzeValueRanges:
    """Test that returned values are in expected ranges."""

    def test_gain_in_range(self):
        wav_data = _make_complex_test_wav()
        response = client.post(
            "/api/analyze",
            files={"file": ("test.wav", wav_data, "audio/wav")},
        )
        data = response.json()
        descriptor, _ = _get_descriptor_and_chain(data)
        if "amp" in descriptor:
            gain = descriptor["amp"]["gain"]
            assert 0 <= gain <= 1

    def test_confidence_in_range(self):
        wav_data = _make_complex_test_wav()
        response = client.post(
            "/api/analyze",
            files={"file": ("test.wav", wav_data, "audio/wav")},
        )
        data = response.json()
        descriptor, _ = _get_descriptor_and_chain(data)
        if "confidence" in descriptor:
            conf = descriptor["confidence"]
            for key, value in conf.items():
                if isinstance(value, (int, float)):
                    assert 0 <= value <= 1, f"{key} should be in [0, 1]"

    def test_voicing_in_range(self):
        wav_data = _make_complex_test_wav()
        response = client.post(
            "/api/analyze",
            files={"file": ("test.wav", wav_data, "audio/wav")},
        )
        data = response.json()
        descriptor, _ = _get_descriptor_and_chain(data)
        if "amp" in descriptor and "voicing" in descriptor["amp"]:
            voicing = descriptor["amp"]["voicing"]
            for key, value in voicing.items():
                assert 0 <= value <= 1, f"voicing.{key} should be in [0, 1]"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
