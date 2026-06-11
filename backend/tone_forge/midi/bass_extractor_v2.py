"""
Advanced Bass Extraction Pipeline v2

This module implements fundamental improvements to bass MIDI extraction:
1. Content-aware analysis before detection
2. Confidence-weighted ensemble combination
3. Adaptive onset detection
4. Note boundary refinement

Target: 80% average F1 on benchmark samples.
"""

import logging
from dataclasses import dataclass, field
from typing import List, Tuple, Optional, Dict
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class ContentProfile:
    """Audio content characteristics for routing decisions."""
    # Temporal characteristics
    note_density: float = 0.0  # Notes per second estimate
    is_sparse: bool = False  # Few, long notes (pad-like)
    is_dense: bool = False  # Many, short notes (rhythmic)

    # Spectral characteristics
    spectral_centroid: float = 0.0  # Hz
    spectral_flatness: float = 0.0  # 0-1
    harmonic_ratio: float = 0.0  # 0-1, higher = more harmonic

    # Pitch characteristics
    estimated_pitch_range: Tuple[float, float] = (0.0, 0.0)  # Hz
    is_sub_bass: bool = False  # Dominant energy below 80Hz

    # Complexity indicators
    polyphony_score: float = 0.0  # 0-1, likelihood of polyphonic content
    transient_strength: float = 0.0  # 0-1, strength of note attacks

    # Routing recommendation
    recommended_detector: str = "pyin"
    confidence: float = 0.0


@dataclass
class DetectorResult:
    """Result from a single detector with confidence scores."""
    detector_name: str
    notes: List['NoteCandidate'] = field(default_factory=list)
    global_confidence: float = 0.0  # Overall confidence in this detector's output
    median_pitch: int = 0
    note_count: int = 0


@dataclass
class NoteCandidate:
    """A note candidate with confidence and provenance."""
    pitch: int
    start: float
    end: float
    velocity: int
    confidence: float  # 0-1 confidence in this note
    detector: str  # Which detector found this note

    # For boundary refinement
    onset_confidence: float = 0.0
    offset_confidence: float = 0.0


def analyze_content(audio_path: str) -> ContentProfile:
    """
    Analyze audio content characteristics before running detectors.

    This enables intelligent routing and parameter selection.
    """
    import librosa

    profile = ContentProfile()

    try:
        # Load audio (full file for comprehensive analysis)
        y, sr = librosa.load(audio_path, sr=22050, mono=True)
        duration = len(y) / sr

        # 1. Spectral Analysis
        # Compute spectral features over the full audio
        S = np.abs(librosa.stft(y, n_fft=2048, hop_length=512))
        freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)

        # Spectral centroid (weighted average frequency)
        centroid = librosa.feature.spectral_centroid(S=S, sr=sr)
        profile.spectral_centroid = float(np.median(centroid))

        # Spectral flatness (noise vs tone)
        flatness = librosa.feature.spectral_flatness(S=S)
        profile.spectral_flatness = float(np.median(flatness))

        # 2. Sub-bass analysis
        # Check energy distribution in sub-bass (<80Hz) vs bass (80-300Hz)
        sub_bass_bins = freqs < 80
        bass_bins = (freqs >= 80) & (freqs < 300)

        sub_bass_energy = np.mean(S[sub_bass_bins, :]) if np.any(sub_bass_bins) else 0
        bass_energy = np.mean(S[bass_bins, :]) if np.any(bass_bins) else 1

        profile.is_sub_bass = sub_bass_energy > bass_energy * 0.8

        # 3. Pitch range estimation using pYIN on a sample
        sample_duration = min(30.0, duration)
        y_sample = y[:int(sample_duration * sr)]

        f0, voiced, _ = librosa.pyin(y_sample, fmin=30, fmax=500, sr=sr)
        voiced_f0 = f0[voiced & ~np.isnan(f0)]

        if len(voiced_f0) > 10:
            profile.estimated_pitch_range = (float(np.percentile(voiced_f0, 10)),
                                            float(np.percentile(voiced_f0, 90)))

        # 4. Note density estimation using onset detection
        onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=512)
        onsets = librosa.onset.onset_detect(onset_envelope=onset_env, sr=sr, hop_length=512)

        profile.note_density = len(onsets) / duration if duration > 0 else 0
        profile.is_sparse = profile.note_density < 0.5  # Less than 0.5 notes/sec
        profile.is_dense = profile.note_density > 4.0   # More than 4 notes/sec

        # 5. Transient strength (ratio of onset envelope peaks to mean)
        if len(onset_env) > 0:
            onset_peaks = np.percentile(onset_env, 95)
            onset_mean = np.mean(onset_env)
            profile.transient_strength = min(1.0, onset_peaks / (onset_mean + 1e-6) / 10)

        # 6. Harmonic ratio estimation
        # Use HPSS to separate harmonic and percussive components
        y_harmonic, y_percussive = librosa.effects.hpss(y_sample)
        harmonic_energy = np.sum(y_harmonic ** 2)
        total_energy = np.sum(y_sample ** 2)
        profile.harmonic_ratio = harmonic_energy / (total_energy + 1e-10)

        # 7. Polyphony estimation using chroma analysis
        chroma = librosa.feature.chroma_cqt(y=y_harmonic, sr=sr)
        active_per_frame = np.sum(chroma > 0.3, axis=0)
        multi_pitch_ratio = np.sum(active_per_frame > 2) / len(active_per_frame)
        profile.polyphony_score = min(1.0, multi_pitch_ratio * 2)

        # 8. Determine recommended detector based on profile
        profile = _determine_routing(profile)

        logger.info(f"Content profile: density={profile.note_density:.2f}/s, "
                   f"sparse={profile.is_sparse}, dense={profile.is_dense}, "
                   f"harmonic_ratio={profile.harmonic_ratio:.2f}, "
                   f"polyphony={profile.polyphony_score:.2f}, "
                   f"recommended={profile.recommended_detector}")

    except Exception as e:
        logger.warning(f"Content analysis failed: {e}, using default profile")
        profile.recommended_detector = "pyin"
        profile.confidence = 0.5

    return profile


def _determine_routing(profile: ContentProfile) -> ContentProfile:
    """Determine optimal detector routing based on content profile."""

    # Sparse pad-like content: basic_pitch often better
    if profile.is_sparse and profile.polyphony_score > 0.3:
        profile.recommended_detector = "basic_pitch"
        profile.confidence = 0.7
        return profile

    # Very dense content: needs good onset detection, pYIN usually better
    if profile.is_dense and profile.transient_strength > 0.5:
        profile.recommended_detector = "pyin"
        profile.confidence = 0.8
        return profile

    # High polyphony: basic_pitch
    if profile.polyphony_score > 0.5:
        profile.recommended_detector = "basic_pitch"
        profile.confidence = 0.75
        return profile

    # High harmonic ratio (clean monophonic): pYIN excels
    if profile.harmonic_ratio > 0.7 and profile.polyphony_score < 0.2:
        profile.recommended_detector = "pyin"
        profile.confidence = 0.85
        return profile

    # Sub-bass content: pYIN often has octave errors, use torchcrepe
    if profile.is_sub_bass:
        profile.recommended_detector = "torchcrepe"
        profile.confidence = 0.6
        return profile

    # Default: pYIN for most bass content
    profile.recommended_detector = "pyin"
    profile.confidence = 0.65
    return profile


def extract_with_adaptive_onset(
    audio_path: str,
    profile: ContentProfile,
) -> DetectorResult:
    """
    Extract using pYIN with adaptive onset detection based on content profile.
    """
    import librosa

    y, sr = librosa.load(audio_path, sr=22050, mono=True)
    duration = len(y) / sr

    # Adaptive frequency range based on profile
    if profile.is_sub_bass:
        fmin, fmax = 20, 400
    else:
        fmin, fmax = 30, 500

    # Run pYIN pitch detection
    f0, voiced_flag, voiced_probs = librosa.pyin(
        y, fmin=fmin, fmax=fmax, sr=sr,
        frame_length=2048, hop_length=512
    )

    frame_dur = 512 / sr

    # Adaptive onset detection based on content profile
    if profile.is_dense:
        # Dense content: use more sensitive onset detection
        onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=512)
        # Use RMS envelope derivative for additional onsets
        rms = librosa.feature.rms(y=y, hop_length=512)[0]
        rms_diff = np.maximum(np.diff(rms, prepend=rms[0]), 0)

        # Combine spectral flux and RMS derivative
        combined_onset = onset_env + 0.5 * rms_diff[:len(onset_env)]

        onset_frames = librosa.onset.onset_detect(
            onset_envelope=combined_onset,
            sr=sr, hop_length=512,
            delta=0.03,  # Lower threshold for dense content
            units='frames'
        )
    elif profile.is_sparse:
        # Sparse content: use more conservative onset detection
        onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=512)
        onset_frames = librosa.onset.onset_detect(
            onset_envelope=onset_env,
            sr=sr, hop_length=512,
            delta=0.1,  # Higher threshold for sparse content
            wait=10,  # Longer wait between detections
            units='frames'
        )
    else:
        # Default onset detection
        onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=512)
        onset_frames = librosa.onset.onset_detect(
            onset_envelope=onset_env,
            sr=sr, hop_length=512,
            units='frames'
        )

    onset_set = set(onset_frames)

    # Adaptive minimum duration
    if profile.is_sparse:
        min_dur = 0.2  # Longer minimum for sparse content
    elif profile.is_dense:
        min_dur = 0.05  # Shorter minimum for dense content
    else:
        min_dur = 0.08  # Default

    # Convert to notes with confidence scoring
    notes = []
    current_midi = None
    note_start = 0
    note_voiced_probs = []

    for i, (hz, is_voiced, prob) in enumerate(zip(f0, voiced_flag, voiced_probs)):
        if is_voiced and not np.isnan(hz) and hz > 0:
            midi = int(round(12 * np.log2(hz / 440) + 69))
        else:
            midi = None

        is_onset = i in onset_set and midi == current_midi and midi is not None
        is_change = midi != current_midi

        if is_change or is_onset:
            if current_midi is not None:
                note_end = i * frame_dur
                if note_end - note_start >= min_dur:
                    # Calculate confidence from voiced probabilities
                    avg_prob = np.mean(note_voiced_probs) if note_voiced_probs else 0.5
                    confidence = float(avg_prob)

                    notes.append(NoteCandidate(
                        pitch=current_midi,
                        start=note_start,
                        end=note_end,
                        velocity=80,
                        confidence=confidence,
                        detector="pyin_adaptive",
                        onset_confidence=confidence,
                        offset_confidence=confidence,
                    ))

            current_midi = midi
            note_start = i * frame_dur
            note_voiced_probs = [prob] if midi is not None else []
        elif midi is not None:
            note_voiced_probs.append(prob)

    # Handle last note
    if current_midi is not None:
        note_end = len(f0) * frame_dur
        if note_end - note_start >= min_dur:
            avg_prob = np.mean(note_voiced_probs) if note_voiced_probs else 0.5
            notes.append(NoteCandidate(
                pitch=current_midi,
                start=note_start,
                end=note_end,
                velocity=80,
                confidence=float(avg_prob),
                detector="pyin_adaptive",
                onset_confidence=float(avg_prob),
                offset_confidence=float(avg_prob),
            ))

    # Calculate global confidence
    if notes:
        avg_confidence = np.mean([n.confidence for n in notes])
        pitches = [n.pitch for n in notes]
        median_pitch = int(np.median(pitches))
    else:
        avg_confidence = 0.0
        median_pitch = 0

    return DetectorResult(
        detector_name="pyin_adaptive",
        notes=notes,
        global_confidence=avg_confidence,
        median_pitch=median_pitch,
        note_count=len(notes),
    )


def extract_with_torchcrepe(audio_path: str, profile: ContentProfile) -> DetectorResult:
    """Extract using torchcrepe with content-aware parameters."""
    from .gpu_extractor import extract_midi_torchcrepe, MIDINote

    # Adaptive model size based on content
    model_size = "full"  # Always use full for best accuracy

    try:
        notes, tempo, duration = extract_midi_torchcrepe(
            audio_path, stem_type="bass", model_size=model_size
        )

        # Convert to NoteCandidate with confidence
        candidates = []
        for n in notes:
            candidates.append(NoteCandidate(
                pitch=n.pitch,
                start=n.start,
                end=n.end,
                velocity=n.velocity,
                confidence=0.7,  # Default confidence for torchcrepe
                detector="torchcrepe",
            ))

        if candidates:
            pitches = [c.pitch for c in candidates]
            median_pitch = int(np.median(pitches))
        else:
            median_pitch = 0

        return DetectorResult(
            detector_name="torchcrepe",
            notes=candidates,
            global_confidence=0.7,
            median_pitch=median_pitch,
            note_count=len(candidates),
        )
    except Exception as e:
        logger.warning(f"torchcrepe extraction failed: {e}")
        return DetectorResult(detector_name="torchcrepe", global_confidence=0.0)


def extract_with_basic_pitch(audio_path: str, profile: ContentProfile) -> DetectorResult:
    """Extract using basic_pitch with content-aware parameters."""
    import tempfile
    import os
    import librosa
    import soundfile as sf
    from basic_pitch.inference import predict

    y, sr = librosa.load(audio_path, sr=22050, mono=True)

    # Adaptive thresholds based on content
    if profile.is_sparse:
        onset_threshold = 0.5  # Higher threshold for sparse content
        frame_threshold = 0.4
    elif profile.is_dense:
        onset_threshold = 0.3  # Lower threshold for dense content
        frame_threshold = 0.25
    else:
        onset_threshold = 0.4
        frame_threshold = 0.3

    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
        tmp_path = tmp.name
        sf.write(tmp_path, y.astype(np.float32), sr)

    try:
        model_output, midi_data, note_events = predict(
            tmp_path,
            onset_threshold=onset_threshold,
            frame_threshold=frame_threshold,
            minimum_note_length=50 if not profile.is_sparse else 100,
        )

        candidates = []
        for note in note_events:
            start, end, pitch, amplitude, _ = note
            if 20 <= pitch <= 72:  # Bass range
                confidence = float(amplitude) if amplitude <= 1 else amplitude / 127
                candidates.append(NoteCandidate(
                    pitch=int(pitch),
                    start=float(start),
                    end=float(end),
                    velocity=int(amplitude * 127) if amplitude <= 1 else int(amplitude),
                    confidence=confidence,
                    detector="basic_pitch",
                ))

        if candidates:
            pitches = [c.pitch for c in candidates]
            median_pitch = int(np.median(pitches))
            avg_confidence = np.mean([c.confidence for c in candidates])
        else:
            median_pitch = 0
            avg_confidence = 0.0

        return DetectorResult(
            detector_name="basic_pitch",
            notes=candidates,
            global_confidence=avg_confidence,
            median_pitch=median_pitch,
            note_count=len(candidates),
        )
    except Exception as e:
        logger.warning(f"basic_pitch extraction failed: {e}")
        return DetectorResult(detector_name="basic_pitch", global_confidence=0.0)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def combine_detectors_weighted(
    results: List[DetectorResult],
    profile: ContentProfile,
) -> List[NoteCandidate]:
    """
    Combine notes from multiple detectors using intelligent selection.

    Strategy:
    1. Select the best detector based on content profile and detection quality
    2. Use secondary detectors to fill gaps and validate
    3. Cross-validate to boost confidence in agreed notes
    """
    if not results or all(r.note_count == 0 for r in results):
        return []

    # Score each detector based on multiple factors
    detector_scores = {}

    # Find max note count for normalization
    max_notes = max((r.note_count for r in results), default=1)

    for result in results:
        if result.note_count == 0:
            detector_scores[result.detector_name] = 0.0
            continue

        score = 0.0

        # Factor 1: Detection confidence
        score += result.global_confidence * 20

        # Factor 2: Note count - more notes generally better (higher recall)
        # But penalize extreme over-detection
        note_ratio = result.note_count / max_notes
        if note_ratio >= 0.7:
            score += 25  # Good note count relative to others
        elif note_ratio >= 0.3:
            score += 15
        else:
            score += 5   # Much fewer notes than others

        # Factor 3: Median pitch in valid bass range
        # Bass typically lives in MIDI 28-55 (E1 to G3)
        if 28 <= result.median_pitch <= 55:
            score += 20  # Good bass range
        elif 24 <= result.median_pitch < 28:
            score += 10  # Low but acceptable
        elif result.median_pitch < 24:
            score -= 15  # Too low, likely octave error
        elif result.median_pitch > 60:
            score -= 10  # High for bass

        # Factor 4: Prefer pYIN for clean content (high harmonic ratio)
        if "pyin" in result.detector_name and profile.harmonic_ratio > 0.7:
            score += 15

        # Factor 5: Prefer basic_pitch for polyphonic content
        if "basic_pitch" in result.detector_name and profile.polyphony_score > 0.5:
            score += 15

        # Factor 6: Penalize severe under-detection
        if result.note_count < 20 and max_notes > 100:
            score -= 20

        detector_scores[result.detector_name] = score

    logger.info(f"Detector scores: {detector_scores}")

    # Select best detector
    best_detector = max(detector_scores.keys(), key=lambda k: detector_scores[k])
    best_result = next(r for r in results if r.detector_name == best_detector)

    logger.info(f"Selected primary detector: {best_detector} with {best_result.note_count} notes")

    # Start with best detector's notes
    primary_notes = list(best_result.notes)

    # Get secondary detectors for gap filling
    secondary_results = [r for r in results if r.detector_name != best_detector and r.note_count > 0]

    # Gap filling: add notes from secondary detectors that don't overlap with primary
    gap_filled_notes = []
    for sec_result in secondary_results:
        for sec_note in sec_result.notes:
            # Check if this note overlaps with any primary note
            overlaps = False
            for prim_note in primary_notes:
                # Same pitch class and overlapping time
                if (sec_note.pitch % 12 == prim_note.pitch % 12 and
                    sec_note.start < prim_note.end + 0.1 and
                    sec_note.end > prim_note.start - 0.1):
                    overlaps = True
                    break

            if not overlaps:
                # This is a gap - add it with reduced confidence
                gap_note = NoteCandidate(
                    pitch=sec_note.pitch,
                    start=sec_note.start,
                    end=sec_note.end,
                    velocity=sec_note.velocity,
                    confidence=sec_note.confidence * 0.7,  # Reduce confidence for gap fills
                    detector=f"gap_{sec_result.detector_name}",
                )
                gap_filled_notes.append(gap_note)

    # Limit gap fills to prevent over-detection
    # Only add gap fills if they don't significantly increase note count
    max_gap_fills = int(len(primary_notes) * 0.3)  # Max 30% increase
    if gap_filled_notes:
        # Sort by confidence and take top ones
        gap_filled_notes.sort(key=lambda n: -n.confidence)
        gap_filled_notes = gap_filled_notes[:max_gap_fills]
        logger.info(f"Adding {len(gap_filled_notes)} gap-filled notes")

    # Combine
    all_notes = primary_notes + gap_filled_notes

    # Cross-validation: boost confidence for notes confirmed by multiple detectors
    for note in all_notes:
        confirmations = 0
        for result in results:
            if result.detector_name == note.detector:
                continue
            for other_note in result.notes:
                # Check if confirmed (same pitch class, overlapping time)
                if (note.pitch % 12 == other_note.pitch % 12 and
                    abs(note.start - other_note.start) < 0.15):
                    confirmations += 1
                    break

        if confirmations > 0:
            note.confidence = min(1.0, note.confidence * (1 + confirmations * 0.1))

    # Sort by start time
    all_notes.sort(key=lambda n: n.start)

    return all_notes


def refine_note_boundaries(
    notes: List[NoteCandidate],
    audio_path: str,
) -> List[NoteCandidate]:
    """
    Refine note boundaries using signal analysis.

    Uses:
    1. Onset envelope peaks for start times
    2. RMS envelope valleys for end times
    """
    if not notes:
        return notes

    import librosa

    y, sr = librosa.load(audio_path, sr=22050, mono=True)
    hop_length = 512
    frame_dur = hop_length / sr

    # Compute onset and RMS envelopes
    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop_length)
    rms = librosa.feature.rms(y=y, hop_length=hop_length)[0]

    refined = []
    for note in notes:
        # Find frames around note start
        start_frame = int(note.start / frame_dur)
        end_frame = int(note.end / frame_dur)

        # Look for onset peak near note start (within 100ms)
        search_start = max(0, start_frame - 4)
        search_end = min(len(onset_env), start_frame + 4)

        if search_end > search_start:
            local_onset = onset_env[search_start:search_end]
            peak_offset = np.argmax(local_onset)
            refined_start_frame = search_start + peak_offset
            refined_start = refined_start_frame * frame_dur
        else:
            refined_start = note.start

        # Look for RMS valley near note end
        search_start = max(0, end_frame - 4)
        search_end = min(len(rms), end_frame + 4)

        if search_end > search_start:
            local_rms = rms[search_start:search_end]
            valley_offset = np.argmin(local_rms)
            refined_end_frame = search_start + valley_offset
            refined_end = refined_end_frame * frame_dur
        else:
            refined_end = note.end

        # Ensure valid duration
        if refined_end <= refined_start:
            refined_end = note.end
            refined_start = note.start

        refined.append(NoteCandidate(
            pitch=note.pitch,
            start=refined_start,
            end=refined_end,
            velocity=note.velocity,
            confidence=note.confidence,
            detector=note.detector,
        ))

    return refined


def extract_bass_v2(audio_path: str) -> Tuple[List['MIDINote'], float, float, str]:
    """
    Main entry point for advanced bass extraction.

    Returns:
        Tuple of (notes, tempo, duration, method_used)
    """
    from .gpu_extractor import MIDINote
    import librosa

    # Load audio for duration/tempo
    y, sr = librosa.load(audio_path, sr=22050, mono=True)
    duration = len(y) / sr

    # Estimate tempo
    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
    if hasattr(tempo, '__len__'):
        tempo = float(tempo[0]) if len(tempo) > 0 else 120.0
    tempo = float(max(60, min(200, tempo)))

    # Step 1: Analyze content
    logger.info("Bass v2: Analyzing content...")
    profile = analyze_content(audio_path)

    # Step 2: Run primary detector first based on content profile
    logger.info("Bass v2: Running detectors...")
    results = []

    # Always run pYIN first (fastest)
    pyin_result = extract_with_adaptive_onset(audio_path, profile)
    results.append(pyin_result)
    logger.info(f"pYIN adaptive: {pyin_result.note_count} notes, median={pyin_result.median_pitch}")

    # Early exit: if pYIN gets good results for clean content, skip others
    if (profile.harmonic_ratio > 0.8 and
        profile.polyphony_score < 0.3 and
        pyin_result.note_count >= 20 and
        pyin_result.global_confidence > 0.6):
        logger.info("Bass v2: Early exit - pYIN sufficient for clean monophonic content")
        midi_notes = [
            MIDINote(pitch=n.pitch, start=n.start, end=n.end, velocity=n.velocity)
            for n in pyin_result.notes
        ]
        return midi_notes, tempo, duration, "bass_v2_pyin_fast"

    # Run torchcrepe only if pYIN results are suspicious
    if pyin_result.median_pitch < 28 or pyin_result.note_count < 15:
        tc_result = extract_with_torchcrepe(audio_path, profile)
        results.append(tc_result)
        logger.info(f"torchcrepe: {tc_result.note_count} notes, median={tc_result.median_pitch}")
    else:
        tc_result = DetectorResult(detector_name="torchcrepe", global_confidence=0.0)

    # Run basic_pitch only for polyphonic content or when others fail
    if profile.polyphony_score > 0.4 or (pyin_result.note_count < 20 and tc_result.note_count < 20):
        bp_result = extract_with_basic_pitch(audio_path, profile)
        results.append(bp_result)
        logger.info(f"basic_pitch: {bp_result.note_count} notes, median={bp_result.median_pitch}")
    else:
        bp_result = DetectorResult(detector_name="basic_pitch", global_confidence=0.0)

    # Step 3: Combine with weighted voting
    logger.info("Bass v2: Combining detectors...")
    combined_notes = combine_detectors_weighted(results, profile)
    logger.info(f"Combined: {len(combined_notes)} notes")

    # Step 4: Refine boundaries
    logger.info("Bass v2: Refining boundaries...")
    refined_notes = refine_note_boundaries(combined_notes, audio_path)

    # Step 5: Post-processing - octave correction if needed
    if refined_notes:
        pitches = [n.pitch for n in refined_notes]
        median_pitch = int(np.median(pitches))

        # Correct if median is below practical bass range
        if median_pitch < 24:  # Below C1
            octave_shift = 12
            while median_pitch + octave_shift < 24:
                octave_shift += 12

            refined_notes = [
                NoteCandidate(
                    pitch=n.pitch + octave_shift,
                    start=n.start,
                    end=n.end,
                    velocity=n.velocity,
                    confidence=n.confidence,
                    detector=n.detector,
                )
                for n in refined_notes
            ]
            logger.info(f"Bass v2: Applied octave correction +{octave_shift}")

    # Convert to MIDINote
    midi_notes = [
        MIDINote(
            pitch=n.pitch,
            start=n.start,
            end=n.end,
            velocity=n.velocity,
        )
        for n in refined_notes
    ]

    method = f"bass_v2_{profile.recommended_detector}"
    logger.info(f"Bass v2 complete: {len(midi_notes)} notes, method={method}")

    return midi_notes, tempo, duration, method
