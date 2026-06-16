"""Reports: corpus-level summaries that answer the directive's
'success state' questions.

- :func:`where_is_jam_wrong`     top failure classes across the corpus.
- :func:`where_are_tabs_wrong`   timestamps flagged ``LIKELY_TAB_ERROR``.
- :func:`engine_version_diff`    score-card comparison between two
                                 ``engine_metrics`` rows.
- :func:`engine_version_song_diff` per-song delta between two engine
                                 versions (which songs improved /
                                 regressed).
- :func:`dominant_failure_class` argmax over ``disagreements.classification``.
- :func:`inspect_song`           per-song drilldown: every analysis,
                                 tab, alignment, disagreement, and the
                                 engine_metrics score cards this song
                                 contributes to.
- :func:`disagreement_trends_over_time` time-bucketed failure-class
                                 counts keyed off analysis ingestion
                                 time.
- :func:`ingestion_trends_over_time` time-bucketed analysis
                                 ingestion volume + distinct songs +
                                 per-engine-version split.

Each report is a pure SQL/aggregation function returning a dict the
caller can JSON-dump or render to markdown; no UI/HTML coupling.
"""

from __future__ import annotations

from .aligner_diff import aligner_diff_report
from .engine_song_diff import engine_version_song_diff
from .queries import (
    dominant_failure_class,
    engine_version_diff,
    where_are_tabs_wrong,
    where_is_jam_wrong,
)
from .song_detail import inspect_song
from .temporal_trends import (
    TemporalReportError,
    disagreement_trends_over_time,
    ingestion_trends_over_time,
)

__all__ = [
    "where_is_jam_wrong",
    "where_are_tabs_wrong",
    "engine_version_diff",
    "engine_version_song_diff",
    "aligner_diff_report",
    "dominant_failure_class",
    "inspect_song",
    "disagreement_trends_over_time",
    "ingestion_trends_over_time",
    "TemporalReportError",
]
