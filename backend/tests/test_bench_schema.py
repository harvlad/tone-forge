"""Tests for ``bench.schema.validate_fixture_json``.

Every closed-vocab + numeric-range branch is exercised against
hand-crafted good/bad inputs. The validator is a pure function
(no I/O), so all inputs are inline dicts.

Tests are organised by validation rule, with a separate group for
the v1-only baseline (legacy compatibility) and the v2 additive
fields.
"""
from __future__ import annotations

from typing import Mapping

import pytest

from bench.schema import (
    LICENSE_VOCAB,
    SCHEMA_VERSION_LATEST,
    SCHEMA_VERSIONS_SUPPORTED,
    SPLIT_VOCAB,
    validate_fixture_json,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_valid_v1() -> dict:
    """Smallest v1-compatible fixture dict (no v2 metadata)."""
    return {
        "duration_s": 10.0,
        "regions": [
            {"start": 0.0, "end": 5.0, "label": "C:maj"},
            {"start": 5.0, "end": 10.0, "label": "G:maj"},
        ],
        "regression_floor_triad_relaxed": 0.5,
    }


def _minimal_valid_v2() -> dict:
    """Smallest v2-compatible fixture dict (all v2 fields populated)."""
    data = _minimal_valid_v1()
    data.update(
        {
            "schema_version": 2,
            "split": "test",
            "genre": "rock",
            "license": "first-party",
            "tags": ["power-chords"],
            "curated_by": "matt",
            "added_at_unix": 1_700_000_000,
        }
    )
    return data


def _has_error_containing(errors: list[str], substr: str) -> bool:
    return any(substr in e for e in errors)


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------


def test_schema_version_latest_is_two() -> None:
    assert SCHEMA_VERSION_LATEST == 2


def test_supported_versions_are_one_and_two() -> None:
    assert SCHEMA_VERSIONS_SUPPORTED == frozenset({1, 2})


def test_split_vocab_exact() -> None:
    assert SPLIT_VOCAB == frozenset({"train", "val", "test", "holdout"})


def test_license_vocab_exact() -> None:
    assert LICENSE_VOCAB == frozenset(
        {
            "first-party",
            "cc-by-4.0",
            "cc-by-sa-4.0",
            "public-domain",
            "proprietary",
            "other",
        }
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_minimal_v1_fixture_is_valid() -> None:
    assert validate_fixture_json(_minimal_valid_v1()) == []


def test_minimal_v2_fixture_is_valid() -> None:
    assert validate_fixture_json(_minimal_valid_v2()) == []


def test_extra_unknown_keys_are_ignored() -> None:
    data = _minimal_valid_v1()
    data["source_audio_other_stem"] = "data/foo/other.wav"
    data["arbitrary_metadata"] = {"anything": "goes"}
    assert validate_fixture_json(data) == []


# ---------------------------------------------------------------------------
# Top-level shape
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [[], "string", 42, 3.14, None, True])
def test_top_level_must_be_mapping(bad: object) -> None:
    errors = validate_fixture_json(bad)  # type: ignore[arg-type]
    assert len(errors) == 1
    assert "top-level must be an object" in errors[0]


# ---------------------------------------------------------------------------
# Required keys (schema v1 + v2)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "missing", ["duration_s", "regions", "regression_floor_triad_relaxed"]
)
def test_missing_required_key_reports_error(missing: str) -> None:
    data = _minimal_valid_v1()
    del data[missing]
    errors = validate_fixture_json(data)
    assert _has_error_containing(errors, f"missing required key: {missing!r}")


def test_all_required_keys_missing_reports_all() -> None:
    errors = validate_fixture_json({})
    for key in ("duration_s", "regions", "regression_floor_triad_relaxed"):
        assert _has_error_containing(errors, f"missing required key: {key!r}")


# ---------------------------------------------------------------------------
# duration_s
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["10", None, [10.0], {"v": 10.0}, True])
def test_duration_s_must_be_number(bad: object) -> None:
    data = _minimal_valid_v1()
    data["duration_s"] = bad
    errors = validate_fixture_json(data)
    assert _has_error_containing(errors, "duration_s must be a number")


@pytest.mark.parametrize("bad", [0.0, -1.0, -0.5])
def test_duration_s_must_be_positive(bad: float) -> None:
    data = _minimal_valid_v1()
    data["duration_s"] = bad
    # Shrink regions so per-region duration check doesn't double-error.
    data["regions"] = []
    errors = validate_fixture_json(data)
    assert _has_error_containing(errors, "duration_s must be > 0")


def test_duration_s_integer_is_accepted() -> None:
    data = _minimal_valid_v1()
    data["duration_s"] = 10  # int instead of float
    assert validate_fixture_json(data) == []


# ---------------------------------------------------------------------------
# regression_floor_triad_relaxed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["0.5", None, [0.5], True])
def test_floor_must_be_number(bad: object) -> None:
    data = _minimal_valid_v1()
    data["regression_floor_triad_relaxed"] = bad
    errors = validate_fixture_json(data)
    assert _has_error_containing(
        errors, "regression_floor_triad_relaxed must be a number"
    )


@pytest.mark.parametrize("bad", [-0.01, -1.0, 1.01, 2.0, 100.0])
def test_floor_must_be_in_unit_interval(bad: float) -> None:
    data = _minimal_valid_v1()
    data["regression_floor_triad_relaxed"] = bad
    errors = validate_fixture_json(data)
    assert _has_error_containing(
        errors, "regression_floor_triad_relaxed must be in [0, 1]"
    )


@pytest.mark.parametrize("ok", [0.0, 0.5, 1.0, 0, 1])
def test_floor_unit_interval_endpoints_are_accepted(ok: float) -> None:
    data = _minimal_valid_v1()
    data["regression_floor_triad_relaxed"] = ok
    assert validate_fixture_json(data) == []


# ---------------------------------------------------------------------------
# regions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["not a list", 42, {"start": 0}, None])
def test_regions_must_be_list(bad: object) -> None:
    data = _minimal_valid_v1()
    data["regions"] = bad
    errors = validate_fixture_json(data)
    assert _has_error_containing(errors, "regions must be a list")


def test_regions_must_not_be_empty() -> None:
    data = _minimal_valid_v1()
    data["regions"] = []
    errors = validate_fixture_json(data)
    assert _has_error_containing(errors, "regions must contain at least one entry")


def test_region_must_be_object() -> None:
    data = _minimal_valid_v1()
    data["regions"] = ["not an object"]
    errors = validate_fixture_json(data)
    assert _has_error_containing(errors, "regions[0] must be an object")


@pytest.mark.parametrize("missing", ["start", "end", "label"])
def test_region_missing_required_key(missing: str) -> None:
    data = _minimal_valid_v1()
    region = {"start": 0.0, "end": 5.0, "label": "C:maj"}
    del region[missing]
    data["regions"] = [region]
    errors = validate_fixture_json(data)
    assert _has_error_containing(
        errors, f"regions[0] missing required key: {missing!r}"
    )


@pytest.mark.parametrize("bad", ["0", None, True, [0.0]])
def test_region_start_must_be_number(bad: object) -> None:
    data = _minimal_valid_v1()
    data["regions"] = [{"start": bad, "end": 5.0, "label": "C:maj"}]
    errors = validate_fixture_json(data)
    assert _has_error_containing(errors, "regions[0].start must be a number")


@pytest.mark.parametrize("bad", ["5", None, True, [5.0]])
def test_region_end_must_be_number(bad: object) -> None:
    data = _minimal_valid_v1()
    data["regions"] = [{"start": 0.0, "end": bad, "label": "C:maj"}]
    errors = validate_fixture_json(data)
    assert _has_error_containing(errors, "regions[0].end must be a number")


@pytest.mark.parametrize("bad", ["", None, 42, ["C:maj"]])
def test_region_label_must_be_non_empty_string(bad: object) -> None:
    data = _minimal_valid_v1()
    data["regions"] = [{"start": 0.0, "end": 5.0, "label": bad}]
    errors = validate_fixture_json(data)
    assert _has_error_containing(
        errors, "regions[0].label must be a non-empty string"
    )


def test_region_start_must_be_nonnegative() -> None:
    data = _minimal_valid_v1()
    data["regions"] = [{"start": -0.5, "end": 5.0, "label": "C:maj"}]
    errors = validate_fixture_json(data)
    assert _has_error_containing(errors, "regions[0].start must be >= 0")


@pytest.mark.parametrize("start,end", [(5.0, 5.0), (5.0, 4.0), (5.0, 0.0)])
def test_region_end_must_exceed_start(start: float, end: float) -> None:
    data = _minimal_valid_v1()
    data["regions"] = [{"start": start, "end": end, "label": "C:maj"}]
    errors = validate_fixture_json(data)
    assert _has_error_containing(errors, "regions[0].end must be > start")


def test_region_end_must_not_exceed_duration() -> None:
    data = _minimal_valid_v1()
    data["duration_s"] = 5.0
    data["regions"] = [{"start": 0.0, "end": 5.5, "label": "C:maj"}]
    errors = validate_fixture_json(data)
    assert _has_error_containing(errors, "exceeds duration_s")


def test_region_end_equal_to_duration_within_epsilon_is_valid() -> None:
    data = _minimal_valid_v1()
    data["duration_s"] = 5.0
    data["regions"] = [{"start": 0.0, "end": 5.0, "label": "C:maj"}]
    assert validate_fixture_json(data) == []


def test_region_int_coordinates_are_accepted() -> None:
    data = _minimal_valid_v1()
    data["duration_s"] = 10
    data["regions"] = [{"start": 0, "end": 5, "label": "C:maj"}]
    assert validate_fixture_json(data) == []


def test_multiple_region_errors_all_reported() -> None:
    data = _minimal_valid_v1()
    data["regions"] = [
        {"start": -1.0, "end": 5.0, "label": "C:maj"},  # start < 0
        {"start": 5.0, "end": 4.0, "label": "G:maj"},  # end <= start
        {"start": 0.0, "end": 5.0, "label": ""},  # empty label
    ]
    errors = validate_fixture_json(data)
    assert _has_error_containing(errors, "regions[0].start must be >= 0")
    assert _has_error_containing(errors, "regions[1].end must be > start")
    assert _has_error_containing(errors, "regions[2].label must be a non-empty string")


# ---------------------------------------------------------------------------
# schema_version (v2 optional)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["2", 2.0, None, [2], True])
def test_schema_version_must_be_int(bad: object) -> None:
    data = _minimal_valid_v1()
    data["schema_version"] = bad
    errors = validate_fixture_json(data)
    assert _has_error_containing(errors, "schema_version must be an int")


@pytest.mark.parametrize("bad", [0, 3, 99, -1])
def test_schema_version_must_be_supported(bad: int) -> None:
    data = _minimal_valid_v1()
    data["schema_version"] = bad
    errors = validate_fixture_json(data)
    assert _has_error_containing(errors, "not in supported set")


@pytest.mark.parametrize("ok", [1, 2])
def test_schema_version_supported_values_accepted(ok: int) -> None:
    data = _minimal_valid_v1()
    data["schema_version"] = ok
    assert validate_fixture_json(data) == []


# ---------------------------------------------------------------------------
# split (v2 optional)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [42, None, ["test"], True])
def test_split_must_be_string(bad: object) -> None:
    data = _minimal_valid_v1()
    data["split"] = bad
    errors = validate_fixture_json(data)
    assert _has_error_containing(errors, "split must be a string")


@pytest.mark.parametrize("bad", ["TRAIN", "Test", "production", "", "dev"])
def test_split_must_be_in_vocab(bad: str) -> None:
    data = _minimal_valid_v1()
    data["split"] = bad
    errors = validate_fixture_json(data)
    assert _has_error_containing(errors, "not in")


@pytest.mark.parametrize("ok", sorted(SPLIT_VOCAB))
def test_split_vocab_values_accepted(ok: str) -> None:
    data = _minimal_valid_v1()
    data["split"] = ok
    assert validate_fixture_json(data) == []


# ---------------------------------------------------------------------------
# license (v2 optional)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [42, None, ["mit"], True])
def test_license_must_be_string(bad: object) -> None:
    data = _minimal_valid_v1()
    data["license"] = bad
    errors = validate_fixture_json(data)
    assert _has_error_containing(errors, "license must be a string")


@pytest.mark.parametrize("bad", ["mit", "gpl", "CC-BY-4.0", "", "Public-Domain"])
def test_license_must_be_in_vocab(bad: str) -> None:
    data = _minimal_valid_v1()
    data["license"] = bad
    errors = validate_fixture_json(data)
    assert _has_error_containing(errors, "not in")


@pytest.mark.parametrize("ok", sorted(LICENSE_VOCAB))
def test_license_vocab_values_accepted(ok: str) -> None:
    data = _minimal_valid_v1()
    data["license"] = ok
    assert validate_fixture_json(data) == []


# ---------------------------------------------------------------------------
# genre (v2 optional)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ok", ["rock", "punk", "", None])
def test_genre_string_or_none_accepted(ok: object) -> None:
    data = _minimal_valid_v1()
    data["genre"] = ok
    assert validate_fixture_json(data) == []


@pytest.mark.parametrize("bad", [42, 3.14, ["rock"], True, {"k": "v"}])
def test_genre_must_be_string_or_none(bad: object) -> None:
    data = _minimal_valid_v1()
    data["genre"] = bad
    errors = validate_fixture_json(data)
    assert _has_error_containing(errors, "genre must be a string or null")


# ---------------------------------------------------------------------------
# tags (v2 optional)
# ---------------------------------------------------------------------------


def test_tags_empty_list_is_valid() -> None:
    data = _minimal_valid_v1()
    data["tags"] = []
    assert validate_fixture_json(data) == []


def test_tags_string_list_is_valid() -> None:
    data = _minimal_valid_v1()
    data["tags"] = ["one", "two", "three"]
    assert validate_fixture_json(data) == []


@pytest.mark.parametrize("bad", ["a,b,c", 42, None, {"a": 1}, True])
def test_tags_must_be_list(bad: object) -> None:
    data = _minimal_valid_v1()
    data["tags"] = bad
    errors = validate_fixture_json(data)
    assert _has_error_containing(errors, "tags must be a list")


def test_tags_entries_must_be_strings() -> None:
    data = _minimal_valid_v1()
    data["tags"] = ["ok", 42, "also-ok", None]
    errors = validate_fixture_json(data)
    assert _has_error_containing(errors, "tags[1] must be a string")
    assert _has_error_containing(errors, "tags[3] must be a string")


# ---------------------------------------------------------------------------
# curated_by (v2 optional)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ok", ["matt", "", None])
def test_curated_by_string_or_none_accepted(ok: object) -> None:
    data = _minimal_valid_v1()
    data["curated_by"] = ok
    assert validate_fixture_json(data) == []


@pytest.mark.parametrize("bad", [42, ["matt"], True, {"k": "v"}])
def test_curated_by_must_be_string_or_none(bad: object) -> None:
    data = _minimal_valid_v1()
    data["curated_by"] = bad
    errors = validate_fixture_json(data)
    assert _has_error_containing(errors, "curated_by must be a string or null")


# ---------------------------------------------------------------------------
# added_at_unix (v2 optional)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ok", [0, 1, 1_700_000_000])
def test_added_at_unix_nonneg_int_accepted(ok: int) -> None:
    data = _minimal_valid_v1()
    data["added_at_unix"] = ok
    assert validate_fixture_json(data) == []


@pytest.mark.parametrize("bad", ["1700000000", 1.0, None, [1], True])
def test_added_at_unix_must_be_int(bad: object) -> None:
    data = _minimal_valid_v1()
    data["added_at_unix"] = bad
    errors = validate_fixture_json(data)
    assert _has_error_containing(errors, "added_at_unix must be an int")


@pytest.mark.parametrize("bad", [-1, -1_700_000_000])
def test_added_at_unix_must_be_nonneg(bad: int) -> None:
    data = _minimal_valid_v1()
    data["added_at_unix"] = bad
    errors = validate_fixture_json(data)
    assert _has_error_containing(errors, "added_at_unix must be >= 0")


# ---------------------------------------------------------------------------
# Errors accumulate (one-pass validation)
# ---------------------------------------------------------------------------


def test_many_independent_errors_accumulate() -> None:
    data: Mapping[str, object] = {
        "duration_s": -1.0,  # not positive
        "regions": [{"start": 0.0, "end": 1.0, "label": ""}],  # empty label
        "regression_floor_triad_relaxed": 1.5,  # > 1
        "schema_version": 99,  # unsupported
        "split": "production",  # not in vocab
        "license": "mit",  # not in vocab
        "tags": "not-a-list",  # not a list
        "added_at_unix": -1,  # < 0
    }
    errors = validate_fixture_json(data)
    # At least eight distinct errors should appear.
    assert len(errors) >= 8
    assert _has_error_containing(errors, "duration_s must be > 0")
    assert _has_error_containing(
        errors, "regression_floor_triad_relaxed must be in [0, 1]"
    )
    assert _has_error_containing(errors, "regions[0].label must be a non-empty string")
    assert _has_error_containing(errors, "schema_version 99 not in supported set")
    assert _has_error_containing(errors, "split 'production' not in")
    assert _has_error_containing(errors, "license 'mit' not in")
    assert _has_error_containing(errors, "tags must be a list")
    assert _has_error_containing(errors, "added_at_unix must be >= 0")
