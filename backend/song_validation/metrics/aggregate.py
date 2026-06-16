"""Per-engine-version metrics aggregation.

Rolls every alignment for one engine_version into a single row in
``engine_metrics``. The engine improvement loop diffs two such rows
across versions to spot regressions and improvements.

Definitions:

- ``agreement_rate``       sum(agreements) / sum(total_points) across
                           all alignments. An alignment's agreements =
                           score * total_points.
- ``boundary_accuracy``    1 - (BOUNDARY_ERROR count / total_points)
- ``slash_chord_accuracy`` 1 - (SLASH_CHORD_COLLAPSE count / total_points)
- ``extension_accuracy``   1 - (EXTENSION_COLLAPSE count / total_points)

If a version has zero alignments yet, the row is upserted with all
metric columns NULL so consumers can detect "not enough data" without
a separate query.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from ..disagreement import DisagreementClass
from ..store import Store


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def aggregate_metrics(engine_version: str, store: Store) -> dict:
    """Recompute engine_metrics for ``engine_version`` and upsert.

    Returns the new metrics dict (same shape as
    :meth:`Store.get_engine_metrics`).
    """
    alignments = store.alignments_for_engine_version(engine_version)
    total_points = sum(int(a.get("total_points") or 0) for a in alignments)

    if total_points == 0:
        store.upsert_engine_metrics(
            engine_version=engine_version,
            agreement_rate=None,
            boundary_accuracy=None,
            slash_chord_accuracy=None,
            extension_accuracy=None,
            updated_at=_utcnow_iso(),
        )
        return {
            "engine_version": engine_version,
            "agreement_rate": None,
            "boundary_accuracy": None,
            "slash_chord_accuracy": None,
            "extension_accuracy": None,
            "total_points": 0,
        }

    agreements = sum(
        float(a.get("score") or 0.0) * int(a.get("total_points") or 0)
        for a in alignments
    )
    agreement_rate = agreements / total_points

    counts = store.count_disagreements_by_class_for_engine_version(
        engine_version
    )
    boundary = counts.get(DisagreementClass.BOUNDARY_ERROR.value, 0)
    slash = counts.get(DisagreementClass.SLASH_CHORD_COLLAPSE.value, 0)
    extension = counts.get(DisagreementClass.EXTENSION_COLLAPSE.value, 0)

    boundary_accuracy = 1.0 - (boundary / total_points)
    slash_chord_accuracy = 1.0 - (slash / total_points)
    extension_accuracy = 1.0 - (extension / total_points)

    store.upsert_engine_metrics(
        engine_version=engine_version,
        agreement_rate=agreement_rate,
        boundary_accuracy=boundary_accuracy,
        slash_chord_accuracy=slash_chord_accuracy,
        extension_accuracy=extension_accuracy,
        updated_at=_utcnow_iso(),
    )

    return {
        "engine_version": engine_version,
        "agreement_rate": agreement_rate,
        "boundary_accuracy": boundary_accuracy,
        "slash_chord_accuracy": slash_chord_accuracy,
        "extension_accuracy": extension_accuracy,
        "total_points": total_points,
    }
