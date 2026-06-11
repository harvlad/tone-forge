#!/usr/bin/env python3
"""
ToneForge Local Engine

A lightweight local server that handles GPU-accelerated audio processing.
Runs on localhost:7777 and accepts jobs from the ToneForge web app.

Features:
- Stem separation (Demucs) on local GPU
- MIDI extraction (basic_pitch)
- Progress streaming via SSE
- Auto-detection by browser

Usage:
    python -m local_engine.server
    # or
    python local_engine/server.py

The web app will automatically detect and use this when available.
"""

import asyncio
import json
import logging
import multiprocessing
import sys
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
import uvicorn

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("toneforge.local")

# Track active analysis processes for cleanup
_active_analyses: dict[str, multiprocessing.Process] = {}
_analysis_lock = asyncio.Lock()

# Timeout for analysis (10 minutes)
ANALYSIS_TIMEOUT = 600


async def cleanup_process(analysis_id: str, process: multiprocessing.Process, timeout: int = ANALYSIS_TIMEOUT):
    """Kill a process if it runs too long."""
    await asyncio.sleep(timeout)
    if process.is_alive():
        logger.warning(f"Analysis {analysis_id} timed out, killing process")
        process.terminate()
        await asyncio.sleep(2)
        if process.is_alive():
            process.kill()
    async with _analysis_lock:
        _active_analyses.pop(analysis_id, None)

app = FastAPI(
    title="ToneForge Local Engine",
    description="GPU-accelerated audio processing for ToneForge",
    version="0.1.0",
)

# Import the Connect supervisor lazily — it has no heavyweight imports
# of its own, but keeping it next to the other module-local helpers
# documents the boundary.
from local_engine.connect_bridge import get_supervisor as _get_connect_supervisor


@app.on_event("startup")
async def _spawn_connect_bridge() -> None:
    """Auto-launch the Connect audio bridge on local engine startup.

    The local engine is what the user installs; the Connect helper is
    bundled with it. We don't error out if the Connect binary isn't
    built yet — the supervisor surfaces that via /api/connect/status
    so the UI can guide the user to run `swift build` once.
    """
    try:
        status = _get_connect_supervisor().start()
        if status.running:
            logger.info("Connect bridge auto-started pid=%s", status.pid)
        else:
            logger.info("Connect bridge not started: %s", status.last_error or "binary missing")
    except Exception as e:
        logger.warning("Connect bridge auto-start failed: %s", e)


@app.on_event("shutdown")
async def _stop_connect_bridge() -> None:
    """Tear the Connect bridge down with the local engine."""
    try:
        _get_connect_supervisor().stop()
    except Exception as e:
        logger.warning("Connect bridge stop failed: %s", e)

# Allow CORS from any origin (needed for browser to call localhost)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# -----------------------------------------------------------------------------
# Status & Detection
# -----------------------------------------------------------------------------

def _get_device_info() -> dict:
    """Get info about available compute devices."""
    import torch

    info = {
        "pytorch_version": torch.__version__,
        "cuda_available": torch.cuda.is_available(),
        "mps_available": hasattr(torch.backends, "mps") and torch.backends.mps.is_available(),
        "device": "cpu",
        "device_name": "CPU",
    }

    if info["cuda_available"]:
        info["device"] = "cuda"
        info["device_name"] = torch.cuda.get_device_name(0)
    elif info["mps_available"]:
        info["device"] = "mps"
        info["device_name"] = "Apple Silicon GPU"

    return info


@app.get("/")
async def root():
    """Health check and capability advertisement."""
    device_info = _get_device_info()

    return {
        "service": "toneforge-local-engine",
        "version": "0.1.0",
        "status": "ready",
        "capabilities": [
            "stem_separation",
            "midi_extraction",
            "analysis",
        ],
        "device": device_info,
    }


@app.get("/health")
async def health():
    """Simple health check for detection."""
    return {"status": "ok", "service": "toneforge-local"}


@app.get("/api/serve-file")
async def serve_file(path: str):
    """Serve a file from the local filesystem (for stem playback)."""
    from fastapi.responses import FileResponse
    import urllib.parse

    # Decode URL-encoded path
    file_path = urllib.parse.unquote(path)
    file_path = Path(file_path)

    # Security: only allow serving from temp directories
    allowed_prefixes = ["/var/folders", "/tmp", tempfile.gettempdir()]
    is_allowed = any(str(file_path).startswith(prefix) for prefix in allowed_prefixes)

    if not is_allowed:
        raise HTTPException(status_code=403, detail="Access denied")

    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {file_path}")

    # Determine media type
    suffix = file_path.suffix.lower()
    media_types = {
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
        ".mid": "audio/midi",
        ".midi": "audio/midi",
    }
    media_type = media_types.get(suffix, "application/octet-stream")

    return FileResponse(file_path, media_type=media_type)


# -----------------------------------------------------------------------------
# Connect bridge — control surface for the Swift audio helper.
#
# The browser hits these to inspect / nudge the supervised process. The
# happy path is fully automatic (startup hook spawns it), so these are
# mostly for the tray UI and recovery.
# -----------------------------------------------------------------------------

def _connect_status_dict() -> dict:
    s = _get_connect_supervisor().status()
    return {
        "running": s.running,
        "pid": s.pid,
        "session_id": s.session_id,
        "binary": s.binary,
        "last_error": s.last_error,
        "log_path": s.log_path,
    }


@app.get("/api/connect/status")
async def connect_status():
    """Current state of the supervised Connect bridge child."""
    return _connect_status_dict()


@app.post("/api/connect/start")
async def connect_start():
    """Spawn the Connect bridge child if it isn't already running."""
    _get_connect_supervisor().start()
    return _connect_status_dict()


@app.post("/api/connect/stop")
async def connect_stop():
    """Stop the supervised Connect bridge child (SIGTERM then SIGKILL)."""
    _get_connect_supervisor().stop()
    return _connect_status_dict()


@app.post("/api/connect/restart")
async def connect_restart():
    """Stop + start in one call. Useful when the bridge gets wedged."""
    _get_connect_supervisor().restart()
    return _connect_status_dict()


@app.post("/api/engine/shutdown")
async def shutdown_engine():
    """Gracefully shutdown the local engine server."""
    import asyncio
    import os

    async def delayed_shutdown():
        await asyncio.sleep(0.5)  # Give time for response to be sent
        logger.info("Shutting down server...")
        os._exit(0)

    asyncio.create_task(delayed_shutdown())
    return {"status": "shutting_down", "message": "Server will stop in 0.5 seconds"}


# -----------------------------------------------------------------------------
# Stem Separation
# -----------------------------------------------------------------------------

class StemSeparationRequest(BaseModel):
    """Request for stem separation."""
    # Audio can be sent as base64 or file upload
    audio_base64: Optional[str] = None
    stems: list[str] = ["drums", "bass", "other", "vocals"]
    model: str = "htdemucs"


@app.post("/api/separate-stems")
async def separate_stems_endpoint(file: UploadFile = File(...)):
    """
    Separate audio into stems using Demucs.

    Returns SSE stream with progress updates and final result.
    """
    async def generate():
        try:
            # Save uploaded file
            with tempfile.NamedTemporaryFile(suffix=Path(file.filename).suffix, delete=False) as tmp:
                content = await file.read()
                tmp.write(content)
                tmp_path = tmp.name

            yield f"data: {json.dumps({'type': 'progress', 'stage': 'upload', 'message': 'File received'})}\n\n"

            # Import here to avoid slow startup
            from tone_forge.stem_separator import separate_all_stems

            yield f"data: {json.dumps({'type': 'progress', 'stage': 'loading', 'message': 'Loading Demucs model...'})}\n\n"

            # Run separation
            start_time = time.time()
            stems = separate_all_stems(tmp_path)
            elapsed = time.time() - start_time

            yield f"data: {json.dumps({'type': 'progress', 'stage': 'complete', 'message': f'Separation complete ({elapsed:.1f}s)'})}\n\n"

            # Return stem paths
            result = {
                "type": "result",
                "stems": {name: str(path) for name, path in stems.items()},
                "elapsed_seconds": elapsed,
                "device": _get_device_info()["device"],
            }
            yield f"data: {json.dumps(result)}\n\n"

            # Cleanup
            Path(tmp_path).unlink(missing_ok=True)

        except Exception as e:
            logger.exception("Stem separation failed")
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# -----------------------------------------------------------------------------
# MIDI Extraction
# -----------------------------------------------------------------------------

@app.post("/api/extract-midi")
async def extract_midi_endpoint(file: UploadFile = File(...), profile: str = "default"):
    """
    Extract MIDI from audio using basic_pitch.

    Returns SSE stream with progress and MIDI data.
    """
    async def generate():
        try:
            # Save uploaded file
            with tempfile.NamedTemporaryFile(suffix=Path(file.filename).suffix, delete=False) as tmp:
                content = await file.read()
                tmp.write(content)
                tmp_path = tmp.name

            yield f"data: {json.dumps({'type': 'progress', 'stage': 'upload', 'message': 'File received'})}\n\n"

            import librosa
            from tone_forge.midi_extractor import extract_midi

            yield f"data: {json.dumps({'type': 'progress', 'stage': 'extracting', 'message': 'Extracting MIDI...'})}\n\n"

            start_time = time.time()
            y, sr = librosa.load(tmp_path, sr=22050, mono=True)
            midi_result = extract_midi(y, sr, profile=profile)
            elapsed = time.time() - start_time

            yield f"data: {json.dumps({'type': 'progress', 'stage': 'complete', 'message': f'Extraction complete ({elapsed:.1f}s)'})}\n\n"

            result = {
                "type": "result",
                "midi": midi_result,
                "elapsed_seconds": elapsed,
            }
            yield f"data: {json.dumps(result)}\n\n"

            Path(tmp_path).unlink(missing_ok=True)

        except Exception as e:
            logger.exception("MIDI extraction failed")
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# -----------------------------------------------------------------------------
# Full Analysis (Deep) - Subprocess-based for isolation
# -----------------------------------------------------------------------------

@app.post("/api/analyze-deep")
async def analyze_deep_endpoint(file: UploadFile = File(...)):
    """
    Full deep analysis with stem separation + MIDI extraction.

    Runs in a subprocess to prevent GPU hangs from blocking the server.
    Returns SSE stream with progress and complete analysis result.
    """
    from local_engine.analysis_worker import run_file_analysis

    # Store original filename before saving to temp
    original_filename = file.filename

    # Save uploaded file
    with tempfile.NamedTemporaryFile(suffix=Path(file.filename).suffix, delete=False) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    analysis_id = str(uuid.uuid4())[:8]

    async def generate():
        # Create queue for inter-process communication
        queue = multiprocessing.Queue()

        # Start analysis in subprocess - pass original filename
        process = multiprocessing.Process(
            target=run_file_analysis,
            args=(tmp_path, queue, None, original_filename),  # Add filename parameter
            daemon=True
        )

        async with _analysis_lock:
            _active_analyses[analysis_id] = process

        process.start()
        logger.info(f"Started analysis {analysis_id} in subprocess {process.pid}")

        # Start cleanup task
        cleanup_task = asyncio.create_task(cleanup_process(analysis_id, process))

        yield f"data: {json.dumps({'type': 'progress', 'stage': 'starting', 'progress': 0.01, 'message': 'Starting analysis...'})}\n\n"

        try:
            # Read events from queue
            while True:
                # Non-blocking check with timeout
                try:
                    # Check queue in a thread to avoid blocking
                    event = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: queue.get(timeout=0.5)
                    )

                    if event["type"] == "done":
                        break
                    elif event["type"] == "error":
                        yield f"data: {json.dumps(event)}\n\n"
                        break
                    elif event["type"] == "result":
                        yield f"data: {json.dumps(event)}\n\n"
                    else:
                        # Progress event
                        yield f"data: {json.dumps(event)}\n\n"

                except Exception:
                    # Queue empty or timeout, check if process still alive
                    if not process.is_alive():
                        break
                    continue

        finally:
            cleanup_task.cancel()
            if process.is_alive():
                process.terminate()
            async with _analysis_lock:
                _active_analyses.pop(analysis_id, None)
            # Cleanup temp file
            Path(tmp_path).unlink(missing_ok=True)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


class UrlAnalyzeRequest(BaseModel):
    """Request for URL-based analysis."""
    url: str
    start_time: Optional[float] = None
    end_time: Optional[float] = None


@app.get("/api/analyses")
async def list_active_analyses():
    """List all active analysis processes."""
    async with _analysis_lock:
        analyses = []
        for analysis_id, process in _active_analyses.items():
            analyses.append({
                "id": analysis_id,
                "pid": process.pid,
                "alive": process.is_alive(),
            })
        return {"analyses": analyses}


@app.delete("/api/analyses/{analysis_id}")
async def kill_analysis(analysis_id: str):
    """Kill a specific analysis process."""
    async with _analysis_lock:
        process = _active_analyses.get(analysis_id)
        if not process:
            raise HTTPException(status_code=404, detail="Analysis not found")

        if process.is_alive():
            process.terminate()
            await asyncio.sleep(0.5)
            if process.is_alive():
                process.kill()

        _active_analyses.pop(analysis_id, None)
        return {"status": "killed", "id": analysis_id}


@app.delete("/api/analyses")
async def kill_all_analyses():
    """Kill all active analysis processes."""
    async with _analysis_lock:
        killed = []
        for analysis_id, process in list(_active_analyses.items()):
            if process.is_alive():
                process.terminate()
                killed.append(analysis_id)
        await asyncio.sleep(0.5)
        for analysis_id, process in list(_active_analyses.items()):
            if process.is_alive():
                process.kill()
        _active_analyses.clear()
        return {"status": "killed_all", "count": len(killed)}


@app.post("/api/analyze-url")
async def analyze_url_endpoint(request: UrlAnalyzeRequest):
    """
    Deep analysis from URL (YouTube, etc.) with stem separation + MIDI extraction.

    Runs in a subprocess to prevent GPU hangs from blocking the server.
    Returns SSE stream with progress updates.
    """
    from local_engine.analysis_worker import run_url_analysis

    analysis_id = str(uuid.uuid4())[:8]

    async def generate():
        # Create queue for inter-process communication
        queue = multiprocessing.Queue()

        # Start analysis in subprocess
        process = multiprocessing.Process(
            target=run_url_analysis,
            args=(request.url, queue, request.start_time, request.end_time),
            daemon=True
        )

        async with _analysis_lock:
            _active_analyses[analysis_id] = process

        process.start()
        logger.info(f"Started URL analysis {analysis_id} in subprocess {process.pid}")

        # Start cleanup task
        cleanup_task = asyncio.create_task(cleanup_process(analysis_id, process))

        yield f"data: {json.dumps({'type': 'progress', 'stage': 'starting', 'progress': 0.01, 'message': 'Starting analysis...'})}\n\n"

        try:
            # Read events from queue
            while True:
                try:
                    # Check queue in a thread to avoid blocking
                    event = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: queue.get(timeout=0.5)
                    )

                    if event["type"] == "done":
                        break
                    elif event["type"] == "error":
                        yield f"data: {json.dumps(event)}\n\n"
                        break
                    elif event["type"] == "result":
                        yield f"data: {json.dumps(event)}\n\n"
                    else:
                        # Progress event
                        yield f"data: {json.dumps(event)}\n\n"

                except Exception:
                    # Queue empty or timeout, check if process still alive
                    if not process.is_alive():
                        break
                    continue

        finally:
            cleanup_task.cancel()
            if process.is_alive():
                process.terminate()
            async with _analysis_lock:
                _active_analyses.pop(analysis_id, None)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# -----------------------------------------------------------------------------
# ML Runtime & Plugin APIs
# -----------------------------------------------------------------------------

@app.get("/api/ml/status")
async def ml_status():
    """Get ML runtime status and capabilities."""
    try:
        from local_engine.ml_runtime import get_ml_status
        return get_ml_status()
    except Exception as e:
        logger.exception("Failed to get ML status")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)},
        )


@app.get("/api/ml/models")
async def list_models():
    """List all available models and their status."""
    try:
        from local_engine.ml_runtime import get_downloader, MODELS

        downloader = get_downloader()
        models = {}

        for model_id, model_info in MODELS.items():
            models[model_id] = {
                "name": model_info.name,
                "size_bytes": model_info.size_bytes,
                "size_class": model_info.size_class.value,
                "available": downloader.is_model_available(model_id),
                "status": downloader.get_model_status(model_id).value,
            }

        return {
            "models": models,
            "storage": downloader.get_storage_usage(),
        }
    except Exception as e:
        logger.exception("Failed to list models")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)},
        )


class ModelDownloadRequest(BaseModel):
    """Request to download a model."""
    model_id: str


@app.post("/api/ml/download")
async def download_model(request: ModelDownloadRequest):
    """Download a specific ML model."""
    async def generate():
        try:
            from local_engine.ml_runtime import get_downloader, MODELS

            downloader = get_downloader()
            model_id = request.model_id

            if model_id not in MODELS:
                yield f"data: {json.dumps({'type': 'error', 'message': f'Unknown model: {model_id}'})}\n\n"
                return

            model_info = MODELS[model_id]
            yield f"data: {json.dumps({'type': 'progress', 'stage': 'starting', 'message': f'Downloading {model_info.name}...'})}\n\n"

            result = downloader.download_model(model_id)

            if result:
                yield f"data: {json.dumps({'type': 'result', 'success': True, 'model_id': model_id})}\n\n"
            else:
                yield f"data: {json.dumps({'type': 'error', 'message': 'Download failed'})}\n\n"

        except Exception as e:
            logger.exception("Model download failed")
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


@app.delete("/api/ml/models/{model_id}")
async def delete_model(model_id: str):
    """Delete a downloaded model."""
    try:
        from local_engine.ml_runtime import get_downloader

        downloader = get_downloader()
        downloader.delete_model(model_id)

        return {"success": True, "model_id": model_id}
    except Exception as e:
        logger.exception("Failed to delete model")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)},
        )


@app.get("/api/ml/capabilities")
async def ml_capabilities():
    """Get available ML inference capabilities."""
    try:
        from local_engine.ml_runtime import get_engine

        engine = get_engine()
        return {
            "capabilities": engine.get_available_capabilities(),
        }
    except Exception as e:
        logger.exception("Failed to get capabilities")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)},
        )


# -----------------------------------------------------------------------------
# Plugin Scanner API
# -----------------------------------------------------------------------------

# Background scan state
_scan_in_progress = False
_last_scan_result = None


@app.on_event("startup")
async def startup_plugin_scan():
    """Auto-scan for plugins on startup if needed."""
    global _scan_in_progress, _last_scan_result

    try:
        from local_engine.plugin_scanner import get_database, scan_and_register

        db = get_database()
        last_scan = db.get_last_scan_time()

        # Scan if never scanned or >24h since last scan
        should_scan = False
        if last_scan is None:
            logger.info("No previous plugin scan found, scanning...")
            should_scan = True
        else:
            from datetime import datetime
            hours_since_scan = (datetime.now() - last_scan).total_seconds() / 3600
            if hours_since_scan >= 24:
                logger.info(f"Last scan was {hours_since_scan:.1f}h ago, rescanning...")
                should_scan = True
            else:
                logger.info(f"Last scan was {hours_since_scan:.1f}h ago, using cached plugins")

        if should_scan:
            _scan_in_progress = True
            try:
                _last_scan_result = scan_and_register(
                    scan_au=True,
                    scan_vst3=True,
                    scan_vst2=False,
                    scan_ableton=True,
                )
                logger.info(f"Plugin scan complete: {_last_scan_result}")
            finally:
                _scan_in_progress = False
        else:
            # Load stats from existing data
            _last_scan_result = db.get_stats()

    except Exception as e:
        logger.exception("Startup plugin scan failed: %s", e)
        _scan_in_progress = False


@app.get("/api/plugins/stats")
async def plugin_stats():
    """Get plugin statistics."""
    try:
        from local_engine.plugin_scanner import get_database

        db = get_database()
        stats = db.get_stats()
        last_scan = db.get_last_scan_time()

        return {
            "total": stats.get("available_plugins", 0),
            "by_format": stats.get("by_format", {}),
            "by_type": stats.get("by_type", {}),
            "favorites": stats.get("favorites_count", 0),
            "manufacturers": stats.get("manufacturers_count", 0),
            "last_scan": last_scan.isoformat() if last_scan else None,
            "scan_in_progress": _scan_in_progress,
        }
    except Exception as e:
        logger.exception("Failed to get plugin stats")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)},
        )


@app.post("/api/plugins/match")
async def match_plugins_for_descriptor(descriptor: dict):
    """Find plugins matching a tone descriptor."""
    try:
        from local_engine.plugin_scanner import get_plugins_for_descriptor

        matches = get_plugins_for_descriptor(descriptor)

        return {
            "matches": matches,
            "total_matched": sum(len(v) for v in matches.values()),
        }
    except Exception as e:
        logger.exception("Failed to match plugins")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)},
        )


@app.get("/api/plugins")
async def list_plugins(
    query: str = "",
    format: Optional[str] = None,
    category: Optional[str] = None,
    favorites_only: bool = False,
):
    """List installed plugins."""
    try:
        from local_engine.plugin_scanner import get_database

        db = get_database()
        plugins = db.search_plugins(
            query=query,
            format=format,
            category=category,
            favorites_only=favorites_only,
        )

        return {
            "plugins": plugins,
            "count": len(plugins),
            "stats": db.get_stats(),
        }
    except Exception as e:
        logger.exception("Failed to list plugins")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)},
        )


@app.post("/api/plugins/scan")
async def scan_plugins_endpoint():
    """Scan for installed plugins (VST/AU and Ableton devices)."""
    global _scan_in_progress, _last_scan_result

    async def generate():
        global _scan_in_progress, _last_scan_result

        try:
            import platform
            from local_engine.plugin_scanner import scan_and_register

            if _scan_in_progress:
                yield f"data: {json.dumps({'type': 'error', 'message': 'Scan already in progress'})}\n\n"
                return

            _scan_in_progress = True
            system = platform.system()

            yield f"data: {json.dumps({'type': 'progress', 'stage': 'starting', 'message': f'Scanning plugins on {system}...'})}\n\n"

            yield f"data: {json.dumps({'type': 'progress', 'stage': 'vst', 'message': 'Scanning VST/AU plugins...'})}\n\n"

            result = scan_and_register(
                scan_au=True,
                scan_vst3=True,
                scan_vst2=False,
                scan_ableton=True,
            )
            _last_scan_result = result

            plugins_found = result['plugins_found']
            yield f"data: {json.dumps({'type': 'progress', 'stage': 'complete', 'message': f'Found {plugins_found} plugins'})}\n\n"

            yield f"data: {json.dumps({'type': 'result', 'success': True, **result})}\n\n"

        except Exception as e:
            logger.exception("Plugin scan failed")
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        finally:
            _scan_in_progress = False

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


class PluginMappingRequest(BaseModel):
    """Request to set plugin mapping."""
    plugin_id: str
    block_family: str
    block_type: str
    confidence: float = 1.0


@app.post("/api/plugins/mapping")
async def set_plugin_mapping(request: PluginMappingRequest):
    """Set a custom plugin-to-block mapping."""
    try:
        from local_engine.plugin_scanner import get_database

        db = get_database()
        db.set_block_mapping(
            plugin_id=request.plugin_id,
            block_family=request.block_family,
            block_type=request.block_type,
            confidence=request.confidence,
            user_set=True,
        )

        return {"success": True, "plugin_id": request.plugin_id}
    except Exception as e:
        logger.exception("Failed to set mapping")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)},
        )


@app.post("/api/plugins/{plugin_id}/favorite")
async def toggle_favorite(plugin_id: str, favorite: bool = True):
    """Toggle plugin favorite status."""
    try:
        from local_engine.plugin_scanner import get_database

        db = get_database()
        db.set_favorite(plugin_id, favorite)

        return {"success": True, "plugin_id": plugin_id, "favorite": favorite}
    except Exception as e:
        logger.exception("Failed to toggle favorite")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)},
        )


# -----------------------------------------------------------------------------
# Preferences API
# -----------------------------------------------------------------------------

@app.get("/api/preferences")
async def get_preferences():
    """Get learned user preferences."""
    try:
        from tone_forge.ml.preferences import get_learner, get_tracker

        learner = get_learner()
        tracker = get_tracker()

        preferences = learner.learn_preferences()
        stats = tracker.get_aggregated_stats()

        return {
            "preferences": {
                "amp": {
                    "preferred_families": preferences.amp.preferred_families,
                    "gain_preference": preferences.amp.gain_preference,
                },
                "cab": {
                    "preferred_configs": preferences.cab.preferred_configs,
                    "preferred_speakers": preferences.cab.preferred_speakers,
                },
                "effects": {
                    "frequently_used": preferences.effects.frequently_used,
                    "rarely_used": preferences.effects.rarely_used,
                },
                "genre": {
                    "primary_genres": preferences.genre.primary_genres,
                    "affinities": preferences.genre.affinities,
                },
            },
            "stats": stats,
        }
    except Exception as e:
        logger.exception("Failed to get preferences")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)},
        )


@app.get("/api/preferences/privacy")
async def get_privacy_settings():
    """Get privacy settings and data summary."""
    try:
        from tone_forge.ml.preferences import get_privacy_manager

        manager = get_privacy_manager()
        return {
            "tracking_enabled": manager.is_tracking_enabled(),
            "data_summary": manager.get_data_summary(),
        }
    except Exception as e:
        logger.exception("Failed to get privacy settings")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)},
        )


class TrackingSettingRequest(BaseModel):
    """Request to change tracking setting."""
    enabled: bool


@app.post("/api/preferences/tracking")
async def set_tracking(request: TrackingSettingRequest):
    """Enable or disable behavior tracking."""
    try:
        from tone_forge.ml.preferences import get_privacy_manager

        manager = get_privacy_manager()
        manager.set_tracking_enabled(request.enabled)

        return {"success": True, "tracking_enabled": request.enabled}
    except Exception as e:
        logger.exception("Failed to set tracking")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)},
        )


@app.post("/api/preferences/export")
async def export_preferences():
    """Export all preference data."""
    try:
        from tone_forge.ml.preferences import get_privacy_manager

        manager = get_privacy_manager()
        export_path = manager.export_all_data()

        return {
            "success": True,
            "export_path": str(export_path),
        }
    except Exception as e:
        logger.exception("Failed to export data")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)},
        )


@app.delete("/api/preferences/data")
async def delete_all_data():
    """Delete all collected preference data."""
    try:
        from tone_forge.ml.preferences import get_privacy_manager

        manager = get_privacy_manager()
        manager.delete_all_data()

        return {"success": True, "message": "All data deleted"}
    except Exception as e:
        logger.exception("Failed to delete data")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)},
        )


# -----------------------------------------------------------------------------
# Model Management API
# -----------------------------------------------------------------------------

# Track model download state
_model_download_in_progress = False
_model_download_cancel = False

REQUIRED_MODELS = {
    "htdemucs": {
        "hash": "955717e8",
        "description": "4-stem separation (drums, bass, other, vocals)",
        "size_mb": 80,
        "required": False,  # Fallback model
    },
    "htdemucs_6s": {
        "hash": "5c90dfd2",
        "description": "6-stem separation (drums, bass, guitar, piano, vocals, other)",
        "size_mb": 80,
        "required": True,  # Primary model
    },
}


def _get_model_cache_dir():
    """Get the torch hub cache directory."""
    return Path.home() / ".cache" / "torch" / "hub" / "checkpoints"


def _check_model_cached(model_hash: str) -> bool:
    """Check if a model is already cached."""
    cache_dir = _get_model_cache_dir()
    if not cache_dir.exists():
        return False
    for f in cache_dir.glob(f"{model_hash}*.th"):
        return True
    return False


@app.get("/api/models/status")
async def get_models_status():
    """Get status of required ML models."""
    status = {
        "models": {},
        "all_ready": True,
        "download_in_progress": _model_download_in_progress,
        "cache_dir": str(_get_model_cache_dir()),
    }

    for name, info in REQUIRED_MODELS.items():
        cached = _check_model_cached(info["hash"])
        status["models"][name] = {
            "name": name,
            "description": info["description"],
            "size_mb": info["size_mb"],
            "cached": cached,
            "required": info["required"],
        }
        if info["required"] and not cached:
            status["all_ready"] = False

    return status


@app.get("/api/models/download")
async def download_models():
    """Download required models with streaming progress (GET for EventSource compatibility)."""
    global _model_download_in_progress, _model_download_cancel

    async def generate():
        global _model_download_in_progress, _model_download_cancel
        _model_download_in_progress = True
        _model_download_cancel = False

        try:
            yield f"data: {json.dumps({'type': 'progress', 'stage': 'starting', 'message': 'Checking models...'})}\n\n"

            for name, info in REQUIRED_MODELS.items():
                if _model_download_cancel:
                    yield f"data: {json.dumps({'type': 'cancelled', 'message': 'Download cancelled by user'})}\n\n"
                    return

                if _check_model_cached(info["hash"]):
                    yield f"data: {json.dumps({'type': 'progress', 'stage': name, 'message': f'{name}: Already cached', 'cached': True})}\n\n"
                    continue

                size_mb = info["size_mb"]
                yield f"data: {json.dumps({'type': 'progress', 'stage': name, 'message': f'Downloading {name} ({size_mb}MB)...', 'cached': False})}\n\n"

                try:
                    # Import and download model
                    from demucs.pretrained import get_model
                    model = get_model(name)
                    del model  # Free memory

                    yield f"data: {json.dumps({'type': 'progress', 'stage': name, 'message': f'{name}: Downloaded successfully', 'cached': True})}\n\n"

                except Exception as e:
                    yield f"data: {json.dumps({'type': 'error', 'stage': name, 'message': f'Failed to download {name}: {str(e)}'})}\n\n"
                    if info["required"]:
                        return

            yield f"data: {json.dumps({'type': 'complete', 'message': 'All models ready'})}\n\n"

        except Exception as e:
            logger.exception("Model download failed")
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        finally:
            _model_download_in_progress = False

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


@app.post("/api/models/cancel")
async def cancel_model_download():
    """Cancel an in-progress model download."""
    global _model_download_cancel

    if not _model_download_in_progress:
        return {"success": False, "message": "No download in progress"}

    _model_download_cancel = True
    return {"success": True, "message": "Download cancellation requested"}


@app.on_event("startup")
async def startup_check_models():
    """Check for required models on startup and log status."""
    logger.info("Checking ML model status...")

    missing = []
    for name, info in REQUIRED_MODELS.items():
        if info["required"] and not _check_model_cached(info["hash"]):
            missing.append(name)

    if missing:
        logger.warning(f"Missing required models: {missing}")
        logger.warning("Run model download from the UI or use: python -m local_engine.download_models")
    else:
        logger.info("All required models are cached and ready")


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

def main():
    """Run the local engine server."""
    import argparse

    parser = argparse.ArgumentParser(description="ToneForge Local Engine")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    parser.add_argument("--port", type=int, default=7777, help="Port to bind to")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload")
    args = parser.parse_args()

    device_info = _get_device_info()

    print(f"""
╔══════════════════════════════════════════════════════════════╗
║           ToneForge Local Engine v0.1.0                      ║
╠══════════════════════════════════════════════════════════════╣
║  Device: {device_info['device_name']:<50} ║
║  Compute: {device_info['device'].upper():<49} ║
║  URL: http://{args.host}:{args.port:<43} ║
╚══════════════════════════════════════════════════════════════╝
    """)

    uvicorn.run(
        "local_engine.server:app" if args.reload else app,
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
