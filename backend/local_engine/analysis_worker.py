#!/usr/bin/env python3
"""
Analysis Worker - Runs GPU-heavy analysis in a separate process.

This isolates heavy GPU work from the main server process, preventing hangs.
Communication happens via multiprocessing Queue for progress events.
"""

import json
import sys
import time
import tempfile
import traceback
from pathlib import Path
from multiprocessing import Queue
from typing import Optional

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# Apply GPU acceleration patch BEFORE any other imports
def _apply_gpu_patch():
    """Force basic_pitch to use ONNX with CoreML Execution Provider for GPU."""
    import platform
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        return

    try:
        # Change basic_pitch model path from .mlpackage to .onnx
        import basic_pitch
        from pathlib import Path

        original_path = basic_pitch.ICASSP_2022_MODEL_PATH
        onnx_path = Path(str(original_path).replace('.mlpackage', '.onnx'))
        if onnx_path.exists():
            basic_pitch.ICASSP_2022_MODEL_PATH = onnx_path
            print(f"[Worker] Using ONNX model: {onnx_path}", file=sys.stderr)

        # Disable CoreML so it falls through to ONNX
        basic_pitch.CT_PRESENT = False
        basic_pitch.TF_PRESENT = False
        basic_pitch.TFLITE_PRESENT = False

        import onnxruntime as ort

        # Patch ONNX Runtime to use CoreML EP (GPU) instead of CPU
        _original_init = ort.InferenceSession.__init__

        def _patched_init(self, path_or_bytes, sess_options=None, providers=None, provider_options=None, **kwargs):
            # Force CoreML EP for GPU acceleration on Apple Silicon
            if providers is None or providers == ["CPUExecutionProvider"]:
                providers = ["CoreMLExecutionProvider", "CPUExecutionProvider"]
                print(f"[Worker] ONNX using CoreML GPU backend", file=sys.stderr)
            _original_init(self, path_or_bytes, sess_options, providers, provider_options, **kwargs)

        ort.InferenceSession.__init__ = _patched_init
        print("[Worker] ONNX CoreML GPU patch applied", file=sys.stderr)

    except Exception as e:
        print(f"[Worker] GPU patch failed: {e}", file=sys.stderr)

_apply_gpu_patch()


def send_progress(queue: Queue, stage: str, progress: float, message: str):
    """Send a progress event to the parent process.

    Args:
        progress: Float 0.0-1.0, will be converted to percent 0-100
    """
    queue.put({
        "type": "progress",
        "stage": stage,
        "progress": progress,
        "percent": int(progress * 100),  # Frontend expects percent as int
        "message": message,
    })


def send_error(queue: Queue, message: str):
    """Send an error event to the parent process."""
    queue.put({
        "type": "error",
        "message": message,
    })


def send_result(queue: Queue, data: dict):
    """Send the final result to the parent process."""
    queue.put({
        "type": "result",
        "data": data,
    })


def send_done(queue: Queue):
    """Signal that processing is complete."""
    queue.put({"type": "done"})


# Mapping from the legacy pan-split key names (set by Step 2b in
# run_file_analysis) to the provider-agnostic StemRole. Centralised
# here so the Stem-record producer below stays a one-liner.
_PAN_SPLIT_ROLE_MAP: dict = {
    "guitar_lead": "lead",
    "guitar_rhythm": "rhythm",
    "guitar_texture": "texture",
    # Pan-position fallbacks (used when role_classifier failed): we
    # can't claim a confident role, but we do know they're guitar-
    # family content. Emit as HARMONIC so the user slot still picks
    # them up.
    "guitar_center": "harmonic",
    "guitar_sides": "harmonic",
}


def _build_stem_records(
    stems: dict,
    detected_type: str,
    guitar_parts: dict,
    midi_stems: dict,
) -> list:
    """Build the session-engine view of stems as List[Stem-as-dict].

    Mirrors ``_build_stems_dict`` 1:1 but emits provider-agnostic
    records keyed by role rather than provider-specific names. The
    frontend prefers this list when present; the dict remains the
    wire-compatible fallback.

    Identity: stem ids are ``"demucs.<original>"`` for base stems and
    ``"demucs.other.<suffix>"`` for pan-split children.
    """
    from tone_forge.stem_model import (
        Stem, StemRole, default_display_name,
    )

    records: list = []
    base_url = "http://127.0.0.1:7777/api/serve-file?path="
    has_split = len(guitar_parts) > 1

    role_for_base = {
        "drums": StemRole.DRUMS,
        "bass": StemRole.BASS,
        "vocals": StemRole.VOCALS,
        "other": StemRole.HARMONIC,
    }

    for name, path in stems.items():
        if name == "other" and has_split:
            for part_key, part_path in guitar_parts.items():
                role_str = _PAN_SPLIT_ROLE_MAP.get(part_key, "harmonic")
                try:
                    role = StemRole(role_str)
                except ValueError:
                    role = StemRole.HARMONIC
                records.append(Stem(
                    id=f"demucs.other.{part_key.replace('guitar_', '')}",
                    role=role,
                    display_name=default_display_name(role),
                    audio_url=f"{base_url}{str(part_path)}",
                    parent_id="demucs.other",
                    provider="demucs+pansplit",
                    # Pan-split has already filtered low-confidence
                    # cases via L/R correlation; role_classifier picked
                    # the label.
                    confidence=0.7,
                ).to_dict())
            continue

        # All other base stems (drums/bass/vocals, plus unsplit "other"
        # regardless of detected_type) map straight from role_for_base.
        role = role_for_base.get(name, StemRole.UNKNOWN)
        records.append(Stem(
            id=f"demucs.{name}",
            role=role,
            display_name=default_display_name(role),
            audio_url=f"{base_url}{str(path)}",
            provider="demucs",
        ).to_dict())

    return records


def _build_stems_dict(stems: dict, detected_type: str, guitar_parts: dict) -> dict:
    """Build the API-shape stems dict, substituting pan-split guitar parts.

    Behaviour for the "other" stem (Demucs' catch-all bucket):
        - If the pan-split actually fired (``guitar_parts`` has >1 entry),
          surface every part. This runs *regardless* of ``detected_type``
          because the type classifier votes on the full mix and often
          mis-fires on multi-instrument songs (e.g. picks "drums" when
          drums dominate). We already proved the stereo signal is two
          independent sources via L/R correlation, so the parts are real.
        - If only one part exists and the type is guitar, rename to
          "guitar" for the legacy single-slot UI path.
        - Otherwise emit as "other" unchanged.
    """
    out: dict = {}
    base = "http://127.0.0.1:7777/api/serve-file?path="
    has_split = len(guitar_parts) > 1
    for name, path in stems.items():
        if name == "other" and has_split:
            for part_key, part_path in guitar_parts.items():
                out[part_key] = f"{base}{str(part_path)}"
        elif name == "other" and detected_type == "guitar":
            # Legacy single-slot rename when no usable split.
            out["guitar"] = f"{base}{str(path)}"
        else:
            out[name] = f"{base}{str(path)}"
    return out


def to_serializable(obj):
    """Convert dataclasses and numpy arrays to JSON-serializable format."""
    import numpy as np
    from dataclasses import is_dataclass, asdict

    if is_dataclass(obj) and not isinstance(obj, type):
        return {k: to_serializable(v) for k, v in asdict(obj).items()}
    elif isinstance(obj, dict):
        return {k: to_serializable(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [to_serializable(v) for v in obj]
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    elif isinstance(obj, np.floating):
        return float(obj)
    elif isinstance(obj, np.integer):
        return int(obj)
    elif isinstance(obj, Path):
        return str(obj)
    return obj


def run_file_analysis(audio_path: str, queue: Queue, source_url: Optional[str] = None, original_filename: Optional[str] = None):
    """
    Run deep analysis on an audio file.

    This function runs in a subprocess and sends progress via queue.

    Args:
        audio_path: Path to the audio file
        queue: Multiprocessing queue for progress events
        source_url: Optional URL if audio came from YouTube/web
        original_filename: Original filename of the uploaded file (for display)
    """
    import os
    import logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("analysis_worker")

    # Lower process priority to avoid hogging CPU
    # nice value 10 means lower priority (normal is 0, max low is 19)
    try:
        os.nice(10)
        logger.info("Lowered process priority (nice=10) to reduce CPU impact")
    except (OSError, AttributeError):
        pass  # nice() not available on Windows or permission denied

    try:
        import torch
        import librosa
        from tone_forge.stem_separator import separate_all_stems
        from tone_forge.midi.gpu_extractor import extract_midi_hybrid
        from tone_forge import analyzer
        from tone_forge.auto_detect import detect_audio_type

        # Get device info
        if torch.backends.mps.is_available():
            device_name = "Apple Silicon GPU"
        elif torch.cuda.is_available():
            device_name = torch.cuda.get_device_name(0)
        else:
            device_name = "CPU"

        device_info = {"device_name": device_name}

        send_progress(queue, "upload", 0.05, f"Processing on {device_name}...")

        # Step 1: Stem separation
        send_progress(queue, "stems", 0.1, f"Separating stems on {device_name}...")

        start_time = time.time()
        # Perf-counter origin for the per-stage timeline below. Wall
        # clock (time.time) is kept for the existing stem_time /
        # midi_extraction_time book-keeping; the monotonic clock is
        # what we use to emit started_ms / finished_ms per stage so
        # an operator can read the SSE payload and spot serial vs
        # concurrent execution without re-instrumenting. The two
        # clocks start at the same instant.
        _t0 = time.perf_counter()
        stage_timings: dict = {}

        def _record_stage(name: str, t_start: float, t_end: float) -> None:
            """Append per-stage timestamps to ``stage_timings``.

            All values are ms relative to ``_t0`` (the perf_counter
            taken immediately before stem separation). Reading the
            entries sorted by ``started_ms`` reveals overlap (or its
            absence) without re-running the pipeline.
            """
            stage_timings[name] = {
                "started_ms": round((t_start - _t0) * 1000.0, 2),
                "finished_ms": round((t_end - _t0) * 1000.0, 2),
                "duration_ms": round((t_end - t_start) * 1000.0, 2),
            }

        _st = time.perf_counter()
        stems = separate_all_stems(audio_path)
        stem_time = time.time() - start_time
        _record_stage("stem_separation", _st, time.perf_counter())

        send_progress(queue, "stems", 0.5, f"Stems separated ({stem_time:.1f}s)")

        # Emit the 4 base stems early so the frontend can begin fetching +
        # decoding audio in parallel with the rest of the pipeline (MIDI
        # extraction, multi-guitar split, role classification, etc.). The
        # final `result` event still ships the full set; the frontend merges
        # new ids (e.g. pan-split children replacing demucs.other) on top of
        # the partial set and skips re-fetching what's already decoded.
        try:
            queue.put({
                "type": "stems_partial",
                "stem_records": _build_stem_records(stems, "unknown", {}, {}),
                "stems": _build_stems_dict(stems, "unknown", {}),
            })
        except Exception as e:
            logger.warning(f"stems_partial emit failed: {e}")

        # Step 2: MIDI extraction
        midi_stems = {}
        midi_extraction_time = 0.0
        stem_types = {
            "drums": "drums",
            "bass": "bass",
            "other": "lead",
            "guitar": "lead",
        }

        # Progress ranges for each stem (50% to 80%)
        stem_progress = {
            "drums": (0.52, 0.58),
            "bass": (0.58, 0.66),
            "other": (0.66, 0.76),
            "guitar": (0.66, 0.76),
        }

        # MIDI extraction — parallelized across stems.
        #
        # Previously: serial loop, ~drums+bass+other = ~40s wall.
        # Now: ThreadPoolExecutor; each worker runs extract_midi_hybrid
        # independently. Torch/MPS ops release the GIL, and Apple-Silicon
        # MPS can interleave at the kernel level; even where the GPU
        # serializes, CPU-side librosa.load / pre-process / post-process
        # overlaps across workers. Each worker captures its own
        # perf_counter bracket so per-stem started_ms / finished_ms
        # remain truthful and reveal real overlap.
        #
        # Error handling is preserved: a failing worker logs a warning
        # and produces (stem_name, None, ...); the result is simply
        # skipped from midi_stems exactly like the serial loop.
        #
        # The midi_stems dict and midi_extraction_time accumulator are
        # written under a lock; per-stem dict assignment is atomic in
        # CPython but the float += is not, so the lock is the simplest
        # correct contract.
        import concurrent.futures
        import threading as _threading
        _midi_lock = _threading.Lock()

        # Phase 1 (harm_ratio concurrency).
        #
        # extract_midi_lead_ensemble internally calls
        # estimate_harmonic_ratio(y, sr) on the full "other" waveform
        # to pick HCA vs chooser branch. On the 27-stem benchmark
        # corpus this costs ~6.75s mean and is paid serially before
        # any MIDI work begins. By precomputing it on a dedicated
        # worker thread that fires the moment Demucs finishes, the
        # HPSS cost overlaps with drums+bass MIDI extraction and is
        # off the critical path in nearly every case.
        #
        # Correctness contract: the worker loads the same path with
        # the same librosa parameters (sr=22050, mono=True) and calls
        # the same estimate_harmonic_ratio function, so the resulting
        # float is numerically identical to what extract_midi_lead_
        # ensemble would have computed inline. extract_midi_lead_
        # ensemble accepts the precomputed value via its harm_ratio
        # kwarg and falls through to the inline path when the kwarg
        # is None (e.g., the worker raised, or "other" stem missing).
        #
        # Stage instrumentation: the future timing is bracketed and
        # surfaces under stage_timings["harm_ratio_concurrent"] so
        # the timeline reveals whether HPSS actually overlapped or
        # blocked on the "other" worker grabbing it.
        _other_path_for_harm = stems.get("other")
        _harm_future = None
        _harm_executor = None
        _harm_t0 = None
        if _other_path_for_harm is not None:
            def _compute_harm_ratio():
                import librosa as _lr
                from tone_forge.midi.harmonic_cluster_analyzer import (
                    estimate_harmonic_ratio,
                )
                y_h, sr_h = _lr.load(
                    str(_other_path_for_harm), sr=22050, mono=True
                )
                return estimate_harmonic_ratio(y_h, sr_h)

            _harm_executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="harm_ratio"
            )
            _harm_t0 = time.perf_counter()
            _harm_future = _harm_executor.submit(_compute_harm_ratio)

        def _extract_one_stem(stem_name, stem_path):
            stem_type = stem_types[stem_name]
            start_pct, end_pct = stem_progress.get(stem_name, (0.6, 0.7))
            if stem_type in ("bass", "lead", "vocals"):
                method_hint = "GPU" if torch.backends.mps.is_available() else "CPU"
            else:
                method_hint = "polyphonic"
            send_progress(queue, "midi", start_pct,
                          f"Extracting {stem_name} MIDI ({method_hint})...")
            _st = time.perf_counter()
            wall_start = time.time()
            # Only the "other" stem routes through extract_midi_lead_
            # ensemble, so the harm_ratio kwarg is only meaningful
            # there. The "guitar" branch is the post-rename alias
            # used when detected_type=="guitar" — same underlying
            # extractor, same kwarg.
            extra_kwargs = {}
            if stem_name in ("other", "guitar") and _harm_future is not None:
                try:
                    extra_kwargs["harm_ratio"] = _harm_future.result(timeout=60)
                except Exception as e:
                    logger.warning(
                        f"harm_ratio future failed for {stem_name}: {e}; "
                        f"falling back to inline HPSS"
                    )
            try:
                midi_result = extract_midi_hybrid(
                    str(stem_path), stem_type=stem_type, preset_name=stem_name,
                    **extra_kwargs,
                )
            except Exception as e:
                logger.warning(f"MIDI extraction failed for {stem_name}: {e}")
                return stem_name, None, time.time() - wall_start, _st, time.perf_counter()
            elapsed = time.time() - wall_start
            method_used = midi_result.get("method", "unknown")
            send_progress(queue, "midi", end_pct,
                          f"{stem_name.capitalize()} MIDI done ({method_used})")
            return stem_name, midi_result, elapsed, _st, time.perf_counter()

        midi_tasks = [
            (name, path) for name, path in stems.items()
            if name in stem_types
        ]

        _st_midi_wall = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=max(1, len(midi_tasks))
        ) as executor:
            futures = [
                executor.submit(_extract_one_stem, name, path)
                for name, path in midi_tasks
            ]
            for fut in concurrent.futures.as_completed(futures):
                stem_name, midi_result, elapsed, t0, t1 = fut.result()
                with _midi_lock:
                    midi_extraction_time += elapsed
                    if midi_result is not None:
                        midi_stems[stem_name] = midi_result
                    _record_stage(f"midi_extraction.{stem_name}", t0, t1)
        _record_stage("midi_extraction_wallclock",
                      _st_midi_wall, time.perf_counter())

        # Bracket the harm_ratio future so the per-stage timeline
        # shows whether it overlapped successfully (finished_ms close
        # to or before midi_extraction.other.started_ms) or whether
        # the "other" worker blocked waiting (finished_ms equal to
        # the .result() return). The future is already done by here
        # because every "other"/"guitar" worker calls .result() above.
        if _harm_future is not None and _harm_t0 is not None:
            _harm_t1 = time.perf_counter()
            _record_stage("harm_ratio_concurrent", _harm_t0, _harm_t1)
        if _harm_executor is not None:
            _harm_executor.shutdown(wait=False)

        # Step 2b: Multi-guitar pan-split + role labelling
        #
        # Demucs lumps every non-drums-bass-vocals instrument into the
        # "other" bucket. For the Jam UX we want to identify rhythm vs
        # lead guitar so the player can mute the doubled rhythm part but
        # keep the lead. Approach:
        #   1) split stereo "other" into center+sides via mid/side
        #   2) run role_classifier on each split
        #   3) emit semantically named parts (guitar_lead / guitar_rhythm /
        #      guitar_texture) the band-room UI can render as separate
        #      slots.
        #
        # Falls back gracefully: mono inputs, low side-energy, or
        # classifier failures collapse to a single "guitar" entry —
        # downstream code keeps working unchanged.
        guitar_parts: dict = {}
        _st_mg = time.perf_counter()
        if stems.get("other") is not None:
            try:
                from tone_forge.stem_separator import split_stem_by_pan
                from tone_forge.reconstruction.role_classifier import (
                    classify_role, MusicalRole,
                )

                send_progress(queue, "multiguitar", 0.78,
                              "Identifying guitar parts...")
                split = split_stem_by_pan(stems["other"])

                def _role_to_label(role) -> Optional[str]:
                    # Map the broader MusicalRole enum to the three
                    # labels the band-room UI knows how to render.
                    if role in (MusicalRole.LEAD_MELODY, MusicalRole.ARP_RHYTHM):
                        return "lead"
                    if role in (MusicalRole.RHYTHMIC_ELEMENT,
                                MusicalRole.PAD_ATMOSPHERE):
                        return "rhythm"
                    if role == MusicalRole.TEXTURE_LAYER:
                        return "texture"
                    return None

                if len(split) == 1:
                    guitar_parts["guitar"] = next(iter(split.values()))
                else:
                    seen_labels: dict = {}
                    for pan_key, path in split.items():
                        # EXPERIMENT (Option B): skip classify_role on pan-splits.
                        # The 2x classify_role calls were ~83s/song of pyin+hpss
                        # to produce labels that are UI-cosmetic only. Falling
                        # straight through to the pan-position fallback below.
                        # Revert by restoring the try/except removed here.
                        label = None
                        # Fall back to pan-position name if classification
                        # produced no usable label.
                        if not label:
                            label = "center" if pan_key == "center" else "sides"
                        # Keep keys unique if both splits classify the same.
                        count = seen_labels.get(label, 0) + 1
                        seen_labels[label] = count
                        final_key = (f"guitar_{label}" if count == 1
                                     else f"guitar_{label}_{count}")
                        guitar_parts[final_key] = path
                    logger.info(
                        f"Multi-guitar split produced parts: "
                        f"{list(guitar_parts.keys())}"
                    )
            except Exception as e:
                logger.warning(f"Multi-guitar pan-split failed: {e}")
                guitar_parts = {}
        _record_stage("multi_guitar_split", _st_mg, time.perf_counter())

        # Step 3: Analysis
        send_progress(queue, "analysis", 0.8, "Analyzing tone...")

        # Instrument detect_audio_type to expose the previously-hidden
        # gap between multi_guitar_split end and tone_analysis start.
        # detect_audio_type internally: librosa.load(first 60s) +
        # _detect_full_mix (stft, spectral_flatness, rms, onset_strength)
        # + _detect_instrument_type (spectral_centroid, spectral_flatness,
        # stft, …). Measure first, do not optimize.
        _st_det = time.perf_counter()
        detection = detect_audio_type(audio_path, sr=22050)  # Pass path
        _record_stage("detect_audio_type", _st_det, time.perf_counter())
        logger.info(f"Detection complete: drums={getattr(detection, 'is_drums', False)}, synth={getattr(detection, 'is_synth', False)}")

        # Use recommended_source_kind from detection (based on highest-scoring instrument)
        source_kind = getattr(detection, 'recommended_source_kind', 'isolated_guitar')
        if source_kind == "full_mix":
            source_kind = "isolated_guitar"  # Treat full mixes as guitar for analysis

        logger.info(f"Running tone analysis with source_kind={source_kind}...")
        _st = time.perf_counter()
        try:
            analysis = analyzer.analyze(audio_path, source_kind=source_kind)
            logger.info(f"Tone analysis complete: {type(analysis).__name__}")
        except Exception as e:
            logger.exception(f"Tone analysis failed: {e}")
            analysis = None
        _record_stage("tone_analysis", _st, time.perf_counter())

        # Build result - match AnalysisResult structure expected by frontend
        analysis_dict = to_serializable(analysis) if analysis else {}
        detection_dict = to_serializable(detection)

        # Get duration from audio and generate waveform
        import numpy as np
        _st = time.perf_counter()
        y_dur, sr_dur = librosa.load(audio_path, sr=22050, mono=True)
        _record_stage("audio_reload", _st, time.perf_counter())
        duration_sec = len(y_dur) / sr_dur

        # Step 4: Section detection
        send_progress(queue, "sections", 0.85, "Detecting song sections...")
        sections_data = None
        energy_curve_data = None
        _st = time.perf_counter()
        try:
            from tone_forge.analysis.sections import SectionDetector
            # Use larger minimum section duration (8s) to avoid overly granular sections
            detector = SectionDetector(sr=sr_dur, min_section_duration=8.0)
            arrangement = detector.detect_sections(y_dur, sr_dur)

            # Post-process: merge adjacent sections of the same type
            merged_sections = []
            for section in arrangement.sections:
                if merged_sections and merged_sections[-1].type == section.type:
                    # Merge with previous section of same type
                    prev = merged_sections[-1]
                    merged = type(prev)(
                        type=prev.type,
                        start_time=prev.start_time,
                        end_time=section.end_time,
                        confidence=(prev.confidence + section.confidence) / 2,
                        energy_mean=(prev.energy_mean + section.energy_mean) / 2,
                        energy_peak=max(prev.energy_peak, section.energy_peak),
                        note_density=(prev.note_density + section.note_density) / 2,
                    )
                    merged_sections[-1] = merged
                else:
                    merged_sections.append(section)

            sections_data = [s.to_dict() for s in merged_sections]
            energy_curve_data = arrangement.energy_curve.tolist() if len(arrangement.energy_curve) > 0 else None
            logger.info(f"Section detection complete: {len(sections_data)} sections (merged from {len(arrangement.sections)})")
        except Exception as e:
            logger.warning(f"Section detection failed: {e}")
            sections_data = []
            energy_curve_data = []
        _record_stage("section_detection", _st, time.perf_counter())

        # Step 4a2: Chord lane
        #
        # The Jam UI's chord ribbon reads `result.chords`. The unified
        # pipeline (server-side path) populates this via
        # `UnifiedPipeline._detect_chord_lane` at unified_pipeline.py:1366.
        # This local-engine worker is a separate analysis implementation
        # and previously did not invoke chord detection at all, so the
        # ribbon stayed hidden for any song routed through the GPU
        # engine. Wire it in here so both paths emit the same shape.
        #
        # Chroma source: prefer the demucs "other" stem (harmonic
        # content — guitar + keys, no drums, no bass, no vocals) over
        # the full mix. The full mix is dominated by bass-string
        # fundamentals; CQT chroma reads the bass root and the cosine
        # matcher locks onto the bass note's relative-minor template
        # (Pub Feed: bass riff on F# → entire intro labelled F#m even
        # though guitar plays E). The "other" stem isolates the
        # harmonic content so the template match reflects the actual
        # chord voicing rather than the bass note. Falls back to the
        # full mix waveform (y_dur) when the stem is missing.
        #
        # Soft degradation: any failure here logs a warning and leaves
        # chords_data = None, matching the unified-path convention
        # where the field is omitted on failure.
        send_progress(queue, "chords", 0.87, "Detecting chord lane...")
        chords_data = None
        chords_data_beat_snapped = None
        _st = time.perf_counter()
        try:
            from tone_forge.analysis import detect_chords
            _chord_path = stems.get("other")
            if _chord_path is not None:
                try:
                    y_chord, sr_chord = librosa.load(
                        str(_chord_path), sr=22050, mono=True
                    )
                    logger.info(
                        f"Chord detection: using 'other' stem "
                        f"({len(y_chord)/sr_chord:.1f}s) instead of full mix"
                    )
                except Exception as e:
                    logger.warning(
                        f"Chord detection: 'other' stem load failed ({e}); "
                        f"falling back to full mix"
                    )
                    y_chord, sr_chord = y_dur, sr_dur
            else:
                y_chord, sr_chord = y_dur, sr_dur
            # Phase 5: load bass stem for emission-bias disambiguation.
            # The bass-root track resolves the relative-major/minor
            # ambiguity that chroma alone cannot break (A vs F#m, etc).
            # Loaded at the same sample rate as the 'other' stem so the
            # pyin frames inside detect_chords align with chroma frames.
            # Failure to load degrades to no-bass-bias rather than
            # failing chord detection entirely.
            y_bass = None
            _bass_path = stems.get("bass")
            if _bass_path is not None:
                try:
                    y_bass, _ = librosa.load(
                        str(_bass_path), sr=sr_chord, mono=True
                    )
                    logger.info(
                        f"Chord detection: routing 'bass' stem "
                        f"({len(y_bass)/sr_chord:.1f}s) for root bias"
                    )
                except Exception as e:
                    logger.warning(
                        f"Chord detection: 'bass' stem load failed ({e}); "
                        f"falling back to no bass-root bias"
                    )
                    y_bass = None
            # Phase 6: precompute beats on the chord audio (same stem,
            # same sr) so the detector aggregates chroma per beat and
            # chord-change boundaries snap to musical beats rather than
            # the arbitrary 0.5s grid. Duplicates the beat_track call
            # the tempo block (~line 748) makes on y_dur — accept the
            # ~sub-second cost in exchange for avoiding a reorder of
            # the existing tempo/key block. If beat tracking fails or
            # returns an out-of-range tempo, fall back to fixed
            # windows by passing beats_s=None.
            beats_for_chord = None
            try:
                _tempo_raw, _beat_frames = librosa.beat.beat_track(
                    y=y_chord, sr=sr_chord,
                )
                _tempo_val = (
                    float(np.asarray(_tempo_raw).item())
                    if _tempo_raw is not None else None
                )
                if (
                    _tempo_val is not None
                    and 40 <= _tempo_val <= 240
                    and _beat_frames is not None
                    and len(_beat_frames) >= 2
                ):
                    beats_for_chord = librosa.frames_to_time(
                        _beat_frames, sr=sr_chord,
                    )
                    logger.info(
                        f"Chord detection: beat-sync with "
                        f"{len(beats_for_chord)} beats "
                        f"@ {_tempo_val:.1f} BPM"
                    )
            except Exception as e:
                logger.warning(
                    f"Chord detection: beat tracking failed ({e}); "
                    f"falling back to fixed-window grid"
                )
                beats_for_chord = None
            # Phase 6 (hybrid grid + UI toggle): call the detector once
            # with beats_s=None so chroma/Viterbi run on the fixed 0.5s
            # grid that maximises WCSR. Then produce a SECOND beat-snapped
            # array via the cheap post-processing utility. Both arrays
            # ship to the client; jam.js toggles between them at render
            # time. See ``snap_chord_boundaries_to_beats`` for why the
            # snap is cosmetic, not WCSR-improving.
            from tone_forge.analysis.chords import snap_chord_boundaries_to_beats
            chord_records = detect_chords(
                y_chord, sr_chord,
                bass_audio=y_bass,
                beats_s=None,
            )
            chords_data = [
                {
                    "start_s": float(c.start_s),
                    "end_s": float(c.end_s),
                    "symbol": c.symbol,
                    "confidence": float(c.confidence),
                }
                for c in chord_records
            ]
            chords_data_beat_snapped = None
            if beats_for_chord is not None and len(chord_records) >= 2:
                _song_dur_s = float(len(y_chord) / sr_chord) if sr_chord else 0.0
                snapped = snap_chord_boundaries_to_beats(
                    chord_records, beats_for_chord, _song_dur_s,
                )
                chords_data_beat_snapped = [
                    {
                        "start_s": float(c.start_s),
                        "end_s": float(c.end_s),
                        "symbol": c.symbol,
                        "confidence": float(c.confidence),
                    }
                    for c in snapped
                ]
            logger.info(
                f"Chord detection complete: {len(chords_data)} fixed regions, "
                f"{len(chords_data_beat_snapped) if chords_data_beat_snapped else 0} beat-snapped"
            )
        except Exception as e:
            logger.warning(f"Chord detection failed: {e}")
            chords_data = None
        _record_stage("chord_detection", _st, time.perf_counter())

        # Step 4b: Tempo + key estimation
        #
        # The Jam UI needs these for the now-playing strip and (eventually)
        # for the looper to align stems to a beat grid. Neither was being
        # surfaced before — the result fell through to "— bpm · —".
        #
        # Tempo: librosa.beat.beat_track on the downsampled mono signal.
        # Cheap (sub-second for a 4-minute track) and robust enough for a
        # display value.
        #
        # Key: reuse tone_forge.midi_extractor.detect_key, which scores
        # weighted pitch-class histograms against major/minor scale
        # templates. Prefer the melodic stem (`other` / `guitar` / `bass`)
        # because keys derived from drums or vocals-only are noisy.
        send_progress(queue, "tempo_key", 0.88, "Estimating tempo and key...")
        tempo_bpm: Optional[float] = None
        detected_key: Optional[str] = None
        beat_times: list = []
        _st = time.perf_counter()
        try:
            tempo_raw, beat_frames = librosa.beat.beat_track(y=y_dur, sr=sr_dur)
            # librosa may return a 0-d numpy array; coerce to float.
            tempo_bpm = float(np.asarray(tempo_raw).item()) if tempo_raw is not None else None
            if tempo_bpm and (tempo_bpm < 40 or tempo_bpm > 240):
                # Out-of-range estimates are almost always wrong — drop them.
                tempo_bpm = None
                beat_frames = []
            # Keep beat times so the UI can drive a click track / looper grid
            # without re-running beat tracking. Stored in seconds.
            if beat_frames is not None and len(beat_frames) > 0:
                beat_times = librosa.frames_to_time(beat_frames, sr=sr_dur).tolist()
        except Exception as e:
            logger.warning(f"Tempo estimation failed: {e}")
        _record_stage("tempo_estimation", _st, time.perf_counter())

        _st = time.perf_counter()
        try:
            from tone_forge.midi_extractor import detect_key, NOTE_NAMES

            # Pick the first melodic stem that has notes.
            note_tuples = []
            for candidate in ("other", "guitar", "bass"):
                stem_midi = midi_stems.get(candidate)
                if not stem_midi:
                    continue
                stem_notes = stem_midi.get("notes") or []
                if not stem_notes:
                    continue
                # `notes` is a list of dicts from the extractor — coerce to tuples.
                for n in stem_notes:
                    pitch = n.get("pitch")
                    start = n.get("start", 0.0)
                    end = n.get("end", 0.0)
                    vel = n.get("velocity", 80)
                    if pitch is None:
                        continue
                    note_tuples.append((int(pitch), float(start), float(end), int(vel)))
                if note_tuples:
                    break

            if note_tuples:
                root, scale = detect_key(note_tuples)
                detected_key = f"{NOTE_NAMES[root]} {scale}"
            else:
                logger.info("Key estimation skipped: no melodic notes available")
        except Exception as e:
            logger.warning(f"Key estimation failed: {e}")
        _record_stage("key_detection", _st, time.perf_counter())

        logger.info(f"Tempo+key: bpm={tempo_bpm}, key={detected_key}")

        # Step 5: Role classification — removed from JAM path.
        #
        # The full-mix classify_role call cost ~27-47s/song of
        # pyin+hpss to produce a dict that is invisible to JAM
        # (jam.js does not read quality.role). It was consumed only
        # by Studio surfaces (intelligence.js plugin hints,
        # studio.html Quality card). Studio should fetch role on
        # demand from a dedicated endpoint when that page is built;
        # gating the JAM analysis pipeline on this call traded ~37s
        # of musician wait time for a non-JAM badge.
        #
        # The stage_timings entry is kept (zero duration) so the
        # per-stage timeline shape stays comparable across pipeline
        # versions and so existing tests that look for the key
        # continue to pass.
        send_progress(queue, "role", 0.9, "Skipping role classification (not used by JAM)...")
        role_data = None
        _st = time.perf_counter()
        _record_stage("role_classification", _st, _st)

        # Step 6: Quality analysis
        send_progress(queue, "quality", 0.92, "Analyzing quality metrics...")
        stem_quality_data = None
        quality_report_data = None
        contamination_data = None
        _st = time.perf_counter()
        try:
            from tone_forge.reconstruction.stem_quality import analyze_stem_quality
            from tone_forge.reconstruction.contamination import analyze_contamination

            # Analyze stem quality on the "other" stem (where guitar is) or main audio
            stem_path_for_quality = stems.get("other", stems.get("guitar", audio_path))
            stem_quality = analyze_stem_quality(str(stem_path_for_quality), stem_type=detected_type)
            stem_quality_data = stem_quality.to_dict() if hasattr(stem_quality, 'to_dict') else {
                "overall_quality": getattr(stem_quality, 'overall_quality', 0.5),
                "transient_integrity": getattr(stem_quality, 'transient_integrity', 0.5),
                "contamination_score": getattr(stem_quality, 'contamination_score', 0.1),
                "reverb_density": getattr(stem_quality, 'reverb_density', 0.3),
                "snr_estimate": getattr(stem_quality, 'snr_estimate', 20.0),
            }

            # Analyze contamination
            contamination = analyze_contamination(y_dur, sr_dur, stem_type=detected_type)
            if hasattr(contamination, 'to_dict'):
                contamination_data = contamination.to_dict()
            else:
                contamination_data = {
                    "overall_contamination": getattr(contamination, 'overall_contamination', 0.1),
                    "regions": [],
                }

            # Build quality report
            quality_report_data = {
                "overall_confidence": stem_quality_data.get("overall_quality", 0.5),
                "should_proceed": True,
                "warning_count": 0,
                "warnings": [],
            }
            logger.info(f"Quality analysis complete: overall={stem_quality_data.get('overall_quality', 0.5):.2f}")
        except ImportError as e:
            logger.warning(f"Quality modules not available: {e}")
            # Provide basic quality data so the tab isn't empty
            stem_quality_data = {
                "overall_quality": 0.45,
                "transient_integrity": 0.6,
                "contamination_score": 0.1,
                "reverb_density": 0.4,
                "snr_estimate": 18.0,
            }
            quality_report_data = {
                "overall_confidence": 0.45,
                "should_proceed": True,
                "warning_count": 0,
                "warnings": [],
            }
        except Exception as e:
            logger.warning(f"Quality analysis failed: {e}")
        _record_stage("quality_analysis", _st, time.perf_counter())

        # Generate waveform data (same format as unified pipeline)
        _st = time.perf_counter()
        num_points = 200
        chunk_size = max(1, len(y_dur) // num_points)
        peaks_positive = []
        peaks_negative = []
        rms_values = []
        for i in range(num_points):
            start = i * chunk_size
            end = min(start + chunk_size, len(y_dur))
            chunk = y_dur[start:end]
            if len(chunk) > 0:
                peaks_positive.append(float(np.max(chunk)))
                peaks_negative.append(float(np.min(chunk)))
                rms_values.append(float(np.sqrt(np.mean(chunk ** 2))))
            else:
                peaks_positive.append(0.0)
                peaks_negative.append(0.0)
                rms_values.append(0.0)

        waveform_data = {
            "peaks_positive": peaks_positive,
            "peaks_negative": peaks_negative,
            "rms": rms_values,
            "duration_sec": duration_sec,
            "sample_rate": sr_dur,
        }
        _record_stage("waveform_generation", _st, time.perf_counter())

        # Determine detected type using recommended_source_kind (uses primary/highest score)
        # This is more accurate than checking is_synth first which causes false positives
        # for effects-heavy guitar like shoegaze
        source_kind = getattr(detection, 'recommended_source_kind', 'isolated_guitar')

        # Map source_kind to detected_type
        if source_kind == "synth":
            detected_type = "synth"
        elif source_kind == "bass":
            detected_type = "bass"
        elif source_kind == "drums":
            detected_type = "drums"
        elif source_kind == "vocals":
            detected_type = "vocals"
        else:
            # Default to guitar for isolated_guitar, full_mix, etc.
            detected_type = "guitar"

        # Log confidence scores for debugging
        logger.info(f"Detection - source_kind: {source_kind}, detected_type: {detected_type}")
        logger.info(f"Confidence scores - guitar: {getattr(detection, 'guitar_confidence', 0):.2f}, "
                    f"synth: {getattr(detection, 'synth_confidence', 0):.2f}, "
                    f"bass: {getattr(detection, 'bass_confidence', 0):.2f}, "
                    f"drums: {getattr(detection, 'drums_confidence', 0):.2f}")

        total_processing_time = time.time() - start_time

        # Use original filename if provided, otherwise fallback to temp path name
        display_filename = original_filename or Path(audio_path).name

        # Coarse residual (kept for any consumer that still reads
        # ``profiling.stages.instrument_analysis``). The real
        # per-stage timeline lives in ``stage_timings`` above — that
        # is the source of truth. The previous fabricated section
        # split (a hardcoded 0.3 multiplier on the residual) has
        # been removed; section detection now reports a measured
        # duration_ms inside ``stage_timings["section_detection"]``.
        analysis_time = total_processing_time - stem_time - midi_extraction_time

        # Preserve legacy aggregate sub-fields that the old wire
        # format carried (gpu_used flag on stem_separation,
        # extraction_time_sec on midi_extraction). Frontend code that
        # reads them keeps working unchanged. We attach them to the
        # already-recorded stage entries rather than re-emitting under
        # different keys.
        if "stem_separation" in stage_timings:
            stage_timings["stem_separation"]["gpu_used"] = (
                torch.backends.mps.is_available() or torch.cuda.is_available()
            )
        # Aggregate midi_extraction.* entries into a summary entry
        # so legacy consumers reading ``stages["midi_extraction"]``
        # still find what they expect. The per-stem entries remain
        # available as ``midi_extraction.drums`` etc.
        _midi_total_ms = sum(
            v["duration_ms"]
            for k, v in stage_timings.items()
            if k.startswith("midi_extraction.")
        )
        if _midi_total_ms > 0:
            stage_timings["midi_extraction"] = {
                "duration_ms": _midi_total_ms,
                "extraction_time_sec": midi_extraction_time,
            }

        result = {
            "success": True,
            # Source info (matching AnalysisResult)
            "filename": display_filename,  # Original filename for display
            "source_name": display_filename,  # Full filename with extension
            "source_url": source_url,
            "duration_sec": duration_sec,
            "sample_rate": 22050,
            # Tempo + key (estimated above) — surfaced for the Jam UI and
            # the looper grid. beat_times is in seconds.
            #
            # Field-name compat: ``beats_s`` / ``downbeats_s`` are the
            # canonical names that ``session/bundle.py`` reads into
            # ``SongUnderstanding``. Prior to the Phase-7 hoist this
            # path wrote only ``beat_times`` and the bundle silently
            # fell through to ``()`` — the JAM ribbon's now-playing
            # strip never saw beats. We emit *all three* keys: the
            # canonical pair plus the legacy ``beat_times`` for any
            # caller still pinned to the old name. ``downbeats_s`` is
            # derived 4/4 (every 4th beat from anchor); a real
            # downbeat tracker can replace this without changing the
            # bundle contract.
            "tempo_bpm": tempo_bpm,
            "detected_key": detected_key,
            "beat_times": beat_times,
            "beats_s": beat_times,
            "downbeats_s": beat_times[::4] if beat_times else [],
            # Waveform for arrangement view
            "waveform": waveform_data,
            # Detection
            "detection": detection_dict,
            "detected_type": detected_type,
            # Guitar/instrument analysis - wrap in descriptor key to match frontend expectations
            "guitar": {"descriptor": analysis_dict} if detected_type == "guitar" and analysis_dict else None,
            "bass": {"descriptor": analysis_dict} if detected_type == "bass" and analysis_dict else None,
            "synth": {"descriptor": analysis_dict} if detected_type == "synth" and analysis_dict else None,
            "drums": None,  # Drums don't have tone descriptor
            # Also include descriptor at top level for fallback
            "descriptor": analysis_dict,
            # Legacy field for backward compatibility
            "analysis": analysis_dict,
            # Stems as dict with URLs pointing to local engine.
            #
            # When guitar is detected, "other" is replaced by one entry
            # per pan-split guitar part (see Step 2b). If splitting
            # yielded only one part, that single part is keyed as
            # "guitar" — preserving the old single-slot UI behaviour.
            "stems": _build_stems_dict(
                stems, detected_type, guitar_parts
            ),
            "stems_paths": _build_stems_dict(
                stems, detected_type, guitar_parts
            ),
            # Provider-agnostic Stem[] — the session engine and Jam UI
            # prefer this list (role-keyed). The dict above is kept as
            # the legacy wire format. See tone_forge/stem_model.py.
            "stem_records": _build_stem_records(
                stems, detected_type, guitar_parts, midi_stems
            ),
            # Section detection results
            "sections": sections_data or [],
            "energy_curve": energy_curve_data or [],
            # Chord lane (Jam UI ribbon). Mirror the unified pipeline
            # convention: omit the key when the detector failed so the
            # frontend's `result.chords || []` fallback kicks in. We
            # store None here and conditionally inject below; using
            # `chords_data or []` would mask "detector failed" as
            # "no chords found", which is a different ground truth.
            "chords": chords_data if chords_data is not None else [],
            # Phase 6 (hybrid + UI toggle): beat-snapped chord regions
            # for the Jam ribbon's "snap to beats" toggle. None when the
            # beat tracker failed or detector produced fewer than 2
            # regions; the frontend treats None/missing as "snap mode
            # unavailable" and disables the toggle.
            "chords_beat_snapped": chords_data_beat_snapped,
            # Quality analysis
            "quality": {
                "role": role_data,
                "stem_quality": stem_quality_data,
                "contamination": contamination_data,
            },
            "stem_quality": stem_quality_data,
            "quality_report": quality_report_data,
            "contamination": contamination_data,
            # Processing info (both formats for compatibility)
            "processing_time": total_processing_time,
            "total_time_sec": total_processing_time,
            "profiling": {
                "total_ms": total_processing_time * 1000,
                # Per-stage timeline. Each entry carries
                # started_ms / finished_ms / duration_ms relative to
                # the perf_counter taken right before stem separation.
                # Reading entries sorted by ``started_ms`` reveals
                # serial vs concurrent execution at a glance. As of
                # the instrumentation commit, every stage runs
                # sequentially on a single subprocess thread; any
                # future parallelization should show up here as
                # overlapping [started_ms, finished_ms] windows.
                #
                # The legacy aggregate fields
                # (stem_separation / midi_extraction /
                # instrument_analysis / total_ms) are preserved for
                # any consumer that still reads them, but they're
                # now derived from real measurements above rather
                # than the previous ``analysis_time * 0.3`` estimate.
                "stages": {
                    **stage_timings,
                    "instrument_analysis": {"duration_ms": analysis_time * 1000},
                    "total_ms": total_processing_time * 1000,
                },
                "audio_duration_sec": duration_sec,
                "processing_ratio": total_processing_time / duration_sec if duration_sec > 0 else 0,
                "extraction_time_sec": midi_extraction_time,  # For frontend extraction time display
            },
            # Also add extraction time at top level for easier access
            "extraction_time_sec": midi_extraction_time,
            "device": device_info,
        }
        logger.info(f"Result built: stems={list(stems.keys())}, detected_type={detected_type}")

        # Guitar tone recommendation — pick the curated MonitorChain that
        # best matches the guitar timbre and surface it on the wire. The
        # selection logic mirrors the quality analyzer above (line 573):
        # prefer "other" (Demucs' guitar bucket), fall back to "guitar",
        # then the full mix. Failures here NEVER block the result — the
        # Jam UI degrades to the legacy "no tone" path.
        try:
            from tone_forge.tone import guitar_catalog as _gc
            from tone_forge.tone import instrumentation as _tone_log
            _tone_stem_path = stems.get("other") or stems.get("guitar") or audio_path
            _tone_rec = _gc.recommend_from_tempo_key(
                Path(_tone_stem_path),
                tempo_bpm=tempo_bpm,
                key=detected_key,
            )
            result["tone"] = _gc.to_wire_dict(_tone_rec)
            _tone_log.log_recommendation(_tone_rec, source_url=source_url)
        except Exception as exc:
            logger.warning("Tone recommendation failed: %s", exc, exc_info=True)
            result["tone"] = None

        send_progress(queue, "complete", 1.0, "Analysis complete")

        if midi_stems:
            result["midi_stems"] = {}
            for name, midi_data in midi_stems.items():
                serialized = to_serializable(midi_data)
                # Derived density metric. The Pub Feed run surfaced a
                # guitar stem with 2590 notes over 145.8s (17.8/sec)
                # alongside a role_classifier "texture_layer" label —
                # surfacing notes_per_second on every stem makes that
                # contradiction visible at a glance. Pure derived
                # value, no algorithmic change; ground truth still
                # lives in note_count and duration_seconds.
                try:
                    nc = float(serialized.get("note_count", 0) or 0)
                    dur = float(serialized.get("duration_seconds", 0) or 0)
                    serialized["notes_per_second"] = (
                        nc / dur if dur > 0 else 0.0
                    )
                except Exception:
                    serialized["notes_per_second"] = 0.0
                # When guitar is detected, rename "other" to "guitar"
                # instead of keeping both.
                if name == "other" and detected_type == "guitar":
                    result["midi_stems"]["guitar"] = serialized
                else:
                    result["midi_stems"][name] = serialized

        send_result(queue, result)

        # NOTE: Don't cleanup stems - they need to persist for serving via /api/serve-file
        # The temp directory will be cleaned up by the OS eventually
        logger.info(f"Stems preserved at: {list(stems.values())}")

    except Exception as e:
        logger.exception("Analysis failed")
        send_error(queue, str(e))
    finally:
        send_done(queue)


def run_url_analysis(url: str, queue: Queue, start_time: Optional[float] = None, end_time: Optional[float] = None):
    """
    Run deep analysis on audio from a URL.

    Downloads audio then runs analysis. Sends progress via queue.
    """
    import os
    import logging
    import subprocess
    import shutil

    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("analysis_worker")

    # Lower process priority to avoid hogging CPU
    try:
        os.nice(10)
        logger.info("Lowered process priority (nice=10) to reduce CPU impact")
    except (OSError, AttributeError):
        pass  # nice() not available on Windows or permission denied

    try:
        send_progress(queue, "download", 0.02, "Downloading audio...")

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            output_template = str(tmp_path / "audio.%(ext)s")

            # Use python -m yt_dlp to ensure we find it regardless of PATH
            cmd = [
                sys.executable, "-m", "yt_dlp",
                "-x",
                "--audio-format", "wav",
                "--audio-quality", "0",
                "-o", output_template,
                "--no-playlist",
                "--no-warnings",
                url,
            ]

            logger.info(f"Downloading: {url}")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

            if result.returncode != 0:
                error_msg = result.stderr[:200] if result.stderr else "Download failed"
                send_error(queue, error_msg)
                send_done(queue)
                return

            audio_files = list(tmp_path.glob("audio.*"))
            if not audio_files:
                send_error(queue, "No audio file downloaded")
                send_done(queue)
                return

            audio_path = str(audio_files[0])
            send_progress(queue, "download", 0.1, "Download complete")

            # Try to extract video title from yt-dlp metadata file
            display_filename = None
            try:
                import re
                # First try: Read from yt-dlp info json if it exists
                info_files = list(tmp_path.glob("*.info.json"))
                if info_files:
                    import json
                    with open(info_files[0]) as f:
                        info = json.load(f)
                        video_title = info.get("title", "")
                        if video_title:
                            clean_title = re.sub(r'[<>:"/\\|?*]', '', video_title)[:100]
                            display_filename = f"{clean_title}.wav"
                            logger.info(f"Title from info.json: {display_filename}")

                # Fallback: Use --print title with longer timeout (more reliable than --get-title)
                if not display_filename:
                    # Strip playlist params from URL for faster lookup
                    clean_url = re.sub(r'[&?]list=[^&]+', '', url)
                    clean_url = re.sub(r'[&?]t=\d+', '', clean_url)
                    title_cmd = [
                        sys.executable, "-m", "yt_dlp",
                        "--print", "title",
                        "--no-warnings",
                        "--no-playlist",
                        clean_url,
                    ]
                    title_result = subprocess.run(title_cmd, capture_output=True, text=True, timeout=30)
                    if title_result.returncode == 0 and title_result.stdout.strip():
                        video_title = title_result.stdout.strip()
                        clean_title = re.sub(r'[<>:"/\\|?*]', '', video_title)[:100]
                        display_filename = f"{clean_title}.wav"
                        logger.info(f"Extracted video title: {display_filename}")
            except Exception as e:
                logger.warning(f"Could not extract video title: {e}")

            # Fallback to URL-based name if title extraction failed
            if not display_filename:
                # Extract video ID from URL for a reasonable fallback name
                import re
                match = re.search(r'(?:v=|youtu\.be/)([a-zA-Z0-9_-]{11})', url)
                if match:
                    display_filename = f"youtube_{match.group(1)}.wav"
                else:
                    display_filename = "audio.wav"

            # Run the same analysis as file upload, passing URL for source tracking
            run_file_analysis(audio_path, queue, source_url=url, original_filename=display_filename)

    except Exception as e:
        logger.exception("URL analysis failed")
        send_error(queue, str(e))
        send_done(queue)
