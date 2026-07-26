"""Basic Pitch adapter — wraps the OFFICIAL predict() path that produced
the validated benchmark (mt3_vs_basic_pitch.py).  Do not change the
inference call: the parameters below ARE the validated configuration.
"""
from __future__ import annotations

from pathlib import Path
from typing import List

from .base import ModelAdapter


class BasicPitchAdapter(ModelAdapter):
    model_id = "basic_pitch"
    model_version = "icassp_2022"
    adapter_version = "v2"  # v2: source_index order preserved in output
    display_name = "Basic Pitch (Spotify, ICASSP 2022)"
    supported_devices = ("cpu",)
    requires_gpu = False
    input_sample_rate = None  # official predict() handles resampling

    # Validated configuration — identical to mt3_vs_basic_pitch.py
    ONSET_THRESHOLD = 0.5
    FRAME_THRESHOLD = 0.3
    MINIMUM_NOTE_LENGTH_MS = 127.7
    MELODIA_TRICK = True

    def available(self) -> bool:
        try:
            import basic_pitch.inference  # noqa: F401
            return True
        except ImportError:
            return False

    def inference_config(self) -> dict:
        return {
            "api": "basic_pitch.inference.predict",
            "onset_threshold": self.ONSET_THRESHOLD,
            "frame_threshold": self.FRAME_THRESHOLD,
            "minimum_note_length": self.MINIMUM_NOTE_LENGTH_MS,
            "melodia_trick": self.MELODIA_TRICK,
        }

    def checkpoint_hash(self) -> str:
        from basic_pitch import ICASSP_2022_MODEL_PATH
        path = Path(ICASSP_2022_MODEL_PATH)
        if path.is_dir():
            return self._memoized_dir_hash(path)
        return self._memoized_file_hash(path)

    def predict(self, audio_path: Path) -> List[dict]:
        from basic_pitch.inference import predict as bp_predict
        _, _, note_events = bp_predict(
            str(audio_path),
            onset_threshold=self.ONSET_THRESHOLD,
            frame_threshold=self.FRAME_THRESHOLD,
            minimum_note_length=self.MINIMUM_NOTE_LENGTH_MS,
            melodia_trick=self.MELODIA_TRICK,
        )
        notes = []
        for event in note_events:
            onset, offset, pitch, amplitude = event[0], event[1], event[2], event[3]
            notes.append({
                "pitch": int(pitch),
                "onset": float(onset),
                "offset": float(offset),
                "velocity": int(amplitude * 127),
                "confidence": float(amplitude),
            })
        return notes
