"""
HarmonicClusterAnalyzer - Multi-pitch extraction for polyphonic content.

This module extracts multiple simultaneous pitches from audio using
onset-based harmonic cluster analysis. It excels at:
- Strummed guitar chords
- Organ chords
- Dense polyphonic textures
- Any content where monophonic detectors (CREPE, pYIN) fail

Key insight: The fundamentals of all notes in a chord ARE present
and detectable - it's an extraction problem, not a detection problem.
Monophonic detectors pick one pitch when multiple are clearly visible
in the CQT spectrum.

Algorithm:
1. Apply light high-pass filter to reduce low-frequency masking
2. Compute CQT for per-pitch energy detection
3. Detect onsets with sensitive threshold
4. At each onset, extract ALL pitches above energy threshold
5. Use peak detection to avoid CQT spillover artifacts
6. Limit to top 6 pitches per cluster (typical chord voicing)

Performance (BabySlakh lead benchmark):
- HCA alone: Dramatically improves dense polyphonic content
- Hybrid router: +8.2% mean F1 over torchcrepe baseline
"""

import logging
from typing import List, Tuple, Optional
from dataclasses import dataclass

import numpy as np
from scipy import signal

logger = logging.getLogger(__name__)


@dataclass
class HCANote:
    """A note extracted by HCA."""
    pitch: int
    start: float
    end: float
    velocity: int
    confidence: float = 1.0


class HarmonicClusterAnalyzer:
    """
    Multi-pitch extraction using harmonic cluster analysis.

    Uses onset-based detection with CQT energy extraction to find
    all simultaneous pitches in polyphonic content.
    """

    def __init__(
        self,
        sr: int = 22050,
        hop_length: int = 512,
        min_note: int = 40,  # E2
        max_note: int = 90,  # F#6
        highpass_freq: float = 80.0,  # Light high-pass to reduce rumble
        energy_threshold_ratio: float = 0.02,  # Min energy as ratio of global max
        relative_threshold: float = 0.12,  # Min energy as ratio of local max
        max_pitches_per_cluster: int = 6,  # Typical chord voicing limit
    ):
        """
        Initialize HCA.

        Args:
            sr: Sample rate
            hop_length: Hop length for CQT
            min_note: Minimum MIDI note to extract
            max_note: Maximum MIDI note to extract
            highpass_freq: High-pass filter cutoff (Hz), 0 to disable
            energy_threshold_ratio: Absolute energy threshold as ratio of global max
            relative_threshold: Relative energy threshold as ratio of local max
            max_pitches_per_cluster: Maximum pitches to extract per onset cluster
        """
        self.sr = sr
        self.hop_length = hop_length
        self.min_note = min_note
        self.max_note = max_note
        self.highpass_freq = highpass_freq
        self.energy_threshold_ratio = energy_threshold_ratio
        self.relative_threshold = relative_threshold
        self.max_pitches_per_cluster = max_pitches_per_cluster

    def extract(self, audio: np.ndarray) -> List[HCANote]:
        """
        Extract multi-pitch notes from audio.

        Args:
            audio: Mono audio signal

        Returns:
            List of HCANote objects
        """
        import librosa

        # Ensure mono
        if audio.ndim > 1:
            audio = np.mean(audio, axis=0)

        # Apply high-pass filter if configured
        if self.highpass_freq > 0:
            nyquist = self.sr / 2
            normalized_freq = self.highpass_freq / nyquist
            if normalized_freq < 1.0:  # Must be below Nyquist
                b, a = signal.butter(2, normalized_freq, btype='high')
                audio = signal.filtfilt(b, a, audio)

        # Compute CQT (Constant-Q Transform)
        # This gives us per-pitch energy detection
        cqt = np.abs(librosa.cqt(
            y=audio,
            sr=self.sr,
            hop_length=self.hop_length,
            fmin=librosa.note_to_hz('C2'),  # MIDI 36
            n_bins=60,  # 5 octaves
            bins_per_octave=12,
        ))

        # Detect onsets with sensitive threshold
        onset_env = librosa.onset.onset_strength(
            y=audio, sr=self.sr, hop_length=self.hop_length
        )
        onset_frames = librosa.onset.onset_detect(
            onset_envelope=onset_env,
            sr=self.sr,
            hop_length=self.hop_length,
            backtrack=True,
            units='frames',
            delta=0.05,  # Sensitive
        )

        frame_time = self.hop_length / self.sr
        notes = []

        # Adaptive threshold based on content energy
        global_max = np.max(cqt)
        energy_threshold = max(0.005, global_max * self.energy_threshold_ratio)

        for i, onset_frame in enumerate(onset_frames):
            if onset_frame >= cqt.shape[1]:
                continue

            onset_time = onset_frame * frame_time

            # Determine end time (next onset or default duration)
            if i + 1 < len(onset_frames):
                end_frame = onset_frames[i + 1]
            else:
                end_frame = min(onset_frame + int(0.4 / frame_time), cqt.shape[1] - 1)
            end_time = end_frame * frame_time

            # Average CQT over a few frames for stability
            avg_end = min(onset_frame + 3, cqt.shape[1])
            cqt_slice = np.mean(cqt[:, onset_frame:avg_end], axis=1)

            # Skip if below energy threshold
            local_max = np.max(cqt_slice)
            if local_max < energy_threshold:
                continue

            # Find all active pitches
            cluster_pitches = self._find_pitches_in_slice(
                cqt_slice, energy_threshold, local_max
            )

            # Create notes for each pitch in cluster
            for midi_note, energy in cluster_pitches:
                velocity = int(min(127, max(1, (energy / local_max) * 100)))
                confidence = float(energy / local_max)
                notes.append(HCANote(
                    pitch=midi_note,
                    start=onset_time,
                    end=end_time,
                    velocity=velocity,
                    confidence=confidence,
                ))

        logger.info(f"HCA extracted {len(notes)} notes from {len(onset_frames)} onsets")
        return notes

    def _find_pitches_in_slice(
        self,
        cqt_slice: np.ndarray,
        energy_threshold: float,
        local_max: float,
    ) -> List[Tuple[int, float]]:
        """
        Find active pitches in a CQT slice.

        Uses peak detection to avoid CQT spillover artifacts.

        Args:
            cqt_slice: CQT energies for one time slice
            energy_threshold: Absolute energy threshold
            local_max: Maximum energy in this slice

        Returns:
            List of (midi_note, energy) tuples
        """
        pitches = []

        for bin_idx, energy in enumerate(cqt_slice):
            midi_note = 36 + bin_idx  # C2 = MIDI 36

            # Skip out of range
            if midi_note < self.min_note or midi_note > self.max_note:
                continue

            # Skip below absolute threshold
            if energy < energy_threshold:
                continue

            # Skip below relative threshold
            if energy < local_max * self.relative_threshold:
                continue

            # Peak detection - avoid spillover from adjacent bins
            is_peak = True
            if bin_idx > 0 and cqt_slice[bin_idx - 1] > energy * 0.92:
                is_peak = False
            if bin_idx < len(cqt_slice) - 1 and cqt_slice[bin_idx + 1] > energy * 0.92:
                is_peak = False

            if is_peak:
                pitches.append((midi_note, float(energy)))

        # Limit to top N pitches by energy
        if len(pitches) > self.max_pitches_per_cluster:
            pitches = sorted(pitches, key=lambda x: x[1], reverse=True)
            pitches = pitches[:self.max_pitches_per_cluster]

        return pitches


def estimate_harmonic_ratio(audio: np.ndarray, sr: int) -> float:
    """
    Estimate harmonic ratio - key signal for routing decision.

    High harmonic ratio (>0.8) indicates clean pitched content
    that works well with monophonic detectors like CREPE/pYIN.

    Low harmonic ratio (<0.75) indicates complex content that
    may benefit from HCA.

    Args:
        audio: Mono audio signal
        sr: Sample rate

    Returns:
        Harmonic ratio (0-1)
    """
    import librosa

    if audio.ndim > 1:
        audio = np.mean(audio, axis=0)

    y_harm, y_perc = librosa.effects.hpss(audio)
    harm_energy = np.sum(y_harm**2)
    total_energy = np.sum(audio**2) + 1e-10
    return harm_energy / total_energy


def extract_with_hca(
    audio_path: str,
    sr: int = 22050,
    duration: float = 30.0,
) -> Tuple[List[HCANote], float]:
    """
    Extract notes from audio file using HCA.

    Args:
        audio_path: Path to audio file
        sr: Sample rate to use
        duration: Maximum duration to process

    Returns:
        Tuple of (notes, tempo)
    """
    import librosa

    audio, sr = librosa.load(str(audio_path), sr=sr, duration=duration)

    analyzer = HarmonicClusterAnalyzer(sr=sr)
    notes = analyzer.extract(audio)

    # Estimate tempo from note onsets
    if len(notes) < 2:
        tempo = 120.0
    else:
        onsets = sorted([n.start for n in notes])
        iois = np.diff(onsets)
        iois = iois[(iois > 0.1) & (iois < 2.0)]
        if len(iois) > 0:
            tempo = 60.0 / (np.median(iois) * 2)
            tempo = float(np.clip(tempo, 60, 200))
        else:
            tempo = 120.0

    return notes, tempo


def should_use_hca(audio: np.ndarray, sr: int) -> Tuple[bool, str]:
    """
    Determine if HCA should be used for this audio.

    Based on harmonic ratio analysis from extensive benchmarking.

    Args:
        audio: Mono audio signal
        sr: Sample rate

    Returns:
        Tuple of (should_use_hca, reason)
    """
    harm_ratio = estimate_harmonic_ratio(audio, sr)

    if harm_ratio > 0.78:
        return False, f"torchcrepe (harm_ratio={harm_ratio:.2f} > 0.78, clean pitched)"
    elif harm_ratio < 0.75:
        return True, f"HCA (harm_ratio={harm_ratio:.2f} < 0.75, complex polyphonic)"
    else:
        # Edge case - could go either way
        # Default to torchcrepe for slightly cleaner signal
        return False, f"torchcrepe (harm_ratio={harm_ratio:.2f} in edge zone)"
