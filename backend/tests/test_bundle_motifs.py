"""Riley Patterns → SongUnderstanding.motifs.

The bundle assembler must populate the previously-empty `motifs` seat from the
Musical Graph the analysis worker attaches, so Riley is the canonical source of
the song's repeated-riff understanding. Guarded: no graph -> empty tuple.
"""
from __future__ import annotations

from tone_forge.contracts import Motif
from tone_forge.performance.graph import (
    GridPos,
    MusicalGraph,
    Pattern,
    Phrase,
)
from tone_forge.session.bundle import _build_motifs


def _graph_with_pattern():
    pos = GridPos(4.0, 8.0, 8, 4, 2, 1, True)
    ph = Phrase(stem="other", pos=pos).with_id()
    pat = Pattern(
        stem="other",
        fingerprint="fp1",
        occurrences_s=(4.0, 12.0, 20.0),
        representative_phrase_id=ph.id,
        length_beats=4,
        recurrence_count=3,
        confidence=0.87,
    ).with_id()
    return MusicalGraph(
        song_id="s",
        content_hash="h",
        module_version="0.1.0",
        config_hash="c",
        grid_tempo_bpm=120.0,
        time_signature=(4, 4),
        phrases=(ph,),
        patterns=(pat,),
    ).with_hash()


def test_motifs_populated_from_riley_graph():
    result = {"performance_graph": _graph_with_pattern().to_dict()}
    motifs = _build_motifs(result)
    assert len(motifs) == 1
    m = motifs[0]
    assert isinstance(m, Motif)
    assert m.occurrences_s == (4.0, 12.0, 20.0)
    assert m.confidence == 0.87
    assert m.fingerprint == "fp1"
    assert (m.start_s, m.end_s) == (4.0, 8.0)


def test_no_graph_yields_empty_and_never_raises():
    assert _build_motifs({}) == ()
    assert _build_motifs({"performance_graph": None}) == ()
    assert _build_motifs({"performance_graph": {"assets": []}}) == ()
