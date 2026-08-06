"""The Unified Musical Graph — immutable, content-addressed musical objects.

Object hierarchy (each is a view onto the same underlying song):

    Song
      └─ Phrase        a musically-bounded region of one stem (riff, lick,
      │                 fill, sustain, chord cycle) — cut by STRUCTURE, on the
      │                 grid, never by arbitrary time.
      └─ Pattern       a set of Phrases that are the "same" musical material;
      │                 ``occurrences`` = every time it happens. Frequency is a
      │                 confidence signal.
      └─ Variation     Patterns clustered as versions of one idea (verse riff,
      │                 verse-riff-var, bridge version) → Primary + Variations.
      └─ Loop          a Phrase promoted to a loopable region with a measured
      │                 loop-confidence + optional optimized seam.
      └─ PerformanceAsset  a Loop/Phrase packaged for a target (launchpad pad,
                           guitar loop, practice region…) with a content type,
                           playable ranking and difficulty.

IDs are content hashes so identical objects dedupe and everything is replayable.
"""
from __future__ import annotations

import sys
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

# Reuse the exact cache-identity hashing the lab already uses.
sys.path.insert(0, __file__.rsplit("/tone_forge/", 1)[0])  # backend/ on path
try:
    from lab.hashing import config_hash, short  # type: ignore
except Exception:  # pragma: no cover - fallback if lab layout differs
    import hashlib
    import json as _json

    def config_hash(obj) -> str:  # type: ignore
        return hashlib.sha256(
            _json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()

    def short(h: str, n: int = 20) -> str:  # type: ignore
        return h[:n]


MODULE_ID = "performance_intelligence"
MODULE_VERSION = "0.1.0"


class ContentType(str, Enum):
    """How a performance asset is meant to be used — drives Launchpad layout,
    pad colour, and the "how do I use this pad?" affordance."""

    RHYTHM_LOOP = "rhythm_loop"
    LEAD_LOOP = "lead_loop"
    CHORD_LOOP = "chord_loop"
    BASS_GROOVE = "bass_groove"
    TEXTURE = "texture"
    DRONE = "drone"
    IMPACT = "impact"
    TRANSITION = "transition"
    ONE_SHOT = "one_shot"
    PICKUP = "pickup"
    ENDING = "ending"
    AMBIENT = "ambient"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class GridPos:
    """A musical position/length on the song grid. Everything snaps to this —
    a loop is always a whole number of beats or bars, never arbitrary time."""

    start_s: float
    end_s: float
    start_beat: int          # beat index (0-based) of start
    length_beats: float      # duration in beats (should be whole/half)
    start_bar: int           # bar index (0-based) of start
    length_bars: float       # duration in bars (0 if sub-bar)
    is_bar_aligned: bool     # start lands on a downbeat
    is_pickup: bool = False  # begins before the downbeat (anacrusis)

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


@dataclass(frozen=True)
class LoopQuality:
    """Loop-confidence breakdown at the seam (0..1 each). ``confidence`` is the
    weighted roll-up used for ranking. Populated by LoopAnalyzer."""

    confidence: float = 0.0
    attack_stability: float = 0.0
    release_stability: float = 0.0
    tail_decay: float = 0.0
    zero_crossing: float = 0.0
    spectral_continuity: float = 0.0
    harmonic_continuity: float = 0.0
    beat_alignment: float = 0.0
    phase_continuity: float = 0.0
    # Seam repair the optimizer applied (0 = raw boundaries usable as-is).
    crossfade_ms: float = 0.0
    optimized_start_s: Optional[float] = None
    optimized_end_s: Optional[float] = None


@dataclass(frozen=True)
class Phrase:
    """A musically-bounded region of one stem, on the grid."""

    stem: str                # other/bass/vocals/drums/guitar_left/...
    pos: GridPos
    onset_density: float = 0.0    # onsets per beat (energy/activity)
    pitched: bool = False
    energy: float = 0.0
    id: str = ""             # content hash, filled by __post_init__ via with_id

    def with_id(self) -> "Phrase":
        payload = {
            "stem": self.stem,
            "s": round(self.pos.start_s, 4),
            "e": round(self.pos.end_s, 4),
        }
        return _replace(self, id=short(config_hash(payload)))


@dataclass(frozen=True)
class Pattern:
    """Phrases that are the same musical material; occurrences = every time it
    happens. Repetition count is a headline confidence signal."""

    stem: str
    fingerprint: str
    occurrences_s: Tuple[float, ...]      # start times of every occurrence
    representative_phrase_id: str
    length_beats: float
    recurrence_count: int
    confidence: float = 1.0
    id: str = ""

    def with_id(self) -> "Pattern":
        return _replace(self, id=short(config_hash({"fp": self.fingerprint, "st": self.stem})))


@dataclass(frozen=True)
class Variation:
    """A cluster of Patterns that are versions of one idea → Primary + list."""

    primary_pattern_id: str
    variant_pattern_ids: Tuple[str, ...] = ()
    label: str = ""
    id: str = ""

    def with_id(self) -> "Variation":
        return _replace(self, id=short(config_hash({"p": self.primary_pattern_id})))


@dataclass(frozen=True)
class Loop:
    """A Phrase promoted to a loopable region with measured quality."""

    phrase_id: str
    stem: str
    pos: GridPos
    quality: LoopQuality = field(default_factory=LoopQuality)
    id: str = ""

    def with_id(self) -> "Loop":
        return _replace(self, id=short(config_hash({"ph": self.phrase_id})))


@dataclass(frozen=True)
class PerformanceAsset:
    """A Loop/Phrase packaged for a target with a content type + ranking."""

    source_id: str           # loop or phrase id
    stem: str
    pos: GridPos
    content_type: ContentType = ContentType.UNKNOWN
    performance_score: float = 0.0   # playable ranking (0..1)
    difficulty: float = 0.0          # 0=easy .. 1=hard
    loopable: bool = False
    loop_confidence: float = 0.0
    color_hint: Optional[str] = None
    label: str = ""
    pattern_id: Optional[str] = None       # ties variations together
    id: str = ""

    def with_id(self) -> "PerformanceAsset":
        return _replace(self, id=short(config_hash({"src": self.source_id, "ct": self.content_type})))


@dataclass(frozen=True)
class MusicalGraph:
    """The canonical performance-intelligence artifact for one song version.

    ``graph_hash`` content-addresses the whole graph = fn(analysis content hash,
    module version, config). Immutable + replayable: an identical input yields an
    identical hash, so we never regenerate."""

    song_id: str
    content_hash: str        # the analysis/audio content hash it derives from
    module_version: str
    config_hash: str
    grid_tempo_bpm: float
    time_signature: Tuple[int, int]
    phrases: Tuple[Phrase, ...] = ()
    patterns: Tuple[Pattern, ...] = ()
    variations: Tuple[Variation, ...] = ()
    loops: Tuple[Loop, ...] = ()
    assets: Tuple[PerformanceAsset, ...] = ()
    graph_hash: str = ""

    def with_hash(self) -> "MusicalGraph":
        return _replace(
            self,
            graph_hash=short(
                config_hash(
                    {
                        "c": self.content_hash,
                        "v": self.module_version,
                        "cfg": self.config_hash,
                    }
                )
            ),
        )

    def to_dict(self) -> Dict:
        return _to_jsonable(asdict(self))

    def ranked_assets(self, limit: Optional[int] = None) -> List[PerformanceAsset]:
        ranked = sorted(self.assets, key=lambda a: a.performance_score, reverse=True)
        return ranked[:limit] if limit else ranked


# --- helpers -------------------------------------------------------------

def _replace(obj, **changes):
    from dataclasses import replace as _dc_replace

    return _dc_replace(obj, **changes)


def _to_jsonable(obj):
    """Convert Enums/tuples in the asdict output to plain JSON types."""
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(v) for v in obj]
    if isinstance(obj, Enum):
        return obj.value
    return obj
