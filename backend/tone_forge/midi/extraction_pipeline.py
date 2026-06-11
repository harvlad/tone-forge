"""Multi-pass MIDI extraction pipeline.

Orchestrates multiple extraction passes to produce high-quality MIDI
from audio with confidence tracking and quality awareness.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field, replace
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

# Profile-aware cleanup passes (Sprint 3)
from .passes.harmonic_suppression import HarmonicSuppressionPass
from .passes.delay_cleanup import DelayCleanupPass
from .passes.octave_correction import OctaveCorrectionPass
from .passes.subharmonic_suppression import SubHarmonicSuppressionPass
from .passes.octave_doubling import OctaveDoublingPass
from .passes.beat_grid_filter import BeatGridFilterPass
from .passes.key_conformity import KeyConformityPass

from .profiles import ExtractionProfile, get_profile, get_default_profile_for_stem

logger = logging.getLogger(__name__)

# Optional: profile classifier for auto-classification
try:
    from .profile_classifier import (
        classify_profile,
        classify_profile_from_role,
        ProfileClassification,
    )
    _CLASSIFIER_AVAILABLE = True
except ImportError:
    _CLASSIFIER_AVAILABLE = False

# Optional: synth behavior analyzer for synth-aware extraction
try:
    from ..analysis.synth_behavior import (
        SynthBehavior,
        SynthBehaviorAnalyzer,
        analyze_synth_behavior,
    )
    _SYNTH_ANALYZER_AVAILABLE = True
except ImportError:
    _SYNTH_ANALYZER_AVAILABLE = False


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

    def _extract_drums(
        self,
        audio: np.ndarray,
        sr: int,
        tempo: Optional[float] = None,
    ) -> "MIDIExtractionResult":
        """Extract drum MIDI using specialized onset detection.

        Drums require onset-based detection rather than pitch-based.
        Uses bandpass filtering and spectral classification.

        Args:
            audio: Audio signal
            sr: Sample rate
            tempo: Tempo in BPM (estimated if not provided)

        Returns:
            MIDIExtractionResult with drum notes
        """
        import tempfile
        import soundfile as sf

        # Write audio to temp file for drum extractor
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
            sf.write(f.name, audio, sr)
            audio_path = f.name

        try:
            from ..midi_extractor import extract_drum_midi

            result = extract_drum_midi(audio_path, "Drums")

            # Convert to notes format for consistency
            # Drums use GM mapping: kick=36, snare=38, hihat=42
            notes = []
            if hasattr(result, 'notes') and result.notes:
                notes = result.notes
            elif result.content:
                # Decode MIDI and extract notes
                import base64
                import io
                try:
                    import pretty_midi
                    midi_bytes = base64.b64decode(result.content)
                    pm = pretty_midi.PrettyMIDI(io.BytesIO(midi_bytes))
                    for inst in pm.instruments:
                        for note in inst.notes:
                            notes.append(
                                ExtractedNote(
                                    pitch=note.pitch,
                                    start=note.start,
                                    end=note.end,
                                    velocity=note.velocity,
                                    confidence=0.8,  # Default confidence for drums
                                    source_pass=0,  # Drum extraction pass
                                )
                            )
                except ImportError:
                    logger.warning("pretty_midi not available for drum MIDI parsing")

            return MIDIExtractionResult(
                notes=notes,
                tempo=result.tempo_bpm or 120.0,
                key=(0, "major"),
                time_signature=(4, 4),
                overall_confidence=0.8,
                pass_results=[],
                total_execution_time_ms=0.0,
                warnings=[],
                metadata={"profile_used": "drums", "stem_type": "drums"},
            )
        finally:
            import os
            try:
                os.unlink(audio_path)
            except Exception:
                pass

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
        profile: Optional[ExtractionProfile] = None,
        profile_name: Optional[str] = None,
        auto_classify: bool = False,
        synth_behavior: Optional[Any] = None,
        analyze_synth: bool = False,
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
            profile: ExtractionProfile to use (overrides thresholds)
            profile_name: Name of profile to use (alternative to profile)
            auto_classify: If True, auto-classify profile from audio features
            synth_behavior: Pre-analyzed SynthBehavior (optional)
            analyze_synth: If True, analyze synth behavior automatically

        Returns:
            MIDIExtractionResult with notes and statistics
        """
        start_time = time.time()
        all_warnings: List[str] = []
        synth_analysis = None
        profile_classification = None

        # Ensure mono audio
        if audio.ndim > 1:
            audio = np.mean(audio, axis=0)

        # Synth behavior analysis
        if synth_behavior is not None:
            synth_analysis = synth_behavior
        elif analyze_synth and _SYNTH_ANALYZER_AVAILABLE:
            try:
                synth_analysis = analyze_synth_behavior(audio, sr, tempo)
                logger.debug(
                    f"Synth behavior: dominant={synth_analysis.dominant_behavior}, "
                    f"supersaw={synth_analysis.supersaw_detected}, "
                    f"glide={synth_analysis.glide_detected}, "
                    f"arp={synth_analysis.arpeggiator_detected}"
                )
            except Exception as e:
                all_warnings.append(f"Synth analysis failed: {e}")

        # Resolve profile: explicit profile > profile_name > auto-classify > stem default
        if profile is None and profile_name is not None:
            profile = get_profile(profile_name)
            if profile is None:
                all_warnings.append(f"Profile '{profile_name}' not found, using defaults")

        if profile is None and auto_classify and _CLASSIFIER_AVAILABLE:
            # Auto-classify from audio or role classification
            if role_classification is not None:
                profile_name_classified = classify_profile_from_role(role_classification)
                profile = get_profile(profile_name_classified)
                logger.debug(f"Auto-classified from role: {profile_name_classified}")
            else:
                profile_classification = classify_profile(audio, sr, stem_type)
                profile = get_profile(profile_classification.profile_name)
                logger.debug(
                    f"Auto-classified profile: {profile_classification.profile_name} "
                    f"(confidence: {profile_classification.confidence:.2f})"
                )

        if profile is None and stem_type is not None:
            profile = get_default_profile_for_stem(stem_type)

        # Drums use specialized onset-based extraction, not melodic pipeline
        if stem_type == "drums":
            return self._extract_drums(audio, sr, tempo)

        # Apply profile parameters if available
        if profile is not None:
            onset_threshold = profile.onset_threshold
            frame_threshold = profile.frame_threshold
            min_note_ms = profile.min_note_ms
            min_velocity = profile.min_velocity
            logger.debug(f"Using profile: {profile.name}")

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

        # Apply pitch offset if specified in profile
        # Bass synths often sound an octave lower than written, so this corrects the octave
        pitch_offset = getattr(profile, 'pitch_offset', 0) if profile else 0
        if pitch_offset != 0:
            notes = [
                replace(n, pitch=n.pitch + pitch_offset)
                for n in notes
            ]
            logger.debug(f"Applied pitch offset of {pitch_offset} semitones to {len(notes)} notes")

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
                "profile_used": profile.name if profile else None,
                "profile_auto_classified": profile_classification is not None,
                "profile_classification": (
                    profile_classification.to_dict() if profile_classification else None
                ),
                "synth_analyzed": synth_analysis is not None,
                "synth_behavior": (
                    synth_analysis.to_dict() if synth_analysis else None
                ),
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


def create_extractor_for_profile(
    extraction_profile: ExtractionProfile,
    **kwargs,
) -> MultiPassExtractor:
    """Create an extractor configured for a specific ExtractionProfile.

    This creates a pass pipeline based on the profile's cleanup toggles:
    - Always includes HighConfidencePass
    - Conditionally includes cleanup passes based on profile settings
    - Always includes MusicalityCheckPass at the end

    Profile toggles respected:
    - enable_harmonic_suppression: HarmonicSuppressionPass
    - enable_delay_cleanup: DelayCleanupPass (probabilistic)
    - enable_octave_correction: OctaveCorrectionPass
    - enable_beat_grid_filter: BeatGridFilterPass
    - enable_key_conformity: KeyConformityPass

    Args:
        extraction_profile: ExtractionProfile to configure from
        **kwargs: Additional arguments for MultiPassExtractor

    Returns:
        Configured MultiPassExtractor with profile-driven passes
    """
    passes: List[ExtractionPass] = []
    pass_num = 0

    # Pass 1: High confidence extraction with profile thresholds
    pass_num += 1
    passes.append(
        HighConfidencePass(
            pass_number=pass_num,
            min_confidence=0.4,  # Base confidence threshold
            onset_threshold=extraction_profile.onset_threshold,
            frame_threshold=extraction_profile.frame_threshold,
        )
    )

    # Pass 2: Harmonic recovery (always run - recovery != suppression)
    pass_num += 1
    passes.append(HarmonicRecoveryPass(pass_number=pass_num))

    # Pass 3: Phrase grouping
    pass_num += 1
    passes.append(PhraseGroupingPass(pass_number=pass_num))

    # Profile-driven cleanup passes (Sprint 3)

    # Octave correction (for bass stems primarily)
    if extraction_profile.enable_octave_correction:
        pass_num += 1
        # Use aggressive settings for bass stems
        is_bass_stem = extraction_profile.stem_type and extraction_profile.stem_type.lower() in (
            "bass", "sub_bass", "mono_bass", "poly_bass"
        )
        passes.append(
            OctaveCorrectionPass(
                pass_number=pass_num,
                min_correction_probability=0.5 if is_bass_stem else 0.6,
                check_double_octave=True,
                aggressive_bass_correction=is_bass_stem,
            )
        )

    # Sub-harmonic suppression (removes sub-octave artifacts)
    if getattr(extraction_profile, 'enable_subharmonic_suppression', False):
        pass_num += 1
        passes.append(
            SubHarmonicSuppressionPass(
                pass_number=pass_num,
                timing_tolerance_ms=50.0,
                min_suppression_probability=0.6,
                apply_pitch_floor=True,
            )
        )

    # Octave doubling (adds missing upper octave notes for bass)
    if getattr(extraction_profile, 'enable_octave_doubling', False):
        pass_num += 1
        passes.append(
            OctaveDoublingPass(
                pass_number=pass_num,
                min_confidence_for_doubling=0.5,
                doubling_confidence_factor=0.7,
            )
        )

    # Harmonic suppression (removes octave/fifth artifacts)
    if extraction_profile.enable_harmonic_suppression:
        pass_num += 1
        passes.append(
            HarmonicSuppressionPass(
                pass_number=pass_num,
                octave_enabled=True,
                fifth_enabled=True,
                third_enabled=False,  # Conservative by default
                min_harmonic_probability=0.7,
            )
        )

    # Probabilistic delay cleanup (replaces binary effect suppression)
    if extraction_profile.enable_delay_cleanup:
        pass_num += 1
        passes.append(
            DelayCleanupPass(
                pass_number=pass_num,
                min_suppression_probability=0.85,  # High threshold - preserve real notes
            )
        )

    # Beat grid filter
    if extraction_profile.enable_beat_grid_filter:
        pass_num += 1
        passes.append(
            BeatGridFilterPass(
                pass_number=pass_num,
                grid_strength=extraction_profile.quantize_strength,
                grid_divisions=16,
            )
        )

    # Key conformity validation
    if extraction_profile.enable_key_conformity:
        pass_num += 1
        passes.append(
            KeyConformityPass(
                pass_number=pass_num,
                strictness=extraction_profile.key_filter_strictness,
            )
        )

    # Genre refinement
    pass_num += 1
    passes.append(GenreRefinementPass(pass_number=pass_num))

    # Confidence quantization (final timing adjustment)
    pass_num += 1
    passes.append(
        ConfidenceQuantizationPass(
            pass_number=pass_num,
            base_strength=extraction_profile.quantize_strength,
        )
    )

    # Musicality check (always last)
    pass_num += 1
    passes.append(MusicalityCheckPass(pass_number=pass_num))

    # Renumber passes sequentially
    for i, p in enumerate(passes):
        p.pass_number = i + 1

    return MultiPassExtractor(passes=passes, **kwargs)


def create_synth_aware_extractor(
    synth_behavior: Any,
    base_profile: Optional[ExtractionProfile] = None,
    stem_type: Optional[str] = None,
    **kwargs,
) -> MultiPassExtractor:
    """Create an extractor adapted to synth behavior.

    Adapts the extraction pipeline based on detected synth characteristics:
    - Supersaw: Disable harmonic suppression, preserve octave layering
    - Glide: Enable pitch smoothing, reduce onset sensitivity
    - Arpeggiator: Use tight timing, high rhythmic precision
    - Sidechain: Modulate velocity with pump pattern awareness
    - Pad: Lower onset threshold, longer sustains
    - Bass: Aggressive octave correction, sub-harmonic awareness

    Args:
        synth_behavior: SynthBehavior from analysis
        base_profile: Optional base ExtractionProfile
        stem_type: Type of stem for fallback defaults
        **kwargs: Additional arguments for MultiPassExtractor

    Returns:
        Configured MultiPassExtractor with synth-aware passes
    """
    if not _SYNTH_ANALYZER_AVAILABLE:
        # Fallback to default extractor
        return MultiPassExtractor(**kwargs)

    passes: List[ExtractionPass] = []
    pass_num = 0

    # Determine behavior characteristics
    dominant = getattr(synth_behavior, 'dominant_behavior', 'standard')
    is_supersaw = getattr(synth_behavior, 'supersaw_detected', False)
    has_glide = getattr(synth_behavior, 'glide_detected', False)
    has_arp = getattr(synth_behavior, 'arpeggiator_detected', False)
    has_sidechain = getattr(synth_behavior, 'sidechain_detected', False)
    has_octave_layers = getattr(synth_behavior, 'octave_layering', False)
    is_monophonic = getattr(synth_behavior, 'is_monophonic', True)

    # Calculate adjusted thresholds based on synth behavior
    onset_threshold = 0.5
    frame_threshold = 0.4
    min_note_ms = 50.0

    if dominant == "pad" or is_supersaw:
        # Pads/supersaws have soft attacks
        onset_threshold = 0.35
        frame_threshold = 0.30
        min_note_ms = 100.0

    if has_arp:
        # Arps need tight timing
        onset_threshold = 0.55
        min_note_ms = 30.0

    if has_glide:
        # Glide notes need looser onset detection
        onset_threshold = 0.40
        min_note_ms = 80.0

    if dominant == "bass":
        onset_threshold = 0.50
        frame_threshold = 0.45
        min_note_ms = 60.0

    # Override with base profile if provided
    if base_profile:
        onset_threshold = base_profile.onset_threshold
        frame_threshold = base_profile.frame_threshold
        min_note_ms = base_profile.min_note_ms

    # Pass 1: High confidence extraction with synth-aware thresholds
    pass_num += 1
    passes.append(
        HighConfidencePass(
            pass_number=pass_num,
            min_confidence=0.4,
            onset_threshold=onset_threshold,
            frame_threshold=frame_threshold,
        )
    )

    # Pass 2: Harmonic recovery (always)
    pass_num += 1
    passes.append(HarmonicRecoveryPass(pass_number=pass_num))

    # Pass 3: Phrase grouping
    pass_num += 1
    if has_arp:
        # Tighter phrase grouping for arps
        passes.append(PhraseGroupingPass(pass_number=pass_num, gap_threshold_ms=150))
    else:
        passes.append(PhraseGroupingPass(pass_number=pass_num))

    # Synth-specific passes

    # Octave correction (skip for supersaw/layered sounds)
    if dominant == "bass" and not has_octave_layers:
        pass_num += 1
        passes.append(
            OctaveCorrectionPass(
                pass_number=pass_num,
                min_correction_probability=0.5,
                aggressive_bass_correction=True,
            )
        )
    elif is_monophonic and not is_supersaw and not has_octave_layers:
        pass_num += 1
        passes.append(
            OctaveCorrectionPass(
                pass_number=pass_num,
                min_correction_probability=0.6,
            )
        )

    # Harmonic suppression (SKIP for supersaw and octave-layered sounds)
    if not is_supersaw and not has_octave_layers:
        pass_num += 1
        passes.append(
            HarmonicSuppressionPass(
                pass_number=pass_num,
                octave_enabled=True,
                fifth_enabled=True,
                # Be less aggressive for lead synths with vibrato
                min_harmonic_probability=0.8 if dominant == "lead" else 0.7,
            )
        )

    # Effect suppression for delay/reverb
    # Less aggressive for pads and supersaws
    if not (dominant == "pad" or is_supersaw):
        pass_num += 1
        passes.append(
            EffectSuppressionPass(
                pass_number=pass_num,
                min_delay_repeats=3 if has_sidechain else 2,
                reverb_decay_threshold=0.2,
            )
        )

    # Delay cleanup (skip if sidechain is dominant - pumping can look like delay)
    if not has_sidechain or (has_sidechain and synth_behavior.sidechain_depth < 0.5):
        pass_num += 1
        passes.append(
            DelayCleanupPass(
                pass_number=pass_num,
                min_suppression_probability=0.85,
            )
        )

    # Genre refinement
    pass_num += 1
    passes.append(GenreRefinementPass(pass_number=pass_num))

    # Beat grid filter (tighter for arps)
    pass_num += 1
    if has_arp:
        passes.append(
            BeatGridFilterPass(
                pass_number=pass_num,
                grid_strength=0.9,
                grid_divisions=32,  # Tighter grid for arps
            )
        )
    else:
        passes.append(
            BeatGridFilterPass(
                pass_number=pass_num,
                grid_strength=0.7,
                grid_divisions=16,
            )
        )

    # Confidence quantization
    pass_num += 1
    quantize_strength = 0.7
    if has_arp:
        quantize_strength = 0.9  # Tight quantization for arps
    elif dominant == "pad":
        quantize_strength = 0.5  # Looser for pads

    passes.append(
        ConfidenceQuantizationPass(
            pass_number=pass_num,
            base_strength=quantize_strength,
        )
    )

    # Musicality check (always last)
    pass_num += 1
    passes.append(
        MusicalityCheckPass(
            pass_number=pass_num,
            # More lenient for pads and supersaws
            min_final_confidence=0.25 if (dominant == "pad" or is_supersaw) else 0.3,
        )
    )

    # Renumber passes sequentially
    for i, p in enumerate(passes):
        p.pass_number = i + 1

    return MultiPassExtractor(passes=passes, **kwargs)


def extract_with_synth_awareness(
    audio: np.ndarray,
    sr: int,
    stem_type: Optional[str] = None,
    genre: Optional[str] = None,
    tempo: Optional[float] = None,
    **kwargs,
) -> MIDIExtractionResult:
    """Convenience function for synth-aware extraction.

    Analyzes audio for synth behavior first, then creates an
    appropriate extractor and runs extraction.

    Args:
        audio: Audio signal
        sr: Sample rate
        stem_type: Type of stem
        genre: Detected genre
        tempo: Tempo hint
        **kwargs: Additional extraction arguments

    Returns:
        MIDIExtractionResult
    """
    if not _SYNTH_ANALYZER_AVAILABLE:
        # Fallback to standard extraction
        extractor = MultiPassExtractor()
        return extractor.extract(audio, sr, stem_type=stem_type, genre=genre, tempo=tempo, **kwargs)

    # Analyze synth behavior
    synth_behavior = analyze_synth_behavior(audio, sr, tempo)

    # Create synth-aware extractor
    extractor = create_synth_aware_extractor(
        synth_behavior,
        stem_type=stem_type,
    )

    # Run extraction with synth behavior info
    return extractor.extract(
        audio,
        sr,
        stem_type=stem_type,
        genre=genre,
        tempo=tempo,
        synth_behavior=synth_behavior,
        **kwargs,
    )
