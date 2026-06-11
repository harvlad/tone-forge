"""Frequency-aware spectral validation for MIDI notes.

Provides spectral analysis to validate extracted notes by examining:
- Fundamental frequency energy
- Overtone structure
- Missing fundamentals
- Harmonic-only activations

This is lightweight DSP-based validation, not heavy ML.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np


@dataclass
class SpectralValidation:
    """Spectral validation result for a note."""
    pitch: int
    fundamental_energy: float  # Energy at fundamental frequency (0-1)
    harmonic_energy: float     # Energy in harmonics (0-1)
    total_energy: float        # Total energy in note band (0-1)
    harmonic_ratio: float      # Ratio of harmonics to fundamental
    missing_fundamental: bool  # True if harmonics present but no fundamental
    spectral_support: float    # Overall spectral support score (0-1)

    # Detailed harmonic analysis
    harmonic_energies: Dict[int, float] = field(default_factory=dict)

    @property
    def is_likely_artifact(self) -> bool:
        """Check if note is likely a spectral artifact."""
        # Missing fundamental with strong harmonics = artifact
        if self.missing_fundamental and self.harmonic_energy > 0.3:
            return True
        # Very weak fundamental relative to harmonics
        if self.harmonic_ratio > 5.0 and self.fundamental_energy < 0.1:
            return True
        return False


class SpectralValidator:
    """Validates notes using spectral analysis.

    Uses STFT-based analysis to check:
    1. Is there energy at the fundamental frequency?
    2. Is the overtone structure consistent with a real note?
    3. Is this a "phantom" note from harmonics of another pitch?
    """

    def __init__(
        self,
        n_fft: int = 4096,
        hop_length: int = 512,
        fundamental_bandwidth_cents: float = 100.0,
        harmonic_bandwidth_cents: float = 50.0,
        n_harmonics: int = 6,
    ):
        """Initialize validator.

        Args:
            n_fft: FFT size
            hop_length: Hop length for STFT
            fundamental_bandwidth_cents: Bandwidth around fundamental (cents)
            harmonic_bandwidth_cents: Bandwidth around harmonics (cents)
            n_harmonics: Number of harmonics to analyze
        """
        self.n_fft = n_fft
        self.hop_length = hop_length
        self.fundamental_bandwidth_cents = fundamental_bandwidth_cents
        self.harmonic_bandwidth_cents = harmonic_bandwidth_cents
        self.n_harmonics = n_harmonics

        # Cache
        self._stft_cache: Dict[int, Tuple[np.ndarray, np.ndarray, np.ndarray]] = {}

    def validate_note(
        self,
        pitch: int,
        start_time: float,
        end_time: float,
        audio: np.ndarray,
        sr: int,
    ) -> SpectralValidation:
        """Validate a single note using spectral analysis.

        Args:
            pitch: MIDI pitch
            start_time: Note start time in seconds
            end_time: Note end time in seconds
            audio: Audio signal
            sr: Sample rate

        Returns:
            SpectralValidation with analysis results
        """
        # Get or compute STFT
        stft, freqs, times = self._get_stft(audio, sr)

        # Convert pitch to frequency
        fundamental_freq = self._midi_to_hz(pitch)

        # Find time range for this note
        time_mask = (times >= start_time) & (times <= end_time)
        if not np.any(time_mask):
            return SpectralValidation(
                pitch=pitch,
                fundamental_energy=0.0,
                harmonic_energy=0.0,
                total_energy=0.0,
                harmonic_ratio=0.0,
                missing_fundamental=False,
                spectral_support=0.0,
            )

        # Get magnitude spectrum for note duration
        note_stft = np.abs(stft[:, time_mask])

        # Measure fundamental energy
        fundamental_energy = self._measure_band_energy(
            note_stft, freqs, fundamental_freq, self.fundamental_bandwidth_cents
        )

        # Measure harmonic energies
        harmonic_energies = {}
        total_harmonic_energy = 0.0

        for h in range(2, self.n_harmonics + 2):
            harmonic_freq = fundamental_freq * h
            if harmonic_freq > freqs[-1]:
                break

            h_energy = self._measure_band_energy(
                note_stft, freqs, harmonic_freq, self.harmonic_bandwidth_cents
            )
            harmonic_energies[h] = h_energy
            total_harmonic_energy += h_energy

        # Compute total energy in note band
        note_band_low = fundamental_freq * 0.9
        note_band_high = fundamental_freq * (self.n_harmonics + 1)
        freq_mask = (freqs >= note_band_low) & (freqs <= note_band_high)
        total_energy = float(np.mean(note_stft[freq_mask])) if np.any(freq_mask) else 0.0

        # Normalize energies
        max_energy = max(fundamental_energy, total_harmonic_energy, 0.001)
        fundamental_energy_norm = fundamental_energy / max_energy
        harmonic_energy_norm = total_harmonic_energy / max_energy

        # Compute harmonic ratio
        harmonic_ratio = total_harmonic_energy / fundamental_energy if fundamental_energy > 0.001 else 10.0

        # Check for missing fundamental
        missing_fundamental = (
            fundamental_energy < 0.1 * total_harmonic_energy and
            total_harmonic_energy > 0.1
        )

        # Compute overall spectral support score
        # High fundamental + reasonable harmonic structure = good support
        spectral_support = self._compute_spectral_support(
            fundamental_energy_norm,
            harmonic_energy_norm,
            harmonic_ratio,
            missing_fundamental,
        )

        return SpectralValidation(
            pitch=pitch,
            fundamental_energy=fundamental_energy_norm,
            harmonic_energy=harmonic_energy_norm,
            total_energy=total_energy / max_energy if max_energy > 0 else 0,
            harmonic_ratio=harmonic_ratio,
            missing_fundamental=missing_fundamental,
            spectral_support=spectral_support,
            harmonic_energies=harmonic_energies,
        )

    def validate_notes(
        self,
        notes: List[Tuple[int, float, float]],  # [(pitch, start, end), ...]
        audio: np.ndarray,
        sr: int,
    ) -> List[SpectralValidation]:
        """Validate multiple notes efficiently.

        Args:
            notes: List of (pitch, start_time, end_time) tuples
            audio: Audio signal
            sr: Sample rate

        Returns:
            List of SpectralValidation objects
        """
        # Pre-compute STFT once
        self._get_stft(audio, sr)

        return [
            self.validate_note(pitch, start, end, audio, sr)
            for pitch, start, end in notes
        ]

    def find_competing_fundamentals(
        self,
        pitch: int,
        start_time: float,
        end_time: float,
        audio: np.ndarray,
        sr: int,
        search_range_semitones: int = 24,
    ) -> List[Tuple[int, float]]:
        """Find other pitches that might explain this note as a harmonic.

        If this note at pitch P could be explained as a harmonic of
        another pitch Q, this function finds those Q candidates.

        Args:
            pitch: MIDI pitch to investigate
            start_time: Note start time
            end_time: Note end time
            audio: Audio signal
            sr: Sample rate
            search_range_semitones: How far below to search

        Returns:
            List of (competing_pitch, energy_ratio) tuples
        """
        competitors = []
        note_freq = self._midi_to_hz(pitch)

        # Get STFT
        stft, freqs, times = self._get_stft(audio, sr)
        time_mask = (times >= start_time) & (times <= end_time)

        if not np.any(time_mask):
            return competitors

        note_stft = np.abs(stft[:, time_mask])

        # Get energy at this note's pitch
        this_energy = self._measure_band_energy(
            note_stft, freqs, note_freq, self.fundamental_bandwidth_cents
        )

        # Check if this note could be a harmonic of a lower note
        for semitones_below in range(12, search_range_semitones + 1, 12):
            lower_pitch = pitch - semitones_below
            if lower_pitch < 21:  # Below A0
                continue

            lower_freq = self._midi_to_hz(lower_pitch)

            # Check if this note's freq is a harmonic of lower_freq
            harmonic_num = round(note_freq / lower_freq)
            if abs(note_freq - lower_freq * harmonic_num) > note_freq * 0.02:
                continue  # Not close enough to a harmonic

            # Measure energy at the potential fundamental
            lower_energy = self._measure_band_energy(
                note_stft, freqs, lower_freq, self.fundamental_bandwidth_cents
            )

            if lower_energy > this_energy * 0.5:
                # Lower note has significant energy
                energy_ratio = lower_energy / max(this_energy, 0.001)
                competitors.append((lower_pitch, energy_ratio))

        return sorted(competitors, key=lambda x: -x[1])

    def _get_stft(
        self,
        audio: np.ndarray,
        sr: int,
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Get or compute STFT."""
        cache_key = id(audio)

        if cache_key not in self._stft_cache:
            import librosa
            stft = librosa.stft(audio, n_fft=self.n_fft, hop_length=self.hop_length)
            freqs = librosa.fft_frequencies(sr=sr, n_fft=self.n_fft)
            times = librosa.times_like(stft, sr=sr, hop_length=self.hop_length)
            self._stft_cache[cache_key] = (stft, freqs, times)

        return self._stft_cache[cache_key]

    def _midi_to_hz(self, midi: int) -> float:
        """Convert MIDI note to frequency."""
        return 440.0 * (2 ** ((midi - 69) / 12))

    def _cents_to_ratio(self, cents: float) -> float:
        """Convert cents to frequency ratio."""
        return 2 ** (cents / 1200)

    def _measure_band_energy(
        self,
        stft: np.ndarray,
        freqs: np.ndarray,
        center_freq: float,
        bandwidth_cents: float,
    ) -> float:
        """Measure energy in a frequency band."""
        ratio = self._cents_to_ratio(bandwidth_cents)
        low_freq = center_freq / ratio
        high_freq = center_freq * ratio

        mask = (freqs >= low_freq) & (freqs <= high_freq)
        if not np.any(mask):
            return 0.0

        return float(np.mean(stft[mask]))

    def _compute_spectral_support(
        self,
        fundamental_energy: float,
        harmonic_energy: float,
        harmonic_ratio: float,
        missing_fundamental: bool,
    ) -> float:
        """Compute overall spectral support score."""
        # Start with fundamental energy
        score = fundamental_energy * 0.6

        # Add harmonic contribution (but not too much)
        score += min(harmonic_energy * 0.3, 0.3)

        # Penalty for suspicious harmonic ratios
        if harmonic_ratio > 3.0:
            score *= (3.0 / harmonic_ratio)

        # Strong penalty for missing fundamental
        if missing_fundamental:
            score *= 0.3

        return min(1.0, max(0.0, score))

    def clear_cache(self):
        """Clear STFT cache."""
        self._stft_cache.clear()


# Convenience function
def validate_note_spectral(
    pitch: int,
    start_time: float,
    end_time: float,
    audio: np.ndarray,
    sr: int,
) -> SpectralValidation:
    """Validate a single note using spectral analysis."""
    validator = SpectralValidator()
    return validator.validate_note(pitch, start_time, end_time, audio, sr)
