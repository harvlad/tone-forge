"""Real-adapter smoke: official Basic Pitch on a synthetic tone.

Slow-ish (~seconds).  Verifies the adapter chain end-to-end: official
predict() -> normalize -> validate -> cache.
"""
from __future__ import annotations

import numpy as np
import pytest

from conftest import write_wav

basic_pitch = pytest.importorskip("basic_pitch")


def test_basic_pitch_adapter_smoke(lab_env, tmp_path):
    from lab.adapters import get_adapter
    from lab import schema
    adapter = get_adapter("basic_pitch")
    assert adapter.available()
    cfg = adapter.inference_config()
    assert cfg["onset_threshold"] == 0.5
    assert cfg["minimum_note_length"] == 127.7

    wav = tmp_path / "tone.wav"
    write_wav(wav, duration=3.0, sr=22050, freq=440.0)  # A4
    notes = adapter.predict(wav)
    df = schema.normalize_predictions(notes)
    schema.validate_predictions(df, audio_duration=3.0)
    assert len(df) >= 1, "a clean 440Hz tone must yield at least one note"
    # pitch should be A4=69 (allow octave slack — BP sometimes halves)
    assert any(int(p) % 12 == 9 for p in df["pitch"]), f"pitches: {df['pitch'].tolist()}"
    assert df["onset"].max() < 3.5, "timing must not be stretched"


def test_checkpoint_hash_stable(lab_env):
    from lab.adapters import get_adapter
    a = get_adapter("basic_pitch")
    h1 = a.checkpoint_hash()
    h2 = a.checkpoint_hash()
    assert h1 == h2 and len(h1) == 64
