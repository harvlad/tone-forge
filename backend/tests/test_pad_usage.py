"""Tests for the pad usage feedback loop (store + ranking fold)."""

import pytest

from tone_forge import pad_usage


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("TONEFORGE_PAD_USAGE", str(tmp_path / "usage"))


def test_record_and_load_round_trip():
    accepted = pad_usage.record("song1", [
        {"assetId": "a1", "kind": "play"},
        {"assetId": "a1", "kind": "play"},
        {"assetId": "a2", "kind": "skip"},
        {"assetId": "", "kind": "play"},        # rejected
        {"assetId": "a3", "kind": "banana"},    # rejected
        "not-a-dict",                            # rejected
    ])
    assert accepted == 3
    usage = pad_usage.load("song1")
    assert usage["a1"]["play"] == 2
    assert usage["a2"]["skip"] == 1
    assert "a3" not in usage


def test_digest_changes_with_usage():
    d0 = pad_usage.digest(pad_usage.load("song2"))
    pad_usage.record("song2", [{"assetId": "x", "kind": "play"}])
    d1 = pad_usage.digest(pad_usage.load("song2"))
    assert d0 != d1
    # Stable for identical content.
    assert d1 == pad_usage.digest(pad_usage.load("song2"))


def test_usage_fold_reorders_kit_ranking():
    """Two equal-scored assets: skips demote, plays promote."""
    from tone_forge.performance.kit_builder import AutoKitBuilder

    class _Pos:
        start_s = 10.0
        end_s = 18.0

    class _Asset:
        def __init__(self, aid):
            self.id = aid
            self.stem = "guitar"
            self.content_type = type("CT", (), {"value": "rhythm_loop"})()
            self.performance_score = 0.6
            self.loop_confidence = 0.8
            self.difficulty = 0.4
            self.loopable = True
            self.pattern_id = None
            self.source_id = None
            self.pos = _Pos()
            self.color_hint = "#123456"

    builder = AutoKitBuilder()
    a, b = _Asset("liked"), _Asset("hated")

    # Prime the score closure via build()'s internals: call build on a
    # minimal fake graph.
    class _Graph:
        graph_hash = "h"
        module_version = "t"
        song_id = "s"
        loops = ()
        assets = (a, b)

        def ranked_assets(self):
            return [a, b]

    usage = {"liked": {"play": 10, "skip": 0},
             "hated": {"play": 0, "skip": 10}}
    kit = builder.build(_Graph(), pads=2, usage=usage)
    ids = [p["assetId"] for p in kit["pads"]]
    assert ids[0] == "liked"
    # provenance carries the usage digest (cache bust)
    assert "use=" in kit["provenance"]
    assert "use=0" not in kit["provenance"]
