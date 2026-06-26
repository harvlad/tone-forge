"""C2 — chords_by_stem persistence through the bundle assembler.

Locks the contract that the additive per-stem chord-lane dict
introduced by C1 round-trips cleanly through the session bundle:

1. A persisted history result with ``chords_by_stem`` populated
   produces a ``SongUnderstanding.chords_by_stem`` dict with typed
   ``Chord`` records.
2. Legacy bundles without the new field load fine — the dict is
   empty rather than missing or crashing.
3. The legacy ``chords`` field stays independent — populating one
   doesn't affect the other.
"""

from __future__ import annotations

from tone_forge.contracts import Chord
from tone_forge.session.bundle import build as build_bundle


def test_bundle_round_trip_chords_by_stem():
    """3-stem dict survives the bundle assembler with typed Chord tuples."""
    persisted = {
        "detected_type": "guitar",
        "duration_sec": 30.0,
        "sample_rate": 22050,
        "chords": [
            {"start_s": 0.0, "end_s": 4.0, "symbol": "Am", "confidence": 0.9},
        ],
        "chords_by_stem": {
            "other": [
                {"start_s": 0.0, "end_s": 4.0,
                 "symbol": "Am", "confidence": 0.9},
                {"start_s": 4.0, "end_s": 8.0,
                 "symbol": "F", "confidence": 0.8},
            ],
            "bass": [
                {"start_s": 0.0, "end_s": 4.0,
                 "symbol": "A", "confidence": 0.7},
            ],
            "vocals": [],
        },
        "chords_beat_snapped_by_stem": {
            "other": [
                {"start_s": 0.0, "end_s": 4.0,
                 "symbol": "Am", "confidence": 0.9},
            ],
            "bass": None,
        },
    }

    bundle = build_bundle(persisted, session_id="abc123")

    cbs = bundle.understanding.chords_by_stem
    assert isinstance(cbs, dict)
    assert set(cbs.keys()) == {"other", "bass", "vocals"}

    # Other stem: typed Chord tuple, two entries.
    assert all(isinstance(c, Chord) for c in cbs["other"])
    assert len(cbs["other"]) == 2
    assert cbs["other"][0].symbol == "Am"
    assert cbs["other"][1].symbol == "F"

    # Bass stem: single entry.
    assert len(cbs["bass"]) == 1
    assert cbs["bass"][0].symbol == "A"

    # Vocals stem: empty list survives as empty tuple.
    assert cbs["vocals"] == ()

    # Beat-snapped variant: present for "other", None collapses to
    # empty tuple for "bass".
    cbs_snap = bundle.understanding.chords_beat_snapped_by_stem
    assert set(cbs_snap.keys()) == {"other", "bass"}
    assert len(cbs_snap["other"]) == 1
    assert cbs_snap["bass"] == ()


def test_bundle_round_trip_no_chords_by_stem_field():
    """Legacy bundles (pre-C1) have no ``chords_by_stem`` key — the
    bundle assembler returns an empty dict rather than crashing.
    """
    persisted = {
        "detected_type": "guitar",
        "duration_sec": 30.0,
        "sample_rate": 22050,
        "chords": [
            {"start_s": 0.0, "end_s": 4.0,
             "symbol": "Am", "confidence": 0.9},
        ],
        # No chords_by_stem, no chords_beat_snapped_by_stem.
    }

    bundle = build_bundle(persisted, session_id="legacy456")

    assert bundle.understanding.chords_by_stem == {}
    assert bundle.understanding.chords_beat_snapped_by_stem == {}
    # Legacy ``chords`` field is unaffected.
    assert len(bundle.understanding.chords) == 1
    assert bundle.understanding.chords[0].symbol == "Am"


def test_bundle_chords_by_stem_independent_of_legacy_chords():
    """The legacy ``chords`` field and the new ``chords_by_stem`` dict
    are independent — populating one doesn't affect the other. This
    matches the wire-level contract: the pipeline sets ``chords`` to
    the "other" lane AND ships the full per-stem dict separately.
    """
    persisted = {
        "detected_type": "guitar",
        "duration_sec": 30.0,
        "sample_rate": 22050,
        "chords": [
            {"start_s": 0.0, "end_s": 4.0,
             "symbol": "C", "confidence": 0.9},
        ],
        "chords_by_stem": {
            "bass": [
                {"start_s": 0.0, "end_s": 4.0,
                 "symbol": "G", "confidence": 0.5},
            ],
            # Note: no "other" entry here. The legacy chords field
            # carries the C lane; the per-stem dict carries only the
            # bass lane. This is an unusual but legal state.
        },
    }

    bundle = build_bundle(persisted, session_id="indep789")

    assert bundle.understanding.chords[0].symbol == "C"
    assert "other" not in bundle.understanding.chords_by_stem
    assert bundle.understanding.chords_by_stem["bass"][0].symbol == "G"


def test_bundle_chords_by_stem_ignores_non_string_keys():
    """Defensive: a malformed persisted dict with non-string keys
    drops them rather than crashing the bundle build.
    """
    persisted = {
        "detected_type": "guitar",
        "duration_sec": 30.0,
        "sample_rate": 22050,
        "chords_by_stem": {
            "other": [
                {"start_s": 0.0, "end_s": 1.0,
                 "symbol": "C", "confidence": 0.9},
            ],
            42: [  # numeric key — invalid
                {"start_s": 0.0, "end_s": 1.0,
                 "symbol": "X", "confidence": 0.9},
            ],
        },
    }

    bundle = build_bundle(persisted, session_id="malformed")

    assert set(bundle.understanding.chords_by_stem.keys()) == {"other"}


def test_bundle_chords_by_stem_default_factory_returns_independent_dicts():
    """Frozen-dataclass default_factory: two bundles built from input
    that has no per-stem dict must get independent empty dicts (not
    a shared mutable default that would alias across bundles).
    """
    persisted_a = {"detected_type": "guitar", "duration_sec": 1.0,
                   "sample_rate": 22050}
    persisted_b = {"detected_type": "guitar", "duration_sec": 1.0,
                   "sample_rate": 22050}

    bundle_a = build_bundle(persisted_a, session_id="a")
    bundle_b = build_bundle(persisted_b, session_id="b")

    # Identity must differ — otherwise a future mutation through one
    # bundle would leak across all bundles built with default values.
    assert (
        bundle_a.understanding.chords_by_stem
        is not bundle_b.understanding.chords_by_stem
    )
