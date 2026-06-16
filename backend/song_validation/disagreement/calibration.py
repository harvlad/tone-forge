"""Confidence-threshold calibration for the LIKELY_TAB_ERROR rule.

The classifier's :data:`LIKELY_TAB_ERROR_CONF_THRESHOLD` is the only
free hyperparameter in the v1 taxonomy. It controls one decision: a
disagreement that no other rule fires on becomes ``LIKELY_TAB_ERROR``
when its tab's ``source_confidence`` is *below* the threshold, and
stays ``UNKNOWN`` otherwise.

Picking that number by intuition is wrong long-term. As the corpus
grows, the distribution of tab confidences should drive the cutoff:
too low and most engine bugs land in ``UNKNOWN`` (the classifier
abdicates); too high and real engine bugs get blamed on the tabs
(the classifier overclaims).

:func:`confidence_calibration_report` profiles the
``UNKNOWN ∪ LIKELY_TAB_ERROR`` slice of the disagreements table (the
only slice the threshold can affect — every other class fires earlier
in the rule order) and reports, for each candidate threshold, how
many rows would flip in either direction. The operator picks the
cutoff that gives the most informative ``LIKELY_TAB_ERROR`` set.

The report does **not** rewrite labels. It's pure diagnostic;
applying a new threshold to the store is done via
:func:`song_validation.disagreement.reclassify_all_alignments` with
``likely_tab_error_threshold=...``.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional, Sequence

from ..store import Store
from . import DisagreementClass
from .classifier import LIKELY_TAB_ERROR_CONF_THRESHOLD


# Default candidate thresholds. 11 evenly-spaced points from 0.0 to
# 1.0 inclusive — coarse enough to inspect at a glance, fine enough
# to spot the inflection point in any realistic distribution.
DEFAULT_CANDIDATE_THRESHOLDS: tuple[float, ...] = tuple(
    round(i * 0.1, 1) for i in range(11)
)

# Histogram bin width for the source_confidence distribution.
_BIN_WIDTH = 0.1


def _bin_label(low: float) -> str:
    """Return a human-readable label for the histogram bin
    [low, low + _BIN_WIDTH). The top bin is closed on the right so
    a row with confidence == 1.0 lands there instead of in a
    phantom "1.0-1.1" bucket."""
    high = round(low + _BIN_WIDTH, 1)
    return f"{low:.1f}-{high:.1f}"


def _empty_histogram() -> dict[str, int]:
    """Pre-seeded histogram so the report always has every bin present
    (zeros included) for caller convenience."""
    bins = [round(i * _BIN_WIDTH, 1) for i in range(10)]
    out = {_bin_label(b): 0 for b in bins}
    out["null"] = 0  # tab rows missing source_confidence
    return out


def _bin_for(conf: Optional[float]) -> str:
    if conf is None:
        return "null"
    c = float(conf)
    if c >= 1.0:
        return _bin_label(0.9)
    if c < 0.0:
        return _bin_label(0.0)
    low = round((int(c * 10) / 10), 1)
    return _bin_label(low)


def _fetch_candidate_rows(store: Store) -> list[tuple[str, Optional[float]]]:
    """Return ``[(classification, source_confidence), ...]`` for every
    disagreement row whose classification could change under a
    threshold tweak. That's exactly the union of UNKNOWN and
    LIKELY_TAB_ERROR — every other class fires earlier in the rule
    order and so is independent of the threshold."""
    sql = (
        "SELECT d.classification, ts.source_confidence "
        "FROM disagreements d "
        "JOIN alignment_results al ON al.alignment_id = d.alignment_id "
        "JOIN tab_sources ts ON ts.tab_id = al.tab_id "
        "WHERE d.classification IN (?, ?)"
    )
    params = (
        DisagreementClass.UNKNOWN.value,
        DisagreementClass.LIKELY_TAB_ERROR.value,
    )
    with store.connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [(r[0], r[1]) for r in rows]


def _project_threshold(
    rows: Sequence[tuple[str, Optional[float]]],
    threshold: float,
) -> dict[str, int]:
    """Hypothetically apply ``threshold`` to ``rows`` and return the
    relabel projection.

    A row is hypothetically labeled LIKELY_TAB_ERROR iff its
    source_confidence is NOT NULL and strictly less than the
    threshold; otherwise it falls back to UNKNOWN. (Matches the
    classifier's actual semantics: NULL confidence is treated as
    "unknown -> keep UNKNOWN".)
    """
    likely = 0
    unknown = 0
    would_gain = 0   # UNKNOWN -> LIKELY_TAB_ERROR
    would_lose = 0   # LIKELY_TAB_ERROR -> UNKNOWN
    for classification, conf in rows:
        new_label = (
            DisagreementClass.LIKELY_TAB_ERROR.value
            if conf is not None and float(conf) < threshold
            else DisagreementClass.UNKNOWN.value
        )
        if new_label == DisagreementClass.LIKELY_TAB_ERROR.value:
            likely += 1
        else:
            unknown += 1
        if (
            classification == DisagreementClass.UNKNOWN.value
            and new_label == DisagreementClass.LIKELY_TAB_ERROR.value
        ):
            would_gain += 1
        elif (
            classification == DisagreementClass.LIKELY_TAB_ERROR.value
            and new_label == DisagreementClass.UNKNOWN.value
        ):
            would_lose += 1
    return {
        "threshold": round(threshold, 4),
        "rows_would_be_likely": likely,
        "rows_would_be_unknown": unknown,
        "rows_would_gain_label": would_gain,
        "rows_would_lose_label": would_lose,
    }


def confidence_calibration_report(
    store: Store,
    *,
    candidate_thresholds: Optional[Iterable[float]] = None,
    current_threshold: float = LIKELY_TAB_ERROR_CONF_THRESHOLD,
) -> dict[str, Any]:
    """Profile the ``UNKNOWN ∪ LIKELY_TAB_ERROR`` slice for threshold
    tuning.

    Args:
        store: the validation store to read from.
        candidate_thresholds: thresholds to project labels under. The
            default is 0.0 .. 1.0 step 0.1.
        current_threshold: the threshold currently in force. The
            report includes the current state's projection so the
            operator can compare candidates against the status quo.

    Returns::

        {
            "current_threshold": float,
            "candidate_pool": int,        # total UNKNOWN+LIKELY rows examined
            "current_label_counts": {
                "LIKELY_TAB_ERROR": int,
                "UNKNOWN": int,
            },
            "confidence_histogram": {
                "0.0-0.1": int, ..., "0.9-1.0": int, "null": int,
            },
            "projections": [
                {
                    "threshold": float,
                    "rows_would_be_likely": int,
                    "rows_would_be_unknown": int,
                    "rows_would_gain_label": int,  # UNKNOWN -> LIKELY
                    "rows_would_lose_label": int,  # LIKELY -> UNKNOWN
                },
                ...
            ],
        }

    The report is pure diagnostic; it does NOT mutate any rows. Apply
    a chosen threshold via
    :func:`song_validation.disagreement.reclassify_all_alignments`.
    """
    rows = _fetch_candidate_rows(store)

    label_counts: dict[str, int] = {
        DisagreementClass.LIKELY_TAB_ERROR.value: 0,
        DisagreementClass.UNKNOWN.value: 0,
    }
    histogram = _empty_histogram()
    for classification, conf in rows:
        label_counts[classification] = label_counts.get(classification, 0) + 1
        histogram[_bin_for(conf)] = histogram.get(_bin_for(conf), 0) + 1

    if candidate_thresholds is None:
        thresholds: list[float] = list(DEFAULT_CANDIDATE_THRESHOLDS)
    else:
        thresholds = [float(t) for t in candidate_thresholds]

    projections = [_project_threshold(rows, t) for t in thresholds]

    return {
        "current_threshold": float(current_threshold),
        "candidate_pool": len(rows),
        "current_label_counts": label_counts,
        "confidence_histogram": histogram,
        "projections": projections,
    }
