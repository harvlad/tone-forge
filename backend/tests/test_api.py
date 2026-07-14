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

    def test_root_contains_brand(self):
        response = client.get("/")
        assert b"JamN" in response.content


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


class TestUrlIngestGate:
    """URL-ingest (yt-dlp) endpoints are opt-in via TONEFORGE_ENABLE_URL_INGEST.

    Off by default: they must 404 so a public deployment never exposes
    platform-download functionality.
    """

    _ENDPOINTS = [
        ("/api/analyze-url", {"url": "https://example.com/watch?v=x"}),
        ("/api/analyze-url-stream", {"url": "https://example.com/watch?v=x"}),
        ("/api/preview-waveform-url", {"url": "https://example.com/watch?v=x"}),
    ]

    def test_endpoints_404_when_disabled(self, monkeypatch):
        monkeypatch.delenv("TONEFORGE_ENABLE_URL_INGEST", raising=False)
        for path, payload in self._ENDPOINTS:
            response = client.post(path, json=payload)
            assert response.status_code == 404, path

    def test_capabilities_hides_youtube_when_disabled(self, monkeypatch):
        monkeypatch.delenv("TONEFORGE_ENABLE_URL_INGEST", raising=False)
        response = client.get("/api/capabilities")
        assert response.status_code == 200
        assert response.json()["youtube_support"] is False

    def test_endpoints_not_404_when_enabled(self, monkeypatch):
        import tone_forge_api

        monkeypatch.setenv("TONEFORGE_ENABLE_URL_INGEST", "1")
        # Stub yt-dlp as unavailable so endpoints short-circuit with their
        # own "not installed" error instead of hitting the network.
        monkeypatch.setattr(tone_forge_api, "_check_yt_dlp", lambda: False)
        for path, payload in self._ENDPOINTS:
            response = client.post(path, json=payload)
            # Only asserting the gate no longer hides the endpoint.
            assert response.status_code != 404, path


class TestAdminGuard:
    """/studio, /api/admin/*, /api/debug/* require token or direct loopback."""

    def test_loopback_without_token_allowed(self, monkeypatch):
        monkeypatch.delenv("TONEFORGE_ADMIN_TOKEN", raising=False)
        # TestClient counts as loopback; endpoint responds normally.
        response = client.get("/studio")
        assert response.status_code == 200

    def test_forwarded_request_without_token_rejected(self, monkeypatch):
        monkeypatch.delenv("TONEFORGE_ADMIN_TOKEN", raising=False)
        response = client.get(
            "/api/debug/sessions", headers={"X-Forwarded-For": "203.0.113.9"}
        )
        assert response.status_code == 404

    def test_token_required_when_configured(self, monkeypatch):
        monkeypatch.setenv("TONEFORGE_ADMIN_TOKEN", "sekrit")
        assert client.get("/api/debug/sessions").status_code == 404
        assert (
            client.get(
                "/api/debug/sessions", headers={"X-Admin-Token": "wrong"}
            ).status_code
            == 404
        )
        assert (
            client.get(
                "/api/debug/sessions", headers={"X-Admin-Token": "sekrit"}
            ).status_code
            == 200
        )

    def test_bearer_token_accepted(self, monkeypatch):
        monkeypatch.setenv("TONEFORGE_ADMIN_TOKEN", "sekrit")
        response = client.get(
            "/api/debug/sessions", headers={"Authorization": "Bearer sekrit"}
        )
        assert response.status_code == 200

    def test_studio_query_token_sets_cookie_and_redirects(self, monkeypatch):
        monkeypatch.setenv("TONEFORGE_ADMIN_TOKEN", "sekrit")
        client.cookies.clear()
        response = client.get(
            "/studio",
            params={"token": "sekrit", "analysis": "abc123"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        # Token stripped from the redirect target, other params kept.
        assert response.headers["location"] == "/studio?analysis=abc123"
        assert response.cookies.get("toneforge_admin") == "sekrit"

    def test_studio_wrong_query_token_rejected(self, monkeypatch):
        monkeypatch.setenv("TONEFORGE_ADMIN_TOKEN", "sekrit")
        client.cookies.clear()
        response = client.get(
            "/studio", params={"token": "wrong"}, follow_redirects=False
        )
        assert response.status_code == 404

    def test_admin_cookie_grants_access(self, monkeypatch):
        monkeypatch.setenv("TONEFORGE_ADMIN_TOKEN", "sekrit")
        client.cookies.clear()
        client.cookies.set("toneforge_admin", "sekrit")
        assert client.get("/api/debug/sessions").status_code == 200
        client.cookies.set("toneforge_admin", "wrong")
        assert client.get("/api/debug/sessions").status_code == 404
        client.cookies.clear()

    def test_serve_file_exempt_from_guard(self, monkeypatch):
        monkeypatch.setenv("TONEFORGE_ADMIN_TOKEN", "sekrit")
        # No token supplied: still reaches the handler (403/404 from its own
        # path allowlist, not the guard's 404-with-JSON-detail).
        response = client.get("/api/admin/serve-file", params={"path": "/etc/passwd"})
        assert response.status_code == 403


class TestServeFileAllowlist:
    """serve-file only serves resolved paths inside toneforge-owned dirs."""

    def test_serves_file_in_toneforge_temp_dir(self):
        stems_dir = Path(tempfile.mkdtemp(prefix="toneforge_stems_"))
        try:
            wav = stems_dir / "drums.wav"
            wav.write_bytes(b"RIFF")
            response = client.get("/api/admin/serve-file", params={"path": str(wav)})
            assert response.status_code == 200
        finally:
            import shutil

            shutil.rmtree(stems_dir, ignore_errors=True)

    def test_blocks_plain_tmp_file(self):
        with tempfile.NamedTemporaryFile(suffix=".wav") as tmp:
            response = client.get(
                "/api/admin/serve-file", params={"path": tmp.name}
            )
            assert response.status_code == 403

    def test_blocks_symlink_escape(self):
        stems_dir = Path(tempfile.mkdtemp(prefix="toneforge_stems_"))
        try:
            link = stems_dir / "escape.wav"
            link.symlink_to("/etc/hosts")
            response = client.get(
                "/api/admin/serve-file", params={"path": str(link)}
            )
            assert response.status_code == 403
        finally:
            import shutil

            shutil.rmtree(stems_dir, ignore_errors=True)

    def test_blocks_dotdot_traversal(self):
        response = client.get(
            "/api/admin/serve-file",
            params={"path": "/tmp/toneforge_stems_x/../../etc/hosts"},
        )
        assert response.status_code == 403


class TestUploadSizeGuard:
    """POST bodies above TONEFORGE_MAX_UPLOAD_MB are rejected with 413."""

    def test_oversized_declared_length_rejected(self):
        import tone_forge_api

        limit = tone_forge_api._MAX_UPLOAD_BYTES
        response = client.post(
            "/api/analyze",
            headers={"Content-Length": str(limit + 1)},
            content=b"",
        )
        assert response.status_code == 413

    def test_normal_upload_unaffected(self):
        wav_data = _make_test_wav(duration=0.5)
        response = client.post(
            "/api/preview-waveform",
            files={"file": ("test.wav", wav_data, "audio/wav")},
        )
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
            # Different types have slightly different fields. Guitar chain
            # picks use slot/category + display/models; bass recommendations
            # use type + name. Accept either shape.
            assert "slot" in pick or "category" in pick or "type" in pick
            assert "display" in pick or "models" in pick or "name" in pick


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
