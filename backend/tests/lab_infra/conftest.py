"""Lab test fixtures: isolated lab_data root, fake dataset, fake adapter."""
from __future__ import annotations

import wave
from pathlib import Path

import mido
import numpy as np
import pytest

from lab import config


@pytest.fixture()
def lab_env(tmp_path, monkeypatch):
    """Redirect every lab data path into tmp_path and register a tiny
    fake dataset."""
    root = tmp_path / "lab_data"
    monkeypatch.setattr(config, "LAB_DATA", root)
    for name, sub in [
        ("CORPUS_DIR", "corpus"), ("GT_DIR", "gt"), ("PREDICTIONS_DIR", "predictions"),
        ("MATCHES_DIR", "matches"), ("FEATURES_DIR", "features"),
        ("EXPERIMENTS_DIR", "experiments"), ("MODELS_DIR", "models"),
        ("TIERS_DIR", "tiers"), ("JOBS_DIR", "jobs"), ("LEGACY_DIR", "legacy"),
        ("REPORTS_DIR", "reports"),
    ]:
        monkeypatch.setattr(config, name, root / sub)
    monkeypatch.setattr(config, "MANIFEST_PATH", root / "corpus" / "manifest.parquet")
    monkeypatch.setattr(config, "EXPERIMENTS_LOG", root / "experiments" / "experiments.jsonl")
    monkeypatch.setattr(config, "MODEL_REGISTRY_PATH", root / "models" / "registry.json")
    monkeypatch.setattr(config, "THROUGHPUT_PATH", root / "models" / "throughput.json")

    dataset_root = tmp_path / "fakeslakh"
    monkeypatch.setitem(config.DATASETS, "fakeslakh", dataset_root)
    config.ensure_dirs()
    return {"root": root, "dataset_root": dataset_root}


def write_wav(path: Path, duration: float = 1.0, sr: int = 22050, freq: float = 440.0):
    path.parent.mkdir(parents=True, exist_ok=True)
    t = np.arange(int(duration * sr)) / sr
    samples = (np.sin(2 * np.pi * freq * t) * 0.5 * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(samples.tobytes())


def write_midi(path: Path, notes, tempo_bpm: float = 120.0):
    """notes: list of (pitch, onset_s, offset_s)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    mid = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    tempo = mido.bpm2tempo(tempo_bpm)
    track.append(mido.MetaMessage("set_tempo", tempo=tempo, time=0))
    events = []
    for pitch, onset, offset in notes:
        events.append((onset, "note_on", pitch))
        events.append((offset, "note_off", pitch))
    events.sort()
    prev = 0.0
    for t_s, kind, pitch in events:
        delta = int(round(mido.second2tick(t_s - prev, 480, tempo)))
        track.append(mido.Message(kind, note=pitch, velocity=64 if kind == "note_on" else 0,
                                  time=delta))
        prev = t_s
    mid.save(str(path))


def make_fake_dataset(root: Path, n_tracks: int = 2, stems_per_track: int = 3):
    """Slakh-layout fake dataset with sine audio + simple GT MIDI."""
    # real Slakh track names are globally unique across splits
    for si_offset, split in ((0, "validation"), (100, "test")):
        for ti_base in range(n_tracks):
            ti = ti_base + si_offset
            track = f"Track{ti:05d}"
            tdir = root / split / track
            meta = {"UUID": f"fake-{split}-{ti}", "stems": {}}
            for si in range(stems_per_track):
                stem = f"S{si:02d}"
                # programs: 33 (Bass), 0 (Piano), 25 (Guitar)
                program = [33, 0, 25][si % 3]
                meta["stems"][stem] = {"program_num": program, "inst_class": "x",
                                       "is_drum": False, "plugin_name": "fake"}
                write_wav(tdir / "stems" / f"{stem}.wav", duration=1.0,
                          freq=220.0 + 20 * si + 100 * ti)
                write_midi(tdir / "MIDI" / f"{stem}.mid",
                           [(40 + si, 0.10, 0.40), (52 + si, 0.60, 0.90)])
            import yaml
            (tdir / "metadata.yaml").write_text(yaml.safe_dump(meta))
    return root


@pytest.fixture()
def fake_corpus(lab_env):
    from lab import corpus
    make_fake_dataset(lab_env["dataset_root"])
    manifest = corpus.build_manifest(datasets=["fakeslakh"], progress=False)
    return manifest


class FakeAdapter:
    """Deterministic fake model: emits notes derived from GT-like fixed
    pattern; counts predict() calls so tests can assert zero inference."""
    model_id = "fake_model"
    model_version = "1.0"
    adapter_version = "v1"
    display_name = "Fake"
    supported_devices = ("cpu",)
    requires_gpu = False
    input_sample_rate = None
    environment = "default"
    calls = 0
    checkpoint = "ckpt-A"
    notes_to_return = [
        {"pitch": 40, "onset": 0.10, "offset": 0.40, "velocity": 80},
        {"pitch": 52, "onset": 0.60, "offset": 0.90, "velocity": 80},
    ]

    def available(self):
        return True

    def inference_config(self):
        return {"api": "fake", "threshold": 0.5}

    def checkpoint_hash(self):
        return type(self).checkpoint

    def predict(self, audio_path):
        type(self).calls += 1
        return [dict(n) for n in type(self).notes_to_return]


@pytest.fixture()
def fake_adapter(monkeypatch):
    import lab.adapters as adapters_mod
    FakeAdapter.calls = 0
    FakeAdapter.checkpoint = "ckpt-A"
    FakeAdapter.notes_to_return = [
        {"pitch": 40, "onset": 0.10, "offset": 0.40, "velocity": 80},
        {"pitch": 52, "onset": 0.60, "offset": 0.90, "velocity": 80},
    ]
    monkeypatch.setitem(adapters_mod.ADAPTERS, "fake_model", FakeAdapter)
    return FakeAdapter
