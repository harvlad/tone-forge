"""Instrument pack (tone_forge.instrument_pack) — zip assembly from
fabricated prerequisites: drum composites in the render cache, a bass stem
with known MIDI notes. Graph-less result → stab group simply absent."""
from __future__ import annotations

import io
import json
import zipfile

import numpy as np
import pytest
import soundfile as sf

from tone_forge import instrument_pack as ip
from tone_forge.performance.drum_kit import HITS_VERSION
from tone_forge.performance.drum_kit_render import RENDER_VERSION

SR = 44100


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("TONEFORGE_DRUMKIT_CACHE", str(tmp_path / "kits"))
    monkeypatch.setenv("TONEFORGE_KIT_CACHE", str(tmp_path / "zips"))
    monkeypatch.setenv("TONEFORGE_STEM_CACHE", "0")

    # Drum composites cache for the song.
    d = tmp_path / "kits" / f"song-v{HITS_VERSION}.{RENDER_VERSION}"
    d.mkdir(parents=True)
    files = {}
    for i, cls in enumerate(("kick", "snare")):
        fname = f"pad{i:02d}_{cls}_v{HITS_VERSION}-{RENDER_VERSION}.wav"
        sf.write(str(d / fname), np.full(4410, 0.5, dtype=np.float32), SR)
        files[str(i)] = fname
    (d / "files.json").write_text(json.dumps(files))

    # Bass stem: a 110 Hz (A2 = MIDI 45) tone from 1.0–2.2 s.
    y = np.zeros(int(4 * SR), dtype=np.float32)
    t = np.arange(int(1.2 * SR)) / SR
    y[int(1.0 * SR): int(1.0 * SR) + t.size] = \
        (np.sin(2 * np.pi * 110.0 * t) * 0.7).astype(np.float32)
    bass = tmp_path / "bass.wav"
    sf.write(str(bass), y, SR)

    result = {
        "stems_local": {"bass": str(bass)},
        "midi_stems": {"bass": {"notes": [
            {"pitch": 45, "start": 1.0, "end": 2.2, "velocity": 100},
            {"pitch": 47, "start": 3.0, "end": 3.1, "velocity": 40},
        ]}},
    }
    return result


def test_pack_zip_contents(env):
    data, filename = ip.build_instrument_pack_zip("song", env, song_name="My Song")
    assert filename.endswith("Instrument.zip")
    z = zipfile.ZipFile(io.BytesIO(data))
    names = z.namelist()
    root = names[0].split("/")[0]

    sfz = z.read(f"{root}/{root}.sfz").decode()
    # Drums keyed from C1 (36) up, one region each.
    assert "key=36" in sfz and "key=37" in sfz
    # Bass: single root sample, sampler transposes — keycenter is the
    # DETECTED pitch, not a hardcoded C.
    assert "pitch_keycenter=45" in sfz
    assert "lokey=24 hikey=59" in sfz
    # No graph in the fixture → no stab region, and that's fine.
    assert "chord_stab" not in sfz

    assert any(n.endswith("bass_root.wav") for n in names)
    assert sum(1 for n in names if "/Samples/pad" in n) == 2

    manifest = json.loads(z.read(f"{root}/pack.json"))
    assert manifest["bass"]["rootMidi"] == 45
    # Repo-wide octave convention is C3 = 60 (matches ableton_kit_export's
    # README note names), so MIDI 45 renders as A1 here, not scientific A2.
    assert manifest["bass"]["rootName"] == "A1"
    assert len(manifest["drums"]) == 2


def test_bass_note_pick_prefers_long_isolated(env):
    note = ip._pick_bass_note(env)
    assert note["pitch"] == 45  # the sustained loud one, not the 0.1 s blip


def test_cache_roundtrip(env):
    d1, f1 = ip.build_instrument_pack_zip("song", env, song_name="My Song")
    d2, f2 = ip.build_instrument_pack_zip("song", env, song_name="My Song")
    assert d1 == d2 and f1 == f2


def test_nothing_renderable_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("TONEFORGE_DRUMKIT_CACHE", str(tmp_path / "none"))
    monkeypatch.setenv("TONEFORGE_KIT_CACHE", "0")
    monkeypatch.setenv("TONEFORGE_STEM_CACHE", "0")
    with pytest.raises(ValueError):
        ip.build_instrument_pack_zip("empty", {}, song_name="X")
