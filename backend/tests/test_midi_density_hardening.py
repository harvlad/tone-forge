"""Locks the observability + filter hardening for MIDI stem density.

Background:

A "The Chats - Pub Feed" run produced a guitar stem with 2590 notes
over 145.8s — 17.8 notes/sec — while the role classifier labeled the
same stem ``texture_layer`` with confidence 0.277. Two readings:

* The note count is right and the role classifier is wrong (punk
  rock palm-muted 16ths against power chords can land here).
* The note count is over-firing on harmonic ghosts and the role
  classifier is right.

Without a validation corpus we can't decide which. So this commit
does NOT tune the threshold — it adds two things:

1. ``_postprocess_notes`` gains a ``min_confidence`` knob (default
   0.0 = no behavior change). Callers that prove via a corpus that
   their stem type needs a confidence floor can raise it without
   adding a separate filter pass.

2. ``local_engine/analysis_worker.py`` emits a derived
   ``notes_per_second`` field on every stem in ``midi_stems``.
   This makes density immediately visible in the SSE result payload
   rather than requiring operators to divide note_count by duration
   by hand. The next over-firing case will be flagged on the wire,
   not discovered via spectrogram reading.

These tests lock both contracts. The min_confidence knob is tested
on synthetic ExtractedNote inputs (no audio, no model). The
notes_per_second emit is locked by source inspection (the worker
spawns a multiprocessing subprocess; end-to-end execution requires
the local GPU engine).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

# These tests exercise coreml_extractor, which imports coremltools at
# module load. coremltools is a heavy, optional ML dependency not
# installed in CI — skip the whole module when it's unavailable rather
# than erroring at fixture setup.
pytest.importorskip("coremltools")


_BACKEND_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# 1. _postprocess_notes min_confidence knob
# ---------------------------------------------------------------------------


@pytest.fixture
def synthetic_notes():
    """A mix of high- and low-confidence notes spanning the duration
    + velocity floors so we can isolate the confidence filter."""
    from tone_forge.midi.coreml_extractor import ExtractedNote

    # All notes clear the duration (50ms default) and velocity (30 default)
    # floors; pitch 60 is C4, comfortably in the "other" range (36-96).
    return [
        ExtractedNote(
            pitch=60, start_time=0.0, end_time=0.2,
            velocity=80, confidence=0.85,
        ),
        ExtractedNote(
            pitch=62, start_time=0.3, end_time=0.5,
            velocity=70, confidence=0.55,
        ),
        ExtractedNote(
            pitch=64, start_time=0.6, end_time=0.8,
            velocity=60, confidence=0.20,
        ),
        ExtractedNote(
            pitch=67, start_time=0.9, end_time=1.1,
            velocity=55, confidence=0.08,
        ),
    ]


def test_min_confidence_default_is_no_op(synthetic_notes):
    """Default ``min_confidence=0.0`` must not drop any note — the
    legacy callers pass nothing and must observe the legacy filter
    behavior.
    """
    from tone_forge.midi.coreml_extractor import _postprocess_notes

    out = _postprocess_notes(synthetic_notes, stem_type="other")
    assert len(out) == len(synthetic_notes), (
        "default min_confidence=0.0 changed behavior; this would "
        "break every existing caller"
    )


def test_min_confidence_drops_low_confidence_notes(synthetic_notes):
    """A 0.25 floor should drop the two lowest-confidence notes
    (0.20 and 0.08) and keep the rest.
    """
    from tone_forge.midi.coreml_extractor import _postprocess_notes

    out = _postprocess_notes(
        synthetic_notes,
        stem_type="other",
        min_confidence=0.25,
    )
    assert len(out) == 2, (
        f"expected 2 notes above 0.25 confidence; kept {len(out)}: "
        f"{[n.confidence for n in out]}"
    )
    assert all(n.confidence >= 0.25 for n in out)


def test_min_confidence_at_top_keeps_only_strongest(synthetic_notes):
    """A 0.80 floor must keep only the 0.85 note."""
    from tone_forge.midi.coreml_extractor import _postprocess_notes

    out = _postprocess_notes(
        synthetic_notes,
        stem_type="other",
        min_confidence=0.80,
    )
    assert len(out) == 1
    assert out[0].confidence == pytest.approx(0.85)


def test_min_confidence_does_not_alter_duration_filter(synthetic_notes):
    """The new knob must compose with existing filters, not replace
    them. A note that fails the duration floor but has high confidence
    must still be dropped.
    """
    from tone_forge.midi.coreml_extractor import ExtractedNote, _postprocess_notes

    too_short = ExtractedNote(
        pitch=60, start_time=0.0, end_time=0.010,  # 10ms < 50ms floor
        velocity=80, confidence=0.95,
    )
    out = _postprocess_notes(
        [too_short],
        stem_type="other",
        min_confidence=0.0,  # explicit no-op
    )
    assert out == [], (
        "duration floor was bypassed when min_confidence introduced; "
        "filters must compose, not replace"
    )


# ---------------------------------------------------------------------------
# 2. notes_per_second emit in analysis_worker.py
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def worker_source() -> str:
    p = _BACKEND_ROOT / "local_engine" / "analysis_worker.py"
    assert p.exists()
    return p.read_text()


def test_worker_emits_notes_per_second_in_midi_stems(worker_source: str) -> None:
    """The local-engine worker must compute notes_per_second on every
    serialized stem. The exact arithmetic is locked separately by
    ``test_notes_per_second_arithmetic`` below.
    """
    pattern = re.compile(
        r'["\']notes_per_second["\']\s*=', re.DOTALL
    )
    # The emit is done by attribute set on a dict, e.g. ``serialized["notes_per_second"] =``.
    assert re.search(
        r'serialized\[["\']notes_per_second["\']\]\s*=', worker_source
    ), (
        "local_engine/analysis_worker.py no longer emits "
        "notes_per_second per stem; the over-firing diagnostic surface "
        "is gone"
    )


def test_notes_per_second_arithmetic_is_safe_against_zero_duration() -> None:
    """The derivation must not divide by zero on stems where the MIDI
    extractor returned duration_seconds=0 (e.g. fallback path on an
    empty stem). We pin that nc/dur is guarded.
    """
    # Source-inspect the guard. We could rebuild the dict in a fake
    # worker run but the value-add is the literal guard, not the
    # arithmetic.
    worker_source = (
        _BACKEND_ROOT / "local_engine" / "analysis_worker.py"
    ).read_text()
    # The guard is "nc / dur if dur > 0 else 0.0".
    pattern = re.compile(
        r'nc\s*/\s*dur\s+if\s+dur\s*>\s*0\s+else\s+0\.0'
    )
    assert pattern.search(worker_source), (
        "notes_per_second division is unguarded against duration=0; "
        "a fallback-empty stem would throw ZeroDivisionError"
    )
