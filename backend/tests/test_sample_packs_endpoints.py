"""HTTP-level coverage for the /api/sample-packs routes.

Pins the contract the mobile ``PackClient`` + Browse Packs sheet rely
on:

  - Catalog passes new fields (genres, moods, previewUrl) through
    verbatim — the endpoint is a dumb JSON relay, so a richer
    catalog.json needs no code change.
  - Missing catalog file = empty list, not an error.
  - Manifest 200 for a real pack, 404 for unknown.
  - Pad file served with the right media type; 404 when missing;
    traversal filenames rejected with 400.
  - Cover/preview endpoints serve ``cover.jpg`` / ``preview.m4a``
    from the pack dir, 404 when the asset is absent, and reject
    traversal pack ids with 400.

Fixtures monkeypatch ``_SAMPLES_ROOT`` to a per-test tmp dir so the
tests never depend on the packs shipped in static/samples/.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402
import tone_forge_api  # noqa: E402


client = TestClient(tone_forge_api.app)


@pytest.fixture
def samples_root(tmp_path, monkeypatch):
    """Redirect the samples root to a per-test tmp dir."""
    root = tmp_path / "samples"
    root.mkdir()
    monkeypatch.setattr(tone_forge_api, "_SAMPLES_ROOT", root)
    return root


def _make_pack(root: Path, pack_id: str = "test-pack") -> Path:
    pack_dir = root / pack_id
    pads_dir = pack_dir / "pads"
    pads_dir.mkdir(parents=True)
    manifest = {
        "packId": pack_id,
        "name": "Test Pack",
        "family": "percussion",
        "paletteHint": None,
        "pads": [
            {
                "padIdx": 0,
                "name": "Kick",
                "family": "percussion",
                "filename": "00_kick.m4a",
            }
        ],
    }
    (pack_dir / "manifest.json").write_text(json.dumps(manifest))
    (pads_dir / "00_kick.m4a").write_bytes(b"\x00" * 64)
    return pack_dir


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


def test_catalog_passes_new_fields_through(samples_root):
    catalog = {
        "packs": [
            {
                "packId": "rich",
                "name": "Rich Pack",
                "family": "pads",
                "tags": ["ambient"],
                "genres": ["shoegaze", "dream pop"],
                "moods": ["dreamy", "hazy"],
                "coverUrl": "/api/sample-packs/rich/cover",
                "previewUrl": "/api/sample-packs/rich/preview",
                "padCount": 8,
            }
        ]
    }
    (samples_root / "catalog.json").write_text(json.dumps(catalog))

    resp = client.get("/api/sample-packs")
    assert resp.status_code == 200
    packs = resp.json()["packs"]
    assert len(packs) == 1
    assert packs[0]["genres"] == ["shoegaze", "dream pop"]
    assert packs[0]["moods"] == ["dreamy", "hazy"]
    assert packs[0]["previewUrl"] == "/api/sample-packs/rich/preview"


def test_catalog_missing_file_returns_empty_list(samples_root):
    resp = client.get("/api/sample-packs")
    assert resp.status_code == 200
    assert resp.json() == {"packs": []}


def test_shipped_catalog_declares_genres_and_moods():
    """The real static/samples/catalog.json carries the Phase 10
    fields for every pack."""
    catalog_path = (
        Path(tone_forge_api.__file__).resolve().parent
        / "static"
        / "samples"
        / "catalog.json"
    )
    data = json.loads(catalog_path.read_text())
    assert data["packs"], "shipped catalog should not be empty"
    for pack in data["packs"]:
        assert isinstance(pack.get("genres"), list), pack["packId"]
        assert isinstance(pack.get("moods"), list), pack["packId"]
        assert "previewUrl" in pack, pack["packId"]


# ---------------------------------------------------------------------------
# Manifest + pads
# ---------------------------------------------------------------------------


def test_manifest_roundtrip_and_unknown_pack(samples_root):
    _make_pack(samples_root)

    ok = client.get("/api/sample-packs/test-pack")
    assert ok.status_code == 200
    assert ok.json()["packId"] == "test-pack"

    missing = client.get("/api/sample-packs/nope")
    assert missing.status_code == 404


def test_pad_serving_and_traversal_rejection(samples_root):
    _make_pack(samples_root)

    ok = client.get("/api/sample-packs/test-pack/pads/00_kick.m4a")
    assert ok.status_code == 200
    assert ok.headers["content-type"] == "audio/mp4"

    missing = client.get("/api/sample-packs/test-pack/pads/99_none.m4a")
    assert missing.status_code == 404

    # Explicit ".." guard fires even without a slash in the segment.
    dotdot = client.get("/api/sample-packs/test-pack/pads/..secret")
    assert dotdot.status_code == 400

    # Encoded slash: the router refuses to match the segment before
    # the handler's own guard runs — rejected either way, never served.
    traversal = client.get(
        "/api/sample-packs/test-pack/pads/..%2Fmanifest.json"
    )
    assert traversal.status_code in (400, 404)


# ---------------------------------------------------------------------------
# Cover + preview
# ---------------------------------------------------------------------------


def test_cover_served_when_present(samples_root):
    pack_dir = _make_pack(samples_root)
    (pack_dir / "cover.jpg").write_bytes(b"\xff\xd8\xff\xe0jpegish")

    resp = client.get("/api/sample-packs/test-pack/cover")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"


def test_cover_404_when_absent(samples_root):
    _make_pack(samples_root)
    resp = client.get("/api/sample-packs/test-pack/cover")
    assert resp.status_code == 404


def test_preview_served_when_present(samples_root):
    pack_dir = _make_pack(samples_root)
    (pack_dir / "preview.m4a").write_bytes(b"\x00" * 128)

    resp = client.get("/api/sample-packs/test-pack/preview")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "audio/mp4"


def test_preview_404_when_absent(samples_root):
    _make_pack(samples_root)
    resp = client.get("/api/sample-packs/test-pack/preview")
    assert resp.status_code == 404


def test_cover_rejects_traversal_pack_id(samples_root):
    # Explicit ".." guard in _resolve_pack_dir.
    dotdot = client.get("/api/sample-packs/..evil/cover")
    assert dotdot.status_code == 400

    # Encoded slashes: router-level rejection before the guard —
    # rejected either way, never served.
    encoded = client.get("/api/sample-packs/..%2F..%2Fetc/cover")
    assert encoded.status_code in (400, 404)


def test_preview_404_for_unknown_pack(samples_root):
    resp = client.get("/api/sample-packs/ghost/preview")
    assert resp.status_code == 404
