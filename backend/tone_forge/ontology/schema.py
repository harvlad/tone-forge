"""Descriptor schema versioning and validation.

Treats descriptor schemas like API contracts:
- Version carefully
- Maintain backward compatibility
- Avoid uncontrolled schema drift

This is critical for:
- Embedding consistency
- Retrieval coherence
- Export compatibility
- Training data stability
"""
from __future__ import annotations

from typing import Dict, List, Optional, Any, Tuple
import logging

logger = logging.getLogger(__name__)

# Current schema version
ONTOLOGY_VERSION = "1.0.0"

# Schema version history
SCHEMA_VERSIONS = {
    "1.0.0": {
        "release_date": "2024-01-01",
        "amp_families": 12,
        "speaker_characters": 6,
        "effect_types": 10,
        "changes": "Initial schema",
    },
}

# Required fields by schema version
REQUIRED_FIELDS = {
    "1.0.0": {
        "source": ["kind", "duration_sec"],
        "amp": ["family", "gain"],
        "cab": ["configuration", "speaker_character"],
    },
}

# Deprecated field mappings for migration
DEPRECATED_FIELDS = {
    "1.0.0": {
        # old_field -> new_field
    },
}

# Valid values by schema version
VALID_VALUES = {
    "1.0.0": {
        "source.kind": [
            "isolated_guitar", "stem_separated", "full_mix",
            "isolated_bass", "isolated_synth", "isolated_drums",
            "user_recording", "reference_track",
        ],
        "amp.family": [
            "fender_clean", "tweed", "vox_chime", "ac30",
            "marshall_plexi", "marshall_jcm", "mesa_rectifier",
            "5150_peavey", "bogner", "soldano", "dumble", "unknown",
        ],
        "cab.speaker_character": [
            "v30_like", "g12m_like", "g12h_like",
            "alnico_blue_like", "jensen_like", "unknown",
        ],
        "cab.configuration": [
            "1x8", "1x10", "1x12", "2x10", "2x12", "4x10", "4x12",
        ],
    },
}


def validate_descriptor(
    descriptor: Dict,
    schema_version: str = ONTOLOGY_VERSION,
) -> Tuple[bool, List[str]]:
    """Validate a descriptor against schema.

    Args:
        descriptor: Descriptor dict to validate
        schema_version: Schema version to validate against

    Returns:
        (is_valid, list_of_errors)
    """
    errors = []

    if schema_version not in SCHEMA_VERSIONS:
        return False, [f"Unknown schema version: {schema_version}"]

    required = REQUIRED_FIELDS.get(schema_version, {})
    valid_values = VALID_VALUES.get(schema_version, {})

    # Check required fields
    for section, fields in required.items():
        if section not in descriptor:
            errors.append(f"Missing required section: {section}")
            continue
        section_data = descriptor[section]
        if not isinstance(section_data, dict):
            errors.append(f"Section '{section}' must be a dict")
            continue
        for field in fields:
            if field not in section_data:
                errors.append(f"Missing required field: {section}.{field}")

    # Check valid values
    for field_path, valid in valid_values.items():
        parts = field_path.split(".")
        value = descriptor
        for part in parts:
            if isinstance(value, dict):
                value = value.get(part)
            else:
                value = None
                break
        if value is not None and value not in valid:
            errors.append(
                f"Invalid value for {field_path}: '{value}'. "
                f"Must be one of: {valid}"
            )

    # Value range checks
    amp = descriptor.get("amp", {})
    if "gain" in amp:
        gain = amp["gain"]
        if not isinstance(gain, (int, float)) or gain < 0 or gain > 1:
            errors.append(f"amp.gain must be 0-1, got: {gain}")

    voicing = amp.get("voicing", {})
    for param in ["bass", "mid", "treble", "presence"]:
        if param in voicing:
            val = voicing[param]
            if not isinstance(val, (int, float)) or val < 0 or val > 1:
                errors.append(f"amp.voicing.{param} must be 0-1, got: {val}")

    # Check confidence scores
    confidence = descriptor.get("confidence", {})
    for param in ["amp_family", "gain", "cab", "effects"]:
        if param in confidence:
            val = confidence[param]
            if not isinstance(val, (int, float)) or val < 0 or val > 1:
                errors.append(f"confidence.{param} must be 0-1, got: {val}")

    return len(errors) == 0, errors


def migrate_descriptor(
    descriptor: Dict,
    from_version: str,
    to_version: str = ONTOLOGY_VERSION,
) -> Dict:
    """Migrate a descriptor between schema versions.

    Args:
        descriptor: Descriptor to migrate
        from_version: Current schema version
        to_version: Target schema version

    Returns:
        Migrated descriptor (new dict, original unchanged)
    """
    if from_version == to_version:
        return descriptor.copy()

    migrated = descriptor.copy()

    # Apply migrations version by version
    versions = list(SCHEMA_VERSIONS.keys())
    from_idx = versions.index(from_version) if from_version in versions else 0
    to_idx = versions.index(to_version) if to_version in versions else len(versions) - 1

    for i in range(from_idx, to_idx):
        current_version = versions[i]
        next_version = versions[i + 1]

        # Apply deprecated field renames
        deprecated = DEPRECATED_FIELDS.get(next_version, {})
        for old_field, new_field in deprecated.items():
            old_parts = old_field.split(".")
            new_parts = new_field.split(".")

            # Get old value
            old_value = migrated
            for part in old_parts[:-1]:
                old_value = old_value.get(part, {})
            old_value = old_value.get(old_parts[-1])

            if old_value is not None:
                # Set new value
                new_parent = migrated
                for part in new_parts[:-1]:
                    if part not in new_parent:
                        new_parent[part] = {}
                    new_parent = new_parent[part]
                new_parent[new_parts[-1]] = old_value

                # Remove old value
                old_parent = migrated
                for part in old_parts[:-1]:
                    old_parent = old_parent.get(part, {})
                if old_parts[-1] in old_parent:
                    del old_parent[old_parts[-1]]

    # Add schema version marker
    migrated["_schema_version"] = to_version

    return migrated


def get_schema_info(version: str = ONTOLOGY_VERSION) -> Dict:
    """Get information about a schema version."""
    return SCHEMA_VERSIONS.get(version, {})


def ensure_schema_compatibility(descriptor: Dict) -> Dict:
    """Ensure a descriptor is compatible with current schema.

    Adds defaults for missing required fields and migrates from
    older schema versions if needed.
    """
    schema_version = descriptor.get("_schema_version", "1.0.0")

    # Migrate if needed
    if schema_version != ONTOLOGY_VERSION:
        descriptor = migrate_descriptor(descriptor, schema_version, ONTOLOGY_VERSION)

    # Add defaults for missing optional fields
    if "confidence" not in descriptor:
        descriptor["confidence"] = {
            "amp_family": 0.5,
            "gain": 0.5,
            "cab": 0.5,
            "effects": 0.5,
        }

    if "effects" not in descriptor:
        descriptor["effects"] = {}

    if "guitar" not in descriptor:
        descriptor["guitar"] = {
            "pickup_brightness": 0.5,
            "playing_style": "unknown",
        }

    return descriptor
