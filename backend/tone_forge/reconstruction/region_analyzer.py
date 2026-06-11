"""Region-based reconstruction analysis.

Enables focused analysis of specific time regions:
- Re-analyze problematic sections
- Compare extraction variants
- Loop section analysis
- Verse/chorus isolation
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class RegionBounds:
    """Time bounds for a region."""

    start: float
    end: float
    track_id: Optional[str] = None  # None = all tracks

    @property
    def duration(self) -> float:
        """Duration of the region."""
        return self.end - self.start

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "start": self.start,
            "end": self.end,
            "duration": self.duration,
            "track_id": self.track_id,
        }


@dataclass
class RegionConfidenceDetail:
    """Detailed confidence information for a region."""

    overall: float
    note_confidence: float
    timing_confidence: float
    pitch_confidence: float
    velocity_confidence: float

    # Issue breakdown
    octave_ambiguity_score: float = 0.0
    harmonic_confusion_score: float = 0.0
    reverb_artifact_score: float = 0.0
    timing_drift_score: float = 0.0

    # Recommendations
    needs_cleanup: bool = False
    suggested_passes: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "overall": self.overall,
            "note_confidence": self.note_confidence,
            "timing_confidence": self.timing_confidence,
            "pitch_confidence": self.pitch_confidence,
            "velocity_confidence": self.velocity_confidence,
            "issues": {
                "octave_ambiguity": self.octave_ambiguity_score,
                "harmonic_confusion": self.harmonic_confusion_score,
                "reverb_artifacts": self.reverb_artifact_score,
                "timing_drift": self.timing_drift_score,
            },
            "needs_cleanup": self.needs_cleanup,
            "suggested_passes": self.suggested_passes,
        }


@dataclass
class RegionProvenance:
    """Provenance information for a region."""

    detector_contributions: Dict[str, float]  # detector_name -> contribution
    cleanup_passes_applied: List[str]
    corrections_made: int
    octave_corrections: int
    timing_adjustments: int

    # FP/FN risk
    fp_risk: float = 0.0
    fn_risk: float = 0.0

    # Detailed notes
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "detector_contributions": self.detector_contributions,
            "cleanup_passes_applied": self.cleanup_passes_applied,
            "corrections_made": self.corrections_made,
            "octave_corrections": self.octave_corrections,
            "timing_adjustments": self.timing_adjustments,
            "fp_risk": self.fp_risk,
            "fn_risk": self.fn_risk,
            "notes": self.notes,
        }


@dataclass
class RegionAnalysisResult:
    """Result of analyzing a specific region."""

    bounds: RegionBounds
    section_type: Optional[str] = None  # verse, chorus, etc.

    # MIDI extraction
    notes: List[Dict] = field(default_factory=list)
    note_count: int = 0

    # Confidence
    confidence: RegionConfidenceDetail = None

    # Provenance
    provenance: RegionProvenance = None

    # Audio features
    energy_mean: float = 0.0
    energy_peak: float = 0.0
    spectral_centroid: float = 0.0
    tempo_local: float = 0.0

    # Waveform data for visualization
    waveform_peaks: np.ndarray = field(default_factory=lambda: np.array([]))

    def to_dict(self) -> dict:
        """Convert to dictionary for API response."""
        return {
            "bounds": self.bounds.to_dict(),
            "section_type": self.section_type,
            "notes": self.notes,
            "note_count": self.note_count,
            "confidence": self.confidence.to_dict() if self.confidence else None,
            "provenance": self.provenance.to_dict() if self.provenance else None,
            "audio_features": {
                "energy_mean": self.energy_mean,
                "energy_peak": self.energy_peak,
                "spectral_centroid": self.spectral_centroid,
                "tempo_local": self.tempo_local,
            },
            "waveform_peaks": self.waveform_peaks.tolist()
            if len(self.waveform_peaks) > 0
            else [],
        }


class RegionAnalyzer:
    """Analyze specific regions of audio for focused reconstruction.

    Enables:
    - Focused MIDI extraction on problematic sections
    - Region comparison (A vs B)
    - Detailed provenance per region
    - Section-aware extraction settings
    """

    def __init__(self, sr: int = 22050):
        """Initialize the analyzer.

        Args:
            sr: Sample rate
        """
        self.sr = sr

    def analyze_region(
        self,
        audio: np.ndarray,
        sr: int,
        start_time: float,
        end_time: float,
        stem_type: str = "other",
        genre: Optional[str] = None,
        track_id: Optional[str] = None,
        include_midi: bool = True,
        include_provenance: bool = True,
    ) -> RegionAnalysisResult:
        """Analyze a specific time region.

        Args:
            audio: Full audio array
            sr: Sample rate
            start_time: Region start in seconds
            end_time: Region end in seconds
            stem_type: Type of stem (bass, drums, lead, etc.)
            genre: Genre hint for extraction
            track_id: Track identifier
            include_midi: Whether to extract MIDI
            include_provenance: Whether to include detailed provenance

        Returns:
            RegionAnalysisResult with detailed analysis
        """
        import librosa

        # Validate bounds
        duration = len(audio) / sr
        start_time = max(0, min(start_time, duration))
        end_time = max(start_time + 0.1, min(end_time, duration))

        bounds = RegionBounds(start=start_time, end=end_time, track_id=track_id)

        # Extract region audio
        start_sample = int(start_time * sr)
        end_sample = int(end_time * sr)
        region_audio = audio[start_sample:end_sample]

        # Compute audio features
        energy_mean, energy_peak = self._compute_energy(region_audio)
        spectral_centroid = self._compute_spectral_centroid(region_audio, sr)
        tempo_local = self._estimate_local_tempo(region_audio, sr)
        waveform_peaks = self._compute_waveform_peaks(region_audio, num_points=200)

        # Extract MIDI if requested
        notes = []
        note_count = 0
        provenance = None
        confidence = None

        if include_midi:
            notes, note_count, confidence, provenance = self._extract_region_midi(
                region_audio,
                sr,
                stem_type,
                genre,
                bounds,
                include_provenance,
            )

        # Detect section type if we have section detector
        section_type = self._detect_section_type(region_audio, sr, energy_mean)

        return RegionAnalysisResult(
            bounds=bounds,
            section_type=section_type,
            notes=notes,
            note_count=note_count,
            confidence=confidence,
            provenance=provenance,
            energy_mean=energy_mean,
            energy_peak=energy_peak,
            spectral_centroid=spectral_centroid,
            tempo_local=tempo_local,
            waveform_peaks=waveform_peaks,
        )

    def compare_regions(
        self,
        audio: np.ndarray,
        sr: int,
        region_a: Tuple[float, float],
        region_b: Tuple[float, float],
        stem_type: str = "other",
    ) -> Dict[str, Any]:
        """Compare two regions for similarity.

        Useful for comparing verse 1 vs verse 2, or variations.

        Args:
            audio: Full audio array
            sr: Sample rate
            region_a: (start, end) of first region
            region_b: (start, end) of second region
            stem_type: Type of stem

        Returns:
            Dictionary with comparison metrics
        """
        result_a = self.analyze_region(
            audio, sr, region_a[0], region_a[1], stem_type, include_provenance=False
        )
        result_b = self.analyze_region(
            audio, sr, region_b[0], region_b[1], stem_type, include_provenance=False
        )

        # Compute similarity metrics
        energy_similarity = 1.0 - abs(result_a.energy_mean - result_b.energy_mean)
        spectral_similarity = 1.0 - abs(
            result_a.spectral_centroid - result_b.spectral_centroid
        ) / max(result_a.spectral_centroid, result_b.spectral_centroid, 1)

        # Note count similarity
        max_notes = max(result_a.note_count, result_b.note_count, 1)
        note_count_similarity = (
            1.0 - abs(result_a.note_count - result_b.note_count) / max_notes
        )

        overall_similarity = (
            energy_similarity * 0.3
            + spectral_similarity * 0.3
            + note_count_similarity * 0.4
        )

        return {
            "region_a": result_a.to_dict(),
            "region_b": result_b.to_dict(),
            "similarity": {
                "overall": overall_similarity,
                "energy": energy_similarity,
                "spectral": spectral_similarity,
                "note_count": note_count_similarity,
            },
            "is_similar": overall_similarity > 0.7,
        }

    def _compute_energy(self, audio: np.ndarray) -> Tuple[float, float]:
        """Compute mean and peak energy."""
        if len(audio) == 0:
            return 0.0, 0.0

        rms = np.sqrt(np.mean(audio**2))
        peak = np.max(np.abs(audio))
        return float(rms), float(peak)

    def _compute_spectral_centroid(self, audio: np.ndarray, sr: int) -> float:
        """Compute spectral centroid."""
        import librosa

        if len(audio) == 0:
            return 0.0

        centroid = librosa.feature.spectral_centroid(y=audio, sr=sr)
        return float(np.mean(centroid))

    def _estimate_local_tempo(self, audio: np.ndarray, sr: int) -> float:
        """Estimate local tempo for region."""
        import librosa

        if len(audio) < sr:  # Need at least 1 second
            return 120.0

        tempo, _ = librosa.beat.beat_track(y=audio, sr=sr)
        if hasattr(tempo, "__iter__"):
            tempo = float(tempo[0]) if len(tempo) > 0 else 120.0
        return float(tempo) if tempo > 0 else 120.0

    def _compute_waveform_peaks(
        self, audio: np.ndarray, num_points: int = 200
    ) -> np.ndarray:
        """Compute waveform peaks for visualization."""
        if len(audio) == 0:
            return np.array([])

        chunk_size = max(1, len(audio) // num_points)
        peaks = []

        for i in range(0, len(audio), chunk_size):
            chunk = audio[i : i + chunk_size]
            if len(chunk) > 0:
                peaks.append(float(np.max(np.abs(chunk))))

        return np.array(peaks[:num_points])

    def _extract_region_midi(
        self,
        audio: np.ndarray,
        sr: int,
        stem_type: str,
        genre: Optional[str],
        bounds: RegionBounds,
        include_provenance: bool,
    ) -> Tuple[List[Dict], int, RegionConfidenceDetail, RegionProvenance]:
        """Extract MIDI for the region."""
        try:
            from tone_forge.midi_extractor import extract_midi_from_array
        except ImportError:
            logger.warning("MIDI extractor not available")
            return [], 0, None, None

        try:
            result = extract_midi_from_array(
                audio,
                sr,
                stem_type=stem_type,
                genre=genre,
            )

            # Convert notes to list of dicts
            notes = []
            if result and hasattr(result, "notes"):
                for note in result.notes:
                    notes.append(
                        {
                            "pitch": note.pitch if hasattr(note, "pitch") else note[0],
                            "start": (
                                note.start if hasattr(note, "start") else note[1]
                            )
                            + bounds.start,
                            "end": (note.end if hasattr(note, "end") else note[2])
                            + bounds.start,
                            "velocity": (
                                note.velocity if hasattr(note, "velocity") else note[3]
                            ),
                            "confidence": (
                                note.confidence
                                if hasattr(note, "confidence")
                                else 0.8
                            ),
                        }
                    )

            note_count = len(notes)

            # Build confidence detail
            avg_confidence = (
                np.mean([n.get("confidence", 0.8) for n in notes])
                if notes
                else 0.5
            )

            confidence = RegionConfidenceDetail(
                overall=float(avg_confidence),
                note_confidence=float(avg_confidence),
                timing_confidence=0.8,  # Default
                pitch_confidence=0.85,
                velocity_confidence=0.75,
                needs_cleanup=avg_confidence < 0.7,
                suggested_passes=(
                    ["harmonic_suppression", "timing_correction"]
                    if avg_confidence < 0.7
                    else []
                ),
            )

            # Build provenance
            provenance = None
            if include_provenance:
                provenance = RegionProvenance(
                    detector_contributions={"basic-pitch": 1.0},
                    cleanup_passes_applied=[],
                    corrections_made=0,
                    octave_corrections=0,
                    timing_adjustments=0,
                    fp_risk=0.15 if avg_confidence > 0.7 else 0.35,
                    fn_risk=0.2 if avg_confidence > 0.7 else 0.4,
                )

                # Add notes based on extraction
                if result and hasattr(result, "provenance") and result.provenance:
                    prov = result.provenance
                    if hasattr(prov, "cleanup_passes"):
                        provenance.cleanup_passes_applied = prov.cleanup_passes
                    if hasattr(prov, "corrections"):
                        provenance.corrections_made = prov.corrections

            return notes, note_count, confidence, provenance

        except Exception as e:
            logger.error(f"MIDI extraction failed for region: {e}")
            return [], 0, None, None

    def _detect_section_type(
        self, audio: np.ndarray, sr: int, energy_mean: float
    ) -> Optional[str]:
        """Detect the type of section based on characteristics."""
        import librosa

        if len(audio) < sr:
            return None

        # Simple heuristic classification
        onset_env = librosa.onset.onset_strength(y=audio, sr=sr)
        onset_density = len(librosa.onset.onset_detect(onset_envelope=onset_env, sr=sr)) / (
            len(audio) / sr
        )

        # Classify based on energy and density
        if energy_mean > 0.15 and onset_density > 4:
            return "chorus"
        elif energy_mean > 0.1 and onset_density > 2:
            return "verse"
        elif energy_mean < 0.05:
            return "breakdown"
        elif onset_density > 6:
            return "drop"
        else:
            return "unknown"


def analyze_region(
    audio: np.ndarray,
    sr: int,
    start_time: float,
    end_time: float,
    stem_type: str = "other",
    genre: Optional[str] = None,
) -> RegionAnalysisResult:
    """Convenience function to analyze a region.

    Args:
        audio: Full audio array
        sr: Sample rate
        start_time: Region start in seconds
        end_time: Region end in seconds
        stem_type: Type of stem
        genre: Genre hint

    Returns:
        RegionAnalysisResult
    """
    analyzer = RegionAnalyzer(sr=sr)
    return analyzer.analyze_region(
        audio, sr, start_time, end_time, stem_type, genre
    )
