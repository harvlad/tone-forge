"""Pluggable music sources for the unified Songs page.

ACTIVE subsystem (not frozen). Every catalog the Songs table can browse
maps into the one :class:`~tone_forge.contracts.SourceTrack` row shape
and answers a common :class:`MusicSource` protocol, so the table shell
is source-agnostic: search/filter/sort/paginate is written once here and
every source inherits it.

This pass ships ONLY :class:`LibrarySource` — the signed-in user's own
analyzed history unioned with their in-flight jobs. ``CrateSource`` /
``JamendoSource`` / ``CcmixterSource`` / ``DeviceSource`` are declared
seams (``SourceId`` in contracts) implemented in later passes.

Boundary (EXECUTION_PLAN §1): this package imports only
``tone_forge.contracts`` — never ``tone_forge_api`` or another
subsystem's internals. The composition point (``tone_forge_api``)
injects already-scoped plain dicts (history entries, job dicts) and this
package turns them into contract DTOs. Enforced by
``tests/test_subsystem_boundaries.py``.
"""
from __future__ import annotations

from tone_forge.sources.base import (
    MusicSource,
    SourceCaps,
    VALID_SORTS,
    decode_cursor,
    encode_cursor,
)
from tone_forge.sources.library import LibrarySource

__all__ = [
    "MusicSource",
    "SourceCaps",
    "LibrarySource",
    "VALID_SORTS",
    "encode_cursor",
    "decode_cursor",
]
