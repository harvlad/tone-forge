"""Practical chord-lane validation for the Jam MVP (P4b).

This harness validates the production chord-lane path that ships in the
analysis subsystem (``tone_forge.analysis.detect_chords``). It is the
sibling of ``chord_spike.py`` (P4 spike), differentiated in three ways:

1. It exercises ``analysis.detect_chords`` (boundary-shaped public API),
   not the internal ``chord_detector`` symbol. The spike measured raw
   detector quality; this harness measures what Jam actually sees.

2. The progression set targets **practical Jam usage** rather than
   academic chord-recognition coverage. Per founder directive, this
   means common guitar-centric progressions across the keys guitarists
   actually play in (E, A, D, G, C, Am, Em). Edge cases (jazz extended
   harmony, modal cadences, key changes, inversions) are explicitly out
   of scope.

3. Pass criteria are tuned for "useful chord names in Jam," not
   research benchmarks:

     - Aggregate **root-only** accuracy ≥ 75%
     - Aggregate **triad-relaxed** accuracy ≥ 60%
     - **No single progression** below 50% root-only

   These thresholds are deliberately permissive. If they hold, Jam ships
   the chord lane with a "beta" badge per execution plan §5.

Run:

    cd backend && python3 scripts/chord_validation.py

Outputs:

    scripts/chord_validation_report.json
"""
from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np

# Make backend/ importable regardless of CWD.
HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent
sys.path.insert(0, str(BACKEND))

from tone_forge.analysis import detect_chords  # noqa: E402

logging.basicConfig(level=logging.WARNING)

SR = 22050

# ---------------------------------------------------------------------------
# Note / chord vocabulary
# ---------------------------------------------------------------------------

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# Pitch-class shorthand
C, Cs, D, Ds, E, F, Fs, G, Gs, A, As, B = range(12)

MAJ = "maj"
MIN = "min"
DOM7 = "dom7"

# How the detector spells a chord — used to compare against expected root
# without depending on the internal symbol formatter. See ``Chord.symbol``
# in ``tone_forge.contracts``.
SYMBOL_FOR: Dict[Tuple[int, str], str] = {}
for pc, name in enumerate(NOTE_NAMES):
    SYMBOL_FOR[(pc, MAJ)] = name
    SYMBOL_FOR[(pc, MIN)] = f"{name}m"
    SYMBOL_FOR[(pc, DOM7)] = f"{name}dom7"

INTERVALS: Dict[str, List[int]] = {
    "maj":  [0, 4, 7],
    "min":  [0, 3, 7],
    "dom7": [0, 4, 7, 10],
}

# Triad family for the triad-relaxed metric.
TRIAD_OF: Dict[str, str] = {
    "maj": "maj", "min": "min", "dim": "dim", "aug": "aug",
    "maj7": "maj", "min7": "min", "dom7": "maj", "dim7": "dim",
    "sus2": "maj", "sus4": "maj", "add9": "maj",
    "min9": "min", "maj9": "maj",
}


# ---------------------------------------------------------------------------
# Practical Jam-usage progression set
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Progression:
    label: str
    key_signature: str
    tempo_bpm: float
    beats_per_chord: int
    chords: Tuple[Tuple[int, str], ...]


# Selection logic:
# - Keys: E, A, D, G, C (open-string guitar keys) + Am, Em (relative minors).
# - Progressions: pop axis variants, blues-rock I-IV-V, classic minor
#   loops, the "anthem" vi-IV-I-V. All common; none jazz/extended.
PROGRESSIONS: Tuple[Progression, ...] = (
    Progression(
        "I-V-vi-IV in G (pop)",     "G major",  110, 4,
        ((G, MAJ), (D, MAJ), (E, MIN), (C, MAJ)),
    ),
    Progression(
        "I-V-vi-IV in D (pop)",     "D major",  120, 4,
        ((D, MAJ), (A, MAJ), (B, MIN), (G, MAJ)),
    ),
    Progression(
        "vi-IV-I-V in C (anthem)",  "C major",  100, 4,
        ((A, MIN), (F, MAJ), (C, MAJ), (G, MAJ)),
    ),
    Progression(
        "I-vi-IV-V in C (50s)",     "C major",  108, 4,
        ((C, MAJ), (A, MIN), (F, MAJ), (G, MAJ)),
    ),
    Progression(
        "I-IV-V in E (blues)",      "E major",   88, 4,
        ((E, MAJ), (A, MAJ), (B, MAJ), (E, MAJ)),
    ),
    Progression(
        "I-IV-V7-I in A (blues)",   "A major",   96, 4,
        ((A, MAJ), (D, MAJ), (E, DOM7), (A, MAJ)),
    ),
    Progression(
        "i-VII-VI-VII in Am (rock minor)", "A minor", 130, 4,
        ((A, MIN), (G, MAJ), (F, MAJ), (G, MAJ)),
    ),
    Progression(
        "i-iv-V in Em (minor loop)",       "E minor", 100, 4,
        ((E, MIN), (A, MIN), (B, MAJ), (E, MIN)),
    ),
    Progression(
        "I-bVII-IV in A (rock)",    "A major",  118, 4,
        ((A, MAJ), (G, MAJ), (D, MAJ), (A, MAJ)),
    ),
    Progression(
        "I-IV-vi-V in D (modern pop)", "D major", 115, 4,
        ((D, MAJ), (G, MAJ), (B, MIN), (A, MAJ)),
    ),
)

# Aggregate pass criteria. Per execution plan §5 the values are tuned
# for usefulness, not state-of-the-art.
PASS_AGGREGATE_ROOT = 0.75
PASS_AGGREGATE_TRIAD = 0.60
PASS_PER_PROGRESSION_ROOT = 0.50


# ---------------------------------------------------------------------------
# Synthesis (additive-harmonic strum — same approach as chord_spike)
# ---------------------------------------------------------------------------

def voiced(root_pc: int, quality_key: str) -> List[int]:
    """Bass note + body voicing approximating an open-chord strum."""
    intervals = INTERVALS[quality_key]
    bass = 36 + root_pc          # C2 = 36
    body = [60 + (root_pc + i) % 12 for i in intervals]  # C4 = 60
    return [bass] + body


def synth_strum(midi_notes: List[int], duration_s: float, sr: int = SR) -> np.ndarray:
    """5 ms attack, 100 ms release. Harmonics 1..4 at decreasing amps."""
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
        env = np.minimum(local_t * 200.0, 1.0)
        env = np.minimum(env, (duration_s - local_t) * 10.0)
        env = np.clip(env, 0.0, 1.0)
        sig *= env
        audio[offset:offset + len(sig)] += sig.astype(np.float32)
    peak = float(np.max(np.abs(audio))) or 1.0
    return (audio / peak * 0.7).astype(np.float32)


@dataclass(frozen=True)
class TruthRegion:
    start_s: float
    end_s: float
    root_pc: int
    quality_key: str

    @property
    def symbol(self) -> str:
        return SYMBOL_FOR[(self.root_pc, self.quality_key)]


def build_clip(prog: Progression) -> Tuple[np.ndarray, List[TruthRegion]]:
    chord_dur = (60.0 / prog.tempo_bpm) * prog.beats_per_chord
    chunks: List[np.ndarray] = []
    truth: List[TruthRegion] = []
    t = 0.0
    for root_pc, quality_key in prog.chords:
        midi = voiced(root_pc, quality_key)
        chunks.append(synth_strum(midi, chord_dur))
        truth.append(TruthRegion(t, t + chord_dur, root_pc, quality_key))
        t += chord_dur
    return np.concatenate(chunks), truth


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _parse_symbol(symbol: str) -> Tuple[int, str]:
    """Split a contracts.Chord.symbol back into (root_pc, quality_key).

    The chord_detector formatter spells maj as bare ("C"), min as "Cm",
    and other qualities as raw template names ("Cdom7", "Cmaj7", ...).
    We only need the root for the root-only metric and a coarse quality
    bucket for the triad-relaxed metric.
    """
    if not symbol:
        return 0, "maj"
    # Note name is 1 or 2 chars (sharp / flat). NOTE_NAMES uses only sharps.
    if len(symbol) >= 2 and symbol[1] in ("#", "b"):
        note = symbol[:2]
        tail = symbol[2:]
    else:
        note = symbol[:1]
        tail = symbol[1:]
    try:
        root_pc = NOTE_NAMES.index(note)
    except ValueError:
        return 0, "maj"
    if tail == "":
        return root_pc, "maj"
    return root_pc, tail


def score_clip(detected, truth: List[TruthRegion],
               total_s: float, frame_hop_s: float = 0.05
               ) -> Dict[str, float]:
    """Frame-level chord match at strict / triad / root tolerance."""
    times = np.arange(0.0, total_s, frame_hop_s)

    def truth_at(ts: float):
        for tr in truth:
            if tr.start_s <= ts < tr.end_s:
                return tr
        return None

    def detected_at(ts: float):
        for d in detected:
            if d.start_s <= ts < d.end_s:
                return d
        return None

    strict = relaxed = root = denom = 0
    for ts in times:
        tr = truth_at(float(ts))
        d = detected_at(float(ts))
        if tr is None:
            continue
        denom += 1
        if d is None:
            continue
        d_root, d_quality = _parse_symbol(d.symbol)
        if d_root == tr.root_pc:
            root += 1
            d_triad = TRIAD_OF.get(d_quality, d_quality)
            t_triad = TRIAD_OF.get(tr.quality_key, tr.quality_key)
            if d_triad == t_triad:
                relaxed += 1
                if d_quality == tr.quality_key:
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
    rows: List[Dict] = []
    header = (
        f"{'Progression':40} {'key':10} {'#det':>5}  "
        f"{'strict':>7}  {'triad':>7}  {'root':>7}"
    )
    print(header)
    print("-" * len(header))

    failing_progressions: List[str] = []
    for prog in PROGRESSIONS:
        audio, truth = build_clip(prog)
        total_s = len(audio) / SR
        detected = detect_chords(audio, SR, min_chord_duration_s=0.3)
        scores = score_clip(detected, truth, total_s)

        truth_summary = " > ".join(tr.symbol for tr in truth)
        detected_summary = " > ".join(d.symbol for d in detected) or "(none)"

        per_prog_passes = scores["root_only"] >= PASS_PER_PROGRESSION_ROOT
        if not per_prog_passes:
            failing_progressions.append(prog.label)

        print(
            f"{prog.label:40} {prog.key_signature:10} {len(detected):>5}  "
            f"{scores['strict']*100:>6.1f}%  "
            f"{scores['triad_relaxed']*100:>6.1f}%  "
            f"{scores['root_only']*100:>6.1f}%"
        )

        rows.append({
            "progression": prog.label,
            "key_signature": prog.key_signature,
            "tempo_bpm": prog.tempo_bpm,
            "beats_per_chord": prog.beats_per_chord,
            "truth": truth_summary,
            "detected": detected_summary,
            "n_detected": len(detected),
            "scores": scores,
            "passes_per_progression_floor": per_prog_passes,
        })

    avg = {
        k: sum(r["scores"][k] for r in rows) / len(rows)
        for k in ("strict", "triad_relaxed", "root_only")
    } if rows else {"strict": 0, "triad_relaxed": 0, "root_only": 0}

    print("-" * len(header))
    print(
        f"{'AVERAGE':40} {'':10} {'':>5}  "
        f"{avg['strict']*100:>6.1f}%  "
        f"{avg['triad_relaxed']*100:>6.1f}%  "
        f"{avg['root_only']*100:>6.1f}%"
    )

    pass_aggregate = (
        avg["root_only"] >= PASS_AGGREGATE_ROOT
        and avg["triad_relaxed"] >= PASS_AGGREGATE_TRIAD
    )
    pass_per_prog = not failing_progressions
    overall_pass = pass_aggregate and pass_per_prog

    print()
    print("Pass criteria (P4b ship gate):")
    print(
        f"  aggregate root_only      >= {PASS_AGGREGATE_ROOT*100:.0f}%   "
        f"actual {avg['root_only']*100:.1f}%   "
        f"{'PASS' if avg['root_only'] >= PASS_AGGREGATE_ROOT else 'FAIL'}"
    )
    print(
        f"  aggregate triad_relaxed  >= {PASS_AGGREGATE_TRIAD*100:.0f}%   "
        f"actual {avg['triad_relaxed']*100:.1f}%   "
        f"{'PASS' if avg['triad_relaxed'] >= PASS_AGGREGATE_TRIAD else 'FAIL'}"
    )
    print(
        f"  per-progression floor    >= {PASS_PER_PROGRESSION_ROOT*100:.0f}% root_only   "
        f"{'PASS' if pass_per_prog else 'FAIL'}"
    )
    if failing_progressions:
        print("  failing progressions:")
        for label in failing_progressions:
            print(f"    - {label}")
    print()
    print(f"OVERALL: {'PASS' if overall_pass else 'FAIL'}")

    report = {
        "sr": SR,
        "frame_hop_s": 0.05,
        "rows": rows,
        "average": avg,
        "pass_criteria": {
            "aggregate_root_only": PASS_AGGREGATE_ROOT,
            "aggregate_triad_relaxed": PASS_AGGREGATE_TRIAD,
            "per_progression_root_only": PASS_PER_PROGRESSION_ROOT,
        },
        "pass_aggregate": pass_aggregate,
        "pass_per_progression": pass_per_prog,
        "overall_pass": overall_pass,
        "failing_progressions": failing_progressions,
    }
    report_path = HERE / "chord_validation_report.json"
    report_path.write_text(json.dumps(report, indent=2))
    print(f"\nWrote {report_path.relative_to(BACKEND)}")
    return 0 if overall_pass else 1


if __name__ == "__main__":
    sys.exit(main())
