"""SessionBundle.build() coverage.

The assembler is a translation layer between the legacy
``AnalysisResult.to_dict()`` shape and the new ``SessionBundle``
contract. Tests pin two things:

1. The happy-path mapping — a representative legacy dict produces a
   bundle whose fields match exactly what jam.js expects.
2. The conservative behavior on missing / malformed fields — the
   assembler never raises, every gap becomes a sensible default.
"""

from __future__ import annotations

import pytest

from tone_forge.contracts import (
    ConfidenceTier,
    DeviceClass,
    SessionBundle,
    UserRole,
)
from tone_forge.session.bundle import build


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _legacy_result() -> dict:
    """A realistic AnalysisResult.to_dict() shape."""
    return {
        "source_name": "Test Song",
        "source_url": "https://youtube.com/watch?v=abc",
        "duration_sec": 215.4,
        "sample_rate": 44100,
        "content_hash": "abc123",
        "wav_path": "/tmp/cache/abc123.wav",
        "detected_type": "guitar",
        "stems": {
            "drums": "/api/admin/serve-file?path=drums.wav",
            "bass": "/api/admin/serve-file?path=bass.wav",
            "vocals": "/api/admin/serve-file?path=vocals.wav",
            "other": "/api/admin/serve-file?path=other.wav",
        },
        "midi": {
            "notes": [
                {"pitch": 60, "start": 0.0, "end": 0.5},
                {"pitch": 64, "start": 0.5, "end": 1.0},
            ],
            "overall_confidence": 0.78,
        },
        "preset_matches": {
            "guitar": {
                "preset_name": "Analog Lead",
                "distance": 0.42,
            }
        },
        "sections": [
            {"start_s": 0.0, "end_s": 20.0, "label": "intro", "confidence": 0.9},
            {"start_s": 20.0, "end_s": 80.0, "label": "verse", "confidence": 0.85},
        ],
        "chords": [
            {"start_s": 0.0, "end_s": 4.0, "symbol": "Cmaj7", "confidence": 0.9},
            {"start_s": 4.0, "end_s": 8.0, "symbol": "G", "confidence": 0.88},
        ],
        "descriptor": {
            "tempo": 124.0,
            "key": "C major",
        },
    }


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_build_returns_session_bundle() -> None:
    bundle = build(_legacy_result(), session_id="sess-42")
    assert isinstance(bundle, SessionBundle)
    assert bundle.session_id == "sess-42"


def test_build_populates_audio_from_url_source() -> None:
    bundle = build(_legacy_result(), session_id="x")
    assert bundle.audio.source_kind == "url"
    assert bundle.audio.source_uri == "https://youtube.com/watch?v=abc"
    assert bundle.audio.source_title == "Test Song"
    assert bundle.audio.duration_s == pytest.approx(215.4)
    assert bundle.audio.sample_rate == 44100
    assert bundle.audio.content_hash == "abc123"
    assert bundle.audio.wav_path == "/tmp/cache/abc123.wav"


def test_build_marks_upload_when_no_source_url() -> None:
    result = _legacy_result()
    result["source_url"] = None
    bundle = build(result, session_id="x")
    assert bundle.audio.source_kind == "upload"
    assert bundle.audio.source_uri is None


def test_build_populates_stems_with_urls() -> None:
    bundle = build(_legacy_result(), session_id="x")
    assert bundle.stems.drums is not None
    assert bundle.stems.drums.audio_url.endswith("drums.wav")
    assert bundle.stems.bass is not None
    assert bundle.stems.vocals is not None
    assert bundle.stems.other is not None
    assert bundle.stems.guitar_left is None  # not in fixture
    assert bundle.stems.content_hash == "abc123"


def test_build_populates_understanding_tempo_and_key() -> None:
    bundle = build(_legacy_result(), session_id="x")
    assert bundle.understanding.tempo_bpm == pytest.approx(124.0)
    assert bundle.understanding.tempo_confidence > 0  # legacy = conservative
    assert bundle.understanding.key == "C major"
    assert bundle.understanding.time_signature == (4, 4)


def test_build_populates_sections_and_chords() -> None:
    bundle = build(_legacy_result(), session_id="x")
    assert len(bundle.understanding.sections) == 2
    assert bundle.understanding.sections[0].label == "intro"
    assert len(bundle.understanding.chords) == 2
    assert bundle.understanding.chords[0].symbol == "Cmaj7"


def test_build_uses_detected_type_for_user_role() -> None:
    bundle = build(_legacy_result(), session_id="x")
    assert bundle.user_role == UserRole.GUITAR


def test_build_populates_user_midi_when_present() -> None:
    bundle = build(_legacy_result(), session_id="x")
    assert bundle.user_midi is not None
    assert bundle.user_midi.role == UserRole.GUITAR
    assert len(bundle.user_midi.notes) == 2
    assert bundle.user_midi.overall_confidence == pytest.approx(0.78)


def test_build_returns_unknown_tier_when_no_tone_match_injected() -> None:
    """The API edge dependency-injects the ``tone_match`` (P6 lives at
    the composition layer, not inside session/). When no match is
    passed, the bundle assembler degrades to a conservative UNKNOWN
    so the UI takes the curated-chain fallback path. Pinned so the
    legacy preset_match dict never leaks through as a HIGH/MEDIUM
    suggestion."""
    bundle = build(_legacy_result(), session_id="x")
    assert bundle.tone.tier == ConfidenceTier.UNKNOWN
    assert bundle.tone.chosen is None
    assert "tone_match" in bundle.tone.rationale


def test_build_guidance_mirrors_sections_and_chords() -> None:
    bundle = build(_legacy_result(), session_id="x")
    assert bundle.guidance.sections == bundle.understanding.sections
    assert bundle.guidance.chord_lane == bundle.understanding.chords


def test_build_defaults_device_caps_to_interface_only() -> None:
    bundle = build(_legacy_result(), session_id="x")
    assert bundle.device_caps.cls == DeviceClass.INTERFACE_ONLY
    assert bundle.device_caps.can_monitor is True
    assert bundle.device_caps.can_receive_preset is False


def test_build_defaults_initial_transport_to_stopped_muted() -> None:
    bundle = build(_legacy_result(), session_id="x")
    assert bundle.initial_transport.playing is False
    assert bundle.initial_transport.user_mute is True
    assert bundle.initial_transport.monitor_gain == 0.0
    assert bundle.initial_transport.position_s == 0.0


# ---------------------------------------------------------------------------
# Conservative defaults on partial input
# ---------------------------------------------------------------------------

def test_build_handles_empty_result() -> None:
    bundle = build({}, session_id="empty")
    assert bundle.session_id == "empty"
    assert bundle.audio.source_kind == "upload"
    assert bundle.audio.duration_s == 0.0
    assert bundle.user_role == UserRole.GUITAR
    assert bundle.user_midi is None
    assert bundle.understanding.tempo_bpm == 0.0
    assert bundle.understanding.chords == ()
    assert bundle.tone.tier == ConfidenceTier.UNKNOWN


def test_build_drops_malformed_sections() -> None:
    """Sections missing start_s/end_s must be skipped silently — partial
    pipelines must still produce a renderable bundle."""
    result = _legacy_result()
    result["sections"] = [
        {"start_s": 0.0, "end_s": 10.0, "label": "good"},
        {"label": "missing times"},
        "not even a dict",
    ]
    bundle = build(result, session_id="x")
    assert len(bundle.understanding.sections) == 1
    assert bundle.understanding.sections[0].label == "good"


def test_build_drops_chords_without_symbol() -> None:
    result = _legacy_result()
    result["chords"] = [
        {"start_s": 0.0, "end_s": 4.0, "symbol": "Cmaj7"},
        {"start_s": 4.0, "end_s": 8.0},  # missing symbol
    ]
    bundle = build(result, session_id="x")
    assert len(bundle.understanding.chords) == 1


def test_build_explicit_user_role_overrides_detected_type() -> None:
    bundle = build(_legacy_result(), session_id="x", user_role=UserRole.BASS)
    assert bundle.user_role == UserRole.BASS


def test_build_explicit_device_caps_override_default() -> None:
    from tone_forge.contracts import DeviceCaps, MonitorChainFamily

    caps = DeviceCaps(
        cls=DeviceClass.HELIX,
        display_name="Line 6 Helix",
        can_monitor=True,
        can_receive_preset=False,
        preferred_chain_family=MonitorChainFamily.CLASSIC_ROCK,
    )
    bundle = build(_legacy_result(), session_id="x", device_caps=caps)
    assert bundle.device_caps.cls == DeviceClass.HELIX
    assert bundle.device_caps.display_name == "Line 6 Helix"


def test_build_uses_injected_tone_match_verbatim() -> None:
    """When the API edge passes ``tone_match=``, the bundle carries it
    through unchanged. This is the P6 wiring path — the assembler must
    not second-guess the injected match."""
    from tone_forge.contracts import ToneCandidate, ToneMatch

    chosen = ToneCandidate(
        preset_id="p1",
        preset_name="Analog Lead",
        instrument="Analog",
        distance=0.1,
        calibrated_confidence=0.7,
    )
    injected = ToneMatch(
        tier=ConfidenceTier.MEDIUM,
        chosen=chosen,
        alternates=(),
        fallback_chain_id=None,
        rationale="injected match",
        debug={"src": "test"},
    )
    bundle = build(_legacy_result(), session_id="x", tone_match=injected)
    assert bundle.tone is injected  # carried verbatim, no copying
    assert bundle.tone.tier == ConfidenceTier.MEDIUM
    assert bundle.tone.chosen is not None
    assert bundle.tone.chosen.preset_id == "p1"


def test_build_injected_tone_match_overrides_legacy_blob() -> None:
    """Even with a legacy preset_matches dict present, an injected
    tone_match wins. Pins the precedence so a stale on-disk blob
    can't shadow a freshly-computed tier."""
    from tone_forge.contracts import ToneMatch

    injected = ToneMatch(
        tier=ConfidenceTier.LOW,
        chosen=None,
        alternates=(),
        fallback_chain_id="tfc.edge_of_breakup",
        rationale="low tier",
        debug={},
    )
    bundle = build(_legacy_result(), session_id="x", tone_match=injected)
    assert bundle.tone.tier == ConfidenceTier.LOW
    assert bundle.tone.fallback_chain_id == "tfc.edge_of_breakup"


def test_build_explicit_transport_override() -> None:
    from tone_forge.contracts import TransportState

    saved = TransportState(
        playing=False,
        position_s=42.5,
        tempo_pct=0.8,
        loop_in_s=10.0,
        loop_out_s=30.0,
        user_mute=False,
        monitor_gain=0.6,
    )
    bundle = build(_legacy_result(), session_id="x", initial_transport=saved)
    assert bundle.initial_transport.position_s == 42.5
    assert bundle.initial_transport.tempo_pct == 0.8
    assert bundle.initial_transport.loop_in_s == 10.0


def test_build_handles_stems_paths_fallback() -> None:
    """Legacy result may carry ``stems_paths`` instead of ``stems``."""
    result = _legacy_result()
    result.pop("stems")
    result["stems_paths"] = {
        "drums": "/path/drums.wav",
        "bass": "/path/bass.wav",
    }
    bundle = build(result, session_id="x")
    assert bundle.stems.drums is not None
    assert bundle.stems.drums.audio_url == "/path/drums.wav"


def test_build_reads_midi_from_midi_stems_fallback() -> None:
    """If ``midi`` is missing/empty, fall through to ``midi_stems[role]``."""
    result = _legacy_result()
    result["midi"] = None
    result["midi_stems"] = {
        "guitar": {
            "notes": [{"pitch": 64, "start": 0.0, "end": 0.5}],
            "overall_confidence": 0.5,
        }
    }
    bundle = build(result, session_id="x")
    assert bundle.user_midi is not None
    assert len(bundle.user_midi.notes) == 1


def test_build_returns_no_midi_when_notes_empty() -> None:
    result = _legacy_result()
    result["midi"] = {"notes": [], "overall_confidence": 0.0}
    result.pop("midi_stems", None)
    bundle = build(result, session_id="x")
    assert bundle.user_midi is None


def test_build_does_not_mutate_input() -> None:
    """The assembler must be read-only; we may pass cached dicts in."""
    result = _legacy_result()
    snapshot = {
        "sections_len": len(result["sections"]),
        "stems_keys": sorted(result["stems"].keys()),
    }
    build(result, session_id="x")
    assert len(result["sections"]) == snapshot["sections_len"]
    assert sorted(result["stems"].keys()) == snapshot["stems_keys"]
