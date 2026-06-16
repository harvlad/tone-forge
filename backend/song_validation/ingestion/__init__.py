"""Ingestion: accept analysis bundles + tab payloads, write rows.

The runtime path uploads an ``analysis_bundle.json`` (the directive's
example shape: ``song_id, chords, sections, key, tempo``); the
ingestion module is the single point of entry that validates the
payload, ensures a ``songs`` row exists for the bundle's
``song_id``, and inserts an ``analysis_results`` row.

Tab ingestion runs through a sibling entry point
(:func:`ingest_tab_source`) that writes to ``tab_sources``. Tab
sources are never authoritative on their own — they're evidence the
alignment module compares against the engine's analysis.
"""

from __future__ import annotations

from .bundle import ingest_analysis_bundle, AnalysisBundleError
from .tab import ingest_tab_source, TabSourceError

__all__ = [
    "ingest_analysis_bundle",
    "AnalysisBundleError",
    "ingest_tab_source",
    "TabSourceError",
]
