"""Section grouping for the JAM rehearsal-v2 view (Phase 3).

Once all upstream section-type refinement passes have settled
(Stage A H2-derived, Stage B song-form refinement, duration guard,
resegmentation), sections that share a ``type`` are treated as
recurrences of the same musical part. This module attaches two
purely derivative fields to each section dict:

* ``group_id`` — the shared ``type`` string when the cluster has
  ≥ 2 members, else ``None``.
* ``recurrence_count`` — cluster size (≥ 1; 1 for singletons).

The frontend uses these to collapse DEVELOPMENTs under an ANCHOR
and to render subtitles like "The Chorus Hook (repeats 4×)".

Pure function, stdlib-only, deterministic. Never mutates the
existing ``type`` field.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, List, MutableMapping


def assign_section_groups(sections: List[MutableMapping[str, Any]]) -> None:
    """Attach ``group_id`` + ``recurrence_count`` to each section dict.

    Mutates ``sections`` in place. Idempotent — safe to call more
    than once. Sections with a missing or empty ``type`` never
    receive a ``group_id`` (only ``recurrence_count = 1``).
    """
    if not sections:
        return
    type_counts: Counter[str] = Counter(
        str(s.get("type", "")) for s in sections
    )
    for section in sections:
        t = str(section.get("type", ""))
        count = int(type_counts.get(t, 1))
        section["group_id"] = t if (count > 1 and t) else None
        section["recurrence_count"] = count
