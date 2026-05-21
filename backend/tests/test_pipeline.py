"""End-to-end smoke test using a brief generated audio fixture.

The analyzer now actually loads audio (no more stubs), so we synthesize
a half-second tone in /tmp on each run rather than carrying a binary
sample around the repo.

Run from the backend/ directory:
    python -m pytest tests/ -v
or just:
    python tests/test_pipeline.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tone_forge import analyzer, helix_translator
from tone_forge.descriptor import ToneDescriptor


def _make_fixture() -> str:
    """Half-second 200Hz sine + 600Hz partial, just enough to exercise the pipeline."""
    sr = 22050
    t = np.linspace(0, 0.5, int(sr * 0.5), endpoint=False)
    sig = 0.6 * np.sin(2 * np.pi * 200 * t) + 0.2 * np.sin(2 * np.pi * 600 * t)
    path = Path(tempfile.gettempdir()) / "tone_forge_smoke.wav"
    sf.write(str(path), sig, sr)
    return str(path)


_FIXTURE = _make_fixture()


def test_pipeline_returns_descriptor():
    d = analyzer.analyze(_FIXTURE)
    assert isinstance(d, ToneDescriptor)
    assert d.version == "0.1.0"
    assert 0 <= d.amp.gain <= 1
    assert 0 <= d.confidence.amp_family <= 1


def test_helix_translation_has_amp_and_cab():
    d = analyzer.analyze(_FIXTURE)
    card = helix_translator.translate(d)
    slots = {p.slot for p in card.picks}
    assert "amp" in slots
    assert "cab" in slots


def test_descriptor_serializes_to_dict():
    d = analyzer.analyze(_FIXTURE)
    j = d.to_dict()
    assert j["amp"]["family"] in (
        "fender_clean", "vox_chime", "marshall_plexi", "marshall_jcm",
        "mesa_rectifier", "5150_peavey", "bogner", "soldano", "ac30",
        "tweed", "dumble", "unknown",
    )


if __name__ == "__main__":
    test_pipeline_returns_descriptor()
    test_helix_translation_has_amp_and_cab()
    test_descriptor_serializes_to_dict()
    print("all tests passed ✓")
