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

import logging
from dataclasses import replace as _dc_replace
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
from .phrase_analyzer import _PITCHED_STEMS, PhraseAnalyzer

logger = logging.getLogger(__name__)

try:
    from lab.hashing import config_hash  # type: ignore
except Exception:  # pragma: no cover
    from .graph import config_hash  # type: ignore

# stem_loader(path) -> (mono_float_array, sample_rate) or
# (mono_float_array, sample_rate, meta) — meta is an optional dict of
# file-level quality signals (currently {"collapse_ratio": float}). The
# 2-tuple form stays supported so injected test loaders keep working.
StemLoader = Callable[[str], Tuple[np.ndarray, int]]

_CONFIG = {
    "phrase_bars_pref": [4, 2, 8, 1],
    "loop_same_thresh": 0.92,
    "loop_min_confidence": 0.55,
    "version": MODULE_VERSION,
    # v2: stereo-aware loader (phase-cancellation detect) + collapse_ratio /
    # parent_overlap quality signals. Bumped so NEW analyses re-derive; cached
    # graphs rehydrate with clean defaults and are untouched.
    "loader": 2,
}


def _default_stem_loader(path: str):  # pragma: no cover
    """Load a stem as mono + a file-level quality meta dict.

    The mono fold is where a whole defect class was born: the pan-split
    ``sides`` stem is written ``[side, -side]`` (so stereo playback sums
    back to the original), and ``mean(axis=1)`` folds that to EXACT digital
    silence — the builder then saw zero phrases for the stem and its
    content silently vanished from every kit. Detect the cancellation
    (mono RMS collapsing far below the per-channel RMS) and analyze the
    left channel instead, which for ``[x, -x]`` files IS the content.
    The collapse ratio also ships in meta: phrases from a heavily
    phase-cancelling stem carry it so kit selection can veto the class.
    """
    try:
        import soundfile as sf

        y, sr = sf.read(path, always_2d=False)
    except Exception:
        import librosa

        y, sr = librosa.load(path, sr=None, mono=True)
    y = np.asarray(y, dtype=np.float64)
    collapse = 1.0
    if y.ndim > 1 and y.shape[1] >= 2:
        mono = y.mean(axis=1)
        ch_rms = 0.5 * (
            float(np.sqrt(np.mean(y[:, 0] ** 2)))
            + float(np.sqrt(np.mean(y[:, 1] ** 2)))
        )
        mono_rms = float(np.sqrt(np.mean(mono**2)))
        if ch_rms > 1e-6:
            collapse = mono_rms / ch_rms
        # 0.10 threshold: an [x, -x] file folds to exactly 0; real stereo
        # folds to >= ~0.7. Enormous clean plateau — this is a physical
        # cancellation detector, never a perceptual ranker.
        if ch_rms > 1e-6 and collapse < 0.10:
            logger.warning(
                "[graph] stem %r mono-fold cancels (ratio=%.3f) — "
                "analyzing L channel", path, collapse,
            )
            y = np.ascontiguousarray(y[:, 0])
        else:
            y = mono
    elif y.ndim > 1:
        y = y.mean(axis=1)
    return y, int(sr), {"collapse_ratio": collapse}


def _window_spectrum(y: np.ndarray, sr: int, start_s: float, end_s: float,
                     nfft: int = 8192):
    """Mean log-magnitude spectrum of a time window (parent_overlap input).

    Deliberately coarse: hann frames, half-overlap, capped at 64 frames.
    This feeds a CORRELATION between two renditions of the same bars — a
    duplicate-content detector, not a fidelity metric — so robustness beats
    resolution. Returns None when the window is too short (fail-open).
    """
    a = int(max(0.0, start_s) * sr)
    b = int(min(len(y) / max(1, sr), max(start_s, end_s)) * sr)
    seg = y[a:b]
    if len(seg) < nfft // 2:
        return None
    n = min(nfft, len(seg))
    w = np.hanning(n)
    acc = None
    cnt = 0
    for h in range(0, max(1, len(seg) - n + 1), max(1, n // 2)):
        mag = np.abs(np.fft.rfft(seg[h:h + n] * w))
        acc = mag if acc is None else acc + mag
        cnt += 1
        if cnt >= 64:
            break
    if not cnt:
        return None
    return np.log1p(acc / cnt)


def _spec_corr(a, b) -> Optional[float]:
    """Pearson correlation of two spectra; None on any mismatch (fail-open)."""
    if a is None or b is None or len(a) != len(b):
        return None
    sa = a - a.mean()
    sb = b - b.mean()
    denom = float(np.sqrt((sa**2).sum() * (sb**2).sum()))
    if denom <= 0:
        return None
    return float((sa * sb).sum() / denom)


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

        # Per-stem audio kept ONLY for the pan-split variant family, so the
        # post-loop pass can measure each child phrase against the raw parent
        # over the same bars (parent_overlap). Everything else is dropped as
        # before — this is three stems of mono audio at most.
        _VARIANT_FAMILY = ("guitar", "guitar_center", "guitar_sides")
        fam_audio: Dict[str, Tuple[np.ndarray, int, List[Phrase]]] = {}

        for stem, path in stem_paths.items():
            try:
                loaded = self.load_stem(path)
            except Exception as exc:  # noqa: BLE001
                # Was a bare `continue`: a stem that failed to decode dropped
                # out of the graph with no trace, and the only symptom was a
                # kit missing that instrument's pads. Log it -- an unreadable
                # drums stem and a drums stem that scores badly produce the
                # same empty pad grid, and they need opposite fixes.
                logger.warning(
                    "[graph] stem %r failed to load (%s): %s", stem, path, exc
                )
                continue
            # Loader contract: (y, sr) legacy or (y, sr, meta) with file-level
            # quality signals. Injected test loaders keep the 2-tuple.
            y, sr = loaded[0], loaded[1]
            meta = loaded[2] if len(loaded) > 2 and isinstance(loaded[2], dict) else {}
            if y is None or len(y) == 0:
                logger.warning("[graph] stem %r decoded empty: %s", stem, path)
                continue

            pitched = stem in _PITCHED_STEMS
            phrases = self.phraser.analyze(y, sr, grid, stem, sections)
            if not phrases:
                logger.warning(
                    "[graph] stem %r produced 0 phrases (grid/sections too "
                    "short?)", stem
                )
                continue
            # Stamp the file-level collapse ratio on every phrase of the stem
            # (id is stem+window only, so replace() keeps ids stable).
            _collapse = float(meta.get("collapse_ratio", 1.0))
            if _collapse != 1.0:
                phrases = [_dc_replace(p, collapse_ratio=_collapse) for p in phrases]
            if stem in _VARIANT_FAMILY:
                fam_audio[stem] = (y, sr, phrases)
            # loop score per phrase (phrase window = loop candidate)
            loops_by_phrase: Dict[str, Loop] = {}
            for ph in phrases:
                q = self.looper.analyze(y, sr, ph.pos, optimize=True,
                                        pitched=pitched)
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

        # Post-pass: parent_overlap for pan-split children. When the raw
        # parent guitar stem is visible alongside its center/sides children
        # (analysis_worker re-adds it to stems_local for exactly this), each
        # child phrase gets the spectral correlation against the parent over
        # the same bars. High overlap == the split separated nothing (mid/side
        # of correlated stereo — both children are the SAME part at worse
        # quality); kit selection then ships the raw parent instead.
        if "guitar" in fam_audio and any(
            k in fam_audio for k in ("guitar_center", "guitar_sides")
        ):
            try:
                py, psr, pphr = fam_audio["guitar"]
                parent_specs = {
                    (round(p.pos.start_s, 2), round(p.pos.end_s, 2)):
                        _window_spectrum(py, psr, p.pos.start_s, p.pos.end_s)
                    for p in pphr
                }
                overlap_by_id: Dict[str, float] = {}
                for child in ("guitar_center", "guitar_sides"):
                    if child not in fam_audio:
                        continue
                    cy, csr, cphr = fam_audio[child]
                    for p in cphr:
                        pv = parent_specs.get(
                            (round(p.pos.start_s, 2), round(p.pos.end_s, 2)))
                        if pv is None:
                            continue
                        cv = _window_spectrum(cy, csr, p.pos.start_s, p.pos.end_s)
                        ov = _spec_corr(pv, cv)
                        if ov is not None:
                            overlap_by_id[p.id] = ov
                if overlap_by_id:
                    all_phrases = [
                        _dc_replace(p, parent_overlap=overlap_by_id[p.id])
                        if p.id in overlap_by_id else p
                        for p in all_phrases
                    ]
            except Exception:  # noqa: BLE001 — signal is optional, never fatal
                logger.exception("[graph] parent_overlap pass failed; "
                                 "children keep fail-open defaults")

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
            # 6s ran iff it produced a real guitar/piano stem; then `other`
            # is the synth residual, not the guitar bucket.
            residual_is_synth=("guitar" in stem_paths or "piano" in stem_paths),
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
    """Local stem paths for DSP. The worker rewrites ``stems_paths`` into
    ``http://127.0.0.1:7777/...serve-file`` URLs for the backend handoff, which
    soundfile can't open — so we (a) prefer an explicit raw-path ``stems_local``
    the worker stashes for derivation, and (b) fall THROUGH to the next candidate
    when a dict yields no usable local paths (the old code returned the empty
    filtered dict from ``stems_paths`` and never reached the raw paths → empty
    graph on every worker-derived analysis)."""
    for key in ("stems_local", "stems_paths", "stems"):
        d = result.get(key)
        if isinstance(d, dict) and d:
            local = {
                k: v for k, v in d.items()
                if isinstance(v, str) and v and not v.startswith("http")
            }
            if local:
                return local
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
               pitched=p["pitched"], energy=p["energy"],
               bar_energies=tuple(p.get("bar_energies") or ()),
               # Default the composite-quality signals so graphs cached before
               # these fields existed still rehydrate (no-penalty == clean).
               peak_ratio=float(p.get("peak_ratio", 0.0) or 0.0),
               flatness=float(p.get("flatness", 0.0) or 0.0),
               # Variant-quality signals: clean defaults on legacy graphs.
               collapse_ratio=float(p.get("collapse_ratio", 1.0) if p.get("collapse_ratio") is not None else 1.0),
               parent_overlap=float(p.get("parent_overlap", 0.0) or 0.0),
               id=p["id"])
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
        residual_is_synth=bool(d.get("residual_is_synth", False)),
    )
