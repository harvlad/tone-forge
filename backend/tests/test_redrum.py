"""Re-Drum (performance.redrum) — target kit composites placed at the source
song's hit times. Fixtures fabricate the two prerequisites directly (a hits
table and a rendered-composite cache) so the test pins the recombination,
not the upstream pipelines that already have their own suites.
"""
from __future__ import annotations

import json

import numpy as np
import pytest
import soundfile as sf

from tone_forge.performance import redrum
from tone_forge.performance.drum_kit import DRUM_HITS_RESULT_KEY, HITS_VERSION
from tone_forge.performance.drum_kit_render import RENDER_VERSION

SR = 44100


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("TONEFORGE_DRUMKIT_CACHE", str(tmp_path / "kits"))
    monkeypatch.setenv("TONEFORGE_REDRUM_CACHE", str(tmp_path / "redrum"))
    return tmp_path


def _make_kit(tmp_path, entry_id: str, classes):
    """Fabricate a rendered-composite cache dir for `entry_id` with one
    distinctive constant-value sample per (padIdx, class)."""
    d = tmp_path / "kits" / f"{entry_id}-v{HITS_VERSION}.{RENDER_VERSION}"
    d.mkdir(parents=True)
    files = {}
    for i, (cls, value) in enumerate(classes):
        fname = f"pad{i:02d}_{cls}_v{HITS_VERSION}-{RENDER_VERSION}.wav"
        tone = np.full(int(0.05 * SR), value, dtype=np.float32)
        sf.write(str(d / fname), tone, SR, subtype="FLOAT")
        files[str(i)] = fname
    (d / "files.json").write_text(json.dumps(files))
    return files


def _hits(*specs):
    return {"version": HITS_VERSION,
            "hits": [{"t": t, "end": t + 0.2, "cls": c, "strength": s,
                      "isolation": 1.0} for (t, c, s) in specs]}


def test_places_kit_samples_at_hit_times(env):
    _make_kit(env, "kitsong", [("kick", 0.5), ("snare", 0.25)])
    result = {
        DRUM_HITS_RESULT_KEY: _hits(
            (1.0, "kick", 1.0), (2.0, "snare", 1.0), (3.0, "kick", 0.0)),
        "duration_sec": 5.0,
    }
    path = redrum.render_redrum("src", result, "kitsong")
    assert path is not None
    y, sr = sf.read(str(path))
    assert sr == SR
    # Stereo: this file replaces a stereo demucs stem in the clients' stem
    # players, and a channel-count change on a live mixer bus is a graph
    # reconfiguration iOS restarts the whole engine on (v1 wrote mono).
    assert y.ndim == 2 and y.shape[1] == 2
    assert np.array_equal(y[:, 0], y[:, 1])  # duplicated, not a fake image
    y = y[:, 0]

    def _at(t):
        return float(np.abs(y[int(t * sr) + 10]))

    # Full-strength kick at 1.0 s, snare at 2.0 s, silence between hits.
    assert _at(1.0) > 0.4
    assert _at(2.0) > 0.2
    assert float(np.abs(y[int(0.5 * sr)])) < 1e-6
    # strength 0.0 → velocity floor 0.4, so the 3.0 s kick is quieter but real.
    assert 0.1 < _at(3.0) < _at(1.0)


def test_class_fallback_when_kit_lacks_class(env):
    _make_kit(env, "kitsong", [("kick", 0.5)])  # no hats in the kit
    result = {
        DRUM_HITS_RESULT_KEY: _hits((1.0, "hat_closed", 1.0)),
        "duration_sec": 3.0,
    }
    path = redrum.render_redrum("src2", result, "kitsong")
    y, sr = sf.read(str(path))
    y = y[:, 0]
    # Hat fell through the fallback chain to SOME sample — no dropped hit.
    assert float(np.abs(y[int(1.0 * sr) + 10])) > 0.1


def test_round_robin_variants(env):
    _make_kit(env, "kitsong", [("kick", 0.5), ("kick", 0.9)])
    result = {
        DRUM_HITS_RESULT_KEY: _hits((1.0, "kick", 1.0), (2.0, "kick", 1.0)),
        "duration_sec": 4.0,
    }
    path = redrum.render_redrum("src3", result, "kitsong")
    y, sr = sf.read(str(path))
    y = y[:, 0]
    a = float(np.abs(y[int(1.0 * sr) + 10]))
    b = float(np.abs(y[int(2.0 * sr) + 10]))
    # Two consecutive kicks use the two variants → different levels.
    assert abs(a - b) > 0.2


def test_cache_hit_and_missing_prereqs(env):
    _make_kit(env, "kitsong", [("kick", 0.5)])
    result = {DRUM_HITS_RESULT_KEY: _hits((0.5, "kick", 1.0)),
              "duration_sec": 2.0}
    p1 = redrum.render_redrum("src4", result, "kitsong")
    assert redrum.rendered_path("src4", "kitsong") == p1
    m1 = p1.stat().st_mtime_ns
    assert redrum.render_redrum("src4", result, "kitsong") == p1
    assert p1.stat().st_mtime_ns == m1  # served from cache, not re-rendered

    assert redrum.render_redrum("nohits", {}, "kitsong") is None
    assert redrum.render_redrum("src4", result, "nokit") is None


def test_concurrent_renders_of_same_pair_dont_corrupt(env):
    """The render pool is no longer single-worker, so two jobs can render the
    same (song, kit) pair at once. Every cache write goes to a unique scratch
    name and is renamed into place, so each racer sees a complete file —
    before that, both streamed into one fixed ".part.wav" and the winner
    renamed the interleaved wreckage into the cache."""
    import threading

    _make_kit(env, "kitsong", [("kick", 0.5), ("snare", 0.25)])
    result = {
        DRUM_HITS_RESULT_KEY: _hits((1.0, "kick", 1.0), (2.0, "snare", 1.0)),
        "duration_sec": 5.0,
    }
    results, errors = [], []
    start = threading.Barrier(4)

    def _race():
        try:
            start.wait()
            results.append(redrum.render_redrum("racer", result, "kitsong"))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=_race) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    assert results and all(p is not None for p in results)
    # Every racer names the same cache entry, and it must be a readable,
    # correctly shaped stereo WAV — not a torn one.
    assert len({str(p) for p in results}) == 1
    y, sr = sf.read(str(results[0]))
    assert sr == SR
    assert y.ndim == 2 and y.shape[1] == 2
    assert float(np.abs(y[int(1.0 * sr) + 10, 0])) > 0.1
    # No scratch files left behind.
    assert not list(results[0].parent.glob("*.part.wav"))


def test_kit_candidates_ranking():
    def entry(eid, classes, n, iso=1.0):
        hits = [{"t": i * 0.5, "end": i * 0.5 + 0.2, "cls": classes[i % len(classes)],
                 "strength": 1.0, "isolation": iso} for i in range(n)]
        return {"id": eid, "name": eid,
                "result": {DRUM_HITS_RESULT_KEY: {"version": HITS_VERSION,
                                                  "hits": hits}}}

    full = entry("full", ["kick", "snare", "hat_closed"], 300)
    partial = entry("partial", ["kick"], 300)
    nohits = {"id": "none", "name": "none", "result": {}}
    me = entry("me", ["kick", "snare", "hat_closed"], 300)

    ranked = redrum.kit_candidates([partial, nohits, full, me], exclude_id="me")
    ids = [c["entryId"] for c in ranked]
    assert ids[0] == "full"          # full class coverage wins
    assert "none" not in ids         # no hits table → not suggested
    assert "me" not in ids           # never suggest the song itself
    assert ranked[0]["classes"] == ["hat_closed", "kick", "snare"]
