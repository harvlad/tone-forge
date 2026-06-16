"""ToneForge chord-detector benchmark + sweep harness.

This package is the evidence engine for the self-improving
chord-recognition platform (M1 of the plan in
``.claude/plans/effervescent-twirling-neumann.md``). It runs the
production chord detector against a curated corpus of fixtures,
measures a panel of accuracy + cost metrics, and supports parameter
sweeps that propose changes to ``DetectorConfig`` for human review.

Strict one-way dependency: ``bench`` imports from
``tone_forge.analysis`` (read-only); ``tone_forge`` must NEVER
import from ``bench``. The unit test
``tests/test_bench_import_boundary.py`` enforces this statically.

Public surface (filled in across M1.2 - M1.5):

  * ``iter_corpus_fixtures``         (M1.3, in ``bench.corpus``)
  * ``run_benchmark``                (M1.4, in ``bench.benchmark``)
  * ``run_sweep``                    (M1.5, in ``bench.sweep``)
  * Metric helpers                   (M1.2, in ``bench.metrics``)
  * ``RunRecord``, ``FixtureResult``,
    ``CorpusResult`` dataclasses    (M1.4, in ``bench.store``)

M1.1 (this file) intentionally re-exports nothing yet. Submodules
will populate ``__all__`` as their content lands so the import
surface only grows when there is actually code behind it.
"""

__all__: list[str] = []
