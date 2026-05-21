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
)
from .passes import (
    ExtractionPass,
    PassResult,
    ExtractedNote,
    PassStatistics,
    ExtractionContext,
    NoteFlag,
    HighConfidencePass,
    EffectSuppressionPass,
    ConfidenceQuantizationPass,
)

__all__ = [
    # Pipeline
    "MIDIExtractionResult",
    "MultiPassExtractor",
    "create_extractor",
    # Base types
    "ExtractionPass",
    "PassResult",
    "ExtractedNote",
    "PassStatistics",
    "ExtractionContext",
    "NoteFlag",
    # Passes
    "HighConfidencePass",
    "EffectSuppressionPass",
    "ConfidenceQuantizationPass",
]
