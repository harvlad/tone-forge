"""MT3-family variants via mt3_infer registry: YourMT3+ and MR-MT3.

Same official wrapper + validated decode as the incumbent MT3 adapter.
License notes (registry has details): yourmt3 weights Apache-2.0 but
UPSTREAM CODE GPL-3.0 (YELLOW); mr_mt3 MIT end-to-end (GREEN).
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..config import BACKEND_ROOT, REPO_ROOT
from .mt3_adapter import MT3Adapter


class YourMT3Adapter(MT3Adapter):
    model_id = "yourmt3"
    model_version = "yourmt3"
    adapter_version = "v2"
    display_name = "YourMT3+ (YPTF, via mt3-infer)"
    environment = "mt3"

    def inference_config(self) -> dict:
        cfg = super().inference_config()
        cfg["model"] = "yourmt3"
        return cfg

    def _checkpoint_path(self) -> Optional[Path]:
        for root in (BACKEND_ROOT / ".mt3_checkpoints", REPO_ROOT / ".mt3_checkpoints",
                     Path.home() / ".mt3_checkpoints"):
            d = root / "yourmt3"
            if d.is_dir():
                cands = sorted(list(d.rglob("*.pth")) + list(d.rglob("*.ckpt"))
                               + list(d.rglob("*.safetensors")))
                if cands:
                    return cands[0]
        return None

    def checkpoint_hash(self) -> str:
        p = self._checkpoint_path()
        if p is None:
            # let mt3_infer download it, then locate
            import mt3_infer
            mt3_infer.download_model("yourmt3")
            p = self._checkpoint_path()
        if p is None:
            raise FileNotFoundError("yourmt3 checkpoint not found after download")
        return self._memoized_file_hash(p)

    def predict(self, audio_path: Path):
        import librosa
        import mido
        import mt3_infer
        audio, _ = librosa.load(str(audio_path), sr=self.input_sample_rate, mono=True)
        midi_file = mt3_infer.transcribe(audio, model="yourmt3",
                                         sr=self.input_sample_rate)
        return self._decode(midi_file, mido)

    def _decode(self, midi_file, mido):
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
                        notes.append({"pitch": int(msg.note), "onset": float(start),
                                      "offset": float(current_time), "velocity": int(vel)})
        return notes


class MRMT3Adapter(YourMT3Adapter):
    model_id = "mr_mt3"
    model_version = "mr_mt3"
    display_name = "MR-MT3 (gudgud96, MIT)"

    def inference_config(self) -> dict:
        cfg = super().inference_config()
        cfg["model"] = "mr_mt3"
        return cfg

    def _checkpoint_path(self) -> Optional[Path]:
        for root in (REPO_ROOT / ".mt3_checkpoints", BACKEND_ROOT / ".mt3_checkpoints",
                     Path.home() / ".mt3_checkpoints"):
            d = root / "mr_mt3"
            if d.is_dir():
                cands = sorted(d.glob("*.pth"))
                if cands:
                    return cands[0]
        return None

    def checkpoint_hash(self) -> str:
        p = self._checkpoint_path()
        if p is None:
            import mt3_infer
            mt3_infer.download_model("mr_mt3")
            p = self._checkpoint_path()
        if p is None:
            raise FileNotFoundError("mr_mt3 checkpoint not found")
        return self._memoized_file_hash(p)

    def predict(self, audio_path: Path):
        import librosa
        import mido
        import mt3_infer
        audio, _ = librosa.load(str(audio_path), sr=self.input_sample_rate, mono=True)
        midi_file = mt3_infer.transcribe(audio, model="mr_mt3",
                                         sr=self.input_sample_rate)
        return self._decode(midi_file, mido)
