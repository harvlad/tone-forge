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
    # The strength-0.0 kick at 3.0 s is a ghost/bleed onset — the musical
    # filter drops it (below the strength floor) so Re-Drum plays the
    # backbone, not the wash. Silent here.
    assert _at(3.0) < 1e-6


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


def test_same_class_hits_choke_no_tail_soup(env):
    """A long kick sample placed on fast repeats must be CHOKED by the next
    kick, not ring full-length into overlap. Kit sample is 0.5 s of constant
    0.5; hits every 0.1 s. Without choke the sustained overlap pushes the
    steady-state level far above one sample; with choke each is cut to ~0.1 s
    so the level stays near a single hit."""
    import numpy as np
    import soundfile as sf
    _make_kit(env, "kitsong", [("kick", 0.5)])
    result = {
        DRUM_HITS_RESULT_KEY: _hits(*[(1.0 + i * 0.1, "kick", 1.0) for i in range(8)]),
        "duration_sec": 4.0,
    }
    path = redrum.render_redrum("choketest", result, "kitsong")
    y, sr = sf.read(str(path), always_2d=True)
    # Sample level for one full-gain hit is 0.5. With choke the summed
    # signal in the dense run stays close to that (brief cross-fade overlap
    # only); uncontrolled overlap of 5+ full samples would blow past 1.0.
    dense = np.abs(y[int(1.2 * sr):int(1.7 * sr)]).max()
    assert dense < 0.8, dense


def test_musical_hits_drops_ghosts_and_flams():
    from tone_forge.performance.redrum import _musical_hits
    hits = (
        # strong backbone kicks, well spaced
        [{"t": i * 0.5, "cls": "kick", "strength": 0.8} for i in range(8)]
        # ghosts below the strength floor — must be dropped
        + [{"t": 0.25 + i * 0.5, "cls": "kick", "strength": 0.05} for i in range(8)]
        # a flam 20 ms after a real kick — too close, dropped
        + [{"t": 0.02, "cls": "kick", "strength": 0.7}]
    )
    kept = _musical_hits(hits)
    assert all(h["strength"] >= 0.18 for h in kept)      # no ghosts
    kt = sorted(h["t"] for h in kept if h["cls"] == "kick")
    # every kept pair respects the kick min-gap
    assert all(b - a >= 0.09 - 1e-9 for a, b in zip(kt, kt[1:]))
    assert len(kt) == 8  # the 8 backbone kicks, flam + ghosts gone
