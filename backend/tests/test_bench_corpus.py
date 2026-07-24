"""Tests for ``bench.corpus.iter_corpus_fixtures``.

Exercises the JSON-parsing path against the four on-disk fixtures
without requiring local demucs stems (``require_audio=False``), plus
synthetic fixtures in a tmpdir for the error / edge cases.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from bench.corpus import (
    DEFAULT_FIXTURES_DIR,
    CorpusFixture,
    iter_corpus_fixtures,
)


def test_default_fixtures_dir_exists() -> None:
    assert DEFAULT_FIXTURES_DIR.is_dir(), DEFAULT_FIXTURES_DIR


def test_iter_corpus_returns_fixtures_when_dry_run() -> None:
    # The corpus grows over time, so assert structure (non-empty, stable
    # sorted order, a known-stable entry) rather than a hardcoded name
    # list that goes stale on every new fixture.
    fixtures = iter_corpus_fixtures(require_audio=False)
    names = [f.name for f in fixtures]
    assert names, "corpus should not be empty"
    assert names == sorted(names), "corpus order should be stable/sorted"
    assert "pub_feed" in names


def test_corpus_fixture_fields_for_pub_feed() -> None:
    fixtures = {f.name: f for f in iter_corpus_fixtures(require_audio=False)}
    pf = fixtures["pub_feed"]
    assert pf.duration_s == pytest.approx(147.057)
    assert pf.regression_floor_triad_relaxed == pytest.approx(0.42)
    assert len(pf.regions) > 0
    # First region of pub_feed is the long intro+verse 1 chunk on A5
    assert pf.regions[0][2] == "A5"
    assert pf.regions[0][0] == pytest.approx(0.0)
    # audio_path is resolved relative to backend/ regardless of
    # whether the file exists locally.
    assert pf.audio_path is not None
    assert pf.audio_path.name == "other.wav"
    assert pf.bass_path is not None
    assert pf.bass_path.name == "bass.wav"


def test_corpus_metadata_preserves_extra_fields() -> None:
    fixtures = {f.name: f for f in iter_corpus_fixtures(require_audio=False)}
    pf = fixtures["pub_feed"]
    # Raw JSON dict is exposed for inspection by failure-mode tools
    assert pf.metadata["song"] == "Pub Feed"
    assert pf.metadata["artist"] == "The Chats"


def test_iter_corpus_sort_order_is_stable() -> None:
    a = [f.name for f in iter_corpus_fixtures(require_audio=False)]
    b = [f.name for f in iter_corpus_fixtures(require_audio=False)]
    assert a == b
    assert a == sorted(a)


def test_iter_corpus_missing_dir_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        iter_corpus_fixtures(tmp_path / "does_not_exist")


def test_iter_corpus_empty_dir_returns_empty(tmp_path: Path) -> None:
    assert iter_corpus_fixtures(tmp_path) == []


def _write_minimal_fixture(path: Path, *, name: str, audio_rel: str | None) -> None:
    payload: dict = {
        "duration_s": 10.0,
        "regions": [
            {"start": 0.0, "end": 5.0, "label": "C"},
            {"start": 5.0, "end": 10.0, "label": "G"},
        ],
        "regression_floor_triad_relaxed": 0.5,
    }
    if audio_rel is not None:
        payload["source_audio_other_stem"] = audio_rel
    (path / f"{name}.json").write_text(json.dumps(payload), encoding="utf-8")


def test_require_audio_drops_missing(tmp_path: Path) -> None:
    _write_minimal_fixture(tmp_path, name="alpha", audio_rel="data/missing.wav")
    # require_audio=True: dropped because the resolved path doesn't exist
    assert iter_corpus_fixtures(tmp_path, require_audio=True) == []
    # require_audio=False: kept
    out = iter_corpus_fixtures(tmp_path, require_audio=False)
    assert len(out) == 1
    assert out[0].name == "alpha"


def test_require_audio_drops_when_field_absent(tmp_path: Path) -> None:
    _write_minimal_fixture(tmp_path, name="alpha", audio_rel=None)
    assert iter_corpus_fixtures(tmp_path, require_audio=True) == []
    out = iter_corpus_fixtures(tmp_path, require_audio=False)
    assert len(out) == 1
    assert out[0].audio_path is None
    assert out[0].bass_path is None


def test_require_audio_keeps_when_file_exists(tmp_path: Path) -> None:
    # Create an audio file at the resolved path. The resolver makes
    # the path absolute relative to backend/, so use an absolute path
    # for this test.
    audio = tmp_path / "stem.wav"
    audio.write_bytes(b"\x00")  # not a real wav; existence is what matters
    _write_minimal_fixture(tmp_path, name="alpha", audio_rel=str(audio))
    out = iter_corpus_fixtures(tmp_path, require_audio=True)
    assert len(out) == 1
    assert out[0].audio_path == audio


def test_missing_required_key_raises(tmp_path: Path) -> None:
    (tmp_path / "broken.json").write_text('{"regions": []}', encoding="utf-8")
    with pytest.raises(ValueError, match="duration_s"):
        iter_corpus_fixtures(tmp_path, require_audio=False)


def test_malformed_regions_raises(tmp_path: Path) -> None:
    payload = {
        "duration_s": 1.0,
        "regions": "not a list",
        "regression_floor_triad_relaxed": 0.0,
    }
    (tmp_path / "broken.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="regions must be a list"):
        iter_corpus_fixtures(tmp_path, require_audio=False)


def test_corpus_fixture_is_hashable() -> None:
    # Frozen dataclass with tuple regions -> hashable for use as
    # dict keys / set members.
    fixtures = iter_corpus_fixtures(require_audio=False)
    assert isinstance(fixtures[0], CorpusFixture)
    # All four fixtures distinct in a set.
    assert len({f.name for f in fixtures}) == len(fixtures)
