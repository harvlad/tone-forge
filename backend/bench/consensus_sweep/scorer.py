"""Score the consensus corpus's engine-vs-consensus agreement.

Computes per-entry agreement on the two directive-tracked fields
(``guidance_mode``, ``chord_sequence``) and aggregates to a corpus
score the acceptance gate can compare across runs.

Per-entry match rules (mirrors ``bench.failures.miner``'s diff logic
so failure mining and the sweep gate agree on what "match" means):

    * guidance_mode:
        - consensus has no decision  -> excluded from the rate
          (we can't judge an engine on a field the corpus didn't
          decide)
        - jam_output missing         -> 0.0 (engine didn't decide)
        - exact string match         -> 1.0 else 0.0
    * chord_sequence:
        - consensus has no decision  -> excluded
        - jam_output missing         -> 0.0
        - exact tuple equality       -> 1.0 else 0.0
        - additionally a continuous Jaccard score for the
          aggregate so threshold drift on near-misses (one wrong
          chord out of seven) doesn't crash the gate
"""
from __future__ import annotations

import json
import platform
import socket
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Tuple

from ..corpus_consensus.loader import (
    ConsensusCorpusConfig,
    ConsensusCorpusEntry,
    iter_consensus_corpus,
)
from ..evidence.store import EvidenceStore


__all__ = [
    "ConsensusEntryScore",
    "ConsensusCorpusScore",
    "ConsensusScoreConfig",
    "score_entry",
    "score_consensus_corpus",
    "dump_consensus_score",
    "load_consensus_score",
]


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConsensusScoreConfig:
    """Tunables for corpus scoring.

    ``min_confidence`` flows into the underlying corpus loader so the
    gate runs over the same trust bar as Phase 5's ``stats`` output.
    """

    min_confidence: float = 0.8
    require_jam_output: bool = False
    song_id: Optional[str] = None

    def to_corpus_config(self) -> ConsensusCorpusConfig:
        return ConsensusCorpusConfig(
            min_confidence=self.min_confidence,
            require_jam_output=self.require_jam_output,
            song_id=self.song_id,
        )


# ---------------------------------------------------------------------------
# Per-entry score
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConsensusEntryScore:
    """One section's engine-vs-consensus diff in numeric form.

    ``None`` values for a field mean "consensus didn't decide here,
    excluded from this run's aggregate rate". This is how Phase 4's
    failure miner treats undecided consensus, and we keep the same
    semantics so failure-mining and sweep-gate reports agree.
    """

    song_id: str
    section_id: str
    ref_confidence: float
    guidance_mode_match: Optional[float] = None      # 0.0 / 1.0 / None
    chord_sequence_match: Optional[float] = None     # 0.0 / 1.0 / None
    chord_sequence_jaccard: Optional[float] = None   # 0.0..1.0 / None
    jam_present: bool = False


def _normalise_chord_seq(value: Any) -> Optional[Tuple[str, ...]]:
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return tuple(str(c) for c in value if c is not None and str(c) != "")
    return None


def _jam_chord_sequence(jam_output: Mapping[str, Any]) -> Optional[Tuple[str, ...]]:
    """Derive a chord sequence from a JAM output's chords_in_section."""
    chords = jam_output.get("chords_in_section") or []
    seq = tuple(str(c.get("symbol")) for c in chords if c.get("symbol"))
    return seq or None


def _jaccard(a: Optional[Tuple[str, ...]],
             b: Optional[Tuple[str, ...]]) -> Optional[float]:
    if a is None or b is None:
        return None
    set_a, set_b = set(a), set(b)
    if not set_a and not set_b:
        return 1.0
    union = set_a | set_b
    if not union:
        return 1.0
    inter = set_a & set_b
    return len(inter) / len(union)


def score_entry(entry: ConsensusCorpusEntry) -> ConsensusEntryScore:
    """Compute per-field match scores for one corpus entry."""
    jam = entry.latest_jam_output
    jam_present = jam is not None

    # guidance_mode
    g_match: Optional[float]
    if entry.ref_guidance_mode is None:
        g_match = None
    elif not jam_present:
        g_match = 0.0
    else:
        jam_gm = jam.get("guidance_mode")  # type: ignore[union-attr]
        if jam_gm is None:
            g_match = 0.0
        else:
            g_match = 1.0 if jam_gm == entry.ref_guidance_mode else 0.0

    # chord_sequence
    cs_match: Optional[float]
    cs_jaccard: Optional[float]
    if entry.ref_chord_sequence is None:
        cs_match = None
        cs_jaccard = None
    elif not jam_present:
        cs_match = 0.0
        cs_jaccard = 0.0
    else:
        jam_seq = _jam_chord_sequence(jam)  # type: ignore[arg-type]
        ref_seq = _normalise_chord_seq(entry.ref_chord_sequence)
        if jam_seq is None:
            cs_match = 0.0
            cs_jaccard = 0.0
        else:
            cs_match = 1.0 if jam_seq == ref_seq else 0.0
            cs_jaccard = _jaccard(jam_seq, ref_seq) or 0.0

    return ConsensusEntryScore(
        song_id=entry.song_id,
        section_id=entry.section_id,
        ref_confidence=entry.ref_confidence,
        guidance_mode_match=g_match,
        chord_sequence_match=cs_match,
        chord_sequence_jaccard=cs_jaccard,
        jam_present=jam_present,
    )


# ---------------------------------------------------------------------------
# Corpus-level aggregate
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConsensusCorpusScore:
    """Aggregated agreement metrics across the consensus corpus.

    ``combined_match_rate`` is the unweighted mean of the two
    per-field rates. It's the headline metric the gate ratchets on
    — the directive's "corpus improves" check fires when this
    value rises across runs.

    Per-entry rows are preserved so a failed gate can show *which*
    sections regressed. (Same reasoning as ``bench.store.RunRecord``
    keeping ``per_fixture`` alongside ``corpus``.)
    """

    n_entries: int
    n_entries_with_jam: int
    n_guidance_evaluated: int
    n_chord_sequence_evaluated: int
    guidance_mode_match_rate: float
    chord_sequence_match_rate: float
    chord_sequence_mean_jaccard: float
    combined_match_rate: float
    config: Mapping[str, Any] = field(default_factory=dict)
    timestamp_utc: str = ""
    hostname: str = ""
    python_version: str = ""
    score_wall_seconds: float = 0.0
    entries: Tuple[ConsensusEntryScore, ...] = ()


def _mean(values: Iterable[float]) -> float:
    vs = list(values)
    return sum(vs) / len(vs) if vs else 0.0


def score_consensus_corpus(
    store: EvidenceStore,
    *,
    config: ConsensusScoreConfig = ConsensusScoreConfig(),
) -> ConsensusCorpusScore:
    """Run the gate over the store and return a ``ConsensusCorpusScore``.

    Deterministic: the same store + config always produce the same
    score. Wall time is captured for the runtime check in the gate
    but is excluded from determinism (writes are filtered out of
    equality checks by the gate's runtime rule).
    """
    t0 = datetime.now(timezone.utc)
    entries = list(iter_consensus_corpus(store, config=config.to_corpus_config()))
    scores = [score_entry(e) for e in entries]

    g_values = [s.guidance_mode_match for s in scores if s.guidance_mode_match is not None]
    cs_values = [s.chord_sequence_match for s in scores if s.chord_sequence_match is not None]
    cs_jac = [s.chord_sequence_jaccard for s in scores if s.chord_sequence_jaccard is not None]

    g_rate = _mean(g_values)
    cs_rate = _mean(cs_values)
    # Combined = mean of the two field rates, weighted equally so a
    # corpus with lots of "undecided chord_sequence" entries doesn't
    # dilute the guidance_mode signal.
    decided_rates = []
    if g_values:
        decided_rates.append(g_rate)
    if cs_values:
        decided_rates.append(cs_rate)
    combined = _mean(decided_rates) if decided_rates else 0.0

    t1 = datetime.now(timezone.utc)
    return ConsensusCorpusScore(
        n_entries=len(entries),
        n_entries_with_jam=sum(1 for s in scores if s.jam_present),
        n_guidance_evaluated=len(g_values),
        n_chord_sequence_evaluated=len(cs_values),
        guidance_mode_match_rate=g_rate,
        chord_sequence_match_rate=cs_rate,
        chord_sequence_mean_jaccard=_mean(cs_jac),
        combined_match_rate=combined,
        config={
            "min_confidence": config.min_confidence,
            "require_jam_output": config.require_jam_output,
            "song_id": config.song_id,
        },
        timestamp_utc=t0.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        hostname=socket.gethostname(),
        python_version=platform.python_version(),
        score_wall_seconds=(t1 - t0).total_seconds(),
        entries=tuple(scores),
    )


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------


def _entry_to_jsonable(s: ConsensusEntryScore) -> dict:
    return {
        "song_id": s.song_id,
        "section_id": s.section_id,
        "ref_confidence": s.ref_confidence,
        "guidance_mode_match": s.guidance_mode_match,
        "chord_sequence_match": s.chord_sequence_match,
        "chord_sequence_jaccard": s.chord_sequence_jaccard,
        "jam_present": s.jam_present,
    }


def _score_to_jsonable(score: ConsensusCorpusScore) -> dict:
    return {
        "n_entries": score.n_entries,
        "n_entries_with_jam": score.n_entries_with_jam,
        "n_guidance_evaluated": score.n_guidance_evaluated,
        "n_chord_sequence_evaluated": score.n_chord_sequence_evaluated,
        "guidance_mode_match_rate": score.guidance_mode_match_rate,
        "chord_sequence_match_rate": score.chord_sequence_match_rate,
        "chord_sequence_mean_jaccard": score.chord_sequence_mean_jaccard,
        "combined_match_rate": score.combined_match_rate,
        "config": dict(score.config),
        "timestamp_utc": score.timestamp_utc,
        "hostname": score.hostname,
        "python_version": score.python_version,
        "score_wall_seconds": score.score_wall_seconds,
        "entries": [_entry_to_jsonable(e) for e in score.entries],
    }


def _jsonable_to_entry(data: Mapping[str, Any]) -> ConsensusEntryScore:
    return ConsensusEntryScore(
        song_id=str(data["song_id"]),
        section_id=str(data["section_id"]),
        ref_confidence=float(data["ref_confidence"]),
        guidance_mode_match=data.get("guidance_mode_match"),
        chord_sequence_match=data.get("chord_sequence_match"),
        chord_sequence_jaccard=data.get("chord_sequence_jaccard"),
        jam_present=bool(data.get("jam_present", False)),
    )


def _jsonable_to_score(data: Mapping[str, Any]) -> ConsensusCorpusScore:
    return ConsensusCorpusScore(
        n_entries=int(data["n_entries"]),
        n_entries_with_jam=int(data["n_entries_with_jam"]),
        n_guidance_evaluated=int(data["n_guidance_evaluated"]),
        n_chord_sequence_evaluated=int(data["n_chord_sequence_evaluated"]),
        guidance_mode_match_rate=float(data["guidance_mode_match_rate"]),
        chord_sequence_match_rate=float(data["chord_sequence_match_rate"]),
        chord_sequence_mean_jaccard=float(data["chord_sequence_mean_jaccard"]),
        combined_match_rate=float(data["combined_match_rate"]),
        config=dict(data.get("config", {})),
        timestamp_utc=str(data.get("timestamp_utc", "")),
        hostname=str(data.get("hostname", "")),
        python_version=str(data.get("python_version", "")),
        score_wall_seconds=float(data.get("score_wall_seconds", 0.0)),
        entries=tuple(_jsonable_to_entry(e) for e in data.get("entries", [])),
    )


def dump_consensus_score(score: ConsensusCorpusScore, path: Path | str) -> Path:
    """Write the score to a JSON file (pretty-printed for diffability)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(_score_to_jsonable(score), indent=2, sort_keys=False),
        encoding="utf-8",
    )
    return target


def load_consensus_score(path: Path | str) -> ConsensusCorpusScore:
    """Read a previously-dumped score back into a dataclass."""
    return _jsonable_to_score(json.loads(Path(path).read_text(encoding="utf-8")))
