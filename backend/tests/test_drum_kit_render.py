"""Median-stack renderer (performance.drum_kit_render) — the cleanup claim
itself is the test: every synthetic kick instance carries its own
uncorrelated noise burst ("bleed"), and the rendered composite must match
the clean kick template BETTER than any raw slice does, because the median
cancels what doesn't repeat.
"""
from __future__ import annotations

import numpy as np
import pytest

from tone_forge.performance import drum_kit as dk
from tone_forge.performance import drum_kit_render as dkr

SR = 44100
BEAT = 0.5  # 120 BPM


def _kick_template(sr=SR):
    n = int(0.30 * sr)
    t = np.arange(n) / sr
    return np.sin(2 * np.pi * 55.0 * t) * np.exp(-t / 0.08)


@pytest.fixture()
def noisy_song(tmp_path, monkeypatch):
    """16 kicks + off-beat hats; every kick smeared with its OWN noise burst
    (uncorrelated hit-to-hit — the thing median stacking exists to cancel)."""
    import soundfile as sf
    from scipy import signal as sig

    monkeypatch.setenv("TONEFORGE_DRUMKIT_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("TONEFORGE_STEM_CACHE", "0")

    rng = np.random.default_rng(7)
    dur = 16 * BEAT + 1.5
    y = np.zeros(int(dur * SR))
    kick = _kick_template()

    n_h = int(0.08 * SR)
    sos_h = sig.butter(4, [6000 / (SR / 2), 10000 / (SR / 2)],
                       btype="band", output="sos")
    hat = sig.sosfilt(sos_h, rng.standard_normal(n_h)) \
        * np.exp(-np.arange(n_h) / SR / 0.015)
    hat *= 0.4 / (np.abs(hat).max() + 1e-9)

    kick_times = [0.5 + i * BEAT for i in range(16)]
    for t in kick_times:
        i = int(t * SR)
        # Heavy per-hit bleed (~ -3 dB vs the kick peak): raw slices must be
        # audibly dirty for the stack-beats-raw margin to mean anything.
        noise = sig.sosfilt(
            sig.butter(4, 2000 / (SR / 2), btype="low", output="sos"),
            rng.standard_normal(kick.size)) * 0.7
        y[i:i + kick.size] += kick + noise
    for t in [0.5 + i * BEAT + BEAT / 2 for i in range(16)]:
        i = int(t * SR)
        y[i:i + n_h] += hat

    y *= 0.9 / (np.abs(y).max() + 1e-9)
    wav = tmp_path / "drums.wav"
    sf.write(wav, y.astype(np.float32), SR)

    table = dk.detect_drum_hits(wav)
    assert table["hits"], "detector found nothing on the fixture"
    result = {
        dk.DRUM_HITS_RESULT_KEY: table,
        "stems_local": {"drums": str(wav)},
    }
    return result, y, kick_times


def _norm_corr(a, b):
    n = min(a.size, b.size)
    a, b = a[:n], b[:n]
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-12))


def test_render_produces_cleaner_kick_than_raw_slice(noisy_song):
    import soundfile as sf

    result, y, kick_times = noisy_song
    files = dkr.render_drum_samples("song1", result)
    assert files, "renderer produced no files"

    manifest = dkr.load_manifest("song1")
    assert manifest == files

    picks = dk.select_one_shots(result[dk.DRUM_HITS_RESULT_KEY]["hits"])
    kick_pads = [i for i, (cls, _, _) in enumerate(picks) if cls == "kick"]
    assert kick_pads, "no kick pad selected on a kick-heavy fixture"
    pad_idx = kick_pads[0]
    assert pad_idx in files

    comp, sr = sf.read(str(dkr.sample_path("song1", files[pad_idx])))
    assert sr == SR

    template = _kick_template()
    template /= np.abs(template).max()

    # Raw slice for the same exemplar, peak-normalized like the composite.
    _, _, exemplar = picks[pad_idx]
    i0 = int(exemplar["t"] * SR)
    raw = y[i0: i0 + template.size].copy()
    raw /= np.abs(raw).max() + 1e-12

    # The renderer trims leading air to ~2 ms before the attack; find the
    # exact attack in both signals and compare from there so the test
    # measures cleanliness, not residual sub-ms offset.
    def _from_attack(x):
        i = int(np.argmax(np.abs(x) > 0.5 * np.abs(x).max()))
        return x[i:]

    corr_comp = _norm_corr(_from_attack(comp), _from_attack(template))
    corr_raw = _norm_corr(_from_attack(raw), _from_attack(template))
    # The whole point: stacking must beat the raw slice, and by a real
    # margin, not a rounding error.
    assert corr_comp > corr_raw + 0.05, (corr_comp, corr_raw)
    assert corr_comp > 0.9


def test_manifest_wires_sample_urls(noisy_song):
    result, _, _ = noisy_song
    files = dkr.render_drum_samples("song2", result)
    assert files
    kit = dk.build_drum_kit("song2", result, sample_files=files)
    with_url = [p for p in kit["pads"] if "sampleUrl" in p]
    assert with_url
    for p in with_url:
        assert p["sampleUrl"] == \
            f"/api/song/song2/drum-sample/{files[p['padIdx']]}"
        # stemSlice stays — old clients keep working from the raw window.
        assert p["stemSlice"]["stemRole"] == "drums"
    # Grooves (loopable) never get files.
    assert all("sampleUrl" not in p for p in kit["pads"] if p["loopable"])


def test_load_manifest_rejects_half_written_cache(noisy_song, tmp_path):
    result, _, _ = noisy_song
    files = dkr.render_drum_samples("song3", result)
    assert files
    # Delete one listed file — the manifest must refuse to serve the rest.
    victim = dkr.sample_path("song3", next(iter(files.values())))
    victim.unlink()
    assert dkr.load_manifest("song3") is None
