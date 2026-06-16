"""Corpus loader for the chord-detector benchmark.

Walks ``backend/tests/fixtures/chord_groundtruth/*.json``, resolves
audio paths relative to ``backend/``, and yields a stably-sorted
list of ``CorpusFixture`` records.

Strictly read-only with respect to ``tone_forge`` and the fixture
directory: this module never writes JSON or audio. It is the M1.3
piece of the benchmark substrate; ``bench.benchmark`` (M1.4)
consumes its output.

Fixture JSON schema (the relevant subset; extra fields are
preserved verbatim in ``CorpusFixture.metadata``)::

    {
      "duration_s": <float>,
      "regions": [
        {"start": <float>, "end": <float>, "label": <str>, ...},
        ...
      ],
      "regression_floor_triad_relaxed": <float>,
      "source_audio_other_stem": "<relative-to-backend>",   # optional
      "source_audio_bass_stem":  "<relative-to-backend>",   # optional
    }
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional, Tuple


__all__ = ["CorpusFixture", "iter_corpus_fixtures", "DEFAULT_FIXTURES_DIR"]


# ``backend/`` is two parents up from this file:
#   .../backend/bench/corpus.py -> parents[1] = .../backend
_BACKEND_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_FIXTURES_DIR = _BACKEND_ROOT / "tests" / "fixtures" / "chord_groundtruth"


@dataclass(frozen=True)
class CorpusFixture:
    """A single corpus entry consumed by ``bench.benchmark``.

    ``audio_path`` is the "other" stem when available (matches
    production routing); ``bass_path`` is the bass stem when
    available. Either may be ``None`` if the JSON omits the
    corresponding field, but the iterator skips fixtures that lack
    a usable ``audio_path`` when ``require_audio=True``.
    """

    name: str
    json_path: Path
    audio_path: Optional[Path]
    bass_path: Optional[Path]
    regions: Tuple[Tuple[float, float, str], ...]
    duration_s: float
    regression_floor_triad_relaxed: float
    metadata: Mapping[str, object] = field(default_factory=dict)


def _resolve_audio(rel_or_abs: str) -> Path:
    """Resolve an audio path string against ``backend/`` if relative."""
    p = Path(rel_or_abs)
    if not p.is_absolute():
        p = _BACKEND_ROOT / p
    return p


def _parse_regions(raw: object) -> Tuple[Tuple[float, float, str], ...]:
    """Convert the JSON ``regions`` array into the canonical tuple shape.

    Accepts the dict-with-start/end/label shape used by the on-disk
    fixtures. Returns an immutable tuple so ``CorpusFixture`` stays
    hashable.
    """
    if not isinstance(raw, list):
        raise ValueError(f"regions must be a list, got {type(raw).__name__}")
    out: list[tuple[float, float, str]] = []
    for i, r in enumerate(raw):
        if not isinstance(r, dict):
            raise ValueError(f"regions[{i}] must be an object, got {type(r).__name__}")
        try:
            start = float(r["start"])
            end = float(r["end"])
            label = str(r["label"])
        except KeyError as exc:
            raise ValueError(f"regions[{i}] missing required key {exc!r}") from exc
        out.append((start, end, label))
    return tuple(out)


def iter_corpus_fixtures(
    fixtures_dir: Optional[Path] = None,
    *,
    require_audio: bool = True,
) -> list[CorpusFixture]:
    """Load corpus fixtures from ``fixtures_dir`` (or the default).

    Parameters
    ----------
    fixtures_dir:
        Directory containing ``*.json`` ground-truth files. Defaults
        to ``backend/tests/fixtures/chord_groundtruth``.
    require_audio:
        When True (the default), fixtures whose ``audio_path`` is
        absent OR whose audio file does not exist on disk are
        SILENTLY DROPPED from the returned list. When False, every
        well-formed JSON file is returned regardless of whether
        the audio is available locally. This is the CI / dry-run
        affordance: ``test_bench_corpus`` exercises the JSON
        parsing path without needing demucs stems on disk.

    Sort order is by fixture ``name`` ascending, which guarantees
    reproducible corpus-mean computation.
    """
    fdir = Path(fixtures_dir) if fixtures_dir is not None else DEFAULT_FIXTURES_DIR
    if not fdir.is_dir():
        raise FileNotFoundError(f"fixtures_dir does not exist: {fdir}")

    fixtures: list[CorpusFixture] = []
    for json_path in sorted(fdir.glob("*.json")):
        with json_path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise ValueError(f"{json_path}: top-level must be an object")

        name = json_path.stem
        try:
            duration_s = float(data["duration_s"])
            regions = _parse_regions(data["regions"])
            floor = float(data["regression_floor_triad_relaxed"])
        except KeyError as exc:
            raise ValueError(f"{json_path}: missing required key {exc!r}") from exc

        audio_rel = data.get("source_audio_other_stem")
        bass_rel = data.get("source_audio_bass_stem")
        audio_path = _resolve_audio(audio_rel) if isinstance(audio_rel, str) else None
        bass_path = _resolve_audio(bass_rel) if isinstance(bass_rel, str) else None

        if require_audio:
            if audio_path is None or not audio_path.exists():
                continue

        fixtures.append(
            CorpusFixture(
                name=name,
                json_path=json_path,
                audio_path=audio_path,
                bass_path=bass_path,
                regions=regions,
                duration_s=duration_s,
                regression_floor_triad_relaxed=floor,
                metadata=data,
            )
        )

    fixtures.sort(key=lambda f: f.name)
    return fixtures
