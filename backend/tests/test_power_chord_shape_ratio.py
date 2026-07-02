"""Round-2 Fix 1 — Spectral-shape ratio power-chord substitution.

Pins the geometric-signature gate that replaces the raw third-bin
magnitude test. The new criterion:

    shape_ratio = (root_bin + fifth_bin) / (third_bin + seventh_bin + eps)

fires substitution when shape_ratio >= shape_ratio_min. A distorted
power chord's root+5th dominate; the 3rd and 7th only carry
intermodulation residue. A real triad has all four bins comparable.
The ratio is scale-free — invariant under harmonic-distortion
overtone inflation because both numerator and denominator inflate
together under a diatonic tone stack.
"""
from __future__ import annotations

import numpy as np

from tone_forge.analysis.chord_detector import (
    Chord,
    _substitute_power_chords_on_dyads,
)


def _region_chroma(root_pc: int, *, third: float, fifth: float, seventh: float,
                   root_mass: float = 1.0) -> np.ndarray:
    """Build a single-column chroma with configurable per-bin mass."""
    chroma = np.zeros((12, 1), dtype=np.float32)
    chroma[root_pc, 0] = root_mass
    chroma[(root_pc + 4) % 12, 0] = third   # major-third bin
    chroma[(root_pc + 7) % 12, 0] = fifth   # perfect-fifth bin
    chroma[(root_pc + 10) % 12, 0] = seventh  # minor-seventh bin
    return chroma


def test_shape_ratio_gate_substitutes_root_plus_fifth():
    """Root+5th dominant, 3rd/7th near-zero → shape_ratio ~ huge →
    substitution fires; region rewrites to ``quality='5'``."""
    # Root=C (0), fifth strong, third+seventh near zero.
    chroma = _region_chroma(root_pc=0, third=0.02, fifth=0.9, seventh=0.02)
    times = np.array([0.0], dtype=np.float64)
    chords = [Chord(root=0, quality='maj', start_time=0.0, end_time=0.5,
                    confidence=0.5)]

    # Disable the raw-ratio gate (0.0). Enable ONLY the shape gate.
    out = _substitute_power_chords_on_dyads(
        chords, chroma, times,
        third_ratio_max=0.0, margin=0.5, shape_ratio_min=2.0,
    )
    assert out[0].quality == '5', (
        f"expected shape-ratio gate to substitute quality='5'; got "
        f"{out[0].quality!r}"
    )


def test_shape_ratio_gate_preserves_real_triad():
    """All four diatonic bins carry comparable mass → shape_ratio ~ 1.0
    → substitution suppressed; region keeps its ``quality='maj'``."""
    # Root=C (0). All four bins ~0.5.
    chroma = _region_chroma(root_pc=0, third=0.5, fifth=0.6, seventh=0.5,
                            root_mass=0.8)
    times = np.array([0.0], dtype=np.float64)
    chords = [Chord(root=0, quality='maj', start_time=0.0, end_time=0.5,
                    confidence=0.5)]

    out = _substitute_power_chords_on_dyads(
        chords, chroma, times,
        third_ratio_max=0.0, margin=0.5, shape_ratio_min=2.0,
    )
    # shape_ratio = (0.8 + 0.6) / (0.5 + 0.5 + eps) ~ 1.4 < 2.0 → keep.
    assert out[0].quality == 'maj', (
        f"expected triad to survive the shape gate; got {out[0].quality!r}"
    )


def test_shape_ratio_gate_disabled_by_default():
    """When shape_ratio_min=0.0 the gate is a no-op; only the legacy
    raw-third-ratio gate can trigger substitution. Preserves bench-
    bit-exact contract on default DetectorConfig()."""
    # A signal that WOULD trigger the shape gate if it were on...
    chroma = _region_chroma(root_pc=0, third=0.02, fifth=0.9, seventh=0.02)
    times = np.array([0.0], dtype=np.float64)
    chords = [Chord(root=0, quality='maj', start_time=0.0, end_time=0.5,
                    confidence=0.5)]

    # ...but with both gates disabled the function is a no-op.
    out = _substitute_power_chords_on_dyads(
        chords, chroma, times,
        third_ratio_max=0.0, margin=0.5, shape_ratio_min=0.0,
    )
    assert out[0].quality == 'maj', (
        "expected no-op when both gates are disabled (default "
        "DetectorConfig contract)"
    )


def test_shape_ratio_invariant_under_overtone_inflation():
    """Distorted-guitar overtone inflation lifts root+5th AND 3rd+7th
    proportionally. The shape ratio stays the same → gate decision
    unchanged. This is the geometric invariance argument that makes
    the shape ratio genre-neutral."""
    # Baseline power chord: root+5th=0.9, 3rd+7th=0.04 → shape ~22.
    baseline = _region_chroma(root_pc=0, third=0.02, fifth=0.9, seventh=0.02,
                              root_mass=1.0)
    # Overtone-inflated: everything scaled by 3x. Ratio unchanged.
    inflated = _region_chroma(root_pc=0, third=0.06, fifth=2.7, seventh=0.06,
                              root_mass=3.0)
    times = np.array([0.0], dtype=np.float64)

    def _quality_after(chroma):
        chords = [Chord(root=0, quality='maj', start_time=0.0, end_time=0.5,
                        confidence=0.5)]
        return _substitute_power_chords_on_dyads(
            chords, chroma, times,
            third_ratio_max=0.0, margin=0.5, shape_ratio_min=2.0,
        )[0].quality

    assert _quality_after(baseline) == '5'
    assert _quality_after(inflated) == '5', (
        "shape ratio should be invariant under proportional overtone "
        "inflation; substitution decision should not change"
    )
