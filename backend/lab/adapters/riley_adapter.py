"""Riley/QMUL hf-midi-transcription adapters (GAPS guitar, FiloBass bass).

Official inference: `hf_midi_transcription.MidiTranscriptionModel`
(MIT code + MIT HF weights).  The official API writes a MIDI file; we
decode it with the shared validated decoder — no custom inference.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from .base import ModelAdapter
from .midi_decode import decode_midi_file

_MODELS = {}  # instrument -> loaded model


class _RileyAdapter(ModelAdapter):
    instrument = ""  # set by subclass
    adapter_version = "v1"
    supported_devices = ("cpu", "cuda")
    requires_gpu = False
    environment = "default"

    # package defaults, recorded for cache identity
    ONSET_THRESHOLD = 0.3
    OFFSET_THRESHOLD = 0.3
    FRAME_THRESHOLD = 0.1

    def available(self) -> bool:
        try:
            import hf_midi_transcription  # noqa: F401
            return True
        except ImportError:
            return False

    def inference_config(self) -> dict:
        return {
            "api": "hf_midi_transcription.MidiTranscriptionModel.transcribe",
            "instrument": self.instrument,
            "onset_threshold": self.ONSET_THRESHOLD,
            "offset_threshold": self.OFFSET_THRESHOLD,
            "frame_threshold": self.FRAME_THRESHOLD,
        }

    def _hf_cache_dir(self) -> Optional[Path]:
        root = Path.home() / ".cache" / "huggingface" / "hub"
        cands = list(root.glob("models--xavriley--*"))
        return cands[0] if cands else None

    def checkpoint_hash(self) -> str:
        d = self._hf_cache_dir()
        if d is None:
            self._get_model()  # trigger HF download
            d = self._hf_cache_dir()
        if d is None:
            raise FileNotFoundError("Riley HF checkpoint cache not found")
        snap = d / "snapshots"
        return self._memoized_dir_hash(snap if snap.is_dir() else d)

    def _get_model(self):
        if self.instrument not in _MODELS:
            from hf_midi_transcription import MidiTranscriptionModel
            _MODELS[self.instrument] = MidiTranscriptionModel(
                device="cpu", instrument=self.instrument,
                onset_threshold=self.ONSET_THRESHOLD,
                offset_threshold=self.OFFSET_THRESHOLD,
                frame_threshold=self.FRAME_THRESHOLD,
            )
        return _MODELS[self.instrument]

    def predict(self, audio_path: Path) -> List[dict]:
        import tempfile
        model = self._get_model()
        with tempfile.NamedTemporaryFile(suffix=".mid") as tmp:
            model.transcribe(str(audio_path), tmp.name)
            return decode_midi_file(Path(tmp.name))


class RileyGuitarAdapter(_RileyAdapter):
    model_id = "riley_guitar"
    model_version = "gaps_2024"
    display_name = "Riley GAPS guitar (QMUL 2024)"
    instrument = "guitar"


class RileyBassAdapter(_RileyAdapter):
    model_id = "riley_bass"
    model_version = "filobass_2023"
    display_name = "Riley FiloBass (QMUL 2023)"
    instrument = "bass"
