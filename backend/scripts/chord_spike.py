"""Chord detection spike runner.

Validates that the existing
``tone_forge.chord_detector.detect_chords_from_audio`` is good enough
to populate ``SongUnderstanding.chords`` for the Jam MVP.

Spike scope (per ``/EXECUTION_PLAN.md`` Priority 4):
    - Five guitar-centric progressions, all in C major.
    - Synthesized as additive-harmonic strums with quick-attack envelopes.
      Not realistic guitar, but contains the right pitch-class content
      for chroma-based detection — a fair test of the detector's job.
    - Three metrics per clip: strict (root + exact quality template),
      triad-relaxed (root + maj/min/dim/aug family), root-only.

Decision rule (proposed):
    - root-only >= 90% AND triad-relaxed >= 75% → existing detector is
      good enough; wire it into analysis/chords.py and move on.
    - root-only >= 90% but triad-relaxed < 75% → root tracking works,
      quality classification doesn't. Worth fixing — but a quality-only
      bug fix, not a research project.
    - root-only < 90% → detector is not useful for Jam. Evaluate an
      alternative or punt the chord lane.

Run:
    cd backend && python3 scripts/chord_spike.py
"""
from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np

# Make backend/ importable when invoked from anywhere.
HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent
sys.path.insert(0, str(BACKEND))

from tone_forge.chord_detector import (  # noqa: E402
    NOTE_NAMES,
    Chord,
    detect_chords_from_audio,
)

# Quiet the chord_detector's own logging during the run.
logging.basicConfig(level=logging.WARNING)

SR = 22050

# ---------------------------------------------------------------------------
# Progressions (in C major; voicings approximate a strummed open chord)
# ---------------------------------------------------------------------------

# Pitch classes for chord roots
C, D, E, F, G, A, B = 0, 2, 4, 5, 7, 9, 11

# Quality template keys (must match CHORD_TEMPLATES in chord_detector.py)
MAJ = "maj"
MIN = "min"
DOM7 = "dom7"

# Triad family for the triad-relaxed metric
TRIAD_OF: Dict[str, str] = {
    "maj": "maj", "min": "min", "dim": "dim", "aug": "aug",
    "maj7": "maj", "min7": "min", "dom7": "maj", "dim7": "dim",
    "sus2": "maj", "sus4": "maj", "add9": "maj",
    "min9": "min", "maj9": "maj",
}

# Interval sets for synthesis (must mirror chord_detector.CHORD_TEMPLATES)
INTERVALS: Dict[str, List[int]] = {
    "maj":  [0, 4, 7],
    "min":  [0, 3, 7],
    "dom7": [0, 4, 7, 10],
}


def voiced(root_pc: int, quality_key: str) -> List[int]:
    """Bass note (~C2) + body voicing (~C4-up) — approximates an open-chord strum."""
    intervals = INTERVALS[quality_key]
    bass = 36 + root_pc  # C2 = 36
    body = [60 + (root_pc + i) % 12 for i in intervals]  # C4 = 60
    return [bass] + body


@dataclass(frozen=True)
class TruthChord:
    start_s: float
    end_s: float
    root_pc: int
    quality_key: str

    @property
    def symbol(self) -> str:
        return Chord(self.root_pc, self.quality_key, 0, 0, 1.0).name


# Each progression: (label, beats_per_chord, tempo_bpm,
#                    [(root_pc, quality_key), ...])
PROGRESSIONS: List[Tuple[str, int, float, List[Tuple[int, str]]]] = [
    ("I-V-vi-IV (pop)",        4, 120, [(C, MAJ), (G, MAJ), (A, MIN), (F, MAJ)]),
    ("I-vi-IV-V (50s)",        4, 110, [(C, MAJ), (A, MIN), (F, MAJ), (G, MAJ)]),
    ("vi-IV-I-V (anthem)",     4, 100, [(A, MIN), (F, MAJ), (C, MAJ), (G, MAJ)]),
    ("I-IV-V7-I",              4,  90, [(C, MAJ), (F, MAJ), (G, DOM7), (C, MAJ)]),
    ("ii-V-I (jazz cadence)",  4, 130, [(D, MIN), (G, MAJ), (C, MAJ), (C, MAJ)]),
]


# ---------------------------------------------------------------------------
# Synthesis
# ---------------------------------------------------------------------------

def synth_strum(midi_notes: List[int], duration_s: float, sr: int = SR) -> np.ndarray:
    """Additive-harmonic strum approximation.

    Stagger each note by ~10 ms (strum), sum fundamental + harmonics 2..4
    with decreasing amplitude, apply a 5 ms attack / 100 ms release env.
    """
    n = int(sr * duration_s)
    t = np.arange(n) / sr
    audio = np.zeros(n, dtype=np.float32)
    for idx, midi in enumerate(midi_notes):
        freq = 440.0 * 2.0 ** ((midi - 69) / 12.0)
        offset = int(idx * 0.010 * sr)
        if offset >= n:
            continue
        local_t = t[offset:] - offset / sr
        sig = np.zeros_like(local_t)
        for harmonic, amp in [(1, 1.0), (2, 0.4), (3, 0.2), (4, 0.1)]:
            sig += amp * np.sin(2 * np.pi * freq * harmonic * local_t)
        # Envelope: 5 ms attack, 100 ms release
        env = np.minimum(local_t * 200.0, 1.0)
        env = np.minimum(env, (duration_s - local_t) * 10.0)
        env = np.clip(env, 0.0, 1.0)
        sig *= env
        audio[offset:offset + len(sig)] += sig.astype(np.float32)
    peak = float(np.max(np.abs(audio))) or 1.0
    return (audio / peak * 0.7).astype(np.float32)


def build_clip(
    progression: List[Tuple[int, str]],
    beats_per_chord: int,
    tempo_bpm: float,
    sr: int = SR,
) -> Tuple[np.ndarray, List[TruthChord]]:
    beat_s = 60.0 / tempo_bpm
    chord_dur = beat_s * beats_per_chord
    chunks: List[np.ndarray] = []
    truth: List[TruthChord] = []
    t = 0.0
    for root_pc, quality_key in progression:
        midi = voiced(root_pc, quality_key)
        chunks.append(synth_strum(midi, chord_dur, sr))
        truth.append(TruthChord(t, t + chord_dur, root_pc, quality_key))
        t += chord_dur
    return np.concatenate(chunks), truth


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def truth_at(truth: List[TruthChord], time_s: float) -> TruthChord | None:
    for tc in truth:
        if tc.start_s <= time_s < tc.end_s:
            return tc
    return None


def detected_at(detected: List[Chord], time_s: float) -> Chord | None:
    for d in detected:
        if d.start_time <= time_s < d.end_time:
            return d
    return None


def score_clip(detected: List[Chord], truth: List[TruthChord],
               total_s: float, frame_hop_s: float = 0.05
               ) -> Dict[str, float]:
    """Frame-level chord-match accuracy at three strictness levels."""
    times = np.arange(0.0, total_s, frame_hop_s)
    strict = relaxed = root = denom = 0
    for ts in times:
        tc = truth_at(truth, float(ts))
        dc = detected_at(detected, float(ts))
        if tc is None:
            continue
        denom += 1
        if dc is None:
            continue
        # Root match
        if dc.root == tc.root_pc:
            root += 1
            # Triad-relaxed: same root + same triad family
            d_triad = TRIAD_OF.get(dc.quality, dc.quality)
            t_triad = TRIAD_OF.get(tc.quality_key, tc.quality_key)
            if d_triad == t_triad:
                relaxed += 1
                # Strict: same root + same exact quality template
                if dc.quality == tc.quality_key:
                    strict += 1
    if denom == 0:
        return {"strict": 0.0, "triad_relaxed": 0.0, "root_only": 0.0}
    return {
        "strict": strict / denom,
        "triad_relaxed": relaxed / denom,
        "root_only": root / denom,
    }


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def main() -> int:
    results: List[Dict] = []
    print(f"{'Progression':28} {'#det':>5}  {'strict':>7}  {'triad':>7}  {'root':>7}")
    print("-" * 64)
    for label, beats, tempo, prog in PROGRESSIONS:
        audio, truth = build_clip(prog, beats, tempo)
        total_s = len(audio) / SR
        detected = detect_chords_from_audio(audio, SR, min_chord_duration=0.3)
        scores = score_clip(detected, truth, total_s)
        detected_summary = " > ".join(d.name for d in detected) or "(none)"
        truth_summary = " > ".join(tc.symbol for tc in truth)
        print(f"{label:28} {len(detected):>5}  "
              f"{scores['strict']*100:>6.1f}%  "
              f"{scores['triad_relaxed']*100:>6.1f}%  "
              f"{scores['root_only']*100:>6.1f}%")
        results.append({
            "progression": label,
            "tempo_bpm": tempo,
            "beats_per_chord": beats,
            "truth": truth_summary,
            "detected": detected_summary,
            "n_detected": len(detected),
            "scores": scores,
        })

    # Aggregate
    n = len(results)
    if n:
        avg = {k: sum(r["scores"][k] for r in results) / n
               for k in ("strict", "triad_relaxed", "root_only")}
        print("-" * 64)
        print(f"{'AVERAGE':28} {'':>5}  "
              f"{avg['strict']*100:>6.1f}%  "
              f"{avg['triad_relaxed']*100:>6.1f}%  "
              f"{avg['root_only']*100:>6.1f}%")

    report = {
        "sr": SR,
        "frame_hop_s": 0.05,
        "results": results,
        "average": avg if n else None,
        "decision_rule": {
            "ship_as_is": "root_only >= 0.90 AND triad_relaxed >= 0.75",
            "quality_bug": "root_only >= 0.90 AND triad_relaxed < 0.75",
            "swap_or_punt": "root_only < 0.90",
        },
    }
    report_path = HERE / "chord_spike_report.json"
    report_path.write_text(json.dumps(report, indent=2))
    print(f"\nWrote {report_path.relative_to(BACKEND)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
