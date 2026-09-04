"""Pins for tone_forge.monitor.tuner — per-song chain EQ derivation.

The tuner's promises: identity input produces zero correction, every
correction respects the clamp, the derived spec stays wire-compatible
(same parameter schema Connect's decoder reads), and failure modes
raise instead of emitting a chain tuned against a guess.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from tone_forge.monitor.tuner import (
    _EQ_BANDS,
    _HPF_CHOICES_HZ,
    _MAX_BAND_DELTA_DB,
    derive_tuned_chain,
)

_CHAINS = Path("tone_forge/monitor/chains")
_BASE = "tfc.edge_of_breakup"
_REFERENCE = _CHAINS / f"{_BASE}.wav"

pytestmark = pytest.mark.skipif(
    not _REFERENCE.is_file(),
    reason="bundled chain reference renders not present",
)


def _write_noise_stem(path: Path, seconds: float = 2.0, tilt: float = 0.0) -> Path:
    """Synth stem: white noise, optionally spectrally tilted.

    ``tilt`` > 0 brightens (first-difference), < 0 darkens (cumsum).
    Deterministic seed — test must not flake on noise draw.
    """
    rng = np.random.default_rng(20260904)
    x = rng.standard_normal(int(48_000 * seconds))
    if tilt > 0:
        x = np.diff(x, prepend=0.0)
    elif tilt < 0:
        x = np.cumsum(x)
    x = (x / (np.max(np.abs(x)) or 1.0) * 0.5).astype(np.float32)
    sf.write(str(path), x, 48_000)
    return path


def test_identity_produces_zero_deltas():
    result = derive_tuned_chain(_BASE, _REFERENCE)
    for name, _, _ in _EQ_BANDS:
        assert result.band_deltas_db[name] == pytest.approx(0.0, abs=1e-6)


def test_deltas_respect_clamp(tmp_path):
    stem = _write_noise_stem(tmp_path / "bright.wav", tilt=1.0)
    result = derive_tuned_chain(_BASE, stem)
    for delta in result.band_deltas_db.values():
        assert abs(delta) <= _MAX_BAND_DELTA_DB


def test_derived_spec_is_wire_compatible(tmp_path):
    stem = _write_noise_stem(tmp_path / "stem.wav")
    result = derive_tuned_chain(_BASE, stem)
    chain = result.chain
    assert chain.id == f"{_BASE}.tuned"
    assert chain.family.value == "edge_of_breakup"
    eq = chain.parameters["eq"]
    for name, _, _ in _EQ_BANDS:
        assert isinstance(eq[name], float)
    assert chain.parameters["input"]["high_pass_hz"] in _HPF_CHOICES_HZ
    # V2 contract: nonlinear knobs stay inside their DSP-legal ranges
    # and move at most halfway from the base value; output trim and the
    # gain-stage type are never touched.
    from tone_forge.monitor.loader import load_chain
    base = load_chain(_BASE)
    assert chain.parameters["output"] == base.parameters["output"]
    assert chain.parameters["gain_stage"]["type"] == base.parameters["gain_stage"]["type"]
    base_drive = float(base.parameters["gain_stage"]["drive"])
    drive = float(chain.parameters["gain_stage"]["drive"])
    assert 0.0 <= drive <= 1.0
    assert abs(drive - base_drive) <= 0.5 * max(base_drive, 1.0 - base_drive) + 1e-9
    mix = float(chain.parameters["reverb"]["mix"])
    assert 0.0 <= mix <= 0.5


def test_deterministic(tmp_path):
    stem = _write_noise_stem(tmp_path / "stem.wav")
    a = derive_tuned_chain(_BASE, stem)
    b = derive_tuned_chain(_BASE, stem)
    assert a.band_deltas_db == b.band_deltas_db
    assert a.chain == b.chain


def test_missing_reference_render_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        derive_tuned_chain(_BASE, _REFERENCE, chains_root=tmp_path)


def test_short_stem_raises(tmp_path):
    stem = _write_noise_stem(tmp_path / "short.wav", seconds=0.2)
    with pytest.raises(ValueError):
        derive_tuned_chain(_BASE, stem)
