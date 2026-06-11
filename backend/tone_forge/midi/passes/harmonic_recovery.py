"""Pass 2: Harmonic recovery.

Uses harmonic context from high-confidence notes to recover
notes that were missed due to soft attacks or interference.
"""
from __future__ import annotations

import logging
import time
from dataclasses import replace
from typing import Dict, List, Optional, Set, Tuple

import librosa
import numpy as np

from .base import (
    ExtractionContext,
    ExtractionPass,
    ExtractedNote,
    NoteFlag,
    PassResult,
)

logger = logging.getLogger(__name__)


class HarmonicRecoveryPass(ExtractionPass):
    """Recover missed notes using harmonic context.

    This pass analyzes the harmonic structure implied by high-confidence
    notes and searches for evidence of related pitches that were missed
    in the initial detection.

    Recovery strategies:
    1. Octave recovery: Look for octave relationships
    2. Fifth recovery: Look for perfect fifth relationships
    3. Gap filling: Look for sustained pitches in silent gaps
    4. Envelope continuation: Extend truncated notes
    """

    def __init__(
        self,
        pass_number: int = 2,
        recovery_confidence: float = 0.5,
        octave_search_enabled: bool = True,
        fifth_search_enabled: bool = True,
        gap_fill_enabled: bool = True,
        min_gap_ms: float = 100.0,
        max_gap_ms: float = 2000.0,
    ):
        """Initialize harmonic recovery pass.

        Args:
            pass_number: Pass number in pipeline
            recovery_confidence: Confidence assigned to recovered notes
            octave_search_enabled: Search for octave relationships
            fifth_search_enabled: Search for fifth relationships
            gap_fill_enabled: Fill gaps between notes
            min_gap_ms: Minimum gap size to consider
            max_gap_ms: Maximum gap size to consider
        """
        super().__init__(pass_number)
        self.recovery_confidence = recovery_confidence
        self.octave_search_enabled = octave_search_enabled
        self.fifth_search_enabled = fifth_search_enabled
        self.gap_fill_enabled = gap_fill_enabled
        self.min_gap_ms = min_gap_ms
        self.max_gap_ms = max_gap_ms

    @property
    def name(self) -> str:
        return "harmonic_recovery"

    def process(
        self,
        notes: List[ExtractedNote],
        context: ExtractionContext,
    ) -> PassResult:
        """Recover notes using harmonic context.

        Args:
            notes: High-confidence notes from previous pass
            context: Extraction context

        Returns:
            PassResult with original plus recovered notes
        """
        start_time = time.time()
        input_count = len(notes)

        if input_count == 0:
            # Can't recover without context
            return PassResult(
                notes=[],
                statistics=self._create_statistics(notes, [], 0.0),
                warnings=["No input notes for harmonic recovery"],
            )

        # Build harmonic context
        harmonic_context = self._build_harmonic_context(notes, context)

        recovered_notes = []

        # Skip harmonic recovery for lead stems - leads are monophonic melodies
        # where octave/fifth recovery adds false positives
        is_lead = context.stem_type and context.stem_type.lower() in ("lead", "guitar", "other", "vocals")

        # Strategy 1: Octave recovery (skip for leads)
        if self.octave_search_enabled and not is_lead:
            octave_notes = self._recover_octaves(notes, context, harmonic_context)
            recovered_notes.extend(octave_notes)

        # Strategy 2: Fifth recovery (skip for leads)
        if self.fifth_search_enabled and not is_lead:
            fifth_notes = self._recover_fifths(notes, context, harmonic_context)
            recovered_notes.extend(fifth_notes)

        # Strategy 3: Gap filling (skip for leads - gaps are intentional)
        if self.gap_fill_enabled and not is_lead:
            gap_notes = self._fill_gaps(notes, context, harmonic_context)
            recovered_notes.extend(gap_notes)

        # Strategy 4: Envelope continuation (can still apply to leads)
        extended_notes = self._extend_truncated(notes, context) if not is_lead else []

        # Merge all notes, removing duplicates
        all_notes = self._merge_notes(notes, recovered_notes, extended_notes)

        execution_time = (time.time() - start_time) * 1000

        stats = self._create_statistics(
            notes,
            all_notes,
            execution_time,
            octaves_recovered=len([n for n in recovered_notes
                                   if n.harmonic_context and
                                   n.harmonic_context.get("type") == "octave"]),
            fifths_recovered=len([n for n in recovered_notes
                                  if n.harmonic_context and
                                  n.harmonic_context.get("type") == "fifth"]),
            gaps_filled=len([n for n in recovered_notes
                            if n.harmonic_context and
                            n.harmonic_context.get("type") == "gap"]),
        )

        return PassResult(
            notes=all_notes,
            statistics=stats,
            metadata={"harmonic_context": harmonic_context},
        )

    def _build_harmonic_context(
        self,
        notes: List[ExtractedNote],
        context: ExtractionContext,
    ) -> Dict:
        """Build harmonic context from notes.

        Analyzes the pitch distribution, temporal patterns, and
        implied harmonic relationships.
        """
        if not notes:
            return {}

        pitches = [n.pitch for n in notes]
        pitch_classes = [p % 12 for p in pitches]

        # Count pitch class occurrences
        pitch_class_counts = {}
        for pc in pitch_classes:
            pitch_class_counts[pc] = pitch_class_counts.get(pc, 0) + 1

        # Find most common pitch classes
        sorted_pcs = sorted(pitch_class_counts.items(), key=lambda x: -x[1])
        dominant_pcs = [pc for pc, count in sorted_pcs[:3]]

        # Find pitch range
        min_pitch = min(pitches)
        max_pitch = max(pitches)

        # Compute average note density
        if notes:
            total_duration = max(n.end for n in notes) - min(n.start for n in notes)
            density = len(notes) / max(total_duration, 0.1)
        else:
            density = 0

        return {
            "pitch_class_counts": pitch_class_counts,
            "dominant_pitch_classes": dominant_pcs,
            "pitch_range": (min_pitch, max_pitch),
            "note_density": density,
            "total_notes": len(notes),
        }

    def _recover_octaves(
        self,
        notes: List[ExtractedNote],
        context: ExtractionContext,
        harmonic_context: Dict,
    ) -> List[ExtractedNote]:
        """Look for missed octave notes.

        If we have a note at pitch P, look for evidence of P-12 or P+12
        that might have been missed due to masking.
        """
        recovered = []
        audio = context.audio
        sr = context.sr

        if audio.ndim > 1:
            audio = np.mean(audio, axis=0)

        # Get spectral representation
        hop_length = 512
        n_fft = 2048
        S = np.abs(librosa.stft(audio, n_fft=n_fft, hop_length=hop_length))

        for note in notes:
            # Only use high-confidence notes as reference
            if note.confidence < 0.6:
                continue

            # Check octave above
            octave_above = note.pitch + 12
            if octave_above <= 108:  # Reasonable upper limit
                if self._has_spectral_evidence(
                    S, sr, hop_length, note.start, note.end, octave_above
                ):
                    recovered_note = ExtractedNote(
                        pitch=octave_above,
                        start=note.start,
                        end=note.end,
                        velocity=int(note.velocity * 0.8),  # Typically quieter
                        confidence=self.recovery_confidence,
                        source_pass=self.pass_number,
                        flags={NoteFlag.HARMONIC_RECOVERY},
                        harmonic_context={"type": "octave", "reference_pitch": note.pitch},
                    )
                    recovered.append(recovered_note)

            # Check octave below
            octave_below = note.pitch - 12
            if octave_below >= 21:  # Reasonable lower limit
                if self._has_spectral_evidence(
                    S, sr, hop_length, note.start, note.end, octave_below
                ):
                    recovered_note = ExtractedNote(
                        pitch=octave_below,
                        start=note.start,
                        end=note.end,
                        velocity=int(note.velocity * 0.9),
                        confidence=self.recovery_confidence,
                        source_pass=self.pass_number,
                        flags={NoteFlag.HARMONIC_RECOVERY},
                        harmonic_context={"type": "octave", "reference_pitch": note.pitch},
                    )
                    recovered.append(recovered_note)

        return recovered

    def _recover_fifths(
        self,
        notes: List[ExtractedNote],
        context: ExtractionContext,
        harmonic_context: Dict,
    ) -> List[ExtractedNote]:
        """Look for missed perfect fifth notes.

        Perfect fifths are common in synth stacks and bass lines.
        """
        recovered = []
        audio = context.audio
        sr = context.sr

        if audio.ndim > 1:
            audio = np.mean(audio, axis=0)

        # Only for certain stem types
        if context.stem_type and context.stem_type.lower() not in ["synth", "pad", "bass", "lead"]:
            return recovered

        hop_length = 512
        n_fft = 2048
        S = np.abs(librosa.stft(audio, n_fft=n_fft, hop_length=hop_length))

        for note in notes:
            if note.confidence < 0.7:  # Higher threshold for fifths
                continue

            # Perfect fifth above (+7 semitones)
            fifth_above = note.pitch + 7
            if fifth_above <= 108:
                if self._has_spectral_evidence(
                    S, sr, hop_length, note.start, note.end, fifth_above,
                    threshold=0.3,  # Lower threshold - fifths are quieter
                ):
                    recovered_note = ExtractedNote(
                        pitch=fifth_above,
                        start=note.start,
                        end=note.end,
                        velocity=int(note.velocity * 0.7),
                        confidence=self.recovery_confidence * 0.8,
                        source_pass=self.pass_number,
                        flags={NoteFlag.HARMONIC_RECOVERY},
                        harmonic_context={"type": "fifth", "reference_pitch": note.pitch},
                    )
                    recovered.append(recovered_note)

        return recovered

    def _fill_gaps(
        self,
        notes: List[ExtractedNote],
        context: ExtractionContext,
        harmonic_context: Dict,
    ) -> List[ExtractedNote]:
        """Fill gaps between notes with sustained pitches.

        Looks for evidence of continuous pitch content in gaps
        between detected notes.
        """
        recovered = []
        audio = context.audio
        sr = context.sr

        if audio.ndim > 1:
            audio = np.mean(audio, axis=0)

        if len(notes) < 2:
            return recovered

        # Sort by start time
        sorted_notes = sorted(notes, key=lambda n: n.start)

        hop_length = 512
        n_fft = 2048
        S = np.abs(librosa.stft(audio, n_fft=n_fft, hop_length=hop_length))

        for i in range(len(sorted_notes) - 1):
            current = sorted_notes[i]
            next_note = sorted_notes[i + 1]

            gap_start = current.end
            gap_end = next_note.start
            gap_ms = (gap_end - gap_start) * 1000

            # Check if gap is in our target range
            if gap_ms < self.min_gap_ms or gap_ms > self.max_gap_ms:
                continue

            # Look for pitch content in gap
            # Check both the ending pitch and starting pitch of surrounding notes
            candidate_pitches = {current.pitch, next_note.pitch}

            for candidate_pitch in candidate_pitches:
                if self._has_spectral_evidence(
                    S, sr, hop_length, gap_start, gap_end, candidate_pitch,
                    threshold=0.25,  # Lower threshold for gaps
                ):
                    # Estimate velocity from spectral energy
                    velocity = self._estimate_velocity_in_region(
                        S, sr, hop_length, gap_start, gap_end, candidate_pitch
                    )

                    recovered_note = ExtractedNote(
                        pitch=candidate_pitch,
                        start=gap_start,
                        end=gap_end,
                        velocity=velocity,
                        confidence=self.recovery_confidence * 0.9,
                        source_pass=self.pass_number,
                        flags={NoteFlag.HARMONIC_RECOVERY},
                        harmonic_context={
                            "type": "gap",
                            "gap_ms": gap_ms,
                            "reference_pitch": current.pitch,
                        },
                    )
                    recovered.append(recovered_note)
                    break  # Only fill with one pitch

        return recovered

    def _extend_truncated(
        self,
        notes: List[ExtractedNote],
        context: ExtractionContext,
    ) -> List[ExtractedNote]:
        """Extend notes that appear to be truncated.

        Uses envelope analysis to find notes that should sustain longer.
        """
        extended = []
        audio = context.audio
        sr = context.sr

        if audio.ndim > 1:
            audio = np.mean(audio, axis=0)

        hop_length = 512
        n_fft = 2048
        S = np.abs(librosa.stft(audio, n_fft=n_fft, hop_length=hop_length))

        for note in notes:
            # Look for spectral evidence continuing past note end
            extension_check_duration = 0.5  # Check up to 500ms past end
            extension_end = min(note.end + extension_check_duration, len(audio) / sr)

            if extension_end <= note.end + 0.05:  # Need at least 50ms to check
                continue

            if self._has_spectral_evidence(
                S, sr, hop_length, note.end, extension_end, note.pitch,
                threshold=0.2,  # Lower threshold for extensions
            ):
                # Find where the pitch actually ends
                new_end = self._find_pitch_end(
                    S, sr, hop_length, note.end, extension_end, note.pitch
                )

                if new_end > note.end + 0.05:  # Meaningful extension
                    extended_note = replace(
                        note,
                        end=new_end,
                        original_end=note.end,
                    )
                    extended_note.flags.add(NoteFlag.HARMONIC_RECOVERY)
                    extended.append(extended_note)

        return extended

    def _has_spectral_evidence(
        self,
        S: np.ndarray,
        sr: int,
        hop_length: int,
        start_time: float,
        end_time: float,
        pitch: int,
        threshold: float = 0.3,
    ) -> bool:
        """Check for spectral evidence of a pitch in a time region."""
        freq = librosa.midi_to_hz(pitch)

        # Convert times to frames
        start_frame = max(0, int(start_time * sr / hop_length))
        end_frame = min(S.shape[1], int(end_time * sr / hop_length))

        if end_frame <= start_frame:
            return False

        # Find frequency bin
        n_fft = (S.shape[0] - 1) * 2
        freqs = librosa.fft_frequencies(sr=sr, n_fft=n_fft)
        freq_bin = np.argmin(np.abs(freqs - freq))

        # Check energy in target bin and nearby (for slight pitch drift)
        bin_range = range(max(0, freq_bin - 2), min(S.shape[0], freq_bin + 3))

        region_energy = S[list(bin_range), start_frame:end_frame]
        if region_energy.size == 0:
            return False

        mean_energy = np.mean(region_energy)

        # Compare to overall energy in the region
        overall_mean = np.mean(S[:, start_frame:end_frame])

        if overall_mean == 0:
            return False

        return (mean_energy / overall_mean) > threshold

    def _estimate_velocity_in_region(
        self,
        S: np.ndarray,
        sr: int,
        hop_length: int,
        start_time: float,
        end_time: float,
        pitch: int,
    ) -> int:
        """Estimate note velocity from spectral energy."""
        freq = librosa.midi_to_hz(pitch)

        start_frame = max(0, int(start_time * sr / hop_length))
        end_frame = min(S.shape[1], int(end_time * sr / hop_length))

        n_fft = (S.shape[0] - 1) * 2
        freqs = librosa.fft_frequencies(sr=sr, n_fft=n_fft)
        freq_bin = np.argmin(np.abs(freqs - freq))

        bin_range = range(max(0, freq_bin - 2), min(S.shape[0], freq_bin + 3))
        region_energy = S[list(bin_range), start_frame:end_frame]

        if region_energy.size == 0:
            return 64  # Default middle velocity

        # Normalize to velocity range
        max_energy = np.max(S)
        if max_energy == 0:
            return 64

        normalized = np.mean(region_energy) / max_energy
        velocity = int(np.clip(normalized * 127, 30, 110))

        return velocity

    def _find_pitch_end(
        self,
        S: np.ndarray,
        sr: int,
        hop_length: int,
        start_time: float,
        max_end_time: float,
        pitch: int,
    ) -> float:
        """Find where a pitch actually ends in the spectrogram."""
        freq = librosa.midi_to_hz(pitch)

        start_frame = int(start_time * sr / hop_length)
        max_end_frame = min(S.shape[1], int(max_end_time * sr / hop_length))

        n_fft = (S.shape[0] - 1) * 2
        freqs = librosa.fft_frequencies(sr=sr, n_fft=n_fft)
        freq_bin = np.argmin(np.abs(freqs - freq))

        bin_range = range(max(0, freq_bin - 2), min(S.shape[0], freq_bin + 3))

        # Find where energy drops below threshold
        threshold = 0.1 * np.mean(S[list(bin_range), start_frame:max_end_frame])

        for frame in range(start_frame, max_end_frame):
            frame_energy = np.mean(S[list(bin_range), frame])
            if frame_energy < threshold:
                return frame * hop_length / sr

        return max_end_time

    def _merge_notes(
        self,
        original: List[ExtractedNote],
        recovered: List[ExtractedNote],
        extended: List[ExtractedNote],
    ) -> List[ExtractedNote]:
        """Merge all notes, handling duplicates and overlaps."""
        all_notes = []

        # Start with original notes, replacing with extended versions
        extended_pitches = {(n.pitch, n.start) for n in extended}

        for note in original:
            key = (note.pitch, note.start)
            if key in extended_pitches:
                # Use extended version
                ext_note = next(n for n in extended if (n.pitch, n.start) == key)
                all_notes.append(ext_note)
            else:
                all_notes.append(note)

        # Add recovered notes that don't overlap with existing
        for rec_note in recovered:
            if not self._overlaps_existing(rec_note, all_notes):
                all_notes.append(rec_note)

        # Sort by start time
        all_notes.sort(key=lambda n: (n.start, n.pitch))

        return all_notes

    def _overlaps_existing(
        self,
        note: ExtractedNote,
        existing: List[ExtractedNote],
    ) -> bool:
        """Check if a note overlaps with existing notes at same pitch."""
        for ex in existing:
            if ex.pitch != note.pitch:
                continue

            # Check for time overlap
            if note.start < ex.end and note.end > ex.start:
                return True

        return False
