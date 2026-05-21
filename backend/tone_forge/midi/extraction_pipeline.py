"""Multi-pass MIDI extraction pipeline.

Orchestrates multiple extraction passes to produce high-quality MIDI
from audio with confidence tracking and quality awareness.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .passes.base import (
    ExtractionContext,
    ExtractionPass,
    ExtractedNote,
    PassResult,
    PassStatistics,
)
from .passes.high_confidence import HighConfidencePass
from .passes.harmonic_recovery import HarmonicRecoveryPass
from .passes.phrase_builder import PhraseGroupingPass
from .passes.effect_suppression import EffectSuppressionPass
from .passes.genre_refinement import GenreRefinementPass
from .passes.confidence_quantizer import ConfidenceQuantizationPass
from .passes.musicality import MusicalityCheckPass

logger = logging.getLogger(__name__)


@dataclass
class MIDIExtractionResult:
    """Complete result from multi-pass extraction."""

    notes: List[ExtractedNote]
    tempo: float
    key: Optional[Tuple[int, str]]  # (root, mode)
    time_signature: Tuple[int, int]
    overall_confidence: float
    pass_results: List[PassResult]
    total_execution_time_ms: float
    warnings: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def note_count(self) -> int:
        """Total number of notes."""
        return len(self.notes)

    @property
    def pass_statistics(self) -> Dict[int, PassStatistics]:
        """Statistics from each pass."""
        return {
            result.statistics.pass_number: result.statistics
            for result in self.pass_results
        }

    def get_notes_by_confidence(
        self,
        min_confidence: float = 0.0,
    ) -> List[ExtractedNote]:
        """Get notes above a confidence threshold."""
        return [n for n in self.notes if n.confidence >= min_confidence]

    def to_tuples(self) -> List[Tuple[int, float, float, int]]:
        """Convert to list of (pitch, start, end, velocity) tuples."""
        return [n.to_tuple() for n in self.notes]

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "notes": [n.to_dict() for n in self.notes],
            "tempo": self.tempo,
            "key": self.key,
            "time_signature": self.time_signature,
            "overall_confidence": self.overall_confidence,
            "note_count": self.note_count,
            "total_execution_time_ms": self.total_execution_time_ms,
            "warnings": self.warnings,
            "pass_statistics": {
                k: v.to_dict() for k, v in self.pass_statistics.items()
            },
        }


class MultiPassExtractor:
    """Orchestrates multi-pass MIDI extraction.

    The extractor runs a sequence of passes, each refining the MIDI
    output. Passes can add, remove, or modify notes, with full
    tracking of what changed.

    Default passes (full pipeline):
    1. HighConfidencePass - Conservative initial detection
    2. HarmonicRecoveryPass - Fill gaps using harmonic context
    3. PhraseGroupingPass - Group into musical phrases
    4. EffectSuppressionPass - Remove delay/reverb artifacts
    5. GenreRefinementPass - Apply genre archetype priors
    6. ConfidenceQuantizationPass - Grid-snap with confidence
    7. MusicalityCheckPass - Validate musical coherence
    """

    def __init__(
        self,
        passes: Optional[List[ExtractionPass]] = None,
        skip_passes: Optional[List[str]] = None,
    ):
        """Initialize the multi-pass extractor.

        Args:
            passes: Custom pass sequence (uses defaults if None)
            skip_passes: Pass names to skip from default sequence
        """
        if passes is not None:
            self.passes = passes
        else:
            self.passes = self._create_default_passes()

        # Remove skipped passes
        if skip_passes:
            self.passes = [
                p for p in self.passes
                if p.name not in skip_passes
            ]

        # Ensure pass numbers are sequential
        for i, p in enumerate(self.passes):
            p.pass_number = i + 1

    def _create_default_passes(self) -> List[ExtractionPass]:
        """Create the default pass sequence with all 7 passes."""
        return [
            HighConfidencePass(pass_number=1),
            HarmonicRecoveryPass(pass_number=2),
            PhraseGroupingPass(pass_number=3),
            EffectSuppressionPass(pass_number=4),
            GenreRefinementPass(pass_number=5),
            ConfidenceQuantizationPass(pass_number=6),
            MusicalityCheckPass(pass_number=7),
        ]

    def extract(
        self,
        audio: np.ndarray,
        sr: int,
        stem_type: Optional[str] = None,
        genre: Optional[str] = None,
        tempo: Optional[float] = None,
        key: Optional[Tuple[int, str]] = None,
        time_signature: Tuple[int, int] = (4, 4),
        stem_quality: Optional[Any] = None,
        contamination: Optional[Any] = None,
        role_classification: Optional[Any] = None,
        confidence_map: Optional[Any] = None,
        onset_threshold: float = 0.5,
        frame_threshold: float = 0.4,
        min_note_ms: float = 50.0,
        min_velocity: int = 20,
    ) -> MIDIExtractionResult:
        """Run full multi-pass extraction.

        Args:
            audio: Audio signal (mono or stereo)
            sr: Sample rate
            stem_type: Type of stem ("bass", "vocals", etc.)
            genre: Detected genre
            tempo: Tempo in BPM (estimated if not provided)
            key: Key as (root, mode) tuple
            time_signature: Time signature as (numerator, denominator)
            stem_quality: Quality analysis from reconstruction module
            contamination: Contamination analysis
            role_classification: Role classification
            confidence_map: Region confidence map
            onset_threshold: Base onset detection threshold
            frame_threshold: Base frame threshold
            min_note_ms: Minimum note duration in milliseconds
            min_velocity: Minimum MIDI velocity

        Returns:
            MIDIExtractionResult with notes and statistics
        """
        start_time = time.time()

        # Ensure mono audio
        if audio.ndim > 1:
            audio = np.mean(audio, axis=0)

        # Build context
        context = ExtractionContext(
            audio=audio,
            sr=sr,
            stem_type=stem_type,
            genre=genre,
            tempo=tempo,
            key=key,
            time_signature=time_signature,
            stem_quality=stem_quality,
            contamination=contamination,
            confidence_map=confidence_map,
            role_classification=role_classification,
            onset_threshold=onset_threshold,
            frame_threshold=frame_threshold,
            min_note_ms=min_note_ms,
            min_velocity=min_velocity,
        )

        # Run passes
        notes: List[ExtractedNote] = []
        pass_results: List[PassResult] = []
        all_warnings: List[str] = []

        for extraction_pass in self.passes:
            logger.debug(f"Running pass {extraction_pass.pass_number}: {extraction_pass.name}")

            result = extraction_pass.process(notes, context)

            notes = result.notes
            pass_results.append(result)
            all_warnings.extend(result.warnings)

            logger.debug(
                f"Pass {extraction_pass.name}: "
                f"{result.statistics.notes_input} -> {result.statistics.notes_output} notes"
            )

            # Update tempo in context if estimated
            if context.tempo is None and "tempo" in result.metadata:
                context = ExtractionContext(
                    audio=context.audio,
                    sr=context.sr,
                    stem_type=context.stem_type,
                    genre=context.genre,
                    tempo=result.metadata["tempo"],
                    key=context.key,
                    time_signature=context.time_signature,
                    stem_quality=context.stem_quality,
                    contamination=context.contamination,
                    confidence_map=context.confidence_map,
                    role_classification=context.role_classification,
                    onset_threshold=context.onset_threshold,
                    frame_threshold=context.frame_threshold,
                    min_note_ms=context.min_note_ms,
                    min_velocity=context.min_velocity,
                )

        # Sort notes by start time
        notes = sorted(notes, key=lambda n: (n.start, n.pitch))

        # Calculate overall confidence
        overall_confidence = self._calculate_overall_confidence(notes, pass_results)

        # Get final tempo (from context or estimate)
        final_tempo = context.tempo
        if final_tempo is None:
            final_tempo = self._estimate_tempo(notes)

        total_time = (time.time() - start_time) * 1000

        return MIDIExtractionResult(
            notes=notes,
            tempo=final_tempo,
            key=context.key,
            time_signature=context.time_signature,
            overall_confidence=overall_confidence,
            pass_results=pass_results,
            total_execution_time_ms=total_time,
            warnings=all_warnings,
            metadata={
                "stem_type": stem_type,
                "genre": genre,
                "passes_run": [p.name for p in self.passes],
            },
        )

    def _calculate_overall_confidence(
        self,
        notes: List[ExtractedNote],
        pass_results: List[PassResult],
    ) -> float:
        """Calculate overall extraction confidence."""
        if len(notes) == 0:
            return 0.0

        # Weighted average of note confidences
        avg_note_confidence = np.mean([n.confidence for n in notes])

        # Penalty for heavy filtering (many notes removed)
        if pass_results:
            initial_count = pass_results[0].statistics.notes_output
            final_count = len(notes)
            if initial_count > 0:
                retention_ratio = final_count / initial_count
            else:
                retention_ratio = 1.0
        else:
            retention_ratio = 1.0

        # Penalty for warnings
        warning_count = sum(len(r.warnings) for r in pass_results)
        warning_penalty = max(0, 1 - warning_count * 0.05)

        # Combine factors
        overall = (
            avg_note_confidence * 0.6 +
            retention_ratio * 0.2 +
            warning_penalty * 0.2
        )

        return float(np.clip(overall, 0, 1))

    def _estimate_tempo(self, notes: List[ExtractedNote]) -> float:
        """Estimate tempo from notes."""
        if len(notes) < 2:
            return 120.0

        onsets = sorted([n.start for n in notes])
        iois = np.diff(onsets)

        if len(iois) == 0:
            return 120.0

        # Filter very short IOIs
        iois = iois[iois > 0.1]
        if len(iois) == 0:
            return 120.0

        # Use median and assume 8th note
        median_ioi = np.median(iois)
        tempo = 60.0 / (median_ioi * 2)

        return float(np.clip(tempo, 60, 200))

    def add_pass(self, extraction_pass: ExtractionPass, position: int = -1):
        """Add a pass to the pipeline.

        Args:
            extraction_pass: Pass to add
            position: Position in sequence (-1 for end)
        """
        if position == -1:
            self.passes.append(extraction_pass)
        else:
            self.passes.insert(position, extraction_pass)

        # Renumber passes
        for i, p in enumerate(self.passes):
            p.pass_number = i + 1

    def remove_pass(self, pass_name: str):
        """Remove a pass by name.

        Args:
            pass_name: Name of pass to remove
        """
        self.passes = [p for p in self.passes if p.name != pass_name]

        # Renumber passes
        for i, p in enumerate(self.passes):
            p.pass_number = i + 1


def create_extractor(
    profile: str = "default",
    **kwargs,
) -> MultiPassExtractor:
    """Create an extractor with a preset profile.

    Args:
        profile: Preset profile name
            - "default": Full 7-pass extraction pipeline
            - "high_quality": All passes with conservative settings
            - "fast": Minimal passes for speed
            - "synthwave": Optimized for synthwave genre
        **kwargs: Additional arguments for MultiPassExtractor

    Returns:
        Configured MultiPassExtractor
    """
    if profile == "default":
        return MultiPassExtractor(**kwargs)

    elif profile == "fast":
        # Minimal passes for speed
        return MultiPassExtractor(
            passes=[
                HighConfidencePass(min_confidence=0.5),
                ConfidenceQuantizationPass(base_strength=0.7),
            ],
            **kwargs,
        )

    elif profile == "high_quality":
        # All passes with conservative settings
        return MultiPassExtractor(
            passes=[
                HighConfidencePass(
                    min_confidence=0.7,
                    onset_threshold=0.6,
                    frame_threshold=0.5,
                ),
                HarmonicRecoveryPass(
                    recovery_confidence=0.6,
                ),
                PhraseGroupingPass(
                    min_phrase_notes=3,
                ),
                EffectSuppressionPass(
                    min_delay_repeats=2,
                    reverb_decay_threshold=0.25,
                ),
                GenreRefinementPass(
                    strict_mode=True,
                ),
                ConfidenceQuantizationPass(
                    base_strength=0.8,
                    grid_divisions=16,
                ),
                MusicalityCheckPass(
                    min_final_confidence=0.4,
                ),
            ],
            **kwargs,
        )

    elif profile == "synthwave":
        # Optimized for reverb-heavy synthwave
        return MultiPassExtractor(
            passes=[
                HighConfidencePass(
                    min_confidence=0.5,  # Lower - soft attacks
                    onset_threshold=0.4,
                    frame_threshold=0.35,
                ),
                HarmonicRecoveryPass(
                    recovery_confidence=0.5,
                    octave_search_enabled=True,
                    fifth_search_enabled=True,  # Synth stacks use fifths
                ),
                PhraseGroupingPass(
                    gap_threshold_ms=400,  # Longer gaps in synthwave
                ),
                EffectSuppressionPass(
                    min_delay_repeats=3,  # More repeats to confirm
                    reverb_decay_threshold=0.2,  # Aggressive reverb removal
                    max_echo_gap_ms=600,  # Longer echoes
                ),
                GenreRefinementPass(
                    apply_velocity_adjustment=True,
                    apply_sustain_adjustment=True,
                ),
                ConfidenceQuantizationPass(
                    base_strength=0.6,  # Less strict - synthwave is loose
                    grid_divisions=16,
                    swing_amount=0.1,  # Slight swing
                ),
                MusicalityCheckPass(
                    min_final_confidence=0.3,
                    dissonance_tolerance=0.4,  # More dissonance allowed
                ),
            ],
            **kwargs,
        )

    else:
        raise ValueError(f"Unknown profile: {profile}")
