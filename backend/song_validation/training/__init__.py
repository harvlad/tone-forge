"""Training: dataset construction for future ML experiments.

The directive draws a hard line: "do not train directly from all
tabs". Training candidates must come from the corpus-derived
high-confidence subset — alignment confidence high AND tab
confidence high AND engine confidence high.

Public surface:

- :func:`iter_high_confidence_progressions` -- generator yielding one
  qualifying analysis at a time, ready to feed the future harmony LM.
- :func:`corpus_stats` -- aggregate sizing info ("do we have enough
  data yet?").

First target model is the harmony language model (inputs: previous
chords + current chord + key + section → output: probable next
chord), used for contextual weighting only — it never overrides
audio. Model architecture and training loop remain deferred.

Resource-isolation requirement: training jobs run on a separate GPU
pool from runtime analysis. The Python code here doesn't enforce
that — it's an infra concern — but consumers of this module must
NOT be invoked from the runtime path.
"""

from __future__ import annotations

from .corpus import (
    DEFAULT_MIN_ALIGNMENT_SCORE,
    DEFAULT_MIN_TAB_CONFIDENCE,
    corpus_stats,
    iter_high_confidence_progressions,
)
from .exporter import (
    CORPUS_SNAPSHOT_SCHEMA_VERSION,
    CorpusExportError,
    export_corpus,
    read_corpus_snapshot,
)

__all__ = [
    "iter_high_confidence_progressions",
    "corpus_stats",
    "DEFAULT_MIN_ALIGNMENT_SCORE",
    "DEFAULT_MIN_TAB_CONFIDENCE",
    "export_corpus",
    "read_corpus_snapshot",
    "CorpusExportError",
    "CORPUS_SNAPSHOT_SCHEMA_VERSION",
]
