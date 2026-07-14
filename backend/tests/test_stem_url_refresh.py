"""Presign-on-read hardening (A3' non-breaking slice).

Pins two behaviours added together:

1. ``_refresh_r2_stem_urls`` — every read choke point (history /
   session / bundle / chops) re-presigns stored R2 stem URLs so the
   7-day presign cap can never serve a dead link. Foreign URLs and
   local paths must pass through untouched.

2. ``_add_to_history`` ids are full-entropy (32 hex chars). Ids appear
   in shareable /jam/{id} URLs and are the only access gate today, so
   the old 8-char truncation (~32 bits) was enumerable.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tone_forge_api  # noqa: E402
from tone_forge import r2_storage  # noqa: E402


STALE = (
    "https://acct.r2.cloudflarestorage.com/tone-forge-stems/"
    "bundles/abc/stems/drums.m4a?X-Amz-Signature=stale"
)
FRESH = (
    "https://acct.r2.cloudflarestorage.com/tone-forge-stems/"
    "bundles/abc/stems/drums.m4a?X-Amz-Signature=fresh"
)


class TestRefreshR2StemUrls:
    def test_refreshes_our_urls_in_place(self, monkeypatch):
        monkeypatch.setattr(
            r2_storage, "refresh_url",
            lambda url, ttl_sec=None: FRESH if url == STALE else url,
        )
        result = {"stems_paths": {"drums": STALE, "bass": "/tmp/bass.wav"}}
        tone_forge_api._refresh_r2_stem_urls(result)
        assert result["stems_paths"]["drums"] == FRESH
        # Local paths never even reach refresh_url (not https).
        assert result["stems_paths"]["bass"] == "/tmp/bass.wav"

    def test_leaves_local_engine_and_api_urls_alone(self, monkeypatch):
        called = []
        monkeypatch.setattr(
            r2_storage, "refresh_url",
            lambda url, ttl_sec=None: called.append(url) or url,
        )
        result = {"stems_paths": {
            "drums": "http://127.0.0.1:7777/api/serve-file?path=/tmp/d.wav",
            "bass": "/api/admin/serve-file?path=/tmp/b.wav",
            "vocals": None,
            "other": 42,
        }}
        before = dict(result["stems_paths"])
        tone_forge_api._refresh_r2_stem_urls(result)
        assert result["stems_paths"] == before
        assert called == []  # only https URLs are candidates

    def test_tolerates_missing_or_bad_shapes(self):
        # Must never raise — these run on every read request.
        tone_forge_api._refresh_r2_stem_urls(None)
        tone_forge_api._refresh_r2_stem_urls({})
        tone_forge_api._refresh_r2_stem_urls({"stems_paths": None})
        tone_forge_api._refresh_r2_stem_urls({"stems_paths": []})
        tone_forge_api._refresh_r2_stem_urls({"stems_paths": {}})

    def test_refresh_failure_keeps_original(self, monkeypatch):
        def boom(url, ttl_sec=None):
            raise RuntimeError("presign exploded")

        monkeypatch.setattr(r2_storage, "refresh_url", boom)
        result = {"stems_paths": {"drums": STALE}}
        tone_forge_api._refresh_r2_stem_urls(result)
        assert result["stems_paths"]["drums"] == STALE


class TestHistoryIdEntropy:
    def test_new_ids_are_full_uuid_hex(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            tone_forge_api, "_HISTORY_FILE", tmp_path / "history.json"
        )
        entry = tone_forge_api._add_to_history({"filename": "x.wav"})
        assert len(entry["id"]) == 32
        int(entry["id"], 16)  # pure hex

    def test_short_legacy_ids_still_resolve(self, tmp_path, monkeypatch):
        import json

        monkeypatch.setattr(
            tone_forge_api, "_HISTORY_FILE", tmp_path / "history.json"
        )
        (tmp_path / "history.json").write_text(
            json.dumps([{"id": "abc12345", "timestamp": "2026-01-01T00:00:00"}])
        )
        found = tone_forge_api._get_history_item("abc12345")
        assert found is not None and found["id"] == "abc12345"
