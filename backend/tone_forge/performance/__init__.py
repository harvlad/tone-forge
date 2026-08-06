"""Performance Intelligence — the Unified Musical Graph.

A DERIVED analysis layer. It consumes a completed song analysis (beat grid,
sections + grouping, per-stem chords, per-stem MIDI, stem fingerprints) and
turns "what is this song?" into "what should the musician PLAY?".

The single canonical output is a ``MusicalGraph``: immutable, content-addressed
musical objects — Phrases → Patterns → Variations → Loops → PerformanceAssets —
that every product surface (Launchpad kits, guitar loops, practice regions, hand
animation, backing tracks) consumes as a different VIEW of the same objects.

Design rules (from the platform spec):
  * Derive once. Never re-run beat/section/chord analysis — read the persisted
    result as the substrate (like derived_audio / contribute_chops already do).
  * Everything aligns to the musical grid — loop lengths are 1/2/4/8 beats or
    bars, never arbitrary time.
  * Objects are immutable + content-addressed + versioned → cache & replay,
    never regenerate an identical analysis.
  * Additive: populate the already-declared ``SongUnderstanding.motifs`` seat
    and serve extra payload under namespaced keys; break no existing consumer.
"""

from .graph import (  # noqa: F401
    MODULE_ID,
    MODULE_VERSION,
    ContentType,
    GridPos,
    Loop,
    MusicalGraph,
    Pattern,
    PerformanceAsset,
    Phrase,
    Variation,
)
from .grid import MusicalGrid  # noqa: F401
