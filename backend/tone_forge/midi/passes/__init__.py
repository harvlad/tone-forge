"""Multi-pass MIDI extraction passes.

Each pass refines the MIDI extraction:
1. High-Confidence: Conservative initial detection
2. Harmonic Recovery: Fill gaps using harmonic context
3. Phrase Grouping: Identify musical phrases
4. Effect Suppression: Remove delay/reverb artifacts
5. Genre Refinement: Apply archetype priors
6. Confidence Quantization: Quality-aware grid snap
7. Musicality Check: Validate musical coherence
"""
from __future__ import annotations

from .base import (
    ExtractionPass,
    PassResult,
    ExtractedNote,
    PassStatistics,
    ExtractionContext,
    NoteFlag,
)
from .high_confidence import HighConfidencePass
from .harmonic_recovery import HarmonicRecoveryPass
from .phrase_builder import PhraseGroupingPass, Phrase
from .effect_suppression import EffectSuppressionPass
from .genre_refinement import GenreRefinementPass
from .confidence_quantizer import ConfidenceQuantizationPass
from .musicality import MusicalityCheckPass

__all__ = [
    # Base
    "ExtractionPass",
    "PassResult",
    "ExtractedNote",
    "PassStatistics",
    "ExtractionContext",
    "NoteFlag",
    # Passes
    "HighConfidencePass",
    "HarmonicRecoveryPass",
    "PhraseGroupingPass",
    "Phrase",
    "EffectSuppressionPass",
    "GenreRefinementPass",
    "ConfidenceQuantizationPass",
    "MusicalityCheckPass",
]
