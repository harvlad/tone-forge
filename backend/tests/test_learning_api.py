"""HTTP-level coverage for ``POST /api/learning/correct``.

The domain layer is exercised by ``test_learning_corrections.py``;
this file pins the wire contract:

  1. Happy-path POST returns ``{success: True, ...}`` and persists
     one record to the evidence store.
  2. Unknown ``correction_type`` returns 400 with the allowlist
     message.
  3. Missing ``song_id`` / ``section_id`` returns 400.
  4. The endpoint is JSON-only and rejects malformed bodies via
     FastAPI's standard 422.

The endpoint normally writes to the production evidence root under
``backend/data/evidence/``; tests monkeypatch the domain layer to
route appends into a ``tmp_path`` store so the operator's real
evidence directory stays untouched.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

import tone_forge_api
from bench.evidence.store import EvidenceStore
from bench.learning import CorrectionPayload, record_correction


client = TestClient(tone_forge_api.app)


@pytest.fixture
def tmp_evidence_store(tmp_path, monkeypatch):
    """Redirect the learning endpoint's appends into a tmp store."""

    store = EvidenceStore(root=tmp_path / "evidence")

    def routed(payload: CorrectionPayload):
        return record_correction(payload, store=store)

    monkeypatch.setattr(tone_forge_api, "_record_correction", routed)
    return store


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_post_correction_returns_success_and_persists(tmp_evidence_store):
    payload = {
        "song_id": "abc123",
        "section_id": "abc123:0000",
        "correction_type": "guidance_mode",
        "previous_value": "chord",
        "corrected_value": "riff",
        "user_id": "user-1",
        "note": "this section is a riff loop",
    }
    response = client.post("/api/learning/correct", json=payload)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    assert body["song_id"] == "abc123"
    assert body["section_id"] == "abc123:0000"
    assert body["timestamp_utc"].endswith("Z")

    # Record landed in the store.
    records = list(tmp_evidence_store.iter_records())
    assert len(records) == 1
    rec = records[0]
    assert rec.corrections[0].correction_type == "guidance_mode"
    assert rec.corrections[0].previous_value == "chord"
    assert rec.corrections[0].corrected_value == "riff"
    assert rec.corrections[0].user_id == "user-1"


def test_post_correction_supports_complex_values(tmp_evidence_store):
    payload = {
        "song_id": "abc123",
        "section_id": "abc123:0001",
        "correction_type": "chord_sequence",
        "previous_value": ["C", "G", "Am", "F"],
        "corrected_value": ["C", "G", "Em", "F"],
    }
    response = client.post("/api/learning/correct", json=payload)
    assert response.status_code == 200, response.text
    rec = next(tmp_evidence_store.iter_records())
    assert rec.corrections[0].previous_value == ["C", "G", "Am", "F"]
    assert rec.corrections[0].corrected_value == ["C", "G", "Em", "F"]


def test_post_correction_optional_fields_default(tmp_evidence_store):
    payload = {
        "song_id": "abc123",
        "section_id": "abc123:0002",
        "correction_type": "key",
        "corrected_value": "A minor",
    }
    response = client.post("/api/learning/correct", json=payload)
    assert response.status_code == 200, response.text
    rec = next(tmp_evidence_store.iter_records())
    assert rec.corrections[0].previous_value is None
    assert rec.corrections[0].user_id is None
    assert rec.corrections[0].note is None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_unknown_correction_type_returns_400(tmp_evidence_store):
    payload = {
        "song_id": "abc",
        "section_id": "abc:0000",
        "correction_type": "vibe_meter",
        "corrected_value": 1.0,
    }
    response = client.post("/api/learning/correct", json=payload)
    assert response.status_code == 400
    assert "allowlist" in response.json()["detail"]
    # Nothing persisted on rejection.
    assert tmp_evidence_store.count() == 0


def test_empty_song_id_returns_400(tmp_evidence_store):
    payload = {
        "song_id": "",
        "section_id": "abc:0000",
        "correction_type": "chord",
        "corrected_value": "G",
    }
    response = client.post("/api/learning/correct", json=payload)
    assert response.status_code == 400
    assert "song_id" in response.json()["detail"]
    assert tmp_evidence_store.count() == 0


def test_both_values_null_returns_400(tmp_evidence_store):
    payload = {
        "song_id": "abc",
        "section_id": "abc:0000",
        "correction_type": "chord",
        "previous_value": None,
        "corrected_value": None,
    }
    response = client.post("/api/learning/correct", json=payload)
    assert response.status_code == 400
    assert "non-null" in response.json()["detail"]
    assert tmp_evidence_store.count() == 0


def test_missing_required_field_returns_422(tmp_evidence_store):
    """FastAPI/Pydantic returns 422 on schema violations."""
    response = client.post(
        "/api/learning/correct",
        json={"section_id": "abc:0000", "correction_type": "chord"},
    )
    assert response.status_code == 422
    assert tmp_evidence_store.count() == 0
