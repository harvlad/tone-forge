"""ML-based dynamics modeling for MIDI refinement.

Provides intelligent velocity processing that:
- Learns appropriate velocity curves from context
- Preserves dynamic expression while reducing noise
- Adapts to different instrument types and styles
- Suggests velocity adjustments based on musical role

Unlike simple velocity normalization, this uses context-aware
decisions about velocity scaling and smoothing.
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
class DynamicsContext:
    """Context for dynamics modeling decisions."""

    # Note properties
    pitch: int
    velocity: int
    duration: float
    start_time: float

    # Position context
    beat_position: float          # 0-1, position within beat
    bar_position: float           # 0-1, position within bar
    is_downbeat: bool = False
    is_phrase_start: bool = False
    is_phrase_end: bool = False

    # Surrounding velocities
    prev_velocity: int = 64       # Previous note velocity
    next_velocity: int = 64       # Next note velocity
    avg_velocity: float = 64.0    # Average velocity in context
    velocity_std: float = 20.0    # Velocity std dev

    # Musical role
    is_bass_note: bool = False    # Low register
    is_melody_note: bool = False  # High register, sparse timing
    is_chord_note: bool = False   # Part of chord
    is_accent: bool = False       # Detected accent

    # Instrument context
    instrument_type: str = "unknown"  # "bass", "lead", "pad", "drums", etc.

    def to_array(self) -> np.ndarray:
        """Convert to feature array for ML model."""
        instrument_encoding = {
            "bass": 0.0,
            "lead": 0.2,
            "pad": 0.4,
            "drums": 0.6,
            "synth": 0.8,
            "unknown": 0.5,
        }

        return np.array([
            self.pitch / 127.0,
            self.velocity / 127.0,
            min(self.duration, 5.0) / 5.0,
            self.beat_position,
            self.bar_position,
            1.0 if self.is_downbeat else 0.0,
            1.0 if self.is_phrase_start else 0.0,
            1.0 if self.is_phrase_end else 0.0,
            self.prev_velocity / 127.0,
            self.next_velocity / 127.0,
            self.avg_velocity / 127.0,
            self.velocity_std / 127.0,
            1.0 if self.is_bass_note else 0.0,
            1.0 if self.is_melody_note else 0.0,
            1.0 if self.is_chord_note else 0.0,
            1.0 if self.is_accent else 0.0,
            instrument_encoding.get(self.instrument_type, 0.5),
        ], dtype=np.float32)

    @classmethod
    def num_features(cls) -> int:
        """Number of features in the array."""
        return 17


@dataclass
class DynamicsAdjustment:
    """Dynamics adjustment for a note."""

    original_velocity: int
    adjusted_velocity: int

    # Adjustment details
    scale_factor: float           # Multiplier applied
    compression_amount: float     # Amount of compression (0-1)
    accent_boost: float           # Accent boost applied

    # Quality indicators
    confidence: float             # Confidence in adjustment
    preserve_dynamics: bool       # Whether to preserve original dynamics
    reason: str                   # Explanation for adjustment


class DynamicsModel:
    """ML-based dynamics model.

    Uses context-aware decisions to:
    - Apply appropriate velocity curves
    - Compress or expand dynamic range
    - Detect and preserve accents
    - Smooth velocity artifacts

    Falls back to heuristic processing when models aren't available.
    """

    def __init__(
        self,
        model_dir: Optional[Path] = None,
        use_ml: bool = True,
    ):
        """Initialize the dynamics model.

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
        """Try to load the dynamics model."""
        model_path = self.model_dir / "dynamics_model.lgb"

        if not model_path.exists():
            logger.debug("No dynamics model found at %s", model_path)
            return False

        try:
            import lightgbm as lgb
            self._model = lgb.Booster(model_file=str(model_path))
            self._model_loaded = True
            logger.info("Loaded dynamics model from %s", model_path)
            return True
        except ImportError:
            logger.debug("LightGBM not available, using heuristic dynamics")
            return False
        except Exception as e:
            logger.warning("Failed to load dynamics model: %s", e)
            return False

    def is_ml_ready(self) -> bool:
        """Check if ML model is loaded."""
        return self._model_loaded and self._model is not None

    def analyze_dynamics(
        self,
        notes: List[Tuple[int, float, float, int]],
    ) -> Dict[str, Any]:
        """Analyze dynamics characteristics of a note sequence.

        Args:
            notes: List of (pitch, start, end, velocity) tuples

        Returns:
            Dictionary with dynamics analysis
        """
        if not notes:
            return {
                "avg_velocity": 64,
                "velocity_range": (64, 64),
                "dynamic_range": 0,
                "has_accents": False,
                "compression_needed": False,
            }

        velocities = [n[3] for n in notes]
        pitches = [n[0] for n in notes]

        avg_vel = np.mean(velocities)
        min_vel = min(velocities)
        max_vel = max(velocities)
        std_vel = np.std(velocities)

        # Detect accents (notes significantly louder than context)
        threshold = avg_vel + 2 * std_vel
        accent_count = sum(1 for v in velocities if v > threshold)
        has_accents = accent_count > 0

        # Determine if compression is needed
        dynamic_range = max_vel - min_vel
        compression_needed = dynamic_range > 80 or (
            std_vel > 30 and not has_accents
        )

        return {
            "avg_velocity": avg_vel,
            "velocity_range": (min_vel, max_vel),
            "dynamic_range": dynamic_range,
            "velocity_std": std_vel,
            "has_accents": has_accents,
            "accent_threshold": threshold,
            "compression_needed": compression_needed,
            "pitch_range": (min(pitches), max(pitches)),
        }

    def extract_context(
        self,
        note: Tuple[int, float, float, int],
        all_notes: List[Tuple[int, float, float, int]],
        tempo_bpm: float = 120.0,
        instrument_type: str = "unknown",
    ) -> DynamicsContext:
        """Extract dynamics context for a note.

        Args:
            note: (pitch, start, end, velocity) tuple
            all_notes: All notes for context
            tempo_bpm: Detected tempo
            instrument_type: Type of instrument

        Returns:
            DynamicsContext with extracted features
        """
        pitch, start, end, velocity = note
        duration = end - start

        seconds_per_beat = 60.0 / tempo_bpm
        seconds_per_bar = seconds_per_beat * 4  # Assuming 4/4

        context = DynamicsContext(
            pitch=pitch,
            velocity=velocity,
            duration=duration,
            start_time=start,
            beat_position=(start % seconds_per_beat) / seconds_per_beat,
            bar_position=(start % seconds_per_bar) / seconds_per_bar,
            instrument_type=instrument_type,
        )

        # Position analysis
        context.is_downbeat = context.beat_position < 0.1

        # Sort notes for context
        sorted_notes = sorted(all_notes, key=lambda n: n[1])
        note_idx = next(
            (i for i, n in enumerate(sorted_notes) if n[1] == start and n[0] == pitch),
            -1
        )

        # Surrounding velocities
        if note_idx > 0:
            context.prev_velocity = sorted_notes[note_idx - 1][3]
        if note_idx >= 0 and note_idx < len(sorted_notes) - 1:
            context.next_velocity = sorted_notes[note_idx + 1][3]

        # Global velocity stats
        velocities = [n[3] for n in all_notes]
        context.avg_velocity = np.mean(velocities)
        context.velocity_std = np.std(velocities)

        # Musical role detection
        pitches = [n[0] for n in all_notes]
        pitch_range = max(pitches) - min(pitches) if pitches else 0

        # Bass note: in lower 1/3 of range
        context.is_bass_note = pitch < min(pitches) + pitch_range / 3

        # Melody note: in upper 1/3 and sparse timing
        if pitch > max(pitches) - pitch_range / 3:
            # Check if sparse (isolated)
            nearby = sum(
                1 for n in all_notes
                if abs(n[1] - start) < 0.5 and n != note
            )
            context.is_melody_note = nearby < 2

        # Chord note: multiple notes at same time
        concurrent = sum(
            1 for n in all_notes
            if n[1] <= start < n[2] and n != note
        )
        context.is_chord_note = concurrent > 0

        # Accent detection
        if context.velocity_std > 0:
            z_score = (velocity - context.avg_velocity) / context.velocity_std
            context.is_accent = z_score > 1.5

        # Phrase detection (simplified)
        if note_idx == 0:
            context.is_phrase_start = True
        elif note_idx > 0:
            gap = start - sorted_notes[note_idx - 1][2]
            context.is_phrase_start = gap > seconds_per_beat

        if note_idx == len(sorted_notes) - 1:
            context.is_phrase_end = True
        elif note_idx >= 0 and note_idx < len(sorted_notes) - 1:
            gap = sorted_notes[note_idx + 1][1] - end
            context.is_phrase_end = gap > seconds_per_beat

        return context

    def compute_adjustment(
        self,
        note: Tuple[int, float, float, int],
        all_notes: List[Tuple[int, float, float, int]],
        tempo_bpm: float = 120.0,
        instrument_type: str = "unknown",
        target_range: Tuple[int, int] = (60, 110),
        preserve_accents: bool = True,
    ) -> DynamicsAdjustment:
        """Compute velocity adjustment for a note.

        Args:
            note: (pitch, start, end, velocity) tuple
            all_notes: All notes for context
            tempo_bpm: Detected tempo
            instrument_type: Type of instrument
            target_range: Target velocity range (min, max)
            preserve_accents: Whether to preserve detected accents

        Returns:
            DynamicsAdjustment with adjustment details
        """
        context = self.extract_context(
            note, all_notes, tempo_bpm, instrument_type
        )

        if self.is_ml_ready():
            adjustment = self._compute_ml(context, target_range, preserve_accents)
        else:
            adjustment = self._compute_heuristic(
                context, target_range, preserve_accents
            )

        return adjustment

    def _compute_ml(
        self,
        context: DynamicsContext,
        target_range: Tuple[int, int],
        preserve_accents: bool,
    ) -> DynamicsAdjustment:
        """Compute adjustment using ML model."""
        features = context.to_array().reshape(1, -1)

        # Model outputs: [scale_factor, compression_amount]
        predictions = self._model.predict(features)[0]

        scale_factor = float(predictions[0])
        compression = float(predictions[1])

        return self._apply_adjustment(
            context, scale_factor, compression, target_range, preserve_accents
        )

    def _compute_heuristic(
        self,
        context: DynamicsContext,
        target_range: Tuple[int, int],
        preserve_accents: bool,
    ) -> DynamicsAdjustment:
        """Compute adjustment using heuristics."""
        target_min, target_max = target_range
        target_avg = (target_min + target_max) / 2

        # Base scaling to target average
        if context.avg_velocity > 0:
            scale_factor = target_avg / context.avg_velocity
        else:
            scale_factor = 1.0

        # Compression based on dynamic range
        if context.velocity_std > 30:
            compression = 0.3  # Moderate compression
        elif context.velocity_std > 20:
            compression = 0.15
        else:
            compression = 0.0

        # Reduce compression for accents if preserving
        if preserve_accents and context.is_accent:
            compression = 0.0

        # Boost downbeats slightly
        if context.is_downbeat:
            scale_factor *= 1.05

        # Reduce chord notes slightly to avoid mud
        if context.is_chord_note:
            scale_factor *= 0.95

        return self._apply_adjustment(
            context, scale_factor, compression, target_range, preserve_accents
        )

    def _apply_adjustment(
        self,
        context: DynamicsContext,
        scale_factor: float,
        compression: float,
        target_range: Tuple[int, int],
        preserve_accents: bool,
    ) -> DynamicsAdjustment:
        """Apply the computed adjustment."""
        target_min, target_max = target_range
        target_mid = (target_min + target_max) / 2

        original = context.velocity

        # Apply scaling
        scaled = original * scale_factor

        # Apply compression (move toward center)
        if compression > 0:
            compressed = scaled + (target_mid - scaled) * compression
        else:
            compressed = scaled

        # Accent boost
        accent_boost = 0.0
        if preserve_accents and context.is_accent:
            accent_boost = 10
            compressed += accent_boost

        # Phrase shaping
        if context.is_phrase_start:
            compressed *= 1.05  # Slight emphasis on phrase start
        elif context.is_phrase_end:
            compressed *= 0.95  # Slight fade on phrase end

        # Clamp to valid MIDI range and target range
        adjusted = int(max(target_min, min(target_max, compressed)))
        adjusted = max(1, min(127, adjusted))

        # Determine if we should preserve dynamics
        preserve = (
            preserve_accents and context.is_accent
        ) or abs(adjusted - original) < 5

        # Generate reason
        reasons = []
        if scale_factor != 1.0:
            reasons.append(f"scaled {scale_factor:.2f}x")
        if compression > 0:
            reasons.append(f"compressed {compression:.0%}")
        if accent_boost > 0:
            reasons.append("accent preserved")
        if context.is_downbeat:
            reasons.append("downbeat boost")

        return DynamicsAdjustment(
            original_velocity=original,
            adjusted_velocity=adjusted,
            scale_factor=scale_factor,
            compression_amount=compression,
            accent_boost=accent_boost,
            confidence=0.8,
            preserve_dynamics=preserve,
            reason=", ".join(reasons) if reasons else "no adjustment",
        )

    def process_dynamics(
        self,
        notes: List[Tuple[int, float, float, int]],
        tempo_bpm: float = 120.0,
        instrument_type: str = "unknown",
        target_range: Tuple[int, int] = (60, 110),
        preserve_accents: bool = True,
    ) -> List[Tuple[int, float, float, int]]:
        """Process dynamics for all notes.

        Args:
            notes: List of (pitch, start, end, velocity) tuples
            tempo_bpm: Detected tempo
            instrument_type: Type of instrument
            target_range: Target velocity range
            preserve_accents: Whether to preserve accents

        Returns:
            List of notes with adjusted velocities
        """
        processed = []

        for note in notes:
            adjustment = self.compute_adjustment(
                note, notes, tempo_bpm, instrument_type,
                target_range, preserve_accents
            )
            processed.append((
                note[0],  # pitch
                note[1],  # start
                note[2],  # end
                adjustment.adjusted_velocity,
            ))

        # Log summary
        orig_vels = [n[3] for n in notes]
        new_vels = [n[3] for n in processed]
        logger.info(
            "Dynamics processing: vel range [%d-%d] -> [%d-%d], "
            "avg %.1f -> %.1f",
            min(orig_vels) if orig_vels else 0,
            max(orig_vels) if orig_vels else 0,
            min(new_vels) if new_vels else 0,
            max(new_vels) if new_vels else 0,
            np.mean(orig_vels) if orig_vels else 0,
            np.mean(new_vels) if new_vels else 0,
        )

        return processed


# Module-level singleton
_dynamics_model: Optional[DynamicsModel] = None


def get_dynamics_model(
    model_dir: Optional[Path] = None,
    use_ml: bool = True,
) -> DynamicsModel:
    """Get or create the global DynamicsModel instance."""
    global _dynamics_model

    if _dynamics_model is None:
        _dynamics_model = DynamicsModel(model_dir=model_dir, use_ml=use_ml)

    return _dynamics_model


def process_dynamics(
    notes: List[Tuple[int, float, float, int]],
    tempo_bpm: float = 120.0,
    instrument_type: str = "unknown",
    target_range: Tuple[int, int] = (60, 110),
    provenance_chain=None,  # Optional ProvenanceChain for tracking
) -> List[Tuple[int, float, float, int]]:
    """Process note dynamics using the global model.

    Args:
        notes: List of (pitch, start, end, velocity) tuples
        tempo_bpm: Detected tempo
        instrument_type: Type of instrument for context
        target_range: Target velocity range (min, max)
        provenance_chain: Optional ProvenanceChain for decision tracking

    Returns:
        Notes with processed dynamics
    """
    processed = get_dynamics_model().process_dynamics(
        notes, tempo_bpm, instrument_type, target_range
    )

    # Record dynamics adjustments in provenance
    if provenance_chain is not None and len(notes) == len(processed):
        from tone_forge.provenance import DecisionAction, DecisionDomain, ReasonGraph

        for i, (orig, adjusted) in enumerate(zip(notes, processed)):
            if orig[3] != adjusted[3]:  # Velocity changed
                vel_delta = adjusted[3] - orig[3]
                record = provenance_chain.create_record(
                    action=DecisionAction.MODIFIED,
                    stage="dynamics_processor",
                    entity_type="note",
                    entity_id=f"n{i}",
                    entity_data={
                        "pitch": orig[0],
                        "original_velocity": orig[3],
                        "adjusted_velocity": adjusted[3],
                    },
                    domain=DecisionDomain.MIDI_REFINEMENT,
                )
                record.reason = ReasonGraph(
                    summary=f"Velocity adjusted by {vel_delta:+d}",
                    model_used="dynamics_model",
                )
                record.reason.add_factor("velocity_delta", vel_delta)
                record.reason.add_factor("target_min", target_range[0])
                record.reason.add_factor("target_max", target_range[1])
                record.reason.add_factor("instrument_type", instrument_type)

    return processed


def analyze_dynamics(
    notes: List[Tuple[int, float, float, int]],
) -> Dict[str, Any]:
    """Analyze dynamics of a note sequence."""
    return get_dynamics_model().analyze_dynamics(notes)
