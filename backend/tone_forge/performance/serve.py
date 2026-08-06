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


def build_graph(entry_id: str, result: Dict) -> MusicalGraph:
    return _get_builder().build(
        result=result, song_id=entry_id, content_hash=_content_hash(entry_id, result)
    )


def performance_payload(entry_id: str, result: Dict) -> Dict:
    g = build_graph(entry_id, result)
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


def kit_payload(entry_id: str, result: Dict, skill: str = "intermediate", pads: int = 8) -> Dict:
    g = build_graph(entry_id, result)
    kit = AutoKitBuilder().build(g, skill=skill, pads=pads)
    kit["analysisId"] = entry_id
    return kit


def motifs_for(entry_id: str, result: Dict) -> List[Dict]:
    """Motif dicts for SongUnderstanding.motifs (bundle assembler builds the
    frozen Motif). Empty on failure — additive, never breaks the bundle."""
    try:
        return to_motifs(build_graph(entry_id, result))
    except Exception:
        return []
