"""Tests for the JAM rehearsal-v2 ``group_id`` / ``recurrence_count``
section grouping (Phase 3).

Covers:
* ``song_form.grouping.assign_section_groups`` — the pure helper
  invoked from ``unified_pipeline`` step 7e.
* ``session.bundle`` round-trip — legacy bundles (no keys) and
  Phase-3 bundles (with keys) both deserialise into
  ``contracts.Section`` with the expected values.
"""
from __future__ import annotations

from tone_forge.song_form.grouping import assign_section_groups
from tone_forge.session.bundle import _iter_sections
from tone_forge.contracts import Section


def _section(type_str: str, start: float, end: float) -> dict:
    """Minimal section dict shaped like ``ArrangementSection.to_dict``."""
    return {
        "type": type_str,
        "start_time": start,
        "end_time": end,
        "confidence": 0.8,
    }


def test_three_recurring_choruses_share_group_id():
    sections = [
        _section("intro", 0.0, 8.0),
        _section("verse", 8.0, 24.0),
        _section("chorus", 24.0, 40.0),
        _section("verse", 40.0, 56.0),
        _section("chorus", 56.0, 72.0),
        _section("bridge", 72.0, 88.0),
        _section("chorus", 88.0, 104.0),
        _section("outro", 104.0, 112.0),
    ]

    assign_section_groups(sections)

    # All three choruses share the same group_id + recurrence_count.
    choruses = [s for s in sections if s["type"] == "chorus"]
    assert len(choruses) == 3
    assert all(s["group_id"] == "chorus" for s in choruses)
    assert all(s["recurrence_count"] == 3 for s in choruses)

    # Two verses form their own group.
    verses = [s for s in sections if s["type"] == "verse"]
    assert all(s["group_id"] == "verse" for s in verses)
    assert all(s["recurrence_count"] == 2 for s in verses)

    # Singletons: group_id is None, recurrence_count is 1.
    for label in ("intro", "bridge", "outro"):
        singleton = next(s for s in sections if s["type"] == label)
        assert singleton["group_id"] is None
        assert singleton["recurrence_count"] == 1


def test_assign_section_groups_is_idempotent():
    sections = [
        _section("chorus", 0.0, 8.0),
        _section("chorus", 8.0, 16.0),
    ]
    assign_section_groups(sections)
    assign_section_groups(sections)
    for s in sections:
        assert s["group_id"] == "chorus"
        assert s["recurrence_count"] == 2


def test_assign_section_groups_handles_missing_type():
    sections = [
        {"start_time": 0.0, "end_time": 8.0, "confidence": 0.5},
        _section("chorus", 8.0, 16.0),
    ]
    assign_section_groups(sections)
    # Section with missing/empty type gets no group_id (falsy filter).
    assert sections[0]["group_id"] is None
    assert sections[0]["recurrence_count"] == 1
    assert sections[1]["group_id"] is None  # only 1 chorus
    assert sections[1]["recurrence_count"] == 1


def test_assign_section_groups_empty_list():
    # Must not raise on an empty pipeline.
    sections: list = []
    assign_section_groups(sections)
    assert sections == []


def test_bundle_roundtrip_with_group_id():
    """A Phase-3 section dict round-trips through ``_iter_sections``
    into a ``contracts.Section`` with populated group fields.
    """
    raw = [{
        "start_time": 24.0,
        "end_time": 40.0,
        "type": "chorus",
        "confidence": 0.9,
        "group_id": "chorus",
        "recurrence_count": 3,
    }]
    sections = list(_iter_sections(raw))
    assert len(sections) == 1
    s = sections[0]
    assert isinstance(s, Section)
    assert s.group_id == "chorus"
    assert s.recurrence_count == 3


def test_bundle_roundtrip_legacy_bundle_has_none_defaults():
    """A pre-Phase-3 bundle (no group_id / recurrence_count keys)
    deserialises with ``None`` defaults so legacy bundles keep
    parsing unchanged.
    """
    raw = [{
        "start_time": 0.0,
        "end_time": 8.0,
        "type": "intro",
        "confidence": 0.9,
    }]
    sections = list(_iter_sections(raw))
    assert len(sections) == 1
    assert sections[0].group_id is None
    assert sections[0].recurrence_count is None


def test_bundle_roundtrip_malformed_recurrence_count_defaults_to_none():
    """A malformed ``recurrence_count`` (non-numeric, ≤ 0) is
    silently coerced to ``None`` rather than crashing the loader.
    """
    for bad in ("not-a-number", -1, 0, None):
        raw = [{
            "start_time": 0.0,
            "end_time": 8.0,
            "type": "chorus",
            "confidence": 0.9,
            "group_id": "chorus",
            "recurrence_count": bad,
        }]
        sections = list(_iter_sections(raw))
        assert sections[0].recurrence_count is None, (
            f"Expected None for malformed value {bad!r}"
        )
