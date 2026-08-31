#!/usr/bin/env python3
"""Offline eval for the candidate adaptive bass-octave normalization.

Both blind listening packs agree riley_bass beats the incumbent but
disagree on the octave: synth bass (pack v1) wanted raw sounding pitch,
real electric bass (pack v2) leaned +12. A fixed shift cannot win both.
This script tests whether a runtime estimator — looking at the separated
stem's audio — can pick the register the blind listener picked, clip by
clip, BEFORE any route change.

Estimators (choose shift in {0, +12}):
  median_f0    pyin median f0 of the stem vs median transcribed pitch
  note_energy  per-note STFT energy at the fundamental of pitch vs
               pitch+12 over the note span; majority vote

Ground truth = the blind verdicts (clips where the incumbent won carry
no register preference and are scored separately as "no-pref").

Usage (from backend/):
    python3 scripts/register_match_eval.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SR = 44100

# clip dir -> blind-preferred shift (0 raw, 12 p12, None = incumbent won /
# no register preference recorded)
GROUND_TRUTH = {
    # pack v1 — synth-heavy samples/
    "lab_data/listening_pack_v1/03_-_Simulated_Life__bass": 0,
    "lab_data/listening_pack_v1/12_-_Mr_Dance__bass": 0,
    "lab_data/listening_pack_v1/15_-_Death_by_Nanobots__bass": 0,
    "lab_data/listening_pack_v1/17_-_Winter_Mode__bass": None,
    "lab_data/listening_pack_v1/23_-_Hello_AI_ft_Jason_Hanes__bass": 0,
    "lab_data/listening_pack_v1/24_-_Hello_AI_Ancestry__bass": 0,
    # pack v2 — real-band Cambridge
    "lab_data/listening_pack_v2/AngelsInAmplifiers_I__bass": 12,
    "lab_data/listening_pack_v2/BalkunBrothers_SoHiS__bass": 0,
    "lab_data/listening_pack_v2/BannedFromTheZoo_Bla__bass": None,
    "lab_data/listening_pack_v2/BigStoneCulture_Frag__bass": 12,
    "lab_data/listening_pack_v2/BosnianRainbows_Morn__bass": 12,
    "lab_data/listening_pack_v2/Candlebox_HappyPills__bass": 12,
    "lab_data/listening_pack_v2/Forkupines_Semantics__bass": None,
    "lab_data/listening_pack_v2/HollowGround_IllFate__bass": 0,
    "lab_data/listening_pack_v2/TheBrew_WhatIWant__bass": None,
}


def _load_mono(path: Path) -> np.ndarray:
    data, fsr = sf.read(str(path), always_2d=True)
    mono = data.mean(axis=1)
    if fsr != SR:
        mono = np.interp(np.linspace(0, len(mono) - 1,
                                     int(len(mono) * SR / fsr)),
                         np.arange(len(mono)), mono)
    return mono.astype(np.float32)


def riley_notes(stem_path: Path) -> list[dict]:
    from tone_forge.specialist.router import RoutingRequest, resolve
    from tone_forge.specialist.runner import _run_riley, warm_imports
    decision = resolve(RoutingRequest(engine="experimental_specialist",
                                      family="bass"))
    warm_imports(decision)
    return _run_riley(decision, stem_path)


def estimate_median_f0(audio: np.ndarray) -> float | None:
    import librosa
    f0 = librosa.pyin(audio, fmin=25.0, fmax=500.0, sr=SR,
                      frame_length=4096)[0]
    voiced = f0[np.isfinite(f0)]
    if len(voiced) < 10:
        return None
    return float(np.median(librosa.hz_to_midi(voiced)))


def choose_by_median_f0(notes: list[dict], audio: np.ndarray) -> int | None:
    med_f0 = estimate_median_f0(audio)
    if med_f0 is None or not notes:
        return None
    med_pitch = float(np.median([n["pitch"] for n in notes]))
    d0 = abs(med_f0 - med_pitch)
    d12 = abs(med_f0 - (med_pitch + 12))
    return 0 if d0 <= d12 else 12


def choose_by_note_energy(notes: list[dict], audio: np.ndarray) -> int | None:
    """Per note: does the stem carry more energy at f(pitch) or
    f(pitch+12) during the note span?  Majority vote across notes."""
    if not notes:
        return None
    n_fft = 8192
    hop = 2048
    spec = np.abs(np.fft.rfft(
        np.lib.stride_tricks.sliding_window_view(
            np.pad(audio, (0, n_fft)), n_fft)[::hop]
        * np.hanning(n_fft), axis=1))
    freqs = np.fft.rfftfreq(n_fft, 1 / SR)
    votes0 = votes12 = 0
    for n in notes:
        i0 = int(n["onset"] * SR / hop)
        i1 = max(i0 + 1, int(n["offset"] * SR / hop))
        if i0 >= len(spec):
            continue
        frame = spec[i0:min(i1, len(spec))].mean(axis=0)
        e = []
        for shift in (0, 12):
            f = 440.0 * 2 ** ((n["pitch"] + shift - 69) / 12)
            lo = np.searchsorted(freqs, f * 0.94)
            hi = np.searchsorted(freqs, f * 1.06)
            e.append(float(frame[lo:max(hi, lo + 1)].max()))
        if e[0] > e[1]:
            votes0 += 1
        elif e[1] > e[0]:
            votes12 += 1
    if votes0 + votes12 == 0:
        return None
    return 0 if votes0 >= votes12 else 12


def main() -> None:
    rows = []
    for clip, want in GROUND_TRUTH.items():
        stem = Path(clip) / "ref_separated.wav"
        if not stem.exists():
            print(f"MISSING {stem}")
            continue
        audio = _load_mono(stem)
        notes = riley_notes(stem)
        got_f0 = choose_by_median_f0(notes, audio)
        got_en = choose_by_note_energy(notes, audio)
        rows.append((clip.rsplit("/", 1)[-1], want, got_f0, got_en,
                     len(notes)))
        print(f"{clip.rsplit('/', 1)[-1]:<40} want={str(want):>4} "
              f"median_f0={got_f0} note_energy={got_en} n={len(notes)}",
              flush=True)

    for name, idx in (("median_f0", 2), ("note_energy", 3)):
        scored = [(r[1], r[idx]) for r in rows if r[1] is not None]
        hits = sum(1 for want, got in scored if got == want)
        nopref = [(r[0], r[idx]) for r in rows if r[1] is None]
        print(f"\n{name}: {hits}/{len(scored)} match blind preference; "
              f"no-pref clips chose: {dict(nopref)}")


if __name__ == "__main__":
    main()
