"""Lead stem extraction pipeline.

Optimized for:
- Fast transients and staccato notes
- Melody preservation
- Delay/echo artifact handling
- Vibrato and pitch modulation
"""
from __future__ import annotations

from typing import List, Optional, Tuple

import numpy as np

from tone_forge.midi.passes.base import ExtractedNote
from tone_forge.midi.filters.octave_false_positive import OctaveFalsePositiveFilter
from tone_forge.midi.filters.harmonic_duplicate import HarmonicDuplicateFilter
from tone_forge.midi.filters.transient_validator import TransientNoteValidator
from tone_forge.midi.filters.repeated_pattern import RepeatedPatternValidator
from .base import StemPipeline, PipelineConfig


class LeadPipeline(StemPipeline):
    """Pipeline optimized for lead/melody extraction.

    Key behaviors:
    - Preserves staccato and repeated notes
    - Does NOT aggressively remove "delay" patterns (they might be intentional)
    - Moderate quantization to preserve expression
    - Strong transient validation
    - Melodic continuity protection
    """

    @property
    def name(self) -> str:
        return "lead"

    def _default_config(self) -> PipelineConfig:
        return PipelineConfig(
            # Higher onset threshold for cleaner transients
            onset_threshold=0.5,
            frame_threshold=0.4,
            min_note_ms=30.0,  # Allow short staccato notes

            # Minimal quantization for lead (preserve timing)
            quantize_strength=0.0,  # Disabled - quantize creates overlaps
            quantize_grid=16,

            # Minimal merging - preserve articulation
            merge_max_gap_ms=10.0,
            merge_enabled=False,  # Disable for lead

            # Light key filtering
            key_filter_strictness=0.3,

            # Isolated note handling - be permissive
            isolated_filter_enabled=False,

            # Harmonic handling
            harmonic_suppression_enabled=True,
            octave_correction_enabled=True,  # Correct octave-low detection
            subharmonic_cleanup_enabled=False,

            # Velocity
            velocity_normalize=True,
            velocity_min=70,
            velocity_max=120,

            # Precision filters
            precision_filters=[
                "octave_false_positive",
                "harmonic_duplicate",
                "transient_validator",
                "repeated_pattern",
            ],
        )

    def _setup_filters(self) -> None:
        """Set up precision recovery filters for lead."""
        # Octave hallucination filter with moderate settings
        self._precision_filters.append(
            OctaveFalsePositiveFilter(
                min_suppression_confidence=0.75,
                protection_weight=1.5,
            )
        )

        # Harmonic duplicate filter
        self._precision_filters.append(
            HarmonicDuplicateFilter(
                min_suppression_confidence=0.7,
                protection_weight=1.8,  # Higher protection for melodic lines
            )
        )

        # Transient validator - verify note attacks
        self._precision_filters.append(
            TransientNoteValidator(
                min_suppression_confidence=0.7,
                protection_weight=1.5,
            )
        )

        # Pattern validator - protect repeated melodic patterns
        self._precision_filters.append(
            RepeatedPatternValidator(
                min_suppression_confidence=0.6,
                protection_weight=2.0,  # Strong pattern protection
            )
        )

    def _preprocess_audio(
        self,
        audio: np.ndarray,
        sr: int,
    ) -> np.ndarray:
        """Preprocess audio for lead extraction.

        - High-pass filter to remove low rumble
        - Mild compression to even out dynamics
        """
        from scipy import signal

        # High-pass filter at 150 Hz to remove bass content
        nyquist = sr / 2
        high_cutoff = min(150 / nyquist, 0.99)
        b, a = signal.butter(4, high_cutoff, btype='high')
        filtered = signal.filtfilt(b, a, audio)

        return filtered.astype(np.float32)

    def _postprocess_notes(
        self,
        notes: List[ExtractedNote],
        audio: np.ndarray,
        sr: int,
        tempo: Optional[float],
        key: Optional[Tuple[int, str]],
    ) -> List[ExtractedNote]:
        """Post-process notes for lead extraction.

        - Correct octave errors (basic-pitch often detects lead an octave low)
        - Enforce reasonable pitch range (C3-C7)
        - Remove very short notes that aren't part of patterns
        - Preserve melodic continuity
        """
        if not notes:
            return notes

        # First, apply octave correction (lead often detected too low)
        if self.config.octave_correction_enabled:
            notes = self._correct_lead_octave(notes)

        # Filter to reasonable lead pitch range (C3=48 to C7=96)
        filtered = [n for n in notes if 48 <= n.pitch <= 96]

        # Sort by time
        filtered = sorted(filtered, key=lambda n: n.start)

        # Remove overlapping same-pitch notes (keep higher confidence)
        filtered = self._remove_overlapping_duplicates(filtered)

        # Remove delay/echo artifacts
        if tempo:
            filtered = self._remove_delay_artifacts(filtered, tempo)

        # Remove isolated very short notes (< 20ms) that aren't patterns
        min_duration = 0.02  # 20ms
        result = []

        for i, note in enumerate(filtered):
            if note.duration >= min_duration:
                result.append(note)
            else:
                # Check if part of a fast pattern
                has_context = False
                for other in filtered:
                    if other == note:
                        continue
                    if abs(other.start - note.start) < 0.3 and other.pitch == note.pitch:
                        has_context = True
                        break
                if has_context:
                    result.append(note)

        return result

    def _remove_overlapping_duplicates(
        self,
        notes: List[ExtractedNote],
    ) -> List[ExtractedNote]:
        """Remove overlapping notes of the same pitch, keeping the higher confidence one.

        Basic-pitch often detects the same note multiple times with slight overlaps.
        This cleans up those duplicates while preserving intentional repeated notes.
        """
        if len(notes) < 2:
            return notes

        from collections import defaultdict

        # Group by pitch
        by_pitch: dict = defaultdict(list)
        for note in notes:
            by_pitch[note.pitch].append(note)

        cleaned = []

        for pitch, pitch_notes in by_pitch.items():
            # Sort by start time
            sorted_notes = sorted(pitch_notes, key=lambda n: n.start)

            # Merge overlapping notes
            current = sorted_notes[0]

            for next_note in sorted_notes[1:]:
                # Check for overlap (current ends after next starts)
                if current.end > next_note.start:
                    # Overlap - merge by extending current to cover both
                    # Keep the higher confidence
                    if next_note.confidence > current.confidence:
                        # Use next note's timing but extend if needed
                        current = ExtractedNote(
                            pitch=pitch,
                            start=min(current.start, next_note.start),
                            end=max(current.end, next_note.end),
                            velocity=next_note.velocity,
                            confidence=next_note.confidence,
                            source_pass=next_note.source_pass,
                        )
                    else:
                        # Extend current note
                        current = ExtractedNote(
                            pitch=pitch,
                            start=current.start,
                            end=max(current.end, next_note.end),
                            velocity=current.velocity,
                            confidence=current.confidence,
                            source_pass=current.source_pass,
                        )
                else:
                    # No overlap - save current and move to next
                    cleaned.append(current)
                    current = next_note

            # Don't forget the last note
            cleaned.append(current)

        return sorted(cleaned, key=lambda n: n.start)

    def _correct_lead_octave(
        self,
        notes: List[ExtractedNote],
    ) -> List[ExtractedNote]:
        """Correct octave errors in lead detection.

        Basic-pitch often detects synth leads one octave too low.
        If most notes cluster in a low range (D3-D4) but the audio
        sounds like a lead, shift up an octave.

        Strategy:
        1. Find the dominant pitch class (e.g., D)
        2. If most instances are in octave 3-4, shift to octave 4-5
        3. Be conservative - only shift if pattern is clear
        """
        if not notes:
            return notes

        from collections import Counter

        # Count notes by octave
        octaves = Counter(n.pitch // 12 for n in notes)

        # If most notes are in octave 4-5 (pitches 48-71), consider shifting up
        # Typical synth leads are in octave 5-6 (pitches 72-95)
        low_octave_count = sum(octaves.get(o, 0) for o in [4, 5])  # Octaves 4-5
        high_octave_count = sum(octaves.get(o, 0) for o in [6, 7])  # Octaves 6-7
        total = len(notes)

        # If > 70% of notes are in low octaves and few in high octaves,
        # shift low notes up one octave (keep high notes as-is)
        if total > 0 and low_octave_count / total > 0.7 and high_octave_count / total < 0.15:
            corrected = []
            for note in notes:
                note_octave = note.pitch // 12
                # Only shift notes in octaves 4-5, keep octaves 6-7 as-is
                if note_octave in [4, 5]:
                    new_pitch = note.pitch + 12
                    if new_pitch <= 96:  # Stay within C7
                        corrected.append(ExtractedNote(
                            pitch=new_pitch,
                            start=note.start,
                            end=note.end,
                            velocity=note.velocity,
                            confidence=note.confidence * 0.95,  # Slightly reduce confidence
                            source_pass=note.source_pass,
                            flags=note.flags,
                            provenance=note.provenance,
                        ))
                    else:
                        corrected.append(note)
                else:
                    corrected.append(note)
            return corrected

        return notes

    def _remove_delay_artifacts(
        self,
        notes: List[ExtractedNote],
        tempo: float,
    ) -> List[ExtractedNote]:
        """Remove delay/echo artifacts from lead extraction.

        Delay effects create repeated notes at regular intervals (typically
        beat-aligned: 1/4, 1/2, 1 beat, 2 beats). These echoes have:
        - Same pitch as source note
        - Regular timing intervals
        - Often lower confidence than source

        Strategy:
        1. Detect common delay intervals (beat divisions)
        2. For each pitch, find chains of notes at delay intervals
        3. Keep only the first note in each chain (the source)
        """
        if not notes or tempo <= 0:
            return notes

        from collections import defaultdict

        beat_duration = 60.0 / tempo

        # Common delay times (in beats) - including 2 beats for this track
        delay_beats = [0.25, 0.5, 0.75, 1.0, 1.5, 2.0]
        delay_times = [beat_duration * b for b in delay_beats]
        tolerance = 0.05  # 50ms tolerance

        # Group notes by pitch
        by_pitch: dict = defaultdict(list)
        for note in notes:
            by_pitch[note.pitch].append(note)

        kept_ids = set()
        removed_ids = set()

        for pitch, pitch_notes in by_pitch.items():
            if len(pitch_notes) < 3:
                # Not enough notes to form a delay chain
                for n in pitch_notes:
                    kept_ids.add(id(n))
                continue

            # Sort by start time
            sorted_notes = sorted(pitch_notes, key=lambda n: n.start)

            # Find delay chains using a sliding window approach
            processed = set()

            for i, source in enumerate(sorted_notes):
                if id(source) in processed:
                    continue

                # Try to find echoes of this source note
                echoes = []

                for j in range(i + 1, min(i + 20, len(sorted_notes))):  # Look ahead max 20 notes
                    candidate = sorted_notes[j]
                    if id(candidate) in processed:
                        continue

                    gap = candidate.start - source.start

                    # Check if gap matches a delay time (or multiple of it)
                    is_delay = False
                    for delay_time in delay_times:
                        # Check direct match or multiples (2x, 3x, etc.)
                        for mult in [1, 2, 3, 4, 5, 6, 7, 8]:
                            if abs(gap - delay_time * mult) < tolerance:
                                # Additional check: echo should have similar or lower confidence
                                if candidate.confidence <= source.confidence * 1.2:
                                    echoes.append(candidate)
                                    is_delay = True
                                    break
                        if is_delay:
                            break

                # If we found a significant echo chain (2+ echoes)
                if len(echoes) >= 2:
                    # Mark source as kept
                    kept_ids.add(id(source))
                    processed.add(id(source))

                    # Mark echoes as removed
                    for echo in echoes:
                        removed_ids.add(id(echo))
                        processed.add(id(echo))
                else:
                    # No significant chain - keep the note
                    kept_ids.add(id(source))
                    processed.add(id(source))

            # Handle any remaining notes
            for note in sorted_notes:
                if id(note) not in processed:
                    kept_ids.add(id(note))

        # Return kept notes
        return sorted([n for n in notes if id(n) in kept_ids], key=lambda n: n.start)
