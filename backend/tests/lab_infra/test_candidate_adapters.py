"""Candidate adapter contract tests (no heavy inference)."""
from __future__ import annotations

import mido
import pytest

from lab.adapters import ADAPTERS, get_adapter
from lab.adapters.midi_decode import decode_midi_file

CANDIDATES = ["kong_piano", "riley_guitar", "riley_bass", "swiftf0",
              "yourmt3", "mr_mt3"]


def test_all_candidates_registered():
    for mid in CANDIDATES:
        assert mid in ADAPTERS


@pytest.mark.parametrize("mid", CANDIDATES)
def test_inference_config_is_canonical(mid):
    a = get_adapter(mid)
    cfg1, cfg2 = a.inference_config(), a.inference_config()
    assert cfg1 == cfg2
    assert "api" in cfg1, "config must pin the official API used"
    from lab.hashing import canonical_json
    canonical_json(cfg1)  # must be JSON-serializable deterministically


def test_distinct_cache_identities():
    """Different candidates must never collide on cache identity inputs."""
    seen = set()
    for mid in CANDIDATES + ["basic_pitch", "mt3"]:
        a = get_adapter(mid)
        key = (a.model_id, a.model_version, str(sorted(a.inference_config().items())))
        assert key not in seen
        seen.add(key)


def test_decode_midi_file_roundtrip(tmp_path):
    mid = mido.MidiFile(ticks_per_beat=480)
    tr = mido.MidiTrack()
    mid.tracks.append(tr)
    tempo = mido.bpm2tempo(120)
    tr.append(mido.MetaMessage("set_tempo", tempo=tempo, time=0))
    tr.append(mido.Message("note_on", note=60, velocity=90, time=480))   # t=0.5s
    tr.append(mido.Message("note_off", note=60, velocity=0, time=480))   # t=1.0s
    p = tmp_path / "x.mid"
    mid.save(str(p))
    notes = decode_midi_file(p)
    assert len(notes) == 1
    n = notes[0]
    assert n["pitch"] == 60 and abs(n["onset"] - 0.5) < 1e-6 and abs(n["offset"] - 1.0) < 1e-6
    assert n["velocity"] == 90
