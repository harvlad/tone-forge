"""Chord-lane config invariants + power-chord detection.

Hardening 2026-07: the chord-lane stage (``detect_chords_with_key``)
previously opted into a tuned DetectorConfig (self_loop_bonus=0.03
plus power-chord emission/post-Viterbi levers). Ground-truth ablation
via ``scripts.analysis_eval`` showed that tuning LOSES up to 35
triad-relaxed WCSR points on corpus fixtures, so the stage was
reverted to the default config — the same one the bench regression
floors ratchet against.

These tests pin the new invariants:

  1. The stage constructs a *default* DetectorConfig (no kwarg
     overrides). Any future re-tuning must go through the corpus
     scoreboard first and update this test with measured numbers.
  2. Power-chord detection still works WITHOUT the removed levers:
     a pure root+5th waveform (no 3rd) resolves to at least one
     ``*5`` region via the built-in ``5`` chord template.
"""
from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pytest

from tone_forge.analysis import detector_config as detector_config_module
from tone_forge.analysis.chords import detect_chords_with_key


SR = 22050


def _capture_stage_config(monkeypatch: pytest.MonkeyPatch) -> Dict[str, Any]:
    """Monkeypatch DetectorConfig to record the kwargs the chord-lane
    stage passes down; returns a dict populated on first construction."""
    captured: Dict[str, Any] = {"called": False, "kwargs": None}
    original = detector_config_module.DetectorConfig

    def spy(*args: Any, **kwargs: Any) -> Any:
        if not captured["called"]:
            captured["called"] = True
            captured["kwargs"] = dict(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(detector_config_module, "DetectorConfig", spy)
    return captured


def test_chord_lane_stage_uses_default_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The chord-lane stage runs the bench-default DetectorConfig.

    No overrides: production scores exactly what the corpus floors
    gate. If this fails because someone re-added tuning, that tuning
    must first prove itself on ``python -m scripts.analysis_eval``.
    """
    captured = _capture_stage_config(monkeypatch)

    n = int(1.0 * SR)
    audio = (np.sin(2 * np.pi * 138.59 * np.arange(n) / SR)
             * 0.3).astype(np.float32)
    detect_chords_with_key(audio, SR, min_chord_duration_s=0.5)

    assert captured["called"], (
        "detect_chords_with_key did not construct a DetectorConfig"
    )
    assert captured["kwargs"] == {}, (
        f"chord-lane stage passed config overrides {captured['kwargs']!r}; "
        f"expected the bench-default DetectorConfig (no kwargs). "
        f"Re-tuning requires corpus-scoreboard evidence."
    )


def _make_power_chord_clip(
    root_hz: float,
    duration_s: float = 4.0,
    sr: int = SR,
) -> np.ndarray:
    """Synthesise a pure root+5th 'power chord' waveform.

    Sums the root sine + a perfect-5th sine (root * 3/2) with a mild
    saturating nonlinearity to add overtones the way an overdriven
    guitar would. Deliberately omits any 3rd content so the ``5``
    template can win on chroma shape alone.
    """
    n = int(duration_s * sr)
    t = np.arange(n) / sr
    root = np.sin(2 * np.pi * root_hz * t)
    fifth = np.sin(2 * np.pi * (root_hz * 1.5) * t)
    mix = 0.5 * root + 0.5 * fifth
    saturated = np.tanh(2.0 * mix) * 0.6
    return saturated.astype(np.float32)


def test_power_chord_emerges_from_pure_root_fifth_signal() -> None:
    """A pure root+5th waveform (no 3rd) surfaces at least one ``*5``
    chord region through the chord-lane stage — with the DEFAULT
    config, i.e. the ``5`` template alone carries detection without
    the removed power-chord priors.
    """
    # A5 = 110 Hz + E5 = 165 Hz, sustained long enough to clear the
    # min-chord-duration floor.
    audio = _make_power_chord_clip(root_hz=110.0, duration_s=4.0)
    chords, _key = detect_chords_with_key(audio, SR, min_chord_duration_s=0.5)

    assert chords, "detector emitted no chord regions on a sustained tonal input"
    power_regions = [c for c in chords if c.symbol.endswith("5")]
    assert power_regions, (
        f"pure root+5th waveform yielded no power-chord regions; "
        f"got symbols: {[c.symbol for c in chords]!r}"
    )
