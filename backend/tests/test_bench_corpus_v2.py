"""Tests for the M2 schema-v2 fields on ``CorpusFixture``.

Covers:

* The four annotated M1 fixtures load with their declared metadata.
* Legacy v1 JSON (no v2 fields) yields the documented defaults
  (``schema_version=1, split="test", license="first-party"`` etc).
* Closed-vocab violations in JSON raise ``ValueError`` at load time
  (loader is the last line of defence before bad metadata reaches
  benchmark filters).
* ``CorpusFixture`` remains a frozen, hashable dataclass with the
  expanded field set.
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


# ---------------------------------------------------------------------------
# Annotated M1 fixtures (M2.6)
# ---------------------------------------------------------------------------


def test_all_m1_fixtures_are_schema_version_2() -> None:
    fixtures = iter_corpus_fixtures(require_audio=False)
    assert len(fixtures) == 4
    for f in fixtures:
        assert f.schema_version == 2, f.name


def test_all_m1_fixtures_have_split_test() -> None:
    fixtures = iter_corpus_fixtures(require_audio=False)
    for f in fixtures:
        assert f.split == "test", f.name


def test_all_m1_fixtures_have_license_first_party() -> None:
    fixtures = iter_corpus_fixtures(require_audio=False)
    for f in fixtures:
        assert f.license == "first-party", f.name


def test_all_m1_fixtures_curated_by_matt() -> None:
    fixtures = iter_corpus_fixtures(require_audio=False)
    for f in fixtures:
        assert f.curated_by == "matt", f.name


def test_pub_feed_annotation() -> None:
    fixtures = {f.name: f for f in iter_corpus_fixtures(require_audio=False)}
    pf = fixtures["pub_feed"]
    assert pf.genre == "rock"
    assert "power-chords" in pf.tags
    assert "hand-validated" in pf.tags
    assert isinstance(pf.tags, tuple)


@pytest.mark.parametrize(
    "name", ["demolition_warning", "jump_and_die", "lets_make_it_pain"]
)
def test_baseline_captured_fixtures_have_baseline_tag(name: str) -> None:
    fixtures = {f.name: f for f in iter_corpus_fixtures(require_audio=False)}
    assert "baseline-captured" in fixtures[name].tags


@pytest.mark.parametrize(
    "name", ["demolition_warning", "jump_and_die", "lets_make_it_pain"]
)
def test_baseline_captured_fixtures_have_punk_genre(name: str) -> None:
    fixtures = {f.name: f for f in iter_corpus_fixtures(require_audio=False)}
    assert fixtures[name].genre == "punk"


# ---------------------------------------------------------------------------
# Legacy-v1 defaults
# ---------------------------------------------------------------------------


def _write_legacy_v1(path: Path, name: str) -> None:
    """Write a minimal v1 fixture (no v2 fields)."""
    payload = {
        "duration_s": 10.0,
        "regions": [{"start": 0.0, "end": 10.0, "label": "C:maj"}],
        "regression_floor_triad_relaxed": 0.5,
    }
    (path / f"{name}.json").write_text(json.dumps(payload), encoding="utf-8")


def test_legacy_v1_fixture_loads_with_defaults(tmp_path: Path) -> None:
    _write_legacy_v1(tmp_path, "legacy")
    out = iter_corpus_fixtures(tmp_path, require_audio=False)
    assert len(out) == 1
    f = out[0]
    assert f.schema_version == 1
    assert f.split == "test"
    assert f.genre is None
    assert f.license == "first-party"
    assert f.tags == ()
    assert f.curated_by is None


def test_partial_v2_fixture_fills_missing_with_defaults(tmp_path: Path) -> None:
    # Only schema_version + genre set; rest should default.
    payload = {
        "duration_s": 10.0,
        "regions": [{"start": 0.0, "end": 10.0, "label": "C:maj"}],
        "regression_floor_triad_relaxed": 0.5,
        "schema_version": 2,
        "genre": "jazz",
    }
    (tmp_path / "partial.json").write_text(json.dumps(payload), encoding="utf-8")
    out = iter_corpus_fixtures(tmp_path, require_audio=False)
    assert len(out) == 1
    f = out[0]
    assert f.schema_version == 2
    assert f.genre == "jazz"
    assert f.split == "test"
    assert f.license == "first-party"
    assert f.tags == ()
    assert f.curated_by is None


# ---------------------------------------------------------------------------
# Closed-vocab violations
# ---------------------------------------------------------------------------


def _write_v2(path: Path, name: str, **overrides: object) -> None:
    payload: dict[str, object] = {
        "duration_s": 10.0,
        "regions": [{"start": 0.0, "end": 10.0, "label": "C:maj"}],
        "regression_floor_triad_relaxed": 0.5,
        "schema_version": 2,
        "split": "test",
        "license": "first-party",
    }
    payload.update(overrides)
    (path / f"{name}.json").write_text(json.dumps(payload), encoding="utf-8")


def test_invalid_split_at_load_raises(tmp_path: Path) -> None:
    _write_v2(tmp_path, "bad_split", split="production")
    with pytest.raises(ValueError, match="split 'production' not in"):
        iter_corpus_fixtures(tmp_path, require_audio=False)


def test_invalid_license_at_load_raises(tmp_path: Path) -> None:
    _write_v2(tmp_path, "bad_lic", license="MIT")
    with pytest.raises(ValueError, match="license 'MIT' not in"):
        iter_corpus_fixtures(tmp_path, require_audio=False)


def test_unknown_genre_is_accepted(tmp_path: Path) -> None:
    # Genre vocabulary is OPEN (free-form). "experimental-noise-jazz"
    # should pass.
    _write_v2(tmp_path, "weird_genre", genre="experimental-noise-jazz")
    out = iter_corpus_fixtures(tmp_path, require_audio=False)
    assert out[0].genre == "experimental-noise-jazz"


def test_string_tags_are_preserved(tmp_path: Path) -> None:
    _write_v2(tmp_path, "tags", tags=["a", "b", "c"])
    out = iter_corpus_fixtures(tmp_path, require_audio=False)
    assert out[0].tags == ("a", "b", "c")


def test_non_string_tag_entries_are_silently_dropped(tmp_path: Path) -> None:
    # Loader is permissive on tags (validator is the strict checker).
    _write_v2(tmp_path, "mixed_tags", tags=["ok", 42, None, "also-ok"])
    out = iter_corpus_fixtures(tmp_path, require_audio=False)
    assert out[0].tags == ("ok", "also-ok")


# ---------------------------------------------------------------------------
# Hashable + immutable
# ---------------------------------------------------------------------------


def test_v2_fixture_tags_are_tuple(tmp_path: Path) -> None:
    """Tags are an immutable tuple, not a list (parallel to ``regions``)."""
    _write_v2(tmp_path, "a", tags=["x", "y"])
    out = iter_corpus_fixtures(tmp_path, require_audio=False)
    assert isinstance(out[0].tags, tuple)
    assert out[0].tags == ("x", "y")


def test_v2_fixture_is_frozen(tmp_path: Path) -> None:
    _write_v2(tmp_path, "a")
    f = iter_corpus_fixtures(tmp_path, require_audio=False)[0]
    with pytest.raises((AttributeError, Exception)):
        f.split = "train"  # type: ignore[misc]


def test_default_fixtures_dir_exists() -> None:
    """Sanity check: M1 fixture directory still exists post-annotation."""
    assert DEFAULT_FIXTURES_DIR.is_dir()
