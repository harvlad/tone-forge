"""Song Understanding analysis.

Produces ``SongUnderstanding`` (tempo / key / sections / chords / etc.)
from acquired audio and stems.

MVP scope: tempo, key, sections, chords. Phase-3 scope: tuning, capo,
difficulty, motifs.

Public surface (importable from ``tone_forge.analysis``):

* ``detect_chords`` — chord lane in ``contracts.Chord`` shape (P4a).

Other helpers (sections, tempo_key, etc.) ship from their own
submodules and will be re-exported here as they land.
"""

from tone_forge.analysis.chords import detect_chords

__all__ = ["detect_chords"]
