"""
GPU-accelerated MIDI extraction using native CoreML on Apple Silicon.

Uses the native .mlpackage model with full GPU/ANE acceleration instead of
ONNX with partial CoreML support.

This provides 3-5x speedup over CPU inference.
"""

import logging
import tempfile
import base64
from pathlib import Path
from typing import List, Tuple, Optional
from dataclasses import dataclass

import numpy as np
import torch
import torchaudio
import coremltools as ct

logger = logging.getLogger(__name__)

# Model path
MODEL_PATH = Path("/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/site-packages/basic_pitch/saved_models/icassp_2022/nmp.mlpackage")

# Check MPS availability for preprocessing
MPS_AVAILABLE = torch.backends.mps.is_available()
DEVICE = torch.device("mps") if MPS_AVAILABLE else torch.device("cpu")

# Audio parameters (must match model training)
AUDIO_SAMPLE_RATE = 22050
FFT_HOP = 256
N_FFT = 2048
N_MELS = 229
MEL_FMIN = 30.0
MEL_FMAX = 8000.0

# Model parameters
ANNOTATIONS_FPS = AUDIO_SAMPLE_RATE / FFT_HOP  # ~86 fps
MIDI_OFFSET = 21  # A0


@dataclass
class ExtractedNote:
    """A MIDI note extracted from audio."""
    pitch: int
    start_time: float
    end_time: float
    velocity: int
    confidence: float


class CoreMLMIDIExtractor:
    """
    GPU-accelerated MIDI extractor using native CoreML.

    Pipeline:
    1. Load audio with torchaudio
    2. Compute mel spectrogram on GPU (MPS)
    3. Run neural network on GPU/ANE (CoreML)
    4. Post-process predictions on GPU (MPS)
    """

    def __init__(self):
        self.model = None
        self.mel_transform = None
        self._load_model()
        self._setup_mel_transform()

    def _load_model(self):
        """Load CoreML model with GPU/ANE compute units."""
        if not MODEL_PATH.exists():
            raise FileNotFoundError(f"CoreML model not found at {MODEL_PATH}")

        logger.info("Loading CoreML model with GPU/ANE acceleration...")
        self.model = ct.models.MLModel(
            str(MODEL_PATH),
            compute_units=ct.ComputeUnit.ALL  # GPU + ANE + CPU
        )
        logger.info("CoreML model loaded successfully")

    def _setup_mel_transform(self):
        """Setup mel spectrogram transform on GPU."""
        self.mel_transform = torchaudio.transforms.MelSpectrogram(
            sample_rate=AUDIO_SAMPLE_RATE,
            n_fft=N_FFT,
            hop_length=FFT_HOP,
            n_mels=N_MELS,
            f_min=MEL_FMIN,
            f_max=MEL_FMAX,
            power=1.0,  # Magnitude spectrogram
            norm="slaney",
            mel_scale="slaney",
        ).to(DEVICE)
        logger.info(f"Mel transform on {DEVICE}")

    def _load_audio(self, audio_path: str) -> Tuple[torch.Tensor, int]:
        """Load and preprocess audio on GPU."""
        import soundfile as sf

        # Load audio
        try:
            torchaudio.set_audio_backend("soundfile")
            waveform, sr = torchaudio.load(audio_path)
        except Exception:
            # Fallback to soundfile
            data, sr = sf.read(audio_path, dtype='float32')
            if data.ndim == 1:
                waveform = torch.from_numpy(data).unsqueeze(0)
            else:
                waveform = torch.from_numpy(data.T)

        # Move to GPU
        waveform = waveform.to(DEVICE)

        # Convert to mono
        if waveform.shape[0] > 1:
            waveform = waveform.mean(dim=0, keepdim=True)

        # Resample if needed
        if sr != AUDIO_SAMPLE_RATE:
            resampler = torchaudio.transforms.Resample(sr, AUDIO_SAMPLE_RATE).to(DEVICE)
            waveform = resampler(waveform)

        return waveform, AUDIO_SAMPLE_RATE

    # Chunking geometry, shared by inference and decode.
    CHUNK_SIZE = 43844  # Model's expected input size
    OVERLAP = 30 * 256  # 30 frames overlap for continuity
    HOP = CHUNK_SIZE - OVERLAP

    def _run_inference_chunked(
        self, waveform: torch.Tensor
    ) -> List[Tuple[np.ndarray, np.ndarray, float]]:
        """Run CoreML inference on overlapping audio chunks.

        Returns one ``(note_pred, onset_pred, offset_seconds)`` tuple
        per chunk, where ``offset_seconds`` is the chunk's exact start
        position in the source audio.

        The previous implementation concatenated the full frame output
        of every chunk into one timeline. Chunks advance by ``HOP``
        samples but each emits frames covering ``CHUNK_SIZE`` samples,
        so concatenation stretched all note times by
        CHUNK_SIZE / HOP = 1.2124x and duplicated the overlap regions.
        Measured on a 60s stem: last decoded onset landed at 72.5s and
        strict onset F1 vs ground truth was 0.05. Decoding each chunk
        against its true offset removes both the stretch and the
        duplicates.
        """
        audio = waveform.squeeze(0).cpu().numpy()  # (samples,)
        n_samples = len(audio)

        chunks: List[Tuple[np.ndarray, np.ndarray, float]] = []
        for start in range(0, n_samples, self.HOP):
            end = start + self.CHUNK_SIZE

            # Extract chunk, pad if needed
            if end <= n_samples:
                chunk = audio[start:end]
            else:
                # Pad last chunk
                chunk = np.zeros(self.CHUNK_SIZE, dtype=np.float32)
                chunk[:n_samples - start] = audio[start:]

            # Reshape for CoreML: (1, samples, 1)
            chunk = chunk.reshape(1, self.CHUNK_SIZE, 1).astype(np.float32)

            # Run inference
            predictions = self.model.predict({"input_2": chunk})

            note_pred = predictions["Identity"]  # (1, frames, pitches)
            onset_pred = predictions["Identity_1"]

            chunks.append((note_pred, onset_pred, start / AUDIO_SAMPLE_RATE))

        return chunks

    def _decode_notes(
        self,
        note_pred: np.ndarray,
        onset_pred: np.ndarray,
        onset_threshold: float = 0.3,
        frame_threshold: float = 0.2,
        min_note_len: int = 3,
        time_offset_s: float = 0.0,
    ) -> List[ExtractedNote]:
        """Decode neural network outputs to MIDI notes on GPU.

        ``time_offset_s`` shifts all note times by the chunk's start
        position in the source audio (chunked inference).
        """
        # Note pred may have 264 values (3x88 pitches), onset has 88
        # Use onset shape for pitch dimension
        onsets_t = torch.from_numpy(onset_pred).squeeze().to(DEVICE)  # (time, 88)
        n_frames, n_pitches = onsets_t.shape

        # If note_pred has 264 values, reshape/reduce to 88
        notes_squeezed = np.squeeze(note_pred)
        if notes_squeezed.shape[-1] == 264:
            # 264 = 88 * 3, take max across the 3 values per pitch
            notes_reshaped = notes_squeezed.reshape(notes_squeezed.shape[0], 88, 3)
            notes_reduced = notes_reshaped.max(axis=2)  # (time, 88)
            notes_t = torch.from_numpy(notes_reduced).to(DEVICE)
        else:
            notes_t = torch.from_numpy(notes_squeezed).to(DEVICE)

        # Threshold predictions
        note_mask = notes_t > frame_threshold
        onset_mask = onsets_t > onset_threshold

        # Find note events
        notes = []

        # Process on CPU for note extraction (complex logic)
        note_mask_np = note_mask.cpu().numpy()
        onset_mask_np = onset_mask.cpu().numpy()
        notes_np = notes_t.cpu().numpy()

        for pitch_idx in range(n_pitches):
            pitch = pitch_idx + MIDI_OFFSET

            # Find note regions
            in_note = False
            note_start = 0
            note_confidence = 0.0
            note_frames = 0

            for frame_idx in range(n_frames):
                is_note = note_mask_np[frame_idx, pitch_idx]
                is_onset = onset_mask_np[frame_idx, pitch_idx]

                if is_onset and not in_note:
                    # Start new note
                    in_note = True
                    note_start = frame_idx
                    note_confidence = notes_np[frame_idx, pitch_idx]
                    note_frames = 1
                elif (
                    is_onset and in_note and frame_idx > note_start
                    and not onset_mask_np[frame_idx - 1, pitch_idx]
                ):
                    # Rising-edge re-onset while a note is active: the
                    # instrument re-struck the same pitch. Close the
                    # current note and start a new one, otherwise
                    # repeated notes (arpeggios, 8th-note comping)
                    # collapse into one long note and recall craters.
                    # Rising-edge check keeps a single strike whose
                    # onset activation spans several frames from
                    # splitting repeatedly.
                    if note_frames >= min_note_len:
                        avg_confidence = note_confidence / note_frames
                        notes.append(ExtractedNote(
                            pitch=pitch,
                            start_time=time_offset_s + note_start / ANNOTATIONS_FPS,
                            end_time=time_offset_s + frame_idx / ANNOTATIONS_FPS,
                            velocity=int(40 + avg_confidence * 80),
                            confidence=avg_confidence,
                        ))
                    note_start = frame_idx
                    note_confidence = notes_np[frame_idx, pitch_idx]
                    note_frames = 1
                elif is_note and in_note:
                    # Continue note
                    note_confidence += notes_np[frame_idx, pitch_idx]
                    note_frames += 1
                elif not is_note and in_note:
                    # End note
                    if note_frames >= min_note_len:
                        avg_confidence = note_confidence / note_frames
                        notes.append(ExtractedNote(
                            pitch=pitch,
                            start_time=time_offset_s + note_start / ANNOTATIONS_FPS,
                            end_time=time_offset_s + frame_idx / ANNOTATIONS_FPS,
                            velocity=int(40 + avg_confidence * 80),
                            confidence=avg_confidence,
                        ))
                    in_note = False

            # Handle note at end
            if in_note and note_frames >= min_note_len:
                avg_confidence = note_confidence / note_frames
                notes.append(ExtractedNote(
                    pitch=pitch,
                    start_time=time_offset_s + note_start / ANNOTATIONS_FPS,
                    end_time=time_offset_s + n_frames / ANNOTATIONS_FPS,
                    velocity=int(40 + avg_confidence * 80),
                    confidence=avg_confidence,
                ))

        # Sort by start time
        notes.sort(key=lambda n: (n.start_time, n.pitch))

        return notes

    def extract(
        self,
        audio_path: str,
        onset_threshold: float = 0.3,
        frame_threshold: float = 0.2,
        min_note_len: int = 3,
    ) -> Tuple[List[ExtractedNote], float]:
        """
        Extract MIDI notes from audio using GPU acceleration.

        Defaults tuned on Slakh (BabySlakh) polyphonic "other" stems
        against per-stem MIDI ground truth (mir_eval, onset 50ms /
        pitch 50c): sweeping onset x frame x min_len showed the old
        0.5/0.3/5 defaults left most notes undetected (onset F1 0.271 /
        0.338 on Track00001/00003) while 0.3/0.2/3 scored 0.323 /
        0.492 with full-note F1 also improving.

        Args:
            audio_path: Path to audio file
            onset_threshold: Threshold for note onset detection
            frame_threshold: Threshold for note presence
            min_note_len: Minimum note length in frames

        Returns:
            Tuple of (notes, duration_seconds)
        """
        logger.info(f"Extracting MIDI with CoreML GPU from {Path(audio_path).name}")

        # 1. Load audio on GPU
        waveform, sr = self._load_audio(audio_path)
        duration = waveform.shape[1] / sr
        logger.info(f"Audio loaded: {duration:.1f}s on {DEVICE}")

        # 2. Run chunked inference on GPU/ANE
        logger.info(f"Running CoreML inference on {int(duration / 2) + 1} chunks...")
        chunks = self._run_inference_chunked(waveform)
        logger.info(f"Inference complete on GPU/ANE: {len(chunks)} chunks")

        # 3. Decode each chunk at its true audio offset, then keep each
        # note only from the chunk that "owns" its onset. Chunks overlap
        # by OVERLAP samples, so an onset near a boundary is decoded by
        # two chunks; ownership windows split the timeline at the middle
        # of each overlap region so exactly one copy survives.
        half_overlap_s = (self.OVERLAP / 2) / AUDIO_SAMPLE_RATE
        hop_s = self.HOP / AUDIO_SAMPLE_RATE
        tagged: List[Tuple[int, ExtractedNote]] = []
        for i, (note_pred, onset_pred, offset_s) in enumerate(chunks):
            chunk_notes = self._decode_notes(
                note_pred, onset_pred,
                onset_threshold=onset_threshold,
                frame_threshold=frame_threshold,
                min_note_len=min_note_len,
                time_offset_s=offset_s,
            )
            own_start = 0.0 if i == 0 else offset_s + half_overlap_s
            own_end = (
                float("inf") if i == len(chunks) - 1
                else offset_s + hop_s + half_overlap_s
            )
            tagged.extend(
                (i, n) for n in chunk_notes
                if own_start <= n.start_time < own_end
            )

        # 4. Merge notes split across chunk boundaries: a sustained note
        # decoded as (tail in chunk k) + (head in chunk k+1) shows up as
        # two same-pitch notes with a tiny gap at the boundary. Only
        # merge across DIFFERENT chunks — within one chunk a small gap
        # between same-pitch notes is a real re-struck note (the decoder
        # deliberately splits at re-onsets) and must survive.
        tagged.sort(key=lambda t: (t[1].pitch, t[1].start_time))
        merged: List[Tuple[int, ExtractedNote]] = []
        for chunk_idx, n in tagged:
            prev = merged[-1] if merged else None
            if (
                prev is not None
                and prev[0] != chunk_idx
                and prev[1].pitch == n.pitch
                and n.start_time - prev[1].end_time <= 0.05
            ):
                prev[1].end_time = max(prev[1].end_time, n.end_time)
                prev[1].confidence = max(prev[1].confidence, n.confidence)
                prev[1].velocity = max(prev[1].velocity, n.velocity)
            else:
                merged.append((chunk_idx, n))
        notes = [n for _, n in merged]
        notes.sort(key=lambda n: (n.start_time, n.pitch))
        logger.info(f"Extracted {len(notes)} notes")

        return notes, duration


def notes_to_midi_bytes(notes: List[ExtractedNote], tempo: float = 120.0) -> bytes:
    """Convert notes to MIDI file bytes."""
    import pretty_midi

    midi = pretty_midi.PrettyMIDI(initial_tempo=tempo)
    instrument = pretty_midi.Instrument(program=0, name="Extracted")

    for note in notes:
        midi_note = pretty_midi.Note(
            velocity=note.velocity,
            pitch=note.pitch,
            start=note.start_time,
            end=note.end_time,
        )
        instrument.notes.append(midi_note)

    midi.instruments.append(instrument)

    with tempfile.NamedTemporaryFile(suffix='.mid', delete=False) as f:
        midi.write(f.name)
        with open(f.name, 'rb') as mf:
            midi_bytes = mf.read()
        Path(f.name).unlink()

    return midi_bytes


# Singleton extractor
_extractor: Optional[CoreMLMIDIExtractor] = None


def get_extractor() -> CoreMLMIDIExtractor:
    """Get singleton CoreML extractor."""
    global _extractor
    if _extractor is None:
        _extractor = CoreMLMIDIExtractor()
    return _extractor


def _postprocess_notes(
    notes: List[ExtractedNote],
    stem_type: str,
    min_duration_ms: float = 50.0,
    min_velocity: int = 30,
    min_confidence: float = 0.0,
) -> List[ExtractedNote]:
    """
    Post-process extracted notes to improve accuracy.

    Args:
        notes: List of extracted notes
        stem_type: Type of stem for stem-specific filtering
        min_duration_ms: Minimum note duration in milliseconds
        min_velocity: Minimum velocity to keep
        min_confidence: Minimum CoreML confidence to keep (0.0 = no
            filter, the default — preserves legacy behavior). Tunable
            knob for callers that observe over-firing on distorted
            sources (e.g. saturated guitar produces a halo of low-
            confidence "ghost" notes from harmonics). Setting this
            above 0 trades recall for precision; recommended range
            is 0.15-0.30 if the upstream corpus shows obvious
            false-positive density. Don't crank past 0.5 — legitimate
            notes from basic_pitch frequently land in the 0.3-0.5
            band, especially short attacks.

    Returns:
        Filtered list of notes
    """
    if not notes:
        return notes

    min_duration = min_duration_ms / 1000.0

    # Stem-specific pitch ranges for octave correction
    pitch_ranges = {
        "bass": (24, 60),    # C1 to C4 - bass range
        "lead": (48, 96),    # C3 to C7 - lead range
        "pads": (36, 96),    # C2 to C7 - pad range
        "other": (36, 96),   # General range
    }

    pitch_range = pitch_ranges.get(stem_type, (24, 108))
    min_pitch, max_pitch = pitch_range

    filtered = []
    for note in notes:
        # Filter by duration
        if (note.end_time - note.start_time) < min_duration:
            continue

        # Filter by velocity
        if note.velocity < min_velocity:
            continue

        # Filter by confidence (default 0.0 = no-op, matches legacy
        # behaviour). The CoreML extractor sets ``confidence`` from the
        # mean per-frame note probability; very low values are nearly
        # always harmonic ghosts on saturated input.
        if note.confidence < min_confidence:
            continue

        # Octave correction for stem type
        pitch = note.pitch

        # Move pitch into expected range via octave shifts
        while pitch < min_pitch and pitch + 12 <= 127:
            pitch += 12
        while pitch > max_pitch and pitch - 12 >= 0:
            pitch -= 12

        # If still out of range, skip note (likely noise)
        if pitch < min_pitch or pitch > max_pitch:
            continue

        # Create new note with corrected pitch
        if pitch != note.pitch:
            filtered.append(ExtractedNote(
                pitch=pitch,
                start_time=note.start_time,
                end_time=note.end_time,
                velocity=note.velocity,
                confidence=note.confidence,
            ))
        else:
            filtered.append(note)

    return filtered


def extract_midi_coreml(
    audio_path: str,
    preset_name: str = "Extracted",
    onset_threshold: float = 0.3,
    frame_threshold: float = 0.2,
    stem_type: str = "other",
) -> dict:
    """
    Extract MIDI using CoreML GPU acceleration.

    Args:
        audio_path: Path to audio file
        preset_name: Name for the MIDI output
        onset_threshold: Note onset detection threshold
        frame_threshold: Note presence threshold
        stem_type: Type of stem for post-processing

    Returns:
        Dict with MIDI data compatible with analysis pipeline
    """
    extractor = get_extractor()
    notes, duration = extractor.extract(
        audio_path,
        onset_threshold=onset_threshold,
        frame_threshold=frame_threshold,
    )

    # Post-process notes based on stem type
    notes = _postprocess_notes(notes, stem_type)

    logger.info(f"After post-processing: {len(notes)} notes for {stem_type}")

    if len(notes) == 0:
        return {
            "filename": f"{preset_name}.mid",
            "content": "",
            "note_count": 0,
            "duration_seconds": duration,
            # Empty-result default. Renamed from ``tempo_bpm`` to make it
            # clear this is the extractor's local estimate (here: 120.0
            # placeholder because there are no notes to derive density
            # from), not the canonical session tempo.
            "extraction_tempo_bpm": 120.0,
            "pitch_range": (0, 0),
            "method": "coreml_gpu",
        }

    # Estimate tempo from note density
    note_density = len(notes) / duration
    tempo = 120.0  # Default
    if note_density > 8:
        tempo = 140.0
    elif note_density > 4:
        tempo = 120.0
    else:
        tempo = 100.0

    midi_bytes = notes_to_midi_bytes(notes, tempo)
    midi_b64 = base64.b64encode(midi_bytes).decode('ascii')

    pitches = [n.pitch for n in notes]

    return {
        "filename": f"{preset_name}.mid",
        "content": midi_b64,
        "note_count": len(notes),
        "duration_seconds": duration,
        # Heuristic 3-bucket tempo from note density (100/120/140). Per
        # the rename rationale at the empty-result branch: this is the
        # extractor's local estimate, not the canonical session tempo.
        "extraction_tempo_bpm": tempo,
        "pitch_range": (min(pitches), max(pitches)),
        "method": "coreml_gpu",
    }
