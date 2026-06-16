"""Offline song-validation & learning subsystem.

This package is the learning center for JAM's chord/section/key
engine. It exists entirely outside the user-facing runtime path: it
ingests `analysis_bundle.json` artifacts produced by the runtime
analysis pipeline, compares them against tab-derived chord
progressions, classifies the disagreements, and aggregates metrics
that measure engine accuracy per version.

Critical invariants (per the architecture directive):
- Tabs are never required for playback.
- Tabs are never required for analysis.
- Tabs are never required at runtime.
- This subsystem must never block playback or analysis.
- This subsystem must never consume realtime GPU resources.

Submodules:
- ``ingestion``    accept analysis bundles + tab payloads, write rows.
- ``alignment``    time-align analysis chord sequences to tab chord
                   progressions (timestamp-by-timestamp).
- ``disagreement`` classify per-timestamp mismatches into the
                   directive's taxonomy (BOUNDARY_ERROR,
                   EXTENSION_COLLAPSE, etc.).
- ``metrics``      aggregate per-engine-version scores
                   (agreement_rate, boundary_accuracy, ...).
- ``training``     dataset construction for the future harmony LM;
                   gated on high-confidence corpus entries only.
- ``reports``      summary readouts: "Where is JAM wrong?",
                   "Which engine versions improved?", etc.

Storage is sqlite3 (see :mod:`song_validation.store`), following the
same `~/.toneforge/<name>.db` convention used elsewhere in the
codebase (e.g. ``tone_forge.ml.retrieval.reference_library``). The
schema mirrors the directive's six tables 1:1.

Nothing in this package is imported from
``backend/tone_forge_api.py``; the runtime path stays unaware of it.
A separate worker/queue commit will wire async ingestion calls in.
"""

from __future__ import annotations

__version__ = "0.1.0"

from .maintenance import list_songs, purge_song, vacuum_store
from .pipeline import PipelineError, validate_song, validate_songs
from .store import Store

__all__ = [
    "Store",
    "PipelineError",
    "validate_song",
    "validate_songs",
    "list_songs",
    "purge_song",
    "vacuum_store",
    "__version__",
]
