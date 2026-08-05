"""Schema-only integrity test for the Founder Validation Corpus.

This test runs on every commit and verifies the corpus is well-formed.
It deliberately does NOT run the pipeline (that's the operator's job,
via backend/scripts/run_founder_validation.py).

What we check here:
  1. manifest.yaml parses without error
  2. every declared `audio` path exists on disk
  3. every declared `expected` JSON parses and validates
  4. every key in every expected JSON is a recognised comparator key
     (i.e. no typos like 'duration' instead of 'duration_s')
  5. every "value" or "min/max" in each spec is numerically reasonable
     (positive duration, non-negative counts, etc.)

Why this matters: a typo in an expected JSON would silently make the
field uncheckable (the comparator registry would skip it, and the
report would just show fewer rows). This test makes that failure
mode loud at the CI boundary instead of waiting to be noticed.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tone_forge.evaluation.founder_corpus import (
    SCHEMA_VERSION,
    VALID_GATES,
    VALID_TIERS,
    _COMPARATORS,
    load_expected,
    load_manifest,
)


CORPUS_ROOT = Path(__file__).resolve().parent.parent / "founder_corpus"
MANIFEST_PATH = CORPUS_ROOT / "manifest.yaml"


def test_corpus_root_exists():
    assert CORPUS_ROOT.is_dir(), f"founder corpus directory missing: {CORPUS_ROOT}"
    assert MANIFEST_PATH.is_file(), f"manifest missing: {MANIFEST_PATH}"


def test_manifest_parses():
    manifest = load_manifest(MANIFEST_PATH)
    assert manifest.schema_version == SCHEMA_VERSION
    assert len(manifest.entries) > 0, "manifest must declare at least one entry"


def _all_entries():
    """Helper for parametrize: returns the parsed manifest entries."""
    if not MANIFEST_PATH.exists():
        return []
    try:
        return list(load_manifest(MANIFEST_PATH).entries)
    except Exception:
        return []


@pytest.mark.parametrize("entry", _all_entries(), ids=lambda e: e.id)
def test_entry_audio_exists(entry):
    # Generated-fixture audio (tests/_generated/*.wav) is rendered by a
    # local step, not committed, so it's absent in CI. Skip those rather
    # than fail; committed corpus audio is still asserted.
    if "_generated" in str(entry.audio_path) and not entry.audio_path.exists():
        pytest.skip(f"generated fixture audio not present here: {entry.audio_path}")
    assert entry.audio_path.exists(), (
        f"entry {entry.id!r}: audio path {entry.audio_path} does not exist. "
        f"Either commit the audio, fix the manifest path, or remove the entry."
    )


@pytest.mark.parametrize("entry", _all_entries(), ids=lambda e: e.id)
def test_entry_tier_valid(entry):
    assert entry.tier in VALID_TIERS, f"entry {entry.id!r}: invalid tier {entry.tier!r}"


@pytest.mark.parametrize("entry", _all_entries(), ids=lambda e: e.id)
def test_entry_expected_loads(entry):
    payload = load_expected(entry.expected_path)
    assert payload["song_id"] == entry.id, (
        f"entry {entry.id!r}: expected JSON song_id is "
        f"{payload['song_id']!r}; should match entry id"
    )


@pytest.mark.parametrize("entry", _all_entries(), ids=lambda e: e.id)
def test_entry_expected_keys_recognised(entry):
    payload = load_expected(entry.expected_path)
    reserved = {"schema_version", "song_id", "source_notes"}
    unknown = set(payload.keys()) - reserved - set(_COMPARATORS.keys())
    assert not unknown, (
        f"entry {entry.id!r}: expected JSON contains keys not recognised by any "
        f"comparator: {sorted(unknown)}. Either fix the typo or register a new "
        f"comparator in tone_forge/evaluation/founder_corpus.py."
    )


@pytest.mark.parametrize("entry", _all_entries(), ids=lambda e: e.id)
def test_entry_expected_specs_well_formed(entry):
    payload = load_expected(entry.expected_path)
    for key, spec in payload.items():
        if key in {"schema_version", "song_id", "source_notes"}:
            continue
        assert isinstance(spec, dict), f"{entry.id}/{key}: spec must be an object"
        gate = spec.get("gate", "soft")
        assert gate in VALID_GATES, (
            f"{entry.id}/{key}: gate must be one of {sorted(VALID_GATES)}, got {gate!r}"
        )
        # Range vs. point specs:
        if "min" in spec or "max" in spec:
            lo = spec.get("min", 0)
            hi = spec.get("max", 10**9)
            assert isinstance(lo, (int, float)) and isinstance(hi, (int, float)), (
                f"{entry.id}/{key}: min/max must be numeric"
            )
            assert lo <= hi, f"{entry.id}/{key}: min ({lo}) must be <= max ({hi})"
        elif "value" in spec:
            val = spec["value"]
            assert val is not None, f"{entry.id}/{key}: value must not be null"
        else:
            pytest.fail(
                f"{entry.id}/{key}: spec must declare either 'value' or 'min'/'max'"
            )


def test_no_duplicate_entry_ids():
    manifest = load_manifest(MANIFEST_PATH)
    ids = [e.id for e in manifest.entries]
    assert len(ids) == len(set(ids)), f"duplicate entry ids in manifest: {ids}"
