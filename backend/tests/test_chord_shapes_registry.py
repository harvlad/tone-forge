"""Validates ``backend/static/chord_shapes.json`` — the single source of
truth for curated guitar chord fingerings consumed by
``chord_diagrams.js`` (Phase B of the chord-diagram queue feature).

The JSON is hand-edited and easy to typo, so this test pins:

  1. The top-level schema (``version`` / ``tuning`` / ``shapes``).
  2. The 6-fret invariant for every shape (one entry per guitar string).
  3. Fret value ranges (-1 muted, otherwise 0..24).
  4. Finger-number ranges (0..4) when ``fingers`` is present.
  5. Barre object shape when present.
  6. A sanity floor: the canonical first-chords every beginner-chord-chart
     ships with must be present.

Adding a new shape is a one-line JSON diff. The test grows only when
new structural fields are added to the schema.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


_SHAPES_PATH = (
    Path(__file__).resolve().parent.parent / "static" / "chord_shapes.json"
)


SUPPORTED_TUNINGS = frozenset({"EADGBE"})

# Open-position shapes that any guitar reference card includes. If any
# of these go missing the registry has regressed.
SANITY_FLOOR_KEYS = (
    "C:maj", "D:maj", "E:maj", "F:maj", "G:maj", "A:maj",
    "A:min", "D:min", "E:min",
)


@pytest.fixture(scope="module")
def registry() -> dict:
    with open(_SHAPES_PATH, "r") as f:
        return json.load(f)


def test_top_level_schema(registry: dict) -> None:
    assert registry["version"] == 1
    assert registry["tuning"] in SUPPORTED_TUNINGS
    assert isinstance(registry["shapes"], dict)
    assert registry["shapes"], "registry must contain at least one shape"


def test_every_shape_has_six_frets(registry: dict) -> None:
    for key, shape in registry["shapes"].items():
        frets = shape.get("frets")
        assert isinstance(frets, list), f"{key}: frets must be a list"
        assert len(frets) == 6, (
            f"{key}: expected 6 fret entries (one per string), got {len(frets)}"
        )


def test_fret_values_in_valid_range(registry: dict) -> None:
    for key, shape in registry["shapes"].items():
        for i, fret in enumerate(shape["frets"]):
            assert isinstance(fret, int), (
                f"{key} string {i}: fret must be int, got {type(fret).__name__}"
            )
            assert fret == -1 or 0 <= fret <= 24, (
                f"{key} string {i}: fret {fret} out of range (-1 or 0..24)"
            )


def test_fingers_when_present_are_valid(registry: dict) -> None:
    for key, shape in registry["shapes"].items():
        fingers = shape.get("fingers")
        if fingers is None:
            continue
        assert isinstance(fingers, list), f"{key}: fingers must be a list"
        assert len(fingers) == 6, (
            f"{key}: expected 6 finger entries, got {len(fingers)}"
        )
        for i, finger in enumerate(fingers):
            assert isinstance(finger, int), (
                f"{key} string {i}: finger must be int"
            )
            assert 0 <= finger <= 4, (
                f"{key} string {i}: finger {finger} out of range (0..4); "
                f"0=open/muted, 1-4=index/middle/ring/pinky"
            )


def test_barre_when_present_is_valid(registry: dict) -> None:
    for key, shape in registry["shapes"].items():
        barre = shape.get("barre")
        if barre is None:
            continue
        assert isinstance(barre, dict), f"{key}: barre must be dict or null"
        assert {"fret", "from_string", "to_string"} <= set(barre.keys()), (
            f"{key}: barre missing required fields"
        )
        assert 1 <= barre["fret"] <= 24, f"{key}: barre fret out of range"
        assert 0 <= barre["from_string"] <= 5
        assert 0 <= barre["to_string"] <= 5
        assert barre["from_string"] <= barre["to_string"], (
            f"{key}: barre from_string must be <= to_string"
        )


def test_sanity_floor_shapes_present(registry: dict) -> None:
    """Beginner-chart staples (C, D, E, F, G, A, Am, Dm, Em) must exist."""
    shapes = registry["shapes"]
    missing = [k for k in SANITY_FLOOR_KEYS if k not in shapes]
    assert not missing, (
        f"sanity-floor shapes missing from registry: {missing}. "
        f"These are the canonical open-position chords every beginner "
        f"reference card includes."
    )


def test_shape_keys_follow_root_colon_quality_format(registry: dict) -> None:
    """Keys are ``<root>:<quality>`` where root is a note name and
    quality is one of the supported chord-detector vocabulary entries.
    """
    valid_roots = {
        "C", "C#", "Db", "D", "D#", "Eb", "E", "F", "F#",
        "Gb", "G", "G#", "Ab", "A", "A#", "Bb", "B",
    }
    valid_qualities = {
        "maj", "min", "5", "7", "m7", "maj7",
        "sus2", "sus4", "dim", "aug",
    }
    for key in registry["shapes"].keys():
        assert ":" in key, f"shape key {key!r} missing ':' separator"
        root, quality = key.split(":", 1)
        assert root in valid_roots, f"shape key {key!r}: unknown root {root!r}"
        assert quality in valid_qualities, (
            f"shape key {key!r}: unknown quality {quality!r}"
        )
