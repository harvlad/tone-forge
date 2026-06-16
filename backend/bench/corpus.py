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
from typing import Iterable, Mapping, Optional, Tuple

from bench.schema import LICENSE_VOCAB, SPLIT_VOCAB


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
    # --- M2 (schema v2) additive fields -----------------------------
    # Defaults preserve M1 semantics for legacy fixtures: legacy JSON
    # parses as schema_version=1, split=test (treated as held-out
    # regression anchor), license=first-party (curator owns content
    # unless declared otherwise).
    schema_version: int = 1
    split: str = "test"
    genre: Optional[str] = None
    license: str = "first-party"
    tags: Tuple[str, ...] = ()
    curated_by: Optional[str] = None


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


def _extract_v2_metadata(data: Mapping[str, object], json_path: Path) -> dict:
    """Extract schema-v2 fields from ``data`` with M1-compatible defaults.

    Returns a dict keyed by CorpusFixture field name. The defaults
    match the dataclass defaults so legacy v1 JSON loads to
    ``schema_version=1, split="test", license="first-party"`` etc.

    Light validation only: this raises ``ValueError`` for the closed
    vocabularies (``split``, ``license``) because the loader is the
    last line of defence before bad metadata reaches benchmark
    filters. Type-level checks (e.g. tags must be a list of str) are
    deferred to ``bench.schema.validate_fixture_json`` for callers
    who want full validation.
    """
    out: dict[str, object] = {}

    raw_sv = data.get("schema_version")
    if raw_sv is not None and not isinstance(raw_sv, bool) and isinstance(raw_sv, int):
        out["schema_version"] = raw_sv

    raw_split = data.get("split")
    if isinstance(raw_split, str):
        if raw_split not in SPLIT_VOCAB:
            raise ValueError(
                f"{json_path}: split {raw_split!r} not in {sorted(SPLIT_VOCAB)}"
            )
        out["split"] = raw_split

    raw_genre = data.get("genre")
    if isinstance(raw_genre, str):
        out["genre"] = raw_genre
    # null/missing -> dataclass default (None)

    raw_lic = data.get("license")
    if isinstance(raw_lic, str):
        if raw_lic not in LICENSE_VOCAB:
            raise ValueError(
                f"{json_path}: license {raw_lic!r} not in {sorted(LICENSE_VOCAB)}"
            )
        out["license"] = raw_lic

    raw_tags = data.get("tags")
    if isinstance(raw_tags, list):
        tags: list[str] = []
        for t in raw_tags:
            if isinstance(t, str):
                tags.append(t)
        out["tags"] = tuple(tags)

    raw_cb = data.get("curated_by")
    if isinstance(raw_cb, str):
        out["curated_by"] = raw_cb

    return out


def iter_corpus_fixtures(
    fixtures_dir: Optional[Path] = None,
    *,
    require_audio: bool = True,
    splits: Optional[Iterable[str]] = None,
    genres: Optional[Iterable[str]] = None,
    licenses: Optional[Iterable[str]] = None,
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
    splits:
        If provided, keep only fixtures whose ``split`` is in this
        iterable. ``None`` (default) means no filtering on this axis.
    genres:
        If provided, keep only fixtures whose ``genre`` is in this
        iterable. ``None`` matches a fixture whose genre is unset
        only when the literal string ``"unspecified"`` is included.
    licenses:
        If provided, keep only fixtures whose ``license`` is in this
        iterable.

    Multiple filters AND together: a fixture must satisfy every
    non-``None`` filter to be returned.

    Sort order is by fixture ``name`` ascending, which guarantees
    reproducible corpus-mean computation.
    """
    fdir = Path(fixtures_dir) if fixtures_dir is not None else DEFAULT_FIXTURES_DIR
    if not fdir.is_dir():
        raise FileNotFoundError(f"fixtures_dir does not exist: {fdir}")

    split_filter = frozenset(splits) if splits is not None else None
    genre_filter = frozenset(genres) if genres is not None else None
    license_filter = frozenset(licenses) if licenses is not None else None

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

        v2_kwargs = _extract_v2_metadata(data, json_path)

        fixture = CorpusFixture(
            name=name,
            json_path=json_path,
            audio_path=audio_path,
            bass_path=bass_path,
            regions=regions,
            duration_s=duration_s,
            regression_floor_triad_relaxed=floor,
            metadata=data,
            **v2_kwargs,  # type: ignore[arg-type]
        )

        # Apply filters last so unfiltered behaviour matches M1 exactly.
        if split_filter is not None and fixture.split not in split_filter:
            continue
        if genre_filter is not None:
            g = fixture.genre if fixture.genre is not None else "unspecified"
            if g not in genre_filter:
                continue
        if license_filter is not None and fixture.license not in license_filter:
            continue

        fixtures.append(fixture)

    fixtures.sort(key=lambda f: f.name)
    return fixtures


# ===========================================================================
# Curator CLI (M2.4)
# ---------------------------------------------------------------------------
# Three subcommands, all dispatched from ``bench/__main__.py``:
#
#     python -m bench.corpus stats [--fixtures-dir DIR] [--split S] [--json]
#     python -m bench.corpus validate <fixture.json>
#     python -m bench.corpus add --json <path> --other <audio.wav>
#                                [--bass <audio.wav>] [--name NAME]
#                                [--measure-floor]
#                                [--fixtures-dir DIR] [--audio-dir DIR]
#
# No new runtime deps. ``add --measure-floor`` lazy-imports the chord
# detector + audio loaders so plain ``stats`` / ``validate`` runs stay
# fast (and importable without librosa).
# ===========================================================================


DEFAULT_AUDIO_DIR = _BACKEND_ROOT / "data" / "chord_groundtruth_audio"


def _cmd_stats(args) -> int:
    """``python -m bench.corpus stats`` -- tabulate corpus counts."""
    import sys

    splits = [args.split] if args.split else None
    try:
        fixtures = iter_corpus_fixtures(
            fixtures_dir=args.fixtures_dir,
            require_audio=False,
            splits=splits,
        )
    except FileNotFoundError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2

    splits_count: dict[str, int] = {}
    genres_count: dict[str, int] = {}
    licenses_count: dict[str, int] = {}
    total_duration = 0.0
    for f in fixtures:
        splits_count[f.split] = splits_count.get(f.split, 0) + 1
        g = f.genre if f.genre is not None else "unspecified"
        genres_count[g] = genres_count.get(g, 0) + 1
        licenses_count[f.license] = licenses_count.get(f.license, 0) + 1
        total_duration += f.duration_s

    if args.json:
        payload = {
            "n_fixtures": len(fixtures),
            "total_duration_s": round(total_duration, 3),
            "splits": splits_count,
            "genres": genres_count,
            "licenses": licenses_count,
            "fixtures": [
                {
                    "name": f.name,
                    "duration_s": f.duration_s,
                    "split": f.split,
                    "genre": f.genre,
                    "license": f.license,
                    "tags": list(f.tags),
                    "curated_by": f.curated_by,
                    "regression_floor_triad_relaxed": (
                        f.regression_floor_triad_relaxed
                    ),
                }
                for f in fixtures
            ],
        }
        sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        return 0

    # Plain-text output.
    out = sys.stdout
    out.write(f"Corpus: {len(fixtures)} fixtures, ")
    out.write(f"{total_duration / 60.0:.2f} min total\n")
    out.write("\n")

    def _write_count(title: str, counts: dict[str, int]) -> None:
        out.write(f"{title}:\n")
        if not counts:
            out.write("  (empty)\n")
            return
        for k in sorted(counts):
            out.write(f"  {k:<24s} {counts[k]:>4d}\n")

    _write_count("By split", splits_count)
    out.write("\n")
    _write_count("By genre", genres_count)
    out.write("\n")
    _write_count("By license", licenses_count)
    out.write("\n")
    out.write("Fixtures:\n")
    for f in fixtures:
        genre = f.genre if f.genre is not None else "unspecified"
        out.write(
            f"  {f.name:<28s} {f.duration_s:>8.2f}s "
            f"split={f.split:<8s} genre={genre:<12s} "
            f"floor={f.regression_floor_triad_relaxed:.2f}\n"
        )
    return 0


def _cmd_validate(args) -> int:
    """``python -m bench.corpus validate <fixture.json>``."""
    import sys

    # Lazy import to keep CLI startup cheap.
    from bench.schema import validate_fixture_json

    path = Path(args.path)
    if not path.exists():
        sys.stderr.write(f"file not found: {path}\n")
        return 2
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"{path}: invalid JSON: {exc}\n")
        return 1

    errors = validate_fixture_json(data)
    if errors:
        for err in errors:
            sys.stderr.write(f"{path}: {err}\n")
        return 1
    sys.stdout.write(f"{path}: OK\n")
    return 0


def _slug_from_metadata(data: Mapping[str, object], fallback: str) -> str:
    """Derive a filesystem-safe slug from the JSON's ``song`` field.

    Falls back to ``fallback`` (typically the JSON filename stem) if
    ``song`` is missing or empty. Only strips/replaces characters
    that are problematic in path components.
    """
    raw = data.get("song")
    if not isinstance(raw, str) or not raw.strip():
        return fallback
    cleaned: list[str] = []
    for ch in raw.lower().strip():
        if ch.isalnum():
            cleaned.append(ch)
        elif ch in (" ", "-", "_"):
            cleaned.append("_")
        # other punctuation is dropped
    slug = "".join(cleaned).strip("_")
    return slug or fallback


def _measure_triad_relaxed_floor(
    audio_path: Path, bass_path: Optional[Path], data: Mapping[str, object]
) -> float:
    """Run the production detector and return triad-relaxed WCSR.

    Mirrors the pattern in ``bench.benchmark._detect_one`` +
    ``_per_fixture_metrics``: default DetectorConfig, librosa load
    at 22050 Hz mono, best-effort beat track, ``detect_chords_from_audio``.

    Lazy imports keep the rest of the CLI cheap.
    """
    import librosa
    import numpy as np

    from tone_forge.analysis.chord_detector import detect_chords_from_audio
    from tone_forge.analysis.detector_config import DetectorConfig
    from bench.metrics import triad_relaxed_wcsr_score

    y, sr = librosa.load(str(audio_path), sr=22050, mono=True)
    bass_y = None
    if bass_path is not None and bass_path.exists():
        bass_y, _ = librosa.load(str(bass_path), sr=sr, mono=True)

    beats_s = None
    try:
        tempo_raw, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
        tempo_val = float(np.asarray(tempo_raw).item())
        if 40 <= tempo_val <= 240 and len(beat_frames) >= 2:
            beats_s = librosa.frames_to_time(beat_frames, sr=sr)
    except Exception:
        beats_s = None

    predicted = detect_chords_from_audio(
        y, sr, bass_y=bass_y, beats_s=beats_s, config=DetectorConfig()
    )

    ref_regions = _parse_regions(data["regions"])
    duration_s = float(data["duration_s"])
    return triad_relaxed_wcsr_score(predicted, list(ref_regions), duration_s)


def _cmd_add(args) -> int:
    """``python -m bench.corpus add`` -- ingest a new fixture."""
    import shutil
    import sys
    import time as _time

    from bench.schema import validate_fixture_json

    # 1. Load + validate the source JSON.
    json_src = Path(args.json)
    if not json_src.exists():
        sys.stderr.write(f"--json not found: {json_src}\n")
        return 2
    data = json.loads(json_src.read_text(encoding="utf-8"))
    errors = validate_fixture_json(data)
    if errors:
        sys.stderr.write(f"{json_src}: schema validation failed\n")
        for err in errors:
            sys.stderr.write(f"  - {err}\n")
        return 1

    # 2. Validate audio inputs exist.
    other_src = Path(args.other)
    if not other_src.exists():
        sys.stderr.write(f"--other not found: {other_src}\n")
        return 2
    bass_src = Path(args.bass) if args.bass else None
    if bass_src is not None and not bass_src.exists():
        sys.stderr.write(f"--bass not found: {bass_src}\n")
        return 2

    # 3. Resolve fixture name.
    fallback = json_src.stem
    name = args.name if args.name else _slug_from_metadata(data, fallback)

    fixtures_dir = (
        Path(args.fixtures_dir) if args.fixtures_dir else DEFAULT_FIXTURES_DIR
    )
    audio_dir = (
        Path(args.audio_dir) if args.audio_dir else DEFAULT_AUDIO_DIR
    )
    fixtures_dir.mkdir(parents=True, exist_ok=True)
    audio_dir.mkdir(parents=True, exist_ok=True)

    dst_audio_dir = audio_dir / name
    dst_audio_dir.mkdir(parents=True, exist_ok=True)
    dst_other = dst_audio_dir / "other.wav"
    dst_bass = dst_audio_dir / "bass.wav"

    # 4. Copy audio.
    shutil.copy2(other_src, dst_other)
    if bass_src is not None:
        shutil.copy2(bass_src, dst_bass)

    # 5. Update JSON's audio paths. Prefer a path relative to
    #    ``backend/`` (matches M1 fixture convention); fall back to
    #    absolute when the audio-dir is outside the backend tree
    #    (e.g. ad-hoc curator runs into a scratch directory).
    def _rel_or_abs(p: Path) -> str:
        try:
            return p.relative_to(_BACKEND_ROOT).as_posix()
        except ValueError:
            return str(p.resolve())

    data = dict(data)  # shallow copy; original was a Mapping
    data["source_audio_other_stem"] = _rel_or_abs(dst_other)
    if bass_src is not None:
        data["source_audio_bass_stem"] = _rel_or_abs(dst_bass)

    # 6. Stamp curator metadata if absent.
    if "schema_version" not in data:
        data["schema_version"] = 2
    if "added_at_unix" not in data:
        data["added_at_unix"] = int(_time.time())

    # 7. Optional floor measurement.
    if args.measure_floor:
        sys.stderr.write(f"measuring triad-relaxed WCSR floor for {name}...\n")
        measured = _measure_triad_relaxed_floor(
            dst_other, dst_bass if bass_src is not None else None, data
        )
        # Round DOWN to nearest 0.01 (conservative; matches pub_feed
        # pattern: 0.2347 measured -> 0.22 pinned).
        floor = int(measured * 100.0) / 100.0
        sys.stderr.write(
            f"  measured={measured:.4f} -> floor={floor:.2f}\n"
        )
        data["regression_floor_triad_relaxed"] = floor

    # 8. Write out the canonical fixture JSON.
    dst_json = fixtures_dir / f"{name}.json"
    dst_json.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    sys.stdout.write(f"added fixture {name!r}\n")
    sys.stdout.write(f"  json:  {dst_json}\n")
    sys.stdout.write(f"  audio: {dst_other}\n")
    if bass_src is not None:
        sys.stdout.write(f"  bass:  {dst_bass}\n")
    sys.stdout.write(
        f"  floor: {data['regression_floor_triad_relaxed']}\n"
    )
    sys.stdout.write(
        "\nNext: run `python -m bench.benchmark` to confirm the new "
        "fixture loads and the corpus mean is sensible.\n"
    )
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    """``python -m bench.corpus`` dispatcher.

    Returns a UNIX exit code (0 = success, 1 = validation/usage
    failure, 2 = file-not-found / argparse error).
    """
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="python -m bench.corpus",
        description="Curator-facing corpus CLI (M2).",
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    p_stats = sub.add_parser(
        "stats", help="Tabulate fixture counts by split/genre/license."
    )
    p_stats.add_argument("--fixtures-dir", type=Path, default=None)
    p_stats.add_argument(
        "--split",
        choices=("train", "val", "test", "holdout"),
        default=None,
        help="Limit stats to a single split.",
    )
    p_stats.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON."
    )

    p_val = sub.add_parser(
        "validate",
        help="Validate a fixture JSON against schema v2.",
    )
    p_val.add_argument("path", type=Path, help="Path to fixture JSON.")

    p_add = sub.add_parser(
        "add",
        help="Ingest a new fixture: copy audio, write canonical JSON.",
    )
    p_add.add_argument(
        "--json", dest="json", required=True, type=Path,
        help="Path to the source fixture JSON (schema v2).",
    )
    p_add.add_argument(
        "--other", required=True, type=Path,
        help="Path to the 'other' stem WAV (production-routing input).",
    )
    p_add.add_argument(
        "--bass", required=False, type=Path, default=None,
        help="Optional path to the bass stem WAV.",
    )
    p_add.add_argument(
        "--name", required=False, default=None,
        help="Override fixture slug (defaults to slugified JSON 'song').",
    )
    p_add.add_argument(
        "--measure-floor", action="store_true",
        help="Run detector and pin regression_floor_triad_relaxed.",
    )
    p_add.add_argument("--fixtures-dir", type=Path, default=None)
    p_add.add_argument("--audio-dir", type=Path, default=None)

    args = parser.parse_args(argv)

    if args.subcommand == "stats":
        return _cmd_stats(args)
    if args.subcommand == "validate":
        return _cmd_validate(args)
    if args.subcommand == "add":
        return _cmd_add(args)
    # argparse with required=True should make this unreachable.
    sys.stderr.write("no subcommand\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
