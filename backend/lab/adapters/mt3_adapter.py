"""MT3 adapter — wraps mt3_infer.transcribe with the mt3_pytorch
checkpoint, identical to the validated benchmark (mt3_vs_basic_pitch.py:
librosa 16 kHz mono load, first-set_tempo mido decode).
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from ..config import BACKEND_ROOT
from .base import ModelAdapter

# Checkpoint actually used by the validated run (mt3_infer downloads to a
# package-managed location; this repo copy is the pinned identity source).
CHECKPOINT_CANDIDATES = [
    BACKEND_ROOT / ".mt3_checkpoints" / "mt3_pytorch" / "mt3.pth",
    Path.home() / ".mt3_checkpoints" / "mt3_pytorch" / "mt3.pth",
]


class MT3Adapter(ModelAdapter):
    model_id = "mt3"
    model_version = "mt3_pytorch"
    adapter_version = "v2"  # v2: source_index order preserved in output
    display_name = "MT3 (mt3-infer, PyTorch port)"
    supported_devices = ("cpu", "mps", "cuda")
    requires_gpu = False  # runs on CPU, just slowly; GPU strongly preferred
    input_sample_rate = 16000
    environment = "mt3"

    def available(self) -> bool:
        try:
            import mt3_infer  # noqa: F401
            return True
        except ImportError:
            return False

    def inference_config(self) -> dict:
        return {
            "api": "mt3_infer.transcribe",
            "model": self.model_version,
            "sr": self.input_sample_rate,
            "mono": True,
        }

    def _checkpoint_path(self) -> Optional[Path]:
        for p in CHECKPOINT_CANDIDATES:
            if p.exists():
                return p
        return None

    def checkpoint_hash(self) -> str:
        p = self._checkpoint_path()
        if p is None:
            raise FileNotFoundError(
                f"MT3 checkpoint not found in {[str(c) for c in CHECKPOINT_CANDIDATES]}")
        return self._memoized_file_hash(p)

    def predict(self, audio_path: Path) -> List[dict]:
        import librosa
        import mido
        import mt3_infer

        audio, _ = librosa.load(str(audio_path), sr=self.input_sample_rate, mono=True)
        midi_file = mt3_infer.transcribe(audio, model=self.model_version,
                                         sr=self.input_sample_rate)

        # Validated decode: first set_tempo wins, per-track pairing.
        tempo = 500000
        for track in midi_file.tracks:
            for msg in track:
                if msg.type == "set_tempo":
                    tempo = msg.tempo
                    break
        notes = []
        for track in midi_file.tracks:
            current_time = 0.0
            active = {}
            for msg in track:
                current_time += mido.tick2second(msg.time, midi_file.ticks_per_beat, tempo)
                if msg.type == "note_on" and msg.velocity > 0:
                    active[msg.note] = (current_time, msg.velocity)
                elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
                    if msg.note in active:
                        start, vel = active.pop(msg.note)
                        notes.append({
                            "pitch": int(msg.note),
                            "onset": float(start),
                            "offset": float(current_time),
                            "velocity": int(vel),
                        })
        return notes
