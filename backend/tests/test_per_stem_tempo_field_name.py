"""Locks the per-stem tempo field name on the MIDI extractor outputs.

Background:

A real run on "The Chats - Pub Feed" emitted:

    result.tempo_bpm                       = 95.7  (canonical, beat_track)
    result.midi_stems.drums.tempo_bpm      = 95.70
    result.midi_stems.bass.tempo_bpm       = 129.20
    result.midi_stems.guitar.tempo_bpm     = 107.66

Three materially different tempos for a single song. The disagreement
is not a bug in the canonical estimate — it is a *naming* bug. Each
per-stem MIDI extractor estimates its own tempo from its stem alone
(onset autocorrelation for pYIN+torchcrepe, note-density heuristic
for CoreML basic_pitch). The result is a legitimate per-stem
quantity, but the field name ``tempo_bpm`` makes it look like
"THE tempo of the song" and confuses every operator who reads the
payload.

Fix: rename the per-stem field to ``extraction_tempo_bpm`` at every
emit site:

    * ``tone_forge/midi/gpu_extractor.py`` (4 sites: drum / bass /
      lead / basic_pitch_onnx)
    * ``tone_forge/midi/coreml_extractor.py`` (2 sites: empty +
      populated)
    * ``tone_forge_api.py:2058`` (direct path stem MIDI build)

The top-level ``result.tempo_bpm`` (session-canonical) is unchanged.

These tests source-inspect each emit site rather than running the
extractors. The extractors require librosa, torch, basic_pitch ONNX
runtimes, and audio fixtures — all heavy and not justified to lock
a wire-shape rename. The contract here is "the dict literal at
site X uses key 'extraction_tempo_bpm'"; source matching is the
appropriate granularity.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_BACKEND_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def gpu_extractor_source() -> str:
    p = _BACKEND_ROOT / "tone_forge" / "midi" / "gpu_extractor.py"
    assert p.exists(), f"gpu_extractor missing at {p}"
    return p.read_text()


@pytest.fixture(scope="module")
def coreml_extractor_source() -> str:
    p = _BACKEND_ROOT / "tone_forge" / "midi" / "coreml_extractor.py"
    assert p.exists(), f"coreml_extractor missing at {p}"
    return p.read_text()


@pytest.fixture(scope="module")
def api_source() -> str:
    p = _BACKEND_ROOT / "tone_forge_api.py"
    assert p.exists(), f"tone_forge_api missing at {p}"
    return p.read_text()


# ---------------------------------------------------------------------------
# 1. Old name is gone from per-stem dict literals.
# ---------------------------------------------------------------------------


def _count_per_stem_old_name(source: str) -> int:
    """Count occurrences of ``"tempo_bpm":`` (the dict-literal form)
    that are *inside per-stem return dicts*, not the top-level session
    tempo emit.

    We're loose here: the only place ``"tempo_bpm":`` should remain in
    these files is inside expressions like ``response["tempo_bpm"]`` or
    type annotations, not inside dict literals returned per stem. The
    extractor files only contain per-stem returns, so any literal
    occurrence is a regression. ``tone_forge_api.py`` has multiple
    top-level emits that legitimately use ``"tempo_bpm"`` — we exclude
    those from the count by only inspecting the stem-MIDI builder.
    """
    # Match dict-literal key. Allow either '"' or "'".
    return len(re.findall(r'["\']tempo_bpm["\']\s*:', source))


def test_gpu_extractor_dropped_old_per_stem_name(gpu_extractor_source: str) -> None:
    """``gpu_extractor.extract_midi_hybrid`` had 4 emit sites returning
    ``"tempo_bpm": ...`` per stem. All must be renamed.
    """
    count = _count_per_stem_old_name(gpu_extractor_source)
    assert count == 0, (
        f"gpu_extractor.py still contains {count} per-stem 'tempo_bpm' "
        "dict-literal emit(s); rename all to 'extraction_tempo_bpm'"
    )


def test_coreml_extractor_dropped_old_per_stem_name(
    coreml_extractor_source: str,
) -> None:
    """``coreml_extractor.extract_midi_coreml`` had 2 emit sites.
    Both must be renamed.
    """
    count = _count_per_stem_old_name(coreml_extractor_source)
    assert count == 0, (
        f"coreml_extractor.py still contains {count} per-stem 'tempo_bpm' "
        "dict-literal emit(s); rename all to 'extraction_tempo_bpm'"
    )


# ---------------------------------------------------------------------------
# 2. New name is present at every emit site.
# ---------------------------------------------------------------------------


def test_gpu_extractor_emits_new_name_four_times(gpu_extractor_source: str) -> None:
    """gpu_extractor has 4 stem branches (drum, bass, lead, basic_pitch).
    Each must emit the renamed field.
    """
    count = len(
        re.findall(
            r'["\']extraction_tempo_bpm["\']\s*:', gpu_extractor_source
        )
    )
    assert count == 4, (
        f"gpu_extractor.py emits 'extraction_tempo_bpm' at {count} sites; "
        "expected 4 (drum / bass / lead / basic_pitch_onnx)"
    )


def test_coreml_extractor_emits_new_name_twice(coreml_extractor_source: str) -> None:
    """coreml_extractor has 2 return branches (empty + populated).
    Both must emit the renamed field.
    """
    count = len(
        re.findall(
            r'["\']extraction_tempo_bpm["\']\s*:', coreml_extractor_source
        )
    )
    assert count == 2, (
        f"coreml_extractor.py emits 'extraction_tempo_bpm' at {count} sites; "
        "expected 2 (empty-result + populated branch)"
    )


# ---------------------------------------------------------------------------
# 3. Direct-path stem-MIDI builder in tone_forge_api.py
# ---------------------------------------------------------------------------


def test_api_stem_midi_builder_uses_new_name(api_source: str) -> None:
    """The direct (non-local-engine) path builds ``stem_midi_data``
    around tone_forge_api.py:2050. That dict must emit the renamed
    field, otherwise the direct path will be inconsistent with the
    local-engine path.
    """
    # The marker for the right block is the dict that also contains
    # ``stem_midi.note_count`` — find that block and check for the
    # renamed field within a reasonable window.
    pattern = re.compile(
        r'stem_midi_data\s*=\s*\{[^}]*?extraction_tempo_bpm[^}]*?\}',
        re.DOTALL,
    )
    assert pattern.search(api_source), (
        "stem_midi_data dict in tone_forge_api.py no longer emits "
        "'extraction_tempo_bpm'; the direct path will diverge from the "
        "local-engine path"
    )


# ---------------------------------------------------------------------------
# 4. Top-level session tempo at result root remains 'tempo_bpm'.
# ---------------------------------------------------------------------------


def test_local_engine_top_level_tempo_still_canonical() -> None:
    """The session-canonical tempo at ``result.tempo_bpm`` MUST NOT be
    renamed — the frontend reads it for the now-playing strip and the
    looper grid. Only the per-stem field was ambiguous.
    """
    worker = (
        _BACKEND_ROOT / "local_engine" / "analysis_worker.py"
    ).read_text()
    # The result dict carries a single top-level "tempo_bpm" key.
    assert re.search(r'"tempo_bpm"\s*:\s*tempo_bpm', worker), (
        "local_engine/analysis_worker.py result dict no longer carries "
        "the canonical top-level 'tempo_bpm' field; the Jam UI's "
        "now-playing strip will fall through to '— bpm'"
    )
