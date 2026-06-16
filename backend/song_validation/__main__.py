"""Module entry-point for ``python -m song_validation``.

Thin shim around :func:`song_validation.cli.main` so the CLI can be
invoked without an installed console script. See ``cli.py`` for the
subcommand surface.
"""

from __future__ import annotations

import sys

from .cli import main


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
