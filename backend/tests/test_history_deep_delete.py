"""DELETE /api/history/{id} must purge server-side audio, not just the row.

Compliance contract: when a user deletes an analysis, the uploaded
audio's derivatives (local stem files, R2 objects, layer JSON) go with
it. These tests pin:

  - local stems referenced as raw paths are deleted (whole
    ``toneforge_stems_*`` scratch dir removed);
  - stems wrapped as ``/api/admin/serve-file?path=...`` and local-engine
    ``http://127.0.0.1:7777/api/serve-file?path=...`` URLs are unwrapped
    and deleted;
  - remote https URLs (R2) are NOT treated as local paths;
  - R2 objects are deleted via ``r2_storage.delete_analysis_objects``;
  - the per-song layers directory is removed;
  - paths outside the serve-file allowlist are never touched;
  - deleting a missing id is a 200 no-op (idempotent);
  - DELETE /api/history purges every entry.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402
import tone_forge_api  # noqa: E402
from tone_forge import r2_storage  # noqa: E402


client = TestClient(tone_forge_api.app)

ENTRY_ID = "abc12345"


@pytest.fixture
def storage(tmp_path, monkeypatch):
    """Redirect history + layers to tmp and record R2 deletes."""
    monkeypatch.setattr(tone_forge_api, "_HISTORY_FILE", tmp_path / "history.json")
    monkeypatch.setattr(tone_forge_api, "_LAYERS_ROOT", tmp_path / "layers")
    r2_calls: list[str] = []
    monkeypatch.setattr(
        r2_storage, "delete_analysis_objects", lambda analysis_id: r2_calls.append(analysis_id) or 1
    )
    return {"tmp": tmp_path, "r2_calls": r2_calls}


def _seed_entry(storage, entry_id: str = ENTRY_ID) -> dict:
    """Create an entry with one stem per URL flavour + a layer file."""
    tmp = storage["tmp"]

    stems_dir = tmp / "toneforge_stems_test"
    stems_dir.mkdir()
    raw_stem = stems_dir / "vocals.wav"
    raw_stem.write_bytes(b"RIFFfake")
    sibling = stems_dir / "drums.wav"  # only referenced via raw_stem's dir rmtree
    sibling.write_bytes(b"RIFFfake")

    # Dir names carry a toneforge_ component: the allowlist requires the
    # resolved path to include one (real stems always live in
    # toneforge_* scratch dirs).
    served_stem = tmp / "toneforge_served" / "bass.wav"
    served_stem.parent.mkdir()
    served_stem.write_bytes(b"RIFFfake")

    engine_stem = tmp / "toneforge_engine" / "other.wav"
    engine_stem.parent.mkdir()
    engine_stem.write_bytes(b"RIFFfake")

    layers_dir = tmp / "layers" / entry_id
    layers_dir.mkdir(parents=True)
    (layers_dir / "layer-1.json").write_text("{}")

    entry = {
        "id": entry_id,
        "timestamp": "2026-07-01T12:00:00",
        "name": "test song",
        "result": {
            "stems_paths": {
                "vocals": str(raw_stem),
                "bass": f"/api/admin/serve-file?path={served_stem}",
                "other": f"http://127.0.0.1:7777/api/serve-file?path={engine_stem}",
                "drums_remote": f"https://cdn.example.com/bundles/{entry_id}/stems/drums.m4a",
            }
        },
    }
    (tmp / "history.json").write_text(json.dumps([entry]))
    return {
        "entry": entry,
        "stems_dir": stems_dir,
        "served_stem": served_stem,
        "engine_stem": engine_stem,
        "layers_dir": layers_dir,
    }


def test_delete_purges_local_stems_r2_and_layers(storage):
    seeded = _seed_entry(storage)

    resp = client.delete(f"/api/history/{ENTRY_ID}")

    assert resp.status_code == 200
    assert resp.json() == {"status": "deleted"}
    # Raw path → whole scratch dir removed, including unreferenced sibling.
    assert not seeded["stems_dir"].exists()
    # Wrapped URLs unwrapped and deleted.
    assert not seeded["served_stem"].exists()
    assert not seeded["engine_stem"].exists()
    # Layers gone.
    assert not seeded["layers_dir"].exists()
    # R2 purged for exactly this analysis.
    assert storage["r2_calls"] == [ENTRY_ID]
    # History row gone.
    assert json.loads((storage["tmp"] / "history.json").read_text()) == []


def test_delete_missing_id_is_idempotent(storage):
    _seed_entry(storage)
    assert client.delete(f"/api/history/{ENTRY_ID}").status_code == 200

    resp = client.delete(f"/api/history/{ENTRY_ID}")

    assert resp.status_code == 200
    assert resp.json() == {"status": "deleted"}
    # No second R2 purge for a row that no longer exists.
    assert storage["r2_calls"] == [ENTRY_ID]


def test_delete_never_touches_paths_outside_allowlist(storage, monkeypatch):
    seeded = _seed_entry(storage)
    monkeypatch.setattr(tone_forge_api, "_ALLOWED_FILE_PREFIXES", ["/nonexistent-root/"])

    resp = client.delete(f"/api/history/{ENTRY_ID}")

    assert resp.status_code == 200
    # Entry removed from history, but no file under the (now disallowed)
    # tmp tree was deleted.
    assert seeded["stems_dir"].exists()
    assert seeded["served_stem"].exists()
    assert seeded["engine_stem"].exists()


def test_remote_https_stems_are_not_local_paths(storage):
    """An entry whose stems are all R2 URLs deletes cleanly."""
    tmp = storage["tmp"]
    entry = {
        "id": "remote01",
        "timestamp": "2026-07-01T12:00:00",
        "result": {
            "stems_paths": {
                "vocals": "https://cdn.example.com/bundles/remote01/stems/vocals.m4a",
            }
        },
    }
    (tmp / "history.json").write_text(json.dumps([entry]))

    resp = client.delete("/api/history/remote01")

    assert resp.status_code == 200
    assert storage["r2_calls"] == ["remote01"]


def test_clear_history_purges_every_entry(storage):
    seeded = _seed_entry(storage)
    second = {
        "id": "second01",
        "timestamp": "2026-07-02T12:00:00",
        "result": {"stems_paths": {}},
    }
    history = json.loads((storage["tmp"] / "history.json").read_text())
    history.append(second)
    (storage["tmp"] / "history.json").write_text(json.dumps(history))

    resp = client.delete("/api/history")

    assert resp.status_code == 200
    assert resp.json() == {"status": "cleared"}
    assert not seeded["stems_dir"].exists()
    assert sorted(storage["r2_calls"]) == [ENTRY_ID, "second01"]
    assert json.loads((storage["tmp"] / "history.json").read_text()) == []
