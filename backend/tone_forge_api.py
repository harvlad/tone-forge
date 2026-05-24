"""FastAPI server for Tone Forge.

Endpoints + static file serving:
  GET  /              -> static/index.html
  GET  /static/*      -> static assets
  POST /api/analyze   -> accepts a WAV/MP3, returns descriptor + chain card
  POST /api/analyze-url -> accepts a YouTube URL, downloads audio, returns descriptor + chain

Run:
  cd backend
  uvicorn tone_forge_api:app --reload --port 8000

Then open http://localhost:8000 in a browser.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np


class NumpyJSONEncoder(json.JSONEncoder):
    """JSON encoder that handles numpy types."""
    def default(self, obj):
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super().default(obj)


def _convert_numpy_types(obj):
    """Recursively convert numpy types to native Python types."""
    if isinstance(obj, dict):
        return {k: _convert_numpy_types(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_convert_numpy_types(v) for v in obj]
    elif isinstance(obj, np.bool_):
        return bool(obj)
    elif isinstance(obj, np.integer):
        return int(obj)
    elif isinstance(obj, np.floating):
        # Handle NaN and Inf which aren't valid JSON
        if np.isnan(obj):
            return None
        if np.isinf(obj):
            return None
        return float(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    elif isinstance(obj, float):
        # Handle Python float NaN/Inf too
        if obj != obj:  # NaN check
            return None
        if obj == float('inf') or obj == float('-inf'):
            return None
    return obj


def _compute_waveform_peaks(y: np.ndarray, num_points: int = 1000) -> dict:
    """Compute waveform peaks for visualization.

    Downsamples audio to a fixed number of points showing min/max envelope.

    Args:
        y: Audio samples (mono)
        num_points: Number of points for visualization

    Returns:
        Dict with 'peaks_positive', 'peaks_negative', 'rms' arrays
    """
    if len(y) == 0:
        return {"peaks_positive": [], "peaks_negative": [], "rms": []}

    # Ensure we don't have more points than samples
    num_points = min(num_points, len(y))

    # Compute chunk size
    chunk_size = max(1, len(y) // num_points)
    actual_points = len(y) // chunk_size

    peaks_pos = []
    peaks_neg = []
    rms_values = []

    for i in range(actual_points):
        start = i * chunk_size
        end = start + chunk_size
        chunk = y[start:end]

        peaks_pos.append(float(np.max(chunk)))
        peaks_neg.append(float(np.min(chunk)))
        rms_values.append(float(np.sqrt(np.mean(chunk ** 2))))

    return {
        "peaks_positive": peaks_pos,
        "peaks_negative": peaks_neg,
        "rms": rms_values,
        "sample_rate": None,  # Will be set by caller
        "duration_sec": None,  # Will be set by caller
    }


import asyncio
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from tone_forge import analyzer, helix_translator
from tone_forge import translator
from tone_forge.hardware import Platform

# Reconstruction pipeline for quality-aware analysis
_RECONSTRUCTION_AVAILABLE = False
try:
    from tone_forge.reconstruction.pipeline import get_pipeline, ReconstructionConfig
    _RECONSTRUCTION_AVAILABLE = True
except ImportError:
    pass

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# History Storage
# -----------------------------------------------------------------------------
_HISTORY_FILE = Path(__file__).parent / "data" / "history.json"


def _load_history() -> list[dict]:
    """Load history from JSON file."""
    if not _HISTORY_FILE.exists():
        return []
    try:
        with open(_HISTORY_FILE) as f:
            return json.load(f)
    except Exception:
        return []


def _save_history(history: list[dict]) -> None:
    """Save history to JSON file."""
    _HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2, cls=NumpyJSONEncoder)


def _add_to_history(entry: dict, full_result: dict = None) -> dict:
    """Add an entry to history and return it with ID.

    Args:
        entry: Metadata about the analysis (name, detected_type, etc.)
        full_result: The complete analysis result for reloading later
    """
    history = _load_history()
    entry["id"] = str(uuid.uuid4())[:8]
    entry["timestamp"] = datetime.now().isoformat()
    if full_result:
        entry["result"] = full_result
    history.insert(0, entry)  # Most recent first
    # Keep only last 100 entries
    history = history[:100]
    _save_history(history)
    return entry


def _get_history_item(entry_id: str) -> dict | None:
    """Get a specific history entry by ID."""
    history = _load_history()
    for entry in history:
        if entry.get("id") == entry_id:
            return entry
    return None

# Supported platforms
SUPPORTED_PLATFORMS = ["helix", "pedals", "synth"]


app = FastAPI(title="Tone Forge", version="0.5.0")

_STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


@app.get("/")
async def root() -> FileResponse:
    return FileResponse(_STATIC_DIR / "index.html")


@app.get("/analysis/{analysis_id}")
async def analysis_page(analysis_id: str) -> FileResponse:
    """Serve the app for a specific analysis (shareable URL)."""
    return FileResponse(_STATIC_DIR / "index.html")


_ACCEPTED_SUFFIXES = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aiff", ".aif", ".webm"}


@app.post("/api/analyze")
async def analyze_endpoint(
    file: UploadFile = File(...),
    source_kind: str = "auto",  # Auto-detect by default
    platform: str = "auto",
    extract_midi: bool = True,  # Extract MIDI by default
) -> JSONResponse:
    """Analyze an uploaded audio clip and return descriptor + signal chain recommendations.

    Auto-detects:
    - Whether it's a full mix or isolated instrument
    - Whether it's guitar, synth, or other

    Returns recommendations for all platforms (Helix, Pedals, Synth).
    """
    suffix = Path(file.filename or "").suffix.lower()
    if suffix and suffix not in _ACCEPTED_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type {suffix}. Accepted: {sorted(_ACCEPTED_SUFFIXES)}.",
        )

    # Write to a temp file so librosa can load it from a real path.
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix or ".wav") as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = Path(tmp.name)

    try:
        from tone_forge import synth_analyzer, auto_detect

        # Auto-detect audio type (now includes bass and drums)
        detection = auto_detect.detect_audio_type(str(tmp_path))

        # Use detection results - can have multiple True values for full mixes
        is_synth = detection.is_synth
        is_bass = detection.is_bass
        is_drums = detection.is_drums
        is_guitar = detection.is_guitar
        actual_source_kind = detection.recommended_source_kind

        # Mutual exclusion: skip guitar analysis if synth is clearly dominant
        # This prevents synthwave from getting amp/cab recommendations
        skip_guitar = (
            detection.synth_confidence > 0.5 and
            detection.guitar_confidence < 0.25
        )
        if skip_guitar:
            is_guitar = False
            logger.info("Skipping guitar analysis - synth dominant (synth: %.2f, guitar: %.2f)",
                       detection.synth_confidence, detection.guitar_confidence)

        # Initialize result containers
        synth_result = None
        guitar_result = None
        bass_result = None
        drums_result = None

        # Analyze ALL detected instrument types (not just primary)
        # This allows showing multiple tabs for full mixes

        if is_drums:
            from tone_forge import drum_analyzer
            drum_desc = drum_analyzer.analyze_drums(str(tmp_path))
            drums_result = {
                "descriptor": _drum_descriptor_to_dict(drum_desc),
                "machine_match": drum_analyzer.match_drum_machine(drum_desc),
                "tweak_hints": _generate_drum_hints(drum_desc),
            }

        if is_synth:
            synth_desc = synth_analyzer.analyze_synth(str(tmp_path))
            synth_result = {
                "descriptor": synth_desc.to_dict(),
                "chain": [],
                "tweak_hints": _generate_synth_hints(synth_desc),
            }

        if is_bass:
            from tone_forge import bass_analyzer
            bass_desc = bass_analyzer.analyze_bass(str(tmp_path))
            bass_result = {
                "descriptor": _bass_descriptor_to_dict(bass_desc),
                "recommendations": _get_bass_recommendations(bass_desc),
                "tweak_hints": _generate_bass_hints(bass_desc),
            }

        if is_guitar:
            if source_kind != "auto":
                actual_source_kind = source_kind

            # Run quality analysis if reconstruction pipeline is available
            stem_quality = None
            contamination = None
            quality_warnings = []

            if _RECONSTRUCTION_AVAILABLE:
                try:
                    import librosa
                    # Load audio for quality analysis
                    audio, sr = librosa.load(str(tmp_path), sr=22050, mono=True)

                    # Run quality analysis (fast config - no MIDI extraction)
                    config = ReconstructionConfig(
                        extract_midi=False,  # We handle MIDI separately
                        analyze_continuity=False,  # Skip expensive analysis
                    )
                    pipeline = get_pipeline(config)
                    analysis, quality_report = pipeline.analyze_only(
                        audio=audio,
                        sr=sr,
                        stem_type="guitar",
                    )

                    stem_quality = analysis.stem_quality
                    contamination = analysis.contamination

                    # Collect warnings for the user
                    if quality_report and quality_report.warnings:
                        quality_warnings = [
                            {
                                "level": w.level.value if hasattr(w.level, 'value') else str(w.level),
                                "message": w.message,
                                "recommendation": w.recommendation,
                            }
                            for w in quality_report.warnings
                        ]

                    logger.debug(
                        f"Quality analysis: overall={quality_report.overall_confidence:.2f}, "
                        f"warnings={len(quality_warnings)}"
                    )
                except Exception as e:
                    logger.warning(f"Quality analysis failed (non-fatal): {e}")

            descriptor = analyzer.analyze(
                str(tmp_path),
                source_kind=actual_source_kind if actual_source_kind != "synth" else "isolated_guitar",
                display_name=file.filename or tmp_path.name,
                stem_quality=stem_quality,
                contamination=contamination,
            )

            helix_card = helix_translator.translate(descriptor)
            helix_chain = [asdict(p) for p in helix_card.picks]

            pedal_card = translator.translate(descriptor, platform="pedals")
            pedal_chain = [asdict(p) for p in pedal_card.picks]

            guitar_result = {
                "descriptor": descriptor.to_dict(),
                "tweak_hints": helix_card.tweak_hints,
                "platforms": {
                    "helix": helix_chain,
                    "pedals": pedal_chain,
                },
                "quality_warnings": quality_warnings,
            }

        # Also provide synth analysis for non-drum audio (for tab switching)
        if not is_drums and synth_result is None:
            synth_desc = synth_analyzer.analyze_synth(str(tmp_path))
            synth_result = {
                "descriptor": synth_desc.to_dict(),
                "chain": [],
                "tweak_hints": _generate_synth_hints(synth_desc),
            }

        # Extract MIDI if requested
        midi_result = None
        if extract_midi:
            try:
                from tone_forge import midi_extractor
                midi_data = midi_extractor.extract_midi(
                    str(tmp_path),
                    preset_name=file.filename or "Extracted MIDI",
                )
                midi_result = {
                    "filename": midi_data.filename,
                    "content": midi_data.content,
                    "note_count": midi_data.note_count,
                    "duration_seconds": midi_data.duration_seconds,
                    "tempo_bpm": midi_data.tempo_bpm,
                    "pitch_range": {
                        "lowest": midi_data.pitch_range[0],
                        "highest": midi_data.pitch_range[1],
                    },
                }
                # Include provenance if available
                if midi_data.provenance:
                    midi_result["provenance"] = midi_data.provenance
            except Exception as e:
                logger.warning(f"MIDI extraction failed: {e}")
                # Non-fatal - continue without MIDI

    except Exception as e:  # noqa: BLE001 - surface anything to the client
        raise HTTPException(status_code=500, detail=f"Analysis failed: {e}") from e
    finally:
        tmp_path.unlink(missing_ok=True)

    # Determine the primary detected type based on what was analyzed
    # Priority for default tab: drums > bass > guitar > synth
    if drums_result:
        detected_type = "drums"
    elif bass_result:
        detected_type = "bass"
    elif guitar_result:
        detected_type = "guitar"
    elif synth_result:
        detected_type = "synth"
    else:
        detected_type = "guitar"

    # Build unified response with all platform results
    response = {
        "detected_type": detected_type,
        "detection": {
            "is_full_mix": detection.is_full_mix,
            "is_guitar": detection.is_guitar,
            "is_synth": detection.is_synth,
            "is_bass": detection.is_bass,
            "is_drums": detection.is_drums,
            "summary": detection.summary,
            "confidence": {
                "mix": detection.mix_confidence,
                "instrument": detection.instrument_confidence,
                "drums": detection.drums_confidence,
                "synth": detection.synth_confidence,
                "bass": detection.bass_confidence,
                "guitar": detection.guitar_confidence,
            },
        },
        "synth": synth_result,
    }

    if guitar_result:
        response["guitar"] = guitar_result
    if bass_result:
        response["bass"] = bass_result
    if drums_result:
        response["drums"] = drums_result
    if midi_result:
        response["midi"] = midi_result

    # For backward compatibility - use detected_type consistently
    response["type"] = detected_type
    if detected_type == "drums" and drums_result:
        response["descriptor"] = drums_result["descriptor"]
        response["chain"] = []
        response["tweak_hints"] = drums_result["tweak_hints"]
    elif detected_type == "bass" and bass_result:
        response["descriptor"] = bass_result["descriptor"]
        response["chain"] = bass_result.get("recommendations", [])
        response["tweak_hints"] = bass_result["tweak_hints"]
    elif detected_type == "synth" and synth_result:
        response["descriptor"] = synth_result["descriptor"]
        response["chain"] = []
        response["tweak_hints"] = synth_result["tweak_hints"]
    elif guitar_result:
        response["descriptor"] = guitar_result["descriptor"]
        response["chain"] = guitar_result["platforms"].get(platform if platform != "auto" else "helix", guitar_result["platforms"]["helix"])
        response["tweak_hints"] = guitar_result["tweak_hints"]
        response["platform"] = platform if platform != "auto" else "helix"

    # Get duration from whichever result is available
    duration = None
    if guitar_result:
        duration = guitar_result["descriptor"].get("source", {}).get("duration_sec")
    elif bass_result:
        duration = bass_result["descriptor"].get("source", {}).get("duration_sec")
    elif drums_result:
        duration = drums_result["descriptor"].get("source", {}).get("duration_sec")
    elif synth_result:
        duration = synth_result["descriptor"].get("duration_sec")

    # Save to history with full result for reloading
    history_entry = _add_to_history({
        "name": file.filename or "Uploaded file",
        "detected_type": detected_type,
        "summary": response.get("detection", {}).get("summary", ""),
        "amp_family": guitar_result["descriptor"].get("amp", {}).get("family") if guitar_result else (bass_result["descriptor"].get("amp", {}).get("family") if bass_result else None),
        "gain": guitar_result["descriptor"].get("amp", {}).get("gain") if guitar_result else None,
        "duration": duration,
    }, full_result=response)

    # Include history ID for shareable URL
    response["history_id"] = history_entry["id"]

    return JSONResponse(_convert_numpy_types(response))


@app.post("/api/analyze-stream")
async def analyze_stream_endpoint(
    file: UploadFile = File(...),
    source_kind: str = Form("auto"),
    platform: str = Form("auto"),
    extract_midi: str = Form("true"),  # Form data is string
    fast_mode: str = Form("true"),  # Set to "false" for deep analysis with stem separation
) -> StreamingResponse:
    """Analyze with SSE progress streaming for real-time status updates.

    Args:
        fast_mode: If "true" (default), skip stem separation for speed.
                   If "false", perform deep analysis with stem separation and MIDI extraction.
    """
    # Convert string form params to booleans
    extract_midi_bool = extract_midi.lower() not in ("false", "0", "no")
    fast_mode_bool = fast_mode.lower() not in ("false", "0", "no")

    async def generate():
        def send_progress(message: str, percent: int = 0):
            return f"data: {json.dumps({'type': 'progress', 'message': message, 'percent': percent})}\n\n"

        suffix = Path(file.filename or "").suffix.lower()
        if suffix and suffix not in _ACCEPTED_SUFFIXES:
            yield f"data: {json.dumps({'type': 'error', 'message': f'Unsupported file type {suffix}'})}\n\n"
            return

        yield send_progress("Uploading file...", 5)
        await asyncio.sleep(0)

        # Write to temp file
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix or ".wav") as tmp:
            shutil.copyfileobj(file.file, tmp)
            tmp_path = Path(tmp.name)

        try:
            yield send_progress("Loading audio...", 15)
            await asyncio.sleep(0)

            from tone_forge import synth_analyzer, auto_detect

            yield send_progress("Detecting instrument types...", 25)
            await asyncio.sleep(0)

            detection = auto_detect.detect_audio_type(str(tmp_path))
            is_synth = detection.is_synth
            is_bass = detection.is_bass
            is_drums = detection.is_drums
            is_guitar = detection.is_guitar
            actual_source_kind = detection.recommended_source_kind

            # Mutual exclusion: skip guitar analysis if synth is clearly dominant
            skip_guitar = (
                detection.synth_confidence > 0.5 and
                detection.guitar_confidence < 0.25
            )
            if skip_guitar:
                is_guitar = False
                logger.info("Skipping guitar analysis - synth dominant")

            # Report detection
            detected = []
            if is_drums: detected.append("drums")
            if is_synth: detected.append("synth")
            if is_bass: detected.append("bass")
            if is_guitar: detected.append("guitar")
            yield send_progress(f"Detected: {', '.join(detected) or 'unknown'}", 30)
            await asyncio.sleep(0)

            # Deep analysis: stem separation and MIDI extraction
            stem_paths = {}
            stems_paths_output = {}
            midi_stems = None
            guitar_audio_path = tmp_path  # Default to original file

            if not fast_mode_bool and detection.is_full_mix:
                yield send_progress("Separating stems (GPU)...", 35)
                await asyncio.sleep(0)

                try:
                    from tone_forge import stem_separator
                    import concurrent.futures
                    loop = asyncio.get_event_loop()

                    def run_stem_separation():
                        return stem_separator.separate_all_stems(
                            str(tmp_path),
                            model_name="htdemucs_6s",
                        )

                    with concurrent.futures.ThreadPoolExecutor() as pool:
                        stem_paths = await loop.run_in_executor(pool, run_stem_separation)

                    # Prepare output paths for client
                    for stem_name, stem_path in stem_paths.items():
                        stems_paths_output[stem_name] = str(stem_path)

                    # Use guitar stem for guitar analysis if available
                    if "guitar" in stem_paths:
                        guitar_audio_path = Path(stem_paths["guitar"])
                        actual_source_kind = "isolated_guitar"
                    elif "other" in stem_paths:
                        guitar_audio_path = Path(stem_paths["other"])
                        actual_source_kind = "isolated_guitar"

                    logger.info(f"Stem separation complete: {list(stem_paths.keys())}")

                except Exception as e:
                    logger.warning(f"Stem separation failed: {e}")

                # Extract MIDI from stems
                if stem_paths and extract_midi_bool:
                    yield send_progress("Extracting MIDI from stems...", 38)
                    await asyncio.sleep(0)

                    try:
                        from tone_forge.midi_extractor import extract_midi
                        import concurrent.futures
                        import base64

                        midi_stems = {}
                        midi_dir = Path(tempfile.mkdtemp(prefix="toneforge_midi_"))

                        for stem_name, stem_path in stem_paths.items():
                            if stem_name in ("drums", "bass", "guitar", "piano", "vocals", "other"):
                                try:
                                    def extract_stem_midi(path=stem_path, name=stem_name, out_dir=midi_dir):
                                        result = extract_midi(str(path), stem_type=name)
                                        if result and result.content:
                                            # Save base64 content to file
                                            midi_path = out_dir / f"{name}.mid"
                                            midi_data = base64.b64decode(result.content)
                                            with open(midi_path, 'wb') as f:
                                                f.write(midi_data)
                                            return str(midi_path)
                                        return None

                                    with concurrent.futures.ThreadPoolExecutor() as pool:
                                        midi_path = await loop.run_in_executor(pool, extract_stem_midi)

                                    if midi_path:
                                        midi_stems[stem_name] = midi_path
                                except Exception as e:
                                    logger.warning(f"MIDI extraction failed for {stem_name}: {e}")

                        logger.info(f"MIDI extraction complete: {list(midi_stems.keys())}")

                    except Exception as e:
                        logger.warning(f"MIDI extraction failed: {e}")

            synth_result = None
            guitar_result = None
            bass_result = None
            drums_result = None

            # Analyze each detected type
            if is_drums:
                yield send_progress("Analyzing drums...", 40)
                await asyncio.sleep(0)
                from tone_forge import drum_analyzer
                drum_desc = drum_analyzer.analyze_drums(str(tmp_path))
                drums_result = {
                    "descriptor": _drum_descriptor_to_dict(drum_desc),
                    "machine_match": drum_analyzer.match_drum_machine(drum_desc),
                    "tweak_hints": _generate_drum_hints(drum_desc),
                }

            if is_synth:
                yield send_progress("Analyzing synth parameters...", 50)
                await asyncio.sleep(0)
                synth_desc = synth_analyzer.analyze_synth(str(tmp_path))
                synth_result = {
                    "descriptor": synth_desc.to_dict(),
                    "chain": [],
                    "tweak_hints": _generate_synth_hints(synth_desc),
                }

            if is_bass:
                yield send_progress("Analyzing bass tone...", 60)
                await asyncio.sleep(0)
                from tone_forge import bass_analyzer
                # Use separated bass stem if available
                bass_audio_path = stem_paths.get("bass", tmp_path)
                bass_desc = bass_analyzer.analyze_bass(str(bass_audio_path))
                bass_result = {
                    "descriptor": _bass_descriptor_to_dict(bass_desc),
                    "recommendations": _get_bass_recommendations(bass_desc),
                    "tweak_hints": _generate_bass_hints(bass_desc),
                }

            if is_guitar:
                yield send_progress("Analyzing guitar tone...", 70)
                await asyncio.sleep(0)

                if source_kind != "auto":
                    actual_source_kind = source_kind

                from tone_forge import analyzer
                # Use separated guitar stem if available (set in stem separation above)
                descriptor = analyzer.analyze(str(guitar_audio_path), source_kind=actual_source_kind)

                yield send_progress("Generating recommendations...", 85)
                await asyncio.sleep(0)

                helix_card = helix_translator.translate(descriptor)
                pedals_card = translator.translate_for_pedals(descriptor)

                # Convert to list of dicts (same format as non-streaming endpoint)
                helix_chain = [asdict(p) for p in helix_card.picks]
                pedals_chain = [asdict(p) for p in pedals_card.picks]

                guitar_result = {
                    "descriptor": descriptor.to_dict(),
                    "platforms": {
                        "helix": helix_chain,
                        "pedals": pedals_chain,
                    },
                    "tweak_hints": helix_card.tweak_hints,
                }

            # Also provide synth analysis for non-synth audio
            if not is_synth and synth_result is None:
                yield send_progress("Running synth parameter extraction...", 90)
                await asyncio.sleep(0)
                synth_desc = synth_analyzer.analyze_synth(str(tmp_path))
                synth_result = {
                    "descriptor": synth_desc.to_dict(),
                    "chain": [],
                    "tweak_hints": _generate_synth_hints(synth_desc),
                }

            yield send_progress("Finalizing...", 95)
            await asyncio.sleep(0)

            # Determine detected type priority
            if drums_result:
                detected_type = "drums"
            elif bass_result:
                detected_type = "bass"
            elif is_synth:
                detected_type = "synth"
            else:
                detected_type = "guitar"

            # Build response
            response = {
                "filename": file.filename,
                "source_kind": actual_source_kind,
                "detected_type": detected_type,
                "detection": {
                    "is_full_mix": detection.is_full_mix,
                    "is_synth": detection.is_synth,
                    "is_bass": detection.is_bass,
                    "is_drums": detection.is_drums,
                    "is_guitar": detection.is_guitar,
                    "summary": detection.summary,
                    "confidence": {
                        "mix": detection.mix_confidence,
                        "instrument": detection.instrument_confidence,
                        "drums": detection.drums_confidence,
                        "synth": detection.synth_confidence,
                        "bass": detection.bass_confidence,
                        "guitar": detection.guitar_confidence,
                    },
                },
            }

            if guitar_result:
                response["guitar"] = guitar_result
            if synth_result:
                response["synth"] = synth_result
            if bass_result:
                response["bass"] = bass_result
            if drums_result:
                response["drums"] = drums_result

            # Add deep analysis data (stem paths and MIDI)
            if stems_paths_output:
                response["stems_paths"] = stems_paths_output
                response["deep_analysis"] = True
            if midi_stems:
                response["midi_stems"] = midi_stems

            # Set primary descriptor/chain based on detected type
            if detected_type == "drums" and drums_result:
                response["descriptor"] = drums_result["descriptor"]
                response["chain"] = drums_result.get("machine_match", {})
                response["tweak_hints"] = drums_result["tweak_hints"]
            elif detected_type == "bass" and bass_result:
                response["descriptor"] = bass_result["descriptor"]
                response["chain"] = bass_result.get("recommendations", [])
                response["tweak_hints"] = bass_result["tweak_hints"]
            elif detected_type == "synth" and synth_result:
                response["descriptor"] = synth_result["descriptor"]
                response["chain"] = synth_result.get("chain", [])
                response["tweak_hints"] = synth_result["tweak_hints"]
            elif guitar_result:
                response["descriptor"] = guitar_result["descriptor"]
                response["chain"] = guitar_result["platforms"]["helix"]["picks"]
                response["tweak_hints"] = guitar_result.get("tweak_hints", [])

            # Get duration
            duration = None
            if guitar_result:
                duration = guitar_result["descriptor"].get("source", {}).get("duration_sec")
            elif synth_result:
                duration = synth_result["descriptor"].get("duration_sec")
            elif bass_result:
                duration = bass_result["descriptor"].get("source", {}).get("duration_sec")
            elif drums_result:
                duration = drums_result["descriptor"].get("source", {}).get("duration_sec")

            # Add to history
            history_entry = _add_to_history({
                "name": file.filename or "Uploaded file",
                "detected_type": detected_type,
                "summary": response.get("detection", {}).get("summary", ""),
                "amp_family": guitar_result["descriptor"].get("amp", {}).get("family") if guitar_result else None,
                "gain": guitar_result["descriptor"].get("amp", {}).get("gain") if guitar_result else None,
                "duration": duration,
            }, full_result=response)

            response["history_id"] = history_entry["id"]

            yield send_progress("Analysis complete", 100)
            await asyncio.sleep(0)

            # Send final result
            yield f"data: {json.dumps({'type': 'result', 'data': _convert_numpy_types(response)})}\n\n"

        except Exception as e:
            logger.exception("Stream analysis failed")
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        finally:
            tmp_path.unlink(missing_ok=True)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _generate_synth_hints(desc) -> list[str]:
    """Generate tweak hints for synth sounds."""
    hints = []

    if desc.oscillator.type == "saw":
        hints.append("Start with a sawtooth oscillator for this buzzy, harmonically-rich tone.")
    elif desc.oscillator.type == "square":
        hints.append("Use a square/pulse wave oscillator for this hollow, woody character.")
    elif desc.oscillator.type == "sine":
        hints.append("A pure sine wave will get you close to this smooth, fundamental-heavy tone.")

    if desc.oscillator.num_voices > 1:
        hints.append(f"Add unison with {desc.oscillator.num_voices} voices and ~{desc.oscillator.detune:.0f} cents detune for width.")

    if desc.filter.cutoff_normalized < 0.7:
        hints.append(f"Low-pass filter around {desc.filter.cutoff_hz:.0f}Hz gives this muffled character.")

    if desc.filter.resonance > 0.3:
        hints.append("Add some filter resonance for that characteristic 'quack'.")

    if desc.amp_envelope.attack_ms > 50:
        hints.append(f"Slow attack (~{desc.amp_envelope.attack_ms:.0f}ms) creates the pad-like swell.")
    elif desc.amp_envelope.attack_ms < 10:
        hints.append("Keep attack very short for punchy, percussive response.")

    if desc.lfo and desc.lfo.rate_hz > 0:
        hints.append(f"LFO at ~{desc.lfo.rate_hz:.1f}Hz modulating {desc.lfo.target} creates the movement.")

    if desc.has_chorus:
        hints.append("Chorus effect adds the stereo width and shimmer.")

    if desc.has_reverb:
        hints.append("Add reverb for the ambient, spacious quality.")

    return hints


def _generate_bass_hints(desc) -> list[str]:
    """Generate tweak hints for bass sounds."""
    hints = []

    amp_family = desc.amp.family
    if amp_family == "ampeg_svt":
        hints.append("Classic Ampeg SVT tone - try driving the preamp for that signature growl.")
    elif amp_family == "darkglass":
        hints.append("Modern Darkglass tone - blend clean and dirty for clarity with grit.")
    elif amp_family == "fender_bassman":
        hints.append("Fender Bassman provides clean headroom with vintage sparkle.")

    if desc.amp.gain > 0.5:
        hints.append(f"Significant overdrive detected ({desc.amp.gain:.0%}) - consider a dedicated bass drive pedal.")

    if desc.technique == "slap":
        hints.append("Slap technique detected - boost high mids (~2-3kHz) for pop and cut lows for tightness.")
    elif desc.technique == "pick":
        hints.append("Pick attack detected - a slight mid boost brings out the percussive quality.")
    elif desc.technique == "fretless":
        hints.append("Fretless character - emphasize mids for that 'mwah' and consider subtle chorus.")

    if desc.effects.compressor > 0.4:
        hints.append("Heavy compression detected - try a bass compressor with slow attack to preserve transients.")

    if desc.effects.octaver > 0.3:
        hints.append("Sub-octave detected - an octave pedal like the Boss OC-3 or EHX POG will recreate this.")

    return hints


def _generate_drum_hints(desc) -> list[str]:
    """Generate tweak hints for drum sounds."""
    hints = []

    machine = desc.matched_machine
    if machine == "tr808":
        hints.append("TR-808 style - long kick decay, snappy snares, sizzly hats.")
    elif machine == "tr909":
        hints.append("TR-909 style - punchy kicks with attack, crisp sample-based hats.")
    elif machine == "sp1200":
        hints.append("SP-1200 style - crunchy lo-fi character from 12-bit sampling.")

    if desc.kick.sub_presence > 0.6:
        hints.append(f"Heavy sub-bass in the kick - tune around {desc.kick.pitch_hz:.0f}Hz for maximum impact.")

    if desc.snare.noise > 0.6:
        hints.append("Snare has lots of noise/sizzle - real snare wires or white noise layering.")

    if desc.overall.compression > 0.5:
        hints.append("Drums are heavily compressed - parallel compression or a bus compressor.")

    if desc.overall.swing > 0.15:
        hints.append(f"Swing detected (~{desc.overall.swing:.0%}) - adjust your sequencer's swing/groove.")

    if desc.overall.tempo_bpm > 0:
        hints.append(f"Tempo estimated at ~{desc.overall.tempo_bpm:.0f} BPM.")

    return hints


def _bass_descriptor_to_dict(desc) -> dict:
    """Convert BassDescriptor to dict for JSON response."""
    return {
        "source": {
            "kind": desc.source.kind,
            "duration_sec": float(desc.source.duration_sec),
            "sample_rate": int(desc.source.sample_rate),
            "filename": desc.source.filename,
        },
        "technique": desc.technique,
        "amp": {
            "family": desc.amp.family,
            "gain": float(desc.amp.gain),
            "voicing": {
                "bass": float(desc.amp.voicing.bass),
                "low_mid": float(desc.amp.voicing.low_mid),
                "mid": float(desc.amp.voicing.mid),
                "treble": float(desc.amp.voicing.treble),
            },
            "alternates": desc.amp.alternates,
        },
        "cab": {
            "configuration": desc.cab.configuration,
            "speaker_size": desc.cab.speaker_size,
            "character": desc.cab.character,
        },
        "effects": {
            "compressor": float(desc.effects.compressor),
            "overdrive": float(desc.effects.overdrive),
            "chorus": float(desc.effects.chorus),
            "octaver": float(desc.effects.octaver),
            "envelope_filter": float(desc.effects.envelope_filter),
        },
        "confidence": {
            "amp_family": float(desc.confidence.amp_family),
            "gain": float(desc.confidence.gain),
            "cab": float(desc.confidence.cab),
            "technique": float(desc.confidence.technique),
        },
    }


def _drum_descriptor_to_dict(desc) -> dict:
    """Convert DrumDescriptor to dict for JSON response."""
    return {
        "source": {
            "kind": desc.source.kind,
            "duration_sec": float(desc.source.duration_sec),
            "sample_rate": int(desc.source.sample_rate),
            "filename": desc.source.filename,
        },
        "kick": {
            "pitch_hz": float(desc.kick.pitch_hz),
            "decay_ms": float(desc.kick.decay_ms),
            "saturation": float(desc.kick.saturation),
            "sub_presence": float(desc.kick.sub_presence),
            "click": float(desc.kick.click),
        },
        "snare": {
            "pitch_hz": float(desc.snare.pitch_hz),
            "noise": float(desc.snare.noise),
            "snap": float(desc.snare.snap),
            "decay_ms": float(desc.snare.decay_ms),
            "body": float(desc.snare.body),
        },
        "hihat": {
            "open_ratio": float(desc.hihat.open_ratio),
            "open_closed_ratio": float(desc.hihat.open_ratio),  # Alias for frontend
            "decay_ms": float(desc.hihat.decay_ms),
            "brightness": float(desc.hihat.brightness),
            "sizzle": float(desc.hihat.sizzle),
        },
        "overall": {
            "tempo_bpm": float(desc.overall.tempo_bpm),
            "swing": float(desc.overall.swing),
            "compression": float(desc.overall.compression),
            "saturation": float(desc.overall.saturation),
            "style": desc.overall.style,
        },
        # Top-level aliases for frontend compatibility
        "tempo_bpm": float(desc.overall.tempo_bpm),
        "swing": float(desc.overall.swing),
        "compression": float(desc.overall.compression),
        "matched_machine": desc.matched_machine,
        "confidence": {
            "tempo": float(desc.confidence.tempo),
            "style": float(desc.confidence.style),
            "kick": float(desc.confidence.kick),
            "snare": float(desc.confidence.snare),
        },
    }


def _get_bass_recommendations(desc) -> list[dict]:
    """Get bass gear recommendations based on descriptor."""
    import json
    bass_blocks_path = Path(__file__).parent / "data" / "bass_blocks.json"
    if not bass_blocks_path.exists():
        return []

    with open(bass_blocks_path) as f:
        catalog = json.load(f)

    recommendations = []
    amp_family = desc.amp.family

    # Find matching amp
    for amp in catalog.get("amps", []):
        if amp_family in amp.get("families", []):
            recommendations.append({
                "slot": "amp",
                "category": "amp",
                "display": amp["display"],
                "models": amp.get("models", ""),
                "rationale": f"Matches detected {amp_family.replace('_', ' ')} tone",
                "price_estimate": amp.get("price_estimate", ""),
                "params": {
                    "gain": float(round(float(desc.amp.gain) * 10, 1)),
                    "bass": float(round(float(desc.amp.voicing.bass) * 10, 1)),
                    "mid": float(round(float(desc.amp.voicing.mid) * 10, 1)),
                    "treble": float(round(float(desc.amp.voicing.treble) * 10, 1)),
                },
            })
            break

    # Add cab recommendation
    for cab in catalog.get("cabs", []):
        if cab.get("config") == desc.cab.configuration or cab.get("character") == desc.cab.character:
            recommendations.append({
                "slot": "cab",
                "category": "cab",
                "display": cab["display"],
                "models": cab.get("models", ""),
                "rationale": f"{desc.cab.configuration} configuration with {desc.cab.character} character",
                "price_estimate": cab.get("price_estimate", ""),
            })
            break

    # Add drive if overdrive detected
    if float(desc.effects.overdrive) > 0.2:
        for drive in catalog.get("drives", []):
            recommendations.append({
                "slot": "drive",
                "category": "drive",
                "display": drive["display"],
                "models": drive.get("models", ""),
                "rationale": "Adds grit and definition to your bass tone",
                "price_estimate": drive.get("price_estimate", ""),
            })
            break

    # Add compressor if compression detected
    if float(desc.effects.compressor) > 0.3:
        for comp in catalog.get("compressors", []):
            recommendations.append({
                "slot": "compressor",
                "category": "compressor",
                "display": comp["display"],
                "models": comp.get("models", ""),
                "rationale": "Tightens dynamics and adds sustain",
                "price_estimate": comp.get("price_estimate", ""),
            })
            break

    return recommendations


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "version": app.version}


@app.get("/studio")
async def admin_page():
    """Serve the admin UI."""
    return FileResponse("static/studio.html")


@app.get("/api/admin/serve-file")
async def admin_serve_file(
    path: str = Query(..., description="Path to the file to serve"),
    download: bool = Query(False, description="Force download instead of streaming"),
):
    """Serve stem files or other generated files for playback/download.

    Security: Only serves files from allowed directories (temp dirs, demucs output).
    """
    from pathlib import Path
    import os

    file_path = Path(path)

    # Security: only allow serving from specific directories
    allowed_prefixes = [
        "/tmp/",
        "/private/tmp/",  # macOS /tmp symlink target
        "/var/folders/",  # macOS temp
        "/private/var/folders/",  # macOS /var symlink target
        str(Path.home() / ".cache"),
    ]

    path_str = str(file_path.resolve())
    if not any(path_str.startswith(prefix) for prefix in allowed_prefixes):
        raise HTTPException(status_code=403, detail="Access denied: file not in allowed directory")

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    # Determine content type
    suffix = file_path.suffix.lower()
    content_types = {
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
        ".flac": "audio/flac",
        ".mid": "audio/midi",
        ".midi": "audio/midi",
    }
    content_type = content_types.get(suffix, "application/octet-stream")

    if download:
        return FileResponse(
            path=str(file_path),
            filename=file_path.name,
            media_type="application/octet-stream",  # Force download, not playback
            headers={"Content-Disposition": f'attachment; filename="{file_path.name}"'},
        )
    else:
        return FileResponse(
            path=str(file_path),
            media_type=content_type,
        )


@app.post("/api/admin/analyze-quality")
async def admin_analyze_quality(
    file: UploadFile = File(...),
    quick: bool = False,
) -> JSONResponse:
    """Deep quality analysis for admin view.

    Returns detailed stem quality metrics, confidence maps,
    MIDI pass statistics, and archetype information.
    """
    logger.info(f"Admin analyze-quality: received file {file.filename}")
    suffix = Path(file.filename or "").suffix.lower()

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix or ".wav") as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = Path(tmp.name)

    logger.info(f"Admin analyze-quality: saved to {tmp_path}, size={tmp_path.stat().st_size} bytes")

    try:
        import librosa
        import time

        start_time = time.time()
        logger.info("Admin analyze-quality: loading audio...")

        # Load audio
        audio, sr = librosa.load(str(tmp_path), sr=22050, mono=True)
        load_time = time.time() - start_time
        logger.info(f"Admin analyze-quality: loaded {len(audio)/sr:.1f}s audio in {load_time*1000:.0f}ms")

        result = {
            "filename": file.filename,
            "duration_sec": len(audio) / sr,
            "sample_rate": sr,
            "load_time_ms": load_time * 1000,
            "reconstruction_available": _RECONSTRUCTION_AVAILABLE,
        }

        # Initialize analysis to None in case reconstruction isn't available
        analysis = None

        if _RECONSTRUCTION_AVAILABLE:
            analysis_start = time.time()
            logger.info("Admin analyze-quality: starting reconstruction analysis...")

            # Run reconstruction analysis
            if quick:
                config = ReconstructionConfig.fast()
                config.extract_midi = False
            else:
                config = ReconstructionConfig(
                    extract_midi=False,
                    analyze_continuity=False,
                )
            pipeline = get_pipeline(config)
            logger.info("Admin analyze-quality: running analyze_only...")
            analysis, quality_report = pipeline.analyze_only(
                audio=audio,
                sr=sr,
                stem_type="guitar",
            )

            analysis_time = time.time() - analysis_start

            # Stem Quality Details
            if analysis.stem_quality:
                sq = analysis.stem_quality
                result["stem_quality"] = {
                    "overall_quality": getattr(sq, 'overall_quality', None),
                    "contamination_score": getattr(sq, 'contamination_score', None),
                    "transient_integrity": getattr(sq, 'transient_integrity', None),
                    "harmonic_purity": getattr(sq, 'harmonic_purity', None),
                    "reverb_density": getattr(sq, 'reverb_density', None),
                    "stereo_coherence": getattr(sq, 'stereo_coherence', None),
                    "snr_estimate": getattr(sq, 'snr_estimate', None),
                }

            # Contamination Details
            if analysis.contamination:
                ct = analysis.contamination
                result["contamination"] = {
                    "overall_contamination": getattr(ct, 'overall_contamination', None),
                    "bass_bleed": getattr(ct, 'bass_bleed', None),
                    "drum_bleed": getattr(ct, 'drum_bleed', None),
                    "vocal_bleed": getattr(ct, 'vocal_bleed', None),
                    "reverb_contamination": getattr(ct, 'reverb_contamination', None),
                }

            # Artifact Details
            if analysis.artifacts:
                af = analysis.artifacts
                result["artifacts"] = {
                    "clipping_detected": getattr(af, 'clipping_detected', None),
                    "clipping_severity": getattr(af, 'clipping_severity', None),
                    "noise_floor_db": getattr(af, 'noise_floor_db', None),
                    "dc_offset": getattr(af, 'dc_offset', None),
                    "phase_issues": getattr(af, 'phase_issues', None),
                }

            # Role Classification
            if analysis.role:
                role = analysis.role
                result["role"] = {
                    "primary_role": getattr(role, 'primary_role', None),
                    "confidence": getattr(role, 'confidence', None),
                    "spectral_profile": getattr(role, 'spectral_profile', None),
                    "temporal_profile": getattr(role, 'temporal_profile', None),
                }

            # Confidence Map Summary
            if analysis.confidence_map:
                cm = analysis.confidence_map
                result["confidence_map"] = {
                    "global_confidence": getattr(cm, 'global_confidence', None),
                    "region_count": getattr(cm, 'region_count', None),
                    "low_confidence_regions": getattr(cm, 'low_confidence_region_count', 0),
                    "high_confidence_regions": getattr(cm, 'high_confidence_region_count', 0),
                }

            # Continuity Analysis
            if analysis.continuity:
                cont = analysis.continuity
                result["continuity"] = {
                    "sustained_regions": getattr(cont, 'sustained_region_count', None),
                    "avg_sustain_duration": getattr(cont, 'avg_sustain_duration', None),
                    "pitch_stability": getattr(cont, 'pitch_stability', None),
                }

            # Archetype Priors
            if analysis.priors:
                priors = analysis.priors
                result["priors"] = {
                    "source_archetype": getattr(priors, 'source_archetype', None),
                    "onset_threshold": getattr(priors, 'suggested_onset_threshold', None),
                    "frame_threshold": getattr(priors, 'suggested_frame_threshold', None),
                    "min_note_ms": getattr(priors, 'min_note_ms', None),
                    "quantization_strength": getattr(priors, 'quantization_strength', None),
                }

            # Quality Report
            if quality_report:
                result["quality_report"] = {
                    "overall_confidence": quality_report.overall_confidence,
                    "quality_level": quality_report.overall_quality.value if hasattr(quality_report.overall_quality, 'value') else str(quality_report.overall_quality),
                    "should_proceed": quality_report.should_proceed,
                    "warning_count": len(quality_report.warnings),
                    "warnings": [
                        {
                            "level": w.level.value if hasattr(w.level, 'value') else str(w.level),
                            "category": w.category.value if hasattr(w.category, 'value') else str(w.category),
                            "message": w.message,
                            "recommendation": w.recommendation,
                        }
                        for w in quality_report.warnings
                    ],
                }

            result["analysis_time_ms"] = analysis_time * 1000

        # Also run standard analysis to get confidence scores
        descriptor = analyzer.analyze(
            str(tmp_path),
            source_kind="isolated_guitar",
            stem_quality=analysis.stem_quality if analysis else None,
            contamination=analysis.contamination if analysis else None,
        )

        result["confidence_scores"] = {
            "amp_family": descriptor.confidence.amp_family,
            "gain": descriptor.confidence.gain,
            "cab": descriptor.confidence.cab,
            "effects": descriptor.confidence.effects,
        }

        result["detected"] = {
            "amp_family": descriptor.amp.family,
            "gain": descriptor.amp.gain,
        }

        return JSONResponse(_convert_numpy_types(result))

    except Exception as e:
        logger.exception("Admin quality analysis failed")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        tmp_path.unlink(missing_ok=True)


@app.post("/api/admin/analyze-deep")
async def admin_analyze_deep(
    file: UploadFile = File(...),
) -> StreamingResponse:
    """Deep GPU-powered analysis with live progress streaming via SSE.

    Performs:
    1. Stem separation (demucs - GPU)
    2. Quality analysis (CPU)
    3. MIDI extraction (basic_pitch - GPU)

    Returns Server-Sent Events with progress updates.
    """
    import asyncio
    import time
    from concurrent.futures import ThreadPoolExecutor

    suffix = Path(file.filename or "").suffix.lower()

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix or ".wav") as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = Path(tmp.name)

    # Thread pool for CPU/GPU bound operations
    executor = ThreadPoolExecutor(max_workers=1)

    async def event_stream():
        """Generate SSE events during processing."""
        loop = asyncio.get_event_loop()

        def sse_event(event_type: str, data: dict) -> str:
            return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"

        try:
            yield sse_event("start", {
                "filename": file.filename,
                "file_size": tmp_path.stat().st_size,
                "timestamp": datetime.now().isoformat(),
            })
            await asyncio.sleep(0)  # Flush

            # Timing accumulator for profiling
            stage_timings = {}
            pipeline_start = time.time()

            # Stage 1: Load audio
            stage_start = time.time()
            yield sse_event("progress", {
                "stage": "loading",
                "message": "Loading audio file...",
                "percent": 5,
            })
            await asyncio.sleep(0)

            import librosa

            def load_audio():
                return librosa.load(str(tmp_path), sr=22050, mono=True)

            audio, sr = await loop.run_in_executor(executor, load_audio)
            duration_sec = len(audio) / sr
            stage_timings["loading"] = {"duration_ms": (time.time() - stage_start) * 1000}

            # Compute waveform peaks for visualization
            waveform_data = _compute_waveform_peaks(audio, num_points=1000)
            waveform_data["sample_rate"] = int(sr)
            waveform_data["duration_sec"] = duration_sec

            yield sse_event("progress", {
                "stage": "loading",
                "message": f"Loaded {duration_sec:.1f}s audio",
                "percent": 10,
                "duration_sec": duration_sec,
                "stage_duration_ms": stage_timings["loading"]["duration_ms"],
            })
            await asyncio.sleep(0)

            # Stage 2: Stem separation (GPU)
            stage_start = time.time()
            yield sse_event("progress", {
                "stage": "stem_separation",
                "message": "Starting stem separation (GPU)...",
                "percent": 15,
            })
            await asyncio.sleep(0)

            stem_result = None
            try:
                from tone_forge.stem_separator import separate_all_stems, is_available
                if is_available():
                    yield sse_event("progress", {
                        "stage": "stem_separation",
                        "message": "Running Demucs model on GPU...",
                        "percent": 20,
                    })
                    await asyncio.sleep(0)

                    def run_stem_sep():
                        # Use 6-stem model for better separation (guitar, piano, other, drums, bass, vocals)
                        return separate_all_stems(tmp_path, model_name="htdemucs_6s")

                    stem_paths = await loop.run_in_executor(executor, run_stem_sep)
                    stem_result = {name: str(path) for name, path in stem_paths.items()}
                    stage_timings["stem_separation"] = {
                        "duration_ms": (time.time() - stage_start) * 1000,
                        "gpu_used": True,
                    }

                    yield sse_event("progress", {
                        "stage": "stem_separation",
                        "message": f"Separated {len(stem_paths)} stems",
                        "percent": 40,
                        "stems": list(stem_paths.keys()),
                        "stage_duration_ms": stage_timings["stem_separation"]["duration_ms"],
                    })
                    await asyncio.sleep(0)
                else:
                    stage_timings["stem_separation"] = {"duration_ms": 0, "skipped": True}
                    yield sse_event("progress", {
                        "stage": "stem_separation",
                        "message": "Demucs not available, skipping stem separation",
                        "percent": 40,
                        "skipped": True,
                    })
                    await asyncio.sleep(0)
            except Exception as e:
                stage_timings["stem_separation"] = {
                    "duration_ms": (time.time() - stage_start) * 1000,
                    "error": str(e),
                }
                yield sse_event("progress", {
                    "stage": "stem_separation",
                    "message": f"Stem separation failed: {e}",
                    "percent": 40,
                    "error": str(e),
                })
                await asyncio.sleep(0)

            # Stage 3: Quality analysis (broken into sub-steps for progress)
            quality_result = {}
            analysis_results = {}
            quality_report = None

            # Load guitar stem if available for better quality analysis
            guitar_audio = audio  # Default to original mix
            guitar_sr = sr
            if stem_result and "guitar" in stem_result:
                yield sse_event("progress", {
                    "stage": "loading_stem",
                    "message": "Loading separated guitar stem for analysis...",
                    "percent": 42,
                })
                await asyncio.sleep(0)

                try:
                    def load_guitar_stem():
                        return librosa.load(stem_result["guitar"], sr=22050, mono=True)
                    guitar_audio, guitar_sr = await loop.run_in_executor(executor, load_guitar_stem)
                except Exception as e:
                    logger.warning(f"Failed to load guitar stem, using original mix: {e}")

            if _RECONSTRUCTION_AVAILABLE:
                from tone_forge.reconstruction import (
                    get_analyzer as get_stem_analyzer,
                    get_detector as get_contamination_detector,
                    get_artifact_detector,
                    get_confidence_mapper,
                    get_role_classifier,
                    get_continuity_analyzer,
                    get_quality_reporter,
                )

                # Step 3a: Stem quality
                stage_start = time.time()
                yield sse_event("progress", {
                    "stage": "stem_quality",
                    "message": "Analyzing stem quality...",
                    "percent": 45,
                })
                await asyncio.sleep(0)

                try:
                    def run_stem_quality():
                        return get_stem_analyzer().analyze(guitar_audio, guitar_sr, "guitar")
                    analysis_results["stem_quality"] = await loop.run_in_executor(executor, run_stem_quality)
                    stage_timings["stem_quality"] = {"duration_ms": (time.time() - stage_start) * 1000}
                except Exception as e:
                    logger.warning(f"Stem quality failed: {e}")
                    stage_timings["stem_quality"] = {"duration_ms": (time.time() - stage_start) * 1000, "error": str(e)}

                # Step 3b: Contamination
                stage_start = time.time()
                yield sse_event("progress", {
                    "stage": "contamination",
                    "message": "Detecting contamination...",
                    "percent": 48,
                })
                await asyncio.sleep(0)

                try:
                    def run_contamination():
                        return get_contamination_detector().detect(guitar_audio, guitar_sr, "guitar")
                    analysis_results["contamination"] = await loop.run_in_executor(executor, run_contamination)
                    stage_timings["contamination"] = {"duration_ms": (time.time() - stage_start) * 1000}
                except Exception as e:
                    logger.warning(f"Contamination detection failed: {e}")
                    stage_timings["contamination"] = {"duration_ms": (time.time() - stage_start) * 1000, "error": str(e)}

                # Step 3c: Artifacts
                stage_start = time.time()
                yield sse_event("progress", {
                    "stage": "artifacts",
                    "message": "Detecting artifacts...",
                    "percent": 50,
                })
                await asyncio.sleep(0)

                try:
                    def run_artifacts():
                        return get_artifact_detector().detect(guitar_audio, guitar_sr, "guitar")
                    analysis_results["artifacts"] = await loop.run_in_executor(executor, run_artifacts)
                    stage_timings["artifacts"] = {"duration_ms": (time.time() - stage_start) * 1000}
                except Exception as e:
                    logger.warning(f"Artifact detection failed: {e}")
                    stage_timings["artifacts"] = {"duration_ms": (time.time() - stage_start) * 1000, "error": str(e)}

                # Step 3d: Role classification
                stage_start = time.time()
                yield sse_event("progress", {
                    "stage": "role",
                    "message": "Classifying role...",
                    "percent": 52,
                })
                await asyncio.sleep(0)

                try:
                    def run_role():
                        return get_role_classifier().classify(guitar_audio, guitar_sr, "guitar")
                    analysis_results["role"] = await loop.run_in_executor(executor, run_role)
                    stage_timings["role"] = {"duration_ms": (time.time() - stage_start) * 1000}
                except Exception as e:
                    logger.warning(f"Role classification failed: {e}")
                    stage_timings["role"] = {"duration_ms": (time.time() - stage_start) * 1000, "error": str(e)}

                # Step 3e: Continuity analysis (the slow one)
                stage_start = time.time()
                yield sse_event("progress", {
                    "stage": "continuity",
                    "message": "Analyzing temporal continuity...",
                    "percent": 54,
                })
                await asyncio.sleep(0)

                try:
                    def run_continuity():
                        return get_continuity_analyzer().analyze(guitar_audio, guitar_sr)
                    analysis_results["continuity"] = await loop.run_in_executor(executor, run_continuity)
                    stage_timings["continuity"] = {"duration_ms": (time.time() - stage_start) * 1000}

                    yield sse_event("progress", {
                        "stage": "continuity",
                        "message": "Continuity analysis complete",
                        "percent": 58,
                        "stage_duration_ms": stage_timings["continuity"]["duration_ms"],
                    })
                    await asyncio.sleep(0)
                except Exception as e:
                    logger.warning(f"Continuity analysis failed: {e}")
                    stage_timings["continuity"] = {"duration_ms": (time.time() - stage_start) * 1000, "error": str(e)}
                    yield sse_event("progress", {
                        "stage": "continuity",
                        "message": f"Continuity analysis failed: {e}",
                        "percent": 58,
                        "error": str(e),
                    })
                    await asyncio.sleep(0)

                # Step 3f: Confidence map
                stage_start = time.time()
                yield sse_event("progress", {
                    "stage": "confidence_map",
                    "message": "Building confidence map...",
                    "percent": 59,
                })
                await asyncio.sleep(0)

                try:
                    def run_confidence_map():
                        return get_confidence_mapper().build_map(
                            audio, sr, "guitar",
                            stem_quality=analysis_results.get("stem_quality"),
                            contamination=analysis_results.get("contamination"),
                            artifacts=analysis_results.get("artifacts"),
                        )
                    analysis_results["confidence_map"] = await loop.run_in_executor(executor, run_confidence_map)
                    stage_timings["confidence_map"] = {"duration_ms": (time.time() - stage_start) * 1000}
                except Exception as e:
                    logger.warning(f"Confidence map failed: {e}")
                    stage_timings["confidence_map"] = {"duration_ms": (time.time() - stage_start) * 1000, "error": str(e)}

                # Step 3g: Quality report
                stage_start = time.time()
                yield sse_event("progress", {
                    "stage": "quality_report",
                    "message": "Generating quality report...",
                    "percent": 60,
                })
                await asyncio.sleep(0)

                try:
                    reporter = get_quality_reporter()
                    quality_report = reporter.generate_report(
                        stem_type="guitar",
                        stem_quality=analysis_results.get("stem_quality"),
                        contamination=analysis_results.get("contamination"),
                        artifacts=analysis_results.get("artifacts"),
                        confidence_map=analysis_results.get("confidence_map"),
                    )
                    stage_timings["quality_report"] = {"duration_ms": (time.time() - stage_start) * 1000}
                except Exception as e:
                    logger.warning(f"Quality report failed: {e}")
                    stage_timings["quality_report"] = {"duration_ms": (time.time() - stage_start) * 1000, "error": str(e)}

                # Collect results
                if analysis_results.get("stem_quality"):
                    sq = analysis_results["stem_quality"]
                    quality_result["stem_quality"] = {
                        "overall_quality": getattr(sq, 'overall_quality', None),
                        "contamination_score": getattr(sq, 'contamination_score', None),
                        "transient_integrity": getattr(sq, 'transient_integrity', None),
                        "harmonic_purity": getattr(sq, 'harmonic_purity', None),
                    }

                if analysis_results.get("contamination"):
                    ct = analysis_results["contamination"]
                    # Extract contamination regions for waveform overlay
                    contamination_regions = []
                    for e in getattr(ct, 'events', []):
                        contamination_regions.append({
                            "type": e.contamination_type.value if hasattr(e.contamination_type, 'value') else str(e.contamination_type),
                            "start": e.time_start,
                            "end": e.time_end,
                            "severity": e.severity,
                            "confidence": e.confidence,
                            "source": e.source_stem,
                        })
                    quality_result["contamination"] = {
                        "overall_score": getattr(ct, 'overall_contamination', None),
                        "drum_bleed": getattr(ct, 'drum_bleed', None),
                        "vocal_bleed": getattr(ct, 'vocal_bleed', None),
                        "regions": contamination_regions,  # For waveform overlay
                    }

                if analysis_results.get("continuity"):
                    cont = analysis_results["continuity"]
                    quality_result["continuity"] = {
                        "sustained_ratio": getattr(cont, 'sustained_ratio', None),
                        "average_stability": getattr(cont, 'average_stability', None),
                        "phrase_count": getattr(cont, 'phrase_count', None),
                        "dominant_envelope": getattr(cont, 'dominant_envelope', None),
                    }
                    if hasattr(quality_result["continuity"]["dominant_envelope"], 'value'):
                        quality_result["continuity"]["dominant_envelope"] = quality_result["continuity"]["dominant_envelope"].value

                if analysis_results.get("artifacts"):
                    art = analysis_results["artifacts"]
                    # Extract artifact regions for waveform overlay
                    artifact_regions = []
                    for a in getattr(art, 'artifacts', []):
                        artifact_regions.append({
                            "type": a.artifact_type.value if hasattr(a.artifact_type, 'value') else str(a.artifact_type),
                            "start": a.time_start,
                            "end": a.time_end,
                            "severity": a.severity,
                            "confidence": a.confidence,
                        })
                    quality_result["artifacts"] = {
                        "overall_score": getattr(art, 'overall_artifact_score', None),
                        "artifact_count": getattr(art, 'artifact_count', None),
                        "clipping_detected": any(
                            a.artifact_type.value == "clipping"
                            for a in getattr(art, 'artifacts', [])
                        ),
                        "noise_floor_db": None,  # Not directly available
                        "dc_offset": None,
                        "phase_issues": any(
                            a.artifact_type.value == "phase_artifact"
                            for a in getattr(art, 'artifacts', [])
                        ),
                        "regions": artifact_regions,  # For waveform overlay
                    }

                if analysis_results.get("role"):
                    role = analysis_results["role"]
                    quality_result["role"] = {
                        "primary_role": role.primary_role.value if hasattr(role.primary_role, 'value') else str(role.primary_role),
                        "confidence": role.confidence,
                        "spectral_profile": role.spectral_profile.value if hasattr(role.spectral_profile, 'value') else str(role.spectral_profile),
                        "temporal_profile": role.temporal_profile.value if hasattr(role.temporal_profile, 'value') else str(role.temporal_profile),
                    }

                if quality_report:
                    quality_result["quality_report"] = {
                        "overall_quality": quality_report.overall_quality,
                        "overall_confidence": quality_report.overall_confidence,
                        "should_proceed": quality_report.should_proceed,
                        "warning_count": quality_report.total_warnings,
                        "warnings": [
                            {
                                "level": w.level.value if hasattr(w.level, 'value') else str(w.level),
                                "category": w.category.value if hasattr(w.category, 'value') else str(w.category),
                                "message": w.message,
                                "recommendation": w.recommendation,
                            }
                            for w in quality_report.warnings
                        ],
                    }

            # Create a mock analysis object for downstream use
            class AnalysisHolder:
                pass
            analysis = AnalysisHolder()
            analysis.stem_quality = analysis_results.get("stem_quality")
            analysis.contamination = analysis_results.get("contamination")

            # Stage 4: Per-stem MIDI extraction (GPU)
            # Extract MIDI from each separated stem for cleaner results
            stage_start = time.time()
            yield sse_event("progress", {
                "stage": "midi_extraction",
                "message": "Starting per-stem MIDI extraction...",
                "percent": 65,
            })
            await asyncio.sleep(0)

            midi_result = {}
            midi_stems = {}
            try:
                from tone_forge import midi_extractor

                # Define which stems to extract MIDI from and their display names
                # (stem_key, display_label, is_drums, stem_type_for_profile)
                stem_configs = [
                    ("drums", "Drums", True, "drums"),
                    ("bass", "Bass", False, "bass"),
                    ("guitar", "Guitar", False, "lead"),
                    ("piano", "Keys", False, "pad"),
                    ("other", "Synth", False, "synth"),
                    ("vocals", "Vocals", False, "vocals"),
                ]

                if stem_result:
                    # Extract MIDI from each available stem
                    available_stems = [cfg for cfg in stem_configs if cfg[0] in stem_result]
                    total_stems = len(available_stems)

                    for idx, (stem_key, stem_label, is_drums, stem_type) in enumerate(available_stems):
                        progress = 65 + int(((idx + 1) / total_stems) * 20)  # 65-85%
                        yield sse_event("progress", {
                            "stage": "midi_extraction",
                            "message": f"Extracting {stem_label} MIDI ({idx + 1}/{total_stems})...",
                            "percent": progress,
                        })
                        await asyncio.sleep(0)

                        stem_path = stem_result[stem_key]
                        display_name = file.filename or 'Track'

                        try:
                            if is_drums:
                                def extract_fn(path=stem_path, name=display_name, label=stem_label):
                                    return midi_extractor.extract_drum_midi(
                                        str(path),
                                        preset_name=f"{name} - {label}",
                                    )
                            else:
                                def extract_fn(path=stem_path, name=display_name, label=stem_label, stype=stem_type):
                                    return midi_extractor.extract_midi(
                                        str(path),
                                        preset_name=f"{name} - {label}",
                                        stem_type=stype,
                                    )

                            stem_midi = await loop.run_in_executor(executor, extract_fn)

                            # Only include stems that have notes
                            if stem_midi.note_count > 0:
                                stem_midi_data = {
                                    "label": stem_label,
                                    "filename": stem_midi.filename,
                                    "content": stem_midi.content,
                                    "note_count": stem_midi.note_count,
                                    "duration_seconds": stem_midi.duration_seconds,
                                    "tempo_bpm": stem_midi.tempo_bpm,
                                    "pitch_range": {
                                        "lowest": int(stem_midi.pitch_range[0]),
                                        "highest": int(stem_midi.pitch_range[1]),
                                    },
                                }
                                if stem_midi.provenance:
                                    stem_midi_data["provenance"] = stem_midi.provenance
                                midi_stems[stem_key] = stem_midi_data
                                logger.info(f"{stem_label} MIDI: {stem_midi.note_count} notes")
                        except Exception as e:
                            logger.warning(f"MIDI extraction failed for {stem_label}: {e}")

                    # Summary
                    total_notes = sum(s.get("note_count", 0) for s in midi_stems.values())
                    stem_summary = ", ".join(f"{v['label']}" for v in midi_stems.values())
                    yield sse_event("progress", {
                        "stage": "midi_extraction",
                        "message": f"MIDI extracted: {total_notes} notes ({stem_summary})",
                        "percent": 85,
                    })
                    await asyncio.sleep(0)

                    # Set midi_result for backward compatibility (priority: guitar > piano > other > bass)
                    for stem_key in ["guitar", "piano", "other", "bass", "vocals"]:
                        if stem_key in midi_stems:
                            midi_result = midi_stems[stem_key]
                            break

                stage_timings["midi_extraction"] = {"duration_ms": (time.time() - stage_start) * 1000, "gpu_used": True}

            except Exception as e:
                stage_timings["midi_extraction"] = {"duration_ms": (time.time() - stage_start) * 1000, "error": str(e)}
                yield sse_event("progress", {
                    "stage": "midi_extraction",
                    "message": f"MIDI extraction error: {e}",
                    "percent": 85,
                    "error": str(e),
                })
                await asyncio.sleep(0)

            # Stage 5: Tone analysis
            stage_start = time.time()
            # Use separated guitar stem if available for better accuracy
            tone_audio_path = stem_result.get("guitar", str(tmp_path)) if stem_result else str(tmp_path)
            tone_source_kind = "isolated_guitar" if stem_result and "guitar" in stem_result else "full_mix"

            yield sse_event("progress", {
                "stage": "tone_analysis",
                "message": f"Running tone analysis on {'guitar stem' if tone_source_kind == 'isolated_guitar' else 'original mix'}...",
                "percent": 90,
            })
            await asyncio.sleep(0)

            tone_result = {}
            try:
                def run_tone_analysis():
                    return analyzer.analyze(
                        tone_audio_path,
                        source_kind=tone_source_kind,
                        stem_quality=analysis.stem_quality if analysis else None,
                        contamination=analysis.contamination if analysis else None,
                        capture_reasoning=True,  # Enable explainability
                    )

                descriptor = await loop.run_in_executor(executor, run_tone_analysis)

                tone_result = {
                    "amp_family": descriptor.amp.family,
                    "gain": descriptor.amp.gain,
                    "amp": {
                        "family": descriptor.amp.family,
                        "gain": descriptor.amp.gain,
                        "voicing": {
                            "bass": descriptor.amp.voicing.bass,
                            "mid": descriptor.amp.voicing.mid,
                            "treble": descriptor.amp.voicing.treble,
                            "presence": descriptor.amp.voicing.presence,
                            "mid_scoop": descriptor.amp.voicing.mid_scoop,
                        },
                        "alternates": descriptor.amp.alternates,
                    },
                    "cab": {
                        "configuration": descriptor.cab.configuration,
                        "speaker_character": descriptor.cab.speaker_character,
                        "mic_position": descriptor.cab.mic_position,
                    },
                    "effects": {
                        "overdrive": {
                            "style": descriptor.effects.overdrive_pedal.style if descriptor.effects.overdrive_pedal else None,
                            "drive": descriptor.effects.overdrive_pedal.drive if descriptor.effects.overdrive_pedal else 0,
                        } if descriptor.effects.overdrive_pedal else None,
                        "compressor": {
                            "amount": descriptor.effects.compressor.amount if descriptor.effects.compressor else 0,
                            "character": descriptor.effects.compressor.character if descriptor.effects.compressor else None,
                        } if descriptor.effects.compressor else None,
                        "modulation": {
                            "type": descriptor.effects.modulation.type if descriptor.effects.modulation else "none",
                            "rate": descriptor.effects.modulation.rate if descriptor.effects.modulation else 0,
                            "depth": descriptor.effects.modulation.depth if descriptor.effects.modulation else 0,
                        } if descriptor.effects.modulation else None,
                        "delay": {
                            "type": descriptor.effects.delay.type if descriptor.effects.delay else "none",
                            "time_ms": descriptor.effects.delay.time_ms if descriptor.effects.delay else 0,
                            "feedback": descriptor.effects.delay.feedback if descriptor.effects.delay else 0,
                            "mix": descriptor.effects.delay.mix if descriptor.effects.delay else 0,
                        } if descriptor.effects.delay else None,
                        "reverb": {
                            "type": descriptor.effects.reverb.type if descriptor.effects.reverb else "none",
                            "size": descriptor.effects.reverb.size if descriptor.effects.reverb else 0,
                            "mix": descriptor.effects.reverb.mix if descriptor.effects.reverb else 0,
                        } if descriptor.effects.reverb else None,
                    },
                    "guitar": {
                        "pickup_brightness": descriptor.guitar.pickup_brightness,
                        "playing_style": descriptor.guitar.playing_style,
                        "estimated_tuning": descriptor.guitar.estimated_tuning,
                    },
                    "confidence": {
                        "amp_family": descriptor.confidence.amp_family,
                        "gain": descriptor.confidence.gain,
                        "cab": descriptor.confidence.cab,
                        "effects": descriptor.confidence.effects,
                    },
                    "reasoning": descriptor.reasoning.to_dict() if descriptor.reasoning else None,
                }
                stage_timings["tone_analysis"] = {"duration_ms": (time.time() - stage_start) * 1000}
            except Exception as e:
                stage_timings["tone_analysis"] = {"duration_ms": (time.time() - stage_start) * 1000, "error": str(e)}
                yield sse_event("progress", {
                    "stage": "tone_analysis",
                    "message": f"Tone analysis error: {e}",
                    "percent": 95,
                    "error": str(e),
                })
                await asyncio.sleep(0)

            # Complete
            total_time = time.time() - pipeline_start

            # Calculate audio processing ratio
            audio_ratio = total_time / duration_sec if duration_sec > 0 else 0

            # Build final response
            final_response = _convert_numpy_types({
                "success": True,
                "total_time_sec": total_time,
                "duration_sec": duration_sec,
                "sample_rate": sr,
                "filename": file.filename or "Unknown",
                "stems": stem_result,
                "quality": quality_result,
                "midi": midi_result,
                "midi_stems": midi_stems,  # Per-stem MIDI for export
                "tone": tone_result,
                "waveform": waveform_data,
                "profiling": {
                    "total_ms": total_time * 1000,
                    "audio_duration_sec": duration_sec,
                    "processing_ratio": audio_ratio,  # time to process / audio duration
                    "stages": stage_timings,
                },
            })

            # Save to history
            history_entry = _add_to_history({
                "name": file.filename or "Admin upload",
                "detected_type": "guitar",
                "summary": f"Deep analysis: {tone_result.get('amp_family', 'unknown')} amp",
                "amp_family": tone_result.get("amp_family"),
                "gain": tone_result.get("gain"),
                "duration": duration_sec,
                "deep_analysis": True,
                "has_quality_data": bool(quality_result),
                "has_reasoning": bool(tone_result.get("reasoning")),
            }, full_result=final_response)

            # Add history ID and admin URL to response
            final_response["history_id"] = history_entry["id"]
            final_response["admin_url"] = f"/studio?analysis={history_entry['id']}"

            yield sse_event("complete", final_response)

        except Exception as e:
            logger.exception("Deep analysis failed")
            yield sse_event("error", {
                "message": str(e),
            })
        finally:
            tmp_path.unlink(missing_ok=True)
            executor.shutdown(wait=False)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/history")
async def get_history(
    q: Optional[str] = Query(None, description="Search query"),
    limit: int = Query(50, ge=1, le=100),
) -> JSONResponse:
    """Get analysis history, optionally filtered by search query."""
    history = _load_history()

    if q:
        q_lower = q.lower()
        history = [
            entry for entry in history
            if q_lower in entry.get("name", "").lower()
            or q_lower in entry.get("detected_type", "").lower()
            or q_lower in entry.get("summary", "").lower()
            or q_lower in (entry.get("amp_family") or "").lower()
        ]

    return JSONResponse({"history": history[:limit]})


@app.delete("/api/history")
async def clear_history() -> JSONResponse:
    """Clear all history."""
    _save_history([])
    return JSONResponse({"status": "cleared"})


@app.delete("/api/history/{entry_id}")
async def delete_history_entry(entry_id: str) -> JSONResponse:
    """Delete a specific history entry."""
    history = _load_history()
    history = [e for e in history if e.get("id") != entry_id]
    _save_history(history)
    return JSONResponse({"status": "deleted"})


@app.post("/api/history/save")
async def save_to_history(request: Request) -> JSONResponse:
    """Save a local engine result to history for admin access."""
    data = await request.json()
    filename = data.get("filename", "Unknown")
    result = data.get("result", {})

    # Extract metadata from the result
    detected_type = result.get("detected_type", "unknown")
    detection = result.get("detection", {})
    guitar = result.get("guitar", {})
    descriptor = guitar.get("descriptor", {}) if guitar else {}

    entry = _add_to_history({
        "filename": filename,
        "detected_type": detected_type,
        "summary": detection.get("summary", ""),
        "amp_family": descriptor.get("amp", {}).get("family") if descriptor else None,
        "gain": descriptor.get("amp", {}).get("gain") if descriptor else None,
        "duration": descriptor.get("source", {}).get("duration_sec") if descriptor else None,
        "source": "local_engine",
    }, full_result=result)

    return JSONResponse({"id": entry["id"], "status": "saved"})


@app.get("/api/history/{entry_id}")
async def get_history_entry(entry_id: str) -> JSONResponse:
    """Get a specific history entry with full result."""
    entry = _get_history_item(entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="History entry not found")
    return JSONResponse(entry)


def _check_yt_dlp() -> bool:
    """Check if yt-dlp is available."""
    import sys
    try:
        result = subprocess.run(
            [sys.executable, "-m", "yt_dlp", "--version"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


@app.get("/api/capabilities")
async def capabilities() -> dict:
    """Return server capabilities, including supported platforms and features."""
    from tone_forge import stem_separator

    return {
        "version": app.version,
        "stem_separation": stem_separator.is_available(),
        "youtube_support": _check_yt_dlp(),
        "supported_source_kinds": ["isolated_guitar", "stem_separated", "full_mix", "synth"],
        "supported_platforms": SUPPORTED_PLATFORMS,
        "platform_info": {
            "helix": "Line 6 Helix/HX Stomp/POD Go block recommendations",
            "pedals": "Real pedal and amp recommendations with prices",
            "synth": "Synth parameter analysis and recreation hints",
        },
        "note": (
            "full_mix requires stem_separation=true. "
            "If false, install with: pip install demucs torch torchaudio"
        ) if not stem_separator.is_available() else None,
    }


class UrlAnalyzeRequest(BaseModel):
    url: str
    source_kind: str = "auto"  # Changed default to auto
    platform: str = "auto"
    fast_mode: bool = True  # Skip stem separation for speed (default: fast)
    extract_midi: bool = True  # Extract MIDI notes from audio


class ExportRequest(BaseModel):
    chain: list[dict] = []
    descriptor: dict = {}
    format: str = "hlx"
    preset_name: str = "Tone Forge Export"
    # For bass/drums exports
    recommendations: list[dict] = []
    machine_match: dict = None
    # For Ableton Live Set export (full analysis result)
    full_result: dict = None
    # For MIDI export (stored from analysis)
    midi_data: dict = None


class MIDIExtractRequest(BaseModel):
    """Request for extracting MIDI from audio file path or stored data."""
    preset_name: str = "Extracted MIDI"
    min_note_duration_ms: float = 50
    velocity_sensitivity: float = 1.0
    quantize_to: int = None  # e.g., 16 for 16th notes


@app.post("/api/export")
async def export_preset(request: ExportRequest) -> JSONResponse:
    """Export signal chain to a downloadable preset format.

    Supported formats:
    - hlx: Line 6 Helix preset
    - json: Generic JSON
    - neural_dsp: Neural DSP Quad Cortex
    - synth_serum: Synth preset (Serum-style)
    - synth_vital: Synth preset (Vital-style)
    """
    from tone_forge import preset_export

    format_type = request.format.lower()

    try:
        if format_type == "hlx":
            result = preset_export.export_helix_preset(
                request.chain,
                None,  # We don't need full descriptor for Helix
                request.preset_name,
            )
        elif format_type == "hlx_stomp":
            result = preset_export.export_hx_stomp_preset(
                request.chain,
                None,
                request.preset_name,
            )
        elif format_type == "json":
            result = preset_export.export_json_preset(
                request.chain,
                request.descriptor,
                request.preset_name,
            )
        elif format_type == "neural_dsp":
            result = preset_export.export_neural_dsp_preset(
                request.chain,
                request.descriptor,
                request.preset_name,
            )
        elif format_type.startswith("synth"):
            target = format_type.replace("synth_", "") or "serum"
            result = preset_export.export_synth_preset(
                request.descriptor,
                request.preset_name,
                target,
            )
        elif format_type == "bass":
            result = preset_export.export_bass_preset(
                request.descriptor,
                request.recommendations or request.chain,
                request.preset_name,
            )
        elif format_type == "drums":
            result = preset_export.export_drums_preset(
                request.descriptor,
                request.machine_match,
                request.preset_name,
            )
        elif format_type == "ableton":
            result = preset_export.export_ableton_preset(
                request.descriptor,
                request.chain,
                request.preset_name,
                instrument_type="guitar",
            )
        elif format_type == "ableton_synth":
            result = preset_export.export_ableton_synth(
                request.descriptor,
                request.preset_name,
            )
        elif format_type == "ableton_drums":
            result = preset_export.export_ableton_drums(
                request.descriptor,
                request.machine_match,
                request.preset_name,
            )
        elif format_type == "ableton_live_set":
            if not request.full_result:
                raise HTTPException(
                    status_code=400,
                    detail="Full analysis result required for Live Set export",
                )
            result = preset_export.export_ableton_live_set(
                request.full_result,
                request.preset_name,
            )
        elif format_type == "project_bundle":
            # ZIP bundle with MIDI, stems, presets - works with ANY DAW
            if not request.full_result:
                raise HTTPException(
                    status_code=400,
                    detail="Full analysis result required for Project Bundle export",
                )
            result = preset_export.export_project_bundle(
                request.full_result,
                request.preset_name,
            )
        elif format_type == "text":
            if not request.full_result:
                raise HTTPException(
                    status_code=400,
                    detail="Full analysis result required for text export",
                )
            result = preset_export.export_text_analysis(
                request.full_result,
                request.preset_name,
            )
        elif format_type == "ableton_wavetable":
            # Get synth descriptor from full_result or descriptor
            synth_desc = request.descriptor
            if request.full_result and request.full_result.get("synth"):
                synth_desc = request.full_result["synth"].get("descriptor", synth_desc)
            if not synth_desc:
                raise HTTPException(
                    status_code=400,
                    detail="Synth analysis required for Wavetable export",
                )
            result = preset_export.export_ableton_wavetable(
                synth_desc,
                request.preset_name,
            )
        elif format_type == "ableton_analog":
            # Get synth descriptor from full_result or descriptor
            synth_desc = request.descriptor
            if request.full_result and request.full_result.get("synth"):
                synth_desc = request.full_result["synth"].get("descriptor", synth_desc)
            if not synth_desc:
                raise HTTPException(
                    status_code=400,
                    detail="Synth analysis required for Analog export",
                )
            result = preset_export.export_ableton_analog(
                synth_desc,
                request.preset_name,
            )
        elif format_type == "midi":
            # MIDI export uses stored midi_data from analysis
            if not request.midi_data:
                raise HTTPException(
                    status_code=400,
                    detail="MIDI data required. Re-analyze with MIDI extraction enabled.",
                )
            # Return the stored MIDI data
            from tone_forge.preset_export import ExportedPreset
            result = ExportedPreset(
                filename=request.midi_data.get("filename", f"{request.preset_name}.mid"),
                format="midi",
                content=request.midi_data.get("content", ""),
                content_type="audio/midi",
            )
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown format: {format_type}. Supported: {list(preset_export.EXPORT_FORMATS.keys())}",
            )

        return JSONResponse({
            "filename": result.filename,
            "format": result.format,
            "content": result.content,
            "content_type": result.content_type,
        })

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Export failed: {e}") from e


@app.get("/api/export-formats")
async def list_export_formats() -> dict:
    """List available export formats."""
    from tone_forge import preset_export
    return {"formats": preset_export.EXPORT_FORMATS}


@app.post("/api/preview")
async def generate_tone_preview(request: Request) -> JSONResponse:
    """
    Generate a reconstruction preview of the detected tone.

    Uses IR convolution and simple amp simulation to create an audio
    preview of what the detected tone sounds like. Also returns
    matching reference tones from the library.

    Request body:
        descriptor: The tone descriptor from analysis
        midi_content: Optional base64 MIDI to render (otherwise uses test signal)
        preset_type: "guitar", "bass", or "synth"

    Returns:
        audio_b64: Base64 WAV of the preview
        reference: Matching reference tone info
        metadata: Processing details
    """
    from tone_forge import tone_preview

    try:
        body = await request.json()
        descriptor = body.get("descriptor")
        midi_content = body.get("midi_content")
        preset_type = body.get("preset_type", "guitar")

        # Validate descriptor
        if not descriptor:
            raise HTTPException(status_code=400, detail="No tone descriptor provided for preview")

        # Generate preview
        result = tone_preview.generate_preview_response(
            descriptor=descriptor,
            midi_content_b64=midi_content,
            preset_type=preset_type,
        )

        return JSONResponse(content=result)

    except Exception as e:
        logger.exception("Preview generation failed")
        raise HTTPException(status_code=500, detail=f"Preview generation failed: {e}") from e


@app.get("/api/reference-tones")
async def get_reference_tones(
    amp_family: Optional[str] = None,
    gain_stage: Optional[str] = None,
) -> JSONResponse:
    """
    Get reference tone library entries.

    Can filter by amp_family and gain_stage.
    """
    from tone_forge.tone_preview import REFERENCE_LIBRARY

    tones = []
    for ref in REFERENCE_LIBRARY:
        if amp_family and ref.amp_family != amp_family:
            continue
        if gain_stage and ref.gain_stage != gain_stage:
            continue

        tones.append({
            "name": ref.name,
            "description": ref.description,
            "amp_family": ref.amp_family,
            "gain_stage": ref.gain_stage,
            "cab_type": ref.cab_type,
            "mic_type": ref.mic_type,
            "effects": ref.effects,
            "tags": ref.tags or [],
        })

    return JSONResponse(content={"tones": tones})


@app.get("/api/hardware-profiles")
async def get_hardware_profiles() -> JSONResponse:
    """Get available preset hardware profiles."""
    from tone_forge.hardware_profile import PRESET_PROFILES

    profiles = {}
    for key, profile in PRESET_PROFILES.items():
        profiles[key] = {
            "name": profile.name,
            "type": profile.primary_amp.type if profile.primary_amp else None,
            "max_blocks": profile.primary_amp.max_blocks if profile.primary_amp else None,
        }

    return JSONResponse(content={"profiles": profiles})


@app.get("/api/hardware-profile-template")
async def get_hardware_profile_template() -> JSONResponse:
    """Get a template for creating a custom hardware profile."""
    from tone_forge.hardware_profile import get_profile_template

    return JSONResponse(content={"template": get_profile_template()})


@app.get("/api/local-engine/download")
async def download_local_engine():
    """
    Download the local engine installer.

    Serves the actual DMG/installer file if available,
    otherwise shows an info page with instructions.
    """
    import platform

    system = platform.system()

    # Check for built installers
    dist_dir = Path(__file__).parent / "dist"
    installers = {
        "Darwin": dist_dir / "ToneForge-Studio.dmg",
        "Windows": dist_dir / "ToneForge Local Engine" / "ToneForge Local Engine.exe",
        "Linux": dist_dir / "toneforge-local",
    }

    installer_path = installers.get(system)

    # Serve the actual file if it exists
    if installer_path and installer_path.exists():
        filename = installer_path.name
        media_type = {
            ".dmg": "application/x-apple-diskimage",
            ".exe": "application/x-msdownload",
            "": "application/octet-stream",
        }.get(installer_path.suffix, "application/octet-stream")

        return FileResponse(
            path=str(installer_path),
            filename=filename,
            media_type=media_type,
        )

    # Fallback to info page
    platform_name = {"Darwin": "macOS", "Windows": "Windows", "Linux": "Linux"}.get(system, "macOS")

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>ToneForge Studio — Local Acceleration</title>
        <style>
            body {{
                font-family: 'IBM Plex Sans', system-ui, sans-serif;
                max-width: 540px;
                margin: 60px auto;
                padding: 24px;
                color: #1a1612;
                line-height: 1.6;
            }}
            h1 {{
                font-family: 'Fraunces', Georgia, serif;
                font-weight: 500;
                font-size: 28px;
                margin-bottom: 8px;
            }}
            .subtitle {{
                color: #666;
                font-size: 15px;
                margin-bottom: 32px;
            }}
            .benefit {{
                background: linear-gradient(135deg, #f8f5f0 0%, #f0ebe3 100%);
                border: 1px solid #e0d8cc;
                border-radius: 8px;
                padding: 24px;
                margin: 24px 0;
            }}
            .benefit h2 {{
                font-size: 14px;
                text-transform: uppercase;
                letter-spacing: 0.1em;
                color: #888;
                margin: 0 0 16px 0;
                font-weight: 500;
            }}
            .benefit ul {{
                margin: 0;
                padding-left: 20px;
            }}
            .benefit li {{
                margin: 10px 0;
                color: #444;
            }}
            .benefit strong {{
                color: #1a1612;
            }}
            .cta {{
                background: #1a1612;
                color: #fff;
                border: none;
                padding: 14px 28px;
                border-radius: 6px;
                font-size: 14px;
                font-weight: 500;
                cursor: pointer;
                text-decoration: none;
                display: inline-block;
                margin: 8px 0;
            }}
            .cta:hover {{ background: #2d2520; }}
            .note {{
                font-size: 13px;
                color: #888;
                margin-top: 8px;
            }}
            .dev-section {{
                margin-top: 40px;
                padding-top: 24px;
                border-top: 1px solid #e0d8cc;
            }}
            .dev-section h3 {{
                font-size: 13px;
                text-transform: uppercase;
                letter-spacing: 0.08em;
                color: #888;
                margin-bottom: 12px;
            }}
            code {{
                background: #ebe3d4;
                padding: 3px 8px;
                border-radius: 4px;
                font-family: 'IBM Plex Mono', monospace;
                font-size: 13px;
            }}
            .back {{
                display: inline-block;
                margin-top: 32px;
                color: #888;
                text-decoration: none;
                font-size: 14px;
            }}
            .back:hover {{ color: #1a1612; }}
        </style>
    </head>
    <body>
        <h1>ToneForge Studio</h1>
        <p class="subtitle">Local acceleration for professional workflows</p>

        <div class="benefit">
            <h2>What you get</h2>
            <ul>
                <li><strong>~2x faster</strong> stem separation and deep analysis</li>
                <li><strong>GPU acceleration</strong> using your machine's hardware</li>
                <li><strong>Private processing</strong> — audio stays on your computer</li>
                <li><strong>Automatic detection</strong> — works seamlessly with ToneForge</li>
            </ul>
        </div>

        <p class="note">
            Available for {platform_name}. Runs quietly in your menu bar.
        </p>

        <div class="dev-section">
            <h3>Developer Preview</h3>
            <p>Run from source while in development:</p>
            <p><code>python -m local_engine.tray</code></p>
        </div>

        <a href="/" class="back">&larr; Back to ToneForge</a>
    </body>
    </html>
    """

    return HTMLResponse(content=html)


@app.post("/api/local-engine/start")
async def start_local_engine():
    """
    Start the local GPU engine process.

    Spawns the local engine server as a background process.
    Returns immediately; use /health check on port 7777 to verify it started.
    """
    import subprocess
    import sys

    # Check if already running
    try:
        import httpx
        resp = httpx.get("http://127.0.0.1:7777/health", timeout=1.0)
        if resp.status_code == 200:
            return JSONResponse({"status": "already_running", "message": "Local engine is already running"})
    except Exception:
        pass  # Not running, proceed to start

    # Start the local engine
    backend_dir = Path(__file__).parent
    server_script = backend_dir / "local_engine" / "server.py"

    if not server_script.exists():
        raise HTTPException(status_code=404, detail="Local engine server.py not found")

    try:
        # Use nohup to keep it running after this process ends
        process = subprocess.Popen(
            [sys.executable, str(server_script)],
            cwd=str(backend_dir),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,  # Detach from parent process
        )
        return JSONResponse({
            "status": "started",
            "message": "Local engine starting...",
            "pid": process.pid
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start local engine: {e}")


@app.post("/api/adapt-to-hardware")
async def adapt_to_hardware(request: Request) -> JSONResponse:
    """
    Adapt analysis output to a user's hardware profile.

    Request body:
        descriptor: Tone descriptor from analysis
        chain: Signal chain blocks
        profile: Hardware profile (from preset key or custom object)

    Returns:
        Adapted chain with suggestions and warnings.
    """
    from tone_forge import hardware_profile

    try:
        body = await request.json()
        descriptor = body.get("descriptor", {})
        chain = body.get("chain", [])
        profile_data = body.get("profile")

        # Load profile (preset key or custom)
        if isinstance(profile_data, str):
            # Preset profile key
            profile = hardware_profile.PRESET_PROFILES.get(profile_data)
            if not profile:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown profile: {profile_data}"
                )
        elif isinstance(profile_data, dict):
            # Custom profile
            profile = hardware_profile.HardwareProfile.from_dict(profile_data)
        else:
            raise HTTPException(
                status_code=400,
                detail="Profile must be a preset key or profile object"
            )

        # Adapt to profile
        result = hardware_profile.generate_profile_adapted_output(
            descriptor=descriptor,
            chain=chain,
            profile=profile,
        )

        return JSONResponse(content=result)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Hardware adaptation failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/extract-midi")
async def extract_midi_endpoint(
    file: UploadFile = File(...),
    preset_name: str = "Extracted MIDI",
    min_note_duration_ms: float = 50,
    velocity_sensitivity: float = 1.0,
    quantize_to: Optional[int] = None,
    stem_type: str = "other",  # bass, drums, synth, pad, lead, vocals, other
) -> JSONResponse:
    """Extract MIDI notes from uploaded audio.

    Uses stem-specific extraction profiles for optimal results.

    Args:
        file: Audio file (WAV, MP3, etc.)
        preset_name: Name for the output MIDI file
        min_note_duration_ms: Minimum note duration in milliseconds
        velocity_sensitivity: Scale factor for velocity (1.0 = normal)
        quantize_to: If set, quantize to this note division (e.g., 16 = 16th notes)
        stem_type: Type of audio (bass, drums, synth, pad, lead, vocals, other)

    Returns:
        JSON with filename, content (base64), and extraction metadata
    """
    from tone_forge import midi_extractor

    suffix = Path(file.filename or "").suffix.lower()
    if suffix and suffix not in _ACCEPTED_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type {suffix}. Accepted: {sorted(_ACCEPTED_SUFFIXES)}.",
        )

    # Write to temp file
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix or ".wav") as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = Path(tmp.name)

    try:
        result = midi_extractor.extract_midi(
            str(tmp_path),
            preset_name=preset_name or file.filename or "Extracted MIDI",
            min_note_duration_ms=min_note_duration_ms,
            velocity_sensitivity=velocity_sensitivity,
            quantize_to=quantize_to,
            stem_type=stem_type,
        )

        return JSONResponse(_convert_numpy_types({
            "filename": result.filename,
            "content": result.content,
            "content_type": "audio/midi",
            "note_count": result.note_count,
            "duration_seconds": result.duration_seconds,
            "tempo_bpm": result.tempo_bpm,
            "pitch_range": {
                "lowest": result.pitch_range[0],
                "highest": result.pitch_range[1],
            },
        }))

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"MIDI extraction failed: {e}") from e
    finally:
        tmp_path.unlink(missing_ok=True)


@app.get("/api/synth-hardware")
async def list_synth_hardware() -> dict:
    """List available hardware synths for translation."""
    from tone_forge import synth_hardware
    return {"hardware": synth_hardware.get_available_hardware()}


class SynthHardwareRequest(BaseModel):
    synth_descriptor: dict
    hardware_id: str


@app.post("/api/synth-hardware")
async def translate_synth_hardware(request: SynthHardwareRequest) -> JSONResponse:
    """Translate synth descriptor to hardware synth settings.

    Returns knob positions and tips for recreating the sound
    on a specific hardware synthesizer.
    """
    from tone_forge import synth_hardware
    from tone_forge.synth_analyzer import SynthDescriptor, SynthOscillator, SynthFilter, SynthEnvelope, SynthLFO

    # Reconstruct the descriptor from dict
    desc_dict = request.synth_descriptor
    try:
        osc = SynthOscillator(
            type=desc_dict.get("oscillator", {}).get("type", "saw"),
            num_voices=desc_dict.get("oscillator", {}).get("num_voices", 1),
            detune=desc_dict.get("oscillator", {}).get("detune", 0),
            sub_osc=desc_dict.get("oscillator", {}).get("sub_osc", False),
        )
        filt = SynthFilter(
            cutoff_hz=desc_dict.get("filter", {}).get("cutoff_hz", 8000),
            cutoff_normalized=desc_dict.get("filter", {}).get("cutoff_normalized", 0.8),
            resonance=desc_dict.get("filter", {}).get("resonance", 0),
        )
        env = SynthEnvelope(
            attack_ms=desc_dict.get("amp_envelope", {}).get("attack_ms", 10),
            decay_ms=desc_dict.get("amp_envelope", {}).get("decay_ms", 100),
            sustain=desc_dict.get("amp_envelope", {}).get("sustain", 0.7),
            release_ms=desc_dict.get("amp_envelope", {}).get("release_ms", 200),
        )
        lfo = None
        if desc_dict.get("lfo"):
            lfo = SynthLFO(
                rate_hz=desc_dict["lfo"].get("rate_hz", 0),
                depth=desc_dict["lfo"].get("depth", 0),
                target=desc_dict["lfo"].get("target", "filter"),
            )

        descriptor = SynthDescriptor(
            oscillator=osc,
            filter=filt,
            amp_envelope=env,
            lfo=lfo,
            has_chorus=desc_dict.get("has_chorus", False),
            has_phaser=desc_dict.get("has_phaser", False),
            has_reverb=desc_dict.get("has_reverb", False),
            has_delay=desc_dict.get("has_delay", False),
            brightness=desc_dict.get("brightness", 0.5),
            duration_sec=desc_dict.get("duration_sec", 0),
        )

        config = synth_hardware.translate_to_hardware(descriptor, request.hardware_id)

        if not config:
            raise HTTPException(status_code=400, detail=f"Unknown hardware: {request.hardware_id}")

        return JSONResponse({
            "synth_name": config.synth_name,
            "synth_model": config.synth_model,
            "description": config.description,
            "controls": [
                {
                    "name": c.name,
                    "value": c.value,
                    "display": c.display,
                    "note": c.note,
                }
                for c in config.controls
            ],
            "notes": config.notes,
        })

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Translation failed: {e}") from e


def _parse_youtube_timestamp(url: str) -> int:
    """Parse timestamp from YouTube URL.

    Supports formats:
    - ?t=120 or &t=120 (seconds)
    - ?t=2m30s or &t=2m30s (minutes and seconds)
    - ?t=1h2m30s (hours, minutes, seconds)

    Returns timestamp in seconds, or 0 if no timestamp found.
    """
    import re
    from urllib.parse import urlparse, parse_qs

    parsed = urlparse(url)
    params = parse_qs(parsed.query)

    # Get the 't' parameter
    t_param = params.get('t', [None])[0]
    if not t_param:
        return 0

    # Try parsing as plain seconds
    if t_param.isdigit():
        return int(t_param)

    # Try parsing format like "2m30s" or "1h2m30s"
    total_seconds = 0
    hours = re.search(r'(\d+)h', t_param)
    minutes = re.search(r'(\d+)m', t_param)
    seconds = re.search(r'(\d+)s', t_param)

    if hours:
        total_seconds += int(hours.group(1)) * 3600
    if minutes:
        total_seconds += int(minutes.group(1)) * 60
    if seconds:
        total_seconds += int(seconds.group(1))

    return total_seconds


def _format_timestamp(seconds: int) -> str:
    """Format seconds as MM:SS or HH:MM:SS for yt-dlp."""
    if seconds >= 3600:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        return f"{h}:{m:02d}:{s:02d}"
    else:
        m = seconds // 60
        s = seconds % 60
        return f"{m}:{s:02d}"


# YouTube audio cache directory
YOUTUBE_CACHE_DIR = Path(tempfile.gettempdir()) / "toneforge_yt_cache"
YOUTUBE_CACHE_DIR.mkdir(exist_ok=True)
CACHE_MAX_AGE_HOURS = 24  # Delete cached files older than this


def _extract_video_id(url: str) -> str | None:
    """Extract YouTube video ID from URL."""
    from urllib.parse import urlparse, parse_qs
    import re

    parsed = urlparse(url)

    # Handle youtu.be short URLs
    if parsed.netloc in ('youtu.be', 'www.youtu.be'):
        return parsed.path.lstrip('/')

    # Handle youtube.com URLs
    if 'youtube' in parsed.netloc:
        # Standard watch URL
        if parsed.path == '/watch':
            params = parse_qs(parsed.query)
            return params.get('v', [None])[0]
        # Embed or shorts URL
        match = re.match(r'/(embed|shorts|v)/([^/?]+)', parsed.path)
        if match:
            return match.group(2)

    return None


def _get_cache_key(video_id: str, start_time: int, duration: int) -> str:
    """Generate cache key for a specific video segment."""
    return f"{video_id}_{start_time}_{duration}"


def _get_cached_audio(video_id: str, start_time: int, duration: int) -> tuple[Path, str] | None:
    """Check if audio is cached and return (path, title) if exists."""
    cache_key = _get_cache_key(video_id, start_time, duration)
    cache_path = YOUTUBE_CACHE_DIR / f"{cache_key}.wav"
    meta_path = YOUTUBE_CACHE_DIR / f"{cache_key}.json"

    if cache_path.exists():
        # Check if cache is not too old
        age_hours = (datetime.now().timestamp() - cache_path.stat().st_mtime) / 3600
        if age_hours < CACHE_MAX_AGE_HOURS:
            # Try to load title from metadata
            title = cache_key  # Default to cache key
            if meta_path.exists():
                try:
                    with open(meta_path) as f:
                        meta = json.load(f)
                        title = meta.get("title", cache_key)
                except Exception:
                    pass
            logger.info(f"Using cached audio: {title}")
            return cache_path, title
        else:
            # Cache expired, delete it
            cache_path.unlink(missing_ok=True)
            meta_path.unlink(missing_ok=True)

    return None


def _cache_audio(source_path: Path, video_id: str, start_time: int, duration: int, title: str = None) -> Path:
    """Copy audio file to cache with metadata and return cache path."""
    cache_key = _get_cache_key(video_id, start_time, duration)
    cache_path = YOUTUBE_CACHE_DIR / f"{cache_key}.wav"
    meta_path = YOUTUBE_CACHE_DIR / f"{cache_key}.json"

    shutil.copy2(source_path, cache_path)

    # Save metadata including title
    if title:
        with open(meta_path, 'w') as f:
            json.dump({"title": title, "video_id": video_id, "start_time": start_time}, f)

    logger.info(f"Cached audio: {title or cache_path.name}")
    return cache_path


def _cleanup_old_cache():
    """Remove cache files older than CACHE_MAX_AGE_HOURS."""
    try:
        now = datetime.now().timestamp()
        for f in YOUTUBE_CACHE_DIR.glob("*.wav"):
            age_hours = (now - f.stat().st_mtime) / 3600
            if age_hours > CACHE_MAX_AGE_HOURS:
                f.unlink(missing_ok=True)
                logger.debug(f"Removed old cache: {f.name}")
    except Exception as e:
        logger.warning(f"Cache cleanup error: {e}")


def _download_youtube_audio(url: str, output_dir: Path, duration: int = 30) -> tuple[Path, int, str]:
    """Download audio from a YouTube URL using yt-dlp.

    Args:
        url: YouTube URL (can include timestamp like ?t=120)
        output_dir: Directory to save the file
        duration: Seconds to download (default 30)

    Returns tuple of (path to downloaded file, start timestamp in seconds, display name).
    """
    import sys

    # Parse timestamp from URL
    start_time = _parse_youtube_timestamp(url)

    # Check cache first
    video_id = _extract_video_id(url)
    if video_id:
        cached_result = _get_cached_audio(video_id, start_time, duration)
        if cached_result:
            cached_path, cached_title = cached_result
            # Copy cached file to output_dir for consistency
            output_path = output_dir / f"{cached_title}.wav"
            shutil.copy2(cached_path, output_path)
            return output_path, start_time, cached_title

        # Cleanup old cache files occasionally
        _cleanup_old_cache()

    output_template = str(output_dir / "%(title).50s.%(ext)s")
    end_time = start_time + duration

    # Format times for yt-dlp
    start_str = _format_timestamp(start_time)
    end_str = _format_timestamp(end_time)

    logger.info(f"Downloading {duration}s starting at {start_str} (t={start_time}s)")

    # Use python -m yt_dlp to ensure we find the installed package
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--extract-audio",
        "--audio-format", "wav",
        "--audio-quality", "0",
        "--no-playlist",
        # Download specific section based on timestamp
        "--download-sections", f"*{start_str}-{end_str}",
        "--output", output_template,
        url,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)

    if result.returncode != 0:
        # Fallback: some videos don't support sections, try without limit
        logger.info("Section download failed, trying full download...")
        cmd_fallback = [
            sys.executable, "-m", "yt_dlp",
            "--extract-audio",
            "--audio-format", "wav",
            "--audio-quality", "0",
            "--no-playlist",
            "--output", output_template,
            url,
        ]
        result = subprocess.run(cmd_fallback, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            raise RuntimeError(f"yt-dlp failed: {result.stderr}")

    # Find the downloaded file
    wav_files = list(output_dir.glob("*.wav"))
    if not wav_files:
        raise RuntimeError("No audio file was downloaded")

    downloaded_file = wav_files[0]
    # Extract title from filename (yt-dlp uses video title)
    display_name = downloaded_file.stem

    # Cache the downloaded file for future use
    if video_id:
        _cache_audio(downloaded_file, video_id, start_time, duration, title=display_name)

    return downloaded_file, start_time, display_name


@app.post("/api/analyze-url")
async def analyze_url_endpoint(request: UrlAnalyzeRequest) -> JSONResponse:
    """Analyze audio from a YouTube URL.

    Downloads the audio using yt-dlp, then runs analysis.
    For best results with YouTube videos containing full mixes,
    use source_kind="full_mix" to enable stem separation.

    Supports multiple platforms: helix, pedals, synth.
    """
    if not _check_yt_dlp():
        raise HTTPException(
            status_code=501,
            detail="yt-dlp is not installed. Install with: pip install yt-dlp",
        )

    url = request.url.strip()
    source_kind = request.source_kind
    platform = request.platform
    fast_mode = request.fast_mode

    if source_kind not in ("isolated_guitar", "stem_separated", "full_mix", "synth", "auto"):
        raise HTTPException(status_code=400, detail=f"Unknown source_kind {source_kind!r}.")
    if platform not in SUPPORTED_PLATFORMS + ["auto"]:
        raise HTTPException(status_code=400, detail=f"Unknown platform {platform!r}. Supported: {SUPPORTED_PLATFORMS + ['auto']}")

    # Basic URL validation
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="Invalid URL format.")

    # Create temp directory for download
    tmp_dir = Path(tempfile.mkdtemp(prefix="toneforge_yt_"))

    try:
        logger.info(f"Downloading audio from: {url} (fast_mode={fast_mode})")
        audio_path, start_timestamp, display_name = _download_youtube_audio(url, tmp_dir)

        logger.info(f"Analyzing: {display_name} (starting at {start_timestamp}s)")

        from tone_forge import synth_analyzer, auto_detect
        import librosa

        # Load audio ONCE and reuse for all analysis
        y, sr = librosa.load(str(audio_path), sr=22050, mono=True)

        # Auto-detect audio type (pass loaded audio to avoid reload)
        detection = auto_detect.detect_audio_type(str(audio_path))

        # Use detection results - can have multiple True values for full mixes
        is_synth = detection.is_synth
        is_bass = detection.is_bass
        is_drums = detection.is_drums
        is_guitar = detection.is_guitar
        actual_source_kind = detection.recommended_source_kind

        # Mutual exclusion: skip guitar analysis if synth is clearly dominant
        skip_guitar = (
            detection.synth_confidence > 0.5 and
            detection.guitar_confidence < 0.25
        )
        if skip_guitar:
            is_guitar = False
            logger.info("Skipping guitar analysis - synth dominant (synth: %.2f, guitar: %.2f)",
                       detection.synth_confidence, detection.guitar_confidence)

        # In fast mode, skip stem separation (treat full_mix as isolated for speed)
        if fast_mode and actual_source_kind == "full_mix":
            actual_source_kind = "isolated_guitar"
            logger.info("Fast mode: skipping stem separation")

        # Always analyze for synth characteristics
        synth_desc = synth_analyzer.analyze_synth(str(audio_path))
        synth_result = {
            "descriptor": synth_desc.to_dict(),
            "chain": [],
            "tweak_hints": _generate_synth_hints(synth_desc),
        }

        # Analyze as guitar if detected
        guitar_result = None
        if is_guitar:
            # Use detected source kind, or override if user specified
            if source_kind not in ("auto", "synth"):
                # User explicitly requested a source kind
                if fast_mode and source_kind == "full_mix":
                    actual_source_kind = "isolated_guitar"  # Fast mode override
                else:
                    actual_source_kind = source_kind

            descriptor = analyzer.analyze(
                str(audio_path),
                source_kind=actual_source_kind if actual_source_kind != "synth" else "isolated_guitar",
                display_name=display_name,
            )

            # Get Helix recommendations
            helix_card = helix_translator.translate(descriptor)
            helix_chain = [asdict(p) for p in helix_card.picks]

            # Get Pedals recommendations
            pedal_card = translator.translate(descriptor, platform="pedals")
            pedal_chain = [asdict(p) for p in pedal_card.picks]

            guitar_result = {
                "descriptor": descriptor.to_dict(),
                "tweak_hints": helix_card.tweak_hints,
                "platforms": {
                    "helix": helix_chain,
                    "pedals": pedal_chain,
                }
            }

        # Analyze as bass if detected
        bass_result = None
        if is_bass:
            from tone_forge import bass_analyzer
            # Use full_mix source_kind for stem separation in deep analysis mode
            bass_source_kind = "full_mix" if (detection.is_full_mix and not fast_mode) else "isolated_bass"
            bass_desc = bass_analyzer.analyze_bass(str(audio_path), source_kind=bass_source_kind)
            bass_result = {
                "descriptor": _bass_descriptor_to_dict(bass_desc),
                "recommendations": _get_bass_recommendations(bass_desc),
                "tweak_hints": _generate_bass_hints(bass_desc),
            }

        # Analyze as drums if detected
        drums_result = None
        if is_drums:
            from tone_forge import drum_analyzer
            # Note: drum_analyzer doesn't have stem separation yet, but structure is ready
            drum_desc = drum_analyzer.analyze_drums(str(audio_path))
            drums_result = {
                "descriptor": _drum_descriptor_to_dict(drum_desc),
                "machine_match": drum_analyzer.match_drum_machine(drum_desc),
                "tweak_hints": _generate_drum_hints(drum_desc),
            }

        # Extract MIDI if requested
        midi_result = None
        if request.extract_midi:
            try:
                from tone_forge import midi_extractor
                midi_data = midi_extractor.extract_midi(
                    str(audio_path),
                    preset_name=display_name or "Extracted MIDI",
                )
                midi_result = {
                    "filename": midi_data.filename,
                    "content": midi_data.content,
                    "note_count": midi_data.note_count,
                    "duration_seconds": midi_data.duration_seconds,
                    "tempo_bpm": midi_data.tempo_bpm,
                    "pitch_range": {
                        "lowest": midi_data.pitch_range[0],
                        "highest": midi_data.pitch_range[1],
                    },
                }
                # Include provenance if available
                if midi_data.provenance:
                    midi_result["provenance"] = midi_data.provenance
            except Exception as e:
                logger.warning(f"MIDI extraction failed: {e}")
                # Non-fatal - continue without MIDI

    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Download timed out. Try a shorter video or check your connection.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Analysis failed: {e}") from e
    finally:
        # Clean up temp directory
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # Determine the primary detected type based on what was analyzed
    # Priority for default tab: drums > bass > guitar > synth
    if drums_result:
        detected_type = "drums"
    elif bass_result:
        detected_type = "bass"
    elif guitar_result:
        detected_type = "guitar"
    elif synth_result:
        detected_type = "synth"
    else:
        detected_type = "guitar"

    # Build unified response with all platform results
    response = {
        "detected_type": detected_type,
        "source_name": display_name,  # Video title for export naming
        "source_url": url,  # Original URL for linking back
        "source_timestamp": start_timestamp if start_timestamp > 0 else None,
        "detection": {
            "is_full_mix": detection.is_full_mix,
            "is_guitar": detection.is_guitar,
            "is_synth": detection.is_synth,
            "is_bass": detection.is_bass,
            "is_drums": detection.is_drums,
            "summary": detection.summary,
            "confidence": {
                "mix": detection.mix_confidence,
                "instrument": detection.instrument_confidence,
                "drums": detection.drums_confidence,
                "synth": detection.synth_confidence,
                "bass": detection.bass_confidence,
                "guitar": detection.guitar_confidence,
            },
        },
        "synth": synth_result,
    }

    if guitar_result:
        response["guitar"] = guitar_result

    if bass_result:
        response["bass"] = bass_result

    if drums_result:
        response["drums"] = drums_result

    if midi_result:
        response["midi"] = midi_result

    # Backward compatibility - use detected_type for primary type
    response["type"] = detected_type
    if detected_type == "drums" and drums_result:
        response["descriptor"] = drums_result["descriptor"]
        response["chain"] = []
        response["tweak_hints"] = drums_result["tweak_hints"]
    elif detected_type == "bass" and bass_result:
        response["descriptor"] = bass_result["descriptor"]
        response["chain"] = bass_result.get("recommendations", [])
        response["tweak_hints"] = bass_result["tweak_hints"]
    elif detected_type == "synth" and synth_result:
        response["descriptor"] = synth_result["descriptor"]
        response["chain"] = []
        response["tweak_hints"] = synth_result["tweak_hints"]
    elif guitar_result:
        response["descriptor"] = guitar_result["descriptor"]
        response["chain"] = guitar_result["platforms"].get(platform if platform != "auto" else "helix", guitar_result["platforms"]["helix"])
        response["tweak_hints"] = guitar_result["tweak_hints"]
        response["platform"] = platform if platform != "auto" else "helix"

    # Save to history with full result for reloading
    history_entry = _add_to_history({
        "name": display_name or url[:50],
        "detected_type": response.get("detected_type", "guitar"),
        "summary": response.get("detection", {}).get("summary", ""),
        "amp_family": guitar_result["descriptor"].get("amp", {}).get("family") if guitar_result else None,
        "gain": guitar_result["descriptor"].get("amp", {}).get("gain") if guitar_result else None,
        "duration": guitar_result["descriptor"].get("source", {}).get("duration_sec") if guitar_result else synth_result["descriptor"].get("duration_sec"),
        "source_url": url,
    }, full_result=response)

    # Include history ID for shareable URL
    response["history_id"] = history_entry["id"]

    return JSONResponse(_convert_numpy_types(response))


@app.post("/api/analyze-url-stream")
async def analyze_url_stream_endpoint(request: UrlAnalyzeRequest):
    """Analyze audio from a YouTube URL with SSE progress streaming.

    Returns Server-Sent Events with progress updates, then final result.
    """
    async def generate():
        def send_progress(message: str, percent: int = 0):
            return f"data: {json.dumps({'type': 'progress', 'message': message, 'percent': percent})}\n\n"

        if not _check_yt_dlp():
            yield f"data: {json.dumps({'type': 'error', 'message': 'yt-dlp not installed'})}\n\n"
            return

        url = request.url.strip()
        source_kind = request.source_kind
        platform = request.platform
        fast_mode = request.fast_mode

        if not url.startswith(("http://", "https://")):
            yield f"data: {json.dumps({'type': 'error', 'message': 'Invalid URL format'})}\n\n"
            return

        tmp_dir = Path(tempfile.mkdtemp(prefix="toneforge_yt_"))

        try:
            # Step 1: Download
            yield send_progress("Downloading audio...", 10)
            await asyncio.sleep(0)  # Allow message to be sent

            audio_path, start_timestamp, display_name = _download_youtube_audio(url, tmp_dir)

            # Step 2: Load audio
            yield send_progress("Loading audio...", 25)
            await asyncio.sleep(0)

            from tone_forge import synth_analyzer, auto_detect
            import librosa
            import time as time_module

            # Track profiling for deep mode
            stage_timings = {} if not fast_mode else None
            pipeline_start = time_module.time() if not fast_mode else None

            stage_start = time_module.time() if not fast_mode else None
            y, sr = librosa.load(str(audio_path), sr=22050, mono=True)
            duration_sec = len(y) / sr

            if stage_timings is not None:
                stage_timings["loading"] = {"duration_ms": (time_module.time() - stage_start) * 1000}

            # Compute waveform peaks for visualization (deep mode only)
            waveform_data = None
            if not fast_mode:
                waveform_data = _compute_waveform_peaks(y, num_points=1000)
                waveform_data["sample_rate"] = int(sr)
                waveform_data["duration_sec"] = duration_sec

            # Step 3: Detect type
            yield send_progress("Detecting instrument types...", 35)
            await asyncio.sleep(0)

            detection = auto_detect.detect_audio_type(str(audio_path))
            is_synth = detection.is_synth
            is_bass = detection.is_bass
            is_drums = detection.is_drums
            is_guitar = detection.is_guitar
            actual_source_kind = detection.recommended_source_kind

            # Mutual exclusion: skip guitar analysis if synth is clearly dominant
            skip_guitar = (
                detection.synth_confidence > 0.5 and
                detection.guitar_confidence < 0.25
            )
            if skip_guitar:
                is_guitar = False
                logger.info("Skipping guitar analysis - synth dominant")

            # Report what was detected
            detected_types = []
            if is_drums: detected_types.append("drums")
            if is_synth: detected_types.append("synth")
            if is_bass: detected_types.append("bass")
            if is_guitar: detected_types.append("guitar")
            yield send_progress(f"Detected: {', '.join(detected_types) or 'unknown'}", 40)
            await asyncio.sleep(0)

            if fast_mode and actual_source_kind == "full_mix":
                actual_source_kind = "isolated_guitar"

            # Deep analysis: stem separation and quality analysis
            quality_result = {}
            stem_paths = {}
            guitar_audio = y
            guitar_sr = sr
            analysis_holder = None

            if not fast_mode and detection.is_full_mix:
                yield send_progress("Separating stems (GPU)...", 45)
                await asyncio.sleep(0)

                if stage_timings is not None:
                    stage_start = time_module.time()

                try:
                    from tone_forge import stem_separator
                    import concurrent.futures
                    loop = asyncio.get_event_loop()

                    def run_stem_separation():
                        return stem_separator.separate_all_stems(
                            str(audio_path),
                            model_name="htdemucs_6s",
                        )

                    with concurrent.futures.ThreadPoolExecutor() as pool:
                        stem_paths = await loop.run_in_executor(pool, run_stem_separation)

                    if stage_timings is not None:
                        stage_timings["stem_separation"] = {"duration_ms": (time_module.time() - stage_start) * 1000, "gpu_used": True}

                    # Load guitar stem for analysis
                    if "guitar" in stem_paths:
                        guitar_audio, guitar_sr = librosa.load(str(stem_paths["guitar"]), sr=22050, mono=True)
                        actual_source_kind = "isolated_guitar"
                    elif "other" in stem_paths:
                        guitar_audio, guitar_sr = librosa.load(str(stem_paths["other"]), sr=22050, mono=True)
                        actual_source_kind = "isolated_guitar"

                except Exception as e:
                    logger.warning(f"Stem separation failed: {e}")
                    if stage_timings is not None:
                        stage_timings["stem_separation"] = {"duration_ms": (time_module.time() - stage_start) * 1000, "error": str(e)}

                # Quality analysis on separated stem
                if _RECONSTRUCTION_AVAILABLE and stem_paths:
                    yield send_progress("Analyzing stem quality...", 50)
                    await asyncio.sleep(0)

                    try:
                        from tone_forge.reconstruction import ReconstructionPipeline
                        import concurrent.futures

                        if stage_timings is not None:
                            stage_start = time_module.time()

                        def run_quality_analysis():
                            pipeline = ReconstructionPipeline()
                            return pipeline.analyze_only(
                                guitar_audio,
                                guitar_sr,
                                stem_type="guitar",
                            )

                        with concurrent.futures.ThreadPoolExecutor() as pool:
                            analysis_holder = await loop.run_in_executor(pool, run_quality_analysis)

                        if stage_timings is not None:
                            stage_timings["quality_analysis"] = {"duration_ms": (time_module.time() - stage_start) * 1000}

                        # Extract quality results
                        if analysis_holder:
                            if analysis_holder.stem_quality:
                                sq = analysis_holder.stem_quality
                                quality_result["stem_quality"] = {
                                    "overall_quality": getattr(sq, 'overall_quality', None),
                                    "contamination_score": getattr(sq, 'contamination_score', None),
                                    "transient_integrity": getattr(sq, 'transient_integrity', None),
                                    "harmonic_purity": getattr(sq, 'harmonic_purity', None),
                                }
                            if analysis_holder.contamination:
                                ct = analysis_holder.contamination
                                contamination_regions = []
                                for e in getattr(ct, 'events', []):
                                    contamination_regions.append({
                                        "type": e.contamination_type.value if hasattr(e.contamination_type, 'value') else str(e.contamination_type),
                                        "start": e.time_start,
                                        "end": e.time_end,
                                        "severity": e.severity,
                                    })
                                quality_result["contamination"] = {
                                    "overall_score": getattr(ct, 'overall_contamination', None),
                                    "regions": contamination_regions,
                                }
                            if analysis_holder.artifacts:
                                art = analysis_holder.artifacts
                                artifact_regions = []
                                for a in getattr(art, 'artifacts', []):
                                    artifact_regions.append({
                                        "type": a.artifact_type.value if hasattr(a.artifact_type, 'value') else str(a.artifact_type),
                                        "start": a.time_start,
                                        "end": a.time_end,
                                        "severity": a.severity,
                                    })
                                quality_result["artifacts"] = {
                                    "overall_score": getattr(art, 'overall_artifact_score', None),
                                    "artifact_count": getattr(art, 'artifact_count', None),
                                    "regions": artifact_regions,
                                }

                    except Exception as e:
                        logger.warning(f"Quality analysis failed: {e}")
                        if stage_timings is not None:
                            stage_timings["quality_analysis"] = {"duration_ms": 0, "error": str(e)}

            # Step 4: Analyze synth
            yield send_progress("Analyzing synth characteristics...", 50)
            await asyncio.sleep(0)

            synth_desc = synth_analyzer.analyze_synth(str(audio_path))
            synth_result = {
                "descriptor": synth_desc.to_dict(),
                "chain": [],
                "tweak_hints": _generate_synth_hints(synth_desc),
            }

            # Step 5: Analyze guitar if detected
            guitar_result = None
            if is_guitar:
                yield send_progress("Analyzing guitar tone...", 60)
                await asyncio.sleep(0)

                if stage_timings is not None:
                    stage_start = time_module.time()

                if source_kind not in ("auto", "synth"):
                    if fast_mode and source_kind == "full_mix":
                        actual_source_kind = "isolated_guitar"
                    else:
                        actual_source_kind = source_kind

                # Use guitar stem if available, otherwise original audio
                analysis_audio_path = str(stem_paths.get("guitar", stem_paths.get("other", audio_path)))

                descriptor = analyzer.analyze(
                    analysis_audio_path,
                    source_kind=actual_source_kind if actual_source_kind != "synth" else "isolated_guitar",
                    display_name=display_name,
                    stem_quality=analysis_holder.stem_quality if analysis_holder else None,
                    contamination=analysis_holder.contamination if analysis_holder else None,
                    capture_reasoning=not fast_mode,  # Capture reasoning in deep mode
                )

                if stage_timings is not None:
                    stage_timings["tone_analysis"] = {"duration_ms": (time_module.time() - stage_start) * 1000}

                helix_card = helix_translator.translate(descriptor)
                helix_chain = [asdict(p) for p in helix_card.picks]
                pedal_card = translator.translate(descriptor, platform="pedals")
                pedal_chain = [asdict(p) for p in pedal_card.picks]

                guitar_result = {
                    "descriptor": descriptor.to_dict(),
                    "tweak_hints": helix_card.tweak_hints,
                    "platforms": {
                        "helix": helix_chain,
                        "pedals": pedal_chain,
                    }
                }

                # Add reasoning in deep mode
                if not fast_mode and descriptor.reasoning:
                    guitar_result["reasoning"] = descriptor.reasoning.to_dict()

            # Step 6: Analyze bass if detected
            bass_result = None
            if is_bass:
                yield send_progress("Analyzing bass tone...", 75)
                await asyncio.sleep(0)

                from tone_forge import bass_analyzer
                bass_source_kind = "full_mix" if (detection.is_full_mix and not fast_mode) else "isolated_bass"
                bass_desc = bass_analyzer.analyze_bass(str(audio_path), source_kind=bass_source_kind)
                bass_result = {
                    "descriptor": _bass_descriptor_to_dict(bass_desc),
                    "recommendations": _get_bass_recommendations(bass_desc),
                    "tweak_hints": _generate_bass_hints(bass_desc),
                }

            # Step 7: Analyze drums if detected
            drums_result = None
            if is_drums:
                yield send_progress("Analyzing drums...", 80)
                await asyncio.sleep(0)

                from tone_forge import drum_analyzer
                drum_desc = drum_analyzer.analyze_drums(str(audio_path))
                drums_result = {
                    "descriptor": _drum_descriptor_to_dict(drum_desc),
                    "machine_match": drum_analyzer.match_drum_machine(drum_desc),
                    "tweak_hints": _generate_drum_hints(drum_desc),
                }

            # Extract MIDI in deep analysis mode using stem separation
            # This produces cleaner MIDI by extracting from isolated stems
            # Uses 6-stem model if available for guitar/piano separation
            # Skip in fast mode since basic-pitch takes 30-60s per stem
            midi_result = None
            midi_stems = None
            if request.extract_midi and not fast_mode:
                yield send_progress("Separating stems for MIDI extraction...", 80)
                await asyncio.sleep(0)

                try:
                    from tone_forge import midi_extractor, stem_separator
                    import concurrent.futures

                    loop = asyncio.get_event_loop()

                    # Try 6-stem model first (has guitar, piano), fall back to 4-stem
                    try:
                        with concurrent.futures.ThreadPoolExecutor() as pool:
                            stem_paths = await loop.run_in_executor(
                                pool,
                                lambda: stem_separator.separate_all_stems(
                                    str(audio_path),
                                    model_name="htdemucs_6s"  # 6 stems: drums, bass, other, vocals, guitar, piano
                                )
                            )
                        logger.info(f"Using 6-stem model, got stems: {list(stem_paths.keys())}")
                    except Exception as e:
                        logger.warning(f"6-stem model failed, falling back to 4-stem: {e}")
                        with concurrent.futures.ThreadPoolExecutor() as pool:
                            stem_paths = await loop.run_in_executor(
                                pool,
                                lambda: stem_separator.separate_all_stems(str(audio_path))
                            )

                    # Extract MIDI from each stem
                    midi_stems = {}

                    # Define which stems to extract MIDI from and their display names
                    # (stem_key, display_label, is_drums, stem_type_for_profile)
                    # stem_type options: bass, drums, synth, pad, lead, vocals, other
                    stem_configs = [
                        ("drums", "Drums", True, "drums"),
                        ("bass", "Bass", False, "bass"),
                        ("guitar", "Guitar", False, "lead"),  # Guitar treated as lead for MIDI
                        ("piano", "Keys", False, "pad"),      # Keys often pad-like in synthwave
                        ("other", "Synth", False, "synth"),   # "other" contains remaining synths
                        ("vocals", "Vocals", False, "vocals"),
                    ]

                    total_stems = len([s for s, _, _, _ in stem_configs if s in stem_paths])
                    current_stem = 0

                    for stem_key, stem_label, is_drums, stem_type in stem_configs:
                        if stem_key not in stem_paths:
                            continue

                        current_stem += 1
                        progress = 82 + int((current_stem / total_stems) * 13)  # 82-95%
                        yield send_progress(f"Extracting {stem_label} MIDI ({current_stem}/{total_stems})...", progress)
                        await asyncio.sleep(0)

                        # Use drum-specific extraction for drums stem
                        if is_drums:
                            def extract_stem_midi(path, name, label, stype):
                                return midi_extractor.extract_drum_midi(
                                    str(path),
                                    preset_name=f"{name} - {label}",
                                )
                        else:
                            def extract_stem_midi(path, name, label, stype):
                                return midi_extractor.extract_midi(
                                    str(path),
                                    preset_name=f"{name} - {label}",
                                    stem_type=stype,
                                )

                        with concurrent.futures.ThreadPoolExecutor() as pool:
                            stem_midi = await loop.run_in_executor(
                                pool,
                                lambda sk=stem_key, sl=stem_label, st=stem_type: extract_stem_midi(
                                    stem_paths[sk],
                                    display_name or 'Track',
                                    sl,
                                    st
                                )
                            )

                        # Only include stems that have notes
                        if stem_midi.note_count > 0:
                            stem_midi_data = {
                                "label": stem_label,
                                "filename": stem_midi.filename,
                                "content": stem_midi.content,
                                "note_count": stem_midi.note_count,
                                "duration_seconds": stem_midi.duration_seconds,
                                "tempo_bpm": stem_midi.tempo_bpm,
                                "pitch_range": {
                                    "lowest": int(stem_midi.pitch_range[0]),
                                    "highest": int(stem_midi.pitch_range[1]),
                                },
                            }
                            if stem_midi.provenance:
                                stem_midi_data["provenance"] = stem_midi.provenance
                            midi_stems[stem_key] = stem_midi_data
                            logger.info(f"{stem_label} MIDI: {stem_midi.note_count} notes")

                    # Summary
                    total_notes = sum(s.get("note_count", 0) for s in midi_stems.values())
                    stem_summary = ", ".join(f"{v['label']}" for v in midi_stems.values())
                    yield send_progress(f"MIDI extracted: {total_notes} notes ({stem_summary})", 95)
                    await asyncio.sleep(0)

                    # Clean up stem files and their parent directory
                    stem_dir = None
                    for stem_path in stem_paths.values():
                        if stem_dir is None:
                            stem_dir = stem_path.parent
                        stem_path.unlink(missing_ok=True)
                    if stem_dir and stem_dir.exists():
                        try:
                            stem_dir.rmdir()
                        except OSError:
                            pass  # Directory not empty or other issue

                except Exception as e:
                    logger.warning(f"Per-stem MIDI extraction failed: {e}")
                    yield send_progress("MIDI extraction failed, continuing...", 95)
                    await asyncio.sleep(0)

            # Step 9: Build response
            yield send_progress("Building recommendations...", 95)
            await asyncio.sleep(0)

            # Determine primary type
            if drums_result:
                detected_type = "drums"
            elif bass_result:
                detected_type = "bass"
            elif guitar_result:
                detected_type = "guitar"
            elif synth_result:
                detected_type = "synth"
            else:
                detected_type = "guitar"

            response = {
                "detected_type": detected_type,
                "source_name": display_name,  # Video title for export naming
                "source_url": url,  # Original URL for linking back
                "source_timestamp": start_timestamp if start_timestamp > 0 else None,
                "deep_analysis": not fast_mode,  # Flag indicating deep analysis was used
                "duration_sec": duration_sec,
                "detection": {
                    "is_full_mix": detection.is_full_mix,
                    "is_guitar": detection.is_guitar,
                    "is_synth": detection.is_synth,
                    "is_bass": detection.is_bass,
                    "is_drums": detection.is_drums,
                    "summary": detection.summary,
                    "confidence": {
                        "mix": detection.mix_confidence,
                        "instrument": detection.instrument_confidence,
                        "drums": detection.drums_confidence,
                        "synth": detection.synth_confidence,
                        "bass": detection.bass_confidence,
                        "guitar": detection.guitar_confidence,
                    },
                },
                "synth": synth_result,
            }

            # Add deep analysis data if available
            if not fast_mode:
                if quality_result:
                    response["quality"] = quality_result
                if waveform_data:
                    response["waveform"] = _convert_numpy_types(waveform_data)
                if stage_timings:
                    total_time = time_module.time() - pipeline_start
                    response["profiling"] = {
                        "total_ms": total_time * 1000,
                        "audio_duration_sec": duration_sec,
                        "processing_ratio": total_time / duration_sec if duration_sec > 0 else 0,
                        "stages": stage_timings,
                    }
                # Keep stem paths for admin playback (convert to URLs)
                if stem_paths:
                    response["stems"] = {
                        stem_type: f"/api/serve-file?path={str(path)}"
                        for stem_type, path in stem_paths.items()
                    }

            if guitar_result:
                response["guitar"] = guitar_result
            if bass_result:
                response["bass"] = bass_result
            if drums_result:
                response["drums"] = drums_result
            if midi_stems:
                response["midi_stems"] = midi_stems
                # Also provide a combined "midi" for backward compatibility
                # Priority: guitar > piano > other > bass > vocals
                for stem_key in ["guitar", "piano", "other", "bass", "vocals"]:
                    if stem_key in midi_stems:
                        response["midi"] = midi_stems[stem_key]
                        break

            # Backward compatibility
            response["type"] = detected_type
            if detected_type == "drums" and drums_result:
                response["descriptor"] = drums_result["descriptor"]
                response["chain"] = []
                response["tweak_hints"] = drums_result["tweak_hints"]
            elif detected_type == "bass" and bass_result:
                response["descriptor"] = bass_result["descriptor"]
                response["chain"] = bass_result.get("recommendations", [])
                response["tweak_hints"] = bass_result["tweak_hints"]
            elif detected_type == "synth" and synth_result:
                response["descriptor"] = synth_result["descriptor"]
                response["chain"] = []
                response["tweak_hints"] = synth_result["tweak_hints"]
            elif guitar_result:
                response["descriptor"] = guitar_result["descriptor"]
                response["chain"] = guitar_result["platforms"].get(platform if platform != "auto" else "helix", guitar_result["platforms"]["helix"])
                response["tweak_hints"] = guitar_result["tweak_hints"]
                response["platform"] = platform if platform != "auto" else "helix"

            # Save to history
            history_entry = _add_to_history({
                "name": display_name or url[:50],
                "detected_type": response.get("detected_type", "guitar"),
                "summary": response.get("detection", {}).get("summary", ""),
                "amp_family": guitar_result["descriptor"].get("amp", {}).get("family") if guitar_result else None,
                "gain": guitar_result["descriptor"].get("amp", {}).get("gain") if guitar_result else None,
                "duration": duration_sec,
                "source_url": url,
                "deep_analysis": not fast_mode,
                "has_quality_data": bool(quality_result),
                "has_reasoning": bool(guitar_result and guitar_result.get("reasoning")),
            }, full_result=response)

            # Include history ID for shareable URL
            response["history_id"] = history_entry["id"]

            # Include admin analysis URL for deep analysis
            if not fast_mode:
                response["admin_url"] = f"/studio?analysis={history_entry['id']}"

            yield send_progress("Complete!", 100)
            yield f"data: {json.dumps({'type': 'result', 'data': response})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


# =============================================================================
# Feedback API - Collect user corrections for ML training
# =============================================================================

class BlockFeedbackRequest(BaseModel):
    """User feedback on block recommendations."""
    analysis_id: str
    slot: str  # "amp", "cab", "drive", etc.
    selected_block_id: str
    selected_block_family: str
    was_top_pick: bool = False
    original_rank: int = 0
    rating: Optional[float] = None  # 1-5 rating
    notes: Optional[str] = None


@app.post("/api/feedback/block")
async def submit_block_feedback(feedback: BlockFeedbackRequest):
    """Submit feedback on a block recommendation."""
    try:
        from tone_forge.ml.translator import submit_feedback

        submit_feedback(
            analysis_id=feedback.analysis_id,
            slot=feedback.slot,
            selected_block_id=feedback.selected_block_id,
            selected_block_family=feedback.selected_block_family,
            was_top_pick=feedback.was_top_pick,
            original_rank=feedback.original_rank,
            rating=feedback.rating,
            notes=feedback.notes,
        )

        return {"success": True}
    except ImportError:
        # ML module not available - silently accept feedback
        return {"success": True, "note": "ML not available"}
    except Exception as e:
        logger.exception("Failed to submit block feedback")
        raise HTTPException(status_code=500, detail=str(e))


class ParameterFeedbackRequest(BaseModel):
    """User feedback on parameter adjustments."""
    analysis_id: str
    slot: str
    block_id: str
    parameter: str
    original_value: float
    adjusted_value: float


@app.post("/api/feedback/parameter")
async def submit_parameter_feedback(feedback: ParameterFeedbackRequest):
    """Submit feedback on a parameter adjustment."""
    try:
        from tone_forge.ml.preferences import get_tracker

        tracker = get_tracker()
        tracker.track_parameter_edit(
            slot=feedback.slot,
            block_id=feedback.block_id,
            parameter=feedback.parameter,
            old_value=feedback.original_value,
            new_value=feedback.adjusted_value,
        )

        return {"success": True}
    except ImportError:
        return {"success": True, "note": "ML not available"}
    except Exception as e:
        logger.exception("Failed to submit parameter feedback")
        raise HTTPException(status_code=500, detail=str(e))


class ExportFeedbackRequest(BaseModel):
    """Feedback when user exports a preset."""
    analysis_id: str
    export_format: str  # "helix", "pedals", etc.
    rating: Optional[float] = None


@app.post("/api/feedback/export")
async def submit_export_feedback(feedback: ExportFeedbackRequest):
    """Submit feedback when exporting (indicates success)."""
    try:
        from tone_forge.ml.preferences import get_tracker
        from tone_forge.ml.retrieval import get_augmenter

        # Track export
        tracker = get_tracker()
        tracker.track_export(
            analysis_id=feedback.analysis_id,
            export_format=feedback.export_format,
            rating=feedback.rating,
        )

        # Mark reference as successful export
        augmenter = get_augmenter()
        augmenter.mark_successful(
            feedback.analysis_id,
            rating=feedback.rating,
        )

        return {"success": True}
    except ImportError:
        return {"success": True, "note": "ML not available"}
    except Exception as e:
        logger.exception("Failed to submit export feedback")
        raise HTTPException(status_code=500, detail=str(e))


class AnalysisFeedbackRequest(BaseModel):
    """General feedback on analysis quality."""
    analysis_id: str
    overall_rating: float  # 1-5
    descriptor_accuracy: Optional[float] = None
    recommendations_usefulness: Optional[float] = None
    notes: Optional[str] = None


@app.post("/api/feedback/analysis")
async def submit_analysis_feedback(feedback: AnalysisFeedbackRequest):
    """Submit overall feedback on an analysis."""
    try:
        from tone_forge.ml.preferences import get_tracker

        tracker = get_tracker()
        tracker.track_analysis_feedback(
            analysis_id=feedback.analysis_id,
            overall_rating=feedback.overall_rating,
            descriptor_accuracy=feedback.descriptor_accuracy,
            recommendations_usefulness=feedback.recommendations_usefulness,
            notes=feedback.notes,
        )

        return {"success": True}
    except ImportError:
        return {"success": True, "note": "ML not available"}
    except Exception as e:
        logger.exception("Failed to submit analysis feedback")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/feedback/stats")
async def get_feedback_stats():
    """Get feedback statistics (for admin/debugging)."""
    try:
        from tone_forge.ml.translator import get_feedback_stats
        from tone_forge.ml.preferences import get_tracker

        tracker = get_tracker()

        return {
            "translator_feedback": get_feedback_stats(),
            "behavior_stats": tracker.get_aggregated_stats(),
        }
    except ImportError:
        return {"error": "ML not available"}
    except Exception as e:
        logger.exception("Failed to get feedback stats")
        raise HTTPException(status_code=500, detail=str(e))
