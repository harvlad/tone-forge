"""Ensemble pitch detection for improved accuracy.

Combines multiple pitch detectors with intelligent arbitration:
- basic-pitch: Polyphonic, good for chords and pads
- CREPE: Monophonic precision, best for bass and leads
- pYIN: Stable pitch tracking for sustained notes
- Spectral peaks: Harmonic analysis for verification

The ensemble approach reduces octave errors and improves accuracy
for difficult material like bass and reverb-heavy synths.
"""
from __future__ import annotations

import os
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple, Any

import numpy as np

# Suppress ONNX Runtime verbose logging
os.environ.setdefault("ORT_LOGGING_LEVEL", "3")
os.environ.setdefault("ONNX_LOG_LEVEL", "3")

try:
    import onnxruntime as ort
    ort.set_default_logger_severity(3)  # ERROR only
except ImportError:
    pass

# Patch basic-pitch to remove debug prints in CoreML inference
# Must be done before basic_pitch is imported
from tone_forge.midi import basic_pitch_patch  # noqa: F401

logger = logging.getLogger(__name__)


class DetectorType(str, Enum):
    """Available pitch detector types."""
    BASIC_PITCH = "basic-pitch"
    CREPE = "crepe"
    PYIN = "pyin"
    SPECTRAL = "spectral"
    HCA = "hca"  # HarmonicClusterAnalyzer for polyphonic content


@dataclass
class DetectedNote:
    """A note detected by a single detector."""
    pitch: int  # MIDI pitch
    start: float  # Start time in seconds
    end: float  # End time in seconds
    velocity: int  # MIDI velocity 0-127
    confidence: float  # Detection confidence 0-1
    detector: DetectorType  # Which detector found this

    # Optional detailed metrics
    pitch_stability: float = 1.0  # How stable the pitch is (0-1)
    harmonic_ratio: float = 1.0  # Ratio of harmonic to noise energy

    def to_dict(self) -> dict:
        return {
            "pitch": self.pitch,
            "start": self.start,
            "end": self.end,
            "velocity": self.velocity,
            "confidence": self.confidence,
            "detector": self.detector.value,
            "pitch_stability": self.pitch_stability,
            "harmonic_ratio": self.harmonic_ratio,
        }


@dataclass
class EnsembleNote:
    """A note produced by ensemble arbitration."""
    pitch: int
    start: float
    end: float
    velocity: int
    confidence: float

    # Provenance tracking
    detector_contributions: Dict[str, float] = field(default_factory=dict)
    agreement_score: float = 0.0  # How many detectors agreed
    octave_correction_applied: bool = False
    original_pitch: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "pitch": self.pitch,
            "start": self.start,
            "end": self.end,
            "velocity": self.velocity,
            "confidence": self.confidence,
            "detector_contributions": self.detector_contributions,
            "agreement_score": self.agreement_score,
            "octave_correction_applied": self.octave_correction_applied,
            "original_pitch": self.original_pitch,
        }


@dataclass
class EnsembleResult:
    """Result from ensemble pitch extraction."""
    notes: List[EnsembleNote]
    tempo: float
    overall_confidence: float
    detector_stats: Dict[str, Dict[str, Any]]
    arbitration_stats: Dict[str, Any]
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "notes": [n.to_dict() for n in self.notes],
            "tempo": self.tempo,
            "overall_confidence": self.overall_confidence,
            "detector_stats": self.detector_stats,
            "arbitration_stats": self.arbitration_stats,
            "warnings": self.warnings,
        }


class PitchDetectorWrapper:
    """Base wrapper for pitch detectors with common interface."""

    def __init__(self, detector_type: DetectorType):
        self.detector_type = detector_type
        self.available = self._check_availability()

    def _check_availability(self) -> bool:
        """Check if detector is available."""
        raise NotImplementedError

    def detect(
        self,
        audio: np.ndarray,
        sr: int,
        **kwargs,
    ) -> List[DetectedNote]:
        """Detect notes in audio."""
        raise NotImplementedError


class BasicPitchDetector(PitchDetectorWrapper):
    """Wrapper for basic-pitch polyphonic detector."""

    def __init__(self):
        super().__init__(DetectorType.BASIC_PITCH)

    def _check_availability(self) -> bool:
        try:
            import basic_pitch
            return True
        except ImportError:
            return False

    def detect(
        self,
        audio: np.ndarray,
        sr: int,
        onset_threshold: float = 0.5,
        frame_threshold: float = 0.4,
        min_note_ms: float = 50.0,
        **kwargs,
    ) -> List[DetectedNote]:
        if not self.available:
            return []

        try:
            from basic_pitch.inference import predict
            from basic_pitch import ICASSP_2022_MODEL_PATH
            import tempfile
            import soundfile as sf

            # Resample if needed
            target_sr = 22050
            if sr != target_sr:
                import librosa
                audio = librosa.resample(audio, orig_sr=sr, target_sr=target_sr)

            # Write to temp file - basic-pitch needs a file path
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                sf.write(tmp.name, audio, target_sr)
                tmp_path = tmp.name

            try:
                # Run basic-pitch with file path
                model_output, midi_data, note_events = predict(
                    tmp_path,
                    ICASSP_2022_MODEL_PATH,
                    onset_threshold=onset_threshold,
                    frame_threshold=frame_threshold,
                    minimum_note_length=min_note_ms,
                )
            finally:
                # Clean up temp file
                import os
                try:
                    os.unlink(tmp_path)
                except:
                    pass

            # Convert to DetectedNote
            # basic_pitch returns: (start_time, end_time, pitch, amplitude, bends)
            # bends is a list, not confidence - use amplitude as proxy for confidence
            notes = []
            for note in note_events:
                start_time, end_time, pitch, amplitude, _bends = note
                # amplitude is 0-1, use as both velocity and confidence
                notes.append(DetectedNote(
                    pitch=int(pitch),
                    start=float(start_time),
                    end=float(end_time),
                    velocity=int(float(amplitude) * 127),
                    confidence=float(amplitude),
                    detector=self.detector_type,
                ))

            return notes

        except Exception as e:
            logger.error(f"basic-pitch detection failed: {e}")
            return []

    def detect_with_posteriors(
        self,
        audio: np.ndarray,
        sr: int,
        onset_threshold: float = 0.5,
        frame_threshold: float = 0.4,
        min_note_ms: float = 50.0,
        **kwargs,
    ) -> Tuple[List[DetectedNote], Optional[Dict[str, np.ndarray]]]:
        """
        Detect notes AND return raw frame-wise posteriors.

        The posteriors contain frame-by-frame pitch probabilities that can be
        used for segment-level confidence decisions in hybrid detection.

        Returns:
            Tuple of (notes, posteriors_dict) where posteriors_dict contains:
            - 'note': (frames, 88) - per-pitch activation probability
            - 'onset': (frames, 88) - per-pitch onset probability
            - 'contour': (frames, 360) - pitch contour posteriors
            Returns ([], None) on failure.
        """
        if not self.available:
            return [], None

        try:
            from basic_pitch.inference import predict
            from basic_pitch import ICASSP_2022_MODEL_PATH
            import tempfile
            import soundfile as sf

            # Resample if needed
            target_sr = 22050
            if sr != target_sr:
                import librosa
                audio = librosa.resample(audio, orig_sr=sr, target_sr=target_sr)

            # Write to temp file - basic-pitch needs a file path
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                sf.write(tmp.name, audio, target_sr)
                tmp_path = tmp.name

            try:
                # Run basic-pitch with file path
                model_output, midi_data, note_events = predict(
                    tmp_path,
                    ICASSP_2022_MODEL_PATH,
                    onset_threshold=onset_threshold,
                    frame_threshold=frame_threshold,
                    minimum_note_length=min_note_ms,
                )
            finally:
                # Clean up temp file
                import os
                try:
                    os.unlink(tmp_path)
                except:
                    pass

            # Convert to DetectedNote
            notes = []
            for note in note_events:
                start_time, end_time, pitch, amplitude, _bends = note
                notes.append(DetectedNote(
                    pitch=int(pitch),
                    start=float(start_time),
                    end=float(end_time),
                    velocity=int(float(amplitude) * 127),
                    confidence=float(amplitude),
                    detector=self.detector_type,
                ))

            # Extract posteriors from model_output
            # model_output is a dict with keys: 'note', 'onset', 'contour'
            # Each is a numpy array with shape (frames, pitches)
            posteriors = {
                'note': model_output.get('note'),      # (frames, 88) A0-C8
                'onset': model_output.get('onset'),    # (frames, 88)
                'contour': model_output.get('contour'),  # (frames, 360)
                'frame_rate': 22050 / 256,  # basic_pitch uses 256 hop at 22050
            }

            logger.debug(f"basic-pitch posteriors: note shape={posteriors['note'].shape if posteriors['note'] is not None else None}")

            return notes, posteriors

        except Exception as e:
            logger.error(f"basic-pitch detection with posteriors failed: {e}")
            return [], None


class CrepeDetector(PitchDetectorWrapper):
    """Wrapper for CREPE monophonic pitch detector using torchcrepe (GPU)."""

    def __init__(self):
        super().__init__(DetectorType.CREPE)

    def _check_availability(self) -> bool:
        try:
            import torchcrepe
            return True
        except ImportError:
            return False

    def detect(
        self,
        audio: np.ndarray,
        sr: int,
        model_capacity: str = "tiny",
        viterbi: bool = True,
        min_confidence: float = 0.5,
        **kwargs,
    ) -> List[DetectedNote]:
        if not self.available:
            return []

        try:
            import torchcrepe
            import torch

            # Check for MPS (Apple Silicon GPU)
            device = "mps" if torch.backends.mps.is_available() else "cpu"

            # CREPE expects specific sample rate
            target_sr = 16000
            if sr != target_sr:
                import librosa
                audio = librosa.resample(audio, orig_sr=sr, target_sr=target_sr)
                sr = target_sr

            # Convert to torch tensor
            audio_tensor = torch.from_numpy(audio).unsqueeze(0).float().to(device)

            # Run torchcrepe on GPU
            pitch, periodicity = torchcrepe.predict(
                audio_tensor,
                sr,
                hop_length=160,  # 10ms at 16kHz
                fmin=50,
                fmax=2000,
                model=model_capacity,
                decoder=torchcrepe.decode.viterbi if viterbi else torchcrepe.decode.argmax,
                return_periodicity=True,
                device=device,
                batch_size=2048,
            )

            # Convert to numpy
            frequency = pitch.squeeze().cpu().numpy()
            confidence = periodicity.squeeze().cpu().numpy()
            time = np.arange(len(frequency)) * 0.01  # 10ms hop

            # Convert F0 trajectory to notes
            notes = self._f0_to_notes(
                time, frequency, confidence,
                min_confidence=min_confidence,
            )

            logger.info(f"torchcrepe detected {len(notes)} notes on {device}")
            return notes

        except Exception as e:
            logger.error(f"CREPE detection failed: {e}")
            return []

    def _f0_to_notes(
        self,
        time: np.ndarray,
        frequency: np.ndarray,
        confidence: np.ndarray,
        min_confidence: float = 0.5,
        min_duration: float = 0.05,
    ) -> List[DetectedNote]:
        """Convert F0 trajectory to note events."""
        notes = []

        # Convert frequency to MIDI pitch
        midi_pitch = np.zeros_like(frequency)
        valid = frequency > 0
        midi_pitch[valid] = 12 * np.log2(frequency[valid] / 440.0) + 69

        # Segment into notes
        in_note = False
        note_start = 0
        note_pitch = 0
        note_confidence = []

        for i in range(len(time)):
            if confidence[i] >= min_confidence and frequency[i] > 0:
                if not in_note:
                    # Start new note
                    in_note = True
                    note_start = i
                    note_pitch = int(round(midi_pitch[i]))
                    note_confidence = [confidence[i]]
                else:
                    # Continue note - check for pitch change
                    current_pitch = int(round(midi_pitch[i]))
                    if abs(current_pitch - note_pitch) > 1:
                        # Pitch changed - end previous note
                        if time[i-1] - time[note_start] >= min_duration:
                            notes.append(DetectedNote(
                                pitch=note_pitch,
                                start=float(time[note_start]),
                                end=float(time[i-1]),
                                velocity=80,  # CREPE doesn't estimate velocity
                                confidence=float(np.mean(note_confidence)),
                                detector=self.detector_type,
                                pitch_stability=float(1.0 - np.std(midi_pitch[note_start:i]) / 12),
                            ))
                        # Start new note
                        note_start = i
                        note_pitch = current_pitch
                        note_confidence = [confidence[i]]
                    else:
                        note_confidence.append(confidence[i])
            else:
                if in_note:
                    # End note
                    if time[i-1] - time[note_start] >= min_duration:
                        notes.append(DetectedNote(
                            pitch=note_pitch,
                            start=float(time[note_start]),
                            end=float(time[i-1]),
                            velocity=80,
                            confidence=float(np.mean(note_confidence)),
                            detector=self.detector_type,
                            pitch_stability=float(1.0 - np.std(midi_pitch[note_start:i]) / 12) if i > note_start else 1.0,
                        ))
                    in_note = False

        # Handle final note
        if in_note and time[-1] - time[note_start] >= min_duration:
            notes.append(DetectedNote(
                pitch=note_pitch,
                start=float(time[note_start]),
                end=float(time[-1]),
                velocity=80,
                confidence=float(np.mean(note_confidence)),
                detector=self.detector_type,
            ))

        return notes


class PYinDetector(PitchDetectorWrapper):
    """Wrapper for pYIN pitch detector from librosa."""

    def __init__(self):
        super().__init__(DetectorType.PYIN)

    def _check_availability(self) -> bool:
        try:
            import librosa
            return True
        except ImportError:
            return False

    def detect(
        self,
        audio: np.ndarray,
        sr: int,
        fmin: float = 50.0,
        fmax: float = 2000.0,
        **kwargs,
    ) -> List[DetectedNote]:
        if not self.available:
            return []

        try:
            import librosa

            # Run pYIN
            f0, voiced_flag, voiced_probs = librosa.pyin(
                audio,
                fmin=fmin,
                fmax=fmax,
                sr=sr,
                frame_length=2048,
                hop_length=512,
            )

            # Convert to time
            times = librosa.frames_to_time(
                np.arange(len(f0)),
                sr=sr,
                hop_length=512,
            )

            # Convert to notes
            notes = self._f0_to_notes(times, f0, voiced_probs)

            return notes

        except Exception as e:
            logger.error(f"pYIN detection failed: {e}")
            return []

    def _f0_to_notes(
        self,
        time: np.ndarray,
        frequency: np.ndarray,
        confidence: np.ndarray,
        min_confidence: float = 0.5,
        min_duration: float = 0.05,
    ) -> List[DetectedNote]:
        """Convert F0 trajectory to note events."""
        notes = []

        # Handle NaN frequencies
        valid_freq = np.nan_to_num(frequency, nan=0.0)

        # Convert frequency to MIDI pitch
        midi_pitch = np.zeros_like(valid_freq)
        valid = valid_freq > 0
        midi_pitch[valid] = 12 * np.log2(valid_freq[valid] / 440.0) + 69

        # Segment into notes (similar to CREPE)
        in_note = False
        note_start = 0
        note_pitch = 0
        note_confidence = []

        for i in range(len(time)):
            conf = confidence[i] if confidence[i] is not None else 0

            if conf >= min_confidence and valid_freq[i] > 0:
                if not in_note:
                    in_note = True
                    note_start = i
                    note_pitch = int(round(midi_pitch[i]))
                    note_confidence = [conf]
                else:
                    current_pitch = int(round(midi_pitch[i]))
                    if abs(current_pitch - note_pitch) > 1:
                        if time[i-1] - time[note_start] >= min_duration:
                            notes.append(DetectedNote(
                                pitch=note_pitch,
                                start=float(time[note_start]),
                                end=float(time[i-1]),
                                velocity=80,
                                confidence=float(np.mean(note_confidence)),
                                detector=self.detector_type,
                            ))
                        note_start = i
                        note_pitch = current_pitch
                        note_confidence = [conf]
                    else:
                        note_confidence.append(conf)
            else:
                if in_note:
                    if time[i-1] - time[note_start] >= min_duration:
                        notes.append(DetectedNote(
                            pitch=note_pitch,
                            start=float(time[note_start]),
                            end=float(time[i-1]),
                            velocity=80,
                            confidence=float(np.mean(note_confidence)),
                            detector=self.detector_type,
                        ))
                    in_note = False

        if in_note and time[-1] - time[note_start] >= min_duration:
            notes.append(DetectedNote(
                pitch=note_pitch,
                start=float(time[note_start]),
                end=float(time[-1]),
                velocity=80,
                confidence=float(np.mean(note_confidence)),
                detector=self.detector_type,
            ))

        return notes


class SpectralPeakDetector(PitchDetectorWrapper):
    """Spectral peak detector for harmonic verification."""

    def __init__(self):
        super().__init__(DetectorType.SPECTRAL)

    def _check_availability(self) -> bool:
        try:
            import librosa
            return True
        except ImportError:
            return False

    def detect(
        self,
        audio: np.ndarray,
        sr: int,
        **kwargs,
    ) -> List[DetectedNote]:
        """Spectral peak detection - returns fundamental frequencies."""
        if not self.available:
            return []

        try:
            import librosa
            from scipy.signal import find_peaks

            # Compute STFT
            D = librosa.stft(audio, n_fft=4096, hop_length=512)
            S = np.abs(D)
            freqs = librosa.fft_frequencies(sr=sr, n_fft=4096)
            times = librosa.frames_to_time(np.arange(S.shape[1]), sr=sr, hop_length=512)

            notes = []

            # Find peaks in each frame
            for frame_idx in range(S.shape[1]):
                frame = S[:, frame_idx]

                # Find peaks
                peaks, properties = find_peaks(frame, height=np.max(frame) * 0.1, distance=5)

                if len(peaks) == 0:
                    continue

                # Get fundamental (lowest significant peak)
                peak_freqs = freqs[peaks]
                peak_heights = frame[peaks]

                # Filter to reasonable range
                valid_mask = (peak_freqs >= 50) & (peak_freqs <= 2000)
                if not np.any(valid_mask):
                    continue

                peak_freqs = peak_freqs[valid_mask]
                peak_heights = peak_heights[valid_mask]

                # Find fundamental - likely lowest strong peak
                if len(peak_freqs) > 0:
                    # Sort by frequency and find lowest with sufficient energy
                    sorted_idx = np.argsort(peak_freqs)
                    for idx in sorted_idx:
                        if peak_heights[idx] >= np.max(peak_heights) * 0.3:
                            f0 = peak_freqs[idx]
                            midi_pitch = int(round(12 * np.log2(f0 / 440.0) + 69))

                            # Check for harmonic series
                            harmonic_ratio = self._compute_harmonic_ratio(
                                peak_freqs, peak_heights, f0
                            )

                            notes.append(DetectedNote(
                                pitch=midi_pitch,
                                start=float(times[frame_idx]),
                                end=float(times[frame_idx] + 512/sr),
                                velocity=int(min(127, peak_heights[idx] / np.max(S) * 127)),
                                confidence=float(min(1.0, harmonic_ratio)),
                                detector=self.detector_type,
                                harmonic_ratio=float(harmonic_ratio),
                            ))
                            break

            # Merge consecutive frames into notes
            return self._merge_frame_detections(notes)

        except Exception as e:
            logger.error(f"Spectral peak detection failed: {e}")
            return []

    def _compute_harmonic_ratio(
        self,
        peak_freqs: np.ndarray,
        peak_heights: np.ndarray,
        f0: float,
    ) -> float:
        """Compute ratio of harmonic to total energy."""
        harmonic_energy = 0.0
        total_energy = np.sum(peak_heights)

        for h in range(1, 6):  # Check first 5 harmonics
            expected_freq = f0 * h
            # Find closest peak
            if len(peak_freqs) > 0:
                closest_idx = np.argmin(np.abs(peak_freqs - expected_freq))
                if np.abs(peak_freqs[closest_idx] - expected_freq) < expected_freq * 0.03:
                    harmonic_energy += peak_heights[closest_idx]

        if total_energy > 0:
            return harmonic_energy / total_energy
        return 0.0

    def _merge_frame_detections(
        self,
        frame_notes: List[DetectedNote],
        max_gap: float = 0.05,
    ) -> List[DetectedNote]:
        """Merge consecutive frame detections into sustained notes."""
        if len(frame_notes) == 0:
            return []

        # Sort by start time
        frame_notes = sorted(frame_notes, key=lambda n: n.start)

        merged = []
        current = frame_notes[0]

        for note in frame_notes[1:]:
            # Check if same pitch and consecutive
            if (note.pitch == current.pitch and
                note.start - current.end <= max_gap):
                # Extend current note
                current = DetectedNote(
                    pitch=current.pitch,
                    start=current.start,
                    end=note.end,
                    velocity=max(current.velocity, note.velocity),
                    confidence=(current.confidence + note.confidence) / 2,
                    detector=current.detector,
                    harmonic_ratio=(current.harmonic_ratio + note.harmonic_ratio) / 2,
                )
            else:
                # Save current and start new
                if current.end - current.start >= 0.05:  # Min duration
                    merged.append(current)
                current = note

        # Don't forget last note
        if current.end - current.start >= 0.05:
            merged.append(current)

        return merged


class HCADetector(PitchDetectorWrapper):
    """Wrapper for HarmonicClusterAnalyzer polyphonic detector.

    Uses CQT-based multi-pitch detection for polyphonic content
    like strummed chords, arpeggios, and pads.
    """

    def __init__(self):
        super().__init__(DetectorType.HCA)

    def _check_availability(self) -> bool:
        try:
            from .harmonic_cluster_analyzer import HarmonicClusterAnalyzer
            return True
        except ImportError:
            return False

    def detect(
        self,
        audio: np.ndarray,
        sr: int,
        min_note: int = 40,
        max_note: int = 90,
        **kwargs,
    ) -> List[DetectedNote]:
        if not self.available:
            return []

        try:
            from .harmonic_cluster_analyzer import HarmonicClusterAnalyzer

            # Run HCA
            analyzer = HarmonicClusterAnalyzer(
                sr=sr,
                min_note=min_note,
                max_note=max_note,
            )
            hca_notes = analyzer.extract(audio)

            # Convert to DetectedNote format
            notes = []
            for n in hca_notes:
                notes.append(DetectedNote(
                    pitch=n.pitch,
                    start=n.start,
                    end=n.end,
                    velocity=n.velocity,
                    confidence=0.7,  # HCA doesn't provide confidence, use default
                    detector=self.detector_type,
                ))

            logger.info(f"HCA detected {len(notes)} notes (polyphonic)")
            return notes

        except Exception as e:
            logger.error(f"HCA detection failed: {e}")
            return []


class PitchEnsembleExtractor:
    """Ensemble pitch extractor combining multiple detectors.

    Provides intelligent arbitration to resolve conflicts between
    detectors and reduce octave errors.
    """

    def __init__(
        self,
        use_basic_pitch: bool = True,
        use_crepe: bool = True,
        use_pyin: bool = True,
        use_spectral: bool = True,
        use_hca: bool = True,
    ):
        """Initialize ensemble extractor.

        Args:
            use_basic_pitch: Use basic-pitch detector
            use_crepe: Use CREPE detector
            use_pyin: Use pYIN detector
            use_spectral: Use spectral peak detector
            use_hca: Use HarmonicClusterAnalyzer for polyphonic content
        """
        self.detectors: Dict[DetectorType, PitchDetectorWrapper] = {}

        if use_basic_pitch:
            bp = BasicPitchDetector()
            if bp.available:
                self.detectors[DetectorType.BASIC_PITCH] = bp

        if use_crepe:
            crepe = CrepeDetector()
            if crepe.available:
                self.detectors[DetectorType.CREPE] = crepe

        if use_pyin:
            pyin = PYinDetector()
            if pyin.available:
                self.detectors[DetectorType.PYIN] = pyin

        if use_spectral:
            spec = SpectralPeakDetector()
            if spec.available:
                self.detectors[DetectorType.SPECTRAL] = spec

        if use_hca:
            hca = HCADetector()
            if hca.available:
                self.detectors[DetectorType.HCA] = hca

        logger.info(
            f"Ensemble extractor initialized with detectors: "
            f"{[d.value for d in self.detectors.keys()]}"
        )

    def extract(
        self,
        audio: np.ndarray,
        sr: int,
        stem_type: str = "other",
        genre: Optional[str] = None,
        polyphony_estimate: Optional[int] = None,
        **kwargs,
    ) -> EnsembleResult:
        """Extract pitch with ensemble of detectors.

        Args:
            audio: Audio signal (mono)
            sr: Sample rate
            stem_type: Type of stem for detector weighting
            genre: Genre hint
            polyphony_estimate: Estimated polyphony (1=mono, >4=dense)
            **kwargs: Additional detector-specific options

        Returns:
            EnsembleResult with arbitrated notes
        """
        if audio.ndim > 1:
            audio = np.mean(audio, axis=0)

        # Determine which detectors to prioritize based on content
        is_monophonic = self._estimate_polyphony(audio, sr) <= 1.5
        if polyphony_estimate is not None:
            is_monophonic = polyphony_estimate <= 1

        is_bass = stem_type.lower() in ("bass", "sub_bass", "mono_bass")
        is_lead = stem_type.lower() in ("lead", "vocals", "melody")

        # Use harmonic ratio to detect polyphonic content (strummed chords, pads)
        is_polyphonic = False
        harm_ratio = None
        try:
            from .harmonic_cluster_analyzer import estimate_harmonic_ratio
            harm_ratio = estimate_harmonic_ratio(audio, sr)
            # Low harmonic ratio (<0.75) indicates polyphonic content
            is_polyphonic = harm_ratio < 0.75
        except ImportError:
            pass

        # Run all available detectors
        detector_results: Dict[DetectorType, List[DetectedNote]] = {}
        detector_stats: Dict[str, Dict[str, Any]] = {}
        warnings = []

        for detector_type, detector in self.detectors.items():
            # Skip HCA for monophonic content (use CREPE/pYIN instead)
            if is_monophonic and detector_type == DetectorType.HCA:
                continue

            # Skip monophonic detectors (CREPE, pYIN) for clearly polyphonic content
            if is_polyphonic and detector_type in (DetectorType.CREPE, DetectorType.PYIN):
                continue

            # Skip polyphonic detectors for clearly monophonic content
            if is_monophonic and detector_type == DetectorType.BASIC_PITCH:
                # Still run basic-pitch but with lower weight
                pass

            # Skip monophonic detectors for dense polyphony
            if polyphony_estimate and polyphony_estimate > 4:
                if detector_type in (DetectorType.CREPE, DetectorType.PYIN):
                    continue

            try:
                notes = detector.detect(audio, sr, **kwargs)
                detector_results[detector_type] = notes

                detector_stats[detector_type.value] = {
                    "note_count": len(notes),
                    "avg_confidence": np.mean([n.confidence for n in notes]) if notes else 0,
                    "pitch_range": (
                        min(n.pitch for n in notes) if notes else 0,
                        max(n.pitch for n in notes) if notes else 0,
                    ),
                }

            except Exception as e:
                warnings.append(f"{detector_type.value} failed: {e}")

        # Log routing decision
        if harm_ratio is not None:
            logger.info(f"Ensemble extract: harm_ratio={harm_ratio:.2f}, is_polyphonic={is_polyphonic}, detectors_used={list(detector_results.keys())}")

        # Arbitrate results
        ensemble_notes, arbitration_stats = self._arbitrate(
            detector_results,
            is_bass=is_bass,
            is_monophonic=is_monophonic,
        )

        # Estimate tempo
        tempo = self._estimate_tempo(ensemble_notes)

        # Calculate overall confidence
        overall_confidence = self._calculate_overall_confidence(
            ensemble_notes, detector_stats
        )

        return EnsembleResult(
            notes=ensemble_notes,
            tempo=tempo,
            overall_confidence=overall_confidence,
            detector_stats=detector_stats,
            arbitration_stats=arbitration_stats,
            warnings=warnings,
        )

    def _estimate_polyphony(self, audio: np.ndarray, sr: int) -> float:
        """Estimate polyphony level from audio."""
        try:
            from ..polyphony_estimator import estimate_polyphony
            return estimate_polyphony(audio, sr)
        except ImportError:
            # Fallback: use spectral flatness
            import librosa
            flatness = librosa.feature.spectral_flatness(y=audio)
            avg_flatness = np.mean(flatness)
            # Higher flatness = more noise-like = likely more polyphony
            return 1 + avg_flatness * 6

    def _arbitrate(
        self,
        detector_results: Dict[DetectorType, List[DetectedNote]],
        is_bass: bool = False,
        is_monophonic: bool = False,
    ) -> Tuple[List[EnsembleNote], Dict[str, Any]]:
        """Arbitrate between detector results.

        Uses UNION_MERGE strategy for maximum recall, then applies
        post-processing based on content type.
        """
        from .detector_arbitration import (
            DetectorArbitrator,
            ArbitrationStrategy,
        )

        # Use CONFIDENCE_WEIGHTED for balanced precision/recall
        # Filter out low-confidence notes to reduce false positives
        arbitrator = DetectorArbitrator(
            strategy=ArbitrationStrategy.CONFIDENCE_WEIGHTED,
            time_tolerance=0.05,  # 50ms overlap window
            octave_correction=is_bass,  # Only for bass
            min_agreement=0.3,  # Require 30% agreement to reduce false positives
        )

        return arbitrator.arbitrate(detector_results)

    def _estimate_tempo(self, notes: List[EnsembleNote]) -> float:
        """Estimate tempo from note onsets."""
        if len(notes) < 2:
            return 120.0

        onsets = sorted([n.start for n in notes])
        iois = np.diff(onsets)

        if len(iois) == 0:
            return 120.0

        # Filter very short/long IOIs
        iois = iois[(iois > 0.1) & (iois < 2.0)]
        if len(iois) == 0:
            return 120.0

        # Use median IOI
        median_ioi = np.median(iois)

        # Assume 8th note grid
        tempo = 60.0 / (median_ioi * 2)

        return float(np.clip(tempo, 60, 200))

    def _calculate_overall_confidence(
        self,
        notes: List[EnsembleNote],
        detector_stats: Dict[str, Dict[str, Any]],
    ) -> float:
        """Calculate overall extraction confidence."""
        if len(notes) == 0:
            return 0.0

        # Average note confidence
        avg_note_conf = np.mean([n.confidence for n in notes])

        # Agreement bonus
        avg_agreement = np.mean([n.agreement_score for n in notes])

        # Detector agreement bonus
        detector_count = len(detector_stats)
        agreement_bonus = min(0.2, (detector_count - 1) * 0.1) if detector_count > 1 else 0

        overall = avg_note_conf * 0.6 + avg_agreement * 0.2 + agreement_bonus + 0.2

        return float(np.clip(overall, 0, 1))


def extract_with_ensemble(
    audio: np.ndarray,
    sr: int,
    stem_type: str = "other",
    **kwargs,
) -> EnsembleResult:
    """Convenience function for ensemble extraction.

    Args:
        audio: Audio signal
        sr: Sample rate
        stem_type: Type of stem
        **kwargs: Additional options

    Returns:
        EnsembleResult
    """
    extractor = PitchEnsembleExtractor()
    return extractor.extract(audio, sr, stem_type=stem_type, **kwargs)
