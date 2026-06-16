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
    "usage: python -m bench {benchmark,sweep} [<args>...]\n"
    "\n"
    "Subcommands:\n"
    "  benchmark  Run the detector against the corpus, write RunRecord JSON\n"
    "  sweep      Run a parameter sweep against a corpus baseline\n"
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
    sys.stderr.write(f"unknown subcommand: {sub!r}\n\n{_USAGE}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
