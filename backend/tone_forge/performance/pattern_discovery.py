"""Pattern Discovery + Variation Clustering.

Instead of storing riff #1/#2/#3, discover that Pattern A occurs 12×, B 8×, C
2×. Repetition frequency is a confidence signal — repeated phrases are usually
the best launchpad material.

Two passes over the phrases of a stem:
  1. Pattern discovery — group phrases whose audio fingerprints are near-
     identical (same musical material at different times). Each group → Pattern
     with all occurrence start-times + a recurrence count.
  2. Variation clustering — group Patterns that are versions of one idea (looser
     similarity) → Variation(primary, variants), so the UI shows Primary +
     Available Variations instead of dozens of near-dupes.

Locally uses a compact numpy fingerprint (mel-ish band energies over the phrase,
beat-normalized) so it's testable; the backend can swap in
fingerprint/stem_fingerprint for a stronger signature — the grouping logic is
identical either way.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .graph import Pattern, Phrase, Variation

# cosine-similarity thresholds
_SAME = 0.92      # same pattern (occurrence)
_VARIANT = 0.75   # a variation of the same idea


def _phrase_fingerprint(y: np.ndarray, sr: int, phrase: Phrase, bands: int = 24, cols: int = 16) -> np.ndarray:
    """A tempo-normalized band×time energy grid → flattened, L2-normalized.
    Same material at different times/tempi maps to a similar vector."""
    a, b = int(phrase.pos.start_s * sr), int(phrase.pos.end_s * sr)
    seg = y[max(0, a):max(a + 1, b)]
    if len(seg) < 256:
        return np.zeros(bands * cols)
    # split into `cols` equal time chunks (beat-normalized length), band energies
    chunk = max(1, len(seg) // cols)
    feat = []
    fb = np.linspace(0, 1, bands + 1)
    for c in range(cols):
        w = seg[c * chunk:(c + 1) * chunk]
        if len(w) < 8:
            feat.append(np.zeros(bands)); continue
        mag = np.abs(np.fft.rfft(w.astype(np.float64) * np.hanning(len(w))))
        # log-spaced band energies
        edges = (fb * (len(mag) - 1)).astype(int)
        be = [float(np.mean(mag[edges[i]:max(edges[i] + 1, edges[i + 1])])) for i in range(bands)]
        feat.append(np.log1p(np.asarray(be)))
    v = np.concatenate(feat)
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


def _cos(a: np.ndarray, b: np.ndarray) -> float:
    d = np.linalg.norm(a) * np.linalg.norm(b)
    return float(np.dot(a, b) / d) if d > 0 else 0.0


class PatternDiscovery:
    def __init__(self, fingerprint_fn: Optional[Callable] = None):
        # override to plug in fingerprint/stem_fingerprint on the backend
        self._fp = fingerprint_fn or _phrase_fingerprint

    def discover(
        self, y: np.ndarray, sr: int, stem: str, phrases: Sequence[Phrase]
    ) -> Tuple[List[Pattern], List[Variation]]:
        if not phrases:
            return [], []
        if y.ndim > 1:
            y = y.mean(axis=1)
        fps = [self._fp(y, sr, p) for p in phrases]

        # --- pass 1: group into patterns (same material) ---
        assigned = [-1] * len(phrases)
        groups: List[List[int]] = []
        for i in range(len(phrases)):
            if assigned[i] != -1:
                continue
            gi = len(groups); groups.append([i]); assigned[i] = gi
            for j in range(i + 1, len(phrases)):
                if assigned[j] == -1 and _cos(fps[i], fps[j]) >= _SAME:
                    assigned[j] = gi; groups[gi].append(j)

        patterns: List[Pattern] = []
        pat_fp: List[np.ndarray] = []
        for members in groups:
            occ = tuple(round(phrases[m].pos.start_s, 3) for m in members)
            rep = phrases[members[0]]
            centroid = np.mean([fps[m] for m in members], axis=0)
            # frequency → confidence (more occurrences = stronger)
            conf = float(min(1.0, 0.5 + 0.1 * (len(members) - 1)))
            patterns.append(
                Pattern(
                    stem=stem,
                    fingerprint=_hash_fp(centroid),
                    occurrences_s=occ,
                    representative_phrase_id=rep.id,
                    length_beats=rep.pos.length_beats,
                    recurrence_count=len(members),
                    confidence=conf,
                ).with_id()
            )
            pat_fp.append(centroid)

        # --- pass 2: cluster patterns into variations (versions of one idea) ---
        vassigned = [-1] * len(patterns)
        variations: List[Variation] = []
        # seed clusters by descending recurrence (the most-repeated is primary)
        order = sorted(range(len(patterns)), key=lambda k: patterns[k].recurrence_count, reverse=True)
        for i in order:
            if vassigned[i] != -1:
                continue
            variants = []
            for j in order:
                if j != i and vassigned[j] == -1 and _VARIANT <= _cos(pat_fp[i], pat_fp[j]) < _SAME:
                    vassigned[j] = i; variants.append(patterns[j].id)
            if variants:
                vassigned[i] = i
                variations.append(
                    Variation(
                        primary_pattern_id=patterns[i].id,
                        variant_pattern_ids=tuple(variants),
                        label=stem,
                    ).with_id()
                )
        return patterns, variations


def _hash_fp(v: np.ndarray) -> str:
    import hashlib

    q = np.round(v * 32).astype(np.int16).tobytes()
    return hashlib.sha256(q).hexdigest()[:20]
