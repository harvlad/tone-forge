"""``GET /api/session/{entry_id}`` route coverage.

This is the new Jam-shaped entry point. It composes the session/
subsystem (``build`` + ``serialize``) with the legacy history store.

Tests pin three things:
  1. 404 when the entry doesn't exist.
  2. 422 when the entry exists but never persisted its analysis result
     (history rows from before full_result was carried).
  3. 200 + correctly-shaped payload for a real entry — wire format
     matches what jam.js will consume (P5e).

The history store is monkey-patched at the module level so the test
doesn't depend on the on-disk ``history.json``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

import tone_forge_api
from tone_forge_api import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _history_entry() -> dict:
    """A history row with a realistic ``result`` payload."""
    return {
        "id": "sess-real",
        "filename": "song.wav",
        "result": {
            "source_name": "Test Song",
            "source_url": "https://youtube.com/watch?v=xyz",
            "duration_sec": 180.0,
            "sample_rate": 44100,
            "content_hash": "hash-xyz",
            "wav_path": "/tmp/cache/hash-xyz.wav",
            "detected_type": "guitar",
            "stems": {
                "drums": "/api/admin/serve-file?path=drums.wav",
                "bass": "/api/admin/serve-file?path=bass.wav",
            },
            "sections": [
                {"start_s": 0.0, "end_s": 16.0, "label": "intro"},
            ],
            "chords": [
                {"start_s": 0.0, "end_s": 4.0, "symbol": "G"},
            ],
            "descriptor": {"tempo": 120.0, "key": "G major"},
            "midi": {
                "notes": [{"pitch": 67, "start": 0.0, "end": 0.25}],
                "overall_confidence": 0.6,
            },
        },
    }


def _no_result_entry() -> dict:
    """A history row that lacks the full ``result`` blob."""
    return {"id": "sess-stub", "filename": "x.wav"}


@pytest.fixture(autouse=True)
def _stub_history(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the history lookup so tests don't touch disk."""

    rows = {
        "sess-real": _history_entry(),
        "sess-stub": _no_result_entry(),
    }

    def fake_get(entry_id: str):
        return rows.get(entry_id)

    monkeypatch.setattr(tone_forge_api, "_get_history_item", fake_get)


# ---------------------------------------------------------------------------
# Route behavior
# ---------------------------------------------------------------------------

def test_session_route_returns_404_for_missing_entry() -> None:
    resp = client.get("/api/session/does-not-exist")
    assert resp.status_code == 404


def test_session_route_returns_422_when_result_missing() -> None:
    """History row exists but has no ``result`` — Jam can't render it."""
    resp = client.get("/api/session/sess-stub")
    assert resp.status_code == 422


def test_session_route_returns_bundle_payload() -> None:
    resp = client.get("/api/session/sess-real")
    assert resp.status_code == 200
    body = resp.json()

    # Top-level SessionBundle keys.
    assert body["session_id"] == "sess-real"
    assert set(body) >= {
        "session_id", "audio", "stems", "understanding",
        "user_role", "user_midi", "tone", "guidance",
        "device_caps", "initial_transport",
    }


def test_session_route_payload_enums_are_strings() -> None:
    """``serialize`` must unwrap str-Enums to their .value so jam.js
    receives plain JSON strings, not ``{"_name_": ...}`` Enum reprs."""
    resp = client.get("/api/session/sess-real")
    body = resp.json()
    assert body["user_role"] == "guitar"
    assert body["tone"]["tier"] == "unknown"
    assert body["device_caps"]["cls"] == "interface_only"


def test_session_route_audio_block_matches_legacy_fields() -> None:
    resp = client.get("/api/session/sess-real")
    audio = resp.json()["audio"]
    assert audio["source_kind"] == "url"
    assert audio["source_uri"] == "https://youtube.com/watch?v=xyz"
    assert audio["source_title"] == "Test Song"
    assert audio["duration_s"] == pytest.approx(180.0)
    assert audio["content_hash"] == "hash-xyz"


def test_session_route_stems_block_carries_urls() -> None:
    resp = client.get("/api/session/sess-real")
    stems = resp.json()["stems"]
    assert stems["drums"]["audio_url"].endswith("drums.wav")
    assert stems["bass"]["audio_url"].endswith("bass.wav")
    assert stems["vocals"] is None  # not in fixture


def test_session_route_understanding_carries_tempo_and_chords() -> None:
    resp = client.get("/api/session/sess-real")
    u = resp.json()["understanding"]
    assert u["tempo_bpm"] == pytest.approx(120.0)
    assert u["key"] == "G major"
    assert len(u["sections"]) == 1
    assert u["sections"][0]["label"] == "intro"
    assert len(u["chords"]) == 1
    assert u["chords"][0]["symbol"] == "G"


def test_session_route_initial_transport_is_stopped_muted() -> None:
    resp = client.get("/api/session/sess-real")
    t = resp.json()["initial_transport"]
    assert t["playing"] is False
    assert t["user_mute"] is True
    assert t["monitor_gain"] == 0.0


def test_session_route_unknown_tier_carries_fallback_chain() -> None:
    """No preset_matches in the fixture → tone.retrieve() returns
    UNKNOWN with a fallback chain id picked by the policy from the
    fixture's tempo/key (120 BPM + 'G major' → clean_strat).
    Pins the P6 wiring: the route runs the tone retrieval at the
    composition edge."""
    resp = client.get("/api/session/sess-real")
    tone = resp.json()["tone"]
    assert tone["tier"] == "unknown"
    assert tone["fallback_chain_id"] == "tfc.clean_strat"
    assert tone["chosen"] is None


def test_session_route_promotes_tier_when_preset_match_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A history row with a usable preset_matches blob must surface as
    a tier-promoted ToneMatch (HIGH or MEDIUM — never UNKNOWN once a
    candidate is present). Pre-calibration cap keeps HIGH unreachable,
    so a close single-candidate match lands at MEDIUM."""
    entry = _history_entry()
    entry["result"]["preset_matches"] = {
        "guitar": {
            "preset_id": "p1",
            "preset_name": "Analog Lead",
            "instrument": "Analog",
            "distance": 0.1,
        }
    }
    monkeypatch.setattr(
        tone_forge_api, "_get_history_item", lambda _: entry,
    )

    resp = client.get("/api/session/sess-real")
    tone = resp.json()["tone"]
    assert tone["tier"] in ("medium", "high")
    assert tone["chosen"] is not None
    assert tone["chosen"]["preset_id"] == "p1"
    assert tone["fallback_chain_id"] is None


def test_session_route_low_tier_when_match_is_weak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A far-distance preset_match collapses confidence below the
    MEDIUM threshold; the route must return LOW (chosen None, fallback
    set) so the UI takes the curated-chain path."""
    entry = _history_entry()
    entry["result"]["preset_matches"] = {
        "guitar": {
            "preset_id": "weak",
            "preset_name": "Far Match",
            "instrument": "Analog",
            "distance": 12.0,
        }
    }
    monkeypatch.setattr(
        tone_forge_api, "_get_history_item", lambda _: entry,
    )

    resp = client.get("/api/session/sess-real")
    tone = resp.json()["tone"]
    assert tone["tier"] == "low"
    assert tone["chosen"] is None
    assert tone["fallback_chain_id"] == "tfc.clean_strat"


# ---------------------------------------------------------------------------
# Serialization unit coverage
# ---------------------------------------------------------------------------

def test_serialize_handles_nested_enums() -> None:
    """Direct unit test for the JSON serializer — protects against
    accidental re-introduction of Enum instances in nested fields."""
    from tone_forge.session import build, serialize

    bundle = build(_history_entry()["result"], session_id="x")
    payload = serialize(bundle)

    # Enums should be plain strings at every nesting level we render.
    assert isinstance(payload["user_role"], str)
    assert isinstance(payload["tone"]["tier"], str)
    assert isinstance(payload["device_caps"]["cls"], str)
    # Tuples become lists for JSON.
    assert isinstance(payload["understanding"]["sections"], list)
    assert isinstance(payload["understanding"]["chords"], list)
