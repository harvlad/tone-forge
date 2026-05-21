"""Tests for the ToneForge Studio local engine.

Tests the local engine server, build configuration, and integration.
"""
from __future__ import annotations

import io
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import numpy as np
import soundfile as sf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SR = 22050


def _make_test_wav(duration: float = 1.0, freq: float = 220) -> bytes:
    """Generate a simple test WAV file and return as bytes."""
    t = np.linspace(0, duration, int(SR * duration), endpoint=False)
    sig = 0.7 * np.sin(2 * np.pi * freq * t)
    buffer = io.BytesIO()
    sf.write(buffer, sig.astype(np.float32), SR, format="WAV")
    buffer.seek(0)
    return buffer.read()


class TestLocalEngineServer:
    """Tests for local_engine/server.py"""

    def test_server_imports(self):
        """Server module should import without errors."""
        from local_engine import server
        assert hasattr(server, 'app')
        assert hasattr(server, 'health')  # async def health()

    def test_health_endpoint(self):
        """Health endpoint should return status ok."""
        from local_engine.server import app
        from fastapi.testclient import TestClient

        client = TestClient(app)
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["service"] == "toneforge-local"

    def test_root_endpoint_info(self):
        """Root endpoint should return service info."""
        from local_engine.server import app
        from fastapi.testclient import TestClient

        client = TestClient(app)
        response = client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert data["service"] == "toneforge-local-engine"
        assert "version" in data
        assert "capabilities" in data
        assert "device" in data

    def test_capabilities_include_stem_separation(self):
        """Capabilities should include stem_separation."""
        from local_engine.server import app
        from fastapi.testclient import TestClient

        client = TestClient(app)
        response = client.get("/")
        data = response.json()
        assert "stem_separation" in data["capabilities"]

    def test_device_info_structure(self):
        """Device info should have expected structure."""
        from local_engine.server import app
        from fastapi.testclient import TestClient

        client = TestClient(app)
        response = client.get("/")
        device = response.json()["device"]
        assert "pytorch_version" in device
        assert "cuda_available" in device
        assert "mps_available" in device
        assert "device" in device
        assert "device_name" in device


class TestLocalEngineBuild:
    """Tests for local_engine/build.py"""

    def test_build_imports(self):
        """Build module should import without errors."""
        from local_engine import build
        assert hasattr(build, 'get_hidden_imports')
        assert hasattr(build, 'get_data_files')
        assert hasattr(build, 'APP_NAME')

    def test_hidden_imports_include_core_deps(self):
        """Hidden imports should include core dependencies."""
        from local_engine.build import get_hidden_imports

        imports = get_hidden_imports()
        assert "uvicorn" in imports
        assert "fastapi" in imports
        assert "torch" in imports
        assert "demucs" in imports
        assert "pystray" in imports

    def test_hidden_imports_include_tone_forge(self):
        """Hidden imports should include tone_forge modules."""
        from local_engine.build import get_hidden_imports

        imports = get_hidden_imports()
        assert "tone_forge" in imports
        assert "tone_forge.analyzer" in imports
        assert "tone_forge.stem_separator" in imports

    def test_app_name_defined(self):
        """APP_NAME should be defined."""
        from local_engine.build import APP_NAME
        assert APP_NAME == "ToneForge Local Engine"

    def test_version_defined(self):
        """VERSION should be defined."""
        from local_engine.build import VERSION
        assert VERSION == "0.1.0"


class TestLocalEngineTray:
    """Tests for local_engine/tray.py"""

    def test_tray_imports(self):
        """Tray module should import without errors."""
        from local_engine import tray
        assert hasattr(tray, 'main')
        assert hasattr(tray, 'start_server')
        assert hasattr(tray, 'stop_server')

    def test_tray_has_required_functions(self):
        """Tray module should have required functions."""
        from local_engine import tray

        assert hasattr(tray, 'start_server')
        assert hasattr(tray, 'stop_server')
        assert hasattr(tray, 'create_icon_image')
        assert hasattr(tray, 'create_menu')

    def test_autostart_functions_exist(self):
        """Auto-start functions should exist."""
        from local_engine import tray

        assert hasattr(tray, 'enable_autostart')
        assert hasattr(tray, 'disable_autostart')
        assert hasattr(tray, 'is_autostart_enabled')


class TestStemSeparatorMPS:
    """Tests for MPS GPU support in stem_separator.py"""

    def test_get_torch_device_function_exists(self):
        """_get_torch_device should exist."""
        from tone_forge.stem_separator import _get_torch_device
        assert callable(_get_torch_device)

    def test_get_torch_device_returns_valid_device(self):
        """_get_torch_device should return a valid device."""
        from tone_forge.stem_separator import _get_torch_device
        import torch

        device = _get_torch_device()
        assert isinstance(device, torch.device)
        assert device.type in ("cuda", "mps", "cpu")

    def test_device_priority_cuda_first(self):
        """CUDA should be preferred over MPS."""
        from tone_forge.stem_separator import _get_torch_device
        import torch

        # Mock CUDA available
        with patch.object(torch.cuda, 'is_available', return_value=True):
            device = _get_torch_device()
            assert device.type == "cuda"

    def test_device_fallback_to_cpu(self):
        """Should fallback to CPU when no GPU available."""
        from tone_forge.stem_separator import _get_torch_device
        import torch

        # Mock no GPU available
        with patch.object(torch.cuda, 'is_available', return_value=False):
            with patch.object(torch.backends.mps, 'is_available', return_value=False):
                device = _get_torch_device()
                assert device.type == "cpu"


class TestNumpyJSONSerialization:
    """Tests for numpy JSON serialization in tone_forge_api.py"""

    def test_numpy_encoder_handles_int64(self):
        """NumpyJSONEncoder should handle np.int64."""
        from tone_forge_api import NumpyJSONEncoder
        import json

        data = {"value": np.int64(42)}
        result = json.dumps(data, cls=NumpyJSONEncoder)
        assert result == '{"value": 42}'

    def test_numpy_encoder_handles_float64(self):
        """NumpyJSONEncoder should handle np.float64."""
        from tone_forge_api import NumpyJSONEncoder
        import json

        data = {"value": np.float64(3.14)}
        result = json.dumps(data, cls=NumpyJSONEncoder)
        assert '"value": 3.14' in result

    def test_numpy_encoder_handles_array(self):
        """NumpyJSONEncoder should handle np.ndarray."""
        from tone_forge_api import NumpyJSONEncoder
        import json

        data = {"values": np.array([1, 2, 3])}
        result = json.dumps(data, cls=NumpyJSONEncoder)
        assert result == '{"values": [1, 2, 3]}'

    def test_convert_numpy_types_recursive(self):
        """_convert_numpy_types should handle nested structures."""
        from tone_forge_api import _convert_numpy_types

        data = {
            "int": np.int64(42),
            "float": np.float64(3.14),
            "list": [np.int32(1), np.int32(2)],
            "nested": {"value": np.float32(1.5)},
        }
        result = _convert_numpy_types(data)

        assert isinstance(result["int"], int)
        assert isinstance(result["float"], float)
        assert all(isinstance(v, int) for v in result["list"])
        assert isinstance(result["nested"]["value"], float)


class TestLocalEngineDownload:
    """Tests for local engine download endpoint."""

    def test_download_endpoint_exists(self):
        """Download endpoint should exist."""
        from tone_forge_api import app
        from fastapi.testclient import TestClient

        client = TestClient(app)
        response = client.get("/api/local-engine/download")
        # Should return 200 (either file or info page)
        assert response.status_code == 200

    def test_download_returns_html_when_no_dmg(self):
        """Should return HTML info page when DMG doesn't exist."""
        from tone_forge_api import app
        from fastapi.testclient import TestClient

        # Temporarily move DMG if it exists
        dmg_path = Path(__file__).parent.parent / "dist" / "ToneForge-Studio.dmg"
        dmg_exists = dmg_path.exists()

        if dmg_exists:
            temp_path = dmg_path.with_suffix('.dmg.test_backup')
            dmg_path.rename(temp_path)

        try:
            client = TestClient(app)
            response = client.get("/api/local-engine/download")
            assert response.status_code == 200
            assert "text/html" in response.headers.get("content-type", "")
            assert "ToneForge Studio" in response.text
        finally:
            if dmg_exists:
                temp_path.rename(dmg_path)


class TestProcessingModeIndicator:
    """Tests for processing mode indicator in app.js (conceptual)."""

    def test_index_has_indicator_element(self):
        """index.html should have local-engine-indicator element."""
        index_path = Path(__file__).parent.parent / "static" / "index.html"
        content = index_path.read_text()
        assert 'id="local-engine-indicator"' in content

    def test_css_has_processing_mode_styles(self):
        """style.css should have processing mode styles."""
        css_path = Path(__file__).parent.parent / "static" / "style.css"
        content = css_path.read_text()
        assert ".processing-mode" in content
        assert ".processing-dot" in content
        assert ".processing-upgrade" in content

    def test_js_has_local_engine_detection(self):
        """app.js should have local engine detection."""
        js_path = Path(__file__).parent.parent / "static" / "app.js"
        content = js_path.read_text()
        assert "LOCAL_ENGINE_URL" in content
        assert "checkLocalEngine" in content
        assert "updateLocalEngineUI" in content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
