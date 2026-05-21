"""Base classes for multi-pass MIDI extraction.

Defines common types and the ExtractionPass interface.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np

logger = logging.getLogger(__name__)


class NoteFlag(str, Enum):
    """Flags indicating how a note was processed."""

    ORIGINAL = "original"
    HARMONIC_RECOVERY = "harmonic_recovery"
    PHRASE_INFERRED = "phrase_inferred"
    DELAY_REMOVED = "delay_removed"
    REVERB_REMOVED = "reverb_removed"
    QUANTIZED = "quantized"
    MERGED = "merged"
    SPLIT = "split"
    VELOCITY_ADJUSTED = "velocity_adjusted"
    LOW_CONFIDENCE = "low_confidence"


@dataclass
class ExtractedNote:
    """A note extracted from audio with confidence and metadata."""

    pitch: int  # MIDI note number
    start: float  # Start time in seconds
    end: float  # End time in seconds
    velocity: int  # MIDI velocity (0-127)
    confidence: float  # Extraction confidence (0-1)
    source_pass: int = 1  # Which pass created this note
    flags: Set[NoteFlag] = field(default_factory=set)

    # Optional metadata
    original_start: Optional[float] = None  # Before quantization
    original_end: Optional[float] = None
    harmonic_context: Optional[Dict[str, Any]] = None

    @property
    def duration(self) -> float:
        """Duration in seconds."""
        return self.end - self.start

    @property
    def duration_ms(self) -> float:
        """Duration in milliseconds."""
        return (self.end - self.start) * 1000

    def to_tuple(self) -> Tuple[int, float, float, int]:
        """Convert to (pitch, start, end, velocity) tuple."""
        return (self.pitch, self.start, self.end, self.velocity)

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "pitch": self.pitch,
            "start": self.start,
            "end": self.end,
            "velocity": self.velocity,
            "confidence": self.confidence,
            "source_pass": self.source_pass,
            "flags": [f.value for f in self.flags],
            "duration_ms": self.duration_ms,
        }

    @classmethod
    def from_tuple(
        cls,
        t: Tuple[int, float, float, int],
        confidence: float = 1.0,
        source_pass: int = 0,
    ) -> "ExtractedNote":
        """Create from (pitch, start, end, velocity) tuple."""
        return cls(
            pitch=t[0],
            start=t[1],
            end=t[2],
            velocity=t[3],
            confidence=confidence,
            source_pass=source_pass,
        )


@dataclass
class PassStatistics:
    """Statistics from a single extraction pass."""

    pass_number: int
    pass_name: str
    notes_input: int
    notes_output: int
    notes_added: int = 0
    notes_removed: int = 0
    notes_modified: int = 0
    execution_time_ms: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def notes_delta(self) -> int:
        """Change in note count."""
        return self.notes_output - self.notes_input

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "pass_number": self.pass_number,
            "pass_name": self.pass_name,
            "notes_input": self.notes_input,
            "notes_output": self.notes_output,
            "notes_added": self.notes_added,
            "notes_removed": self.notes_removed,
            "notes_modified": self.notes_modified,
            "notes_delta": self.notes_delta,
            "execution_time_ms": self.execution_time_ms,
            "metadata": self.metadata,
        }


@dataclass
class PassResult:
    """Result from an extraction pass."""

    notes: List[ExtractedNote]
    statistics: PassStatistics
    warnings: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def note_count(self) -> int:
        """Number of notes."""
        return len(self.notes)


@dataclass
class ExtractionContext:
    """Context passed between extraction passes."""

    audio: np.ndarray
    sr: int
    stem_type: Optional[str] = None
    genre: Optional[str] = None
    tempo: Optional[float] = None
    key: Optional[Tuple[int, str]] = None  # (root, mode)
    time_signature: Tuple[int, int] = (4, 4)

    # Quality information from reconstruction module
    stem_quality: Optional[Any] = None
    contamination: Optional[Any] = None
    confidence_map: Optional[Any] = None
    role_classification: Optional[Any] = None

    # Extraction parameters
    onset_threshold: float = 0.5
    frame_threshold: float = 0.4
    min_note_ms: float = 50.0
    min_velocity: int = 20

    def to_dict(self) -> dict:
        """Convert to dictionary (without audio)."""
        return {
            "sr": self.sr,
            "stem_type": self.stem_type,
            "genre": self.genre,
            "tempo": self.tempo,
            "key": self.key,
            "time_signature": self.time_signature,
            "onset_threshold": self.onset_threshold,
            "frame_threshold": self.frame_threshold,
            "min_note_ms": self.min_note_ms,
            "min_velocity": self.min_velocity,
        }


class ExtractionPass(ABC):
    """Base class for extraction passes.

    Each pass takes notes and context, processes them, and returns
    refined notes with statistics.
    """

    def __init__(self, pass_number: int = 0):
        """Initialize the pass.

        Args:
            pass_number: The pass number in the pipeline
        """
        self.pass_number = pass_number

    @property
    @abstractmethod
    def name(self) -> str:
        """Name of this pass."""
        pass

    @abstractmethod
    def process(
        self,
        notes: List[ExtractedNote],
        context: ExtractionContext,
    ) -> PassResult:
        """Process notes through this pass.

        Args:
            notes: Input notes from previous pass
            context: Extraction context

        Returns:
            PassResult with processed notes and statistics
        """
        pass

    def _create_statistics(
        self,
        input_notes: List[ExtractedNote],
        output_notes: List[ExtractedNote],
        execution_time_ms: float = 0.0,
        **metadata,
    ) -> PassStatistics:
        """Create statistics for this pass."""
        # Count modifications
        input_ids = {(n.pitch, n.start) for n in input_notes}
        output_ids = {(n.pitch, n.start) for n in output_notes}

        added = len(output_ids - input_ids)
        removed = len(input_ids - output_ids)

        # Count notes with modification flags
        modified = sum(
            1 for n in output_notes
            if n.flags - {NoteFlag.ORIGINAL}
        )

        return PassStatistics(
            pass_number=self.pass_number,
            pass_name=self.name,
            notes_input=len(input_notes),
            notes_output=len(output_notes),
            notes_added=added,
            notes_removed=removed,
            notes_modified=modified,
            execution_time_ms=execution_time_ms,
            metadata=metadata,
        )
