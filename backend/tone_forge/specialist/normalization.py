"""Register normalization — an explicit pipeline stage, never buried in
a model wrapper.

Wave-2/3 finding: several specialist models emit SOUNDING pitch while
score-style ground truth (Slakh) uses WRITTEN pitch (bass sounds -12 vs
written). For REAL audio, sounding pitch is treated as the musically
faithful representation, so the runtime default is `register_passthrough`
(shift 0). The Slakh +12 rule remains a Lab evaluation convention
(riley_bass_p12) and must not silently become a universal product rule.

Every application records provenance so raw vs normalized behavior can
be compared later without re-running inference.
"""
from __future__ import annotations

from typing import List, Tuple


def apply(notes: List[dict], shift_semitones: int, rule_id: str,
          rule_version: str) -> Tuple[List[dict], dict]:
    """Apply a deterministic register shift.  Returns (notes, provenance).

    Notes are dicts with at least 'pitch'; other keys pass through.
    """
    if shift_semitones == 0:
        out = notes
    else:
        out = []
        for n in notes:
            m = dict(n)
            m["pitch"] = max(0, min(127, int(m["pitch"]) + shift_semitones))
            out.append(m)
    provenance = {
        "rule": rule_id,
        "version": rule_version,
        "shift_semitones": shift_semitones,
        "n_notes": len(out),
        "raw_preserved": shift_semitones == 0,
    }
    return out, provenance
