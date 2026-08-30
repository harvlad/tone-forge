"""SwiftF0 adapter — monophonic f0 -> notes via the package's OFFICIAL
segment_notes() (MIT).  No custom decoding: detection and segmentation
are both the package author's implementations with recorded defaults.

Range: 46.9 Hz (G1) – 2093 Hz (C7).  Fine for Slakh Bass (corpus floor
MIDI 47 ≈ 123 Hz), synth lead, reed, brass, pipe.
"""
from __future__ import annotations

from pathlib import Path
from typing import List

from .base import ModelAdapter

_DETECTOR = None


class SwiftF0Adapter(ModelAdapter):
    model_id = "swiftf0"
    model_version = "swift-f0-pip"
    adapter_version = "v1"
    display_name = "SwiftF0 mono f0->notes (Nieradzik 2025)"
    supported_devices = ("cpu",)
    requires_gpu = False
    input_sample_rate = 16000
    environment = "default"

    CONFIDENCE_THRESHOLD = 0.9   # package default
    SPLIT_SEMITONE_THRESHOLD = 0.8
    MIN_NOTE_DURATION = 0.05
    UNVOICED_GRACE = 0.02

    def available(self) -> bool:
        try:
            import swift_f0  # noqa: F401
            return True
        except ImportError:
            return False

    def inference_config(self) -> dict:
        return {
            "api": "swift_f0.SwiftF0.detect_from_file + swift_f0.segment_notes",
            "confidence_threshold": self.CONFIDENCE_THRESHOLD,
            "split_semitone_threshold": self.SPLIT_SEMITONE_THRESHOLD,
            "min_note_duration": self.MIN_NOTE_DURATION,
            "unvoiced_grace_period": self.UNVOICED_GRACE,
        }

    def checkpoint_hash(self) -> str:
        import swift_f0
        pkg = Path(swift_f0.__file__).parent
        onnx = sorted(pkg.rglob("*.onnx"))
        if not onnx:
            # model may be embedded differently; hash the whole package dir
            return self._memoized_dir_hash(pkg)
        return self._memoized_file_hash(onnx[0])

    def predict(self, audio_path: Path) -> List[dict]:
        global _DETECTOR
        import swift_f0
        if _DETECTOR is None:
            _DETECTOR = swift_f0.SwiftF0(
                confidence_threshold=self.CONFIDENCE_THRESHOLD)
        result = _DETECTOR.detect_from_file(str(audio_path))
        segments = swift_f0.segment_notes(
            result,
            split_semitone_threshold=self.SPLIT_SEMITONE_THRESHOLD,
            min_note_duration=self.MIN_NOTE_DURATION,
            unvoiced_grace_period=self.UNVOICED_GRACE,
        )
        notes = []
        for seg in segments:
            notes.append({
                "pitch": int(seg.pitch_midi),
                "onset": float(seg.start),
                "offset": float(seg.end),
                "velocity": 80,          # model has no velocity output
                "confidence": None,
            })
        return notes
