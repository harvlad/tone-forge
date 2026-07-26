"""Specialist transcription runner.

Executes a RoutingDecision on one separated stem and returns a MIDI
result dict in the EXACT shape extract_midi_hybrid produces
(filename/content/note_count/duration_seconds/extraction_tempo_bpm/
pitch_range/method) so every downstream consumer (bundle persistence,
session/_build_user_midi, preset export) works unchanged.

Wraps the models' OFFICIAL inference APIs directly (no Lab imports —
the Lab is research-only; configs are pinned in specialist_registry.json
with Lab provenance recorded there).  Never reimplement windowing or
decoding: a custom Basic Pitch path once shipped a 1.21x timing-stretch
bug.
"""
from __future__ import annotations

import base64
import io
import logging
import tempfile
import time
from pathlib import Path
from typing import List, Optional, Tuple

from . import normalization
from .router import RoutingDecision

logger = logging.getLogger(__name__)

_MODEL_CACHE: dict = {}


class SpecialistUnavailableError(RuntimeError):
    pass


def _decode_midi_file(midi_path: Path) -> List[dict]:
    """First-set_tempo mido decode (matches Lab-validated decoding)."""
    import mido
    mid = mido.MidiFile(str(midi_path))
    tempo = 500000
    for track in mid.tracks:
        for msg in track:
            if msg.type == "set_tempo":
                tempo = msg.tempo
                break
    notes = []
    for track in mid.tracks:
        t = 0.0
        active: dict = {}
        for msg in track:
            t += mido.tick2second(msg.time, mid.ticks_per_beat, tempo)
            if msg.type == "note_on" and msg.velocity > 0:
                active[msg.note] = (t, msg.velocity)
            elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
                if msg.note in active:
                    start, vel = active.pop(msg.note)
                    notes.append({"pitch": int(msg.note), "onset": float(start),
                                  "offset": float(t), "velocity": int(vel)})
    return notes


def _notes_to_midi_bytes(notes: List[dict], tempo_bpm: float = 120.0) -> bytes:
    import mido
    mid = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    tempo = mido.bpm2tempo(tempo_bpm)
    track.append(mido.MetaMessage("set_tempo", tempo=tempo, time=0))
    events = []
    for n in notes:
        vel = max(1, min(127, int(n.get("velocity", 80) or 80)))
        events.append((float(n["onset"]), 1, int(n["pitch"]), vel))
        events.append((float(n["offset"]), 0, int(n["pitch"]), 0))
    events.sort(key=lambda e: (e[0], e[1]))
    prev = 0.0
    for t, kind, pitch, vel in events:
        delta = max(0, int(round(mido.second2tick(t - prev, 480, tempo))))
        track.append(mido.Message("note_on" if kind else "note_off",
                                  note=pitch, velocity=vel, time=delta))
        prev = t
    buf = io.BytesIO()
    mid.save(file=buf)
    return buf.getvalue()


def _run_riley(decision: RoutingDecision, stem_path: Path) -> List[dict]:
    try:
        from hf_midi_transcription import MidiTranscriptionModel
    except ImportError as e:
        raise SpecialistUnavailableError(f"hf_midi_transcription missing: {e}")
    cfg = decision.transcriber_config
    key = ("riley", cfg["instrument"])
    if key not in _MODEL_CACHE:
        _MODEL_CACHE[key] = MidiTranscriptionModel(
            device="cpu", instrument=cfg["instrument"],
            onset_threshold=cfg["onset_threshold"],
            offset_threshold=cfg["offset_threshold"],
            frame_threshold=cfg["frame_threshold"])
    model = _MODEL_CACHE[key]
    with tempfile.NamedTemporaryFile(suffix=".mid") as tmp:
        model.transcribe(str(stem_path), tmp.name)
        return _decode_midi_file(Path(tmp.name))


def _run_kong(decision: RoutingDecision, stem_path: Path) -> List[dict]:
    try:
        import torch
        from piano_transcription_inference import (PianoTranscription, load_audio,
                                                   sample_rate)
    except ImportError as e:
        raise SpecialistUnavailableError(f"piano_transcription_inference missing: {e}")
    cfg = decision.transcriber_config
    if "kong" not in _MODEL_CACHE:
        _MODEL_CACHE["kong"] = PianoTranscription(
            device=torch.device("cpu"),
            onset_threshold=cfg["onset_threshold"],
            offset_threshold=cfg["offset_threshold"],
            frame_threshold=cfg["frame_threshold"])
    model = _MODEL_CACHE["kong"]
    audio, _ = load_audio(str(stem_path), sr=sample_rate, mono=True)
    with tempfile.NamedTemporaryFile(suffix=".mid") as tmp:
        result = model.transcribe(audio, tmp.name)
    return [{"pitch": int(ev["midi_note"]), "onset": float(ev["onset_time"]),
             "offset": float(ev["offset_time"]), "velocity": int(ev.get("velocity", 80))}
            for ev in result.get("est_note_events", [])]


_IMPL_RUNNERS = {
    "hf_midi_transcription.MidiTranscriptionModel": _run_riley,
    "piano_transcription_inference.PianoTranscription": _run_kong,
}


def warm_imports(decision: RoutingDecision) -> None:
    """Import the routed implementation's modules on the CALLING thread.

    The analysis worker runs stem extraction in a ThreadPoolExecutor;
    Python module imports are not free-threaded, and hf_midi_transcription
    pulls in matplotlib, whose submodule imports race against other
    extractors' imports (observed: KeyError 'matplotlib.axis'). Importing
    here — single-threaded, before the pool spawns — removes the race.
    Weights still load lazily inside the pool (thread-safe once modules
    exist).
    """
    if decision.transcriber_impl.startswith("hf_midi_transcription"):
        import matplotlib
        matplotlib.use("Agg", force=True)  # headless subprocess safety
        import matplotlib.pyplot  # noqa: F401
        import hf_midi_transcription  # noqa: F401
    elif decision.transcriber_impl.startswith("piano_transcription_inference"):
        import piano_transcription_inference  # noqa: F401


def transcribe_stem(decision: RoutingDecision, stem_path: str,
                    preset_name: str = "Extracted MIDI",
                    tempo_bpm: Optional[float] = None) -> Tuple[dict, dict]:
    """Run the routed specialist on a stem.

    Returns (midi_result_dict, provenance_dict). Raises on failure —
    the caller decides fallback policy (and must record the failure).
    """
    runner = _IMPL_RUNNERS.get(decision.transcriber_impl)
    if runner is None:
        raise SpecialistUnavailableError(
            f"no runner for impl '{decision.transcriber_impl}'")
    t0 = time.time()
    raw_notes = runner(decision, Path(stem_path))
    infer_s = time.time() - t0

    t1 = time.time()
    notes, norm_prov = normalization.apply(
        raw_notes, decision.normalization_shift,
        decision.normalization, decision.normalization_version)
    norm_s = time.time() - t1

    tempo = float(tempo_bpm or 120.0)
    midi_bytes = _notes_to_midi_bytes(notes, tempo_bpm=tempo)
    pitches = [n["pitch"] for n in notes]
    duration = max((n["offset"] for n in notes), default=0.0)
    method = (f"specialist:{decision.transcriber}@{decision.transcriber_version}"
              f"+{decision.normalization}@{decision.normalization_version}")

    midi_result = {
        "filename": f"{preset_name}.mid",
        "content": base64.b64encode(midi_bytes).decode("ascii"),
        "note_count": len(notes),
        "duration_seconds": duration,
        "extraction_tempo_bpm": tempo,
        "pitch_range": (min(pitches), max(pitches)) if pitches else (0, 0),
        "method": method,
    }
    provenance = {
        **decision.provenance(),
        "normalization_applied": norm_prov,
        "timing": {"transcription_s": round(infer_s, 2),
                   "normalization_s": round(norm_s, 4)},
        "raw_note_count": len(raw_notes),
    }
    return midi_result, provenance
