"""Analysis mode configuration for ToneForge.

Provides three analysis tiers with different quality/speed tradeoffs:

- QUICK: Fast preview (~10s for 3-min track)
  Skip stem separation, minimal post-processing

- STUDIO: Balanced quality (~45s for 3-min track)
  Full stem separation, standard cleanup passes

- DEEP: Maximum quality (~90s+ for 3-min track)
  Full processing with all validation passes
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class AnalysisMode(Enum):
    """Analysis mode selection."""
    QUICK = "quick"
    STUDIO = "studio"
    DEEP = "deep"


@dataclass
class MidiExtractionConfig:
    """Configuration for MIDI extraction."""
    # basic-pitch parameters
    onset_threshold: float = 0.5
    frame_threshold: float = 0.3
    minimum_note_length_ms: float = 50.0

    # Post-processing
    enable_multi_pass: bool = True
    enable_octave_correction: bool = True
    enable_harmonic_suppression: bool = True
    enable_timing_correction: bool = True

    # Spectral validation
    enable_spectral_validation: bool = True
    spectral_validation_strictness: float = 0.7

    # Quality analysis
    enable_quality_metrics: bool = True
    enable_provenance_tracking: bool = True


@dataclass
class StemSeparationConfig:
    """Configuration for stem separation."""
    enabled: bool = True
    model: str = "htdemucs_ft"
    stems: List[str] = field(default_factory=lambda: ["bass", "vocals", "other", "drums"])

    # Performance
    shifts: int = 1  # Number of random shifts for better quality (1-5)
    overlap: float = 0.25


@dataclass
class AnalysisConfig:
    """Complete analysis configuration."""
    mode: AnalysisMode
    stem_separation: StemSeparationConfig
    midi_extraction: MidiExtractionConfig

    # Global settings
    cache_spectral_features: bool = True
    cache_genre_detection: bool = True
    enable_tone_analysis: bool = True
    enable_quality_analysis: bool = True

    # Output
    generate_visualization: bool = True
    export_formats: List[str] = field(default_factory=lambda: ["midi"])

    def __str__(self) -> str:
        return f"AnalysisConfig(mode={self.mode.value})"


def get_quick_config() -> AnalysisConfig:
    """Get configuration for QUICK mode.

    Optimized for speed (~10s for 3-min track):
    - Skip stem separation
    - Lower thresholds for faster detection
    - Skip expensive post-processing
    """
    return AnalysisConfig(
        mode=AnalysisMode.QUICK,
        stem_separation=StemSeparationConfig(
            enabled=False,  # Skip stem separation
        ),
        midi_extraction=MidiExtractionConfig(
            onset_threshold=0.6,  # Higher = fewer notes, faster
            frame_threshold=0.4,
            minimum_note_length_ms=80.0,  # Skip very short notes
            enable_multi_pass=False,  # Skip multi-pass pipeline
            enable_octave_correction=False,
            enable_harmonic_suppression=False,
            enable_timing_correction=False,
            enable_spectral_validation=False,
            enable_quality_metrics=False,
            enable_provenance_tracking=False,
        ),
        cache_spectral_features=True,
        cache_genre_detection=True,
        enable_tone_analysis=False,  # Skip
        enable_quality_analysis=False,  # Skip
        generate_visualization=False,
        export_formats=["midi"],
    )


def get_studio_config() -> AnalysisConfig:
    """Get configuration for STUDIO mode.

    Balanced quality/speed (~45s for 3-min track):
    - Full stem separation
    - Standard post-processing
    - Basic quality metrics
    """
    return AnalysisConfig(
        mode=AnalysisMode.STUDIO,
        stem_separation=StemSeparationConfig(
            enabled=True,
            model="htdemucs_ft",
            shifts=1,  # Single shift for speed
        ),
        midi_extraction=MidiExtractionConfig(
            onset_threshold=0.5,
            frame_threshold=0.3,
            minimum_note_length_ms=50.0,
            enable_multi_pass=True,
            enable_octave_correction=True,
            enable_harmonic_suppression=True,
            enable_timing_correction=True,
            enable_spectral_validation=True,
            spectral_validation_strictness=0.7,
            enable_quality_metrics=True,
            enable_provenance_tracking=True,
        ),
        cache_spectral_features=True,
        cache_genre_detection=True,
        enable_tone_analysis=True,
        enable_quality_analysis=True,
        generate_visualization=True,
        export_formats=["midi", "json"],
    )


def get_deep_config() -> AnalysisConfig:
    """Get configuration for DEEP mode.

    Maximum quality (~90s+ for 3-min track):
    - Full stem separation with multiple shifts
    - All post-processing passes
    - Strict spectral validation
    - Full quality analysis
    """
    return AnalysisConfig(
        mode=AnalysisMode.DEEP,
        stem_separation=StemSeparationConfig(
            enabled=True,
            model="htdemucs_ft",
            shifts=3,  # Multiple shifts for better quality
            overlap=0.5,  # Higher overlap
        ),
        midi_extraction=MidiExtractionConfig(
            onset_threshold=0.4,  # Lower = more notes detected
            frame_threshold=0.25,
            minimum_note_length_ms=30.0,  # Catch short notes
            enable_multi_pass=True,
            enable_octave_correction=True,
            enable_harmonic_suppression=True,
            enable_timing_correction=True,
            enable_spectral_validation=True,
            spectral_validation_strictness=0.9,  # Strict
            enable_quality_metrics=True,
            enable_provenance_tracking=True,
        ),
        cache_spectral_features=True,
        cache_genre_detection=True,
        enable_tone_analysis=True,
        enable_quality_analysis=True,
        generate_visualization=True,
        export_formats=["midi", "json", "musicxml"],
    )


def get_config(mode: str | AnalysisMode) -> AnalysisConfig:
    """Get configuration for a given mode.

    Args:
        mode: "quick", "studio", "deep" or AnalysisMode enum

    Returns:
        AnalysisConfig for the requested mode
    """
    if isinstance(mode, str):
        mode = AnalysisMode(mode.lower())

    configs = {
        AnalysisMode.QUICK: get_quick_config,
        AnalysisMode.STUDIO: get_studio_config,
        AnalysisMode.DEEP: get_deep_config,
    }

    return configs[mode]()


# Convenience exports
QUICK = get_quick_config()
STUDIO = get_studio_config()
DEEP = get_deep_config()


def estimate_time(mode: AnalysisMode, audio_duration_sec: float) -> float:
    """Estimate processing time for a given mode and audio duration.

    Args:
        mode: Analysis mode
        audio_duration_sec: Duration of audio in seconds

    Returns:
        Estimated processing time in seconds
    """
    # Based on profiling data (realtime factors)
    realtime_factors = {
        AnalysisMode.QUICK: 0.05,   # ~10s for 3-min track
        AnalysisMode.STUDIO: 0.22,  # ~45s for 3-min track
        AnalysisMode.DEEP: 0.45,    # ~90s for 3-min track
    }

    factor = realtime_factors.get(mode, 0.22)
    return audio_duration_sec * factor


def describe_mode(mode: AnalysisMode) -> Dict[str, Any]:
    """Get a human-readable description of a mode.

    Args:
        mode: Analysis mode

    Returns:
        Dict with mode details
    """
    descriptions = {
        AnalysisMode.QUICK: {
            "name": "Quick",
            "description": "Fast preview - analyzes original audio without stem separation",
            "use_case": "Quick MIDI preview, live performance",
            "features": [
                "Basic MIDI extraction",
                "No stem separation",
                "Minimal post-processing",
            ],
            "estimated_time": "~10s for 3-min track",
        },
        AnalysisMode.STUDIO: {
            "name": "Studio",
            "description": "Balanced quality - full stem separation with standard cleanup",
            "use_case": "DAW production, general use",
            "features": [
                "Full stem separation (HTDemucs)",
                "Multi-pass MIDI cleanup",
                "Octave/harmonic correction",
                "Spectral validation",
                "Quality metrics",
            ],
            "estimated_time": "~45s for 3-min track",
        },
        AnalysisMode.DEEP: {
            "name": "Deep",
            "description": "Maximum quality - all features enabled with strict validation",
            "use_case": "Professional transcription, archival",
            "features": [
                "Enhanced stem separation (3 shifts)",
                "Full multi-pass pipeline",
                "Strict spectral validation",
                "Complete provenance tracking",
                "MusicXML export",
            ],
            "estimated_time": "~90s for 3-min track",
        },
    }

    return descriptions.get(mode, {})
