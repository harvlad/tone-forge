"""ML-based timing correction for MIDI refinement.

Provides intelligent quantization that:
- Learns appropriate grid divisions from context
- Preserves intentional swing and groove
- Adapts to different musical styles
- Corrects timing while maintaining feel

Unlike fixed-grid quantization, this uses context-aware
decisions about when and how much to quantize.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

import numpy as np

logger = logging.getLogger(__name__)

# Model paths
DEFAULT_MODEL_DIR = Path.home() / ".toneforge" / "models" / "midi"


@dataclass
class TimingContext:
    """Context for timing correction decisions."""

    # Note timing
    start_time: float
    end_time: float
    beat_position: float          # 0-1, position within beat

    # Grid distances
    dist_to_16th: float           # Distance to nearest 16th note
    dist_to_8th: float            # Distance to nearest 8th note
    dist_to_quarter: float        # Distance to nearest quarter note
    dist_to_triplet: float        # Distance to nearest triplet

    # Musical context
    tempo_bpm: float
    time_signature_num: int = 4   # e.g., 4 in 4/4
    time_signature_den: int = 4   # e.g., 4 in 4/4

    # Phrase context
    phrase_start: bool = False    # Is this near phrase start
    phrase_end: bool = False      # Is this near phrase end
    downbeat: bool = False        # Is this on a downbeat

    # Style indicators
    swing_amount: float = 0.0     # Detected swing (0 = straight, 1 = full swing)
    groove_type: str = "straight" # "straight", "swing", "shuffle", "free"

    # Surrounding notes
    avg_timing_error: float = 0.0  # Average timing error in context
    timing_variance: float = 0.0   # Variance in timing errors

    def to_array(self) -> np.ndarray:
        """Convert to feature array for ML model."""
        groove_encoding = {
            "straight": 0.0,
            "swing": 0.25,
            "shuffle": 0.5,
            "free": 1.0,
        }

        return np.array([
            self.beat_position,
            self.dist_to_16th,
            self.dist_to_8th,
            self.dist_to_quarter,
            self.dist_to_triplet,
            self.tempo_bpm / 200.0,  # Normalize to ~0-1 range
            self.time_signature_num / 8.0,
            1.0 if self.phrase_start else 0.0,
            1.0 if self.phrase_end else 0.0,
            1.0 if self.downbeat else 0.0,
            self.swing_amount,
            groove_encoding.get(self.groove_type, 0.0),
            self.avg_timing_error,
            self.timing_variance,
        ], dtype=np.float32)

    @classmethod
    def num_features(cls) -> int:
        """Number of features in the array."""
        return 14


@dataclass
class TimingCorrection:
    """Timing correction decision for a note."""

    original_start: float
    original_end: float

    corrected_start: float
    corrected_end: float

    grid_division: int           # Grid used (4, 8, 16, etc.)
    correction_amount: float     # Amount of correction applied (seconds)
    correction_strength: float   # 0-1, how much to apply correction
    confidence: float            # Confidence in this correction

    preserve_groove: bool        # Whether to preserve original feel
    is_intentional_offset: bool  # Detected as intentional timing


class TimingCorrector:
    """ML-based timing corrector.

    Uses context-aware decisions to determine:
    - Which grid division to use (16th, 8th, triplet, etc.)
    - How much correction to apply (strength)
    - Whether to preserve intentional timing

    Falls back to heuristic correction when models aren't available.
    """

    def __init__(
        self,
        model_dir: Optional[Path] = None,
        use_ml: bool = True,
    ):
        """Initialize the timing corrector.

        Args:
            model_dir: Directory containing trained models
            use_ml: Whether to use ML models (vs pure heuristics)
        """
        self.model_dir = model_dir or DEFAULT_MODEL_DIR
        self.use_ml = use_ml
        self._model = None
        self._model_loaded = False

        if use_ml:
            self._try_load_model()

    def _try_load_model(self) -> bool:
        """Try to load the timing model."""
        model_path = self.model_dir / "timing_corrector.lgb"

        if not model_path.exists():
            logger.debug("No timing corrector model found at %s", model_path)
            return False

        try:
            import lightgbm as lgb
            self._model = lgb.Booster(model_file=str(model_path))
            self._model_loaded = True
            logger.info("Loaded timing corrector from %s", model_path)
            return True
        except ImportError:
            logger.debug("LightGBM not available, using heuristic timing")
            return False
        except Exception as e:
            logger.warning("Failed to load timing corrector: %s", e)
            return False

    def is_ml_ready(self) -> bool:
        """Check if ML model is loaded."""
        return self._model_loaded and self._model is not None

    def detect_groove(
        self,
        notes: List[Tuple[int, float, float, int]],
        tempo_bpm: float,
    ) -> Tuple[str, float]:
        """Detect groove type and swing amount from notes.

        Args:
            notes: List of (pitch, start, end, velocity) tuples
            tempo_bpm: Detected tempo

        Returns:
            (groove_type, swing_amount)
        """
        if len(notes) < 8:
            return "straight", 0.0

        seconds_per_beat = 60.0 / tempo_bpm
        eighth_note = seconds_per_beat / 2

        # Analyze timing of notes relative to 8th note grid
        offsets = []
        for pitch, start, end, vel in notes:
            # Position within 8th note
            pos_in_eighth = (start % eighth_note) / eighth_note
            offsets.append(pos_in_eighth)

        offsets = np.array(offsets)

        # Detect swing: notes consistently late on upbeats
        upbeat_notes = [o for o in offsets if 0.4 < o < 0.8]
        if len(upbeat_notes) > len(offsets) * 0.2:
            avg_upbeat_offset = np.mean(upbeat_notes)
            if avg_upbeat_offset > 0.55:  # Consistently late
                swing_amount = min((avg_upbeat_offset - 0.5) * 4, 1.0)
                return "swing", swing_amount

        # Detect shuffle: alternating long-short pattern
        timing_variance = np.std(offsets)
        if timing_variance > 0.15:
            return "shuffle", timing_variance

        # Check if very free timing
        if timing_variance > 0.25:
            return "free", 0.0

        return "straight", 0.0

    def extract_context(
        self,
        note: Tuple[int, float, float, int],
        all_notes: List[Tuple[int, float, float, int]],
        tempo_bpm: float,
        groove_type: str = "straight",
        swing_amount: float = 0.0,
    ) -> TimingContext:
        """Extract timing context for a note.

        Args:
            note: (pitch, start, end, velocity) tuple
            all_notes: All notes for context
            tempo_bpm: Detected tempo
            groove_type: Detected groove type
            swing_amount: Detected swing amount

        Returns:
            TimingContext with extracted features
        """
        pitch, start, end, velocity = note
        seconds_per_beat = 60.0 / tempo_bpm

        # Beat position (0-1 within beat)
        beat_position = (start % seconds_per_beat) / seconds_per_beat

        # Distances to various grid positions
        grids = {
            16: seconds_per_beat / 4,     # 16th note
            8: seconds_per_beat / 2,      # 8th note
            4: seconds_per_beat,          # Quarter note
            6: seconds_per_beat / 3,      # Triplet 8th
        }

        def dist_to_grid(time: float, grid_size: float) -> float:
            pos_in_grid = time % grid_size
            return min(pos_in_grid, grid_size - pos_in_grid) / grid_size

        context = TimingContext(
            start_time=start,
            end_time=end,
            beat_position=beat_position,
            dist_to_16th=dist_to_grid(start, grids[16]),
            dist_to_8th=dist_to_grid(start, grids[8]),
            dist_to_quarter=dist_to_grid(start, grids[4]),
            dist_to_triplet=dist_to_grid(start, grids[6]),
            tempo_bpm=tempo_bpm,
            swing_amount=swing_amount,
            groove_type=groove_type,
        )

        # Check if downbeat
        context.downbeat = beat_position < 0.1 or beat_position > 0.9

        # Calculate average timing error in context
        sorted_notes = sorted(all_notes, key=lambda n: n[1])
        timing_errors = []
        for n in sorted_notes:
            err = dist_to_grid(n[1], grids[16])
            timing_errors.append(err)

        if timing_errors:
            context.avg_timing_error = np.mean(timing_errors)
            context.timing_variance = np.std(timing_errors)

        # Phrase detection (simplified)
        note_idx = next(
            (i for i, n in enumerate(sorted_notes) if n[1] == start),
            -1
        )
        if note_idx >= 0:
            # Check if first note or large gap before
            if note_idx == 0:
                context.phrase_start = True
            elif start - sorted_notes[note_idx - 1][2] > seconds_per_beat:
                context.phrase_start = True

            # Check if last note or large gap after
            if note_idx == len(sorted_notes) - 1:
                context.phrase_end = True
            elif note_idx < len(sorted_notes) - 1:
                if sorted_notes[note_idx + 1][1] - end > seconds_per_beat:
                    context.phrase_end = True

        return context

    def compute_correction(
        self,
        note: Tuple[int, float, float, int],
        all_notes: List[Tuple[int, float, float, int]],
        tempo_bpm: float,
        default_strength: float = 0.7,
    ) -> TimingCorrection:
        """Compute timing correction for a note.

        Args:
            note: (pitch, start, end, velocity) tuple
            all_notes: All notes for context
            tempo_bpm: Detected tempo
            default_strength: Default correction strength (0-1)

        Returns:
            TimingCorrection with correction details
        """
        pitch, start, end, velocity = note

        # Detect groove from all notes
        groove_type, swing_amount = self.detect_groove(all_notes, tempo_bpm)

        context = self.extract_context(
            note, all_notes, tempo_bpm, groove_type, swing_amount
        )

        if self.is_ml_ready():
            correction = self._compute_ml(context, default_strength)
        else:
            correction = self._compute_heuristic(context, default_strength)

        return correction

    def _compute_ml(
        self,
        context: TimingContext,
        default_strength: float,
    ) -> TimingCorrection:
        """Compute correction using ML model."""
        features = context.to_array().reshape(1, -1)

        # Model outputs: [grid_division, strength, preserve_groove]
        predictions = self._model.predict(features)[0]

        grid_division = int(round(predictions[0] * 16))
        grid_division = max(4, min(16, grid_division))  # Clamp to valid range

        strength = float(predictions[1])
        preserve_groove = predictions[2] > 0.5

        return self._apply_correction(
            context, grid_division, strength, preserve_groove
        )

    def _compute_heuristic(
        self,
        context: TimingContext,
        default_strength: float,
    ) -> TimingCorrection:
        """Compute correction using heuristics."""
        # Choose grid based on distances
        if context.dist_to_triplet < context.dist_to_16th * 0.8:
            # Closer to triplet grid
            grid_division = 12  # Triplet 8ths
        elif context.dist_to_8th < context.dist_to_16th * 0.5:
            # Much closer to 8th
            grid_division = 8
        else:
            # Default to 16th
            grid_division = 16

        # Adjust strength based on context
        strength = default_strength

        # Reduce strength for phrase boundaries (preserve expression)
        if context.phrase_start or context.phrase_end:
            strength *= 0.5

        # Reduce strength for swing/shuffle
        if context.groove_type in ("swing", "shuffle"):
            strength *= 0.7

        # Increase strength for downbeats
        if context.downbeat:
            strength = min(1.0, strength * 1.2)

        # Preserve groove if swing detected
        preserve_groove = context.swing_amount > 0.2

        return self._apply_correction(
            context, grid_division, strength, preserve_groove
        )

    def _apply_correction(
        self,
        context: TimingContext,
        grid_division: int,
        strength: float,
        preserve_groove: bool,
    ) -> TimingCorrection:
        """Apply the computed correction to get new timing."""
        seconds_per_beat = 60.0 / context.tempo_bpm
        grid_size = seconds_per_beat * (4.0 / grid_division)

        # Find nearest grid position
        grid_position = round(context.start_time / grid_size)
        quantized_start = grid_position * grid_size

        # Apply strength (blend between original and quantized)
        corrected_start = context.start_time + (
            (quantized_start - context.start_time) * strength
        )

        # Maintain note duration
        duration = context.end_time - context.start_time
        corrected_end = corrected_start + duration

        correction_amount = abs(corrected_start - context.start_time)

        # Detect intentional offset (consistent with groove)
        is_intentional = (
            preserve_groove or
            (context.groove_type == "swing" and context.beat_position > 0.4)
        )

        return TimingCorrection(
            original_start=context.start_time,
            original_end=context.end_time,
            corrected_start=corrected_start,
            corrected_end=corrected_end,
            grid_division=grid_division,
            correction_amount=correction_amount,
            correction_strength=strength,
            confidence=0.8 if not is_intentional else 0.6,
            preserve_groove=preserve_groove,
            is_intentional_offset=is_intentional,
        )

    def correct_timing(
        self,
        notes: List[Tuple[int, float, float, int]],
        tempo_bpm: float,
        default_strength: float = 0.7,
    ) -> List[Tuple[int, float, float, int]]:
        """Correct timing for all notes.

        Args:
            notes: List of (pitch, start, end, velocity) tuples
            tempo_bpm: Detected tempo
            default_strength: Default correction strength (0-1)

        Returns:
            List of corrected notes
        """
        corrected = []

        for note in notes:
            correction = self.compute_correction(
                note, notes, tempo_bpm, default_strength
            )
            corrected.append((
                note[0],  # pitch
                correction.corrected_start,
                correction.corrected_end,
                note[3],  # velocity
            ))

        logger.info(
            "Timing correction: adjusted %d notes with avg correction %.3fs",
            len(notes),
            np.mean([
                abs(c[1] - n[1]) for c, n in zip(corrected, notes)
            ]) if notes else 0
        )

        return corrected


# Module-level singleton
_corrector: Optional[TimingCorrector] = None


def get_corrector(
    model_dir: Optional[Path] = None,
    use_ml: bool = True,
) -> TimingCorrector:
    """Get or create the global TimingCorrector instance."""
    global _corrector

    if _corrector is None:
        _corrector = TimingCorrector(model_dir=model_dir, use_ml=use_ml)

    return _corrector


def correct_timing(
    notes: List[Tuple[int, float, float, int]],
    tempo_bpm: float,
    strength: float = 0.7,
    provenance_chain=None,  # Optional ProvenanceChain for tracking
) -> List[Tuple[int, float, float, int]]:
    """Correct note timing using the global corrector.

    Args:
        notes: List of (pitch, start, end, velocity) tuples
        tempo_bpm: Detected tempo
        strength: Quantization strength (0-1)
        provenance_chain: Optional ProvenanceChain for decision tracking

    Returns:
        Timing-corrected notes
    """
    corrected = get_corrector().correct_timing(notes, tempo_bpm, strength)

    # Record timing corrections in provenance
    if provenance_chain is not None and len(notes) == len(corrected):
        from tone_forge.provenance import DecisionAction, DecisionDomain, ReasonGraph

        for i, (orig, fixed) in enumerate(zip(notes, corrected)):
            if orig[1] != fixed[1] or orig[2] != fixed[2]:  # Timing changed
                time_delta = fixed[1] - orig[1]
                record = provenance_chain.create_record(
                    action=DecisionAction.MODIFIED,
                    stage="timing_corrector",
                    entity_type="note",
                    entity_id=f"n{i}",
                    entity_data={
                        "pitch": orig[0],
                        "original_start": orig[1],
                        "original_end": orig[2],
                        "corrected_start": fixed[1],
                        "corrected_end": fixed[2],
                    },
                    domain=DecisionDomain.MIDI_REFINEMENT,
                )
                record.reason = ReasonGraph(
                    summary=f"Timing adjusted by {time_delta*1000:.1f}ms",
                    confidence=strength,
                    model_used="timing_corrector",
                )
                record.reason.add_factor("time_delta_ms", abs(time_delta) * 1000)
                record.reason.add_factor("quantize_strength", strength)

    return corrected


def detect_groove(
    notes: List[Tuple[int, float, float, int]],
    tempo_bpm: float,
) -> Tuple[str, float]:
    """Detect groove type and swing amount."""
    return get_corrector().detect_groove(notes, tempo_bpm)
