"""ML-based note classification for MIDI refinement.

Classifies detected notes as:
- Real notes (intentional musical content)
- Ghost notes (artifacts, noise, delay repeats)
- Harmonic fragments (partial detection of sustained notes)

Uses audio context features alongside note properties for
more accurate classification than threshold-based filtering.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

import numpy as np

logger = logging.getLogger(__name__)

# Model paths
DEFAULT_MODEL_DIR = Path.home() / ".toneforge" / "models" / "midi"


@dataclass
class NoteContext:
    """Context features for a detected note."""

    # Note properties
    pitch: int
    start_time: float
    end_time: float
    velocity: int

    # Audio context at note onset
    spectral_flux: float = 0.0           # Change in spectrum at onset
    onset_strength: float = 0.0          # Strength of onset detection
    harmonic_ratio: float = 0.0          # Harmonic vs noise ratio
    pitch_confidence: float = 0.0        # Confidence of pitch detection

    # Temporal context
    time_since_last_note: float = 0.0    # Gap from previous note
    time_to_next_note: float = 0.0       # Gap to next note
    concurrent_notes: int = 0            # Notes playing at same time

    # Musical context
    in_detected_key: bool = True         # Whether note fits detected key
    beat_alignment: float = 0.0          # 0-1, how close to grid
    phrase_position: float = 0.0         # Position in detected phrase

    # Delay/echo indicators
    matches_delay_pattern: bool = False  # Matches common delay timing
    velocity_vs_prior: float = 1.0       # Ratio to prior same-pitch note

    def to_array(self) -> np.ndarray:
        """Convert to feature array for ML model."""
        return np.array([
            self.pitch / 127.0,
            min(self.end_time - self.start_time, 5.0) / 5.0,  # Normalized duration
            self.velocity / 127.0,
            self.spectral_flux,
            self.onset_strength,
            self.harmonic_ratio,
            self.pitch_confidence,
            min(self.time_since_last_note, 5.0) / 5.0,
            min(self.time_to_next_note, 5.0) / 5.0,
            min(self.concurrent_notes, 10) / 10.0,
            1.0 if self.in_detected_key else 0.0,
            self.beat_alignment,
            self.phrase_position,
            1.0 if self.matches_delay_pattern else 0.0,
            min(self.velocity_vs_prior, 2.0) / 2.0,
        ], dtype=np.float32)

    @classmethod
    def num_features(cls) -> int:
        """Number of features in the array."""
        return 15


@dataclass
class ClassifiedNote:
    """A note with its classification result."""

    pitch: int
    start_time: float
    end_time: float
    velocity: int

    classification: str  # "real", "ghost", "harmonic_fragment"
    confidence: float    # 0-1 confidence in classification
    context: NoteContext

    # Suggestions
    suggested_velocity: Optional[int] = None  # If velocity should be adjusted
    suggested_quantize_to: Optional[float] = None  # Suggested grid position


class NoteClassifier:
    """ML-based note classifier.

    Uses a gradient boosting model trained on labeled MIDI data
    to classify notes as real, ghost, or harmonic fragments.

    Falls back to heuristic classification when models aren't available.
    """

    def __init__(
        self,
        model_dir: Optional[Path] = None,
        use_ml: bool = True,
    ):
        """Initialize the note classifier.

        Args:
            model_dir: Directory containing trained models
            use_ml: Whether to use ML models (vs pure heuristics)
        """
        self.model_dir = model_dir or DEFAULT_MODEL_DIR
        self.use_ml = use_ml
        self._model = None
        self._model_loaded = False

        if use_ml:
            self._try_load_model()

    def _try_load_model(self) -> bool:
        """Try to load the classification model."""
        model_path = self.model_dir / "note_classifier.lgb"

        if not model_path.exists():
            logger.debug("No note classifier model found at %s", model_path)
            return False

        try:
            import lightgbm as lgb
            self._model = lgb.Booster(model_file=str(model_path))
            self._model_loaded = True
            logger.info("Loaded note classifier from %s", model_path)
            return True
        except ImportError:
            logger.debug("LightGBM not available, using heuristic classification")
            return False
        except Exception as e:
            logger.warning("Failed to load note classifier: %s", e)
            return False

    def is_ml_ready(self) -> bool:
        """Check if ML model is loaded."""
        return self._model_loaded and self._model is not None

    def extract_context(
        self,
        note: Tuple[int, float, float, int],
        all_notes: List[Tuple[int, float, float, int]],
        audio: Optional[np.ndarray] = None,
        sr: int = 22050,
        tempo_bpm: float = 120.0,
        detected_key: Optional[Tuple[int, str]] = None,
    ) -> NoteContext:
        """Extract context features for a note.

        Args:
            note: (pitch, start, end, velocity) tuple
            all_notes: All detected notes for temporal context
            audio: Audio array for spectral context (optional)
            sr: Sample rate
            tempo_bpm: Detected tempo
            detected_key: (root, scale) tuple from key detection

        Returns:
            NoteContext with extracted features
        """
        pitch, start, end, velocity = note

        context = NoteContext(
            pitch=pitch,
            start_time=start,
            end_time=end,
            velocity=velocity,
        )

        # Sort notes by start time
        sorted_notes = sorted(all_notes, key=lambda n: n[1])
        note_idx = next(
            (i for i, n in enumerate(sorted_notes) if n[1] == start and n[0] == pitch),
            -1
        )

        # Temporal context
        if note_idx > 0:
            prev_note = sorted_notes[note_idx - 1]
            context.time_since_last_note = start - prev_note[1]
        else:
            context.time_since_last_note = start

        if note_idx >= 0 and note_idx < len(sorted_notes) - 1:
            next_note = sorted_notes[note_idx + 1]
            context.time_to_next_note = next_note[1] - start
        else:
            context.time_to_next_note = 5.0  # No next note

        # Count concurrent notes
        context.concurrent_notes = sum(
            1 for n in all_notes
            if n[1] <= start < n[2] and n != note
        )

        # Beat alignment
        seconds_per_beat = 60.0 / tempo_bpm
        beat_position = (start % seconds_per_beat) / seconds_per_beat
        # Distance to nearest grid position (0, 0.25, 0.5, 0.75)
        grid_positions = [0.0, 0.25, 0.5, 0.75, 1.0]
        min_distance = min(abs(beat_position - g) for g in grid_positions)
        context.beat_alignment = 1.0 - min(min_distance * 4, 1.0)

        # Key membership
        if detected_key:
            root, scale = detected_key
            from tone_forge.midi_extractor import SCALE_PATTERNS
            if scale in SCALE_PATTERNS:
                scale_notes = set((root + interval) % 12 for interval in SCALE_PATTERNS[scale])
                context.in_detected_key = (pitch % 12) in scale_notes

        # Delay pattern detection
        context.matches_delay_pattern = self._check_delay_pattern(
            note, sorted_notes, tempo_bpm
        )

        # Velocity ratio to prior same-pitch note
        prior_same_pitch = [
            n for n in sorted_notes
            if n[0] == pitch and n[1] < start
        ]
        if prior_same_pitch:
            last_same = prior_same_pitch[-1]
            context.velocity_vs_prior = velocity / max(last_same[3], 1)

        # Audio-based features (if audio provided)
        if audio is not None and len(audio) > 0:
            context = self._extract_audio_features(context, audio, sr, start)

        return context

    def _check_delay_pattern(
        self,
        note: Tuple[int, float, float, int],
        sorted_notes: List[Tuple[int, float, float, int]],
        tempo_bpm: float,
    ) -> bool:
        """Check if note matches common delay timing patterns."""
        pitch, start, end, velocity = note
        seconds_per_beat = 60.0 / tempo_bpm

        # Common delay times
        delay_intervals = [
            seconds_per_beat * 0.75,   # Dotted 8th
            seconds_per_beat * 0.5,    # 8th note
            seconds_per_beat * 1.0,    # Quarter
            seconds_per_beat * 1.5,    # Dotted quarter
        ]

        tolerance = 0.03  # 30ms tolerance

        # Find prior notes with same pitch
        for prev in reversed(sorted_notes):
            if prev[0] != pitch:
                continue
            if prev[1] >= start:
                continue

            time_diff = start - prev[1]

            # Check if matches delay interval and has lower velocity
            for delay in delay_intervals:
                if abs(time_diff - delay) < tolerance:
                    if velocity < prev[3] * 0.9:  # At least 10% quieter
                        return True

            # Don't look too far back
            if start - prev[1] > 2.0:
                break

        return False

    def _extract_audio_features(
        self,
        context: NoteContext,
        audio: np.ndarray,
        sr: int,
        onset_time: float,
    ) -> NoteContext:
        """Extract audio-based features at note onset."""
        try:
            import librosa

            # Convert onset time to sample index
            onset_sample = int(onset_time * sr)

            # Window around onset
            window_size = int(0.05 * sr)  # 50ms window
            start_sample = max(0, onset_sample - window_size // 2)
            end_sample = min(len(audio), onset_sample + window_size // 2)

            if end_sample <= start_sample:
                return context

            window = audio[start_sample:end_sample]

            # Spectral flux (change in spectrum)
            if len(window) > 512:
                spec = np.abs(librosa.stft(window, n_fft=512))
                flux = np.mean(np.diff(spec, axis=1) ** 2)
                context.spectral_flux = min(flux, 1.0)

            # Onset strength at this point
            onset_env = librosa.onset.onset_strength(y=audio, sr=sr)
            onset_frame = librosa.time_to_frames(onset_time, sr=sr)
            if 0 <= onset_frame < len(onset_env):
                max_onset = onset_env.max() if onset_env.max() > 0 else 1.0
                context.onset_strength = onset_env[onset_frame] / max_onset

            # Harmonic ratio
            harmonic, percussive = librosa.effects.hpss(window)
            h_energy = np.sum(harmonic ** 2)
            p_energy = np.sum(percussive ** 2)
            total = h_energy + p_energy
            if total > 0:
                context.harmonic_ratio = h_energy / total

        except Exception as e:
            logger.debug("Failed to extract audio features: %s", e)

        return context

    def classify_note(
        self,
        note: Tuple[int, float, float, int],
        all_notes: List[Tuple[int, float, float, int]],
        audio: Optional[np.ndarray] = None,
        sr: int = 22050,
        tempo_bpm: float = 120.0,
        detected_key: Optional[Tuple[int, str]] = None,
    ) -> ClassifiedNote:
        """Classify a single note.

        Args:
            note: (pitch, start, end, velocity) tuple
            all_notes: All detected notes
            audio: Audio array (optional)
            sr: Sample rate
            tempo_bpm: Detected tempo
            detected_key: (root, scale) tuple

        Returns:
            ClassifiedNote with classification and confidence
        """
        context = self.extract_context(
            note, all_notes, audio, sr, tempo_bpm, detected_key
        )

        if self.is_ml_ready():
            classification, confidence = self._classify_ml(context)
        else:
            classification, confidence = self._classify_heuristic(context)

        return ClassifiedNote(
            pitch=note[0],
            start_time=note[1],
            end_time=note[2],
            velocity=note[3],
            classification=classification,
            confidence=confidence,
            context=context,
        )

    def _classify_ml(self, context: NoteContext) -> Tuple[str, float]:
        """Classify using ML model."""
        features = context.to_array().reshape(1, -1)
        probs = self._model.predict(features)[0]

        # Model outputs: [real_prob, ghost_prob, fragment_prob]
        classes = ["real", "ghost", "harmonic_fragment"]
        max_idx = int(np.argmax(probs))
        return classes[max_idx], float(probs[max_idx])

    def _classify_heuristic(self, context: NoteContext) -> Tuple[str, float]:
        """Classify using heuristics.

        This provides reasonable classification when ML models aren't available.
        """
        # Score for being a real note (higher = more likely real)
        real_score = 0.5

        # Strong onset suggests real note
        real_score += context.onset_strength * 0.2

        # Good beat alignment suggests real note
        real_score += context.beat_alignment * 0.15

        # Reasonable velocity
        if 40 <= context.velocity <= 120:
            real_score += 0.1

        # In key is good
        if context.in_detected_key:
            real_score += 0.1

        # Multiple concurrent notes (chord) is usually real
        if context.concurrent_notes > 0:
            real_score += 0.05

        # Penalties
        # Matches delay pattern strongly suggests ghost
        if context.matches_delay_pattern:
            real_score -= 0.4

        # Very quiet compared to prior same-pitch note
        if context.velocity_vs_prior < 0.5:
            real_score -= 0.2

        # Very short isolated notes
        duration = context.end_time - context.start_time
        if duration < 0.05 and context.time_since_last_note > 1.0:
            real_score -= 0.2

        # Low harmonic content
        if context.harmonic_ratio < 0.3:
            real_score -= 0.1

        # Classify based on score
        real_score = max(0.0, min(1.0, real_score))

        if real_score > 0.6:
            return "real", real_score
        elif real_score < 0.4:
            # Determine if ghost or fragment
            if context.matches_delay_pattern:
                return "ghost", 1.0 - real_score
            elif duration < 0.05:
                return "harmonic_fragment", 1.0 - real_score
            else:
                return "ghost", 1.0 - real_score
        else:
            # Borderline - return real with lower confidence
            return "real", 0.5

    def classify_notes(
        self,
        notes: List[Tuple[int, float, float, int]],
        audio: Optional[np.ndarray] = None,
        sr: int = 22050,
        tempo_bpm: float = 120.0,
        detected_key: Optional[Tuple[int, str]] = None,
    ) -> List[ClassifiedNote]:
        """Classify all notes in a list.

        Args:
            notes: List of (pitch, start, end, velocity) tuples
            audio: Audio array (optional)
            sr: Sample rate
            tempo_bpm: Detected tempo
            detected_key: (root, scale) tuple

        Returns:
            List of ClassifiedNotes
        """
        return [
            self.classify_note(note, notes, audio, sr, tempo_bpm, detected_key)
            for note in notes
        ]

    def filter_to_real_notes(
        self,
        notes: List[Tuple[int, float, float, int]],
        audio: Optional[np.ndarray] = None,
        sr: int = 22050,
        tempo_bpm: float = 120.0,
        detected_key: Optional[Tuple[int, str]] = None,
        min_confidence: float = 0.5,
    ) -> List[Tuple[int, float, float, int]]:
        """Filter notes to only include real notes.

        Convenience method for integration with existing MIDI pipeline.

        Args:
            notes: List of (pitch, start, end, velocity) tuples
            audio: Audio array (optional)
            sr: Sample rate
            tempo_bpm: Detected tempo
            detected_key: (root, scale) tuple
            min_confidence: Minimum confidence for real classification

        Returns:
            Filtered list of notes
        """
        classified = self.classify_notes(
            notes, audio, sr, tempo_bpm, detected_key
        )

        real_notes = [
            (c.pitch, c.start_time, c.end_time, c.velocity)
            for c in classified
            if c.classification == "real" and c.confidence >= min_confidence
        ]

        logger.info(
            "Note classifier: %d -> %d notes (filtered %d ghost/fragment)",
            len(notes), len(real_notes), len(notes) - len(real_notes)
        )

        return real_notes


# Module-level singleton
_classifier: Optional[NoteClassifier] = None


def get_classifier(
    model_dir: Optional[Path] = None,
    use_ml: bool = True,
) -> NoteClassifier:
    """Get or create the global NoteClassifier instance."""
    global _classifier

    if _classifier is None:
        _classifier = NoteClassifier(model_dir=model_dir, use_ml=use_ml)

    return _classifier


def classify_notes(
    notes: List[Tuple[int, float, float, int]],
    audio: Optional[np.ndarray] = None,
    sr: int = 22050,
    tempo_bpm: float = 120.0,
    detected_key: Optional[Tuple[int, str]] = None,
) -> List[ClassifiedNote]:
    """Classify notes using the global classifier."""
    return get_classifier().classify_notes(
        notes, audio, sr, tempo_bpm, detected_key
    )


def filter_ghost_notes(
    notes: List[Tuple[int, float, float, int]],
    audio: Optional[np.ndarray] = None,
    sr: int = 22050,
    tempo_bpm: float = 120.0,
    detected_key: Optional[Tuple[int, str]] = None,
    min_confidence: float = 0.5,
    provenance_chain=None,  # Optional ProvenanceChain for tracking
) -> List[Tuple[int, float, float, int]]:
    """Filter ghost notes using the global classifier.

    Args:
        notes: List of (pitch, start, end, velocity) tuples
        audio: Audio array (optional)
        sr: Sample rate
        tempo_bpm: Detected tempo
        detected_key: (root, scale) tuple
        min_confidence: Minimum confidence for real classification
        provenance_chain: Optional ProvenanceChain for decision tracking

    Returns:
        Filtered list of notes (and populates provenance_chain if provided)
    """
    classifier = get_classifier()
    classified = classifier.classify_notes(notes, audio, sr, tempo_bpm, detected_key)

    real_notes = []
    for i, c in enumerate(classified):
        note_tuple = (c.pitch, c.start_time, c.end_time, c.velocity)
        note_id = f"n{i}"

        if c.classification == "real" and c.confidence >= min_confidence:
            real_notes.append(note_tuple)

            # Record retention decision with provenance
            if provenance_chain is not None:
                from tone_forge.provenance import (
                    DecisionAction, DecisionDomain, ReasonGraph
                )
                record = provenance_chain.create_record(
                    action=DecisionAction.RETAINED,
                    stage="ghost_note_classifier",
                    entity_type="note",
                    entity_id=note_id,
                    entity_data={
                        "pitch": c.pitch, "start": c.start_time,
                        "end": c.end_time, "velocity": c.velocity
                    },
                    domain=DecisionDomain.MIDI_REFINEMENT,
                )
                record.reason = ReasonGraph(
                    summary=f"Retained: classified as '{c.classification}'",
                    confidence=c.confidence,
                    model_used="note_classifier_heuristic" if not classifier.is_ml_ready() else "note_classifier_ml",
                )
                record.reason.add_factor("classification", c.classification)
                record.reason.add_factor("classification_confidence", c.confidence, threshold=min_confidence)
                record.reason.add_factor("onset_strength", c.context.onset_strength)
                record.reason.add_factor("beat_alignment", c.context.beat_alignment)
                record.reason.add_factor("in_detected_key", c.context.in_detected_key)
        else:
            # Record removal decision with provenance
            if provenance_chain is not None:
                from tone_forge.provenance import (
                    DecisionAction, DecisionDomain, ReasonGraph
                )
                record = provenance_chain.create_record(
                    action=DecisionAction.REMOVED,
                    stage="ghost_note_classifier",
                    entity_type="note",
                    entity_id=note_id,
                    entity_data={
                        "pitch": c.pitch, "start": c.start_time,
                        "end": c.end_time, "velocity": c.velocity
                    },
                    domain=DecisionDomain.MIDI_REFINEMENT,
                )
                record.reason = ReasonGraph(
                    summary=f"Removed: classified as '{c.classification}'",
                    confidence=c.confidence,
                    model_used="note_classifier_heuristic" if not classifier.is_ml_ready() else "note_classifier_ml",
                )
                record.reason.add_factor("classification", c.classification)
                record.reason.add_factor("classification_confidence", c.confidence, threshold=min_confidence)
                record.reason.add_factor("matches_delay_pattern", c.context.matches_delay_pattern)
                record.reason.add_factor("velocity_vs_prior", c.context.velocity_vs_prior)
                record.reason.add_factor("harmonic_ratio", c.context.harmonic_ratio)
                record.reason.add_factor("temporal_isolation", c.context.time_since_last_note > 1.0)

    logger.info(
        "Note classifier: %d -> %d notes (filtered %d ghost/fragment)",
        len(notes), len(real_notes), len(notes) - len(real_notes)
    )

    return real_notes
