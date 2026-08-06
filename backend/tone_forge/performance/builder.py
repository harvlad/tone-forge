"""PerformanceBuilder — the orchestrator.

Reads a COMPLETED analysis result (never re-runs beat/section/chord analysis),
loads each stem, and derives the Unified Musical Graph:

    result (beats, downbeats, tempo, sections, chords_by_stem, stems_paths)
        │  load each stem
        ▼
    PhraseAnalyzer  → grid-aligned phrases per stem
        ▼
    LoopAnalyzer    → loop-confidence per phrase (best window → Loop)
        ▼
    PatternDiscovery → Patterns (recurrence) + Variations
        ▼
    classifier      → PerformanceAssets (content type + playable ranking)
        ▼
    MusicalGraph (content-addressed) → cache + Motif population

Stem audio loading is injected (``stem_loader``) so this orchestration is
testable without the heavy audio stack; the default loader uses soundfile/
librosa on the backend.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .cache import GraphCache
from .classifier import build_asset
from .graph import (
    MODULE_VERSION,
    Loop,
    MusicalGraph,
    Phrase,
)
from .grid import MusicalGrid
from .loop_analyzer import LoopAnalyzer
from .pattern_discovery import PatternDiscovery
from .phrase_analyzer import PhraseAnalyzer

try:
    from lab.hashing import config_hash  # type: ignore
except Exception:  # pragma: no cover
    from .graph import config_hash  # type: ignore

# stem_loader(path) -> (mono_float_array, sample_rate)
StemLoader = Callable[[str], Tuple[np.ndarray, int]]

_CONFIG = {
    "phrase_bars_pref": [4, 2, 8, 1],
    "loop_same_thresh": 0.92,
    "loop_min_confidence": 0.55,
    "version": MODULE_VERSION,
}


def _default_stem_loader(path: str) -> Tuple[np.ndarray, int]:  # pragma: no cover
    try:
        import soundfile as sf

        y, sr = sf.read(path, always_2d=False)
    except Exception:
        import librosa

        y, sr = librosa.load(path, sr=None, mono=True)
    y = np.asarray(y, dtype=np.float64)
    if y.ndim > 1:
        y = y.mean(axis=1)
    return y, int(sr)


class PerformanceBuilder:
    def __init__(
        self,
        stem_loader: Optional[StemLoader] = None,
        cache: Optional[GraphCache] = None,
    ):
        self.load_stem = stem_loader or _default_stem_loader
        self.cache = cache or GraphCache()
        self.phraser = PhraseAnalyzer()
        self.looper = LoopAnalyzer()
        self.patterns = PatternDiscovery()

    def build(
        self,
        result: Dict,
        song_id: str,
        content_hash: str,
        use_cache: bool = True,
    ) -> MusicalGraph:
        cfg_hash = config_hash(_CONFIG)
        if use_cache:
            cached = self.cache.load(content_hash, cfg_hash)
            if cached:
                return _graph_from_dict(cached["graph"])

        tempo = float(result.get("tempo_bpm") or 0.0)
        beats = list(result.get("beats_s") or [])
        downbeats = list(result.get("downbeats_s") or [])
        ts = tuple(result.get("time_signature") or (4, 4))
        duration = float(result.get("duration_sec") or (beats[-1] if beats else 0.0))
        grid = MusicalGrid(beats, downbeats, tempo, ts, duration)

        sections = _sections_of(result)
        stem_paths = _stem_paths_of(result)

        all_phrases: List[Phrase] = []
        all_loops: List[Loop] = []
        all_patterns = []
        all_variations = []
        all_assets = []

        for stem, path in stem_paths.items():
            try:
                y, sr = self.load_stem(path)
            except Exception:
                continue
            if y is None or len(y) == 0:
                continue

            phrases = self.phraser.analyze(y, sr, grid, stem, sections)
            if not phrases:
                continue
            # loop score per phrase (phrase window = loop candidate)
            loops_by_phrase: Dict[str, Loop] = {}
            for ph in phrases:
                q = self.looper.analyze(y, sr, ph.pos, optimize=True)
                lp = Loop(phrase_id=ph.id, stem=stem, pos=ph.pos, quality=q).with_id()
                loops_by_phrase[ph.id] = lp
                all_loops.append(lp)

            pats, vars_ = self.patterns.discover(y, sr, stem, phrases)
            # map phrase → its pattern (by occurrence start)
            pat_of_phrase: Dict[str, object] = {}
            for pat in pats:
                for occ in pat.occurrences_s:
                    for ph in phrases:
                        if abs(ph.pos.start_s - occ) < 1e-2:
                            pat_of_phrase[ph.id] = pat

            for ph in phrases:
                asset = build_asset(
                    stem, ph, loops_by_phrase.get(ph.id), pat_of_phrase.get(ph.id)
                )
                all_assets.append(asset)

            all_phrases.extend(phrases)
            all_patterns.extend(pats)
            all_variations.extend(vars_)

        graph = MusicalGraph(
            song_id=song_id,
            content_hash=content_hash,
            module_version=MODULE_VERSION,
            config_hash=cfg_hash,
            grid_tempo_bpm=tempo,
            time_signature=ts,
            phrases=tuple(all_phrases),
            patterns=tuple(all_patterns),
            variations=tuple(all_variations),
            loops=tuple(all_loops),
            assets=tuple(all_assets),
        ).with_hash()

        if use_cache:
            try:
                self.cache.store(graph)
            except Exception:
                pass
        return graph


# --- result-dict adapters (read the persisted analysis, don't re-derive) ---

def _sections_of(result: Dict) -> List[Tuple[float, float, str]]:
    out = []
    for s in result.get("sections") or []:
        if isinstance(s, dict):
            a = s.get("start_s", s.get("start"))
            b = s.get("end_s", s.get("end"))
            lbl = s.get("label", s.get("name", ""))
            if a is not None and b is not None:
                out.append((float(a), float(b), str(lbl)))
    return out


def _stem_paths_of(result: Dict) -> Dict[str, str]:
    """Prefer local stem paths; fall back to any stems dict of {role: path}."""
    for key in ("stems_paths", "stems_local", "stems"):
        d = result.get(key)
        if isinstance(d, dict) and d:
            # keep only string paths (skip URL-only entries the worker rewrote)
            return {
                k: v for k, v in d.items()
                if isinstance(v, str) and not v.startswith("http")
            }
    return {}


def to_motifs(graph: MusicalGraph) -> List[dict]:
    """Convert Patterns → contracts.Motif dicts for SongUnderstanding.motifs.
    (dict form so the bundle assembler can build the frozen Motif additively.)"""
    out = []
    for p in graph.patterns:
        if not p.occurrences_s:
            continue
        rep = next((ph for ph in graph.phrases if ph.id == p.representative_phrase_id), None)
        start = rep.pos.start_s if rep else p.occurrences_s[0]
        end = rep.pos.end_s if rep else p.occurrences_s[0]
        out.append(
            {
                "start_s": float(start),
                "end_s": float(end),
                "fingerprint": p.fingerprint,
                "occurrences_s": list(p.occurrences_s),
                "confidence": float(p.confidence),
            }
        )
    return out


def _graph_from_dict(d: Dict) -> MusicalGraph:
    """Rehydrate a cached graph dict into a MusicalGraph (assets/loops kept as
    dict-backed tuples via dataclass reconstruction)."""
    from .graph import (
        ContentType,
        GridPos,
        Loop,
        LoopQuality,
        Pattern,
        PerformanceAsset,
        Phrase,
        Variation,
    )

    def gp(x):
        return GridPos(**x)

    phrases = tuple(
        Phrase(stem=p["stem"], pos=gp(p["pos"]), onset_density=p["onset_density"],
               pitched=p["pitched"], energy=p["energy"], id=p["id"])
        for p in d.get("phrases", [])
    )
    loops = tuple(
        Loop(phrase_id=l["phrase_id"], stem=l["stem"], pos=gp(l["pos"]),
             quality=LoopQuality(**l["quality"]), id=l["id"])
        for l in d.get("loops", [])
    )
    patterns = tuple(Pattern(**{**p, "occurrences_s": tuple(p["occurrences_s"])}) for p in d.get("patterns", []))
    variations = tuple(Variation(**{**v, "variant_pattern_ids": tuple(v["variant_pattern_ids"])}) for v in d.get("variations", []))
    assets = tuple(
        PerformanceAsset(
            source_id=a["source_id"], stem=a["stem"], pos=gp(a["pos"]),
            content_type=ContentType(a["content_type"]),
            performance_score=a["performance_score"], difficulty=a["difficulty"],
            loopable=a["loopable"], loop_confidence=a["loop_confidence"],
            color_hint=a.get("color_hint"), label=a.get("label", ""),
            pattern_id=a.get("pattern_id"), id=a["id"],
        )
        for a in d.get("assets", [])
    )
    return MusicalGraph(
        song_id=d["song_id"], content_hash=d["content_hash"],
        module_version=d["module_version"], config_hash=d["config_hash"],
        grid_tempo_bpm=d["grid_tempo_bpm"], time_signature=tuple(d["time_signature"]),
        phrases=phrases, patterns=patterns, variations=variations,
        loops=loops, assets=assets, graph_hash=d["graph_hash"],
    )
