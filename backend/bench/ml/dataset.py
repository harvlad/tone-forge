"""ML-facing reader on top of the evidence store.

This module deliberately defines its own ``MLExample`` shape
rather than handing back raw ``EvidenceRecord`` objects. Reasons:

  * **Stable contract.** ML pipelines depend on a fixed feature
    schema; if the evidence store grows new fields, that's an
    additive evidence-side change, not an ML retraining trigger.
    Keeping the ML view narrow insulates downstream code.

  * **Filtering at the source.** Only sections with
    high-confidence consensus are emitted as supervised examples
    (per the directive: "low-confidence consensus is reference
    uncertainty, not engine ground truth"). Mid-confidence rows
    can optionally be returned as semi-supervised candidates
    with ``label_confidence`` set, but never with a hard label.

  * **JSON-clean.** ``MLExample.to_dict()`` round-trips through
    JSON without exotic types (no tuples, no datetimes). This
    keeps the contract usable by any framework — torch,
    scikit-learn, jax — that ultimately wants plain dicts /
    arrays.

The features here are intentionally minimal: ``jam_output`` keys
the engine already emits, plus the section's ``(song_id,
section_id)`` provenance. A future Phase X expansion can layer
audio features into the ``extra`` bucket on ``EvidenceRecord``;
this loader will surface them automatically because we pass
``extra`` through verbatim.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterator, Mapping, Optional

from ..evidence.schema import EvidenceRecord
from ..evidence.store import EvidenceStore


__all__ = [
    "MLDatasetConfig",
    "MLDatasetStats",
    "MLExample",
    "SchemaValidationError",
    "compute_dataset_stats",
    "iter_ml_examples",
    "validate_store_schema",
]


# Fields the ML loader treats as canonical training labels. Each
# maps to a ``label_*`` slot on ``MLExample``. Adding a new
# supervised target only requires extending this table; the
# loader walks it generically.
_SUPERVISED_FIELDS: tuple[str, ...] = ("guidance_mode", "chord_sequence")


class SchemaValidationError(ValueError):
    """Raised when the evidence store can't be safely consumed.

    Phase 9's promise is that the schema is forward-compatible
    via the ``extra`` bucket. A schema_version mismatch is the
    one *non*-recoverable case — it means the writer is newer
    than this reader and the safe response is to refuse.
    """


@dataclass(frozen=True)
class MLDatasetConfig:
    """Tunables for the ML reader.

    ``min_label_confidence`` controls which records become
    supervised examples. The 0.8 default matches the directive's
    corpus-trust threshold (also used by Phase 4 / Phase 6 /
    Phase 8); keeping the same number across phases means a sweep
    that improves WCSR can be expected to also help ML — both are
    measured against the same ground truth.

    ``include_semisupervised`` flips lower-confidence rows into
    the output stream with ``label_confidence < min_label_confidence``
    and ``has_hard_label = False`` so an ML pipeline can decide
    whether to use them.
    """

    min_label_confidence: float = 0.8
    include_semisupervised: bool = False
    song_id: Optional[str] = None
    # Optional date-prefix filter (e.g. "2026-06") routed straight
    # into ``EvidenceStore.iter_records`` so an ML pipeline can
    # ingest a sliding window without re-reading old data.
    date_prefix: Optional[str] = None


@dataclass(frozen=True)
class MLExample:
    """One supervised (or semi-supervised) ML row.

    ``features`` is a JSON-clean dict (str/int/float/bool/list/None
    only). ``labels`` is a dict keyed by supervised field name;
    a value of ``None`` means "the consensus had no opinion on
    this field, treat it as missing-at-random for training".

    ``label_confidence`` carries the consensus confidence so the
    pipeline can weight examples or threshold for hard-vs-soft
    label sets.

    ``provenance`` keeps the evidence-store coordinates so an ML
    diagnostic ("which sections did we get wrong?") can map back
    into the JAM UI.
    """

    features: Mapping[str, Any]
    labels: Mapping[str, Any]
    label_confidence: float
    has_hard_label: bool
    provenance: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Plain-dict view — what ``json.dumps`` would see."""
        return {
            "features": dict(self.features),
            "labels": dict(self.labels),
            "label_confidence": self.label_confidence,
            "has_hard_label": self.has_hard_label,
            "provenance": dict(self.provenance),
        }


@dataclass(frozen=True)
class MLDatasetStats:
    """Snapshot of the ML view of an evidence store.

    Operator-facing: "how big is the labelled corpus?", "is the
    label distribution skewed?", "how many sections do we have
    consensus on vs corrections-only?".
    """

    n_records_total: int
    n_supervised_examples: int
    n_semisupervised_examples: int
    n_unique_songs: int
    n_unique_sections: int
    guidance_mode_label_counts: Mapping[str, int]
    chord_sequence_length_histogram: Mapping[int, int]
    mean_label_confidence: float
    config: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_records_total": self.n_records_total,
            "n_supervised_examples": self.n_supervised_examples,
            "n_semisupervised_examples": self.n_semisupervised_examples,
            "n_unique_songs": self.n_unique_songs,
            "n_unique_sections": self.n_unique_sections,
            "guidance_mode_label_counts": dict(self.guidance_mode_label_counts),
            "chord_sequence_length_histogram": (
                {int(k): int(v) for k, v in
                 self.chord_sequence_length_histogram.items()}
            ),
            "mean_label_confidence": self.mean_label_confidence,
            "config": dict(self.config),
        }


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


def validate_store_schema(store: EvidenceStore) -> None:
    """Walk every record once, raise on schema-version mismatch.

    A clean walk also proves the JSONL stream is parseable — the
    operator gets a single yes/no answer to "can an ML pipeline
    safely consume this store?". This function is intentionally
    eager (full walk) so a bad line near the tail of a large
    store doesn't surprise a downstream training job.
    """
    for rec in store.iter_records():
        if rec.schema_version != EvidenceRecord.SCHEMA_VERSION:
            raise SchemaValidationError(
                f"record at {rec.song_id}:{rec.section_id} has "
                f"schema_version={rec.schema_version}; reader supports "
                f"{EvidenceRecord.SCHEMA_VERSION}"
            )


# ---------------------------------------------------------------------------
# Per-section roll-up
# ---------------------------------------------------------------------------


def _latest_per_section(
    store: EvidenceStore,
    *,
    song_id: Optional[str],
    date_prefix: Optional[str],
) -> dict[tuple[str, str], list[EvidenceRecord]]:
    """All records grouped by ``(song_id, section_id)``.

    Used as the basis for ML rows: each section becomes one
    example (or zero, if there's no usable label). We keep the
    full list rather than just the latest because the supervised
    label may come from a different record than the jam_output
    features (consensus and engine runs are independent appends).
    """
    buckets: dict[tuple[str, str], list[EvidenceRecord]] = {}
    for rec in store.iter_records(date_prefix=date_prefix):
        if song_id is not None and rec.song_id != song_id:
            continue
        buckets.setdefault((rec.song_id, rec.section_id), []).append(rec)
    return buckets


def _pick_latest(
    records: list[EvidenceRecord], predicate
) -> Optional[EvidenceRecord]:
    out: Optional[EvidenceRecord] = None
    for rec in records:
        if not predicate(rec):
            continue
        if out is None or rec.timestamp_utc > out.timestamp_utc:
            out = rec
    return out


def _jam_features(rec: Optional[EvidenceRecord]) -> dict[str, Any]:
    """Extract the JSON-clean feature dict from the JAM-side record.

    Returns ``{}`` if no jam_output exists; the ML pipeline can
    treat that as "engine output unavailable for this section"
    and decide whether to fall back to features-from-references.
    """
    if rec is None:
        return {}
    feats: dict[str, Any] = {}
    jam = dict(rec.jam_output)
    for key in ("guidance_mode", "guidance_confidence", "key",
                "tempo_bpm", "polyphony_score", "monophonic_ratio",
                "repetition_score", "lead_activity_score",
                "chord_density_per_s"):
        if key in jam:
            feats[key] = jam[key]
    # chords_in_section → symbol-only sequence so the feature view
    # is symmetric with the consensus label.
    chords = jam.get("chords_in_section")
    if isinstance(chords, list):
        feats["chord_sequence"] = [
            str(c.get("symbol")) for c in chords if isinstance(c, dict)
            and c.get("symbol") is not None
        ]
    # Carry the forward-compat bucket verbatim so a future feature
    # added to ``extra`` flows into ML without code changes here.
    if rec.extra:
        feats["extra"] = dict(rec.extra)
    return feats


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def iter_ml_examples(
    store: EvidenceStore,
    *,
    config: MLDatasetConfig = MLDatasetConfig(),
) -> Iterator[MLExample]:
    """Yield one ``MLExample`` per usable ``(song_id, section_id)``.

    "Usable" = there's a consensus on at least one supervised
    field AND (its confidence ≥ ``min_label_confidence`` OR
    ``include_semisupervised`` is set). Sections with no
    consensus and no corrections are skipped — they carry no
    supervisable signal.
    """
    buckets = _latest_per_section(
        store,
        song_id=config.song_id,
        date_prefix=config.date_prefix,
    )
    for (sid, sec_id), records in buckets.items():
        jam_rec = _pick_latest(records, lambda r: bool(r.jam_output))
        cons_rec = _pick_latest(records, lambda r: r.consensus_output is not None)
        if cons_rec is None:
            continue
        consensus = cons_rec.consensus_output
        if consensus is None:
            continue
        conf = float(consensus.confidence)
        hard = conf >= config.min_label_confidence
        if not hard and not config.include_semisupervised:
            continue

        labels: dict[str, Any] = {}
        for field_name in _SUPERVISED_FIELDS:
            val = getattr(consensus, field_name, None)
            if isinstance(val, tuple):
                val = list(val)
            labels[field_name] = val

        yield MLExample(
            features=_jam_features(jam_rec),
            labels=labels,
            label_confidence=conf,
            has_hard_label=hard,
            provenance={
                "song_id": sid,
                "section_id": sec_id,
                "consensus_timestamp_utc": cons_rec.timestamp_utc,
                "jam_timestamp_utc": (
                    jam_rec.timestamp_utc if jam_rec is not None else None
                ),
            },
        )


def compute_dataset_stats(
    store: EvidenceStore,
    *,
    config: MLDatasetConfig = MLDatasetConfig(),
) -> MLDatasetStats:
    """Single-pass roll-up the operator can paste into a planning doc.

    Counts every record once for the "total" denominator, then
    streams ``iter_ml_examples`` for the labelled / semi-labelled
    breakdown. Cheap on Phase 1 volumes; if the store grows large
    a future variant could memoise by date_prefix.
    """
    # Total records: count without ML config filters so the
    # denominator reflects the raw store size.
    n_total = store.count()

    # We need both hard and semi counts; force include for the
    # walk and tally locally.
    walk_cfg = MLDatasetConfig(
        min_label_confidence=config.min_label_confidence,
        include_semisupervised=True,
        song_id=config.song_id,
        date_prefix=config.date_prefix,
    )
    n_hard = 0
    n_semi = 0
    songs: set[str] = set()
    sections: set[tuple[str, str]] = set()
    guidance_counts: Counter[str] = Counter()
    chord_seq_lengths: Counter[int] = Counter()
    conf_sum = 0.0
    conf_n = 0
    for ex in iter_ml_examples(store, config=walk_cfg):
        if ex.has_hard_label:
            n_hard += 1
        else:
            n_semi += 1
        sid = str(ex.provenance.get("song_id"))
        secid = str(ex.provenance.get("section_id"))
        songs.add(sid)
        sections.add((sid, secid))
        gm = ex.labels.get("guidance_mode")
        if gm is not None:
            guidance_counts[str(gm)] += 1
        seq = ex.labels.get("chord_sequence")
        if isinstance(seq, list):
            chord_seq_lengths[len(seq)] += 1
        conf_sum += ex.label_confidence
        conf_n += 1

    mean_conf = (conf_sum / conf_n) if conf_n else 0.0
    return MLDatasetStats(
        n_records_total=n_total,
        n_supervised_examples=n_hard,
        n_semisupervised_examples=n_semi,
        n_unique_songs=len(songs),
        n_unique_sections=len(sections),
        guidance_mode_label_counts=dict(guidance_counts),
        chord_sequence_length_histogram=dict(chord_seq_lengths),
        mean_label_confidence=mean_conf,
        config={
            "min_label_confidence": config.min_label_confidence,
            "include_semisupervised": config.include_semisupervised,
            "song_id": config.song_id,
            "date_prefix": config.date_prefix,
        },
    )
