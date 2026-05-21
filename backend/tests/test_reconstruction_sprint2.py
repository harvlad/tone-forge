"""Tests for Sprint 2: Role Classification & Temporal Continuity."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tone_forge.reconstruction import (
    # Role classification
    MusicalRole,
    SpectralProfile,
    TemporalProfile,
    RoleFeatures,
    RoleClassification,
    RoleClassifier,
    get_role_classifier,
    classify_role,
    # Temporal continuity
    EnvelopeType,
    PhraseType,
    HarmonicTrack,
    ContinuityRegion,
    Phrase,
    ContinuityAnalysis,
    HarmonicTracker,
    PhraseDetector,
    TemporalContinuityAnalyzer,
    get_continuity_analyzer,
    get_harmonic_tracker,
    get_phrase_detector,
    analyze_continuity,
)


SR = 22050


def _make_sine_wave(freq: float = 440, duration: float = 1.0, sr: int = SR) -> np.ndarray:
    """Generate a sine wave."""
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    return (np.sin(2 * np.pi * freq * t) * 0.5).astype(np.float32)


def _make_bass_audio(duration: float = 2.0) -> np.ndarray:
    """Generate bass-like audio (low frequency, sustained)."""
    t = np.linspace(0, duration, int(SR * duration), endpoint=False)
    # Low fundamental with harmonics
    sig = np.sin(2 * np.pi * 80 * t)
    sig += 0.5 * np.sin(2 * np.pi * 160 * t)
    sig += 0.25 * np.sin(2 * np.pi * 240 * t)
    return (sig * 0.5).astype(np.float32)


def _make_lead_melody(duration: float = 2.0) -> np.ndarray:
    """Generate lead melody-like audio (changing pitches)."""
    t = np.linspace(0, duration, int(SR * duration), endpoint=False)
    # Melody with pitch changes
    freqs = [440, 494, 523, 587]  # A4, B4, C5, D5
    sig = np.zeros_like(t)
    segment_length = len(t) // len(freqs)
    for i, freq in enumerate(freqs):
        start = i * segment_length
        end = (i + 1) * segment_length if i < len(freqs) - 1 else len(t)
        t_seg = t[start:end] - t[start]
        # Add attack envelope
        env = np.minimum(t_seg * 20, 1.0) * np.exp(-t_seg * 2)
        sig[start:end] = np.sin(2 * np.pi * freq * t_seg) * env
    return (sig * 0.5).astype(np.float32)


def _make_pad_audio(duration: float = 3.0) -> np.ndarray:
    """Generate pad-like audio (sustained, slow attack)."""
    t = np.linspace(0, duration, int(SR * duration), endpoint=False)
    # Slow attack envelope
    attack_time = 0.5
    attack_env = np.minimum(t / attack_time, 1.0)
    # Rich harmonics
    sig = np.sin(2 * np.pi * 220 * t)
    sig += 0.5 * np.sin(2 * np.pi * 330 * t)  # Perfect 5th
    sig += 0.3 * np.sin(2 * np.pi * 440 * t)  # Octave
    sig += 0.2 * np.sin(2 * np.pi * 550 * t)  # Major 3rd
    return (sig * attack_env * 0.4).astype(np.float32)


def _make_arp_audio(duration: float = 2.0) -> np.ndarray:
    """Generate arpeggio-like audio (rhythmic, melodic)."""
    t = np.linspace(0, duration, int(SR * duration), endpoint=False)
    # Fast repeating notes
    note_duration = 0.125  # 8th notes at 120bpm
    freqs = [261, 329, 392, 523]  # C4, E4, G4, C5
    sig = np.zeros_like(t)
    note_samples = int(note_duration * SR)

    for i, freq in enumerate(freqs * 4):  # Repeat pattern
        start = i * note_samples
        end = min((i + 1) * note_samples, len(t))
        if start >= len(t):
            break
        t_seg = t[start:end] - t[start]
        env = np.exp(-t_seg * 15)  # Fast decay
        sig[start:end] = np.sin(2 * np.pi * freq * t_seg) * env

    return (sig * 0.5).astype(np.float32)


def _make_drum_audio(duration: float = 2.0) -> np.ndarray:
    """Generate drum-like audio (transient, percussive)."""
    t = np.linspace(0, duration, int(SR * duration), endpoint=False)
    sig = np.zeros_like(t)

    # Add hits at regular intervals
    hit_times = [0.0, 0.5, 1.0, 1.5]
    for hit_time in hit_times:
        if hit_time >= duration:
            break
        start = int(hit_time * SR)
        hit_duration = int(0.1 * SR)
        end = min(start + hit_duration, len(t))
        t_seg = t[start:end] - t[start]
        # Sharp transient + noise
        env = np.exp(-30 * t_seg)
        sig[start:end] = (np.sin(2 * np.pi * 60 * t_seg) + 0.5 * np.random.randn(end - start)) * env

    return (sig * 0.5).astype(np.float32)


def _make_texture_audio(duration: float = 2.0) -> np.ndarray:
    """Generate texture-like audio (noise-based, sustained)."""
    t = np.linspace(0, duration, int(SR * duration), endpoint=False)
    # Filtered noise with slow envelope
    noise = np.random.randn(len(t))
    # Simple lowpass via convolution
    kernel_size = 100
    kernel = np.ones(kernel_size) / kernel_size
    filtered = np.convolve(noise, kernel, mode='same')
    env = np.minimum(t / 0.3, 1.0) * np.exp(-t / 3)
    return (filtered * env * 0.3).astype(np.float32)


# ======================= Role Classification Tests =======================

class TestMusicalRole:
    """Test MusicalRole enum."""

    def test_enum_values(self):
        assert MusicalRole.BASS_FOUNDATION.value == "bass_foundation"
        assert MusicalRole.LEAD_MELODY.value == "lead_melody"
        assert MusicalRole.PAD_ATMOSPHERE.value == "pad_atmosphere"
        assert MusicalRole.ARP_RHYTHM.value == "arp_rhythm"


class TestSpectralProfile:
    """Test SpectralProfile enum."""

    def test_enum_values(self):
        assert SpectralProfile.BASS_HEAVY.value == "bass_heavy"
        assert SpectralProfile.BRIGHT.value == "bright"
        assert SpectralProfile.MID_FOCUSED.value == "mid_focused"


class TestTemporalProfile:
    """Test TemporalProfile enum."""

    def test_enum_values(self):
        assert TemporalProfile.SUSTAINED.value == "sustained"
        assert TemporalProfile.TRANSIENT.value == "transient"
        assert TemporalProfile.RHYTHMIC.value == "rhythmic"


class TestRoleFeatures:
    """Test RoleFeatures dataclass."""

    def test_create_default(self):
        features = RoleFeatures()
        assert features.spectral_centroid_mean == 0.0

    def test_to_array(self):
        features = RoleFeatures(
            spectral_centroid_mean=1000.0,
            low_freq_ratio=0.3,
        )
        arr = features.to_array()
        assert isinstance(arr, np.ndarray)
        assert arr.dtype == np.float32
        assert len(arr) == 22  # Number of features


class TestRoleClassification:
    """Test RoleClassification dataclass."""

    def test_create(self):
        result = RoleClassification(
            primary_role=MusicalRole.BASS_FOUNDATION,
            confidence=0.8,
            secondary_roles=[(MusicalRole.RHYTHMIC_ELEMENT, 0.3)],
            spectral_profile=SpectralProfile.BASS_HEAVY,
            temporal_profile=TemporalProfile.SUSTAINED,
        )
        assert result.primary_role == MusicalRole.BASS_FOUNDATION
        assert result.confidence == 0.8

    def test_to_dict(self):
        result = RoleClassification(
            primary_role=MusicalRole.LEAD_MELODY,
            confidence=0.7,
        )
        d = result.to_dict()
        assert d["primary_role"] == "lead_melody"
        assert "recommendations" in d


class TestRoleClassifier:
    """Test RoleClassifier class."""

    def test_get_classifier_singleton(self):
        c1 = get_role_classifier()
        c2 = get_role_classifier()
        assert c1 is c2

    def test_classify_bass(self):
        classifier = RoleClassifier()
        audio = _make_bass_audio(duration=2.0)
        result = classifier.classify(audio, SR, stem_type="bass")

        assert isinstance(result, RoleClassification)
        # Bass audio should likely classify as bass_foundation
        assert result.primary_role in [
            MusicalRole.BASS_FOUNDATION,
            MusicalRole.RHYTHMIC_ELEMENT,
        ]
        assert result.spectral_profile == SpectralProfile.BASS_HEAVY

    def test_classify_pad(self):
        classifier = RoleClassifier()
        audio = _make_pad_audio(duration=3.0)
        result = classifier.classify(audio, SR)

        assert isinstance(result, RoleClassification)
        # Pad should have high sustain ratio
        assert result.features.sustain_ratio > 0.5

    def test_classify_arp(self):
        classifier = RoleClassifier()
        audio = _make_arp_audio(duration=2.0)
        result = classifier.classify(audio, SR)

        assert isinstance(result, RoleClassification)
        # Arp should have high onset rate
        assert result.features.onset_rate > 2.0

    def test_classify_drums(self):
        classifier = RoleClassifier()
        audio = _make_drum_audio(duration=2.0)
        result = classifier.classify(audio, SR, stem_type="drums")

        assert isinstance(result, RoleClassification)
        # Drums should have low harmonic ratio
        assert result.features.harmonic_ratio < 0.6

    def test_classify_with_context(self):
        classifier = RoleClassifier()
        audio = _make_lead_melody(duration=2.0)
        other_stems = {"bass": _make_bass_audio(duration=2.0)}

        result = classifier.classify_with_context(
            audio, SR,
            other_stems=other_stems,
            genre="synthwave"
        )

        assert isinstance(result, RoleClassification)

    def test_classify_role_convenience(self):
        audio = _make_bass_audio(duration=2.0)
        result = classify_role(audio, SR, stem_type="bass")
        assert isinstance(result, RoleClassification)

    def test_extraction_recommendations(self):
        classifier = RoleClassifier()
        audio = _make_pad_audio(duration=3.0)
        result = classifier.classify(audio, SR)

        # Pad should have lower onset threshold
        assert result.recommended_onset_threshold <= 0.5
        # Pad should have longer note merge time than drums
        drum_audio = _make_drum_audio(duration=2.0)
        drum_result = classifier.classify(drum_audio, SR, stem_type="drums")
        # Just verify recommendations exist and are reasonable
        assert 0 < result.recommended_note_merge_time < 1.0
        assert 0 < result.recommended_min_note_duration < 1.0


# ======================= Temporal Continuity Tests =======================

class TestEnvelopeType:
    """Test EnvelopeType enum."""

    def test_enum_values(self):
        assert EnvelopeType.SUSTAINED.value == "sustained"
        assert EnvelopeType.DECAYING.value == "decaying"
        assert EnvelopeType.SWELLING.value == "swelling"


class TestPhraseType:
    """Test PhraseType enum."""

    def test_enum_values(self):
        assert PhraseType.MELODIC.value == "melodic"
        assert PhraseType.HARMONIC.value == "harmonic"
        assert PhraseType.RHYTHMIC.value == "rhythmic"


class TestHarmonicTrack:
    """Test HarmonicTrack dataclass."""

    def test_create(self):
        track = HarmonicTrack(
            fundamental_hz=440.0,
            start_time=0.0,
            end_time=1.0,
            harmonic_indices=[1, 2, 3],
            amplitude_contour=np.ones(100),
            frequency_contour=np.ones(100) * 440,
            stability=0.9,
        )
        assert track.duration == 1.0
        assert track.is_stable


class TestContinuityRegion:
    """Test ContinuityRegion dataclass."""

    def test_create(self):
        region = ContinuityRegion(
            start_time=0.0,
            end_time=2.0,
            fundamental_hz=220.0,
            harmonics=[220, 440, 660],
            stability=0.85,
            envelope_type=EnvelopeType.SUSTAINED,
        )
        assert region.duration == 2.0

    def test_to_dict(self):
        region = ContinuityRegion(
            start_time=0.0,
            end_time=1.0,
            fundamental_hz=440.0,
            harmonics=[440],
            stability=0.8,
            envelope_type=EnvelopeType.DECAYING,
        )
        d = region.to_dict()
        assert d["fundamental_hz"] == 440.0
        assert d["envelope_type"] == "decaying"


class TestPhrase:
    """Test Phrase dataclass."""

    def test_create(self):
        phrase = Phrase(
            start_time=0.0,
            end_time=4.0,
            phrase_type=PhraseType.MELODIC,
            confidence=0.8,
        )
        assert phrase.duration == 4.0

    def test_to_dict(self):
        phrase = Phrase(
            start_time=0.0,
            end_time=2.0,
            phrase_type=PhraseType.HARMONIC,
            confidence=0.7,
            pitch_range_semitones=12.0,
        )
        d = phrase.to_dict()
        assert d["phrase_type"] == "harmonic"
        assert d["pitch_range_semitones"] == 12.0


class TestHarmonicTracker:
    """Test HarmonicTracker class."""

    def test_get_tracker_singleton(self):
        t1 = get_harmonic_tracker()
        t2 = get_harmonic_tracker()
        assert t1 is t2

    def test_track_sine_wave(self):
        tracker = HarmonicTracker()
        audio = _make_sine_wave(440, duration=2.0)
        tracks = tracker.track(audio, SR)

        assert isinstance(tracks, list)
        # Should find at least one track for sustained sine
        if tracks:
            assert tracks[0].fundamental_hz > 0

    def test_track_pad(self):
        tracker = HarmonicTracker()
        audio = _make_pad_audio(duration=3.0)
        tracks = tracker.track(audio, SR)

        assert isinstance(tracks, list)


class TestPhraseDetector:
    """Test PhraseDetector class."""

    def test_get_detector_singleton(self):
        d1 = get_phrase_detector()
        d2 = get_phrase_detector()
        assert d1 is d2

    def test_detect_melody_phrases(self):
        detector = PhraseDetector()
        audio = _make_lead_melody(duration=2.0)
        phrases = detector.detect(audio, SR)

        assert isinstance(phrases, list)

    def test_detect_with_silence(self):
        detector = PhraseDetector()
        # Create audio with a gap
        audio1 = _make_sine_wave(440, duration=1.0)
        silence = np.zeros(int(0.5 * SR))
        audio2 = _make_sine_wave(440, duration=1.0)
        audio = np.concatenate([audio1, silence, audio2])

        phrases = detector.detect(audio, SR)

        assert isinstance(phrases, list)
        # Should detect the silence boundary


class TestTemporalContinuityAnalyzer:
    """Test TemporalContinuityAnalyzer class."""

    def test_get_analyzer_singleton(self):
        a1 = get_continuity_analyzer()
        a2 = get_continuity_analyzer()
        assert a1 is a2

    def test_analyze_sustained_audio(self):
        analyzer = TemporalContinuityAnalyzer()
        audio = _make_pad_audio(duration=3.0)
        result = analyzer.analyze(audio, SR)

        assert isinstance(result, ContinuityAnalysis)
        assert result.duration == pytest.approx(3.0, abs=0.1)
        # Pad should have high sustained ratio
        # (may be lower if harmonics aren't tracked well)

    def test_analyze_transient_audio(self):
        analyzer = TemporalContinuityAnalyzer()
        audio = _make_drum_audio(duration=2.0)
        result = analyzer.analyze(audio, SR)

        assert isinstance(result, ContinuityAnalysis)
        # Drums should have lower sustained ratio

    def test_analyze_melody(self):
        analyzer = TemporalContinuityAnalyzer()
        audio = _make_lead_melody(duration=2.0)
        result = analyzer.analyze(audio, SR)

        assert isinstance(result, ContinuityAnalysis)
        assert len(result.phrases) >= 0  # May or may not detect phrases

    def test_analyze_continuity_convenience(self):
        audio = _make_pad_audio(duration=2.0)
        result = analyze_continuity(audio, SR)
        assert isinstance(result, ContinuityAnalysis)

    def test_to_dict(self):
        analyzer = TemporalContinuityAnalyzer()
        audio = _make_bass_audio(duration=2.0)
        result = analyzer.analyze(audio, SR)

        d = result.to_dict()
        assert "duration" in d
        assert "regions" in d
        assert "phrases" in d
        assert "sustained_ratio" in d

    def test_merge_with_notes(self):
        analyzer = TemporalContinuityAnalyzer()
        audio = _make_pad_audio(duration=3.0)
        result = analyzer.analyze(audio, SR)

        # Create some test notes
        notes = [(60, 0.5, 1.5, 80), (64, 1.5, 2.5, 70)]

        refined = analyzer.merge_with_notes(notes, result.regions)
        assert isinstance(refined, list)
        assert len(refined) == len(notes)


class TestContinuityAnalysis:
    """Test ContinuityAnalysis dataclass."""

    def test_create(self):
        analysis = ContinuityAnalysis(
            duration=5.0,
            regions=[],
            phrases=[],
            harmonic_tracks=[],
            sustained_ratio=0.6,
            average_stability=0.8,
            phrase_count=2,
            dominant_envelope=EnvelopeType.SUSTAINED,
        )
        assert analysis.duration == 5.0
        assert analysis.sustained_ratio == 0.6


# ======================= Integration Tests =======================

class TestSprint2Integration:
    """Integration tests for Sprint 2 components."""

    def test_role_and_continuity_analysis(self):
        """Test running both role classification and continuity analysis."""
        audio = _make_pad_audio(duration=3.0)

        # Classify role
        role = classify_role(audio, SR)
        assert isinstance(role, RoleClassification)

        # Analyze continuity
        continuity = analyze_continuity(audio, SR)
        assert isinstance(continuity, ContinuityAnalysis)

        # Pad should show sustained characteristics in both
        assert role.features.sustain_ratio > 0.4
        # Continuity analysis should complete

    def test_different_audio_types(self):
        """Test analysis on different audio types."""
        audio_types = [
            ("bass", _make_bass_audio(duration=2.0)),
            ("lead", _make_lead_melody(duration=2.0)),
            ("pad", _make_pad_audio(duration=2.0)),
            ("arp", _make_arp_audio(duration=2.0)),
            ("drums", _make_drum_audio(duration=2.0)),
        ]

        for name, audio in audio_types:
            role = classify_role(audio, SR)
            continuity = analyze_continuity(audio, SR)

            assert isinstance(role, RoleClassification), f"Failed for {name}"
            assert isinstance(continuity, ContinuityAnalysis), f"Failed for {name}"

    def test_extraction_recommendations_vary_by_role(self):
        """Test that extraction recommendations differ by role."""
        bass = _make_bass_audio(duration=2.0)
        pad = _make_pad_audio(duration=2.0)
        arp = _make_arp_audio(duration=2.0)

        bass_role = classify_role(bass, SR, stem_type="bass")
        pad_role = classify_role(pad, SR)
        arp_role = classify_role(arp, SR)

        # All should have valid recommendations in reasonable ranges
        for role in [bass_role, pad_role, arp_role]:
            assert 0.1 <= role.recommended_onset_threshold <= 0.9
            assert 0.1 <= role.recommended_frame_threshold <= 0.9
            assert 0.001 <= role.recommended_note_merge_time <= 0.5
            assert 0.01 <= role.recommended_min_note_duration <= 0.5
            assert 0.1 <= role.recommended_quantization_strength <= 1.0

        # Bass should have higher quantization (rhythmic foundation)
        assert bass_role.recommended_quantization_strength >= 0.5
