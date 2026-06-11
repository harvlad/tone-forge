"""Granular MIDI extraction profiles.

Profiles define extraction parameters and cleanup pass configuration
for different musical content types (staccato leads, sustained pads, etc).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class ExtractionProfile:
    """Granular MIDI extraction profile.

    Each profile is optimized for a specific type of musical content,
    controlling both basic-pitch parameters and post-processing behavior.
    """

    name: str  # Unique profile identifier (e.g., "lead_staccato")
    stem_type: str  # Parent stem type (bass, lead, synth, pad, drums, vocals, other)
    description: str = ""  # Human-readable description

    # Detection thresholds (passed to basic-pitch)
    onset_threshold: float = 0.5  # Note onset sensitivity (0-1, lower = more sensitive)
    frame_threshold: float = 0.4  # Frame activation threshold (0-1)
    min_note_ms: float = 50.0  # Minimum note duration in milliseconds
    min_velocity: int = 25  # Minimum MIDI velocity to retain

    # Post-processing parameters
    key_filter_strictness: float = 0.5  # How strictly to filter out-of-key notes (0-1)
    isolated_min_neighbors: int = 1  # Minimum neighbors to keep isolated notes
    isolated_time_window: float = 2.0  # Time window for neighbor detection (seconds)
    quantize_strength: float = 0.7  # Grid quantization strength (0 = none, 1 = full)
    merge_max_gap: float = 0.01  # Max gap to merge same-pitch notes (seconds)

    # Feature bounds for auto-classification
    max_onset_density: float = float('inf')  # Max onsets/sec for this profile
    min_onset_density: float = 0.0  # Min onsets/sec for this profile
    min_sustain_ratio: float = 0.0  # Minimum sustain-to-total ratio
    max_sustain_ratio: float = 1.0  # Maximum sustain-to-total ratio
    polyphony_range: Tuple[float, float] = (0.0, float('inf'))  # (min, max) avg polyphony

    # Cleanup pass toggles
    enable_harmonic_suppression: bool = True  # Remove harmonic overtones
    enable_delay_cleanup: bool = True  # Remove delay/echo artifacts
    enable_octave_correction: bool = False  # Correct sub-harmonic detection
    enable_subharmonic_suppression: bool = False  # Remove sub-harmonic artifacts
    enable_beat_grid_filter: bool = True  # Enforce beat grid consistency
    enable_key_conformity: bool = True  # Validate against detected key

    # Pitch offset (semitones) - applied after all processing
    # Bass synths often sound an octave lower than written, so +12 shifts to match notation
    pitch_offset: int = 0

    # Octave doubling - adds upper octave notes for bass when model misses them
    enable_octave_doubling: bool = False

    def to_legacy_dict(self) -> Dict:
        """Convert to legacy SYNTHWAVE_PROFILES format for backward compatibility."""
        return {
            'onset_threshold': self.onset_threshold,
            'frame_threshold': self.frame_threshold,
            'min_note_ms': self.min_note_ms,
            'min_velocity': self.min_velocity,
            'key_filter_strictness': self.key_filter_strictness,
            'isolated_min_neighbors': self.isolated_min_neighbors,
            'isolated_time_window': self.isolated_time_window,
            'quantize_strength': self.quantize_strength,
            'merge_max_gap': self.merge_max_gap,
            'filter_harmonics': self.enable_harmonic_suppression,
            'filter_delay_repeats': self.enable_delay_cleanup,
            'octave_shift_if_low': self.enable_octave_correction,
        }

    def to_dict(self) -> Dict:
        """Convert to dictionary for serialization."""
        return {
            'name': self.name,
            'stem_type': self.stem_type,
            'description': self.description,
            'onset_threshold': self.onset_threshold,
            'frame_threshold': self.frame_threshold,
            'min_note_ms': self.min_note_ms,
            'min_velocity': self.min_velocity,
            'key_filter_strictness': self.key_filter_strictness,
            'isolated_min_neighbors': self.isolated_min_neighbors,
            'isolated_time_window': self.isolated_time_window,
            'quantize_strength': self.quantize_strength,
            'merge_max_gap': self.merge_max_gap,
            'max_onset_density': self.max_onset_density,
            'min_onset_density': self.min_onset_density,
            'min_sustain_ratio': self.min_sustain_ratio,
            'max_sustain_ratio': self.max_sustain_ratio,
            'polyphony_range': self.polyphony_range,
            'enable_harmonic_suppression': self.enable_harmonic_suppression,
            'enable_delay_cleanup': self.enable_delay_cleanup,
            'enable_octave_correction': self.enable_octave_correction,
            'enable_subharmonic_suppression': self.enable_subharmonic_suppression,
            'enable_beat_grid_filter': self.enable_beat_grid_filter,
            'enable_key_conformity': self.enable_key_conformity,
            'pitch_offset': self.pitch_offset,
        }


# =============================================================================
# PREDEFINED PROFILES
# =============================================================================

# Bass profiles
MONO_BASS = ExtractionProfile(
    name="mono_bass",
    stem_type="bass",
    description="Single monophonic bass line with strong fundamentals",
    onset_threshold=0.5,       # Balanced for good recall
    frame_threshold=0.4,       # Balanced for good recall
    min_note_ms=80,            # Allow medium-short notes
    min_velocity=25,           # Lower to capture dynamics
    key_filter_strictness=0.4,
    isolated_min_neighbors=0,
    isolated_time_window=3.0,
    quantize_strength=0.8,
    merge_max_gap=0.03,
    polyphony_range=(0.0, 1.5),
    enable_octave_correction=True,
    enable_subharmonic_suppression=True,  # Remove sub-harmonic artifacts
    enable_harmonic_suppression=False,    # Disabled - bass octaves are intentional, not artifacts
    enable_delay_cleanup=True,
    pitch_offset=0,
    enable_octave_doubling=False,  # Disabled to reduce over-extraction
)

POLY_BASS = ExtractionProfile(
    name="poly_bass",
    stem_type="bass",
    description="Layered or stacked bass with multiple voices",
    onset_threshold=0.45,      # Balanced for good recall
    frame_threshold=0.35,      # Balanced for good recall
    min_note_ms=70,            # Allow shorter notes
    min_velocity=20,           # Lower to capture dynamics
    key_filter_strictness=0.4,
    isolated_min_neighbors=0,
    isolated_time_window=3.0,
    quantize_strength=0.8,
    merge_max_gap=0.05,
    polyphony_range=(1.5, 6.0),
    enable_octave_correction=True,
    enable_subharmonic_suppression=True,  # Remove sub-harmonic artifacts
    enable_harmonic_suppression=False,    # Disabled - bass octaves are intentional
    enable_delay_cleanup=True,
    pitch_offset=0,
    enable_octave_doubling=False,  # Disabled to reduce over-extraction
)

# Lead profiles
LEAD_STACCATO = ExtractionProfile(
    name="lead_staccato",
    stem_type="lead",
    description="Fast repeated/staccato lead notes - preserves articulations",
    onset_threshold=0.5,       # Balanced threshold for good recall
    frame_threshold=0.4,       # Balanced threshold for good recall
    min_note_ms=50,            # Allow shorter notes for staccato
    min_velocity=25,           # Lower to capture softer notes
    key_filter_strictness=0.7,
    isolated_min_neighbors=1,  # Require at least 1 neighbor
    isolated_time_window=2.0,
    quantize_strength=0.5,
    merge_max_gap=0.0,  # NO merging - preserves staccato
    min_onset_density=3.0,  # High note density
    enable_delay_cleanup=False,  # Repeated notes != delay
    enable_harmonic_suppression=True,  # Enable to filter harmonic artifacts
)

LEAD_LEGATO = ExtractionProfile(
    name="lead_legato",
    stem_type="lead",
    description="Smooth connected lead phrases",
    onset_threshold=0.45,      # Balanced for good recall
    frame_threshold=0.35,      # Balanced for good recall
    min_note_ms=60,            # Allow medium-short notes
    min_velocity=20,           # Lower to capture dynamics
    key_filter_strictness=0.7,
    isolated_min_neighbors=1,  # Require at least 1 neighbor
    isolated_time_window=2.0,
    quantize_strength=0.4,  # More expressive
    merge_max_gap=0.05,  # Light merging for legato
    max_onset_density=3.0,  # Lower note density
    min_sustain_ratio=0.3,
    enable_delay_cleanup=True,
    enable_harmonic_suppression=True,  # Remove harmonic artifacts
)

# Arpeggio profile
ARP_FAST = ExtractionProfile(
    name="arp_fast",
    stem_type="synth",
    description="Fast arpeggios and sequenced patterns",
    onset_threshold=0.55,
    frame_threshold=0.35,
    min_note_ms=30,  # Very short notes allowed
    min_velocity=20,
    key_filter_strictness=0.4,
    isolated_min_neighbors=0,
    isolated_time_window=1.5,
    quantize_strength=0.85,  # Tight quantization for arps
    merge_max_gap=0.0,  # No merging
    min_onset_density=4.0,  # High note density
    enable_delay_cleanup=False,
    enable_harmonic_suppression=False,
)

# Pad profiles
PAD_SUSTAINED = ExtractionProfile(
    name="pad_sustained",
    stem_type="pad",
    description="Long sustained pad notes with harmonic content",
    onset_threshold=0.45,
    frame_threshold=0.4,
    min_note_ms=300,  # Long minimum duration
    min_velocity=25,
    key_filter_strictness=0.4,
    isolated_min_neighbors=0,
    isolated_time_window=5.0,
    quantize_strength=0.3,  # Loose - pads are free
    merge_max_gap=0.1,  # Merge sustained layers
    max_onset_density=2.0,  # Low note density
    min_sustain_ratio=0.5,  # High sustain
    enable_harmonic_suppression=True,  # Critical for pads
    enable_delay_cleanup=True,
)

CHORD_STACK = ExtractionProfile(
    name="chord_stack",
    stem_type="synth",
    description="Polyphonic chord layers",
    onset_threshold=0.4,
    frame_threshold=0.35,
    min_note_ms=100,
    min_velocity=20,
    key_filter_strictness=0.3,  # Chords use extensions
    isolated_min_neighbors=1,
    isolated_time_window=3.0,
    quantize_strength=0.6,
    merge_max_gap=0.1,
    polyphony_range=(2.0, 8.0),  # Multiple simultaneous notes
    enable_harmonic_suppression=True,
)

DRONE = ExtractionProfile(
    name="drone",
    stem_type="pad",
    description="Long sustained drones with minimal movement",
    onset_threshold=0.3,
    frame_threshold=0.2,
    min_note_ms=500,  # Very long minimum
    min_velocity=20,
    key_filter_strictness=0.2,  # Drones can be chromatic
    isolated_min_neighbors=0,
    isolated_time_window=10.0,
    quantize_strength=0.2,  # Very loose
    merge_max_gap=0.15,  # Aggressive merging
    max_onset_density=0.5,  # Very low note density
    min_sustain_ratio=0.7,  # Very high sustain
    enable_harmonic_suppression=True,
)

PLUCK_TRANSIENT = ExtractionProfile(
    name="pluck_transient",
    stem_type="synth",
    description="Sharp transient plucks and percussive synths",
    onset_threshold=0.7,  # High - only clear attacks
    frame_threshold=0.5,
    min_note_ms=20,  # Very short allowed
    min_velocity=30,
    key_filter_strictness=0.5,
    isolated_min_neighbors=0,
    isolated_time_window=1.0,
    quantize_strength=0.7,
    merge_max_gap=0.0,  # No merging
    max_sustain_ratio=0.3,  # Low sustain
    enable_delay_cleanup=True,
    enable_harmonic_suppression=False,
)

# Drum profiles
DRUMS_ONSET = ExtractionProfile(
    name="drums",
    stem_type="drums",
    description="Percussive drum content - onset-based detection",
    onset_threshold=0.6,  # Moderate - catch drum hits
    frame_threshold=0.5,  # High - drums are transient
    min_note_ms=50,       # Allow short hits but filter noise
    min_velocity=30,      # Filter ghost notes
    key_filter_strictness=0.0,  # No key filtering for drums
    isolated_min_neighbors=0,
    isolated_time_window=1.0,
    quantize_strength=0.9,  # Tight quantization for drums
    merge_max_gap=0.0,      # No merging - preserve hits
    max_sustain_ratio=0.3,  # Low sustain - drums are transient
    enable_harmonic_suppression=False,  # Not applicable to drums
    enable_delay_cleanup=False,  # Drum patterns may repeat
    enable_octave_correction=False,  # Not applicable
    enable_beat_grid_filter=True,  # Drums should align to grid
    enable_key_conformity=False,  # No key for drums
)


# =============================================================================
# USE-CASE PROFILES (purpose-driven rather than content-driven)
# =============================================================================

AGGRESSIVE_RECALL = ExtractionProfile(
    name="aggressive_recall",
    stem_type="other",
    description="Maximum note detection - prioritizes recall over precision",
    onset_threshold=0.3,  # Low - catch everything
    frame_threshold=0.2,  # Very sensitive
    min_note_ms=20,  # Allow very short notes
    min_velocity=10,  # Keep quiet notes
    key_filter_strictness=0.2,  # Very lenient on key
    isolated_min_neighbors=0,  # Keep isolated notes
    isolated_time_window=5.0,
    quantize_strength=0.3,  # Light quantization
    merge_max_gap=0.0,  # No merging - preserve everything
    enable_harmonic_suppression=False,  # Keep potential harmonics
    enable_delay_cleanup=False,  # Keep potential repeats
    enable_octave_correction=False,  # Don't shift octaves
    enable_beat_grid_filter=False,  # No grid filtering
    enable_key_conformity=False,  # Don't filter by key
)

BALANCED = ExtractionProfile(
    name="balanced",
    stem_type="other",
    description="Balanced extraction for general use - good precision/recall tradeoff",
    onset_threshold=0.5,  # Moderate sensitivity
    frame_threshold=0.4,
    min_note_ms=50,  # Standard minimum
    min_velocity=25,
    key_filter_strictness=0.5,  # Moderate key filtering
    isolated_min_neighbors=1,
    isolated_time_window=2.0,
    quantize_strength=0.5,  # Moderate quantization
    merge_max_gap=0.02,  # Light merging
    enable_harmonic_suppression=True,
    enable_delay_cleanup=True,
    enable_octave_correction=False,
    enable_beat_grid_filter=True,
    enable_key_conformity=True,
)

PRECISION_FIRST = ExtractionProfile(
    name="precision_first",
    stem_type="other",
    description="High precision extraction - fewer but more accurate notes",
    onset_threshold=0.7,  # High - only confident detections
    frame_threshold=0.6,  # High frame threshold
    min_note_ms=80,  # Filter very short notes
    min_velocity=40,  # Only clear notes
    key_filter_strictness=0.8,  # Strict key filtering
    isolated_min_neighbors=2,  # Require context for notes
    isolated_time_window=1.5,
    quantize_strength=0.7,  # Strong quantization
    merge_max_gap=0.03,
    enable_harmonic_suppression=True,  # Remove harmonics
    enable_delay_cleanup=True,  # Remove echoes
    enable_octave_correction=True,  # Correct octave errors
    enable_subharmonic_suppression=True,  # Remove sub-harmonics
    enable_beat_grid_filter=True,  # Enforce grid
    enable_key_conformity=True,  # Strict key filtering
)

LIVE_PERFORMANCE = ExtractionProfile(
    name="live_performance",
    stem_type="other",
    description="Optimized for low latency - skips expensive processing",
    onset_threshold=0.5,
    frame_threshold=0.4,
    min_note_ms=40,
    min_velocity=30,
    key_filter_strictness=0.0,  # Skip key analysis
    isolated_min_neighbors=0,  # Skip isolation analysis
    isolated_time_window=0.0,  # No neighbor lookup
    quantize_strength=0.0,  # No quantization
    merge_max_gap=0.0,  # No merging
    enable_harmonic_suppression=False,  # Skip expensive pass
    enable_delay_cleanup=False,  # Skip delay detection
    enable_octave_correction=False,  # Skip octave analysis
    enable_beat_grid_filter=False,  # Skip grid analysis
    enable_key_conformity=False,  # Skip key analysis
)

CLEAN_MIDI_EXPORT = ExtractionProfile(
    name="clean_midi_export",
    stem_type="other",
    description="Optimized for clean DAW import - strong quantization and cleanup",
    onset_threshold=0.55,
    frame_threshold=0.45,
    min_note_ms=60,  # Reasonable minimum
    min_velocity=30,
    key_filter_strictness=0.6,
    isolated_min_neighbors=1,
    isolated_time_window=2.0,
    quantize_strength=0.9,  # Strong quantization for DAW
    merge_max_gap=0.05,  # Merge close notes
    enable_harmonic_suppression=True,
    enable_delay_cleanup=True,
    enable_octave_correction=True,
    enable_beat_grid_filter=True,  # Enforce grid alignment
    enable_key_conformity=True,
)


# =============================================================================
# PROFILE REGISTRY
# =============================================================================

class ProfileRegistry:
    """Registry of extraction profiles with lookup and classification support."""

    def __init__(self):
        self._profiles: Dict[str, ExtractionProfile] = {}
        self._stem_defaults: Dict[str, str] = {}
        self._register_builtin_profiles()

    def _register_builtin_profiles(self):
        """Register all built-in profiles."""
        # Register content-type profiles
        for profile in [
            MONO_BASS, POLY_BASS,
            LEAD_STACCATO, LEAD_LEGATO,
            ARP_FAST,
            PAD_SUSTAINED, CHORD_STACK, DRONE,
            PLUCK_TRANSIENT,
            DRUMS_ONSET,
        ]:
            self.register(profile)

        # Register use-case profiles
        for profile in [
            AGGRESSIVE_RECALL,
            BALANCED,
            PRECISION_FIRST,
            LIVE_PERFORMANCE,
            CLEAN_MIDI_EXPORT,
        ]:
            self.register(profile)

        # Set stem type defaults
        self._stem_defaults = {
            'bass': 'mono_bass',
            'lead': 'lead_staccato',  # Default to staccato to preserve notes
            'synth': 'arp_fast',
            'pad': 'pad_sustained',
            'pads': 'pad_sustained',  # Alias for 'pad'
            'drums': 'drums',  # Use drums profile
            'vocals': 'lead_legato',
            'guitar': 'lead_legato',  # Guitar uses lead profile
            'other': 'lead_legato',
        }

    def register(self, profile: ExtractionProfile):
        """Register a profile."""
        self._profiles[profile.name] = profile
        logger.debug(f"Registered profile: {profile.name}")

    def get(self, name: str) -> Optional[ExtractionProfile]:
        """Get a profile by name."""
        return self._profiles.get(name)

    def get_default_for_stem(self, stem_type: str) -> Optional[ExtractionProfile]:
        """Get the default profile for a stem type."""
        default_name = self._stem_defaults.get(stem_type)
        if default_name:
            return self._profiles.get(default_name)
        return None

    def get_profiles_for_stem(self, stem_type: str) -> List[ExtractionProfile]:
        """Get all profiles compatible with a stem type."""
        return [
            p for p in self._profiles.values()
            if p.stem_type == stem_type
        ]

    def list_profiles(self) -> List[str]:
        """List all registered profile names."""
        return list(self._profiles.keys())

    def list_profiles_by_stem(self) -> Dict[str, List[str]]:
        """List profiles grouped by stem type."""
        result: Dict[str, List[str]] = {}
        for profile in self._profiles.values():
            if profile.stem_type not in result:
                result[profile.stem_type] = []
            result[profile.stem_type].append(profile.name)
        return result


# Global registry instance
_registry: Optional[ProfileRegistry] = None


def get_profile_registry() -> ProfileRegistry:
    """Get the global profile registry (lazy initialization)."""
    global _registry
    if _registry is None:
        _registry = ProfileRegistry()
    return _registry


def get_profile(name: str) -> Optional[ExtractionProfile]:
    """Convenience function to get a profile by name."""
    return get_profile_registry().get(name)


def get_default_profile_for_stem(stem_type: str) -> Optional[ExtractionProfile]:
    """Convenience function to get the default profile for a stem type."""
    return get_profile_registry().get_default_for_stem(stem_type)
