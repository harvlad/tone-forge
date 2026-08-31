"""Tests for the performance-graph backfill (serve.ensure_graph)."""

from pathlib import Path

import pytest

from tone_forge.performance import serve
from tone_forge.stem_fetch import materialize_stems


class _FakeGraph:
    def __init__(self, assets):
        self.assets = assets
        self.phrases = ()
        self.loops = ()

    def to_dict(self):
        return {"assets": list(self.assets)}


def test_ensure_graph_prefers_stored(monkeypatch):
    result = {serve.GRAPH_RESULT_KEY: {"assets": [{"id": "a1"}]}}
    sentinel = _FakeGraph(["a1"])
    monkeypatch.setattr(
        "tone_forge.performance.builder._graph_from_dict", lambda d: sentinel
    )

    g, attached = serve.ensure_graph("e1", result)

    assert g is sentinel
    assert attached is False


def test_ensure_graph_backfills_from_fetched_stems(monkeypatch, tmp_path):
    stem = tmp_path / "drums.wav"
    stem.write_bytes(b"fake")
    result = {"stems_paths": {"drums": "https://r2.example/drums"}}

    monkeypatch.setattr(
        "tone_forge.stem_fetch.materialize_stems",
        lambda res, scratch, roles=None: {"drums": stem},
    )

    calls = {}

    class _FakeBuilder:
        def build(self, result, song_id, content_hash, use_cache=True):
            calls["use_cache"] = use_cache
            calls["stems_local"] = result.get("stems_local")
            return _FakeGraph(["asset1"])

    monkeypatch.setattr(serve, "_get_builder", lambda: _FakeBuilder())

    g, attached = serve.ensure_graph("e1", result)

    assert attached is True
    assert len(g.assets) == 1
    # bypasses the (possibly empty-poisoned) graph cache
    assert calls["use_cache"] is False
    # fetched temp stems were handed to the builder as local paths
    assert calls["stems_local"] == {"drums": str(stem)}
    # graph persisted into the result for the caller to save
    assert result[serve.GRAPH_RESULT_KEY] == {"assets": ["asset1"]}


def test_ensure_graph_no_sources_returns_unattached(monkeypatch):
    result = {}
    monkeypatch.setattr(
        "tone_forge.stem_fetch.materialize_stems",
        lambda res, scratch, roles=None: {},
    )

    class _EmptyBuilder:
        def build(self, result, song_id, content_hash, use_cache=True):
            return _FakeGraph([])

    monkeypatch.setattr(serve, "_get_builder", lambda: _EmptyBuilder())

    g, attached = serve.ensure_graph("e1", result)

    assert attached is False
    assert serve.GRAPH_RESULT_KEY not in result


def test_materialize_stems_prefers_local(tmp_path):
    stem = tmp_path / "bass.wav"
    stem.write_bytes(b"x")
    result = {
        "stems_local": {"bass": str(stem)},
        "stems_paths": {"bass": "https://r2.example/bass"},
    }

    out = materialize_stems(result, tmp_path)

    assert out == {"bass": stem}  # no fetch attempted
