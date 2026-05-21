"""ML-based MIDI refinement modules.

Provides intelligent MIDI processing that improves upon
rule-based approaches:

- Note classification: Identify real vs ghost/artifact notes
- Timing correction: Context-aware quantization with groove preservation
- Dynamics modeling: Intelligent velocity processing

All modules fall back to heuristics when ML models aren't available.
"""
from __future__ import annotations

from .note_classifier import (
    NoteClassifier,
    NoteContext,
    ClassifiedNote,
    get_classifier,
    classify_notes,
    filter_ghost_notes,
)
from .timing_corrector import (
    TimingCorrector,
    TimingContext,
    TimingCorrection,
    get_corrector,
    correct_timing,
    detect_groove,
)
from .dynamics_model import (
    DynamicsModel,
    DynamicsContext,
    DynamicsAdjustment,
    get_dynamics_model,
    process_dynamics,
    analyze_dynamics,
)

__all__ = [
    # Note classification
    "NoteClassifier",
    "NoteContext",
    "ClassifiedNote",
    "get_classifier",
    "classify_notes",
    "filter_ghost_notes",
    # Timing correction
    "TimingCorrector",
    "TimingContext",
    "TimingCorrection",
    "get_corrector",
    "correct_timing",
    "detect_groove",
    # Dynamics
    "DynamicsModel",
    "DynamicsContext",
    "DynamicsAdjustment",
    "get_dynamics_model",
    "process_dynamics",
    "analyze_dynamics",
]


def refine_midi_notes(
    notes: list,
    audio=None,
    sr: int = 22050,
    tempo_bpm: float = 120.0,
    detected_key=None,
    instrument_type: str = "unknown",
    filter_ghosts: bool = True,
    correct_time: bool = True,
    process_velocities: bool = True,
    timing_strength: float = 0.7,
    velocity_range: tuple = (60, 110),
) -> list:
    """Full MIDI refinement pipeline.

    Convenience function that applies all refinement steps.

    Args:
        notes: List of (pitch, start, end, velocity) tuples
        audio: Audio array for context (optional)
        sr: Sample rate
        tempo_bpm: Detected tempo
        detected_key: (root, scale) tuple from key detection
        instrument_type: Type of instrument
        filter_ghosts: Whether to filter ghost notes
        correct_time: Whether to apply timing correction
        process_velocities: Whether to process dynamics
        timing_strength: Strength of timing correction (0-1)
        velocity_range: Target velocity range

    Returns:
        Refined list of notes
    """
    if not notes:
        return notes

    refined = list(notes)

    # Step 1: Filter ghost notes
    if filter_ghosts:
        refined = filter_ghost_notes(
            refined,
            audio=audio,
            sr=sr,
            tempo_bpm=tempo_bpm,
            detected_key=detected_key,
        )

    # Step 2: Correct timing
    if correct_time and refined:
        refined = correct_timing(
            refined,
            tempo_bpm=tempo_bpm,
            strength=timing_strength,
        )

    # Step 3: Process dynamics
    if process_velocities and refined:
        refined = process_dynamics(
            refined,
            tempo_bpm=tempo_bpm,
            instrument_type=instrument_type,
            target_range=velocity_range,
        )

    return refined
