"""Pad/synth pad stem extraction pipeline.

Optimized for:
- Long sustained notes
- Chord detection
- Harmonic suppression (pads have rich harmonics)
- Slow attacks and releases
"""
from __future__ import annotations

from typing import List, Optional, Tuple

import numpy as np

from tone_forge.midi.passes.base import ExtractedNote
from tone_forge.midi.filters.octave_false_positive import OctaveFalsePositiveFilter
from tone_forge.midi.filters.harmonic_duplicate import HarmonicDuplicateFilter
from tone_forge.midi.filters.sustain_overlap import SustainOverlapCleanup
from tone_forge.midi.polyphony_estimator import PolyphonyEstimate, PolyphonyClass
from .base import StemPipeline, PipelineConfig


class PadPipeline(StemPipeline):
    """Pipeline optimized for pad/atmosphere extraction.

    Key behaviors:
    - Focus on long sustained notes
    - Strong harmonic suppression (pads are harmonically rich)
    - Chord grouping
    - Minimal quantization (pads often have loose timing)
    - Handle slow attacks/releases
    """

    @property
    def name(self) -> str:
        return "pad"

    def _default_config(self) -> PipelineConfig:
        return PipelineConfig(
            # Lower thresholds for pads with slow attacks
            onset_threshold=0.35,
            frame_threshold=0.3,
            min_note_ms=300.0,  # Pads are long

            # Minimal quantization
            quantize_strength=0.2,
            quantize_grid=4,  # Quarter note grid

            # Merge overlapping notes (chords)
            merge_max_gap_ms=100.0,
            merge_enabled=True,

            # Strict key filtering for harmonic content
            key_filter_strictness=0.6,

            # Don't filter isolated pad notes
            isolated_filter_enabled=False,

            # Harmonic handling - very important for pads
            harmonic_suppression_enabled=True,
            octave_correction_enabled=False,
            subharmonic_cleanup_enabled=False,

            # Velocity - pads usually steady
            velocity_normalize=True,
            velocity_min=70,
            velocity_max=90,

            # Precision filters
            precision_filters=[
                "octave_false_positive",
                "harmonic_duplicate",
                "sustain_overlap",
            ],
        )

    def _setup_filters(self) -> None:
        """Set up precision recovery filters for pads."""
        # Octave filter
        self._precision_filters.append(
            OctaveFalsePositiveFilter(
                min_suppression_confidence=0.6,  # More aggressive for pads
                protection_weight=1.3,
            )
        )

        # Harmonic duplicate filter - critical for pads
        self._precision_filters.append(
            HarmonicDuplicateFilter(
                min_suppression_confidence=0.6,
                protection_weight=1.5,
            )
        )

        # Sustain overlap cleanup
        self._precision_filters.append(
            SustainOverlapCleanup(
                min_suppression_confidence=0.65,
                protection_weight=1.5,
                sustain_threshold_ms=500.0,
            )
        )

    def _adapt_to_polyphony(self, estimate: PolyphonyEstimate) -> None:
        """Adapt pad pipeline based on polyphony estimation.

        Pads are usually polyphonic (chords), but the degree matters:
        - Light polyphony (2-4 voices): Standard chord extraction
        - Dense polyphony (5+): Be more conservative with harmonic cleanup
        """
        # Store for use in post-processing
        self._polyphony_estimate = estimate

        # Adjust harmonic cleanup based on detected polyphony
        if estimate.polyphony_class == PolyphonyClass.DENSE_POLY:
            # Dense chords - be very careful with harmonic cleanup
            # Many "harmonics" might be intentional chord voicing
            self.config.harmonic_suppression_enabled = False
            self.config.key_filter_strictness = 0.4  # More lenient

            # Adjust precision filter behavior
            for f in self._precision_filters:
                if hasattr(f, 'min_suppression_confidence'):
                    f.min_suppression_confidence = max(
                        f.min_suppression_confidence, 0.75
                    )

        elif estimate.polyphony_class == PolyphonyClass.LIGHT_POLY:
            # Standard chord handling
            pass  # Use defaults

        else:
            # Monophonic pad? Unusual but possible (pad lead)
            self.config.harmonic_suppression_enabled = True
            self.config.merge_enabled = True
            self.config.merge_max_gap_ms = 150.0

        # Use recommended merge gap
        if estimate.recommended_merge_gap > 0:
            # For pads, use longer merge gaps than default recommendation
            self.config.merge_max_gap_ms = max(
                self.config.merge_max_gap_ms,
                estimate.recommended_merge_gap * 1000 * 2  # Double for pads
            )

    def _preprocess_audio(
        self,
        audio: np.ndarray,
        sr: int,
    ) -> np.ndarray:
        """Preprocess audio for pad extraction.

        - Apply smoothing to reduce transient artifacts
        - Focus on mid-range frequencies typical of pads
        """
        from scipy import signal

        # Band-pass filter (100 Hz to 8000 Hz)
        nyquist = sr / 2
        low = min(100 / nyquist, 0.99)
        high = min(8000 / nyquist, 0.99)
        b, a = signal.butter(2, [low, high], btype='band')
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
        """Post-process notes for pad extraction.

        - Merge fragmented sustained notes (critical for pads!)
        - Filter short notes (pads are sustained)
        - Group into chords
        - Clean up harmonic artifacts
        """
        if not notes:
            return notes

        # Filter to pad pitch range (C2-C6)
        filtered = [n for n in notes if 36 <= n.pitch <= 84]

        # CRITICAL: Merge consecutive same-pitch notes into sustained notes
        # Basic-pitch fragments long pad notes into many short ones
        filtered = self._merge_sustained_notes(filtered)

        # Remove short notes AFTER merging
        min_duration = self.config.min_note_ms / 1000.0
        filtered = [n for n in filtered if n.duration >= min_duration]

        # Group simultaneous notes as chords
        filtered = self._group_chords(filtered)

        # Remove sub-octave false positives (notes detected an octave below real notes)
        filtered = self._remove_sub_octave_artifacts(filtered)

        # Filter high-frequency artifacts (above typical pad range)
        filtered = [n for n in filtered if n.pitch <= 79]  # B5 max for pads

        return filtered

    def _merge_sustained_notes(
        self,
        notes: List[ExtractedNote],
    ) -> List[ExtractedNote]:
        """Merge consecutive notes of the same pitch into sustained notes.

        Pads are long sustained notes, but basic-pitch often fragments them.
        This merges consecutive same-pitch notes that are close in time.
        """
        if len(notes) < 2:
            return notes

        # Group by pitch
        from collections import defaultdict
        by_pitch: dict = defaultdict(list)
        for note in notes:
            by_pitch[note.pitch].append(note)

        merged_notes = []

        for pitch, pitch_notes in by_pitch.items():
            # Sort by start time
            sorted_notes = sorted(pitch_notes, key=lambda n: n.start)

            # Merge consecutive notes with larger gaps (pads have slow attacks/decays)
            max_gap = 2.0  # Allow 2s gap for pad sustain (basic-pitch often drops out)

            current_start = sorted_notes[0].start
            current_end = sorted_notes[0].end
            current_confidence = sorted_notes[0].confidence
            note_count = 1

            for note in sorted_notes[1:]:
                gap = note.start - current_end

                if gap <= max_gap:
                    # Merge - extend the current note
                    current_end = max(current_end, note.end)
                    current_confidence = max(current_confidence, note.confidence)
                    note_count += 1
                else:
                    # Gap too large - save current and start new
                    merged_notes.append(ExtractedNote(
                        pitch=pitch,
                        start=current_start,
                        end=current_end,
                        velocity=80,  # Standard velocity for pads
                        confidence=current_confidence,
                        source_pass=1,
                    ))

                    current_start = note.start
                    current_end = note.end
                    current_confidence = note.confidence
                    note_count = 1

            # Don't forget the last merged note
            merged_notes.append(ExtractedNote(
                pitch=pitch,
                start=current_start,
                end=current_end,
                velocity=80,
                confidence=current_confidence,
                source_pass=1,
            ))

        return sorted(merged_notes, key=lambda n: n.start)

    def _group_chords(
        self,
        notes: List[ExtractedNote],
    ) -> List[ExtractedNote]:
        """Group simultaneous notes and clean up chord artifacts.

        If multiple notes start at the same time, they form a chord.
        Remove notes that are likely harmonic artifacts within chords.
        """
        if not notes:
            return notes

        time_tolerance = 0.5  # 500ms - pads have slow attacks

        # Sort by start time
        sorted_notes = sorted(notes, key=lambda n: n.start)

        # Group by start time
        chord_groups = []
        current_group = [sorted_notes[0]]

        for note in sorted_notes[1:]:
            if abs(note.start - current_group[0].start) <= time_tolerance:
                current_group.append(note)
            else:
                chord_groups.append(current_group)
                current_group = [note]

        chord_groups.append(current_group)

        # Process each chord group
        result = []
        for group in chord_groups:
            if len(group) == 1:
                result.extend(group)
            else:
                # Multiple notes - potential chord or harmonics
                cleaned = self._clean_chord_harmonics(group)
                result.extend(cleaned)

        return result

    def _clean_chord_harmonics(
        self,
        chord_notes: List[ExtractedNote],
    ) -> List[ExtractedNote]:
        """Remove likely harmonic artifacts from a chord.

        Keep notes that form standard chord intervals.
        Preserve octave doublings (common in pad voicings).
        Only remove 5th harmonics with low confidence.
        """
        if len(chord_notes) <= 2:
            return chord_notes

        # Sort by pitch
        sorted_by_pitch = sorted(chord_notes, key=lambda n: n.pitch)

        # Keep the lowest note as anchor
        result = [sorted_by_pitch[0]]

        # For each other note, check if it's a likely real chord tone
        for note in sorted_by_pitch[1:]:
            is_harmonic_artifact = False

            for kept in result:
                interval = note.pitch - kept.pitch

                # Preserve octave doublings (12, 24 semitones) - common in pads
                # Only filter 5th harmonics (7, 19 semitones) with very low confidence
                if interval in [7, 19]:
                    # 5th harmonic - only filter if much lower confidence
                    if note.confidence < kept.confidence * 0.5:
                        is_harmonic_artifact = True
                        break

            if not is_harmonic_artifact:
                result.append(note)

        return result

    def _remove_sub_octave_artifacts(
        self,
        notes: List[ExtractedNote],
    ) -> List[ExtractedNote]:
        """Remove notes that are sub-octave artifacts of other notes.

        In pad extraction, basic-pitch sometimes detects notes an octave
        below the actual pitch due to sub-harmonics. If we have both D4
        and D3 starting at the same time, the D3 is likely an artifact.
        """
        if not notes:
            return notes

        time_tolerance = 1.0  # 1 second tolerance for pad onsets

        # Sort by start time
        sorted_notes = sorted(notes, key=lambda n: n.start)

        # Find notes that are sub-octave artifacts
        artifact_ids = set()
        for i, note in enumerate(sorted_notes):
            for j, other in enumerate(sorted_notes):
                if i == j:
                    continue

                # Check if notes start around same time
                if abs(note.start - other.start) > time_tolerance:
                    continue

                # Check if note is exactly one octave below other
                if other.pitch - note.pitch == 12:
                    # Lower note is likely artifact if confidence is lower
                    if note.confidence <= other.confidence:
                        artifact_ids.add(id(note))
                        break

        # Return notes that aren't artifacts
        return [n for n in notes if id(n) not in artifact_ids]
