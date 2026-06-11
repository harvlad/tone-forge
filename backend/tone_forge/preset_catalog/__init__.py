"""Preset catalog for ToneForge reconstruction intelligence.

This module handles:
- Preset discovery from Ableton Live
- Preset rendering to audio
- Fingerprint extraction and cataloging
- Preset similarity search
"""
from .preset_discovery import (
    PresetInfo,
    PresetDiscovery,
    discover_presets,
    safe_filename,
    detect_safe_filename_collisions,
    SUPPORTED_INSTRUMENTS,
)
from .test_sequence import (
    TestNote,
    generate_test_sequence,
    generate_bass_test_sequence,
    generate_lead_test_sequence,
    generate_pad_test_sequence,
    generate_keys_test_sequence,
    get_test_sequence_for_type,
    notes_to_midi_bytes,
)
from .preset_als_generator import (
    RenderJob,
    create_preset_als,
    generate_render_jobs,
    create_als_for_job,
)

__all__ = [
    # Discovery
    "PresetInfo",
    "PresetDiscovery",
    "discover_presets",
    "safe_filename",
    "detect_safe_filename_collisions",
    "SUPPORTED_INSTRUMENTS",
    # Test sequences
    "TestNote",
    "generate_test_sequence",
    "generate_bass_test_sequence",
    "generate_lead_test_sequence",
    "generate_pad_test_sequence",
    "generate_keys_test_sequence",
    "get_test_sequence_for_type",
    "notes_to_midi_bytes",
    # ALS generation
    "RenderJob",
    "create_preset_als",
    "generate_render_jobs",
    "create_als_for_job",
]
