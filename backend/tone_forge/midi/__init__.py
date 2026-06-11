"""MIDI extraction and processing for ToneForge.

This module provides multi-pass MIDI extraction with confidence tracking.

Usage:
    from tone_forge.midi import MultiPassExtractor, create_extractor

    # Create extractor with default passes
    extractor = MultiPassExtractor()

    # Or use a preset profile
    extractor = create_extractor("synthwave")

    # Extract MIDI from audio
    result = extractor.extract(audio, sr)

    # Access notes
    for note in result.notes:
        print(f"Pitch: {note.pitch}, Start: {note.start}, Confidence: {note.confidence}")
"""
from __future__ import annotations

from .extraction_pipeline import (
    MIDIExtractionResult,
    MultiPassExtractor,
    create_extractor,
    create_extractor_for_profile,
    create_synth_aware_extractor,
    extract_with_synth_awareness,
)
from .passes import (
    ExtractionPass,
    PassResult,
    ExtractedNote,
    PassStatistics,
    ExtractionContext,
    NoteFlag,
    NoteProvenance,
    HighConfidencePass,
    EffectSuppressionPass,
    ConfidenceQuantizationPass,
)
from .profiles import (
    ExtractionProfile,
    ProfileRegistry,
    get_profile_registry,
    get_profile,
    get_default_profile_for_stem,
)
from .profile_classifier import (
    ClassificationFeatures,
    ProfileClassification,
    ProfileClassifier,
    get_profile_classifier,
    classify_profile,
    classify_profile_from_role,
)
from .polyphony_estimator import (
    PolyphonyClass,
    PolyphonyEstimate,
    PolyphonyEstimator,
    estimate_polyphony,
    get_extraction_config_for_polyphony,
)

__all__ = [
    # Pipeline
    "MIDIExtractionResult",
    "MultiPassExtractor",
    "create_extractor",
    "create_extractor_for_profile",
    "create_synth_aware_extractor",
    "extract_with_synth_awareness",
    # Base types
    "ExtractionPass",
    "PassResult",
    "ExtractedNote",
    "PassStatistics",
    "ExtractionContext",
    "NoteFlag",
    "NoteProvenance",
    # Passes
    "HighConfidencePass",
    "EffectSuppressionPass",
    "ConfidenceQuantizationPass",
    # Profiles
    "ExtractionProfile",
    "ProfileRegistry",
    "get_profile_registry",
    "get_profile",
    "get_default_profile_for_stem",
    # Profile classification
    "ClassificationFeatures",
    "ProfileClassification",
    "ProfileClassifier",
    "get_profile_classifier",
    "classify_profile",
    "classify_profile_from_role",
    # Polyphony estimation
    "PolyphonyClass",
    "PolyphonyEstimate",
    "PolyphonyEstimator",
    "estimate_polyphony",
    "get_extraction_config_for_polyphony",
]
