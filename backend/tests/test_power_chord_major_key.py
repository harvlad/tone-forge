"""Fix 1 — power-chord detection fires regardless of detected mode.

The chord-lane stage (``detect_chords_with_key``) originally gated
Stage 1.4.1 / 1.4.2 power-chord detection behind
``power_chord_minor_key_only=True``. That guard was calibrated for a
minor-key rock corpus, but the spectral signature (root+5th mass,
both 3rd bins low) is genre-neutral. On a song the key detector
locks onto a major mode (e.g. Linkin Park "One Step Closer" being
tagged C# Major), the gate silences the only detector that would
recognise the power chords, and full triads are emitted instead.

These tests pin the engine-level fix in place:

  1. ``_stage_config`` passed into ``detect_chords_from_audio`` has
     ``power_chord_minor_key_only=False`` and the tuned neighbouring
     defaults (``third_min_streak=2``, ``post_viterbi_margin=0.07``).
  2. A pure root+5th synthetic waveform (no 3rd) resolves to at
     least one ``*5`` region in the emitted chord stream, even when
     the surrounding harmonic context is set up to bias the key
     detector toward major.

Bench-corpus behaviour is unaffected because the bench eval uses the
default ``DetectorConfig`` (all power-chord levers zero/False); this
stage's tuned config is scoped to ``detect_chords_with_key`` only.
"""
from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pytest

from tone_forge.analysis import chords as chords_module
from tone_forge.analysis import detector_config as detector_config_module
from tone_forge.analysis.chords import detect_chords_with_key


SR = 22050


def _capture_stage_config(monkeypatch: pytest.MonkeyPatch) -> Dict[str, Any]:
    """Monkeypatch DetectorConfig to record the kwargs the chord-lane
    stage passes down; returns a dict populated on first construction."""
    captured: Dict[str, Any] = {}
    original = detector_config_module.DetectorConfig

    def spy(*args: Any, **kwargs: Any) -> Any:
        if not captured:
            captured.update(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(detector_config_module, "DetectorConfig", spy)
    return captured


def test_chord_lane_stage_config_lifts_minor_key_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The chord-lane stage no longer gates power-chord detection on
    the key being minor: ``power_chord_minor_key_only`` is False."""
    captured = _capture_stage_config(monkeypatch)

    # Any non-silent audio is fine — we only need the DetectorConfig
    # constructor to fire once. A short sine at the tonic C# is
    # enough; we don't inspect the returned chords here.
    n = int(1.0 * SR)
    audio = (np.sin(2 * np.pi * 138.59 * np.arange(n) / SR)
             * 0.3).astype(np.float32)
    detect_chords_with_key(audio, SR, min_chord_duration_s=0.5)

    assert captured, (
        "detect_chords_with_key did not construct a DetectorConfig — "
        "did the stage stop opting into the tuned config?"
    )
    assert captured.get("power_chord_minor_key_only") is False, (
        f"chord-lane stage still gates power-chord detection on minor "
        f"key: {captured.get('power_chord_minor_key_only')!r}"
    )


def test_chord_lane_stage_config_tuned_streak_and_margin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Neighbour knobs to Fix 1 move together: min-streak 2 (was 3),
    post-Viterbi margin 0.07 (was 0.05).

    Round-2 Fix 1 replaces the post-Viterbi third-bin magnitude gate
    with a spectral-shape ratio gate: the raw third-ratio is disabled
    (0.0) and ``power_chord_shape_ratio_min`` is set to 2.0 so the
    substitution decision rides on the geometric (root+5th)/(3rd+7th)
    shape signature — invariant under distorted-guitar overtone
    inflation — plus the raw-cosine margin.
    """
    captured = _capture_stage_config(monkeypatch)
    n = int(1.0 * SR)
    audio = (np.sin(2 * np.pi * 138.59 * np.arange(n) / SR)
             * 0.3).astype(np.float32)
    detect_chords_with_key(audio, SR, min_chord_duration_s=0.5)

    assert captured.get("power_chord_third_min_streak") == 2, (
        f"expected streak=2 (~1.0s at 0.5s windowing); "
        f"got {captured.get('power_chord_third_min_streak')!r}"
    )
    assert captured.get("power_chord_post_viterbi_margin") == pytest.approx(
        0.07
    ), (
        f"expected post-Viterbi margin=0.07; "
        f"got {captured.get('power_chord_post_viterbi_margin')!r}"
    )
    # Emission-side third-ratio unchanged from prior tuning.
    assert captured.get("power_chord_third_ratio") == pytest.approx(0.4)
    # Round-2 Fix 1: post-Viterbi third-ratio gate is now DISABLED;
    # the shape-ratio gate carries the substitution decision instead.
    assert captured.get("power_chord_post_viterbi_third_ratio") == pytest.approx(
        0.0
    ), (
        f"Round-2 Fix 1: expected post-Viterbi third-ratio DISABLED (0.0), "
        f"got {captured.get('power_chord_post_viterbi_third_ratio')!r}"
    )
    assert captured.get("power_chord_shape_ratio_min") == pytest.approx(2.0), (
        f"Round-2 Fix 1: expected shape_ratio_min=2.0 to gate substitution; "
        f"got {captured.get('power_chord_shape_ratio_min')!r}"
    )
    assert captured.get("power_chord_penalty") == pytest.approx(0.03)


def _make_power_chord_clip(
    root_hz: float,
    duration_s: float = 4.0,
    sr: int = SR,
) -> np.ndarray:
    """Synthesise a pure root+5th 'power chord' waveform.

    Sums the root sine + a perfect-5th sine (root * 3/2) with a mild
    saturating nonlinearity to add overtones the way an overdriven
    guitar would. Deliberately omits any 3rd content so the chroma
    matcher's third-absence gate can fire.
    """
    n = int(duration_s * sr)
    t = np.arange(n) / sr
    root = np.sin(2 * np.pi * root_hz * t)
    fifth = np.sin(2 * np.pi * (root_hz * 1.5) * t)
    mix = 0.5 * root + 0.5 * fifth
    # Soft-clip to add harmonic content while keeping the 3rd (of the
    # root) far weaker than the root/5th mass.
    saturated = np.tanh(2.0 * mix) * 0.6
    return saturated.astype(np.float32)


def test_power_chord_emerges_from_pure_root_fifth_signal() -> None:
    """A pure root+5th waveform (no 3rd) surfaces at least one ``*5``
    chord region through the chord-lane stage.

    Pre-Fix-1 this only worked when the key detector locked onto a
    minor mode. Post-Fix-1 the mode gate is off, so the detector
    fires regardless of the surrounding key context.
    """
    # A5 = 110 Hz + E5 = 165 Hz, sustained long enough to clear the
    # min-chord-duration floor and the streak gate.
    audio = _make_power_chord_clip(root_hz=110.0, duration_s=4.0)
    chords, _key = detect_chords_with_key(audio, SR, min_chord_duration_s=0.5)

    assert chords, "detector emitted no chord regions on a sustained tonal input"
    power_regions = [c for c in chords if c.symbol.endswith("5")]
    assert power_regions, (
        f"pure root+5th waveform yielded no power-chord regions; "
        f"got symbols: {[c.symbol for c in chords]!r}. Fix 1 mode gate "
        f"lift did not take effect."
    )
