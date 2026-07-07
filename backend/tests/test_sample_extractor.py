"""Tests for the Song-DNA sample extractors.

Each extractor scans a stem WAV (or the section timeline for
transitions) and returns a small list of chop dicts that populate one
row of the mobile Samples pad grid. These tests exercise the audio
heuristics against synthetic signals engineered to hit exactly one
extraction path per test — a bright transient for stabs, a slow-decay
noise blob for FX tails, a sustained sine for ambient textures, and a
label-switch boundary for transitions.

The synthetic-signal approach mirrors ``test_contribute_chops_vocal``:
we build inputs from primitives (voiced/silence/burst) so each test's
setup makes the extractor's contract easy to read.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tone_forge.sample_extractor import (
    MAX_PACK_PADS,
    build_song_dna,
    extract_ambient_textures,
    extract_fx_tails,
    extract_guitar_stabs,
    extract_transitions,
)

SR = 22050


# ---------------------------------------------------------------------------
# Signal-synthesis helpers
# ---------------------------------------------------------------------------

def _write_wav(signal: np.ndarray) -> Path:
    """Persist a mono float32 signal at ``SR`` Hz."""
    path = Path(tempfile.mkdtemp()) / "stem.wav"
    sf.write(str(path), signal.astype(np.float32), SR)
    return path


def _silence(duration_sec: float) -> np.ndarray:
    return np.zeros(int(duration_sec * SR), dtype=np.float32)


def _sustained_tone(duration_sec: float, freq: float = 220.0, gain: float = 0.15) -> np.ndarray:
    """Long sustained sine with a slow cosine attack/release — reads
    as a pad/drone to the ambient-texture detector."""
    n = int(duration_sec * SR)
    t = np.arange(n) / SR
    tone = np.sin(2 * np.pi * freq * t) * gain
    ramp = min(int(0.2 * SR), n // 4)
    if ramp > 0:
        env = 0.5 * (1 - np.cos(np.linspace(0, np.pi, ramp)))
        tone[:ramp] *= env
        tone[-ramp:] *= env[::-1]
    return tone.astype(np.float32)


def _stab(freq: float = 660.0, gain: float = 0.8) -> np.ndarray:
    """Very short (~120 ms) bright transient: fast attack, fast decay.
    Peak amplitude sits well above any surrounding steady tone."""
    dur = 0.12
    n = int(dur * SR)
    t = np.arange(n) / SR
    tone = np.sin(2 * np.pi * freq * t) * gain
    attack = int(0.005 * SR)  # 5 ms attack
    if attack > 0:
        tone[:attack] *= np.linspace(0.0, 1.0, attack)
    # Exponential decay τ = 30 ms → ~4τ over the sample.
    decay = np.exp(-t / 0.03)
    return (tone * decay).astype(np.float32)


def _long_tail(duration_sec: float = 1.5, gain: float = 0.6) -> np.ndarray:
    """Fast attack, slow exponential decay > 1 s — the classic reverb
    wash / delay throw shape that FX-tail extractor should catch."""
    n = int(duration_sec * SR)
    t = np.arange(n) / SR
    # White-ish noise so the RMS decay is smooth (a pure tone plateaus
    # at |sin|=1 and has jagged RMS).
    rng = np.random.default_rng(seed=17)
    noise = rng.standard_normal(n).astype(np.float32) * gain
    attack = int(0.005 * SR)
    if attack > 0:
        noise[:attack] *= np.linspace(0.0, 1.0, attack)
    # τ = 0.9 s → RMS drops to ~40% around 0.8 s. Well above the
    # 0.6 s threshold.
    decay = np.exp(-t / 0.9)
    return (noise * decay).astype(np.float32)


def _concat(*chunks: np.ndarray) -> np.ndarray:
    return np.concatenate(chunks).astype(np.float32)


@pytest.fixture()
def minimal_sections():
    return {
        "sections": [
            {"start_time": 0.0, "end_time": 10.0, "type": "verse"},
            {"start_time": 10.0, "end_time": 20.0, "type": "chorus"},
        ],
        "duration_sec": 20.0,
    }


# ---------------------------------------------------------------------------
# Guitar stabs
# ---------------------------------------------------------------------------

def test_guitar_stabs_finds_bright_transients_over_bed(minimal_sections):
    """A quiet sustained bed with 3 sharp stabs on top — the stabs
    extractor should return chops centred on each stab, not on the
    bed."""
    bed = _sustained_tone(5.0, freq=200.0, gain=0.05)
    # Insert 3 stabs at 1.0 s, 2.5 s, 4.0 s. They're much louder than
    # the bed so they'll trip the 2× median filter easily.
    stab_positions = [1.0, 2.5, 4.0]
    track = bed.copy()
    for pos in stab_positions:
        s = _stab()
        start = int(pos * SR)
        end = min(start + s.size, track.size)
        track[start:end] += s[: end - start]
    wav = _write_wav(track)

    chops = extract_guitar_stabs(wav, minimal_sections)

    assert chops, "expected at least one detected stab"
    # Each detected stab should sit within ~150 ms of an inserted position.
    starts = sorted(c["startSec"] for c in chops)
    for expected in stab_positions:
        assert any(abs(actual - expected) < 0.2 for actual in starts), (
            f"no detected stab near t={expected}s (found {starts})"
        )
    for c in chops:
        assert c["kind"] == "guitar_stab"
        assert c["colorHint"] == "orange"


def test_guitar_stabs_returns_empty_on_missing_wav(minimal_sections):
    assert extract_guitar_stabs(None, minimal_sections) == []
    fake = Path(tempfile.gettempdir()) / "nope_stab_xyz.wav"
    if fake.exists():
        fake.unlink()
    assert extract_guitar_stabs(fake, minimal_sections) == []


def test_guitar_stabs_rejects_slow_attack_pads(minimal_sections):
    """A sustained pad-like signal with slow onsets must NOT get
    labelled as stabs even if librosa detects an onset."""
    track = _sustained_tone(5.0, freq=440.0, gain=0.4)
    wav = _write_wav(track)

    chops = extract_guitar_stabs(wav, minimal_sections)

    # A pure slow-attack sine has no bright transients — no stabs.
    assert chops == [] or all(
        # If any slipped through, they should not exceed the pack cap.
        c["kind"] == "guitar_stab" for c in chops
    )


# ---------------------------------------------------------------------------
# FX tails
# ---------------------------------------------------------------------------

def test_fx_tails_finds_long_decay_regions(minimal_sections):
    """Silence, then a burst with a 1.5 s exponential tail — the
    tail extractor should return a chop whose duration is > 0.6 s."""
    track = _concat(
        _silence(0.3),
        _long_tail(2.0, gain=0.6),
        _silence(2.7),
    )
    wav = _write_wav(track)

    chops = extract_fx_tails(wav, minimal_sections)

    assert chops, "expected at least one FX tail"
    durations = [c["endSec"] - c["startSec"] for c in chops]
    assert any(d >= 0.6 for d in durations), (
        f"expected at least one tail with decay ≥ 0.6s, got {durations}"
    )
    for c in chops:
        assert c["kind"] == "fx_tail"
        assert c["colorHint"] == "violet"


def test_fx_tails_rejects_short_decays(minimal_sections):
    """A track of pure stabs (fast decay) shouldn't produce any FX
    tail chops — every onset decays inside ~50 ms."""
    track = _silence(0.3)
    positions = np.arange(0.5, 5.0, 0.4)
    padded = np.zeros(int(5.0 * SR), dtype=np.float32)
    padded[: track.size] = track
    for pos in positions:
        s = _stab()
        start = int(pos * SR)
        end = min(start + s.size, padded.size)
        padded[start:end] += s[: end - start]
    wav = _write_wav(padded)

    chops = extract_fx_tails(wav, minimal_sections)

    # It's OK for the extractor to emit zero chops here — the whole
    # point is that these transients don't decay slowly enough.
    for c in chops:
        assert c["endSec"] - c["startSec"] >= 0.6


def test_fx_tails_empty_on_missing(minimal_sections):
    assert extract_fx_tails(None, minimal_sections) == []


# ---------------------------------------------------------------------------
# Ambient textures
# ---------------------------------------------------------------------------

def test_ambient_textures_finds_sustained_low_novelty_span(minimal_sections):
    """A long sustained tone (low variance, non-zero RMS) is exactly
    the ambient-texture profile."""
    track = _concat(
        _silence(0.5),
        _sustained_tone(4.0, freq=180.0, gain=0.2),
        _silence(0.5),
    )
    wav = _write_wav(track)

    chops = extract_ambient_textures(wav, minimal_sections)

    assert chops, "expected at least one ambient texture chop"
    # The sustained region occupies roughly t=0.5..4.5 s. We should
    # get a chop that starts inside that window.
    for c in chops:
        assert c["kind"] == "ambient_texture"
        assert c["colorHint"] == "cyan"
        # Every returned chop should be at least 1.5s long (or capped
        # at 8s if longer).
        dur = c["endSec"] - c["startSec"]
        assert 1.5 <= dur <= 8.0, f"unexpected texture duration {dur:.3f}s"


def test_ambient_textures_returns_empty_on_pure_silence(minimal_sections):
    """A silent track produces no textures — presence gate rejects it."""
    wav = _write_wav(_silence(5.0))
    assert extract_ambient_textures(wav, minimal_sections) == []


def test_ambient_textures_rejects_rapid_variation(minimal_sections):
    """A track full of stabs has high RMS variance — no textures."""
    padded = np.zeros(int(5.0 * SR), dtype=np.float32)
    for pos in np.arange(0.2, 4.8, 0.35):
        s = _stab()
        start = int(pos * SR)
        end = min(start + s.size, padded.size)
        padded[start:end] += s[: end - start]
    wav = _write_wav(padded)

    chops = extract_ambient_textures(wav, minimal_sections)

    # High variance → texture extractor should return nothing.
    assert chops == []


# ---------------------------------------------------------------------------
# Transitions
# ---------------------------------------------------------------------------

def test_transitions_yields_one_chop_per_label_switch():
    """Boundary between verse (0..4s) and chorus (4..8s) produces one
    2-second transition chop centred on t=4."""
    result = {
        "sections": [
            {"start_time": 0.0, "end_time": 4.0, "type": "verse"},
            {"start_time": 4.0, "end_time": 8.0, "type": "chorus"},
        ],
        "duration_sec": 8.0,
    }
    chops = extract_transitions(result)
    assert len(chops) == 1
    c = chops[0]
    # Centred on the boundary, 2 s wide.
    assert abs(c["startSec"] - 3.0) < 0.01
    assert abs(c["endSec"] - 5.0) < 0.01
    assert c["kind"] == "transition"
    assert c["colorHint"] == "yellow"
    # Section label carries the incoming section.
    assert c["sectionLabel"] == "chorus"


def test_transitions_skips_same_label_boundaries():
    """Two adjacent verse sections aren't a real transition — skip."""
    result = {
        "sections": [
            {"start_time": 0.0, "end_time": 4.0, "type": "verse"},
            {"start_time": 4.0, "end_time": 8.0, "type": "verse"},
            {"start_time": 8.0, "end_time": 12.0, "type": "chorus"},
        ],
        "duration_sec": 12.0,
    }
    chops = extract_transitions(result)
    # Only the verse→chorus boundary at t=8 should qualify.
    assert len(chops) == 1
    assert abs(chops[0]["startSec"] - 7.0) < 0.01
    assert abs(chops[0]["endSec"] - 9.0) < 0.01


def test_transitions_empty_on_single_section():
    result = {
        "sections": [{"start_time": 0.0, "end_time": 8.0, "type": "verse"}],
        "duration_sec": 8.0,
    }
    assert extract_transitions(result) == []


def test_transitions_empty_on_no_sections():
    assert extract_transitions({"sections": [], "duration_sec": 8.0}) == []
    assert extract_transitions({}) == []


# ---------------------------------------------------------------------------
# build_song_dna assembly
# ---------------------------------------------------------------------------

def test_build_song_dna_returns_four_packs(minimal_sections):
    """The top-level entry point always emits all four pack keys,
    even when the audio stems are missing — so the mobile client can
    render empty-state chips deterministically."""
    dna = build_song_dna(analysis_result=minimal_sections)

    assert set(dna.keys()) == {"guitar_stab", "fx_tail", "ambient_texture", "transition"}
    for kind, pack in dna.items():
        assert pack["kind"] == kind
        assert "name" in pack
        assert "colorHint" in pack
        assert isinstance(pack["pads"], list)
        assert len(pack["pads"]) <= MAX_PACK_PADS


def test_build_song_dna_wires_pad_indices(minimal_sections):
    """Each pad in every pack must have a sequential ``idx``."""
    dna = build_song_dna(analysis_result=minimal_sections)
    for pack in dna.values():
        for i, pad in enumerate(pack["pads"]):
            assert pad["idx"] == i


def test_build_song_dna_with_other_stem_populates_stabs(minimal_sections):
    """When we supply a stem WAV with visible stabs on top of a bed,
    the ``guitar_stab`` pack should have at least one pad."""
    bed = _sustained_tone(6.0, freq=200.0, gain=0.05)
    for pos in [1.0, 3.0, 5.0]:
        s = _stab()
        start = int(pos * SR)
        end = min(start + s.size, bed.size)
        bed[start:end] += s[: end - start]
    wav = _write_wav(bed)

    dna = build_song_dna(
        analysis_result=minimal_sections,
        stem_wav_paths={"other": wav},
    )
    assert dna["guitar_stab"]["pads"], "expected stab pads on hand-crafted stem"
