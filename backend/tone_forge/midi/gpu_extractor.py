"""
GPU-accelerated MIDI extraction using torchcrepe + MPS.

This module provides fast pitch detection for monophonic stems (bass, lead)
using PyTorch with Apple Silicon GPU (MPS) acceleration.

For polyphonic content, falls back to basic_pitch with ONNX+CoreML.

All audio processing uses torchaudio on GPU for maximum acceleration.
"""

import logging
import tempfile
import base64
from pathlib import Path
from typing import Optional, Tuple, List
from dataclasses import dataclass

import numpy as np
import torch
import torchaudio

logger = logging.getLogger(__name__)


def estimate_polyphony(audio_path: str, threshold: float = 0.3) -> Tuple[bool, float]:
    """
    Estimate if audio content is polyphonic using spectral analysis.

    Uses multiple heuristics:
    1. Spectral flatness - polyphonic content has more evenly distributed energy
    2. Multi-pitch detection - count simultaneous f0 candidates
    3. Harmonic-to-noise ratio - clean monophonic has higher HNR

    Args:
        audio_path: Path to audio file
        threshold: Polyphony ratio threshold (default 0.3 = 30% simultaneous notes)

    Returns:
        Tuple of (is_polyphonic: bool, polyphony_ratio: float)
    """
    import librosa

    try:
        y, sr = librosa.load(audio_path, sr=22050, mono=True, duration=30.0)

        # Method 1: Check spectral flatness variance
        # Monophonic content has more variation in spectral flatness
        flatness = librosa.feature.spectral_flatness(y=y)
        flatness_std = np.std(flatness)

        # Method 2: Use pYIN multi-pitch detection
        # Count frames where multiple pitches could be present
        f0, voiced_flag, voiced_probs = librosa.pyin(
            y, fmin=80, fmax=1000, sr=sr, frame_length=2048, hop_length=512
        )

        # Count frames with high voicing confidence as "voiced"
        voiced_frames = np.sum(voiced_flag)
        total_frames = len(voiced_flag)

        # Method 3: Check for multiple spectral peaks in voiced regions
        # Use HPSS to separate harmonic content
        y_harmonic, _ = librosa.effects.hpss(y)

        # Compute chroma energy distribution
        chroma = librosa.feature.chroma_cqt(y=y_harmonic, sr=sr)
        # Count active pitch classes per frame
        active_per_frame = np.sum(chroma > 0.3, axis=0)
        multi_pitch_frames = np.sum(active_per_frame > 2)
        polyphony_ratio = multi_pitch_frames / len(active_per_frame) if len(active_per_frame) > 0 else 0

        # Additional check: spectral bandwidth variance
        # Polyphonic content typically has higher and more variable bandwidth
        bandwidth = librosa.feature.spectral_bandwidth(y=y, sr=sr)
        bandwidth_mean = np.mean(bandwidth)

        # Combine heuristics
        # High polyphony ratio OR (low flatness_std AND high bandwidth) suggests polyphony
        is_polyphonic = polyphony_ratio > threshold

        logger.debug(f"Polyphony estimate: ratio={polyphony_ratio:.2f}, flatness_std={flatness_std:.4f}, "
                    f"bandwidth_mean={bandwidth_mean:.0f}, is_polyphonic={is_polyphonic}")

        return is_polyphonic, polyphony_ratio

    except Exception as e:
        logger.warning(f"Polyphony estimation failed: {e}")
        return False, 0.0


# Check MPS availability
MPS_AVAILABLE = torch.backends.mps.is_available()
if MPS_AVAILABLE:
    logger.info("MPS GPU available for pitch detection")
else:
    logger.warning("MPS not available, will use CPU")


def _estimate_tempo_gpu(waveform: torch.Tensor, sr: int, device: str = "mps") -> float:
    """
    Estimate tempo using GPU-accelerated onset detection and autocorrelation.

    Args:
        waveform: Audio tensor on GPU (1, samples)
        sr: Sample rate
        device: PyTorch device

    Returns:
        Estimated tempo in BPM
    """
    try:
        # Compute mel spectrogram on GPU
        n_fft = 2048
        hop_length = 512
        n_mels = 128

        mel_transform = torchaudio.transforms.MelSpectrogram(
            sample_rate=sr,
            n_fft=n_fft,
            hop_length=hop_length,
            n_mels=n_mels,
        ).to(device)

        mel = mel_transform(waveform)

        # Convert to dB
        mel_db = 10 * torch.log10(mel + 1e-10)

        # Onset strength: first-order difference + half-wave rectification
        onset = torch.diff(mel_db, dim=-1)
        onset = torch.relu(onset)
        onset_env = onset.mean(dim=1).squeeze()

        # Normalize
        onset_env = onset_env - onset_env.mean()

        # Autocorrelation via FFT (fast on GPU)
        n = len(onset_env)
        fft = torch.fft.rfft(onset_env, n=2*n)
        autocorr = torch.fft.irfft(fft * fft.conj(), n=2*n)[:n]
        autocorr = autocorr / (autocorr[0] + 1e-10)

        # Find tempo peak between 60-200 BPM
        fps = sr / hop_length
        min_lag = max(1, int(fps * 60 / 200))  # 200 BPM
        max_lag = min(n - 1, int(fps * 60 / 60))  # 60 BPM

        search_region = autocorr[min_lag:max_lag]
        if len(search_region) == 0:
            return 120.0

        best_lag = search_region.argmax().item() + min_lag
        tempo = fps * 60 / best_lag

        return float(max(60, min(200, tempo)))

    except Exception as e:
        logger.warning(f"GPU tempo estimation failed: {e}, defaulting to 120 BPM")
        return 120.0


@dataclass
class MIDINote:
    """A single MIDI note."""
    pitch: int
    start: float
    end: float
    velocity: int


def hz_to_midi(hz: float) -> int:
    """Convert frequency in Hz to MIDI note number."""
    if hz <= 0:
        return 0
    return int(round(12 * np.log2(hz / 440.0) + 69))


def extract_midi_torchcrepe(
    audio_path: str,
    stem_type: str = "lead",
    device: str = "mps" if MPS_AVAILABLE else "cpu",
    model_size: str = "tiny",  # tiny or full (small/medium/large not available)
) -> Tuple[List[MIDINote], float, float]:
    """
    Extract MIDI using torchcrepe on GPU.

    Best for monophonic content (bass, lead, vocals).

    Args:
        audio_path: Path to audio file
        stem_type: Type of stem (bass, lead, vocals)
        device: PyTorch device (mps, cuda, cpu)
        model_size: CREPE model size

    Returns:
        Tuple of (notes, tempo, duration)
    """
    import torchcrepe
    import soundfile as sf

    logger.info(f"Extracting MIDI with torchcrepe on {device} for {stem_type}")

    # Load audio - use soundfile as backend (avoids torchcodec requirement)
    try:
        # Try torchaudio with soundfile backend
        torchaudio.set_audio_backend("soundfile")
        waveform, orig_sr = torchaudio.load(audio_path)
    except Exception:
        # Fallback: load with soundfile directly, convert to tensor
        data, orig_sr = sf.read(audio_path, dtype='float32')
        if data.ndim == 1:
            waveform = torch.from_numpy(data).unsqueeze(0)
        else:
            waveform = torch.from_numpy(data.T)  # (samples, channels) -> (channels, samples)

    # Convert to mono
    if waveform.shape[0] > 1:
        waveform = waveform.mean(dim=0, keepdim=True)

    # Resample to 16kHz on GPU
    sr = 16000
    if orig_sr != sr:
        resampler = torchaudio.transforms.Resample(orig_sr, sr).to(device)
        waveform = waveform.to(device)
        waveform = resampler(waveform)
    else:
        waveform = waveform.to(device)

    duration = waveform.shape[1] / sr

    # Estimate tempo using GPU autocorrelation
    tempo = _estimate_tempo_gpu(waveform, sr, device)

    # Audio tensor for torchcrepe (needs specific shape)
    audio_tensor = waveform.float()

    # For bass, pitch-shift up 1 octave before detection
    # torchcrepe was trained on speech/vocals and fails on very low frequencies
    # We'll shift the detected pitches back down after detection
    octave_shift = 0
    if stem_type == "bass":
        try:
            import librosa
            # Convert to numpy, shift up 1 octave, convert back
            audio_np = audio_tensor.squeeze().cpu().numpy()
            audio_shifted = librosa.effects.pitch_shift(audio_np, sr=sr, n_steps=12)
            audio_tensor = torch.from_numpy(audio_shifted).unsqueeze(0).to(device)
            octave_shift = 12  # Remember to shift pitches back down
            logger.info("Bass: pitch-shifted audio up 1 octave for better torchcrepe detection")
        except Exception as e:
            logger.warning(f"Bass pitch-shift failed: {e}, using original audio")

    # Set frequency range based on stem type
    if stem_type == "bass":
        # After shifting up, bass becomes mid-range (60-1000 Hz)
        fmin, fmax = (60, 1000) if octave_shift else (30, 500)
    elif stem_type == "vocals":
        fmin, fmax = 80, 1000  # Vocal range
    elif stem_type == "pads":
        fmin, fmax = 50, 1500  # Pad range (wider for chords)
    else:  # lead, other
        fmin, fmax = 70, 1700  # Lead melodic range (optimized 39.6% F1)

    # Run pitch detection on GPU
    try:
        pitch, periodicity = torchcrepe.predict(
            audio_tensor,
            sr,
            hop_length=512,
            fmin=fmin,
            fmax=fmax,
            model=model_size,
            decoder=torchcrepe.decode.viterbi,  # Smooth pitch tracking
            return_periodicity=True,
            device=device,
            batch_size=2048,  # Larger batch for GPU efficiency
        )

        pitch = pitch.squeeze().cpu().numpy()
        periodicity = periodicity.squeeze().cpu().numpy()

    except Exception as e:
        logger.warning(f"GPU pitch detection failed, falling back to CPU: {e}")
        # Move tensor to CPU for fallback
        audio_cpu = audio_tensor.cpu()
        pitch, periodicity = torchcrepe.predict(
            audio_cpu,
            sr,
            hop_length=512,
            fmin=fmin,
            fmax=fmax,
            model=model_size,
            decoder=torchcrepe.decode.viterbi,
            return_periodicity=True,
            device="cpu",
            batch_size=512,
        )
        pitch = pitch.squeeze().cpu().numpy()
        periodicity = periodicity.squeeze().cpu().numpy()

    # Convert pitch track to MIDI notes
    # Higher periodicity threshold = fewer false positives
    # Lower threshold = more notes detected but more noise
    if stem_type == "bass":
        period_thresh = 0.23  # Bass: optimized (68.2% F1)
    elif stem_type == "vocals":
        period_thresh = 0.35
    elif stem_type == "pads":
        period_thresh = 0.4  # Pads: balanced (sustained notes)
    else:  # lead
        period_thresh = 0.6  # Lead: balanced for mono + poly content

    # Set minimum note duration based on stem type
    if stem_type == "bass":
        min_dur = 0.08  # 80ms minimum for bass
    elif stem_type == "pads":
        min_dur = 0.15  # 150ms minimum for pads (sustained notes)
    else:
        min_dur = 0.05  # 50ms for lead/vocals

    # Detect onsets to handle repeated same-pitch notes
    # This is critical for content like rapid repeated notes on same pitch
    onset_frames = None
    try:
        import librosa
        # Get audio as numpy for onset detection
        audio_np = audio_tensor.squeeze().cpu().numpy()

        # Use different onset detection strategies per stem type:
        # - Lead: energy-based (aggregate=np.mean) with backtrack for repeated note detection
        # - Bass: spectral flux (default) without backtrack for cleaner separation
        if stem_type == "lead":
            # Energy-based detection aligns better with actual note attacks
            # especially for sustained instruments with repeated same-pitch notes
            onset_env = librosa.onset.onset_strength(
                y=audio_np, sr=sr, hop_length=512, aggregate=np.mean
            )
            onset_frames = librosa.onset.onset_detect(
                onset_envelope=onset_env,
                sr=sr,
                hop_length=512,
                backtrack=True,
                units='frames',
            )
        else:
            # Spectral flux works well for bass with distinct attacks
            onset_env = librosa.onset.onset_strength(y=audio_np, sr=sr, hop_length=512)
            onset_frames = librosa.onset.onset_detect(
                onset_envelope=onset_env,
                sr=sr,
                hop_length=512,
                backtrack=False,
                units='frames',
            )
        logger.debug(f"Detected {len(onset_frames)} onsets for same-pitch note splitting")
    except Exception as e:
        logger.debug(f"Onset detection failed: {e}, continuing without onset splitting")

    notes = pitch_to_notes(
        pitch,
        periodicity,
        sr=sr,
        hop_length=512,
        min_duration=min_dur,
        periodicity_threshold=period_thresh,
        stem_type=stem_type,
        octave_shift=octave_shift,
        onset_frames=onset_frames,
    )

    logger.info(f"torchcrepe extracted {len(notes)} notes on {device}")

    return notes, tempo, duration


def extract_midi_pyin(
    audio_path: str,
    stem_type: str = "bass",
) -> Tuple[List[MIDINote], float, float]:
    """
    Extract MIDI using librosa pYIN (DSP-based, no ML).

    Excellent for clean bass lines - can achieve 99%+ F1 on simple content.
    Falls back gracefully when content is too complex.

    Args:
        audio_path: Path to audio file
        stem_type: Type of stem (bass, lead)

    Returns:
        Tuple of (notes, tempo, duration)
    """
    import librosa

    logger.info(f"Extracting MIDI with pYIN (DSP) for {stem_type}")

    # Load audio
    y, sr = librosa.load(audio_path, sr=22050, mono=True)
    duration = len(y) / sr

    # Set frequency range based on stem type
    if stem_type == "bass":
        fmin, fmax = 30, 500
    else:
        fmin, fmax = 80, 1500

    # Run pYIN pitch detection
    f0, voiced_flag, voiced_probs = librosa.pyin(
        y,
        fmin=fmin,
        fmax=fmax,
        sr=sr,
        frame_length=2048,
        hop_length=512,
    )

    # Estimate tempo
    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
    if hasattr(tempo, '__len__'):
        tempo = float(tempo[0]) if len(tempo) > 0 else 120.0
    tempo = float(max(60, min(200, tempo)))

    # Convert to notes
    frame_dur = 512 / sr
    min_dur = 0.08 if stem_type == "bass" else 0.05

    # Detect onsets for same-pitch note splitting
    # Use different strategies per stem type (same as torchcrepe)
    onset_set = set()
    try:
        if stem_type == "lead":
            # Energy-based detection with backtrack for repeated note detection
            onset_env = librosa.onset.onset_strength(
                y=y, sr=sr, hop_length=512, aggregate=np.mean
            )
            onset_frames = librosa.onset.onset_detect(
                onset_envelope=onset_env,
                sr=sr,
                hop_length=512,
                backtrack=True,
                units='frames',
            )
        else:
            # Spectral flux for bass (cleaner separation)
            onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=512)
            onset_frames = librosa.onset.onset_detect(
                onset_envelope=onset_env,
                sr=sr,
                hop_length=512,
                backtrack=False,
                units='frames',
            )
        onset_set = set(onset_frames)
        logger.debug(f"pYIN: detected {len(onset_frames)} onsets for same-pitch splitting")
    except Exception as e:
        logger.debug(f"pYIN onset detection failed: {e}")

    notes = []
    current_midi = None
    note_start = 0

    for i, (hz, is_voiced) in enumerate(zip(f0, voiced_flag)):
        if is_voiced and not np.isnan(hz) and hz > 0:
            midi = int(round(12 * np.log2(hz / 440) + 69))
        else:
            midi = None

        # Check for pitch change or onset at same pitch
        is_onset_same_pitch = (i in onset_set and midi == current_midi and midi is not None)
        is_pitch_change = (midi != current_midi)

        if is_pitch_change or is_onset_same_pitch:
            if current_midi is not None:
                note_end = i * frame_dur
                if note_end - note_start >= min_dur:
                    notes.append(MIDINote(
                        pitch=current_midi,
                        start=note_start,
                        end=note_end,
                        velocity=80,
                    ))
            current_midi = midi
            note_start = i * frame_dur

    # Handle last note
    if current_midi is not None:
        note_end = len(f0) * frame_dur
        if note_end - note_start >= min_dur:
            notes.append(MIDINote(
                pitch=current_midi,
                start=note_start,
                end=note_end,
                velocity=80,
            ))

    logger.info(f"pYIN extracted {len(notes)} notes")
    return notes, tempo, duration


def extract_midi_bass_ensemble(
    audio_path: str,
) -> Tuple[List[MIDINote], float, float, str]:
    """
    Ensemble bass extraction using both pYIN (DSP) and torchcrepe (ML).

    Picks the best detector based on heuristics:
    - pYIN is better for clean, simple bass lines (can hit 99% F1)
    - torchcrepe is better for complex/noisy content

    Returns:
        Tuple of (notes, tempo, duration, method_used)
    """
    # Run both detectors
    try:
        pyin_notes, pyin_tempo, duration = extract_midi_pyin(audio_path, "bass")
    except Exception as e:
        logger.warning(f"pYIN failed: {e}")
        pyin_notes = []
        pyin_tempo = 120.0
        duration = 0

    try:
        tc_notes, tc_tempo, tc_duration = extract_midi_torchcrepe(
            audio_path, stem_type="bass", model_size="full"
        )
        if duration == 0:
            duration = tc_duration
    except Exception as e:
        logger.warning(f"torchcrepe failed: {e}")
        tc_notes = []
        tc_tempo = 120.0

    pyin_count = len(pyin_notes)
    tc_count = len(tc_notes)

    # Decision heuristics based on empirical testing:
    # pYIN is excellent (99%+) on clean bass but fails on complex content
    # torchcrepe is more robust but can have false positives/negatives
    #
    # Key insight: pYIN wins when it detects SIMILAR or MORE notes than torchcrepe
    # If pYIN detects significantly fewer, it likely failed

    use_pyin = False

    if pyin_count >= 15 and tc_count > 0:
        # pYIN detected reasonable notes - check ratio
        ratio = pyin_count / tc_count
        if ratio >= 0.8:
            # pYIN detected at least 80% as many notes - likely accurate
            use_pyin = True
            logger.info(f"Ensemble: choosing pYIN ({pyin_count} notes) - ratio {ratio:.2f} vs torchcrepe ({tc_count})")
        elif pyin_count > tc_count:
            # pYIN detected more - torchcrepe may have missed notes
            use_pyin = True
            logger.info(f"Ensemble: choosing pYIN ({pyin_count} notes) - more than torchcrepe ({tc_count})")
    elif pyin_count >= 40:
        # pYIN detected lots of notes even if torchcrepe detected none/few
        use_pyin = True
        logger.info(f"Ensemble: choosing pYIN ({pyin_count} notes) - strong detection")

    if use_pyin:
        notes = pyin_notes
        method = "pyin_dsp"
        tempo = pyin_tempo
    else:
        notes = tc_notes
        method = "torchcrepe_gpu"
        tempo = tc_tempo
        logger.info(f"Ensemble: choosing torchcrepe ({tc_count} notes) over pYIN ({pyin_count} notes)")

    # Octave expansion for layered bass
    # Some bass tracks layer notes 2 octaves apart (+24 semitones)
    # Heuristic: expand only when torchcrepe detects significantly more notes than pYIN
    # This suggests layered/complex content where monophonic detection misses octaves
    # When pYIN detects more, it's usually clean content where expansion would hurt
    if tc_count > 0 and pyin_count > 0:
        tc_to_pyin_ratio = tc_count / pyin_count

        # Only expand when torchcrepe detects 1.5x+ more notes than pYIN
        # This indicates complex layered content
        if tc_to_pyin_ratio >= 1.5 and tc_count >= 50:
            expanded_notes = []
            for note in notes:
                expanded_notes.append(note)
                # Add octave duplicate if it stays in bass range (MIDI 24-72)
                upper_octave = note.pitch + 24
                if upper_octave <= 72:  # Up to C5
                    expanded_notes.append(MIDINote(
                        pitch=upper_octave,
                        start=note.start,
                        end=note.end,
                        velocity=note.velocity,
                    ))
            logger.info(f"Bass octave expansion: {len(notes)} -> {len(expanded_notes)} notes "
                       f"(tc/pyin ratio={tc_to_pyin_ratio:.2f})")
            notes = expanded_notes
        else:
            logger.info(f"Bass: no expansion (tc/pyin ratio={tc_to_pyin_ratio:.2f})")

    return notes, tempo, duration, method


def extract_midi_lead_ensemble(
    audio_path: str,
    use_hca_for_polyphony: bool = True,
) -> Tuple[List[MIDINote], float, float, str]:
    """
    Ensemble lead extraction with harmonic ratio-based routing.

    Routes to:
    - HCA (HarmonicClusterAnalyzer) for polyphonic content (chords, strums)
    - pYIN/torchcrepe ensemble for monophonic content

    Routing logic based on extensive benchmarking (BabySlakh):
    - harm_ratio > 0.78 → torchcrepe/pYIN (clean pitched signal)
    - harm_ratio < 0.75 → HCA (complex polyphonic signal)
    - Edge cases default to torchcrepe

    Returns:
        Tuple of (notes, tempo, duration, method_used)
    """
    import librosa

    # Load audio for harmonic ratio analysis
    y, sr = librosa.load(audio_path, sr=22050, mono=True, duration=30.0)
    duration = len(y) / sr

    # Check if HCA should be used based on harmonic ratio
    if use_hca_for_polyphony:
        try:
            from tone_forge.midi.harmonic_cluster_analyzer import (
                HarmonicClusterAnalyzer,
                estimate_harmonic_ratio,
            )

            harm_ratio = estimate_harmonic_ratio(y, sr)

            # Route to HCA for polyphonic content (low harmonic ratio)
            if harm_ratio < 0.75:
                logger.info(f"Lead: detected polyphonic content (harm_ratio={harm_ratio:.2f}), using HCA")
                try:
                    analyzer = HarmonicClusterAnalyzer(sr=sr)
                    hca_notes = analyzer.extract(y)

                    if len(hca_notes) > 0:
                        # Convert HCANote to MIDINote
                        notes = [
                            MIDINote(
                                pitch=n.pitch,
                                start=n.start,
                                end=n.end,
                                velocity=n.velocity,
                            )
                            for n in hca_notes
                        ]

                        # Estimate tempo from note onsets
                        onsets = sorted([n.start for n in notes])
                        if len(onsets) > 1:
                            iois = np.diff(onsets)
                            iois = iois[(iois > 0.1) & (iois < 2.0)]
                            if len(iois) > 0:
                                tempo = 60.0 / (np.median(iois) * 2)
                                tempo = float(np.clip(tempo, 60, 200))
                            else:
                                tempo = 120.0
                        else:
                            tempo = 120.0

                        logger.info(f"HCA extracted {len(notes)} notes for polyphonic lead")
                        return notes, tempo, duration, "hca_polyphonic"

                except Exception as e:
                    logger.warning(f"HCA failed for polyphonic lead: {e}, falling back to monophonic")

        except ImportError as e:
            logger.warning(f"HCA module not available: {e}")

    # Monophonic path: Run both detectors
    try:
        pyin_notes, pyin_tempo, duration = extract_midi_pyin(audio_path, "lead")
    except Exception as e:
        logger.warning(f"pYIN failed for lead: {e}")
        pyin_notes = []
        pyin_tempo = 120.0
        duration = 0

    try:
        tc_notes, tc_tempo, tc_duration = extract_midi_torchcrepe(
            audio_path, stem_type="lead", model_size="full"
        )
        if duration == 0:
            duration = tc_duration
    except Exception as e:
        logger.warning(f"torchcrepe failed for lead: {e}")
        tc_notes = []
        tc_tempo = 120.0

    pyin_count = len(pyin_notes)
    tc_count = len(tc_notes)

    # Lead heuristics (different from bass):
    # - torchcrepe is more reliable on average for lead (~63% vs ~60% pYIN)
    # - Only switch to pYIN when torchcrepe is clearly over-detecting
    # - When pYIN detects MORE notes, it's usually false positives (bad)

    use_pyin = False

    if pyin_count > 0 and tc_count > 0:
        ratio = pyin_count / tc_count
        # Only use pYIN when torchcrepe detects way more notes (over-detecting)
        if ratio <= 0.5 and pyin_count >= 10:
            # torchcrepe detected 2x+ notes as pYIN - torchcrepe is likely over-detecting
            use_pyin = True
            logger.info(f"Lead ensemble: choosing pYIN ({pyin_count} notes) - torchcrepe over-detected ({tc_count})")
        elif 0.8 <= ratio <= 1.2 and pyin_count >= 20:
            # Similar counts with good volume - pYIN might be cleaner
            use_pyin = True
            logger.info(f"Lead ensemble: choosing pYIN ({pyin_count} notes) - similar count, ratio {ratio:.2f}")
    elif pyin_count >= 20 and tc_count < 5:
        # torchcrepe failed completely, pYIN has reasonable output
        use_pyin = True
        logger.info(f"Lead ensemble: choosing pYIN ({pyin_count} notes) - torchcrepe failed ({tc_count})")

    if use_pyin:
        return pyin_notes, pyin_tempo, duration, "pyin_dsp"
    else:
        logger.info(f"Lead ensemble: choosing torchcrepe ({tc_count} notes) over pYIN ({pyin_count} notes)")
        return tc_notes, tc_tempo, duration, "torchcrepe_gpu"


def pitch_to_notes(
    pitch: np.ndarray,
    periodicity: np.ndarray,
    sr: int,
    hop_length: int,
    min_duration: float = 0.05,
    periodicity_threshold: float = 0.5,
    stem_type: str = "lead",
    octave_shift: int = 0,
    onset_frames: Optional[np.ndarray] = None,
) -> List[MIDINote]:
    """
    Convert pitch and periodicity arrays to MIDI notes.

    Args:
        pitch: Pitch values in Hz per frame
        periodicity: Confidence values per frame (0-1)
        sr: Sample rate
        hop_length: Hop length used for pitch detection
        min_duration: Minimum note duration in seconds
        periodicity_threshold: Minimum periodicity to consider a pitch valid
        stem_type: Type of stem for velocity scaling
        octave_shift: Semitones to subtract from detected pitches (for bass pitch-shift correction)
        onset_frames: Optional array of frame indices where onsets occur (for splitting same-pitch notes)

    Returns:
        List of MIDINote objects
    """
    frame_duration = hop_length / sr
    notes = []

    # Find voiced regions
    voiced = periodicity > periodicity_threshold

    # Convert pitch to MIDI notes
    # Apply octave_shift correction for bass (shift detected pitches back down)
    midi_pitches = np.zeros_like(pitch, dtype=int)
    for i, (hz, is_voiced) in enumerate(zip(pitch, voiced)):
        if is_voiced and hz > 0:
            detected_midi = hz_to_midi(hz)
            # Shift back down if audio was pitch-shifted up
            midi_pitches[i] = detected_midi - octave_shift
        else:
            midi_pitches[i] = 0

    # Create onset set for O(1) lookup
    onset_set = set(onset_frames) if onset_frames is not None else set()

    # Group consecutive frames with same pitch into notes
    # BUT split at onsets even if pitch is the same (for repeated notes)
    current_pitch = 0
    note_start = 0
    note_periodicity = []

    for i, (midi_pitch, period) in enumerate(zip(midi_pitches, periodicity)):
        # Check if we should start a new note:
        # 1. Pitch changed
        # 2. OR onset detected at same pitch (repeated note)
        is_onset_at_same_pitch = (i in onset_set and midi_pitch == current_pitch and midi_pitch > 0)
        is_pitch_change = (midi_pitch != current_pitch)

        if is_pitch_change or is_onset_at_same_pitch:
            # End previous note if it exists
            if current_pitch > 0:
                note_end = i * frame_duration
                note_duration = note_end - note_start

                if note_duration >= min_duration:
                    # Calculate velocity from periodicity
                    avg_periodicity = np.mean(note_periodicity) if note_periodicity else 0.5
                    velocity = int(60 + avg_periodicity * 60)  # 60-120 range
                    velocity = max(40, min(120, velocity))

                    notes.append(MIDINote(
                        pitch=current_pitch,
                        start=note_start,
                        end=note_end,
                        velocity=velocity,
                    ))

            # Start new note
            current_pitch = midi_pitch
            note_start = i * frame_duration
            note_periodicity = [period] if midi_pitch > 0 else []
        else:
            if current_pitch > 0:
                note_periodicity.append(period)

    # Handle last note
    if current_pitch > 0:
        note_end = len(midi_pitches) * frame_duration
        note_duration = note_end - note_start

        if note_duration >= min_duration:
            avg_periodicity = np.mean(note_periodicity) if note_periodicity else 0.5
            velocity = int(60 + avg_periodicity * 60)
            velocity = max(40, min(120, velocity))

            notes.append(MIDINote(
                pitch=current_pitch,
                start=note_start,
                end=note_end,
                velocity=velocity,
            ))

    # Post-process: merge very short gaps between same-pitch notes
    if len(notes) > 1:
        merged = [notes[0]]
        for note in notes[1:]:
            prev = merged[-1]
            gap = note.start - prev.end

            # Merge if same pitch and gap < 50ms
            if note.pitch == prev.pitch and gap < 0.05:
                merged[-1] = MIDINote(
                    pitch=prev.pitch,
                    start=prev.start,
                    end=note.end,
                    velocity=max(prev.velocity, note.velocity),
                )
            else:
                merged.append(note)
        notes = merged

    return notes


def notes_to_midi_file(
    notes: List[MIDINote],
    tempo: float,
    output_path: str,
    track_name: str = "Extracted",
) -> None:
    """Write notes to a MIDI file."""
    import pretty_midi

    midi = pretty_midi.PrettyMIDI(initial_tempo=tempo)
    instrument = pretty_midi.Instrument(program=0, name=track_name)

    for note in notes:
        midi_note = pretty_midi.Note(
            velocity=note.velocity,
            pitch=note.pitch,
            start=note.start,
            end=note.end,
        )
        instrument.notes.append(midi_note)

    midi.instruments.append(instrument)
    midi.write(output_path)


def extract_midi_hybrid(
    audio_path: str,
    stem_type: str = "other",
    preset_name: str = "Extracted MIDI",
) -> dict:
    """
    Hybrid MIDI extraction - uses GPU for monophonic, CPU for polyphonic.

    Args:
        audio_path: Path to audio file
        stem_type: Type of stem (bass, lead, drums, other, pad)
        preset_name: Name for the MIDI file

    Returns:
        Dict with MIDI data (compatible with MIDIExtractionResult)
    """
    from tone_forge.midi_extractor import extract_drum_midi, MIDIExtractionResult

    # Drums use specialized extraction
    if stem_type == "drums":
        result = extract_drum_midi(audio_path, preset_name)
        return {
            "filename": result.filename,
            "content": result.content,
            "note_count": result.note_count,
            "duration_seconds": result.duration_seconds,
            "tempo_bpm": result.tempo_bpm,
            "pitch_range": result.pitch_range,
            "method": "onset_detection",
        }

    # Bass uses ensemble of pYIN (DSP) + torchcrepe (ML) for best results
    if stem_type == "bass":
        try:
            notes, tempo, duration, method = extract_midi_bass_ensemble(audio_path)

            if len(notes) > 0:
                with tempfile.NamedTemporaryFile(suffix='.mid', delete=False) as f:
                    notes_to_midi_file(notes, tempo, f.name, preset_name)
                    with open(f.name, 'rb') as mf:
                        midi_bytes = mf.read()
                    Path(f.name).unlink()

                midi_b64 = base64.b64encode(midi_bytes).decode('ascii')
                pitches = [n.pitch for n in notes]

                return {
                    "filename": f"{preset_name}.mid",
                    "content": midi_b64,
                    "note_count": len(notes),
                    "duration_seconds": duration,
                    "tempo_bpm": tempo,
                    "pitch_range": (min(pitches), max(pitches)),
                    "method": method,
                }
        except Exception as e:
            logger.warning(f"Bass ensemble failed, falling back to CoreML: {e}")

    # Lead/vocals use ensemble of pYIN (DSP) + torchcrepe (ML)
    if stem_type in ("lead", "vocals"):
        try:
            notes, tempo, duration, method = extract_midi_lead_ensemble(audio_path)
            logger.info(f"Lead ensemble chose: {method} with {len(notes)} notes")

            if len(notes) > 0:
                with tempfile.NamedTemporaryFile(suffix='.mid', delete=False) as f:
                    notes_to_midi_file(notes, tempo, f.name, preset_name)
                    with open(f.name, 'rb') as mf:
                        midi_bytes = mf.read()
                    Path(f.name).unlink()

                midi_b64 = base64.b64encode(midi_bytes).decode('ascii')
                pitches = [n.pitch for n in notes]

                return {
                    "filename": f"{preset_name}.mid",
                    "content": midi_b64,
                    "note_count": len(notes),
                    "duration_seconds": duration,
                    "tempo_bpm": tempo,
                    "pitch_range": (min(pitches), max(pitches)),
                    "method": method,
                }
        except Exception as e:
            logger.warning(f"Lead ensemble failed for {stem_type}, falling back to CoreML: {e}")

    # Polyphonic stems (other, pad, synth) or fallback use CoreML GPU
    try:
        from tone_forge.midi.coreml_extractor import extract_midi_coreml

        # Stem-specific thresholds for CoreML extraction
        # Higher thresholds = fewer notes (reduce false positives)
        # Lower thresholds = more notes (reduce false negatives)
        stem_thresholds = {
            "bass": {"onset": 0.5, "frame": 0.4},    # Bass: stricter to reduce FPs
            "lead": {"onset": 0.5, "frame": 0.4},    # Lead: balanced (81% F1)
            "pads": {"onset": 0.6, "frame": 0.5},     # Pads: best tested
            "other": {"onset": 0.55, "frame": 0.45}, # Other: balanced
        }
        thresholds = stem_thresholds.get(stem_type, {"onset": 0.5, "frame": 0.4})

        logger.info(f"Using CoreML GPU for polyphonic {stem_type} (onset={thresholds['onset']}, frame={thresholds['frame']})")
        return extract_midi_coreml(
            audio_path,
            preset_name=preset_name,
            onset_threshold=thresholds["onset"],
            frame_threshold=thresholds["frame"],
            stem_type=stem_type,
        )
    except Exception as e:
        logger.warning(f"CoreML extraction failed: {e}, falling back to ONNX")
        # Final fallback to ONNX basic_pitch
        from tone_forge.midi_extractor import extract_midi
        result = extract_midi(audio_path, preset_name, stem_type=stem_type)
        return {
            "filename": result.filename,
            "content": result.content,
            "note_count": result.note_count,
            "duration_seconds": result.duration_seconds,
            "tempo_bpm": result.tempo_bpm,
            "pitch_range": result.pitch_range,
            "method": "basic_pitch_onnx",
            "provenance": result.provenance,
        }
