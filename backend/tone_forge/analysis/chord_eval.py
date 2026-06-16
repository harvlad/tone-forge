"""Chord-detector evaluation harness.

Provides MIREX-style metrics (Weighted Chord Symbol Recall + relaxed
variants) and a time-weighted confusion matrix over predicted-vs-
reference chord region lists. All functions are pure: no I/O, no
audio, no detector calls.

The evaluation flow is:

    predicted = detect_chords(audio, sr)          # List[Chord]
    reference = load_groundtruth(json_path)        # List[ChordRegion]
    score = wcsr(predicted, reference, duration_s) # float in [0, 1]
    cm    = confusion_matrix(predicted, reference) # Dict[(true, pred), s]

A `ChordRegion` is the minimal shape (`start`, `end`, `label`); the
harness accepts either contracts.Chord objects (which expose `start_s`,
`end_s`, `symbol`) or plain dicts/tuples from JSON ground truth, via
the `_to_regions` adapter.

WCSR ("Weighted Chord Symbol Recall") is the industry-standard MIREX
metric: total audio-time where predicted label == reference label,
divided by total audio duration. The "weighted" part is that a 10-s
chord region counts 10× more than a 1-s region — i.e. correctness is
weighted by duration.

Three variants:
- `wcsr`: strict symbol equality after normalisation.
- `triad_relaxed_wcsr`: same root, ignore quality (A and Am both match A).
- `root_only_wcsr`: same root, ignore everything else (diagnostic floor).

Normalisation collapses extended labels to triad form to match the
detector's `_collapse_quality` output, so "Amaj7" in ground truth
compares equal to "A" predicted.
"""
from __future__ import annotations

from typing import Iterable, List, Tuple, Dict, Any, Union
from collections import defaultdict
import re


__all__ = [
    "ChordRegion",
    "to_regions",
    "normalise_symbol",
    "root_of",
    "quality_of",
    "wcsr",
    "triad_relaxed_wcsr",
    "root_only_wcsr",
    "confusion_matrix",
    "format_confusion_top_n",
]


# A region is (start_s, end_s, label). Using a tuple instead of a
# dataclass keeps the harness independent of contracts.Chord and lets
# JSON fixtures load as raw tuples.
ChordRegion = Tuple[float, float, str]


# Matches chord symbols like "A", "Am", "F#", "Bbm7", "C#dim", "G7",
# "Ddim7", "Asus4", "F#5". Captures (root, accidental, quality).
_SYMBOL_RE = re.compile(
    r"^\s*([A-G])([#b]?)((?:maj7|maj9|min7|min9|min|m7|m9|m|dim7|dim|aug|sus2|sus4|add9|maj|dom7|7|5)?)\s*$"
)

# Map written sharp/flat root to semitone (C=0).
_ROOT_PC = {
    'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11,
}


def root_of(symbol: str) -> int:
    """Extract pitch class (0=C ... 11=B) from a chord symbol.

    Raises ValueError on unparsable symbols. Use this when you need
    just the root for `root_only_wcsr` or to compare across qualities.
    """
    m = _SYMBOL_RE.match(symbol)
    if not m:
        raise ValueError(f"unparsable chord symbol: {symbol!r}")
    letter, acc, _ = m.groups()
    pc = _ROOT_PC[letter]
    if acc == '#':
        pc = (pc + 1) % 12
    elif acc == 'b':
        pc = (pc - 1) % 12
    return pc


def quality_of(symbol: str) -> str:
    """Extract collapsed quality family from a chord symbol.

    Returns one of: 'maj', 'min', 'dim', 'aug', 'sus2', 'sus4', '7',
    '5'. Extended qualities (maj7/maj9/min9/add9 etc.) collapse to the
    triad family the same way the detector's `_collapse_quality` does.

    Raises ValueError on unparsable symbols.
    """
    m = _SYMBOL_RE.match(symbol)
    if not m:
        raise ValueError(f"unparsable chord symbol: {symbol!r}")
    _, _, q = m.groups()
    if q in ('', 'maj', 'maj7', 'maj9', 'add9'):
        return 'maj'
    if q in ('m', 'min', 'm7', 'min7', 'm9', 'min9'):
        return 'min'
    if q in ('dim', 'dim7'):
        return 'dim'
    if q in ('7', 'dom7'):
        return '7'
    return q  # 'aug', 'sus2', 'sus4', '5'


def normalise_symbol(symbol: str) -> str:
    """Canonicalise a chord symbol for strict equality comparison.

    Output form is `<root_name><quality>` where root_name is one of
    C C# D D# E F F# G G# A A# B and quality is the collapsed family
    from `quality_of` (with 'maj' rendered as empty string so that a
    plain major reads "A", matching ground-truth convention).
    """
    root_names = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#',
                  'G', 'G#', 'A', 'A#', 'B']
    pc = root_of(symbol)
    q = quality_of(symbol)
    if q == 'maj':
        return root_names[pc]
    if q == 'min':
        return f"{root_names[pc]}m"
    return f"{root_names[pc]}{q}"


def to_regions(items: Iterable[Any]) -> List[ChordRegion]:
    """Adapt heterogeneous chord-region inputs to a list of tuples.

    Accepts:
    - contracts.Chord objects (with start_s, end_s, symbol attributes).
    - chord_detector.Chord objects (with start_time, end_time, name).
    - dicts with keys 'start'/'end'/'label' or 'start_s'/'end_s'/'symbol'.
    - tuples (start, end, label).

    Returns a list of `(start, end, label)` tuples sorted by start.
    """
    out: List[ChordRegion] = []
    for it in items:
        if hasattr(it, 'start_s') and hasattr(it, 'end_s') and hasattr(it, 'symbol'):
            out.append((float(it.start_s), float(it.end_s), str(it.symbol)))
        elif hasattr(it, 'start_time') and hasattr(it, 'end_time') and hasattr(it, 'name'):
            out.append((float(it.start_time), float(it.end_time), str(it.name)))
        elif isinstance(it, dict):
            if 'start' in it:
                out.append((float(it['start']), float(it['end']), str(it['label'])))
            else:
                out.append((float(it['start_s']), float(it['end_s']), str(it['symbol'])))
        elif isinstance(it, (tuple, list)) and len(it) >= 3:
            out.append((float(it[0]), float(it[1]), str(it[2])))
        else:
            raise TypeError(f"cannot adapt chord region: {it!r}")
    out.sort(key=lambda r: r[0])
    return out


def _label_at(regions: List[ChordRegion], t: float) -> str:
    """Return the label active at time t, or '' if none.

    Regions are assumed sorted by start. A region [a, b) covers a
    inclusive, b exclusive. Binary search would be faster; linear scan
    is fine for the < 200 regions we see in practice.
    """
    for start, end, lab in regions:
        if start <= t < end:
            return lab
        if start > t:
            return ''
    return ''


def _overlap(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    """Length of overlap between two intervals (0 if disjoint)."""
    lo = max(a_start, b_start)
    hi = min(a_end, b_end)
    return max(0.0, hi - lo)


def _generic_wcsr(
    predicted: Iterable[Any],
    reference: Iterable[Any],
    duration_s: float,
    match_fn,
) -> float:
    """Time-weighted recall using `match_fn(ref_label, pred_label) -> bool`.

    Iterates reference regions (the "ground truth budget") and for
    each, sums overlap with each predicted region whose label matches
    under `match_fn`. Divides by `duration_s` (NOT total reference
    duration — that would inflate scores when ground truth has gaps).

    Returns 0.0 if duration_s <= 0.
    """
    if duration_s <= 0:
        return 0.0
    ref = to_regions(reference)
    pred = to_regions(predicted)
    matched = 0.0
    for r_start, r_end, r_lab in ref:
        for p_start, p_end, p_lab in pred:
            if p_end <= r_start:
                continue
            if p_start >= r_end:
                break
            ov = _overlap(r_start, r_end, p_start, p_end)
            if ov <= 0:
                continue
            try:
                if match_fn(r_lab, p_lab):
                    matched += ov
            except ValueError:
                # Unparsable label on either side: count as miss.
                # Don't raise — eval harness should not crash on a
                # malformed predicted label.
                continue
    return matched / duration_s


def _strict_match(r_lab: str, p_lab: str) -> bool:
    return normalise_symbol(r_lab) == normalise_symbol(p_lab)


def _triad_relaxed_match(r_lab: str, p_lab: str) -> bool:
    return root_of(r_lab) == root_of(p_lab)


def wcsr(
    predicted: Iterable[Any],
    reference: Iterable[Any],
    duration_s: float,
) -> float:
    """Strict-quality Weighted Chord Symbol Recall.

    Normalises both sides via `normalise_symbol` (so "Amaj7" == "A",
    "F#min" == "F#m") before equality. Returns the fraction of
    `duration_s` where predicted matches reference.
    """
    return _generic_wcsr(predicted, reference, duration_s, _strict_match)


def triad_relaxed_wcsr(
    predicted: Iterable[Any],
    reference: Iterable[Any],
    duration_s: float,
) -> float:
    """Triad-relaxed WCSR: A and Am both count as a match against A.

    Useful as the primary headline metric when the detector's
    quality-discrimination (maj vs min) is what we're actively
    improving — strict WCSR penalises the relative-minor confusion
    that the bass-rooted disambiguation step is designed to fix.
    """
    return _generic_wcsr(predicted, reference, duration_s, _triad_relaxed_match)


def root_only_wcsr(
    predicted: Iterable[Any],
    reference: Iterable[Any],
    duration_s: float,
) -> float:
    """Root-only WCSR (alias of triad_relaxed_wcsr for clarity).

    Kept as a named function so the CLI prints a labelled column;
    semantically identical to triad_relaxed_wcsr.
    """
    return triad_relaxed_wcsr(predicted, reference, duration_s)


def confusion_matrix(
    predicted: Iterable[Any],
    reference: Iterable[Any],
) -> Dict[Tuple[str, str], float]:
    """Time-weighted confusion: seconds of (ref_label, pred_label).

    For each reference region, attributes its overlap with predicted
    regions to (ref_label, pred_label) buckets. Reference seconds
    *not* covered by any predicted region accrue under (ref_label, '').

    Labels are normalised before bucketing (so "Amaj7" and "A" bucket
    together). Unparsable labels are passed through verbatim and will
    show up in the matrix as-is — useful for spotting bugs.
    """
    ref = to_regions(reference)
    pred = to_regions(predicted)
    cm: Dict[Tuple[str, str], float] = defaultdict(float)
    for r_start, r_end, r_lab_raw in ref:
        try:
            r_lab = normalise_symbol(r_lab_raw)
        except ValueError:
            r_lab = r_lab_raw
        covered = 0.0
        for p_start, p_end, p_lab_raw in pred:
            if p_end <= r_start:
                continue
            if p_start >= r_end:
                break
            ov = _overlap(r_start, r_end, p_start, p_end)
            if ov <= 0:
                continue
            try:
                p_lab = normalise_symbol(p_lab_raw)
            except ValueError:
                p_lab = p_lab_raw
            cm[(r_lab, p_lab)] += ov
            covered += ov
        gap = (r_end - r_start) - covered
        if gap > 1e-6:
            cm[(r_lab, '')] += gap
    return dict(cm)


def format_confusion_top_n(
    cm: Dict[Tuple[str, str], float],
    n: int = 10,
) -> str:
    """Render the top-N confusion entries as a human-readable block.

    Diagonal (true == pred, non-empty) entries are shown separately
    above the off-diagonal block so the eyeball pattern is "diagonal
    big, off-diagonal small". Off-diagonal entries with pred == ''
    (uncovered ground truth) are flagged with [GAP] for clarity.
    """
    if not cm:
        return "(empty confusion matrix)"
    diag = [(k, v) for k, v in cm.items() if k[0] == k[1] and k[1]]
    off = [(k, v) for k, v in cm.items() if k[0] != k[1] or not k[1]]
    diag.sort(key=lambda kv: -kv[1])
    off.sort(key=lambda kv: -kv[1])
    lines = ["correct (diagonal):"]
    for (ref, pred), secs in diag[:n]:
        lines.append(f"  {ref:>8} ->  {pred:<8}  {secs:8.2f}s")
    lines.append("confusions (off-diagonal):")
    for (ref, pred), secs in off[:n]:
        tag = " [GAP]" if pred == '' else ""
        lines.append(f"  {ref:>8} ->  {pred or '(none)':<8}  {secs:8.2f}s{tag}")
    return "\n".join(lines)
