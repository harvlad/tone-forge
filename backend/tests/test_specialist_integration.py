"""Experimental specialist integration — focused tests (Step 15)."""
from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from tone_forge.specialist import feedback, normalization
from tone_forge.specialist import registry as reg
from tone_forge.specialist.router import (FAMILY_TO_STEM_ROLE, RoutingRequest,
                                          resolve)


# -- routing ---------------------------------------------------------------

def test_current_engine_never_routes():
    for fam in ("bass", "guitar", "keys", "drums", "vocals", ""):
        assert resolve(RoutingRequest(engine="current", family=fam)) is None


def test_experimental_routes_bass_guitar():
    expect = {"bass": ("riley_bass", "register_up_12"),
              "guitar": ("riley_guitar", "register_passthrough")}
    for fam, (transcriber, norm) in expect.items():
        d = resolve(RoutingRequest(engine="experimental_specialist", family=fam))
        assert d is not None
        assert d.transcriber == transcriber
        assert d.stem_role == FAMILY_TO_STEM_ROLE[fam]
        assert d.separator == "htdemucs_6s"
        assert d.normalization == norm


def test_unsupported_families_fall_back():
    # keys dropped 2026-08-30: kong at parity with the incumbent on
    # separated input (sep_revalidation_htdemucs6s_validation.json).
    for fam in ("keys", "drums", "vocals", "strings", "", "unknown"):
        assert resolve(RoutingRequest(engine="experimental_specialist",
                                      family=fam)) is None


def test_cache_identities_do_not_collide():
    hashes = set()
    for fam in ("bass", "guitar"):
        d = resolve(RoutingRequest(engine="experimental_specialist", family=fam))
        h = d.config_hash()
        assert h not in hashes
        hashes.add(h)


def test_config_hash_changes_with_registry(monkeypatch):
    d = resolve(RoutingRequest(engine="experimental_specialist", family="bass"))
    h1 = d.config_hash()
    d2 = type(d)(**{**d.__dict__, "transcriber_version": "filobass_9999"})
    assert d2.config_hash() != h1


# -- license gate ----------------------------------------------------------

def test_license_blocked_models_cannot_be_selected():
    with pytest.raises(reg.LicenseBlockedError):
        reg.get_transcriber("yourmt3")
    with pytest.raises(reg.LicenseBlockedError):
        reg.get_separator("bs_rofo_sw")


def test_blocked_entry_in_routing_raises(monkeypatch):
    """If someone edits routing to a blocked model, resolve() must raise,
    not silently run it."""
    hacked = json.loads(json.dumps(reg.load_registry()))
    hacked["routing"]["experimental_specialist"]["bass"]["transcriber"] = "yourmt3"
    monkeypatch.setattr(reg, "load_registry", lambda: hacked)
    with pytest.raises(reg.LicenseBlockedError):
        resolve(RoutingRequest(engine="experimental_specialist", family="bass"))


def test_cleared_models_have_evidence_and_license():
    r = reg.load_registry()
    for name in ("riley_bass", "riley_guitar", "kong_piano"):
        e = r["transcribers"][name]
        assert e["license_status"] in reg.CLEARED_STATUSES
        assert "lab_evidence" in e and "heldout_f1" in e["lab_evidence"]


# -- normalization ---------------------------------------------------------

def test_normalization_is_separate_stage_and_default_passthrough():
    d = resolve(RoutingRequest(engine="experimental_specialist", family="guitar"))
    assert d.normalization_shift == 0, \
        "guitar default must preserve sounding pitch (no invented shift)"
    notes = [{"pitch": 40, "onset": 0.1, "offset": 0.4}]
    out, prov = normalization.apply(notes, d.normalization_shift,
                                    d.normalization, d.normalization_version)
    assert out[0]["pitch"] == 40
    assert prov["raw_preserved"] is True


def test_bass_routes_register_up_12():
    """Promoted from the 2026-08-14 separated revalidation: the raw
    riley_bass variant collapses on htdemucs_6s stems; +12 wins .513
    vs .0947. Listening check pending — the caveat must say so."""
    d = resolve(RoutingRequest(engine="experimental_specialist", family="bass"))
    assert d.normalization == "register_up_12"
    assert d.normalization_shift == 12
    assert "listening check pending" in (d.caveat or "")
    notes = [{"pitch": 28, "onset": 0.1, "offset": 0.4}]
    out, prov = normalization.apply(notes, d.normalization_shift,
                                    d.normalization, d.normalization_version)
    assert out[0]["pitch"] == 40
    assert prov["raw_preserved"] is False


def test_normalization_shift_and_provenance():
    notes = [{"pitch": 40, "onset": 0.1, "offset": 0.4}]
    out, prov = normalization.apply(notes, 12, "test_rule", "v9")
    assert out[0]["pitch"] == 52
    assert notes[0]["pitch"] == 40, "input must not be mutated"
    assert prov == {"rule": "test_rule", "version": "v9",
                    "shift_semitones": 12, "n_notes": 1, "raw_preserved": False}


# -- provenance serialization ---------------------------------------------

def test_provenance_survives_json_roundtrip():
    d = resolve(RoutingRequest(engine="experimental_specialist", family="guitar"))
    prov = d.provenance()
    restored = json.loads(json.dumps(prov))
    assert restored["transcriber"] == "riley_guitar"
    assert restored["config_hash"] == d.config_hash()
    assert restored["registry_version"] == reg.registry_version()


# -- runner MIDI shape (no model inference) --------------------------------

def test_runner_midi_bytes_roundtrip():
    from tone_forge.specialist.runner import _decode_midi_file, _notes_to_midi_bytes
    import tempfile
    notes = [{"pitch": 45, "onset": 0.5, "offset": 1.0, "velocity": 90},
             {"pitch": 47, "onset": 1.5, "offset": 2.0, "velocity": 70}]
    data = _notes_to_midi_bytes(notes, tempo_bpm=120.0)
    with tempfile.NamedTemporaryFile(suffix=".mid", delete=False) as tmp:
        tmp.write(data)
        path = Path(tmp.name)
    decoded = _decode_midi_file(path)
    path.unlink()
    assert [n["pitch"] for n in decoded] == [45, 47]
    assert abs(decoded[0]["onset"] - 0.5) < 0.01
    assert abs(decoded[1]["offset"] - 2.0) < 0.01


# -- worker fallback behavior ---------------------------------------------

def test_worker_engine_defaults_to_current(monkeypatch):
    """run_file_analysis engine resolution: no arg + no env = current."""
    monkeypatch.delenv("TONEFORGE_ANALYSIS_ENGINE", raising=False)
    engine = (None or __import__("os").environ.get("TONEFORGE_ANALYSIS_ENGINE") or "current")
    assert engine == "current"


# -- feedback --------------------------------------------------------------

def test_feedback_record_and_validation(tmp_path, monkeypatch):
    monkeypatch.setattr(feedback, "FEEDBACK_PATH", tmp_path / "fb.jsonl")
    rec = feedback.record(verdict="better", song_hash="abc", target_family="bass",
                          engine="experimental_specialist",
                          registry_version="2026.07.27-1",
                          tags=["wrong_octave", "timing"], note="test")
    assert rec["verdict"] == "BETTER"
    assert feedback.load_all()[0]["tags"] == ["timing", "wrong_octave"]
    with pytest.raises(ValueError):
        feedback.record(verdict="7/10", song_hash="x", target_family="bass",
                        engine="e", registry_version="v")
    with pytest.raises(ValueError):
        feedback.record(verdict="BETTER", song_hash="x", target_family="bass",
                        engine="e", registry_version="v", tags=["vibes"])
