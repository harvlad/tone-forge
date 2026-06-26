"""H2 — per-section chord-trigram recurrence extractor.

Implements `backend/h2_specification.md` (frozen 2026-06-21). Any
behavioural change to this module that breaks the canonical-6 anchors
in spec Section 5 is a spec violation; the spec must be updated and
reapproved before the implementation moves.

The extractor is a pure function over a persisted analysis bundle
dict. It performs no I/O, no audio re-decode, and no chord
re-detection. The reference implementation uses only the standard
library; NumPy is permitted only if the canonical-6 anchors still
match within ±0.001.

CLI (diagnostic only — never participates in the classifier):

    python3 -m tone_forge.song_form.h2 <bundle-id>

Reads `backend/data/history.json`, locates the bundle's analysis JSON,
runs the extractor, prints per-section H2 values and the `h2_sep`
scalar.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

# --- Public types ------------------------------------------------------------

PC_NONE = -1
"""Sentinel pitch-class for chords whose symbol failed to parse.

Per spec §3.1, PC_NONE entries are dropped from the chord-PC sequence
before n-gram extraction; they never appear in trigram tuples and
never contribute to the global multiplicity table.
"""


@dataclass(frozen=True)
class H2Result:
    """Frozen H2 extractor output (spec §4)."""

    per_section: tuple[float, ...]
    h2_sep: float
    n_used: int
    degenerate: bool
    section_names: tuple[str, ...]


# --- Symbol parser (spec §3.1) ----------------------------------------------

_ROOT_TO_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_SYMBOL_RE = re.compile(r"^([A-G])([#b\u266F\u266D]?)")
_SHARP_CHARS = frozenset({"#", "\u266F"})
_FLAT_CHARS = frozenset({"b", "\u266D"})


def _root_pc(symbol: Any) -> int:
    """Return the root pitch class (0..11) for a chord symbol.

    Returns `PC_NONE` for unparseable inputs (None, empty string,
    `"N.C."`, etc.). Quality and extensions are intentionally
    ignored — only the root + accidental matter for H2.
    """
    if not isinstance(symbol, str):
        return PC_NONE
    m = _SYMBOL_RE.match(symbol)
    if m is None:
        return PC_NONE
    pc = _ROOT_TO_PC[m.group(1)]
    accidental = m.group(2)
    if accidental in _SHARP_CHARS:
        pc = (pc + 1) % 12
    elif accidental in _FLAT_CHARS:
        pc = (pc - 1) % 12
    return pc


# --- Extractor (spec §3.2 — §3.7) -------------------------------------------


def _midpoint_s(chord: dict) -> float:
    """Chord midpoint in seconds (spec §3.2)."""
    # No fixup for corrupt start_s > end_s; spec §8 requires we use
    # the midpoint as-is and not raise.
    return (float(chord["start_s"]) + float(chord["end_s"])) / 2.0


def _section_bounds(sec: dict) -> tuple[float, float]:
    """Read `(start_s, end_s)` from a section dict.

    Accepts both the legacy `start_s`/`end_s` keys and the current
    `start_time`/`end_time` keys produced by the unified pipeline.
    Spec §2 documents the dual-name handling; the persisted bundle
    schema uses `start_time`/`end_time` for sections but `start_s`/
    `end_s` for chords, an inconsistency this helper isolates.
    """
    start = sec.get("start_s")
    if start is None:
        start = sec.get("start_time", 0.0)
    end = sec.get("end_s")
    if end is None:
        end = sec.get("end_time", start)
    return float(start), float(end)


def _section_pcs(
    sec: dict,
    chord_pcs: list[tuple[float, int]],
) -> list[int]:
    """Pitch-class sequence of chords whose midpoint lies in
    `[section_start, section_end)` (spec §3.3).

    Order is preserved from `chord_pcs` (which is the input chord
    order). Duplicate consecutive PCs are *not* collapsed — spec §3.3
    explicitly permits back-to-back identical chords in trigrams.
    """
    start_s, end_s = _section_bounds(sec)
    return [pc for (mid, pc) in chord_pcs if pc != PC_NONE and start_s <= mid < end_s]


def _ngrams(seq: list[int], n: int) -> list[tuple[int, ...]]:
    """Spec §3.4."""
    if n <= 0 or len(seq) < n:
        return []
    return [tuple(seq[i : i + n]) for i in range(len(seq) - n + 1)]


def _choose_n(full_pc_seq: list[int]) -> tuple[int, bool]:
    """Pick the n-gram order per spec §3.4.

    Song-level switch keyed on the full chord sequence length:
        n = 3 if len(full_pc_seq) >= 6 else 2
    Degenerate iff the full PC sequence has fewer than 2 entries
    (no n-grams of any order possible).
    """
    if len(full_pc_seq) < 2:
        return 0, True
    return (3, False) if len(full_pc_seq) >= 6 else (2, False)


def _h2_per_section(
    full_pc_seq: list[int],
    section_pcs_list: list[list[int]],
    n: int,
) -> tuple[tuple[float, ...], Counter]:
    """Per-section H2 + the global multiplicity table (spec §3.5 — §3.6).

    The global multiplicity table is built from n-grams over the full
    concatenated chord sequence (boundary trigrams included). Per-section
    H2 still counts only that section's n-grams in the numerator/
    denominator, but uses the global table for the `>= 2` lookup.
    """
    global_counts: Counter = Counter(_ngrams(full_pc_seq, n))

    per_section: list[float] = []
    for pcs in section_pcs_list:
        grams = _ngrams(pcs, n)
        if not grams:
            per_section.append(0.0)
            continue
        repeated = sum(1 for g in grams if global_counts[g] >= 2)
        per_section.append(repeated / len(grams))
    return tuple(per_section), global_counts


def _h2_sep(per_section: tuple[float, ...]) -> float:
    """Song-level separability (spec §3.7).

    Population stdev / (mean + 1e-9), clipped to [0, 1].
    """
    if len(per_section) < 2:
        return 0.0
    mu = statistics.fmean(per_section)
    sigma = statistics.pstdev(per_section)
    sep = sigma / (mu + 1e-9)
    return max(0.0, min(1.0, sep))


def _section_name(sec: dict, idx: int) -> str:
    """Best-effort diagnostic label; spec §2 makes this passthrough.

    Looks at `name`, `label`, then `type` (the persisted bundle's
    section-type field, e.g. `"intro"`, `"verse"`). Falls back to
    `section_{idx}` if none are present.
    """
    for key in ("name", "label", "type"):
        v = sec.get(key)
        if isinstance(v, str) and v:
            return v
    return f"section_{idx}"


def extract_h2(bundle: dict) -> H2Result:
    """Compute H2 for one persisted analysis bundle (spec §3 + §8).

    See `backend/h2_specification.md` for the operational contract.
    """
    chords_raw = bundle.get("chords") or []
    sections_raw = bundle.get("sections") or []

    section_names = tuple(_section_name(s, i) for i, s in enumerate(sections_raw))

    # Empty inputs (spec §8 rows 1–2).
    if not sections_raw:
        return H2Result(
            per_section=(),
            h2_sep=0.0,
            n_used=0,
            degenerate=True,
            section_names=(),
        )
    if not chords_raw:
        return H2Result(
            per_section=(0.0,) * len(sections_raw),
            h2_sep=0.0,
            n_used=0,
            degenerate=True,
            section_names=section_names,
        )

    chord_pcs: list[tuple[float, int]] = [
        (_midpoint_s(c), _root_pc(c.get("symbol"))) for c in chords_raw
    ]
    # Full-song chord-PC sequence (spec §3.4 + §3.5): the global
    # n-gram multiplicity table is built from this, including
    # boundary trigrams that span adjacent sections.
    full_pc_seq = [pc for (_, pc) in chord_pcs if pc != PC_NONE]
    section_pcs_list = [_section_pcs(s, chord_pcs) for s in sections_raw]

    n_used, degenerate = _choose_n(full_pc_seq)
    if degenerate:
        return H2Result(
            per_section=(0.0,) * len(sections_raw),
            h2_sep=0.0,
            n_used=0,
            degenerate=True,
            section_names=section_names,
        )

    per_section, _ = _h2_per_section(full_pc_seq, section_pcs_list, n_used)
    h2_sep = _h2_sep(per_section)

    return H2Result(
        per_section=per_section,
        h2_sep=h2_sep,
        n_used=n_used,
        degenerate=False,
        section_names=section_names,
    )


# --- Tiny CLI (diagnostic) ---------------------------------------------------


def _resolve_bundle(bundle_id: str, history_path: Path) -> dict:
    """Locate a persisted analysis bundle by ID from `history.json`.

    Returns the inner analysis-result dict (whose schema matches what
    `extract_h2` consumes: `chords` + `sections` at the top level).
    History entries wrap that under `result`. Raises `KeyError` if
    the ID is not in history or its result payload is missing.
    """
    history = json.loads(history_path.read_text())
    for entry in history:
        if entry.get("id") == bundle_id:
            result = entry.get("result")
            if not isinstance(result, dict):
                raise KeyError(
                    f"bundle id {bundle_id!r} has no `result` dict"
                )
            return result
    raise KeyError(f"bundle id {bundle_id!r} not found in {history_path}")


def _format_result(bundle_id: str, result: H2Result) -> str:
    lines = [f"H2 — bundle {bundle_id}"]
    lines.append(f"  n_used     = {result.n_used}")
    lines.append(f"  degenerate = {result.degenerate}")
    lines.append(f"  h2_sep     = {result.h2_sep:.4f}")
    lines.append(f"  sections   = {len(result.per_section)}")
    for i, (name, val) in enumerate(zip(result.section_names, result.per_section)):
        lines.append(f"    [{i:2d}] {name:<20s}  h2={val:.3f}")
    return "\n".join(lines)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="H2 extractor diagnostic CLI (spec: backend/h2_specification.md)",
    )
    parser.add_argument("bundle_id", help="bundle id (e.g. 73b5931b)")
    parser.add_argument(
        "--history",
        default=str(Path(__file__).resolve().parents[2] / "data" / "history.json"),
        help="path to history.json (default: backend/data/history.json)",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    history_path = Path(args.history)
    try:
        bundle = _resolve_bundle(args.bundle_id, history_path)
    except (KeyError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    result = extract_h2(bundle)
    print(_format_result(args.bundle_id, result))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
