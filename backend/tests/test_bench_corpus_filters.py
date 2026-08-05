"""Tests for the M2.3 filter kwargs on ``iter_corpus_fixtures``.

Verifies that:

* When all filter kwargs are ``None`` (default), returns all corpus
  fixtures.
* Each filter (``splits``, ``genres``, ``licenses``) independently
  narrows the returned set.
* Multiple filters AND together (fixture must satisfy every
  non-``None`` filter).
* An over-restrictive filter (no match) returns an empty list, not
  an error.
* ``genres=("unspecified",)`` matches fixtures whose ``genre`` is
  unset (None / missing).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from bench.corpus import iter_corpus_fixtures


# ---------------------------------------------------------------------------
# Real-corpus filters (22 midi-derived fixtures post-M3 expansion)
# ---------------------------------------------------------------------------
# Corpus composition: 21 synth (midi-derived) + 1 rock (pub_feed)
# All split='test', all license='first-party'


def test_no_filters_returns_all_corpus_fixtures() -> None:
    out = iter_corpus_fixtures(require_audio=False)
    assert len(out) >= 22  # corpus may grow; never shrink


def test_split_test_returns_all_corpus_fixtures() -> None:
    out = iter_corpus_fixtures(require_audio=False, splits=["test"])
    assert len(out) >= 22  # all are split=test
    assert {f.split for f in out} == {"test"}


def test_split_train_returns_empty() -> None:
    out = iter_corpus_fixtures(require_audio=False, splits=["train"])
    assert out == []


def test_split_holdout_returns_empty() -> None:
    out = iter_corpus_fixtures(require_audio=False, splits=["holdout"])
    assert out == []


def test_split_train_or_test_returns_all() -> None:
    out = iter_corpus_fixtures(require_audio=False, splits=["train", "test"])
    assert len(out) >= 22


def test_genre_rock_returns_pub_feed() -> None:
    out = iter_corpus_fixtures(require_audio=False, genres=["rock"])
    assert [f.name for f in out] == ["pub_feed"]


def test_genre_synth_returns_midi_derived_fixtures() -> None:
    out = iter_corpus_fixtures(require_audio=False, genres=["synth"])
    names = sorted(f.name for f in out)
    # At minimum contains the baseline fixtures re-derived from MIDI
    assert "demolition_warning" in names
    assert "jump_and_die" in names
    assert len(names) >= 21


def test_genre_rock_or_synth_returns_all() -> None:
    out = iter_corpus_fixtures(require_audio=False, genres=["rock", "synth"])
    assert len(out) >= 22


def test_license_first_party_returns_all() -> None:
    out = iter_corpus_fixtures(require_audio=False, licenses=["first-party"])
    assert len(out) >= 22


def test_license_other_returns_empty() -> None:
    out = iter_corpus_fixtures(require_audio=False, licenses=["other"])
    assert out == []


# ---------------------------------------------------------------------------
# AND semantics
# ---------------------------------------------------------------------------


def test_split_test_and_genre_rock_returns_pub_feed_only() -> None:
    out = iter_corpus_fixtures(
        require_audio=False, splits=["test"], genres=["rock"]
    )
    assert [f.name for f in out] == ["pub_feed"]


def test_split_train_and_genre_rock_returns_empty() -> None:
    # split filter excludes all 4, no rock left.
    out = iter_corpus_fixtures(
        require_audio=False, splits=["train"], genres=["rock"]
    )
    assert out == []


def test_three_filters_all_must_match() -> None:
    out = iter_corpus_fixtures(
        require_audio=False,
        splits=["test"],
        genres=["rock"],
        licenses=["first-party"],
    )
    assert [f.name for f in out] == ["pub_feed"]


def test_one_failing_filter_drops_the_fixture() -> None:
    # Right split + right license + WRONG genre -> dropped.
    out = iter_corpus_fixtures(
        require_audio=False,
        splits=["test"],
        genres=["jazz"],
        licenses=["first-party"],
    )
    assert out == []


# ---------------------------------------------------------------------------
# Synthetic-corpus filter behaviour (lets us probe rarer corners)
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


def test_split_kwarg_filters_synthetic_corpus(tmp_path: Path) -> None:
    _write_v2(tmp_path, "a", split="train")
    _write_v2(tmp_path, "b", split="val")
    _write_v2(tmp_path, "c", split="test")
    _write_v2(tmp_path, "d", split="holdout")

    train = iter_corpus_fixtures(tmp_path, require_audio=False, splits=["train"])
    assert [f.name for f in train] == ["a"]

    train_val = iter_corpus_fixtures(
        tmp_path, require_audio=False, splits=["train", "val"]
    )
    assert sorted(f.name for f in train_val) == ["a", "b"]

    all_four = iter_corpus_fixtures(tmp_path, require_audio=False)
    assert len(all_four) == 4


def test_genres_unspecified_matches_missing_genre(tmp_path: Path) -> None:
    # Fixture with no genre field at all.
    _write_v2(tmp_path, "no_genre")
    # Fixture with genre set.
    _write_v2(tmp_path, "rock_one", genre="rock")

    # genres=["unspecified"] picks up the one with no genre.
    out = iter_corpus_fixtures(
        tmp_path, require_audio=False, genres=["unspecified"]
    )
    assert [f.name for f in out] == ["no_genre"]

    # genres=["rock", "unspecified"] picks up both.
    out2 = iter_corpus_fixtures(
        tmp_path, require_audio=False, genres=["rock", "unspecified"]
    )
    assert sorted(f.name for f in out2) == ["no_genre", "rock_one"]


def test_license_kwarg_filters_synthetic_corpus(tmp_path: Path) -> None:
    _write_v2(tmp_path, "fp", license="first-party")
    _write_v2(tmp_path, "cc", license="cc-by-4.0")
    _write_v2(tmp_path, "pd", license="public-domain")

    cc = iter_corpus_fixtures(
        tmp_path, require_audio=False, licenses=["cc-by-4.0"]
    )
    assert [f.name for f in cc] == ["cc"]

    open_only = iter_corpus_fixtures(
        tmp_path,
        require_audio=False,
        licenses=["cc-by-4.0", "cc-by-sa-4.0", "public-domain"],
    )
    assert sorted(f.name for f in open_only) == ["cc", "pd"]


def test_empty_filter_iterable_returns_empty(tmp_path: Path) -> None:
    """An empty (but non-None) iterable filter should match nothing."""
    _write_v2(tmp_path, "a")
    out = iter_corpus_fixtures(tmp_path, require_audio=False, splits=[])
    assert out == []


def test_filter_accepts_arbitrary_iterables(tmp_path: Path) -> None:
    """Filter kwargs accept any iterable (set / tuple / generator)."""
    _write_v2(tmp_path, "a", genre="rock")
    _write_v2(tmp_path, "b", genre="punk")

    # Set
    assert len(iter_corpus_fixtures(
        tmp_path, require_audio=False, genres={"rock", "punk"}
    )) == 2
    # Tuple
    assert len(iter_corpus_fixtures(
        tmp_path, require_audio=False, genres=("rock",)
    )) == 1
    # Generator
    assert len(iter_corpus_fixtures(
        tmp_path, require_audio=False, genres=(g for g in ["punk"])
    )) == 1


def test_filter_sort_order_unchanged(tmp_path: Path) -> None:
    _write_v2(tmp_path, "charlie", split="test")
    _write_v2(tmp_path, "alpha", split="test")
    _write_v2(tmp_path, "bravo", split="train")

    out = iter_corpus_fixtures(tmp_path, require_audio=False, splits=["test"])
    assert [f.name for f in out] == ["alpha", "charlie"]
