"""Section-structure and key evaluation metrics.

Pure functions, no I/O — the section/key counterpart of
``bench.metrics`` (chords). Consumed by ``scripts.analysis_eval``.

Metrics
-------

* ``boundary_f_measure`` — MIREX segment-boundary hit rate at a time
  tolerance (standard windows: 0.5 s strict, 3.0 s coarse). A
  predicted boundary matches at most one reference boundary and vice
  versa (greedy closest-pair matching, same convention as
  ``bench.metrics.boundary_iou``).
* ``section_label_accuracy`` — time-weighted fraction of the song
  where the predicted section's *canonical* label equals the
  reference's canonical label. Canonicalisation maps free-form
  ground-truth names ("Chorus 2", "Post-Chorus", "Guitar Solo") and
  detector ``SectionType`` values onto one shared vocabulary so the
  metric scores structure understanding, not string formatting.
* ``key_score`` — MIREX weighted key accuracy: exact 1.0, perfect
  fifth 0.5, relative major/minor 0.3, parallel major/minor 0.2,
  else 0.0.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, List, Optional, Tuple


__all__ = [
    "canonical_section_label",
    "to_section_regions",
    "boundary_f_measure",
    "section_label_accuracy",
    "parse_key",
    "key_score",
]


# ---------------------------------------------------------------------------
# Canonical section vocabulary
# ---------------------------------------------------------------------------

# Maps lowercase, digit-stripped ground-truth names AND detector
# SectionType values into one vocabulary. Anything unmapped
# canonicalises to itself (so novel labels still compare equal to
# themselves).
_SECTION_SYNONYMS = {
    "intro": "intro",
    "introduction": "intro",
    "verse": "verse",
    "prechorus": "prechorus",
    "pre-chorus": "prechorus",
    "pre chorus": "prechorus",
    "chorus": "chorus",
    "refrain": "chorus",
    "hook": "chorus",
    "postchorus": "chorus",
    "post-chorus": "chorus",
    "post chorus": "chorus",
    "drop": "drop",
    "breakdown": "breakdown",
    "bridge": "bridge",
    "middle eight": "bridge",
    "middle 8": "bridge",
    "buildup": "buildup",
    "build-up": "buildup",
    "build": "buildup",
    "transition": "transition",
    "outro": "outro",
    "coda": "outro",
    "ending": "outro",
    "instrumental": "instrumental",
    "solo": "instrumental",
    "guitar solo": "instrumental",
    "inst": "instrumental",
    "interlude": "instrumental",
    "unknown": "unknown",
}

_TRAILING_QUALIFIER_RE = re.compile(r"[\s\-_]*\d+$")


def canonical_section_label(label: str) -> str:
    """Normalise a section label to the shared canonical vocabulary.

    "Chorus 2" -> "chorus"; "Guitar Solo" -> "instrumental";
    "SectionType.VERSE" / "verse" -> "verse". Unknown labels pass
    through lowercased so self-comparison still works.
    """
    s = str(label).strip().lower()
    if s.startswith("sectiontype."):
        s = s.split(".", 1)[1]
    # Exact synonym first — "Middle 8" must not lose its digit before
    # lookup. Then retry with the trailing pass-number stripped
    # ("Chorus 2" -> "chorus").
    if s in _SECTION_SYNONYMS:
        return _SECTION_SYNONYMS[s]
    s = _TRAILING_QUALIFIER_RE.sub("", s).strip()
    return _SECTION_SYNONYMS.get(s, s)


# ---------------------------------------------------------------------------
# Region adapter
# ---------------------------------------------------------------------------


def to_section_regions(items: Iterable[Any]) -> List[Tuple[float, float, str]]:
    """Adapt section shapes to sorted (start, end, label) tuples.

    Accepts:
    - ArrangementSection-likes (start_time, end_time, type)
    - dicts with start/end/label (fixture JSON) or
      start_time/end_time/type (bundle JSON)
    - (start, end, label) tuples
    """
    out: List[Tuple[float, float, str]] = []
    for it in items:
        if hasattr(it, "start_time") and hasattr(it, "type"):
            t = it.type
            label = getattr(t, "value", t)
            out.append((float(it.start_time), float(it.end_time), str(label)))
        elif isinstance(it, dict):
            if "start" in it:
                out.append((float(it["start"]), float(it["end"]),
                            str(it.get("label", it.get("type", "unknown")))))
            else:
                out.append((float(it["start_time"]), float(it["end_time"]),
                            str(it.get("type", "unknown"))))
        elif isinstance(it, (tuple, list)) and len(it) >= 3:
            out.append((float(it[0]), float(it[1]), str(it[2])))
        else:
            raise TypeError(f"cannot adapt section region: {it!r}")
    out.sort(key=lambda r: r[0])
    return out


def _internal_boundaries(
    regions: List[Tuple[float, float, str]],
) -> List[float]:
    """Boundaries between adjacent sections (song start/end excluded)."""
    if len(regions) < 2:
        return []
    return [float(regions[i][1]) for i in range(len(regions) - 1)]


# ---------------------------------------------------------------------------
# Boundary F-measure
# ---------------------------------------------------------------------------


def boundary_f_measure(
    predicted: Iterable[Any],
    reference: Iterable[Any],
    window_s: float = 3.0,
) -> Tuple[float, float, float]:
    """MIREX boundary hit rate: (precision, recall, f_measure).

    A predicted boundary hits a reference boundary when they are
    within ``window_s``; greedy closest-pair matching, one-to-one.
    Both sides empty -> perfect (1, 1, 1): both agree the song has
    no internal structure.
    """
    if window_s < 0:
        raise ValueError("window_s must be >= 0")
    pred_b = _internal_boundaries(to_section_regions(predicted))
    ref_b = _internal_boundaries(to_section_regions(reference))
    if not pred_b and not ref_b:
        return (1.0, 1.0, 1.0)
    if not pred_b or not ref_b:
        return (0.0, 0.0, 0.0)
    pairs = sorted(
        (abs(p - r), i, j)
        for i, p in enumerate(pred_b)
        for j, r in enumerate(ref_b)
        if abs(p - r) <= window_s
    )
    used_p: set = set()
    used_r: set = set()
    hits = 0
    for _, i, j in pairs:
        if i in used_p or j in used_r:
            continue
        used_p.add(i)
        used_r.add(j)
        hits += 1
    precision = hits / len(pred_b)
    recall = hits / len(ref_b)
    if precision + recall == 0:
        return (0.0, 0.0, 0.0)
    f = 2 * precision * recall / (precision + recall)
    return (precision, recall, f)


# ---------------------------------------------------------------------------
# Label accuracy
# ---------------------------------------------------------------------------


def section_label_accuracy(
    predicted: Iterable[Any],
    reference: Iterable[Any],
    duration_s: float,
) -> float:
    """Time-weighted canonical-label agreement over the song.

    For every overlap between a predicted and a reference section,
    the overlap duration counts when their canonical labels match.
    Divided by ``duration_s`` (gaps count as wrong, matching the
    WCSR denominator convention).
    """
    if duration_s <= 0:
        return 0.0
    pred = to_section_regions(predicted)
    ref = to_section_regions(reference)
    matched = 0.0
    for r_start, r_end, r_lab in ref:
        r_canon = canonical_section_label(r_lab)
        for p_start, p_end, p_lab in pred:
            if p_end <= r_start:
                continue
            if p_start >= r_end:
                break
            ov = min(r_end, p_end) - max(r_start, p_start)
            if ov > 0 and canonical_section_label(p_lab) == r_canon:
                matched += ov
    return matched / duration_s


# ---------------------------------------------------------------------------
# Key score
# ---------------------------------------------------------------------------

_KEY_PC = {
    "c": 0, "c#": 1, "db": 1, "d": 2, "d#": 3, "eb": 3, "e": 4,
    "f": 5, "f#": 6, "gb": 6, "g": 7, "g#": 8, "ab": 8, "a": 9,
    "a#": 10, "bb": 10, "b": 11,
}


def parse_key(text: str) -> Optional[Tuple[int, str]]:
    """Parse "A major" / "F# minor" / "Bbm" -> (pitch_class, mode).

    Mode is "major" or "minor". Returns None on unparsable input.
    """
    s = str(text).strip().lower()
    m = re.match(r"^([a-g][#b]?)\s*(major|minor|maj|min|m)?$", s)
    if not m:
        return None
    root, mode_raw = m.groups()
    pc = _KEY_PC.get(root)
    if pc is None:
        return None
    if mode_raw in ("minor", "min", "m"):
        mode = "minor"
    else:
        mode = "major"
    return (pc, mode)


def key_score(predicted: str, reference: str) -> float:
    """MIREX weighted key accuracy.

    exact 1.0 | perfect fifth (same mode) 0.5 | relative maj/min 0.3
    | parallel maj/min 0.2 | otherwise 0.0. Unparsable -> 0.0.
    """
    p = parse_key(predicted) if predicted else None
    r = parse_key(reference) if reference else None
    if p is None or r is None:
        return 0.0
    p_pc, p_mode = p
    r_pc, r_mode = r
    if p == r:
        return 1.0
    if p_mode == r_mode and (p_pc - r_pc) % 12 in (5, 7):
        return 0.5
    if p_mode != r_mode:
        # Relative: minor is 9 semitones above its relative major.
        if r_mode == "major" and p_mode == "minor" and p_pc == (r_pc + 9) % 12:
            return 0.3
        if r_mode == "minor" and p_mode == "major" and p_pc == (r_pc + 3) % 12:
            return 0.3
        if p_pc == r_pc:
            return 0.2
    return 0.0
