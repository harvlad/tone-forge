"""Kong/ByteDance high-resolution piano transcription adapter.

Official inference: pip `piano-transcription-inference` (paper author's
package).  Returns the transcriptor's own est_note_events — no custom
decoding.  Weights CC-BY-4.0 (Zenodo 4034264), auto-downloaded to
~/piano_transcription_inference_data/.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from .base import ModelAdapter

_TRANSCRIPTOR = None  # model-load cache (350MB weights; load once per process)


class KongPianoAdapter(ModelAdapter):
    model_id = "kong_piano"
    model_version = "note_pedal_zenodo4034264"
    adapter_version = "v1"
    display_name = "ByteDance High-Res Piano (Kong 2020)"
    supported_devices = ("cpu", "cuda")
    requires_gpu = False
    input_sample_rate = 16000
    environment = "default"

    # Package defaults — recorded explicitly so they are part of cache identity
    ONSET_THRESHOLD = 0.3
    OFFSET_THRESHOLD = 0.3
    FRAME_THRESHOLD = 0.1

    def _checkpoint_path(self) -> Optional[Path]:
        d = Path.home() / "piano_transcription_inference_data"
        if d.is_dir():
            cands = sorted(d.glob("*.pth"))
            if cands:
                return cands[0]
        return None

    def available(self) -> bool:
        try:
            import piano_transcription_inference  # noqa: F401
            return True
        except ImportError:
            return False

    def inference_config(self) -> dict:
        return {
            "api": "piano_transcription_inference.PianoTranscription.transcribe",
            "model_type": "Note_pedal",
            "onset_threshold": self.ONSET_THRESHOLD,
            "offset_threshold": self.OFFSET_THRESHOLD,
            "frame_threshold": self.FRAME_THRESHOLD,
            "sr": self.input_sample_rate,
        }

    def checkpoint_hash(self) -> str:
        p = self._checkpoint_path()
        if p is None:
            # trigger package's own checkpoint download via model construction
            self._get_transcriptor()
            p = self._checkpoint_path()
        if p is None:
            raise FileNotFoundError("Kong piano checkpoint not found after init")
        return self._memoized_file_hash(p)

    def _get_transcriptor(self):
        global _TRANSCRIPTOR
        if _TRANSCRIPTOR is None:
            import torch
            from piano_transcription_inference import PianoTranscription
            _TRANSCRIPTOR = PianoTranscription(
                device=torch.device("cpu"),
                onset_threshold=self.ONSET_THRESHOLD,
                offset_threshold=self.OFFSET_THRESHOLD,
                frame_threshold=self.FRAME_THRESHOLD,
            )
        return _TRANSCRIPTOR

    def predict(self, audio_path: Path) -> List[dict]:
        import tempfile

        from piano_transcription_inference import load_audio, sample_rate
        transcriptor = self._get_transcriptor()
        audio, _ = load_audio(str(audio_path), sr=sample_rate, mono=True)
        with tempfile.NamedTemporaryFile(suffix=".mid") as tmp:
            result = transcriptor.transcribe(audio, tmp.name)
        notes = []
        for ev in result.get("est_note_events", []):
            notes.append({
                "pitch": int(ev["midi_note"]),
                "onset": float(ev["onset_time"]),
                "offset": float(ev["offset_time"]),
                "velocity": int(ev.get("velocity", 0)),
            })
        return notes
