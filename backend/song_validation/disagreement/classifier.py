"""Disagreement classifier v1.

Given the raw ``disagreements`` rows the aligner emits with
``classification=UNKNOWN``, walk through them and apply a small
rule-based labeler. The taxonomy is fixed (see
:class:`DisagreementClass`); this module decides *which* label fits.

The rules are deliberately conservative. False positives in the
classifier mislead the engine-improvement loop, so we'd rather leave
something as ``UNKNOWN`` than slap on a wrong label.

Rule order (first match wins):

1. **BOUNDARY_ERROR**     jam_chord equals the tab's *previous* or
                          *next* chord at this timestamp. Symptom of
                          the engine sliding the chord-change boundary
                          a beat early/late.
2. **EXTENSION_COLLAPSE** roots match and one symbol carries an
                          extension the other dropped (e.g. C vs
                          Cmaj7, G7 vs G).
3. **SLASH_CHORD_COLLAPSE** roots match and one symbol has a slash
                          bass note the other dropped (C vs C/G).
4. **KEY_CONTEXT_ERROR**  enharmonic-equivalent root names with the
                          same quality (C# vs Db, F# vs Gb).
5. **LIKELY_TAB_ERROR**   tab's ``source_confidence`` is below a
                          configurable threshold (default 0.4). Caller
                          must supply that confidence.
6. **UNKNOWN**            fallback when no rule fires.

Public surface: :func:`classify_disagreement` for a single row and
:func:`classify_alignment` for the batch case that walks every
disagreement under one ``alignment_id`` and updates each row
in-place.
"""

from __future__ import annotations

import re
from typing import Mapping, Optional, Sequence

from . import DisagreementClass
from ..store import Store


LIKELY_TAB_ERROR_CONF_THRESHOLD = 0.4


# Map enharmonic root pairs that are spelling-only differences.
# Both directions intentional so lookup is symmetric.
_ENHARMONIC_ROOTS = {
    ("C#", "Db"), ("Db", "C#"),
    ("D#", "Eb"), ("Eb", "D#"),
    ("F#", "Gb"), ("Gb", "F#"),
    ("G#", "Ab"), ("Ab", "G#"),
    ("A#", "Bb"), ("Bb", "A#"),
}


# A chord symbol is: root (letter + optional sharp/flat), then
# optional quality+extensions, then optional slash bass.
_CHORD_RE = re.compile(
    r"^(?P<root>[A-G](?:#|b)?)"
    r"(?P<quality>[^/]*)"
    r"(?:/(?P<bass>[A-G](?:#|b)?))?$"
)


def _parse_chord(sym: Optional[str]) -> Optional[Mapping[str, Optional[str]]]:
    if sym is None:
        return None
    m = _CHORD_RE.match(sym.strip())
    if not m:
        return None
    return {
        "root": m.group("root"),
        "quality": m.group("quality") or "",
        "bass": m.group("bass"),
    }


def _is_boundary_error(
    jam_sym: Optional[str],
    tab_sym: Optional[str],
    tab_progression: Sequence[Mapping[str, object]],
    timestamp: float,
) -> bool:
    """jam_sym matches the tab's previous OR next chord at this time."""
    if jam_sym is None or tab_sym is None:
        return False
    # Find the index of the segment containing timestamp; if not in
    # any, find the nearest neighbours.
    active_idx: Optional[int] = None
    for i, seg in enumerate(tab_progression):
        start = float(seg.get("startSec", 0.0))  # type: ignore[arg-type]
        end = float(seg.get("endSec", start))    # type: ignore[arg-type]
        if start <= timestamp < end:
            active_idx = i
            break
    if active_idx is None:
        return False
    prev_sym = (
        tab_progression[active_idx - 1].get("symbol")
        if active_idx > 0 else None
    )
    next_sym = (
        tab_progression[active_idx + 1].get("symbol")
        if active_idx + 1 < len(tab_progression) else None
    )
    return jam_sym in (prev_sym, next_sym)


def _is_extension_collapse(
    jam_sym: Optional[str], tab_sym: Optional[str]
) -> bool:
    a = _parse_chord(jam_sym)
    b = _parse_chord(tab_sym)
    if a is None or b is None:
        return False
    if a["root"] != b["root"]:
        return False
    qa, qb = a["quality"], b["quality"]
    if qa == qb:
        return False
    # One quality string must be a prefix-or-empty extension of the
    # other (e.g. "" vs "maj7", "7" vs "9", "m" vs "m7").
    if qa == "" or qb == "":
        return True
    if qa.startswith(qb) or qb.startswith(qa):
        return True
    return False


def _is_slash_chord_collapse(
    jam_sym: Optional[str], tab_sym: Optional[str]
) -> bool:
    a = _parse_chord(jam_sym)
    b = _parse_chord(tab_sym)
    if a is None or b is None:
        return False
    if a["root"] != b["root"] or a["quality"] != b["quality"]:
        return False
    # One side has a bass note, the other doesn't.
    return (a["bass"] is None) != (b["bass"] is None)


def _is_key_context_error(
    jam_sym: Optional[str], tab_sym: Optional[str]
) -> bool:
    a = _parse_chord(jam_sym)
    b = _parse_chord(tab_sym)
    if a is None or b is None:
        return False
    if a["quality"] != b["quality"]:
        return False
    if (a["root"], b["root"]) in _ENHARMONIC_ROOTS:
        return True
    return False


def classify_disagreement(
    *,
    jam_chord: Optional[str],
    tab_chord: Optional[str],
    timestamp: float,
    tab_progression: Sequence[Mapping[str, object]],
    tab_source_confidence: Optional[float] = None,
    likely_tab_error_threshold: float = LIKELY_TAB_ERROR_CONF_THRESHOLD,
) -> DisagreementClass:
    """Return the best-fitting :class:`DisagreementClass` for one row."""
    if _is_boundary_error(jam_chord, tab_chord, tab_progression, timestamp):
        return DisagreementClass.BOUNDARY_ERROR
    if _is_extension_collapse(jam_chord, tab_chord):
        return DisagreementClass.EXTENSION_COLLAPSE
    if _is_slash_chord_collapse(jam_chord, tab_chord):
        return DisagreementClass.SLASH_CHORD_COLLAPSE
    if _is_key_context_error(jam_chord, tab_chord):
        return DisagreementClass.KEY_CONTEXT_ERROR
    if (
        tab_source_confidence is not None
        and tab_source_confidence < likely_tab_error_threshold
    ):
        return DisagreementClass.LIKELY_TAB_ERROR
    return DisagreementClass.UNKNOWN


def classify_alignment(
    alignment_id: str,
    store: Store,
    *,
    likely_tab_error_threshold: float = LIKELY_TAB_ERROR_CONF_THRESHOLD,
) -> Mapping[str, int]:
    """Classify every disagreement row under ``alignment_id`` in-place.

    Loads the parent alignment, the tab progression, and the tab's
    source_confidence, then updates each ``disagreements.classification``
    column. Returns a counts-by-class summary the caller can log or
    pass into the metrics roll-up.
    """
    alignment = store.get_alignment_result(alignment_id)
    if alignment is None:
        raise ValueError(f"alignment not found: {alignment_id!r}")

    tab = store.get_tab_source(alignment["tab_id"])
    if tab is None:
        raise ValueError(
            f"tab referenced by alignment is missing: {alignment['tab_id']!r}"
        )
    tab_progression = list(tab.get("progression", []))
    tab_source_confidence = tab.get("source_confidence")

    counts: dict[str, int] = {c.value: 0 for c in DisagreementClass}
    for row in store.list_disagreements_for_alignment(alignment_id):
        cls = classify_disagreement(
            jam_chord=row["jam_chord"],
            tab_chord=row["tab_chord"],
            timestamp=row["timestamp"],
            tab_progression=tab_progression,
            tab_source_confidence=tab_source_confidence,
            likely_tab_error_threshold=likely_tab_error_threshold,
        )
        store.update_disagreement_classification(
            row["disagreement_id"], cls.value
        )
        counts[cls.value] += 1
    return counts
