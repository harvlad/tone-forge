"""``python -m bench`` -> dispatch to subcommands.

Routes to ``bench.benchmark`` or ``bench.sweep`` based on the first
positional argument. Each subcommand's own ``main`` is the source
of truth for its argparse spec.

Usage:
    python -m bench benchmark [<benchmark args>...]
    python -m bench sweep <space.yaml> [<sweep args>...]

M1.1 skeleton: this dispatcher exists so the package is invokable
from the get-go. ``benchmark`` and ``sweep`` modules are filled in
by M1.4 / M1.5 respectively. Until then, invoking the subcommands
will raise NotImplementedError from inside those modules.
"""
from __future__ import annotations

import sys


_USAGE = (
    "usage: python -m bench "
    "{benchmark,sweep,corpus,evidence,reference,consensus,failures,"
    "corpus_consensus,consensus_sweep,roadmap,ml} "
    "[<args>...]\n"
    "\n"
    "Subcommands:\n"
    "  benchmark         Run the detector against the corpus, write RunRecord JSON\n"
    "  sweep             Run a parameter sweep against a corpus baseline\n"
    "  corpus            Corpus curator CLI (stats, validate, add)\n"
    "  evidence          JAM Learning System evidence store (stats, replay, show)\n"
    "  reference         JAM Learning System reference import (ingest, list, template)\n"
    "  consensus         JAM Learning System consensus builder (build, show, inspect)\n"
    "  failures          JAM Learning System failure mining (report, summary)\n"
    "  corpus_consensus  Consensus-derived benchmark corpus (stats, list, export)\n"
    "  consensus_sweep   Consensus-corpus regression gate (score, compare, show)\n"
    "  roadmap           Disagreement-driven engine roadmap (build, show)\n"
    "  ml                Future-ML compatibility view (validate, stats, dump)\n"
)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        sys.stderr.write(_USAGE)
        return 2
    sub, rest = args[0], args[1:]
    if sub in ("-h", "--help"):
        sys.stdout.write(_USAGE)
        return 0
    if sub == "benchmark":
        from bench import benchmark as _benchmark
        return _benchmark.main(rest)
    if sub == "sweep":
        from bench import sweep as _sweep
        return _sweep.main(rest)
    if sub == "corpus":
        from bench import corpus as _corpus
        return _corpus.main(rest)
    if sub == "evidence":
        from bench.evidence.__main__ import main as _evidence_main
        return _evidence_main(rest)
    if sub == "reference":
        from bench.reference.__main__ import main as _reference_main
        return _reference_main(rest)
    if sub == "consensus":
        from bench.consensus.__main__ import main as _consensus_main
        return _consensus_main(rest)
    if sub == "failures":
        from bench.failures.__main__ import main as _failures_main
        return _failures_main(rest)
    if sub == "corpus_consensus":
        from bench.corpus_consensus.__main__ import main as _cc_main
        return _cc_main(rest)
    if sub == "consensus_sweep":
        from bench.consensus_sweep.__main__ import main as _cs_main
        return _cs_main(rest)
    if sub == "roadmap":
        from bench.roadmap.__main__ import main as _roadmap_main
        return _roadmap_main(rest)
    if sub == "ml":
        from bench.ml.__main__ import main as _ml_main
        return _ml_main(rest)
    sys.stderr.write(f"unknown subcommand: {sub!r}\n\n{_USAGE}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
