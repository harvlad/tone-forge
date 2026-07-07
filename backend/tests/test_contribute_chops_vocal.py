"""Tests for the vocal-phrase slicer in ``contribute_chops``.

The slicer's job: given a vocals-stem WAV plus the song's analysis
result, return short (~0.3–2.5 s) chops that start on the first
voiced frame of a phrase (not on the section downbeat). These tests
cover the invariants that the UX depends on:

  * Silent leading air is stripped — a WAV that opens with 1 s of
    silence produces a first chop whose ``startSec`` is inside the
    voiced region, not at 0.
  * No phrase exceeds ``MAX_PHRASE_SEC`` (2.5 s).
  * All phrases meet ``MIN_PHRASE_SEC`` (0.15 s).
  * Fully-instrumental / silent WAVs return an empty list so the
    caller's waterfall (section fallback) fires.
  * Missing / unreadable WAVs return an empty list without raising.
  * The public ``build_chops`` dispatcher routes ``slice_mode='phrase'``
    with ``stem='vocals'`` through the new slicer and falls back to
    section chops when the WAV is absent.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tone_forge.contribute_chops import (
    _chops_from_vocal_phrases,
    build_chops,
)

SR = 22050


def _write_wav(signal: np.ndarray) -> Path:
    """Write ``signal`` to a temp WAV and return the path."""
    path = Path(tempfile.mkdtemp()) / "vocals.wav"
    sf.write(str(path), signal.astype(np.float32), SR)
    return path


def _voiced_burst(duration_sec: float, freq: float = 220.0, gain: float = 0.4) -> np.ndarray:
    """Synth a simple sung-vowel-like sine burst — narrowband tone
    at ``freq`` Hz, envelope tapered at the edges so RMS ramps in
    and out realistically."""
    n = int(duration_sec * SR)
    t = np.arange(n) / SR
    tone = np.sin(2 * np.pi * freq * t) * gain
    # Cosine-shaped attack/release so RMS onset isn't a hard step.
    ramp_len = min(int(0.02 * SR), n // 4)
    if ramp_len > 0:
        ramp = 0.5 * (1 - np.cos(np.linspace(0, np.pi, ramp_len)))
        tone[:ramp_len] *= ramp
        tone[-ramp_len:] *= ramp[::-1]
    return tone.astype(np.float32)


def _silence(duration_sec: float) -> np.ndarray:
    return np.zeros(int(duration_sec * SR), dtype=np.float32)


def _build_track(*chunks: np.ndarray) -> np.ndarray:
    """Concat a sequence of numpy arrays into one contiguous track."""
    return np.concatenate(chunks).astype(np.float32)


# ---------------------------------------------------------------------------
# Fixture: minimal analysis result. The slicer only reads ``sections``
# from the result dict; every other field is ignored.
# ---------------------------------------------------------------------------

@pytest.fixture()
def minimal_result():
    return {
        "sections": [
            {"start_time": 0.0, "end_time": 30.0, "type": "verse"},
        ],
        "duration_sec": 30.0,
    }


# ---------------------------------------------------------------------------
# Core invariants
# ---------------------------------------------------------------------------

def test_slicer_strips_leading_silence(minimal_result):
    """A track that opens with 1 s of silence, then a voiced burst,
    must produce a first chop whose start is inside the voiced
    region — not at 0."""
    lead_silence = 1.0
    track = _build_track(
        _silence(lead_silence),
        _voiced_burst(0.6),
        _silence(0.5),
        _voiced_burst(0.6),
    )
    wav = _write_wav(track)

    chops = _chops_from_vocal_phrases(minimal_result, wav)

    assert chops, "expected at least one voiced phrase"
    first_start = chops[0]["startSec"]
    # First voiced sample is at t = lead_silence. Allow generous
    # tolerance since smoothing + threshold shift the detected
    # onset by a few frames. Anything under 0.5 s is a clear pass
    # (the pre-fix behaviour would have been 0.0).
    assert 0.5 < first_start < lead_silence + 0.3, (
        f"first chop start {first_start:.3f}s should sit inside the "
        f"voiced burst starting at {lead_silence:.3f}s"
    )


def test_no_chop_exceeds_max_phrase_seconds(minimal_result):
    """A single sustained 6-second voiced pass must be split into
    multiple chops, none longer than the 2.5 s cap."""
    track = _build_track(
        _silence(0.2),
        _voiced_burst(6.0),
        _silence(0.2),
    )
    wav = _write_wav(track)

    chops = _chops_from_vocal_phrases(minimal_result, wav)

    assert len(chops) >= 2, "6-second sustained voiced region should split"
    for c in chops:
        dur = c["endSec"] - c["startSec"]
        assert dur <= 2.55, (
            f"phrase duration {dur:.3f}s exceeds MAX_PHRASE_SEC (2.5s)"
        )


def test_all_chops_meet_min_phrase_length(minimal_result):
    """No returned chop should be shorter than MIN_PHRASE_SEC (0.15 s).
    Sub-threshold spans are dropped as breath/plosive artefacts."""
    track = _build_track(
        _silence(0.3),
        _voiced_burst(0.5),
        _silence(0.4),
        _voiced_burst(0.4),
        _silence(0.4),
        _voiced_burst(0.35),
    )
    wav = _write_wav(track)

    chops = _chops_from_vocal_phrases(minimal_result, wav)

    assert chops, "expected voiced phrases in the fixture"
    for c in chops:
        dur = c["endSec"] - c["startSec"]
        assert dur >= 0.15, f"phrase duration {dur:.3f}s below minimum"


def test_silent_track_returns_empty(minimal_result):
    """A track with no vocal content produces no phrase chops so
    the caller can fall back to sections."""
    track = _silence(5.0)
    wav = _write_wav(track)

    chops = _chops_from_vocal_phrases(minimal_result, wav)

    assert chops == []


def test_missing_wav_returns_empty(minimal_result):
    """A non-existent WAV path must not raise; the caller falls
    back to the section-based waterfall."""
    fake_path = Path(tempfile.gettempdir()) / "does_not_exist_xyz.wav"
    if fake_path.exists():
        fake_path.unlink()

    chops = _chops_from_vocal_phrases(minimal_result, fake_path)

    assert chops == []


def test_none_wav_returns_empty(minimal_result):
    """None (no stem WAV supplied) must degrade cleanly to []."""
    chops = _chops_from_vocal_phrases(minimal_result, None)
    assert chops == []


# ---------------------------------------------------------------------------
# Section-label attachment
# ---------------------------------------------------------------------------

def test_phrase_inherits_section_label():
    """A phrase that starts inside a section span carries that
    section's label. Used by the client for pad colouring."""
    result = {
        "sections": [
            {"start_time": 0.0, "end_time": 2.0, "type": "verse"},
            {"start_time": 2.0, "end_time": 6.0, "type": "chorus"},
        ],
        "duration_sec": 6.0,
    }
    # Two bursts: one in the verse span, one in the chorus span.
    track = _build_track(
        _silence(0.3),        # 0.0 - 0.3
        _voiced_burst(0.6),   # 0.3 - 0.9  (verse)
        _silence(1.4),        # 0.9 - 2.3
        _voiced_burst(0.6),   # 2.3 - 2.9  (chorus)
        _silence(3.1),        # 2.9 - 6.0
    )
    wav = _write_wav(track)

    chops = _chops_from_vocal_phrases(result, wav)

    assert len(chops) >= 2
    labels = {c["sectionLabel"] for c in chops}
    assert "verse" in labels
    assert "chorus" in labels


# ---------------------------------------------------------------------------
# build_chops dispatcher wiring
# ---------------------------------------------------------------------------

def test_build_chops_routes_vocals_phrase_through_slicer(minimal_result):
    """build_chops with stem='vocals' + slice_mode='phrase' must
    invoke the new slicer when a WAV is supplied — the returned
    chops carry ``kind='phrase'`` (only the vocal-phrase slicer
    tags chops this way)."""
    track = _build_track(
        _silence(0.3),
        _voiced_burst(0.6),
        _silence(0.4),
        _voiced_burst(0.6),
    )
    wav = _write_wav(track)

    chops = build_chops(
        stem="vocals",
        slice_mode="phrase",
        analysis_result=minimal_result,
        stem_wav_path=wav,
    )

    assert chops, "expected phrase chops"
    # At least one phrase-tagged chop confirms the slicer fired.
    assert any(c.get("kind") == "phrase" for c in chops)


def test_build_chops_phrase_falls_back_to_sections_without_wav(minimal_result):
    """No WAV supplied → the dispatcher falls back to section chops
    (the pre-fix behaviour), so the pad grid still populates on
    songs where the vocals stem file is missing."""
    chops = build_chops(
        stem="vocals",
        slice_mode="phrase",
        analysis_result=minimal_result,
        stem_wav_path=None,
    )

    assert chops, "expected fallback section chops"
    # Section fallback produces sectionLabel from the fixture verse.
    assert any(c.get("sectionLabel") == "verse" for c in chops)
    # And none of them are phrase-tagged (that tag is exclusive to
    # the vocal-phrase slicer).
    assert not any(c.get("kind") == "phrase" for c in chops)


def test_build_chops_non_vocals_stem_ignores_slicer(minimal_result):
    """stem='drums' with slice_mode='phrase' must NOT invoke the
    vocal-phrase slicer even when a WAV is present — the slicer's
    heuristics are only meaningful for vocals."""
    # A WAV is supplied but the stem name is 'drums'; the dispatcher
    # should skip the vocal-phrase branch and fall through to
    # sections.
    track = _build_track(_silence(0.3), _voiced_burst(0.6))
    wav = _write_wav(track)

    chops = build_chops(
        stem="drums",
        slice_mode="phrase",
        analysis_result=minimal_result,
        stem_wav_path=wav,
    )

    # Fallback is section chops → no phrase-tagged entries.
    assert not any(c.get("kind") == "phrase" for c in chops)
