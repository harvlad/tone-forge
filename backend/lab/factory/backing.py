"""ScenarioProvider — supplies backing audio for a Scenario's roles.

Mirrors SourceProvider / TransformProvider: the VirtualStudio depends only on this
Protocol, never on how backing is produced. `SyntheticBackingProvider` generates
deterministic per-role audio (reproducible from a seed) so the Virtual Studio can be
validated end-to-end with no external stems and a clean, owned license. A future
`StemPoolBackingProvider` (drawing real backing Assets from the catalog) plugs in
with zero downstream change.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

import numpy as np

from .scenario import RoleSpec, Scenario

SR = 44100


@runtime_checkable
class ScenarioProvider(Protocol):
    id: str
    dataset_key: str          # -> training_data registry (license of the backing material)
    def backing_for(self, role: RoleSpec, scenario: Scenario, seed: int, n: int) -> np.ndarray: ...
    def health(self) -> bool: ...


def _rng(seed: int, role: str, scen_sig: str) -> np.random.Generator:
    # deterministic per (seed, role, scenario) -> reproducible backing
    mix = (seed * 2654435761) ^ (hash(role) & 0xFFFFFFFF) ^ (hash(scen_sig) & 0xFFFFFFFF)
    return np.random.default_rng(mix & 0xFFFFFFFF)


def _grid(n: int, sr: int, bpm: float) -> np.ndarray:
    step = int(sr * 60.0 / bpm)
    return np.arange(0, n, max(1, step))


class SyntheticBackingProvider:
    """Deterministic, owned, license-clean synthetic backing. NOT realistic music —
    its job is to validate the Virtual Studio's scenario/mix/provenance machinery
    deterministically. Realism arrives when a real stem pool provider is added."""
    id = "synthetic_backing"
    dataset_key = "synthetic_backing"

    def __init__(self, bpm: float = 100.0):
        self.bpm = bpm

    def health(self) -> bool:
        return True

    def backing_for(self, role: RoleSpec, scenario: Scenario, seed: int, n: int) -> np.ndarray:
        rng = _rng(seed, role.role + role.voice, scenario.signature())
        r = role.role
        if r == "drums":
            y = self._drums(n, rng)
        elif r == "percussion":
            y = self._brushes(n, rng)
        elif r == "bass":
            y = self._bass(n, rng, upright=(role.voice == "upright"))
        elif r in ("vocals",):
            y = self._voice(n, rng, scream=(role.voice == "scream"),
                            female=(role.voice == "female"))
        elif r in ("synth", "keys"):
            y = self._pad(n, rng, piano=(role.voice == "piano"))
        else:
            y = self._pad(n, rng)
        peak = float(np.max(np.abs(y)) + 1e-9)
        return (y / peak * 0.5).astype(np.float32)

    # ---- compact deterministic generators (mono) ----
    def _drums(self, n, rng):
        y = np.zeros(n)
        for i, g in enumerate(_grid(n, SR, self.bpm)):
            L = int(0.12 * SR)
            seg = np.zeros(min(L, n - g))
            t = np.arange(len(seg)) / SR
            if i % 2 == 0:               # kick: low thump
                seg += np.sin(2 * np.pi * 60 * t) * np.exp(-t / 0.05)
            else:                        # snare: mid noise burst
                seg += rng.standard_normal(len(seg)) * np.exp(-t / 0.06) * 0.6
            y[g:g + len(seg)] += seg
        return y

    def _brushes(self, n, rng):
        env = np.abs(np.sin(2 * np.pi * (self.bpm / 60) * np.arange(n) / SR))
        return rng.standard_normal(n) * (0.15 + 0.2 * env)

    def _bass(self, n, rng, upright=False):
        y = np.zeros(n)
        notes = [41.2, 55.0, 49.0, 61.7]  # E1 A1 G1 B1
        for i, g in enumerate(_grid(n, SR, self.bpm / 2)):
            f = notes[rng.integers(0, len(notes))]
            L = min(int(0.5 * SR), n - g)
            t = np.arange(L) / SR
            wave = np.sin(2 * np.pi * f * t)
            if not upright:
                wave += 0.3 * np.sin(2 * np.pi * 2 * f * t)  # brighter electric
            y[g:g + L] += wave * np.exp(-t / 0.4)
        return y

    def _voice(self, n, rng, scream=False, female=False):
        t = np.arange(n) / SR
        f0 = (330 if female else 180) * (1.4 if scream else 1.0)
        vib = 1 + 0.02 * np.sin(2 * np.pi * 5 * t)
        y = np.zeros(n)
        for k, amp in ((1, 1.0), (2, 0.5), (3, 0.3), (4, 0.15)):  # formant-ish stack
            y += amp * np.sin(2 * np.pi * k * f0 * vib * t)
        if scream:
            y += 0.4 * rng.standard_normal(n)     # rasp
        phrase = (np.sin(2 * np.pi * 0.25 * t) > -0.3).astype(float)  # on/off phrasing
        return y * phrase

    def _pad(self, n, rng, piano=False):
        t = np.arange(n) / SR
        chord = [220.0, 277.2, 329.6]  # A major-ish
        y = sum(np.sin(2 * np.pi * f * t) for f in chord)
        if piano:
            env = np.zeros(n)
            for g in _grid(n, SR, self.bpm):
                L = min(int(0.6 * SR), n - g)
                env[g:g + L] = np.maximum(env[g:g + L], np.exp(-np.arange(L) / SR / 0.4))
            y = y * env
        return np.asarray(y)


_p: ScenarioProvider = SyntheticBackingProvider()  # type: ignore[assignment]
