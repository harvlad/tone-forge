"""Schema v2 validator for chord-groundtruth fixture JSON.

A pure-function module: no I/O, no imports from elsewhere in
``bench`` or ``tone_forge``. Used by:

* ``bench.corpus.iter_corpus_fixtures`` -- defensive validation when
  parsing (M2 makes this opt-in via the public ``validate_fixture_json``
  function; the loader still accepts legacy schema-v1 JSON for
  backward compatibility).
* ``bench.corpus`` CLI ``validate`` subcommand -- explicit lint pass
  for fixtures the curator is about to add.

Validation philosophy
---------------------

* **Additive only**: M2's new fields (``schema_version``, ``split``,
  ``genre``, ``license``, ``tags``, ``curated_by``, ``added_at_unix``)
  are all OPTIONAL. A fixture JSON that omits all of them is
  schema-v1 and remains valid.
* **Closed vocabularies are enforced** for ``split`` and ``license``
  because they're machine-consumed (sweep filtering, license auditing);
  ``genre`` and ``tags`` are free-form because they're descriptive.
* **Numeric ranges are sanity-checked, not statistically validated.**
  E.g., ``regression_floor_triad_relaxed`` must be in ``[0, 1]`` because
  WCSR has that range by construction; we don't try to validate
  whether a particular floor is "reasonable".
* **Errors are accumulated and returned as a list of strings**, not
  raised. The validator inspects everything possible in one pass so
  the curator sees all problems at once.
"""
from __future__ import annotations

from typing import Iterable, List, Mapping


__all__ = [
    "SCHEMA_VERSION_LATEST",
    "SCHEMA_VERSIONS_SUPPORTED",
    "SPLIT_VOCAB",
    "LICENSE_VOCAB",
    "validate_fixture_json",
]


SCHEMA_VERSION_LATEST: int = 2
SCHEMA_VERSIONS_SUPPORTED: frozenset[int] = frozenset({1, 2})

SPLIT_VOCAB: frozenset[str] = frozenset({"train", "val", "test", "holdout"})

LICENSE_VOCAB: frozenset[str] = frozenset(
    {
        "first-party",
        "cc-by-4.0",
        "cc-by-sa-4.0",
        "public-domain",
        "proprietary",
        "other",
    }
)


# The three keys that have been required since schema v1.
_REQUIRED_KEYS: tuple[str, ...] = (
    "duration_s",
    "regions",
    "regression_floor_triad_relaxed",
)


def validate_fixture_json(data: Mapping[str, object]) -> List[str]:
    """Validate a fixture JSON ``dict``. Return a list of error strings.

    An empty return list means the fixture is valid under schema v2
    (which is a strict superset of v1).

    Parameters
    ----------
    data:
        The parsed JSON object (top-level must be a mapping).

    Returns
    -------
    list[str]
        Human-readable error messages, one per problem, in source
        order. Empty when the input is valid.
    """
    errors: List[str] = []

    if not isinstance(data, Mapping):
        return [f"top-level must be an object, got {type(data).__name__}"]

    # --- Required keys (schema v1 + v2) ---------------------------------
    for key in _REQUIRED_KEYS:
        if key not in data:
            errors.append(f"missing required key: {key!r}")

    # --- duration_s ------------------------------------------------------
    duration_s: float | None = None
    if "duration_s" in data:
        raw_dur = data["duration_s"]
        if not isinstance(raw_dur, (int, float)) or isinstance(raw_dur, bool):
            errors.append(
                f"duration_s must be a number, got {type(raw_dur).__name__}"
            )
        else:
            duration_s = float(raw_dur)
            if duration_s <= 0.0:
                errors.append(
                    f"duration_s must be > 0, got {duration_s}"
                )

    # --- regression_floor_triad_relaxed ---------------------------------
    if "regression_floor_triad_relaxed" in data:
        raw_floor = data["regression_floor_triad_relaxed"]
        if not isinstance(raw_floor, (int, float)) or isinstance(raw_floor, bool):
            errors.append(
                "regression_floor_triad_relaxed must be a number, got "
                f"{type(raw_floor).__name__}"
            )
        else:
            floor = float(raw_floor)
            if not (0.0 <= floor <= 1.0):
                errors.append(
                    "regression_floor_triad_relaxed must be in [0, 1], got "
                    f"{floor}"
                )

    # --- regions ---------------------------------------------------------
    if "regions" in data:
        errors.extend(_validate_regions(data["regions"], duration_s))

    # --- Optional v2 fields ---------------------------------------------
    if "schema_version" in data:
        raw_sv = data["schema_version"]
        if isinstance(raw_sv, bool) or not isinstance(raw_sv, int):
            errors.append(
                f"schema_version must be an int, got {type(raw_sv).__name__}"
            )
        elif raw_sv not in SCHEMA_VERSIONS_SUPPORTED:
            errors.append(
                f"schema_version {raw_sv} not in supported set "
                f"{sorted(SCHEMA_VERSIONS_SUPPORTED)}"
            )

    if "split" in data:
        raw_split = data["split"]
        if not isinstance(raw_split, str):
            errors.append(
                f"split must be a string, got {type(raw_split).__name__}"
            )
        elif raw_split not in SPLIT_VOCAB:
            errors.append(
                f"split {raw_split!r} not in {sorted(SPLIT_VOCAB)}"
            )

    if "license" in data:
        raw_lic = data["license"]
        if not isinstance(raw_lic, str):
            errors.append(
                f"license must be a string, got {type(raw_lic).__name__}"
            )
        elif raw_lic not in LICENSE_VOCAB:
            errors.append(
                f"license {raw_lic!r} not in {sorted(LICENSE_VOCAB)}"
            )

    if "genre" in data:
        raw_genre = data["genre"]
        if raw_genre is not None and not isinstance(raw_genre, str):
            errors.append(
                f"genre must be a string or null, got {type(raw_genre).__name__}"
            )

    if "tags" in data:
        raw_tags = data["tags"]
        if not isinstance(raw_tags, list):
            errors.append(
                f"tags must be a list, got {type(raw_tags).__name__}"
            )
        else:
            for i, tag in enumerate(raw_tags):
                if not isinstance(tag, str):
                    errors.append(
                        f"tags[{i}] must be a string, got {type(tag).__name__}"
                    )

    if "curated_by" in data:
        raw_cb = data["curated_by"]
        if raw_cb is not None and not isinstance(raw_cb, str):
            errors.append(
                f"curated_by must be a string or null, got "
                f"{type(raw_cb).__name__}"
            )

    if "added_at_unix" in data:
        raw_aau = data["added_at_unix"]
        if isinstance(raw_aau, bool) or not isinstance(raw_aau, int):
            errors.append(
                f"added_at_unix must be an int, got {type(raw_aau).__name__}"
            )
        elif raw_aau < 0:
            errors.append(f"added_at_unix must be >= 0, got {raw_aau}")

    return errors


def _validate_regions(raw: object, duration_s: float | None) -> List[str]:
    """Validate the ``regions`` array. Returns error strings."""
    errors: List[str] = []
    if not isinstance(raw, list):
        return [f"regions must be a list, got {type(raw).__name__}"]
    if len(raw) == 0:
        errors.append("regions must contain at least one entry")
        return errors

    for i, region in enumerate(raw):
        if not isinstance(region, Mapping):
            errors.append(
                f"regions[{i}] must be an object, got {type(region).__name__}"
            )
            continue
        # Required region keys.
        for key in ("start", "end", "label"):
            if key not in region:
                errors.append(f"regions[{i}] missing required key: {key!r}")
        if not all(k in region for k in ("start", "end", "label")):
            continue

        start_raw = region["start"]
        end_raw = region["end"]
        label_raw = region["label"]

        if isinstance(start_raw, bool) or not isinstance(start_raw, (int, float)):
            errors.append(
                f"regions[{i}].start must be a number, got "
                f"{type(start_raw).__name__}"
            )
            continue
        if isinstance(end_raw, bool) or not isinstance(end_raw, (int, float)):
            errors.append(
                f"regions[{i}].end must be a number, got "
                f"{type(end_raw).__name__}"
            )
            continue
        if not isinstance(label_raw, str) or label_raw == "":
            errors.append(
                f"regions[{i}].label must be a non-empty string"
            )
            continue

        start = float(start_raw)
        end = float(end_raw)
        if start < 0.0:
            errors.append(f"regions[{i}].start must be >= 0, got {start}")
        if end <= start:
            errors.append(
                f"regions[{i}].end must be > start, got start={start}, end={end}"
            )
        if duration_s is not None and end > duration_s + 1e-6:
            errors.append(
                f"regions[{i}].end ({end}) exceeds duration_s ({duration_s})"
            )

    return errors
