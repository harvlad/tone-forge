"""Unit tests for the founder-corpus per-field comparators.

Pure logic — no pipeline, no I/O. Each test feeds a synthetic
AnalysisResult.to_dict()-shaped payload through the relevant comparator
and asserts the FieldResult is what we expect.
"""
from __future__ import annotations

import pytest

from tone_forge.evaluation.founder_corpus import (
    EXIT_HARD_FAIL,
    EXIT_OK,
    CorpusEntry,
    FieldResult,
    compare,
    compute_exit_code,
    format_markdown_report,
)
from pathlib import Path


# --- tiny fixture builders --------------------------------------------------

def _entry(id_: str = "x", tier: str = "smoke") -> CorpusEntry:
    return CorpusEntry(
        id=id_,
        audio_path=Path("/tmp/__nonexistent.wav"),
        expected_path=Path("/tmp/__nonexistent.json"),
        tier=tier,
        notes="",
    )


# --- duration ---------------------------------------------------------------

def test_duration_within_tolerance_passes():
    results = compare({"duration_s": {"value": 10.0, "tolerance_s": 0.5, "gate": "hard"}},
                      {"duration_sec": 10.3})
    assert len(results) == 1
    assert results[0].passed
    assert results[0].field == "duration_s"
    assert results[0].gate == "hard"
    assert "Δ 0.30" in results[0].actual_repr


def test_duration_outside_tolerance_fails():
    results = compare({"duration_s": {"value": 10.0, "tolerance_s": 0.5, "gate": "hard"}},
                      {"duration_sec": 11.0})
    assert not results[0].passed
    assert results[0].severity == "FAIL"


def test_duration_missing_is_failure_with_message():
    results = compare({"duration_s": {"value": 10.0, "tolerance_s": 0.5, "gate": "hard"}},
                      {})
    assert not results[0].passed
    assert "missing" == results[0].actual_repr
    assert "duration_sec" in results[0].message


# --- tempo ------------------------------------------------------------------

def test_tempo_reads_from_midi_block():
    results = compare({"tempo_bpm": {"value": 120.0, "tolerance_bpm": 2.0, "gate": "hard"}},
                      {"midi": {"tempo": 121.0}})
    assert results[0].passed


def test_tempo_falls_back_to_midi_stats():
    results = compare({"tempo_bpm": {"value": 120.0, "tolerance_bpm": 2.0, "gate": "hard"}},
                      {"midi_stats": {"tempo": 121.0}})
    assert results[0].passed


def test_tempo_missing_fails_hard():
    results = compare({"tempo_bpm": {"value": 120.0, "gate": "hard"}}, {})
    assert results[0].severity == "FAIL"


# --- key --------------------------------------------------------------------

def test_key_exact_match_passes():
    results = compare({"key": {"value": "C major", "gate": "soft"}},
                      {"key": "C major"})
    assert results[0].passed


def test_key_relative_minor_optional():
    spec = {"key": {"value": "C major", "allow_relative_minor": True, "gate": "soft"}}
    assert compare(spec, {"key": "A minor"})[0].passed
    assert compare(spec, {"key": "D minor"})[0].passed is False


def test_key_relative_minor_default_off():
    spec = {"key": {"value": "C major", "gate": "soft"}}
    assert compare(spec, {"key": "A minor"})[0].passed is False


def test_key_searches_understanding_path():
    results = compare({"key": {"value": "G major", "gate": "soft"}},
                      {"understanding": {"key": "G major"}})
    assert results[0].passed


def test_key_missing_is_soft_warn_by_default():
    r = compare({"key": {"value": "C major"}}, {})[0]
    assert r.gate == "soft"
    assert r.severity == "WARN"


# --- detected_type ----------------------------------------------------------

def test_detected_type_match():
    r = compare({"detected_type": {"value": "guitar", "gate": "hard"}},
                {"detected_type": "guitar"})[0]
    assert r.passed


def test_detected_type_mismatch():
    r = compare({"detected_type": {"value": "guitar", "gate": "hard"}},
                {"detected_type": "synth"})[0]
    assert not r.passed
    assert r.severity == "FAIL"


# --- section_count + chord_count -------------------------------------------

def test_section_count_within_tolerance():
    r = compare({"section_count": {"value": 4, "tolerance": 1}},
                {"sections": [{}, {}, {}, {}, {}]})[0]
    assert r.passed


def test_section_count_outside_tolerance():
    r = compare({"section_count": {"value": 4, "tolerance": 0}},
                {"sections": [{}, {}, {}]})[0]
    assert not r.passed


def test_chord_count_in_range():
    r = compare({"chord_count": {"min": 4, "max": 10}},
                {"chords": [{}, {}, {}, {}, {}, {}]})[0]
    assert r.passed


def test_chord_count_below_min():
    r = compare({"chord_count": {"min": 4, "max": 10}},
                {"chords": [{}, {}]})[0]
    assert not r.passed


def test_chord_count_above_max():
    r = compare({"chord_count": {"min": 4, "max": 10}},
                {"chords": [{}] * 11})[0]
    assert not r.passed


# --- guitar_midi_note_count -------------------------------------------------

def test_guitar_midi_note_count_prefers_per_stem():
    r = compare({"guitar_midi_note_count": {"min": 10, "max": 100}},
                {"midi_stems": {"guitar": {"note_count": 50}},
                 "midi": {"note_count": 999}})[0]
    assert r.passed  # 50 wins, not 999


def test_guitar_midi_note_count_falls_back_to_top_level():
    r = compare({"guitar_midi_note_count": {"min": 10, "max": 100}},
                {"midi": {"note_count": 50}})[0]
    assert r.passed


def test_guitar_midi_note_count_missing_fails():
    r = compare({"guitar_midi_note_count": {"min": 10, "max": 100, "gate": "hard"}}, {})[0]
    assert not r.passed
    assert r.severity == "FAIL"


# --- gate roll-up -----------------------------------------------------------

def test_unknown_keys_silently_ignored():
    # Forward-compat: an expected with a key the registry doesn't know about
    # is allowed (the integrity test catches typos separately; the comparator
    # itself stays permissive).
    results = compare({"future_field_we_dont_know_about": {"value": 1}}, {})
    assert results == []


def test_invalid_gate_raises():
    with pytest.raises(ValueError, match="gate"):
        compare({"duration_s": {"value": 10.0, "gate": "blocking"}}, {"duration_sec": 10.0})


# --- exit-code rollup -------------------------------------------------------

def test_compute_exit_code_all_pass_returns_zero():
    entry = _entry("a")
    rs = compare({"duration_s": {"value": 10.0, "tolerance_s": 0.5, "gate": "hard"}},
                 {"duration_sec": 10.1})
    assert compute_exit_code([(entry, rs)]) == EXIT_OK


def test_compute_exit_code_soft_warn_still_zero():
    entry = _entry("a")
    rs = compare({"duration_s": {"value": 10.0, "tolerance_s": 0.5, "gate": "soft"}},
                 {"duration_sec": 99.0})
    assert compute_exit_code([(entry, rs)]) == EXIT_OK


def test_compute_exit_code_hard_fail_returns_nonzero():
    entry = _entry("a")
    rs = compare({"duration_s": {"value": 10.0, "tolerance_s": 0.5, "gate": "hard"}},
                 {"duration_sec": 99.0})
    assert compute_exit_code([(entry, rs)]) == EXIT_HARD_FAIL


# --- markdown report --------------------------------------------------------

def test_format_markdown_report_renders_passing_run():
    entry = _entry("alpha")
    rs = compare({"duration_s": {"value": 10.0, "tolerance_s": 0.5, "gate": "hard"}},
                 {"duration_sec": 10.1})
    report = format_markdown_report(
        [(entry, rs)],
        run_iso="2026-06-11T00:00:00+00:00",
        tier_filter="all",
        pipeline_version="abc1234",
        runtime_s=1.5,
    )
    assert "PASS" in report
    assert "alpha" in report
    assert "duration_s" in report
    assert "abc1234" in report


def test_format_markdown_report_renders_errors_section():
    entry = _entry("beta")
    report = format_markdown_report(
        [(entry, [])],
        run_iso="2026-06-11T00:00:00+00:00",
        tier_filter="all",
        errors=[("beta", "audio not found: /tmp/missing.wav")],
    )
    assert "Harness errors" in report
    assert "beta" in report
    assert "audio not found" in report


def test_format_markdown_report_overall_fail_when_hard_fails():
    entry = _entry("gamma")
    rs = compare({"duration_s": {"value": 10.0, "tolerance_s": 0.5, "gate": "hard"}},
                 {"duration_sec": 99.0})
    report = format_markdown_report(
        [(entry, rs)],
        run_iso="2026-06-11T00:00:00+00:00",
        tier_filter="all",
    )
    assert "— FAIL" in report.split("\n", 1)[0]
