"""7-day retention: analyses older than the window are purged server-side.

Pins:
  - entries older than _RETENTION_DAYS are dropped + deep-deleted;
  - fresh entries survive;
  - unparseable/missing timestamps are treated as expired (compliance
    default: delete when we can't prove freshness);
  - the purge is idempotent;
  - entries falling off the 100-row history cap are deep-deleted too.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tone_forge_api  # noqa: E402


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(tone_forge_api, "_HISTORY_FILE", tmp_path / "history.json")
    monkeypatch.setattr(tone_forge_api, "_LAYERS_ROOT", tmp_path / "layers")
    deleted: list[str] = []
    monkeypatch.setattr(
        tone_forge_api, "_deep_delete_entry", lambda entry: deleted.append(entry.get("id"))
    )
    return {"tmp": tmp_path, "deleted": deleted}


def _write_history(tmp: Path, entries: list[dict]) -> None:
    (tmp / "history.json").write_text(json.dumps(entries))


def _entry(entry_id: str, age_days: float | None) -> dict:
    stamp = (
        (datetime.now() - timedelta(days=age_days)).isoformat()
        if age_days is not None
        else "not-a-timestamp"
    )
    return {"id": entry_id, "timestamp": stamp, "result": {}}


def test_purge_drops_expired_and_malformed_keeps_fresh(storage):
    _write_history(
        storage["tmp"],
        [_entry("old00001", 8), _entry("fresh001", 6), _entry("badstamp", None)],
    )

    count = tone_forge_api._purge_expired_history()

    assert count == 2
    assert sorted(storage["deleted"]) == ["badstamp", "old00001"]
    remaining = json.loads((storage["tmp"] / "history.json").read_text())
    assert [e["id"] for e in remaining] == ["fresh001"]


def test_purge_is_idempotent(storage):
    _write_history(storage["tmp"], [_entry("old00001", 8), _entry("fresh001", 1)])

    assert tone_forge_api._purge_expired_history() == 1
    assert tone_forge_api._purge_expired_history() == 0
    assert storage["deleted"] == ["old00001"]


def test_purge_respects_configured_window(storage, monkeypatch):
    monkeypatch.setattr(tone_forge_api, "_RETENTION_DAYS", 1.0)
    _write_history(storage["tmp"], [_entry("twodays1", 2), _entry("hours001", 0.5)])

    assert tone_forge_api._purge_expired_history() == 1
    assert storage["deleted"] == ["twodays1"]


def test_history_cap_overflow_is_deep_deleted(storage):
    entries = [_entry(f"id{i:06d}", 0.1) for i in range(100)]
    _write_history(storage["tmp"], entries)

    tone_forge_api._add_to_history({"name": "the 101st"})

    history = json.loads((storage["tmp"] / "history.json").read_text())
    assert len(history) == 100
    assert history[0]["name"] == "the 101st"
    # The oldest entry (last in the list) fell off the cap and was purged.
    assert storage["deleted"] == ["id000099"]
