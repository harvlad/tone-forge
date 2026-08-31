"""Serving helpers — build the Musical Graph for a persisted analysis and shape
it for the API. Keeps the route bodies in tone_forge_api.py tiny.

Reads the persisted ``result`` (never re-analyzes), resolves LOCAL stem paths,
builds (or loads-from-cache) the graph, and produces:
  * performance_payload — the graph for clients that want the raw musical objects,
  * kit_payload — an auto-built Launchpad kit in the SamplePack manifest shape,
  * motifs_for — contracts.Motif dicts for SongUnderstanding.motifs.

If a song has no local stems (URL-only on a serving box), the graph is empty and
the payloads degrade gracefully rather than erroring — the heavy derivation runs
where stems are local (analysis box / dev), and the result is cached.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from .builder import PerformanceBuilder, to_motifs
from .graph import MusicalGraph
from .kit_builder import AutoKitBuilder


def _content_hash(entry_id: str, result: Dict) -> str:
    for k in ("content_hash", "audio_hash", "acquired_hash"):
        v = result.get(k)
        if isinstance(v, str) and v:
            return v
    acq = result.get("acquired_audio") or result.get("acquired") or {}
    if isinstance(acq, dict) and isinstance(acq.get("content_hash"), str):
        return acq["content_hash"]
    return f"entry:{entry_id}"


_builder: Optional[PerformanceBuilder] = None


def _get_builder() -> PerformanceBuilder:
    global _builder
    if _builder is None:
        _builder = PerformanceBuilder()  # default soundfile/librosa loader + cache
    return _builder


# The GPU analysis worker (where stems are LOCAL) derives the graph at analysis
# time and attaches it here; the no-GPU prod box then serves it without needing
# the stem audio. Namespaced so the bundle assembler / other consumers ignore it.
GRAPH_RESULT_KEY = "performance_graph"


def graph_from_result(entry_id: str, result: Dict) -> MusicalGraph:
    """Prefer the graph the worker already derived (stems were local there);
    fall back to building it here (needs local stems, degrades to empty)."""
    stored = result.get(GRAPH_RESULT_KEY)
    if isinstance(stored, dict) and stored.get("assets") is not None:
        try:
            from .builder import _graph_from_dict

            return _graph_from_dict(stored)
        except Exception:
            pass
    return build_graph(entry_id, result)


def build_graph(entry_id: str, result: Dict) -> MusicalGraph:
    return _get_builder().build(
        result=result, song_id=entry_id, content_hash=_content_hash(entry_id, result)
    )


def ensure_graph(entry_id: str, result: Dict) -> tuple[MusicalGraph, bool]:
    """Graph for a persisted result, BACKFILLING legacy entries.

    Songs analyzed before ``derive_and_attach`` existed persisted no
    ``performance_graph``; on an R2-only serving box the local-stems
    build degrades to an empty graph and every kit is 0 pads forever.
    This path closes that hole: when there's no stored graph and no
    local stems, fetch the (already re-presigned) R2 stems to a temp
    dir, derive the graph there, and attach it to ``result`` in place.

    Returns (graph, attached) — ``attached`` means the caller should
    persist the mutated result (history save) so the fetch+derivation
    only ever happens once per song. Heavy (stem download + DSP); call
    off the event loop.
    """
    stored = result.get(GRAPH_RESULT_KEY)
    if isinstance(stored, dict) and stored.get("assets") is not None:
        try:
            from .builder import _graph_from_dict

            return _graph_from_dict(stored), False
        except Exception:
            pass

    from .builder import _stem_paths_of

    # Local stems present (analysis box / dev): the normal cached build.
    if _stem_paths_of(result):
        g = build_graph(entry_id, result)
        if g.assets:
            result[GRAPH_RESULT_KEY] = g.to_dict()
            return g, True

    import tempfile
    from pathlib import Path

    from tone_forge.stem_fetch import materialize_stems

    with tempfile.TemporaryDirectory(prefix="toneforge_graphfill_") as td:
        stems = materialize_stems(result, Path(td))
        shadow = dict(result)
        shadow["stems_local"] = {k: str(v) for k, v in stems.items()}
        # use_cache=False both ways: earlier no-stem builds STORED an
        # empty graph under this content hash (cache.store runs even for
        # empties), and reading it back here would defeat the backfill.
        g = _get_builder().build(
            result=shadow,
            song_id=entry_id,
            content_hash=_content_hash(entry_id, result),
            use_cache=False,
        )
        if g.assets:
            result[GRAPH_RESULT_KEY] = g.to_dict()
            return g, True
        return g, False


def derive_and_attach(entry_id: str, result: Dict) -> bool:
    """Called on the analysis worker after `result` is assembled and stems are
    LOCAL: derive the Musical Graph and attach it under GRAPH_RESULT_KEY so it
    persists with the result and the prod box serves it cache-free. Additive +
    guarded — never fails the analysis. Returns True if a graph was attached."""
    import logging
    _log = logging.getLogger(__name__)
    try:
        g = build_graph(entry_id, result)
        if g.assets:
            result[GRAPH_RESULT_KEY] = g.to_dict()
            return True
        _log.info(
            "performance graph derived but EMPTY (phrases=%d loops=%d assets=%d) "
            "— check stem paths were local/loadable",
            len(g.phrases), len(g.loops), len(g.assets),
        )
    except Exception as exc:  # noqa: BLE001
        _log.info("performance graph derivation failed: %s", exc, exc_info=True)
    return False


def performance_payload(entry_id: str, result: Dict) -> Dict:
    g = graph_from_result(entry_id, result)
    d = g.to_dict()
    return {
        "analysisId": entry_id,
        "graphHash": g.graph_hash,
        "moduleVersion": g.module_version,
        "counts": {
            "phrases": len(g.phrases),
            "patterns": len(g.patterns),
            "variations": len(g.variations),
            "loops": len(g.loops),
            "assets": len(g.assets),
        },
        "graph": d,
    }


def _sections_of(result: Dict) -> List:
    """Normalize the result's sections to [(start_s, end_s, label), …] for the
    kit's section-aware pad labels ('Chorus Guitar riff')."""
    out: List = []
    for s in result.get("sections") or []:
        if not isinstance(s, dict):
            continue
        start = s.get("start_time", s.get("start_s", s.get("start")))
        end = s.get("end_time", s.get("end_s", s.get("end")))
        label = s.get("type") or s.get("label") or s.get("name") or ""
        if isinstance(start, (int, float)) and isinstance(end, (int, float)):
            out.append((float(start), float(end), str(label)))
    return out


def kit_payload(entry_id: str, result: Dict, skill: str = "intermediate", pads: int = 8) -> Dict:
    g = graph_from_result(entry_id, result)
    kit = AutoKitBuilder().build(g, skill=skill, pads=pads, sections=_sections_of(result))
    kit["analysisId"] = entry_id
    return kit


def motifs_for(entry_id: str, result: Dict) -> List[Dict]:
    """Motif dicts for SongUnderstanding.motifs (bundle assembler builds the
    frozen Motif). Empty on failure — additive, never breaks the bundle."""
    try:
        return to_motifs(graph_from_result(entry_id, result))
    except Exception:
        return []
