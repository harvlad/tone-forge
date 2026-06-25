"""Schema regression for the ``SectionType`` enum.

Locks the enum's string-value surface so additive changes (B1 added
``INSTRUMENTAL``) don't accidentally rename or remove existing values.
"""

from __future__ import annotations

from tone_forge.analysis.sections import SectionType


def test_instrumental_value_round_trips():
    """B1 invariant: SectionType.INSTRUMENTAL exists and is constructible from its string value."""
    assert SectionType.INSTRUMENTAL.value == "instrumental"
    assert SectionType("instrumental") is SectionType.INSTRUMENTAL


def test_full_enum_value_set():
    """All section-type string values, locked. Adding a new value is
    fine (extend this set); renaming or removing one fires this test.
    """
    assert {st.value for st in SectionType} == {
        "intro",
        "verse",
        "prechorus",
        "chorus",
        "drop",
        "breakdown",
        "bridge",
        "buildup",
        "transition",
        "outro",
        "instrumental",
        "unknown",
    }
